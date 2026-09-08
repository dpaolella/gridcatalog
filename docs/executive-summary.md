# OpenGrid Data Hub — an executive summary

*Written 8 September 2026. Every number below was measured on that date against
the live catalog; they will drift, so re-check before quoting them.*

---

## What it is

The Data Hub is a **catalog of grid-modelling data — not a source of it**. It
tells you what datasets exist, how well each one is described, what you are
permitted to do with it, and where to go and get it. It never hosts, serves or
redistributes the data itself.

That boundary is the most important thing to understand about the project,
because everything else follows from it. We are not competing with EIA or
ENTSO-E to publish data. We are trying to solve the problem that comes *before*
the download: a modeller with a question, facing several plausible datasets, who
needs to know which one actually answers it — and, just as often, needs to find
out that none of them do.

## Who it is for

Three people, in rough order of how well we serve them today.

**The modeller starting a study.** They need wind speeds for a resource
assessment, or a network model for a power-flow case, and they have three
candidates. What they need is not more datasets; it is enough information to
reject two of them quickly. Most of what a catalog is for is *rejection* —
there are ten thousand datasets and one of them is right.

**The analyst who has to answer for the licence.** Can we publish results
derived from this? Can a client use it commercially? Does attribution have to
appear in the report? These are answerable questions that currently cost people
half a day and a lawyer.

**The team lead scoping work.** Does the data for this question exist at all,
in any form we could get? Sometimes the honest answer is no, and knowing that in
week one instead of week six is worth a great deal.

It is **not** for someone who wants to download and analyse data — they should
go to the source. It is not a data warehouse, and it is not an analytics tool.

## What it does

For each dataset it holds a structured description: what it covers in space and
time, how often it updates, who publishes it, under what licence, and how to
reach it. On top of that description sit the things that make it more than a
list of links.

**It says how well described each record is.** A record is marked
*discoverable*, *interpretable*, or *linked*. Discoverable means we know it
exists, where it is and what licence it carries. Interpretable means we can also
tell you what its columns mean. Linked means those columns are tied to a shared
vocabulary, so questions can be asked across datasets. A reader always knows
whether they are looking at a stub or a fully worked record. Nothing is
presented as more complete than it is.

**It distinguishes "the source does not say" from silence.** If a publisher
never stated how their values were produced, the record says so explicitly
rather than leaving a blank that could mean anything.

**It assesses quality on three separate axes and refuses to combine them** —
provenance, documentation, and currency. A dataset can be beautifully documented
and completely untraceable. A single composite score would hide exactly the
distinction a modeller needs, so there is no overall grade and there never will
be.

**It records what a dataset is *not* good for.** The exclusions are the half you
cannot get anywhere else. "This supports capacity expansion but not AC power
flow, because it carries no measured resistance" is the sentence that saves
somebody a fortnight.

**It catalogs data you cannot get.** Twenty-four records describe datasets that
are CEII-designated, commercially licensed, or membership-restricted, each with
a stated reason. The gap is documented rather than looking like absence. This
is deliberate and, as far as I can establish, unusual.

**It warns when two datasets are not independent.** If two apparently separate
inputs both derive from the same reanalysis, an ensemble built on them is
narrower than it looks. The catalog flags the shared origin.

**It is readable by machines as well as people** — a public website, an API, and
an integration that lets an AI assistant query the catalog directly and answer
questions about coverage and licensing conversationally.

## Where the descriptions come from

Two streams.

A **hand-curated inventory** of datasets a grid modeller would actually reach
for, built deliberately and covering all ten of our subject domains. This is 91
of the 306 records, and it is where nearly all the depth lives.

**Automated harvesting** from public data catalogs. Eleven sources are
configured. One — the AWS Registry of Open Data — has actually run, contributing
215 records. Harvested records arrive with far less detail than curated ones,
and the gap is visible in the completeness marking on every one of them.

## How it stays current

A weekly automated harvest re-reads its sources and **opens a proposed change
for review rather than publishing silently**. Merging it republishes the site.

Records that pass a set of mechanical checks — the description validates, the
licence resolves to a real identifier, no access link is known to be dead — are
published without a person reading them. This was a deliberate trade: per-record
human review does not scale past a few hundred datasets, and the alternative was
a catalog capped at what one person can read. The cost is that mistakes reach
users before anyone sees them. Every record carries a marker saying whether a
human confirmed it, so no reader can mistake one for the other.

Access links are re-checked on a schedule, and a dataset whose links have gone
dead is meant to be flagged. **In practice this is barely running** — see below.

## How it compares

I have grouped these by *kind*, because the kinds behave differently and the
distinctions within a kind matter less than the ones between them. Claims about
specific platforms below are limited to what I could check on 8 September 2026;
where I could not check, I say so rather than guess.

**General-purpose repositories** — Zenodo, DataCite, data.gov, the AWS Registry
of Open Data. Vast and unopinionated. They will tell you a dataset exists and
give you a DOI; they have no notion of whether it suits a power-flow study,
because they serve every discipline equally. Our 215 harvested records came from
one of these, and the experience is instructive: of roughly 1,200 entries in the
AWS registry, most are genomics, satellite imagery and machine-learning corpora.
Breadth of that kind is not a virtue for us.

