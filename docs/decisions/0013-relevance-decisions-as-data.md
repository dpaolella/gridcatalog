# ADR-0013: The relevance classifier is a committed file, not a model call

**Status:** Accepted · **Date:** 2026-09-07 · **Source:** PRD §7.2, ADR-0012, [#44](https://github.com/dpaolella/gridcatalog/issues/44), [#31](https://github.com/dpaolella/gridcatalog/issues/31)

## Context

PRD §7.2 specifies a two-stage relevance filter: keyword and vocabulary matching
first, then *"an LLM relevance classifier on the ambiguous middle."* The middle
was built as a runtime dependency — configure a model, configure a key, and
every harvest calls out once per record — behind the `enrichment_enabled`
switch, which is off by default.

Nothing was ever wired to it. `RelevanceFilter` was constructed with no
classifier, and `_classify` took its "no classifier configured" branch, which
**accepts**. That branch was correct in isolation and its comment says why:

> Every path that does not reach a working classifier **accepts**. An
> unavailable third party must never quietly start shrinking the catalog.

Measured over the 353 harvested records in `data/catalog/yaml_repo/`: 131 were
accepted on a real grid signal and **185 — 52% — were accepted because nothing
could say no.** The catalog published the Human Cell Atlas, Tabula Sapiens, a
marmoset connectivity study, a protein-ligand binding set and a multiple-choice
question corpus, each tagged *Renewable resource & weather*.

ADR-0012 removed the human who was supposed to catch this. The filter's whole
justification is an asymmetry stated at the top of `relevance.py` — *"a wrongly
included dataset costs a steward thirty seconds in the review queue, and the
queue exists anyway"* — and auto-promotion made "included for review" mean
"published".

## Decision

**The third stage reads a committed decision file.** `data/relevance-decisions.yaml`
maps a harvest `source_id` to a boolean and a reason.
`decisions.StaticClassifier` implements the existing `Classifier` protocol
against it, and is now the default `RelevanceFilter` builds when no classifier
is given.

Three supporting changes, each of which was independently a defect:

1. **The classifier is no longer gated on `enrichment_enabled`.** That switch
   governs model calls and has a cost and a vendor behind it. A file lookup has
   neither, and coupling them meant the specified third stage was dead in every
   deployment.
2. **Only *subject* vocabularies feed the filter.** `_vocabulary_phrases`
   globbed every `.ttl`, so 41 labels from `og-access-restriction` and
   `og-provenance-class` — "open access", "data use agreement",
   "crowd-sourced", "bias-corrected" — scored as evidence a dataset is about
   power systems. 15 published records reached the ambiguous middle on nothing
   else. `SUBJECT_SCHEMES` is an allow-list, so adding a vocabulary is no
   longer a silent widening.
3. **`Undecided` is distinct from "not relevant".** A record the file has never
   seen is accepted, and the reason says it was undecided rather than judged.

## Why data rather than a call

Relevance is not a computation the pipeline needs to redo. It is a fact about a
dataset, true until the dataset changes, and ADR-0012 already makes git the
system of record for exactly that kind of fact.

* **It is reviewable.** A verdict arrives in the harvest pull request next to
  the record it admits or excludes. The alternative happened inside a build
  container and left a log line.
* **It is stable.** The same 1,199 registry entries produce the same catalog on
  every build, rather than depending on what a model said that morning.
* **It costs nothing to run.** No key, no budget, no vendor, and the second
  stage works in CI, in a fork, and on a laptop with no network. The reason
  this ADR exists at all is that a key was a prerequisite nobody had met, for
  months, while the catalog filled with cell biology.
* **It cannot fail open.** An unreachable API became "accept everything". A
  missing file entry becomes "undecided", which is recorded as such.

## Consequences

**A record the file has not seen is still accepted.** That is deliberate — the
recall argument is unchanged and a dataset excluded by an omission is invisible
— but it means coverage matters. `test_the_file_decides_the_ambiguous_middle_and_only_that`
fails when an ambiguous record has no decision, so a new source arrives with the
gap visible rather than as a quietly growing catalog.

**The decisions are one agent's judgement, recorded once.** No one has
re-checked them by hand. Two lines were drawn deliberately and are stated in the
file's header: derived physical quantities count as siting inputs while raw
sensor archives do not, and operational space-weather forecasting counts while
heliophysics research does not. Both are arguable, and disagreeing means editing
one line with a diff that says what changed.

**The runtime classifier is not ruled out.** A source too large or too fast to
decide by hand can supply one through the same protocol; it reports the same
`decided` stage and names itself in `model`. What changes is that the *default*
is free and the fallback is honest.

**Effect on the AWS registry**, all 1,199 entries: 527 accepted before, 258
after. Of the rest, 704 are refused by the keyword stage, 237 by a recorded
decision.

## Alternatives considered

**Ship the key and call the model.** The original design. It makes the catalog's
contents depend on a credential, a budget and a third party's uptime, re-decides
1,199 fixed records on every run, and leaves no artifact anybody can review. For
a bounded, enumerable set of datasets, it is the wrong shape.

**A deny-list of off-topic slugs.** Smaller, and it inverts the failure mode the
filter is built around: an omission would exclude rather than include, silently.

**Raise `REJECT_BELOW` until the junk falls out.** Tried on paper. The junk
scores 0.35 and so do `csiro-cafe60`, `euro-cordex` and `emearth`, which are
reanalysis products that genuinely feed energy models. There is no threshold
that separates them, which is the reason PRD §7.2 asked for a classifier.
