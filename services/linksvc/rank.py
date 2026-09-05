"""Combining signals into a ranked, explained pairing (WP-8.1).

PRD §F6's formula, with the two rules that are *not* tunable:

* **The penalty reduces; it never zeroes or hides** (§F6.9). A correlated pair
  is floored at tier 1 and stays in the ranking. Hiding it would remove exactly
  the information the user needs — that these two are not independent — and
  leave them believing the two are unrelated, which is a stronger and more
  wrong claim than the warning would have made.
* **Every pairing carries a concrete reason.** A pairing that cannot say why it
  is a pairing is dropped rather than surfaced with a bare number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from datahub.linksvc.describe import Description, Relation
from datahub.linksvc.signals import PairSignals
from datahub.linksvc.weights import Weights


@dataclass(frozen=True, slots=True)
class Link:
    """One ranked, explained pairing."""

    source: str
    target: str
    score: float
    tier: int
    relation: Relation
    descriptor: str
    reasons: tuple[str, ...] = ()
    joinable_keys: tuple[str, ...] = ()
    shared_workflow_tags: tuple[str, ...] = ()
    warning: str | None = None
    shared_origin: str | None = None
    #: The score before the correlation penalty, and the tier the floor
    #: rescued. Both kept because "why is this ranked here" is the question a
    #: reviewer asks first, and reconstructing it from the weights is work.
    unpenalised_score: float | None = None
    floored_from: int | None = None
    contributions: dict[str, float] = field(default_factory=dict)

    @property
    def penalised(self) -> bool:
        return self.unpenalised_score is not None and self.unpenalised_score > self.score


def score(pair: PairSignals, weights: Weights, description: Description) -> Link:
    """One pairing, scored and tiered."""
    contributions = {
        name: round(weight * pair.value(name), 6) for name, weight in weights.signals.items()
    }
    raw = sum(contributions.values())

    penalty = weights.shared_origin_penalty if pair.correlated else 0.0
    penalised = max(0.0, raw + penalty)
    tier = weights.tier_for(penalised)
    floored = weights.floored(tier) if pair.correlated else tier

    return Link(
        source=pair.source,
        target=pair.target,
        score=round(penalised, 6),
        tier=floored,
        relation=description.relation,
        descriptor=description.descriptor,
        reasons=description.reasons,
        joinable_keys=tuple(pair.evidence("joinable_key_present").get("keys") or ()),
        shared_workflow_tags=tuple(pair.evidence("workflow_tag_overlap").get("shared") or ()),
        warning=description.warning,
        shared_origin=description.shared_origin,
        unpenalised_score=round(raw, 6) if pair.correlated else None,
        floored_from=tier if floored != tier else None,
        contributions=contributions,
    )


def rank(links: list[Link], pairs: dict[str, PairSignals], weights: Weights) -> list[Link]:
    """Order by strength, break ties deterministically, take the top N.

    The tie-break order comes from config and ends in the dataset id, which is
    what makes the ranking *stable*: without a total order, two runs over
    unchanged data can produce different top-12 lists, and a user who refreshes
    a page sees the suggestions move for no reason.
    """

    def key(link: Link) -> tuple[Any, ...]:
        pair = pairs.get(link.target)
        parts: list[Any] = [-link.score]
        for name in weights.tie_break:
            if name == "dataset_id":
                parts.append(link.target)
            elif pair is not None and name in pair.signals:
                parts.append(-pair.value(name))
            else:
                parts.append(0)
        parts.append(link.target)
        return tuple(parts)

    return sorted(links, key=key)[: weights.top_n]


def worth_surfacing(link: Link) -> bool:
    """PRD §F6: *a bare numeric score is not sufficient and should fail review.*

    So a pairing with nothing to say about itself is not surfaced with a number
    attached — it is not surfaced. A correlated pair is the exception and is
    always kept: the warning *is* the reason, and it is the one pairing a user
    most needs to see.
    """
    if link.warning:
        return True
    return bool(link.reasons) and link.score > 0


__all__ = ["Link", "rank", "score", "worth_surfacing"]
