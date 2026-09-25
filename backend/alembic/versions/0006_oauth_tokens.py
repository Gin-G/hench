"""oauth tokens, for read-only Gmail

Revision ID: 0006_oauth_tokens
Revises: 0005_bill_sources
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_oauth_tokens"
down_revision = "0005_bill_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_tokens",
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("account_email", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("oauth_tokens")
