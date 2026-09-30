"""promotional rates and APR overrides on debts

Revision ID: 0008_promo_rates
Revises: 0007_pay_from
Create Date: 2026-09-29

Plaid reports APRs but never when a promotional rate ends, so the promo is
entered by hand. User edits, like the nickname: sync never writes them.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_promo_rates"
down_revision = "0007_pay_from"
branch_labels = None
depends_on = None

_RATE = sa.Numeric(7, 4)
_MONEY = sa.Numeric(14, 2)


def _promo_columns() -> list[sa.Column]:
    return [
        sa.Column("promo_apr", _RATE, nullable=True),
        sa.Column("promo_ends_on", sa.Date(), nullable=True),
        sa.Column("promo_balance", _MONEY, nullable=True),
        sa.Column(
            "promo_deferred_interest",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.add_column("accounts", sa.Column("apr_override", _RATE, nullable=True))
    for table in ("accounts", "bills"):
        for column in _promo_columns():
            op.add_column(table, column)


def downgrade() -> None:
    for table in ("accounts", "bills"):
        for column in _promo_columns():
            op.drop_column(table, column.name)
    op.drop_column("accounts", "apr_override")
