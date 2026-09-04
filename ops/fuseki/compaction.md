# TDB2 operations

TDB2 does not reclaim space on delete; it appends. An uncompacted store
degrades quietly rather than failing loudly (ADR-0001), so compaction is
scheduled, not reactive.

## Compaction

```bash
curl -u admin:$FUSEKI_PASSWORD -XPOST \
  "http://fuseki:3030/$/compact/datahub?deleteOld=true"
```

Run weekly, and always after a full recompute of `og:graph/computed` or a
vocabulary regeneration of `og:graph/inferred` — those drop and rebuild whole
graphs, which is exactly the pattern that grows a TDB2 store.

Cron entry (`ops/fuseki/crontab`):

```
0 4 * * 0 curl -sS -u admin:$FUSEKI_PASSWORD -XPOST "http://fuseki:3030/$/compact/datahub?deleteOld=true"
```

## Backup

```bash
curl -u admin:$FUSEKI_PASSWORD -XPOST "http://fuseki:3030/$/backup/datahub"
```

Backups land in `/fuseki/backups` as gzipped N-Quads. Only the authored graphs
need to survive a restore (`AUTHORED_GRAPHS` in `datahub.graph.graphs`); the
inferred and computed graphs are rebuilt by `datahub reason materialize` and
`datahub semantic recompute --all`. Restoring derived graphs from a backup is a
mistake: it hides the fact that they can be rebuilt, which is the property that
makes them safe to drop.

## Restore

```bash
curl -u admin:$FUSEKI_PASSWORD -XPOST \
  --data-binary @backup.nq.gz -H 'Content-Type: application/n-quads' \
  "http://fuseki:3030/datahub/data"
```

## Upgrade

TDB2 on-disk format is stable across 5.x. Across a major version, back up,
recreate the dataset, and reload from the backup — never upgrade in place.
