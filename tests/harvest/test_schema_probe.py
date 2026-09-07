"""Reading a dataset's own schema surface (WP-11.4).

The rule every test here defends: **a probe reports what the source states and
nothing else.** A surface with no units yields fields with no unit, a surface
that cannot be read yields no fields and says why, and a record that already
describes a field keeps its description.

`tests/fixtures/schema/` holds real payloads rather than hand-written ones,
because an extractor tested against a guess about a format is tested against a
guess.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.schema import ProbeOutcome, SchemaProber, apply, surfaces_for, to_http
from datahub.harvest.schema.surfaces import (
    from_arcgis,
    from_ckan_datastore,
    from_csv_header,
    from_datapackage,
    from_socrata,
    from_stac_datacube,
    from_zarr_v2,
    from_zarr_v3,
    numpy_dtype,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "schema"
ERA5 = (FIXTURES / "era5-arco.zmetadata.json").read_bytes()


# ---- the ERA5 case this package exists for -------------------------------


def test_era5_yields_every_variable_it_publishes() -> None:
    """The number that motivated the whole work package.

    The catalog published 4 hand-typed fields for ERA5. The store publishes
    273, each with a long name and a unit, in one 130 KB read.
    """
    fields = from_zarr_v2(ERA5)
    assert len(fields) == 273
    assert all(f.unit_as_stated for f in fields)
    assert all(f.label for f in fields)


def test_coordinates_are_not_fields() -> None:
    """`time`, `latitude` and `longitude` are the axes the data is indexed by.
    Listing them as fields is like listing a CSV's row number as a column."""
    names = {f.local_name for f in from_zarr_v2(ERA5)}
    assert not names & {"time", "latitude", "longitude", "level"}


def test_a_colliding_short_name_falls_back_for_both_of_them() -> None:
    """ERA5 publishes `geopotential` and `geopotential_at_surface`, and GRIB
    calls both `z`.

    Both fall back to the array name, not just the second one seen. Letting the
    first keep `z` would make which variable is called `z` depend on sort
    order, and would mint one IRI for two different quantities — the store
    would then merge them into a single node carrying both sets of attributes.
    """
    names = [f.local_name for f in from_zarr_v2(ERA5)]
    assert len(names) == len(set(names)), "every field name is unique"
    assert "geopotential" in names
    assert "geopotential_at_surface" in names
    assert "z" not in names


def test_a_unique_short_name_is_kept() -> None:
    """The short name is what makes a field resolvable: the concept
    vocabulary's altLabels are `ssrd`, `swgdn`, `da_lmp`. Falling back to array
    names wholesale would break concept resolution to avoid a rare collision."""
    names = {f.local_name for f in from_zarr_v2(ERA5)}
    assert {"ssrd", "t2m", "u100", "ro"} <= names


def test_a_field_records_where_it_was_read_from() -> None:
    """So a wrong field is traceable to a wrong parse rather than to
    nobody-knows."""
    field = next(f for f in from_zarr_v2(ERA5) if f.local_name == "ssrd")
    assert field.read_from == ".zmetadata:surface_solar_radiation_downwards/.zattrs"


def test_the_unit_is_the_sources_own_string_not_a_qudt_iri() -> None:
    """`og:unit` is a QUDT IRI and is the semantic layer's to assign at level
    3. Writing one here would be a unit claim nobody checked."""
    field = next(f for f in from_zarr_v2(ERA5) if f.local_name == "t2m")
    node = field.as_node("https://catalog.opengrid.org/field/", "ecmwf-era5")
    assert node["unitAsStated"] == "K"
    assert "unit" not in node


# ---- the other surfaces --------------------------------------------------


