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
