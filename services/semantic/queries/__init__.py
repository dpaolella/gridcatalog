"""Named SPARQL queries, loaded from ``.rq`` files rather than embedded in code.

Two reasons they are files:

* They are the regression suite for the storage decision (PRD §4.6). A reviewer
  assessing whether the triple store earns its keep should be able to read the
  queries without reading the Python around them.
* A query in a file can be run by hand against a live Fuseki during an incident.
  A query built by string concatenation cannot.

Parameters use the ``??name`` placeholder form from :mod:`datahub.graph.sparql`,
so binding is injection-safe.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from datahub.graph.graphs import NamedGraph
from datahub.graph.store import Binding, GraphStore

QUERY_DIR = Path(__file__).resolve().parent


@functools.lru_cache(maxsize=32)
def load(name: str) -> str:
    """Read a named query. ``load("q1")`` finds ``q1-shared-upstream-origin.rq``."""
    exact = QUERY_DIR / f"{name}.rq"
    if exact.exists():
        return exact.read_text()
    matches = sorted(QUERY_DIR.glob(f"{name}-*.rq"))
    if not matches:
        raise KeyError(f"no query named {name!r}; have {query_names()}")
    if len(matches) > 1:
        raise KeyError(f"{name!r} is ambiguous: {[p.stem for p in matches]}")
    return matches[0].read_text()


def query_names() -> list[str]:
    return sorted(p.stem.split("-", 1)[0] for p in QUERY_DIR.glob("*.rq"))


def scoped(query: str, *graphs: NamedGraph | str) -> str:
    """Restrict a query to the given named graphs, merged into its default graph.

    Implemented with SPARQL dataset clauses, not by wrapping the pattern in
    ``GRAPH … UNION GRAPH …``. The difference matters: Q3 joins a concept
    hierarchy held in ``og:graph/vocab`` to fields held in ``og:graph/catalog``
    within one solution, and a union of two self-contained patterns can never
    produce that join. It returns nothing, silently, which is the worst way for
    a scoping bug to present.

    Queries themselves are written unscoped so they read clearly and can be
    pasted into a Fuseki console. Production reads are always scoped: a
    provenance walk that wandered into ``og:graph/draft`` would traverse records
    no user is entitled to see.
    """
    if not graphs:
        return query
    # Both forms, always. FROM merges the graphs into the default graph, which
    # is what a query written as a plain pattern needs. FROM alone also CLEARS
    # the named graphs, so a query using `GRAPH ?g { … }` — Q5 does, to confine
    # each crosswalk's annotations to its own scheme — would silently match
    # nothing. Emitting FROM NAMED alongside keeps both styles working.
    clauses = "\n".join(f"FROM <{g}>\nFROM NAMED <{g}>" for g in graphs)
    index = _where_index(query)
    return f"{query[:index]}{clauses}\n{query[index:]}"


def _where_index(query: str) -> int:
    """Position of the top-level WHERE keyword, skipping comments and strings."""
    depth = 0
    in_string = False
    index = 0
    while index < len(query):
        char = query[index]
        if char == "#" and not in_string:
            index = query.find("\n", index)
            if index == -1:
                break
            continue
        if char == '"':
            in_string = not in_string
        elif not in_string:
            if char in "<(":
                depth += 1
            elif char in ">)":
                depth -= 1
            elif (
                depth == 0
                and query[index : index + 5].upper() == "WHERE"
                and (index == 0 or not query[index - 1].isalnum())
            ):
                return index
        index += 1
    raise ValueError("query has no top-level WHERE clause to scope")


def run(
    store: GraphStore,
    name: str,
    params: Mapping[str, Any] | None = None,
    *,
    graphs: tuple[NamedGraph | str, ...] = (),
) -> list[Binding]:
    """Load, scope and execute a named query."""
    return store.select(scoped(load(name), *graphs), params)
