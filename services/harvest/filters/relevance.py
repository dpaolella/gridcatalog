"""Grid relevance: is this dataset one the catalog should carry? (WP-3.4)

PRD §7.2:

> Most harvest sources are broader than grid modeling. A two-stage filter:
> keyword and domain-vocabulary matching first, then an LLM relevance
> classifier on the ambiguous middle. **Log every rejection with its reason so
> recall can be audited. Err toward inclusion; a wrongly excluded dataset is
> invisible, a wrongly included one is a review-queue cost.**

Both halves of that last sentence are load-bearing and they point the same way.
The asymmetry is the whole design:

* A **wrongly included** dataset costs a steward thirty seconds in the review
  queue, and the queue exists anyway.
* A **wrongly excluded** dataset is invisible by construction. Nobody searches
  for a record that was never created, so the mistake is never reported and
  never found. Recall failures are silent; precision failures are noisy.

So the filter is deliberately generous, the ambiguous middle goes to a
classifier rather than to a rule, and **every decision is written down** — the
accepts too, not only the rejections, because a recall audit needs to compare
what was taken against what was passed over.

Three stages, in increasing cost:

1. **keyword** — literal terms, near-free. A strong hit accepts outright.
2. **vocabulary** — labels from the 164-concept grid vocabulary and the ten
   data-domain concepts, so the filter improves when the vocabulary does
   rather than when this file is edited.
3. **decided** — only the middle, and a failure to reach a verdict accepts
   rather than rejects.

That last rule matters more than it looks. If the classifier cannot answer, it
must not silently start shrinking the catalog.

The third stage was specified as an LLM call per record and is now a lookup
into a committed decision file — same protocol, no key, no budget, and the
verdict lands in a pull request where somebody can disagree with it. See
:mod:`datahub.harvest.filters.decisions`. The stage is named ``decided`` rather
than ``llm`` because what it records is that a verdict existed, not how it was
reached; a runtime classifier plugged into the same protocol reports the same
stage and names itself in ``model``.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from datahub.config import Settings, get_settings
from datahub.logging import get_logger

log = get_logger(__name__)

#: Terms that alone are enough. Every one of these names a thing that is only
#: interesting to someone modelling a power system; a dataset carrying one is
#: worth a steward's thirty seconds even if it turns out to be off-target.
STRONG_TERMS: frozenset[str] = frozenset(
    {
        "power system",
        "power grid",
        "electric grid",
        "electricity grid",
        "transmission network",
        "transmission line",
        "distribution network",
        "power flow",
        "load flow",
        "optimal power flow",
        "unit commitment",
        "economic dispatch",
        "capacity expansion",
        "electricity market",
        "energy system model",
        "generation fleet",
        "generator fleet",
        "power plant",
        "substation",
        "switchgear",
        "busbar",
        "interconnection queue",
        "grid interconnection",
        "transmission capacity",
        "electricity demand",
        "electricity load",
        "load profile",
        "demand profile",
        "capacity factor",
        "renewable generation",
        "wind resource",
        "solar resource",
        "grid emission factor",
        "locational marginal price",
        "ancillary service",
        "frequency response",
        "grid topology",
        "network topology",
        "one-line diagram",
        "single-line diagram",
        "pypsa",
        "plexos",
        "matpower",
        "reeds",
        "gridpath",
        "switch model",
        "powsybl",
        "opendss",
        "cyme",
        "pss/e",
        "psse",
        # DD9 — cost and financial. Unambiguous inside the domain and rare
        # outside it; a dataset saying "LCOE" is a power-sector dataset.
        "levelized cost of electricity",
        "levelised cost of electricity",
        "lcoe",
        "annual technology baseline",
        "capacity credit",
        "overnight capital cost",
        "heat rate",
        "fuel cost",
        "capital cost",
        # DD8 — policy and regulatory.
        "renewable portfolio standard",
        "capacity market",
        "emissions trading",
        "carbon price",
        "feed-in tariff",
        "contracts for difference",
        "interconnection agreement",
        # DD7 — fuel and commodity.
        "henry hub",
        "natural gas price",
        "coal price",
        "fuel price",
        # DD6 — emerging technology.
        "battery storage",
        "energy storage",
        "long duration storage",
        "electrolyser cost",
        "direct air capture",
    }
)

#: Terms that count only in company. Each is common outside the grid — "energy"
#: appears in nutrition datasets and "storage" in cloud pricing — so one alone
#: means nothing and two together mean quite a lot.
WEAK_TERMS: frozenset[str] = frozenset(
    {
        "electricity",
        "electrical",
        "energy",
        "power",
        "grid",
        "generation",
        "generator",
        "transmission",
        "distribution",
        "substation",
        "voltage",
        "megawatt",
        "gigawatt",
        "kilowatt",
        "mwh",
        "gwh",
        "kwh",
        "renewable",
        "solar",
        "photovoltaic",
        "wind",
        "hydropower",
        "nuclear",
        "coal",
        "natural gas",
        "battery",
        "storage",
        "hydrogen",
        "electrolyser",
        "electrolyzer",
        "emissions",
        "carbon intensity",
        "decarbonisation",
        "decarbonization",
        "curtailment",
        "reanalysis",
        "weather",
        "irradiance",
        "wind speed",
        "hourly",
        "time series",
        "utility",
        "iso",
        "rto",
        "tso",
        "dso",
        "balancing",
        "dispatch",
        "tariff",
        "outage",
        "reliability",
        "resource adequacy",
        "land cover",
        "land use",
        "protected area",
        "bathymetry",
        "terrain",
        # Words that appear across DD3, DD4, DD6, DD7, DD8 and DD9 and are far
        # too common on their own — a cost dataset and a shipping-freight
        # dataset both say "cost" — but which pair up with the rest of this
        # list into a real signal.
        "cost",
        "capex",
        "opex",
        "levelized",
        "levelised",
        "technoeconomic",
        "techno-economic",
        "technology baseline",
        "discount rate",
        "queue",
        "pipeline",
        "permitting",
        "siting",
        "policy",
        "regulatory",
        "subsidy",
        "incentive",
        "market",
        "price",
        "bidding zone",
        "balancing authority",
        "capacity",
        "demand",
        "load",
        "consumption",
    }
)

#: Terms that make a match a coincidence rather than a signal. Present only
#: where the collision is genuine and frequent — "solar wind" in heliophysics,
#: "power" in statistics. Each subtracts from the score; none rejects outright,
#: because a paper on statistical power in grid reliability studies is a real
#: thing and this filter must not be the one to decide it is not.
COUNTER_TERMS: frozenset[str] = frozenset(
    {
        "solar wind",
        "solar flare",
        "coronal mass ejection",
        "heliosphere",
        "statistical power",
        "power law",
        "power analysis",
        "purchasing power",
        "bargaining power",
        "muscle power",
        "horsepower",
        "power of attorney",
        "energy drink",
        "energy expenditure",
        "caloric",
        "dietary energy",
        "binding energy",
        "activation energy",
        "wind instrument",
        "grid computing",
        "grid search",
        "national grid reference",
        "grid cell size",
    }
)

#: Distinguishes "no classifier argument given", which takes the default
#: decision-file classifier, from an explicit ``classifier=None``, which means
#: run the first two stages only. Without it there is no way to switch the
#: third stage off, and a test that means to cannot.
_DEFAULT = object()

#: Score at or above which a record is accepted without reaching the classifier.
ACCEPT_AT = 1.0
#: Score below which a record is rejected without reaching the classifier.
REJECT_BELOW = 0.15

_WORD = re.compile(r"[a-z0-9][a-z0-9+/.-]*")


@dataclass(slots=True)
class RelevanceDecision:
    """One filter decision, with everything needed to audit it later."""

    accepted: bool
    stage: str
    reason: str
    score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None

    def as_row(self) -> dict[str, Any]:
        """The shape :class:`~datahub.api.models.repositories.RelevanceRepository`
        stores, so the audit trail and the decision cannot drift apart."""
        return {
            "accepted": self.accepted,
            "stage": self.stage,
            "reason": self.reason,
            "score": self.score,
            "matched_terms": self.matched_terms,
            "model": self.model,
            "prompt_version": self.prompt_version,
        }


def _default_classifier(settings: Settings) -> Classifier | None:
    """The decision-file classifier, or None if it cannot be built.

    Imported here rather than at module scope because `decisions` imports
    `Verdict` and `Undecided` from this module. None on failure, which restores
    the previous behaviour instead of failing a harvest over a missing file.
    """
    try:
        from datahub.harvest.filters.decisions import StaticClassifier

        return StaticClassifier(settings=settings)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("relevance decision file unavailable", error=str(exc))
        return None


class RelevanceFilter:
    """Decides whether a harvested record is worth a steward's attention.

    The vocabulary terms are read once from the SKOS schemes, so widening the
    vocabulary widens the filter. That is the intended way to improve recall:
    editing this module's word lists should be rare, because a term worth
    filtering on is usually a term worth having as a concept.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        classifier: Classifier | object | None = _DEFAULT,
        vocabulary_terms: Iterable[str] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # Defaults to the committed decision file rather than to nothing. The
        # old default — no classifier at all — meant the ambiguous middle took
        # the accept-everything branch on every deployment, which is how half
        # the catalog got published without anything having decided it belongs.
        self.classifier: Classifier | None = (
            _default_classifier(self.settings) if classifier is _DEFAULT else classifier  # type: ignore[assignment]
        )
        self._vocabulary = (
            frozenset(vocabulary_terms)
            if vocabulary_terms is not None
            else vocabulary_phrases(self.settings)
        )

    # ---- the decision ----------------------------------------------------

    def decide(
        self, text: str, *, title: str | None = None, key: str | None = None
    ) -> RelevanceDecision:
        """Classify one record's text.

        ``title`` is scored twice if given: a grid term in a title is a much
        stronger signal than the same term in the fourth paragraph of a
        boilerplate licence notice.

        ``key`` is the record's ``source_id``, passed through to the classifier
        for the ambiguous middle.
        """
        haystack = _normalise(f"{title or ''} {title or ''} {text}")
        score, matched = self.score(haystack)

        if score >= ACCEPT_AT:
            return RelevanceDecision(
                accepted=True,
                stage="keyword" if self._has_strong(haystack) else "vocabulary",
                reason=self._reason(matched, score, "clear grid signal"),
                score=score,
                matched_terms=matched,
            )

        if score < REJECT_BELOW:
            # The reason distinguishes "nothing matched" from "only generic
            # words matched". They call for different fixes when a recall audit
            # finds this rejection was wrong: the first wants a new term, the
            # second wants the weak-pair threshold revisited.
            summary = (
                "matched only generic terms, which alone are not a grid signal"
                if matched
                else "no grid vocabulary term matched"
            )
            return RelevanceDecision(
                accepted=False,
                stage="keyword",
                reason=self._reason(matched, score, summary),
                score=score,
                matched_terms=matched,
            )

        return self._classify(text, title, score, matched, key)

    def _classify(
        self,
        text: str,
        title: str | None,
        score: float,
        matched: list[str],
        key: str | None = None,
    ) -> RelevanceDecision:
        """The ambiguous middle.

        Every path that does not reach a *deciding* classifier **accepts**. An
        unavailable third party must never quietly start shrinking the catalog,
        and a record that reaches this branch already carries some grid signal.

        **Not gated on `enrichment_enabled`.** It used to be, and that coupling
        was a bug of its own: the default classifier reads a committed file and
        needs no model, no key and no budget, so switching off enrichment has
        no business switching it off. The two were only ever related by both
        having been described as "the LLM bits".
        """
        if self.classifier is None:
            return RelevanceDecision(
                accepted=True,
                stage="vocabulary",
                reason=self._reason(
                    matched,
                    score,
                    "ambiguous and no classifier configured; included for review",
                ),
                score=score,
                matched_terms=matched,
            )
        try:
            verdict = self.classifier.classify(text, title=title, key=key)
        except Undecided as exc:
            # "Nobody has looked", which is not "not relevant". Accepted, and
            # the reason says which of the two it is so a recall audit can find
            # the uncovered records rather than inferring them.
            return RelevanceDecision(
                accepted=True,
                stage="vocabulary",
                reason=self._reason(matched, score, f"ambiguous and undecided ({exc}); included"),
                score=score,
                matched_terms=matched,
            )
        except Exception as exc:
            log.warning("relevance classifier unavailable", error=str(exc))
            return RelevanceDecision(
                accepted=True,
                stage="vocabulary",
                reason=(
                    f"ambiguous and the classifier was unavailable ({type(exc).__name__}); "
                    "included rather than dropped"
                ),
                score=score,
                matched_terms=matched,
            )
        return RelevanceDecision(
            accepted=verdict.relevant,
            stage="decided",
            reason=verdict.reason,
            score=verdict.confidence,
            matched_terms=matched,
            model=verdict.model,
            prompt_version=self.settings.enrichment_prompt_version,
        )

    # ---- scoring ---------------------------------------------------------

    def score(self, haystack: str) -> tuple[float, list[str]]:
        """A score and the terms that produced it.

        The terms come back with the score because a number nobody can explain
        is not auditable, and this filter's whole justification is that its
        mistakes can be found.
        """
        matched: list[str] = []
        total = 0.0

        for term in STRONG_TERMS:
            if _contains(haystack, term):
                matched.append(term)
                total += 1.0

        weak_hits = [t for t in WEAK_TERMS if _contains(haystack, t)]
        matched += weak_hits
        # Two weak terms make a signal; one makes a coincidence. Sublinear
        # after that, so a licence boilerplate listing every energy carrier
        # does not outscore a record that actually says "transmission line".
        if len(weak_hits) >= 2:
            total += 0.45 + 0.12 * min(len(weak_hits) - 2, 6)

        vocab_hits = [t for t in self._vocabulary if _contains(haystack, t)]
        matched += vocab_hits
        if vocab_hits:
            total += min(0.35 * len(vocab_hits), 1.0)

        counter_hits = [t for t in COUNTER_TERMS if _contains(haystack, t)]
        if counter_hits:
            matched += [f"-{t}" for t in counter_hits]
            total -= 0.5 * len(counter_hits)

        return max(total, 0.0), sorted(set(matched))

    def _has_strong(self, haystack: str) -> bool:
        return any(_contains(haystack, term) for term in STRONG_TERMS)

    @staticmethod
    def _reason(matched: Sequence[str], score: float, summary: str) -> str:
        terms = ", ".join(matched[:8]) if matched else "none"
        more = f" (+{len(matched) - 8} more)" if len(matched) > 8 else ""
        return f"{summary}; score {score:.2f}; matched: {terms}{more}"


