"""server public address

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-22 05:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("public_address", sa.String(length=255), nullable=True))
    # SQLite 的 ALTER 不支持 server_default 表达式，这里用 Python 回填。
    op.execute("UPDATE servers SET public_address = '' WHERE public_address IS NULL")


def downgrade() -> None:
    op.drop_column("servers", "public_address")