def test_zarr_v3_consolidated_metadata() -> None:
    payload = json.dumps(
        {
            "zarr_format": 3,
            "consolidated_metadata": {
                "metadata": {
                    "t2m": {
                        "node_type": "array",
                        "data_type": "float32",
                        "attributes": {
                            "long_name": "2 metre temperature",
                            "units": "K",
                            "_ARRAY_DIMENSIONS": ["time", "lat", "lon"],
                        },
                    },
                    "time": {
                        "node_type": "array",
                        "data_type": "int64",
                        "attributes": {"_ARRAY_DIMENSIONS": ["time"]},
                    },
                }
            },
        }
    ).encode()
    fields = from_zarr_v3(payload)
    assert [f.local_name for f in fields] == ["t2m"]
    assert fields[0].unit_as_stated == "K"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b"a,b,c\n1,2,3", ["a", "b", "c"]),
        (b"a;b;c\n1;2;3", ["a", "b", "c"]),
        (b"a\tb\tc\n1\t2\t3", ["a", "b", "c"]),
        (b'"country","capacity_mw"\n', ["country", "capacity_mw"]),
        # A BOM is invisible and would otherwise become part of the first name.
        ("﻿name,value\n".encode(), ["name", "value"]),
    ],
)
def test_a_csv_header_is_read_whatever_separates_it(line: bytes, expected: list[str]) -> None:
    assert [f.local_name for f in from_csv_header(line)] == expected


def test_a_csv_yields_names_and_makes_no_further_claim() -> None:
    """A CSV states no units and no definitions. Reaching C1 and stopping is
    the honest outcome, and still the difference between a record that lists
    its columns and one that says nothing about its shape."""
    fields = from_csv_header(b"country,capacity_mw,commissioning_year\n")
    assert all(f.unit_as_stated is None and f.definition is None for f in fields)


def test_a_datapackage_carries_the_richest_tabular_metadata() -> None:
    payload = json.dumps(
        {
            "resources": [
                {
                    "name": "load",
                    "schema": {
                        "fields": [
                            {
                                "name": "DE_load_actual",
                                "title": "Germany, actual load",
                                "description": "Total load as published by ENTSO-E.",
                                "type": "number",
                                "unit": "MW",
                            }
                        ]
                    },
                }
            ]
        }
    ).encode()
    (field,) = from_datapackage(payload)
    assert field.local_name == "DE_load_actual"
    assert field.unit_as_stated == "MW"
    assert field.definition.startswith("Total load")


def test_stac_datacube_skips_the_dimensions() -> None:
    payload = json.dumps(
        {
            "cube:dimensions": {"time": {"type": "temporal"}},
            "cube:variables": {
                "time": {"type": "data"},
                "ghi": {"type": "data", "unit": "W/m2", "description": "Global horizontal"},
            },
        }
    ).encode()
    assert [f.local_name for f in from_stac_datacube(payload)] == ["ghi"]


def test_arcgis_drops_the_esri_type_prefix() -> None:
    payload = json.dumps(
        {"fields": [{"name": "VOLTAGE", "alias": "Voltage (kV)", "type": "esriFieldTypeDouble"}]}
    ).encode()
    (field,) = from_arcgis(payload)
    assert field.data_type == "Double"
    assert field.label == "Voltage (kV)"


def test_socrata_and_ckan_read_their_own_column_lists() -> None:
    socrata = json.dumps(
        {"columns": [{"fieldName": "plant_id", "name": "Plant ID", "dataTypeName": "number"}]}
    ).encode()
    (field,) = from_socrata(socrata)
    assert (field.local_name, field.data_type) == ("plant_id", "number")

    ckan = json.dumps(
        {
            "result": {
                "fields": [
                    {"id": "_id", "type": "int"},
                    {"id": "mw", "type": "numeric", "info": {"label": "Capacity", "unit": "MW"}},
                ]
            }
        }
    ).encode()
    (field,) = from_ckan_datastore(ckan)
    assert (field.local_name, field.unit_as_stated) == ("mw", "MW")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("<f4", "float32"), (">f8", "float64"), ("<i8", "int64"), ("|b1", "bool"), (None, None)],
)
def test_numpy_dtypes_become_the_names_the_schema_uses(raw, expected) -> None:
    assert numpy_dtype(raw) == expected


# ---- dispatch and URIs ---------------------------------------------------


