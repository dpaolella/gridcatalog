# Hosting the catalog on GitHub Pages

The web UI can be built two ways from one source tree.

| | **Server** | **Static** |
|---|---|---|
| How | `npm run build && npm start` | `DATAHUB_SNAPSHOT=… npm run build` |
| Renders | per request, against a live API | once, at build time, from JSON on disk |
| Needs | a Node process and a reachable API | a web server that returns files |
| Search | the API's scoring function over the index | token matching, in the reader's browser |
| Writes | issue reports, dataset submissions, the steward queue | none — the pages say so |
| Sees | whatever the caller is entitled to | only what an anonymous caller can see |

The static build is what goes on GitHub Pages. It is a real copy of the
catalog, not a marketing page: every record, schema, grade, coverage window and
connection is the API's own output, exported by driving the app in-process.

---

## What is published, and why that is safe

`datahub snapshot export` starts the real FastAPI application and makes every
request **without credentials**. Whatever an anonymous caller cannot see never
reaches the disk, and there is no flag to change that.

That is a structural guarantee rather than a checked one. A snapshot written by
reading the store directly would need an entitlement filter of its own, and a
second implementation of a rule is a second chance to get it wrong — here, in
the one place where getting it wrong is irreversible, because a file that has
been served has been copied.

The three cells of the entitlement matrix (PRD §F7) land like this:

| Visibility | In the snapshot |
|---|---|
| Public existence, public metadata | The full record and every detail file |
| Public existence, restricted metadata | A stub: title and domain, no detail files |
| Allow-listed existence | **Nothing.** No entry, no directory, no page, and the identifier appears in no file |

`tests/snapshot/` asserts all of this against the exported tree, and the Pages
workflow runs those tests **before** the upload.

The site also carries no session, no token and no way to obtain one. Sign-in,
the steward review queue, issue reports and dataset submissions all need the
live deployment; in the static build those pages render a notice saying so
rather than a form that silently fails.

---

## Publishing it

### 1. Turn on Pages

In the repository: **Settings → Pages → Build and deployment → Source →
GitHub Actions**. Not "Deploy from a branch" — the workflow uploads an
artifact and Pages serves it, so there is no `gh-pages` branch to keep.

### 2. Get the workflow onto the default branch

`.github/workflows/pages.yml` deploys on every push to the repository's
**default branch**, whichever branch that is, and on demand from **Actions →
Pages → Run workflow**. GitHub reads workflow files from the default branch
only, so nothing runs — and the manual trigger does not appear — until it is
there.

The branch is not named in the trigger. The job carries
`if: github.ref_name == github.event.repository.default_branch` instead, so a
push to any other branch produces a skipped run and a rename of the default
branch does not silently stop the deploys. Hardcoding `main` has to be right
twice, and when it is wrong the workflow simply never runs.

It builds a catalog — the seed inventory for breadth, the golden set for depth
— exports the snapshot, checks it, builds the site, and deploys.

### 3. Wait for the first run, then read the URL

The deploy job prints the published URL. For a project site it is
`https://<owner>.github.io/<repo>/`.

### Optional: point readers at a live API

The **Connect with AI** and **Developers** pages tell a reader where to point
their MCP client and SDK. With nothing configured they show
`http://localhost:8000`, which is correct for somebody following the
quickstart on their own machine.

If you run a public API, set a repository variable — **Settings → Secrets and
variables → Actions → Variables → New repository variable** — named
`DATAHUB_PUBLIC_API_URL`, and the next build will use it.

It is a variable and not a secret on purpose: it is printed on the page, so
pretending it is confidential would only make it harder to change.

---

## The base path

A project site is served from a subdirectory — `<owner>.github.io/<repo>/` —
and every absolute URL the app emits has to carry that prefix. Getting it wrong
is the classic Pages failure: the HTML loads and every stylesheet, font and
link 404s.

The workflow works it out from the repository name:

- `<owner>/<repo>` → `NEXT_PUBLIC_BASE_PATH=/<repo>`
- `<owner>/<owner>.github.io` → empty, because a user or organisation site is
  served from the root

**A custom domain is also served from the root.** If you set one under
**Settings → Pages → Custom domain**, set `NEXT_PUBLIC_BASE_PATH` to the empty
string in `pages.yml` — the automatic rule cannot see your DNS. Add the domain
to `web/public/CNAME` as well, so it survives each deploy.

---

## Building and checking it locally

```bash
make demo                 # a populated local catalog, from nothing
make site BASE_PATH=/gridcatalog
make site-serve BASE_PATH=/gridcatalog
```

Then open `http://localhost:4321/gridcatalog/`.

`make site-serve` is deliberately not `serve -s` or any other SPA server.
Those fall back to `index.html` for every miss, so a page that was never
exported still renders — wearing the wrong URL — and a broken link looks fine
until it is deployed. `web/scripts/serve-export.mjs` does what Pages does: the
file, or `<path>/index.html`, or `404.html` with a 404 status.

