# Metadata schema

The contract every component builds on. Records are JSON-LD over DCAT 3, Dublin
Core Terms, PROV-O and schema.org, extended with an `og:` namespace. Controlled
vocabularies are SKOS. Units are IRIs.

Requirement ids (D1–D21, C1–C16) match PRD §4 so the two documents stay
traceable. `tests/test_jsonld_context.py` drives off the same table.

| Artifact | Where |
|---|---|
| Context | [`schemas/opengrid-datahub.jsonld`](../schemas/opengrid-datahub.jsonld) |
| Shapes | [`shapes/opengrid-datahub.ttl`](../shapes/opengrid-datahub.ttl) |
| Vocabularies | [`vocab/`](../vocab/) |
| Worked records | [`tests/fixtures/records/`](../tests/fixtures/records/) |

## Completeness levels

| Level | Name | Has | Enables |
|---|---|---|---|
| 1 | Discoverable | Dataset-level metadata, ≥1 distribution, licence, coverage | Search, filter, access plan, download |
| 2 | Interpretable | Field names, definitions, types, value basis | Schema tab, Provenance and Documentation grading |
| 3 | Linked | Concept IRIs, unit IRIs, field sources | Semantic resolution, inter-dataset links |

Level is a **promotion gate, not a publication gate** (ADR-0004). A record
publishes at 1. A level-3 constraint does not block it.

## Dataset level

`Req` is the PRD requirement id. `Lvl` is the level the field is required at.
`Enrich` says whether the LLM enricher may draft it (ADR-0005) — the closed
allow-list, enforced by filtering the model's output, not by prompting.

| Req | Term | Type | Lvl | Enrich | Notes |
|---|---|---|---|---|---|
| D1 | `id` | IRI | 1 | no | The record's own IRI, minted under `https://catalog.opengrid.org/ds/` |
| D1 | `persistentId` | IRI | 1 | no | DOI, handle or stable landing URL |
| D1 | `conceptDoi` / `versionDoi` | IRI | 2 | no | Zenodo's concept-vs-version distinction. Conflating them is the trap PRD §D1 names |
| D2 | `title` | langString | 1 | no | Exactly one |
| D2 | `description` | langString | 1 | **yes** | Full text |
| D2 | `summary` | langString | 1 | **yes** | One line, rendered in the list view |
| D3 | `dataDomain` | concept[] | 1 | **yes** | DD1–DD10. Controlled, not free text |
| D4 | `upstreamSource`, `wasDerivedFrom` | IRI[] | 2 | no | **Absent means "not captured", never "no source"** |
| D5 | *(traversal)* | — | 2 | — | Multi-hop is a property of the graph, not a field |
| D6 | `provenanceClass` | concept | 1 | **yes** | Caps the Provenance grade via `og:baseProvenanceGrade` |
| D7 | `supersedes`, `supersededBy`, `complements` | IRI[] | 2 | no | A dataset may not supersede itself |
| D8 | `license` | IRI | 1 | **no** | SPDX where one fits. Must be **absolute** — see below |
| D9 | `accessRestriction` | concept | 1 | no | Six values; caps tier via `og:tierCeiling` |
| D10 | `anonymousAccess` | boolean | 1 | no | A Tier 1 criterion, and what an unauthenticated evaluator filters on |
| D11 | `distribution` | IRI[] | 1 | no | ≥1. Even a Tier 3 pointer has one: the landing page |
| D12 | `supportedAnalysis`, `excludedAnalysis` | concept[] | 2 | **yes** | Exclusions are the half users cannot get elsewhere |
| D13 | `qualityFlags` | object | 1 | **yes** | Staleness, caveats, planned successor |
| D14 | `bbox`, `nativeCRS`, `geometryTypes`, `featureCount` | | 1 | **yes** | Mandatory at level 1 when `geospatialPrimary` |
| D15 | `temporal`, `updateCadence`, `timeResolution` | | 1 | **yes** | Cadence is an ISO 8601 duration |
| D16 | `voltageClass`, `hasTopology`, `hasImpedance`, `spatialGranularity` | | 2 | **yes** | Granularity decides whether power flow is possible at all |
| D17 | `fieldSchema`, `conformsTo` | IRI | 2 | no | |
| D18 | `documentationStatus` | enum | 1 | **yes** | fully-documented / partial / external-standard-only / none |
| D19 | `hasFileGroup`, `hasVariable`, `hasDimension`, `variableShape` | | 3 | no | Hierarchical, not a flattened column list |
| D20 | `hasNodeType`, `hasEdgeType` | | deferred | no | Terms exist; no shapes, no UI |
| D21 | `hasLayer`, `layerGeometryType` | | deferred | no | As above |

Added by this build:

| Term | Type | Purpose |
|---|---|---|
| `completenessLevel` | 1 \| 2 \| 3 | Makes harvested-vs-curated quality visible (PRD §6) |
| `harvestSource` | string | Which harvester produced this, or `curated`. Without it a per-source recall audit is impossible |
| `reviewState` | enum | `draft`, `in-review`, `confirmed`, `flagged`. Decides which named graph the record lives in |
| `lastComputedAt` | signal → timestamp | Makes the semantic layer's freshness lag visible rather than hidden |
| `enrichmentBasis` | enum | X3. With model id and prompt version, or validation fails |
| `conceptGap` | object | X4. An explicit gap with a stated reason, never a silent omission |

## Distribution

Each access path is its own object. This matters more than it looks: the same
dataset commonly has an anonymous S3 copy and an account-gated API, and the
barrier classification differs between them.

