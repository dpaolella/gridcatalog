"""The concept and unit index the resolver matches against (WP-7.1).

Built once from the vocabulary graph and then queried in memory. Resolution
touches every field of every record — tens of thousands of lookups in a full
recompute — and a SPARQL query per field would make the pass quadratic in the
size of a vocabulary that fits comfortably in a dictionary.

The index is derived state in the strict sense: drop it and rebuild it from
``og:graph/vocab`` and nothing is lost.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from datahub.graph.graphs import NamedGraph
from datahub.graph.store import GraphStore
from datahub.namespaces import OG, QUDT
from rdflib import Graph, URIRef
from rdflib.namespace import RDFS, SKOS

#: Tokens that carry no discriminating meaning in a column name. Stripped
#: before matching so ``wind_speed_value`` and ``windspeed`` agree.
NOISE_TOKENS: frozenset[str] = frozenset(
    {
        "value",
        "values",
        "val",
        "data",
        "field",
        "col",
        "column",
        "avg",
        "mean",
        "the",
        "of",
        "per",
        "in",
        "at",
    }
)

#: The scheme a *field* may resolve to. The catalog holds five concept schemes
#: and only one of them describes quantities: data-domain, analysis-type,
#: access-restriction and provenance-class describe *datasets*.
#:
#: This is not a tidiness rule. Without it a column called ``D`` resolves to the
#: data domain ``DD4`` on an altLabel match — a confident, plausible, entirely
#: wrong answer of exactly the kind the resolution ladder exists to prevent.
RESOLVABLE_SCHEMES: frozenset[str] = frozenset({"https://schema.opengrid.org/concept/grid-concept"})

_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
#: Letters and digits are separate tokens, so ``wind_speed_100m`` and
#: ``WindSpeed100m`` produce the same key. Without this the camelCase split
#: alone yields ``speed100m`` from one and ``speed``, ``100m`` from the other,
#: and the two never match.
_DIGIT_EDGE = re.compile(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")


def normalise(text: str) -> str:
    """A comparable form of a name.

    Case-folded, camelCase split, punctuation and underscores collapsed, noise
    tokens dropped, remaining tokens sorted. Sorting is what makes
    ``speed_wind`` and ``wind_speed`` the same key — column naming order is a
    house style, not a distinction.
    """
    return " ".join(tokens(text))


def tokens(text: str) -> list[str]:
    spaced = _DIGIT_EDGE.sub(" ", _CAMEL.sub(" ", text or ""))
    parts = [p for p in _SPLIT.split(spaced.lower()) if p]
    kept = [p for p in parts if p not in NOISE_TOKENS]
    return sorted(kept or parts)


@dataclass(frozen=True, slots=True)
class Unit:
    """One entry of the unit registry."""

    iri: str
    label: str | None = None
    symbol: str | None = None
    quantity_kind: str | None = None
    multiplier: float | None = None
    offset: float = 0.0

    def si_factor_to(self, other: Unit) -> float | None:
        """The factor converting a value in this unit to *other*.

        ``None`` when the two are not comparable — different quantity kinds, a
        missing multiplier, or an offset scale where a single factor would be
        wrong. Returning 1.0 for "don't know" is how a megawatt column silently
        becomes a kilowatt one.
        """
        if self.quantity_kind != other.quantity_kind:
            return None
        if self.multiplier is None or other.multiplier is None or not other.multiplier:
            return None
        if self.offset or other.offset:
            return None
        return self.multiplier / other.multiplier


# No ``slots=True`` here, unlike the other records in this module: the two
# cached properties below are computed once per concept and read once per field
# of every record, and ``cached_property`` needs an instance ``__dict__``.
@dataclass(frozen=True)
class Concept:
    """One SKOS concept, with the labels and unit expectation resolution needs."""

    iri: str
    pref_label: str
    definition: str | None = None
    alt_labels: tuple[str, ...] = ()
    broader: tuple[str, ...] = ()
    scheme: str | None = None
    default_unit: str | None = None
    quantity_kind: str | None = None
    abstract: bool = False
    categorical: bool = False
    is_join_key: bool = False
    scope_note: str | None = None

    @cached_property
    def keys(self) -> frozenset[str]:
        """Every normalised form this concept answers to."""
        return frozenset(normalise(t) for t in (self.pref_label, *self.alt_labels) if t)

    @cached_property
    def definition_tokens(self) -> frozenset[str]:
        return frozenset(tokens(f"{self.pref_label} {self.definition or ''}"))


@dataclass
class Vocabulary:
    """Concepts and units, indexed for lookup.

    ``by_key`` maps a normalised label to every concept claiming it. A list
    rather than a single concept because collisions are real and silently
    picking one is the failure this whole module exists to avoid: ``capacity``
    is an altLabel of both installed capacity and line rating, and a resolver
    that picked the first would be right half the time and confident always.
    """

    concepts: dict[str, Concept] = field(default_factory=dict)
    units: dict[str, Unit] = field(default_factory=dict)
    by_key: dict[str, list[str]] = field(default_factory=dict)
    #: Schemes a field may resolve *to*. Concepts outside them are still held —
    #: a record refers to its data domains and access restriction by IRI, and
    #: those need labels — they are simply never resolution targets.
    resolvable_schemes: frozenset[str] = RESOLVABLE_SCHEMES
    _unit_by_symbol: dict[str, str] = field(default_factory=dict)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_store(cls, store: GraphStore, **kwargs: Any) -> Vocabulary:
        graph = store.get_graph(NamedGraph.VOCAB)
        return cls.from_graph(graph, **kwargs)

    @classmethod
    def from_graph(cls, graph: Graph, **kwargs: Any) -> Vocabulary:
        vocab = cls(**kwargs)
        # `isinstance` rather than a cast: rdflib's iterators are typed as
        # `Node`, and a blank node reaching here would be a vocabulary that has
        # not been skolemised (ADR-0008) — which should be skipped, not
        # silently treated as a concept with an unusable identifier.
        for subject in set(graph.subjects(SKOS.inScheme, None)):
            if not isinstance(subject, URIRef):
                continue
            concept = _concept(graph, subject)
            if concept is not None:
                vocab.add(concept)
        for subject in set(graph.subjects(QUDT.hasQuantityKind, None)):
            if isinstance(subject, URIRef):
                vocab.add_unit(_unit(graph, subject))
        return vocab

    def add(self, concept: Concept) -> None:
        self.concepts[concept.iri] = concept
        if not self.is_resolvable(concept):
            return
        for key in concept.keys:
            self.by_key.setdefault(key, []).append(concept.iri)

    def is_resolvable(self, concept: Concept) -> bool:
        return not self.resolvable_schemes or concept.scheme in self.resolvable_schemes

    def add_unit(self, unit: Unit) -> None:
        self.units[unit.iri] = unit
        for form in (unit.symbol, unit.label):
            if form:
                self._unit_by_symbol.setdefault(normalise(form), unit.iri)

    # -- lookup ------------------------------------------------------------

    def get(self, iri: str | URIRef | None) -> Concept | None:
        return self.concepts.get(str(iri)) if iri else None

    def unit(self, iri: str | URIRef | None) -> Unit | None:
        return self.units.get(str(iri)) if iri else None

    def unit_from_text(self, text: str | None) -> Unit | None:
        """Best effort at a unit IRI from a stated unit string.

        Deliberately conservative: an unrecognised string yields ``None`` and
        the field keeps its ``unitAsStated``. Guessing that ``MWh`` written in
        a header means megawatt-hours is safe; guessing what ``units: 1``
        means is not.
        """
        if not text:
            return None
        iri = self._unit_by_symbol.get(normalise(text))
        return self.units.get(iri) if iri else None

    def matching(self, name: str) -> list[Concept]:
        """Concepts whose pref or alt label normalises to the same key."""
        return [self.concepts[i] for i in self.by_key.get(normalise(name), ())]

    def ancestors(self, iri: str) -> list[str]:
        """Every broader concept, transitively, nearest first.

        Cycle-safe: a vocabulary edit that makes A broader than B broader than A
        should produce a wrong hierarchy, not a hung recompute.
        """
        out: list[str] = []
        seen = {iri}
        queue = list(self.concepts[iri].broader) if iri in self.concepts else []
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            out.append(current)
            concept = self.concepts.get(current)
            if concept:
                queue.extend(concept.broader)
        return out

    def __len__(self) -> int:
        return len(self.concepts)

    def __iter__(self) -> Iterator[Concept]:
        return iter(self.concepts.values())

    def candidates(self, exclude_abstract: bool = True) -> Iterable[Concept]:
        """Concepts a field may resolve *to*.

        Abstract concepts are excluded. They exist to hold a subtree together —
        ``solarIrradiance`` has no unit of its own and no dataset measures it —
        and resolving a column to one loses the distinction the subtree was
        created to make.
        """
        for concept in self.concepts.values():
            if exclude_abstract and concept.abstract:
                continue
            if not self.is_resolvable(concept):
                continue
            yield concept


def _concept(graph: Graph, subject: URIRef) -> Concept | None:
    pref = graph.value(subject, SKOS.prefLabel)
    if pref is None:
        return None
    return Concept(
        iri=str(subject),
        pref_label=str(pref),
        scheme=_text(graph.value(subject, SKOS.inScheme)),
        definition=_text(graph.value(subject, SKOS.definition)),
        alt_labels=tuple(str(o) for o in graph.objects(subject, SKOS.altLabel)),
        broader=tuple(str(o) for o in graph.objects(subject, SKOS.broader)),
        default_unit=_text(graph.value(subject, OG.defaultUnit)),
        quantity_kind=_text(graph.value(subject, OG.quantityKind)),
        abstract=bool(graph.value(subject, OG.abstract)),
        categorical=bool(graph.value(subject, OG.isCategorical)),
        is_join_key=bool(graph.value(subject, OG.isJoinKey)),
        scope_note=_text(graph.value(subject, SKOS.scopeNote)),
    )


def _unit(graph: Graph, subject: URIRef) -> Unit:
    multiplier = graph.value(subject, OG.conversionMultiplier)
    offset = graph.value(subject, OG.conversionOffset)
    return Unit(
        iri=str(subject),
        label=_text(graph.value(subject, RDFS.label)),
        symbol=_text(graph.value(subject, QUDT.symbol)),
        quantity_kind=_text(graph.value(subject, QUDT.hasQuantityKind)),
        multiplier=float(multiplier) if multiplier is not None else None,
        offset=float(offset) if offset is not None else 0.0,
    )


def _text(node: object) -> str | None:
    return str(node) if node is not None else None


__all__ = [
    "NOISE_TOKENS",
    "RESOLVABLE_SCHEMES",
    "Concept",
    "Unit",
    "Vocabulary",
    "normalise",
    "tokens",
]
