"""task attempts

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("attempt", sa.Integer(), server_default="1", nullable=False))
    op.add_column(
        "task_photos", sa.Column("attempt", sa.Integer(), server_default="1", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("task_photos", "attempt")
    op.drop_column("tasks", "attempt")
