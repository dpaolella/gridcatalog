"""Graph store protocol and its two implementations (ADR-0002).

``FusekiStore`` is the production system of record. ``RdflibStore`` is an
in-process store with the same SPARQL 1.1 semantics — property paths, named
graphs and ``SERVICE`` included — so the Q1–Q5 regression suite, the conformance
suite and the entitlement matrix all run without a container runtime.

Nothing outside this package constructs a SPARQL client.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import rdflib.plugins.sparql
from datahub.config import GraphBackend, Settings, get_settings
from datahub.graph.graphs import NamedGraph
from datahub.graph.sparql import bind, prologue
from rdflib import Dataset, Graph, URIRef
from rdflib.query import Result
from rdflib.term import Node

log = logging.getLogger(__name__)

Binding = dict[str, Node | None]

# rdflib resolves a SPARQL ``FROM <g>`` by FETCHING g OVER THE NETWORK unless
# this flag is off. Two reasons it must be off here, and the second is the
# serious one:
#
#   * Every query this project scopes names graphs like
#     ``https://schema.opengrid.org/ns#graph/computed``. Before the semantic
#     layer has written anything, that graph is empty, rdflib reaches for the
#     public internet, and the error names a URL rather than the missing graph.
#   * With it on, any query carrying a ``FROM <http://attacker/>`` makes the
#     server issue an outbound request to an attacker-chosen URL — a
#     server-side request forgery, reachable from anywhere a query string is
#     accepted. Nothing in this project needs remote FROM; federation uses
#     ``SERVICE``, which this flag does not affect.
#
# It is a module-level global in rdflib, so it is set once, here, in the only
# module that constructs a store.
rdflib.plugins.sparql.SPARQL_LOAD_GRAPHS = False


class GraphStoreError(RuntimeError):
    """A query or update failed at the store."""


class GraphStore(ABC):
    """The narrow surface every store must provide.

    Implementations must agree on:

    * SPARQL 1.1 Query and Update, including property paths and ``GRAPH``.
    * ``select`` returning one dict per solution, values as rdflib terms and
      ``None`` for unbound variables.
    * Updates being atomic per call.
    """

    # ---- reads ----------------------------------------------------------

    @abstractmethod
    def _raw_query(self, query: str) -> Result: ...

    @abstractmethod
    def _raw_update(self, update: str) -> None: ...

    def select(self, query: str, params: Mapping[str, Any] | None = None) -> list[Binding]:
        """Run a SELECT and return solutions as dicts."""
        result = self._raw_query(prologue(bind(query, params)))
        if result.type != "SELECT":
            raise GraphStoreError(f"expected SELECT, got {result.type}")
        variables = [str(v) for v in (result.vars or [])]
        rows: list[Binding] = []
        for row in result:
            rows.append({v: row[v] for v in variables})  # type: ignore[index]
        return rows

    def ask(self, query: str, params: Mapping[str, Any] | None = None) -> bool:
        result = self._raw_query(prologue(bind(query, params)))
        if result.type != "ASK":
            raise GraphStoreError(f"expected ASK, got {result.type}")
        return bool(result.askAnswer)

    def construct(self, query: str, params: Mapping[str, Any] | None = None) -> Graph:
        """Run a CONSTRUCT or DESCRIBE and return the resulting graph."""
        result = self._raw_query(prologue(bind(query, params)))
        if result.type not in ("CONSTRUCT", "DESCRIBE"):
            raise GraphStoreError(f"expected CONSTRUCT, got {result.type}")
        graph = result.graph if result.graph is not None else Graph()
        out = Graph()
        for triple in graph:
            out.add(triple)
        return out

    def update(self, update: str, params: Mapping[str, Any] | None = None) -> None:
        """Run a SPARQL Update. Atomic per call."""
        self._raw_update(prologue(bind(update, params)))

    # ---- whole-graph operations ----------------------------------------

    @abstractmethod
    def get_graph(self, name: NamedGraph | str) -> Graph:
        """Read a named graph in full. Returns an empty graph if absent."""

    @abstractmethod
    def put_graph(self, name: NamedGraph | str, data: Graph) -> None:
        """Replace a named graph's contents wholesale."""

    @abstractmethod
    def add_graph(self, name: NamedGraph | str, data: Graph) -> None:
        """Merge triples into a named graph."""

    @abstractmethod
    def remove_graph(self, name: NamedGraph | str, data: Graph) -> None:
        """Remove specific triples from a named graph.

        The mirror of :meth:`add_graph`, and the only backend-neutral way to
        retract computed state. Without it the semantic runner reached for
        ``get_graph`` and removed triples from the copy it got back, so a value
        that should have disappeared simply accumulated alongside its
        replacement — a record ending up with two grades and whichever the
        query returned first winning.

        Triples that are not present are not an error: a retraction that has
        already happened is the state the caller wanted.
        """

    @abstractmethod
    def drop_graph(self, name: NamedGraph | str) -> None:
        """Remove a named graph and everything in it."""

    @abstractmethod
    def count(self, name: NamedGraph | str | None = None) -> int:
        """Triple count, for a named graph or for the whole store."""

    def clear(self) -> None:
        """Empty every graph. Test helper; never called in production paths."""
        for name in self.graph_names():
            self.drop_graph(name)

    @abstractmethod
    def graph_names(self) -> list[str]:
        """IRIs of the named graphs that currently hold triples."""

    def ensure_graphs(self, names: Iterable[NamedGraph | str]) -> None:
        """Make sure each named graph exists, even if empty.

        rdflib treats a ``FROM <g>`` naming a graph its dataset does not know
        as an instruction to *fetch that IRI over the network*. Since the graph
        IRIs are ``https://schema.opengrid.org/ns#graph/…``, a query scoped to
        a graph nobody has written yet — ``og:graph/computed`` before the
        semantic layer first runs — reaches for the public internet and fails
        with a parse error naming a URL, which is a long way from the actual
        problem.

        Fuseki has no such behaviour, so this is a no-op there. It is on the
        base class rather than on the rdflib store because callers should not
        have to know which backend they have.
        """

    # ---- lifecycle ------------------------------------------------------

    def flush(self) -> None:
        """Persist any buffered state. No-op where writes are already durable."""

    def close(self) -> None:
        """Release resources."""

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.flush()
        self.close()


