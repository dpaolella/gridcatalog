"""LLM-assisted drafting of fields the source did not carry (WP-3.6).

PRD §7.4 draws the line and ADR-0005 says how it is held:

> Enrichment may draft: summary, data domain assignment, provenance class,
> supported and excluded analysis types, coverage facets, field labels and
> definitions, and candidate concept mappings.
>
> Enrichment may never invent: identifiers, licenses, access URLs, byte sizes,
> or provenance links. **If the source does not state it, the field stays empty
> and the record's completeness level reflects that. A fabricated license is
> worse than a missing one.**

Three enforcement layers, and **not one of them is a prompt**:

1. :data:`ENRICHABLE_FIELDS` is a closed set, and the model's output is filtered
   against it *after* the call. A model that returns a licence has its licence
   dropped on the floor and the drop is logged. The prompt asks for the right
   things too, but the prompt is a courtesy — the filter is the control.
2. Every drafted value is written under ``og:enrichmentBasis "inferred"`` with
   the model id and prompt version. That is a SHACL constraint, so a record
   whose enriched value lost its basis fails validation rather than passing
   quietly.
3. A field with no confident concept mapping gets an explicit ``og:conceptGap``
   with a reason. Never a silent omission: a missing field means "not
   captured", never "does not exist".

**Never overwrite.** Enrichment fills gaps. A value the source stated is a fact
about the source; replacing it with a nicer-sounding inference is the single
most damaging thing this component could do, because the result is
indistinguishable from real metadata at the point of use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from datahub.config import Settings, get_settings
from datahub.harvest.enrich.client import (
    Completion,
    EnrichmentUnavailable,
    LlmClient,
    make_client,
)
from datahub.logging import get_logger
from datahub.namespaces import (
    DATASET_BASE,
    SCHEME_ANALYSIS_TYPE,
    SCHEME_DATA_DOMAIN,
    SCHEME_PROVENANCE_CLASS,
)

log = get_logger(__name__)

#: The closed set. Enforced by filtering the model's output, not by asking.
#:
#: Every term here is a *description* of a dataset — what it is for, what it
#: covers, what its columns mean. Every term deliberately absent is a *fact
#: about the world* that only the source can state: what it is called, who may
#: use it, where it lives, how big it is, what it came from.
ENRICHABLE_FIELDS: frozenset[str] = frozenset(
    {
        "summary",
        "dataDomain",
        "provenanceClass",
        "supportedAnalysis",
        "excludedAnalysis",
        "exclusionRationale",
        "spatialGranularity",
        "spatialLabel",
        "timeResolution",
        "updateCadence",
        "geospatialPrimary",
        "keyword",
        # Field-level: labels and definitions for columns the source named but
        # did not describe. The field's *identity* still comes from the source.
        "fieldLabels",
        "conceptCandidates",
    }
)

#: Named so a rejection can say which rule it broke, and so the test suite can
#: assert on the category rather than on a list membership.
FORBIDDEN_REASON: dict[str, str] = {
    "license": "a fabricated licence is worse than a missing one (PRD §7.4)",
    "licenseNote": "licence terms are the source's to state",
    "redistributionAllowed": "a reuse permission cannot be inferred",
    "commercialUseAllowed": "a reuse permission cannot be inferred",
    "shareAlike": "a reuse permission cannot be inferred",
    "accessURL": "an access URL that does not resolve is worse than no URL",
    "downloadURL": "an access URL that does not resolve is worse than no URL",
    "distribution": "access paths come from the source",
    "byteSize": "a size nobody measured is a number that looks measured",
    "checksum": "a checksum nobody computed cannot verify anything",
    "persistentId": "an identifier must resolve",
    "conceptDoi": "an identifier must resolve",
    "versionDoi": "an identifier must resolve",
    "identifier": "an identifier must resolve",
    "upstreamSource": "a provenance link is a claim about another dataset",
    "wasDerivedFrom": "a provenance link is a claim about another dataset",
    "supersedes": "a version relation is a claim about another dataset",
    "supersededBy": "a version relation is a claim about another dataset",
    "id": "identity is minted, never drafted",
    "reviewState": "only a steward confirms a record (PRD §7.6)",
    "completenessLevel": "the level is computed from what the record carries",
    "visibility": "an entitlement decision is never a model's to make",
    "qualityGrade": "grades are computed by the semantic layer",
}

#: How each term is written into the record. Terms whose values are concepts
#: get their scheme prefixed, so the model returns "DD5" and the record carries
#: the IRI — a model asked for full IRIs will eventually invent one.
CONCEPT_SCHEMES: dict[str, str] = {
    "dataDomain": f"{SCHEME_DATA_DOMAIN}/",
    "provenanceClass": f"{SCHEME_PROVENANCE_CLASS}/",
    "supportedAnalysis": f"{SCHEME_ANALYSIS_TYPE}/",
    "excludedAnalysis": f"{SCHEME_ANALYSIS_TYPE}/",
}

SYSTEM_PROMPT = """\
You are drafting catalog metadata for an open power-system data catalog.

