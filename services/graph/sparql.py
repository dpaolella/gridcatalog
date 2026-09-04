"""SPARQL text construction: prologue and safe parameter binding.

Queries are written as templates with ``?var`` placeholders bound through
:func:`bind`, which serialises RDF terms in N3 form. Never interpolate a string
into a query with an f-string; a title containing ``"}"`` is enough to change
the shape of a query.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from datahub.namespaces import PREFIXES
from rdflib import BNode, Literal, URIRef
from rdflib.term import Node

_PLACEHOLDER = re.compile(r"\?\?([A-Za-z_][A-Za-z0-9_]*)")

PROLOGUE = "\n".join(f"PREFIX {p}: <{ns}>" for p, ns in PREFIXES.items()) + "\n"


def prologue(query: str) -> str:
    """Prepend the standard prefix block unless the query declares its own."""
    if "PREFIX" in query.split("SELECT")[0].upper() and query.lstrip().upper().startswith("PREFIX"):
        return query
    return PROLOGUE + query


def to_term(value: Any) -> Node:
    """Coerce a Python value to an RDF term.

    ``str`` is deliberately *not* coerced to a URI. Ambiguity here is how a
    literal ends up in a subject position.
    """
    if isinstance(value, Node):
        return value
    if isinstance(value, bool):
        return Literal(value)
    if isinstance(value, datetime | date):
        return Literal(value)
    if isinstance(value, int | float):
        return Literal(value)
    if isinstance(value, str):
        return Literal(value)
    raise TypeError(f"cannot bind {type(value).__name__} into SPARQL: {value!r}")


def n3(value: Any) -> str:
    """N3 serialisation of a value, safe to interpolate into a query."""
    term = to_term(value)
    if isinstance(term, BNode):
        raise ValueError("blank nodes cannot be bound into a query; use a skolem IRI")
    return term.n3()


def iri(value: str | URIRef) -> str:
    """Angle-bracket an IRI, rejecting anything that would break out of it."""
    text = str(value)
    if any(c in text for c in '<>"{}|^`\\ \n\r\t'):
        raise ValueError(f"refusing to interpolate malformed IRI: {text!r}")
    return f"<{text}>"


def bind(template: str, params: Mapping[str, Any] | None = None) -> str:
    """Substitute ``??name`` placeholders in *template* with N3 terms.

    ``??name`` rather than ``?name`` so that ordinary SPARQL variables are never
    accidentally captured.

    >>> bind("SELECT * { ??s ?p ?o }", {"s": URIRef("urn:a")})
    'SELECT * { <urn:a> ?p ?o }'
    """
    params = params or {}
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            missing.append(name)
            return match.group(0)
        value = params[name]
        if isinstance(value, URIRef):
            return iri(value)
        return n3(value)

    out = _PLACEHOLDER.sub(_sub, template)
    if missing:
        raise KeyError(f"unbound SPARQL placeholders: {sorted(set(missing))}")
    return out


def values_clause(var: str, items: list[Any]) -> str:
    """A ``VALUES`` block, or a clause that matches nothing for an empty list.

    An empty allow-list must match nothing rather than everything; that
    distinction is the difference between a closed door and an open one
    (ADR-0006).
    """
    if not items:
        return f"VALUES ?{var} {{ }}"
    terms = " ".join(iri(i) if isinstance(i, URIRef) else n3(i) for i in items)
    return f"VALUES ?{var} {{ {terms} }}"
