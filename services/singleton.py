"""A process-wide singleton that is actually built once.

`functools.lru_cache` is the obvious way to write one and is wrong for this
job: it does not hold its lock across the call it is caching. N threads that
miss together each run the factory and each keep their own result, and only one
of them ends up in the cache. For a pure function that is a wasted computation.
For a *resource* — a store, an index client, a validator that parses a
vocabulary — it is a correctness bug, because the instances are not
interchangeable: a write served by one is invisible to every caller holding
another.

It surfaced as 500s. The API serves sync handlers on a threadpool, a record page
fetches its tabs in parallel, and the first such page after a restart built four
graph stores at once — which meant four rdflib SPARQL parsers running
concurrently, which rdflib does not support (see `datahub.graph.sparql.parsing`).
The crash was the lucky part; the silent divergence would have been worse.
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class Once[T]:
    """Calls *build* at most once, however many threads arrive together."""

    def __init__(self, build: Callable[[], T]) -> None:
        self._build = build
        self._lock = threading.Lock()
        self._value: T | None = None

    def __call__(self) -> T:
        # Double-checked, and safe to be so: the fast path reads one attribute,
        # which is atomic under the GIL, and a stale `None` only costs a trip
        # through the lock.
        if self._value is None:
            with self._lock:
                if self._value is None:
                    self._value = self._build()
        return self._value

    def peek(self) -> T | None:
        """The instance if one was built, without building one."""
        return self._value

    def clear(self) -> None:
        """Forget the instance. Does not close it — the caller owns that."""
        with self._lock:
            self._value = None


def once[T](build: Callable[[], T]) -> Once[T]:
    """Wrap *build* so it runs at most once per process."""
    return Once(build)
