"""Link-health probing, auto-heal and exclusion (WP-5.3).

PRD §F1's acceptance criterion, restated as tests:

> A distribution that fails 3 consecutive probes is excluded from the access
> plan and a live sibling is returned instead. A stable redirect self-heals and
> appears in revision history.

Both halves are here, and so are the restraints that make the prober safe to
run against sources nobody asked: HEAD only, three failures not one, and
auto-heal only on a *stable permanent* redirect.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.broker import DEGRADED, REDIRECTED, UNREACHABLE, VERIFIED, Prober
from datahub.api.models.repositories import Repositories

URL = "https://example.org/data.nc"
DIST = "https://catalog.opengrid.org/dist/x--nc"
DATASET = "https://catalog.opengrid.org/ds/x"


@pytest.fixture
def db(settings):
    from datahub.api.models.base import create_all, reset_engine, session_scope

    reset_engine()
    create_all(settings)
    yield session_scope
    reset_engine()


def prober(handler, db, settings) -> Prober:
    return Prober(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        session_factory=db,
    )


def responds(status: int, headers: dict | None = None, *, record: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append((request.method, str(request.url), dict(request.headers)))
        return httpx.Response(status, headers=headers or {})

    return handler


# ---- one probe -----------------------------------------------------------


def test_a_probe_is_a_head_never_a_download(db, settings) -> None:
    """A prober that fetched what it was checking would move terabytes a week
    across sources that did not ask to be crawled, and would be
    indistinguishable from abuse."""
    calls: list = []
    subject = prober(responds(200, record=calls), db, settings)

    subject.probe(URL, distribution_id=DIST, dataset_id=DATASET)

    assert [c[0] for c in calls] == ["HEAD"]


def test_a_refused_head_falls_back_to_one_byte(db, settings) -> None:
    """Some object stores and CDNs refuse HEAD outright. A one-byte range is
    the smallest thing that still answers the question."""
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.headers.get("range")))
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(206, headers={"content-range": "bytes 0-0/1000"})

    outcome = prober(handler, db, settings).probe(URL, distribution_id=DIST, dataset_id=DATASET)

    assert calls == [("HEAD", None), ("GET", "bytes=0-0")]
    assert outcome.status == VERIFIED
    assert outcome.supports_range is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, VERIFIED),
        (204, VERIFIED),
        (301, REDIRECTED),
        (302, REDIRECTED),
        (401, DEGRADED),
        (403, DEGRADED),
        (429, DEGRADED),
        (404, UNREACHABLE),
        (500, UNREACHABLE),
    ],
)
def test_status_classification(db, settings, status: int, expected: str) -> None:
    headers = {"location": "https://example.org/moved.nc"} if 300 <= status < 400 else {}
    if status in (401, 403):
        # 403 on a HEAD triggers the range fallback; make that fail the same way.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status)
    else:
        handler = responds(status, headers)

    outcome = prober(handler, db, settings).probe(URL, distribution_id=DIST, dataset_id=DATASET)
    assert outcome.status == expected


def test_a_gated_url_is_degraded_not_broken(db, settings) -> None:
    """The resource is there and the caller needs credentials, which the record
    already says. Calling that a dead link would exclude a working dataset."""
    outcome = prober(responds(401), db, settings).probe(
        URL, distribution_id=DIST, dataset_id=DATASET
    )

    assert outcome.status == DEGRADED
    assert outcome.succeeded is False
    assert "authentication" in outcome.error


def test_an_object_store_uri_is_not_probed_and_not_condemned(db, settings) -> None:
    """An `s3://` URI is a perfectly good access path and its health is not
    knowable over HTTP. Recording it unreachable would exclude working data."""
    outcome = prober(responds(200), db, settings).probe(
        "s3://era5-pds/zarr/", distribution_id=DIST, dataset_id=DATASET
    )

    assert outcome.status == VERIFIED
    assert outcome.skipped_reason
    assert "not reachable over HTTP" in outcome.skipped_reason


def test_a_redirect_is_not_followed(db, settings) -> None:
    """Following it would report the destination's health under the old URL's
    name, so a dataset that had silently moved would look fine for ever."""
    calls: list = []
    handler = responds(301, {"location": "https://example.org/moved.nc"}, record=calls)

    outcome = prober(handler, db, settings).probe(URL, distribution_id=DIST, dataset_id=DATASET)

    assert len(calls) == 1
    assert outcome.redirect_target == "https://example.org/moved.nc"
    assert outcome.redirect_permanent is True


def test_capabilities_are_observed_not_taken_on_trust(db, settings) -> None:
    """A source's own metadata is often wrong about range support and CORS, and
    observing them costs nothing on a probe we are making anyway."""
    handler = responds(
        200,
        {
            "content-length": "4096",
            "accept-ranges": "bytes",
            "access-control-allow-origin": "*",
        },
    )
    outcome = prober(handler, db, settings).probe(URL, distribution_id=DIST, dataset_id=DATASET)

    assert outcome.content_length == 4096
    assert outcome.supports_range is True
    assert outcome.cors_enabled is True


def test_accept_ranges_none_means_no(db, settings) -> None:
    outcome = prober(responds(200, {"accept-ranges": "none"}), db, settings).probe(
        URL, distribution_id=DIST, dataset_id=DATASET
    )
    assert outcome.supports_range is False


def test_a_transport_error_is_unreachable_not_a_crash(db, settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    outcome = prober(handler, db, settings).probe(URL, distribution_id=DIST, dataset_id=DATASET)

    assert outcome.status == UNREACHABLE
    assert "ConnectError" in outcome.error


@pytest.fixture
def records(settings):
    """A store with one record carrying the distribution the tests probe."""
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore

    store = RdflibStore()
    bootstrap(store)
    record_store = RecordStore(store)
    record_store.put(
        {
            "@context": "https://schema.opengrid.org/context/opengrid-datahub.jsonld",
            "@graph": [
                {
                    "id": DATASET,
                    "type": "Dataset",
                    "title": "A dataset whose URL is about to move",
                    "description": "Transmission line ratings and substation locations.",
                    "dataDomain": ["https://schema.opengrid.org/concept/data-domain/DD1"],
                    "provenanceClass": (
                        "https://schema.opengrid.org/concept/provenance-class/curated"
                    ),
                    "license": "https://spdx.org/licenses/CC-BY-4.0",
                    "accessRestriction": (
                        "https://schema.opengrid.org/concept/access-restriction/none"
                    ),
                    "anonymousAccess": True,
                    "documentationStatus": "external-standard-only",
                    "completenessLevel": 1,
                    "reviewState": "draft",
                    "visibility": "public",
                    "harvestSource": "curated",
                    "distribution": [
                        {
                            "id": DIST,
                            "type": "Distribution",
                            "accessURL": URL,
                            "formatLabel": "NetCDF",
                            "hostedByOpenGrid": False,
                        }
                    ],
                }
            ],
        }
    )
    yield record_store
    store.close()


def healing_prober(db, settings, records) -> Prober:
    return Prober(
        settings,
        client=httpx.Client(
            transport=httpx.MockTransport(responds(301, {"location": MOVED})),
            follow_redirects=False,
        ),
        session_factory=db,
        records=records,
    )


# ---- exclusion after three failures --------------------------------------


def test_one_failure_does_not_exclude(db, settings) -> None:
    """A single failure is a network hiccup, a certificate renewal, a deploy.
    Excluding on the first one would make the catalog flap, and a catalog that
    flaps is one nobody trusts the link health of."""
    subject = prober(responds(500), db, settings)
    subject.run([(DIST, DATASET, URL)])

    with db() as session:
        health = Repositories(session).health.for_distribution(DIST)
    assert health.consecutive_failures == 1
    assert health.excluded_from_plans is False


def test_three_failures_exclude_from_access_plans(db, settings) -> None:
    """PRD §F1.13's acceptance criterion."""
    subject = prober(responds(500), db, settings)
    for _ in range(settings.probe_failure_threshold):
        subject.run([(DIST, DATASET, URL)])

    with db() as session:
        health = Repositories(session).health.for_distribution(DIST)
    assert health.consecutive_failures == settings.probe_failure_threshold
    assert health.excluded_from_plans is True


