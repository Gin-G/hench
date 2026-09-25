"""account nicknames and hidden recurring streams

Revision ID: 0004_nickname_hidden
Revises: 0003_bills
Create Date: 2026-09-25

Both are user edits on Plaid-owned rows. The sync upserts set only the
columns they list, so neither is overwritten by the next sync.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_nickname_hidden"
down_revision = "0003_bills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("nickname", sa.String(), nullable=True))
    op.add_column(
        "recurring_streams",
        sa.Column("hidden", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("recurring_streams", "hidden")
    op.drop_column("accounts", "nickname")
