"""Relevance decisions as committed data, not as a runtime API call.

PRD §7.2 specifies an LLM classifier on the ambiguous middle, and that was
built as a runtime dependency: configure a model, configure a key, and every
harvest calls out per record. It was never wired up, so `_classify` took its
"no classifier configured" branch — which accepts — on every ambiguous record,
and 52% of the harvested catalog was published because a component nobody had
switched on could not say no.

**The runtime design was the wrong shape for this decision.** Relevance is not
a computation the pipeline needs to redo; it is a fact about a dataset that is
true until the dataset changes. ADR-0012 already makes git the system of record
for the catalog, and a relevance decision belongs there for the same reasons the
records do:

* it is **reviewable** — a decision arrives in the pull request beside the
  record it admits or excludes, where a person can disagree with it, rather
  than happening inside a build container and leaving a log line;
* it is **stable** — the same 1,199 registry entries produce the same catalog
  on every build, instead of depending on what a model said that morning;
* it **needs no key, no budget and no vendor**, so the filter's second stage
  works in CI, in a fork, and on a laptop with no network;
* and it **cannot fail open**. An unavailable API turned into "accept
  everything", which is the failure mode that put a marmoset connectivity study
  in a power-system catalog.

What is lost is that a record this file has never seen gets no verdict. That is
handled by saying so — :meth:`StaticClassifier.classify` raises
:class:`Undecided` rather than guessing — and the caller falls back to the
generous behaviour it had before. A gap in the file is a gap in coverage, not a
silent judgement.

The runtime classifier is still worth having for a source too large or too fast
to decide by hand; this and that are not exclusive. What this changes is that
the *default* costs nothing and the fallback is honest.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from datahub.config import Settings, get_settings
from datahub.harvest.filters.relevance import Undecided, Verdict
from datahub.logging import get_logger

log = get_logger(__name__)

#: Written into every verdict this classifier returns, so a decision's origin
#: survives into the audit table. Not a model name: nothing was inferred at
#: harvest time, the answer was looked up.
DECIDED_BY = "curated-decision-file"


class StaticClassifier:
    """The :class:`~datahub.harvest.filters.relevance.Classifier` protocol,
    answered from a file.

    Keyed by the harvest record's ``source_id`` — ``aws_open_data:tabula-sapiens``
    — which is the same key the harvester is already idempotent on, so a
    decision follows a dataset across re-harvests and a renamed upstream entry
    shows up as an uncovered record rather than as a wrong verdict.
    """

    def __init__(self, path: Path | str | None = None, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.path = Path(path) if path else Path(settings.relevance_decisions_path)

    def classify(
        self,
        text: str,  # noqa: ARG002 - protocol; this classifier reads the decision, not the record
        *,
        title: str | None = None,  # noqa: ARG002 - same
        key: str | None = None,
    ) -> Verdict:
        """The verdict for *key*, or :class:`Undecided`.

        ``text`` and ``title`` are part of the protocol and unused here: this
        classifier does not read the record, it reads what was decided about
        it. They stay in the signature so a runtime classifier remains a
        drop-in alternative.
        """
        if key is None:
            raise Undecided("a decision file is keyed by source id, and none was given")
        entry = load_decisions(str(self.path)).get(key)
        if entry is None:
            raise Undecided(f"no recorded decision for {key!r}")
        return Verdict(
            relevant=bool(entry["relevant"]),
            reason=str(entry.get("reason") or ""),
            # 1.0 because a recorded decision is not a probability. A confidence
            # here would be inventing precision the file does not have.
            confidence=1.0,
            model=DECIDED_BY,
        )

    def covers(self, key: str) -> bool:
        return key in load_decisions(str(self.path))

    def __len__(self) -> int:
        return len(load_decisions(str(self.path)))


@functools.lru_cache(maxsize=4)
def load_decisions(path: str) -> dict[str, dict[str, Any]]:
    """Parse the decision file, once per process.

    A missing file is an empty mapping rather than an error: a deployment that
    has not written one is in exactly the position every deployment was in
    before this existed, and should behave the same way.
    """
    file = Path(path)
    if not file.exists():
        log.info("no relevance decision file", path=path)
        return {}

    raw = yaml.safe_load(file.read_text()) or {}
    decisions = raw.get("decisions") if isinstance(raw, dict) else None
    if not isinstance(decisions, dict):
        raise ValueError(f"{path}: expected a top-level 'decisions' mapping")

    out: dict[str, dict[str, Any]] = {}
    for key, entry in decisions.items():
        if not isinstance(entry, dict) or "relevant" not in entry:
            raise ValueError(f"{path}: {key!r} must be a mapping with a 'relevant' key")
        if not isinstance(entry["relevant"], bool):
            # `relevant: no` parses as False in YAML and `relevant: "no"` does
            # not. Refusing the string is cheaper than debugging a catalog that
            # published everything because a quoted "false" is truthy.
            raise ValueError(f"{path}: {key!r} has a non-boolean 'relevant'")
        out[str(key)] = entry
    log.info("relevance decisions loaded", path=path, count=len(out))
    return out


__all__ = ["DECIDED_BY", "StaticClassifier", "Undecided", "load_decisions"]
