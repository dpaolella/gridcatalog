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

### 2. Merge the workflow to the default branch

`.github/workflows/pages.yml` runs on every push to `main` and on demand from
**Actions → Pages → Run workflow**. A workflow file only runs from the default
branch, so it has to be merged before the manual trigger appears.

It builds a catalog from the curated seed inventory (`datahub seed load`,
graded and linked), exports the snapshot, checks it, builds the site, and
deploys.

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
can filter it. That is fine for hundreds of records and wrong for tens of
thousands; past that, serve the live API and let the static site be a
front door rather than the whole catalog.
