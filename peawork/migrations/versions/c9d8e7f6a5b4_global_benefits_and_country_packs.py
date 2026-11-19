"""global benefits and country packs

Revision ID: c9d8e7f6a5b4
Revises: 7663d9f4f9c0
Create Date: 2026-11-19 09:20:00.000000

Adds country-aware benefit plans, family/dependant coverage and payroll
deduction metadata. Also adds an employee country code so enrollments can be
checked against country-scoped eligibility rules.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d8e7f6a5b4'
down_revision: str | None = '7663d9f4f9c0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.add_column(sa.Column('country_code', sa.String(length=2), nullable=True))

    with op.batch_alter_table('benefit_plans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('country_code', sa.String(length=2), nullable=True))
        batch_op.add_column(sa.Column('policy_number', sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                'coverage_tier',
                sa.Enum(
                    'employee_only',
                    'employee_spouse',
                    'employee_children',
                    'family',
                    'custom',
                    name='benefitcoveragetier',
                    native_enum=False,
                    length=40,
                ),
                nullable=False,
                server_default='employee_only',
            )
        )
        batch_op.add_column(
            sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR')
        )
        batch_op.add_column(
            sa.Column(
                'payroll_deduction',
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                'waiting_period_days', sa.Integer(), nullable=False, server_default='0'
            )
        )
        batch_op.add_column(sa.Column('eligibility', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('metadata_json', sa.JSON(), nullable=True))
        batch_op.create_index(
            'ix_benefit_plans_org_country', ['org_id', 'country_code'], unique=False
        )

    with op.batch_alter_table('employee_benefits', schema=None) as batch_op:
        batch_op.add_column(sa.Column('effective_on', sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column(
                'coverage_tier',
                sa.Enum(
                    'employee_only',
                    'employee_spouse',
                    'employee_children',
                    'family',
                    'custom',
                    name='benefitcoveragetier',
                    native_enum=False,
                    length=40,
                ),
                nullable=False,
                server_default='employee_only',
            )
        )
        batch_op.add_column(
            sa.Column(
                'employee_premium',
                sa.Numeric(precision=12, scale=2),
                nullable=False,
                server_default='0',
            )
        )
        batch_op.add_column(
            sa.Column(
                'employer_premium',
                sa.Numeric(precision=12, scale=2),
                nullable=False,
                server_default='0',
            )
        )
        batch_op.add_column(
            sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR')
        )
        batch_op.add_column(
            sa.Column(
                'payroll_deduction',
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.create_index(
            'ix_employee_benefits_org_employee',
            ['org_id', 'employee_id'],
            unique=False,
        )

    op.create_table(
        'benefit_dependents',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('enrollment_id', sa.Uuid(), nullable=False),
        sa.Column('full_name', sa.String(length=160), nullable=False),
        sa.Column(
            'relationship',
            sa.Enum(
                'spouse',
                'partner',
                'child',
                'parent',
                'other',
                name='dependentrelationship',
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column('date_of_birth', sa.Date(), nullable=True),
        sa.Column('national_id_masked', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['org_id'],
            ['organizations.id'],
            name=op.f('fk_benefit_dependents_org_id_organizations'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['enrollment_id'],
            ['employee_benefits.id'],
            name=op.f('fk_benefit_dependents_enrollment_id_employee_benefits'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_benefit_dependents')),
    )
    with op.batch_alter_table('benefit_dependents', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_benefit_dependents_org_id'), ['org_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_benefit_dependents_enrollment_id'),
            ['enrollment_id'],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('benefit_dependents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_benefit_dependents_enrollment_id'))
        batch_op.drop_index(batch_op.f('ix_benefit_dependents_org_id'))
    op.drop_table('benefit_dependents')

    with op.batch_alter_table('employee_benefits', schema=None) as batch_op:
        batch_op.drop_index('ix_employee_benefits_org_employee')
        batch_op.drop_column('payroll_deduction')
        batch_op.drop_column('currency')
        batch_op.drop_column('employer_premium')
        batch_op.drop_column('employee_premium')
        batch_op.drop_column('coverage_tier')
        batch_op.drop_column('effective_on')

    with op.batch_alter_table('benefit_plans', schema=None) as batch_op:
        batch_op.drop_index('ix_benefit_plans_org_country')
        batch_op.drop_column('metadata_json')
        batch_op.drop_column('eligibility')
        batch_op.drop_column('waiting_period_days')
        batch_op.drop_column('payroll_deduction')
        batch_op.drop_column('currency')
        batch_op.drop_column('coverage_tier')
        batch_op.drop_column('policy_number')
        batch_op.drop_column('country_code')

    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.drop_column('country_code')
