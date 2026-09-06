"""The four gates, and the verdict they produce.

Every gate is a pure function of a record plus the link health already
recorded for it, so a decision is reproducible and explainable: the result
names the gate that refused, and that reason is what the review queue shows
instead of leaving a steward to guess.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from datahub.logging import get_logger

log = get_logger(__name__)

#: The third review state, alongside ``draft`` and ``confirmed``. Never
#: conflated with ``confirmed``: that one means a person checked it.
AUTO_CONFIRMED = "auto-confirmed"

#: Licence identifiers that mean "nobody has established the terms". A record
#: carrying one of these prefixes has a licence *field*, not a licence.
UNRESOLVED_LICENCE_MARKERS: tuple[str, ...] = (
    "LicenseRef-Unreviewed",
    "LicenseRef-Unstated",
)

#: Fields where a model-drafted value could cause real harm rather than mere
#: inaccuracy: someone reuses data they may not, or fetches from somewhere the
#: source never published. Mirrors the enricher's own forbidden set, and is the
#: reason a drafted *summary* is fine here while a drafted licence is not.
GATING_FIELDS: frozenset[str] = frozenset(
    {
        "license",
        "licenseNote",
        "redistributionAllowed",
        "commercialUseAllowed",
        "shareAlike",
        "accessURL",
        "downloadURL",
        "distribution",
        "persistentId",
        "identifier",
        "conceptDoi",
        "versionDoi",
        "upstreamSource",
        "wasDerivedFrom",
        "supersedes",
        "supersededBy",
    }
)

#: Link-health statuses that block promotion. `verified`, `redirected` and a
#: record never probed at all are all fine — see :func:`_links_not_known_dead`.
DEAD_STATUSES: frozenset[str] = frozenset({"unreachable"})


@dataclass(frozen=True, slots=True)
class Decision:
    """One gate's answer, and why."""

    gate: str
    passed: bool
    reason: str = ""


@dataclass(slots=True)
class PromotionResult:
    dataset_id: str
    promoted: bool
    decisions: list[Decision] = field(default_factory=list)

    @property
    def refusals(self) -> list[Decision]:
        return [d for d in self.decisions if not d.passed]

    @property
    def why_not(self) -> str:
        """One line naming every gate that refused, for the review queue."""
        return "; ".join(f"{d.gate}: {d.reason}" for d in self.refusals)


Gate = Callable[[dict[str, Any], Mapping[str, str]], Decision]


def _validates(record: dict[str, Any], _health: Mapping[str, str]) -> Decision:
    """The record conformed to the shapes at its own completeness level.

    Checked upstream and passed in rather than re-run here. Not for speed —
    validation measures at ~33 records/s, so it is a small share of the
    pipeline's budget — but because re-deriving it would mean this module
    owning a second opinion about whether a record is valid, and two answers
    to that question is one too many.
    """
    conforms = bool(record.get("_validation_conforms", False))
    return Decision(
        "validates",
        conforms,
        "" if conforms else "the record does not conform to the shapes at its computed level",
    )


def _licence_resolved(record: dict[str, Any], _health: Mapping[str, str]) -> Decision:
    """The licence is an identifier somebody can act on.

    A wrong licence is one of the two failure modes that actually harms a
    user — they reuse data they may not — so this is checked before anything
    else about the record's content.
    """
    licence = str(record.get("license") or "")
    if not licence:
        return Decision("licence", False, "the record states no licence")
    tail = licence.rsplit("/", 1)[-1]
    for marker in UNRESOLVED_LICENCE_MARKERS:
        if tail.startswith(marker):
            return Decision(
                "licence",
                False,
                f"the licence is unresolved ({tail}); a steward must establish the terms",
            )
    return Decision("licence", True)


