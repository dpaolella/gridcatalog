"""The adapter registry.

Eight adapters plus the curated seed reader (PRD §7.1). One place maps an
adapter name to its class, so ``data/seed-sources.yaml`` can name an adapter in
a string and the runner can build it — and so adding a ninth source means
adding a row to that file, not editing the runner.
"""

from __future__ import annotations

from typing import Any

from datahub.config import Settings
from datahub.harvest.adapters.base import (
    Adapter,
    HarvestedRecord,
    HarvestRunSummary,
    RateLimiter,
    slugify,
)
from datahub.harvest.adapters.cds import CdsAdapter
from datahub.harvest.adapters.ckan import CkanAdapter
from datahub.harvest.adapters.curated import CuratedAdapter
from datahub.harvest.adapters.datacite import DataCiteAdapter
from datahub.harvest.adapters.dcat_sparql import DcatSparqlAdapter
from datahub.harvest.adapters.oep import OepAdapter
from datahub.harvest.adapters.stac import StacAdapter
from datahub.harvest.adapters.yaml_repo import YamlRepoAdapter
from datahub.harvest.adapters.zenodo import ZenodoAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    "ckan": CkanAdapter,
    "zenodo_api": ZenodoAdapter,
    "datacite_api": DataCiteAdapter,
    "stac": StacAdapter,
    "yaml_repo": YamlRepoAdapter,
    "dcat_sparql": DcatSparqlAdapter,
    "oep_api": OepAdapter,
    "cds_catalogue": CdsAdapter,
    "curated": CuratedAdapter,
}


def build(source: dict[str, Any], settings: Settings | None = None, **kwargs: Any) -> Adapter:
    """Construct the adapter one ``harvest_sources`` entry names.

    Everything in the entry that is not a constructor argument is passed
    through as ``config``, so a source can carry adapter-specific settings —
    Zenodo's query list, the registry's path glob — without this function
    knowing what they mean.
    """
    name = source.get("adapter")
    if name not in ADAPTERS:
        raise KeyError(f"unknown adapter {name!r}; known: {', '.join(sorted(ADAPTERS))}")
    cls = ADAPTERS[name]
    reserved = {"id", "adapter", "endpoint", "name", "notes", "priority", "scale_estimate"}
    config = {k: v for k, v in source.items() if k not in reserved}

    if cls is CuratedAdapter:
        return cls(settings, **kwargs)
    return cls(
        source.get("id", name),
        settings,
        endpoint=source.get("endpoint"),
        config=config,
        **kwargs,
    )


__all__ = [
    "ADAPTERS",
    "Adapter",
    "CdsAdapter",
    "CkanAdapter",
    "CuratedAdapter",
    "DataCiteAdapter",
    "DcatSparqlAdapter",
    "HarvestRunSummary",
    "HarvestedRecord",
    "OepAdapter",
    "RateLimiter",
    "StacAdapter",
    "YamlRepoAdapter",
    "ZenodoAdapter",
    "build",
    "slugify",
]
