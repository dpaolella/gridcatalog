# OpenGrid Data Hub 1.0

Build specification. This document is the working contract for implementing Data Hub 1.0. It is written to be handed to an agentic coding tool and executed against directly.

Source of truth for product intent: the OpenGrid Data Hub epic and its six V1 feature specs in Notion, plus the three dependent V1 features from Open Standards & Schema and the four from API & Integrations. This document consolidates those into one buildable spec and makes the open engineering decisions those specs deliberately left open.

---

## 0. Read this first: what changed from the Notion PRD

Three deliberate departures. Each is a judgment call, not an oversight.

**1. Catalog coverage expands from four data domains to all ten. Ingestion does not.**

The Notion spec scopes V1 ingestion to DD1, DD5, DD8 and DD9 because ETL labor, not storage, is the binding constraint. That reasoning is correct and stands. But it conflates two different jobs: *cataloging* a dataset (authoring a metadata record that points at it) and *ingesting* a dataset (doing ETL so it arrives model-ready).

This build separates them:

- **Cataloging covers DD1 through DD10.** Achieved by harvesting existing machine-readable catalogs rather than hand-authoring records. See section 7.
- **Ingestion and Tier 2 ETL stay scoped to DD1, DD5, DD8, DD9.** Unchanged.

The reason this works is that roughly 5,000 to 6,000 grid-relevant datasets already carry structured metadata in DCAT, STAC, Zenodo, or AWS Open Data YAML. Harvesting them is a pipeline problem. Hand-authoring them is a headcount problem. The Notion cost model priced the second.

The cost of this change is honest and should be stated in the product: harvested records start at lower metadata quality than hand-authored ones. Section 6 defines a **completeness level** on every record so that difference is visible rather than hidden.

**2. Field-level metadata is not required for a record to be published.**

The Notion spec treats full field-level metadata as non-optional for every ingested dataset. At ten-domain scale that is the thing that will not happen. This build makes field-level metadata a promotion gate rather than a publication gate. A record can be published at completeness level 1 (discoverable) with only dataset-level metadata, and gets promoted to level 2 (interpretable) and level 3 (linked) as field-level detail is added. Search, filtering and access-plan issuance work at level 1. The semantic layer and inter-dataset links require level 3.

**3. The system of record is a triple store, decided deliberately.**

The Notion schema spec offers "a triple store or JSON-LD-aware document store with materialized views." This build takes the first option, on **Apache Jena Fuseki with TDB2**, and commits to it as the system of record rather than treating it as a later migration.

The reasoning is strategic rather than performance-driven. Three capabilities are native to RDF and would otherwise have to be hand-built and hand-maintained:

- **Unbounded-depth provenance traversal** across mixed edge types (`wasDerivedFrom`, `supersededBy`, `sameConceptAs` in one walk). This is what makes shared-origin correlation detection real rather than a two-hop approximation.
- **Inference over the concept scheme.** A query for "renewable resource" fields returns solar irradiance, wind speed and hydro inflow because the SKOS hierarchy says so, with nobody maintaining an expansion list. Add a concept next year and every existing query picks it up.
- **Federation.** Querying across OpenGrid's catalog and an external endpoint such as the Open Energy Platform, joined on shared concept IRIs, with no replication pipeline. This is the decisive one. If GERS heads toward federated national nodes resolving through shared identifiers, federation is the native mode of the substrate rather than something bolted on.

Section 3.2 records what this commits the project to operationally, and section 4.6 gives worked examples of queries that justify it.

Search is not served from SPARQL. See section 3.1.

Everything else follows the Notion specs.

---

## 1. What Data Hub 1.0 is

A discovery and routing layer for grid-modeling data. It holds metadata about datasets and issues access plans that point at where the bytes actually live. It never stores or proxies dataset bytes.

Four things a user can do:

1. **Find** a dataset by domain, region, time period, license, access barrier, or free text, across all ten data domains.
2. **Understand** it: what each field means, what unit it is in, whether values are measured or modeled, how far to trust it, and what it is and is not fit for.
3. **See how it connects** to other datasets in the catalog, including a warning when two apparently independent datasets share an upstream origin.
4. **Get it**, via a redirect for whole files or a byte-range plan for slices, through the UI, the REST API, a Python SDK, or an MCP server.

The single hard architectural constraint: **the Hub is never in the byte path.** Every design decision downstream follows from this. It is what makes the Hub's cost independent of whether a dataset is 2 KB or 4 TB.

---

## 2. Users

