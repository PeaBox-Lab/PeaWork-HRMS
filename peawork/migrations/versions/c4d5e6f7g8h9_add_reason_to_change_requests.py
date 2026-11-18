"""add reason column to change_requests

Revision ID: c4d5e6f7g8h9
Revises: b2c3d4e5f6g7
Create Date: 2026-11-18 16:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d5e6f7g8h9'
down_revision: str | None = 'b2c3d4e5f6g7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite supports ALTER TABLE ADD COLUMN (since 3.2.0).
    # Use batch mode so the column can be added cleanly on both fresh and legacy DBs.
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reason', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.drop_column('reason')