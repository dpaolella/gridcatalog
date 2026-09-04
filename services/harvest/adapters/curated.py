"""The ``curated`` adapter: ``data/seed-sources.yaml``.

The one adapter with no network. It reads the hand-curated seed inventory —
11 harvest sources and 114 anchor datasets across DD1–DD10 — and emits them in
the same shape every other adapter emits, so the rest of the pipeline does not
have to know which records were hand-authored.

The seed file's own header is the specification for the part that matters:

> Provenance of the DD2/DD3/DD4/DD6/DD7/DD10 entries: assembled for this PRD.
> They have NOT been through the same license and access-path review. Every one
> carries `verified: false` and MUST go through the review queue before it is
> published to the catalog. **Do not treat the license or tier fields on
> unverified rows as authoritative.**

That last sentence is load-bearing and is enforced downstream in
:mod:`datahub.harvest.seed`: an unverified row cannot reach the catalog graph.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from datahub.config import Settings
from datahub.harvest.adapters.base import Adapter, HarvestedRecord, slugify


class CuratedAdapter(Adapter):
    """Reads the curated seed inventory. No network, no rate limit."""

    name = "curated"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        path: Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__("curated", settings, rate_per_second=0, **kwargs)
        self.path = path or self.settings.seed_sources_path

    def load(self) -> dict[str, Any]:
        return yaml.safe_load(self.path.read_text())

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        document = self.load()
        emitted = 0
        seen_after = (checkpoint or {}).get("after")
        started = seen_after is None

        for domain, block in document.get("seed_datasets", {}).items():
            for entry in block.get("datasets", []):
                source_id = self.source_id_for(domain, entry["name"])
                if not started:
                    started = source_id == seen_after
                    continue
                yield HarvestedRecord(
                    source_id=source_id,
                    source=self.name,
                    payload={
                        # The domain and its structural note travel with the
                        # entry so the normaliser does not have to re-open the
                        # file to know which domain a row came from.
                        "data_domain": domain,
                        "domain_name": block.get("domain_name"),
                        **entry,
                    },
                    source_url=entry.get("access"),
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

    @staticmethod
    def source_id_for(domain: str, name: str) -> str:
        """``curated:DD5:ecmwf-era5`` — stable across runs and across edits
        that do not change the name, which is what makes re-harvest an update
        rather than a duplicate."""
        return f"curated:{domain}:{slugify(name)}"

    # ---- the harvest source registry ------------------------------------

    def harvest_sources(self) -> list[dict[str, Any]]:
        """The 11 machine-readable catalogs the other adapters crawl.

        Lives here because it lives in the same file; the harvest runner reads
        it to know what to run.
        """
        return list(self.load().get("harvest_sources", []))

    def domains(self) -> dict[str, dict[str, Any]]:
        """Domain metadata, including each structural note.

        The note is loaded into the vocabulary rather than onto records
        (``vocab/og-data-domain.ttl``); this accessor exists so the loader can
        assert the two have not drifted.
        """
        return {
            domain: {
                "name": block.get("domain_name"),
                "structural_note": block.get("structural_note"),
                "dataset_count": len(block.get("datasets", [])),
            }
            for domain, block in self.load().get("seed_datasets", {}).items()
        }
