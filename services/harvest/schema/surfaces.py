"""One extractor per schema surface. Each returns what the source states.

The rule every extractor holds, and the reason this module is separate from
the thing that fetches: **a surface that carries no units yields fields with no
unit.** Nothing here infers, defaults or fills. The enricher may later draft a
*label* for a named field (ADR-0005); it may never invent the field, and this
module may never invent either.

Each extractor takes bytes already fetched and returns
:class:`ProbedField` values, so every one of them is testable offline against a
recorded payload — which matters, because the build environment cannot reach
most of the sources these read.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, replace
from typing import Any

from datahub.logging import get_logger

log = get_logger(__name__)

#: Array names that are coordinates rather than measured variables. A Zarr
#: store's `time`, `latitude` and `longitude` are the axes the data is indexed
#: by, and listing them as fields is like listing a CSV's row number as a
#: column. Kept as a name check *and* a shape check, because a store is free to
#: call its time axis `valid_time`.
COORDINATE_NAMES: frozenset[str] = frozenset(
    {
        "time",
        "valid_time",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "x",
        "y",
        "level",
        "depth",
        "height",
        "altitude",
        "pressure_level",
        "member",
        "step",
        "number",
        "realization",
        "bnds",
        "bounds",
        "crs",
        "spatial_ref",
        "index",
    }
)

#: numpy dtype letters to the names `og:dataType` carries. A dtype string is
#: `<f4`: byte order, kind, itemsize in bytes.
_DTYPE_KINDS: dict[str, str] = {
    "f": "float",
    "i": "int",
    "u": "uint",
    "b": "bool",
    "S": "bytes",
    "U": "str",
    "M": "datetime",
    "m": "timedelta",
    "c": "complex",
}


@dataclass(frozen=True, slots=True)
class ProbedField:
    """One field, carrying only values the source stated.

    ``read_from`` is what makes a wrong field traceable to a wrong parse rather
    than to nobody-knows. It names the document and the path within it, not the
    URL, because the URL is a property of the distribution and is recorded once
    on the record rather than 273 times on its fields.
    """

    local_name: str
    label: str | None = None
    definition: str | None = None
    data_type: str | None = None
    unit_as_stated: str | None = None
    read_from: str = ""

    def as_node(self, base: str, dataset_slug: str) -> dict[str, Any]:
        """An ``og:Field`` node, minting an IRI from the dataset and the name.

        Stable across runs, so a re-probe updates a field rather than
        accumulating duplicates of it.
        """
        node: dict[str, Any] = {
            "id": f"{base}{dataset_slug}/{_slug(self.local_name)}",
            "type": "Field",
            "localName": self.local_name,
            "fieldId": self.local_name,
        }
        if self.label:
            node["label"] = self.label
        if self.definition:
            node["definition"] = self.definition
        if self.data_type:
            node["dataType"] = self.data_type
        if self.unit_as_stated:
            # `unitAsStated` is the source's own string. `og:unit` is a QUDT
            # IRI and is the semantic layer's to assign (C5, level 3); writing
            # a guess here would be a unit claim nobody checked.
            node["unitAsStated"] = self.unit_as_stated
        return node


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name)).strip("-.")
    return cleaned or "field"


def numpy_dtype(raw: Any) -> str | None:
    """`<f4` to `float32`, in the vocabulary `og:dataType` uses."""
    if not raw:
        return None
    text = str(raw).lstrip("<>|=")
    kind = _DTYPE_KINDS.get(text[:1])
    if kind is None:
        return str(raw)
    if kind == "bool":
        return "bool"
    width = re.match(r"\d+", text[1:])
    return f"{kind}{int(width.group()) * 8}" if width else kind


def _is_coordinate(name: str, dims: list[str]) -> bool:
    """A coordinate: named like one, or a 1-D array indexed by itself."""
    return name.lower() in COORDINATE_NAMES or (len(dims) == 1 and dims[0] == name)


def decollide(fields: list[tuple[str, ProbedField]]) -> list[ProbedField]:
    """Resolve fields whose preferred short name is not unique.

    Taken from `(fallback_name, field)` pairs. A short name is preferred
    because it is what the concept vocabulary's altLabels are — `ssrd`,
    `swgdn`, `da_lmp` — so it is what makes a field resolvable to a concept at
    all. But it is not guaranteed unique: ERA5 publishes both `geopotential`
    and `geopotential_at_surface`, and GRIB calls both of them `z`.

    Where a name collides, **every member of the group** falls back to its
    array name, not just the second one seen. Letting the first keep the short
    name would make which variable is called `z` depend on sort order, and
    would mint one IRI for two different quantities — the store would then
    merge them into a single node carrying both sets of attributes.
    """
    counts: dict[str, int] = {}
    for _, probed in fields:
        counts[probed.local_name] = counts.get(probed.local_name, 0) + 1
    out: list[ProbedField] = []
    for fallback, probed in fields:
        if counts[probed.local_name] > 1 and fallback != probed.local_name:
            log.debug(
                "short name collides; using the array name",
                short_name=probed.local_name,
                array=fallback,
            )
            out.append(replace(probed, local_name=fallback))
        else:
            out.append(probed)
    return out


# ---------------------------------------------------------------------------
# Zarr
# ---------------------------------------------------------------------------


def from_zarr_v2(payload: bytes) -> list[ProbedField]:
    """Zarr v2 consolidated metadata: `<store>/.zmetadata`.

    The richest surface in the catalog and the cheapest to read — one request
    for a whole store's schema. CF conventions put `long_name` and `units` on
    every variable, which is why ERA5 yields 273 fields all carrying both.
    """
    meta = json.loads(payload).get("metadata", {})
    fields: list[tuple[str, ProbedField]] = []
    for key in sorted(meta):
        if not key.endswith("/.zarray"):
            continue
        array = key.split("/")[0]
        attrs = meta.get(f"{array}/.zattrs") or {}
        if _is_coordinate(array, list(attrs.get("_ARRAY_DIMENSIONS") or [])):
            continue
        fields.append(
            (
                array,
                ProbedField(
                    local_name=str(attrs.get("short_name") or array),
                    label=attrs.get("long_name") or attrs.get("standard_name"),
                    definition=attrs.get("description") or attrs.get("comment"),
                    data_type=numpy_dtype((meta.get(key) or {}).get("dtype")),
                    unit_as_stated=attrs.get("units"),
                    read_from=f".zmetadata:{array}/.zattrs",
                ),
            )
        )
    return decollide(fields)


def from_zarr_v3(payload: bytes) -> list[ProbedField]:
    """Zarr v3: `<store>/zarr.json`, whose consolidated form nests children."""
    document = json.loads(payload)
    consolidated = ((document.get("consolidated_metadata") or {}).get("metadata")) or {}
    fields: list[tuple[str, ProbedField]] = []
    for name in sorted(consolidated):
        node = consolidated[name] or {}
        if node.get("node_type") != "array":
            continue
        attrs = node.get("attributes") or {}
        if _is_coordinate(name, list(attrs.get("_ARRAY_DIMENSIONS") or [])):
            continue
        fields.append(
            (
                name,
                ProbedField(
                    local_name=str(attrs.get("short_name") or name),
                    label=attrs.get("long_name") or attrs.get("standard_name"),
                    definition=attrs.get("description") or attrs.get("comment"),
                    data_type=numpy_dtype(node.get("data_type")),
                    unit_as_stated=attrs.get("units"),
                    read_from=f"zarr.json:consolidated_metadata.{name}",
                ),
            )
        )
    return decollide(fields)


# ---------------------------------------------------------------------------
# Tabular
# ---------------------------------------------------------------------------


def from_csv_header(payload: bytes) -> list[ProbedField]:
    """The first line of a CSV, read with a range request.

    Column *names* only. A CSV states no units and no definitions, so this
    reaches C1 and stops — which is still the difference between a record that
    lists its columns and one that says nothing about its shape.
    """
    text = payload.decode("utf-8", errors="replace")
    line = next(iter(text.splitlines()), "")
    if not line:
        return []
    # Sniff the delimiter rather than assuming a comma: `;` is the European
    # convention and tab-separated files are common in this domain.
    try:
        dialect = csv.Sniffer().sniff(line, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    names = next(csv.reader(io.StringIO(line), delimiter=delimiter), [])
    seen: set[str] = set()
    fields: list[ProbedField] = []
    for name in names:
        cleaned = name.strip().strip('"').lstrip("\ufeff")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        fields.append(ProbedField(local_name=cleaned, read_from="header row"))
    return fields


def from_datapackage(payload: bytes) -> list[ProbedField]:
    """A Frictionless `datapackage.json`.

    The richest tabular surface there is — name, title, description, type and
    unit per field, in a standard this modelling community already publishes
    in (Open Power System Data, most notably).
    """
    document = json.loads(payload)
    fields: list[ProbedField] = []
    for resource in document.get("resources") or []:
        if not isinstance(resource, dict):
            continue
        name = resource.get("name", "")
        for spec in (resource.get("schema") or {}).get("fields") or []:
            if not isinstance(spec, dict) or not spec.get("name"):
                continue
            fields.append(
                ProbedField(
                    local_name=str(spec["name"]),
                    label=spec.get("title"),
                    definition=spec.get("description"),
                    data_type=spec.get("type"),
                    unit_as_stated=spec.get("unit"),
                    read_from=f"datapackage.json:resources[{name}].schema.fields",
                )
            )
    return fields


# ---------------------------------------------------------------------------
# Service catalogues
# ---------------------------------------------------------------------------


def from_stac_datacube(payload: bytes) -> list[ProbedField]:
    """`cube:variables` on a STAC collection (the datacube extension).

    Where a catalogue implements it this is field metadata with units and
    dimensions, from a source the harvester already crawls.
    """
    document = json.loads(payload)
    variables = document.get("cube:variables") or (document.get("properties") or {}).get(
        "cube:variables"
    )
    dimensions = document.get("cube:dimensions") or {}
    fields = []
    for name, spec in (variables or {}).items():
        if not isinstance(spec, dict):
            continue
        if name in dimensions or spec.get("type") == "auxiliary":
            continue
        fields.append(
            ProbedField(
                local_name=str(name),
                label=spec.get("description"),
                data_type=spec.get("type"),
                unit_as_stated=spec.get("unit"),
                read_from=f"cube:variables.{name}",
            )
        )
    return fields


def from_arcgis(payload: bytes) -> list[ProbedField]:
    """An ArcGIS FeatureServer or MapServer layer: `?f=json` -> `fields[]`.

    HIFLD and most US utility and state GIS portals speak this, which is a
    large part of DD1 and DD10.
    """
    document = json.loads(payload)
    fields = []
    for spec in document.get("fields") or []:
        if not isinstance(spec, dict) or not spec.get("name"):
            continue
        fields.append(
            ProbedField(
                local_name=str(spec["name"]),
                label=spec.get("alias") if spec.get("alias") != spec.get("name") else None,
                data_type=str(spec.get("type", "")).removeprefix("esriFieldType") or None,
                read_from=f"fields[{spec['name']}]",
            )
        )
    return fields


def from_socrata(payload: bytes) -> list[ProbedField]:
    """A Socrata view: `/api/views/{id}.json` -> `columns[]`. Most US city and
    state open-data portals, which is where DD4 and DD8 live."""
    document = json.loads(payload)
    fields = []
    for spec in document.get("columns") or []:
        if not isinstance(spec, dict) or not spec.get("fieldName"):
            continue
        fields.append(
            ProbedField(
                local_name=str(spec["fieldName"]),
                label=spec.get("name"),
                definition=spec.get("description"),
                data_type=spec.get("dataTypeName"),
                read_from=f"columns[{spec['fieldName']}]",
            )
        )
    return fields


def from_ckan_datastore(payload: bytes) -> list[ProbedField]:
    """A CKAN datastore data dictionary: `datastore_search` -> `fields[]`,
    where `info` carries the curator's own label and description."""
    document = json.loads(payload)
    result = document.get("result") or document
    fields = []
    for spec in result.get("fields") or []:
        if not isinstance(spec, dict) or not spec.get("id") or spec.get("id") == "_id":
            continue
        info = spec.get("info") or {}
        fields.append(
            ProbedField(
                local_name=str(spec["id"]),
                label=info.get("label") or None,
                definition=info.get("notes") or None,
                data_type=spec.get("type"),
                unit_as_stated=info.get("unit") or None,
                read_from=f"result.fields[{spec['id']}]",
            )
        )
    return fields


__all__ = [
    "COORDINATE_NAMES",
    "ProbedField",
    "decollide",
    "from_arcgis",
    "from_ckan_datastore",
    "from_csv_header",
    "from_datapackage",
    "from_socrata",
    "from_stac_datacube",
    "from_zarr_v2",
    "from_zarr_v3",
    "numpy_dtype",
]
