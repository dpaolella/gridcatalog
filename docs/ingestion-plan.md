# Catalog scale-up: an ingestion plan

Status: proposed. Tracking issue: [#13](https://github.com/dpaolella/gridcatalog/issues/13).

The catalog publishes **66 datasets carrying 24 field descriptions between
them**. The pipeline that was built to make it bigger runs, harvests 1,199
records from a single source in four minutes, and publishes **zero** of them.
This document says why, and what to change, in the order the changes unblock
each other.

Everything below is measured on this repository at `57110f0`, not estimated.
Section 9 gives the commands.

---

## 1. Baseline

| | Measured | How |
|---|---|---|
| Published records | **66** | `datahub index reindex` → 66 of 66 |
| ...at completeness level 1 | 57 | search index `completeness_level` |
| ...at level 2 | 4 | |
| ...at level 3 | 5 | |
| Records with **any** field-level metadata | **9 of 66** | index `field_count` |
| Total `og:Field` nodes in the catalog | **24** | sum of `field_count` |
| Largest schema in the catalog | 5 fields (`pypsa-eur-grid`) | |
| ECMWF ERA5, as published | **4 fields** | `tests/fixtures/records/ecmwf-era5.jsonld` |
| ECMWF ERA5, as the dataset publishes itself | **273 fields**, every one with a unit and a long name | §4 |
| Curated seed rows | 114, of which 55 reach the catalog | `datahub seed load` |
| Adapters written | 9 (8 network + `curated`) | `services/harvest/adapters/` |
| Adapters that emit `og:hasField` | **1** (OEP) | `grep -l hasField mappings/` |
| Harvest sources registered | 11, est. 6,350 datasets | `data/seed-sources.yaml` |
| Harvest runs ever executed against the catalog | **0** | the site is built from seed + fixtures only |

The 66 records are the curated seed inventory plus sixteen hand-authored
golden-set fixtures. Every fact about breadth and depth in this document
follows from that one sentence: **the catalog has never been fed by its own
harvester.**

---

## 2. Two problems, and they are independent

**Breadth.** The harvest pipeline reaches the review queue for no record at
all, so running it more, or against more sources, changes nothing. This is a
correctness problem in four specific places, not a scale problem.

**Depth.** No stage of the pipeline ever looks at a dataset's own schema. Eight
of the nine adapters read a *catalog record about* a dataset — title, licence,
access URL — and none of them reads the dataset. The field metadata the
catalog is missing is, in most cases, already published by the dataset itself
in machine-readable form.

They are worth fixing in that order, because depth without breadth is 66
well-described datasets and breadth without depth is 5,000 stubs. But they
share no code, so they can be worked in parallel.

---

## 3. Breadth: why 0 of 1,199

A full run of the AWS Registry of Open Data — one of the eleven registered
sources, and the one that needs no credentials:

```
aws_open_data: 1199 seen, 527 relevant, 0 queued, 524 flagged, 3 errors in 260.1s
```

527 of the 1,199 passed the relevance filter, and **none of the 527 reached the
review queue.** Every one failed SHACL and went to `flagged`, which is the
correct handling of a record that cannot be published and is also, at 527 out
of 527, a statement about the pipeline rather than about the source.

Replaying all 1,199 stored payloads — the filter bypassed, so the blocker
counts describe the source rather than the filter — through the same normaliser
and the same SHACL validator isolates the blockers, then measures what removing
them is worth (`docs/ingestion/spikes/replay_normalize.py`):

```
as built:                              0 queued, 1199 flagged
   1199  og:updateCadence
    998  og:provenanceClass
    724  og:dataDomain
     11  dcat:distribution
      1  dct:license

with repairs:                        172 queued, 1027 flagged
    998  og:provenanceClass
    724  og:dataDomain
     11  dcat:distribution

ceiling: repairs + both fields filled:
                                    1188 queued,   11 flagged
     11  dcat:distribution
```

Read that as three facts. **`og:updateCadence` alone fails 100% of records** —
until it is fixed, nothing else in the pipeline has ever been reached. Two
mechanical repairs, neither of which invents anything, take conformance from 0
to 172. And **1,188 of 1,199 are blocked on nothing but `provenanceClass` and
`dataDomain`** — two fields the enricher already lists by name in
`ENRICHABLE_FIELDS` and never fills, because it never runs. The 11 that fail
for a real reason list no distribution at all, and a record with nowhere to get
the data is correctly refused.

The third arm fills both fields with placeholder values to take the
measurement. It is not the proposal: a blanket provenance class is exactly the
fabricated quality claim `_classify` refuses to make. WP-11.1 and WP-11.2 fill
them honestly. The number it establishes is the ceiling — how much of this
source is reachable at all.

### B1 — `og:updateCadence` fails every record, and it is a one-line mapping bug

`shapes/opengrid-datahub.ttl:261` constrains `og:updateCadence` to an ISO 8601
duration or one of `irregular` / `on-demand` / `discontinued`.
`mappings/yaml_repo.yaml:29` maps the registry's `UpdateFrequency` into it with
`transform: [text]`. The registry's actual values:

```
229  Varies by dataset      67  As Needed        52  Not updated
 22  Daily                  20  Monthly          16  Quarterly
 16  Annually               15  Never            13  Periodically
 11  New data is added as soon as it is available.
```

Free text into a pattern-constrained field. Every record fails, so nothing else
about the pipeline has ever been exercised end to end. The same bug is latent
in every other mapping that carries a cadence.

### B2 / B3 — `provenanceClass` and `dataDomain` are correctly refused and never filled

The normaliser will not guess either (`engine.py:_classify`), and it is right
not to: a provenance class caps the Provenance grade, so a wrong one is a
fabricated quality claim. But the component built to fill them — the enricher,
whose `ENRICHABLE_FIELDS` contains `provenanceClass` and `dataDomain` by
name — never runs, because no model is configured. The design has a hole in
it that the design already anticipated; nobody plugged the cable in.

`og:dataDomain` additionally has no *last-resort* filing. The harvest source
declares in `seed-sources.yaml` which domains it carries; that list is used as
a prior for the classifier and then discarded when the classifier finds
nothing.

### B4 — licence resolution is exact-string only

`engine.py:_licence` matches `seed-license-map.yaml` by exact string. The
registry's licence field is prose:

```
[Creative Commons BY 4.0](https://creativecommons.org/licenses/by/4.0/)
Creative Commons Attribution 4.0 International License
DE Africa makes this data available under the Creative Commons Attribute 4.0 license https://…
```

All three are CC-BY-4.0, which is already in the map. All three miss it and
land as `LicenseRef-Unreviewed-…` with `redistributionAllowed: false`.

### B5 — nothing can be published without a human, per record

`runner.py:_publish` writes every harvested record to `og:graph/draft` and
enqueues it. `og:reviewState` moves to `confirmed` only through the steward UI,
one record at a time. That is the correct default and it is also, at 5,000
records, exactly the headcount constraint the PRD's §0 claims to have designed
around. **Fixing B1–B4 produces a 5,000-item review queue, not a bigger
catalog.** See §6.

### B6 — the relevance filter's precision stage is dark

527 of 1,199 accepted, and the accepts include `personally identifiable
information` and `crowd-sourced` matching at 0.35. The vocabulary stage is
doing the LLM stage's job because the LLM stage is unconfigured, and the module
correctly fails open. Generous is right — a wrongly excluded dataset is
invisible — but the cost lands on a review queue that has no capacity.

---

## 4. Depth: field metadata is already published, by the datasets

The catalog holds 24 field descriptions. Here is one dataset:

```
$ python docs/ingestion/spikes/schema_probe.py --targets docs/ingestion/spikes/targets.json
ECMWF ERA5 (ARCO)          zarr-v2-consolidated   273 fields  273 with units  273 with labels   129.7 KB  ok
ERA5 single-level (ARCO)   zarr-v2-consolidated    44 fields   44 with units   44 with labels   584.4 KB  ok
WRI Global Power Plant DB  csv-header              36 fields    0 with units    0 with labels    64.0 KB  ok
```

**273 fields, each with `long_name`, `short_name` and `units`, in one HTTP
request of 130 KB.** No model, no inference, no steward. The catalog publishes
four of them, hand-typed, because a human typed four.

That is not an ERA5 special case. It is what every self-describing format does:

| Distribution format | Schema surface | Cost | Yields |
|---|---|---|---|
| Zarr | `<store>/.zmetadata` (v2), `zarr.json` (v3) | 1 request | name, long name, dtype, units, dimensions |
| NetCDF / HDF5 over HTTP | THREDDS `.das`/`.dds`, or a header range read | 1 request | same, via CF attributes |
| Parquet | footer via `Range:` on the last 64 KB | 1 request | name, physical + logical type |
| CSV | `Range: bytes=0-65535`, first line | 64 KB | column names only |
| Frictionless datapackage | `datapackage.json` | 1 request | name, title, description, type, unit |
| STAC | `cube:variables` (datacube extension) | already fetched | name, description, unit, dimensions |
| CKAN | resource `datastore` data dictionary | 1 request per resource | name, type, label |
| OEP | OEMetadata `resources[].schema.fields` | already fetched | **implemented** |
| ArcGIS FeatureServer | `?f=json` → `fields[]` | 1 request | name, alias, type |
| Socrata | `/api/views/{id}.json` → `columns[]` | 1 request | name, description, type |

Reading a schema is not being in the byte path. `services/api/broker/prober.py`
already does range-limited HTTP against these same URLs for link health, under
rules — HEAD first, never a full download — that a schema probe can reuse
verbatim.

The consumers are already built and are starving. `services/semantic/resolve.py`
resolves a field name to a SKOS concept over a 164-concept vocabulary whose
altLabels are literally `ssrd`, `swgdn`, `da_lmp`. It has 24 fields to work on.
Give it 273 ERA5 variables and the cross-dataset concept join that the whole
triple-store decision (§0 of the PRD) exists to enable starts returning
answers.

---

## 5. The plan

Seven work packages. Each is one pull request, each has a numeric exit
criterion, and each is independently revertible. WP-11.1 through WP-11.3
unblock breadth; WP-11.4 and WP-11.5 build depth; WP-11.6 and WP-11.7 make the
result survivable at scale.

### WP-11.1 — Normaliser hardening (deterministic, no model)

**Fixes B1, B3, B4.**

- A `cadence` transform in `normalizers/engine.py:TRANSFORMS`, mapping the
  phrase vocabulary onto the values SHACL already accepts, and returning
  `None` — not a guess — for anything it does not understand. Applied in every
  mapping that carries `updateCadence`.
- A licence-string resolver in front of `_licence`: unwrap markdown links,
  match licence URLs and unambiguous name-plus-version patterns. Unambiguous
  only — `"Creative Commons"` with no version stays unresolved, because CC-BY
  and CC-BY-NC-SA are not the same permission.
- A last-resort domain filing: where the classifier finds nothing, file under
  the domains the harvest source declares for itself, marked
  `og:inferredAssignment` with a basis that says so.

**Exit criterion.** Replaying the 1,199 AWS payloads, `og:updateCadence` and
`dct:license` disappear from the blocker table entirely, and `og:dataDomain`
falls below 100. Conformance ≥400 without any model call. No golden-set
expectation changes.

**Landed 2026-09-06. Measured:**

```
before:  1199 payloads ->    0 queued, 1199 flagged
   1199  og:updateCadence
    998  og:provenanceClass
    724  og:dataDomain
     11  dcat:distribution
      1  dct:license

after:   1199 payloads ->  201 queued,  998 flagged
    998  og:provenanceClass
     11  dcat:distribution
```

`updateCadence`, `dataDomain` and `license` are gone — all three constraints
eliminated, not merely reduced. Conformance is **201, not the ≥400 predicted**,
and the reason is worth recording rather than rounding off: the blockers
overlap. Most records missing a domain were also missing a provenance class, so
removing the domain blocker converted far fewer records than its own count of
724 suggested. The prediction was arithmetic on non-disjoint sets.

What it leaves is cleaner than the number implies. **The pipeline is now
blocked on exactly one field for 83% of records, and on a genuinely absent
distribution for the other 11.** Every mechanical failure is fixed; what
remains is the one thing the normaliser is right to refuse to invent.

**Risk.** A cadence phrase mapped to the wrong duration mis-grades Currency.
Mitigation: the table is explicit, it is tested against the registry's real
value distribution, and unrecognised input drops the field rather than guessing.

### WP-11.1a — An absent provenance class becomes a gap marker

**Fixes B2 deterministically, with no model.** [ADR-0011](decisions/0011-provenance-gap-markers.md).

`og:provenanceClass` was the single remaining blocker after WP-11.1: 998 of
1,199 records, and nothing else. The normaliser is right to refuse to guess it
— a wrong class is a fabricated quality claim — but refusing left a hole, and a
hole fails level 1, and a record that fails level 1 is invisible.

So the record now carries an explicit `og:provenanceGap` with a stated
`og:gapReason`. This is `og:conceptGap` — ADR-0005's third enforcement layer,
crosswalk rule X4 — applied one level up: never a silent omission, at dataset
level as at field level. D6 becomes an `sh:or` in the shapes; a class, or an
explicit gap, never neither.

It claims nothing about the data. It says *the catalog cannot show you where
these values came from, and we looked* — the same distinction
`grading/provenance.py` already draws in its D row. Silence graded D before and
grades D now; what changed is that the record exists to be graded.

**Landed 2026-09-06. Measured:**

```
1199 payloads -> 1188 queued, 11 flagged
    11  dcat:distribution

provenance:  998 gap · 123 reanalysis · 40 modeled · 27 derived
              6 osm-derived · 5 curated
```

**The ceiling, reached.** The only records still refused are the 11 that list
no distribution at all, and a record with nowhere to get the data is correctly
refused. From one source, with no model and no API key, the catalog goes from
66 publishable records to **1,254**.

The cost, stated plainly: 998 of those records say "provenance not stated". A
catalog that mostly says that is honest and looks unimpressive, and it is the
right trade against a smaller one that looked better by hiding the same
ignorance behind an empty result set. WP-11.2 is what closes the gaps.

### WP-11.2 — Turn enrichment on, with a budget

**Fixes the residue of B3 and B6, and closes the provenance gaps WP-11.1a
records.**

The enricher is written, its guardrails are written, and its tests pass. What
is missing is configuration, cost control and a cache:

- Content-hash cache keyed on the payload, so a re-harvest of an unchanged
  record makes no call. On a daily crawl this is nearly all of the cost.
- A per-run token budget and a per-source cap, so a runaway crawl cannot spend
  unboundedly.
- The relevance filter's LLM stage enabled on the ambiguous middle only
  (score between 0.2 and 0.6), which is where its precision problem lives.
- Batch drafting: one call per record, not one per field.

Nothing about the honesty guarantees changes. `ENRICHABLE_FIELDS` stays a
closed set filtered after the call, every drafted value keeps
`og:enrichmentBasis "inferred"`, and a drafted value is never allowed into a
gating field (see WP-11.3).

**Exit criterion.** The gap-to-class ratio per source falls below 1:1 — that
is, enrichment closes more provenance gaps than it leaves — with every drafted
value carrying its `og:enrichmentBasis`. Enrichment cost per 1,000 records is
measured and recorded in `docs/operations.md`. Conformance is no longer the
measure here: WP-11.1a already took it to the ceiling, and what enrichment adds
is quality, not volume.

### WP-11.3 — Trust tiers and auto-promotion

**Fixes B5.** Signed off — see §6. The implementation is small:

- A `reviewState` value `auto-confirmed`, third alongside `draft` and
  `confirmed`, added to the shapes, the API schema and the record card — a
  reader must be able to see which one they are looking at without asking.
- A promotion check in `runner.py:_publish` evaluating the four conditions in
  §6 and writing down which one failed when it declines, so the queue explains
  itself.
- A `datahub review demote <id>` command, because auto-promotion is only
  defensible if reversing it is one command.

**Exit criterion.** ≥1,000 records auto-confirmed from `aws_open_data` with no
steward action; a hand-audited sample of 50 shows no wrong licence and no dead
access URL; every auto-confirmed record renders as auto-confirmed in the UI.

**Note on where the real review gate sits.** Because the catalog is rebuilt and
republished from a git commit (§7), an auto-confirmed record still cannot
reach the public site without a merge. The pull request is a second gate that
this plan gets for free, and it is what makes "publish anything the pipeline can
substantiate" a safe default rather than a brave one.

### WP-11.4 — A schema-probe stage

**Fixes D1, D2.** A new stage between normalise and enrich:

```
harvest ─► filter ─► normalize ─► SCHEMA PROBE ─► enrich ─► validate ─► review
```

`services/harvest/schema/` with one extractor per surface from the §4 table,
dispatched on the distribution's media type and URL shape. Rules, which are
the existing rules and not new ones:

1. **Source-stated only.** A surface that carries no units yields fields with
   no unit. The enricher may later draft a *label* for a named field; it may
   never invent the field.
2. **Never a full download.** `Range` and `HEAD`, reusing `broker/prober.py`'s
   limits and its politeness settings. A store whose schema cannot be read in
   one bounded request is skipped, not fetched.
3. **Every field records where it was read from**, so a wrong field is
   traceable to a wrong parse rather than to nobody-knows.
4. **Probe failure is not record failure.** A record whose schema could not be
   read stays at level 1 and says so. That is what level 1 is for.

`docs/ingestion/spikes/schema_probe.py` is a working prototype of the four
highest-value extractors and the source of the §4 numbers.

One prerequisite that will otherwise stall this package: **a probe needs a
distribution it can actually read.** ERA5's record carries
`s3://era5-pds/zarr/`, and `broker/prober.py` already documents that HTTP
cannot reach an `s3://` URI — correctly, and it is not a defect. So the package
also needs an object-store URI resolver (`s3://bucket/key` →
`https://bucket.s3.amazonaws.com/key`, `gs://` → `https://storage.googleapis.com/`),
and ERA5's record needs the ARCO Zarr copy on GCS added as a distribution,
since that is the copy carrying the 273-variable consolidated metadata. Expect
the same to be true of most cloud-native records.

**Exit criterion.** ERA5 publishes ≥250 fields with units. Catalog-wide, the
total `og:Field` count goes from 24 to >2,000, and ≥30% of published records
reach completeness level 2.

### WP-11.5 — Field resolution at 100× the volume

**Fixes D3.** The resolver is correct and untested above 24 fields. At 273
fields on one record:

- `resolve_record` walks every field against 164 concepts. Profile it, and
  index the vocabulary's labels rather than scanning.
- Gap markers become the common case, not the exception. 273 ERA5 variables
  will produce perhaps 30 concept hits and 240 honest gaps, and the record page
  must not read as broken because of it.
- The web schema tab (`web/src/components/DatasetTabs.tsx:339`) renders every
  field in one table with no search, no pagination and no grouping. At 273 rows
  that is unusable. Concept-resolved fields first, then a searchable remainder.

**Exit criterion.** ERA5's schema tab is usable at 273 fields; `semantic run`
over a 2,000-field catalog completes in under ten minutes.

### WP-11.6 — Source expansion

Only after WP-11.1 through WP-11.4 land, because expanding sources before the
pipeline publishes anything multiplies zero.

- The registered eleven, run to completion, priority order. Estimated 6,350.
- The AWS registry alone turned out to hold **1,199 datasets, not the 400 the
  seed file estimates** — check each source's real scale on first run and
  correct `scale_estimate`.
- Sources worth adding, in descending value per unit of adapter work: the
  Copernicus CDS variable-level catalogue (the ERA5 download form's options are
  the field list, and a CDS-specific extractor belongs in WP-11.4);
  data.gov via CKAN with organisation filters for EIA / FERC / EPA; NREL's
  own OEDI submissions API; ENTSO-E Transparency; EIA's v2 API; PUDL; the
  Open Power System Data datapackages, which are Frictionless and therefore
  free field metadata.
- Per-source: an adapter (or a mapping alone, where an existing adapter fits),
  a normaliser mapping, a fixture-backed test, and a `scale_estimate` corrected
  against a real run.

**Exit criterion.** ≥3,000 published records across ≥8 sources, all ten domains
non-empty, and no source contributing more than 40% of the catalog.

### WP-11.7 — Scale mechanics

Measured today and extrapolated linearly, which is the right first
approximation because every stage here is per-record:

| | Measured | Per record | At 5,000 records |
|---|---|---|---|
| Harvest + normalise + validate | 1,199 in 260 s | 4.6 rec/s | ~18 min per source sweep |
| `index reindex` | 66 in 11.9 s | 5.5 rec/s | ~15 min, full rebuild only |
| `graph.nq` | 2,771 triples for 66 catalog records | ~42 triples | ~70 MB before field metadata |
| ...with schemas | — | ~10 triples per `og:Field` | +150 MB at 10 fields/record average |
| Static snapshot | 1.8 MB, 334 files | 27 KB, 5 files | ~135 MB, ~25,000 files |

Nothing here exceeds GitHub Pages' 1 GB site limit, and that is the wrong thing
to worry about. Three real limits, each with an obvious fix:

- **Reindex is full-rebuild only.** 15 minutes to reflect a one-field
  correction. Incremental reindex from the graph's change set, with the full
  rebuild kept as the fallback it should always have been.
- **SHACL validation is the pipeline's throughput floor** at 4.6 records/s, and
  it is on the critical path of every harvest and every replay. The validator
  is stateless per record; run it in a worker pool.
- **The static export renders one page per record.** 5,000 Next.js pages plus a
  25,000-file artefact is a slow build and a slow upload before it is a large
  one. Shard the search index and bundle detail records rather than emitting a
  file each.

The one number that could genuinely surprise is field metadata's weight in the
graph: a 273-field ERA5 record is roughly 2,800 triples on its own, twenty
times a level-1 record. Worth measuring for real in WP-11.4 rather than
projecting.

**Exit criterion.** A full rebuild from empty to 5,000 published records
completes in CI in under 45 minutes, and an incremental reindex of one changed
record completes in under 10 seconds.

---

## 6. Auto-promotion — **decided, 2026-09-06**

Everything else in this plan is engineering. This was policy, and it is settled:
**auto-promotion is approved, with no trust list.**

**The problem it solves.** Per-record human confirmation is the correct default
and it does not scale. 5,000 records at two minutes each is 166 hours of
steward time before the first one is published.

**What was decided.** A record auto-promotes to `og:reviewState
"auto-confirmed"` — a third state, distinct from `confirmed`, and visible as
such in the UI and the API — if and only if **all** of:

1. SHACL conforms at the record's computed completeness level;
2. its licence resolved to an SPDX identifier or a reviewed `LicenseRef` —
   never `LicenseRef-Unreviewed-*` and never `LicenseRef-Unstated`;
3. at least one distribution probed 200 or 206 within the last 30 days;
4. no model-drafted value sits in a **gating** field. Gating fields are
   licence, access, distribution, provenance links and identifiers — the same
   set `ENRICHABLE_FIELDS` already refuses. A drafted summary or domain filing
   is fine.

**The per-source trust list is dropped.** An earlier draft gated promotion on a
`trust: high` marker in `seed-sources.yaml`. That field does not exist and is
not being added: it is a hand-maintained list that has to be kept correct
forever, and every condition it was standing in for is better answered by
evidence about the record itself. Conditions 1 to 4 are all machine-checkable
and all specific to the record, which is the stronger test — a high-trust
source can still publish a record with a dead link, and a low-trust one can
still publish a perfectly-formed record with a resolved licence.

What that leaves is the rule the operator asked for in plain terms: **publish
anything the pipeline can fully substantiate on its own, and queue the rest.**

Everything else stays in the queue, and the queue keeps its existing ordering
so stewards spend their time where the leverage is.

**Why this is honest rather than a shortcut.** The catalog already publishes
its own confidence: `og:completenessLevel` says how much is known,
`og:enrichmentBasis` says which values a model drafted,
`og:inferredAssignment` says which assignments were inferred and on what basis,
and the quality facets grade provenance and documentation separately with no
composite score (ADR-0007). An auto-confirmed level-1 record with a resolved
licence and a probed URL is not a claim that a human checked it — and with
`reviewState` on the record and rendered in the UI, no reader can mistake it
for one.

**What it costs.** A wrong record reaches users before a human sees it. Three
things bound that. Conditions 2 and 3 check the two failure modes that actually
harm someone — a wrong licence, a dead link — before anything else. Demotion is
one field write. And because the catalog is republished from a git commit
(§7), the pull request is a second gate: nothing reaches the public site
without a merge, however it was confirmed.

**The alternative that was rejected:** cap the catalog at what stewards can
confirm, and say so in the product. A legitimate answer, and not compatible
with "5,000 to 6,000 grid-relevant datasets" in PRD §0.

---

## 7. How ingestion runs, and who runs it

Not a detail. This decides where harvested records *live*, and the plan does
not work without an answer.

### What happens today

There is no server. The site is a **static export**, built by
`.github/workflows/pages.yml` in a GitHub Actions runner and served by GitHub
Pages. On every push to the default branch the workflow builds a catalog **from
scratch** — `seed load`, `record load`, `index reindex`, `semantic run`,
`links run` — exports a snapshot, and deploys it. The graph, the search index
and the operational SQLite database are created in the runner and thrown away
with it.

So the catalog's real source of truth is not a database. It is
**`data/seed-sources.yaml` plus the JSON-LD record files, in git.** Everything
else is derived, and derived fresh, every deploy.

That is a good architecture for this product and it should be kept. It is also
the thing that decides the next question.

### Where harvested records go

The harvester writes to a database that does not survive the build. Two ways
out, and only one of them fits what is already here:

**A — harvest to git (recommended).** A scheduled workflow runs the harvest,
writes each accepted record as JSON-LD under `data/catalog/<source>/`, and
opens a pull request. Merging it triggers the existing Pages deploy.

- No server, no database, no hosting bill. The current setup is unchanged.
- Every catalog change is a reviewable git diff. A steward's confirmation is a
  committed file, so it survives; today it would not.
- Reproducible: the published catalog is a function of a commit.
- It is also the second gate that makes §6's auto-promotion safe — nothing
  reaches the public site without a merge, however it was confirmed.
- Cost: repository size. 5,000 records at ~15 KB of JSON-LD is ~75 MB, which
  git handles, and the schema metadata from WP-11.4 will roughly double it.
  Worth watching, not worth avoiding.

**B — run a live deployment.** Fuseki, Postgres, OpenSearch and the API, kept
running, with harvest as a server-side cron. This is what `ops/docker-compose.yml`
already describes and what the steward UI, the submission form and the live
API need.

- Everything in the PRD works, including the review queue as an interactive
  work list.
- Cost: infrastructure to operate and pay for, and a second copy of the catalog
  whose relationship to git has to be managed.

**Recommendation: A now, B later if and when a live API is wanted.** They are
not exclusive — B can be added without undoing A, because in A the git tree is
the system of record and a server would just be another consumer of it.

### Answers, in plain terms

**Does somebody have to ask Claude to re-ingest?** No, once the scheduled
workflow exists. Nothing runs on a schedule today — `pages.yml` triggers on
push and on demand, and `docs/hosting.md` already notes that a `schedule:`
block is how you change that. The harvest workflow gets one. Weekly is the
right starting cadence: most of these catalogs change slowly, re-harvest is
idempotent on `sourceId`, and an unchanged payload short-circuits the whole
pipeline — no re-normalise, no re-validate, no model call. Claude is needed to
*change* the pipeline, not to *run* it.

**Who can run it?** Anyone with write access to this repository, plus the
schedule itself. Concretely: **Actions → Harvest → Run workflow**, the same
place the Pages deploy is triggered by hand today. It has nothing to do with
who administers the hosting — GitHub Pages serves whatever the workflow
uploads, and the workflow's permissions come from the repository.

**What if a harvest goes wrong?** It opens a pull request, which is closeable.
Nothing reaches the site until a merge, and a bad merge is one revert. That is
the whole reason to prefer A.

### What this adds to the plan

A work package, sized S, that can be done any time after WP-11.1:

**WP-11.8 — harvest to git.** A `datahub harvest --write-records
data/catalog/` mode, a `.github/workflows/harvest.yml` running weekly and on
demand, and a `data/catalog/` tree loaded by `record load` in `pages.yml`
alongside the seed inventory. Exit criterion: a scheduled run opens a pull
request whose diff is readable, and merging it changes the published catalog.

---

## 8. Executing this with an agent

The plan is written to be run by Claude, one work package per pull request,
which means each package has to state its own done-ness in numbers rather than
in judgement. Four rules make that work:

**Every package carries a measurement command.** `make ingestion-report` (to be
added in WP-11.1) prints the §1 baseline table against the current catalog.
A pull request that claims WP-11.4 attaches the before and after.

**The golden set is the regression gate, not the test suite.**
`data/golden-set/expectations.yaml` states what the system *should* answer for
16 records, including ERA5's four source-confirmed fields and the deliberate
`ro` concept gap. Ingestion changes that alter a golden-set expectation are
wrong until argued otherwise in the pull request, and `tests/semantic/
test_golden_set.py` is the gate. When WP-11.4 takes ERA5 from 4 fields to 273,
those four keep their concepts, their units and their caveats — that is the
assertion.

**No package may weaken a SHACL shape to pass.** The shapes encode the honesty
guarantees. If a shape blocks a record, either the record is wrong or the
mapping is; changing the shape is a third answer that requires its own pull
request and an ADR.

**Bounded blast radius per package.** WP-11.1 touches
`normalizers/`; WP-11.4 adds `harvest/schema/` and touches the runner's stage
list; WP-11.7 touches the projector and the snapshot. No package touches both
breadth and depth.

Suggested order and rough size:

| | Package | Depends on | Size |
|---|---|---|---|
| ✔ | WP-11.1 normaliser hardening + 11.1a gap markers | landed | — |
| 2 | WP-11.4 schema probe | — | L |
| 3 | WP-11.2 enrichment on, budgeted | 11.1a | M |
| ✔ | WP-11.3 auto-promotion | landed with 11.8 | — |
| 5 | WP-11.5 resolution at volume | 11.4 | M |
| 6 | WP-11.7 scale mechanics | 11.3 | M |
| 7 | WP-11.6 source expansion | all | L, incremental |
| ✔ | WP-11.8 harvest to git (§7) | landed | — |

Four landed. **WP-11.4, the schema probe, is now the one that matters most** —
the pipeline can reach and publish records, and what those records do not yet
carry is the field metadata the whole catalog is for. ERA5 publishes 4 fields;
it describes 273 of its own.

---

## 9. Non-goals

- **Not ETL.** PRD §0 scopes ingestion and Tier 2 ETL to DD1/DD5/DD8/DD9 and
  that is unchanged. This plan catalogs; it does not transform.
- **Not hosting.** The Hub stays out of the byte path. A schema probe reads
  bounded metadata and nothing else.
- **Not hand-authoring.** No work package's exit criterion is met by writing
  records by hand. The golden set stays hand-authored and stays at ~16 records;
  it is a specification, not a corpus.
- **Not a quality score.** ADR-0007 stands. Auto-promotion changes who
  confirms a record, not how it is graded.

---

## 10. Reproducing the numbers

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"

export DATAHUB_GRAPH_STORE_PATH=var/site/graph.nq \
       DATAHUB_SEARCH_STORE_PATH=var/site/index.json \
       DATAHUB_DATABASE_URL=sqlite+pysqlite:///var/site/ops.sqlite3

# §1 — the catalog as published
mkdir -p var/site/curated && cp tests/fixtures/records/*.jsonld var/site/curated/
rm var/site/curated/caiso-nodal-lmp-restricted.jsonld \
   var/site/curated/utility-load-shapes-allowlisted.jsonld
.venv/bin/datahub db upgrade && .venv/bin/datahub graph bootstrap
.venv/bin/datahub seed load && .venv/bin/datahub record load var/site/curated
.venv/bin/datahub index reindex

# §3 — a real harvest, and why nothing survives it
git clone --depth 1 https://github.com/awslabs/open-data-registry \
    var/harvest/aws_open_data
.venv/bin/python -m datahub.harvest --source aws_open_data
.venv/bin/python docs/ingestion/spikes/replay_normalize.py

# §4 — what the datasets themselves publish
.venv/bin/python docs/ingestion/spikes/schema_probe.py \
    --targets docs/ingestion/spikes/targets.json
```

The two spike scripts under `docs/ingestion/spikes/` are evidence, not
production code: nothing under `services/` imports them, and they are meant to
be deleted once WP-11.1 and WP-11.4 land.
