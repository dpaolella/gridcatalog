"""Descriptors, typed relations and correlation warnings (WP-8.2).

PRD §F6 ends with the sentence this module exists for:

> Every surfaced pairing carries at least one concrete human-readable reason.
> A bare numeric score is not sufficient and should fail review.

So a pairing is assembled here from *evidence*, not from a score. Nothing in
this module reads the strength number: a descriptor that varied with the score
would be describing the ranking rather than the relationship, and a user who
learned that would stop reading them.

The three outputs:

* a **complementarity descriptor** — one sentence saying what the two datasets
  are to each other;
* a **typed relation** — complementary, substitute, supersedes, superseded-by,
  or derived-from;
* a **shared-origin warning**, where one applies, naming the upstream source
  and stating the modelling consequence in plain language (PRD §F6.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from datahub.api.search.document import SearchDocument
from datahub.linksvc.signals import PairSignals
from datahub.semantic.provenance import SharedOrigin

Relation = Literal[
    "complementary", "substitute", "supersedes", "superseded-by", "derived-from", "related"
]

#: Granularity pairs that make two datasets about the same thing at different
#: resolutions — the case PRD §F6.4 names directly.
_GRANULARITY_ORDER = ("point", "nodal", "zonal", "administrative", "gridded")


@dataclass(frozen=True, slots=True)
class Description:
    """What one pairing is, in words."""

    relation: Relation
    descriptor: str
    reasons: tuple[str, ...]
    warning: str | None = None
    shared_origin: str | None = None
    shared_origin_depth: tuple[int, int] | None = None


def describe(
    source: SearchDocument,
    target: SearchDocument,
    pair: PairSignals,
    *,
    derives_from: bool = False,
    derived_by: bool = False,
) -> Description:
    reasons = _reasons(source, target, pair)
    relation = _relation(source, target, pair, derives_from=derives_from, derived_by=derived_by)
    warning, origin, depths = _warning(source, target, pair)
    return Description(
        relation=relation,
        descriptor=_descriptor(source, target, pair, relation),
        reasons=tuple(reasons),
        warning=warning,
        shared_origin=origin,
        shared_origin_depth=depths,
    )


# ---------------------------------------------------------------------------
# Typed relation
# ---------------------------------------------------------------------------


def _relation(
    source: SearchDocument,
    target: SearchDocument,
    pair: PairSignals,
    *,
    derives_from: bool,
    derived_by: bool,
) -> Relation:
    """Supersession and derivation are *recorded*; the rest is inferred.

    Order matters. A record that says it supersedes another has settled the
    question, and inferring "substitute" from an overlap on top of that would
    contradict a statement the catalog holds.
    """
    if target.iri in source.supersedes or source.superseded_by == target.iri:
        return "supersedes" if target.iri in source.supersedes else "superseded-by"
    if derives_from or derived_by:
        return "derived-from"

    concepts = pair.value("concept_overlap")
    domains = {c.iri for c in source.data_domains} & {c.iri for c in target.data_domains}

    # Substitute: the same quantities, for the same places and times, from a
    # different publisher. Two nodal LMP feeds for the same market. A user
    # picks one; they do not use both.
    if (
        concepts >= 0.5
        and pair.value("geographic_overlap") >= 0.5
        and pair.value("temporal_overlap") >= 0.5
        and source.publisher != target.publisher
    ):
        return "substitute"

    # Complementary: enough shared context to be used together, without being
    # the same thing. Either a join key, a shared workflow, or a shared domain
    # with partial concept overlap.
    if (
        pair.value("joinable_key_present")
        or pair.value("workflow_tag_overlap") > 0
        or (domains and 0 < concepts < 0.5)
    ):
        return "complementary"
    return "related"


# ---------------------------------------------------------------------------
# Descriptor
# ---------------------------------------------------------------------------


def _descriptor(
    source: SearchDocument, target: SearchDocument, pair: PairSignals, relation: Relation
) -> str:
    """One sentence. Specific, or it is not worth printing.

    The generic fallback is deliberately weak-sounding: a pairing that can only
    be described as "related" *is* weakly described, and dressing it up in the
    same confident prose as a join-key match would make the strong descriptors
    worthless by association.
    """
    if relation == "supersedes":
        return f"Supersedes {target.title}, which this record replaces."
    if relation == "superseded-by":
        return f"Superseded by {target.title}. Prefer that record for new work."
    if relation == "derived-from":
        return (
            "Directly related by lineage: one of these is built from the other, so agreement "
            "between them is not independent evidence."
        )

    granularity = _granularity_phrase(source, target)
    if granularity:
        return granularity

    keys = pair.evidence("joinable_key_present").get("key_labels") or []
    if keys:
        return (
            f"Joinable on {_join(keys)}. Different measurements of the same entities, so the "
            "two can be combined row by row rather than merely compared."
        )

    workflows = pair.evidence("workflow_tag_overlap").get("shared_labels") or []
    if workflows:
        return f"Both feed: {_join(workflows)}."

    if relation == "substitute":
        return (
            f"Alternative source for the same quantities over the same coverage. "
            f"{source.publisher or 'One'} and {target.publisher or 'the other'} publish these "
            "independently; a study uses one, not both."
        )

    shared = pair.evidence("concept_overlap").get("shared_labels") or []
    if shared:
        return f"Different physics, complementary: shares {_join(shared[:3])}."

    return "Related through shared coverage; no stronger connection is recorded."


def _granularity_phrase(source: SearchDocument, target: SearchDocument) -> str | None:
    """PRD §F6.4's own example: *"Nodal versus zonal, different granularity of
    the same network."*"""
    left, right = source.spatial.granularity, target.spatial.granularity
    if not left or not right or left == right:
        return None
    if not ({left, right} <= set(_GRANULARITY_ORDER)):
        return None
    domains = {c.iri for c in source.data_domains} & {c.iri for c in target.data_domains}
    if not domains:
        return None
    return (
        f"{left.capitalize()} versus {right}: different granularity of the same subject. "
        "Useful together for validation, and not interchangeable."
    )


