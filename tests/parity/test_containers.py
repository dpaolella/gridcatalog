"""The same parity assertions, against Fuseki, OpenSearch and Postgres.

Marked `integration`, so the default suite skips them and the container-backed
CI job selects them. That job used to run `pytest -m integration` against a tree
where nothing carried the marker: it started three services, migrated a
database, collected zero tests and reported green. These are the tests it was
always meant to run.

Every assertion here is the same function the in-process suite runs on every
commit, so a failure means the backend differs — not that the test is wrong.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parity.assertions import GRAPH_ASSERTIONS, SEARCH_ASSERTIONS

pytestmark = pytest.mark.integration


def test_the_configured_services_are_the_ones_under_test() -> None:
    """The job's `DATAHUB_*` variables reach the tests.

    `tests/conftest.py` deletes every one of them so a unit test cannot touch a
    developer's real store, and for a long time it did that here too. The
    defaults it fell back to — `http://localhost:3030` and
    `http://localhost:9200` — are the addresses the job publishes, so it looked
    like it worked; what went missing was the credentials, and Fuseki's Shiro
    filter answered 401 before anything logged the request. The database URL
    went the same way, and these tests ran against SQLite while claiming to
    cover Postgres.

    A configuration that silently falls back to a default is exactly the check
    that cannot fail. This is the one that notices.
    """
    from datahub.config import get_settings

    settings = get_settings()
    assert settings.graph_backend.value == "fuseki", settings.graph_backend
    assert settings.search_backend.value == "opensearch", settings.search_backend
    assert settings.database_url.startswith("postgresql"), settings.database_url
    assert settings.fuseki_user, "the Fuseki credentials did not survive into the test process"


@pytest.fixture
def fuseki_store():
    """A Fuseki-backed store, on a graph this test owns and then drops."""
    from datahub.config import get_settings
    from datahub.graph.store import FusekiStore

    settings = get_settings()
    auth = (
        (settings.fuseki_user, settings.fuseki_password)
        if settings.fuseki_user and settings.fuseki_password
        else None
    )
    store = FusekiStore(
        settings.fuseki_query_endpoint,
        settings.fuseki_update_endpoint,
        settings.fuseki_gsp_endpoint,
        auth=auth,
        timeout=settings.graph_query_timeout_s,
    )
    try:
        yield store
    finally:
        # Leave nothing behind: these run against a shared server, and a
        # residue makes the *next* assertion fail for a reason that has
        # nothing to do with it.
        from datahub.graph.graphs import NamedGraph

        for name in (NamedGraph.CATALOG, NamedGraph.DRAFT):
            with contextlib.suppress(Exception):
                store.drop_graph(name)
        store.close()


@pytest.fixture
def opensearch_backend():
    from datahub.api.search.opensearch_backend import OpenSearchBackend
    from datahub.config import get_settings

    settings = get_settings()
    auth = (
        (settings.opensearch_user, settings.opensearch_password)
        if settings.opensearch_user and settings.opensearch_password
        else None
    )
    backend = OpenSearchBackend(settings.opensearch_url, settings.opensearch_index, auth=auth)
    backend.ensure_index()
    try:
        yield backend
    finally:
        with contextlib.suppress(Exception):
            backend.clear()
        backend.close()


@pytest.mark.parametrize("check", GRAPH_ASSERTIONS, ids=lambda f: f.__name__)
def test_fuseki(fuseki_store, check):
    check(fuseki_store)


@pytest.mark.parametrize("check", SEARCH_ASSERTIONS, ids=lambda f: f.__name__)
def test_opensearch(opensearch_backend, check):
    check(opensearch_backend)
