"""Loading the record fixtures.

The corpus is used by the conformance suite, the graph suite, the search tests,
the broker tests and the MCP grounding tests, so it is loaded through one place
and cached: parsing fifteen JSON-LD documents on every test would dominate the
suite's runtime.

Cached, and copied on the way out. See :func:`load_record` for why the second
half is not optional.
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path
from typing import Any

from rdflib import Graph

FIXTURE_DIR = Path(__file__).resolve().parent
RECORDS_DIR = FIXTURE_DIR / "records"
INVALID_DIR = FIXTURE_DIR / "invalid"
REGISTRY_DIR = FIXTURE_DIR / "registry"
CONTEXT_PATH = FIXTURE_DIR.parents[1] / "schemas" / "opengrid-datahub.jsonld"


@functools.lru_cache(maxsize=1)
def context() -> dict[str, Any]:
    return json.loads(CONTEXT_PATH.read_text())


@functools.lru_cache(maxsize=1)
def record_names() -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in RECORDS_DIR.glob("*.jsonld")))


@functools.lru_cache(maxsize=1)
def invalid_names() -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in INVALID_DIR.glob("*.jsonld")))


@functools.lru_cache(maxsize=1)
def registry_names() -> tuple[str, ...]:
    """The registry fixtures: studies, assumption sets, reference models, runs.

    Kept in their own directory rather than beside the dataset records because
    they are a different kind of thing — the Hub holds their bytes rather than
    pointing at somebody else's — and because every test that walks
    `record_names()` assumes a `dcat:Dataset` at the top, which a study is not.
    """
    return tuple(sorted(p.stem for p in REGISTRY_DIR.glob("*.jsonld")))


@functools.lru_cache(maxsize=64)
def _cached_record(name: str) -> dict[str, Any]:
    path = RECORDS_DIR / f"{name}.jsonld"
    if not path.exists():
        path = INVALID_DIR / f"{name}.jsonld"
    if not path.exists():
        path = REGISTRY_DIR / f"{name}.jsonld"
    document = json.loads(path.read_text())
    document["@context"] = context()["@context"]
    return document


def load_record(name: str) -> dict[str, Any]:
    """A fixture document with the project context substituted in.

    Fixtures reference the context by URL so they read like a record a
    publisher would actually write. Resolving it locally keeps the suite off
    the network.

    **A fresh copy every call.** This used to be the `lru_cache` itself, which
    hands every caller the same mutable dict — so one test doing
    `load_record("ecmwf-era5")["@graph"][0]["reviewState"] = "draft"` changed
    what every later test in the session loaded, and ERA5 went to the draft
    graph everywhere. That failed 63 tests across the projector, link service,
    semantic layer, snapshot exporter and SDK, all of them assuming ERA5 is
    published — and only under an ordering that put the mutating module first,
    so it passed in isolation and passed with `-p no:randomly`.

    The cache stays: it is the file read and the context substitution that are
    worth avoiding, and neither is what made it unsafe. Callers get their own
    copy, so mutating a fixture is a local act again.
    """
    return copy.deepcopy(_cached_record(name))


@functools.lru_cache(maxsize=64)
def load_graph(name: str) -> Graph:
    graph = Graph()
    graph.parse(data=json.dumps(load_record(name)), format="json-ld")
    return graph


def dataset_node(name: str) -> dict[str, Any]:
    """The dcat:Dataset node of a fixture, as opposed to its distributions."""
    document = load_record(name)
    nodes = document.get("@graph", [document])
    for node in nodes:
        if node.get("type") == "Dataset":
            return node
    raise KeyError(f"fixture {name} has no Dataset node")


def declared_level(name: str) -> int:
    return int(dataset_node(name).get("completenessLevel", 1))


def all_records() -> list[str]:
    return list(record_names())


def records_at_level(level: int) -> list[str]:
    return [n for n in record_names() if declared_level(n) == level]


def records_in_domain(domain: str) -> list[str]:
    suffix = f"/data-domain/{domain}"
    return [
        n
        for n in record_names()
        if any(str(d).endswith(suffix) for d in dataset_node(n).get("dataDomain", []))
    ]


def corpus_graph() -> Graph:
    """Every valid record in one graph. Used by the Q1-Q5 suite."""
    graph = Graph()
    for name in record_names():
        graph += load_graph(name)
    return graph
