"""scheduled tasks

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22 02:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("cron", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=False),
        sa.Column("last_result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("scheduled_tasks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_scheduled_tasks_server_id"), ["server_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("scheduled_tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scheduled_tasks_server_id"))
    op.drop_table("scheduled_tasks")
