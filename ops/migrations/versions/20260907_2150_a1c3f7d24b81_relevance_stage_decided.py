"""relevance stage 'llm' becomes 'decided'

The third stage was renamed when it stopped being a model call and became a
lookup into a committed decision file (ADR-0013): what the column records is
that a verdict existed, not how it was reached. The CHECK constraint was not
renamed with it, so every decided record failed to insert with

    CHECK constraint failed: ck_relevance_decisions_stage_known

and, because `_process` wraps a record in a try, that surfaced as
`record failed` per record rather than as a schema error. The harvest kept
running and simply lost the audit row — and the record with it.

Existing rows are rewritten rather than left: `llm` and `decided` would then be
two names for the same stage in one table, and a recall audit reading it could
not tell whether a run predated the rename or used a different mechanism.

Revision ID: a1c3f7d24b81
Revises: 4fe332cb2c9d
Create Date: 2026-09-07 21:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3f7d24b81"
down_revision: str | None = "4fe332cb2c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: SQLite cannot ALTER a CHECK constraint, so the table is rebuilt. Alembic's
#: batch mode does that — create, copy, drop, rename — and is a no-op shape on
#: Postgres, where the constraint is simply dropped and re-added.
_OLD = "stage in ('keyword','vocabulary','llm')"
_NEW = "stage in ('keyword','vocabulary','decided')"
#: The *unrendered* name. Alembic's naming convention adds the `ck_<table>_`
#: prefix, and passing the rendered name inside a batch block asks it to drop
#: `ck_relevance_decisions_ck_relevance_decisions_stage_known`.
_NAME = "stage_known"


def upgrade() -> None:
    # Data first: the new constraint would refuse the existing rows otherwise.
    op.execute(sa.text("UPDATE relevance_decisions SET stage = 'decided' WHERE stage = 'llm'"))
    with op.batch_alter_table("relevance_decisions") as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _NEW)


def downgrade() -> None:
    op.execute(sa.text("UPDATE relevance_decisions SET stage = 'llm' WHERE stage = 'decided'"))
    with op.batch_alter_table("relevance_decisions") as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.create_check_constraint(_NAME, _OLD)
