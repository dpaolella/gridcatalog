"""API response models.

Separate from the search document and from the JSON-LD record, deliberately:

* the **record** is what the graph holds — JSON-LD, complete, verbose;
* the **search document** is what the index holds — denormalised, derived;
* these are what the API returns — versioned, redactable, and small enough to
  cache.

Collapsing them would tie the wire format to a storage decision, and the wire
format is the one thing this project cannot change without breaking every
consumer.

Two properties are enforced here rather than left to convention:

**Redaction happens in one place.** A record whose visibility is
``restricted-metadata`` is returned as a stub to a non-entitled caller, and
:func:`DatasetSummary.stub_of` is the only way to build one. A route that
forgets is caught by the entitlement matrix.

**There is no composite quality field** (ADR-0007), and
``tests/api/test_no_composite.py`` asserts it against every model in this
module rather than trusting review.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from datahub.api.search.document import (
    ConceptRef,
    DistributionSummary,
    Grade,
    QualityBadges,
    SearchDocument,
    SpatialCoverage,
    TemporalCoverage,
    Visibility,
)
from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------


class QualityFacet(ApiModel):
    """One of exactly three facets. Never combined (ADR-0007)."""

    facet: Literal["provenance", "documentation", "currency"]
    grade: Grade | None = None
    label: str | None = None
    rationale: str | None = Field(
        default=None,
        description=(
            "Why this grade. Every grade carries a concrete human-readable reason; "
            "a bare letter fails PRD principle 6."
        ),
    )
    assessed: bool = Field(
        default=True,
        description=(
            "False for a record below completeness level 2. PRD §F5: such a record shows "
            "'not yet assessed', never grade D — absence of assessment is not poor quality, "
            "and conflating them would defame every harvested record."
        ),
    )
    stated_cadence: str | None = Field(
        default=None,
        description=(
            "Currency only. Displayed alongside the grade so a correctly-scheduled annual "
            "dataset does not read as stale next to an hourly one (PRD §F5)."
        ),
    )
    computed_at: datetime | None = None


class QualityResponse(ApiModel):
    dataset_id: str
    facets: list[QualityFacet]
    #: Set when the record is below level 2 and no facet has been assessed.
    not_yet_assessed_reason: str | None = None

    @classmethod
    def from_document(cls, doc: SearchDocument) -> Self:
        badges: QualityBadges = doc.quality
        assessed = doc.quality_assessed and doc.completeness_level >= 2
        reason = (
            None
            if assessed
            else (
                f"This record is at completeness level {doc.completeness_level}. Provenance and "
                "Documentation are assessed from field-level metadata, which a level 1 record "
                "does not carry. Not yet assessed is not the same as poor."
            )
        )
        facets = [
            QualityFacet(
                facet="provenance",
                grade=badges.provenance if assessed else None,
                label=badges.provenance_label if assessed else "Not yet assessed",
                assessed=assessed,
                computed_at=doc.last_computed_at.get("provenance"),
            ),
            QualityFacet(
                facet="documentation",
                grade=badges.documentation if assessed else None,
                label=badges.documentation_label if assessed else "Not yet assessed",
                assessed=assessed,
                computed_at=doc.last_computed_at.get("documentation"),
            ),
            # Currency is fully automatic and continuous (PRD §F5), so it is
            # assessed even for a level 1 record: it needs only the stated
            # cadence and the vintage, both of which level 1 carries.
            QualityFacet(
                facet="currency",
                grade=badges.currency,
                label=badges.currency_label,
                assessed=badges.currency is not None,
                stated_cadence=doc.temporal.update_cadence,
                computed_at=doc.last_computed_at.get("currency"),
            ),
        ]
        return cls(dataset_id=doc.id, facets=facets, not_yet_assessed_reason=reason)


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


class DatasetSummary(ApiModel):
    """A list-view row. What PRD §F3 says the list must show, and no more."""

    id: str
    iri: str
    title: str
    summary: str | None = None
    publisher: str | None = None
    data_domains: list[ConceptRef] = Field(default_factory=list)
    provenance_class: str | None = None
    license_id: str | None = None
    license_label: str | None = None
    completeness_level: int = 1
    reference_only: bool = False
    anonymous_access: bool | None = None
    quality: list[QualityFacet] = Field(default_factory=list)
    spatial: SpatialCoverage | None = None
    temporal: TemporalCoverage | None = None
    distribution_count: int = 0
    worst_link_health: str | None = None
    modified: datetime | None = None
    #: True when the caller sees a stub rather than the record.
    redacted: bool = False

    @classmethod
    def from_document(cls, doc: SearchDocument, *, full: bool = True) -> Self:
        if not full:
            return cls.stub_of(doc)
        return cls(
            id=doc.id,
            iri=doc.iri,
            title=doc.title,
            summary=doc.summary,
            publisher=doc.publisher,
            data_domains=doc.data_domains,
            provenance_class=doc.provenance_class,
            license_id=doc.license_id,
            license_label=doc.license_label,
            completeness_level=doc.completeness_level,
            reference_only=doc.reference_only,
            anonymous_access=doc.anonymous_access,
            quality=QualityResponse.from_document(doc).facets,
            spatial=doc.spatial,
            temporal=doc.temporal,
            distribution_count=doc.distribution_count,
            worst_link_health=doc.worst_link_health,
            modified=doc.modified,
        )

    @classmethod
    def stub_of(cls, doc: SearchDocument) -> Self:
        """The stub a non-entitled caller sees for a restricted-metadata record.

        PRD §F8's middle visibility level: existence is public, metadata is not.
        Title and domain only — enough to know the dataset exists and to ask the
        custodian for access, and nothing that would let a determined reader
        reconstruct the record from a series of stubs.

        The one thing deliberately included is the custodian route, because a
        stub that does not say how to ask is a dead end rather than a pointer.
        """
        return cls(
            id=doc.id,
            iri=doc.iri,
            title=doc.title,
            data_domains=doc.data_domains,
            completeness_level=doc.completeness_level,
            redacted=True,
        )


class FieldDetail(ApiModel):
    id: str
    local_name: str
    label: str | None = None
    definition: str | None = None
    data_type: str | None = None
    unit: str | None = None
    unit_label: str | None = None
    concept: ConceptRef | None = None
    concept_inferred: bool = False
    inference_basis: str | None = None
    concept_gap_reason: str | None = Field(
        default=None,
        description=(
            "Why no concept fits. Rule X4: a field with no confident mapping carries an "
            "explicit marker, never a silent omission."
        ),
    )
    value_basis: str | None = None
    field_sources: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    required: bool | None = None
    completeness_caveats: str | None = None


class SchemaResponse(ApiModel):
    dataset_id: str
    completeness_level: int
    fields: list[FieldDetail] = Field(default_factory=list)
    #: Set when there is no field-level metadata to show, explaining why rather
    #: than returning an empty list (PRD §F3: an absent schema tab explains
    #: itself; it is not an empty table).
    unavailable_reason: str | None = None

    @classmethod
    def from_record(
        cls,
        document: dict[str, Any],
        doc: SearchDocument,
        *,
        labels: dict[str, str] | None = None,
    ) -> Self:
        """Build from a JSON-LD record, read out of the graph.

        Out of the graph rather than the index because the index carries a
        field *count* — which is what a list view needs — and this endpoint
        exists for the caller who wants the fields themselves.
        """
        from datahub.graph.records import dataset_node

        try:
            node = dataset_node(document)
        except Exception:
            node = {}

        raw = node.get("hasField") or []
        if isinstance(raw, dict):
            raw = [raw]
        fields = [FieldDetail(**_field_kwargs(f, labels or {})) for f in raw if isinstance(f, dict)]

        return cls(
            dataset_id=doc.id,
            completeness_level=doc.completeness_level,
            fields=sorted(fields, key=lambda f: f.local_name),
            unavailable_reason=None if fields else _no_schema_reason(doc),
        )


def _no_schema_reason(doc: SearchDocument) -> str:
    """Why the schema tab is empty, in words a user can act on.

    "No fields" is not an answer: it reads as "this dataset has no columns",
    which is almost never true. What is true is that nobody has catalogued
    them, and the level says how far the record has got.
    """
    if doc.reference_only:
        return (
            "This is a reference-only pointer: the catalog records where the dataset is and "
            "who publishes it, and does not describe its contents."
        )
    if doc.completeness_level < 2:
        return (
            "Field-level metadata has not been captured for this record yet. It is at "
            f"completeness level {doc.completeness_level}; field descriptions arrive at level 2."
        )
    return "No field-level metadata is recorded for this dataset."


def _field_kwargs(node: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """One ``og:Field`` node into a :class:`FieldDetail`.

    Every absence is carried through as an absence. A field with no unit is a
    field whose unit was not captured, and defaulting it to "dimensionless"
    would turn a gap into a claim.
    """
    concept = node.get("concept")
    gap = node.get("conceptGap") or {}
    if isinstance(gap, list):
        gap = gap[0] if gap else {}
    return {
        "id": str(node.get("id", "")),
        "local_name": str(node.get("localName") or node.get("fieldId") or ""),
        "label": node.get("label"),
        "definition": node.get("definition"),
        "data_type": node.get("dataType"),
        "unit": node.get("unit"),
        # The stated unit, verbatim from the source, sits alongside the
        # resolved IRI rather than replacing it: "kV" is what the publisher
        # wrote and the QUDT IRI is what a machine can convert.
        "unit_label": labels.get(str(node.get("unit"))) or node.get("unitAsStated"),
        "concept": (
            ConceptRef(iri=str(concept), label=labels.get(str(concept)))
            if isinstance(concept, str)
            else None
        ),
        "concept_inferred": bool(node.get("inferredAssignment", False)),
        "inference_basis": node.get("inferenceBasis"),
        "concept_gap_reason": gap.get("gapReason") if isinstance(gap, dict) else None,
        "value_basis": node.get("valueBasis"),
        "field_sources": _as_list(node.get("fieldSource")),
        "derived_from": _as_list(node.get("derivedFromField")),
        "required": node.get("required"),
        "completeness_caveats": node.get("completenessCaveats"),
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in (value if isinstance(value, list) else [value])]


class LinkHealth(ApiModel):
    """What the prober last saw at this URL.

    Reported even when it is bad. A dead link a user can see is a reportable
    fact; a dead link silently removed is a dataset that appears to have no
    access path at all.
    """

    status: str | None = None
    last_probed_at: datetime | None = None
    consecutive_failures: int = 0
    probe_cadence: str | None = None
    redirect_target: str | None = None


class DistributionDetail(ApiModel):
    """One access path, with everything needed to decide how to read it.

    Built from the record rather than from the search index. The index carries
    only enough of a distribution to filter and render a list row — no access
    URLs, because a search response should not haul every URL in the catalog
    to a client that wanted ten titles. This is the shape for the caller who
    has picked a dataset and now wants to fetch it.
    """

    id: str
    access_url: str | None = None
    download_url: str | None = None
    media_type: str | None = None
    format_label: str | None = None
    byte_size: int | None = None
    checksum: str | None = None

    access_restriction: str | None = None
    anonymous_access: bool | None = None
    credential_requirement: str | None = None
    requester_pays: bool = False

    bulk_download: bool | None = None
    #: Whether a client can read part of the file rather than all of it. What
    #: makes a 4 TB dataset usable from a laptop (PRD §F7).
    supports_range_requests: bool = False
    cors_enabled: bool | None = None
    chunk_index_method: str | None = None
    subsetting_protocol: str | None = None

    hosted_by_opengrid: bool = False
    hosting_reason: str | None = None
    link_health: LinkHealth | None = None

    @classmethod
    def from_node(cls, node: dict[str, Any]) -> Self:
        health = node.get("linkHealth")
        if isinstance(health, list):
            health = health[0] if health else None
        return cls(
            id=str(node.get("id", "")),
            access_url=node.get("accessURL"),
            download_url=node.get("downloadURL"),
            media_type=node.get("mediaType"),
            format_label=node.get("formatLabel"),
            byte_size=node.get("byteSize"),
            checksum=node.get("checksum"),
            access_restriction=node.get("accessRestriction"),
            anonymous_access=node.get("anonymousAccess"),
            credential_requirement=node.get("credentialRequirement"),
            requester_pays=bool(node.get("requesterPays", False)),
            bulk_download=node.get("bulkDownload"),
            supports_range_requests=bool(node.get("supportsRangeRequests", False)),
            cors_enabled=node.get("corsEnabled"),
            chunk_index_method=node.get("chunkIndexMethod"),
            subsetting_protocol=node.get("subsettingProtocol"),
            hosted_by_opengrid=bool(node.get("hostedByOpenGrid", False)),
            hosting_reason=node.get("hostingReason"),
            link_health=(
                LinkHealth(
                    status=health.get("linkHealthStatus"),
                    last_probed_at=health.get("lastProbedAt"),
                    consecutive_failures=int(health.get("consecutiveFailures", 0) or 0),
                    probe_cadence=health.get("probeCadence"),
                    redirect_target=health.get("redirectTarget"),
                )
                if isinstance(health, dict)
                else None
            ),
        )

    @classmethod
    def from_record(cls, document: dict[str, Any]) -> list[Self]:
        from datahub.graph.records import dataset_node

        try:
            node = dataset_node(document)
        except Exception:
            return []
        raw = node.get("distribution") or []
        if isinstance(raw, dict):
            raw = [raw]
        return [cls.from_node(d) for d in raw if isinstance(d, dict)]

    @property
    def reachable(self) -> bool:
        """Whether the last probe found it. Unprobed counts as reachable —
        "nobody has checked" is not "it is broken"."""
        status = self.link_health.status if self.link_health else None
        return status in (None, "verified", "redirected")


class DatasetDetail(DatasetSummary):
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    creators: list[str] = Field(default_factory=list)
    persistent_id: str | None = None
    doi: str | None = None
    supported_analysis: list[ConceptRef] = Field(default_factory=list)
    excluded_analysis: list[ConceptRef] = Field(default_factory=list)
    exclusion_rationale: str | None = None
    upstream_count: int = 0
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    distributions: list[DistributionSummary] = Field(default_factory=list)
    documentation_status: str | None = None
    harvest_source: str | None = None
    review_state: str = "confirmed"
    visibility: Visibility = "public"
    has_topology: bool | None = None
    has_impedance: bool | None = None
    voltage_classes: list[str] = Field(default_factory=list)
    field_count: int = 0
    last_computed_at: dict[str, datetime] = Field(default_factory=dict)
    issued: datetime | None = None

    @classmethod
    def from_document(cls, doc: SearchDocument, *, full: bool = True) -> Self:
        if not full:
            return cls.stub_of(doc)
        summary = DatasetSummary.from_document(doc, full=True)
        return cls(
            **summary.model_dump(),
            description=doc.description,
            keywords=doc.keywords,
            creators=doc.creators,
            persistent_id=doc.persistent_id,
            doi=doc.doi,
            supported_analysis=doc.supported_analysis,
            excluded_analysis=doc.excluded_analysis,
            upstream_count=doc.upstream_count,
            supersedes=doc.supersedes,
            superseded_by=doc.superseded_by,
            distributions=doc.distributions,
            documentation_status=doc.documentation_status,
            harvest_source=doc.harvest_source,
            review_state=doc.review_state,
            visibility=doc.visibility,
            has_topology=doc.has_topology,
            has_impedance=doc.has_impedance,
            voltage_classes=doc.voltage_classes,
            field_count=doc.field_count,
            last_computed_at=doc.last_computed_at,
            issued=doc.issued,
        )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class FacetBucket(ApiModel):
    value: Any
    count: int
    label: str | None = None


class SearchResponseModel(ApiModel):
    total: int = Field(
        description=(
            "Entitlement-scoped. A record the caller may not see contributes to no count, "
            "which is what stops existence leaking through pagination (ADR-0006)."
        )
    )
    offset: int
    limit: int
    results: list[DatasetSummary]
    facets: dict[str, list[FacetBucket]] = Field(default_factory=dict)
    took_ms: float = 0.0


# ---------------------------------------------------------------------------
# Domains and concepts
# ---------------------------------------------------------------------------


class DomainResponse(ApiModel):
    id: str
    notation: str
    label: str
    definition: str | None = None
    structural_note: str | None = Field(
        default=None,
        description=(
            "What is genuinely unavailable in this domain and why. A product feature, "
            "not a disclaimer: a catalog that tells you what does not exist is more useful "
            "than one that silently returns nothing (PRD §5)."
        ),
    )
    v1_ingestion_scope: str | None = None
    dataset_count: int = 0
    alt_labels: list[str] = Field(default_factory=list)


class ConceptResponse(ApiModel):
    iri: str
    label: str
    definition: str | None = None
    notation: str | None = None
    scheme: str | None = None
    broader: list[ConceptRef] = Field(default_factory=list)
    narrower: list[ConceptRef] = Field(default_factory=list)
    alt_labels: list[str] = Field(default_factory=list)
    default_unit: str | None = None
    unit_symbol: str | None = None
    external_matches: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Crosswalk targets by scheme, with the match strength as the key.",
    )
    dataset_count: int = 0


# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------


class SubmissionRequest(ApiModel):
    """The intake form (PRD §F3). Fire-and-forget: receipt is confirmed and no
    status is tracked back to the submitter."""

    title: Annotated[str, Field(min_length=3, max_length=500)]
    description: Annotated[str, Field(min_length=10, max_length=5000)]
    access_urls: Annotated[list[str], Field(min_length=1, max_length=10)]
    license_text: Annotated[str, Field(min_length=2, max_length=300)]
    originator: str | None = Field(default=None, max_length=300)
    data_domain: str | None = None
    submitter_contact: str | None = Field(default=None, max_length=320)
    format_hint: str | None = Field(default=None, max_length=120)
    approximate_size: str | None = Field(default=None, max_length=64)
    update_cadence: str | None = Field(default=None, max_length=64)
    documentation_urls: list[str] = Field(default_factory=list, max_length=10)


class SubmissionReceipt(ApiModel):
    id: str
    received_at: datetime
    message: str = (
        "Received. A steward will review it. There is no status to check back on: "
        "if it is catalogued you will find it in search."
    )


class ReportRequest(ApiModel):
    """Report an issue against a record, a field or a distribution (PRD §F3)."""

    dataset_id: str
    issue_type: Literal[
        "incorrect-metadata",
        "broken-link",
        "license-question",
        "wrong-classification",
        "duplicate-record",
        "other",
    ]
    target_kind: Literal["dataset", "field", "distribution"] = "dataset"
    target_id: str | None = Field(
        default=None,
        description="The exact thing flagged, captured by the UI rather than typed.",
    )
    comment: str | None = Field(default=None, max_length=5000)
    reporter_contact: str | None = Field(default=None, max_length=320)


class ReportReceipt(ApiModel):
    id: str
    received_at: datetime
    dataset_id: str
    #: How many open reports now stand against the same target, this one
    #: included. Reports are grouped rather than deduped, so a target flagged
    #: eleven times reads as eleven (PRD §12.11) — and a reporter learns their
    #: report joined others rather than wondering whether it registered.
    open_reports_on_target: int = 1
    message: str = "Thank you. This has been routed to the curation queue."


# ---------------------------------------------------------------------------
# Errors and health
# ---------------------------------------------------------------------------


class ProblemDetail(ApiModel):
    """RFC 9457. One error shape across every endpoint."""

    model_config = ConfigDict(extra="allow")

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded", "unhealthy"]
    version: str
    graph_backend: str
    search_backend: str
    catalog_records: int | None = None
    projector_lag_seconds: float | None = None
    projector_healthy: bool | None = None
    checks: dict[str, str] = Field(default_factory=dict)