```jsonc
{
  "type": "Distribution",
  "id": "https://catalog.opengrid.org/dist/ecmwf-era5--zarr-s3",
  "accessURL": "s3://era5-pds/zarr/",
  "mediaType": "application/vnd+zarr",
  "byteSize": 4398046511104,
  "bulkDownload": true,
  "accessRestriction": ".../access-restriction/none",   // overrides D9
  "anonymousAccess": true,
  "supportsRangeRequests": true,
  "corsEnabled": true,
  "chunkIndexMethod": "zarr-v2",        // required when range requests are true
  "subsettingProtocol": null,           // opendap | thredds | wcs | wfs | …
  "credentialRequirement": null,
  "linkHealth": {
    "type": "LinkHealth",
    "linkHealthStatus": "verified",     // verified | degraded | unreachable | redirected
    "lastProbedAt": "2026-09-01T04:00:00Z",
    "consecutiveFailures": 0,
    "probeCadence": "P7D"
  }
}
```

`chunkIndexMethod` and `subsettingProtocol` are what make path selection
metadata-driven rather than per-dataset code (PRD §F8). A distribution that
advertises range requests without a chunk index fails validation, because the
broker would issue a plan it cannot fulfil.

## Field level

| Req | Term | Lvl | Enrich | Notes |
|---|---|---|---|---|
| C1 | `localName`, `fieldId` | 2 | no | The name the field actually has in the data |
| C2 | `label`, `definition` | 2 | **yes** | Capturable even when the source documents it only externally — cite the document |
| C3 | `dataType`, `fieldGeometryType`, `fieldCRS`, `dimensionality` | 2 | **yes** | |
| C4 | `concept` **or** `conceptGap` | 3 | **candidate only** | What makes two differently-named columns resolvable |
| C4 | `candidateConcept` | 3 | no | On a `conceptGap`: the concepts that fit equally well. Naming them tells a steward what to decide |
| C5 | `unit` | 3 | **candidate only** | A unit IRI, never a string. `"MW"` fails |
| C6 | `valueBasis` | 2 | **yes** | measured / estimated / modeled / synthetic |
| C7 | `fieldSource` | 3 | no | May point at uncatalogued sources |
| C8 | `derivedFromField` | 3 | no | *Which* source field, not the transformation |
| C9 | `codeList` | 3 | **yes** | Each categorical **value** resolves to its own concept |
| C10 | `required`, `completenessCaveats` | 2 | **yes** | |
| C11–C16 | ranges, join candidates, geo-join keys, … | deferred | no | Terms exist; no shapes |

"Candidate only" means the enricher may propose a mapping, which is then stored
with `og:inferredAssignment true` and a stated `og:inferenceBasis`. It never
overwrites a steward-confirmed one.

## Worked example: a level-3 field

From `tests/fixtures/records/ecmwf-era5.jsonld`. Note what the record admits
rather than what it claims — the caveat is the most useful line in it.

```jsonc
{
  "id": "https://catalog.opengrid.org/field/ecmwf-era5/ssrd",
  "type": "Field",
  "localName": "ssrd",
  "label": "Surface solar radiation downwards",
  "definition": "Shortwave radiation reaching a horizontal surface, accumulated over the hour. Equivalent to global horizontal irradiance once divided by the accumulation period, which is the conversion most consumers forget.",
  "dataType": "float32",
  "unit": "http://qudt.org/vocab/unit/W-PER-M2",
  "unitAsStated": "J m**-2 accumulated over 1 hour",
  "concept": "https://schema.opengrid.org/concept/grid-concept/globalHorizontalIrradiance",
  "valueBasis": "modeled",
  "fieldSource": ["https://catalog.opengrid.org/ds/ecmwf-era5"],
  "required": true,
  "completenessCaveats": "Accumulated, not instantaneous. A consumer treating the raw value as W/m2 overstates irradiance by a factor of 3600."
}
```

## Worked example: an honest gap

From `tests/fixtures/records/global-transmission-database.jsonld`. Rule X4: a
field with no confident mapping carries an explicit marker with a reason, and is
never silently omitted.

```jsonc
{
  "id": "https://catalog.opengrid.org/field/global-transmission-database/confidence_class",
  "type": "Field",
  "localName": "confidence_class",
  "label": "Compiler confidence class",
  "definition": "The compilers' own three-level assessment of how well-attested a row is.",
  "dataType": "string",
  "conceptGap": {
    "type": "ConceptGap",
    "gapReason": "No concept in the grid-concept scheme covers a compiler's self-assessed confidence. It is metadata about the row rather than a physical, economic or categorical quantity, and inventing a concept for one dataset's internal grading would pollute a shared vocabulary."
  },
  "valueBasis": "estimated"
}
```

## Validating a record

```python
from datahub.harvest.validate import ValidationRunner, format_report

runner = ValidationRunner()
report = runner.validate_jsonld(record, target_level=3)
if not report.conforms:
    print(format_report(report))     # names the node, the path, the value, the remedy
print(runner.highest_passing_level(graph))
```

## Two edges worth knowing

**A free-text licence does not fail to parse.** `dct:license` is `@type: @id`,
so `"CC BY 4.0"` becomes a relative IRI resolved against the document base —
`file:///…/` in a local run. The shape therefore requires an absolute IRI. The
invalid fixture `license-as-free-text` keeps the guard in place.

**A missing field and an empty field are different claims.** Absent means "not
captured". If a dataset genuinely has no upstream because it is a primary
observation, say so with `upstreamSourceUncaptured: false` rather than with an
empty list.
