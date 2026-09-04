"""Link-health probing, auto-heal and exclusion (WP-5.3).

PRD §F1.10–13:

> 10. Periodic link-health probing: HEAD or single-byte range request, **never
>     a full download**. Status one of verified, degraded, unreachable,
>     redirected.
> 12. A stable 3xx to a new location auto-updates the stored URL and writes a
>     revision-history entry. **Provenance is never silently rewritten.**
> 13. After N consecutive failed probes (default N=3) flag unreachable and
>     exclude from access plans, falling back to a live sibling distribution.

Three things are load-bearing here and each is a rule about restraint.

**Never a full download.** A prober that fetched what it was checking would
move terabytes a week across sources that did not ask to be crawled, and would
be indistinguishable from abuse. HEAD first; a single-byte range only where
HEAD is refused, which some object stores do.

**Three consecutive failures, not one.** A single failure is a network hiccup,
a certificate renewal, a deploy. Excluding on the first one would make the
catalog flap, and a catalog that flaps is one nobody trusts the link health of.

**Auto-heal only on a stable redirect.** A 301 seen once is a load balancer
having an opinion; the same 301 seen three times is the resource having moved.
And when the URL is updated the old one is kept in revision history, because a
record whose provenance was rewritten in place cannot be audited.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from datahub.api.models.operational import ProbeResult
from datahub.api.models.repositories import Repositories
from datahub.config import Settings, get_settings
from datahub.logging import get_logger

log = get_logger(__name__)

#: Statuses, per PRD §F1.10.
VERIFIED = "verified"
DEGRADED = "degraded"
UNREACHABLE = "unreachable"
REDIRECTED = "redirected"

#: Cadence by distribution kind (PRD §F1): APIs daily, bulk files weekly,
#: reference-only pointers monthly. A tier 3 pointer at a government landing
#: page does not need checking every day, and checking it does cost the source.
CADENCE_S: dict[str, int] = {"api": 86_400, "bulk": 604_800, "reference": 2_592_000}

#: Schemes HTTP probing cannot reach. Not a failure: an `s3://` URI is a
#: perfectly good access path and its health is simply not knowable this way.
#: Recording it as unreachable would exclude working data from access plans.
_UNPROBEABLE = ("s3://", "gs://", "gcs://", "az://", "abfs://", "ftp://")


@dataclass(slots=True)
class ProbeOutcome:
    distribution_id: str
    dataset_id: str
    status: str
    http_status: int | None = None
    latency_ms: float | None = None
    method: str = "HEAD"
    redirect_target: str | None = None
    redirect_permanent: bool = False
    content_length: int | None = None
    supports_range: bool | None = None
    cors_enabled: bool | None = None
    error: str | None = None
    skipped_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in (VERIFIED, REDIRECTED)

    def as_row(self, at: datetime | None = None) -> ProbeResult:
        return ProbeResult(
            distribution_id=self.distribution_id,
            dataset_id=self.dataset_id,
            probed_at=at or datetime.now(UTC),
            status=self.status,
            http_status=self.http_status,
            latency_ms=self.latency_ms,
            method=self.method,
            redirect_target=self.redirect_target,
            redirect_permanent=self.redirect_permanent,
            content_length=self.content_length,
            supports_range=self.supports_range,
            cors_enabled=self.cors_enabled,
            error=self.error,
        )


@dataclass(slots=True)
class ProbeRun:
    probed: int = 0
    verified: int = 0
    redirected: int = 0
    degraded: int = 0
    unreachable: int = 0
    skipped: int = 0
    healed: int = 0
    excluded: int = 0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{self.probed} probed", f"{self.verified} ok"]
        for count, label in (
            (self.redirected, "redirected"),
            (self.degraded, "degraded"),
            (self.unreachable, "unreachable"),
            (self.skipped, "not probeable"),
            (self.healed, "auto-healed"),
            (self.excluded, "newly excluded"),
        ):
            if count:
                parts.append(f"{count} {label}")
        return ", ".join(parts) + f" in {self.duration_s:.1f}s"


class Prober:
    """Checks whether the links in the catalog still work."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        session_factory: Any = None,
        records: Any = None,
    ) -> None:
        self.settings = settings or get_settings()
        #: The record store. Without it the prober still probes and still
        #: records health, but it will not auto-heal: writing a revision row
        #: saying "the URL was updated" when the record still holds the old one
        #: puts a false statement in the audit trail, which is worse than not
        #: healing.
        self.records = records
        self._client = client
        self._owns_client = client is None
        if session_factory is None:
            from datahub.api.models.base import session_scope

            session_factory = session_scope
        self._session_factory = session_factory

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.probe_timeout_s,
                # Not followed: a redirect is the finding, and following it
                # would report the destination's health under the old URL's
                # name — so a dataset that had silently moved would look fine
                # for ever.
                follow_redirects=False,
                headers={"User-Agent": self.settings.harvest_user_agent},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> Prober:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- one probe -------------------------------------------------------

    def probe(self, url: str, *, distribution_id: str, dataset_id: str) -> ProbeOutcome:
        """HEAD, falling back to a one-byte range. Never a full download.

        A prober that fetched what it was checking would move terabytes a week
        across sources that did not ask to be crawled, and would look exactly
        like abuse from the other end.
        """
        outcome = ProbeOutcome(
            distribution_id=distribution_id, dataset_id=dataset_id, status=VERIFIED
        )

        if not url.startswith(("http://", "https://")):
            outcome.status = VERIFIED
            outcome.skipped_reason = (
                f"{url.split('://', 1)[0]}:// is not reachable over HTTP. Its health is not "
                "knowable this way, and recording it as unreachable would exclude working "
                "data from access plans."
            )
            return outcome

        started = time.perf_counter()
        try:
            response = self.client.head(url)
            if response.status_code in (403, 405, 501):
                # Some object stores and CDNs refuse HEAD outright. A one-byte
                # range is the smallest thing that still answers the question.
                outcome.method = "GET(range)"
                response = self.client.get(url, headers={"Range": "bytes=0-0"})
        except httpx.HTTPError as exc:
            outcome.status = UNREACHABLE
            outcome.error = f"{type(exc).__name__}: {exc}"
            outcome.latency_ms = (time.perf_counter() - started) * 1000
            return outcome

        outcome.latency_ms = (time.perf_counter() - started) * 1000
        outcome.http_status = response.status_code
        self._read_headers(outcome, response)

        if 300 <= response.status_code < 400 and response.headers.get("location"):
            outcome.status = REDIRECTED
            outcome.redirect_target = str(httpx.URL(url).join(response.headers["location"]))
            outcome.redirect_permanent = response.status_code in (301, 308)
        elif response.status_code < 300:
            outcome.status = VERIFIED
        elif response.status_code in (401, 403):
            # Reachable, and gated. Not a broken link: the resource is there
            # and the caller needs credentials, which the record already says.
            outcome.status = DEGRADED
            outcome.error = f"{response.status_code}: authentication required"
        elif response.status_code == 429:
            outcome.status = DEGRADED
            outcome.error = "429: rate limited by the source"
        else:
            outcome.status = UNREACHABLE
            outcome.error = f"HTTP {response.status_code}"
        return outcome

    @staticmethod
    def _read_headers(outcome: ProbeOutcome, response: httpx.Response) -> None:
        """Capabilities, observed rather than claimed.

        ``Accept-Ranges`` and CORS are what decide whether a partial read or a
        browser fetch is possible, and a source's own metadata is often wrong
        about both. Observing them costs nothing on a probe we are making
        anyway.
        """
        headers = response.headers
        if length := headers.get("content-length"):
            with contextlib.suppress(ValueError):
                outcome.content_length = int(length)
        accept_ranges = headers.get("accept-ranges", "").lower()
        if accept_ranges:
            outcome.supports_range = accept_ranges != "none"
        elif response.status_code == 206:
            outcome.supports_range = True
        outcome.cors_enabled = "access-control-allow-origin" in headers

    # ---- a run -----------------------------------------------------------

    def run(self, targets: Sequence[tuple[str, str, str]], *, limit: int | None = None) -> ProbeRun:
        """Probe a batch of ``(distribution_id, dataset_id, url)``.

        Results are folded into the health rollup and the probe history as each
        one completes, not at the end: a run killed half way should leave half
        the answers, not none.
        """
        run = ProbeRun()
        started = time.perf_counter()

        for distribution_id, dataset_id, url in list(targets)[: limit or None]:
            outcome = self.probe(url, distribution_id=distribution_id, dataset_id=dataset_id)
            run.probed += 1
            if outcome.skipped_reason:
                run.skipped += 1
            else:
                setattr(run, outcome.status, getattr(run, outcome.status) + 1)
            try:
                self.record(outcome, url, run)
            except Exception as exc:
                run.errors.append(f"{distribution_id}: {type(exc).__name__}: {exc}")
                log.warning("probe result not recorded", distribution=distribution_id)

        run.duration_s = time.perf_counter() - started
        log.info("probe run complete", **{"summary": run.summary})
        return run

    def record(self, outcome: ProbeOutcome, url: str, run: ProbeRun) -> None:
        """Persist one probe, and act on what it found."""
        with self._session_factory() as session:
            repos = Repositories(session)
            row = repos.probes.record(outcome.as_row())
            health = repos.health.apply(row)

            if (
                outcome.status == REDIRECTED
                and outcome.redirect_target
                and self._should_heal(repos, outcome)
            ):
                self._heal(repos, outcome, url)
                run.healed += 1

            threshold = self.settings.probe_failure_threshold
            if health.consecutive_failures >= threshold and not health.excluded_from_plans:
                # PRD §F1.13. Excluded rather than merely ranked lower: a plan
                # pointing at a URL we know is dead is worse than a plan that
                # says the only path left is the gated one.
                repos.health.exclude_from_plans(outcome.distribution_id)
                run.excluded += 1
                log.warning(
                    "distribution excluded from access plans",
                    distribution=outcome.distribution_id,
                    failures=health.consecutive_failures,
                )
            elif health.consecutive_failures == 0 and health.excluded_from_plans:
                # It came back. Un-excluding is as important as excluding: a
                # source that fixed its outage should not need a human to
                # notice before its data is usable again.
                repos.health.exclude_from_plans(outcome.distribution_id, excluded=False)
                log.info("distribution restored", distribution=outcome.distribution_id)

    def _should_heal(self, repos: Repositories, outcome: ProbeOutcome) -> bool:
        """Whether this redirect is stable enough to rewrite the URL.

        A 301 seen once is a load balancer having an opinion. The same 301 seen
        by ``probe_failure_threshold`` consecutive probes is the resource
        having moved — and only a permanent redirect counts at all, because a
        302 is the source telling us not to remember it.
        """
        if not outcome.redirect_permanent:
            return False
        if self.records is None:
            log.info(
                "not auto-healing: the prober has no record store",
                distribution=outcome.distribution_id,
            )
            return False
        if any(
            revision.field == "accessURL" and revision.new_value == outcome.redirect_target
            for revision in repos.revisions.history(outcome.distribution_id, limit=20)
        ):
            # Already healed to this target. Without this check a stale target
            # list — or a probe run that has not yet re-read the record — heals
            # the same move again on every pass, and a revision row per probe
            # buries the one real move in noise.
            return False
        history = repos.probes.history(
            outcome.distribution_id, limit=self.settings.probe_failure_threshold
        )
        recent = [p for p in history if p.status == REDIRECTED]
        if len(recent) < self.settings.probe_failure_threshold:
            return False
        return all(p.redirect_target == outcome.redirect_target for p in recent)

    def _heal(self, repos: Repositories, outcome: ProbeOutcome, old_url: str) -> None:
        """Update the stored URL, keeping the old one readable.

        PRD §F1.12: *provenance is never silently rewritten.* The revision row
        is the whole point — a record whose URL changed with no trace cannot be
        audited, and "it used to point somewhere else" is exactly the question
        someone asks when a download starts returning different data.
        """
        # The record first. A revision row is a claim that the stored URL
        # changed; writing it before the change means a crash between the two
        # leaves the audit trail asserting something untrue.
        self._rewrite_url(outcome)
        repos.revisions.record(
            distribution_id=outcome.distribution_id,
            dataset_id=outcome.dataset_id,
            field="accessURL",
            old_value=old_url,
            new_value=outcome.redirect_target,
            source="probe",
            automated=True,
            detail=(
                f"Auto-healed after {self.settings.probe_failure_threshold} consecutive "
                f"permanent redirects to the same target."
            ),
        )
        log.info(
            "access URL auto-healed",
            distribution=outcome.distribution_id,
            old=old_url,
            new=outcome.redirect_target,
        )

    def _rewrite_url(self, outcome: ProbeOutcome) -> None:
        """Point the record at the new location.

        Written back through :class:`RecordStore`, which validates and
        re-projects, so a healed URL reaches search the same way any other edit
        does. The record keeps whichever graph it was in: healing a draft
        record must not publish it.
        """
        from datahub.graph.graphs import NamedGraph
        from datahub.graph.records import dataset_node

        graph = self.records.graph_of(outcome.dataset_id) or NamedGraph.DRAFT
        record = self.records.get(outcome.dataset_id, graph=graph)
        node = dataset_node(record)

        distributions = node.get("distribution") or []
        if isinstance(distributions, dict):
            distributions = [distributions]
        changed = False
        for dist in distributions:
            if isinstance(dist, dict) and dist.get("id") == outcome.distribution_id:
                dist["accessURL"] = outcome.redirect_target
                changed = True
        if not changed:
            raise LookupError(f"{outcome.distribution_id} is not on {outcome.dataset_id} any more")
        self.records.put(record, graph=graph, validate=False)


