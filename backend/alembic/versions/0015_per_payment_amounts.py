"""a chosen payment amount belongs to one payment, not every month

One amount per card (or detected loan payment) stood for every month, so
setting next month's changed this month's too. Each payment now gets its
own, by due date; one with none set goes at the last amount paid. The
amount already set is kept for the payment due next.

Revision ID: 0015_per_payment_amounts
Revises: 0014_net_worth
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_per_payment_amounts"
down_revision = "0014_net_worth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planned_payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.String(),
            sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "stream_id",
            sa.String(),
            sa.ForeignKey("recurring_streams.stream_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("account_id", "due_date"),
        sa.UniqueConstraint("stream_id", "due_date"),
    )
    op.create_index("ix_planned_payments_account_id", "planned_payments", ["account_id"])
    op.create_index("ix_planned_payments_stream_id", "planned_payments", ["stream_id"])
    op.execute(
        """
        INSERT INTO planned_payments (account_id, due_date, amount)
        SELECT a.account_id, l.next_payment_due_date, a.payment_amount
        FROM accounts a JOIN liabilities l ON l.account_id = a.account_id
        WHERE a.payment_amount IS NOT NULL AND l.next_payment_due_date IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO planned_payments (stream_id, due_date, amount)
        SELECT stream_id, predicted_next_date, amount_override
        FROM recurring_streams
        WHERE amount_override IS NOT NULL AND predicted_next_date IS NOT NULL
        """
    )
    op.drop_column("accounts", "payment_amount")
    op.drop_column("recurring_streams", "amount_override")


def downgrade() -> None:
    op.add_column("accounts", sa.Column("payment_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column(
        "recurring_streams", sa.Column("amount_override", sa.Numeric(14, 2), nullable=True)
    )
    op.drop_table("planned_payments")
