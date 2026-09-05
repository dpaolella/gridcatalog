# The projector

Graph to search index, on commit. PRD §3.1's "write to the graph, read from the
index" made concrete.

```
og:graph/catalog  ─┐
og:graph/vocab    ─┼─ construct.rq ─► build_document ─► SearchDocument ─► index
og:graph/inferred ─┤
og:graph/computed ─┘
```

## Why it reads four graphs

The record alone is not enough to build a useful search document:

| Graph | What it contributes | What is lost without it |
|---|---|---|
| `catalog` | the record | — |
| `vocab` | concept **labels** | an index of IRIs: a concept filter no user can read, and no text match on concept names |
| `inferred` | `og:broaderTransitive` | `concept_iris_expanded`, so a filter on a parent concept matches nothing and Q3's inference stops at the search boundary |
| `computed` | quality grades, resolutions | every record shows as unassessed |

The join across `catalog` and `vocab` in one solution is why scoping uses
`FROM` rather than `GRAPH … UNION GRAPH …`; a union of self-contained patterns
cannot express it, and it fails by returning nothing.

## Two rules that are easy to break

**A record below completeness level 2 is `quality_assessed: False`.** PRD §F5:
such a record shows Provenance and Documentation as "not yet assessed", never
as grade D. Most of the catalog is harvested and sits at level 1, so reporting D
would tell every user that most of the catalog is untraceable when the truth is
that nobody has looked yet. `test_a_level_one_record_is_not_yet_assessed` guards
it.

**An unconfirmed record is removed from the index, not skipped.** A record
demoted back to draft that stays indexed is visible to every anonymous search,
and nothing would surface the mistake.

## Lag

PRD §3.1 calls the projector "the piece most likely to be the source of a 'why
is search stale' bug" and asks for the lag to be instrumented. It is a recorded
fact, not an estimate: `projector_state.last_commit_at` and `last_indexed_at`,
compared against `DATAHUB_PROJECTOR_LAG_BUDGET_S` (default 60 s).

```python
projector.health()  # lag_seconds, healthy, pending, last_full_reindex_at
```

`/readyz` and `datahub index status` both read it.

## Reindexing

```bash
datahub index reindex
```

One command, streaming, and `clear` by default — which is what makes it a
rebuild rather than a merge. Without clearing, a record deleted from the graph
survives in the index indefinitely and the only symptom is a search hit that
404s.

`tests/projector/test_reindex.py` asserts that a rebuild reproduces incremental
projection document for document. If the two ever differ, one path is writing
something the other is not, and since the index is derived the extra one is the
bug.
