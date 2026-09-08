"""Does energy literature actually name this dataset?

Every other relevance signal in this pipeline reads a dataset's *description*.
That is how ArcticDEM came to be published: `relevance-decisions.yaml` says
"land cover, elevation or bathymetry stated as a physical quantity, which is
what siting and permitting analysis reads", which is true of elevation as a
*category* and inherited by a dataset covering the Arctic. Nobody sites
transmission with it.

This asks a different question, and one with an answer: **how many papers
classified in an energy field mention this dataset by name?** Measured:

    NREL NSRDB              2,104 papers   282 in energy   13.4%
    Global Wind Atlas       1,612 papers   204 in energy   12.7%
    ECMWF ERA5                  —          387 in energy     —
    ArcticDEM               1,704 papers     1 in energy    0.1%
    EarthDEM                   48 papers     0 in energy      0%

ArcticDEM has a real literature. Almost none of it is about energy.

## Three things that look like they should work and do not

**Energy keywords instead of topic classification.** Searching for the dataset
name AND "wind power OR electricity OR power system" reports *123* energy
papers for ArcticDEM. They are glaciology: "Challenges in modeling the energy
*balance* and melt in the percolation zone", "*Load* Stress Controls on
Directional Lava Dome Growth". The words appear; the subject does not. Only
:data:`ENERGY_FIELDS` — a maintained classification of what a paper is *about* —
separates them.

**An unquoted phrase.** ``fulltext.search:Wind Integration National Dataset``
returns 179,365 works because it ORs the words; quoted, 549. Getting this wrong
does not degrade the signal, it inverts it: every dataset with a generic name
scores as enormously popular. :func:`_phrase` is the only place a query is
built, so there is one place for that to be right.

**Trusting a low score on its own.** NOAA HRRR scores 5. HRRR is a workhorse of
wind and solar forecasting, but those papers appear in *Meteorology* journals
and are classified there. A threshold on this number alone deletes it. Hence
:func:`refuses_publication`, which is a conjunction of independent signals and
never this one by itself.

## Cost, and why the scan is resumable

OpenAlex is no longer unmetered: a request costs $0.001 against a daily
allowance that resets at midnight UTC, and a full pass over this catalog is
about 600 requests. So a scan is **resumable and partial by design** — it scores
what the budget allows, writes what it got, and the next run continues. A record
that was not reached is *unscored*, which is not the same as scoring zero and is
never read as one.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from datahub.logging import get_logger

log = get_logger(__name__)

API = "https://api.openalex.org/works"

#: OpenAlex fields whose papers count as energy work.
#:
#: 21 is Energy. 22 is Engineering, which carries a great deal of power-system
#: research published outside energy journals — omitting it loses most of the
#: transmission and power-electronics literature.
#:
#: Deliberately *not* every plausible field. Adding Environmental Science would
#: sweep in the entire remote-sensing corpus and every land-cover product would
#: score as heavily used, which is the failure this module exists to avoid.
ENERGY_FIELDS: tuple[int, ...] = (21, 22)

#: How long to wait between calls. OpenAlex asks for a polite pace and answers
#: 429 when it is not given one.
PACE_S = 0.7


class BudgetExhausted(RuntimeError):
    """The daily OpenAlex allowance is spent.

    Raised rather than returning zero, because "we could not ask" and "nobody
    uses this" are opposite facts and the whole design turns on not confusing
    them.
    """


@dataclass(frozen=True, slots=True)
class LiteratureScore:
    """What the literature says about one dataset, and when it was asked."""

    slug: str
    query: str
    works: int
    energy_works: int
    measured_at: str

    @property
    def share(self) -> float:
        return self.energy_works / self.works if self.works else 0.0

    def as_row(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "works": self.works,
            "energy_works": self.energy_works,
            "measured_at": self.measured_at,
        }


class OpenAlexScorer:
    """Counts works naming a dataset, and works naming it in an energy field.

    Two requests per dataset. An API key raises the allowance and is optional:
    without one the scan runs against the free daily budget and stops when it
    is spent, which is why :meth:`score` raises :class:`BudgetExhausted` rather
    than swallowing it.
    """

    def __init__(
        self,
        *,
        mailto: str | None = None,
        api_key: str | None = None,
        fields: tuple[int, ...] = ENERGY_FIELDS,
        pace_s: float = PACE_S,
    ) -> None:
        self.mailto = mailto
        self.api_key = api_key
        self.fields = fields
        self.pace_s = pace_s

    # ---- the query -------------------------------------------------------

    @staticmethod
    def _phrase(name: str) -> str:
        """A quoted phrase, which is the difference between 549 and 179,365.

        Trailing punctuation and an inner quote are stripped rather than
        escaped: OpenAlex has no escape syntax inside a filter value, and a
        stray quote silently truncates the phrase into a much broader search.
        """
        cleaned = name.replace('"', " ").strip().strip(",;:-")
        return f'"{cleaned}"'

    def _filters(self, name: str, *, energy: bool) -> str:
        parts = [f"fulltext.search:{self._phrase(name)}"]
        if energy:
            parts.append("primary_topic.field.id:" + "|".join(str(f) for f in self.fields))
        return ",".join(parts)

    # ---- the call --------------------------------------------------------

    def _count(self, filters: str, *, tries: int = 4) -> int:
        params = {"per_page": "1", "filter": filters}
        if self.mailto:
            params["mailto"] = self.mailto
        if self.api_key:
            params["api_key"] = self.api_key
        url = f"{API}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe=':,|"')}"

        for attempt in range(tries):
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "OpenGrid-DataHub/1.0"}
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    return int(json.load(response)["meta"]["count"])
            except urllib.error.HTTPError as exc:
                body = ""
                with contextlib.suppress(Exception):
                    body = exc.read().decode()[:400]
                # A spent budget is not a transient failure and retrying it
                # burns the rest of the run against a wall.
                if exc.code == 429 and "budget" in body.lower():
                    raise BudgetExhausted(body) from exc
                if attempt == tries - 1:
                    raise
                time.sleep(2.0 * (attempt + 1))
            except Exception:
                if attempt == tries - 1:
                    raise
                time.sleep(2.0 * (attempt + 1))
        raise RuntimeError("unreachable")

    def score(self, slug: str, name: str) -> LiteratureScore:
        works = self._count(self._filters(name, energy=False))
        time.sleep(self.pace_s)
        energy = self._count(self._filters(name, energy=True))
        time.sleep(self.pace_s)
        return LiteratureScore(
            slug=slug,
            query=self._phrase(name),
            works=works,
            energy_works=energy,
            measured_at=datetime.now(UTC).date().isoformat(),
        )


# ---------------------------------------------------------------------------
# The committed file
# ---------------------------------------------------------------------------


def load_scores(path: str | Path) -> dict[str, LiteratureScore]:
    """Read the committed scores. A missing file is an empty mapping.

    Absent means unmeasured, everywhere. No caller may read a missing entry as
    a zero — see :func:`refuses_publication`.
    """
    file = Path(path)
    if not file.exists():
        log.info("no literature scores", path=str(path))
        return {}
    raw = yaml.safe_load(file.read_text()) or {}
    rows = raw.get("scores") if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        raise ValueError(f"{path}: expected a top-level 'scores' mapping")

    out: dict[str, LiteratureScore] = {}
    for slug, row in rows.items():
        try:
            out[str(slug)] = LiteratureScore(
                slug=str(slug),
                query=str(row["query"]),
                works=int(row["works"]),
                energy_works=int(row["energy_works"]),
                measured_at=str(row["measured_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}: {slug!r} is malformed: {exc}") from exc
    log.info("literature scores loaded", path=str(path), count=len(out))
    return out


HEADER = """\
# How much energy literature names each dataset (issue #58).
#
# Written by `datahub literature scan`, which queries OpenAlex. Committed rather
# than measured at build time, for the reasons in ADR-0013: a number that
# decides what the catalog publishes belongs in a pull request where somebody
# can disagree with it, not in a build container.
#
#   works        papers whose full text names the dataset
#   energy_works of those, papers classified in OpenAlex fields {fields}
#                (Energy, Engineering) — a claim about what the paper is
#                *about*, not a keyword match. Searching for energy *words*
#                instead reports 123 hits for ArcticDEM, all of them glaciology
#                papers containing "energy balance".
#
# **A dataset absent from this file is unmeasured, not unused.** The scan is
# resumable because OpenAlex bills per request against a daily allowance, so a
# run stops when the budget is spent and the next one continues. Nothing may
# read a missing entry as a zero.
#
# A low score never removes a dataset on its own: NOAA HRRR scores 5 and is a
# wind-forecasting workhorse, because its papers are classified in Meteorology.
# See `refuses_publication` for the conjunction this feeds.