@pytest.mark.parametrize(
    ("url", "media", "first"),
    [
        ("s3://era5-pds/zarr/", "application/vnd+zarr", "zarr-v2-consolidated"),
        ("https://x/data.zarr", None, "zarr-v2-consolidated"),
        ("https://x/plants.csv", None, "csv-header"),
        ("https://x/datapackage.json", None, "datapackage"),
        ("https://services.arcgis.com/a/FeatureServer/0", None, "arcgis-fields"),
        ("https://data.city.gov/api/views/ab-12.json", None, "socrata-columns"),
    ],
)
def test_dispatch_picks_a_surface_from_the_media_type_or_the_url(url, media, first) -> None:
    """Many records carry a perfectly good `.csv` URL and no media type at
    all, so the URL shape has to be enough on its own."""
    assert surfaces_for(url, media)[0].name == first


def test_a_format_with_no_readable_surface_is_not_probed() -> None:
    """A PDF has no machine-readable schema. Not attempting it is the point:
    the probe is bounded by what it will *try*, not only by what it reads."""
    assert surfaces_for("https://x/report.pdf", "application/pdf") == ()


def test_object_store_uris_resolve_to_something_http_can_read() -> None:
    """`broker/prober.py` documents that HTTP cannot reach an `s3://` URI. That
    is why ERA5 had no readable surface despite publishing 273 variables."""
    assert to_http("s3://era5-pds/zarr/") == "https://era5-pds.s3.amazonaws.com/zarr/"
    assert to_http("gs://b/k").startswith("https://storage.googleapis.com/b/k")
    # A dotted bucket breaks the wildcard certificate, so it takes path style.
    assert to_http("s3://my.bucket/k") == "https://s3.amazonaws.com/my.bucket/k"
    assert to_http("ftp://x/y") is None


# ---- merging onto a record -----------------------------------------------


def era5_outcome() -> ProbeOutcome:
    return ProbeOutcome(
        dataset_id="https://catalog.opengrid.org/ds/ecmwf-era5",
        fields=from_zarr_v2(ERA5),
        surface="zarr-v2-consolidated",
        source_url="https://storage.googleapis.com/b/era5.zarr",
    )


def test_a_probe_adds_fields_without_touching_the_ones_already_there() -> None:
    """The merge rule, and the reason it is a merge.

    A hand-authored field carries a resolved concept, a QUDT unit and a stated
    caveat — ERA5's `ssrd` says that reading it as W/m2 overstates irradiance
    by 3600x. Overwriting that with `long_name` would be a loss dressed as an
    update. Refusing to touch the record at all would leave four descriptions
    and a silent gap where 269 more fields exist.
    """
    hand_authored = {
        "id": "https://catalog.opengrid.org/field/ecmwf-era5/ssrd",
        "type": "Field",
        "localName": "ssrd",
        "concept": "https://schema.opengrid.org/concept/grid-concept/globalHorizontalIrradiance",
        "completenessCaveats": "Accumulated, not instantaneous.",
    }
    document = {"id": "x", "hasField": [hand_authored]}
    apply(document, era5_outcome(), slug="ecmwf-era5")

    fields = document["hasField"]
    assert len(fields) == 273, "4 hand-authored deduped against 273 probed"
    ssrd = [f for f in fields if f["localName"] == "ssrd"]
    assert len(ssrd) == 1, "no duplicate ssrd"
    assert ssrd[0]["concept"], "the concept survived"
    assert ssrd[0]["completenessCaveats"], "the caveat survived"


def test_a_record_carrying_fields_as_bare_iris_still_dedupes() -> None:
    """A record on disk holds its fields as a flat `@graph` of IRIs, not as
    nested nodes. Deduping only the nested shape would add a second `ssrd`
    beside the one that carries the concept."""
    document = {
        "id": "x",
        "hasField": ["https://catalog.opengrid.org/field/ecmwf-era5/ssrd"],
    }
    apply(document, era5_outcome(), slug="ecmwf-era5")
    probed_ssrd = [
        f for f in document["hasField"] if isinstance(f, dict) and f["localName"] == "ssrd"
    ]
    assert probed_ssrd == []


def test_a_probe_that_found_nothing_changes_nothing() -> None:
    """A failed probe is not a failed record. PRD §6: the completeness level
    says how far the record got, and level 1 is what it is for."""
    document = {"id": "x", "hasField": []}
    before = dict(document)
    apply(document, ProbeOutcome(dataset_id="x", reason="no surface"), slug="x")
    assert document == before
    assert "schemaSource" not in document