# ---------------------------------------------------------------------------
# In-process backend
# ---------------------------------------------------------------------------


class RdflibStore(GraphStore):
    """rdflib-backed store. Memory by default, N-Quads on disk when given a path.

    Persistence is deliberately simple: the whole dataset is serialised on
    :meth:`flush`. This is a development and test backend, not a scale story;
    the production path is :class:`FusekiStore`.
    """

    def __init__(self, path: Path | None = None, *, autoflush: bool = True) -> None:
        self.path = Path(path) if path else None
        self.autoflush = autoflush
        self._lock = threading.RLock()
        self.dataset = Dataset(default_union=False)
        for prefix, ns in _prefix_items():
            self.dataset.bind(prefix, ns)
        if self.path and self.path.exists():
            self.dataset.parse(self.path.as_posix(), format="nquads")

    # -- helpers --

    def _named(self, name: NamedGraph | str) -> Graph:
        return self.dataset.graph(URIRef(str(name)))

    def _raw_query(self, query: str) -> Result:
        with self._lock:
            return self.dataset.query(query)

    def _raw_update(self, update: str) -> None:
        with self._lock:
            self.dataset.update(update)
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        if self.path and self.autoflush:
            self.flush()

    # -- whole-graph --

    def get_graph(self, name: NamedGraph | str) -> Graph:
        out = Graph()
        with self._lock:
            for triple in self._named(name):
                out.add(triple)
        for prefix, ns in _prefix_items():
            out.bind(prefix, ns)
        return out

    def put_graph(self, name: NamedGraph | str, data: Graph) -> None:
        with self._lock:
            self.dataset.remove_graph(URIRef(str(name)))
            target = self._named(name)
            for triple in data:
                target.add(triple)
        self._maybe_flush()

    def add_graph(self, name: NamedGraph | str, data: Graph) -> None:
        with self._lock:
            target = self._named(name)
            for triple in data:
                target.add(triple)
        self._maybe_flush()

    def remove_graph(self, name: NamedGraph | str, data: Graph) -> None:
        with self._lock:
            target = self._named(name)
            for triple in data:
                target.remove(triple)
        self._maybe_flush()

    def drop_graph(self, name: NamedGraph | str) -> None:
        with self._lock:
            self.dataset.remove_graph(URIRef(str(name)))
        self._maybe_flush()

    def count(self, name: NamedGraph | str | None = None) -> int:
        with self._lock:
            if name is None:
                return sum(len(g) for g in self.dataset.graphs())
            return len(self._named(name))

    def graph_names(self) -> list[str]:
        with self._lock:
            return [
                str(g.identifier)
                for g in self.dataset.graphs()
                if len(g) and not str(g.identifier).startswith("urn:x-rdflib")
            ]

    def ensure_graphs(self, names: Iterable[NamedGraph | str]) -> None:
        with self._lock:
            for name in names:
                self.dataset.graph(URIRef(str(name)))

    def flush(self) -> None:
        if not self.path:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            self.dataset.serialize(destination=tmp.as_posix(), format="nquads")
            tmp.replace(self.path)


# ---------------------------------------------------------------------------
# Production backend
# ---------------------------------------------------------------------------


