# Giving someone the catalog inside their Claude

The goal this page exists for: a colleague pastes **one URL** into Claude and
can then ask about the catalog. No install, no config file, no terminal, no
account to create.

That is a *remote MCP connector*, and it needs something running — the static
site cannot answer a protocol handshake. This page is how to run it for a few
dollars a month.

---

## What gets deployed

One container, one process, one port:

| Path | What |
|---|---|
| `/mcp` | streamable-HTTP MCP — the URL a connector points at |
| `/` | the REST API, unchanged |

`services/mcp/asgi.py` composes them. The MCP tools reach the API through an
in-process transport, so PRD §F9's rule that the MCP server is *a thin client
over the REST API* still holds exactly: nothing bypasses a router, an
entitlement check or a rate limit. What is avoided is a second deployment
whose only job would be to forward requests to the first.

The catalog is **baked into the image at build time** (`ops/Dockerfile.mcp`).
A container that scales to zero and back has to answer its first request, not
begin a catalog build.

---

## Setting it up

You need a Fly.io account. Steps 1 and 2 are once, ever.

### 1. Create the app

```bash
fly launch --no-deploy          # reads fly.toml; keep the app name or change it
```

If you change the name, change `app` in `fly.toml` too, and
`DATAHUB_API_URL` under `[env]` to match.

### 2. Give CI a token

```bash
fly tokens create deploy -x 8760h
```

Put it in the repository as **Settings → Secrets and variables → Actions →
New repository secret**, named `FLY_API_TOKEN`.

`.github/workflows/deploy-mcp.yml` then deploys on every push to the default
branch. Until that secret exists the workflow skips itself rather than failing
— a repository nobody has set this up for should not have red builds because
of it.

### 3. Deploy

```bash
fly deploy
```

Or push to the default branch and let the workflow do it.

### 4. Check it

```bash
curl -sS -H 'Accept: application/json, text/event-stream' \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"you","version":"1"}}}' \
     https://<app>.fly.dev/mcp/
```

A `serverInfo` in the reply means it is live. The workflow runs this too, and
fails the deploy if the endpoint does not answer — a deploy that reports
success and serves a broken endpoint is the failure worth catching.

### 5. Hand over the URL

```
https://<app>.fly.dev/mcp
```

In Claude: **Settings → Connectors → Add custom connector**, paste, done.

Also set the repository variable `DATAHUB_PUBLIC_API_URL` to
`https://<app>.fly.dev` so the site's **Connect with AI** and **Developers**
pages show the real address instead of `http://localhost:8000`.

---

## Why this shape

**Why not a free tier.** Hugging Face Spaces and Render both sleep when idle —
48 hours and 15 minutes respectively — and wake in 30 to 50 seconds. Somebody
who consults a catalog weekly meets a cold start *every single time*, and that
is long enough for an MCP client to give up. The deciding factor is the usage
pattern, not the price: a machine that stays up is a few dollars a month and
never does that. `min_machines_running = 1` in `fly.toml` is that decision.

**Why no authentication.** The catalog's anonymous surface is the product. PRD
§2 names the external evaluator — a regulator, an intervenor — as the persona
*most likely to be dropped during implementation and the one that most
differentiates this product*, and requires read-only quality and provenance
inspection to work with no account at all. So the endpoint is public and
adding it is one paste. The tier-gated tool (`author_workflow`) is present for
every caller and refuses per call with a message naming the tier, which is the
behaviour PRD §F9 specifies.

**This is a decision about this deployment, not a property of the code.** A
deployment carrying restricted metadata must not mount this app
unauthenticated: entitlement is resolved from the caller's token, and every
caller here is anonymous. The snapshot tests
(`tests/snapshot/`) assert what an anonymous caller can see; that set is what
this endpoint serves.

**Why 512 MB.** The whole catalog is an in-memory rdflib graph plus a JSON
index. Comfortable at the current size, and the first number to revisit when
the catalog passes a few thousand records.

---

## What a reader gets

Seven tools. `search_datasets`, `get_dataset`, `get_dataset_schema`,
`explain_connection`, `preview_dataset`, `get_access_plan`, and
`author_workflow` (tier 1).

**The server never returns data.** It returns metadata and access plans that
say where the data is and how to read it — the Hub is a control plane and is
never in the byte path. An agent that wants rows is told, in the payload, how
to fetch them itself, and for an anonymous tier-1 dataset it simply can:
ERA5's Zarr store is a public range-readable URL.

### A known limit

Every read tool is capped at 100 KB (PRD §F9, so bulk data cannot flood an
agent's context), and truncation is *reported* rather than silent. ERA5's
schema is 273 fields and comes back as roughly 237 of them with
`truncated: true`. The agent is told the answer is incomplete, which is honest,
but `get_dataset_schema` has no paging so there is currently no way to ask for
the rest. Worth fixing when field counts of this size become normal rather
than exceptional.
