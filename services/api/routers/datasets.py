"""``/v1/datasets`` — search and read (WP-4.3).

PRD §F8's dataset endpoints. Two rules run through all of them.

**Entitlement is compiled in, never applied afterwards** (ADR-0006). Every read
goes through the search index with the caller's entitlement in the query, so a
record the caller may not see contributes to no count and appears in no page.
Filtering results after the fact leaks existence through the total, and a total
is enough to confirm a dataset exists.

**A refusal on an allow-listed-existence record is a 404, not a 403.** A 403
says "this exists and you cannot have it", which for a record whose *existence*
is restricted is the disclosure itself. The audit log records the real outcome;
the caller cannot tell the two apart, which is the point.

The detail endpoints read the *record* rather than the index once entitlement
has been established, because the index is a flattened projection and a caller
asking for a dataset's schema wants the schema, not the summary of it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from datahub.api.deps import CallerDep, RecordsDep, SearchDep, SessionDep
from datahub.api.entitlement import Caller, tokens
from datahub.api.entitlement.visibility import absent, entitled_document
from datahub.api.schemas import (
    AccessPlanRequest,
    AccessPlanResponse,
    DatasetDetail,
    DatasetSummary,
    DistributionDetail,
    FacetBucket,
    LinkedDataset,
    LinksResponse,
    QualityResponse,
    SchemaResponse,
    SearchResponseModel,
)
from datahub.api.search.document import SearchDocument
from datahub.api.search.query import SearchParams, build
from datahub.errors import NoUsableDistribution
from datahub.logging import get_logger
from fastapi import APIRouter, Path, Query, Request, Response, status
from fastapi.responses import RedirectResponse

log = get_logger(__name__)

router = APIRouter(tags=["datasets"])

DatasetId = Annotated[
    str,
    Path(
        description=(
            "The dataset's slug, which is the last segment of its IRI — "
            "`ecmwf-era5` for `https://catalog.opengrid.org/ds/ecmwf-era5`. A caller "
            "holding the IRI takes its last segment; a full IRI is not accepted in the "
            "path, because its slashes are indistinguishable from the sub-resource "
            "paths (`/schema`, `/quality`) that follow it."
        ),
        examples=["ecmwf-era5"],
    ),
]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@router.get(
    "/datasets",
    response_model=SearchResponseModel,
    summary="Search, filter, facet and paginate the catalog",
)
def search_datasets(
    caller: CallerDep,
    backend: SearchDep,
    q: Annotated[
        str | None, Query(description="Free text. Prefix-matched on the last token.")
    ] = None,
    data_domain: Annotated[
        list[str] | None, Query(description="DD1-DD10, or the concept IRI.")
    ] = None,
    provenance_class: Annotated[list[str] | None, Query()] = None,
    # Named for the facet it filters, not for the document field it reads.
    # They disagreed: the facet came back as "license" and the parameter was
    # "license_id", so a UI built from the facet response sent a name the
    # route ignored, and anything sending the documented name hit an unknown
    # filter field and a 500. Both are the same bug from opposite ends.
    license: Annotated[list[str] | None, Query(description="SPDX id or LicenseRef.")] = None,
    spatial_granularity: Annotated[list[str] | None, Query()] = None,
    format: Annotated[list[str] | None, Query(description="Distribution format label.")] = None,
    completeness_level: Annotated[list[int] | None, Query()] = None,
    anonymous_access: Annotated[bool | None, Query()] = None,
    bbox: Annotated[str | None, Query(description="west,south,east,north in WGS 84.")] = None,
    temporal_start: Annotated[str | None, Query(description="ISO 8601.")] = None,
    temporal_end: Annotated[str | None, Query(description="ISO 8601.")] = None,
    sort: Annotated[
        str | None,
        Query(description="Comma-separated fields; a leading `-` is descending, e.g. `-modified`."),
    ] = None,
    facets: Annotated[str | None, Query(description="Comma-separated facet fields.")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    # ge=0, not ge=1: "give me the facet counts and no results" is how a
    # filter panel is populated, and making the caller ask for a row they
    # discard costs a projection per query for nothing.
    limit: Annotated[int, Query(ge=0, le=200)] = 20,
    include_unconfirmed: Annotated[bool, Query(description="Stewards only.")] = False,
) -> SearchResponseModel:
    """Search the catalog.

    Search-while-typing is the intended interaction (PRD §F3), so the last token
    of ``q`` is prefix-expanded and there is no submit step. Facets come back
    with the results rather than from a second call: a UI that had to ask twice
    would show counts that disagree with the list for as long as the second
    call is in flight.
    """
    params = SearchParams(
        q=q,
        filters=_filters(
            data_domain=data_domain,
            provenance_class=provenance_class,
            license=license,
            spatial_granularity=spatial_granularity,
            format=format,
            completeness_level=completeness_level,
            anonymous_access=anonymous_access,
        ),
        bbox=_bbox(bbox),
        temporal_start=_when(temporal_start, "temporal_start"),
        temporal_end=_when(temporal_end, "temporal_end"),
        sort=sort,
        # Through `parse_facets`, not split inline: the helper checks the names
        # against FACET_FIELDS and raises a 400 naming the valid ones. Splitting
        # here skipped that, so an unknown facet reached SearchRequest and came
        # back as a ValueError — a 500 for what is plainly a client error.
        facets=_facets(facets),
        offset=offset,
        limit=limit,
        include_unconfirmed=include_unconfirmed,
    )
    response = backend.search(build(params, caller.entitlement))

    return SearchResponseModel(
        total=response.total,
        offset=offset,
        limit=limit,
        results=[
            DatasetSummary.from_document(hit.document, full=hit.full_metadata)
            for hit in response.hits
        ],
        facets={
            name: [FacetBucket(value=v.value, count=v.count, label=v.label) for v in values]
            for name, values in response.facets.items()
        },
        took_ms=response.took_ms,
    )


# ---------------------------------------------------------------------------
# One dataset
# ---------------------------------------------------------------------------


@router.get("/datasets/{dataset_id}", response_model=DatasetDetail, summary="One record")
def get_dataset(
    dataset_id: DatasetId,
    caller: CallerDep,
    backend: SearchDep,
    response: Response,
) -> DatasetDetail:
    document, full = entitled_document(dataset_id, caller, backend)
    if not full:
        # A stub: the caller may know it exists and no more. Not cached
        # publicly, because the response depends on who asked.
        response.headers["Cache-Control"] = "private, max-age=0"
    return DatasetDetail.from_document(document, full=full)


@router.get(
    "/datasets/{dataset_id}/schema",
    response_model=SchemaResponse,
    summary="Field-level metadata",
)
def get_schema(
    dataset_id: DatasetId,
    caller: CallerDep,
    backend: SearchDep,
    records: RecordsDep,
) -> SchemaResponse:
    """The record's fields, with units and concepts where they resolve.

    Read from the graph rather than the index: the index carries a field
    *count* because that is what a list view needs, and this endpoint exists
    for the caller who wants the fields themselves.
    """
    document, full = entitled_document(dataset_id, caller, backend)
    if not full:
        raise absent(dataset_id)
    record = records.get(document.iri)
    return SchemaResponse.from_record(record, document, labels=_labels(record, records))


@router.get(
    "/datasets/{dataset_id}/quality",
    response_model=QualityResponse,
    summary="The three quality facets",
)
def get_quality(
    dataset_id: DatasetId,
    caller: CallerDep,
    backend: SearchDep,
    records: RecordsDep,
) -> QualityResponse:
    """Currency, provenance and documentation, graded independently.

    There is deliberately no composite score (ADR-0007). A dataset can be
    perfectly current and completely unprovenanced, and averaging those into
    one number destroys the only information a user could act on.
    """
    document, full = entitled_document(dataset_id, caller, backend)
    if not full:
        raise absent(dataset_id)
    return QualityResponse.from_document(document, _rationales(document.iri, records))


@router.get(
    "/datasets/{dataset_id}/distributions",
    response_model=list[DistributionDetail],
    summary="Access paths, with capabilities and link health",
)
def get_distributions(
    dataset_id: DatasetId,
    caller: CallerDep,
    backend: SearchDep,
    records: RecordsDep,
) -> list[DistributionDetail]:
    """Every way to get the data, and what is known about each.

    Read from the record, not the index: the index carries only enough of a
    distribution to filter and render a list row, and deliberately no access
    URLs — a search response should not haul every URL in the catalog to a
    client that wanted ten titles.

    Unhealthy paths are included and marked. A dead link a user can see is a
    reportable fact; a dead link silently removed is a dataset that appears to
    have no access path at all.
    """
    document, full = entitled_document(dataset_id, caller, backend)
    if not full:
        raise absent(dataset_id)
    return DistributionDetail.from_record(records.get(document.iri))


@router.get(
    "/datasets/{dataset_id}/links",
    response_model=LinksResponse,
    summary="Datasets that go with this one, and why",
)
def get_links(
    dataset_id: DatasetId,
    caller: CallerDep,
    backend: SearchDep,
    records: RecordsDep,
) -> LinksResponse:
    """Ranked, explained connections to other catalog records (PRD §F6).

    Computed at request time rather than read from the stored graph, because
    the candidate set depends on who is asking. Generating candidates through
    the index with the caller's entitlement compiled in is ADR-0006's rule;
    reading a stored list and filtering it afterwards would be the post-filter
    the ADR forbids, and would leak the existence of an allow-listed record
    through a suggestion that then disappeared.

    A correlated pairing is **reduced, never hidden** (PRD §F6.9). A user told
    that two datasets are related-but-not-independent can act on it; a user
    shown nothing concludes they are unrelated, which is a stronger and more
    wrong claim.
    """
    from datahub.linksvc import LinkService

    document, full = entitled_document(dataset_id, caller, backend)
    if not full:
        raise absent(dataset_id)

    service = LinkService(backend=backend, store=records.store)
    links = service.links_for(document.id, entitlement=caller.entitlement)
    return LinksResponse(
        dataset_id=document.id,
        links=[
            LinkedDataset(
                dataset_id=link.target,
                title=_title_of(link.target, backend, caller),
                relation=link.relation,
                strength=link.tier,
                descriptor=link.descriptor,
                reasons=list(link.reasons),
                joinable_keys=list(link.joinable_keys),
                shared_workflow_tags=list(link.shared_workflow_tags),
                correlation_warning=link.warning,
                shared_origin=link.shared_origin,
                strength_reduced_by_correlation=link.penalised,
            )
            for link in links
        ],
        unavailable_reason=None if links else _no_links_reason(document),
    )


def _title_of(dataset_id: str, backend: SearchDep, caller: CallerDep) -> str | None:
    document = backend.get(dataset_id)
    if document is None or not caller.entitlement.can_see_full_metadata(document):
        return None
    return document.title


def _no_links_reason(document: SearchDocument) -> str:
    """Why the list is empty, in words a user can act on.

    "No links" reads as "nothing in this catalog relates to this dataset",
    which is almost never what happened. What happened is that nothing shares
    enough recorded metadata to say so, and the level is usually why.
    """
    if document.completeness_level < 2:
        return (
            "Nothing links to this record yet. Links are computed from concepts, coverage and "
            f"supported analyses; this record is at completeness level "
            f"{document.completeness_level} and carries too few of them to pair confidently."
        )
    return (
        "No other catalog record shares enough recorded metadata with this one to pair it "
        "confidently. That is a statement about what has been catalogued, not about the data."
    )


@router.post(
    "/datasets/{dataset_id}/access-plan",
    response_model=AccessPlanResponse,
    summary="How to read this dataset. Never the bytes themselves.",
)
def access_plan(
    dataset_id: DatasetId,
    caller: CallerDep,
    backend: SearchDep,
    records: RecordsDep,
    session: SessionDep,
    request: Request,
    body: AccessPlanRequest | None = None,
) -> AccessPlanResponse:
    """Issue an access plan.

    One uniform shape whether the dataset is 800 KB or 4 TB; only the path
    differs. Licence, attribution and quality grades travel *in the plan*,
    which is what makes agentic access defensible — the guardrail metadata is
    in the payload rather than in a page the agent never read (PRD §F7).

    POST rather than GET because the request carries a slice specification, and
    because issuing a plan is an auditable event: PRD §F10 requires grants and
    refusals to be logged, and §12.9 leaves open whether a plan is revoked when
    an allow-list changes. Both need a row per issue.
    """
    # A public plan is available anonymously; a token that asked only to read
    # the catalog is still only reading the catalog.
    tokens.require_scope(caller, "catalog:read", allow_anonymous=True)
    from datahub.api.broker import Broker, SliceSpec

    document, full = entitled_document(dataset_id, caller, backend)
    if not full:
        # An access plan for a record the caller may see but not read would be
        # the disclosure the stub exists to prevent: the plan carries the URL.
        raise absent(dataset_id)

    body = body or AccessPlanRequest()
    plan = Broker().plan(
        document,
        records.get(document.iri),
        slice_spec=SliceSpec(
            time=(body.time_start, body.time_end) if body.time_start and body.time_end else None,
            bbox=tuple(body.bbox) if body.bbox else None,
            variables=tuple(body.variables),
        ),
        distribution_id=body.distribution_id,
    )
    _record_plan(session, caller, request, plan)
    return AccessPlanResponse.from_plan(plan)


@router.get(
    "/datasets/{dataset_id}/download",
    status_code=status.HTTP_302_FOUND,
    summary="Redirect to the source. The API never serves bytes.",
    responses={302: {"description": "Redirect to the best available access URL."}},
)
def download(
    dataset_id: DatasetId,
    caller: CallerDep,
    backend: SearchDep,
    records: RecordsDep,
    session: SessionDep,
    request: Request,
) -> RedirectResponse:
    """The human-facing path: click, and end up at the source.

    A redirect rather than a proxy, and that is a design decision rather than
    an optimisation. Proxying would make OpenGrid an egress bill, a bandwidth
    bottleneck and a party to every licence it does not hold. The redirect also
    means the source sees its own traffic, which is what keeps a data publisher
    willing to be catalogued.
    """
    document, full = entitled_document(dataset_id, caller, backend)
    if not full:
        raise absent(dataset_id)

    target = _best_distribution(DistributionDetail.from_record(records.get(document.iri)))
    if target is None:
        # 409 rather than 404: the record exists and this endpoint cannot
        # serve it, which is a different thing from the record being absent
        # and needs a different response from the client.
        raise NoUsableDistribution(
            f"{document.id} has no distribution a browser can follow",
            hint=(
                "Its access paths are object-store URIs or protocol endpoints. "
                f"Use POST /v1/datasets/{document.id}/access-plan, which returns "
                "instructions for reading them."
            ),
            dataset_id=document.id,
        )

    _audit(session, caller, request, document, target)
    return RedirectResponse(url=target.access_url, status_code=status.HTTP_302_FOUND)


def _rationales(iri: str, records: RecordsDep) -> dict[str, str]:
    """Why each facet got the grade it did, read from the computed graph.

    PRD §F5: *every grade derives from recorded facts.* That is only checkable
    if the facts travel with the grade, and this is the endpoint where somebody
    is asking. Never fatal: a grade whose rationale cannot be read is still a
    grade, and refusing the whole response for a missing sentence would be a
    worse answer than the sentence is worth.
    """
    from datahub.graph.graphs import NamedGraph
    from datahub.namespaces import OG
    from rdflib import URIRef

    try:
        graph = records.store.get_graph(NamedGraph.COMPUTED)
    except Exception as exc:  # pragma: no cover - a store that just answered
        log.warning("could not read grade rationales", dataset=iri, error=str(exc))
        return {}

    out: dict[str, str] = {}
    for node in graph.objects(URIRef(iri), OG.qualityGrade):
        facet = graph.value(node, OG.facet)
        rationale = graph.value(node, OG.gradeRationale)
        if facet is not None and rationale is not None:
            out[str(facet)] = str(rationale)
    return out


def _labels(record: dict[str, Any], records: RecordsDep) -> dict[str, dict[str, str]]:
    """Display label and definition for every concept and unit the record names.

    One query for all of them rather than one per field: a record with ninety
    fields would otherwise be ninety round trips to render one page, and the
    labels all live in the same graph.

    The definition is fetched here and nowhere else. PRD §F4.2 asks that a
    plain-language definition sit beside every resolved concept, so that a
    field documented only through CIM or CGMES is intelligible to somebody who
    does not own the standard — and this is the endpoint where a user is asking
    what a field means.
    """
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.records import dataset_node

    try:
        node = dataset_node(record)
    except Exception:
        return {}

    fields = node.get("hasField") or []
    if isinstance(fields, dict):
        fields = [fields]
    iris = {
        str(value)
        for field in fields
        if isinstance(field, dict)
        for key in ("concept", "unit")
        if isinstance(value := field.get(key), str)
    }
    if not iris:
        return {}

    from datahub.graph.sparql import values_clause
    from rdflib import URIRef

    rows = records.store.select(
        f"""
        SELECT ?iri ?label ?definition WHERE {{
          GRAPH ??vocab {{
            ?iri ?p ?label .
            OPTIONAL {{ ?iri skos:definition ?definition }}
          }}
          {values_clause("iri", [URIRef(i) for i in sorted(iris)])}
          VALUES ?p {{ skos:prefLabel rdfs:label qudt:symbol }}
        }}
        """,
        {"vocab": NamedGraph.VOCAB.uri()},
    )
    terms: dict[str, dict[str, str]] = {}
    for row in rows:
        entry = terms.setdefault(str(row["iri"]), {})
        entry["label"] = str(row["label"])
        if row.get("definition") is not None:
            entry["definition"] = str(row["definition"])
    return terms


# ---------------------------------------------------------------------------
# Entitlement
# ---------------------------------------------------------------------------


def _best_distribution(distributions: list[DistributionDetail]) -> DistributionDetail | None:
    """The distribution to send a browser to.

    Preferring, in order: a reachable link over a known-dead one, an
    anonymously readable path over a gated one, a bulk download over an API. A
    user who clicked "download" wants a file, and sending them to a login form
    when an open mirror exists is a worse answer than the mirror.

    ``min`` over a rank tuple rather than a chain of filters, so "no healthy
    anonymous path exists" still yields the best of a bad set rather than
    nothing at all.
    """

    def rank(dist: DistributionDetail) -> tuple[int, int, int]:
        return (
            0 if dist.reachable else 1,
            0 if dist.anonymous_access is not False else 1,
            0 if dist.bulk_download else 1,
        )

    # http(s) only. An `s3://` or `gs://` URI is a real access path and belongs
    # in an access plan, but a browser cannot follow it — redirecting to one
    # produces a dead tab, which is a worse answer than saying so.
    usable = [
        d
        for d in distributions
        if d.access_url and d.access_url.startswith(("http://", "https://"))
    ]
    return min(usable, key=rank) if usable else None


def _record_plan(session: Any, caller: Caller, request: Request, plan: Any) -> None:
    """Log the issue, and the grant.

    Two rows because they answer two questions: the plan row is "who was told
    where this data is, and until when" (PRD §12.9 needs it to make revocation
    implementable), and the audit row is the §F10 requirement that grants and
    refusals are logged. Neither is fatal — a plan that failed to log is still
    a plan, and refusing to issue it because the audit table is unreachable
    would take the catalog down with the database.
    """
    if session is None:
        return
    try:
        from datetime import UTC, datetime

        from datahub.api.models.repositories import Repositories

        repos = Repositories(session)
        ttl = (plan.expires_at - datetime.now(UTC)) if plan.expires_at else None
        repos.plans.issue(
            dataset_id=plan.dataset_id,
            distribution_id=plan.distribution_id,
            mode=plan.mode,
            ttl=ttl or timedelta(seconds=900),
            principal_id=caller.principal_id,
            client=request.headers.get("x-client", "api"),
            slice_spec=plan.requested_slice or None,
        )
        repos.audit.record(
            action="dataset.access_plan",
            outcome="granted",
            resource_kind="distribution",
            resource_id=plan.distribution_id,
            principal_id=caller.principal_id,
            principal_kind=caller.client_kind,
            client=request.headers.get("x-client", "api"),
        )
    except Exception as exc:
        log.warning("access plan not recorded", error=str(exc), dataset=plan.dataset_id)


def _audit(
    session: Any,
    caller: Caller,
    request: Request,
    document: SearchDocument,
    distribution: DistributionDetail,
) -> None:
    """Record the redirect. Never fatal.

    A download that failed to log is still a download; refusing to serve it
    because the audit table is unreachable would take the catalog down with the
    database, and the redirect target is public information anyway.
    """
    if session is None:
        return
    try:
        from datahub.api.models.repositories import Repositories

        Repositories(session).audit.record(
            action="dataset.download",
            outcome="granted",
            resource_kind="distribution",
            resource_id=distribution.id,
            principal_id=caller.principal_id,
            principal_kind=caller.client_kind,
            client=request.headers.get("x-client", "api"),
        )
    except Exception as exc:
        log.warning("audit write failed", error=str(exc), dataset=document.id)


# ---------------------------------------------------------------------------
# Query parsing
# ---------------------------------------------------------------------------


def _filters(**kwargs: Any) -> dict[str, list[str]]:
    """Repeated query parameters into the filter map.

    Named parameters rather than a free-form ``filter[]``: FastAPI then
    validates and documents each one, and a client discovers the filterable
    fields from the OpenAPI document rather than from prose.
    """
    out: dict[str, list[str]] = {}
    for name, value in kwargs.items():
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        out[name] = [str(v).lower() if isinstance(v, bool) else str(v) for v in values]
    return out


def _bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    from datahub.api.search.query import parse_bbox

    return parse_bbox(raw)


def _when(raw: str | None, field: str) -> Any:
    from datahub.api.search.query import parse_datetime

    return parse_datetime(raw, field=field)


def _facets(raw: str | None) -> Any:
    from datahub.api.search.query import parse_facets

    return parse_facets(raw)