def test_a_recovery_restores_it_without_a_human(db, settings) -> None:
    """A source that fixed its outage should not need someone to notice before
    its data is usable again."""
    failing = prober(responds(500), db, settings)
    for _ in range(settings.probe_failure_threshold):
        failing.run([(DIST, DATASET, URL)])

    prober(responds(200), db, settings).run([(DIST, DATASET, URL)])

    with db() as session:
        health = Repositories(session).health.for_distribution(DIST)
    assert health.consecutive_failures == 0
    assert health.excluded_from_plans is False


def test_the_run_reports_what_it_excluded(db, settings) -> None:
    subject = prober(responds(500), db, settings)
    runs = [subject.run([(DIST, DATASET, URL)]) for _ in range(settings.probe_failure_threshold)]

    assert sum(r.excluded for r in runs) == 1, "excluded once, not once per probe"
    assert runs[-1].unreachable == 1
    assert "unreachable" in runs[-1].summary


# ---- auto-heal -----------------------------------------------------------


MOVED = "https://example.org/v2/data.nc"


def test_a_stable_permanent_redirect_heals(db, settings, records) -> None:
    """PRD §F1.12's acceptance criterion. A 301 seen once is a load balancer
    having an opinion; the same 301 three times is the resource having moved."""
    subject = healing_prober(db, settings, records)
    for _ in range(settings.probe_failure_threshold):
        run = subject.run([(DIST, DATASET, URL)])

    assert run.healed == 1
    with db() as session:
        revisions = Repositories(session).revisions.history(DIST)
    assert len(revisions) == 1
    assert revisions[0].old_value == URL
    assert revisions[0].new_value == MOVED
    assert revisions[0].source == "probe"


