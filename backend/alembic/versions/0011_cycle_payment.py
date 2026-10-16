"""a payment amount chosen for a card's current cycle

Set from the Upcoming row: pay $200 instead of the $40 minimum, or the whole
balance, this cycle only. Tied to the due date it was chosen for, so it
lapses on its own once the next statement moves the due date on.

Revision ID: 0011_cycle_payment
Revises: 0010_autopay_amount
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_cycle_payment"
down_revision = "0010_autopay_amount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("payment_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("accounts", sa.Column("payment_due", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "payment_due")
    op.drop_column("accounts", "payment_amount")
