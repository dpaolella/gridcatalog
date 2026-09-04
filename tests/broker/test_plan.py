"""The access plan (WP-5.1, WP-5.2).

PRD §F7's central claim is that the plan is *one uniform shape regardless of
whether the dataset is 800 KB or 4 TB* and that *licence, attribution and
quality grades travel with the plan*. Both are tested here, and the second is
the one that matters:

> This is what makes agentic access defensible: the guardrail metadata is in
> the payload, not in a page the agent never read.

An agent handed a URL cannot know it may not redistribute what it downloads.
An agent handed a plan is told, in the same object, in a field it cannot miss.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.broker import READERS, Broker, SliceSpec
from datahub.api.schemas import DistributionDetail, LinkHealth
from datahub.api.search.document import QualityBadges, SearchDocument
from datahub.errors import NoUsableDistribution


def document(**kwargs) -> SearchDocument:
    base = {
        "id": "ecmwf-era5",
        "iri": "https://catalog.opengrid.org/ds/ecmwf-era5",
        "title": "ECMWF ERA5",
        "license_id": "https://spdx.org/licenses/CC-BY-4.0",
        "quality": QualityBadges(provenance="A", documentation="A", currency="B"),
    }
    return SearchDocument(**{**base, **kwargs})


def record(dataset: dict | None = None, distributions: list[dict] | None = None) -> dict:
    node = {
        "id": "https://catalog.opengrid.org/ds/ecmwf-era5",
        "type": "Dataset",
        "title": "ECMWF ERA5",
        "license": "https://spdx.org/licenses/CC-BY-4.0",
        "attribution": "Copernicus Climate Change Service (C3S)",
        "redistributionAllowed": True,
        "commercialUseAllowed": True,
        **(dataset or {}),
    }
    node["distribution"] = distributions if distributions is not None else [ZARR]
    return {"@graph": [node]}


ZARR = {
    "id": "https://catalog.opengrid.org/dist/ecmwf-era5--zarr-s3",
    "type": "Distribution",
    "accessURL": "s3://era5-pds/zarr/",
    "formatLabel": "Zarr v2",
    "mediaType": "application/vnd+zarr",
    "supportsRangeRequests": True,
    "chunkIndexMethod": "zarr-v2",
    "anonymousAccess": True,
    "bulkDownload": True,
}
CSV = {
    "id": "https://catalog.opengrid.org/dist/ecmwf-era5--csv",
    "type": "Distribution",
    "accessURL": "https://example.org/era5.csv",
    "formatLabel": "CSV",
    "anonymousAccess": True,
    "bulkDownload": True,
}
CDS_API = {
    "id": "https://catalog.opengrid.org/dist/ecmwf-era5--cds",
    "type": "Distribution",
    "accessURL": "https://cds.climate.copernicus.eu/api/retrieve/v1",
    "formatLabel": "GRIB or NetCDF",
    "subsettingProtocol": "cds-request-api",
    "anonymousAccess": False,
    "credentialRequirement": "A free CDS account and per-dataset licence acceptance.",
}


@pytest.fixture
def broker(settings) -> Broker:
    return Broker(settings)


# ---- the guardrail metadata ----------------------------------------------


def test_the_licence_travels_with_the_plan(broker) -> None:
    """The whole point of the object. An agent handed a URL cannot know it may
    not redistribute what it downloads."""
    plan = broker.plan(document(), record())

    assert plan.license == "https://spdx.org/licenses/CC-BY-4.0"
    assert plan.attribution == "Copernicus Climate Change Service (C3S)"
    assert plan.redistribution_allowed is True
    assert plan.commercial_use_allowed is True


def test_the_quality_grades_travel_with_the_plan(broker) -> None:
    """Three facets, never combined (ADR-0007). An agent choosing between two
    datasets needs to know one is grade D on provenance."""
    plan = broker.plan(document(), record())

    assert plan.quality_grades == {"provenance": "A", "documentation": "A", "currency": "B"}
    assert "composite" not in plan.quality_grades


def test_an_unrecorded_permission_is_not_a_permission(broker) -> None:
    """A client reading `redistribution_allowed` and finding null has to go and
    look. One finding `true` because we defaulted it has been told something
    false."""
    silent = record({"redistributionAllowed": None, "commercialUseAllowed": None})
    plan = broker.plan(document(), silent)

    assert plan.redistribution_allowed is None
    assert any("default copyright" in c for c in plan.caveats)


def test_a_prohibited_redistribution_is_stated_as_a_caveat(broker) -> None:
    plan = broker.plan(document(), record({"redistributionAllowed": False}))
    assert any("not permitted" in c for c in plan.caveats)


def test_a_stewards_caveat_reaches_the_plan(broker) -> None:
    """A caveat in a record the agent never fetched is a caveat nobody saw."""
    flagged = record(
        {
            "qualityFlags": {
                "type": "QualityFlags",
                "staleness": "current",
                "caveat": ["Wind speeds are biased low in complex terrain."],
            }
        }
    )
    plan = broker.plan(document(), flagged)
    assert any("biased low" in c for c in plan.caveats)


# ---- path selection ------------------------------------------------------


def test_range_support_plus_a_chunk_index_gives_partial_read(broker) -> None:
    """PRD §F7's first rule, verbatim."""
    plan = broker.plan(document(), record(distributions=[ZARR]))

    assert plan.mode == "partial-read"
    assert "byte-range" in plan.path_rationale
    assert plan.partial_read_unavailable_reason is None


