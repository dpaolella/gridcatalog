# `web` — the OpenGrid Data Hub UI

Next.js 15 (App Router), React 19, Tailwind 4, `next-intl`.

```bash
npm install
DATAHUB_API_URL=http://localhost:8000 npm run dev     # :3000
```

From the repo root, `make demo` populates a catalog and `make web` starts this.

## What it is allowed to do

It calls the REST API and nothing else — no SPARQL, no store client, no second
copy of any rule the API owns. Entitlement, quality grading and link ranking are
decided server-side. A second implementation here would eventually disagree with
the first, and the one that disagreed would be the one a user was standing
behind when they published a figure.

Every fetch is server-side. A browser holding an API token is a token an XSS bug
exfiltrates, and server rendering also means the first paint is the data rather
than a spinner.

## Three rules the components carry

- **Absent means "not captured".** A missing value renders as those words, never
  as an em dash, a zero or a blank cell — each of which a reader takes as a
  statement about the dataset. `NotCaptured` in `components/EmptyState.tsx`.
- **The three quality facets are never combined.** `QualityBadges` maps over
  facets and renders each; there is no variable in it holding more than one
  grade, so there is nothing to average even by accident (ADR-0007).
- **Every empty state says what happened, not what is missing.** "No fields"
  reads as "this dataset has no columns", which is almost never true.

## Localisation

English only, and every user-facing string is in `src/messages/en.json`; every
date, number, byte size and cadence goes through `src/lib/format.ts`. That is
architectural readiness rather than a second-locale deliverable — and it is much
cheaper now than retrofitted, because retrofitting means finding every string in
every component.

## `DATAHUB_API_URL`

Read from the process environment **at request time**, not through Next's `env`
config option. `env` inlines values into the bundle at build time, which turns
one deployment mistake into a silent one: the container runs, the pages render,
and every request goes to localhost.

## Tests

`npx playwright test` from the repo root, or `make e2e`. The specs are in
`tests/e2e/` and drive a real API and a real UI — mocking either would test the
components against a fiction of the other, which is exactly the class of bug the
suite exists to catch, and did.
