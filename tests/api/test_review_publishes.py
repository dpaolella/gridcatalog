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

    `promote` reads the draft subgraph, which for a published record is empty,
    so re-promoting would write an empty graph over a real one.
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
