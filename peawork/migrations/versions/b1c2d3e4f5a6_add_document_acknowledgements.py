"""Add document acknowledgements.

Adds a `document_acknowledgements` table that records when an employee (or their
manager) has read and acknowledged a document. Documents also gain
`acknowledged_at` / `acknowledged_by` summary columns for quick filtering.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "0a2cb4e18832"
branch_labels: list[str] | None = None
depends_on: list[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_acknowledgements",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, default=sa.func.uuid4()),
        sa.Column(
            "org_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "acknowledger_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=False,
        ),
        sa.Column(
            "acknowledger_employee_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ip",
            sa.String(64),
            nullable=True,
        ),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.UniqueConstraint(
            "document_id",
            "acknowledger_id",
            name="uq_document_acknowledgements_doc_user",
        ),
        sa.Index(
            "ix_document_acknowledgements_org_created",
            "org_id",
            "acknowledged_at",
        ),
    )
    op.create_index(
        "ix_document_acknowledgements_acknowledger_id",
        "document_acknowledgements",
        ["acknowledger_id"],
    )
    op.add_column(
        "documents",
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "acknowledged_by",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index("ix_documents_acknowledged_by", "documents", ["acknowledged_by"])


def downgrade() -> None:
    op.drop_column("documents", "acknowledged_by")
    op.drop_column("documents", "acknowledged_at")
    op.drop_table("document_acknowledgements")