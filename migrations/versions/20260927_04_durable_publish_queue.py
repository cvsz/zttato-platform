"""Persist encrypted request data for durable publish processing.

Revision ID: 20260927_04
Revises: 20260927_03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260927_04"
down_revision = "20260927_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_jobs", sa.Column("request_cipher", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("publish_jobs", "request_cipher")
