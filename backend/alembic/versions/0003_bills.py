"""manually entered bills

Bills and debts Plaid cannot see — rent, utilities on unlinked cards, loans at
unlinked lenders — entered by hand from the UI.

Revision ID: 0003_bills
Revises: 0002_planner_inputs
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_bills"
down_revision = "0002_planner_inputs"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)
RATE = sa.Numeric(7, 4)


def upgrade() -> None:
    op.create_table(
        "bills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("amount", MONEY, nullable=True),
        sa.Column(
            "frequency", sa.String(), server_default="monthly", nullable=False
        ),
        sa.Column("next_due_date", sa.Date(), nullable=False),
        sa.Column("due_day", sa.Integer(), nullable=False),
        sa.Column("autopay", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("balance", MONEY, nullable=True),
        sa.Column("apr", RATE, nullable=True),
        sa.Column("last_paid_date", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
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
    op.create_index("ix_bills_next_due_date", "bills", ["next_due_date"])


def downgrade() -> None:
    op.drop_index("ix_bills_next_due_date", "bills")
    op.drop_table("bills")
