"""STAC — Planetary Computer, Earth Search (WP-3.3).

PRD §7.3 says STAC sources "normalize almost losslessly", and the seed file
says to use STAC as the geospatial normalization target. A STAC *collection*
carries bbox, temporal extent and asset-level media types, which is D14, D15
and D11 with nothing left over.

**Collections, not items.** A STAC catalog holds millions of items — one per
scene, per tile, per day — and every one of them is a file, not a dataset. The
dataset is the collection. Harvesting items would produce a catalog where
"Sentinel-2" appears four million times, which is not a catalog.

**Anonymous access is not assumed.** Planetary Computer assets need a SAS token
and Earth Search assets do not, and both speak the same STAC. So the adapter
sets ``_anonymous`` only where the catalog documents its own answer, keyed on
the endpoint; where it does not, nothing is claimed and the record fails level
1 until the prober or a steward settles it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from datahub.harvest.adapters.base import Adapter, HarvestedRecord
from datahub.logging import get_logger
from datahub.namespaces import SCHEME_ACCESS_RESTRICTION

log = get_logger(__name__)

#: What each catalog documents about reading its assets. Keyed on host, because
#: it is a property of the deployment rather than of STAC.
ASSET_ACCESS: dict[str, tuple[bool, str]] = {
    "planetarycomputer.microsoft.com": (False, "accountRequired"),
    "earth-search.aws.element84.com": (True, "none"),
}


class StacAdapter(Adapter):
    name = "stac"

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        base = str(self.endpoint or "").rstrip("/")
        after = (checkpoint or {}).get("after")
        started = after is None
        emitted = 0

        payload = self.get_json(f"{base}/collections")
        collections = payload.get("collections") or []

        for collection in collections:
            identifier = str(collection.get("id") or "")
            if not identifier:
                continue
            if not started:
                started = identifier == after
                continue
            yield HarvestedRecord(
                source_id=f"{self.source_id}:{identifier}",
                source=self.name,
                payload=self._prepare(base, collection),
                source_url=self._self_link(collection),
            )
            emitted += 1
            if limit is not None and emitted >= limit:
                return

    def _prepare(self, base: str, collection: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(collection)
        prepared.update(self._bbox(collection))
        prepared["_assets"] = self._assets(collection)
        if geometry := self._geometry_types(collection):
            prepared["_geometry_types"] = geometry
        prepared["_crs"] = self._crs(collection)

        host = base.split("://")[-1].split("/")[0]
        if host in ASSET_ACCESS:
            anonymous, restriction = ASSET_ACCESS[host]
            prepared["_anonymous"] = anonymous
            prepared["_access_restriction"] = f"{SCHEME_ACCESS_RESTRICTION}/{restriction}"
        return prepared

    @staticmethod
    def _bbox(collection: dict[str, Any]) -> dict[str, Any]:
        """The four bbox scalars.

        STAC gives ``[[west, south, east, north], ...]`` where the first box is
        the overall extent. Split into four scalars rather than kept as a list
        because a skolemised ``rdf:List`` cannot be serialised back out of the
        store (ADR-0008) — the record would write and then fail to read.
        """
        extent = (collection.get("extent") or {}).get("spatial") or {}
        boxes = extent.get("bbox") or []
        if not (boxes and isinstance(boxes[0], list) and len(boxes[0]) >= 4):
            return {}
        west, south, east, north = (float(v) for v in boxes[0][:4])
        return {
            "_bbox_min_lon": west,
            "_bbox_min_lat": south,
            "_bbox_max_lon": east,
            "_bbox_max_lat": north,
        }

    @staticmethod
    def _assets(collection: dict[str, Any]) -> list[dict[str, Any]]:
        """Collection-level assets, plus the item-asset templates.

        ``item_assets`` describes what every item carries but has no href — it
        is a schema, not a file. Those are skipped: a Distribution with no
        access URL cannot answer "where do I get it", which is the one question
        a distribution exists to answer.
        """
        out: list[dict[str, Any]] = []
        for key, asset in (collection.get("assets") or {}).items():
            if not isinstance(asset, dict) or not asset.get("href"):
                continue
            out.append(
                {
                    "href": asset["href"],
                    "type": asset.get("type"),
                    "_format": asset.get("title") or key,
                    # Cloud-optimised formats support byte-range reads, which is
                    # what makes partial-read access plans possible (PRD §F7).
                    "_range": _supports_range(asset.get("type") or ""),
                    "_chunk_index": _range_index(asset.get("type") or ""),
                }
            )
        return out

    @staticmethod
    def _geometry_types(collection: dict[str, Any]) -> list[str]:
        """`raster`, where the assets say so. Nothing otherwise.

        A STAC collection is geospatial-primary by definition, and the shape
        requires a geometry type of one at level 1 — so with nothing derived
        here every STAC record was flagged, which is #31 item 2. Read from the
        media types of the assets the collection actually advertises, including
        `item_assets`: that one has no href and so yields no distribution, but
        it is still the collection stating what its items contain.
        """
        types: list[str] = []
        for source in (collection.get("assets") or {}, collection.get("item_assets") or {}):
            for asset in source.values():
                if not isinstance(asset, dict):
                    continue
                media = str(asset.get("type") or "").lower()
                if any(marker in media for marker in RASTER_MARKERS):
                    types.append("raster")
        return sorted(set(types))

    @staticmethod
    def _crs(collection: dict[str, Any]) -> str | None:
        for key in ("proj:epsg", "crs", "cube:dimensions"):
            value = collection.get(key)
            if isinstance(value, int):
                return f"EPSG:{value}"
            if isinstance(value, str):
                return value
        # STAC's own spec fixes collection-level bbox to WGS 84; saying so is a
        # fact about the format, not a guess about the data.
        return "EPSG:4326" if (collection.get("extent") or {}).get("spatial") else None

    @staticmethod
    def _self_link(collection: dict[str, Any]) -> str | None:
        for link in collection.get("links") or []:
            if isinstance(link, dict) and link.get("rel") == "self":
                return link.get("href")
        return None


#: Media type marker -> the index method that makes a byte range usable.
#:
#: Paired deliberately (#31). `og:supportsRangeRequests` used to be set from a
#: marker list alone, and `og:AccessPathShape` then refused the record: *"a
#: distribution advertising range requests must say how to find the bytes: set
#: og:chunkIndexMethod ... or set og:supportsRangeRequests false. Advertising a
#: capability the broker cannot use produces a partial-read plan that does not
#: work."* The shape is right, and the honest reading is that range support and
#: the index method are one fact, not two — so they are derived together and a
#: format whose index this cannot name does not advertise the capability.
#:
#: `zarr` is the instructive omission. The media type says "zarr" and not which
#: version, and v2's `.zarray` and v3's `zarr.json` are different files in
#: different places, so a guess produces a plan that fetches the wrong path.
#: The schema prober tries both because it can afford to be wrong once; a
#: published access plan cannot.
RANGE_INDEX: tuple[tuple[str, str], ...] = (
    ("cloud-optimized", "cog-tiles"),
    ("geotiff", "cog-tiles"),
    ("application/x-parquet", "parquet-rowgroup"),
    ("application/x-netcdf", "netcdf4-chunks"),
    ("application/x-hdf5", "hdf5-btree"),
)


def _range_index(media_type: str) -> str | None:
    """The index method for this media type, or None if the bytes are opaque."""
    lowered = (media_type or "").lower()
    for marker, method in RANGE_INDEX:
        if marker in lowered:
            return method
    return None


#: Media type markers that mean the asset is a raster grid.
#:
#: Only raster is derived. A GeoTIFF, a NetCDF or a GRIB file is a grid by
#: construction, which is a fact about the format. Vector is not the mirror
#: image: a GeoJSON or a GeoPackage may hold points, lines or polygons and the
#: media type does not say which, so nothing is claimed and the record stays at
#: level 1 until a prober or a steward settles it. `og:geometryTypes` takes
#: point, line, polygon or raster, and inventing one of the first three from a
#: container format would be exactly the guess PRD §14.2 forbids.
RASTER_MARKERS: tuple[str, ...] = (
    "geotiff",
    "image/tiff",
    "application/x-netcdf",
    "application/netcdf",
    "grib",
    "zarr",
    "application/x-hdf5",
    "cloud-optimized",
)


def _supports_range(media_type: str) -> bool:
    return _range_index(media_type) is not None


__all__ = ["ASSET_ACCESS", "StacAdapter"]
