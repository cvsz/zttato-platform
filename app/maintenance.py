"""Scheduled deletion of expired local authorization and media records."""

import logging
import time
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from app.db import BrowserSession, LinkedAccount, MediaAsset, OAuthRequest, PublishJob, QuotaBucket


LOGGER = logging.getLogger("zttato.maintenance")
_CLEANUP_LOCK_ID = 7_415_027_092
_TERMINAL_JOB_STATES = frozenset(
    {"FAILED", "INITIATION_FAILED", "PUBLISH_COMPLETE", "COMPLETE", "COMPLETED", "CANCELLED", "CANCELED"}
)


def _local_upload_path(media_dir: str | Path, stored_path: str) -> Path | None:
    """Return a path only when it resolves beneath the configured media root."""
    root = Path(media_dir).resolve()
    raw = Path(stored_path)
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    if resolved == root or not resolved.is_relative_to(root):
        return None
    return resolved


def remove_local_upload(media_dir: str | Path, stored_path: str) -> bool:
    path = _local_upload_path(media_dir, stored_path)
    if path is None:
        # Photo-set records store normalized JSON instead of a local file path.
        return True
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def run_cleanup(
    session_factory: sessionmaker,
    media_dir: str | Path,
    *,
    media_retention_days: int,
    record_retention_days: int,
    now: int | None = None,
) -> dict[str, int | bool]:
    """Delete expired rows and files; safe to call repeatedly from multiple replicas."""
    timestamp = int(time.time()) if now is None else now
    media_cutoff = timestamp - media_retention_days * 86_400
    record_cutoff = timestamp - record_retention_days * 86_400
    result: dict[str, int | bool] = {
        "skipped_lock": False,
        "oauth_requests": 0,
        "sessions": 0,
        "linked_accounts": 0,
        "media_assets": 0,
        "publish_jobs": 0,
        "quota_buckets": 0,
        "file_delete_failures": 0,
    }

    with session_factory() as session:  # type: Session
        if session.get_bind().dialect.name == "postgresql":
            acquired = session.scalar(select(func.pg_try_advisory_xact_lock(_CLEANUP_LOCK_ID)))
            if not acquired:
                session.rollback()
                result["skipped_lock"] = True
                return result

        expired_oauth = session.execute(delete(OAuthRequest).where(OAuthRequest.expires_at <= timestamp)).rowcount or 0
        result["oauth_requests"] = expired_oauth

        expired_sessions = session.scalars(select(BrowserSession).where(BrowserSession.expires_at <= timestamp)).all()
        expired_ids = {row.id for row in expired_sessions}
        blocked_expired_ids: set[str] = set()
        for expired in expired_sessions:
            assets = session.scalars(select(MediaAsset).where(MediaAsset.session_id == expired.id)).all()
            deleted_all_files = True
            for asset in assets:
                if not remove_local_upload(media_dir, asset.path):
                    result["file_delete_failures"] = int(result["file_delete_failures"]) + 1
                    deleted_all_files = False
            if not deleted_all_files:
                blocked_expired_ids.add(expired.id)
                continue
            result["publish_jobs"] = int(result["publish_jobs"]) + (
                session.execute(delete(PublishJob).where(PublishJob.session_id == expired.id)).rowcount or 0
            )
            result["linked_accounts"] = int(result["linked_accounts"]) + (
                session.execute(delete(LinkedAccount).where(LinkedAccount.session_id == expired.id)).rowcount or 0
            )
            session.execute(delete(OAuthRequest).where(OAuthRequest.session_id == expired.id))
            result["media_assets"] = int(result["media_assets"]) + (
                session.execute(delete(MediaAsset).where(MediaAsset.session_id == expired.id)).rowcount or 0
            )
            result["sessions"] = int(result["sessions"]) + (
                session.execute(delete(BrowserSession).where(BrowserSession.id == expired.id)).rowcount or 0
            )

        old_terminal_jobs = (
            session.execute(
                delete(PublishJob).where(
                    PublishJob.updated_at <= record_cutoff,
                    PublishJob.status.in_(_TERMINAL_JOB_STATES),
                    *([PublishJob.session_id.not_in(blocked_expired_ids)] if blocked_expired_ids else []),
                )
            ).rowcount
            or 0
        )
        result["publish_jobs"] = int(result["publish_jobs"]) + old_terminal_jobs

        old_assets = session.scalars(select(MediaAsset).where(MediaAsset.created_at <= media_cutoff)).all()
        for asset in old_assets:
            if asset.session_id in expired_ids:
                continue
            jobs = session.scalars(select(PublishJob).where(PublishJob.media_id == asset.id)).all()
            if any(job.status not in _TERMINAL_JOB_STATES or job.created_at > record_cutoff for job in jobs):
                continue
            if not remove_local_upload(media_dir, asset.path):
                result["file_delete_failures"] = int(result["file_delete_failures"]) + 1
                continue
            for job in jobs:
                session.delete(job)
                result["publish_jobs"] = int(result["publish_jobs"]) + 1
            session.delete(asset)
            result["media_assets"] = int(result["media_assets"]) + 1

        result["quota_buckets"] = (
            session.execute(delete(QuotaBucket).where(QuotaBucket.window_start <= timestamp - 2 * 86_400)).rowcount or 0
        )
        session.commit()

    if result["file_delete_failures"]:
        LOGGER.error("retention_cleanup_file_delete_failed count=%d", result["file_delete_failures"])
    LOGGER.info(
        "retention_cleanup_complete sessions=%d accounts=%d oauth=%d media=%d jobs=%d quotas=%d",
        result["sessions"],
        result["linked_accounts"],
        result["oauth_requests"],
        result["media_assets"],
        result["publish_jobs"],
        result["quota_buckets"],
    )
    return result
