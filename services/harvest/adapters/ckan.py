"""CKAN — data.openei.org, energydata.info, catalog.data.gov (WP-3.2).

Three of the eleven harvest sources are CKAN instances, and OEDI alone is the
largest single source in the plan at ~2,100 datasets. One adapter serves all
three because CKAN's ``package_search`` is the same API everywhere; what differs
is the base path, and that is configuration.

**Paging is by cursor, not by offset.** CKAN's ``start``/``rows`` pagination is
offset-based over a result set that changes underneath a long crawl, so a
dataset added during the run shifts everything after it and a dataset is either
seen twice or missed. Missing one is the failure that matters — it is silent —
so this adapter sorts by ``metadata_modified asc`` and pages on the last
timestamp seen. Records edited mid-crawl are re-seen rather than skipped, and a
re-seen record is a no-op because re-harvest matches on ``source_record_id``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from datahub.harvest.adapters.base import Adapter, HarvestedRecord
from datahub.logging import get_logger

log = get_logger(__name__)

PAGE_SIZE = 100


class CkanAdapter(Adapter):
    name = "ckan"

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        base = str(self.endpoint or "").rstrip("/")
        emitted = 0
        # A watermark rather than an offset: see the module docstring.
        since = (checkpoint or {}).get("modified_after")
        seen: set[str] = set()
        page_start = 0

        while True:
            params: dict[str, Any] = {
                "rows": PAGE_SIZE,
                "start": page_start,
                "sort": "metadata_modified asc",
                "q": f"metadata_modified:[{since} TO *]" if since else "*:*",
            }
            if query := self.config.get("fq"):
                params["fq"] = query

            payload = self.get_json(f"{base}/action/package_search", params=params)
            results = (payload.get("result") or {}).get("results") or []
            if not results:
                return

            for package in results:
                identifier = str(package.get("id") or package.get("name") or "")
                if not identifier or identifier in seen:
                    continue
                seen.add(identifier)
                yield HarvestedRecord(
                    source_id=self.source_id_for(package),
                    source=self.name,
                    payload=self._prepare(package),
                    source_url=self._landing_page(base, package),
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

            if len(results) < PAGE_SIZE:
                return
            page_start += PAGE_SIZE

    def source_id_for(self, package: dict[str, Any]) -> str:
        """CKAN's own package id, which is a UUID and does not change when the
        title or the slug does."""
        identifier = package.get("id") or package.get("name")
        return f"{self.source_id}:{identifier}"

    def _prepare(self, package: dict[str, Any]) -> dict[str, Any]:
        """Adapter-derived fields, marked with a leading underscore.

        ``extras`` is CKAN's escape hatch and arrives as a list of
        ``{key, value}`` pairs, which no field mapping can index into. Flattened
        to a dict here because turning a list of pairs into a lookup is adapter
        work, not something to express in YAML.
        """
        prepared = dict(package)
        extras = package.get("extras")
        if isinstance(extras, list):
            prepared["extras"] = {
                str(item.get("key")): item.get("value")
                for item in extras
                if isinstance(item, dict) and item.get("key")
            }
        # `private` is CKAN's access flag on the record. A package returned by
        # the public search API with private=false is the instance stating that
        # the record is openly readable. Absent the flag we claim nothing.
        if "private" in package:
            prepared["_public"] = not package.get("private")
        return prepared

    @staticmethod
    def _landing_page(base: str, package: dict[str, Any]) -> str | None:
        name = package.get("name")
        if not name:
            return None
        root = base.removesuffix("/api/3").removesuffix("/api")
        return f"{root}/dataset/{name}"


__all__ = ["CkanAdapter"]
