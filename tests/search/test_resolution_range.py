"""Spatial resolution as a number, not just a class (#53).

`og:spatialGranularity` says *gridded*, which is the right answer to "can this
serve power flow at all" and no answer at all to the question the domain
assessment actually records:

    ERA5 … is insufficient since it is only at a 30km resolution — IRP CoLab

ERA5 at 30 km and NSRDB at 4 km are both `gridded`, and that difference is the
whole of the finding. `dcat:spatialResolutionInMeters` carries it, and
`?resolution_max_m=` turns the sentence into a query.

The two coexist deliberately. The enum is a class a harvester can often infer;
the number is a fact a publisher states, and most records will never have one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.search import Entitlement, SearchRequest
from datahub.api.search.backend import InMemorySearchBackend, RangeFilter
from datahub.api.search.document import RANGE_FIELDS, range_path
from datahub.api.search.query import BadSearchRequest, SearchParams, build
from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore
from datahub.graph.store import RdflibStore
from datahub.harvest.seed import SeedLoader
from datahub.projector.build import build_document


@pytest.fixture(scope="module")
def catalog() -> InMemorySearchBackend:
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    backend = InMemorySearchBackend()
    backend.index(
        [
            build_document(records.get_graph(iri, graph=NamedGraph.CATALOG), iri)
            for iri in records.list_ids(graph=NamedGraph.CATALOG)
        ]
    )
    backend.refresh()
    return backend


def ids(backend: InMemorySearchBackend, metres: float) -> list[str]:
    response = backend.search(
        SearchRequest(
            q="",
            limit=50,
            entitlement=Entitlement.anonymous(),
            ranges={"spatial_resolution_m": RangeFilter(lte=metres)},
        )
    )
    return sorted(hit.document.id for hit in response.hits)


def test_the_irp_colab_finding_is_now_a_query(catalog) -> None:
    """A siting analyst asking for 5 km or finer does not get ERA5."""
    fine = ids(catalog, 5000)
    assert "ecmwf-era5" not in fine
    assert "nrel-nsrdb" in fine and "nrel-wind-toolkit" in fine


def test_the_bound_actually_bounds(catalog) -> None:
    assert ids(catalog, 250) == ["global-wind-atlas"]
    assert ids(catalog, 1000) == ["global-solar-atlas", "global-wind-atlas"]


def test_a_record_with_no_stated_resolution_is_excluded_not_assumed(catalog) -> None:
    """PRD §14.2, and the reason the parameter's description says so.

    Silence is not "fine enough". A generous filter that let unstated records
    through would hand a siting analyst the entire catalog and call it filtered
    — which is worse than the sentence in the assessment, because it looks like
    an answer.
    """
    everything = ids(catalog, 10_000_000)
    assert set(everything) == {
        "ecmwf-era5",
        "global-solar-atlas",
        "global-wind-atlas",
        "nrel-nsrdb",
        "nrel-wind-toolkit",
    }, "only the five rows that state a resolution should ever match a resolution bound"


def test_the_class_and_the_number_disagree_on_purpose(catalog) -> None:
    """All five are `gridded`; the enum cannot separate 250 m from 30 km."""
    response = catalog.search(
        SearchRequest(
            q="",
            limit=50,
            entitlement=Entitlement.anonymous(),
            filters={"spatial_granularity": ["gridded"]},
        )
    )
    gridded = {hit.document.id for hit in response.hits}
    assert {"ecmwf-era5", "global-wind-atlas"} <= gridded
    assert len(gridded) > len(ids(catalog, 5000)), (
        "if the enum selected the same set as the bound, the number would be redundant"
    )


def test_a_negative_bound_is_refused_rather_than_matching_nothing() -> None:
    with pytest.raises(BadSearchRequest):
        build(SearchParams(resolution_max_m=-1), Entitlement.anonymous())


def test_an_unknown_range_field_is_refused_by_both_backends() -> None:
    """It used to be refused by neither, in two different ways.

    Both backends resolved the path with `FACET_FIELDS.get(name) or
    SORT_FIELDS[name]`: in memory that returned `None` and the record silently
    failed the filter, so an unknown name matched *nothing*; against OpenSearch
    the same name raised `KeyError` and the request 500'd. One request, two
    answers, and neither of them said the field was unknown.
    """
    with pytest.raises(ValueError, match="unknown range field"):
        SearchRequest(
            q="",
            entitlement=Entitlement.anonymous(),
            ranges={"no_such_field": RangeFilter(lte=1)},
        )
    with pytest.raises(KeyError):
        range_path("no_such_field")


def test_every_range_field_resolves_to_a_real_document_path() -> None:
    from datahub.api.search.document import SearchDocument

    for name, path in RANGE_FIELDS.items():
        root, _, sub = path.partition(".")
        assert root in SearchDocument.model_fields, (name, path)
        if sub:
            nested = SearchDocument.model_fields[root].annotation
            assert sub in nested.model_fields, (name, path)  # type: ignore[union-attr]
