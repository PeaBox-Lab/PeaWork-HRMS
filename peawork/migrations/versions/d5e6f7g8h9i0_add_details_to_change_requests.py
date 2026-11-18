"""add details column to change_requests

Revision ID: d5e6f7g8h9i0
Revises: c4d5e6f7g8h9
Create Date: 2026-11-18 17:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e6f7g8h9i0'
down_revision: str | None = 'c4d5e6f7g8h9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Structured payload of the change (employee field -> new value),
    # used to apply an approved request to the employee record.
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('details', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.drop_column('details')
