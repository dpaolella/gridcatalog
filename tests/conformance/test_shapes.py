"""The conformance suite (PRD §11).

Two halves:

* every valid fixture conforms **at its declared completeness level**;
* every invalid fixture fails **with the expected violation**, matched on the
  constraint component and the property path rather than on message text.

The second half is the one that does the work. Asserting only that an invalid
record fails would let a shape that fires for the wrong reason pass — and a
constraint that catches the right records by accident stops catching them the
moment anything nearby changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.validate import ValidationRunner, format_report
from fixtures.loader import (
    INVALID_DIR,
    declared_level,
    invalid_names,
    load_graph,
    record_names,
)

EXPECTED = yaml.safe_load((INVALID_DIR / "expected-violations.yaml").read_text())


@pytest.fixture(scope="module")
def runner() -> ValidationRunner:
    return ValidationRunner()


# ---- valid fixtures ------------------------------------------------------


@pytest.mark.parametrize("name", record_names())
def test_valid_fixture_conforms_at_its_declared_level(runner: ValidationRunner, name: str) -> None:
    level = declared_level(name)
    report = runner.validate(load_graph(name), level)
    assert report.conforms, (
        f"{name} claims completeness level {level} and does not meet it:\n{format_report(report)}"
    )


@pytest.mark.parametrize("name", record_names())
def test_valid_fixture_does_not_overclaim(runner: ValidationRunner, name: str) -> None:
    """A record's declared level must be one it actually satisfies, and the
    honest thing is for it to be the highest such level — a level-1 record that
    would pass at 3 is under-selling itself and hiding usable detail."""
    highest = runner.highest_passing_level(load_graph(name))
    declared = declared_level(name)
    assert highest >= declared, f"{name} declares level {declared} but only reaches {highest}"


def test_the_corpus_covers_every_domain() -> None:
    """A conformance corpus that misses a domain cannot catch a shape that is
    wrong for that domain."""
    from fixtures.loader import records_in_domain

    missing = [f"DD{i}" for i in range(1, 11) if not records_in_domain(f"DD{i}")]
    assert not missing, f"no fixture covers {missing}"


def test_the_corpus_covers_every_level() -> None:
    from fixtures.loader import records_at_level

    for level in (1, 2, 3):
        assert records_at_level(level), f"no fixture at completeness level {level}"


# ---- invalid fixtures ----------------------------------------------------


@pytest.mark.parametrize("name", invalid_names())
def test_invalid_fixture_fails(runner: ValidationRunner, name: str) -> None:
    expected = EXPECTED[name]
    report = runner.validate(load_graph(name), expected["level"])
    assert not report.conforms, (
        f"{name} was expected to fail at level {expected['level']} and did not. "
        f"It exists because: {expected['why']}"
    )


@pytest.mark.parametrize("name", invalid_names())
def test_invalid_fixture_fails_for_the_expected_reason(runner: ValidationRunner, name: str) -> None:
    expected = EXPECTED[name]
    report = runner.validate(load_graph(name), expected["level"])

    matches = [
        v
        for v in report.violations
        if v.constraint == expected["constraint"]
        and (expected["path"] is None or v.path == expected["path"])
        and (expected["focus_node"] is None or v.focus_node == expected["focus_node"])
    ]
    assert matches, (
        f"{name} failed, but not for the expected reason.\n"
        f"expected: constraint={expected['constraint']} path={expected['path']} "
        f"focus={expected['focus_node']}\n"
        f"why this fixture exists: {expected['why']}\n"
        f"actual:\n{format_report(report)}"
    )
    assert any(expected["message_contains"] in v.message for v in matches), (
        f"{name}: the right constraint fired but the message did not contain "
        f"{expected['message_contains']!r}. Messages are what a steward acts on.\n"
        f"{format_report(report)}"
    )


@pytest.mark.parametrize("name", invalid_names())
def test_every_invalid_fixture_has_an_expectation(name: str) -> None:
    """A fixture with no entry would be silently unasserted."""
    assert name in EXPECTED, (
        f"tests/fixtures/invalid/{name}.jsonld has no entry in expected-violations.yaml"
    )
    entry = EXPECTED[name]
    assert entry.get("why"), f"{name} does not say why it exists"


def test_no_stale_expectations() -> None:
    orphans = set(EXPECTED) - set(invalid_names())
    assert not orphans, f"expected-violations.yaml names fixtures that do not exist: {orphans}"


# ---- the M1 done-criterion ----------------------------------------------


def test_report_points_at_the_failing_triple(runner: ValidationRunner) -> None:
    """PRD §10, M1: 'an invalid record is rejected with a message pointing at
    the failing triple'.

    Asserted on the rendered output a steward actually reads, not on the report
    graph — the graph has always had the information; the question is whether it
    reaches a human.
    """
    report = runner.validate(load_graph("missing-license"), 1)
    rendered = format_report(report)
    assert "ds:broken" in rendered, "the failing node is not named"
    assert "dct:license" in rendered, "the failing property path is not named"
    assert "MinCount" in rendered, "the constraint that fired is not named"
    assert "states a licence" in rendered, "the remedy is not stated"


def test_report_names_the_value_that_failed(runner: ValidationRunner) -> None:
    report = runner.validate(load_graph("negative-byte-size"), 1)
    rendered = format_report(report)
    assert "-1" in rendered, "the offending value is not shown"
    assert "dist:broken--d1" in rendered


# ---- level parameterisation (ADR-0004) ----------------------------------


def test_a_level_three_constraint_does_not_block_a_level_one_record(
    runner: ValidationRunner,
) -> None:
    """The whole point of ADR-0004. `field-without-concept-or-gap` is invalid at
    level 3 and must be perfectly publishable at level 1."""
    graph = load_graph("field-without-concept-or-gap")
    assert not runner.validate(graph, 3).conforms
    assert runner.validate(graph, 1).conforms


def test_a_level_one_constraint_still_blocks_at_level_three(
    runner: ValidationRunner,
) -> None:
    """Filtering is one-directional: a higher level enforces everything the
    lower ones do."""
    graph = load_graph("missing-license")
    for level in (1, 2, 3):
        assert not runner.validate(graph, level).conforms, f"passed at level {level}"


def test_level_graphs_grow_monotonically(runner: ValidationRunner) -> None:
    sizes = [len(runner.shapes_for_level(level)) for level in (1, 2, 3)]
    assert sizes[0] < sizes[1] < sizes[2], sizes
