"""Spike: read a dataset's *own* schema surface and emit `og:Field` nodes.

Evidence for the field-metadata half of the ingestion plan. Not wired into the
harvest pipeline and not imported by anything under `services/`; it exists to
establish that the field metadata the catalog is missing is *already published*
by the datasets themselves, in machine-readable form, and costs one HTTP
request to read.

Four extractors, one per schema surface, chosen because between them they cover
every distribution format in the current catalog:

    zarr        <store>/.zmetadata  (v2, consolidated) or <store>/zarr.json (v3)
    csv         Range: bytes=0-N, first line
    datapackage <dir>/datapackage.json  (Frictionless)
    stac        a STAC collection's `cube:variables` (datacube extension)

Every extractor returns *only what the source states*. Nothing is inferred, no
model is called, and a surface that carries no units yields fields with no
unit rather than a guessed one — the same rule `services/harvest/enrich`
already holds for dataset-level metadata (PRD §7.4, ADR-0005).

    python docs/ingestion/spikes/schema_probe.py --targets docs/ingestion/spikes/targets.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TIMEOUT = 60
UA = "opengrid-datahub-spike/0.1 (+https://github.com/dpaolella/gridcatalog)"

#: Zarr array attributes that are *coordinates*, not measured variables. Kept
#: out of the field list for the same reason a CSV's row index is not a column.
COORD_HINTS = frozenset({"time", "latitude", "longitude", "lat", "lon", "level", "depth", "x", "y"})


@dataclass
class ProbedField:
    """One `og:Field`, carrying only source-stated values."""

    local_name: str
    label: str | None = None
    definition: str | None = None
    data_type: str | None = None
    unit_as_stated: str | None = None
    #: Where in the source document each value above was read from. This is
    #: what makes the result auditable rather than a claim.
    read_from: str = ""


@dataclass
class ProbeResult:
    target: str
    surface: str
    ok: bool
    fields: list[ProbedField] = field(default_factory=list)
    bytes_read: int = 0
    error: str | None = None

    @property
    def n(self) -> int:
        return len(self.fields)


def _get(url: str, *, byte_range: tuple[int, int] | None = None) -> tuple[bytes, int]:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    if byte_range:
        request.add_header("Range", f"bytes={byte_range[0]}-{byte_range[1]}")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = response.read()
    return body, len(body)


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------


def probe_zarr(store: str) -> ProbeResult:
    """Zarr v2 consolidated metadata, falling back to v3.

    One request for the whole store's schema. CF conventions put `long_name`
    and `units` on every variable, so a Zarr store is the richest field surface
    in the catalog and the cheapest to read.
    """
    base = store.rstrip("/")
    for name, surface in ((".zmetadata", "zarr-v2-consolidated"), ("zarr.json", "zarr-v3")):
        try:
            body, size = _get(f"{base}/{name}")
        except Exception:
            continue
        document = json.loads(body)
        meta = document.get("metadata", document)
        fields: list[ProbedField] = []
        for key in sorted(meta):
            if not key.endswith("/.zarray"):
                continue
            array = key.split("/")[0]
            attrs = meta.get(f"{array}/.zattrs", {})
            dims = attrs.get("_ARRAY_DIMENSIONS") or []
            # A 1-D array whose only dimension is itself is a coordinate.
            if array.lower() in COORD_HINTS or (len(dims) == 1 and dims[0] == array):
                continue
            fields.append(
                ProbedField(
                    local_name=attrs.get("short_name") or array,
                    label=attrs.get("long_name") or attrs.get("standard_name"),
                    definition=attrs.get("description") or attrs.get("comment"),
                    data_type=_numpy_dtype(meta[key].get("dtype")),
                    unit_as_stated=attrs.get("units"),
                    read_from=f"{name}:{array}/.zattrs",
                )
            )
        return ProbeResult(store, surface, True, fields, size)
    return ProbeResult(store, "zarr", False, error="no .zmetadata and no zarr.json")


def probe_csv_header(url: str, *, limit: int = 65_535) -> ProbeResult:
    """The first line of a CSV, via a range request.

    Column *names* only. A CSV states no units and no definitions, so this
    reaches C1 (`og:localName`, `og:fieldId`) and stops there — which is still
    the difference between a record that lists its columns and one that does
    not.
    """
    try:
        body, size = _get(url, byte_range=(0, limit))
    except Exception as exc:
        return ProbeResult(url, "csv-header", False, error=str(exc))
    text = body.decode("utf-8", errors="replace")
    first = text.splitlines()[0] if text.splitlines() else ""
    names = next(csv.reader(io.StringIO(first)), [])
    fields = [
        ProbedField(local_name=name.strip(), read_from="header row")
        for name in names
        if name.strip()
    ]
    return ProbeResult(url, "csv-header", True, fields, size)


def probe_datapackage(url: str) -> ProbeResult:
    """A Frictionless `datapackage.json`.

    The richest tabular surface there is: name, title, description, type and
    unit per field, already in a standard the modelling community publishes in.
    """
    try:
        body, size = _get(url)
    except Exception as exc:
        return ProbeResult(url, "datapackage", False, error=str(exc))
    document = json.loads(body)
    fields: list[ProbedField] = []
    for resource in document.get("resources", []):
        resource_name = resource.get("name", "")
        schema = resource.get("schema") or {}
        for spec in schema.get("fields", []):
            fields.append(
                ProbedField(
                    local_name=spec.get("name", ""),
                    label=spec.get("title"),
                    definition=spec.get("description"),
                    data_type=spec.get("type"),
                    unit_as_stated=spec.get("unit"),
                    read_from=f"resources[{resource_name}].schema.fields",
                )
            )
    return ProbeResult(url, "datapackage", True, fields, size)


def probe_stac_datacube(url: str) -> ProbeResult:
    """`cube:variables` on a STAC collection (datacube extension).

    Where a STAC catalog implements the extension this is dataset-level field
    metadata with units and dimensions, for free, from a source the harvester
    already crawls.
    """
    try:
        body, size = _get(url)
    except Exception as exc:
        return ProbeResult(url, "stac-datacube", False, error=str(exc))
    document = json.loads(body)
    variables = document.get("cube:variables") or document.get("properties", {}).get(
        "cube:variables", {}
    )
    fields = [
        ProbedField(
            local_name=name,
            label=spec.get("description"),
            data_type=spec.get("type"),
            unit_as_stated=spec.get("unit"),
            read_from="cube:variables",
        )
        for name, spec in (variables or {}).items()
    ]
    return ProbeResult(url, "stac-datacube", bool(variables), fields, size)


PROBES = {
    "zarr": probe_zarr,
    "csv": probe_csv_header,
    "datapackage": probe_datapackage,
    "stac": probe_stac_datacube,
}


def _numpy_dtype(raw: Any) -> str | None:
    """`<f4` to `float32`. The store's own word, in the schema's vocabulary."""
    if not raw:
        return None
    text = str(raw).lstrip("<>|=")
    kinds = {"f": "float", "i": "int", "u": "uint", "b": "bool", "S": "bytes", "U": "str"}
    kind = kinds.get(text[:1])
    if kind is None:
        return str(raw)
    if kind == "bool":
        return "bool"
    width = text[1:]
    return f"{kind}{int(width) * 8}" if width.isdigit() else kind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", required=True, help="JSON list of {surface, url, dataset}")
    parser.add_argument("--out", help="Write full results here as JSON")
    args = parser.parse_args(argv)

    targets = json.loads(Path(args.targets).read_text())
    results = []
    for target in targets:
        probe = PROBES[target["surface"]]
        result = probe(target["url"])
        results.append({"dataset": target.get("dataset"), **asdict(result)})
        status = "ok" if result.ok else f"FAILED ({result.error})"
        with_units = sum(1 for f in result.fields if f.unit_as_stated)
        with_labels = sum(1 for f in result.fields if f.label)
        print(
            f"{target.get('dataset', ''):34s} {result.surface:22s} "
            f"{result.n:4d} fields  {with_units:4d} with units  "
            f"{with_labels:4d} with labels  {result.bytes_read / 1024:8.1f} KB  {status}"
        )
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
