"""Persist consent evidence and complete publish idempotency fingerprint.

Revision ID: 20260927_03
Revises: 20260927_02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260927_03"
down_revision = "20260927_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_jobs", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("publish_jobs", sa.Column("consented_at", sa.Integer(), nullable=True))
    op.add_column("publish_jobs", sa.Column("consent_version", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("publish_jobs", "consent_version")
    op.drop_column("publish_jobs", "consented_at")
    op.drop_column("publish_jobs", "request_fingerprint")
