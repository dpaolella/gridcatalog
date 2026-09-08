"""A published record the filter now rejects must be retractable (#47).

`harvest.yml` runs, in order:

    datahub record load data/catalog/*/     # every published record, back in the graph
    python -m datahub.harvest --source ...  # filter, normalise, write new/changed
    datahub record auto-promote
    datahub record export data/catalog --prune

A record the relevance filter now rejects is dropped at the filter -- nothing
written -- but step 1 already put its existing copy in the catalog graph, and
`--prune` removes files whose record is *not* in the graph. So it survives.

**Rejection therefore only ever applied to records that had never been
published.** Once in `data/catalog`, a record was immune to every later
tightening of the filter, and its continued presence looked exactly like a
record that passed. That is backwards for the direction that matters: a record
wrongly excluded is recovered by widening the filter, while a record wrongly
included was permanent until somebody deleted the file by hand -- which #44
required, 138 times.
"""

from __future__ import annotations

import pytest
from datahub.harvest.promote.policy import promote


def _node(state: str) -> dict[str, str]:
    return {
        "id": "https://catalog.opengrid.org/ds/example",
        "reviewState": state,
        "title": "Example",
    }


def test_a_flagged_record_is_never_auto_promoted() -> None:
    """The interaction that would have made retraction a no-op.

    `demote` stamps `flagged` and moves the record to the *draft* graph, which
    is exactly where `auto-promote` looks. `promote` skipped only `confirmed`,
    so the retraction would have been undone by the next step of the same
    workflow -- and the record would come back stamped `auto-confirmed`, which
    reads as the pipeline having substantiated it.
    """
    result = promote(_node("flagged"), health={})
    assert not result.promoted
    assert any(d.gate == "flagged" for d in result.decisions), result.decisions


def test_the_same_guard_protects_a_steward_flag() -> None:
    """PRD 7.6 flags a record when a source change lands under a confirmed field.

    That flag went to the same place and was undone the same way, so this is
    not only about retraction -- the flag was decorative for any record the
    gates would otherwise pass.
    """
    assert not promote(_node("flagged"), health={}).promoted


@pytest.mark.parametrize("state", ["draft", "auto-confirmed"])
def test_an_unflagged_record_reaches_the_real_gates(state: str) -> None:
    """The guard must not become "refuse everything".

    An unflagged record may still be refused -- these bare nodes carry no
    licence and no distribution -- but it must be refused *by the gates*, not
    short-circuited by the review-state branch.
    """
    result = promote(_node(state), health={})
    assert not any(d.gate in ("flagged", "already-confirmed") for d in result.decisions), (
        f"{state!r} was short-circuited instead of being judged: {result.decisions}"
    )


def test_demote_moves_the_record_and_records_why(tmp_path, monkeypatch) -> None:
    """Demoted, not deleted: the act has to be reversible and auditable.

    The one-off deletion in `72ad14e` was a correction, not a precedent. A
    retraction that deleted the file outright would leave nothing to review and
    no way to tell a retraction from a harvest that simply stopped seeing the
    record.
    """
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore, dataset_node
    from datahub.graph.store import RdflibStore

    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)

    import json
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "records" / "ecmwf-era5.jsonld"
    records.put(json.loads(fixture.read_text()), graph=NamedGraph.CATALOG, validate=False)
    dataset_id = next(iter(records.list_ids(graph=NamedGraph.CATALOG)))

    records.demote(dataset_id, reason="retracted by the relevance filter: no grid term matched")

    assert dataset_id not in set(records.list_ids(graph=NamedGraph.CATALOG)), (
        "a retracted record is still in the catalog graph, so --prune keeps its file"
    )
    node = dataset_node(records.get(dataset_id, graph=NamedGraph.DRAFT))
    assert node["reviewState"] == "flagged"
    assert "relevance filter" in str(node.get("knownIssue")), (
        "the reason did not survive the demotion, so a reviewer cannot tell why"
    )
