"""The ``datahub`` command line.

Every operational task the PRD names as routine is a command here, because a
task that lives only in a runbook is a task that gets done differently each
time. In particular PRD §3.1: *reindex-from-scratch must be a single command
and must be routinely exercised.*

    datahub db upgrade                 # apply migrations
    datahub graph bootstrap            # vocabulary, shapes, entailments
    datahub seed load                  # the 114 curated anchor datasets
    datahub record get <id>            # read a record as JSON-LD
    datahub record validate <file>     # SHACL, at a chosen level
    datahub index reindex              # rebuild the search index from the graph
    datahub search "offshore wind"     # query the index as an anonymous caller
    datahub query run q1               # the semantic layer's named queries
    datahub status                     # what is loaded, indexed and lagging

Commands print human-readable output by default and JSON with ``--json``, so
the same command serves a person reading a terminal and a script parsing it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from datahub.config import get_settings, reset_settings
from datahub.logging import configure_logging

app = typer.Typer(
    name="datahub",
    help="OpenGrid Data Hub operations.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(name="db", help="Operational database.", no_args_is_help=True)
graph_app = typer.Typer(name="graph", help="The RDF store.", no_args_is_help=True)
seed_app = typer.Typer(name="seed", help="The curated seed inventory.", no_args_is_help=True)
record_app = typer.Typer(name="record", help="Catalog records.", no_args_is_help=True)
index_app = typer.Typer(name="index", help="The search index.", no_args_is_help=True)
query_app = typer.Typer(name="query", help="Named semantic queries.", no_args_is_help=True)
harvest_app = typer.Typer(name="harvest", help="Harvest adapters.", no_args_is_help=True)

app.add_typer(db_app)
app.add_typer(graph_app)
app.add_typer(seed_app)
app.add_typer(record_app)
app.add_typer(index_app)
app.add_typer(query_app)
app.add_typer(harvest_app)


def err(message: str) -> None:
    """Diagnostics go to stderr.

    So `datahub record get x > record.jsonld` writes a record or writes
    nothing, and never writes an error message into the file.
    """
    typer.echo(message, err=True)


def _emit(payload: dict[str, Any] | list[Any], text: str, *, as_json: bool) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str) if as_json else text)


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Warnings and errors only.")] = False,
) -> None:
    """Configure logging before any command runs.

    Set on the environment rather than passed, because ``configure_logging``
    reads settings and every module-level ``get_logger`` call has already
    invoked it by import time — so the level has to be in place before the
    first import of anything that logs.
    """
    if verbose or quiet:
        os.environ["DATAHUB_LOG_LEVEL"] = "DEBUG" if verbose else "WARNING"
        reset_settings()
    configure_logging()


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------


def _alembic_config() -> Any:
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    # `stdout` is bound explicitly: alembic's Config takes it as a *default
    # argument*, so it captures whatever sys.stdout was when alembic.config was
    # first imported. Anything that redirects stdout afterwards — a test
    # harness, a server capturing output — gets alembic's output in the wrong
    # place, or a write to a closed stream.
    config = Config(str(root / "ops" / "alembic.ini"), stdout=sys.stdout)
    config.set_main_option("script_location", str(root / "ops" / "migrations"))
    # This process already configured logging; alembic must not replace it.
    config.attributes["configure_logger"] = False
    return config


def _require_schema() -> None:
    """Fail with an instruction rather than a SQL error.

    A command that needs the operational store and finds no schema is the
    normal state of a fresh checkout. Meeting that with a SQLAlchemy traceback
    naming a missing table teaches nothing; naming the command that fixes it
    does.
    """
    from datahub.api.models.base import get_engine
    from sqlalchemy import inspect

    try:
        tables = set(inspect(get_engine()).get_table_names())
    except Exception as exc:
        err(f"cannot reach the operational database: {exc}")
        raise typer.Exit(1) from None
    if "alembic_version" not in tables:
        err("the operational database has no schema — run `datahub db upgrade` first")
        raise typer.Exit(1)


@db_app.command("upgrade")
def db_upgrade(
    revision: Annotated[str, typer.Argument(help="Target revision.")] = "head",
) -> None:
    """Apply migrations."""
    from alembic import command

    command.upgrade(_alembic_config(), revision)
    typer.echo(f"database at {revision}")


@db_app.command("downgrade")
def db_downgrade(revision: Annotated[str, typer.Argument(help="Target revision.")]) -> None:
    """Roll migrations back."""
    from alembic import command

    command.downgrade(_alembic_config(), revision)
    typer.echo(f"database at {revision}")


@db_app.command("current")
def db_current() -> None:
    """Show the applied revision."""
    from alembic import command

    command.current(_alembic_config(), verbose=True)


@db_app.command("create-all")
def db_create_all() -> None:
    """Create every table directly, skipping migrations.

    Development and tests only. It leaves no ``alembic_version`` row, so a
    database created this way cannot later be migrated — which is exactly why
    production uses ``upgrade``.
    """
    from datahub.api.models.base import create_all

    create_all()
    typer.echo("tables created (no migration history — development only)")


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


@graph_app.command("bootstrap")
def graph_bootstrap(
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Load vocabulary and shapes, and materialise entailments."""
    from datahub.graph.loader import bootstrap
    from datahub.graph.store import make_store

    with make_store() as store:
        result = bootstrap(store)
    _emit(
        {
            "files": result.files,
            "triples": result.total_triples,
            "checksum": result.checksum,
            "changed": result.changed,
        },
        result.summary,
        as_json=json_out,
    )


