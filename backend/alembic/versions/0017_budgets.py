"""monthly budgets, and pushing an alert for each charge against one

A budget is a monthly limit on a spending group or one item in it. After
each sync every new charge against a budget is pushed to the user's ntfy
topic with what is left. alerted_transactions remembers what has been looked
at; everything already here counts as seen, so the first sync after this
does not push the whole history.

Revision ID: 0017_budgets
Revises: 0016_users
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_budgets"
down_revision = "0016_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("ntfy_topic", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column("alerts_enabled", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "transactions", sa.Column("pending_transaction_id", sa.String(), nullable=True)
    )
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("group", sa.String(), nullable=False),
        sa.Column("item", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "user_id", "group", "item", postgresql_nulls_not_distinct=True
        ),
    )
    op.create_table(
        "alerted_transactions",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("transaction_id", sa.String(), primary_key=True),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute(
        "INSERT INTO alerted_transactions (user_id, transaction_id) "
        "SELECT user_id, transaction_id FROM transactions"
    )


def downgrade() -> None:
    op.drop_table("alerted_transactions")
    op.drop_table("budgets")
    op.drop_column("transactions", "pending_transaction_id")
    op.drop_column("users", "alerts_enabled")
    op.drop_column("users", "ntfy_topic")
