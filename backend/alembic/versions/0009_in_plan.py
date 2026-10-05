"""accounts can be left out of the plan

An account left out stays linked (its transactions still count toward cash
flow) but its balance, the money landing in it and the payments drawn from
it are not part of the forward-looking plan — e.g. a spouse's own checking.

Revision ID: 0009_in_plan
Revises: 0008_promo_rates
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_in_plan"
down_revision = "0008_promo_rates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("in_plan", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("accounts", "in_plan")
