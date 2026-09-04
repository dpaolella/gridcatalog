"""The command line (WP-2.6).

Every operational task the PRD calls routine is a command, and a command with
no test is a command that breaks the first time somebody renames a method it
calls — which is how this suite found four such renames.

Exit codes are asserted as carefully as output: the CLI is meant to be usable
in CI, and a validator that prints violations and exits 0 is worse than no
validator.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.cli import app

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """Point the CLI at a temporary store and database."""
    from datahub.api.models.base import reset_engine
    from datahub.config import reset_settings

    monkeypatch.setenv("DATAHUB_GRAPH_STORE_PATH", str(tmp_path / "graph.nq"))
    monkeypatch.setenv("DATAHUB_SEARCH_STORE_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("DATAHUB_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/ops.sqlite3")
    reset_settings()
    reset_engine()
    yield tmp_path
    reset_engine()
    reset_settings()


@pytest.fixture
def bootstrapped(runner, store_env):
    """A store with the vocabulary loaded and the operational schema applied.

    Both, because the projector reads entitlement and writes lag, so an
    unmigrated database makes a reindex fail — deliberately, rather than
    producing an index that looks complete and entitles nobody.
    """
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    result = runner.invoke(app, ["graph", "bootstrap"])
    assert result.exit_code == 0, result.output
    return store_env


# ---- the commands exist and are discoverable ----------------------------


def test_help_lists_every_command_group(runner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in ("db", "graph", "seed", "record", "index", "query", "harvest", "status"):
        assert group in result.output


@pytest.mark.parametrize(
    "command",
    [
        ["db", "--help"],
        ["graph", "--help"],
        ["seed", "--help"],
        ["record", "--help"],
        ["index", "--help"],
        ["query", "--help"],
        ["harvest", "--help"],
        ["search", "--help"],
        ["status", "--help"],
    ],
)
def test_every_group_has_usable_help(runner, command: list[str]) -> None:
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output


def test_version_prints_something(runner) -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip()


# ---- graph ---------------------------------------------------------------


def test_bootstrap_loads_the_vocabulary(runner, store_env) -> None:
    result = runner.invoke(app, ["graph", "bootstrap", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["triples"] > 3000
    assert payload["checksum"]


def test_stats_reports_every_named_graph(runner, bootstrapped) -> None:
    result = runner.invoke(app, ["graph", "stats", "--json"])
    assert result.exit_code == 0
    counts = json.loads(result.stdout)
    assert counts["vocab"] > 0
    assert counts["shapes"] > 0
    assert set(counts) >= {"catalog", "draft", "vocab", "inferred", "computed", "shapes"}


def test_checksum_agrees_after_a_bootstrap(runner, bootstrapped) -> None:
    """A mismatch means the entailed graph was built from a vocabulary that has
    since changed, and every inference drawn from it is suspect — so the
    command exits non-zero rather than merely printing."""
    result = runner.invoke(app, ["graph", "checksum"])
    assert result.exit_code == 0, result.output
    assert "STALE" not in result.output


# ---- seed ----------------------------------------------------------------


def test_seed_load_respects_a_limit(runner, bootstrapped) -> None:
    result = runner.invoke(app, ["seed", "load", "--limit", "6", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["total"] == 6
    assert payload["failures"] == []


def test_seed_sources_lists_the_harvest_registry(runner, store_env) -> None:
    result = runner.invoke(app, ["seed", "sources", "--json"])
    assert result.exit_code == 0
    assert len(json.loads(result.stdout)) == 11


# ---- record --------------------------------------------------------------


def test_validate_accepts_a_conforming_record(runner, store_env) -> None:
    result = runner.invoke(
        app, ["record", "validate", str(FIXTURES / "records" / "ecmwf-era5.jsonld"), "-l", "3"]
    )
    assert result.exit_code == 0, result.output
    assert "conforms" in result.output


def test_validate_exits_non_zero_on_a_violation(runner, store_env) -> None:
    """The property that makes this usable in CI. A validator that prints
    violations and exits 0 is worse than no validator, because a pipeline built
    on it reports green."""
    invalid = sorted((FIXTURES / "invalid").glob("*.jsonld"))[0]
    result = runner.invoke(app, ["record", "validate", str(invalid)])
    assert result.exit_code == 1
    assert "violation" in result.output.lower()


def test_validate_points_at_the_failing_triple(runner, store_env) -> None:
    """PRD §7.5: validation output points at the specific triple that failed."""
    invalid = sorted((FIXTURES / "invalid").glob("*.jsonld"))[0]
    result = runner.invoke(app, ["record", "validate", str(invalid), "--json"])
    payload = json.loads(result.stdout)
    assert payload["conforms"] is False
    assert payload["violations"], "a failure with no violation is not actionable"
    assert payload["violations"][0]["focusNode"]


def test_put_then_get_round_trips(runner, bootstrapped) -> None:
    source = FIXTURES / "records" / "ecmwf-era5.jsonld"
    assert runner.invoke(app, ["record", "put", str(source)]).exit_code == 0

    result = runner.invoke(app, ["record", "get", "ecmwf-era5"])

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["@graph"][0]["type"] == "Dataset"
    assert document["@graph"][0]["title"] == "ECMWF ERA5 reanalysis"


def test_get_on_an_absent_record_writes_nothing_to_stdout(runner, bootstrapped) -> None:
    """`datahub record get x > file` writes a record or writes nothing. An
    error message in the file would be a corrupt record that parses."""
    result = runner.invoke(app, ["record", "get", "not-a-dataset"])
    assert result.exit_code == 1
    assert result.stdout.strip() == ""


def test_put_refuses_a_record_that_fails_validation(runner, bootstrapped) -> None:
    invalid = sorted((FIXTURES / "invalid").glob("*.jsonld"))[0]
    result = runner.invoke(app, ["record", "put", str(invalid)])
    assert result.exit_code == 1


def test_list_shows_catalog_and_draft_separately(runner, bootstrapped) -> None:
    runner.invoke(app, ["seed", "load", "--limit", "20"])

    catalog = runner.invoke(app, ["record", "list", "--limit", "100"])
    draft = runner.invoke(app, ["record", "list", "--draft", "--limit", "100"])

    assert catalog.exit_code == draft.exit_code == 0
    catalog_ids = set(catalog.stdout.split())
    draft_ids = set(draft.stdout.split())
    assert catalog_ids and draft_ids
    assert not (catalog_ids & draft_ids), "a record is in one graph or the other, never both"


# ---- index and search ----------------------------------------------------


def test_reindex_then_search_finds_the_record(runner, bootstrapped) -> None:
    """PRD §3.1: reindex-from-scratch is one command, and it is meant to be run
    often."""
    runner.invoke(app, ["record", "put", str(FIXTURES / "records" / "ecmwf-era5.jsonld")])

    reindexed = runner.invoke(app, ["index", "reindex", "--json"])
    assert reindexed.exit_code == 0, reindexed.output
    assert json.loads(reindexed.stdout)["errors"] == []

    found = runner.invoke(app, ["search", "reanalysis", "--json"])
    assert found.exit_code == 0
    assert any("era5" in hit["id"] for hit in json.loads(found.stdout)["hits"])


def test_search_filters_by_a_bare_domain_code(runner, bootstrapped) -> None:
    """`--domain DD1` is what a person types; the index holds a concept IRI."""
    runner.invoke(app, ["seed", "load", "--limit", "40"])
    runner.invoke(app, ["index", "reindex"])

    matched = runner.invoke(app, ["search", "--domain", "DD1", "--json"])
    unmatched = runner.invoke(app, ["search", "--domain", "DD9", "--json"])

    assert matched.exit_code == unmatched.exit_code == 0
    assert json.loads(matched.stdout)["total"] > 0
    assert json.loads(unmatched.stdout)["total"] == 0, "the filter filters"


def test_a_reindex_drops_a_record_deleted_from_the_graph(runner, bootstrapped) -> None:
    """``clear`` is what makes it a rebuild rather than a merge: a record
    deleted from the graph would otherwise survive in the index forever."""
    runner.invoke(app, ["record", "put", str(FIXTURES / "records" / "ecmwf-era5.jsonld")])
    runner.invoke(app, ["index", "reindex"])
    assert json.loads(runner.invoke(app, ["search", "--json"]).stdout)["total"] == 1

    from datahub.graph.records import RecordStore
    from datahub.graph.store import make_store

    with make_store() as store:
        RecordStore(store).delete("ecmwf-era5")

    runner.invoke(app, ["index", "reindex"])
    assert json.loads(runner.invoke(app, ["search", "--json"]).stdout)["total"] == 0


# ---- query ---------------------------------------------------------------


def test_query_list_names_all_seven(runner, store_env) -> None:
    result = runner.invoke(app, ["query", "list"])
    assert result.exit_code == 0
    assert sorted(result.output.split()) == [f"q{n}" for n in range(1, 8)]


def test_a_query_that_needs_bindings_says_so(runner, bootstrapped) -> None:
    """An unbound placeholder would otherwise surface as a SPARQL parse error
    naming a variable the caller never wrote."""
    result = runner.invoke(app, ["query", "run", "q1"])

    assert result.exit_code == 2
    assert "needs a, b" in result.output


def test_query_params_lists_the_bindings(runner, store_env) -> None:
    assert runner.invoke(app, ["query", "params", "q1"]).output.split() == ["a", "b"]
    assert runner.invoke(app, ["query", "params", "q5"]).output.strip() == "(none)"


def test_a_parameterless_query_runs(runner, bootstrapped) -> None:
    runner.invoke(app, ["seed", "load", "--limit", "10"])
    result = runner.invoke(app, ["query", "run", "q7", "--json"])
    assert result.exit_code == 0, result.output
    assert isinstance(json.loads(result.stdout), list)


def test_an_unknown_query_is_refused(runner, store_env) -> None:
    result = runner.invoke(app, ["query", "run", "q99"])
    assert result.exit_code == 1
    assert "no such query" in result.output


def test_a_malformed_param_is_refused(runner, bootstrapped) -> None:
    result = runner.invoke(app, ["query", "run", "q1", "-p", "justakey"])
    assert result.exit_code == 2


# ---- db and status -------------------------------------------------------


def test_db_upgrade_then_current(runner, store_env) -> None:
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    result = runner.invoke(app, ["db", "current"])
    assert result.exit_code == 0


def test_status_answers_the_operator_question(runner, bootstrapped) -> None:
    runner.invoke(app, ["seed", "load", "--limit", "5"])

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["catalog_records"] + report["draft_records"] == 5
    assert report["entailments_current"] is True
    assert report["vocabulary_checksum"]


def test_status_survives_a_database_it_cannot_reach(runner, bootstrapped, monkeypatch) -> None:
    """A down dependency is a status to report, not a stack trace. An operator
    running `status` because something is broken must not be met with the
    thing that is broken."""
    monkeypatch.setenv("DATAHUB_DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none")
    from datahub.api.models.base import reset_engine
    from datahub.config import reset_settings

    reset_settings()
    reset_engine()

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0, result.output
    assert "database_error" in json.loads(result.stdout)


def test_reindex_says_what_to_do_when_the_schema_is_missing(runner, store_env) -> None:
    """A fresh checkout has no schema. Meeting that with a SQLAlchemy traceback
    naming a missing table teaches nothing; naming the command that fixes it
    does."""
    runner.invoke(app, ["graph", "bootstrap"])

    result = runner.invoke(app, ["index", "reindex"])

    assert result.exit_code == 1
    assert "datahub db upgrade" in result.output


def test_json_output_is_parseable_with_logging_on(runner, store_env) -> None:
    """Diagnostics go to stderr, data to stdout — otherwise
    `datahub graph bootstrap --json | jq` gets a log line for breakfast."""
    result = runner.invoke(app, ["--verbose", "graph", "bootstrap", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["checksum"]
