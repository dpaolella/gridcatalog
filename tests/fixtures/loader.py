"""Loading the record fixtures.

The corpus is used by the conformance suite, the graph suite, the search tests,
the broker tests and the MCP grounding tests, so it is loaded through one place
and cached: parsing fifteen JSON-LD documents on every test would dominate the
suite's runtime.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from rdflib import Graph

FIXTURE_DIR = Path(__file__).resolve().parent
RECORDS_DIR = FIXTURE_DIR / "records"
INVALID_DIR = FIXTURE_DIR / "invalid"
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


@functools.lru_cache(maxsize=64)
def load_record(name: str) -> dict[str, Any]:
    """A fixture document with the project context substituted in.

    Fixtures reference the context by URL so they read like a record a
    publisher would actually write. Resolving it locally keeps the suite off
    the network.
    """
    path = RECORDS_DIR / f"{name}.jsonld"
    if not path.exists():
        path = INVALID_DIR / f"{name}.jsonld"
    document = json.loads(path.read_text())
    document["@context"] = context()["@context"]
    return document


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
