"""bills kept up to date from an outside source

A bill with ``source`` set (currently only "longmont", the City of Longmont
utility portal) is written by sync rather than by hand.

Revision ID: 0005_bill_sources
Revises: 0004_nickname_hidden
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_bill_sources"
down_revision = "0004_nickname_hidden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bills", sa.Column("source", sa.String(), nullable=True))
    op.add_column(
        "bills",
        sa.Column("source_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bills",
        sa.Column("source_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("bills", sa.Column("source_error", sa.Text(), nullable=True))
    op.add_column("bills", sa.Column("source_detail", postgresql.JSONB(), nullable=True))
    op.add_column(
        "bills",
        sa.Column(
            "due_date_estimated", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.create_index("ix_bills_source", "bills", ["source"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_bills_source", "bills")
    op.drop_column("bills", "due_date_estimated")
    op.drop_column("bills", "source_detail")
    op.drop_column("bills", "source_error")
    op.drop_column("bills", "source_attempted_at")
    op.drop_column("bills", "source_synced_at")
    op.drop_column("bills", "source")