| Persona | What they do here | What they never do |
|---|---|---|
| Primary modeler (human) | Search, inspect, download, assemble study inputs | |
| Programmatic modeler | Same via SDK; scripts pipelines | Uses the UI |
| AI agent (on a user's behalf) | Search, inspect, bounded preview, author a workflow spec | Executes anything |
| External evaluator (regulator, intervenor, advocate) | Reads provenance and quality grades to validate someone else's study | Downloads bulk data; needs no entitlement |
| Data provider | Submits a dataset via intake form; manages an allow-list for restricted entries | Surrenders custody of bytes |
| Data steward (OpenGrid) | Reviews and confirms LLM-drafted records, assigns quality facets | |

The external evaluator is the persona most likely to be dropped during implementation and is the one that most differentiates this product. Read-only quality and provenance inspection must work with no authentication and no entitlement.

---

## 3. Architecture

```
                    ┌──────────────────────────────────┐
                    │  Semantic layer                  │
                    │  concept resolution · linkage ·  │
                    │  currency grading                │
                    └────────────────▲─────────────────┘
                                     │ enrich / write back
                                     │ (bidirectional)
   humans ──────────► ┌──────────────────────────────────────────┐ ◄────── agents (MCP)
   (Next.js UI)       │  Catalog & metadata                      │         tools (SDK)
                      │  search · schema · provenance · license  │
   evaluators ──────► │                                          │
   (no auth)          └────────────────────┬─────────────────────┘
                                           │ capabilities + access tier
                                           ▼
   humans ──────────► ┌──────────────────────────────────────────┐ ◄────── agents / tools
   "download this"    │  Access broker                           │         "give me this slice"
                      │  access plans · auth · allow-lists       │
                      └────────────────────┬─────────────────────┘
                                           │ issues ACCESS PLAN (small JSON or 302)
                                           ▼
                      ┌──────────────────────────────────────────┐
                      │  external storage                        │
                      │  partner-hosted · public · OpenGrid S3   │
                      └──────────────────────────────────────────┘
                                           │
                      bytes flow directly storage ──► client
                      (this hop deliberately bypasses the Hub)
```

Feeding the catalog from the left is the acquisition pipeline, which is not in the Notion architecture diagram because it was assumed to be manual work:

```
harvest ──► normalize ──► LLM enrich ──► SHACL validate ──► review queue ──► publish
 (CKAN,      (to JSON-LD    (fill gaps,     (block bad      (human           (level 1/2/3)
  STAC,       DCAT-3)        draft field     records)        confirm)
  Zenodo,                    metadata)
  YAML,
  curated)
```

### 3.1 Storage: write to the graph, read from the index

The single most important structural decision. **The triple store is the system of record. It is not the search backend.**

```
                    writes                          reads
                      │                               │
                      ▼                               │
        ┌───────────────────────────┐                 │
        │  Jena Fuseki / TDB2       │  ── projector ──┼──► OpenSearch
        │  system of record         │   (on commit)   │    denormalized
        │  · all records as RDF     │                 │    search documents
        │  · SKOS concept schemes   │                 │         │
        │  · provenance graph       │                 │         ▼
        │  · SHACL shapes           │                 │   list view, facets,
        └───────────────┬───────────┘                 │   search-while-typing
                        │                             │
                        └── SPARQL ───────────────────┴──► detail view,
                            (graph queries only)           provenance chains,
                                                           links, concepts
```

Every read path goes to the store that is good at it. Search-while-typing with faceted filters over the full catalog is an OpenSearch query and never touches SPARQL. Provenance traversal, concept inference and link computation are SPARQL and never touch OpenSearch.

The projector runs on commit: a confirmed record change triggers a SPARQL CONSTRUCT that flattens the record into a denormalized search document and indexes it. Reindex-from-scratch must be a single command and must be routinely exercised, because the index is derived state and treating it as precious is how it drifts.

This removes the main performance objection to an all-RDF design. The interaction users hit on every visit is served by a search engine built for it. It does mean two stores to keep consistent, which is real cost, and the projector is the piece most likely to be the source of a "why is search stale" bug. Instrument it: expose projector lag as a metric and surface it in the admin UI.

### 3.2 What this decision commits the project to

Recorded here so it is a standing obligation rather than a discovery.

- **At least one person on staff or under contract who is fluent in SPARQL, SHACL and RDF modeling, continuously.** Not for the build, for the life of the project. This is the trade being made in exchange for the capabilities in section 4.6.
- **Fuseki operational competence:** TDB2 backup and restore, compaction, and upgrade path. TDB2 requires periodic compaction; an uncompacted store degrades quietly rather than failing loudly.
- **Concept scheme governance.** Inference means the SKOS hierarchy has query-visible consequences. A careless `skos:broader` edit changes what every existing query returns. Vocabulary changes go through the same review as code, with the conformance suite as the regression gate.
- **Contributor onboarding cost.** A modeler who wants to contribute will know Python and probably not know SPARQL. Mitigate by keeping the SDK, the REST API and the harvest adapters entirely SPARQL-free, so the graph is an implementation detail for everyone except the semantic layer. Only `services/semantic/` and `services/linksvc/` should contain SPARQL.

If the staffing commitment lapses, the fallback is not a rewrite. Records are JSON-LD, so they export losslessly to a document store; what would be lost is inference and federation. Document that as the exit path rather than pretending there isn't one.

### 3.3 Stack

| Layer | Choice | Why |
|---|---|---|
| System of record | Apache Jena Fuseki 5.x, TDB2 | ASF governance, mature, Apache-2.0, largest hiring pool of the open options, built-in RDFS and OWL reasoning |
| Search index | OpenSearch 2.x | Derived from the graph via the projector. Serves list view, facets, search-while-typing |
| Operational store | PostgreSQL 16 | Not catalog records. Users, sessions, allow-lists, harvest run state, review queue, reports, submissions, probe history |
| Backend | Python 3.12, FastAPI | Ecosystem alignment with the modeling community |
| RDF in Python | `rdflib` + `SPARQLWrapper` | Record construction, query execution against Fuseki |
| Validation | `pySHACL` on commit; Fuseki SHACL endpoint for batch | Enforces the metadata contract |
| Reasoning | Jena RDFS/OWL reasoner over the concept schemes | Materialized on vocabulary change, not per query |
| Object store | S3, AWS Open Data Sponsorship where eligible | Only for datasets OpenGrid hosts by exception |
| Queue | Celery + Redis | Harvest, enrich, projector, link-health probes, recompute batches |
| Frontend | Next.js 15 (App Router), TypeScript, Tailwind | |
| Map | MapLibre GL | Coverage AOI rendering; no license cost |
| Graph viz | Cytoscape.js | One-hop link graph |
| Auth | Authlib OIDC; GitHub, Google, Microsoft | Federated SSO per spec |
| SDK | Python first (`opengrid-datahub`) | Julia deferred. No SPARQL exposed. |
| MCP | `fastmcp`, thin client over REST | Never talks to any store directly |

Postgres does not disappear. It holds everything that is operational rather than semantic, which is a clean line: if it would be meaningless to publish as RDF, it belongs in Postgres.

**Named graphs in Fuseki:**

| Graph | Contents |
|---|---|
| `og:graph/catalog` | Published, steward-confirmed dataset records |
| `og:graph/draft` | Harvested and enriched records pending review |
| `og:graph/vocab` | SKOS concept schemes, versioned |
| `og:graph/inferred` | Materialized entailments, regenerated on vocab change |
| `og:graph/computed` | Semantic layer output: links, grades, resolutions |

Keeping computed output in its own graph means a full recompute is a graph drop and rebuild, not a surgical update. Keeping draft separate from catalog means entitlement and visibility rules only ever need to reason over `og:graph/catalog`.

---

## 4. The metadata schema

This is the contract every other component builds on. Records are JSON-LD over DCAT 3, Dublin Core Terms, PROV-O and schema.org, extended with an `og:` namespace. Controlled vocabularies are SKOS concept schemes. Units are QUDT.

Namespaces:

```
dcat:  http://www.w3.org/ns/dcat#
dct:   http://purl.org/dc/terms/
prov:  http://www.w3.org/ns/prov#
skos:  http://www.w3.org/2004/02/skos/core#
qudt:  http://qudt.org/schema/qudt/
og:    https://schema.opengrid.org/ns#
```

### 4.1 Dataset-level

Requirement IDs match the Notion spec (D1 to D21) so the two documents stay traceable.

| ID | Field | Type | Required at level | Notes |
|---|---|---|---|---|
| D1 | `@id`, `og:persistentId` | IRI, DOI | 1 | Concept-DOI and version-DOI distinguished |
| D2 | `dct:title`, `dct:description`, `og:summary` | string | 1 | |
| D3 | `og:dataDomain` | SKOS concept | 1 | DD1 to DD10, controlled, not free text |
| D4 | `og:upstreamSource` / `prov:wasDerivedFrom` | IRI[] | 2 | Absent means "not captured", never "no source" |
| D5 | multi-hop provenance | traversable | 2 | |
| D6 | `og:provenanceClass` | enum | 1 | primary, curated, modeled, reanalysis, derived, synthetic, osm-derived, institutional |
| D7 | `og:supersedes`, `og:supersededBy`, `og:complements` | IRI[] | 2 | |
| D8 | `dct:license` | SPDX id | 1 | |
| D9 | `og:accessRestriction` | enum | 1 | none, account-required, ceii, pii, commercial-paywall, discontinued |
| D10 | `og:anonymousAccess` | boolean | 1 | |
| D11 | `dcat:distribution` | Distribution[] | 1 | See 4.2 |
| D12 | `og:supportedAnalysis`, `og:excludedAnalysis` | SKOS[] | 2 | Fitness for purpose |
| D13 | `og:qualityFlags` | object | 1 | staleness, caveats, planned successors |
| D14 | `dct:spatial`, `og:bbox`, `og:geometryTypes`, `og:nativeCRS`, `og:featureCount` | | 1 | bbox/CRS/geometry mandatory for geospatial-primary |
| D15 | `dct:temporal`, `og:updateCadence`, `og:timeResolution` | | 1 | |
| D16 | `og:voltageClass`, `og:hasTopology`, `og:hasImpedance`, `og:spatialGranularity` | | 2 | zonal vs nodal |
| D17 | `og:fieldSchema`, `dct:conformsTo` | IRI | 2 | |
| D18 | `og:documentationStatus` | enum | 1 | fully-documented, partial, external-standard-only, none |
| D19 | hierarchical schema: groups, dimensions, variable shapes | | 3 | Not a flattened column list |
| D20 | graph schema: node and edge types | | deferred | Optional for later version |
| D21 | geospatial schema: per-layer inventory | | 3 | Optional for later version |

Additional fields this build adds, not in the Notion spec:

| Field | Type | Purpose |
|---|---|---|
| `og:completenessLevel` | 1, 2, or 3 | Section 6. Makes harvested-vs-curated quality visible. |
| `og:harvestSource` | string | Which harvester produced this record, or `curated` |
| `og:reviewState` | enum | `draft`, `in-review`, `confirmed`, `flagged` |
| `og:lastComputedAt` | map of signal → timestamp | Makes the semantic layer's freshness lag visible rather than hidden (Notion NFR) |

### 4.2 Distribution

Each access path is a separate `dcat:Distribution`. This matters more than it looks: the same dataset commonly has an anonymous S3 copy and an account-gated API, and the access-barrier classification differs between them.

```jsonc
{
  "@type": "dcat:Distribution",
  "dcat:accessURL": "s3://nrel-pds-nsrdb/...",
  "dcat:mediaType": "application/vnd+zarr",
  "dcat:byteSize": 4398046511104,
  "og:bulkDownload": true,
  "og:accessRestriction": "none",           // overrides dataset-level D9
  "og:supportsRangeRequests": true,
  "og:corsEnabled": true,
  "og:chunkIndexMethod": "zarr-v2",          // physical-to-index bridge
  "og:subsettingProtocol": null,             // opendap | thredds | wcs | null
  "og:credentialRequirement": null,
  "og:linkHealth": {
    "status": "verified",                    // verified | degraded | unreachable | redirected
    "lastProbedAt": "2026-09-01T04:00:00Z",
    "consecutiveFailures": 0
  }
}
```

### 4.3 Field-level

| ID | Field | Required at level | Notes |
|---|---|---|---|
| C1 | `og:localName`, `og:fieldId` | 2 | |
| C2 | `og:label`, `og:definition` | 2 | Capturable even when the source only documents it externally |
| C3 | `og:dataType`; geometry type / CRS / dimensionality for geometry fields | 2 | |
| C4 | `og:concept` | 3 | SKOS concept IRI. This is what makes two differently-named columns resolvable. |
| C5 | `og:unit` | 3 | QUDT IRI, never free text |
| C6 | `og:valueBasis` | 2 | measured, estimated, modeled, synthetic |
| C7 | `og:fieldSource` | 3 | One or more, may point at uncatalogued sources |
| C8 | `og:derivedFromField` | 3 | Field-to-field lineage. Records *which* source field, not the transformation. |
| C9 | `og:codeList` | 3 | Each categorical value resolves to its own concept IRI, not just the field |
| C10 | `og:required`, `og:completenessCaveats` | 2 | |
| C11 to C16 | ranges, join candidates, geo-join keys, `og:sameConceptAs`, hierarchical shape, graph edges | deferred | Optional for later version per Notion |

### 4.4 Crosswalk honesty rules

Non-negotiable. These are what separate this from a generic catalog.

- **X1** Concept-to-external-scheme mappings are authored once as shared versioned SKOS schemes, never per dataset.
- **X2** Use the correct SKOS match predicate. Reserve `skos:exactMatch` for genuine identity of quantity, unit *and* granularity. A nodal voltage and a zonal average voltage are not an exact match.
- **X3** An inferred concept assignment must be flagged as inferred with a stated basis.
- **X4** A field with no confident mapping carries an explicit gap marker. It is never silently omitted.

X3 and X4 are the honesty guarantees. Implement them as SHACL constraints so they cannot be skipped.

### 4.5 Validation

`shapes/opengrid-datahub.ttl` holds the SHACL shapes, stored in Fuseki and applied on every write to `og:graph/draft` and on promotion to `og:graph/catalog`. Violations block the record from the review queue's "ready" state. Examples of shapes that must exist:

- A field with `og:valueBasis` of `estimated` or `modeled` and no `og:derivedFromField` and no `og:fieldSource` fails.
- A geospatial-primary dataset without `og:bbox`, `og:nativeCRS` and `og:geometryTypes` fails at level 1.
- A field with neither `og:concept` nor an explicit gap marker fails at level 3.
- A distribution with `og:supportsRangeRequests: true` and no `og:chunkIndexMethod` fails.

### 4.6 Why the graph: worked queries

These are the queries the storage decision is being made for. Each is either impossible or requires hand-built machinery in a relational store. Implement each as a named, tested query in `services/semantic/queries/` and use them as the regression suite for the storage layer.

**Q1. Shared upstream origin at unbounded depth.**

A modeler pairs Global Wind Atlas for siting with PyPSA-Eur weather cutouts for time series, believing they are independent sources. Both trace back to ERA5. The apparent agreement between them is partly ERA5 agreeing with itself, and the study's uncertainty band is narrower than it should be.

```sparql
SELECT ?sharedOrigin ?depthA ?depthB WHERE {
  <og:ds/global-wind-atlas>  (og:upstreamSource|prov:wasDerivedFrom)+ ?sharedOrigin .
  <og:ds/pypsa-eur-cutouts>  (og:upstreamSource|prov:wasDerivedFrom)+ ?sharedOrigin .
}
```

The `+` is the point. Neither depth is known in advance, and the same pattern applies to DD2, where PyPSA-Eur's generator fleet is assembled from GEM and other sources, so comparing it against GEM GIPT is partly comparing GEM to itself. Note that both derivation chains need confirming during golden-set curation; they are the kind of relationship nobody records in machine-readable form, which is precisely the gap.

**Q2. Blast radius of an upstream correction.**

"ERA5 issued a correction. What in my study is affected?" Follows provenance, version and concept-equivalence edges in one walk, at unknown depth.

```sparql
SELECT DISTINCT ?affected WHERE {
  ?affected (og:upstreamSource|prov:wasDerivedFrom|og:supersedes|og:sameConceptAs)+ <og:ds/era5> .
}
```

**Q3. Inference over the concept scheme.**

Return every dataset carrying a renewable resource field, without anyone enumerating what counts as one. Solar irradiance, wind speed and hydro inflow are returned because `og:graph/vocab` says they are `skos:narrower` than the parent concept. Adding geothermal gradient next year makes this query return more, with no code change.

```sparql
SELECT ?dataset ?field ?concept WHERE {
  ?concept skos:broader+ og:concept/renewableResource .
  ?field og:concept ?concept .
  ?dataset og:hasField ?field .
}
```

**Q4. Federation across an external endpoint.**

Query OpenGrid's catalog and the Open Energy Platform in one query, joined on shared concept IRIs, with no ingestion of their records. This is the GERS federated-node pattern in miniature.

```sparql
SELECT ?ogDataset ?oepDataset ?concept WHERE {
  ?ogDataset og:hasField/og:concept ?concept .
  SERVICE <https://openenergyplatform.org/sparql> {
    ?oepDataset oeo:hasQuantity ?concept .
  }
}
```

**Q5. Unit-mismatch and match-strength audit.**

Find concept mappings asserted as `skos:exactMatch` where the two fields carry different QUDT units. These are X2 violations and modeling hazards, since nodal voltage and zonal average voltage look identical at the header level. This is a data-quality query over the crosswalk itself, which is only expressible because the crosswalk is data rather than code.

---

## 5. Data domains

Ten domains, controlled vocabulary, `og:dataDomain`.

| ID | Domain | V1 catalog | V1 ingestion/ETL |
|---|---|---|---|
| DD1 | Network topology & parameters | Yes | Yes |
| DD2 | Generator fleet | Yes | Harvest only |
| DD3 | IC queue & project pipeline | Yes | Harvest only |
| DD4 | Load & demand | Yes | Harvest only |
| DD5 | Renewable resource & weather | Yes | Yes |
| DD6 | Emerging technology parameters | Yes | Harvest only |
| DD7 | Fuel & commodity | Yes | Harvest only |
| DD8 | Policy & regulatory | Yes | Yes |
| DD9 | Cost & financial | Yes | Yes |
| DD10 | Geospatial & siting | Yes | Harvest only |

"Harvest only" means: records are created and published, but no Tier 2 ETL and no OpenGrid-hosted derived copies for that domain in V1.

Each domain gets a **domain page** in the UI carrying its structural note: what is genuinely unavailable and why. DD1's note about CEII, DD4's note about substation and behind-the-meter data, DD7's note about forward curves. These notes are a product feature, not a disclaimer. A catalog that tells you what does not exist is more useful than one that silently returns nothing.

Domain definitions, structural notes and the curated seed inventory live in `data/seed-sources.yaml`.

### Tier framework

Applied per dataset. Five criteria, all must hold for Tier 1: permissive license, anonymous free access on at least one path, bulk download available, model-ready structure, documented and parseable schema.

- **Tier 1** Ready to use. Cataloged by reference, no transformation.
- **Tier 2** Accessible, requires preparation. Cleaning, format conversion, structuring, or extraction from PDF or similar.
- **Tier 3** Reference only. Paywalled, restricted, stale and superseded, or a calculator producing no ingestible artifact. Listed for discovery with a pointer rationale; carries no field-level metadata and no inter-dataset links.

Tier is an internal build-prioritization fact, not a user-facing quality grade. Do not display it as a quality signal. Do display "reference only" on Tier 3 records so users know why there is no schema tab.

---

## 6. Completeness levels

New in this build. Solves the problem created by expanding to ten domains.

| Level | Name | Has | Enables |
|---|---|---|---|
| 1 | Discoverable | Dataset-level metadata, at least one distribution, license, coverage | Search, filter, access plan, download |
| 2 | Interpretable | Field-level names, definitions, types, value basis | Schema tab, quality grading (Provenance and Documentation) |
| 3 | Linked | Concept IRIs, unit IRIs, field sources | Semantic resolution, inter-dataset links, cross-dataset reasoning |

Displayed on every record. A level 1 record is honestly labeled as such rather than presented as a fully described dataset with empty tabs.

Target for the V1 launch catalog:

- Level 1: all published records across DD1 to DD10.
- Level 2: 100% of DD1, DD5, DD8, DD9 Tier 1 and Tier 2 records, plus the DD2, DD3, DD4 anchors named in the seed file.
- Level 3: the ~60 dataset golden set spanning all ten domains. Enough to make the semantic layer and link graph demonstrably real rather than a stub.

---

## 7. Catalog acquisition pipeline

This is the part that makes ten-domain coverage feasible. Six stages, each a separately testable module.

### 7.1 Harvest

`harvesters/` with one adapter per source type. Adapters required for V1:

| Adapter | Sources |
|---|---|
| `ckan` | OEDI/OpenEI, energydata.info, data.gov |
| `stac` | Planetary Computer, Earth Search |
| `zenodo` | Zenodo REST API, community and query scoped |
| `yaml_repo` | AWS Registry of Open Data (`datasets/*.yaml`) |
| `dcat_sparql` | data.europa.eu |
| `oep` | Open Energy Platform |
| `datacite` | DataCite DOI search |
| `curated` | `data/seed-sources.yaml` |

Each adapter:
- Emits raw records with a stable `sourceId` for idempotent re-harvest.
- Records a harvest run with counts, errors and duration.
- Is rate-limited and polite. Never look like abusive traffic to a source you do not control.
- Is independently runnable: `python -m datahub.harvest --source oedi --limit 100`.

The full source registry with endpoints, expected scale and domain mapping is in `data/seed-sources.yaml` under `harvest_sources`.

### 7.2 Filter

Grid relevance. Most harvest sources are broader than grid modeling. A two-stage filter: keyword and domain-vocabulary matching first, then an LLM relevance classifier on the ambiguous middle. Log every rejection with its reason so recall can be audited. Err toward inclusion; a wrongly excluded dataset is invisible, a wrongly included one is a review-queue cost.

### 7.3 Normalize

Map source-native metadata onto the OpenGrid JSON-LD schema. Per-adapter field mappings live in `normalizers/mappings/*.yaml` so they are editable without touching code. Record which fields were populated from source versus left empty; this determines the initial completeness level.

DCAT-native sources (data.europa.eu) and STAC sources normalize almost losslessly. CKAN sources map cleanly at dataset level and carry almost nothing at field level. Zenodo carries excellent identity and versioning but weak coverage facets.

### 7.4 Enrich

LLM-assisted drafting of fields the source did not carry. Structured output only, one field group per call, with the source metadata and any linked documentation as context.

Enrichment may draft: summary, data domain assignment, provenance class, supported and excluded analysis types, coverage facets, field labels and definitions, and candidate concept mappings.

Enrichment may never invent: identifiers, licenses, access URLs, byte sizes, or provenance links. If the source does not state it, the field stays empty and the record's completeness level reflects that. A fabricated license is worse than a missing one.

Every enriched field is tagged `og:enrichmentBasis: "inferred"` with the model and prompt version. This satisfies X3.

### 7.5 Validate

pySHACL against `shapes/opengrid-datahub.ttl`. Failures move to `flagged`, not to the review queue. Validation output is human-readable and points at the specific triple that failed.

### 7.6 Review and publish

A steward UI at `/admin/review`. Queue sorted by domain and by inbound link count, so high-leverage records get reviewed first. Per record the steward can confirm or correct each field, set the Provenance and Documentation Completeness quality facets, and promote the completeness level.

Publication is per record, not per batch. `og:reviewState` moves to `confirmed` and the record becomes visible.

Re-harvest is idempotent: matching on `sourceId` updates the source-derived fields and leaves steward-confirmed fields alone unless the underlying source value changed, in which case the record is flagged for re-review rather than silently overwritten.

---

## 8. Feature specifications

Nine features. Each maps to a Notion V1 feature spec. FR and AC numbering is preserved where it exists so the two documents stay traceable.

### F1. Centralized catalog

**Owns:** holding and serving records. Not defining the schema (F7 does that).

Requirements:

1. One metadata record per dataset. Bytes never stored centrally.
2. Descriptive metadata plus structured filterable coverage facets.
3. Access metadata: license, redistribution flags, access-barrier classification, anonymous-access flag.
4. Each access path a separate `dcat:Distribution` with its own format, size and terms.
5. Per-distribution access capabilities so routing is metadata-driven, never per-dataset code.
6. Provenance as explicit upstream links plus a creation-type label, enabling shared-origin detection.
7. Dataset-level sensitivity tag plus a custodian-declared visibility property defaulting to public stub.
8. On access request, return an access plan. Never bytes.
9. Queryable by humans and by MCP clients, filtered to what the requester is entitled to see.
10. Periodic link-health probing: HEAD or single-byte range request, never a full download. Status one of verified, degraded, unreachable, redirected.
11. Revision history per distribution URL: source, timestamp, old value, new value, automated or manual.
12. A stable 3xx to a new location auto-updates the stored URL and writes a revision-history entry. Provenance is never silently rewritten.
13. After N consecutive failed probes (default N=3, configurable per distribution type), flag unreachable and exclude from access plans, falling back to a live sibling distribution.
14. Notify the custodian contact on status change to unreachable. Expose a "report a broken link" action for users.

Probe cadence defaults, configurable per distribution: APIs daily, bulk files weekly, Tier 3 pointers monthly.

**Acceptance:** a distribution that fails 3 consecutive probes is excluded from the access plan and a live sibling is returned instead. A stable redirect self-heals and appears in revision history. Two datasets tracing to the same upstream source can be flagged as shared-origin.

### F2. Ingestion and hosting

**Policy: broker by default, host by exception.** The Hub hosts a copy only when one of these holds:

(a) the source format is not cloud-native or partial-read-friendly,
(b) the only access path is fragile or likely to disappear,
(c) the only accessible path charges the end user, contrary to mission,
(d) OpenGrid has already done the ETL to make a Tier 2 dataset usable and the source only offers the raw form.

Anything hosted goes to a hyperscaler object store, in a cloud-native format (Zarr, Parquet, GeoParquet, COG, chunked NetCDF-4), pursuing AWS Open Data Sponsorship where eligible. Never proxied through application servers.

Every hosted copy has a **named refresh owner assigned before launch, not after.** This is the documented failure mode in every comparable project. Enforce it: a hosted distribution with no owner cannot be published.

Datasets over ~1GB that pass the sliceability gates get converted to a partial-read format, and the catalog gets `og:chunkIndexMethod`, coordinate geometry, and range-request and CORS flags.

Conversion code is versioned and re-runnable. Not notebooks.

**Metric that matters:** the ratio of brokered to hosted records. A rising hosted count signals drift away from the policy.

### F3. Search UI

**Owns:** the human front door. Renders what other features compute; computes nothing itself.

List view (default): title, creator, summary, domain tag, provenance tag, license tag, temporal coverage as a timeline, geographic coverage as an AOI on a map, completeness level, and the three quality badges.

Search-while-typing, no submit step. Filtering on any dataset-level metadata field. Sorting by relevance or any sortable field.

Detail view tabs:

- **Overview** IRI, domains, fitness-for-purpose, completeness level
- **Provenance & Access** provenance class, upstream sources, superseding and superseded links, license detail, per-distribution access terms
- **Coverage** geographic scope and granularity, AOI as WKT, temporal coverage and granularity
- **Schema** per file and per field: concept, unit, data type, field role, field-level provenance, enrichment notes. Each concept links to the semantic layer. Absent at level 1 with an explanation, not an empty table.
- **Data Quality** three independent badges, letter grade plus label. **Never a composite score.** This is a hard constraint, not a preference.
- **Connections** one-hop graph centered on this dataset, edge thickness proportional to 5-point link strength, mirrored as a list, correlated-origin links visibly flagged with the modeling consequence explained on inspection
- **Downloads** redirect to source where permitted, plus SDK pointer for partial reads

Also: Developers page (API and SDK docs), Help, About, Connect with AI (MCP connection details formatted for common clients), federated SSO register and login.

**Report an issue** on any record, field or distribution. Issue type (incorrect metadata, broken link), optional comment, auto-captured reference to the exact thing flagged, timestamp, reporter identity if authenticated. Anonymous reports allowed. Confirmation without leaving the page. Routes to the curation queue.

**Data intake form.** Core dataset info (title, description, originator, domain, license, submitter contact), distribution info (access URLs, format, approximate size, update cadence), optional links to codebooks and data dictionaries. Required: title, description, at least one access URL, license. No login required, but capture contact. CAPTCHA and rate limiting. Fire-and-forget: confirm receipt, no status tracking back to the submitter.

**Localization readiness.** All user-facing strings externalized, all dates, numbers and units through a locale-aware layer. Ship English only. Use `next-intl`. This is architectural readiness, not a second-locale deliverable, and it is much cheaper now than retrofitted.

Empty states matter here: a zero-result search, a level 1 record with no schema tab, a dataset with no links. Design each explicitly.

Large link graphs need a cap. Default to top 12 one-hop neighbors with a "show more" affordance rather than rendering an unreadable hairball.

### F4. Semantic layer

**Owns:** turning raw catalog metadata into machine-actionable meaning. Reads catalog entries, computes, writes back.

1. Resolve fields to concept IRIs and unit IRIs across all four data shapes: per-column for tabular, per-variable for hierarchical, per-layer for geospatial, per node and edge property for graph.
2. Surface a plain-language definition alongside each resolved concept, so a field documented only via CIM/CGMES is understandable without the user owning that standard.
3. Recompute on two distinct triggers:
   - **Relational signals** (links, shared-origin flags, anything comparative) recompute when a related record is created or updated.
   - **Self-contained signals** (facts derivable from the record alone, notably Currency & Maintenance) recompute on a scheduled batch, because these go stale purely from time passing with no write event to hook.
   
   Getting this split wrong is the most likely correctness bug in the whole build. A dataset does not become stale because someone edited it.
4. Compute the Currency & Maintenance quality facet from update cadence and `supersededBy`. Write it back.
5. Compute inter-dataset link candidates and strength from concept overlap, geographic overlap, temporal overlap, domain match, joinable keys, with quality as a contributing factor. Include typed relationships.
6. Flag shared-origin relationships so correlation risk is surfaceable.
7. Expose identical resolved data to UI and to API/MCP. No divergence.
8. Distinguish inferred concept assignments from source-confirmed ones.
9. Explicit gap marker where no confident mapping exists. Never omit the field.
10. Resolve categorical values to their own concept IRIs where a controlled vocabulary governs the category, not just the field.

`og:lastComputedAt` per signal is exposed on the record so the freshness lag is visible. Evaluators need this; modelers can ignore it.

Concept resolution strategy: exact match on normalized name and unit first, then SKOS `altLabel` match, then embedding similarity over concept definitions with a confidence threshold, then gap marker. Never guess past the threshold.

### F5. Dataset quality grading

Three independent facets. Letter grade A to D plus descriptive label. Independent badges. **Never combined into a composite score.**

**Provenance**

| Grade | Label | Condition |
|---|---|---|
| A | Primary & Traced | Values measured or primary, upstream origin recorded |
| B | Derived & Traced | Values estimated, modeled or synthetic, full lineage to an origin recorded |
| C | Traced, Basis Unconfirmed | Upstream link exists, per-field basis flag not yet reviewer-confirmed |
| D | Untraced | No upstream link recorded, regardless of basis |

**Documentation Completeness**

| Grade | Label | Condition |
|---|---|---|
| A | Fully documented | Every field has definition, unit and allowed range, native or curated |
| B | Partially documented | Some fields lack definitions or units |
| C | Documented via external standard only | Fields meaningful only if the user already knows the referenced standard |
| D | Minimal | No dedicated metadata beyond a filename and a loose description |

**Currency & Maintenance**

| Grade | Label | Condition |
|---|---|---|
| A | Current | Latest known vintage, last update within stated cadence |
| B | Aging | Past due against its own cadence, no newer version known |
| D | Superseded | Explicit `supersededBy` link to a newer catalog entry |

C is deliberately unused on this facet. Leave it unused rather than inventing a level to fill it.

Rules:

- Provenance and Documentation are confirmed once by a domain expert in the onboarding review, re-confirmed when the dataset version changes.
- Currency & Maintenance is fully automatic and continuous. Never manual, never a re-trigger.
- Hierarchical datasets grade Provenance per-variable, not per-file. A single NetCDF can mix directly-observed and bias-corrected variables and one grade would lie about both.
- Geospatial datasets evaluate geometry column documentation (type, CRS, extent) separately from attribute columns.
- Currency displays the dataset's own stated cadence alongside the label, so a correctly-scheduled annual dataset does not read as stale next to an hourly one.
- Every grade derives from recorded facts. Never assessor impression. Never relative ranking against other catalog entries.
- Visible to external evaluators with no access entitlement and no authentication.

A record below completeness level 2 shows Provenance and Documentation as "not yet assessed" rather than as grade D. Absence of assessment is not the same as poor quality, and conflating them would systematically defame every harvested record.

### F6. Inter-dataset links

**Owns:** turning the semantic layer's per-pair signals into ranked, explained, workflow-aware connections. Computes no raw signals itself.

1. Retrieve candidate signals from the semantic layer.
2. Combine into one strength score, mapped onto a 5-point scale.
3. Rank and select top-N per a defined tie-break policy. Default N=12.
4. Attach a human-readable complementarity descriptor. "Different physics, complementary." "Nodal versus zonal, different granularity of the same network."
5. Attach the specific joinable keys identified.
6. Attach shared downstream workflow tags. "Both feed: Capacity Expansion Modeling, Production Cost Modeling."
7. Label typed relationships: complementary, substitute or alternative, supersedes or superseded-by.
8. Surface a shared-origin warning naming the upstream source and stating the modeling consequence in plain language.
9. **Reduce, never zero out or hide, a pairing's strength when a correlation warning applies.** Hiding it would remove exactly the information the user needs.
10. Expose identically to UI and API/MCP.
11. Recompute when the semantic layer supplies updated signals.

Every surfaced pairing carries at least one concrete human-readable reason. A bare numeric score is not sufficient and should fail review.

Starting weights, to be tuned against the golden set:

```
strength = 0.30·concept_overlap
         + 0.20·geographic_overlap
         + 0.15·temporal_overlap
         + 0.15·joinable_key_present
         + 0.10·workflow_tag_overlap
         + 0.10·quality_contribution
         − 0.15·shared_origin_penalty       // floor at tier 1, never 0
```

Put the weights in config, not code. They will change.

### F7. Metadata schema and controlled vocabularies

Section 4 is the schema. Implementation deliverables:

- `schemas/opengrid-datahub.jsonld` context
- `shapes/opengrid-datahub.ttl` SHACL shapes
- `vocab/` SKOS concept schemes, versioned, one file per scheme:
  - `og-data-domain.ttl` DD1 to DD10
  - `og-provenance-class.ttl`
  - `og-analysis-type.ttl` CEM, PCM, RA, power flow, market simulation, siting
  - `og-grid-concept.ttl` the physical quantity scheme, the big one
  - `og-access-restriction.ttl`
- Crosswalks to CIM/CGMES, PyPSA, MATPOWER, Sienna, using correct SKOS match strength per X2
- A conformance test suite: valid and invalid record fixtures, asserted against the shapes

Bootstrap `og-grid-concept.ttl` from the Open Energy Ontology and from the Sienna schema rather than authoring from scratch. Both already have community adoption; a third parallel vocabulary would be a net negative for the ecosystem.

Concept scheme versioning follows SemVer with a published change policy. Removing or redefining a concept is breaking.

### F8. Unified API

REST, OpenAPI 3.1, the canonical contract everything else calls.

**Control plane / data plane split.** The API returns small cacheable JSON. It never streams bytes.

```
GET  /v1/datasets                      search, filter, facet, paginate
GET  /v1/datasets/{id}                 full record, entitlement-filtered
GET  /v1/datasets/{id}/schema          field-level metadata
GET  /v1/datasets/{id}/quality         three facets
GET  /v1/datasets/{id}/links           ranked pairings with reasons and warnings
GET  /v1/datasets/{id}/distributions   access paths with capabilities and link health
POST /v1/datasets/{id}/access-plan     returns an access plan, never bytes
GET  /v1/datasets/{id}/download        302 redirect to source, human-facing path
GET  /v1/concepts                      SKOS concept scheme
GET  /v1/concepts/{id}                 concept plus datasets using it
GET  /v1/domains                       DD1 to DD10 with structural notes
POST /v1/submissions                   intake form
POST /v1/reports                       report an issue
GET  /v1/allowlists/{datasetId}        custodian only
PUT  /v1/allowlists/{datasetId}        custodian only
```

**The access plan.** One uniform shape regardless of whether the dataset is 800 KB or 4 TB. Only the path differs.

```jsonc
{
  "datasetId": "og:ds/era5-single-levels",
  "distributionId": "og:dist/era5-zarr-s3",
  "mode": "partial-read",              // redirect | partial-read | subsetting-protocol
  "format": "zarr",
  "location": "s3://era5-pds/zarr/...",
  "readInstructions": {
    "library": "xarray",
    "engine": "zarr",
    "storageOptions": { "anon": true }
  },
  "requestedSlice": {
    "time": ["2019-01-01", "2019-12-31"],
    "bbox": [5.9, 45.8, 10.5, 47.8]
  },
  "byteRanges": [ { "url": "...", "start": 1048576, "end": 2097151 } ],
  "credentials": null,
  "expiresAt": "2026-09-03T12:00:00Z",
  "license": "CC-BY-4.0",
  "attribution": "Copernicus Climate Change Service (C3S)",
  "redistributionAllowed": true,
  "qualityGrades": { "provenance": "A", "documentation": "A", "currency": "A" }
}
```

License, attribution and quality grades travel with the plan. This is what makes agentic access defensible: the guardrail metadata is in the payload, not in a page the agent never read.

Path selection is automatic from metadata: `og:supportsRangeRequests` plus `og:chunkIndexMethod` gives partial-read; `og:subsettingProtocol` set gives that protocol in preference to a full redirect; otherwise redirect, and the plan states that no partial read is available.

Restricted-dataset routing: sensitivity tag checked at both discovery time and plan-issuance time against the per-dataset allow-list OpenGrid stores. **The dataset creator manages the allow-list. OpenGrid stores and enforces it and never arbitrates its contents.** Three visibility levels, matching the catalog:

| Visibility | Non-entitled user sees | Entitled user sees |
|---|---|---|
| Public existence, public metadata | Everything except the bytes | Bytes too |
| Public existence, restricted metadata | Stub only | Full record and plan |
| Allow-listed existence | Nothing at all | Full record and plan |

The third is the hard one. Enforce it at query construction, not by filtering results after the fact, or the existence leaks through result counts and pagination.

### F9. MCP server and SDK

**MCP server.** Open-source, remotely hosted, thin client over the REST API. No independent data access.

Tools:

| Tool | Tier | Does |
|---|---|---|
| `search_datasets` | 0 | Catalog search, entitlement-scoped, payload-capped |
| `get_dataset` | 0 | Full record |
| `get_dataset_schema` | 0 | Field-level metadata |
| `explain_connection` | 0 | Why two datasets are linked, including correlation warnings |
| `preview_dataset` | 0 | Bounded preview, hard row and byte cap |
| `get_access_plan` | 0 | Access plan under the user's identity |
| `author_workflow` | 1 | Structured inert workflow spec. No execution. |

Rules:

- Every response grounded strictly in real catalog metadata. **The server never fabricates a dataset or a field.** Treat this as the single most important correctness property; a plausible fabricated dataset is worse than no answer.
- Every query scoped server-side to the authenticated user's permissions.
- Payload cap on every read tool so bulk data can never enter the agent's context. Default 100 KB per response.
- **Tier-gated tools are present in the interface for all callers.** Authorization is checked per call, returning 403, rather than hiding the tool. Hiding it makes the agent hallucinate around the gap.
- Identity propagates through every call. No privilege escalation path.
- Rate limits sized for agentic traffic being several times chattier than human traffic.
- Every call logged for audit and revocation.
- The agent is an **untrusted client fully outside OpenGrid's control.** No guardrail may depend on the agent's cooperation. Every enforceable control lives server-side.

Execution tiers 2 and 3 (bounded local execution, remote compute) are out of scope. They belong to Workflow Orchestration.

**Python SDK** (`opengrid-datahub`): search and filter returning native objects, access-plan retrieval and execution, lazy xarray and pandas readers that consume access plans, auth handling. Target: from zero to first dataset pull in one line.

```python
from opengrid import DataHub
hub = DataHub()
ds = hub.search(domain="DD5", region="DE", concept="solar_irradiance")[0]
da = ds.open(time=slice("2019-01", "2019-12"), bbox=[5.9, 45.8, 10.5, 47.8])
```

`ds.open()` fetches an access plan and executes it client-side. The Hub is not in the path.

### F10. Authentication and access control

- Token-based, OIDC-compatible, consistent across REST, SDK, MCP and UI.
- Federated SSO: GitHub, Google, Microsoft. Plus an OpenGrid-native credential path.
- Identity propagates through MCP and SDK calls. Agent requests are strictly bounded by the represented user's own permissions.
- Allow-list checks at discovery and at plan issuance.
- Rate limiting across all client types, with different budgets for human and agent traffic.
- 401 and 403 with clear errors, never silent degradation. A silently truncated result set is a correctness bug that looks like a UX choice.
- Authorization grants and refusals logged.

Anonymous read of public records must work with no login. Do not gate browsing.

---

## 9. Repository layout

```
opengrid-datahub/
├── README.md                    this document
├── docs/
│   ├── architecture.md
│   ├── metadata-schema.md
│   ├── api.md                   generated from OpenAPI
│   └── decisions/               ADRs
├── schemas/
│   └── opengrid-datahub.jsonld
├── shapes/
│   └── opengrid-datahub.ttl
├── vocab/                       SKOS concept schemes, versioned
├── data/
│   ├── seed-sources.yaml        harvest registry + curated seed inventory
│   └── golden-set/              ~60 fully-specified records, level 3, all domains
├── services/
│   ├── graph/                   the ONLY module that talks to Fuseki
│   │   ├── client.py            SPARQL query and update wrapper
│   │   ├── graphs.py            named-graph constants and helpers
│   │   ├── records.py           JSON-LD record read/write
│   │   └── reason.py            materialize entailments on vocab change
│   ├── projector/               graph → OpenSearch, on commit
│   │   ├── construct.rq         CONSTRUCT flattening a record to a search doc
│   │   ├── index.py
│   │   └── reindex.py           full rebuild, must be one command
│   ├── api/                     FastAPI. No SPARQL here.
│   │   ├── routers/
│   │   ├── models/              Pydantic; SQLAlchemy for operational tables
│   │   ├── broker/              access-plan issuance, path selection
│   │   ├── entitlement/         allow-lists, visibility enforcement
│   │   └── search/              OpenSearch client, query builders
│   ├── harvest/                 No SPARQL. Emits JSON-LD, hands to services/graph.
│   │   ├── adapters/            ckan, stac, zenodo, yaml_repo, dcat_sparql, oep, datacite
│   │   ├── filters/
│   │   ├── normalizers/
│   │   │   └── mappings/        per-source field maps, YAML
│   │   ├── enrich/              LLM drafting, structured output
│   │   └── validate/            pySHACL runner
│   ├── semantic/                SPARQL lives here
│   │   ├── queries/             Q1-Q5 and friends, named and tested
│   │   ├── resolve.py           field → concept + unit
│   │   ├── linkage.py           pair candidate signals
│   │   ├── grading.py           currency facet
│   │   └── triggers.py          relational vs scheduled recompute
│   ├── linksvc/                 ranking, descriptors, warnings
│   └── mcp/                     fastmcp server. Calls REST only.
├── web/                         Next.js 15
│   ├── app/
│   │   ├── datasets/
│   │   ├── domains/
│   │   ├── concepts/
│   │   ├── submit/
│   │   ├── admin/review/        steward queue
│   │   └── connect/             MCP connection details
│   ├── components/
│   └── messages/                i18n resources, en only
├── sdk/python/
├── ops/
│   ├── docker-compose.yml       fuseki, opensearch, postgres, redis, api, web
│   ├── fuseki/                  TDB2 config, backup, compaction cron
│   ├── migrations/              alembic, operational tables only
│   └── probes/                  link-health worker
└── tests/
    ├── fixtures/                valid and invalid record fixtures
    ├── conformance/             SHACL conformance suite
    ├── graph/                   Q1-Q5 regression suite against a seeded store
    └── e2e/                     playwright
```

---

## 10. Build sequence

Ten milestones. Each ends in something demonstrable, not a layer.

**M1. Schema, vocabulary and validation.** JSON-LD context, SHACL shapes, the five SKOS schemes, named-graph layout, conformance fixtures, Fuseki running locally with TDB2. Nothing else can be built correctly before this exists.
*Done when:* the conformance suite passes, an invalid record is rejected with a message pointing at the failing triple, and Q3 (concept inference) returns narrower concepts with no enumeration anywhere in code.

**M2. Catalog core and projector.** `services/graph` record CRUD, distribution model, revision history, SHACL on commit, plus the projector into OpenSearch and a one-command full reindex. Operational tables in Postgres. Load the curated seed from `seed-sources.yaml`.
*Done when:* every curated seed dataset is in `og:graph/catalog`, validates, is retrievable via a Python call, appears in the search index within the projector's stated lag budget, and survives a full reindex unchanged.

**M3. Harvest pipeline.** All eight adapters, filter, normalizer, enricher, validator, and the review queue data model. Run against OEDI, AWS Open Data, Zenodo, energydata.info.
*Done when:* 2,000+ candidate records are harvested, normalized and validated, with a per-source recall audit.

**M4. Search and API.** OpenSearch indexing, the read endpoints, faceting, OpenAPI spec.
*Done when:* a search across all ten domains returns correctly faceted results in under 200 ms at catalog scale.

**M5. Access broker.** Access-plan issuance, path selection, redirect path, byte-range path, subsetting-protocol path, link-health probing with revision history and auto-heal.
*Done when:* a 4 TB Zarr dataset and an 800 KB CSV both return a plan of identical shape, and a slice request against the Zarr transfers only the slice.

**M6. Auth and entitlement.** OIDC, federated SSO, allow-lists, the three visibility levels, rate limiting.
*Done when:* a non-entitled user cannot detect the existence of an allow-listed-existence dataset through any endpoint, including result counts and pagination.

**M7. Semantic layer and grading.** Concept resolution across four shapes, both recompute triggers, the currency facet, gap markers, inferred flags, and the Q1-Q5 query suite.
*Done when:* two differently-named fields for the same quantity resolve to the same concept IRI; a dataset whose cadence lapses re-grades on the next batch with no write event; Q1 returns the shared ERA5 origin for the GWA / PyPSA-Eur cutout pair at correct depth; and Q4 successfully federates against the live OEP endpoint.

**M8. Links.** Candidate signals, ranking, descriptors, typed relationships, shared-origin warnings.
*Done when:* a known correlated pair from the golden set surfaces with a warning that names the shared upstream source and states the modeling consequence.

**M9. Web UI.** List, detail with all seven tabs, map and timeline, link graph, quality badges, download surface, report-an-issue, intake form, i18n scaffold, steward review queue.
*Done when:* a modeler can go from landing page to a correct access plan for a DD5 dataset in under 60 seconds, and an unauthenticated evaluator can read all three quality grades.

**M10. MCP and SDK.** MCP server with the seven tools, Python SDK, Connect with AI page, docs.
*Done when:* an agent in a standard client can search, inspect, explain a connection, and hand back an access plan without ever receiving bulk data, and a 403 is returned for an out-of-tier tool rather than the tool being hidden.

M1 through M5 are the critical path. M6 can run in parallel from M4. M7 and M8 depend on level 3 records existing, so the golden set must be curated during M3.

**M1 carries the storage risk.** Before committing past it, load the golden set into Fuseki and run Q1 through Q5. If federation against OEP does not work in practice, or if inference materialization on a realistic vocabulary is unworkable, that is the moment to revisit section 0 decision 3, not month six. Budget a week for this and treat a negative result as a successful experiment.

---

## 11. Testing

- **Conformance:** every record fixture asserted against the SHACL shapes. Invalid fixtures must fail with the expected violation.
- **Golden set:** ~60 fully-specified level 3 records across all ten domains, used to regression-test concept resolution, link ranking and quality grading. Hand-curated once, then frozen.
- **Entitlement:** a matrix over {anonymous, authenticated, allow-listed, custodian} × {public, restricted-metadata, allow-listed-existence} × every endpoint. Existence leakage is the failure mode to hunt for.
- **Broker:** access-plan shape invariance across five orders of magnitude of dataset size.
- **Grounding:** MCP responses asserted against the catalog. Any dataset or field in a response that is not in the catalog is a test failure.
- **Link health:** simulate 3xx, 404, timeout and intermittent failure. Assert auto-heal, revision history entries and fallback to a sibling distribution.
- **E2E:** Playwright over the M9 done-criterion flows.

---

## 12. Open questions carried forward

Not resolved here. Flagged so they are decided deliberately rather than by whoever writes the code first.

**Product, needs TC or MC input:**

1. **Quality grade disputes.** If a provider contests a facet grade, who corrects it and on what authority. Flagged in the Notion spec as the single Data Hub topic where TC input matters most, and as expected to be contested. Do not let an implementation default become the policy.
2. **Documentation Completeness grade C weighting.** Is "documented via external standard only" equally acceptable for raw CIM/CGMES as for a convention users are assumed to know?
3. **Currency facet grade C.** Leave unused or define a fourth level.
4. **CEII governance.** Whether a de-sensitized CEII derivative under controlled access is a defensible niche. Needs Legal plus product.

**Engineering, decidable in flight:**

5. Self-contained recompute batch cadence. Likely varies by the dataset's own stated cadence rather than one global interval.
6. Whether an all-distributions-unreachable record needs distinct treatment beyond per-distribution status, and whether unreachable rate feeds the Currency facet or stays independent.
7. Who is notified on unreachable when no custodian contact is on file.
8. Whether a correlation warning caps strength at a maximum tier or applies a fixed penalty. Start with the fixed penalty in the formula above and tune against the golden set.
9. In-flight access plans issued to a user subsequently removed from an allow-list: immediate revocation or expiry. Default to expiry with a short TTL, but decide explicitly.
10. Whether custodians can delegate allow-list management, and how that is audited.
11. Report deduplication: surface repeated reports against the same field as a count, or dedupe.
12. Report routing for externally-custodian-owned records: OpenGrid queue only, or forwarded to the custodian.

---

## 13. Non-goals

Stated so they do not creep in.

- Storing or proxying dataset bytes. Ever.
- Workflow execution. Tiers 2 and 3 belong to Workflow Orchestration.
- Data transformation at query time. That is SDK and compute.
- Defining the data domain schemas themselves. That is Standardized Data Domain Schema.
- Synthetic dataset generation and external dataset production. Data Hub 2.0.
- Tool-ready data delivery and per-tool packaging. Data Hub 3.0.
- Julia and R SDKs. Python first.
- A second locale. Readiness only.
- Graph-format dataset support (D20). Deferred.
- Becoming a clearance authority for restricted data. OpenGrid stores and enforces the custodian's allow-list and never decides its contents.

---

## 14. Principles to hold under pressure

When a tradeoff comes up mid-build, these are the tiebreakers.

1. **The Hub is never in the byte path.** If a solution puts it there, the solution is wrong.
2. **A missing field means "not captured", never "does not exist."** Gap markers over silent omission, every time.
3. **Never a composite quality score.** Three independent facets, or the signal is lost.
4. **Grounded or absent.** Never fabricate a dataset, a field, a license or a URL. This applies to the enricher and to the MCP server equally.
5. **Honest completeness.** A thin record labeled thin beats a thin record dressed as complete.
6. **Explainability over score.** Every link, every grade, every warning carries a concrete human-readable reason.
7. **Server-side enforcement.** Agents and clients are untrusted. Nothing depends on their cooperation.
8. **The graph is the record; the index is derived.** Never write to OpenSearch as a source of truth, and never let a fix land only in the index. If a full reindex would lose it, it was in the wrong place.
9. **SPARQL stays contained.** Only `services/graph`, `services/semantic`, `services/linksvc` and `services/projector` contain SPARQL. A contributor who knows Python and not RDF must be able to write a harvester, an API route or an SDK method without learning it.