You describe datasets. You do not state facts about them that only their
publisher can state. If the source text does not support a value, omit the
field — an omitted field is recorded honestly as "not captured", and a guess is
indistinguishable from a checked fact at the point of use.

Never return a licence, an access URL, an identifier, a file size, a checksum,
or a link to another dataset, even if you are confident. Those are filtered out
and the attempt is logged.

Answer only from the source text you are given. Where you are unsure of a
concept mapping, return it in `conceptGaps` with the reason rather than
guessing a concept."""


@dataclass(slots=True)
class EnrichmentResult:
    """What enrichment did, in enough detail to audit and to undo."""

    #: Terms written, and the value written under each.
    drafted: dict[str, Any] = field(default_factory=dict)
    #: Terms the model returned that the allow-list refused, with the reason.
    refused: dict[str, str] = field(default_factory=dict)
    #: Fields the model could not map to a concept, and why.
    gaps: list[dict[str, str]] = field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None
    skipped_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def enriched(self) -> bool:
        return bool(self.drafted)

    @property
    def summary(self) -> str:
        if self.skipped_reason:
            return f"not enriched: {self.skipped_reason}"
        parts = [f"drafted {len(self.drafted)}"]
        if self.refused:
            parts.append(f"refused {len(self.refused)}")
        if self.gaps:
            parts.append(f"{len(self.gaps)} concept gaps")
        return ", ".join(parts)


class Enricher:
    """Drafts the fields a source left empty, within the allow-list."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: LlmClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or make_client(self.settings)

    # ---- the entry point -------------------------------------------------

    def enrich(self, document: dict[str, Any]) -> EnrichmentResult:
        """Draft what is missing, and return the record unchanged if it cannot.

        A record that is not enriched is a record with fewer fields, which is a
        completeness level rather than a failure. Nothing here raises on an
        unavailable model.
        """
        if not self.settings.enrichment_enabled:
            return EnrichmentResult(skipped_reason="enrichment is disabled")

        wanted = self.missing_fields(document)
        if not wanted:
            return EnrichmentResult(skipped_reason="nothing enrichable is missing")

        try:
            completion = self.client.complete(
                self.prompt(document, wanted),
                schema=self.schema(wanted),
                system=SYSTEM_PROMPT,
            )
        except EnrichmentUnavailable as exc:
            log.info("enrichment skipped", dataset=document.get("id"), reason=str(exc))
            return EnrichmentResult(skipped_reason=str(exc))

        return self.merge(document, completion)

    def missing_fields(self, document: dict[str, Any]) -> list[str]:
        """Enrichable terms this record does not already carry.

        Only the missing ones are asked for, because a model shown a field it
        is not being asked about will sometimes return an improved version of
        it — and the merge would then have to decide whether to take it. Not
        asking is a cheaper guarantee than not accepting.
        """
        return sorted(
            term
            for term in ENRICHABLE_FIELDS
            if term not in ("fieldLabels", "conceptCandidates") and not document.get(term)
        )

    # ---- the call --------------------------------------------------------

    def prompt(self, document: dict[str, Any], wanted: list[str]) -> str:
        """One field group per call, with the source metadata as context.

        The record is passed as the *source's* words rather than as our JSON-LD,
        so the model is reading a dataset description rather than pattern-
        matching on a schema it might then try to complete.
        """
        lines = [
            "Source metadata for one dataset:",
            "",
            f"Title: {document.get('title', '(none)')}",
        ]
        if description := document.get("description"):
            lines.append(f"Description: {description}")
        if keywords := document.get("keyword"):
            lines.append(f"Keywords: {', '.join(str(k) for k in keywords)}")
        if formats := [
            d.get("formatLabel") for d in document.get("distribution", []) if d.get("formatLabel")
        ]:
            lines.append(f"Distribution formats: {', '.join(formats)}")
        if fields := document.get("hasField"):
            names = [f.get("fieldId") or f.get("localName") for f in fields][:40]
            lines.append(f"Column names: {', '.join(str(n) for n in names if n)}")

        lines += [
            "",
            "Draft only these fields: " + ", ".join(wanted) + ".",
            "Omit any you cannot support from the text above.",
        ]
        return "\n".join(lines)

    def schema(self, wanted: list[str]) -> dict[str, Any]:
        """A tool schema restricted to the fields being asked for.

        The schema is the first filter and the allow-list is the second. Both,
        because a schema constrains a cooperative model and the allow-list
        constrains every other kind.
        """
        properties: dict[str, Any] = {}
        for term in wanted:
            properties[term] = _FIELD_SCHEMAS.get(term, {"type": "string"})
        properties["conceptGaps"] = {
            "type": "array",
            "description": "Fields you could not map to a concept, and why.",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["field", "reason"],
            },
        }
        return {"type": "object", "properties": properties, "required": []}

    # ---- the filter ------------------------------------------------------

    def merge(self, document: dict[str, Any], completion: Completion) -> EnrichmentResult:
        """Apply the model's answer, dropping everything it was not allowed to say.

        Two rules, both enforced here rather than upstream:

        * A term outside the allow-list is dropped and logged, whatever the
          model called it and however plausible the value.
        * A term the record already carries is left alone. Enrichment fills
          gaps; a value the source stated is a fact about the source, and
          replacing it with a nicer-sounding inference is the most damaging
          thing this component could do.
        """
        result = EnrichmentResult(
            model=completion.model,
            prompt_version=completion.prompt_version,
            usage=dict(completion.usage),
        )

        for term, value in completion.data.items():
            if term == "conceptGaps":
                result.gaps = _clean_gaps(value)
                continue
            if term not in ENRICHABLE_FIELDS:
                reason = FORBIDDEN_REASON.get(term, "not in the enrichable field set (ADR-0005)")
                result.refused[term] = reason
                log.warning(
                    "enrichment returned a forbidden field",
                    dataset=document.get("id"),
                    field=term,
                    reason=reason,
                )
                continue
            if document.get(term):
                # Not refused — the model was not asked for it and answering
                # anyway is harmless as long as it is ignored.
                continue
            cleaned = _clean(term, value)
            if cleaned is not None:
                result.drafted[term] = cleaned

        return result

    # ---- writing it back -------------------------------------------------

    def apply(self, document: dict[str, Any], result: EnrichmentResult) -> dict[str, Any]:
        """Write drafted values into a record, with their provenance.

        Returns a new document; the caller decides whether to keep it. The
        basis, model and prompt version go on together because the SHACL shape
        requires all three — a value tagged ``inferred`` without a model and a
        prompt version fails validation, which is what makes a bad prompt's
        output identifiable and revocable in bulk.
        """
        if not result.drafted and not result.gaps:
            return document

        enriched = dict(document)
        enriched.update(result.drafted)

        if result.drafted:
            enriched["enrichedField"] = sorted(result.drafted)
            enriched["enrichmentBasis"] = "inferred"
            enriched["enrichmentModel"] = result.model
            enriched["enrichmentPromptVersion"] = result.prompt_version

        if result.gaps:
            slug = str(document.get("id", "")).rsplit("/", 1)[-1]
            enriched["conceptGap"] = [
                {
                    "id": f"{DATASET_BASE}{slug}#gap-{index}",
                    "type": "ConceptGap",
                    "gapReason": gap["reason"],
                    **({"fieldId": gap["field"]} if gap.get("field") else {}),
                }
                for index, gap in enumerate(result.gaps)
            ]
        return enriched


