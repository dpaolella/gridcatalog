"""Comparing numbers written in different units (#83).

The question a diff between two assumption sets keeps asking is not "are these
numbers equal" but "is this a disagreement". The unit registry is what makes
the difference answerable, and these are the four answers it has to keep apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.units import Unit, compare

MT = Unit("unit:MegaTON_Metric", "megatonne", "Mt", "qk:Mass", 1e9)
T = Unit("unit:TON_Metric", "tonne", "t", "qk:Mass", 1e3)
MW = Unit("unit:MegaW", "megawatt", "MW", "qk:Power", 1e6)
USD = Unit("unit:USD", "US dollar", "$", "http://qudt.org/vocab/quantitykind/Currency", 1.0)
EUR = Unit("unit:EUR", "euro", "€", "http://qudt.org/vocab/quantitykind/Currency", 1.0)
UNRATED = Unit("og:unit/widgets", "widget", "w", "qk:Widgetry", None)


def test_the_same_unit_compares_as_numbers() -> None:
    assert compare(4.2, MT, 4.2, MT).relation == "identical"
    assert compare(4.2, MT, 5.0, MT).relation == "different"


def test_the_same_value_in_different_units_is_not_a_disagreement() -> None:
    """The case the issue calls five seconds that prove the vocabulary is
    doing work. Reported as a 999,999x discrepancy it would be worse than
    useless — a reader would chase the wrong number for an hour."""
    result = compare(4.2, MT, 4_200_000, T)

    assert result.relation == "equivalent"
    assert result.delta == 0
    assert "different units" in (result.note or "")


def test_a_real_difference_survives_the_conversion() -> None:
    """Converting must not flatten a genuine change into "equivalent"."""
    result = compare(4.2, MT, 3_150_000, T)  # 3.15 Mt against 4.2 Mt

    assert result.relation == "different"
    assert result.relative == pytest.approx(-0.25)


def test_two_quantity_kinds_have_no_delta_between_them() -> None:
    """A price and a mass do not differ by a number, and returning one would
    invite a reader to act on it."""
    result = compare(4.2, MT, 4.2, MW)

    assert result.relation == "incomparable"
    assert "quantity kinds" in (result.note or "")


def test_two_currencies_are_a_deflator_question_and_are_refused() -> None:
    """The registry gives every currency a multiplier of 1.0 and says why: a
    fixed factor cannot relate 2015 dollars to 2024 dollars. Converting on that
    1.0 would report two different currencies as the same money, which flatters
    every cost comparison in the catalog."""
    result = compare(100.0, USD, 100.0, EUR)

    assert result.relation == "incomparable"
    assert "deflator" in (result.note or "")

    # The same currency on both sides is still a straight comparison.
    assert compare(100.0, USD, 120.0, USD).relation == "different"


def test_a_bare_number_against_a_united_one_is_unknown_rather_than_assumed() -> None:
    """Guessing that the unitless side is in the other's unit is exactly the
    assumption a catalog must not make on the reader's behalf."""
    assert compare(4.2, None, 4.2, MT).relation == "unknown"
    assert compare(4.2, MT, 4.2, None).relation == "unknown"
    # Neither side stating one is a plain numeric comparison, not a mystery.
    assert compare(0.098, None, 0.074, None).relation == "different"


def test_a_unit_with_no_conversion_factor_says_so_rather_than_guessing() -> None:
    other = Unit("og:unit/gadgets", "gadget", "g", "qk:Widgetry", None)
    assert compare(1.0, UNRATED, 1.0, other).relation == "unknown"


def test_a_relative_change_against_zero_is_absent_rather_than_infinite() -> None:
    result = compare(0.0, None, 5.0, None)
    assert result.relation == "different"
    assert result.delta == 5.0
    assert result.relative is None
