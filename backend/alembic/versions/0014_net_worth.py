"""what is owned and owed outside Plaid, and which payments can go

The home, the car and retirement accounts are not linked, nor are the
mortgage and loans at lenders Plaid cannot reach, so they are entered by
hand. Each new value is kept with its date, so net worth can be charted
over time. A detected payment can be marked as debt (can be paid off), an
essential (cannot go) or optional, in place of the guess from its category.

Revision ID: 0014_net_worth
Revises: 0013_duplicates
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_net_worth"
down_revision = "0013_duplicates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "holdings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("value", sa.Numeric(14, 2), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "secured_by_id",
            sa.Integer(),
            sa.ForeignKey("holdings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "holding_values",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "holding_id",
            sa.Integer(),
            sa.ForeignKey("holdings.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        ),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(14, 2), nullable=False),
        sa.UniqueConstraint("holding_id", "as_of"),
    )
    op.add_column("recurring_streams", sa.Column("commitment", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("recurring_streams", "commitment")
    op.drop_table("holding_values")
    op.drop_table("holdings")
