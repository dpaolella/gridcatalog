"""A well-formed STAC collection must yield a valid level-1 record (#31 item 2).

Both configured STAC sources produced **100% flagged** records. STAC is the
adapter PRD 7.1 relies on for DD5 and DD10 geospatial coverage, so those two
domains could not be populated by harvest at all.

Two shapes refused every record, and both refusals were correct:

* `og:geometryTypes` -- a STAC collection is geospatial-primary by definition,
  and `geospatialPrimary: true` was set as a constant while no geometry type
  was ever derived. The shape requires one at level 1.
* `og:chunkIndexMethod` -- `supportsRangeRequests` was set from a media-type
  marker list, and a distribution that advertises range reads without saying
  how to find the bytes produces a partial-read plan that does not work.

The fix derives both from the assets the collection advertises, and derives
*nothing* where the media type does not say.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from datahub.config import get_settings
from datahub.harvest.adapters.stac import StacAdapter, _range_index
from datahub.harvest.normalizers.engine import Normalizer
from datahub.harvest.validate.runner import ValidationRunner

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "harvest"


def _records():
    body = json.loads((FIXTURES / "stac_collections.json").read_text())
    adapter = StacAdapter(
        "earth_search_stac",
        endpoint="https://earth-search.aws.element84.com/v1",
        rate_per_second=0,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    )
    normalizer = Normalizer("stac", settings=get_settings())
    validator = ValidationRunner(settings=get_settings())
    for record in adapter.iter_records():
        result = normalizer.normalize(record)
        if not result.document:
            continue
        level = int(result.document.get("completenessLevel", 1))
        yield result.document, validator.validate_jsonld(result.document, level)


def test_a_collection_that_advertises_assets_validates_at_level_1() -> None:
    """The assertion #31 asks for, scoped to what it can honestly cover.

    Not "zero flagged for both sources": one fixture collection advertises no
    assets at all -- no `assets`, no `item_assets`, no item links -- so there
    is genuinely nothing to catalogue and flagging it is the correct answer,
    not a mapping defect. The rule is about collections that *do* say where
    their data is.
    """
    checked = 0
    for document, report in _records():
        if not (document.get("distribution") or []):
            continue
        checked += 1
        assert report.conforms, (
            f"{document.get('id')} advertises a distribution and still fails level 1: "
            f"{[v.message[:120] for v in report.violations]}"
        )
    assert checked, "no fixture collection advertises an asset, so this test covers nothing"


def test_a_raster_collection_states_its_geometry() -> None:
    for document, _ in _records():
        if document.get("distribution"):
            assert document.get("geometryTypes") == ["raster"], (
                f"{document.get('id')} advertises GeoTIFF assets and declares "
                f"{document.get('geometryTypes')!r}"
            )


def test_a_collection_with_nothing_to_offer_is_still_refused() -> None:
    """The other half, so the fix cannot become "make everything pass".

    A record with no distribution cannot answer "where do I get it", which is
    one of the four things the catalog exists to answer. Passing it would be
    worse than flagging it.
    """
    refused = [d for d, r in _records() if not r.conforms]
    assert refused, "every fixture collection now passes, including one with no assets"
    assert all(not (d.get("distribution") or []) for d in refused), (
        "a collection with a real distribution is still being refused"
    )


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("image/tiff; application=geotiff; profile=cloud-optimized", "cog-tiles"),
        ("application/x-parquet", "parquet-rowgroup"),
        ("application/x-netcdf", "netcdf4-chunks"),
        ("image/png", None),
        ("application/json", None),
    ],
)
def test_range_support_and_index_method_are_one_fact(media_type: str, expected) -> None:
    """Derived together so they cannot disagree.

    The shape's own message offers the alternative -- "or set
    og:supportsRangeRequests false" -- and that is what an unknown index means
    here: no claim, rather than a claim the broker cannot act on.
    """
    assert _range_index(media_type) == expected


def test_zarr_does_not_guess_a_version() -> None:
    """The instructive omission.

    The media type says "zarr" and not which version, and v2's `.zarray` and
    v3's `zarr.json` are different files in different places. The schema prober
    tries both because it can afford to be wrong once; a published access plan
    cannot.
    """
    assert _range_index("application/vnd+zarr") is None
