"""`/v1/gaps` — requirements nothing open supplies (#56).

Deliberately its own endpoint rather than a document type inside
`/v1/datasets`. A gap is not a dataset: it has no licence, no access path and
no quality grade, and letting one into a dataset count, a search result or the
snapshot's dataset list would be the catalog asserting a record for something
that does not exist. Separate route, separate response model, separate count.

The caller this exists for is an empty search result. A reader who types "nodal
demand", gets nothing, and is told "no datasets match this search" has learned
that the catalog is small. Told instead *nothing open supplies this, here is
why, here is who found that and when*, they have learned something true about
the field — and that is the answer this catalog can give that a search engine
cannot.
"""

from __future__ import annotations

from typing import Annotated

from datahub.api.schemas import DataGapModel, GapSearchResponse
from datahub.gaps import search
from fastapi import APIRouter, Query

router = APIRouter(tags=["gaps"])


@router.get(
    "/gaps",
    response_model=GapSearchResponse,
    summary="Data requirements with no known open supplier",
)
def search_gaps(
    q: Annotated[
        str | None,
        Query(description="Free text, matched against the title, category and reason."),
    ] = None,
    data_domain: Annotated[str | None, Query(description="DD1-DD10.")] = None,
    needed_by: Annotated[
        str | None,
        Query(
            description=(
                "An analysis class that needs this, from the analysis-type scheme — "
                "capacityExpansion, productionCost, reliabilityAssessment, acPowerFlow."
            )
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> GapSearchResponse:
    """Search the gap register.

    Everything here is a curated finding with a named observer and a date, not
    a computation. The register is small and hand-maintained on purpose: a gap
    claims something about the whole open landscape, which is not a claim to
    generate.
    """
    found = search(q, domain=data_domain, needed_by=needed_by, limit=limit)
    return GapSearchResponse(
        total=len(found),
        gaps=[DataGapModel.of(gap) for gap in found],
    )
