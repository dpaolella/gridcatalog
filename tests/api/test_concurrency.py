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


def test_the_rate_limiter_counts_every_request_it_is_given(api_env):
    """`rate_limit` is a sync dependency, so FastAPI runs it on the threadpool.

    `_count` was `self._counts[key] = self._counts.get(key, 0) + cost` — a read
    and a write with a bytecode boundary between them. Two threads that read the
    same value both write one more than it, and one request goes uncounted. The
    consequence is quiet and in the wrong direction: a limit that under-counts
    lets a caller through, and it under-counts most under exactly the concurrent
    load it exists to stop.

    The dict rebuild in the same method is worse than one lost increment. It
    replaces `self._counts` wholesale, so a thread holding the old dict writes
    into an object nothing reads again — a whole window's counts, gone.

    At the default switch interval this is invisible: 8 threads incrementing
    500 times each lose nothing, because 5 ms is long enough that a thread
    finishes its loop before the interpreter preempts it. `setswitchinterval`
    makes the window the code actually has visible rather than hoping the
    scheduler cooperates — measured at 1 ns, 16 threads × 3,000 counted 14,483
    of 48,000. A test that only fails on a busy production box is not a test.
    """
    import sys
    import threading

    from datahub.api.ratelimit import RateLimiter

    limiter = RateLimiter()
    threads, per_thread = 16, 2000
    start = threading.Barrier(threads)

    def hammer() -> None:
        start.wait()
        for _ in range(per_thread):
            limiter._count("user:same", 1, 1)

    original = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    try:
        workers = [threading.Thread(target=hammer) for _ in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    finally:
        sys.setswitchinterval(original)

    counted = limiter._counts[("user:same", 1)]
    assert counted == threads * per_thread, (
        f"counted {counted} of {threads * per_thread}: increments were lost, so "
        "the limiter lets a caller past its budget"
    )
