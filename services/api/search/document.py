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
    #: Optional because a record can carry a concept IRI this deployment holds
    #: no label for — a crosswalk target, or a concept added to the vocabulary
    #: after the record was written. Returning the IRI with no label is honest;
    #: inventing a label from the IRI's last segment is not.
    label: str | None = None
    notation: str | None = None
    #: The concept's plain-language definition. PRD §F4.2: *surface a
    #: plain-language definition alongside each resolved concept, so a field
    #: documented only via CIM/CGMES is understandable without the user owning
    #: that standard.*
    #:
    #: Populated only where a caller asked what a field means — the `/schema`
    #: endpoint. Never in the index: a definition is two or three sentences and
    #: a search result carrying one per concept would multiply the size of
    #: every response for text nobody reads in a list view.
    definition: str | None = None


class SpatialCoverage(BaseModel):
    bbox: list[float] | None = None  # [minLon, minLat, maxLon, maxLat], EPSG:4326
    place_labels: list[str] = Field(default_factory=list)
    place_iris: list[str] = Field(default_factory=list)
    native_crs: str | None = None
    geometry_types: list[str] = Field(default_factory=list)
    granularity: str | None = None  # nodal | zonal | gridded | administrative | point
    #: Grid cell size or positional precision, in metres. The companion to
    #: `granularity`, which is a class and cannot say 30 km versus 4 km (#53).
    resolution_meters: float | None = None
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


class UsageEvidenceRef(BaseModel):
    """A study, tutorial or tool that used this dataset.

    `asserted_by` is not decoration. A data provider's self-report through the
    AWS registry, a citation index, and a steward's reading are three different
    strengths of claim, and a reader who cannot tell them apart is being asked
    to trust the weakest at the strength of the strongest. The UI renders it.

    `url` is required all the way down (see `og:UsageEvidenceShape`): a citation
    a reader cannot follow is an assertion, and this is the one field where an
    unverifiable one does real damage — it gets carried into somebody's
    bibliography and acquires a second life there.
    """

    title: str
    url: str
    kind: str  # publication | tutorial | tool
    author: str | None = None
    asserted_by: str


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
    #: Analyses this dataset is a PRODUCT of, as opposed to an input to (#51).
    output_of_analysis: list[ConceptRef] = Field(default_factory=list)
    #: What this dataset was built from, by IRI — a catalog record where the
    #: upstream is catalogued, its own URL where it is not (#52).
    derived_from: list[str] = Field(default_factory=list)
    #: Longest recorded chain from here to an observational root, or `None`
    #: when it is not established. **None is not zero.** A modelled product
    #: with no lineage recorded must not read as one hop from measurement.
    assumption_depth: int | None = None
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
    #: How many studies, tutorials or tools a source records as having used this
    #: dataset. A count, not the entries: the list belongs on the record page,
    #: and what a search needs is "is there any".
    #:
    #: Zero means **nothing recorded**, never "unused". Coverage is entirely a
    #: property of the source — the AWS registry has a DataAtWork block and most
    #: CKAN and STAC sources have nothing of the kind — so this ranks
    #: catalogues, not datasets, and every label built on it has to say so.
    usage_evidence_count: int = 0
    #: The facet field. A boolean rather than the count, because faceting on a
    #: count gives one bucket per distinct number and answers a question nobody
    #: asked; what a reader wants is "show me the ones somebody has used".
    #:
    #: Named for what it is. "Has recorded usage" is true; "is used" is not
    #: something this catalog knows, and a facet labelled that way would present
    #: a gap in a source's schema as a fact about a dataset.
    has_usage_evidence: bool = False
    #: The entries themselves, for the record page. Carried on the document for
    #: the same reason `distributions` is: the record response is built from the
    #: document, so anything the page renders has to be here.
    usage_evidence: list[UsageEvidenceRef] = Field(default_factory=list)
    all_distributions_unreachable: bool = False

    # -- coverage --
    spatial: SpatialCoverage = Field(default_factory=SpatialCoverage)
    temporal: TemporalCoverage = Field(default_factory=TemporalCoverage)

    # -- build and curation state --
    tier: int | None = None
    """Internal build-prioritisation fact. Never rendered as a quality signal
    (PRD §5); the API exposes it only as ``reference_only`` for tier 3."""
    reference_only: bool = False
    pointer_rationale: str | None = None
    """Why a reference-only record has no access path.

    Required by ``og:AccessPathShape`` of any reference-only record with no
    distribution, so on those it is always present — and it is the only thing
    the Downloads tab has to show, since there is nothing to download. Without
    it the reader gets a blank tab where 34 records have their whole point
    (PRD §5: a catalog that says what does not exist)."""
    completeness_level: CompletenessLevel = 1
    review_state: str = "draft"
    harvest_source: str | None = None
    documentation_status: str | None = None
    quality: QualityBadges = Field(default_factory=QualityBadges)
    quality_assessed: bool = False
    #: What a steward or the pipeline recorded about using this dataset.
    #: Projected but never surfaced until #55: 478 of them sat in the graph,
    #: carried through `construct.rq`, and stopped at the document boundary —
    #: so no API caller and no page ever saw one.
    caveats: list[str] = Field(default_factory=list)

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
    # Indexed and projected since it was written, and absent from this map, so
    # nothing could ask for it (#53). Sub-hourly is the single most repeated
    # requirement in the domain assessment — "15-min temporal resolution is
    # needed to model flexibility products" — and it was the one axis of
    # fitness a modeller could not filter on.
    "time_resolution": "temporal.time_resolution",
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
    "has_usage_evidence": "has_usage_evidence",
}

#: Fields a caller may bound with a range rather than match exactly. Kept
#: separate from FACET_FIELDS: faceting a continuous quantity gives one bucket
#: per distinct value and answers a question nobody asked, and `RangeFilter`
#: needs a numeric path rather than a keyword one.
RANGE_FIELDS: dict[str, str] = {
    "completeness_level": "completeness_level",
    "spatial_resolution_m": "spatial.resolution_meters",
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


def range_path(name: str) -> str:
    """Where a range filter reads its value from.

    `RANGE_FIELDS` first, then the facet and sort maps, because two fields were
    range-filterable before this map existed and callers may still name them.
    """
    path = RANGE_FIELDS.get(name) or FACET_FIELDS.get(name) or SORT_FIELDS.get(name)
    if path is None:
        raise KeyError(f"unknown range field: {name}")
    return path
