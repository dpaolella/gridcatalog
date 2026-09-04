"""Open Energy Platform — https://openenergyplatform.org/api/v0 (WP-3.3).

The seed file calls this the *"highest-value source for controlled-vocabulary
bootstrapping"*: OEP tables carry Open Energy Ontology annotations, so their
concept terms are a direct input to the SKOS crosswalk rather than something to
re-derive.

It is also the only source in the set with a real field-level schema. OEMetadata
``resources[0].schema.fields`` gives name, description, type and unit per field
— C1, C2, C3 and a unit *string* — which is why an OEP record can carry
``hasField`` and reach level 2 straight from the harvester.

**The units stay strings.** They land in ``og:unitAsStated`` and the semantic
layer resolves them to QUDT later (M7). Writing a guessed QUDT IRI here would be
a level 3 claim made by a normaliser, and level 3 means a machine can convert
the units without asking anyone.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from datahub.harvest.adapters.base import Adapter, HarvestedRecord, slugify
from datahub.logging import get_logger
from datahub.namespaces import DATASET_BASE

log = get_logger(__name__)


class OepAdapter(Adapter):
    name = "oep_api"

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        base = str(self.endpoint or "").rstrip("/")
        schema = self.config.get("schema", "model_draft")
        after = (checkpoint or {}).get("after")
        started = after is None
        emitted = 0

        names = self._table_names(self.get_json(f"{base}/schema/{schema}/tables/"))

        for name in names:
            if not name:
                continue
            if not started:
                started = name == after
                continue
            try:
                meta = self.get_json(f"{base}/schema/{schema}/tables/{name}/meta/")
            except Exception as exc:
                log.warning("OEP table metadata unavailable", table=name, error=str(exc))
                continue
            if not isinstance(meta, dict) or not meta:
                continue
            yield HarvestedRecord(
                source_id=f"{self.source_id}:{schema}.{name}",
                source=self.name,
                payload=self._prepare(meta, schema, name),
                source_url=f"https://openenergyplatform.org/dataedit/view/{schema}/{name}",
            )
            emitted += 1
            if limit is not None and emitted >= limit:
                return

    @staticmethod
    def _table_names(payload: Any) -> list[str]:
        """Table names, whichever of three shapes the API returned.

        OEP's table listing has been a dict keyed by name, a list of objects
        and a plain list of strings across API versions. Handling all three
        costs six lines; handling one and guessing wrong costs a source that
        silently harvests nothing — the adapter reports a clean run with zero
        records, which reads exactly like a source that has no data.
        """
        if isinstance(payload, dict):
            return sorted(payload)
        if not isinstance(payload, list):
            return []
        names: list[str] = []
        for item in payload:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
        return names

    def _prepare(self, meta: dict[str, Any], schema: str, table: str) -> dict[str, Any]:
        prepared = dict(meta)
        prepared.setdefault("name", table)
        prepared["id"] = f"https://openenergyplatform.org/dataedit/view/{schema}/{table}"

        licences = meta.get("licenses")
        if isinstance(licences, list) and licences:
            prepared["licenses"] = licences[0]

        resources = meta.get("resources")
        if isinstance(resources, list) and resources:
            prepared["resources"] = [
                {**r, "path": r.get("path") or prepared["id"]}
                for r in resources
                if isinstance(r, dict)
            ]
            fields = self._fields(resources[0], slugify(table))
            if fields:
                prepared["_fields"] = fields
        return prepared

    @staticmethod
    def _fields(resource: dict[str, Any], slug: str) -> list[dict[str, Any]]:
        """OEMetadata field descriptors as ``og:Field`` nodes.

        The one source that can populate C1–C3 at harvest time. C4 (the unit
        IRI) and C5 (the concept) are deliberately not attempted: the string
        goes in ``unitAsStated`` and the semantic layer resolves it, because a
        guessed QUDT IRI is a level 3 claim and level 3 means a machine can
        convert the units without asking anyone.
        """
        schema = resource.get("schema") or {}
        out: list[dict[str, Any]] = []
        for index, field in enumerate(schema.get("fields") or []):
            if not isinstance(field, dict) or not field.get("name"):
                continue
            node: dict[str, Any] = {
                "id": f"{DATASET_BASE}{slug}#field-{index}",
                "type": "Field",
                "fieldId": str(field["name"]),
                "localName": str(field["name"]),
            }
            if description := field.get("description"):
                node["definition"] = " ".join(str(description).split())
            if data_type := field.get("type"):
                node["dataType"] = str(data_type)
            if unit := field.get("unit"):
                node["unitAsStated"] = str(unit)
            out.append(node)
        return out


__all__ = ["OepAdapter"]
