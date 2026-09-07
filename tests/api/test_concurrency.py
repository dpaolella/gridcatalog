"""The API serves requests on a threadpool. Two things had to be true and were not.

Both bugs fired on the *first* record page after a restart and never again,
which is why nothing caught them until an end-to-end run happened to open one:
a record page fetches its tabs in parallel, FastAPI runs sync handlers in a
threadpool, and everything below is a cold-start race.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[2]


def test_the_graph_store_is_built_once_however_many_threads_ask_at_the_same_time(api_env):
    """`functools.lru_cache` does not hold its lock across the call it caches.

    So N threads that miss together each ran `make_store()` and each kept its
    own store. The 500s were the loud half; the quiet half is that a write
    served by one instance is invisible to every request that landed on
    another.
    """
    import threading

    from datahub.api import deps

    seen: list[object] = []
    start = threading.Barrier(6)

    def build() -> None:
        start.wait()
        seen.append(deps.graph_store())

    threads = [threading.Thread(target=build) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len({id(store) for store in seen}) == 1, (
        "six threads asked for the store at once and got more than one of them"
    )


# The parser race can only be observed in a process that has not parsed SPARQL
# yet: `_trim_arity` corrupts its state while discovering each parse action's
# arity, and once discovered it stops changing. By the time a pytest session
# reaches any test, rdflib has parsed a hundred queries and the window is shut.
# So this runs in a subprocess, and is written the way it was found — eight
# threads, cold, straight at `Graph.query`.
COLD_PARSE = """
import threading

from datahub.graph.sparql import parsing
from rdflib import Graph

# A spread of grammar branches, because each parse action resolves its arity
# separately and each resolution is its own window.
QUERIES = [
    "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }",
    "SELECT ?s WHERE { GRAPH ?g { ?s <urn:p>* ?o } }",
    "ASK { ?s ?p ?o FILTER(isIRI(?s)) }",
    "SELECT (COUNT(?s) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p",
]

errors: list[str] = []


def go(query: str) -> None:
    try:
        with parsing():
            Graph().query(query)
    except Exception as exc:  # noqa: BLE001 - the point is what escaped
        errors.append(f"{type(exc).__name__}: {exc}")


threads = [threading.Thread(target=go, args=(q,)) for q in QUERIES for _ in range(3)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
print(len(errors), errors[:1])
"""


@pytest.mark.parametrize("attempt", range(6))
def test_a_cold_sparql_parser_survives_eight_threads(attempt: int) -> None:
    """rdflib's SPARQL parser keeps global mutable state; ours is serialised.

    Six attempts because the race is probabilistic — unguarded, this fails in
    roughly half of them with `TypeError: expandTriples() missing 1 required
    positional argument`, out of a query that is perfectly well-formed.
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(COLD_PARSE)],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.startswith("0 "), (
        f"a cold parser raced across threads: {result.stdout.strip()}"
    )