# ---------------------------------------------------------------------------
# The classifier seam
# ---------------------------------------------------------------------------


class Undecided(LookupError):
    """A classifier has no opinion about this record.

    Distinct from a negative verdict, and the distinction is the whole point:
    "we looked and it is not relevant" and "nobody has looked" call for
    different handling, and collapsing them is how a filter starts quietly
    excluding things nobody chose to exclude.
    """


@dataclass(slots=True)
class Verdict:
    relevant: bool
    reason: str
    confidence: float = 0.0
    model: str | None = None


class Classifier:
    """What the third stage must provide.

    A protocol rather than a concrete client so the filter is testable without
    a network and without an API key, and so a deployment that has neither
    still runs the first two stages.

    ``key`` is the harvest record's ``source_id``. A classifier that reads the
    record does not need it; one that looks a decision up — see
    :mod:`datahub.harvest.filters.decisions`, which is the default — needs
    nothing else. Raising :class:`~datahub.harvest.filters.decisions.Undecided`
    means "no opinion", and is not the same as returning an irrelevant verdict.
    """

    def classify(
        self, text: str, *, title: str | None = None, key: str | None = None
    ) -> Verdict:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Vocabulary terms
# ---------------------------------------------------------------------------


#: Vocabulary files whose labels say what a dataset is *about*.
#:
#: Not every scheme does. `og-access-restriction` and `og-provenance-class`
#: describe how you get a dataset and where it came from, and their labels —
#: "open access", "data use agreement", "crowd-sourced", "bias-corrected" —
#: are true of a protein structure database and a locational marginal price
#: series alike. Globbing the directory put all 41 of them into the filter,
#: each worth 0.35, which is enough on its own to clear the rejection floor and
#: reach the auto-accepting middle. That is how the Human Cell Atlas, Tabula
#: Sapiens and a marmoset connectivity study came to be published in a
#: power-system catalog.
#:
#: An allow-list rather than a deny-list, so adding a vocabulary is not a
#: silent widening of the filter: a new scheme has to be named here to count.
SUBJECT_SCHEMES: tuple[str, ...] = (
    "og-grid-concept.ttl",
    "og-data-domain.ttl",
    "og-analysis-type.ttl",
)


