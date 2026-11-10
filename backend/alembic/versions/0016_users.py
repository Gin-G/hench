"""separate users, each with their own data and their own key

Everyone who signs in gets their own linked banks, bills, plan and net
worth. Every table gains the user it belongs to; what is here already goes
to the owner (HENCH_OWNER_EMAIL, else the first allowed email). Columns that
will hold text encrypted with the user's key become plain text: JSON ones
included, since a ciphertext is not JSON. Nothing is encrypted here (that
needs OpenBao, which a migration should not); ``python -m app.cli encrypt``
does that, and plaintext reads fine until it has.

Revision ID: 0016_users
Revises: 0015_per_payment_amounts
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa

from app.config import get_settings

revision = "0016_users"
down_revision = "0015_per_payment_amounts"
branch_labels = None
depends_on = None

_OWNED = (
    "items",
    "accounts",
    "transactions",
    "category_overrides",
    "liabilities",
    "recurring_streams",
    "bills",
    "planned_payments",
    "holdings",
    "holding_values",
    "oauth_tokens",
    "rules",
)

_TO_TEXT = {
    "accounts": ("name", "official_name", "mask", "nickname"),
    "transactions": ("name", "merchant_name"),
    "recurring_streams": ("description", "merchant_name"),
    "bills": ("name",),
    "holdings": ("name",),
    "oauth_tokens": ("account_email",),
}
_JSON_TO_TEXT = {"liabilities": "raw", "bills": "source_detail"}


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("wrapped_key", sa.Text(), nullable=True),
        sa.Column("key_provider", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    owner = get_settings().owner
    op.execute(sa.text("INSERT INTO users (email) VALUES (:e)").bindparams(e=owner))

    for table in _OWNED:
        op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
        op.execute(
            sa.text(f"UPDATE {table} SET user_id = (SELECT id FROM users WHERE email = :e)")
            .bindparams(e=owner)
        )
        op.alter_column(table, "user_id", nullable=False)
        op.create_foreign_key(
            f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        if table != "oauth_tokens":
            op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    # A Google grant per provider per user.
    op.drop_constraint("oauth_tokens_pkey", "oauth_tokens", type_="primary")
    op.create_primary_key("oauth_tokens_pkey", "oauth_tokens", ["user_id", "provider"])
    # A fetched bill per source per user.
    op.drop_index("ix_bills_source", table_name="bills")
    op.create_unique_constraint("bills_user_id_source_key", "bills", ["user_id", "source"])

    for table, columns in _TO_TEXT.items():
        for column in columns:
            op.alter_column(table, column, type_=sa.Text())
    for table, column in _JSON_TO_TEXT.items():
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE text USING {column}::text")


def downgrade() -> None:
    # Only safe before ``app.cli encrypt`` has run: ciphertext is not JSON.
    for table, column in _JSON_TO_TEXT.items():
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE jsonb USING {column}::jsonb")
    op.drop_constraint("bills_user_id_source_key", "bills", type_="unique")
    op.create_index("ix_bills_source", "bills", ["source"], unique=True)
    op.drop_constraint("oauth_tokens_pkey", "oauth_tokens", type_="primary")
    op.create_primary_key("oauth_tokens_pkey", "oauth_tokens", ["provider"])
    for table in _OWNED:
        op.drop_column(table, "user_id")
    op.drop_table("users")