# ---------------------------------------------------------------------------
# Choosing what to probe
# ---------------------------------------------------------------------------


def due_targets(
    records: Any, repos: Repositories, *, limit: int = 500, now: datetime | None = None
) -> list[tuple[str, str, str]]:
    """Distributions whose next probe is due, as ``(dist, dataset, url)``.

    Health rows carry the schedule but not the URL — the URL lives in the
    record, because it is catalog data and the health row is operational state
    (PRD §3.3). So the schedule is read here and the URLs are looked up.
    """
    from datahub.api.schemas import DistributionDetail
    from datahub.graph.graphs import NamedGraph

    due = {row.distribution_id for row in repos.health.due(now=now, limit=limit)}
    targets: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for graph in (NamedGraph.CATALOG, NamedGraph.DRAFT):
        for dataset_id in records.list_ids(graph=graph):
            for dist in DistributionDetail.from_record(records.get(dataset_id, graph=graph)):
                if not dist.access_url or dist.id in seen:
                    continue
                seen.add(dist.id)
                # Due if the scheduler says so, or if it has never been probed
                # at all. The second case is the one that matters: waiting for
                # a scheduler to create the row first would mean a newly
                # harvested record is never checked.
                never_probed = repos.health.for_distribution(dist.id) is None
                if dist.id in due or never_probed:
                    targets.append((dist.id, dataset_id, dist.access_url))
                if len(targets) >= limit:
                    return targets
    return targets


