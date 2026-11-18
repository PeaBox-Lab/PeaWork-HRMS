"""employee_lifecycle_change_requests

Revision ID: a3b7c1d2e4f5
Revises: f4324d9c1355
Create Date: 2026-11-18 14:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b7c1d2e4f5'
down_revision: str | None = 'f4324d9c1355'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('change_requests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('employee_id', sa.Uuid(), nullable=True),
    sa.Column('change_type', sa.String(length=48), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('old_value', sa.Text(), nullable=True),
    sa.Column('new_value', sa.Text(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('approved_by', sa.Uuid(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True)),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_change_requests_approved_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_change_requests_actor_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_change_requests_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_change_requests_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_change_requests')),
    sa.UniqueConstraint('org_id', 'code', name='uq_change_requests_org_code')
    )
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_requests_employee'), ['employee_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_change_requests_org_status'), ['org_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_change_requests_org_type'), ['org_id', 'change_type'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_change_requests_org_type'))
        batch_op.drop_index(batch_op.f('ix_change_requests_org_status'))
        batch_op.drop_index(batch_op.f('ix_change_requests_employee'))

    op.drop_table('change_requests')