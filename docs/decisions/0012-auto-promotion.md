# ADR-0012: Auto-promotion, and git as the system of record

**Status:** Accepted · **Date:** 2026-09-06 · **Source:** PRD §7.6, §0, ADR-0007, [ingestion plan §6–§7](../ingestion-plan.md)

## Context

Two problems that turn out to be one problem.

**Nothing can be published without a person.** PRD §7.6: a steward moves
`og:reviewState` to `confirmed`, per record. Correct as a default, and it does
not scale — 5,000 records at two minutes each is 166 hours before the first one
is visible. That is precisely the headcount constraint PRD §0 claims harvesting
avoids, reintroduced at the review gate. After WP-11.1 and WP-11.1a, one source
alone produces 1,188 publishable records and a review queue nobody can drain.

**Nothing the harvester writes survives.** There is no server. `pages.yml`
rebuilds the catalog from scratch in a runner on every deploy — `seed load`,
`record load`, `index reindex`, `semantic run`, `links run` — and the graph, the
search index and the operational SQLite store are created there and discarded
with the container. A harvester writing to a database is writing to something
nobody keeps.

The second problem supplies the answer to the first.

## Decision

**Harvested records live in git, and a record the pipeline can substantiate on
its own is published without waiting for a person.**

### Git as the system of record

`datahub record export` writes each record as JSON-LD under
`data/catalog/<source>/<slug>.jsonld`; `datahub record load` reads it back.
A scheduled `harvest.yml` runs the pipeline and opens a pull request.

The export is **deterministic** — sorted keys, fixed indent, and a file is
rewritten only when its bytes change — because the diff is the product. A
weekly harvest where twelve records changed must produce a twelve-file diff,
not four thousand reserialised ones, or nobody will read it and the review gate
this buys is theatre.

### Auto-promotion

A third `og:reviewState`, `auto-confirmed`, distinct from `confirmed` and
rendered as such. Granted when **all four** gates pass, each a fact about the
record itself:

| Gate | Refuses when |
|---|---|
| `validates` | the record does not conform at its computed level |
| `licence` | the licence is absent or `LicenseRef-Unreviewed-*` / `-Unstated` |
| `links` | every distribution has been probed and found unreachable |
| `drafted-values` | a model-drafted value sits in a gating field |

Gating fields are licence, reuse permissions, access URLs, distributions,
identifiers and provenance links — the same set the enricher already refuses to
write. A drafted summary or domain filing is fine.

`confirmed` is never overwritten. A human judgement outranks this one, and
restamping it would erase the fact that somebody looked.

## Two deliberate departures from the plan as approved

**No per-source trust list.** An earlier draft gated promotion on marking
sources `trust: high`. Dropped: a hand-maintained list has to be kept correct
forever, and every question it answers is answered better by the record. A
trusted source can publish a dead link; an untrusted one can publish a
well-formed record with a resolved licence.

**The link gate is "not known dead", not "probed live".** The plan said a
distribution must have probed 200 or 206 within 30 days. Taken literally that
blocks the first run entirely — nothing has been probed, and nothing can be
until records exist to probe. So the gate refuses a record whose distributions
have all been probed *and failed*, and passes one never probed. This is weaker,
and it tightens on its own: `harvest.yml` runs the prober before promoting, so
probe history accumulates and a link that dies demotes the record on the next
pass. Stated here rather than buried, because it is a real weakening of a
condition that was signed off in a stronger form.

## Why this is not a shortcut

The catalog already publishes its own confidence: `og:completenessLevel`,
`og:enrichmentBasis`, `og:inferredAssignment`, `og:provenanceGap`, and quality
facets graded separately with no composite score (ADR-0007). An auto-confirmed
level-1 record is not a claim that a human checked it, and with `og:reviewState`
on the record and in the UI, no reader can mistake it for one.

And the pull request is a second gate, free. Nothing reaches the public site
without a merge, however it was confirmed. "Publish anything we can
substantiate" is therefore a default somebody can veto by closing a tab, not a
claim nobody sees. Auto-promotion would be a considerably braver decision
against a live database, and it is worth being explicit that the safety here
comes from the deployment shape rather than from the gates alone.

## Consequences

**Good.** The catalog can grow to the size the harvester can reach. Every
change to it is a reviewable diff with history and blame. A steward's
confirmation is a committed file, so it survives the next build — today it
would not. Demotion is one field write.

**The cost.** A record nobody checked can reach users. The licence and link
gates are aimed at exactly the two failure modes that harm somebody — reusing
data you may not, and following a link to nothing — and the merge gate catches
the rest.

Repository size grows, and the first real export corrected the estimate that
was here. **Median record: 8.1 KB. Mean: 54 KB.** The gap is a long tail — five
NASA collection records carry descriptions of 270 KB to 669 KB, because the AWS
registry's `Description` field is where those collections list every product
they contain in prose. So 5,000 records is ~270 MB rather than the ~75 MB first
estimated, still under a 1 GB warning threshold but no longer comfortably.
Capping an unbounded prose field, with the truncation stated on the record,
belongs in WP-11.7 with the rest of the scale mechanics.

**Watch for.** A pull request nobody reads is the failure mode this design is
most exposed to, because it converts the merge gate into a rubber stamp while
looking exactly the same. The mitigation is that the diff stays small and
legible; if harvest pull requests start arriving with thousands of changed
files, the determinism of the export has broken and that is a bug to fix rather
than noise to accept.

## Alternatives rejected

**A live deployment with a real database.** Fuseki, Postgres and OpenSearch
kept running, harvest as a server-side cron, the steward UI working as
designed. This is what the PRD assumes and it remains the right answer *if and
when* a live API is wanted. It is not needed to grow a read-only catalog, it
costs money and operational attention, and it makes auto-promotion strictly
more dangerous by removing the merge gate. Additive later: in this design git
is the system of record, so a server becomes another consumer of it rather than
a migration.

**Committing the built site instead of the records.** Smaller diffs, and it
throws away the thing that makes this work — records in git can be re-derived,
re-validated and re-indexed by a later pipeline; a rendered page cannot.

**Auto-promoting on validation alone.** Simplest, and it publishes records
whose licence nobody resolved. A catalog entry saying "CC-BY-4.0" when nobody
established the terms is the specific harm PRD §7.4 exists to prevent.
