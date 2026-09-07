"""Confirming a record must publish it (PRD §7.6).

The endpoint used to update the queue row and write an audit entry and stop
there. The record stayed in the draft graph, where the projector never looks, so
a steward confirmed a record, the API answered `state: confirmed`, an audit row
said it happened — and the catalog was unchanged. The only thing that published
anything was the out-of-band CLI, which the review UI does not invoke.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DRAFT_SLUG = "review-publish-me"


@pytest.fixture
def steward(client):
    from datahub.api.entitlement import tokens
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        repos = Repositories(session)
        user = repos.users.upsert_federated(
            "local", "publisher", email="publisher@example.org", display_name="p"
        )
        user.role = "steward"
        session.flush()
        return tokens.mint(repos, user, name="steward").token


@pytest.fixture
def drafted(client, loaded):
    """A valid record sitting in the draft graph, queued for review.

    A copy of a fixture record under a fresh id, so the corpus's own published
    copy is left alone and this test's assertions are about this record only.
    """
    import json

    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories
    from datahub.graph.graphs import NamedGraph
    from fixtures.loader import load_record

    document = json.dumps(load_record("ecmwf-era5")).replace("/ds/ecmwf-era5", f"/ds/{DRAFT_SLUG}")
    loaded.put(json.loads(document), graph=NamedGraph.DRAFT)

    with session_scope() as session:
        Repositories(session).review.enqueue(DRAFT_SLUG)
    return loaded


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_confirming_moves_the_record_into_the_catalog(client, drafted, steward):
    from datahub.graph.graphs import NamedGraph

    assert drafted.exists(DRAFT_SLUG, graph=NamedGraph.DRAFT)
    assert not drafted.exists(DRAFT_SLUG, graph=NamedGraph.CATALOG)

    response = client.post(
        f"/v1/review/{DRAFT_SLUG}/confirm",
        json={"confirmed_fields": [], "notes": None},
        headers=auth(steward),
    )
    assert response.status_code == 200, response.text[:300]

    assert drafted.exists(DRAFT_SLUG, graph=NamedGraph.CATALOG), (
        "the steward confirmed it and it is still not in the catalog graph — "
        "which is the whole of the bug: the queue says confirmed and nothing "
        "was published"
    )
    assert not drafted.exists(DRAFT_SLUG, graph=NamedGraph.DRAFT)


def test_a_confirmed_record_becomes_visible_to_an_anonymous_search(client, drafted, steward):
    """The point of publishing, asserted from outside rather than from the graph."""
    before = client.get(f"/v1/datasets/{DRAFT_SLUG}")
    assert before.status_code == 404

    client.post(
        f"/v1/review/{DRAFT_SLUG}/confirm",
        json={"confirmed_fields": [], "notes": None},
        headers=auth(steward),
    )

    after = client.get(f"/v1/datasets/{DRAFT_SLUG}")
    assert after.status_code == 200, (
        "the record was promoted but never reprojected, so it is published and "
        "unfindable — visible in the graph and absent from every search"
    )


def test_confirming_an_already_published_record_is_not_an_error(client, loaded, steward):
    """A second confirmation, for more fields, must not fail.

    This used to say re-promoting "would write an empty graph over a real one".
    It would not: `put` resolves the dataset IRI and validates before it writes
    anything, so a promote of an absent draft raises `ValidationFailed` — 422,
    "record contains no dcat:Dataset node with an IRI" — and the catalog copy is
    untouched. Loud and wrong rather than quiet and destructive, which is a
    materially different bug and worth stating accurately: the guard in
    `_publish` exists to avoid a confusing error, not a data loss.
    """
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        Repositories(session).review.enqueue("ecmwf-era5")

    response = client.post(
        "/v1/review/ecmwf-era5/confirm",
        json={"confirmed_fields": ["title"], "notes": None},
        headers=auth(steward),
    )
    assert response.status_code == 200, response.text[:300]
    assert client.get("/v1/datasets/ecmwf-era5").status_code == 200


def test_a_refused_confirm_publishes_nothing(client, loaded, steward) -> None:
    """A 404 must not leave the record published.

    Publishing before recording is right — a validation failure during promotion
    has to surface with the queue untouched — but it must not come before the
    *refusal*. It did, and a draft with no queue row (one demoted by a
    re-harvest, or loaded by the CLI) was promoted into the catalog, indexed and
    left readable by anonymous callers while the response said 404. The `raise`
    also skipped the audit row, so nothing recorded that it had happened.
    """
    import json

    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories
    from datahub.graph.graphs import NamedGraph
    from fixtures.loader import load_record

    slug = "unqueued-draft"
    document = json.dumps(load_record("ecmwf-era5")).replace("/ds/ecmwf-era5", f"/ds/{slug}")
    loaded.put(json.loads(document), graph=NamedGraph.DRAFT)

    with session_scope() as session:
        assert Repositories(session).review.by_dataset(slug) is None

    response = client.post(
        f"/v1/review/{slug}/confirm",
        json={"confirmed_fields": [], "notes": None},
        headers=auth(steward),
    )

    assert response.status_code == 404
    assert not loaded.exists(slug, graph=NamedGraph.CATALOG), (
        "the record was published by a request that answered 404"
    )
    assert loaded.exists(slug, graph=NamedGraph.DRAFT), "the draft was consumed by a failed confirm"
    assert client.get(f"/v1/datasets/{slug}").status_code == 404, (
        "an anonymous caller can read a record no steward successfully confirmed"
    )


def test_the_published_record_names_the_reviewer_with_a_resolvable_iri(client, drafted, steward):
    """`og:reviewedBy` must be an absolute IRI, not a bare database key.

    `caller.principal_id` is `User.id`, which is `uuid4().hex` — 32 hex
    characters and nothing else. `promote` wraps it in `URIRef` unchanged, and a
    URIRef with no scheme is a *relative* IRI: it resolves against whatever base
    the consumer happens to have, which for a record served as JSON-LD from one
    origin and re-serialised somewhere else is two different subjects, and for
    most consumers is nothing at all.

    Exactly the failure the shapes file warns about for licences — "free text
    like 'CC BY 4.0' silently becomes a relative IRI that resolves to nothing
    rather than failing to parse". Nothing catches it, because `sh:nodeKind
    sh:IRI` is satisfied by a relative IRI.

    `AGENT_BASE` has existed in `namespaces.py` since M1 with no callers. This
    is its caller.
    """
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.records import dataset_node
    from datahub.namespaces import AGENT_BASE

    client.post(
        f"/v1/review/{DRAFT_SLUG}/confirm",
        json={"confirmed_fields": [], "notes": None},
        headers=auth(steward),
    )

    node = dataset_node(drafted.get(DRAFT_SLUG, graph=NamedGraph.CATALOG))
    reviewer = node.get("reviewedBy")
    assert isinstance(reviewer, str) and reviewer, "the published record names no reviewer"
    assert reviewer.startswith(AGENT_BASE), (
        f"{reviewer!r} is not an absolute IRI, so it identifies nobody outside this database"
    )


def test_confirming_a_record_that_exists_in_neither_graph_is_refused(client, loaded, steward):
    """A queue row whose record is gone must not answer "confirmed".

    `_publish` returned False for anything not in the draft graph, on the
    reasoning that it must therefore already be in the catalog — true for the
    case it was written for (a second confirmation, for more fields) and false
    for a record that is in neither graph, which a delete or a failed harvest
    leaves behind. The steward got a 200, the queue row said confirmed, and an
    audit row recorded a publication of nothing.
    """
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        Repositories(session).review.enqueue("no-such-dataset")

    response = client.post(
        "/v1/review/no-such-dataset/confirm",
        json={"confirmed_fields": [], "notes": None},
        headers=auth(steward),
    )
    assert response.status_code == 404, response.text[:300]

    with session_scope() as session:
        item = Repositories(session).review.by_dataset("no-such-dataset")
        assert item is not None and item.state != "confirmed", (
            "the queue says a record nobody can read was confirmed"
        )


def test_losing_a_race_to_confirm_is_not_reported_as_an_invalid_record(
    client, drafted, steward, monkeypatch
):
    """Two stewards confirm at once; the loser must not be told the record is broken.

    `exists(DRAFT)` then `promote` is a check and an act with no lock between
    them. The winner promotes and deletes the draft; the loser's `promote` then
    reads an empty draft subgraph, builds a record consisting of three review
    triples, and fails SHACL — so the API answers 422 with a violation list
    saying the record has no title and no licence. It has both. It is published,
    by the request that ran a millisecond earlier.

    Nothing is corrupted — `put` validates before it writes — so the whole
    defect is the answer. Simulated by promoting from inside the loser's own
    `exists` call, which is the interleaving without the flakiness of threads.
    """
    from datahub.graph.records import RecordStore

    real_exists = RecordStore.exists
    fired = False

    def promote_from_under_it(self, dataset_id, *, graph=None):
        nonlocal fired
        answer = real_exists(self, dataset_id, graph=graph)
        if not fired and answer and dataset_id == DRAFT_SLUG:
            fired = True
            self.promote(dataset_id)  # the other steward, winning
        return answer

    monkeypatch.setattr(RecordStore, "exists", promote_from_under_it)
    response = client.post(
        f"/v1/review/{DRAFT_SLUG}/confirm",
        json={"confirmed_fields": [], "notes": None},
        headers=auth(steward),
    )
    monkeypatch.undo()

    assert response.status_code == 200, (
        f"the loser of the race was told its record failed validation:\n{response.text[:400]}"
    )
    from datahub.graph.graphs import NamedGraph

    assert drafted.exists(DRAFT_SLUG, graph=NamedGraph.CATALOG)
