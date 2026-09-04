"""Graph to search index. Derived state, rebuilt by one command.

The index is never a source of truth (PRD principle 8). If a fix would be lost
by a full reindex, the fix landed in the wrong place.
"""

from __future__ import annotations

from datahub.projector.build import GRADE_LABELS, build_document
from datahub.projector.index import (
    ProjectionResult,
    Projector,
    ProjectorHealth,
    make_projector,
)
from datahub.projector.reindex import ReindexResult, reindex

__all__ = [
    "GRADE_LABELS",
    "ProjectionResult",
    "Projector",
    "ProjectorHealth",
    "ReindexResult",
    "build_document",
    "make_projector",
    "reindex",
]
