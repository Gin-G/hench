"""account balances, liabilities, recurring streams

Adds the forward-looking inputs the planner needs: balances on accounts,
credit/loan detail from /liabilities/get, and detected recurring inflow and
outflow streams from /transactions/recurring/get.

Revision ID: 0002_planner_inputs
Revises: 0001_initial
Create Date: 2026-08-03

The revision id is kept short deliberately: alembic_version.version_num is
varchar(32), so a longer descriptive id fails at the point alembic stamps the
version, after the schema changes have already applied.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_planner_inputs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)
RATE = sa.Numeric(7, 4)


def upgrade() -> None:
    op.add_column("accounts", sa.Column("current_balance", MONEY, nullable=True))
    op.add_column("accounts", sa.Column("available_balance", MONEY, nullable=True))
    op.add_column("accounts", sa.Column("credit_limit", MONEY, nullable=True))
    op.add_column(
        "accounts", sa.Column("iso_currency_code", sa.String(), nullable=True)
    )
    op.add_column(
        "accounts",
        sa.Column("balances_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "liabilities",
        sa.Column(
            "account_id",
            sa.String(),
            sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("liability_type", sa.String(), nullable=False),
        sa.Column("next_payment_due_date", sa.Date(), nullable=True),
        sa.Column("minimum_payment_amount", MONEY, nullable=True),
        sa.Column("last_payment_amount", MONEY, nullable=True),
        sa.Column("last_payment_date", sa.Date(), nullable=True),
        sa.Column("is_overdue", sa.Boolean(), nullable=True),
        sa.Column("last_statement_balance", MONEY, nullable=True),
        sa.Column("last_statement_issue_date", sa.Date(), nullable=True),
        sa.Column("purchase_apr", RATE, nullable=True),
        sa.Column("aprs", postgresql.JSONB(), nullable=True),
        sa.Column("interest_rate_percentage", RATE, nullable=True),
        sa.Column("origination_principal_amount", MONEY, nullable=True),
        sa.Column("origination_date", sa.Date(), nullable=True),
        sa.Column("outstanding_interest_amount", MONEY, nullable=True),
        sa.Column("expected_payoff_date", sa.Date(), nullable=True),
        sa.Column("loan_status", sa.String(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "recurring_streams",
        sa.Column("stream_id", sa.String(), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(),
            sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.String(),
            sa.ForeignKey("items.item_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("merchant_name", sa.String(), nullable=True),
        sa.Column("frequency", sa.String(), nullable=True),
        sa.Column("average_amount", MONEY, nullable=True),
        sa.Column("last_amount", MONEY, nullable=True),
        sa.Column("iso_currency_code", sa.String(), nullable=True),
        sa.Column("first_date", sa.Date(), nullable=True),
        sa.Column("last_date", sa.Date(), nullable=True),
        sa.Column("predicted_next_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("category_primary", sa.String(), nullable=True),
        sa.Column("category_detailed", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_recurring_streams_account_id", "recurring_streams", ["account_id"]
    )
    op.create_index("ix_recurring_streams_item_id", "recurring_streams", ["item_id"])
    op.create_index(
        "ix_recurring_streams_direction", "recurring_streams", ["direction"]
    )
    op.create_index(
        "ix_recurring_streams_predicted_next_date",
        "recurring_streams",
        ["predicted_next_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_recurring_streams_predicted_next_date", "recurring_streams")
    op.drop_index("ix_recurring_streams_direction", "recurring_streams")
    op.drop_index("ix_recurring_streams_item_id", "recurring_streams")
    op.drop_index("ix_recurring_streams_account_id", "recurring_streams")
    op.drop_table("recurring_streams")
    op.drop_table("liabilities")
    op.drop_column("accounts", "balances_updated_at")
    op.drop_column("accounts", "iso_currency_code")
    op.drop_column("accounts", "credit_limit")
    op.drop_column("accounts", "available_balance")
    op.drop_column("accounts", "current_balance")