def test_the_old_url_stays_readable(db, settings, records) -> None:
    """PRD §F1.12: provenance is never silently rewritten. "It used to point
    somewhere else" is exactly the question someone asks when a download starts
    returning different data."""
    subject = healing_prober(db, settings, records)
    for _ in range(settings.probe_failure_threshold):
        subject.run([(DIST, DATASET, URL)])

    with db() as session:
        revision = Repositories(session).revisions.history(DIST)[0]
    assert revision.old_value == URL
    assert revision.automated is True
    assert "consecutive permanent redirects" in revision.detail


def test_a_temporary_redirect_never_heals(db, settings, records) -> None:
    """A 302 is the source telling us not to remember it."""
    subject = Prober(
        settings,
        client=httpx.Client(
            transport=httpx.MockTransport(responds(302, {"location": MOVED})),
            follow_redirects=False,
        ),
        session_factory=db,
        records=records,
    )
    for _ in range(settings.probe_failure_threshold + 2):
        run = subject.run([(DIST, DATASET, URL)])

    assert run.healed == 0
    with db() as session:
        assert Repositories(session).revisions.history(DIST) == []


def test_a_redirect_seen_once_does_not_heal(db, settings, records) -> None:
    run = healing_prober(db, settings, records).run([(DIST, DATASET, URL)])
    assert run.healed == 0


def test_a_wandering_redirect_target_does_not_heal(db, settings, records) -> None:
    """Three redirects to three different places is a load balancer, not a
    move. Healing to the last one would pin the record to one backend."""
    targets = iter([f"{MOVED}?node={n}" for n in range(9)])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": next(targets)})

    subject = Prober(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        session_factory=db,
        records=records,
    )
    for _ in range(settings.probe_failure_threshold + 1):
        run = subject.run([(DIST, DATASET, URL)])

    assert run.healed == 0


