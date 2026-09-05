"""Spike: re-normalise the stored harvest payloads with candidate repairs.

Evidence for the breadth half of the plan. The AWS Open Data run harvested
1,199 records, judged 527 of them grid-relevant, and put **zero** of them in
the review queue: every one failed SHACL and went to `flagged`. This script
replays the same payloads out of `raw_records` through the same normaliser and
the same validator, with two repairs applied, and reports the delta.

The repairs are deliberately the *mechanical* ones — the two places the
pipeline drops source-stated facts on the floor:

  1. `og:updateCadence` is declared in `mappings/yaml_repo.yaml` as
     `transform: [text]`, so the registry's free-text `UpdateFrequency`
     ("Monthly", "Varies by dataset") lands in a field SHACL constrains to an
     ISO 8601 duration or one of three enum values. Every record fails.
  2. Licence resolution matches the seed licence map by exact string, so
     `"[Creative Commons BY 4.0](https://creativecommons.org/licenses/by/4.0/)"`
     and `"Creative Commons Attribution 4.0 International"` both miss a map
     that already contains CC-BY-4.0.

Neither repair invents anything. A cadence phrase maps to the enum value that
already exists for it, and a licence string resolves to an identifier only on
an unambiguous match; anything else stays unresolved exactly as it does today.

    python docs/ingestion/spikes/replay_normalize.py --database var/site/ops.sqlite3
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import yaml
from datahub.harvest.adapters.base import HarvestedRecord
from datahub.harvest.normalizers.engine import Normalizer
from datahub.harvest.validate import ValidationRunner

# ---------------------------------------------------------------------------
# Repair 1: cadence
# ---------------------------------------------------------------------------

#: Phrase to the value SHACL already allows. Ordered longest-first at match
#: time so "not currently being updated" does not match on "update".
CADENCE_PHRASES: list[tuple[str, str]] = [
    ("continuous", "PT1H"),
    ("real-time", "PT1H"),
    ("real time", "PT1H"),
    ("sub-hourly", "PT1H"),
    ("hourly", "PT1H"),
    ("twice daily", "PT12H"),
    ("daily", "P1D"),
    ("weekly", "P7D"),
    ("bi-weekly", "P14D"),
    ("fortnightly", "P14D"),
    ("monthly", "P1M"),
    ("quarterly", "P3M"),
    ("semi-annually", "P6M"),
    ("biannually", "P6M"),
    ("annually", "P1Y"),
    ("annual", "P1Y"),
    ("yearly", "P1Y"),
]

#: Phrases that mean "there is no schedule". `irregular` and `on-demand` are
#: different claims and the Currency grade treats them differently, so they are
#: kept apart rather than collapsed.
CADENCE_IRREGULAR = (
    "varies",
    "periodic",
    "occasional",
    "irregular",
    "sporadic",
    "ad hoc",
    "as new data",
    "as data",
    "when new",
    "as available",
    "as soon as",
    "ongoing",
    "continuously updated",
    "tbd",
    "unknown",
)
CADENCE_ON_DEMAND = ("as needed", "as required", "on demand", "on request", "need-to-update")
CADENCE_DISCONTINUED = (
    "not updated",
    "no longer",
    "never",
    "not currently being updated",
    "static",
    "one-time",
    "one time",
    "complete",
    "final",
    "no update",
)

ISO_DURATION = re.compile(r"^P(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(\d+H)?(\d+M)?(\d+S)?)?$")


def to_cadence(raw: Any) -> str | None:
    """Free text to a value SHACL accepts, or None when it says nothing.

    None is a real answer and the honest one: a source that does not state a
    cadence has not stated one, and an absent `og:updateCadence` is legal at
    every level. Only a *wrong* one is a problem.
    """
    if raw is None:
        return None
    text = " ".join(str(raw).split()).strip().lower()
    if not text or text in {"n/a", "na", "none", "-"}:
        return None
    if ISO_DURATION.match(str(raw).strip()) or str(raw).strip() in {
        "irregular",
        "on-demand",
        "discontinued",
    }:
        return str(raw).strip()
    for phrase in sorted(CADENCE_DISCONTINUED, key=len, reverse=True):
        if phrase in text:
            return "discontinued"
    for phrase in sorted(CADENCE_ON_DEMAND, key=len, reverse=True):
        if phrase in text:
            return "on-demand"
    for phrase, value in sorted(CADENCE_PHRASES, key=lambda p: -len(p[0])):
        if phrase in text:
            return value
    for phrase in sorted(CADENCE_IRREGULAR, key=len, reverse=True):
        if phrase in text:
            return "irregular"
    # Said something, and nothing here understands it. Dropping it is the
    # honest outcome; guessing "irregular" would assert a schedule claim.
    return None


# ---------------------------------------------------------------------------
# Repair 2: licence strings
# ---------------------------------------------------------------------------

MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

#: Unambiguous only. Every pattern here names one licence and one version; a
#: string that says "Creative Commons" with no version stays unresolved,
#: because CC-BY and CC-BY-NC-SA are not the same permission.
LICENCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"creativecommons\.org/publicdomain/zero/1\.0"), "CC0-1.0"),
    (re.compile(r"creativecommons\.org/licenses/by/4\.0"), "CC-BY-4.0"),
    (re.compile(r"creativecommons\.org/licenses/by-sa/4\.0"), "CC-BY-SA-4.0"),
    (re.compile(r"creativecommons\.org/licenses/by-nc/4\.0"), "CC-BY-NC-4.0"),
    (re.compile(r"creativecommons\.org/licenses/by/3\.0"), "CC-BY-3.0"),
    (re.compile(r"opendatacommons\.org/licenses/odbl"), "ODbL-1.0"),
    (re.compile(r"\bcc[- ]?by[- ]?sa[- ]?4(\.0)?\b"), "CC-BY-SA-4.0"),
    (re.compile(r"\bcc[- ]?by[- ]?nc[- ]?4(\.0)?\b"), "CC-BY-NC-4.0"),
    (re.compile(r"\bcc[- ]?by[- ]?4(\.0)?\b"), "CC-BY-4.0"),
    (re.compile(r"\bcc[- ]?by[- ]?3(\.0)?\b"), "CC-BY-3.0"),
    (re.compile(r"\bcc[- ]?0\b|\bcc zero\b"), "CC0-1.0"),
    (re.compile(r"creative commons attribution[ -]non[ -]?commercial 4\.0"), "CC-BY-NC-4.0"),
    (re.compile(r"creative commons attribution[ -]share[ -]?alike 4\.0"), "CC-BY-SA-4.0"),
    (re.compile(r"creative commons attribut\w* 4\.0"), "CC-BY-4.0"),
    (re.compile(r"creative commons attribut\w* 3\.0"), "CC-BY-3.0"),
    (re.compile(r"usa\.gov/publicdomain|17 u\.s\.c\.? § ?105|u\.s\.? government work"), "CC0-1.0"),
    (re.compile(r"\bapache[ -]?2(\.0)?\b"), "Apache-2.0"),
    (re.compile(r"\bmit license\b"), "MIT"),
]


def to_licence(raw: Any) -> Any:
    """Normalise a licence string enough for the existing map to find it.

    Markdown links are unwrapped, then an unambiguous pattern wins. Anything
    else is returned unchanged so it fails exactly as it does today.
    """
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    unwrapped = MARKDOWN_LINK.sub(r"\1 \2", text)
    haystack = unwrapped.lower()
    for pattern, spdx in LICENCE_PATTERNS:
        if pattern.search(haystack):
            return spdx
    return raw


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _harvest_sources() -> list[dict[str, Any]]:
    document = yaml.safe_load(Path("data/seed-sources.yaml").read_text())
    return document.get("harvest_sources", [])


def replay(database: str, *, source: str, repair: bool, backstop: bool = False) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "select source_record_id, payload, source_url from raw_records where source_id = ?",
        (source,),
    ).fetchall()

    domains = next((s["domains"] for s in _harvest_sources() if s.get("id") == source), None)
    normalizer = Normalizer("yaml_repo", source_domains=domains)
    validator = ValidationRunner()
    counts: collections.Counter[str] = collections.Counter()
    blockers: collections.Counter[str] = collections.Counter()

    for source_record_id, payload_json, source_url in rows:
        payload = json.loads(payload_json)
        if repair:
            if "UpdateFrequency" in payload:
                cadence = to_cadence(payload["UpdateFrequency"])
                if cadence is None:
                    payload.pop("UpdateFrequency")
                else:
                    payload["UpdateFrequency"] = cadence
            if "License" in payload:
                payload["License"] = to_licence(payload["License"])

        record = HarvestedRecord(
            source_id=source_record_id,
            source="yaml_repo",
            payload=payload,
            source_url=source_url,
        )
        try:
            normalized = normalizer.normalize(record)
            if backstop:
                _apply_backstops(normalized.document, domains)
        except Exception as exc:
            counts["error"] += 1
            blockers[f"normalise: {type(exc).__name__}"] += 1
            continue

        level = int(normalized.document.get("completenessLevel", 1))
        report = validator.validate_jsonld(normalized.document, level)
        if report.conforms:
            counts["queued"] += 1
            counts[f"level_{level}"] += 1
        else:
            counts["flagged"] += 1
            for violation in report.violations:
                blockers[str(violation.to_dict().get("path"))] += 1
    counts["seen"] = len(rows)
    return {"counts": dict(counts), "blockers": blockers.most_common(8)}


#: The third arm is a *measurement*, not a proposal. Filling both fields with
#: any value at all answers one question: how many records are blocked solely
#: on `og:provenanceClass` and `og:dataDomain`, and how many have some other
#: problem waiting behind them. The values written here are deliberately not a
#: design — a blanket `curated` is exactly the fabricated quality claim
#: `engine.py:_classify` refuses to make, and the plan fills these fields by
#: enrichment (marked `og:enrichmentBasis "inferred"`) and by source-declared
#: filing instead. See WP-11.1 and WP-11.2 in docs/ingestion-plan.md.
def _apply_backstops(document: dict[str, Any], domains: list[str] | None) -> None:
    if not document:
        return
    if not document.get("provenanceClass"):
        document["provenanceClass"] = "https://schema.opengrid.org/concept/provenance-class/curated"
    if not document.get("dataDomain") and domains:
        document["dataDomain"] = [
            f"https://schema.opengrid.org/concept/data-domain/{d}" for d in domains
        ]
        document["inferredAssignment"] = True
        document["inferenceBasis"] = (
            (document.get("inferenceBasis") or "")
            + " Filed under the domains the harvest source declares for itself; "
            "no domain term matched the record's own text."
        ).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="var/site/ops.sqlite3")
    parser.add_argument("--source", default="aws_open_data")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    arms = (
        ("as built", False, False),
        ("with repairs", True, False),
        ("ceiling: repairs + both fields filled", True, True),
    )
    for label, repair, backstop in arms:
        result = replay(args.database, source=args.source, repair=repair, backstop=backstop)
        counts = result["counts"]
        print(f"\n{label}:")
        print(
            f"  {counts.get('seen', 0)} payloads  ->  "
            f"{counts.get('queued', 0)} queued, {counts.get('flagged', 0)} flagged, "
            f"{counts.get('error', 0)} errors"
        )
        for path, n in result["blockers"]:
            print(f"    {n:5d}  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