**Energy-sector catalogs** — DOE's Open Energy Data Initiative, the NREL Data
Catalog, the Open Energy Platform, Open Power System Data, PUDL. These are the
real comparators and several are excellent. Two observations I could verify:
Open Power System Data, long a reference point for European power-system data,
carries package dates from 2020 with a single 2023 refresh, which suggests it is
largely dormant. The Open Energy Platform is live and actively developed. I did
not verify the current scale or feature set of the DOE and NREL catalogs and
would not want the leader quoting me on them.

**Operator and agency portals** — ENTSO-E Transparency, EIA, ENTSOG, national
TSOs. Authoritative, single-source, and definitionally not catalogs: they
publish their own data superbly and say nothing about anyone else's. A modeller
still has to know they exist and which of their many series is the right one.

**Curated analytical products** — Ember, Global Energy Monitor, IRENA, IEA.
These are publishers, not catalogs. They are competitors for attention and
complements in substance: we want to catalog them well.

**Search layers** — Google Dataset Search, re3data. Broad indexes over
everyone's metadata, with the same problem as the general repositories: they
inherit whatever the publisher wrote and cannot assess it.

**Where we differ, if we do.** Not on count — every comparator in the first
group wins on count, permanently, and we should stop treating that as the
scoreboard. The difference is that we are opinionated about fitness and explicit
about gaps: completeness marking, separate quality facets with no composite,
stated unsuitability, documented unobtainable datasets, and shared-origin
warnings. I am not aware of another catalog doing all of these, though I have
not audited the field closely enough to claim it is unique.

## Where it is weak

This is the section I would read first.

**It is not yet a grid catalog.** It is a weather and geospatial catalog with
some grid data in it. Of 306 records, 182 are renewable resource and weather and
95 are geospatial and siting. The domains a planner actually needs are thin:

| domain | records |
|---|---|
| Renewable resource & weather | 182 |
| Geospatial & siting | 95 |
| Network topology & parameters | 15 |
| Cost & financial | 12 |
| Generator fleet | 10 |
| Policy & regulatory | 10 |
| Interconnection queue, load & demand, fuel, emerging technology | fewer still |

Automated harvesting made this skew *worse*, not better, and the reason is
structural: the sources a harvester can reach without credentials are dominated
by earth observation. The data a planner most wants — interconnection queues,
generator-level operations, load by planning area, network parameters — sits
behind portals that require registration, agreement to terms, or scraping.
**Closing that gap is not a harvesting problem. It is a negotiating and
engineering problem, and it is the single most valuable thing we could do.**

**Nine records in ten carry no field-level description.** Only 31 of 306 have
any description of their columns, 264 fields in total. The schema view — the
thing that most directly answers "does this dataset contain what I need" — is
empty for most of the catalog.

**Quality assessment is therefore mostly blank.** Grading needs field-level
detail, so the differentiator we would most like to lead with is unpopulated for
the great majority of records.

**Link checking is barely running.** Eleven access links have been verified
against 4,735 never checked. We promise to tell a user whether a download still
works, and today, usually, we cannot.

**Almost nothing has been read by a person.** That is the deliberate trade
described above, and it is worth restating as a risk rather than a feature.

**Relevance filtering is imperfect in both directions.** ArcticDEM — an
elevation model of the Arctic — was published because elevation data is useful
for siting in general. There is now a mechanism that catches that class of
mistake by asking whether energy researchers actually cite a dataset, and it
will not catch all of them.

**The public website carries the whole catalog inside the page.** That is fine
at this size and will not survive much growth; it already forced one round of
work when the catalog quadrupled.

**One source of eleven has ever run.** The breadth the system was designed for
is not demonstrated, only architected.

## What I would want you to take from this

The catalog works. The pipeline runs end to end, the site publishes, and the
distinctive ideas — completeness marking, honest gaps, separate quality facets,
documented unobtainable data — are built and visible rather than aspirational.

But it is currently strongest in exactly the areas where it is least needed.
Nobody lacks a way to find ERA5. What people lack is a trustworthy account of
interconnection queue data, generator-level operations, and network parameters —
and those are precisely the domains where we hold ten or fifteen records.

If I had to name one strategic choice it would be this: **depth on the datasets
planners actually use will be worth more than breadth across everything
harvestable.** A hundred records of the interconnection-queue and load kind,
each described down to the column with its licence properly read and its
unsuitabilities stated, would be a more defensible product than ten thousand
harvested stubs. It would also play to the only durable advantage available
here, which is judgement about fitness — something a general-purpose repository
cannot replicate at any scale.

The second thing I would flag is that several of the promises the product makes
are currently unfunded by data: link health, quality grading, and field-level
description are all built, all visible in the interface, and all mostly empty.
That is a worse position than not having built them, because a user who checks
one and finds nothing learns to stop checking. Filling them matters more than
adding features.