@functools.lru_cache(maxsize=4)
def _vocabulary_phrases(vocab_dir: str) -> frozenset[str]:
    from pathlib import Path

    from rdflib import Graph
    from rdflib.namespace import SKOS

    graph = Graph()
    for name in SUBJECT_SCHEMES:
        path = Path(vocab_dir) / name
        if path.exists():
            graph.parse(path)

    phrases: set[str] = set()
    for predicate in (SKOS.prefLabel, SKOS.altLabel):
        for label in graph.objects(None, predicate):
            phrase = _normalise(str(label))
            # One-word labels are already covered by the weak list, and short
            # ones ("bus", "line", "node") match half the datasets on earth.
            if len(phrase) >= 8 and " " in phrase:
                phrases.add(phrase)
    return frozenset(phrases)


def vocabulary_phrases(settings: Settings | None = None) -> frozenset[str]:
    """Multi-word labels from the subject SKOS schemes.

    Only multi-word: "bus", "line" and "node" are concept labels and also match
    a bus timetable, a queueing study and a graph-theory paper. The multi-word
    labels — "transmission line", "capacity factor", "balancing authority" —
    are the ones that carry the domain with them.

    Only *subject* schemes: see :data:`SUBJECT_SCHEMES`.
    """
    settings = settings or get_settings()
    return _vocabulary_phrases(str(settings.vocab_dir))


