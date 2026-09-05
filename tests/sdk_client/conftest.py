"""Fixtures for the MCP and SDK suites.

**The directory is `tests/mcpserver`, not `tests/mcp`, and that matters.** This
conftest puts `tests/` on `sys.path` so the fixture corpus imports as
`fixtures`, which also makes every directory under it a top-level package name.
A `tests/mcp` package therefore shadows the official `mcp` SDK — fastmcp
imports `mcp`, gets the test package, and fails with an error that names
neither. Cost: one debugging session, and the symptom was a test that skipped
itself.


The API is mounted **in-process** through httpx's ASGI transport. That is not a
mock: it is the same FastAPI application, the same routers, the same
entitlement predicate, reached without a listening socket. A mocked API would
hide exactly the failures worth catching here — a tool that reads a field the
API does not return, or one that bypasses the entitlement it is supposed to
inherit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    from datahub.api import deps
    from datahub.api.models.base import create_all, reset_engine
    from datahub.config import reset_settings

    monkeypatch.setenv("DATAHUB_GRAPH_STORE_PATH", str(tmp_path / "graph.nq"))
    monkeypatch.setenv("DATAHUB_SEARCH_STORE_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("DATAHUB_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/ops.sqlite3")
    reset_settings()
    reset_engine()
    deps.reset()
    create_all()
    yield tmp_path
    deps.reset()
    reset_engine()
    reset_settings()


@pytest.fixture(scope="session")
def corpus_nquads(tmp_path_factory) -> Path:
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore
    from fixtures.loader import load_record, record_names

    path = tmp_path_factory.mktemp("mcp-corpus") / "corpus.nq"
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    for name in record_names():
        records.put(load_record(name))
    path.write_text(store.dataset.serialize(format="nquads"))
    store.close()
    return path


@pytest.fixture
def catalog(api_env, corpus_nquads):
    """The corpus, loaded and indexed, with the record store to hand."""
    from datahub.api import deps
    from datahub.api.models.base import session_scope
    from datahub.graph.records import RecordStore
    from datahub.projector import reindex

    store = deps.graph_store()
    store.dataset.parse(corpus_nquads.as_posix(), format="nquads")
    records = RecordStore(store)
    reindex(records, deps.search_backend(), session_factory=session_scope)
    return records


@pytest.fixture
def http(catalog):
    """The API, mounted in-process.

    Starlette's ``TestClient`` rather than a bare ``ASGITransport``, because a
    synchronous httpx client cannot drive an async app through a transport
    alone — the portal that bridges the two lives in the TestClient. It *is* an
    ``httpx.Client``, so both the MCP client and the SDK take it directly.
    """
    from datahub.api.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(), base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def client(http):
    from datahub.mcp import ApiClient

    with ApiClient(base_url="http://testserver", client=http) as api:
        yield api


@pytest.fixture
def tools(client):
    from datahub.mcp import Tools

    return Tools(client=client, tier=0)


@pytest.fixture
def tier1_tools(client):
    from datahub.mcp import Tools

    return Tools(client=client, tier=1)


@pytest.fixture
def hub(http):
    from opengrid import DataHub

    with DataHub(base_url="http://testserver", client=http) as h:
        yield h