def _links_not_known_dead(record: dict[str, Any], health: Mapping[str, str]) -> Decision:
    """No distribution has been probed and found unreachable.

    The other failure mode that harms a user: a catalog entry pointing at
    nothing. Note what this does **not** require — that a link has been probed
    successfully. A record whose links have never been checked is not thereby
    suspect, and requiring a successful probe would block every record on the
    first run, when nothing has been probed and nothing can be.

    So the gate is "not known dead" rather than "known live", and it tightens
    on its own as probe history accumulates: once the prober has run, a dead
    link demotes the record on the next pass.
    """
    distributions = record.get("distribution") or []
    if not isinstance(distributions, list):
        distributions = [distributions]
    if not distributions:
        return Decision("links", False, "the record lists no distribution")
    dead = [
        str(d.get("id"))
        for d in distributions
        if isinstance(d, dict) and health.get(str(d.get("id")), "") in DEAD_STATUSES
    ]
    if dead and len(dead) == len(distributions):
        return Decision("links", False, f"every distribution probed unreachable ({len(dead)})")
    return Decision("links", True)


def _no_drafted_gating_value(record: dict[str, Any], _health: Mapping[str, str]) -> Decision:
    """No model-drafted value sits in a field where a wrong one causes harm.

    The enricher already refuses to write these (``ENRICHABLE_FIELDS`` is a
    closed set filtered after the call), so this gate is a backstop rather than
    the primary control. It exists because "the enricher cannot write it" and
    "no drafted value is present" are different claims, and the one that
    matters at publication time is the second.
    """
    drafted = record.get("enrichedField") or []
    if isinstance(drafted, str):
        drafted = [drafted]
    overlap = sorted(GATING_FIELDS.intersection(str(f) for f in drafted))
    if overlap:
        return Decision(
            "drafted-values",
            False,
            f"model-drafted values in gating field(s): {', '.join(overlap)}",
        )
    return Decision("drafted-values", True)


#: Ordered, and the order is the message. Licence and links come first because
#: they are the two failure modes that actually harm somebody; a record that
#: fails one of those should say so before it says anything about its shape.
GATES: tuple[Gate, ...] = (
    _validates,
    _licence_resolved,
    _links_not_known_dead,
    _no_drafted_gating_value,
)


def _node(record: dict[str, Any]) -> dict[str, Any]:
    """The dataset node, whether the caller passed a document or a node.

    A record read back from the store is a framed JSON-LD document — a
    ``@context`` and a ``@graph`` whose first entry is the dataset — so a gate
    reading ``record["license"]`` off the top level reads nothing and every
    record is refused for stating no licence. Reusing the store's own
    ``dataset_node`` rather than re-deriving the convention, because there
    should be exactly one place that knows where a dataset lives in a document.
    """
    if "@graph" in record:
        from datahub.graph.records import dataset_node

        return dataset_node(record)
    return record


def verdict(record: dict[str, Any], health: Mapping[str, str] | None = None) -> PromotionResult:
    """Run every gate. All of them, not until the first refusal.

    A record that fails three gates should report three, because a steward
    fixing one at a time and re-running is the slowest possible way to learn
    what is wrong with it.
    """
    node = _node(record)
    decisions = [gate(node, health or {}) for gate in GATES]
    return PromotionResult(
        dataset_id=str(node.get("id") or ""),
        promoted=all(d.passed for d in decisions),
        decisions=decisions,
    )


def promote(record: dict[str, Any], health: Mapping[str, str] | None = None) -> PromotionResult:
    """Decide, and stamp ``og:reviewState`` on the record when the answer is yes.

    Takes either a whole JSON-LD document or a bare dataset node, and stamps
    the node in place either way, so a caller holding a document can hand it
    straight to ``RecordStore.put``.

    A record already ``confirmed`` by a person is never touched: a human
    judgement outranks this one, and silently restamping it as auto-confirmed
    would erase the fact that somebody looked.
    """
    node = _node(record)
    state = str(node.get("reviewState") or "draft")
    if state == "confirmed":
        return PromotionResult(
            dataset_id=str(node.get("id") or ""),
            promoted=False,
            decisions=[Decision("already-confirmed", False, "a steward has already confirmed it")],
        )

    result = verdict(node, health)
    if result.promoted:
        node["reviewState"] = AUTO_CONFIRMED
    return result