# ---- runs ----------------------------------------------------------------


def test_a_failed_write_does_not_stop_the_run(db, settings, monkeypatch) -> None:
    """A run killed by one bad row should leave the other answers."""
    from datahub.api.models.repositories import ProbeRepository

    calls = {"n": 0}
    original = ProbeRepository.record

    def flaky(self, result):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")
        return original(self, result)

    monkeypatch.setattr(ProbeRepository, "record", flaky)
    run = prober(responds(200), db, settings).run([(DIST, DATASET, URL), ("dist2", DATASET, URL)])

    assert run.probed == 2
    assert len(run.errors) == 1


def test_a_limit_is_honoured(db, settings) -> None:
    targets = [(f"dist{n}", DATASET, URL) for n in range(10)]
    run = prober(responds(200), db, settings).run(targets, limit=3)
    assert run.probed == 3


def test_health_schedules_the_next_probe(db, settings) -> None:
    """A tier 3 pointer at a government landing page does not need checking
    daily, and checking it does cost the source."""
    prober(responds(200), db, settings).run([(DIST, DATASET, URL)])

    with db() as session:
        health = Repositories(session).health.for_distribution(DIST)
    assert health.next_probe_due > datetime.now(UTC) + timedelta(days=6)


def test_cadence_by_kind() -> None:
    from datahub.api.broker import cadence_for
    from datahub.api.schemas import DistributionDetail

    api = DistributionDetail(id="a", subsetting_protocol="cds-request-api")
    bulk = DistributionDetail(id="b", format_label="Zarr")
    pointer = DistributionDetail(id="c", format_label="HTML")

    assert cadence_for(api) == 86_400
    assert cadence_for(bulk) == 604_800
    assert cadence_for(pointer, reference_only=True) == 2_592_000


# ---- auto-heal writes the record, not only the revision ------------------


def test_healing_updates_the_record_not_only_the_revision(db, settings, records) -> None:
    """PRD §F1.12 says the redirect "auto-updates the stored URL **and** writes
    a revision-history entry". Doing only the second half leaves the catalog
    pointing at the old URL for ever, and re-heals on every probe."""
    from datahub.api.schemas import DistributionDetail

    subject = healing_prober(db, settings, records)
    for _ in range(settings.probe_failure_threshold):
        subject.run([(DIST, DATASET, URL)])

    stored = DistributionDetail.from_record(records.get(DATASET))
    assert stored[0].access_url == MOVED


def test_a_healed_url_heals_exactly_once(db, settings, records) -> None:
    """A revision row per probe would bury the one real move in noise."""
    subject = healing_prober(db, settings, records)
    for _ in range(settings.probe_failure_threshold + 4):
        subject.run([(DIST, DATASET, URL)])

    with db() as session:
        assert len(Repositories(session).revisions.history(DIST)) == 1


def test_healing_a_draft_record_does_not_publish_it(db, settings, records) -> None:
    """A URL moving is not a steward confirming the record."""
    from datahub.graph.graphs import NamedGraph

    subject = healing_prober(db, settings, records)
    for _ in range(settings.probe_failure_threshold):
        subject.run([(DIST, DATASET, URL)])

    assert records.graph_of(DATASET) is NamedGraph.DRAFT


def test_without_a_record_store_nothing_is_healed_and_nothing_is_claimed(db, settings) -> None:
    """Writing a revision row saying "the URL was updated" when the record
    still holds the old one puts a false statement in the audit trail, which is
    worse than not healing."""
    subject = prober(responds(301, {"location": MOVED}), db, settings)
    for _ in range(settings.probe_failure_threshold + 1):
        run = subject.run([(DIST, DATASET, URL)])

    assert run.healed == 0
    with db() as session:
        assert Repositories(session).revisions.history(DIST) == []
