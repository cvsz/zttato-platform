"""Add media duration metadata and persistent request quota buckets.

Revision ID: 20260927_02
Revises: 20260924_01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260927_02"
down_revision = "20260924_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_jobs", sa.Column("updated_at", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE publish_jobs SET updated_at = created_at WHERE updated_at IS NULL"))
    op.add_column("media_assets", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.create_table(
        "request_quota_buckets",
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("request_bytes", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("subject_hash", "scope", "window_start", name="pk_request_quota_buckets"),
    )


def downgrade() -> None:
    op.drop_table("request_quota_buckets")
    op.drop_column("media_assets", "duration_ms")
    op.drop_column("publish_jobs", "updated_at")
