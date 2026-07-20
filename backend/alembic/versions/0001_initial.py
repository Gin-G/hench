"""initial schema: items, accounts, transactions, category_overrides, rules

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("item_id", sa.String(), primary_key=True),
        sa.Column("institution_id", sa.String(), nullable=True),
        sa.Column("institution_name", sa.String(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("transactions_cursor", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
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

    op.create_table(
        "accounts",
        sa.Column("account_id", sa.String(), primary_key=True),
        sa.Column(
            "item_id",
            sa.String(),
            sa.ForeignKey("items.item_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("official_name", sa.String(), nullable=True),
        sa.Column("mask", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("subtype", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_accounts_item_id", "accounts", ["item_id"])

    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.String(), primary_key=True),
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
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("iso_currency_code", sa.String(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("authorized_date", sa.Date(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("merchant_name", sa.String(), nullable=True),
        sa.Column("pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payment_channel", sa.String(), nullable=True),
        sa.Column("pfc_primary", sa.String(), nullable=True),
        sa.Column("pfc_detailed", sa.String(), nullable=True),
        sa.Column("pfc_confidence", sa.String(), nullable=True),
        sa.Column("category_primary", sa.String(), nullable=True),
        sa.Column("category_detailed", sa.String(), nullable=True),
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
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_item_id", "transactions", ["item_id"])
    op.create_index("ix_transactions_date", "transactions", ["date"])
    op.create_index("ix_transactions_pfc_primary", "transactions", ["pfc_primary"])
    op.create_index(
        "ix_transactions_category_primary", "transactions", ["category_primary"]
    )

    op.create_table(
        "category_overrides",
        sa.Column(
            "transaction_id",
            sa.String(),
            sa.ForeignKey("transactions.transaction_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("category_primary", sa.String(), nullable=False),
        sa.Column("category_detailed", sa.String(), nullable=True),
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

    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pattern", sa.String(), nullable=False),
        sa.Column(
            "match_type", sa.String(), nullable=False, server_default="contains"
        ),
        sa.Column("category_primary", sa.String(), nullable=False),
        sa.Column("category_detailed", sa.String(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("rules")
    op.drop_table("category_overrides")
    op.drop_index("ix_transactions_category_primary", table_name="transactions")
    op.drop_index("ix_transactions_pfc_primary", table_name="transactions")
    op.drop_index("ix_transactions_date", table_name="transactions")
    op.drop_index("ix_transactions_item_id", table_name="transactions")
    op.drop_index("ix_transactions_account_id", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_accounts_item_id", table_name="accounts")
    op.drop_table("accounts")
    op.drop_table("items")
