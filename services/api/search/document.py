"""The search document: the denormalised projection of a catalog record.

This is a contract between three components — the projector writes it, the
search backends index it, the API reads it — so it is defined once, here, and
the field list is asserted by test rather than trusted.

The document is *derived state*. Nothing may be written here that is not
reconstructible from the graph by a full reindex (PRD principle 8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Grade = Literal["A", "B", "C", "D"]
Visibility = Literal["public", "restricted-metadata", "allowlisted-existence"]
CompletenessLevel = Annotated[int, Field(ge=1, le=3)]


class ConceptRef(BaseModel):
    """A SKOS concept as carried in the index: IRI plus its display label."""

    model_config = ConfigDict(frozen=True)

    iri: str
    label: str
    notation: str | None = None


class SpatialCoverage(BaseModel):
    bbox: list[float] | None = None  # [minLon, minLat, maxLon, maxLat], EPSG:4326
    place_labels: list[str] = Field(default_factory=list)
    place_iris: list[str] = Field(default_factory=list)
    native_crs: str | None = None
    geometry_types: list[str] = Field(default_factory=list)
    granularity: str | None = None  # nodal | zonal | gridded | administrative | point
    feature_count: int | None = None


class TemporalCoverage(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    update_cadence: str | None = None  # ISO 8601 duration or a controlled token
    time_resolution: str | None = None


class QualityBadges(BaseModel):
    """Three independent facets. There is deliberately no composite (ADR-0007)."""

    model_config = ConfigDict(frozen=True)

    provenance: Grade | None = None
    documentation: Grade | None = None
    currency: Grade | None = None
    provenance_label: str | None = None
    documentation_label: str | None = None
    currency_label: str | None = None


class DistributionSummary(BaseModel):
    """Enough of a distribution to filter and to render a list row."""

    id: str
    media_type: str | None = None
    format_label: str | None = None
    byte_size: int | None = None
    access_restriction: str | None = None
    anonymous_access: bool | None = None
    bulk_download: bool | None = None
    supports_range_requests: bool = False
    subsetting_protocol: str | None = None
    link_health: str | None = None  # verified | degraded | unreachable | redirected


class SearchDocument(BaseModel):
    """One catalog record, flattened for retrieval."""

    model_config = ConfigDict(extra="forbid")

    # -- identity --
    id: str  # slug, stable, used in URLs
    iri: str
    persistent_id: str | None = None
    doi: str | None = None

    # -- description --
    title: str
    description: str | None = None
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    publisher: str | None = None
    creators: list[str] = Field(default_factory=list)

    # -- classification --
    data_domains: list[ConceptRef] = Field(default_factory=list)
    provenance_class: str | None = None
    supported_analysis: list[ConceptRef] = Field(default_factory=list)
    excluded_analysis: list[ConceptRef] = Field(default_factory=list)
    concepts: list[ConceptRef] = Field(default_factory=list)
    concept_iris_expanded: list[str] = Field(default_factory=list)
    """Concept IRIs plus every broader ancestor, so a query for a parent concept
    matches without the caller enumerating children (PRD §4.6 Q3)."""

    # -- access --
    license_id: str | None = None
    license_label: str | None = None
    license_url: str | None = None
    redistribution_allowed: bool | None = None
    access_restriction: str | None = None
    anonymous_access: bool | None = None
    bulk_download: bool | None = None
    formats: list[str] = Field(default_factory=list)
    distributions: list[DistributionSummary] = Field(default_factory=list)
    distribution_count: int = 0
    has_range_requests: bool = False
    subsetting_protocols: list[str] = Field(default_factory=list)
    worst_link_health: str | None = None
    all_distributions_unreachable: bool = False

    # -- coverage --
    spatial: SpatialCoverage = Field(default_factory=SpatialCoverage)
    temporal: TemporalCoverage = Field(default_factory=TemporalCoverage)

    # -- build and curation state --
    tier: int | None = None
    """Internal build-prioritisation fact. Never rendered as a quality signal
    (PRD §5); the API exposes it only as ``reference_only`` for tier 3."""
    reference_only: bool = False
    completeness_level: CompletenessLevel = 1
    review_state: str = "draft"
    harvest_source: str | None = None
    documentation_status: str | None = None
    quality: QualityBadges = Field(default_factory=QualityBadges)
    quality_assessed: bool = False

    # -- structural --
    has_topology: bool | None = None
    has_impedance: bool | None = None
    voltage_classes: list[str] = Field(default_factory=list)
    field_count: int = 0

    # -- relationships (counts only; the graph holds the edges) --
    upstream_count: int = 0
    inbound_link_count: int = 0
    superseded_by: str | None = None
    supersedes: list[str] = Field(default_factory=list)

    # -- entitlement (ADR-0006) --
    visibility: Visibility = "public"
    entitled_principals: list[str] = Field(default_factory=list)
    custodian_id: str | None = None

    # -- freshness --
    issued: datetime | None = None
    modified: datetime | None = None
    indexed_at: datetime | None = None
    last_computed_at: dict[str, datetime] = Field(default_factory=dict)

    def full_text(self) -> str:
        """The concatenation the free-text index is built from."""
        parts: list[Any] = [
            self.title,
            self.summary,
            self.description,
            self.publisher,
            *self.creators,
            *self.keywords,
            *(c.label for c in self.data_domains),
            *(c.label for c in self.concepts),
            *(c.label for c in self.supported_analysis),
            *self.spatial.place_labels,
            self.license_id,
            self.provenance_class,
            *self.formats,
        ]
        return " ".join(str(p) for p in parts if p)


#: Fields the API is permitted to return. Asserted by test so a composite score
#: cannot be added without the test that forbids it failing (ADR-0007).
SEARCH_DOCUMENT_FIELDS: frozenset[str] = frozenset(SearchDocument.model_fields)

#: Facetable fields and the document path each reads from.
FACET_FIELDS: dict[str, str] = {
    "data_domain": "data_domains.iri",
    "provenance_class": "provenance_class",
    "license": "license_id",
    "access_restriction": "access_restriction",
    "format": "formats",
    "completeness_level": "completeness_level",
    "spatial_granularity": "spatial.granularity",
    "update_cadence": "temporal.update_cadence",
    "provenance_grade": "quality.provenance",
    "documentation_grade": "quality.documentation",
    "currency_grade": "quality.currency",
    "anonymous_access": "anonymous_access",
    "bulk_download": "bulk_download",
    "supported_analysis": "supported_analysis.iri",
    "concept": "concepts.iri",
    "harvest_source": "harvest_source",
    "review_state": "review_state",
    "voltage_class": "voltage_classes",
    "reference_only": "reference_only",
    "link_health": "worst_link_health",
}

#: Sortable fields. Relevance is the default and is not listed here.
SORT_FIELDS: dict[str, str] = {
    "title": "title",
    "modified": "modified",
    "issued": "issued",
    "temporal_start": "temporal.start",
    "temporal_end": "temporal.end",
    "completeness_level": "completeness_level",
    "distribution_count": "distribution_count",
    "inbound_link_count": "inbound_link_count",
}
