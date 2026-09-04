"""data.europa.eu — native DCAT-AP over SPARQL (WP-3.3).

PRD §7.3: *"Lowest normalization cost of any source here."* The query below is
why: it binds columns already named after our own terms, so the field mapping
for this source is close to an identity function.

**Language is selected in the query, not afterwards.** A DCAT-AP catalog carries
every description in up to 24 languages. Filtering after the fetch would mean
transferring all 24 and discarding 23, on a source with 800 datasets. The
``langMatches`` filter with an English fallback does it server-side.

**Distributions come back in a second query, keyed on the datasets just read.**
One query with an OPTIONAL join over distributions returns the cross product —
a dataset with six distributions appears six times, and its title and
description are transferred six times with it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from datahub.harvest.adapters.base import Adapter, HarvestedRecord
from datahub.logging import get_logger

log = get_logger(__name__)

PAGE_SIZE = 100

DATASETS_QUERY = """
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct:  <http://purl.org/dc/terms/>
SELECT ?dataset ?identifier ?title ?description ?issued ?modified ?license
       ?landingPage ?publisher ?accrualPeriodicity ?accessRights
       (GROUP_CONCAT(DISTINCT ?keyword; separator="|") AS ?keywords)
       (GROUP_CONCAT(DISTINCT ?spatialName; separator="|") AS ?spatialLabel)
WHERE {
  ?dataset a dcat:Dataset ;
           dct:title ?title .
  FILTER(langMatches(lang(?title), "en") || lang(?title) = "")
  OPTIONAL { ?dataset dct:identifier ?identifier }
  OPTIONAL {
    ?dataset dct:description ?description .
    FILTER(langMatches(lang(?description), "en") || lang(?description) = "")
  }
  OPTIONAL { ?dataset dct:issued ?issued }
  OPTIONAL { ?dataset dct:modified ?modified }
  OPTIONAL { ?dataset dct:license ?license }
  OPTIONAL { ?dataset dcat:landingPage ?landingPage }
  OPTIONAL { ?dataset dct:publisher ?publisher }
  OPTIONAL { ?dataset dct:accrualPeriodicity ?accrualPeriodicity }
  OPTIONAL { ?dataset dct:accessRights ?accessRights }
  OPTIONAL { ?dataset dcat:keyword ?keyword }
  OPTIONAL { ?dataset dct:spatial/<http://www.w3.org/2000/01/rdf-schema#label> ?spatialName }
}
GROUP BY ?dataset ?identifier ?title ?description ?issued ?modified ?license
         ?landingPage ?publisher ?accrualPeriodicity ?accessRights
ORDER BY ?dataset
LIMIT %(limit)d OFFSET %(offset)d
"""

DISTRIBUTIONS_QUERY = """
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct:  <http://purl.org/dc/terms/>
SELECT ?dataset ?accessURL ?mediaType ?format ?byteSize
WHERE {
  VALUES ?dataset { %(values)s }
  ?dataset dcat:distribution ?distribution .
  ?distribution dcat:accessURL ?accessURL .
  OPTIONAL { ?distribution dcat:mediaType ?mediaType }
  OPTIONAL { ?distribution dct:format ?format }
  OPTIONAL { ?distribution dcat:byteSize ?byteSize }
}
"""


class DcatSparqlAdapter(Adapter):
    name = "dcat_sparql"

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        offset = int((checkpoint or {}).get("offset", 0))
        emitted = 0

        while True:
            rows = self._select(DATASETS_QUERY % {"limit": PAGE_SIZE, "offset": offset})
            if not rows:
                return

            datasets = {str(row["dataset"]): row for row in rows if row.get("dataset")}
            for iri, distributions in self._distributions(list(datasets)).items():
                datasets[iri]["_distributions"] = distributions

            for iri, row in datasets.items():
                yield HarvestedRecord(
                    source_id=f"{self.source_id}:{row.get('identifier') or iri}",
                    source=self.name,
                    payload={**row, "keywords": _split(row.get("keywords"))},
                    source_url=row.get("landingPage") or iri,
                )
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

            if len(rows) < PAGE_SIZE:
                return
            offset += PAGE_SIZE

    def _distributions(self, iris: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not iris:
            return {}
        values = " ".join(f"<{iri}>" for iri in iris)
        rows = self._select(DISTRIBUTIONS_QUERY % {"values": values})
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            dataset = str(row.get("dataset") or "")
            if dataset:
                grouped.setdefault(dataset, []).append(
                    {k: v for k, v in row.items() if k != "dataset"}
                )
        return grouped

    def _select(self, query: str) -> list[dict[str, Any]]:
        """Run a SPARQL SELECT and flatten the results.

        SPARQL JSON results wrap every value in ``{"type": ..., "value": ...}``,
        which no field mapping can index into and which would put the wrapper
        into every record. Flattened here.
        """
        payload = self.get_json(
            str(self.endpoint or ""),
            params={"query": query, "format": "application/sparql-results+json"},
            headers={"Accept": "application/sparql-results+json"},
        )
        bindings = ((payload.get("results") or {}).get("bindings")) or []
        return [
            {key: cell.get("value") for key, cell in row.items() if isinstance(cell, dict)}
            for row in bindings
        ]


def _split(value: Any) -> list[str] | None:
    """Undo the GROUP_CONCAT."""
    if not value:
        return None
    return [part.strip() for part in str(value).split("|") if part.strip()] or None


__all__ = ["DATASETS_QUERY", "DISTRIBUTIONS_QUERY", "DcatSparqlAdapter"]