def test_the_record_says_where_its_schema_came_from() -> None:
    document = {"id": "x"}
    apply(document, era5_outcome(), slug="ecmwf-era5")
    assert "zarr-v2-consolidated" in document["schemaSource"]
    assert "era5.zarr" in document["schemaSource"]


# ---- the fetch limits ----------------------------------------------------


def test_a_body_over_the_cap_is_abandoned_rather_than_downloaded() -> None:
    """*A prober that fetched what it was checking would move terabytes a week
    across sources that did not ask to be crawled* — `broker/prober.py`. The
    same restraint, enforced while streaming rather than by trusting a
    header."""
    oversized = b"x" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    prober = SchemaProber(client=client, max_bytes=1000)
    assert prober.fetch("https://x/big.json", ranged=False) is None


def test_a_declared_length_over_the_cap_is_refused_before_reading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}", headers={"content-length": "999999999"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert SchemaProber(client=client, max_bytes=1000).fetch("https://x/b", ranged=False) is None


def test_a_csv_probe_asks_for_a_range_not_a_file() -> None:
    """Without this a CSV probe pulls whatever the file happens to be, which
    for this catalog is sometimes terabytes."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(206, content=b"a,b\n1,2")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    prober = SchemaProber(client=client)
    fields, _surface, _ = prober.probe_distribution("https://x/huge.csv", "text/csv")
    assert [f.local_name for f in fields] == ["a", "b"]
    assert seen["range"].startswith("bytes=0-")


@pytest.mark.parametrize("status", [403, 404, 500])
def test_an_unreadable_surface_is_a_gap_not_a_crash(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    prober = SchemaProber(client=client)
    outcome = prober.probe(
        {
            "id": "x",
            "distribution": [{"id": "d", "accessURL": "https://x/a.zarr", "mediaType": "zarr"}],
        }
    )
    assert not outcome.found
    assert outcome.reason


def test_a_surface_that_does_not_parse_is_skipped_not_raised() -> None:
    """A store serving HTML where JSON was expected is a normal thing on the
    open web, and it must not end a 1,199-record harvest."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>nope</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    prober = SchemaProber(client=client)
    fields, _, _ = prober.probe_distribution("https://x/a.zarr", "application/vnd+zarr")
    assert fields == []


def test_the_first_distribution_with_a_schema_wins() -> None:
    """A dataset published as Zarr *and* as CSV has one schema described
    twice. Merging two readings of it would invent disagreements the source
    never had."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/.zmetadata"):
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "metadata": {
                            "t2m/.zarray": {"dtype": "<f4"},
                            "t2m/.zattrs": {"units": "K", "_ARRAY_DIMENSIONS": ["time"]},
                        }
                    }
                ).encode(),
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    outcome = SchemaProber(client=client).probe(
        {
            "id": "x",
            "distribution": [
                {"id": "z", "accessURL": "https://x/a.zarr", "mediaType": "application/vnd+zarr"},
                {"id": "c", "accessURL": "https://x/a.csv", "mediaType": "text/csv"},
            ],
        }
    )
    assert outcome.distribution_id == "z"
    assert not any(url.endswith(".csv") for url in calls)


def test_a_ranged_read_is_capped_on_what_arrives_not_on_what_was_asked_for() -> None:
    """A `Range` header bounds the wire, not the process.

    GitHub serves the WRI power-plant CSV gzipped, so a 64 KB range decoded to
    431 KB on the way in — not a download, but seven times what was asked for,
    and the same ratio on a larger file is not harmless. The cap is therefore
    enforced on bytes received.
    """
    body = b"col_a,col_b\n" + b"x" * 500_000

    def handler(request: httpx.Request) -> httpx.Response:
        # A server that honours the range on the wire and still hands back a
        # much larger decoded body.
        return httpx.Response(206, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    payload = SchemaProber(client=client).fetch("https://x/huge.csv", ranged=True)
    assert payload is not None
    assert len(payload) == 65536, "capped at the header allowance, whatever arrived"
    assert [f.local_name for f in from_csv_header(payload)] == ["col_a", "col_b"]
