"""The API suite's fixtures, reused verbatim.

The exporter drives the real app, so the store it needs is the store the API
tests need. Importing the fixtures rather than rebuilding them also means a
change to how the corpus is loaded cannot make these two suites disagree about
what is in it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest_plugins = ["api.conftest"]