def test_a_subsetting_protocol_wins_over_a_full_redirect(broker) -> None:
    plan = broker.plan(document(), record(distributions=[CDS_API]))

    assert plan.mode == "subsetting-protocol"
    assert "cds-request-api" in plan.path_rationale


def test_otherwise_redirect(broker) -> None:
    plan = broker.plan(document(), record(distributions=[CSV]))
    assert plan.mode == "redirect"


def test_a_redirect_plan_says_why_there_is_no_partial_read(broker) -> None:
    """The clause that matters as much as the other two. A plan that silently
    omits the partial-read section looks identical to one for a dataset that
    supports it but whose metadata is missing."""
    plan = broker.plan(document(), record(distributions=[CSV]))

    assert plan.partial_read_unavailable_reason
    assert "byte-range" in plan.partial_read_unavailable_reason
    assert "chunk index" in plan.partial_read_unavailable_reason


def test_range_support_without_a_chunk_index_is_not_a_partial_read(broker) -> None:
    """Knowing the server *would* serve a byte range is useless without knowing
    which bytes to ask for."""
    half = {**ZARR, "chunkIndexMethod": None}
    plan = broker.plan(document(), record(distributions=[half]))

    assert plan.mode == "redirect"
    assert "no chunk index" in plan.partial_read_unavailable_reason


def test_a_slice_request_prefers_a_path_that_can_serve_one(broker) -> None:
    """Fetching 4 TB to read a month of it is not a smaller inconvenience than
    making an account."""
    plan = broker.plan(
        document(),
        record(distributions=[CSV, CDS_API]),
        slice_spec=SliceSpec(time=("2019-01-01", "2019-12-31")),
    )
    assert plan.mode == "subsetting-protocol"
    assert plan.credentials["required"] is True


def test_without_a_slice_the_open_path_wins(broker) -> None:
    plan = broker.plan(document(), record(distributions=[CSV, CDS_API]))
    assert plan.distribution_id.endswith("--csv")


def test_the_requested_slice_is_echoed_back(broker) -> None:
    """So a client can see that a slice it asked for was understood — and, when
    it was not, that it was dropped rather than applied to the wrong axis."""
    spec = SliceSpec(time=("2019-01-01", "2019-12-31"), bbox=(5.9, 45.8, 10.5, 47.8))
    plan = broker.plan(document(), record(), slice_spec=spec)

    assert plan.requested_slice["time"] == ["2019-01-01", "2019-12-31"]
    assert plan.requested_slice["bbox"] == [5.9, 45.8, 10.5, 47.8]


def test_a_slice_that_cannot_be_pushed_down_says_so(broker) -> None:
    plan = broker.plan(
        document(),
        record(distributions=[CSV]),
        slice_spec=SliceSpec(time=("2019-01-01", "2019-12-31")),
    )
    assert any("after download" in c for c in plan.caveats)


# ---- read instructions ---------------------------------------------------


def test_the_plan_says_which_library_opens_it(broker) -> None:
    plan = broker.plan(document(), record(distributions=[ZARR]))

    assert plan.read_instructions["library"] == "xarray"
    assert plan.read_instructions["engine"] == "zarr"
    assert plan.read_instructions["protocol"] == "s3"
    assert plan.read_instructions["storage_options"] == {"anon": True}


def test_an_unknown_format_gets_no_instructions_rather_than_guessed_ones(broker) -> None:
    """A wrong engine costs a user more time than a missing one: they try it,
    it fails obscurely, and they conclude the data is broken."""
    odd = {**CSV, "formatLabel": "Proprietary binary v3", "mediaType": "application/x-vendor"}
    plan = broker.plan(document(), record(distributions=[odd]))

    assert plan.read_instructions == {}


