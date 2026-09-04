"""The harvest pipeline end to end (WP-3.1, WP-3.7).

    harvest ──► filter ──► normalize ──► enrich ──► validate ──► review queue

Everything upstream of this file is tested in isolation; what is tested here is
the sequencing, which is where the rules that matter live. Each of them is one
sentence in the PRD and one test below:

* re-harvest is idempotent, keyed on ``sourceId``;
* an unchanged payload short-circuits the pipeline;
* a steward-confirmed field is flagged, never overwritten;
* validation failures go to ``flagged``, never to the review queue;
* every relevance decision is logged, accepts included;
* a partial run is kept, with a checkpoint.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.models.repositories import Repositories
from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore, dataset_node
from datahub.graph.store import RdflibStore
from datahub.harvest.adapters.ckan import CkanAdapter
from datahub.harvest.runner import HarvestRunner, harvest_sources, run_sources

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "harvest"

OEDI = {
    "id": "oedi",
    "adapter": "ckan",
    "endpoint": "https://data.openei.org/api/3",
    "domains": ["DD1", "DD2", "DD5"],
    "priority": 1,
}


def ckan_payload() -> Any:
    return json.loads((FIXTURES / "ckan_page1.json").read_text())


def ckan_adapter(payload: Any = None) -> CkanAdapter:
    body = payload if payload is not None else ckan_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return CkanAdapter(
        "oedi",
        endpoint="https://data.openei.org/api/3",
        rate_per_second=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def store():
    store = RdflibStore()
    bootstrap(store)
    yield store
    store.close()


@pytest.fixture
def records(store) -> RecordStore:
    return RecordStore(store)


@pytest.fixture
def db(settings):
    from datahub.api.models.base import create_all, reset_engine, session_scope

    reset_engine()
    create_all(settings)
    yield session_scope
    reset_engine()


@pytest.fixture
def runner(records, settings, db) -> HarvestRunner:
    return HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(), session_factory=db)


def repos(db):
    return db


# ---- the happy path ------------------------------------------------------


def test_a_run_produces_records_and_a_summary(runner, records) -> None:
    result = runner.run()

    assert result.seen == 3
    assert result.accepted == 2, "the library-hours record is not a grid dataset"
    assert result.rejected == 1
    assert result.created == 2
    assert records.count(graph=NamedGraph.DRAFT) == 2
    assert "2 relevant" in result.summary


def test_harvested_records_land_in_draft_not_the_catalog(runner, records) -> None:
    """A steward confirms records (PRD §7.6). Nothing in this pipeline may
    shortcut that, whatever the source says about itself."""
    runner.run()

    assert records.count(graph=NamedGraph.CATALOG) == 0
    for dataset_id in records.list_ids(graph=NamedGraph.DRAFT):
        node = dataset_node(records.get(dataset_id, graph=NamedGraph.DRAFT))
        assert node["reviewState"] == "draft"


def test_a_validating_record_reaches_the_review_queue(runner, db) -> None:
    result = runner.run()

    with db() as session:
        queued = Repositories(session).review.next_batch(state="draft", limit=50)
    assert result.queued == len(queued) >= 1
    assert all(item.validation_conforms for item in queued)
    assert all(item.source_id == "oedi" for item in queued)


def test_the_run_is_recorded_with_its_counts(runner, db) -> None:
    """A run record with counts, errors and duration (PRD §7.1). Without it
    nobody can tell a source that returned nothing from a source that was never
    contacted."""
    result = runner.run()

    with db() as session:
        run = Repositories(session).runs.get(result.run_id)
        assert run.state == "succeeded"
        assert run.records_seen == 3
        assert run.records_accepted == 2
        assert run.records_rejected == 1
        assert run.finished_at is not None


# ---- WP-3.7: flagged, never the review queue -----------------------------


def test_a_record_that_does_not_validate_is_flagged_not_queued(records, settings, db) -> None:
    """The queue is a work list of records that could be published. A record
    that does not validate cannot be, and mixing the two makes the queue
    useless as a work list."""
    payload = ckan_payload()
    # No description and no licence: this cannot reach level 1.
    payload["result"]["results"] = [
        {
            "id": "u1",
            "name": "bare-transmission-data",
            "title": "Transmission line ratings and substation locations",
            "private": False,
            "tags": [],
            "extras": [],
            "resources": [],
        }
    ]
    runner = HarvestRunner(
        OEDI, records, settings, adapter=ckan_adapter(payload), session_factory=db
    )

    result = runner.run()

    assert result.flagged == 1
    assert result.queued == 0
    with db() as session:
        repositories = Repositories(session)
        assert repositories.review.next_batch(state="draft") == []
        flagged = repositories.review.next_batch(state="flagged")
        assert len(flagged) == 1
        assert flagged[0].violations, "a flagged record says what is wrong with it"


def test_a_flagged_record_is_still_written(records, settings, db) -> None:
    """It is still the best information anyone has about that dataset, and
    deleting it would mean re-crawling to see it again."""
    payload = ckan_payload()
    payload["result"]["results"] = [
        {
            "id": "u1",
            "name": "bare-transmission-data",
            "title": "Transmission line ratings and substation locations",
            "private": False,
            "tags": [],
            "extras": [],
            "resources": [],
        }
    ]
    HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(payload), session_factory=db).run()

    assert records.count(graph=NamedGraph.DRAFT) == 1
    assert records.count(graph=NamedGraph.CATALOG) == 0


# ---- idempotency ---------------------------------------------------------


def test_re_harvesting_creates_nothing_new(records, settings, db) -> None:
    first = HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(), session_factory=db).run()
    before = records.count(graph=NamedGraph.DRAFT)

    second = HarvestRunner(
        OEDI, records, settings, adapter=ckan_adapter(), session_factory=db
    ).run()

    assert first.created == 2
    assert second.created == 0
    assert records.count(graph=NamedGraph.DRAFT) == before


def test_an_unchanged_payload_short_circuits_the_pipeline(records, settings, db) -> None:
    """Most of what makes a daily re-harvest of 2,100 records affordable: no
    re-normalise, no re-enrich, no re-validate, no model call."""
    HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(), session_factory=db).run()

    second = HarvestRunner(
        OEDI, records, settings, adapter=ckan_adapter(), session_factory=db
    ).run()

    assert second.unchanged == 2
    assert second.queued == 0, "an unchanged record does not need re-queueing"


def test_a_changed_payload_is_reprocessed(records, settings, db) -> None:
    HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(), session_factory=db).run()

    changed = ckan_payload()
    changed["result"]["results"][0]["notes"] = "Modeled wind speed at 2km. Now with forecasts."
    second = HarvestRunner(
        OEDI, records, settings, adapter=ckan_adapter(changed), session_factory=db
    ).run()

    assert second.unchanged == 1
    assert second.updated == 1
    node = dataset_node(
        records.get("https://catalog.opengrid.org/ds/nrel-wind-toolkit", graph=NamedGraph.DRAFT)
    )
    assert "Now with forecasts" in node["description"]


def test_re_harvest_matches_on_the_source_id(records, settings, db) -> None:
    """Not on anything derived. A source that corrects a typo in a title must
    not thereby create a second record (PRD §7.6)."""
    HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(), session_factory=db).run()

    retitled = ckan_payload()
    retitled["result"]["results"][0]["title"] = "NREL WIND Toolkit (corrected title)"
    HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(retitled), session_factory=db).run()

    assert records.count(graph=NamedGraph.DRAFT) == 2, "still two records, not three"


# ---- steward-confirmed fields --------------------------------------------


def test_a_confirmed_field_changing_at_source_flags_for_re_review(records, settings, db) -> None:
    """PRD §7.6. The steward's confirmation is a human judgement about a source
    that has since changed its mind; taking the new value silently would
    discard the judgement and never say so."""
    HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(), session_factory=db).run()
    dataset_id = "https://catalog.opengrid.org/ds/nrel-wind-toolkit"
    with db() as session:
        Repositories(session).review.confirm(
            dataset_id, reviewed_by="steward@example.org", confirmed_fields=["license"]
        )

    relicensed = ckan_payload()
    relicensed["result"]["results"][0]["license_id"] = "CC-BY-NC-SA-4.0"
    result = HarvestRunner(
        OEDI, records, settings, adapter=ckan_adapter(relicensed), session_factory=db
    ).run()

    assert result.conflicted == 1
    with db() as session:
        item = Repositories(session).review.by_dataset(dataset_id)
    assert item.state == "flagged"
    assert item.conflict_detail[0]["field"] == "license"
    assert "NC" in item.conflict_detail[0]["new"]
    assert item.confirmed_fields == ["license"], "the confirmation itself survives"


def test_an_unconfirmed_field_changing_is_not_a_conflict(records, settings, db) -> None:
    """Only confirmed fields are protected. Everything else is source-derived
    and updating it is the whole point of re-harvest."""
    HarvestRunner(OEDI, records, settings, adapter=ckan_adapter(), session_factory=db).run()

    changed = ckan_payload()
    changed["result"]["results"][0]["version"] = "4.0"
    result = HarvestRunner(
        OEDI, records, settings, adapter=ckan_adapter(changed), session_factory=db
    ).run()

    assert result.conflicted == 0


# ---- the relevance audit trail -------------------------------------------


def test_every_decision_is_logged_accepts_included(runner, db) -> None:
    """PRD §7.2: log every rejection with its reason so recall can be audited.
    The accepts matter too — an audit compares what was taken against what was
    passed over."""
    runner.run()

    with db() as session:
        repositories = Repositories(session)
        rates = repositories.relevance.rates()
        rejections = repositories.relevance.rejections()

    assert sum(sum(v.values()) for v in rates.values()) == 3
    assert len(rejections) == 1
    assert "library" not in rejections[0].reason.lower()
    assert rejections[0].reason, "a rejection with no reason cannot be audited"


def test_a_rejected_record_is_still_stored(runner, db) -> None:
    """The raw payload is kept so a filter change can be replayed without
    re-crawling a third party."""
    runner.run()

    with db() as session:
        raw = Repositories(session).raw.for_source("oedi")
    assert len(raw) == 3, "all three, including the one the filter rejected"


def test_a_rejected_record_reaches_neither_graph_nor_queue(runner, records, db) -> None:
    runner.run()

    ids = records.list_ids(graph=NamedGraph.DRAFT)
    assert not any("library" in i for i in ids)
    with db() as session:
        assert (
            Repositories(session).review.by_dataset(
                "https://catalog.opengrid.org/ds/county-library-hours"
            )
            is None
        )


# ---- partial runs and checkpoints ----------------------------------------


def test_a_source_that_fails_leaves_its_errors_on_the_run(records, settings, db) -> None:
    """A run that got 800 of 2,100 records is worth keeping."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "gone"})

    dead = CkanAdapter(
        "oedi",
        endpoint="https://data.openei.org/api/3",
        rate_per_second=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = HarvestRunner(OEDI, records, settings, adapter=dead, session_factory=db).run()

    assert result.errors
    assert result.seen == 0
    with db() as session:
        assert Repositories(session).runs.get(result.run_id).state == "failed"


def test_a_checkpoint_is_recorded_in_the_adapters_own_shape(runner, db) -> None:
    """A checkpoint the adapter cannot read is worse than none, because it
    looks like resumption is working."""
    result = runner.run()

    assert "modified_after" in result.checkpoint
    with db() as session:
        assert Repositories(session).runs.resume_point("oedi") == result.checkpoint


# ---- enrichment is off unless switched on --------------------------------


def test_no_model_is_called_by_default(runner) -> None:
    """An enricher that ran by default would be a bill and a third-party
    dependency nobody chose."""
    result = runner.run()
    assert result.enriched == 0


def test_enrichment_fills_gaps_when_enabled(records, settings, db, monkeypatch) -> None:
    from datahub.harvest.enrich import Enricher, ScriptedClient

    monkeypatch.setenv("DATAHUB_ENRICHMENT_ENABLED", "true")
    from datahub.config import get_settings, reset_settings

    reset_settings()
    enabled = get_settings()
    client = ScriptedClient(
        [{"summary": "Modeled wind resource time series for the continental US."}] * 4
    )
    runner = HarvestRunner(
        OEDI,
        records,
        enabled,
        adapter=ckan_adapter(),
        enricher=Enricher(enabled, client=client),
        session_factory=db,
    )

    result = runner.run()

    assert result.enriched == 2
    node = dataset_node(
        records.get("https://catalog.opengrid.org/ds/nrel-wind-toolkit", graph=NamedGraph.DRAFT)
    )
    assert node["summary"].startswith("Modeled wind resource")
    assert node["enrichmentBasis"] == "inferred"
    assert node["enrichmentModel"] == "scripted-model"


# ---- running several sources ---------------------------------------------


def test_sources_run_in_priority_order(records, settings, db, monkeypatch) -> None:
    """A run cut short by a schedule or a rate limit should have spent its time
    on OEDI and Zenodo, not on the twenty-record STAC catalog."""
    order: list[str] = []

    class Recording(HarvestRunner):
        def run(self, **kwargs):
            order.append(self.source_id)
            from datahub.harvest.runner import SourceResult

            return SourceResult(source_id=self.source_id, adapter=self.adapter.name)

    monkeypatch.setattr("datahub.harvest.runner.HarvestRunner", Recording)
    run_sources(records, settings=settings, max_priority=1, session_factory=db)

    priorities = {s["id"]: s.get("priority", 9) for s in harvest_sources(settings)}
    assert order
    assert priorities == {**priorities, **dict.fromkeys(order, 1)}
    assert order == sorted(order, key=lambda s: (priorities[s], s))


def test_an_unknown_source_is_refused(records, settings, db) -> None:
    with pytest.raises(KeyError, match="unknown source"):
        run_sources(records, source_ids=["nope"], settings=settings, session_factory=db)


def test_one_source_failing_does_not_stop_the_others(records, settings, db, monkeypatch) -> None:
    class Exploding(HarvestRunner):
        def run(self, **kwargs):
            if self.source_id == "oedi":
                raise RuntimeError("boom")
            from datahub.harvest.runner import SourceResult

            return SourceResult(source_id=self.source_id, adapter=self.adapter.name)

    monkeypatch.setattr("datahub.harvest.runner.HarvestRunner", Exploding)
    results = run_sources(records, settings=settings, max_priority=1, session_factory=db)

    failed = [r for r in results if r.errors]
    assert len(results) > 1
    assert len(failed) == 1
    assert failed[0].source_id == "oedi"


# ---- the module entry point ----------------------------------------------


def test_the_module_lists_sources(capsys) -> None:
    """PRD §7.1: each adapter independently runnable."""
    from datahub.harvest.__main__ import main

    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "oedi" in out
    assert "11 sources" in out


def test_the_module_refuses_to_run_everything_by_accident(capsys) -> None:
    """The default would be an eleven-source crawl of several thousand records
    against third parties, started by someone typing the command to see what it
    did."""
    from datahub.harvest.__main__ import main

    assert main([]) == 2
    assert "nothing to do" in capsys.readouterr().err


def test_the_module_filters_by_priority(capsys) -> None:
    from datahub.harvest.__main__ import main

    main(["--list", "--priority", "1"])
    out = capsys.readouterr().out
    assert "oedi" in out
    assert "datacite" not in out
