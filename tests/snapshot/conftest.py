"""The API suite's fixtures, reused verbatim.

The exporter drives the real app, so the store it needs is the store the API
tests need. Importing the fixtures rather than rebuilding them also means a
change to how the corpus is loaded cannot make these two suites disagree about
what is in it.

Imported rather than declared with `pytest_plugins`, which pytest 8 permits
only in the rootdir conftest — and which passed when this directory was run on
its own and failed the moment the whole suite was.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.conftest import (  # noqa: F401  (re-exported as fixtures)
    api_env,
    client,
    corpus_nquads,
    loaded,
)
