"""make employee_id nullable in change_requests (v2)

Revision ID: b2c3d4e5f6g7
Revises: a3b7c1d2e4f5
Create Date: 2026-11-18 15:00:00.000000

"""
from __future__ import annotations

from alembic import op


revision: str = 'b2c3d4e5f6g7'
down_revision: str | None = 'a3b7c1d2e4f5'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Create temporary table with new schema (employee_id nullable)
    op.execute("""
    CREATE TABLE change_requests_tmp (
        id CHAR(32) NOT NULL,
        org_id CHAR(32) NOT NULL,
        code VARCHAR(32) NOT NULL,
        employee_id CHAR(32),
        change_type VARCHAR(48) NOT NULL,
        status VARCHAR(20) NOT NULL,
        effective_date DATE NOT NULL,
        description TEXT,
        old_value TEXT,
        new_value TEXT,
        notes TEXT,
        actor_id CHAR(32),
        approved_by CHAR(32),
        approved_at DATETIME,
        created_at DATETIME NOT NULL,
        CONSTRAINT pk_change_requests PRIMARY KEY (id),
        CONSTRAINT fk_change_requests_approved_by_users FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL,
        CONSTRAINT fk_change_requests_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL,
        CONSTRAINT fk_change_requests_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE CASCADE,
        CONSTRAINT fk_change_requests_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id) ON DELETE CASCADE,
        CONSTRAINT uq_change_requests_org_code UNIQUE (org_id, code)
    )
    """)

    # Copy data from old table to new table
    op.execute("""
    INSERT INTO change_requests_tmp (id, org_id, code, employee_id, change_type, status, effective_date, 
        description, old_value, new_value, notes, actor_id, approved_by, approved_at, created_at)
    SELECT id, org_id, code, employee_id, change_type, status, effective_date, 
        description, old_value, new_value, notes, actor_id, approved_by, approved_at, created_at
    FROM change_requests
    """)

    # Drop old table and rename new table
    op.execute("DROP TABLE change_requests")
    op.execute("ALTER TABLE change_requests_tmp RENAME TO change_requests")

    # Recreate indexes
    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_requests_employee'), ['employee_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_change_requests_org_status'), ['org_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_change_requests_org_type'), ['org_id', 'change_type'], unique=False)


def downgrade() -> None:
    # Revert: make employee_id non-nullable again
    op.execute("""
    CREATE TABLE change_requests_old (
        id CHAR(32) NOT NULL,
        org_id CHAR(32) NOT NULL,
        code VARCHAR(32) NOT NULL,
        employee_id CHAR(32) NOT NULL,
        change_type VARCHAR(48) NOT NULL,
        status VARCHAR(20) NOT NULL,
        effective_date DATE NOT NULL,
        description TEXT,
        old_value TEXT,
        new_value TEXT,
        notes TEXT,
        actor_id CHAR(32),
        approved_by CHAR(32),
        approved_at DATETIME,
        created_at DATETIME NOT NULL,
        CONSTRAINT pk_change_requests PRIMARY KEY (id),
        CONSTRAINT fk_change_requests_approved_by_users FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL,
        CONSTRAINT fk_change_requests_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL,
        CONSTRAINT fk_change_requests_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE CASCADE,
        CONSTRAINT fk_change_requests_org_id_organizations FOREIGN KEY(org_id) REFERENCES organizations (id) ON DELETE CASCADE,
        CONSTRAINT uq_change_requests_org_code UNIQUE (org_id, code)
    )
    """)

    op.execute("""
    INSERT INTO change_requests_old (id, org_id, code, employee_id, change_type, status, effective_date, 
        description, old_value, new_value, notes, actor_id, approved_by, approved_at, created_at)
    SELECT id, org_id, code, employee_id, change_type, status, effective_date, 
        description, old_value, new_value, notes, actor_id, approved_by, approved_at, created_at
    FROM change_requests
    """)

    op.execute("DROP TABLE change_requests")
    op.execute("ALTER TABLE change_requests_old RENAME TO change_requests")

    with op.batch_alter_table('change_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_change_requests_employee'), ['employee_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_change_requests_org_status'), ['org_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_change_requests_org_type'), ['org_id', 'change_type'], unique=False)