"""Zenodo — https://zenodo.org/api/records (WP-3.2).

PRD §7.3: *Zenodo carries excellent identity and versioning but weak coverage
facets.* The seed file adds that this is "where the modeling community actually
publishes" — PyPSA, PLEXOS-World and most of the open power-system datasets that
are not on a government portal.

**Only the latest version of each deposit is emitted.** Zenodo's search returns
every version as a separate record, and a deposit with eleven releases would
otherwise become eleven catalog records that are all the same dataset. The
concept record id groups them; ``links.latest`` says which is current. Older
versions are not lost — the version chain is recoverable from the concept DOI —
they are simply not separate datasets, which is the D1 distinction the schema
draws.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from datahub.harvest.adapters.base import Adapter, HarvestedRecord
from datahub.logging import get_logger

log = get_logger(__name__)

PAGE_SIZE = 100
#: Zenodo caps deep pagination; past this the API returns an error rather than
#: more results, so a query that would exceed it needs narrowing instead.
MAX_PAGE = 100


class ZenodoAdapter(Adapter):
    name = "zenodo_api"

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        base = str(self.endpoint or "https://zenodo.org/api/records").rstrip("/")
        queries: list[str] = list(self.config.get("queries") or ["q=power+system+data"])
        resume = (checkpoint or {}).get("query_index", 0)
        emitted = 0
        seen_concepts: set[str] = set()

        for index, query in enumerate(queries):
            if index < resume:
                continue
            for record in self._iter_query(base, query, limit, emitted, seen_concepts):
                yield record
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

    def _iter_query(
        self,
        base: str,
        query: str,
        limit: int | None,
        already: int,
        seen_concepts: set[str],
    ) -> Iterator[HarvestedRecord]:
        page = 1
        while page <= MAX_PAGE:
            params = {**_parse_query(query), "size": PAGE_SIZE, "page": page}
            payload = self.get_json(base, params=params)
            hits = ((payload.get("hits") or {}).get("hits")) or []
            if not hits:
                return

            for hit in hits:
                concept = str(hit.get("conceptrecid") or hit.get("id") or "")
                if not concept or concept in seen_concepts:
                    continue
                if not self._is_latest(hit):
                    # Not a loss: the version chain is recoverable from the
                    # concept DOI. It is simply not a separate dataset.
                    continue
                seen_concepts.add(concept)
                yield HarvestedRecord(
                    source_id=f"{self.source_id}:{concept}",
                    source=self.name,
                    payload=hit,
                    source_url=(hit.get("links") or {}).get("self_html"),
                )
                already += 1
                if limit is not None and already >= limit:
                    return

            if len(hits) < PAGE_SIZE:
                return
            page += 1

    @staticmethod
    def _is_latest(hit: dict[str, Any]) -> bool:
        """Whether this is the current release of its deposit.

        Zenodo says so three different ways depending on the API version, and a
        record that says nothing is treated as current — dropping a record
        because its metadata shape changed would be a silent recall failure.
        """
        relations = (hit.get("metadata") or {}).get("relations") or hit.get("relations") or {}
        versions = relations.get("version") or []
        if isinstance(versions, list) and versions:
            first = versions[0]
            if isinstance(first, dict) and "is_last" in first:
                return bool(first["is_last"])
        links = hit.get("links") or {}
        if links.get("latest") and links.get("self"):
            return str(links["latest"]).rstrip("/") == str(links["self"]).rstrip("/")
        return True


def _parse_query(query: str) -> dict[str, str]:
    """``communities=pypsa`` or ``q=power+system&type=dataset`` into params.

    The seed file writes queries as URL fragments because that is how they are
    documented; splitting them here keeps the file readable rather than making
    a steward write JSON.
    """
    params: dict[str, str] = {}
    for part in query.split("&"):
        if "=" in part:
            key, _, value = part.partition("=")
            params[key.strip()] = value.strip().replace("+", " ")
    return params


__all__ = ["ZenodoAdapter"]
