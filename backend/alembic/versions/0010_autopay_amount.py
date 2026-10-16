"""a fixed autopay amount on a card or loan

The bank pays this each month in place of the minimum, so the timeline and
the promo payoff maths use it instead.

Revision ID: 0010_autopay_amount
Revises: 0009_in_plan
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_autopay_amount"
down_revision = "0009_in_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("autopay_amount", sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "autopay_amount")
