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
                }
            )
        return out

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


def _supports_range(media_type: str) -> bool:
    return any(
        marker in media_type
        for marker in ("cloud-optimized", "application/x-parquet", "zarr", "geotiff")
    )


__all__ = ["ASSET_ACCESS", "StacAdapter"]
