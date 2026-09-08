"""Assumption depth over the recorded lineage (#52).

`og:provenanceClass` is one hop. It calls NREL ATB *modeled*, a capacity
expansion portfolio built on ATB *modeled*, and a resource adequacy study built
on that *modeled* — three layers, one word, read as peers. The framework this
comes from states the consequence: "each layer looks like data to the layer
above it."

The rule every test here exists to hold: **unknown is not zero.**
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from datahub.semantic.lineage import OBSERVATIONAL, depths


def test_an_observational_record_with_no_upstream_is_a_root() -> None:
    assert depths({}, {"era5": "reanalysis"}) == {"era5": 0}
    assert depths({}, {"filing": "primary"}) == {"filing": 0}


def test_a_modelled_record_with_no_recorded_upstream_is_unknown_not_zero() -> None:
    """The whole point. NREL ATB is modelled from something; nothing says what.

    Returning 0 would assert it sits one hop from measurement, which is the
    exact misreading the feature exists to prevent, dressed up as a number.
    """
    assert depths({}, {"atb": "modeled"}) == {"atb": None}


def test_curated_is_not_treated_as_observational() -> None:
    """A curated compilation is assembled *from* other sources.

    It is also `_provenance`'s fallback in the seed loader, so treating it as a
    root would hand depth 0 to every row that simply never stated a class.
    """
    assert "curated" not in OBSERVATIONAL
    assert depths({}, {"compilation": "curated"}) == {"compilation": None}


def test_depth_counts_the_longest_chain() -> None:
    upstream = {"cutouts": ["era5"], "profiles": ["cutouts"], "study": ["profiles", "era5"]}
    provenance = {
        "era5": "reanalysis",
        "cutouts": "derived",
        "profiles": "derived",
        "study": "modeled",
    }
    assert depths(upstream, provenance) == {
        "era5": 0,
        "cutouts": 1,
        "profiles": 2,
        "study": 3,
    }, "a record's depth is its deepest parent plus one, not its shallowest"


def test_an_uncatalogued_upstream_leaves_the_chain_unresolved() -> None:
    """A real edge to an unknown depth. Truncating it would understate."""
    assert depths({"x": ["https://example.org/upstream"]}, {"x": "modeled"}) == {"x": None}


def test_unknown_propagates_downstream() -> None:
    """The framework's IRP example: ATB -> portfolio -> dispatch -> metrics.

    ATB's own lineage is unrecorded, so nothing built on it can claim a depth
    either. Four `None`s is the honest answer, and it is more useful than four
    confident numbers computed from a guess at the bottom.
    """
    chain = {"portfolio": ["atb"], "dispatch": ["portfolio"], "metrics": ["dispatch"]}
    classes = dict.fromkeys(["atb", "portfolio", "dispatch", "metrics"], "modeled")
    assert set(depths(chain, classes).values()) == {None}


def test_a_cycle_yields_none_rather_than_recursing_forever() -> None:
    """Two records each citing the other is a data error and reachable."""
    assert depths({"a": ["b"], "b": ["a"]}, {"a": "modeled", "b": "modeled"}) == {
        "a": None,
        "b": None,
    }


def test_a_record_absent_from_provenance_is_not_invented() -> None:
    """The result is keyed by what the catalog holds, not by what is cited."""
    assert depths({"a": ["ghost"]}, {"a": "modeled"}) == {"a": None}


# ---- against the real catalog ---------------------------------------------


@pytest.fixture(scope="module")
def documents() -> dict:
    from datahub.api.search.backend import InMemorySearchBackend
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore
    from datahub.harvest.seed import SeedLoader
    from datahub.projector.index import Projector

    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    projector = Projector(records, InMemorySearchBackend())
    return {
        dataset_id.rsplit("/", 1)[-1]: projector.document_for(dataset_id.rsplit("/", 1)[-1])
        for dataset_id in records.list_ids(graph=NamedGraph.CATALOG)
    }


def test_the_reanalysis_roots_and_their_derivatives(documents) -> None:
    assert documents["ecmwf-era5"].assumption_depth == 0
    for derived in ("pypsa-eur-weather-cutouts", "nrel-nsrdb", "renewables-ninja"):
        assert documents[derived].assumption_depth == 1, derived
        assert any(u.endswith("/ecmwf-era5") for u in documents[derived].derived_from), derived


def test_the_osm_derived_networks_point_at_the_osm_record(documents) -> None:
    for derived in ("pypsa-eur-grid", "gridkit"):
        assert documents[derived].assumption_depth == 1, derived


def test_atb_reads_as_unknown_depth_not_shallow(documents) -> None:
    """The catalog's own instance of the framework's warning.

    ATB is the de facto standard US cost input and it is modelled from
    something nothing here records. It must not read as one hop from a
    measurement just because no edge was written.
    """
    assert documents["nrel-atb"].assumption_depth is None
    assert documents["nrel-atb"].derived_from == []


def test_cambium_says_which_analysis_produced_it(documents) -> None:
    """Direction, which `og:supportedAnalysis` alone cannot express (#51)."""
    doc = documents["nrel-cambium"]
    assert [c.iri.rsplit("/", 1)[-1] for c in doc.output_of_analysis] == ["capacityExpansion"]
    assert doc.assumption_depth is None, (
        "Cambium derives from ReEDS, whose own inputs are unrecorded, so its depth "
        "is a lower bound nobody has established"
    )