# ---------------------------------------------------------------------------
# Text handling
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lower-case tokens, with hyphenated names also split into their parts.

    Both forms are kept. "PyPSA-Eur" becomes ``pypsa-eur pypsa eur``, so a
    filter term of ``pypsa`` matches it — without that, the single most
    recognisable name in European power-system modelling scored zero, because
    whole-token matching (which is what stops "iso" hitting "isotope") also
    stops "pypsa" hitting "pypsa-eur".
    """
    tokens: list[str] = []
    for token in _WORD.findall(str(text).lower()):
        tokens.append(token)
        if "-" in token or "/" in token:
            tokens.extend(part for part in re.split(r"[-/]", token) if part)
    return " ".join(tokens)


def _contains(haystack: str, term: str) -> bool:
    """Whole-token containment, in either the hyphenated or the split form.

    Substring matching would have "iso" hit "isotope" and "wind" hit
    "winding", which is exactly the kind of silent false positive that makes a
    filter's score meaningless. So the match is whole-token, and the term has
    to appear as a contiguous run.

    **The split form is why a hyphenated term matched nothing (#31).**
    `_normalise` expands a hyphenated token into the token *and* its parts, so
    "feed-in tariff" arrives as ``feed-in feed in tariff`` — and the term
    ``feed-in tariff`` is not a contiguous run of that, because the split parts
    are interleaved. Every multi-word hyphenated term therefore scored zero
    against a document that literally contained it. All three of them were in
    `STRONG_TERMS`, where one match alone reaches `ACCEPT_AT` and accepts a
    record outright, so these were the filter's highest-confidence signals and
    they were dead.

    Checking the de-hyphenated form fixes it without widening anything:
    `_normalise` already emits the split parts adjacently, so ``feed in
    tariff`` *is* a contiguous run of the same haystack, and the match stays
    whole-token in both forms.
    """
    padded = f" {haystack} "
    if f" {term} " in padded:
        return True
    if "-" not in term:
        return False
    return f" {term.replace('-', ' ')} " in padded


def text_of(payload: dict[str, Any], *fields: str) -> str:
    """Flatten the fields a source puts prose in, for scoring.

    Lists and nested dicts are flattened rather than skipped: CKAN puts its
    tags in a list of dicts, and a record whose only grid signal is its tags is
    still a record with a grid signal.
    """
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for name in fields or tuple(payload):
        walk(payload.get(name))
    return " ".join(parts)


__all__ = [
    "ACCEPT_AT",
    "COUNTER_TERMS",
    "REJECT_BELOW",
    "STRONG_TERMS",
    "WEAK_TERMS",
    "Classifier",
    "RelevanceDecision",
    "RelevanceFilter",
    "Undecided",
    "Verdict",
    "text_of",
    "vocabulary_phrases",
]
