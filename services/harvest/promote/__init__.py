"""Auto-promotion: publishing what the pipeline can substantiate on its own.

PRD §7.6 says publication is per record and a steward moves ``og:reviewState``
to ``confirmed``. That is the correct default and it does not scale: 5,000
records at two minutes each is 166 hours of steward time before the first one
is published, which is the headcount constraint PRD §0 claims to have designed
around, reintroduced at the review gate.

So a record may reach a third state, ``auto-confirmed`` — distinct from
``confirmed``, and rendered as such — when the pipeline can substantiate it
without a person. The conditions are in :data:`GATES` and every one of them is
a fact about *this record*, machine-checkable, and specific:

1. it validates;
2. its licence resolved to a real identifier;
3. no distribution is known to be unreachable;
4. no model-drafted value sits in a field where a wrong value causes harm.

**There is deliberately no per-source trust list.** An earlier draft gated
promotion on marking sources trustworthy. A hand-maintained list has to be kept
correct forever, and every question it answers is answered better by the record:
a trusted source can still publish a dead link, and an untrusted one can still
publish a well-formed record with a resolved licence.

**Why this is not a shortcut.** The catalog already publishes its own
confidence — ``og:completenessLevel``, ``og:enrichmentBasis``,
``og:inferredAssignment``, separate quality facets with no composite score
(ADR-0007). An auto-confirmed level-1 record is not a claim that a human
checked it, and with ``og:reviewState`` on the record no reader can mistake it
for one. And because the catalog is republished from a git commit, an
auto-confirmed record still reaches the public site only through a merge; the
pull request is a second gate this design gets for free.

**Demotion is one field write**, which is the other half of why this is safe to
do at all.
"""

from datahub.harvest.promote.policy import (
    AUTO_CONFIRMED,
    GATES,
    Decision,
    Gate,
    PromotionResult,
    promote,
    verdict,
)

__all__ = [
    "AUTO_CONFIRMED",
    "GATES",
    "Decision",
    "Gate",
    "PromotionResult",
    "promote",
    "verdict",
]
