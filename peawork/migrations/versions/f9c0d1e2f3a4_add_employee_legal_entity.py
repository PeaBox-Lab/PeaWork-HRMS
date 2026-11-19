"""add employee legal entity link

Revision ID: f9c0d1e2f3a4
Revises: e7a8b9c0d1e2
Create Date: 2026-11-19 15:20:00.000000

Permanent employees belong to a legal entity, and the legal entity's country
selects the payroll engine. The column is nullable so existing rows keep the
employee/site/organisation country fallback.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f9c0d1e2f3a4'
down_revision: str | None = 'e7a8b9c0d1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('employees')}
    indexes = {index['name'] for index in inspector.get_indexes('employees')}

    with op.batch_alter_table('employees', schema=None) as batch_op:
        if 'legal_entity_id' not in columns:
            batch_op.add_column(
                sa.Column(
                    'legal_entity_id',
                    sa.Uuid(),
                    sa.ForeignKey(
                        'legal_entities.id',
                        ondelete='SET NULL',
                        name=op.f('fk_employees_legal_entity_id_legal_entities'),
                    ),
                    nullable=True,
                )
            )

    if 'ix_employees_legal_entity_id' not in indexes:
        op.create_index(
            'ix_employees_legal_entity_id', 'employees', ['legal_entity_id'], unique=False
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('employees')}
    indexes = {index['name'] for index in inspector.get_indexes('employees')}

    if 'ix_employees_legal_entity_id' in indexes:
        op.drop_index('ix_employees_legal_entity_id', table_name='employees')
    if 'legal_entity_id' in columns:
        with op.batch_alter_table('employees', schema=None) as batch_op:
            batch_op.drop_column('legal_entity_id')
