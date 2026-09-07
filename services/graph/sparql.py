"""SPARQL text construction: prologue and safe parameter binding.

Queries are written as templates with ``?var`` placeholders bound through
:func:`bind`, which serialises RDF terms in N3 form. Never interpolate a string
into a query with an f-string; a title containing ``"}"`` is enough to change
the shape of a query.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from datahub.namespaces import PREFIXES
from rdflib import BNode, Literal, URIRef
from rdflib.term import Node

#: Serialises every rdflib SPARQL evaluation in this process.
#:
#: rdflib parses SPARQL with pyparsing, and pyparsing keeps **mutable global
#: state**: each parse action is wrapped by ``_trim_arity``, which discovers the
#: action's arity by calling it, catching ``TypeError`` and retrying with one
#: argument fewer, then remembers the answer in a closure shared by every
#: caller. Two threads parsing at once corrupt that closure — one sets
#: "arity found" while the other is still probing — and the loser gets
#: ``TypeError: expandTriples() missing 1 required positional argument`` out of
#: a query that is perfectly well-formed.
#:
#: It is a cold-start race: once every parse action has resolved its arity, the
#: state stops changing and the same code runs for hours without trouble. That
#: is exactly what makes it nasty — it fires on the first page load after a
#: deploy, on a request that would succeed on retry, and never again.
#: A record page fetches its tabs in parallel, and FastAPI runs sync handlers in
#: a threadpool, so the very first record page anybody opens is the race.
#:
#: A lock, not a warm-up: warming would mean enumerating every grammar branch
#: the project can reach and staying right as queries are added, and getting
#: that wrong restores the bug silently. Serialising costs nothing real —
#: ``RdflibStore`` already serialises its own queries, and it is the in-process
#: development backend. Production is Fuseki, where parsing happens in the
#: server and this lock is never taken.
_PARSE_LOCK = threading.RLock()


@contextmanager
def parsing() -> Iterator[None]:
    """Hold the process-wide lock around an rdflib SPARQL evaluation."""
    with _PARSE_LOCK:
        yield


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


def placeholders(template: str) -> list[str]:
    """The ``??name`` placeholders a query template expects, in order.

    So a caller can be told what a query needs before running it. An unbound
    placeholder otherwise surfaces as a SPARQL parse error naming a variable
    the caller never wrote.
    """
    seen: list[str] = []
    for match in _PLACEHOLDER.finditer(template):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


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
        if isinstance(value, str) and value.startswith(("http://", "https://", "urn:")):
            # Loud, because the alternative is silent and expensive. A string
            # that looks like an IRI binds as a literal, every join against it
            # fails to match, and the query returns an empty result that is
            # indistinguishable from "there is nothing there" — which is what
            # an empty concept scheme, an empty domain list and an empty
            # crosswalk all look like too.
            raise TypeError(
                f"parameter {name!r} looks like an IRI but is a str, so it would bind as a "
                f"literal and match nothing: {value!r}. Wrap it in URIRef(), or pass "
                "Literal() if a string literal really is what you meant."
            )
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
