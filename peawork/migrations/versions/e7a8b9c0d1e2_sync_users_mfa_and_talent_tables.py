"""sync users MFA columns and talent tables with the ORM models

Revision ID: e7a8b9c0d1e2
Revises: c9d8e7f6a5b4
Create Date: 2026-11-19 11:40:00.000000

The ORM gained MFA columns on ``users`` and the R4 talent/skills tables
(``employee_skills``, ``development_goals``, ``calibration_rounds``,
``calibration_entries``) without a matching migration, so every user lookup
failed with "no such column: users.mfa_enabled" and login was impossible.

This migration is defensive: tables, columns and indexes are only created when
they are missing, so it also repairs databases that were partly built with
``Base.metadata.create_all``.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'e7a8b9c0d1e2'
down_revision: str | None = 'c9d8e7f6a5b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SKILL = sa.Enum(
    'python',
    'java',
    'javascript',
    'typescript',
    'sql',
    'excel',
    'powerpoint',
    'project_management',
    'agile',
    'scrum',
    'leadership',
    'communication',
    'analysis',
    'data_analysis',
    'reporting',
    'design',
    'writing',
    name='skill',
    native_enum=False,
    length=40,
)

PROFICIENCY_LEVEL = sa.Enum(
    'beginner',
    'intermediate',
    'advanced',
    'expert',
    name='proficiencylevel',
    native_enum=False,
    length=40,
)

CHANGE_REQUEST_TYPE = sa.Enum(
    'resignation',
    'promotion',
    'position_change',
    'location_change',
    'manager_change',
    'employment_type_change',
    'contract_change',
    'rehire',
    'group_company_transfer',
    'country_transfer',
    'legal_entity_change',
    'project_assignment',
    'cost_center_change',
    'timesheet_adjustment',
    'leave_request',
    'attendance_adjustment',
    'other',
    name='changerequesttype',
    native_enum=False,
    length=40,
)

CHANGE_REQUEST_STATUS = sa.Enum(
    'draft',
    'pending_approval',
    'approved',
    'rejected',
    'cancelled',
    'completed',
    name='changerequeststatus',
    native_enum=False,
    length=40,
)


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {column['name'] for column in _inspector().get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index['name'] for index in _inspector().get_indexes(table)}


def upgrade() -> None:
    inspector = _inspector()

    # --- users: MFA -----------------------------------------------------
    user_columns = _columns('users')
    with op.batch_alter_table('users', schema=None) as batch_op:
        if 'mfa_enabled' not in user_columns:
            batch_op.add_column(
                sa.Column(
                    'mfa_enabled',
                    sa.Boolean(),
                    server_default=sa.false(),
                    nullable=False,
                )
            )
        if 'mfa_secret' not in user_columns:
            batch_op.add_column(sa.Column('mfa_secret', sa.String(length=64)))
        if 'mfa_backup_codes' not in user_columns:
            batch_op.add_column(sa.Column('mfa_backup_codes', sa.String()))

    # --- R4 talent / skills tables -------------------------------------
    if not inspector.has_table('employee_skills'):
        op.create_table(
            'employee_skills',
            sa.Column('id', sa.Uuid(), nullable=False),
            sa.Column('org_id', sa.Uuid(), nullable=False),
            sa.Column('employee_id', sa.Uuid(), nullable=False),
            sa.Column('skill', SKILL, nullable=False),
            sa.Column('proficiency', PROFICIENCY_LEVEL, nullable=False),
            sa.Column('verified', sa.Boolean(), nullable=False),
            sa.Column('verification_notes', sa.Text(), nullable=True),
            sa.Column('years_experience', sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column(
                'last_assessed_at',
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column('assessed_by', sa.Uuid(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ['assessed_by'],
                ['users.id'],
                name=op.f('fk_employee_skills_assessed_by_users'),
                ondelete='SET NULL',
            ),
            sa.ForeignKeyConstraint(
                ['employee_id'],
                ['employees.id'],
                name=op.f('fk_employee_skills_employee_id_employees'),
                ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['org_id'],
                ['organizations.id'],
                name=op.f('fk_employee_skills_org_id_organizations'),
                ondelete='CASCADE',
            ),
            sa.PrimaryKeyConstraint('id', name=op.f('pk_employee_skills')),
            sa.UniqueConstraint(
                'employee_id', 'skill', name='uq_employee_skills_emp_skill'
            ),
        )

    if not inspector.has_table('development_goals'):
        op.create_table(
            'development_goals',
            sa.Column('id', sa.Uuid(), nullable=False),
            sa.Column('org_id', sa.Uuid(), nullable=False),
            sa.Column('employee_id', sa.Uuid(), nullable=False),
            sa.Column('skill', SKILL, nullable=False),
            sa.Column('current_proficiency', PROFICIENCY_LEVEL, nullable=False),
            sa.Column('target_proficiency', PROFICIENCY_LEVEL, nullable=False),
            sa.Column('target_date', sa.Date(), nullable=False),
            sa.Column('status', sa.String(length=32), nullable=False),
            sa.Column('rationale', sa.Text(), nullable=True),
            sa.Column('actions', sa.Text(), nullable=True),
            sa.Column('achieved_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ['employee_id'],
                ['employees.id'],
                name=op.f('fk_development_goals_employee_id_employees'),
                ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['org_id'],
                ['organizations.id'],
                name=op.f('fk_development_goals_org_id_organizations'),
                ondelete='CASCADE',
            ),
            sa.PrimaryKeyConstraint('id', name=op.f('pk_development_goals')),
        )

    if not inspector.has_table('calibration_rounds'):
        op.create_table(
            'calibration_rounds',
            sa.Column('id', sa.Uuid(), nullable=False),
            sa.Column('org_id', sa.Uuid(), nullable=False),
            sa.Column('name', sa.String(length=160), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('start_date', sa.Date(), nullable=False),
            sa.Column('end_date', sa.Date(), nullable=False),
            sa.Column('status', sa.String(length=32), nullable=False),
            sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_by', sa.Uuid(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ['created_by'],
                ['users.id'],
                name=op.f('fk_calibration_rounds_created_by_users'),
                ondelete='SET NULL',
            ),
            sa.ForeignKeyConstraint(
                ['org_id'],
                ['organizations.id'],
                name=op.f('fk_calibration_rounds_org_id_organizations'),
                ondelete='CASCADE',
            ),
            sa.PrimaryKeyConstraint('id', name=op.f('pk_calibration_rounds')),
        )

    if not inspector.has_table('calibration_entries'):
        op.create_table(
            'calibration_entries',
            sa.Column('id', sa.Uuid(), nullable=False),
            sa.Column('round_id', sa.Uuid(), nullable=False),
            sa.Column('employee_id', sa.Uuid(), nullable=False),
            sa.Column('skill', SKILL, nullable=True),
            sa.Column('proposed_rating', sa.Numeric(precision=3, scale=1), nullable=False),
            sa.Column('calibrated_rating', sa.Numeric(precision=3, scale=1), nullable=True),
            sa.Column('rationale_proposed', sa.Text(), nullable=True),
            sa.Column('rationale_calibrated', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=32), nullable=False),
            sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('locked_by', sa.Uuid(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ['employee_id'],
                ['employees.id'],
                name=op.f('fk_calibration_entries_employee_id_employees'),
                ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['locked_by'],
                ['users.id'],
                name=op.f('fk_calibration_entries_locked_by_users'),
                ondelete='SET NULL',
            ),
            sa.ForeignKeyConstraint(
                ['round_id'],
                ['calibration_rounds.id'],
                name=op.f('fk_calibration_entries_round_id_calibration_rounds'),
                ondelete='CASCADE',
            ),
            sa.PrimaryKeyConstraint('id', name=op.f('pk_calibration_entries')),
        )

    # Re-inspect: the inspector above cached the pre-DDL table list.
    inspector = _inspector()

    # --- indexes that create_all-built databases already have ----------
    missing_indexes = [
        ('ix_employee_skills_org', 'employee_skills', ['org_id']),
        ('ix_employee_skills_org_id', 'employee_skills', ['org_id']),
        ('ix_employee_skills_employee_id', 'employee_skills', ['employee_id']),
        ('ix_development_goals_org', 'development_goals', ['org_id']),
        ('ix_development_goals_org_id', 'development_goals', ['org_id']),
        ('ix_development_goals_employee', 'development_goals', ['employee_id']),
        ('ix_development_goals_employee_id', 'development_goals', ['employee_id']),
        ('ix_calibration_rounds_org', 'calibration_rounds', ['org_id']),
        ('ix_calibration_rounds_org_id', 'calibration_rounds', ['org_id']),
        ('ix_calibration_entries_round', 'calibration_entries', ['round_id']),
        ('ix_calibration_entries_round_id', 'calibration_entries', ['round_id']),
        ('ix_calibration_entries_employee_id', 'calibration_entries', ['employee_id']),
        (
            'ix_calibration_entries_employee_skill',
            'calibration_entries',
            ['employee_id', 'skill'],
        ),
        ('ix_change_requests_org_id', 'change_requests', ['org_id']),
        ('ix_review_cycles_org_id', 'review_cycles', ['org_id']),
    ]
    for name, table, columns in missing_indexes:
        if inspector.has_table(table) and name not in _indexes(table):
            op.create_index(name, table, columns, unique=False)

    # --- align change request columns with their ORM enum types --------
    if inspector.has_table('change_requests'):
        with op.batch_alter_table('change_requests', schema=None) as batch_op:
            batch_op.alter_column(
                'change_type',
                existing_type=sa.String(length=48),
                type_=CHANGE_REQUEST_TYPE,
                existing_nullable=False,
            )
            batch_op.alter_column(
                'status',
                existing_type=sa.String(length=20),
                type_=CHANGE_REQUEST_STATUS,
                existing_nullable=False,
            )


def downgrade() -> None:
    inspector = _inspector()

    if inspector.has_table('change_requests'):
        with op.batch_alter_table('change_requests', schema=None) as batch_op:
            batch_op.alter_column(
                'change_type',
                existing_type=CHANGE_REQUEST_TYPE,
                type_=sa.String(length=48),
                existing_nullable=False,
            )
            batch_op.alter_column(
                'status',
                existing_type=CHANGE_REQUEST_STATUS,
                type_=sa.String(length=20),
                existing_nullable=False,
            )

    for name, table in (
        ('ix_review_cycles_org_id', 'review_cycles'),
        ('ix_change_requests_org_id', 'change_requests'),
    ):
        if inspector.has_table(table) and name in _indexes(table):
            op.drop_index(name, table_name=table)

    if inspector.has_table('calibration_entries'):
        op.drop_table('calibration_entries')
    if inspector.has_table('calibration_rounds'):
        op.drop_table('calibration_rounds')
    if inspector.has_table('development_goals'):
        op.drop_table('development_goals')
    if inspector.has_table('employee_skills'):
        op.drop_table('employee_skills')

    user_columns = _columns('users')
    with op.batch_alter_table('users', schema=None) as batch_op:
        if 'mfa_backup_codes' in user_columns:
            batch_op.drop_column('mfa_backup_codes')
        if 'mfa_secret' in user_columns:
            batch_op.drop_column('mfa_secret')
        if 'mfa_enabled' in user_columns:
            batch_op.drop_column('mfa_enabled')