meta:
  source: openalex
  energy_fields: {fields_list}
scores:
"""


def write_scores(path: str | Path, scores: dict[str, LiteratureScore]) -> None:
    lines = [
        HEADER.format(
            fields=", ".join(str(f) for f in ENERGY_FIELDS),
            fields_list=list(ENERGY_FIELDS),
        )
    ]
    for slug in sorted(scores):
        score = scores[slug]
        lines.append(f"  {slug}:\n")
        lines.append(f"    query: {json.dumps(score.query)}\n")
        lines.append(f"    works: {score.works}\n")
        lines.append(f"    energy_works: {score.energy_works}\n")
        lines.append(f"    measured_at: {score.measured_at}\n")
    Path(path).write_text("".join(lines))


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

#: Relevance reasons that admit a dataset for the *class* of data it is rather
#: than for anything about the dataset itself. These are the ones this signal
#: exists to second-guess: they are how a DEM of Antarctica ends up in a
#: power-system catalog.
#:
#: The others — "an inventory of power-sector assets", "grid modelling software
#: and the data it ships with" — name the dataset's actual subject, and a record
#: admitted on one of those is not up for reconsideration here.
CATEGORY_REASONS: tuple[str, ...] = (
    "land cover, elevation or bathymetry",
    "a numerical weather prediction or reanalysis product",
)


@dataclass(frozen=True, slots=True)
class Verdict:
    refused: bool
    reason: str


def refuses_publication(
    slug: str,
    *,
    scores: dict[str, LiteratureScore],
    usage_evidence_count: int,
    relevance_reason: str | None,
    threshold: int = 1,
) -> Verdict:
    """Whether every independent signal of use is absent.

    A conjunction, not a threshold, and the difference is the whole design. Each
    clause has a known failure mode; requiring all three means a dataset is
    refused only where every one of them agrees, and any single false negative
    is survivable.

    * **no energy literature** names it — but topic classification follows the
      journal, so HRRR's forecasting papers sit in Meteorology and it scores 5;
    * **no recorded usage** (#48) — but that comes from a provider's own
      `DataAtWork` block and most sources have no equivalent field;
    * **admitted for its category** — but the category rules are right about
      most of what they admit.

    Unmeasured is not zero. A dataset with no entry in the scores file is never
    refused, because the reason it has no entry may simply be that the scan ran
    out of budget before reaching it.
    """
    score = scores.get(slug)
    if score is None:
        return Verdict(False, "unmeasured: no literature score, which is not a score of zero")
    if score.energy_works >= threshold:
        return Verdict(
            False,
            f"{score.energy_works} energy-field works name it",
        )
    if usage_evidence_count > 0:
        return Verdict(
            False,
            f"no energy literature, but {usage_evidence_count} recorded uses",
        )
    reason = relevance_reason or ""
    if not any(marker in reason for marker in CATEGORY_REASONS):
        return Verdict(
            False,
            "no energy literature and no recorded use, but it was admitted for "
            "its own subject rather than its category",
        )
    return Verdict(
        True,
        f"no energy-field work names it ({score.works} works name it at all), "
        f"nothing records it being used, and it was admitted for its category",
    )


__all__ = [
    "CATEGORY_REASONS",
    "ENERGY_FIELDS",
    "BudgetExhausted",
    "LiteratureScore",
    "OpenAlexScorer",
    "Verdict",
    "load_scores",
    "refuses_publication",
    "write_scores",
]