# ---------------------------------------------------------------------------
# Value handling
# ---------------------------------------------------------------------------

_FIELD_SCHEMAS: dict[str, Any] = {
    "summary": {
        "type": "string",
        "description": "One sentence, under 200 characters, saying what the dataset is.",
    },
    "dataDomain": {
        "type": "array",
        "items": {"type": "string", "pattern": "^DD([1-9]|10)$"},
        "description": "DD1-DD10 codes only.",
    },
    "provenanceClass": {
        "type": "string",
        "enum": [
            "primary",
            "curated",
            "modeled",
            "reanalysis",
            "derived",
            "synthetic",
            "osmDerived",
            "institutional",
        ],
    },
    "supportedAnalysis": {"type": "array", "items": {"type": "string"}},
    "excludedAnalysis": {"type": "array", "items": {"type": "string"}},
    "exclusionRationale": {"type": "string"},
    "spatialGranularity": {
        "type": "string",
        "enum": ["nodal", "zonal", "gridded", "administrative", "point"],
    },
    "spatialLabel": {"type": "array", "items": {"type": "string"}},
    "timeResolution": {"type": "string"},
    "updateCadence": {
        "type": "string",
        "description": "An ISO 8601 duration, or irregular / on-demand / discontinued.",
    },
    "geospatialPrimary": {"type": "boolean"},
    "keyword": {"type": "array", "items": {"type": "string"}},
}

#: Caps. A model that returns forty keywords or a 3,000-character summary is not
#: wrong exactly, but the record becomes unreadable and the index unhelpful.
_LIMITS: dict[str, int] = {"summary": 300, "exclusionRationale": 500}
_MAX_ITEMS = 12


def _clean(term: str, value: Any) -> Any:
    """Coerce a model's value into the shape the record expects, or drop it."""
    scheme = CONCEPT_SCHEMES.get(term)

    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()][:_MAX_ITEMS]
        if scheme:
            items = [v if v.startswith("http") else f"{scheme}{v}" for v in items]
        return items or None

    if isinstance(value, bool):
        return value

    text = str(value).strip()
    if not text:
        return None
    if scheme:
        return text if text.startswith("http") else f"{scheme}{text}"
    if limit := _LIMITS.get(term):
        return text[:limit].rstrip()
    return text


def _clean_gaps(value: Any) -> list[dict[str, str]]:
    """A gap with no reason is not a gap marker.

    X4 exists so a missing mapping is visible and explained. "I could not map
    this" with no reason is the silent omission the marker replaces.
    """
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        out.append({"field": str(item.get("field") or "").strip(), "reason": reason})
    return out[:_MAX_ITEMS]


__all__ = [
    "CONCEPT_SCHEMES",
    "ENRICHABLE_FIELDS",
    "FORBIDDEN_REASON",
    "SYSTEM_PROMPT",
    "Enricher",
    "EnrichmentResult",
]
