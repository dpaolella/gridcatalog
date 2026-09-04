"""``python -m datahub.harvest --source oedi --limit 100``.

PRD §7.1 requires each adapter to be independently runnable, and this is that
requirement's literal form. It exists alongside ``datahub harvest`` in the main
CLI because the two answer different needs: the CLI is what an operator uses,
and this is what a cron entry, a container command and a developer debugging one
source use — no click context, no shared state, one thing.

    python -m datahub.harvest --list
    python -m datahub.harvest --source oedi --limit 100
    python -m datahub.harvest --priority 1        # every priority-1 source
    python -m datahub.harvest --all --dry-run     # what would run, and how much
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from datahub.config import get_settings
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore
from datahub.graph.store import make_store
from datahub.harvest.runner import harvest_sources, run_sources
from datahub.logging import configure_logging, get_logger

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m datahub.harvest",
        description="Run one or more harvest sources.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        metavar="ID",
        help="A source id from data/seed-sources.yaml. Repeatable.",
    )
    parser.add_argument("--all", action="store_true", help="Run every source.")
    parser.add_argument(
        "--priority",
        type=int,
        metavar="N",
        help="Run sources at priority N or better. Priority 1 is the high-value set.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Stop each source after N records. For a smoke test against a real source.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore the stored checkpoint and start from the beginning.",
    )
    parser.add_argument("--list", action="store_true", help="List the sources and exit.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Say what would run, without contacting anything.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    settings = get_settings()
    sources = harvest_sources(settings)

    if args.list or args.dry_run:
        return _describe(args, sources)

    if not (args.sources or args.all or args.priority):
        # Refusing beats defaulting to everything: the default would be an
        # eleven-source crawl of several thousand records against third
        # parties, started by someone who typed the command to see what it did.
        print("nothing to do: pass --source, --priority or --all", file=sys.stderr)
        return 2

    with make_store(settings) as store:
        bootstrap(store)
        results = run_sources(
            RecordStore(store, settings),
            source_ids=args.sources,
            limit=args.limit,
            settings=settings,
            max_priority=args.priority,
            resume=not args.no_resume,
        )

    if args.as_json:
        print(json.dumps([_row(r) for r in results], indent=2, default=str))
    else:
        for result in results:
            print(result.summary)

    # Non-zero when every source failed, zero when some worked: a partial
    # harvest is a success with a warning, and a cron job that alerts on every
    # transient source failure is a cron job nobody reads.
    return 0 if any(not r.errors for r in results) else 1


def _describe(args: argparse.Namespace, sources: list[dict[str, Any]]) -> int:
    wanted = set(args.sources or [])
    rows = [
        source
        for source in sources
        if (not wanted or source.get("id") in wanted)
        and (args.priority is None or int(source.get("priority", 9)) <= args.priority)
    ]
    if args.as_json:
        print(json.dumps(rows, indent=2))
        return 0
    total = 0
    for source in sorted(rows, key=lambda s: int(s.get("priority", 9))):
        scale = int(source.get("scale_estimate", 0))
        total += scale
        print(
            f"p{source.get('priority', '?')}  {source['id']:<22} {source['adapter']:<14} "
            f"~{scale:>5} records  {source.get('endpoint', '')}"
        )
    if rows:
        print(f"\n{len(rows)} sources, roughly {total:,} records before filtering")
    return 0


def _row(result: Any) -> dict[str, Any]:
    return {
        "source": result.source_id,
        "seen": result.seen,
        "accepted": result.accepted,
        "rejected": result.rejected,
        "unchanged": result.unchanged,
        "created": result.created,
        "updated": result.updated,
        "queued": result.queued,
        "flagged": result.flagged,
        "conflicted": result.conflicted,
        "enriched": result.enriched,
        "errors": result.errors,
        "duration_s": round(result.duration_s, 2),
    }


if __name__ == "__main__":
    raise SystemExit(main())
