"""The third resolution strategy, behind a seam (WP-7.1).

PRD §F4 specifies the ladder: *exact match on normalized name and unit first,
then SKOS altLabel match, then embedding similarity over concept definitions
with a confidence threshold, then gap marker. Never guess past the threshold.*

Only the third rung needs a model, and a model is exactly the kind of
dependency that must not be load-bearing for a deterministic recompute. So it
is a protocol with two implementations:

* :class:`LexicalSimilarity`, the default. Token overlap between the field's
  name and definition and the concept's. It is not semantic and does not
  pretend to be — it will not connect "insolation" to "irradiance" — but it is
  deterministic, offline, and free, and it earns its place by catching the
  common case of a descriptive column name that no altLabel happens to list.
* :class:`EmbeddingSimilarity`, which takes any callable that turns text into a
  vector. Nothing in this package requires it.

Both feed the same threshold. A score below it produces a gap marker with a
stated reason, never a guess — which is the rule the whole ladder exists to
enforce.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from datahub.semantic.vocabulary import Concept, tokens


@runtime_checkable
class SimilarityBackend(Protocol):
    """Scores a field description against a concept, in [0, 1]."""

    #: Human-readable, and recorded on every assignment this backend justifies.
    #: A resolution whose basis is not reconstructible is not auditable.
    name: str

    #: The backend's own confidence threshold. Declared here rather than fixed
    #: in the resolver because the two backends do not produce comparable
    #: numbers: token overlap of 0.5 is a strong lexical signal, and cosine
    #: similarity of 0.5 between two sentence embeddings is nearly noise. One
    #: global threshold would make the resolver either reckless or inert
    #: depending on which backend was configured.
    threshold: float

    def score(self, text: str, concept: Concept) -> float: ...


@dataclass(frozen=True)
class LexicalSimilarity:
    """Token overlap against labels and definition, weighted toward the labels.

    Two different metrics, because the two comparisons are different shapes:

    * **Labels** use Jaccard. A field name and a concept label are both short,
      so the symmetric measure is the right one and punishing extra tokens on
      either side is correct.
    * **Definitions** use the containment coefficient — the fraction of the
      *field's* tokens the definition covers. Jaccard here would be almost
      useless: a concept definition runs to forty tokens and a field
      description to five, so even a perfect match scores 5/40, and the metric
      ends up ranking concepts by how short their definitions are.
    """

    name: str = "lexical-token-overlap"
    label_weight: float = 0.7
    #: Calibrated against what this metric actually produces, not against an
    #: intuition about what "high confidence" should look like. Correct matches
    #: over the fixture corpus land between 0.25 and 0.35 — a field name and a
    #: concept definition are never the same words — so a threshold of 0.45
    #: would silence the rung entirely and a threshold of 0.6 would be
    #: theatre.
    #:
    #: The number is not what makes this safe. The margin rule in the resolver
    #: is: a best candidate that is not clearly ahead of the runner-up produces
    #: a gap naming both, whatever either scored. On a compressed scale that is
    #: the guard that does the work.
    threshold: float = 0.25

    def score(self, text: str, concept: Concept) -> float:
        query = set(tokens(text))
        if not query:
            return 0.0
        label = set(tokens(concept.pref_label)) | {
            t for alt in concept.alt_labels for t in tokens(alt)
        }
        return self.label_weight * _jaccard(query, label) + (1 - self.label_weight) * _contained(
            query, concept.definition_tokens
        )


@dataclass(frozen=True)
class EmbeddingSimilarity:
    """Cosine similarity over an injected embedding function.

    The function is injected rather than imported so this module has no
    dependency on any model runtime. ``embed`` must be deterministic for a
    given input, or two recomputes of the same catalog disagree and the
    ``lastComputedAt`` timestamps stop meaning anything.
    """

    embed: Callable[[str], Sequence[float]]
    name: str = "embedding-cosine"
    #: Cosine similarity between unrelated sentence embeddings is commonly
    #: 0.3–0.6 for modern models, so a threshold that would be strict for token
    #: overlap is close to no filter at all here.
    threshold: float = 0.82

    def score(self, text: str, concept: Concept) -> float:
        target = f"{concept.pref_label}. {concept.definition or ''}"
        return _cosine(self.embed(text), self.embed(target))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _contained(query: set[str], corpus: set[str]) -> float:
    """The fraction of *query* the corpus covers. Asymmetric, deliberately."""
    if not query or not corpus:
        return 0.0
    return len(query & corpus) / len(query)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    # Clamped because floating point can produce 1.0000000000000002, and a
    # confidence above 1 makes every downstream comparison look wrong.
    return max(0.0, min(1.0, dot / (na * nb)))


__all__ = ["EmbeddingSimilarity", "LexicalSimilarity", "SimilarityBackend"]
