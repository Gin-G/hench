"""which bank account pays each bill and each card or loan

Revision ID: 0007_pay_from
Revises: 0006_oauth_tokens
Create Date: 2026-09-29

A user edit on both tables, like the nickname: sync never writes it. Null
means the checking pool, which is what the planner assumed before.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_pay_from"
down_revision = "0006_oauth_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("accounts", "bills"):
        op.add_column(
            table,
            sa.Column(
                "pay_from_account_id",
                sa.String(),
                sa.ForeignKey("accounts.account_id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_column("bills", "pay_from_account_id")
    op.drop_column("accounts", "pay_from_account_id")
