"""planned payments stay until changed; detected loan payments get one too

A card's chosen payment was pinned to one due date. It is a planning figure
the user keeps until they change it, so the pin goes. A detected payment
(a mortgage, a student loan Plaid only sees from checking) gets the same:
an amount to plan with in place of Plaid's average.

Revision ID: 0012_planned_payments
Revises: 0011_cycle_payment
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_planned_payments"
down_revision = "0011_cycle_payment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("accounts", "payment_due")
    op.add_column(
        "recurring_streams", sa.Column("amount_override", sa.Numeric(14, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("recurring_streams", "amount_override")
    op.add_column("accounts", sa.Column("payment_due", sa.Date(), nullable=True))
