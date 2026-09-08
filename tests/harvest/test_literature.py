"""Literature scoring, and the gate it feeds (issue #58).

Nothing here touches the network. What is defended is the three things that
would each silently invert this signal rather than degrade it: an unquoted
phrase, a spent budget read as a zero, and a single low score allowed to delete
a dataset on its own.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.filters.literature import (
    ENERGY_FIELDS,
    BudgetExhausted,
    LiteratureScore,
    OpenAlexScorer,
    load_scores,
    refuses_publication,
    write_scores,
)


def score(slug: str, works: int, energy: int) -> LiteratureScore:
    return LiteratureScore(slug, f'"{slug}"', works, energy, "2026-09-08")


# ---- the query -----------------------------------------------------------


def test_the_phrase_is_quoted() -> None:
    """The difference between 549 works and 179,365.

    `fulltext.search:Wind Integration National Dataset` ORs the words. That does
    not weaken the signal, it inverts it — every dataset with a generic name
    scores as enormously popular and the gate starts keeping the wrong things.
    """
    assert OpenAlexScorer._phrase("Wind Integration National Dataset") == (
        '"Wind Integration National Dataset"'
    )


def test_an_inner_quote_cannot_truncate_the_phrase() -> None:
    """OpenAlex has no escape syntax inside a filter value, so a stray quote
    would end the phrase early and silently widen the search."""
    assert '"' not in OpenAlexScorer._phrase('The "Big" Dataset')[1:-1]


def test_the_energy_filter_names_the_fields_and_the_plain_one_does_not() -> None:
    scorer = OpenAlexScorer()
    plain = scorer._filters("ArcticDEM", energy=False)
    energy = scorer._filters("ArcticDEM", energy=True)

    assert plain == 'fulltext.search:"ArcticDEM"'
    assert "primary_topic.field.id:" in energy
    for field in ENERGY_FIELDS:
        assert str(field) in energy


def test_the_field_set_is_not_widened_by_accident() -> None:
    """Energy and Engineering, deliberately. Adding Environmental Science would
    sweep in the whole remote-sensing corpus and every land-cover product would
    score as heavily used — which is the failure this module exists to catch."""
    assert ENERGY_FIELDS == (21, 22)


# ---- the file ------------------------------------------------------------


def test_scores_round_trip(tmp_path) -> None:
    path = tmp_path / "scores.yaml"
    original = {"a": score("a", 100, 7), "b": score("b", 3, 0)}
    write_scores(path, original)
    back = load_scores(path)
    assert back == original


def test_a_missing_file_is_empty_rather_than_fatal(tmp_path) -> None:
    assert load_scores(tmp_path / "nope.yaml") == {}


def test_a_malformed_entry_is_refused(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("scores:\n  a:\n    works: 1\n")
    with pytest.raises(ValueError, match="malformed"):
        load_scores(path)


def test_the_written_file_says_absence_is_not_zero(tmp_path) -> None:
    """The header is load-bearing. Somebody reading this file has to know that a
    dataset missing from it was not measured."""
    path = tmp_path / "scores.yaml"
    write_scores(path, {"a": score("a", 1, 0)})
    text = path.read_text()
    assert "unmeasured, not unused" in text
    assert "resumable" in text


# ---- the budget ----------------------------------------------------------


def test_a_spent_budget_raises_rather_than_scoring_zero(monkeypatch) -> None:
    """The failure that would quietly empty the catalog. "We could not ask" and
    "nobody uses this" are opposite facts, and a 429 that returned 0 would erase
    every dataset the scan had not yet reached.

    Patched at the HTTP layer, not at `_count`: the translation from a 429 to
    `BudgetExhausted` happens *inside* `_count`, so stubbing it would test the
    stub.
    """
    import io
    import urllib.error
    import urllib.request

    def refuse(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "u", 429, "Too Many Requests", {}, io.BytesIO(b'{"error":"Insufficient budget"}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(BudgetExhausted):
        OpenAlexScorer(pace_s=0).score("a", "A dataset")


def test_an_ordinary_429_is_retried_rather_than_treated_as_a_budget(monkeypatch) -> None:
    """Rate limiting and a spent allowance both answer 429 and mean opposite
    things: one clears in a second, the other not until midnight UTC. Retrying
    a spent budget burns the rest of the run against a wall; giving up on a
    rate limit throws away a scan that would have succeeded."""
    import io
    import urllib.error
    import urllib.request

    calls = {"n": 0}

    def flaky(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                "u", 429, "Too Many Requests", {}, io.BytesIO(b"slow down")
            )

        class _R:
            def read(self):
                return b'{"meta": {"count": 7}}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    scorer = OpenAlexScorer(pace_s=0)
    assert scorer._count("x") == 7
    assert calls["n"] == 2


# ---- the gate ------------------------------------------------------------

CATEGORY = "land cover, elevation or bathymetry stated as a physical quantity"
SUBJECT = "an inventory of power-sector assets (DD2, DD3)"


def test_the_case_this_was_built_for() -> None:
    """ArcticDEM: a real literature, almost none of it energy, nothing recording
    its use, admitted because elevation is a siting input."""
    verdict = refuses_publication(
        "pgc-arcticdem",
        scores={"pgc-arcticdem": score("pgc-arcticdem", 1704, 0)},
        usage_evidence_count=0,
        relevance_reason=CATEGORY,
    )
    assert verdict.refused
    assert "1704 works name it at all" in verdict.reason


@pytest.mark.parametrize(
    ("label", "energy", "uses", "reason"),
    [
        # Each of these is the *only* signal present, and each alone is enough
        # to keep the record. That is what makes this a conjunction.
        ("energy literature names it", 282, 0, CATEGORY),
        ("something records it being used", 0, 9, CATEGORY),
        ("admitted for its own subject", 0, 0, SUBJECT),
    ],
)
def test_any_single_signal_of_use_keeps_a_record(
    label: str, energy: int, uses: int, reason: str
) -> None:
    verdict = refuses_publication(
        "x",
        scores={"x": score("x", 500, energy)},
        usage_evidence_count=uses,
        relevance_reason=reason,
    )
    assert not verdict.refused, label


def test_an_unmeasured_record_is_never_refused() -> None:
    """The scan is resumable and stops when the budget runs out, so most of the
    catalog can be unscored at any moment. Reading that as a zero would delete
    the records the scan simply had not reached yet."""
    verdict = refuses_publication(
        "never-scanned", scores={}, usage_evidence_count=0, relevance_reason=CATEGORY
    )
    assert not verdict.refused
    assert "not a score of zero" in verdict.reason


def test_hrrr_survives_a_low_score() -> None:
    """The false negative that proves the conjunction earns its keep.

    NOAA HRRR scores 5 energy-field works because its wind and solar
    forecasting papers are classified in Meteorology. A threshold on the score
    alone would delete one of the most-used forecast products in the field; the
    recorded uses keep it.
    """
    verdict = refuses_publication(
        "noaa-hrrr",
        scores={"noaa-hrrr": score("noaa-hrrr", 1895, 0)},
        usage_evidence_count=4,
        relevance_reason="a numerical weather prediction or reanalysis product",
    )
    assert not verdict.refused
    assert "4 recorded uses" in verdict.reason


def test_one_energy_work_is_enough_by_default() -> None:
    """The threshold is deliberately at the floor. This signal exists to find
    datasets with *no* standing in the field, not to rank the rest."""
    kept = refuses_publication(
        "x", scores={"x": score("x", 9, 1)}, usage_evidence_count=0, relevance_reason=CATEGORY
    )
    assert not kept.refused


def test_share_is_zero_rather_than_undefined_for_an_unnamed_dataset() -> None:
    assert score("x", 0, 0).share == 0.0