class FusekiStore(GraphStore):
    """Apache Jena Fuseki over HTTP: SPARQL 1.1 Query, Update and Graph Store."""

    def __init__(
        self,
        query_endpoint: str,
        update_endpoint: str,
        gsp_endpoint: str,
        *,
        auth: tuple[str, str] | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.query_endpoint = query_endpoint
        self.update_endpoint = update_endpoint
        self.gsp_endpoint = gsp_endpoint
        self._client = client or httpx.Client(timeout=timeout, auth=auth, follow_redirects=True)
        self._owns_client = client is None

    def _raw_query(self, query: str) -> Result:
        accept = (
            "application/n-triples" if _is_graph_query(query) else "application/sparql-results+json"
        )
        response = self._client.post(
            self.query_endpoint,
            data={"query": query},
            headers={"Accept": accept},
        )
        _raise_for_status(response, query)
        if _is_graph_query(query):
            graph = Graph()
            graph.parse(data=response.text, format="nt")
            return Result.parse(source=None, format=None, graph=graph, type_="CONSTRUCT")  # type: ignore[arg-type]
        return Result.parse(source=_BytesSource(response.content), format="json")

    def _raw_update(self, update: str) -> None:
        response = self._client.post(self.update_endpoint, data={"update": update})
        _raise_for_status(response, update)

    def get_graph(self, name: NamedGraph | str) -> Graph:
        response = self._client.get(
            self.gsp_endpoint,
            params={"graph": str(name)},
            headers={"Accept": "application/n-triples"},
        )
        if response.status_code == 404:
            return Graph()
        _raise_for_status(response, f"GET graph {name}")
        graph = Graph()
        if response.content:
            graph.parse(data=response.text, format="nt")
        return graph

    def put_graph(self, name: NamedGraph | str, data: Graph) -> None:
        response = self._client.put(
            self.gsp_endpoint,
            params={"graph": str(name)},
            content=data.serialize(format="nt").encode("utf-8"),
            headers={"Content-Type": "application/n-triples"},
        )
        _raise_for_status(response, f"PUT graph {name}")

    def add_graph(self, name: NamedGraph | str, data: Graph) -> None:
        response = self._client.post(
            self.gsp_endpoint,
            params={"graph": str(name)},
            content=data.serialize(format="nt").encode("utf-8"),
            headers={"Content-Type": "application/n-triples"},
        )
        _raise_for_status(response, f"POST graph {name}")

    def remove_graph(self, name: NamedGraph | str, data: Graph) -> None:
        triples = data.serialize(format="nt").strip()
        if not triples:
            return
        # DELETE DATA rather than a DELETE WHERE pattern: these are ground
        # triples the caller already holds, and a pattern would risk matching
        # more than it was given.
        self.update(f"DELETE DATA {{ GRAPH <{name}> {{ {triples} }} }}")

    def drop_graph(self, name: NamedGraph | str) -> None:
        self.update(f"DROP SILENT GRAPH <{name}>")

    def count(self, name: NamedGraph | str | None = None) -> int:
        if name is None:
            rows = self.select("SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }")
        else:
            rows = self.select(f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{name}> {{ ?s ?p ?o }} }}")
        value = rows[0]["n"] if rows else None
        return int(value) if value is not None else 0

    def graph_names(self) -> list[str]:
        rows = self.select("SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } }")
        return [str(r["g"]) for r in rows if r["g"] is not None]

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class _BytesSource:
    """Minimal adapter so rdflib's result parser can read a bytes payload."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def getByteStream(self) -> Any:  # noqa: N802 - rdflib InputSource API
        import io

        return io.BytesIO(self._data)

    def getCharacterStream(self) -> Any:  # noqa: N802 - rdflib InputSource API
        return None

    def getPublicId(self) -> Any:  # noqa: N802 - rdflib InputSource API
        return None

    def getSystemId(self) -> Any:  # noqa: N802 - rdflib InputSource API
        return None

    def getEncoding(self) -> Any:  # noqa: N802 - rdflib InputSource API
        return None


def _is_graph_query(query: str) -> bool:
    head = query.upper()
    # Strip the prologue so a prefix IRI containing the word cannot mislead us.
    body = head.split("\nPREFIX")[-1]
    for keyword in ("CONSTRUCT", "DESCRIBE"):
        idx = body.find(keyword)
        if idx == -1:
            continue
        select_idx = body.find("SELECT")
        ask_idx = body.find("ASK")
        earlier = [i for i in (select_idx, ask_idx) if i != -1 and i < idx]
        if not earlier:
            return True
    return False


def _raise_for_status(response: httpx.Response, context: str) -> None:
    if response.status_code >= 400:
        raise GraphStoreError(
            f"store returned {response.status_code}: {response.text[:500]}\n"
            f"--- request ---\n{context[:1000]}"
        )


def _prefix_items() -> Iterable[tuple[str, Any]]:
    from datahub.namespaces import PREFIXES

    return PREFIXES.items()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_store(settings: Settings | None = None) -> GraphStore:
    """Construct the configured store. The only place a backend is chosen."""
    settings = settings or get_settings()
    if settings.graph_backend is GraphBackend.FUSEKI:
        auth = (
            (settings.fuseki_user, settings.fuseki_password)
            if settings.fuseki_user and settings.fuseki_password
            else None
        )
        return FusekiStore(
            settings.fuseki_query_endpoint,
            settings.fuseki_update_endpoint,
            settings.fuseki_gsp_endpoint,
            auth=auth,
            timeout=settings.graph_query_timeout_s,
        )
    return RdflibStore(settings.graph_store_path)


@contextmanager
def store_session(settings: Settings | None = None) -> Iterator[GraphStore]:
    """Open a store, flush and close it on exit."""
    store = make_store(settings)
    try:
        yield store
        store.flush()
    finally:
        store.close()
