"""Copernicus Climate Data Store (WP-3.3).

The seed file: *"ERA5 and friends. Account-gated, so most entries land Tier 2
on the access gate."*

The gate is the interesting part. Every CDS dataset needs a free account and a
per-dataset licence acceptance before a single byte can be downloaded, so
``anonymousAccess: false`` is a fact about the platform rather than something to
detect per record.

**Licence and access gate are kept apart.** A CDS dataset is openly licensed —
Copernicus Licence v1.2 permits commercial reuse with attribution — *and* it is
account-gated. Folding the gate into the licence would make an open dataset read
as restricted, and folding the licence into the gate would make a gated one read
as free to take. They are different questions with different answers, and PRD
§4.2 keeps them in different fields for exactly this reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from datahub.harvest.adapters.base import Adapter, HarvestedRecord
from datahub.logging import get_logger

log = get_logger(__name__)

#: Every CDS dataset inherits this. Per-dataset terms are layered on top and
#: accepted in the web UI; the identifier names the base licence, and the
#: acceptance requirement is recorded as an access restriction, not a licence.
BASE_LICENCE = "Copernicus Licence v1.2"


class CdsAdapter(Adapter):
    name = "cds_catalogue"

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        base = str(self.endpoint or "").rstrip("/")
        after = (checkpoint or {}).get("after")
        started = after is None
        emitted = 0

        payload = self.get_json(f"{base}/catalogue/v1/collections", params={"limit": 500})
        collections = payload.get("collections") or payload.get("datasets") or []

        for collection in collections:
            if not isinstance(collection, dict):
                continue
            identifier = str(collection.get("id") or collection.get("name") or "")
            if not identifier:
                continue
            if not started:
                started = identifier == after
                continue
            yield HarvestedRecord(
                source_id=f"{self.source_id}:{identifier}",
                source=self.name,
                payload=self._prepare(collection, identifier),
                source_url=f"https://cds.climate.copernicus.eu/datasets/{identifier}",
            )
            emitted += 1
            if limit is not None and emitted >= limit:
                return

    def _prepare(self, collection: dict[str, Any], identifier: str) -> dict[str, Any]:
        prepared = dict(collection)
        prepared["_license"] = BASE_LICENCE
        prepared["_landing_page"] = f"https://cds.climate.copernicus.eu/datasets/{identifier}"
        prepared["_endpoints"] = [
            {
                "url": f"https://cds.climate.copernicus.eu/api/retrieve/v1/processes/{identifier}",
                "format": "GRIB or NetCDF, selected per request",
                # The CDS request API is a subsetting protocol in the sense
                # PRD §F7 means: the caller states a slice and the service
                # produces it, rather than serving a whole file.
                "protocol": "cds-request-api",
            }
        ]
        if resolution := _time_resolution(collection):
            prepared["_time_resolution"] = resolution
        return prepared


def _time_resolution(collection: dict[str, Any]) -> str | None:
    """Read the temporal resolution the catalogue states, if it states one."""
    for key in ("temporal_resolution", "time_resolution"):
        if value := collection.get(key):
            return str(value)
    keywords = collection.get("keywords") or []
    for keyword in keywords if isinstance(keywords, list) else []:
        text = str(keyword)
        if text.lower().startswith("temporal coverage:"):
            return text.split(":", 1)[1].strip()
    return None


__all__ = ["BASE_LICENCE", "CdsAdapter"]
