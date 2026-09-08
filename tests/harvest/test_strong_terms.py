"""Every filter term must match a document that contains it (#31 item 3).

`STRONG_TERMS` are the filter's highest-confidence signals: one match alone
reaches `ACCEPT_AT` and accepts a record outright. Three of them could never
match anything.

`_normalise` expands a hyphenated token into the token *and* its parts, so
"feed-in tariff" arrives in the haystack as `feed-in feed in tariff`. The term
`feed-in tariff` is not a contiguous run of that -- the split parts are
interleaved -- so `_contains` returned False against a document that literally
said the words. Silent, and in the direction that loses records.
"""

from __future__ import annotations

import pytest
from datahub.harvest.filters.relevance import (
    ACCEPT_AT,
    COUNTER_TERMS,
    STRONG_TERMS,
    WEAK_TERMS,
    RelevanceFilter,
    _contains,
    _normalise,
)

HYPHENATED = sorted(t for t in (*STRONG_TERMS, *WEAK_TERMS, *COUNTER_TERMS) if "-" in t)

#: No trailing punctuation in the probe sentences below. `_WORD` keeps dots --
#: it has to, for "v1.0" and for host names -- so "balancing." is one token and
#: a term of "balancing" does not match it. That is correct tokeniser behaviour
#: and a trap for a test that ends its sentence with a full stop, which the
#: first version of this file did: 108 failures, none of them a real defect.


@pytest.mark.parametrize("term", sorted(STRONG_TERMS))
def test_every_strong_term_matches_a_document_containing_it(term: str) -> None:
    haystack = _normalise(f"A dataset about {term} for system planning")
    assert _contains(haystack, term), (
        f"{term!r} cannot match any document, so it contributes nothing to any score"
    )


@pytest.mark.parametrize("term", sorted(WEAK_TERMS))
def test_every_weak_term_matches_a_document_containing_it(term: str) -> None:
    assert _contains(_normalise(f"A dataset about {term} here"), term)


@pytest.mark.parametrize("term", sorted(COUNTER_TERMS))
def test_every_counter_term_matches_a_document_containing_it(term: str) -> None:
    """Counter terms subtract. A dead one silently stops excluding what it names."""
    assert _contains(_normalise(f"This is a study of {term} only"), term)


def test_hyphenated_terms_exist_so_this_file_is_not_vacuous() -> None:
    assert HYPHENATED, "no hyphenated term left, so the regression above cannot recur here"


@pytest.mark.parametrize("term", sorted(t for t in STRONG_TERMS if "-" in t))
def test_a_hyphenated_strong_term_alone_accepts(term: str) -> None:
    """The consequence, not just the mechanism.

    A strong term reaches `ACCEPT_AT` on its own. These three scored 0.00, so
    the records they exist to catch were decided as if the term were absent.
    """
    score, _ = RelevanceFilter().score(_normalise(f"This dataset describes the {term} clearly"))
    assert score >= ACCEPT_AT, f"{term!r} scored {score}, below ACCEPT_AT={ACCEPT_AT}"


def test_whole_token_matching_still_holds() -> None:
    """The property the fix must not cost.

    Substring matching would have "iso" hit "isotope" and "wind" hit "winding".
    De-hyphenating a term widens it to the split form the normaliser already
    emits, and to nothing else.
    """
    assert not _contains(_normalise("isotope ratios in winding samples"), "iso")
    assert not _contains(_normalise("isotope ratios in winding samples"), "wind")