# ---------------------------------------------------------------------------
# Reasons
# ---------------------------------------------------------------------------


def _reasons(source: SearchDocument, target: SearchDocument, pair: PairSignals) -> list[str]:
    """Concrete, evidence-backed, and never a restatement of the score."""
    reasons: list[str] = []

    labels = pair.evidence("concept_overlap").get("shared_labels") or []
    count = pair.evidence("concept_overlap").get("shared_count") or 0
    if labels:
        reasons.append(f"Shares {count} concept(s), including {_join(labels[:3])}.")
    elif count:
        reasons.append(f"Shares {count} concept(s).")

    keys = pair.evidence("joinable_key_present").get("key_labels") or []
    if keys:
        reasons.append(f"Joinable on {_join(keys)}.")

    workflows = pair.evidence("workflow_tag_overlap").get("shared_labels") or []
    if workflows:
        reasons.append(f"Both feed {_join(workflows)}.")

    temporal = pair.evidence("temporal_overlap")
    if temporal.get("from") and temporal.get("to"):
        reasons.append(f"Coverage overlaps from {temporal['from']} to {temporal['to']}.")

    geographic = pair.evidence("geographic_overlap")
    if geographic.get("method") == "bbox-iou" and pair.value("geographic_overlap") > 0:
        reasons.append(
            f"Bounding boxes overlap over {pair.value('geographic_overlap'):.0%} of their "
            "combined extent."
        )
    elif geographic.get("shared"):
        reasons.append(f"Both cover {len(geographic['shared'])} of the same named place(s).")

    if source.spatial.granularity and source.spatial.granularity == target.spatial.granularity:
        reasons.append(f"Both are {source.spatial.granularity}.")

    return reasons


# ---------------------------------------------------------------------------
# The shared-origin warning
# ---------------------------------------------------------------------------


def _warning(
    source: SearchDocument, target: SearchDocument, pair: PairSignals
) -> tuple[str | None, str | None, tuple[int, int] | None]:
    """PRD §F6.8: name the upstream source and state the modelling consequence.

    In plain language, and specifically: "correlated" is a word a modeller will
    read past. What they will not read past is being told that the agreement
    they are about to treat as corroboration is partly one dataset agreeing
    with itself, and that their uncertainty band is therefore narrower than it
    should be.

    The depth is in the sentence because it is the difference between a warning
    that matters and one that does not. Two datasets one hop from the same
    reanalysis are barely independent; two datasets six hops away through
    different products may be independent enough for the purpose.
    """
    if not pair.shared_origins:
        return None, None, None

    origin: SharedOrigin = pair.shared_origins[0]
    name = origin.title or origin.origin.rsplit("/", 1)[-1]
    hops = (
        "both derive from it directly"
        if origin.depth_a == origin.depth_b == 1
        else (
            f"{source.title} is {_hops(origin.depth_a)} away and {target.title} "
            f"{_hops(origin.depth_b)}"
        )
    )
    return (
        (
            f"These two are not independent: both trace back to {name} — {hops}. "
            "Agreement between them is partly that source agreeing with itself, so treating "
            "them as corroborating evidence understates uncertainty. Use them together for "
            "coverage, not for validation."
        ),
        origin.origin,
        (origin.depth_a, origin.depth_b),
    )


def _hops(depth: int) -> str:
    return "one hop" if depth == 1 else f"{depth} hops"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


__all__ = ["Description", "Relation", "describe"]