def test_geoparquet_is_not_read_as_parquet() -> None:
    """Longest match first. `geoparquet` contains `parquet`, and pandas opening
    a GeoParquet loses the geometry."""
    from datahub.api.broker.plan import _format_key

    assert _format_key(DistributionDetail(id="d", format_label="GeoParquet")) == "geoparquet"
    assert READERS["geoparquet"]["library"] == "geopandas"


def test_requester_pays_reaches_the_storage_options(broker) -> None:
    """A client that omits it gets an access-denied that says nothing about
    why."""
    paid = {**ZARR, "requesterPays": True}
    plan = broker.plan(document(), record(distributions=[paid]))
    assert plan.read_instructions["storage_options"]["requester_pays"] is True


# ---- choosing among distributions ---------------------------------------


def test_a_dead_distribution_is_excluded_and_a_sibling_returned(broker) -> None:
    """PRD §F1.13. Excluded rather than merely ranked lower: a plan pointing at
    a URL we know is dead is worse than one saying the only path left is the
    gated one."""
    dead = {
        **CSV,
        "linkHealth": {"type": "LinkHealth", "linkHealthStatus": "unreachable"},
    }
    plan = broker.plan(document(), record(distributions=[dead, ZARR]))

    assert plan.distribution_id.endswith("--zarr-s3")
    assert plan.fallback_reason
    assert "skipped" in plan.fallback_reason


def test_when_every_path_is_dead_the_plan_says_so(broker) -> None:
    """Rather than returning nothing. The user can still try, and now knows
    what to expect."""
    dead = {**CSV, "linkHealth": {"type": "LinkHealth", "linkHealthStatus": "unreachable"}}
    plan = broker.plan(document(), record(distributions=[dead]))

    assert plan.distribution_id
    assert "expect it not to work" in plan.fallback_reason


def test_a_pinned_distribution_is_honoured_even_if_unhealthy(broker) -> None:
    """A client that pins a path usually knows something the prober does not —
    a transient outage, a network only they can reach."""
    dead = {**CSV, "linkHealth": {"type": "LinkHealth", "linkHealthStatus": "unreachable"}}
    plan = broker.plan(document(), record(distributions=[dead, ZARR]), distribution_id=dead["id"])

    assert plan.distribution_id == dead["id"]
    assert "You pinned" in plan.fallback_reason


def test_pinning_a_distribution_that_does_not_exist_is_refused(broker) -> None:
    with pytest.raises(NoUsableDistribution) as raised:
        broker.plan(document(), record(), distribution_id="https://example.org/nope")
    assert raised.value.context["available"]


def test_a_record_with_no_access_path_is_refused_with_a_hint(broker) -> None:
    with pytest.raises(NoUsableDistribution) as raised:
        broker.plan(document(reference_only=True), record(distributions=[]))
    assert "reference-only" in raised.value.context["hint"]


def test_an_unprobed_distribution_counts_as_reachable() -> None:
    """ "Nobody has checked" is not "it is broken". Treating it as broken would
    exclude every newly harvested record from every access plan."""
    fresh = DistributionDetail(id="d", access_url="https://example.org/x")
    probed_bad = DistributionDetail(
        id="e", access_url="https://example.org/y", link_health=LinkHealth(status="unreachable")
    )
    assert fresh.reachable is True
    assert probed_bad.reachable is False


# ---- the shape -----------------------------------------------------------


def test_the_plan_never_contains_data(broker) -> None:
    """The control-plane rule. The Hub is not in the read path — a decision
    about what OpenGrid is, not an optimisation."""
    plan = broker.plan(document(), record())
    fields = plan.__slots__

    assert "content" not in fields
    assert "body" not in fields
    assert "bytes" not in fields
    assert plan.location.startswith(("s3://", "http"))


def test_the_plan_expires(broker, settings) -> None:
    """PRD §12.9 leaves open whether removal from an allow-list revokes a plan
    already issued; a short TTL is the default answer, and it only works if
    there is one."""
    from datetime import UTC, datetime

    plan = broker.plan(document(), record())
    remaining = (plan.expires_at - datetime.now(UTC)).total_seconds()

    assert 0 < remaining <= settings.access_plan_ttl_s


def test_one_shape_whatever_the_size(broker) -> None:
    """800 KB and 4 TB produce the same object with a different mode."""
    small = broker.plan(document(), record(distributions=[CSV]))
    large = broker.plan(document(), record(distributions=[ZARR]))

    assert small.__slots__ == large.__slots__
    assert small.mode != large.mode
    for plan in (small, large):
        assert plan.dataset_id and plan.distribution_id and plan.location
        assert plan.path_rationale
