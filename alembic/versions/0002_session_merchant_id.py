"""T3-review: add session.merchant_id for per-merchant console scoping

Revision ID: 0002_session_merchant_id
Revises: 0001_initial_schema
Create Date: 2026-09-03

Adds a nullable merchant_id FK to `session` so the merchant console can scope its
dashboards to one merchant instead of aggregating every merchant in the DB
(deep-review finding). Backfills existing rows to the single product-owning
merchant when exactly one exists, so a live demo DB's historical sessions keep
showing after the migration rather than vanishing. Run against the Supabase
DIRECT connection (port 5432), never the pooler — see alembic/env.py.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_session_merchant_id"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session", sa.Column("merchant_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_session_merchant_id"), "session", ["merchant_id"], unique=False)
    op.create_foreign_key("fk_session_merchant_id", "session", "merchant",
                          ["merchant_id"], ["id"])

    # Best-effort backfill: if exactly one merchant actually owns products (the real
    # demo merchant, as opposed to leftover throwaway test merchants), claim all
    # existing sessions for it so historical dashboard data survives the migration.
    # If zero or several product-owning merchants exist, leave rows NULL (the console
    # query treats NULL as "legacy, still visible" for backward-compat).
    op.execute(
        """
        UPDATE session SET merchant_id = m.id
        FROM (
            SELECT merchant_id AS id
            FROM product
            GROUP BY merchant_id
        ) m
        WHERE session.merchant_id IS NULL
          AND (SELECT COUNT(DISTINCT merchant_id) FROM product) = 1
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_session_merchant_id", "session", type_="foreignkey")
    op.drop_index(op.f("ix_session_merchant_id"), table_name="session")
    op.drop_column("session", "merchant_id")