@graph_app.command("materialize")
def graph_materialize(
    force: Annotated[bool, typer.Option("--force", help="Rematerialise even if current.")] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Recompute the inferred graph from the vocabulary."""
    from datahub.graph.reason import materialize, materialize_if_stale
    from datahub.graph.store import make_store

    with make_store() as store:
        result = materialize(store) if force else materialize_if_stale(store)
    if result is None:
        typer.echo("entailments already current")
        return
    _emit(result.counts, result.summary, as_json=json_out)


@graph_app.command("checksum")
def graph_checksum() -> None:
    """Compare the recorded vocabulary checksum with the files on disk.

    A mismatch means the entailed graph was computed from a vocabulary that has
    since changed, and every inference drawn from it is suspect.
    """
    from datahub.graph.loader import recorded_checksum
    from datahub.graph.reason import live_checksum
    from datahub.graph.store import make_store

    with make_store() as store:
        recorded = recorded_checksum(store)
        materialized = live_checksum(store)
    typer.echo(f"vocabulary loaded:   {recorded}")
    typer.echo(f"entailments built from: {materialized}")
    if recorded != materialized:
        err("STALE — run `datahub graph materialize`")
        raise typer.Exit(1)


@graph_app.command("stats")
def graph_stats(json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Triple counts per named graph."""
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.store import make_store

    with make_store() as store:
        counts = {str(name).rsplit("/", 1)[-1]: store.count(name) for name in NamedGraph}
    _emit(
        counts,
        "\n".join(f"{name:<12} {count:>8,}" for name, count in counts.items()),
        as_json=json_out,
    )


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


@seed_app.command("load")
def seed_load(
    limit: Annotated[int | None, typer.Option(help="Load only the first N rows.")] = None,
    skip_validation: Annotated[
        bool, typer.Option("--skip-validation", help="Write without validating.")
    ] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Load the curated seed inventory.

    Idempotent. Unverified rows land in the draft graph and never in the
    catalog, whatever else they carry — the seed file's own instruction.
    """
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store
    from datahub.harvest.seed import load_seed

    with make_store() as store:
        bootstrap(store)
        result = load_seed(RecordStore(store), limit=limit, validate=not skip_validation)
    _emit(
        {
            "total": result.total,
            "confirmed": result.confirmed,
            "drafted": result.drafted,
            "by_domain": result.by_domain,
            "failures": result.failures,
        },
        result.summary,
        as_json=json_out,
    )
    if result.failures:
        raise typer.Exit(1)


@seed_app.command("sources")
def seed_sources(json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List the machine-readable catalogs the harvesters crawl."""
    from datahub.harvest.adapters.curated import CuratedAdapter

    sources = CuratedAdapter().harvest_sources()
    _emit(
        sources,
        "\n".join(f"{s['id']:<24} {s['adapter']:<12} {s.get('endpoint', '')}" for s in sources),
        as_json=json_out,
    )


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


@record_app.command("get")
def record_get(
    dataset_id: Annotated[str, typer.Argument(help="Dataset IRI or slug.")],
    computed: Annotated[
        bool, typer.Option("--computed", help="Include grades and resolutions.")
    ] = False,
) -> None:
    """Print a record as JSON-LD."""
    from datahub.errors import NotFound
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store

    with make_store() as store:
        try:
            document = RecordStore(store).get(dataset_id, include_computed=computed)
        except NotFound:
            err(f"no record for {dataset_id}")
            raise typer.Exit(1) from None
    typer.echo(json.dumps(document, indent=2))


@record_app.command("list")
def record_list(
    draft: Annotated[bool, typer.Option("--draft", help="List drafts instead.")] = False,
    limit: Annotated[int, typer.Option()] = 50,
) -> None:
    """List record IRIs."""
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store

    graph = NamedGraph.DRAFT if draft else NamedGraph.CATALOG
    with make_store() as store:
        records = RecordStore(store)
        ids = records.list_ids(graph=graph, limit=limit)
        total = records.count(graph=graph)
    for dataset_id in ids:
        typer.echo(dataset_id)
    if total > len(ids):
        err(f"... {total - len(ids)} more")


@record_app.command("validate")
def record_validate(
    path: Annotated[Path, typer.Argument(help="A JSON-LD record, or - for stdin.")],
    level: Annotated[int, typer.Option("--level", "-l", min=1, max=3)] = 1,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate a JSON-LD record against the shapes for a completeness level.

    Exits non-zero on violation, so it is usable as a pre-commit hook or a CI
    step on a contributed record.
    """
    from datahub.harvest.validate import ValidationRunner, format_report

    raw = sys.stdin.read() if str(path) == "-" else Path(path).read_text()
    report = ValidationRunner().validate_jsonld(raw, level)
    if json_out:
        typer.echo(
            json.dumps(
                {
                    "conforms": report.conforms,
                    "level": report.target_level,
                    "violations": [v.to_dict() for v in report.violations],
                    "warnings": [v.to_dict() for v in report.warnings],
                },
                indent=2,
                default=str,
            )
        )
    else:
        typer.echo(format_report(report, colour=sys.stdout.isatty()))
    if not report.conforms:
        raise typer.Exit(1)


@record_app.command("put")
def record_put(
    path: Annotated[Path, typer.Argument(help="A JSON-LD record, or - for stdin.")],
    skip_validation: Annotated[bool, typer.Option("--skip-validation")] = False,
) -> None:
    """Write a record into the store."""
    from datahub.errors import ValidationFailed
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store

    raw = sys.stdin.read() if str(path) == "-" else Path(path).read_text()
    document = json.loads(raw)
    with make_store() as store:
        try:
            result = RecordStore(store).put(document, validate=not skip_validation)
        except ValidationFailed as exc:
            err(exc.message)
            for violation in exc.violations:
                err(f"  {violation.focus_node} {violation.path or ''}: {violation.message}")
            raise typer.Exit(1) from None
    typer.echo(
        f"{'created' if result.created else 'updated'} {result.dataset_id} "
        f"in {result.graph_name.rsplit('/', 1)[-1]} "
        f"({result.triples_written} triples)"
    )


@record_app.command("promote")
def record_promote(
    dataset_id: Annotated[str, typer.Argument()],
    reviewed_by: Annotated[str, typer.Option("--reviewed-by", help="Who confirmed it.")],
) -> None:
    """Move a draft record into the catalog.

    Requires a reviewer, because "confirmed" is a claim that a person checked
    the licence and the access path, and an unattributable claim is not one.
    """
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store

    with make_store() as store:
        result = RecordStore(store).promote(dataset_id, reviewed_by=reviewed_by)
    typer.echo(f"promoted {result.dataset_id} to {result.graph_name.rsplit('/', 1)[-1]}")


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


@index_app.command("reindex")
def index_reindex(
    merge: Annotated[bool, typer.Option("--merge", help="Do not clear the index first.")] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Rebuild the search index from the graph.

    PRD §3.1: this is one command and it is meant to be run often. The index is
    derived state; treating it as precious is how it drifts.
    """
    from datahub.api.models.base import get_sessionmaker
    from datahub.api.search.factory import make_search_backend
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store
    from datahub.projector import reindex

    # Entitlement and projector lag both come from the operational store, and a
    # reindex that quietly skipped them would produce an index that looks
    # complete and grants nobody access to a restricted record.
    _require_schema()

    with make_store() as store:
        result = reindex(
            RecordStore(store),
            make_search_backend(),
            session_factory=get_sessionmaker(),
            clear=not merge,
        )
    _emit(
        {
            "total": result.total_records,
            "indexed": result.indexed,
            "skipped_unconfirmed": result.skipped_unconfirmed,
            "errors": result.errors,
            "duration_s": round(result.duration_s, 2),
        },
        result.summary,
        as_json=json_out,
    )
    if result.errors:
        raise typer.Exit(1)


@index_app.command("project")
def index_project(dataset_id: Annotated[str, typer.Argument()]) -> None:
    """Reproject one record."""
    from datahub.api.search.factory import make_search_backend
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store
    from datahub.projector import Projector

    with make_store() as store:
        result = Projector(RecordStore(store), make_search_backend()).project(dataset_id)
    typer.echo(f"indexed {result.indexed}, removed {result.removed}")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@app.command("search")
def search(
    q: Annotated[str, typer.Argument(help="Free-text query.")] = "",
    domain: Annotated[list[str] | None, typer.Option("--domain", help="Filter by DD code.")] = None,
    limit: Annotated[int, typer.Option()] = 10,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Search the index as an anonymous caller sees it.

    Anonymous deliberately: the default view is the one most users get, and a
    CLI that showed a superset would hide exactly the entitlement mistakes it
    should surface.
    """
    from datahub.api.search.backend import Entitlement, SearchRequest
    from datahub.api.search.factory import make_search_backend
    from datahub.namespaces import SCHEME_DATA_DOMAIN

    # `--domain DD5` is what a person types; the index holds the concept IRI.
    domain = [
        d if d.startswith("http") else f"{SCHEME_DATA_DOMAIN}/{d.upper()}" for d in (domain or [])
    ]

    request = SearchRequest(
        q=q or None,
        filters={"data_domain": list(domain)} if domain else {},
        limit=limit,
        entitlement=Entitlement.anonymous(),
    )
    response = make_search_backend().search(request)
    if json_out:
        _emit(
            {
                "total": response.total,
                "hits": [
                    {
                        "id": hit.document.id,
                        "title": hit.document.title,
                        "score": hit.score,
                        "full_metadata": hit.full_metadata,
                    }
                    for hit in response.hits
                ],
            },
            "",
            as_json=True,
        )
        return
    typer.echo(f"{response.total} results")
    for hit in response.hits:
        # A caller who may see that a record exists but not its detail gets the
        # stub, marked as such. Printing the title anyway would leak exactly
        # what the visibility level withholds.
        title = hit.document.title if hit.full_metadata else "(restricted metadata)"
        typer.echo(f"  {hit.score:>6.2f}  {title}")
        typer.echo(f"          {hit.document.id}")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


def _resolve_query(name: str) -> str:
    from datahub.semantic.queries import query_names

    matches = [n for n in query_names() if n == name or n.startswith(f"{name}-")]
    if not matches:
        err(f"no such query: {name}. Try `datahub query list`.")
        raise typer.Exit(1)
    return matches[0]


@query_app.command("list")
def query_list() -> None:
    """The named semantic queries."""
    from datahub.semantic.queries import query_names

    for name in query_names():
        typer.echo(name)


@query_app.command("params")
def query_params(name: Annotated[str, typer.Argument()]) -> None:
    """The placeholders a query needs bound."""
    from datahub.semantic.queries import load, placeholders

    typer.echo(" ".join(placeholders(load(_resolve_query(name)))) or "(none)")


@query_app.command("run")
def query_run(
    name: Annotated[str, typer.Argument(help="A query name, or its q-number.")],
    param: Annotated[
        list[str] | None,
        typer.Option(
            "--param",
            "-p",
            help="Bind a placeholder: -p a=<iri>. Repeatable. See `query params`.",
        ),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    limit: Annotated[int, typer.Option()] = 20,
) -> None:
    """Run a named query against the store.

    Queries that take placeholders say so rather than failing obscurely: an
    unbound placeholder would otherwise surface as a SPARQL parse error naming
    a variable the caller never wrote.
    """
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.store import make_store
    from datahub.semantic.queries import load, placeholders, run
    from rdflib import Literal, URIRef

    resolved = _resolve_query(name)
    bindings: dict[str, Any] = {}
    for pair in param or []:
        if "=" not in pair:
            err(f"--param expects name=value, got {pair!r}")
            raise typer.Exit(2)
        key, _, value = pair.partition("=")
        bindings[key] = URIRef(value) if value.startswith("http") else Literal(value)

    required = set(placeholders(load(resolved)))
    missing = sorted(required - set(bindings))
    if missing:
        err(f"{resolved} needs {', '.join(missing)}. Bind with -p name=value.")
        raise typer.Exit(2)

    with make_store() as store:
        rows = run(
            store,
            resolved,
            bindings,
            graphs=(NamedGraph.CATALOG, NamedGraph.VOCAB, NamedGraph.INFERRED),
        )
    matches = [resolved]
    if json_out:
        _emit([{k: str(v) for k, v in row.items()} for row in rows[:limit]], "", as_json=True)
        return
    typer.echo(f"{matches[0]}: {len(rows)} rows")
    for row in rows[:limit]:
        typer.echo("  " + "  ".join(f"{k}={v}" for k, v in row.items()))


# ---------------------------------------------------------------------------
# harvest
# ---------------------------------------------------------------------------


@harvest_app.command("list")
def harvest_list() -> None:
    """The adapters that can be run."""
    from datahub.harvest.adapters.curated import CuratedAdapter

    typer.echo(f"{'curated':<24} (data/seed-sources.yaml, no network)")
    for source in CuratedAdapter().harvest_sources():
        typer.echo(
            f"{source['id']:<24} {source['adapter']:<14} p{source.get('priority', '?')}  "
            f"~{source.get('scale_estimate', 0)} records"
        )


@harvest_app.command("run")
def harvest_run(
    source: Annotated[
        list[str] | None, typer.Option("--source", "-s", help="Source id. Repeatable.")
    ] = None,
    priority: Annotated[
        int | None, typer.Option("--priority", "-p", help="Run sources at priority N or better.")
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Stop each source after N records.")] = None,
    no_resume: Annotated[bool, typer.Option("--no-resume", help="Ignore the checkpoint.")] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Harvest one or more sources.

    Refuses to run with no selection: the default would be an eleven-source
    crawl of several thousand records against third parties, started by someone
    finding out what the command does.
    """
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store
    from datahub.harvest.runner import run_sources

    if not source and priority is None:
        err("nothing to do: pass --source or --priority (see `datahub harvest list`)")
        raise typer.Exit(2)

    _require_schema()
    with make_store() as store:
        bootstrap(store)
        results = run_sources(
            RecordStore(store),
            source_ids=source,
            limit=limit,
            max_priority=priority,
            resume=not no_resume,
        )

    _emit(
        [
            {
                "source": r.source_id,
                "seen": r.seen,
                "accepted": r.accepted,
                "rejected": r.rejected,
                "unchanged": r.unchanged,
                "queued": r.queued,
                "flagged": r.flagged,
                "conflicted": r.conflicted,
                "errors": r.errors,
            }
            for r in results
        ],
        "\n".join(r.summary for r in results),
        as_json=json_out,
    )
    if all(r.errors for r in results):
        raise typer.Exit(1)


@harvest_app.command("audit")
def harvest_audit(
    source: Annotated[str | None, typer.Option("--source", "-s")] = None,
    stage: Annotated[str | None, typer.Option(help="keyword, vocabulary or llm.")] = None,
    limit: Annotated[int, typer.Option()] = 25,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """The recall audit: what the relevance filter threw away, and why.

    PRD §7.2 requires every rejection to be logged with its reason *so recall
    can be audited* — which only means anything if somebody can read them. This
    is that command. A wrongly excluded dataset is invisible by construction:
    nobody searches for a record that was never created, so this list is the
    only place the mistake can be found.
    """
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    _require_schema()
    with session_scope() as session:
        repos = Repositories(session)
        rates = repos.relevance.rates()
        rejections = [
            {
                "source": row.source_id,
                "stage": row.stage,
                "score": row.score,
                "reason": row.reason,
                "matched": row.matched_terms,
            }
            for row in repos.relevance.rejections(source_id=source, stage=stage, limit=limit)
        ]

    if json_out:
        _emit({"rates": rates, "rejections": rejections}, "", as_json=True)
        return

    for stage_name, counts in sorted(rates.items()):
        total = counts["accepted"] + counts["rejected"]
        share = counts["accepted"] / total if total else 0
        typer.echo(
            f"{stage_name:<12} {counts['accepted']:>6} accepted  "
            f"{counts['rejected']:>6} rejected  ({share:.0%} kept)"
        )
    if rejections:
        typer.echo(f"\nmost recent {len(rejections)} rejections:")
    for row in rejections:
        typer.echo(f"  [{row['source']}/{row['stage']}] {row['reason']}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command("status")
def status(json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """What is loaded, what is indexed, and how far behind the index is.

    One command rather than four, because the question an operator actually has
    is "is this deployment healthy", and answering it from four commands is how
    the fourth gets skipped.
    """
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories
    from datahub.api.search.factory import make_search_backend
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.loader import recorded_checksum
    from datahub.graph.reason import live_checksum
    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store

    settings = get_settings()
    report: dict[str, Any] = {
        "graph_backend": settings.graph_backend,
        "search_backend": settings.search_backend,
    }

    with make_store() as store:
        records = RecordStore(store)
        report["catalog_records"] = records.count(graph=NamedGraph.CATALOG)
        report["draft_records"] = records.count(graph=NamedGraph.DRAFT)
        recorded = recorded_checksum(store)
        report["vocabulary_checksum"] = recorded
        report["entailments_current"] = recorded == live_checksum(store)

    try:
        report["indexed_documents"] = make_search_backend().count()
    except Exception as exc:
        report["indexed_documents"] = None
        report["search_error"] = str(exc)

    try:
        with session_scope() as session:
            repos = Repositories(session)
            report["projector_lag_s"] = repos.projector.lag_seconds()
            report["review_queue"] = repos.review.counts_by_state()
    except Exception as exc:
        report["database_error"] = str(exc)

    if json_out:
        typer.echo(json.dumps(report, indent=2, default=str))
        return
    for key, value in report.items():
        typer.echo(f"{key:<22} {value}")
    if report.get("entailments_current") is False:
        err("entailments are stale — run `datahub graph materialize`")
        raise typer.Exit(1)


@app.command("version")
def version() -> None:
    """Print the package version."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        typer.echo(pkg_version("opengrid-datahub"))
    except PackageNotFoundError:
        typer.echo("0.0.0+unknown")


if __name__ == "__main__":
    app()
