"""Flattening a record into a search document.

The one place the graph's shape is translated into the index's shape. Every
field of :class:`~datahub.api.search.document.SearchDocument` that a record can
fill is filled here; a field the record does not carry stays at its default,
because a search document is derived state and a default is honest where an
invented value is not.

Two rules in this module are worth reading before changing anything:

**A record below completeness level 2 has ``quality_assessed`` False.** PRD §F5
is explicit that such a record shows Provenance and Documentation as "not yet
assessed", never as grade D. Absence of assessment is not poor quality, and
conflating them would systematically defame every harvested record — which is
most of the catalog. This is the single most damaging bug available here.

**Tier never becomes a quality signal.** PRD §5: tier is an internal
build-prioritisation fact. It is carried so the review queue can sort on it, and
the only user-facing consequence is ``reference_only`` on tier 3, which explains
why a record has no schema tab.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from datahub.api.search.document import (
    ConceptRef,
    DistributionSummary,
    QualityBadges,
    SearchDocument,
    SpatialCoverage,
    TemporalCoverage,
)
from datahub.graph.records import read_bbox, slug_of
from datahub.namespaces import OG
from rdflib import Graph, URIRef
from rdflib.namespace import DCAT, DCTERMS, SKOS

#: Link-health statuses ordered worst-first, so an aggregate over a record's
#: distributions is a min rather than a special case per pair.
_HEALTH_ORDER = ("unreachable", "degraded", "redirected", "verified")

#: Grade labels from PRD §F5. Held here rather than recomputed in the UI so the
#: API, the SDK and the MCP server all say the same words.
GRADE_LABELS: dict[str, dict[str, str]] = {
    "provenance": {
        "A": "Primary & Traced",
        "B": "Derived & Traced",
        "C": "Traced, Basis Unconfirmed",
        "D": "Untraced",
    },
    "documentation": {
        "A": "Fully documented",
        "B": "Partially documented",
        "C": "Documented via external standard only",
        "D": "Minimal",
    },
    "currency": {
        "A": "Current",
        "B": "Aging",
        # C is deliberately unused on this facet (PRD §F5, §12.3).
        "D": "Superseded",
    },
}


def build_document(
    graph: Graph,
    dataset_iri: str | URIRef,
    *,
    entitled_principals: list[str] | None = None,
    inbound_link_count: int = 0,
) -> SearchDocument:
    """Project one record's subgraph into a search document.

    ``entitled_principals`` comes from the operational allow-list, not from the
    graph — passed in rather than looked up so the projector does not import the
    database layer and can be run against a graph alone.
    """
    iri = URIRef(str(dataset_iri))
    doc_id = slug_of(str(iri))

    level = _int(graph.value(iri, OG.completenessLevel), default=1)
    tier = _int(graph.value(iri, OG.tier))
    distributions = _distributions(graph, iri)
    quality, assessed = _quality(graph, iri, level)
    concepts = _concepts(graph, iri)

    return SearchDocument(
        id=doc_id,
        iri=str(iri),
        persistent_id=_str(graph.value(iri, OG.persistentId)),
        doi=_str(graph.value(iri, OG.versionDoi)) or _str(graph.value(iri, OG.conceptDoi)),
        title=_str(graph.value(iri, DCTERMS.title)) or doc_id,
        description=_str(graph.value(iri, DCTERMS.description)),
        summary=_str(graph.value(iri, OG.summary)),
        keywords=sorted(_strs(graph, iri, DCAT.keyword)),
        publisher=_label_of(graph, graph.value(iri, DCTERMS.publisher)),
        creators=sorted(
            filter(None, (_label_of(graph, c) for c in graph.objects(iri, DCTERMS.creator)))
        ),
        data_domains=_concept_refs(graph, iri, OG.dataDomain),
        provenance_class=_local(graph.value(iri, OG.provenanceClass)),
        supported_analysis=_concept_refs(graph, iri, OG.supportedAnalysis),
        excluded_analysis=_concept_refs(graph, iri, OG.excludedAnalysis),
        concepts=concepts,
        concept_iris_expanded=_expanded_concepts(graph, concepts),
        license_id=_local(graph.value(iri, DCTERMS.license)),
        license_label=_str(graph.value(iri, OG.licenseNote)),
        license_url=_str(graph.value(iri, DCTERMS.license)),
        redistribution_allowed=_bool(graph.value(iri, OG.redistributionAllowed)),
        access_restriction=_local(graph.value(iri, OG.accessRestriction)),
        anonymous_access=_bool(graph.value(iri, OG.anonymousAccess)),
        bulk_download=any(d.bulk_download for d in distributions) or None,
        formats=sorted({d.media_type for d in distributions if d.media_type}),
        distributions=distributions,
        distribution_count=len(distributions),
        has_range_requests=any(d.supports_range_requests for d in distributions),
        subsetting_protocols=sorted(
            {d.subsetting_protocol for d in distributions if d.subsetting_protocol}
        ),
        worst_link_health=_worst_health(distributions),
        all_distributions_unreachable=bool(distributions)
        and all(d.link_health == "unreachable" for d in distributions),
        spatial=_spatial(graph, iri),
        temporal=_temporal(graph, iri),
        tier=tier,
        reference_only=_bool(graph.value(iri, OG.referenceOnly)) or tier == 3,
        completeness_level=level,
        review_state=_str(graph.value(iri, OG.reviewState)) or "draft",
        harvest_source=_str(graph.value(iri, OG.harvestSource)),
        documentation_status=_str(graph.value(iri, OG.documentationStatus)),
        quality=quality,
        quality_assessed=assessed,
        has_topology=_bool(graph.value(iri, OG.hasTopology)),
        has_impedance=_bool(graph.value(iri, OG.hasImpedance)),
        voltage_classes=sorted(_strs(graph, iri, OG.voltageClass)),
        field_count=len(list(graph.objects(iri, OG.hasField))),
        upstream_count=len(
            set(graph.objects(iri, OG.upstreamSource)) | set(graph.objects(iri, OG.wasDerivedFrom))
        ),
        inbound_link_count=inbound_link_count,
        superseded_by=_str(graph.value(iri, OG.supersededBy)),
        supersedes=sorted(str(s) for s in graph.objects(iri, OG.supersedes)),
        visibility=_visibility(graph.value(iri, OG.visibility)),
        entitled_principals=sorted(entitled_principals or []),
        custodian_id=_str(graph.value(iri, OG.custodian)),
        issued=_dt(graph.value(iri, DCTERMS.issued)),
        modified=_dt(graph.value(iri, DCTERMS.modified)),
        indexed_at=datetime.now(UTC),
        last_computed_at=_last_computed(graph, iri),
    )


# ---------------------------------------------------------------------------
# Field groups
# ---------------------------------------------------------------------------


def _distributions(graph: Graph, iri: URIRef) -> list[DistributionSummary]:
    out: list[DistributionSummary] = []
    for node in sorted(graph.objects(iri, DCAT.distribution), key=str):
        health = graph.value(node, OG.linkHealth)
        out.append(
            DistributionSummary(
                id=str(node),
                media_type=_str(graph.value(node, DCAT.mediaType)),
                format_label=_str(graph.value(node, OG.formatLabel)),
                byte_size=_int(graph.value(node, DCAT.byteSize)),
                # A distribution's own restriction overrides the dataset's
                # (PRD §4.2). Falling back to the dataset's is what lets a
                # filter for anonymous access find the S3 copy of a dataset
                # whose API is key-gated.
                access_restriction=_local(graph.value(node, OG.accessRestriction))
                or _local(graph.value(iri, OG.accessRestriction)),
                anonymous_access=_bool(graph.value(node, OG.anonymousAccess)),
                bulk_download=_bool(graph.value(node, OG.bulkDownload)),
                supports_range_requests=bool(_bool(graph.value(node, OG.supportsRangeRequests))),
                subsetting_protocol=_str(graph.value(node, OG.subsettingProtocol)),
                link_health=_str(graph.value(health, OG.linkHealthStatus)) if health else None,
            )
        )
    return out


def _worst_health(distributions: list[DistributionSummary]) -> str | None:
    """The worst status across a record's access paths.

    Aggregated here so the list view can show one badge and the broker can skip
    a record with nothing live, neither of which should have to walk the
    distributions itself.
    """
    seen = [d.link_health for d in distributions if d.link_health]
    if not seen:
        return None
    return next((status for status in _HEALTH_ORDER if status in seen), None)


def _quality(graph: Graph, iri: URIRef, level: int) -> tuple[QualityBadges, bool]:
    """The three facets, and whether Provenance and Documentation were assessed.

    PRD §F5: a record below completeness level 2 shows those two as "not yet
    assessed", never as grade D. Currency is different — it is fully automatic
    and needs only the stated cadence and the vintage, both of which a level 1
    record carries — so it is reported at any level.
    """
    grades: dict[str, str] = {}
    for node in graph.objects(iri, OG.qualityGrade):
        facet = _str(graph.value(node, OG.facet))
        grade = _str(graph.value(node, OG.grade))
        if facet and grade:
            grades[facet] = grade

    assessed = level >= 2 and {"provenance", "documentation"} <= grades.keys()
    badges = QualityBadges(
        provenance=grades.get("provenance") if assessed else None,  # type: ignore[arg-type]
        documentation=grades.get("documentation") if assessed else None,  # type: ignore[arg-type]
        currency=grades.get("currency"),  # type: ignore[arg-type]
        provenance_label=GRADE_LABELS["provenance"].get(grades.get("provenance", ""))
        if assessed
        else "Not yet assessed",
        documentation_label=GRADE_LABELS["documentation"].get(grades.get("documentation", ""))
        if assessed
        else "Not yet assessed",
        currency_label=GRADE_LABELS["currency"].get(grades.get("currency", "")),
    )
    return badges, assessed


def _concepts(graph: Graph, iri: URIRef) -> list[ConceptRef]:
    """The concepts this record's fields resolve to, with their labels.

    Labels come from the vocabulary graph, which the projector's query joins in.
    An index carrying only IRIs would make a concept filter unreadable in the
    UI and unsearchable by text.
    """
    refs: dict[str, ConceptRef] = {}
    for field in graph.objects(iri, OG.hasField):
        for concept in graph.objects(field, OG.concept):
            label = _str(graph.value(concept, SKOS.prefLabel)) or _local(concept)
            refs[str(concept)] = ConceptRef(iri=str(concept), label=label or str(concept))
    return sorted(refs.values(), key=lambda c: c.label)


def _expanded_concepts(graph: Graph, concepts: list[ConceptRef]) -> list[str]:
    """Each concept plus every ancestor, so a filter on a parent matches.

    This is PRD §4.6 Q3 pushed into the index. The alternative is a property
    path on every search, which is exactly the SPARQL-on-the-read-path that
    §3.1 exists to avoid.
    """
    expanded: set[str] = set()
    for concept in concepts:
        node = URIRef(concept.iri)
        expanded.add(concept.iri)
        expanded.update(str(a) for a in graph.objects(node, OG.broaderTransitive))
    return sorted(expanded)


def _concept_refs(graph: Graph, iri: URIRef, predicate: URIRef) -> list[ConceptRef]:
    refs: list[ConceptRef] = []
    for node in sorted(graph.objects(iri, predicate), key=str):
        refs.append(
            ConceptRef(
                iri=str(node),
                label=_str(graph.value(node, SKOS.prefLabel)) or _local(node) or str(node),
                notation=_str(graph.value(node, SKOS.notation)),
            )
        )
    return refs


def _spatial(graph: Graph, iri: URIRef) -> SpatialCoverage:
    return SpatialCoverage(
        bbox=read_bbox(graph, iri),
        place_labels=sorted(_strs(graph, iri, OG.spatialLabel)),
        place_iris=sorted(str(p) for p in graph.objects(iri, DCTERMS.spatial)),
        native_crs=_str(graph.value(iri, OG.nativeCRS)),
        geometry_types=sorted(_strs(graph, iri, OG.geometryTypes)),
        granularity=_str(graph.value(iri, OG.spatialGranularity)),
        feature_count=_int(graph.value(iri, OG.featureCount)),
    )


def _temporal(graph: Graph, iri: URIRef) -> TemporalCoverage:
    period = graph.value(iri, DCTERMS.temporal)
    return TemporalCoverage(
        start=_dt(graph.value(period, DCAT.startDate)) if period else None,
        end=_dt(graph.value(period, DCAT.endDate)) if period else None,
        update_cadence=_str(graph.value(iri, OG.updateCadence)),
        time_resolution=_str(graph.value(iri, OG.timeResolution)),
    )


def _last_computed(graph: Graph, iri: URIRef) -> dict[str, datetime]:
    """When each semantic-layer signal was last computed.

    PRD §F4 asks for this on the record so the freshness lag is visible rather
    than hidden. Evaluators need it; modelers can ignore it.
    """
    out: dict[str, datetime] = {}
    for node in graph.objects(iri, OG.lastComputedAt):
        name = _str(graph.value(node, OG.signalName))
        when = _dt(graph.value(node, OG.computedAt))
        if name and when:
            out[name] = when
    return out


# ---------------------------------------------------------------------------
# Term coercion
# ---------------------------------------------------------------------------


def _str(term: Any) -> str | None:
    return str(term) if term is not None else None


def _strs(graph: Graph, subject: URIRef, predicate: URIRef) -> list[str]:
    return [str(o) for o in graph.objects(subject, predicate)]


def _int(term: Any, *, default: int | None = None) -> int | None:
    if term is None:
        return default
    try:
        return int(term)
    except (TypeError, ValueError):
        return default


def _bool(term: Any) -> bool | None:
    if term is None:
        return None
    text = str(term).strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


def _dt(term: Any) -> datetime | None:
    if term is None:
        return None
    try:
        value = term.toPython()
    except AttributeError:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


def _local(term: Any) -> str | None:
    if term is None:
        return None
    text = str(term)
    for separator in ("#", "/"):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    return text or None


def _label_of(graph: Graph, node: Any) -> str | None:
    """A human name for an agent node, falling back to its IRI's last segment."""
    if node is None:
        return None
    from datahub.namespaces import FOAF

    for predicate in (FOAF.name, DCTERMS.title, SKOS.prefLabel):
        value = graph.value(node, predicate)
        if value is not None:
            return str(value)
    return _local(node)


def _visibility(term: Any) -> str:
    text = _str(term) or "public"
    return text if text in ("public", "restricted-metadata", "allowlisted-existence") else "public"


__all__ = ["GRADE_LABELS", "build_document"]
