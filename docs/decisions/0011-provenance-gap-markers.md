# ADR-0011: An absent provenance class is a gap marker, not a blank

**Status:** Accepted · **Date:** 2026-09-06 · **Source:** PRD §4.4 (X4), §6, ADR-0005, [ingestion plan §3](../ingestion-plan.md)

## Context

`og:provenanceClass` is required at completeness level 1. It caps the
Provenance quality grade, so the normaliser refuses to guess it: where the
source's own words do not determine a class, none is set
(`engine.py:_classify`). That refusal is correct and is not in question here.

What the refusal cost was not visible until the harvester was run against a
real source for the first time. Of 1,199 records from the AWS Registry of Open
Data, **998 stated nothing this catalog could read as a provenance class.**
Every one of them failed SHACL at level 1 and was flagged, which means:

- the dataset is not in the catalog at all;
- nobody can search for it, find it, or read its licence;
- and the record that *does* exist — with a resolved licence, a working access
  URL, a title, a description and a domain — sits in a draft graph nothing
  publishes from.

After WP-11.1 removed every mechanical blocker, this one field was the *only*
thing standing between 83% of a real source and the catalog.

The two obvious answers are both wrong. **Guessing** a class fabricates a
quality claim, which PRD principle 4 forbids and which is worse than a missing
one because it is indistinguishable from a real one at the point of use.
**Blocking** hides a dataset that the catalog knows plenty about, and hides it
invisibly — nobody searches for a record that was never created, so the cost is
never reported.

## Decision

**A record whose source does not state a provenance class carries an explicit
`og:provenanceGap` with a stated `og:gapReason`, and that satisfies D6.**

This is not a new idea. It is `og:conceptGap` — ADR-0005's third enforcement
layer, PRD crosswalk rule X4 — applied one level up:

| | Field level | Dataset level |
|---|---|---|
| The value | `og:concept` | `og:provenanceClass` |
| No confident answer | `og:conceptGap` + `og:gapReason` | `og:provenanceGap` + `og:gapReason` |
| Enforced at | level 3 | level 1 |
| The rule | never a silent omission | never a silent omission |

Concretely:

1. `og:ProvenanceGapShape` mirrors `og:ConceptGapShape` and reuses
   `og:gapReason`, which is `sh:minCount 1`. **A bare gap is not allowed** — it
   is indistinguishable from not having looked.
2. D6 becomes an `sh:or` on `og:DatasetShape` at level 1: a class from the
   provenance-class scheme, or an explicit gap. Never neither.
3. The normaliser emits the gap where it previously emitted nothing, with a
   reason naming why the class is unset.
4. `Normalizer.level()` treats the gap as satisfying `provenanceClass`, because
   a level calculation that disagreed with the shapes would mint records that
   validate at 2 and are labelled 1.

## What this does and does not claim

**It does not say the data is unprovenanced.** It says *the catalog cannot show
you where these values came from, and we looked.* That is a statement about the
record, not about the dataset — the same distinction `grading/provenance.py`
already draws in its D row: *"A carefully measured dataset with no recorded
upstream is D. That is not a judgement about the measurements; it is a
statement that the catalog cannot show where they came from."*

**It does not soften the Provenance grade.** Silence already grades D and it
still does. A record with a gap marker grades exactly as it would have; what
changes is that it exists to be graded.

**It does not license guessing later.** Enrichment may still draft a
provenance class (it is in `ENRICHABLE_FIELDS`), and when it does the value
carries `og:enrichmentBasis "inferred"` per ADR-0005 and replaces the gap. A
drafted class is a weaker claim than a source-stated one and the record says
so; a gap is weaker still and says that.

## Consequences

**Good.** The catalog can publish what it actually knows. 998 records from one
source stop being invisible. The reason the class is absent becomes a
first-class, queryable fact rather than an absence a reader has to interpret,
which is strictly more information than the previous behaviour and is exactly
what the external-evaluator persona (PRD §2) needs.

**The cost, stated plainly.** More records will carry a gap than a class, at
least until enrichment runs. A catalog where most records say "provenance not
stated" is an honest catalog that looks unimpressive, and that is the right
trade: the alternative was a smaller catalog that looked better by hiding the
same ignorance behind an empty result set.

**Watch for.** If the gap becomes the overwhelming default and nothing ever
closes it, the marker degrades into noise and the D grade stops discriminating.
The mitigation is WP-11.2 — enrichment drafting the class where the source text
supports it — and the metric to watch is the ratio of gaps to classes per
source, which belongs in `make ingestion-report`.

## Alternatives rejected

**An `unstated` concept in the provenance-class scheme.** Simpler — no shape
change — but it puts a non-class into a scheme of real classes, so every query
over the scheme has to special-case it, and `skos:broader` traversal would walk
into it. The scheme describes how data came to exist; "we don't know" is not a
way data comes to exist.

**A boolean `og:provenanceClassUncaptured`, mirroring
`og:upstreamSourceUncaptured`.** Closer to an existing pattern, but that
boolean means something different: `upstreamSourceUncaptured false` is a
*claim* that there is no upstream, which is why it grades differently from
silence. A boolean here could not carry a reason, and a reason is the thing
that distinguishes a gap from a shrug.

**Dropping D6 to level 2.** Would unblock the same records and lose the rule
that every published record says something about its own provenance. The gap
marker keeps the rule and satisfies it honestly.
