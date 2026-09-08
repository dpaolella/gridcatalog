"""Probed fields must reach git, and must not override the record (#45).

`datahub schema probe` reads a dataset's own schema surface and it works: ERA5
publishes 273 variables to a store that answers in one 130 KB request. But the
probe ran only inside `pages.yml`, wrote into a graph the runner throws away,
and never reached the repository — so `data/catalog` held **4** fields for ERA5
and the published site showed **273**, every deploy re-fetched them, and the
site was not reproducible from a checkout.

The fix cannot be "export those records like harvested ones". ERA5 is
`curated`: the build regenerates it from `data/seed-sources.yaml` and the
golden set, and `pages.yml` loads `data/catalog/*/` last, so a committed
whole-record copy wins over the regenerated one. That is #18's regression,
which `tests/test_committed_catalog.py` covers. A sidecar carrying only
`og:hasField` adds to the record instead of replacing it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "data" / "schemas"
CATALOG = ROOT / "data" / "catalog"


def sidecars() -> list[Path]:
    return sorted(SCHEMAS.glob("*.jsonld"))


def test_the_repository_actually_holds_the_probed_schemas() -> None:
    """The headline number in #45: 4 fields for ERA5 in git, 273 on the site."""
    era5 = SCHEMAS / "ecmwf-era5.jsonld"
    assert era5.exists(), "no persisted schema for ERA5; #45's whole example is unfixed"
    fields = json.loads(era5.read_text())["hasField"]
    assert len(fields) > 200, (
        f"ERA5's persisted schema has {len(fields)} fields; its store documents 273, and "
        "4 was the number that made the repository disagree with the published site"
    )


@pytest.mark.parametrize("path", sidecars(), ids=lambda p: p.stem)
def test_a_sidecar_carries_only_fields(path: Path) -> None:
    """The property that makes a sidecar safe where a record copy is not.

    A sidecar with a `license`, an `accessURL` or a `title` would override the
    regenerated record's version of those on load, which is exactly the failure
    mode #18 hit. Keeping it to fields means the worst a stale sidecar can do
    is describe a field that no longer exists.
    """
    document = json.loads(path.read_text())
    allowed = {"@context", "id", "hasField", "schemaSource"}
    extra = sorted(set(document) - allowed)
    assert not extra, (
        f"{path.name} carries {extra} beyond the field payload; a sidecar that states "
        "anything else overrides the record the build regenerates"
    )
    assert document.get("hasField"), f"{path.name} states no fields, so it should not exist"


def test_sidecars_do_not_duplicate_the_committed_catalog() -> None:
    """A harvested record keeps its fields in its own file; a sidecar is for the rest.

    Not an error if they overlap — the merge is additive and dedupes — but two
    homes for one record's schema is how they drift, so this pins the current
    separation and fails loudly if a change starts writing both.
    """
    committed = {p.stem for p in CATALOG.glob("*/*.jsonld")}
    both = sorted({p.stem for p in sidecars()} & committed)
    assert len(both) < 40, (
        f"{len(both)} records now carry a schema in both data/catalog and data/schemas: {both[:5]}"
    )


def test_merge_keeps_the_hand_authored_field() -> None:
    """The rule the sidecar and the live probe share, asserted on the real case.

    ERA5's `ssrd` states that the value is accumulated rather than
    instantaneous and that a consumer reading it as W/m2 overstates irradiance
    by 3600x. A probe knows its long name. Overwriting the first with the
    second is a loss dressed as an update.
    """
    from datahub.harvest.schema import merge_fields

    curated = {
        "localName": "ssrd",
        "unit": "http://qudt.org/vocab/unit/W-PER-M2",
        "valueBasis": "modeled",
        "completenessCaveats": "Accumulated, not instantaneous.",
    }
    document = {"hasField": [curated]}
    merge_fields(
        document,
        [
            {"localName": "ssrd", "label": "Surface solar radiation downwards"},
            {"localName": "t2m", "label": "2 metre temperature"},
        ],
        schema_source="zarr-v2-consolidated at https://example.invalid",
    )

    by_name = {f["localName"]: f for f in document["hasField"]}
    assert len(document["hasField"]) == 2, "the probed ssrd was added beside the curated one"
    assert by_name["ssrd"] == curated, "the probe overwrote a hand-authored field"
    assert by_name["t2m"]["label"] == "2 metre temperature", "a genuinely new field was dropped"
