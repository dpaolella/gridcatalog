# Operations

## Running it locally, with nothing installed

The default backends are in-process (ADR-0002). No container runtime is needed.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

.venv/bin/datahub vocab load          # vocabularies and shapes into the graph
.venv/bin/datahub seed load           # the curated seed catalog
.venv/bin/datahub index reindex       # build the search index from the graph
.venv/bin/datahub serve               # :8000, OpenAPI at /openapi.json
```

State lands under `var/` and is disposable. `rm -rf var && make demo` gets back
to a known catalog — seeded, indexed, graded and linked.

## The scheduled pass

Two signals go stale without anybody writing anything, so they need a schedule
rather than a hook (PRD §F4.3, and the split is the most likely correctness bug
in the build):

```bash
datahub semantic schedule   # Currency & Maintenance. Daily.
datahub probe run           # Link health. Weekly, and only what is due.
```

`datahub semantic signals` prints the whole classification with the reason for
each, which is the thing to read before adding a signal. Hanging Currency off
the write event would grade an abandoned dataset Current forever, and nothing
would error.

## Running the production shape

```bash
make up          # fuseki, opensearch, postgres, redis, api, web
make down
```

Then point the app at them:

```bash
export DATAHUB_GRAPH_BACKEND=fuseki
export DATAHUB_SEARCH_BACKEND=opensearch
export DATAHUB_DATABASE_URL=postgresql+psycopg://datahub:datahub@localhost:5432/datahub
```

`DATAHUB_QUEUE_BACKEND=celery` is accepted and currently pointless: the JobQueue
protocol and a Celery implementation exist in `services/queue.py`, and no task is
registered against them anywhere. Harvest, reindex, grading and link passes are
run from the CLI. Leave it on the default until that changes.

## Configuration

Every setting is `DATAHUB_`-prefixed and lives in `services/config.py`. Nothing
reads `os.environ` directly. The ones that change behaviour rather than
plumbing:

| Setting | Default | What it decides |
|---|---|---|
| `DATAHUB_GRAPH_BACKEND` | `rdflib` | `fuseki` in production |
| `DATAHUB_SEARCH_BACKEND` | `memory` | `opensearch` in production |
| `DATAHUB_PROJECTOR_LAG_BUDGET_S` | 60 | Above this, the projector reports unhealthy |
| `DATAHUB_CONCEPT_MATCH_THRESHOLD` | 0.82 | Below it, concept resolution emits a gap marker instead of guessing |
| `DATAHUB_PROBE_FAILURE_THRESHOLD` | 3 | Consecutive failures before a distribution is excluded from plans |
| `DATAHUB_ACCESS_PLAN_TTL_S` | 900 | Plan expiry — also the revocation window for a removed allow-list entry |
| `DATAHUB_ENRICHMENT_ENABLED` | false | The LLM enricher is opt-in |
| `DATAHUB_MCP_PAYLOAD_CAP_BYTES` | 102400 | Hard cap so bulk data cannot enter an agent's context |
| `DATAHUB_RATE_LIMIT_AGENT_PER_MIN` | 600 | Agent traffic is several times chattier than human traffic |

## Routine operations

**Reindex from scratch.** Must be routine, not an emergency measure.

```bash
.venv/bin/datahub index reindex --yes
```

The index is derived state. If a fix would be lost by a reindex, the fix landed
in the wrong place (PRD principle 8).

**Recompute the semantic layer.**

```bash
.venv/bin/datahub semantic recompute --all        # drops and rebuilds og:graph/computed
.venv/bin/datahub semantic recompute --currency   # the scheduled self-contained batch
```

The two triggers are genuinely different (PRD §F4.3). Relational signals
recompute when a related record changes. Currency recomputes on a schedule,
because a dataset goes stale from time passing, with no write event to hook.
Running the relational path on a timer would not fix a stale currency grade, and
running the currency path on write would re-grade a dataset because someone
fixed a typo in its description.

**Rematerialise entailments** after any vocabulary change:

```bash
.venv/bin/datahub reason materialize
```

A `skos:broader` edit changes what every existing query returns. Vocabulary
changes go through code review with the conformance suite as the regression gate
(ADR-0001).

**Link-health probing** runs from the queue on the per-distribution cadence:
APIs daily, bulk files weekly, Tier 3 pointers monthly. Probes are HEAD or a
single-byte range request, never a full download.

## Fuseki / TDB2

See [`ops/fuseki/compaction.md`](../ops/fuseki/compaction.md). The short
version: TDB2 appends and does not reclaim on delete, so compaction is
scheduled weekly and always after a full recompute or a vocabulary
regeneration, which are exactly the operations that grow the store. An
uncompacted store degrades quietly rather than failing loudly.

Back up the authored graphs (`AUTHORED_GRAPHS` in `datahub.graph.graphs`).
Do **not** back up `og:graph/inferred` or `og:graph/computed` — they rebuild
from a command, and restoring them from a backup hides the property that makes
them safe to drop.

## Health and metrics

| Endpoint | Reports |
|---|---|
| `/health` | process liveness, and nothing else |
| `/health/ready` | graph, index and database reachable |
| `/health/status` | data state: record counts, projector lag, last reindex |

`/health/ready` is the one to put in front of a load balancer. It reports
`unhealthy` only when the graph is unreachable — without it there is no catalog
— and `degraded` for a missing index or an unreachable database, because search
falls back and reads still work, and pulling an instance for a degraded
dependency turns a partial outage into a total one.

**There is no `/metrics` endpoint yet.** This section previously documented one,
along with `/healthz` and `/readyz`, none of which exist; an operator reaching
for them mid-incident would have lost time. The signals below are what the
instrumentation should expose when it is built, not what it exposes now — do
not build alerts on them yet. Projector lag is available today through
`/health/status` and `datahub index status`.

Metrics worth an alert, once they exist:

- **`datahub_projector_lag_seconds`** — the "why is search stale" signal.
- **`datahub_brokered_to_hosted_ratio`** — PRD §F2 names this the metric that
  matters. A rising hosted count signals drift away from broker-by-default.
- **`datahub_distributions_unreachable`** — by source, so a single failing
  provider is distinguishable from a general problem.
- **`datahub_review_queue_depth`** — by domain, because the queue is sorted by
  inbound link count and a deep queue in one domain is a coverage problem.
- **`datahub_records_by_completeness_level`** — level 1 growing while 2 and 3
  stay flat means harvesting is outrunning curation.

## Failure modes seen before, and what is done about them

**A hosted copy with no refresh owner.** PRD §F2 calls this the documented
failure mode in every comparable project. It is enforced rather than
documented: a hosted distribution with no named owner cannot be published.

**Search drifts from the graph.** Projector lag is a metric; reindex is one
command and is exercised in CI.

**An upstream URL moves.** A stable 3xx auto-updates the stored URL and writes
a revision-history entry. Provenance is never silently rewritten.

**A vocabulary edit changes query results.** The conformance suite is the
regression gate, and the Q1–Q5 suite runs against a seeded store on every
commit.
