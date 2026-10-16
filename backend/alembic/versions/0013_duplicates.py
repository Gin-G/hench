"""spot a detected payment that is a linked card's own payment

Plaid lists the transactions behind each recurring stream; keeping them lets
a stream seen from checking be matched against the payments a linked card
received. The user then keeps one: hiding the stream as before, or the
card's own payment row. Or keeps both, which stops the pair being flagged.

Revision ID: 0013_duplicates
Revises: 0012_planned_payments
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_duplicates"
down_revision = "0012_planned_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recurring_streams",
        sa.Column("transaction_ids", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "recurring_streams",
        sa.Column("not_duplicate", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "accounts",
        sa.Column("payment_hidden", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("accounts", "payment_hidden")
    op.drop_column("recurring_streams", "not_duplicate")
    op.drop_column("recurring_streams", "transaction_ids")