def cadence_for(dist: Any, *, reference_only: bool = False) -> int:
    """How often to check this distribution.

    A tier 3 pointer at a government landing page does not need checking
    daily, and checking it does cost the source something.
    """
    if reference_only:
        return CADENCE_S["reference"]
    label = f"{getattr(dist, 'format_label', '') or ''} {getattr(dist, 'subsetting_protocol', '') or ''}".lower()
    if "api" in label or getattr(dist, "subsetting_protocol", None):
        return CADENCE_S["api"]
    return CADENCE_S["bulk"]


def iter_urls(records: Any, graphs: Iterable[Any]) -> list[tuple[str, str, str]]:
    """Every ``(distribution_id, dataset_id, url)`` in the catalog."""
    from datahub.api.schemas import DistributionDetail

    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for graph in graphs:
        for dataset_id in records.list_ids(graph=graph):
            for dist in DistributionDetail.from_record(records.get(dataset_id, graph=graph)):
                if dist.access_url and dist.id not in seen:
                    seen.add(dist.id)
                    out.append((dist.id, dataset_id, dist.access_url))
    return out


__all__ = [
    "CADENCE_S",
    "DEGRADED",
    "REDIRECTED",
    "UNREACHABLE",
    "VERIFIED",
    "ProbeOutcome",
    "ProbeRun",
    "Prober",
    "cadence_for",
    "due_targets",
    "iter_urls",
]
