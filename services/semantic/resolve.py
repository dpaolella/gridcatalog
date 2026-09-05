"""Concept and unit resolution across all four data shapes (WP-7.1).

PRD §F4.1: resolve *per-column for tabular, per-variable for hierarchical,
per-layer for geospatial, per node and edge property for graph*. All four are
the same operation over a differently-named container, so this module reads
whichever containers a record has and treats what it finds uniformly.

The ladder, in order, from PRD §F4:

1. **Exact** — normalised name matches a `skos:prefLabel`, and the unit agrees.
2. **altLabel** — normalised name matches a `skos:altLabel`. This is the rung
   that does most of the work, because the vocabulary's altLabels are the
   column names real datasets use: `ssrd`, `swgdn`, `da_lmp`.
3. **Similarity** — above a stated threshold, over the field's name *and*
   definition. Pluggable, offline by default (see :mod:`.similarity`).
4. **Gap marker** — an explicit `og:conceptGap` with a reason. Never an
   omission, and never a guess (PRD §F4.9, rule X4).

Two properties this module is responsible for, both of them PRD §F4.8:

**An inferred assignment is marked as one.** Every resolution this module
produces carries `og:inferredAssignment true` and an `og:inferenceBasis` naming
the rung and the evidence. A source-confirmed assignment already on the record
is never touched — the resolver reports it and moves on.

**A unit disagreement is not a resolution failure.** A column in kW and a
concept whose default unit is MW are the same quantity; the conversion factor
is recorded and the assignment stands. A column in °C against a concept in MW
is a different quantity, and that *is* a failure, because agreeing on the name
while disagreeing on the quantity kind is how a plausible wrong answer is made.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from datahub.logging import get_logger
from datahub.namespaces import OG
from datahub.semantic.similarity import LexicalSimilarity, SimilarityBackend
from datahub.semantic.vocabulary import Concept, Vocabulary, normalise
from rdflib import Graph, URIRef
from rdflib.namespace import RDF

log = get_logger(__name__)

Rung = Literal["exact", "alt-label", "similarity", "gap"]

#: The containers a record may carry its resolvable parts in, and the shape
#: each implies. All four are read; a record that mixes them — a NetCDF with a
#: tabular index table — resolves both.
SHAPE_CONTAINERS: dict[str, str] = {
    "hasField": "tabular",
    "hasVariable": "hierarchical",
    "hasLayer": "geospatial",
    "hasNodeType": "graph",
    "hasEdgeType": "graph",
}

#: Used only when a similarity backend declares no threshold of its own. Each
#: backend normally carries one, because scores from token overlap and from
#: embedding cosine are not on the same scale (see :mod:`.similarity`).
#:
#: PRD §F4: *never guess past the threshold.* Whatever the number, the rule it
#: enforces is that a wrong concept is worse than a gap — a gap is visible and
#: a wrong concept is confidently invisible.
DEFAULT_THRESHOLD = 0.45

#: The confidence recorded for the deterministic rungs. Not 1.0 for the
#: altLabel rung: an altLabel match is a vocabulary author's judgement that a
#: name denotes a concept, which is strong evidence and not proof.
EXACT_CONFIDENCE = 1.0
ALT_LABEL_CONFIDENCE = 0.9

#: How far ahead the best candidate must be before the resolver treats it as
#: the answer rather than as one of several. A near-tie is a decision the
#: resolver is not entitled to make.
MARGIN = 0.05


@dataclass(frozen=True, slots=True)
class Part:
    """One resolvable part of a record: a column, a variable, a layer, a property."""

    iri: str
    shape: str
    local_name: str | None = None
    label: str | None = None
    definition: str | None = None
    unit: str | None = None
    unit_as_stated: str | None = None
    concept: str | None = None
    """Already present on the record. Source-confirmed unless
    ``inferred`` says otherwise."""
    inferred: bool = False
    has_gap: bool = False
    geometry_type: str | None = None
    crs: str | None = None
    value_basis: str | None = None
    data_type: str | None = None
    has_code_list: bool = False
    """A controlled vocabulary governs this field's values (C9). Such a field
    has no unit by nature, and the documentation grader must not mark it down
    for lacking one."""

    @property
    def text(self) -> str:
        """What the similarity rung reads. Name, label and definition together —
        a column called ``p_nom`` is unmatchable on its name and obvious from
        its definition."""
        return " ".join(p for p in (self.local_name, self.label, self.definition) if p)

    @property
    def is_geometry(self) -> bool:
        """Geometry columns are graded separately from attribute columns
        (PRD §F5), so they have to be identifiable here."""
        return bool(self.geometry_type) or (self.data_type or "").lower() in {
            "geometry",
            "geography",
        }


@dataclass(frozen=True, slots=True)
class Resolution:
    """What the resolver decided about one part, and why."""

    part: Part
    rung: Rung
    concept: str | None = None
    confidence: float = 0.0
    basis: str = ""
    unit: str | None = None
    unit_factor: float | None = None
    """Factor converting the part's stated unit to the concept's default unit.
    ``None`` means no conversion was needed or none could be established."""
    gap_reason: str | None = None
    alternatives: tuple[str, ...] = ()
    """Concepts that matched equally well. Non-empty means the resolver
    declined rather than picked."""

    @property
    def resolved(self) -> bool:
        return self.concept is not None


@dataclass
class ResolutionReport:
    """Every part of one record, resolved or explicitly not."""

    dataset_iri: str
    resolutions: list[Resolution] = field(default_factory=list)

    @property
    def resolved(self) -> list[Resolution]:
        return [r for r in self.resolutions if r.resolved]

    @property
    def gaps(self) -> list[Resolution]:
        return [r for r in self.resolutions if not r.resolved]

    @property
    def coverage(self) -> float:
        """Fraction of parts carrying a concept, counting pre-existing ones.

        Zero parts is 0.0, not 1.0. A record with no fields has not resolved
        everything; it has nothing to resolve, and reporting perfect coverage
        for it would make the metric useless as a level-3 signal.
        """
        if not self.resolutions:
            return 0.0
        return len(self.resolved) / len(self.resolutions)

    def summary(self) -> dict[str, object]:
        return {
            "dataset": self.dataset_iri,
            "parts": len(self.resolutions),
            "resolved": len(self.resolved),
            "gaps": len(self.gaps),
            "by_rung": {
                rung: sum(1 for r in self.resolutions if r.rung == rung)
                for rung in ("exact", "alt-label", "similarity", "gap")
            },
        }


class Resolver:
    """Resolves a record's parts to concept and unit IRIs.

    Stateless with respect to records: the same resolver runs over the whole
    catalog, and running it twice over the same record produces the same
    answer. That is not incidental — ``og:lastComputedAt`` is only meaningful
    if a recompute that changes nothing writes nothing.
    """

    def __init__(
        self,
        vocabulary: Vocabulary,
        *,
        similarity: SimilarityBackend | None = None,
        threshold: float | None = None,
    ) -> None:
        self.vocabulary = vocabulary
        self.similarity = similarity or LexicalSimilarity()
        self.threshold = (
            threshold
            if threshold is not None
            else getattr(self.similarity, "threshold", DEFAULT_THRESHOLD)
        )

    # -- reading -----------------------------------------------------------

    def parts(self, graph: Graph, dataset_iri: URIRef) -> list[Part]:
        """Every resolvable part of a record, whatever shape it is in."""
        out: list[Part] = []
        for term, shape in SHAPE_CONTAINERS.items():
            for node in graph.objects(dataset_iri, OG[term]):
                # A blank node here means the record was not skolemised
                # (ADR-0008), and a part with no stable name cannot carry a
                # resolution back to the record anyway.
                if isinstance(node, URIRef):
                    out.append(_part(graph, node, shape))
        return sorted(out, key=lambda p: p.iri)

    # -- resolving ---------------------------------------------------------

    def resolve_record(self, graph: Graph, dataset_iri: URIRef) -> ResolutionReport:
        report = ResolutionReport(dataset_iri=str(dataset_iri))
        for part in self.parts(graph, dataset_iri):
            report.resolutions.append(self.resolve(part))
        return report

    def resolve(self, part: Part) -> Resolution:
        """Walk the ladder for one part."""
        if part.concept and not part.inferred:
            # Source-confirmed. PRD §F4.8 requires the distinction to survive,
            # and ADR-0005 forbids overwriting a confirmed value with a drafted
            # one. Reported at full confidence so the record's own assertion
            # ranks above anything this module could produce.
            return Resolution(
                part=part,
                rung="exact",
                concept=part.concept,
                confidence=EXACT_CONFIDENCE,
                basis="source-confirmed on the record",
                unit=part.unit,
            )

        for attempt in (self._exact, self._alt_label):
            found = attempt(part)
            if isinstance(found, Resolution):
                return found
            if found is not None:
                # The name matched several concepts. Rather than stopping at a
                # gap, try to break the tie with the evidence the label rungs
                # cannot see — the field's definition — but only among the
                # concepts that already matched by name. Widening the candidate
                # set here would let similarity overrule an exact label match,
                # which is the wrong precedence.
                return self._disambiguate(part, found)

        return self._similar(part) or self._gap(part)

    # -- the rungs ---------------------------------------------------------

    def _exact(self, part: Part) -> Resolution | list[Concept] | None:
        """Normalised name matches a prefLabel, and the unit does not contradict."""
        for name in _names(part):
            key = normalise(name)
            matches = [
                c
                for c in self.vocabulary.matching(name)
                if not c.abstract and normalise(c.pref_label) == key
            ]
            found = self._decide(
                part, matches, "exact", EXACT_CONFIDENCE, f"prefLabel matches {key!r}"
            )
            if found is not None:
                return found
        return None

    def _alt_label(self, part: Part) -> Resolution | list[Concept] | None:
        for name in _names(part):
            matches = [c for c in self.vocabulary.matching(name) if not c.abstract]
            found = self._decide(
                part,
                matches,
                "alt-label",
                ALT_LABEL_CONFIDENCE,
                f"altLabel matches {normalise(name)!r}",
            )
            if found is not None:
                return found
        return None

    def _similar(self, part: Part) -> Resolution | None:
        text = part.text
        if not text:
            return None
        scored = [
            (self.similarity.score(text, concept), concept)
            for concept in self.vocabulary.candidates()
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1].iri))
        if not scored or scored[0][0] < self.threshold:
            return None

        best_score, best = scored[0]
        # A near-tie is a decision the resolver is not entitled to make. The
        # margin is what separates "this concept" from "one of these two", and
        # picking the first would produce a confident wrong answer roughly half
        # the time it fires.
        tied = [c for score, c in scored[1:] if best_score - score < MARGIN]
        if tied:
            return Resolution(
                part=part,
                rung="gap",
                gap_reason=(
                    f"{len(tied) + 1} concepts score within {MARGIN} of each other "
                    f"({', '.join(sorted([best.pref_label, *(c.pref_label for c in tied)]))}). "
                    "Resolving to one of them would be a guess."
                ),
                alternatives=tuple(sorted([best.iri, *(c.iri for c in tied)])),
                basis=self.similarity.name,
            )
        return self._accept(part, best, "similarity", best_score, f"{self.similarity.name} scored")

    def _gap(self, part: Part) -> Resolution:
        name = part.local_name or part.label or part.iri
        return Resolution(
            part=part,
            rung="gap",
            gap_reason=(
                f"No concept in the vocabulary matches {name!r} by label, and no candidate "
                f"scored above the {self.threshold:.2f} confidence threshold. Recorded as a "
                "gap rather than resolved to the nearest concept."
            ),
            basis=self.similarity.name,
        )

    # -- shared decision logic --------------------------------------------

    def _decide(
        self,
        part: Part,
        matches: list[Concept],
        rung: Rung,
        confidence: float,
        basis: str,
    ) -> Resolution | list[Concept] | None:
        """A resolution, a candidate list to disambiguate, or ``None`` for no match."""
        if not matches:
            return None

        compatible = [c for c in matches if self._unit_verdict(part, c) != "wrong-quantity"]
        if not compatible:
            # The name agrees and the quantity does not. That is the most
            # dangerous case in the whole ladder — a plausible wrong answer —
            # so it produces a gap that says exactly what happened.
            return Resolution(
                part=part,
                rung="gap",
                gap_reason=(
                    f"{matches[0].pref_label!r} matches by name, but the field's unit "
                    f"({part.unit_as_stated or part.unit}) is a different quantity kind. "
                    "Resolving on the name alone would assert a physical claim the data "
                    "does not support."
                ),
                basis=basis,
            )
        if len(compatible) > 1:
            return compatible
        return self._accept(part, compatible[0], rung, confidence, basis)

    def _disambiguate(self, part: Part, candidates: list[Concept]) -> Resolution:
        """Break a label tie using the field's definition, or record the tie.

        Scored against the same threshold as the similarity rung and with the
        same margin rule. A tie that similarity cannot separate is a gap naming
        every candidate — which is a more useful record than either a guess or
        a bare "no match", because it tells a steward exactly what to decide.

        **A definition is required.** The labels already tied, so re-scoring the
        name against the same labels adds no information — it just re-ranks the
        tie by which concept happens to share more tokens with it, and returns
        an answer that looks reasoned. Without new evidence the tie stands.
        """
        scored = sorted(
            ((self.similarity.score(part.text, c), c) for c in candidates),
            key=lambda pair: (-pair[0], pair[1].iri),
        )
        best_score, best = scored[0]
        runner_up = scored[1][0]
        if part.definition and best_score >= self.threshold and best_score - runner_up >= MARGIN:
            return self._accept(
                part,
                best,
                "similarity",
                best_score,
                f"{len(candidates)} concepts share the label; {self.similarity.name} separated "
                f"them on the field's definition",
            )
        return Resolution(
            part=part,
            rung="gap",
            gap_reason=(
                f"{len(candidates)} concepts claim the label "
                f"{normalise(part.local_name or part.label or '')!r}: "
                f"{', '.join(sorted(c.pref_label for c in candidates))}. "
                + (
                    "Neither the field's name nor its definition distinguishes them."
                    if part.definition
                    else "The field carries no definition, so there is nothing to separate them by."
                )
            ),
            alternatives=tuple(sorted(c.iri for c in candidates)),
            basis=self.similarity.name,
        )

    def _accept(
        self, part: Part, concept: Concept, rung: Rung, confidence: float, basis: str
    ) -> Resolution:
        factor = self._unit_factor(part, concept)
        detail = basis
        if factor is not None and factor != 1.0:
            detail = f"{basis}; unit converts by a factor of {factor:g} to the concept's default"
        return Resolution(
            part=part,
            rung=rung,
            concept=concept.iri,
            confidence=round(confidence, 4),
            basis=detail,
            unit=part.unit or concept.default_unit,
            unit_factor=factor,
        )

    def _unit_verdict(self, part: Part, concept: Concept) -> str:
        """``agrees``, ``convertible``, ``wrong-quantity`` or ``unknown``."""
        stated = self.vocabulary.unit(part.unit) or self.vocabulary.unit_from_text(
            part.unit_as_stated
        )
        expected = self.vocabulary.unit(concept.default_unit)
        if stated is None or expected is None:
            return "unknown"
        if stated.iri == expected.iri:
            return "agrees"
        if stated.quantity_kind != expected.quantity_kind:
            return "wrong-quantity"
        return "convertible"

    def _unit_factor(self, part: Part, concept: Concept) -> float | None:
        stated = self.vocabulary.unit(part.unit) or self.vocabulary.unit_from_text(
            part.unit_as_stated
        )
        expected = self.vocabulary.unit(concept.default_unit)
        if stated is None or expected is None or stated.iri == expected.iri:
            return None
        return stated.si_factor_to(expected)


# ---------------------------------------------------------------------------
# Reading a part out of the graph
# ---------------------------------------------------------------------------


def _part(graph: Graph, node: URIRef, shape: str) -> Part:
    return Part(
        iri=str(node),
        shape=shape,
        local_name=_text(graph.value(node, OG.localName)),
        label=_text(graph.value(node, OG.label)),
        definition=_text(graph.value(node, OG.definition)),
        unit=_text(graph.value(node, OG.unit)),
        unit_as_stated=_text(graph.value(node, OG.unitAsStated)),
        concept=_text(graph.value(node, OG.concept)),
        inferred=bool(graph.value(node, OG.inferredAssignment)),
        has_gap=graph.value(node, OG.conceptGap) is not None,
        geometry_type=_text(graph.value(node, OG.fieldGeometryType)),
        crs=_text(graph.value(node, OG.fieldCRS)),
        value_basis=_text(graph.value(node, OG.valueBasis)),
        data_type=_text(graph.value(node, OG.dataType)),
        has_code_list=graph.value(node, OG.codeList) is not None,
    )


def _names(part: Part) -> list[str]:
    """The names a label rung tries, local name first.

    Both, not one. The local name is what the data actually calls the field and
    is the more reliable signal when it matches; the human label is often the
    only intelligible one. EIA-930 calls its demand column ``D`` and labels it
    "Demand" — a resolver that tried only the local name would leave the most
    obvious column in the dataset unresolved.
    """
    seen: list[str] = []
    for name in (part.local_name, part.label):
        if name and name not in seen:
            seen.append(name)
    return seen


def _text(node: object) -> str | None:
    return str(node) if node is not None else None


def dataset_iris(graph: Graph) -> Iterator[URIRef]:
    """Every dataset subject in a graph. Convenience for batch passes."""
    from datahub.namespaces import DCAT

    for subject in graph.subjects(RDF.type, DCAT.Dataset):
        if isinstance(subject, URIRef):
            yield subject


__all__ = [
    "ALT_LABEL_CONFIDENCE",
    "DEFAULT_THRESHOLD",
    "EXACT_CONFIDENCE",
    "MARGIN",
    "SHAPE_CONTAINERS",
    "Part",
    "Resolution",
    "ResolutionReport",
    "Resolver",
    "dataset_iris",
]
