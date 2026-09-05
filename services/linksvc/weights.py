"""Link weights, read from config (PRD §F6).

*Put the weights in config, not code. They will change.* — PRD §F6.

They will change because they are tuned against the golden set, and a tuning
pass that requires a code review and a release is a tuning pass nobody does.
What is *not* in config is the shape of the formula, the floor, or the rule
that a correlation warning reduces a pairing rather than hiding it: those are
requirements, and a requirement expressed as a tunable number is a requirement
somebody will tune away.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from datahub.config import get_settings

#: The signals the formula combines. Named here so a weights file that adds a
#: key nobody computes, or drops one that is computed, fails loudly at load
#: rather than quietly contributing zero to every score.
SIGNAL_NAMES: tuple[str, ...] = (
    "concept_overlap",
    "geographic_overlap",
    "temporal_overlap",
    "joinable_key_present",
    "workflow_tag_overlap",
    "quality_contribution",
)

#: Not a signal — a penalty. Kept separate because it is subtracted and floored
#: rather than summed, and because folding it into the signal list would let a
#: future edit turn it positive without anything noticing.
PENALTY_NAME = "shared_origin_penalty"


@dataclass(frozen=True)
class Weights:
    """One loaded weights file."""

    version: int
    signals: dict[str, float]
    shared_origin_penalty: float
    shared_origin_floor_tier: int
    tiers: dict[int, float]
    max_candidates_per_dataset: int
    top_n: int
    tie_break: tuple[str, ...] = ()
    _path: Path | None = field(default=None, compare=False)

    def tier_for(self, score: float) -> int:
        """Map a score onto the 5-point scale. Inclusive lower bound."""
        tier = 1
        for level, floor in sorted(self.tiers.items()):
            if score >= floor:
                tier = level
        return tier

    def floored(self, tier: int) -> int:
        """A penalised pairing never falls out of the ranking entirely.

        PRD §F6.9: *reduce, never zero out or hide.* Hiding a correlated pair
        removes exactly the information the user needs — that these two
        datasets are not independent — and leaves them believing the two are
        unrelated, which is a stronger and more wrong claim than the one the
        warning would have made.
        """
        return max(tier, self.shared_origin_floor_tier)


def load(path: Path | None = None) -> Weights:
    """Read a weights file, validating that it matches the code that uses it."""
    path = path or get_settings().link_weights_path
    data: dict[str, Any] = yaml.safe_load(Path(path).read_text())

    raw = dict(data.get("weights") or {})
    penalty = raw.pop(PENALTY_NAME, None)
    if penalty is None:
        raise ValueError(
            f"{path}: no {PENALTY_NAME}; a link config without one silently drops "
            "the correlation warning from every score"
        )
    if penalty > 0:
        raise ValueError(
            f"{path}: {PENALTY_NAME} is {penalty}, which would make a shared origin *strengthen* "
            "a pairing. It is a penalty (PRD §F6.9)."
        )

    missing = set(SIGNAL_NAMES) - set(raw)
    unknown = set(raw) - set(SIGNAL_NAMES)
    if missing or unknown:
        raise ValueError(
            f"{path}: weights do not match the signals the ranker computes. "
            f"missing={sorted(missing)} unknown={sorted(unknown)}"
        )

    return Weights(
        version=int(data.get("version", 1)),
        signals={name: float(raw[name]) for name in SIGNAL_NAMES},
        shared_origin_penalty=float(penalty),
        shared_origin_floor_tier=int(data.get("shared_origin_floor_tier", 1)),
        tiers={int(k): float(v) for k, v in (data.get("tiers") or {}).items()},
        max_candidates_per_dataset=int(data.get("max_candidates_per_dataset", 200)),
        top_n=int(data.get("top_n", 12)),
        tie_break=tuple(data.get("tie_break") or ()),
        _path=Path(path),
    )


@functools.lru_cache(maxsize=4)
def cached(path: str | None = None) -> Weights:
    return load(Path(path) if path else None)


def reset() -> None:
    cached.cache_clear()


__all__ = ["PENALTY_NAME", "SIGNAL_NAMES", "Weights", "cached", "load", "reset"]