---

## Things that are easy to get wrong

**`.nojekyll`.** `web/public/.nojekyll` is an empty file that ships with the
export. Without it Pages runs the artifact through Jekyll, which drops every
path beginning with an underscore — including `_next/`, which is all the
JavaScript and every font. The symptom is an unstyled page that never
hydrates. It is empty and cannot explain itself, which is why it is explained
here, in `pages.yml`, and asserted by a build step.

**`trailingSlash`.** Pages serves `/thing/index.html` for `/thing/` and does
not redirect `/thing` to `/thing/`. The static build sets `trailingSlash: true`
so every emitted link already has one.

**Stale snapshots.** The site is only as current as its last build. If the
catalog changes on a schedule, add one to `pages.yml`:

```yaml
on:
  schedule:
    - cron: "0 6 * * *"
```

**Size.** The whole public catalog ships inside the search page so the browser
can filter it. Measured rather than guessed: the search index is **1,382 bytes
per record raw and 114 bytes gzipped** — it is a flat list of short strings, so
it compresses about 12×.

| Records | Index, raw | Index, over the wire |
|---|---|---|
| 66 (today) | 91 KB | 7 KB |
| 1,000 | 1.4 MB | 0.11 MB |
| 5,000 | 6.9 MB | 0.57 MB |
| 20,000 | 27.6 MB | 2.3 MB |

So the browser-side search is comfortable to about 20,000 records, not the
"hundreds" this file used to claim. What gets uncomfortable first is elsewhere —
see the next section.

---

## Where this can run, and what it costs

The question that decides the rest: **does the catalog need a server?**

Read-only discovery does not. Search, dataset pages, schemas, grades, coverage
and connections are all served from the static export. What needs a live
deployment is the short list in the table at the top of this file: sign-in, the
steward review queue, issue reports, dataset submissions, and a REST endpoint
for SDK and MCP users to point at.

### The options

| | Cost | You operate | Live API | Where it stops |
|---|---|---|---|---|
| **GitHub Actions + repo + Pages** | $0 | nothing | no | ~20k records; 1 GB site limit |
| Cloudflare Pages | $0 | nothing | no | **20,000 files per deploy** — reached at ~4,000 records |
| Hugging Face Space, free tier | $0 | a container | yes, but it **sleeps** after inactivity and cold-starts | fine |
| Hugging Face Space, upgraded + persistent disk | ~$20–30/mo (verify) | a container | yes | fine |
| Fly.io / Railway / a small VPS | ~$5/mo minimal, ~$20–40/mo for the full designed stack | a real deployment | yes | fine |
| Hugging Face **Datasets** (storage only) | $0 | nothing | n/a | very large |

Prices are approximate and from memory rather than checked — verify before
committing to one.

### What is actually recommended

**Stay on GitHub, and add WP-11.8's scheduled harvest.** Not as a compromise:
for a read-mostly catalog it is the better architecture, and the reasons are
specific rather than budgetary.

- The repository is public, so **Actions minutes and Pages are both free** and
  stay free.
- The catalog is already rebuilt from scratch on every deploy, so git is
  already the system of record. Committing harvested records makes that
  explicit rather than adding anything.
- Every catalog change becomes a **reviewable diff**, and a bad harvest is one
  closed pull request or one revert. No other option on this list gives that.
- A steward's confirmation survives, because it is a committed file. On any
  ephemeral-database option it does not.
- 5,000 records is roughly 75 MB of JSON-LD plus history, against a 1 GB
  soft warning and a 5 GB limit.

**What breaks first, and it is not the host.** Five snapshot files per record
means ~25,000 files at 5,000 records. The GitHub Pages artefact handles that,
slowly; Cloudflare Pages does not, which is the one hard number that rules an
option out. The fix — bundling detail records rather than emitting a file each
— is WP-11.7 and is the same work whichever host is underneath.

### When to add a server, and which one

Add one when you want the live API, the steward UI, or public submissions —
not before, and not to solve a scaling problem the static build does not have.

At that point the honest comparison is between **Hugging Face Spaces** and a
small always-on host like Fly.io. Spaces is attractive if the audience is
already there and if a cold start on the first request is acceptable; the free
tier sleeps, so a public catalog whose first visitor waits 30 seconds is the
thing to check before choosing it. Persistent storage on a Space is a paid
add-on, which matters because the operational store is a real database. Fly.io
or a VPS is more predictable for something meant to stay up, and
`ops/docker-compose.yml` already describes the full stack.

Either way it is **additive**. In the recommended setup the git tree is the
system of record, so a server is another consumer of it rather than a
migration.

### Worth doing regardless: publish the catalog as a Hugging Face dataset

Orthogonal to hosting, free, and aimed at the audience that would use it. A
`datahub snapshot export` tree is already the right shape to push as an HF
dataset, and it makes the catalog loadable in one line for exactly the
energy-modelling and ML people this is for. That is a distribution channel, not
a hosting decision, and it does not compete with anything above.
