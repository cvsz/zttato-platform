"""Verify that a fresh schema can upgrade and downgrade without production data."""

from pathlib import Path
from shutil import copytree

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db import MIGRATION_HEAD

ROOT = Path(__file__).resolve().parents[1]


def test_versioned_fresh_migration(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "migration.db")
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert "alembic_version" in inspector.get_table_names()
    assert set(
        (
            "browser_sessions",
            "oauth_requests",
            "linked_accounts",
            "media_assets",
            "publish_jobs",
            "request_quota_buckets",
        )
    ) <= set(inspector.get_table_names())
    assert "duration_ms" in {column["name"] for column in inspector.get_columns("media_assets")}
    assert "updated_at" in {column["name"] for column in inspector.get_columns("publish_jobs")}
    assert {
        "request_fingerprint",
        "consented_at",
        "consent_version",
        "request_cipher",
    } <= {column["name"] for column in inspector.get_columns("publish_jobs")}
    with engine.connect() as connection:
        from sqlalchemy import text

        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == MIGRATION_HEAD
    engine.dispose()
    command.downgrade(cfg, "base")
    inspector = inspect(create_engine(url))
    assert "publish_jobs" not in inspector.get_table_names()


def test_upgrade_preserves_existing_alembic_data(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "preexisting.db")
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "20260924_01")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO browser_sessions (id, csrf_hash, expires_at, created_at) VALUES ('session', 'csrf', 9999999999, 1700000000)"
        )
        connection.exec_driver_sql(
            "INSERT INTO media_assets (id, session_id, filename, size, path, created_at) VALUES ('media', 'session', 'clip.mp4', 1, '/synthetic/clip.mp4', 1700000000)"
        )
        connection.exec_driver_sql(
            "INSERT INTO publish_jobs (id, session_id, idempotency_key, media_id, mode, status, checked_at, created_at) VALUES ('job', 'session', 'synthetic-idem-key', 'media', 'draft', 'PROCESSING', 0, 1700000000)"
        )
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        from sqlalchemy import text

        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == MIGRATION_HEAD
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM media_assets WHERE id = 'media' AND duration_ms IS NULL")) == 1
        )
        assert connection.scalar(text("SELECT updated_at FROM publish_jobs WHERE id = 'job'")) == 1700000000
        assert connection.scalar(text("SELECT request_fingerprint FROM publish_jobs WHERE id = 'job'")) is None
        assert connection.scalar(text("SELECT consented_at FROM publish_jobs WHERE id = 'job'")) is None
        assert connection.scalar(text("SELECT request_cipher FROM publish_jobs WHERE id = 'job'")) is None
    engine.dispose()


def test_new_migration_template_renders(tmp_path):
    scripts = tmp_path / "migrations"
    copytree(ROOT / "migrations", scripts)
    cfg = Config()
    cfg.set_main_option("script_location", str(scripts))
    generated = command.revision(cfg, message="template smoke", rev_id="20260924_02")
    rendered = Path(generated.path).read_text(encoding="utf-8")
    assert '"""template smoke' in rendered
    assert "${message}" not in rendered
