"""Skolemisation: no blank nodes in the store.

A blank node is a node with no name, and a store you update is a place where
everything needs one. Three concrete failures follow from keeping them:

* ``DELETE DATA`` matches on term identity, and a blank node's label is local to
  the parse that produced it. Reading a record back and writing it again
  inserts *new* blank nodes without deleting the old ones, so the record grows
  a little each time and a round trip is not lossless. This is not theoretical;
  it is what fourteen of fifteen fixtures did before this module existed.
* The semantic layer writes to ``og:graph/computed`` about parts of a record —
  a grade about a variable, a resolution about a field. It cannot address a
  part that has no name.
* A diff between two versions of a record is unreadable when half the nodes are
  labelled ``_:Nb3f2…`` differently on each side.

RDF 1.1 §3.5 sanctions replacing blank nodes with IRIs for exactly this reason.
The names here are *derived from position* rather than from content or from a
counter, so they are stable across unrelated edits: adding a field does not
rename the bounding box. ``ds/era5#temporal`` is also legible in a SPARQL
console, which a content hash would not be.
"""

from __future__ import annotations

import hashlib
import re
from collections import deque

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import RDF

#: Predicates whose blank-node chains are numbered rather than nested. Without
#: this an rdf:List of four numbers produces ``#bbox.rest.rest.rest``.
_CHAIN_PREDICATES = frozenset({RDF.rest})

_SAFE = re.compile(r"[^A-Za-z0-9_.:-]")


def skolemize(graph: Graph, base: URIRef) -> Graph:
    """Return *graph* with every blank node replaced by an IRI under *base*.

    Names are derived from the path from a named node, so the same structure
    gets the same name every time and unrelated edits do not disturb it.
    """
    names = _assign_names(graph, base)
    if not names:
        return graph
    out = Graph()
    for prefix, namespace in graph.namespaces():
        out.bind(prefix, namespace)
    for subject, predicate, obj in graph:
        out.add(
            (
                names.get(subject, subject),  # type: ignore[arg-type]
                predicate,
                names.get(obj, obj),  # type: ignore[arg-type]
            )
        )
    return out


def is_skolem(node: object, base: URIRef | str | None = None) -> bool:
    if not isinstance(node, URIRef):
        return False
    if "#" not in str(node):
        return False
    return base is None or str(node).startswith(f"{base}#")


def _assign_names(graph: Graph, base: URIRef) -> dict[BNode, URIRef]:
    blanks = {node for node in graph.all_nodes() if isinstance(node, BNode)}
    if not blanks:
        return {}

    names: dict[BNode, URIRef] = {}
    taken: set[str] = {
        str(node).split("#", 1)[1]
        for node in graph.all_nodes()
        if isinstance(node, URIRef) and "#" in str(node)
    }

    # Breadth-first from the named nodes, in a deterministic order, so the same
    # graph always produces the same names regardless of iteration order.
    frontier: deque[URIRef] = deque(
        sorted(
            {s for s in graph.subjects() if isinstance(s, URIRef)},
            key=lambda node: (node != base, str(node)),
        )
    )
    seen: set[URIRef] = set()

    while frontier:
        subject = frontier.popleft()
        if subject in seen:
            continue
        seen.add(subject)
        for predicate in sorted(set(graph.predicates(subject)), key=str):
            objects = sorted(graph.objects(subject, predicate), key=str)
            blank_objects = [o for o in objects if isinstance(o, BNode) and o not in names]
            for index, obj in enumerate(blank_objects):
                stem = _stem(subject, predicate, base, names)
                if predicate in _CHAIN_PREDICATES:
                    stem = _next_in_chain(stem)
                elif len(blank_objects) > 1:
                    stem = f"{stem}-{index + 1}"
                names[obj] = URIRef(f"{base}#{_unique(stem, taken)}")
            for obj in objects:
                if isinstance(obj, BNode) and obj in names:
                    frontier.append(names[obj])

    # A blank node unreachable from any named node has no path to be named
    # from. Fall back to a content hash: ugly, stable, and vanishingly rare —
    # a record whose parts are unreachable from its dataset node would not
    # validate anyway.
    for orphan in sorted(blanks - set(names), key=str):
        digest = hashlib.sha256(
            "".join(f"{p}{o}" for p, o in sorted(graph.predicate_objects(orphan), key=str)).encode(
                "utf-8"
            )
        ).hexdigest()[:12]
        names[orphan] = URIRef(f"{base}#b-{_unique(digest, taken)}")

    return names


def _stem(subject: URIRef, predicate: URIRef, base: URIRef, names: dict[BNode, URIRef]) -> str:
    parent = "" if subject == base else _fragment(subject, base)
    local = _local_name(predicate)
    return f"{parent}.{local}" if parent else local


def _next_in_chain(stem: str) -> str:
    """``#bbox`` → ``#bbox_1`` → ``#bbox_2``, rather than ``#bbox.rest.rest``."""
    if "_" in stem and stem.rsplit("_", 1)[-1].isdigit():
        head, index = stem.rsplit("_", 1)
        return f"{head}_{int(index) + 1}"
    return f"{stem}_1"


def _fragment(node: URIRef, base: URIRef) -> str:
    text = str(node)
    if text.startswith(f"{base}#"):
        return text[len(str(base)) + 1 :]
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _local_name(predicate: URIRef) -> str:
    text = str(predicate)
    for separator in ("#", "/"):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    return _SAFE.sub("_", text)


def _unique(stem: str, taken: set[str]) -> str:
    candidate, counter = stem, 1
    while candidate in taken:
        counter += 1
        candidate = f"{stem}-{counter}"
    taken.add(candidate)
    return candidate
