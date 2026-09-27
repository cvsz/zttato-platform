"""zTTato API. All TikTok secrets stay server-side; a browser session is not a TikTok access token."""

import asyncio
import html
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from urllib.parse import urlencode, urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt, StrictStr, ValidationError, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import (
    Settings,
    load_settings,
    media_url_matches_verified_prefix,
    validate_deployment_config,
)
from app.db import (
    BrowserSession,
    LinkedAccount,
    MediaAsset,
    OAuthRequest,
    PublishJob,
    QuotaBucket,
    MIGRATION_HEAD,
    make_session_factory,
)
from app.media import InvalidMP4, mp4_duration_ms
from app.maintenance import recover_stale_publish_jobs, remove_local_upload, run_cleanup
from app.security import TokenCipher, browser_session, digest, new_browser_session, require_csrf
from app.tiktok import TikTokClient
from app.zwallet import (
    ZWalletBillingClient,
    ZWalletInvalidResponse,
    ZWalletNotConfigured,
    ZWalletTimedOut,
    ZWalletUnavailable,
)
from app.i18n.routes import router as i18n_router

WEB = Path(__file__).resolve().parent.parent / "web"
COOKIE = "zttato_session"
CSRF = "zttato_csrf"
LOGGER = logging.getLogger("zttato.app")
_REQUEST_COUNT: Counter[tuple[str, int]] = Counter()
_REQUEST_DURATION: Counter[tuple[str, int]] = Counter()
_OAUTH_ERRORS: Counter[int] = Counter()
_CLEANUP_RUNS: Counter[str] = Counter()
_CLEANUP_DELETED: Counter[str] = Counter()
_CLEANUP_FILE_DELETE_FAILURES = 0
_CLEANUP_LAST_SUCCESS = 0
_VIDEO_UPLOADS = 0
_VIDEO_UPLOAD_BYTES = 0
_METRICS_LOCK = threading.Lock()
MAX_TIKTOK_API_VIDEO_DURATION_SEC = 600
PHOTO_MEDIA_PREFIX = "fernet:v1:"
PUBLISH_CONSENT_VERSION = "publish-consent-v1"


AVATAR_CDN_SUFFIXES = (
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokcdn-eu.com",
    "tiktokcdn-in.com",
)


def safe_avatar_url(value: object) -> str | None:
    """Limit externally rendered profile images to HTTPS TikTok CDN URLs."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in (None, 443)
            or not any(host == suffix or host.endswith("." + suffix) for suffix in AVATAR_CDN_SUFFIXES)
        ):
            return None
    except ValueError:
        return None
    return value


class PublishInput(BaseModel):
    media_id: str
    mode: str
    idempotency_key: str = Field(min_length=16, max_length=128)
    caption: str = Field(default="", max_length=4000)
    privacy: str | None = None
    consent: bool
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    brand_content_toggle: bool = False
    brand_organic_toggle: bool = False
    is_aigc: bool = False
    media_type: str = Field(default="video", pattern="^(video|photo)$")
    photo_images: list[str] | None = None
    photo_cover_index: int | None = None

    @model_validator(mode="after")
    def caption_fits_tiktok_utf16_limit(self):
        limit = 4000 if self.media_type == "photo" else 2200
        if len(self.caption.encode("utf-16-le")) // 2 > limit:
            raise ValueError(f"Caption must not exceed {limit} UTF-16 code units")
        return self


class PhotoMediaInput(BaseModel):
    photo_images: list[StrictStr] = Field(min_length=1, max_length=35)
    photo_cover_index: StrictInt = 0

    model_config = {"extra": "forbid"}

    @field_validator("photo_images")
    @classmethod
    def validate_photo_urls(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or len(value) > 2048 or value != value.strip() or any(char.isspace() for char in value):
                raise ValueError("Photo URL is empty, too long, or contains unescaped whitespace")
            try:
                parsed = urlparse(value)
                host = (parsed.hostname or "").lower().rstrip(".")
                port = parsed.port
            except ValueError as exc:
                raise ValueError("Photo URL is invalid") from exc
            try:
                ipaddress.ip_address(host)
                is_ip_address = True
            except ValueError:
                is_ip_address = False
            if (
                parsed.scheme != "https"
                or not host
                or "." not in host
                or is_ip_address
                or parsed.username
                or parsed.password
                or parsed.fragment
                or port not in (None, 443)
            ):
                raise ValueError("Photo URLs must use HTTPS on a public domain without credentials or fragments")
            suffix = Path(parsed.path).suffix.lower()
            if suffix and suffix not in {".jpg", ".jpeg", ".webp"}:
                raise ValueError("TikTok Photo Post supports JPEG and WebP image URLs only")
        return values

    @model_validator(mode="after")
    def cover_index_must_select_photo(self):
        if self.photo_cover_index < 0 or self.photo_cover_index >= len(self.photo_images):
            raise ValueError("photo_cover_index must select an image in photo_images")
        return self


class InvoiceIntentInput(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    amount_minor: int = Field(gt=0, le=9_007_199_254_740_991, strict=True)

    model_config = {"extra": "forbid"}


def _quota_subject(request: Request, settings: Settings) -> str:
    cookie = request.cookies.get(COOKIE)
    if cookie:
        identity = "session:" + cookie
    else:
        identity = "client:" + (request.client.host if request.client else "unknown")
    key_material = settings.encryption_key or settings.client_secret or "zttato-development-quota-key"
    key = hashlib.sha256(key_material.encode("utf-8") + b"|request-quota-subject").digest()
    return hmac.new(key, identity.encode("utf-8"), hashlib.sha256).hexdigest()


def _publish_request_fingerprint(payload: PublishInput, settings: Settings) -> str:
    """HMAC the effective publish choices without storing caption or media URLs."""
    effective_request = {
        "media_id": payload.media_id,
        "mode": payload.mode,
        "caption": payload.caption,
        "privacy": payload.privacy,
        "consent": payload.consent,
        "disable_comment": payload.disable_comment,
        "disable_duet": payload.disable_duet,
        "disable_stitch": payload.disable_stitch,
        "brand_content_toggle": payload.brand_content_toggle,
        "brand_organic_toggle": payload.brand_organic_toggle,
        "is_aigc": payload.is_aigc,
        "media_type": payload.media_type,
    }
    serialized = json.dumps(effective_request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    secret = settings.encryption_key or settings.client_secret or "development-only-publish-fingerprint-key"
    key = hmac.new(secret.encode("utf-8"), b"zttato/publish-fingerprint/v1", hashlib.sha256).digest()
    return hmac.new(key, serialized.encode("utf-8"), hashlib.sha256).hexdigest()


def _safe_publish_failure_reason(exc: Exception, *, provider_init_started: bool) -> str:
    """Return actionable failure guidance without persisting provider or user supplied text."""
    status_code = exc.status_code if isinstance(exc, HTTPException) else None
    if status_code == 401:
        return "TikTok authorization expired or unavailable; reconnect the account, then check this job before resubmitting."
    if status_code == 403:
        return "TikTok authorization is missing a required permission; reconnect with the requested scope before resubmitting."
    if status_code == 404:
        return (
            "The uploaded video or connected account is unavailable; restore it and check this job before resubmitting."
        )
    if status_code == 413:
        return "The uploaded video exceeds the configured size limit; choose a smaller file before resubmitting."
    if status_code == 415:
        return "The uploaded file is not a supported MP4; choose a valid MP4 before resubmitting."
    if status_code == 422:
        return "TikTok rejected the video duration or publish settings; review them and check this job before resubmitting."
    if status_code == 429:
        return "TikTok or the application rate limit was reached; wait, check this job, then resubmit if needed."
    if status_code is not None and status_code < 500:
        return f"Publish request was rejected with HTTP {status_code}; review the account and settings before resubmitting."
    if not provider_init_started and status_code is not None:
        return f"Publish preflight failed with HTTP {status_code} before TikTok initialization; refresh job status before resubmitting."
    if not provider_init_started:
        return "Publish preflight failed before TikTok publish initialization; check the account, video, and job status before resubmitting."
    return "TikTok did not confirm request acceptance. Keep the same request key and check job status before retrying."


def _increment_quota(
    session: Session,
    *,
    subject_hash: str,
    scope: str,
    limit: int,
    window_seconds: int,
    request_bytes: int = 0,
    byte_limit: int | None = None,
    now: int | None = None,
) -> int | None:
    timestamp = int(time.time()) if now is None else now
    window_start = timestamp - timestamp % window_seconds
    values = {
        "subject_hash": subject_hash,
        "scope": scope,
        "window_start": window_start,
        "request_count": 1,
        "request_bytes": request_bytes,
    }
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert is None:
        raise SQLAlchemyError("Request quotas require PostgreSQL or SQLite")
    statement = insert(QuotaBucket).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[QuotaBucket.subject_hash, QuotaBucket.scope, QuotaBucket.window_start],
        set_={
            "request_count": QuotaBucket.request_count + 1,
            "request_bytes": QuotaBucket.request_bytes + request_bytes,
        },
    ).returning(QuotaBucket.request_count, QuotaBucket.request_bytes)
    count, total_bytes = session.execute(statement).one()
    if count > limit or (byte_limit is not None and total_bytes > byte_limit):
        session.rollback()
        return max(1, window_seconds - (timestamp - window_start))
    session.commit()
    return None


def _record_request_metric(method: str, status_code: int, duration_seconds: float) -> None:
    allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
    key = (method if method in allowed_methods else "OTHER", status_code)
    with _METRICS_LOCK:
        _REQUEST_COUNT[key] += 1
        _REQUEST_DURATION[key] += duration_seconds


def _record_oauth_error(status_code: int) -> None:
    if status_code >= 400:
        with _METRICS_LOCK:
            _OAUTH_ERRORS[status_code] += 1


def _record_video_upload(size: int) -> None:
    global _VIDEO_UPLOADS, _VIDEO_UPLOAD_BYTES
    with _METRICS_LOCK:
        _VIDEO_UPLOADS += 1
        _VIDEO_UPLOAD_BYTES += size


def _record_cleanup_result(result: dict[str, int | bool] | None) -> None:
    global _CLEANUP_FILE_DELETE_FAILURES, _CLEANUP_LAST_SUCCESS
    with _METRICS_LOCK:
        if result is None:
            _CLEANUP_RUNS["failure"] += 1
            return
        _CLEANUP_RUNS["success"] += 1
        _CLEANUP_LAST_SUCCESS = int(time.time())
        if not result.get("skipped_lock"):
            for resource in (
                "sessions",
                "linked_accounts",
                "oauth_requests",
                "media_assets",
                "publish_jobs",
                "quota_buckets",
            ):
                _CLEANUP_DELETED[resource] += int(result.get(resource, 0))
            _CLEANUP_FILE_DELETE_FAILURES += int(result.get("file_delete_failures", 0))


def _prometheus_metrics(
    media_dir: str | None = None,
    *,
    publish_queue_depth: int = 0,
    publish_queue_oldest_age_seconds: int = 0,
) -> str:
    with _METRICS_LOCK:
        counts = dict(_REQUEST_COUNT)
        durations = dict(_REQUEST_DURATION)
        oauth_errors = dict(_OAUTH_ERRORS)
        video_uploads = _VIDEO_UPLOADS
        video_upload_bytes = _VIDEO_UPLOAD_BYTES
    lines = [
        "# HELP zttato_http_requests_total Completed HTTP requests by method and response code.",
        "# TYPE zttato_http_requests_total counter",
    ]
    for (method, status), count in sorted(counts.items()):
        lines.append(f'zttato_http_requests_total{{method="{method}",status="{status}"}} {count}')
    lines.extend(
        [
            "# HELP zttato_http_request_duration_seconds_sum Total observed request duration.",
            "# TYPE zttato_http_request_duration_seconds_sum counter",
        ]
    )
    for (method, status), duration in sorted(durations.items()):
        lines.append(f'zttato_http_request_duration_seconds_sum{{method="{method}",status="{status}"}} {duration:.6f}')
    lines.extend(
        [
            "# HELP zttato_publish_queue_depth Number of consented publish jobs waiting for a worker.",
            "# TYPE zttato_publish_queue_depth gauge",
            f"zttato_publish_queue_depth {publish_queue_depth}",
            "# HELP zttato_publish_queue_oldest_age_seconds Age of the oldest queued publish job.",
            "# TYPE zttato_publish_queue_oldest_age_seconds gauge",
            f"zttato_publish_queue_oldest_age_seconds {publish_queue_oldest_age_seconds}",
        ]
    )
    lines.extend(
        [
            "# HELP zttato_oauth_failures_total OAuth route failures by HTTP status.",
            "# TYPE zttato_oauth_failures_total counter",
        ]
    )
    for status, count in sorted(oauth_errors.items()):
        lines.append(f'zttato_oauth_failures_total{{status="{status}"}} {count}')
    lines.extend(
        [
            "# HELP zttato_video_uploads_total Successfully stored local video uploads.",
            "# TYPE zttato_video_uploads_total counter",
            f"zttato_video_uploads_total {video_uploads}",
            "# HELP zttato_video_upload_bytes_total Bytes in successfully stored local video uploads.",
            "# TYPE zttato_video_upload_bytes_total counter",
            f"zttato_video_upload_bytes_total {video_upload_bytes}",
        ]
    )
    if media_dir:
        try:
            media_stat = os.statvfs(media_dir)
            media_free = media_stat.f_bavail * media_stat.f_frsize
            media_total = media_stat.f_blocks * media_stat.f_frsize
        except OSError:
            media_free = 0
            media_total = 0
        lines.extend(
            [
                "# HELP zttato_media_disk_free_bytes Bytes available to the process in the media filesystem.",
                "# TYPE zttato_media_disk_free_bytes gauge",
                f"zttato_media_disk_free_bytes {media_free}",
                "# HELP zttato_media_disk_total_bytes Total bytes in the media filesystem.",
                "# TYPE zttato_media_disk_total_bytes gauge",
                f"zttato_media_disk_total_bytes {media_total}",
            ]
        )
    with _METRICS_LOCK:
        cleanup_runs = dict(_CLEANUP_RUNS)
        cleanup_deleted = dict(_CLEANUP_DELETED)
        file_delete_failures = _CLEANUP_FILE_DELETE_FAILURES
        cleanup_last_success = _CLEANUP_LAST_SUCCESS
    lines.extend(
        [
            "# HELP zttato_cleanup_runs_total Scheduled retention cleanup runs by result.",
            "# TYPE zttato_cleanup_runs_total counter",
        ]
    )
    for result, count in sorted(cleanup_runs.items()):
        lines.append(f'zttato_cleanup_runs_total{{result="{result}"}} {count}')
    lines.extend(
        [
            "# HELP zttato_cleanup_deleted_rows_total Rows removed by scheduled retention cleanup.",
            "# TYPE zttato_cleanup_deleted_rows_total counter",
        ]
    )
    for resource, count in sorted(cleanup_deleted.items()):
        lines.append(f'zttato_cleanup_deleted_rows_total{{resource="{resource}"}} {count}')
    lines.extend(
        [
            "# HELP zttato_cleanup_file_delete_failures_total Failed local upload deletions.",
            "# TYPE zttato_cleanup_file_delete_failures_total counter",
            f"zttato_cleanup_file_delete_failures_total {file_delete_failures}",
            "# HELP zttato_cleanup_last_success_timestamp_seconds Unix timestamp of the last completed cleanup run.",
            "# TYPE zttato_cleanup_last_success_timestamp_seconds gauge",
            f"zttato_cleanup_last_success_timestamp_seconds {cleanup_last_success}",
        ]
    )
    return "\n".join(lines) + "\n"


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or load_settings()
    validate_deployment_config(s)
    Path(s.media_dir).mkdir(parents=True, exist_ok=True)
    if s.database_url.startswith("sqlite:///"):
        Path(s.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine, session_factory = make_session_factory(s.database_url, bootstrap=s.env not in {"staging", "production"})

    @asynccontextmanager
    async def lifespan(_app):
        stop = asyncio.Event()

        async def cleanup_loop():
            while not stop.is_set():
                try:
                    if s.env in {"staging", "production"}:
                        with session_factory() as check_session:
                            revision = check_session.scalar(text("SELECT version_num FROM alembic_version"))
                        if revision != MIGRATION_HEAD:
                            LOGGER.error("retention_cleanup_skipped migration_revision_mismatch")
                            _record_cleanup_result(None)
                        else:
                            result = await asyncio.to_thread(
                                run_cleanup,
                                session_factory,
                                s.media_dir,
                                media_retention_days=s.media_retention_days,
                                record_retention_days=s.record_retention_days,
                            )
                            _record_cleanup_result(result)
                    else:
                        result = await asyncio.to_thread(
                            run_cleanup,
                            session_factory,
                            s.media_dir,
                            media_retention_days=s.media_retention_days,
                            record_retention_days=s.record_retention_days,
                        )
                        _record_cleanup_result(result)
                except Exception as exc:
                    _record_cleanup_result(None)
                    LOGGER.error("retention_cleanup_failed error_type=%s", type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=s.cleanup_interval_seconds)
                except TimeoutError:
                    pass

        async def publish_worker_loop():
            while not stop.is_set():
                try:
                    schema_ready = True
                    if s.env in {"staging", "production"}:
                        with session_factory() as check_session:
                            revision = check_session.scalar(text("SELECT version_num FROM alembic_version"))
                        schema_ready = revision == MIGRATION_HEAD
                    if schema_ready:
                        await asyncio.to_thread(recover_stale_publish_jobs, session_factory)
                        await run_queued_publish_job()
                    else:
                        LOGGER.error("publish_worker_skipped migration_revision_mismatch")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    LOGGER.error("publish_worker_failed error_type=%s", type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=s.publish_worker_interval_seconds)
                except TimeoutError:
                    pass

        task = asyncio.create_task(cleanup_loop(), name="zttato-retention-cleanup")
        worker_task = asyncio.create_task(publish_worker_loop(), name="zttato-publish-worker")
        _app.state.cleanup_task = task
        _app.state.publish_worker_task = worker_task
        try:
            yield
        finally:
            stop.set()
            await asyncio.gather(task, worker_task, return_exceptions=True)
            engine.dispose()

    app = FastAPI(title="zTTato Creator", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(s.allowed_hosts))
    app.include_router(i18n_router)
    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    cipher = TokenCipher(s.encryption_key)
    app.state.tiktok = TikTokClient(s)
    app.state.zwallet = ZWalletBillingClient(s.zwallet_adapter_url, s.z_platform_service_token)
    app.state.settings = s
    app.state.engine = engine
    app.state.session_factory = session_factory

    def db():
        with session_factory() as session:
            yield session

    def tiktok(request: Request) -> TikTokClient:
        return request.app.state.tiktok

    def current(request: Request, session: Session = Depends(db)) -> BrowserSession:
        return browser_session(request, session)

    def account(row: BrowserSession, session: Session, scope: str | None = None) -> LinkedAccount:
        found = session.scalar(select(LinkedAccount).where(LinkedAccount.session_id == row.id))
        if not found:
            raise HTTPException(401, "Connect your TikTok account first")
        if scope and scope not in found.scopes.split(","):
            raise HTTPException(403, "TikTok permission missing; reconnect with the requested scope")
        return found

    async def access(found: LinkedAccount, session: Session, client: TikTokClient) -> str:
        now = int(time.time())
        if found.access_expires_at > now + 120:
            return cipher.decrypt(found.access_cipher)
        if found.refresh_expires_at <= now + 120:
            raise HTTPException(401, "TikTok authorization expired; reconnect")

        # Refresh-token rotation must be serialized per linked account. Without a row lock,
        # concurrent workers can both redeem the same refresh token and one worker can persist
        # credentials that the other worker has already invalidated upstream.
        locked = session.scalar(select(LinkedAccount).where(LinkedAccount.id == found.id).with_for_update())
        if not locked:
            raise HTTPException(401, "TikTok authorization no longer exists")
        now = int(time.time())
        if locked.access_expires_at > now + 120:
            return cipher.decrypt(locked.access_cipher)
        if locked.refresh_expires_at <= now + 120:
            raise HTTPException(401, "TikTok authorization expired; reconnect")

        current_refresh = cipher.decrypt(locked.refresh_cipher)
        tokens = await client.refresh(current_refresh)
        if tokens.get("open_id") != locked.open_id or not tokens.get("access_token"):
            raise HTTPException(502, "Unexpected refresh response; reconnect")
        access_expires_in = int(tokens.get("expires_in", 0))
        refresh_expires_in = int(tokens.get("refresh_expires_in", 0))
        if access_expires_in <= 0 or refresh_expires_in <= 0:
            raise HTTPException(502, "TikTok returned invalid token expiry")
        locked.access_cipher = cipher.encrypt(tokens["access_token"])
        locked.refresh_cipher = cipher.encrypt(tokens.get("refresh_token") or current_refresh)
        locked.access_expires_at = now + access_expires_in
        locked.refresh_expires_at = now + refresh_expires_in
        locked.scopes = tokens.get("scope", locked.scopes)
        session.commit()
        return tokens["access_token"]

    def claim_publish_job(job_id: str | None = None) -> str | None:
        with session_factory() as session:
            statement = select(PublishJob).where(PublishJob.status == "QUEUED")
            if job_id:
                statement = statement.where(PublishJob.id == job_id)
            job = session.scalar(
                statement.order_by(PublishJob.created_at, PublishJob.id).limit(1).with_for_update(skip_locked=True)
            )
            if not job:
                return None
            job.status = "INITIATING"
            job.fail_reason = None
            job.updated_at = int(time.time())
            session.commit()
            return job.id

    def save_publish_state(
        job_id: str,
        *,
        status: str,
        fail_reason: str | None = None,
        publish_id: str | None = None,
        clear_request: bool = False,
    ) -> None:
        with session_factory() as session:
            job = session.get(PublishJob, job_id)
            if not job:
                return
            job.status = status
            job.fail_reason = fail_reason
            if publish_id is not None:
                job.publish_id = publish_id
            if clear_request:
                job.request_cipher = None
            job.updated_at = int(time.time())
            session.commit()

    async def execute_claimed_publish_job(job_id: str) -> None:
        client: TikTokClient = app.state.tiktok
        provider_publish_id: str | None = None
        provider_init_started = False
        try:
            with session_factory() as session:
                job = session.get(PublishJob, job_id)
                if not job or job.status != "INITIATING" or not job.request_cipher:
                    raise HTTPException(409, "Queued publish request is no longer available")
                request_data = json.loads(cipher.decrypt(job.request_cipher))
                payload = PublishInput(
                    media_id=job.media_id,
                    mode=job.mode,
                    idempotency_key=job.idempotency_key,
                    consent=True,
                    **request_data,
                )
                if not job.request_fingerprint or not hmac.compare_digest(
                    job.request_fingerprint, _publish_request_fingerprint(payload, s)
                ):
                    raise HTTPException(409, "Stored publish request integrity check failed")
                row = session.get(BrowserSession, job.session_id)
                if not row:
                    raise HTTPException(401, "Browser session expired before publish processing")
                media = session.scalar(
                    select(MediaAsset).where(MediaAsset.id == job.media_id, MediaAsset.session_id == row.id)
                )
                if not media:
                    raise HTTPException(404, "Media not found")
                required_scope = "video.publish" if payload.mode == "direct" else "video.upload"
                linked = account(row, session, required_scope)
                token = await access(linked, session, client)
                media_path = media.path
                media_size = media.size
                media_duration_ms = media.duration_ms
                is_photo = payload.media_type == "photo"
                photo_data = None
                if is_photo:
                    stored_photo = media.path
                    if stored_photo.startswith(PHOTO_MEDIA_PREFIX):
                        stored_photo = cipher.decrypt(stored_photo[len(PHOTO_MEDIA_PREFIX) :])
                    photo_data = json.loads(stored_photo)
                    validated_photo = PhotoMediaInput(
                        photo_images=photo_data.get("images"),
                        photo_cover_index=photo_data.get("cover_index"),
                    )
                    if not s.tiktok_verified_media_url_prefixes or any(
                        not media_url_matches_verified_prefix(url, s.tiktok_verified_media_url_prefixes)
                        for url in validated_photo.photo_images
                    ):
                        raise HTTPException(422, "Photo URL is outside the configured TikTok-verified prefix")
                    photo_data = {
                        "images": validated_photo.photo_images,
                        "cover_index": validated_photo.photo_cover_index,
                    }
                else:
                    root = Path(s.media_dir).resolve()
                    resolved_media = Path(media_path).resolve()
                    if not resolved_media.is_relative_to(root) or not resolved_media.is_file():
                        raise HTTPException(404, "Uploaded video is no longer available")
                    media_duration_ms = mp4_duration_ms(media_path)
                    if media_duration_ms <= 0 or media_duration_ms > MAX_TIKTOK_API_VIDEO_DURATION_SEC * 1000:
                        raise HTTPException(422, "Video duration is outside Content Posting API limits")

            privacy = None
            if payload.mode == "direct":
                creator = await client.creator_info(token)
                options = creator.get("privacy_level_options", [])
                if payload.privacy not in options:
                    raise HTTPException(422, "Privacy selection is unavailable for this creator")
                if not is_photo:
                    max_duration = creator.get("max_video_post_duration_sec")
                    if isinstance(max_duration, bool) or not isinstance(max_duration, int) or max_duration <= 0:
                        raise HTTPException(502, "TikTok did not return the creator's current video duration limit")
                    if media_duration_ms > min(max_duration, MAX_TIKTOK_API_VIDEO_DURATION_SEC) * 1000:
                        raise HTTPException(422, "Video exceeds this creator's current TikTok duration limit")
                if not s.app_audited and payload.privacy != "SELF_ONLY":
                    raise HTTPException(422, "Unaudited clients must use SELF_ONLY")
                if creator.get("comment_disabled") and not payload.disable_comment:
                    raise HTTPException(422, "This creator has disabled comments")
                privacy = payload.privacy

            provider_init_started = True
            if is_photo:
                publish_id, _ = await client.init_photo(
                    token,
                    mode=payload.mode,
                    caption=payload.caption,
                    privacy=privacy,
                    disable_comment=payload.disable_comment,
                    brand_content_toggle=payload.brand_content_toggle,
                    brand_organic_toggle=payload.brand_organic_toggle,
                    is_aigc=payload.is_aigc,
                    photo_images=photo_data["images"],
                    photo_cover_index=photo_data["cover_index"],
                )
                provider_publish_id = publish_id
                save_publish_state(
                    job_id,
                    status="PROCESSING",
                    publish_id=publish_id,
                    clear_request=True,
                )
            else:
                publish_id, upload_url = await client.init_video(
                    token,
                    mode=payload.mode,
                    media_size=media_size,
                    caption=payload.caption,
                    privacy=privacy,
                    disable_comment=payload.disable_comment,
                    disable_duet=payload.disable_duet,
                    disable_stitch=payload.disable_stitch,
                    brand_content_toggle=payload.brand_content_toggle,
                    brand_organic_toggle=payload.brand_organic_toggle,
                    is_aigc=payload.is_aigc,
                )
                provider_publish_id = publish_id
                save_publish_state(
                    job_id,
                    status="TRANSFER_PENDING",
                    publish_id=publish_id,
                    clear_request=True,
                )
                await client.upload_video(upload_url, media_path, media_size)
                save_publish_state(job_id, status="PROCESSING")
        except Exception as exc:
            if provider_publish_id:
                status = "RECONCILIATION_REQUIRED"
                reason = "TikTok returned a publish ID, but transfer completion is uncertain. Refresh status before retrying."
            elif not provider_init_started or isinstance(exc, HTTPException) and exc.status_code < 500:
                status = "INITIATION_FAILED"
                reason = _safe_publish_failure_reason(exc, provider_init_started=provider_init_started)
            else:
                status = "INITIATION_UNCERTAIN"
                reason = "TikTok did not confirm request acceptance. Keep the same request key and check account status before retrying."
            try:
                save_publish_state(job_id, status=status, fail_reason=reason, clear_request=True)
            except Exception as persist_exc:
                LOGGER.error("publish_job_state_persist_failed error_type=%s", type(persist_exc).__name__)
            LOGGER.warning("publish_job_processing_failed status=%s error_type=%s", status, type(exc).__name__)

    async def run_queued_publish_job(job_id: str | None = None) -> None:
        claimed_id = await asyncio.to_thread(claim_publish_job, job_id)
        if claimed_id:
            await execute_claimed_publish_job(claimed_id)

    def issue_cookies(response, raw: str, csrf: str):
        response.set_cookie(COOKIE, raw, max_age=86400, secure=s.secure_cookies, httponly=True, samesite="lax")
        response.set_cookie(CSRF, csrf, max_age=86400, secure=s.secure_cookies, httponly=False, samesite="lax")

    def safe_legal(page: str) -> str:
        original = (WEB / page).read_text("utf-8")
        values = {
            "{{LEGAL_ENTITY}}": html.escape(s.legal_entity or "Legal operator details pending"),
            "{{LEGAL_EMAIL}}": html.escape(s.legal_email or "Contact details pending"),
            "{{LEGAL_ADDRESS}}": html.escape(s.legal_address or "Address pending legal review"),
        }
        for old, new in values.items():
            original = original.replace(old, new)
        return original

    def request_quota(request: Request) -> tuple[str, int, int, int, int | None] | JSONResponse | None:
        path = request.scope.get("path", "")
        method = request.method.upper()
        if path.startswith("/health/") or path == "/metrics":
            return None
        if not (path.startswith("/api/") or path.startswith("/auth/tiktok/") or path == "/tiktok/callback"):
            return None

        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            if not raw_length.isdecimal():
                return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
            max_body = s.max_video_bytes + 1024 * 1024 if path == "/api/media" else 256 * 1024
            if int(raw_length) > max_body:
                return JSONResponse({"detail": "Request body exceeds the configured size limit"}, status_code=413)

        if path == "/api/media" and method == "POST":
            if raw_length is None or not raw_length.isdecimal():
                return JSONResponse({"detail": "Content-Length is required for media uploads"}, status_code=411)
            body_length = int(raw_length)
            max_body = s.max_video_bytes + 1024 * 1024
            if body_length > max_body:
                return JSONResponse(
                    {"detail": "Media upload exceeds the configured request size limit"}, status_code=413
                )
            return (
                "upload_daily",
                s.max_uploads_per_day,
                86_400,
                body_length,
                s.max_upload_bytes_per_day,
            )
        if path == "/api/publish" and method == "POST":
            return ("publish_per_minute", 6, 60, 0, None)
        if path == "/api/creator-info":
            return ("creator_info_per_minute", 20, 60, 0, None)
        return ("requests_per_minute", s.max_requests_per_minute, 60, 0, None)

    @app.middleware("http")
    async def operational_middleware(request: Request, call_next):
        started = perf_counter()
        response = None
        status_code = 500
        scope = "public"
        try:
            quota = request_quota(request)
            if isinstance(quota, JSONResponse):
                response = quota
            elif quota is not None:
                scope, limit, window_seconds, request_bytes, byte_limit = quota
                subject = _quota_subject(request, s)
                try:
                    with session_factory() as quota_session:
                        retry_after = _increment_quota(
                            quota_session,
                            subject_hash=subject,
                            scope=scope,
                            limit=limit,
                            window_seconds=window_seconds,
                            request_bytes=request_bytes,
                            byte_limit=byte_limit,
                        )
                except SQLAlchemyError:
                    LOGGER.error("quota_storage_unavailable scope=%s", scope)
                    response = JSONResponse({"detail": "Request quota service is unavailable"}, status_code=503)
                else:
                    if retry_after is not None:
                        LOGGER.warning("request_quota_rejected scope=%s method=%s", scope, request.method)
                        response = JSONResponse(
                            {"detail": "Request quota exceeded"},
                            status_code=429,
                            headers={"Retry-After": str(retry_after)},
                        )
            if response is None:
                response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = perf_counter() - started
            _record_request_metric(request.method, status_code, elapsed)
            if (
                request.scope.get("path", "").startswith("/auth/tiktok/")
                or request.scope.get("path") == "/tiktok/callback"
            ):
                _record_oauth_error(status_code)
            LOGGER.info(
                "http_request method=%s scope=%s status=%d duration_ms=%.1f",
                request.method,
                scope,
                status_code,
                elapsed * 1000,
            )
            if response is not None:
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
                    "img-src 'self' data: https://tiktokcdn.com https://*.tiktokcdn.com "
                    "https://tiktokcdn-us.com https://*.tiktokcdn-us.com "
                    "https://tiktokcdn-eu.com https://*.tiktokcdn-eu.com "
                    "https://tiktokcdn-in.com https://*.tiktokcdn-in.com; "
                    "connect-src 'self'; form-action 'self'"
                )
                response.headers["Cache-Control"] = (
                    "no-store" if request.url.path.startswith(("/api/", "/tiktok/")) else "public, max-age=300"
                )
                if s.secure_cookies:
                    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        if not s.metrics_bearer_token:
            raise HTTPException(404, "Not found")
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, "Bearer " + s.metrics_bearer_token):
            raise HTTPException(404, "Not found")
        queued = select(func.count(PublishJob.id)).where(PublishJob.status == "QUEUED")
        oldest = select(func.min(PublishJob.created_at)).where(PublishJob.status == "QUEUED")
        with session_factory() as session:
            queue_depth = session.scalar(queued) or 0
            queued_at = session.scalar(oldest)
        queue_age = max(0, int(time.time()) - queued_at) if queued_at else 0
        return PlainTextResponse(
            _prometheus_metrics(
                s.media_dir,
                publish_queue_depth=queue_depth,
                publish_queue_oldest_age_seconds=queue_age,
            ),
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/", include_in_schema=False)
    def homepage():
        return FileResponse(WEB / "index.html")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard():
        return FileResponse(WEB / "dashboard.html")

    @app.get("/review-preview", include_in_schema=False)
    def review_preview():
        return FileResponse(WEB / "review-preview.html")

    @app.get("/privacy-policy", include_in_schema=False)
    def privacy_policy():
        return HTMLResponse(safe_legal("privacy-policy.html"))

    @app.get("/terms-of-service", include_in_schema=False)
    def terms_of_service():
        return HTMLResponse(safe_legal("terms-of-service.html"))

    @app.get("/tiktok/uploading/", include_in_schema=False)
    def tiktok_site_verification():
        return FileResponse(WEB / "tiktok-site-verification.txt", media_type="text/plain")

    @app.get("/tiktok/uploading/tiktok9WurARgpbnJkdus0r4bfvSZydNYNxKUC.txt", include_in_schema=False)
    def tiktok_site_verification_file():
        return FileResponse(WEB / "tiktok-site-verification.txt", media_type="text/plain")

    @app.get("/tiktok/uploading/tiktokL5nFtrSuDDcgIfFd8fOmSq9Olco5u3U2.txt", include_in_schema=False)
    def tiktok_uploading_verification_file_2():
        return FileResponse(WEB / "tiktokL5nFtrSuDDcgIfFd8fOmSq9Olco5u3U2.txt", media_type="text/plain")

    @app.get("/tiktok/video/", include_in_schema=False)
    def tiktok_video_verification():
        return FileResponse(
            WEB / "tiktok" / "video" / "tiktokbBQcQDez1ePtWsIckfkAzIAZJYmk3W8P.txt", media_type="text/plain"
        )

    @app.get("/tiktok/video/tiktokbBQcQDez1ePtWsIckfkAzIAZJYmk3W8P.txt", include_in_schema=False)
    def tiktok_video_verification_file():
        return FileResponse(
            WEB / "tiktok" / "video" / "tiktokbBQcQDez1ePtWsIckfkAzIAZJYmk3W8P.txt", media_type="text/plain"
        )

    @app.get("/tiktok/tiktokexEbdyIAfLfQqONX57XBxjXQ8qX0VgqJ.txt", include_in_schema=False)
    def tiktok_root_verification_file():
        return FileResponse(WEB / "tiktok" / "tiktokexEbdyIAfLfQqONX57XBxjXQ8qX0VgqJ.txt", media_type="text/plain")

    @app.get("/tiktok/uploading/tiktokku5On4LTWSUV1cujWkpDjRbLsHJLY3qY.txt", include_in_schema=False)
    def tiktok_uploading_verification_file():
        return FileResponse(
            WEB / "tiktok" / "uploading" / "tiktokku5On4LTWSUV1cujWkpDjRbLsHJLY3qY.txt", media_type="text/plain"
        )

    @app.get("/tiktok/video/tiktokku5On4LTWSUV1cujWkpDjRbLsHJLY3qY.txt", include_in_schema=False)
    def tiktok_video_verification_file_ku5():
        return FileResponse(
            WEB / "tiktok" / "video" / "tiktokku5On4LTWSUV1cujWkpDjRbLsHJLY3qY.txt", media_type="text/plain"
        )

    @app.get("/tiktok/video/tiktokehTZH5bucSOvyOZ3q4JqwjWS4RHvJKpS.txt", include_in_schema=False)
    def tiktok_video_verification_file_eh():
        return FileResponse(
            WEB / "tiktok" / "video" / "tiktokehTZH5bucSOvyOZ3q4JqwjWS4RHvJKpS.txt", media_type="text/plain"
        )

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(session: Session = Depends(db)):
        try:
            session.execute(text("SELECT 1"))
            if s.env in {"staging", "production"}:
                revision = session.scalar(text("SELECT version_num FROM alembic_version"))
                if revision != MIGRATION_HEAD:
                    raise HTTPException(503, "Database migration is not current")
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Database is unavailable or migrations are missing") from exc
        return {"status": "ready"}

    @app.get("/api/session")
    def session_view(request: Request, session: Session = Depends(db)):
        new_cookie_values: tuple[str, str] | None = None
        try:
            row = browser_session(request, session)
            csrf = request.cookies.get(CSRF, "")
            if not csrf or not secrets.compare_digest(digest(csrf), row.csrf_hash):
                raise HTTPException(401)
        except HTTPException:
            raw, csrf, row = new_browser_session(session)
            new_cookie_values = (raw, csrf)

        linked = session.scalar(select(LinkedAccount).where(LinkedAccount.session_id == row.id))
        response = JSONResponse(
            {
                "connected": bool(linked),
                "scopes": linked.scopes.split(",") if linked else [],
                "audited": s.app_audited,
                "legal_ready": bool(s.legal_entity and s.legal_email and s.legal_address),
                "photo_transfer_ready": bool(s.tiktok_verified_media_url_prefixes),
            }
        )
        if new_cookie_values:
            issue_cookies(response, *new_cookie_values)
        return response

    @app.post("/api/billing/invoice-intents")
    async def create_invoice_intent(
        request: Request,
        payload: InvoiceIntentInput,
        row: BrowserSession = Depends(current),
    ):
        require_csrf(request, row)
        try:
            return await request.app.state.zwallet.create_invoice_intent(
                tenant_id=row.id,
                idempotency_key=payload.idempotency_key,
                currency=payload.currency,
                amount_minor=payload.amount_minor,
            )
        except ZWalletNotConfigured as exc:
            raise HTTPException(503, "Billing is not configured") from exc
        except ZWalletTimedOut as exc:
            raise HTTPException(504, "Billing service timed out") from exc
        except ZWalletUnavailable as exc:
            raise HTTPException(503, "Billing service is unavailable") from exc
        except ZWalletInvalidResponse as exc:
            raise HTTPException(502, "Billing service returned an invalid response") from exc

    @app.get("/auth/tiktok/start")
    def start(request: Request, session: Session = Depends(db)):
        if not s.client_key or s.client_key.startswith("REPLACE_"):
            raise HTTPException(503, "TikTok Client Key is not configured")
        new_cookie_values: tuple[str, str] | None = None
        try:
            row = browser_session(request, session)
            csrf = request.cookies.get(CSRF, "")
            if not csrf or digest(csrf) != row.csrf_hash:
                raise HTTPException(401)
        except HTTPException:
            raw, csrf, row = new_browser_session(session)
            new_cookie_values = (raw, csrf)
        state = secrets.token_urlsafe(32)
        session.add(OAuthRequest(state_hash=digest(state), session_id=row.id, expires_at=int(time.time()) + 900))
        session.commit()
        query = urlencode(
            {
                "client_key": s.client_key,
                "response_type": "code",
                "scope": ",".join(s.scopes),
                "redirect_uri": s.redirect_uri,
                "state": state,
            }
        )
        response = RedirectResponse(TikTokClient.AUTHORIZE + "?" + query, status_code=302)
        if new_cookie_values:
            issue_cookies(response, *new_cookie_values)
        return response

    @app.get("/tiktok/callback")
    async def callback(
        request: Request,
        state: str = "",
        code: str = "",
        error: str = "",
        session: Session = Depends(db),
        client: TikTokClient = Depends(tiktok),
    ):
        if error:
            return RedirectResponse("/?error=authorization_denied", status_code=303)
        row = browser_session(request, session)
        if not state or not code:
            raise HTTPException(400, "Missing OAuth authorization response")
        pending = session.get(OAuthRequest, digest(state))
        if not pending or pending.session_id != row.id or pending.expires_at <= int(time.time()):
            raise HTTPException(403, "Invalid or expired OAuth state")
        session.delete(pending)
        session.commit()
        tokens = await client.exchange(code)
        required = ("open_id", "access_token", "refresh_token", "scope", "expires_in", "refresh_expires_in")
        if any(not tokens.get(key) for key in required):
            raise HTTPException(502, "Incomplete TikTok authorization response")
        now = int(time.time())
        found = session.scalar(select(LinkedAccount).where(LinkedAccount.session_id == row.id))
        if not found:
            found = LinkedAccount(
                session_id=row.id,
                open_id=tokens["open_id"],
                scopes=tokens["scope"],
                access_cipher="",
                refresh_cipher="",
                access_expires_at=0,
                refresh_expires_at=0,
            )
            session.add(found)
        found.open_id = tokens["open_id"]
        found.scopes = tokens["scope"]
        found.access_cipher = cipher.encrypt(tokens["access_token"])
        found.refresh_cipher = cipher.encrypt(tokens["refresh_token"])
        found.access_expires_at = now + int(tokens["expires_in"])
        found.refresh_expires_at = now + int(tokens["refresh_expires_in"])
        session.commit()
        return RedirectResponse("/dashboard?connected=1", status_code=303)

    @app.get("/api/profile")
    async def basic_profile(
        row: BrowserSession = Depends(current),
        session: Session = Depends(db),
        client: TikTokClient = Depends(tiktok),
    ):
        linked = account(row, session, "user.info.basic")
        user = await client.user_info(await access(linked, session, client))
        if user.get("open_id") != linked.open_id:
            raise HTTPException(502, "TikTok basic profile does not match the linked account")
        display_name = user.get("display_name")
        return {
            "display_name": display_name[:100] if isinstance(display_name, str) else "",
            "avatar_url": safe_avatar_url(user.get("avatar_url")),
        }

    @app.get("/api/creator-info")
    async def creator_info(
        row: BrowserSession = Depends(current), session: Session = Depends(db), client: TikTokClient = Depends(tiktok)
    ):
        found = account(row, session, "video.publish")
        info = await client.creator_info(await access(found, session, client))
        return {
            "nickname": info.get("creator_nickname", ""),
            "username": info.get("creator_username", ""),
            "privacy_level_options": info.get("privacy_level_options", []),
            "comment_disabled": info.get("comment_disabled", False),
            "duet_disabled": info.get("duet_disabled", False),
            "stitch_disabled": info.get("stitch_disabled", False),
            "max_video_post_duration_sec": info.get("max_video_post_duration_sec"),
        }

    @app.post("/api/media")
    async def add_media(
        request: Request,
        file: UploadFile = File(...),
        session: Session = Depends(db),
        row: BrowserSession = Depends(current),
    ):
        require_csrf(request, row)
        account(row, session)
        if file.content_type not in ("video/mp4", "application/octet-stream"):
            raise HTTPException(415, "MP4 videos only")
        item_id = str(uuid.uuid4())
        path = Path(s.media_dir) / (item_id + ".mp4")
        size = 0
        try:
            with path.open("xb") as dest:
                os.chmod(path, 0o600)
                header = await file.read(12)
                if len(header) < 12 or header[4:8] != b"ftyp":
                    raise HTTPException(415, "Invalid MP4 file header")
                dest.write(header)
                size = len(header)
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > s.max_video_bytes:
                        raise HTTPException(413, "Video exceeds 64 MiB server upload limit")
                    dest.write(chunk)
            duration_ms = mp4_duration_ms(path)
        except InvalidMP4 as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(415, "Invalid or unsupported MP4 duration metadata") from exc
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        asset = MediaAsset(
            id=item_id,
            session_id=row.id,
            filename=Path(file.filename or "video.mp4").name[:200],
            size=size,
            path=str(path),
            duration_ms=duration_ms,
        )
        session.add(asset)
        session.commit()
        _record_video_upload(size)
        return {"media_id": item_id, "filename": asset.filename, "size": size, "duration_ms": duration_ms}

    @app.post("/api/media/photo")
    async def add_photo_media(
        request: Request,
        payload: PhotoMediaInput,
        session: Session = Depends(db),
        row: BrowserSession = Depends(current),
    ):
        require_csrf(request, row)
        account(row, session)
        if not s.tiktok_verified_media_url_prefixes:
            raise HTTPException(503, "Photo Post requires a configured TikTok-verified media URL prefix")
        if any(
            not media_url_matches_verified_prefix(url, s.tiktok_verified_media_url_prefixes)
            for url in payload.photo_images
        ):
            raise HTTPException(422, "Photo URLs must be under a configured TikTok-verified media URL prefix")
        photo_images = payload.photo_images
        photo_cover_index = payload.photo_cover_index
        item_id = str(uuid.uuid4())
        photo_json = json.dumps({"images": photo_images, "cover_index": photo_cover_index})
        asset = MediaAsset(
            id=item_id,
            session_id=row.id,
            filename="photo_set.json",
            size=len(photo_json.encode("utf-8")),
            path=PHOTO_MEDIA_PREFIX + cipher.encrypt(photo_json),
        )
        session.add(asset)
        session.commit()
        return {"media_id": item_id, "filename": asset.filename, "size": asset.size}

    @app.post("/api/publish", status_code=202)
    async def publish(
        request: Request,
        payload: PublishInput,
        background_tasks: BackgroundTasks,
        row: BrowserSession = Depends(current),
        session: Session = Depends(db),
    ):
        require_csrf(request, row)
        if not payload.consent:
            raise HTTPException(422, "Explicit confirmation is required")
        if payload.mode not in ("draft", "direct"):
            raise HTTPException(422, "Choose draft or direct")
        request_fingerprint = _publish_request_fingerprint(payload, s)
        retryable_failed_job = None
        earlier = session.scalar(
            select(PublishJob)
            .where(PublishJob.session_id == row.id, PublishJob.idempotency_key == payload.idempotency_key)
            .with_for_update()
        )
        if earlier:
            if not earlier.request_fingerprint or not hmac.compare_digest(
                earlier.request_fingerprint, request_fingerprint
            ):
                raise HTTPException(409, "Idempotency key already belongs to a different or legacy request")
            if earlier.status == "INITIATION_FAILED" and not earlier.publish_id:
                # A user-confirmed resubmission may safely reuse this job only when the
                # provider did not return an ID. Uncertain/accepted jobs remain immutable.
                retryable_failed_job = earlier
            else:
                if earlier.status == "QUEUED":
                    background_tasks.add_task(run_queued_publish_job, earlier.id)
                return {
                    "job_id": earlier.id,
                    "status": earlier.status,
                    "idempotent_replay": True,
                }
        media = session.scalar(
            select(MediaAsset).where(MediaAsset.id == payload.media_id, MediaAsset.session_id == row.id)
        )
        if not media:
            raise HTTPException(404, "Media not found")

        # Parse photo media data if it's a photo set
        is_photo = payload.media_type == "photo"
        photo_data = None
        if is_photo:
            encoded_photo_data = media.path
            legacy_photo_data = not encoded_photo_data.startswith(PHOTO_MEDIA_PREFIX)
            if not legacy_photo_data:
                try:
                    encoded_photo_data = cipher.decrypt(encoded_photo_data[len(PHOTO_MEDIA_PREFIX) :])
                except HTTPException as exc:
                    raise HTTPException(503, "Photo media key unavailable; re-upload this photo set") from exc
            try:
                photo_data = json.loads(encoded_photo_data)
            except (json.JSONDecodeError, TypeError):
                raise HTTPException(422, "Invalid photo media data")
            if not isinstance(photo_data, dict):
                raise HTTPException(422, "Invalid photo media data")
            try:
                validated_photo = PhotoMediaInput(
                    photo_images=photo_data.get("images"),
                    photo_cover_index=photo_data.get("cover_index"),
                )
            except (TypeError, ValidationError) as exc:
                raise HTTPException(422, "Invalid photo media data") from exc
            if not s.tiktok_verified_media_url_prefixes:
                raise HTTPException(503, "Photo Post requires a configured TikTok-verified media URL prefix")
            if any(
                not media_url_matches_verified_prefix(url, s.tiktok_verified_media_url_prefixes)
                for url in validated_photo.photo_images
            ):
                raise HTTPException(422, "Photo URLs must be under a configured TikTok-verified media URL prefix")
            photo_data = {
                "images": validated_photo.photo_images,
                "cover_index": validated_photo.photo_cover_index,
            }
            if legacy_photo_data:
                media.path = PHOTO_MEDIA_PREFIX + cipher.encrypt(json.dumps(photo_data))
                session.commit()
        else:
            if not Path(media.path).is_file():
                raise HTTPException(404, "Upload a video first")
            duration_ms = media.duration_ms
            if duration_ms is None:
                try:
                    duration_ms = mp4_duration_ms(media.path)
                except (InvalidMP4, OSError) as exc:
                    raise HTTPException(422, "Uploaded MP4 duration could not be validated") from exc
            if duration_ms <= 0:
                raise HTTPException(422, "Uploaded MP4 duration could not be validated")
            if duration_ms > MAX_TIKTOK_API_VIDEO_DURATION_SEC * 1000:
                raise HTTPException(422, "TikTok Content Posting API supports video uploads up to 10 minutes")

        required_scope = "video.publish" if payload.mode == "direct" else "video.upload"
        account(row, session, required_scope)
        if payload.mode == "direct" and not payload.privacy:
            raise HTTPException(422, "Choose a current creator privacy option before publishing")
        if payload.mode == "direct" and not s.app_audited and payload.privacy != "SELF_ONLY":
            raise HTTPException(422, "Unaudited clients must use SELF_ONLY")
        encrypted_request = payload.model_dump(
            include={
                "caption",
                "privacy",
                "disable_comment",
                "disable_duet",
                "disable_stitch",
                "brand_content_toggle",
                "brand_organic_toggle",
                "is_aigc",
                "media_type",
            }
        )
        if retryable_failed_job:
            retryable_failed_job.status = "QUEUED"
            retryable_failed_job.fail_reason = None
            retryable_failed_job.request_cipher = cipher.encrypt(json.dumps(encrypted_request, ensure_ascii=False))
            retryable_failed_job.consented_at = int(time.time())
            retryable_failed_job.consent_version = PUBLISH_CONSENT_VERSION
            retryable_failed_job.checked_at = 0
            session.commit()
            background_tasks.add_task(run_queued_publish_job, retryable_failed_job.id)
            return {
                "job_id": retryable_failed_job.id,
                "status": "QUEUED",
                "idempotent_replay": True,
                "retry_queued": True,
            }

        job = PublishJob(
            id=str(uuid.uuid4()),
            session_id=row.id,
            idempotency_key=payload.idempotency_key,
            media_id=media.id,
            mode=payload.mode,
            status="QUEUED",
            request_fingerprint=request_fingerprint,
            consented_at=int(time.time()),
            consent_version=PUBLISH_CONSENT_VERSION,
            request_cipher=cipher.encrypt(json.dumps(encrypted_request, ensure_ascii=False)),
        )
        session.add(job)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            earlier = session.scalar(
                select(PublishJob).where(
                    PublishJob.session_id == row.id, PublishJob.idempotency_key == payload.idempotency_key
                )
            )
            if earlier:
                if not earlier.request_fingerprint or not hmac.compare_digest(
                    earlier.request_fingerprint, request_fingerprint
                ):
                    raise HTTPException(409, "Idempotency key already belongs to a different or legacy request")
                if earlier.status == "QUEUED":
                    background_tasks.add_task(run_queued_publish_job, earlier.id)
                return {
                    "job_id": earlier.id,
                    "status": earlier.status,
                    "idempotent_replay": True,
                }
            raise
        background_tasks.add_task(run_queued_publish_job, job.id)
        return {
            "job_id": job.id,
            "status": "QUEUED",
            "note": "Saved securely; TikTok processing will continue in the background.",
        }

    @app.get("/api/jobs/{job_id}")
    async def job_status(
        job_id: str,
        row: BrowserSession = Depends(current),
        session: Session = Depends(db),
        client: TikTokClient = Depends(tiktok),
    ):
        job = session.scalar(select(PublishJob).where(PublishJob.id == job_id, PublishJob.session_id == row.id))
        if not job:
            raise HTTPException(404, "Job not found")
        now = int(time.time())
        if job.publish_id and (not job.checked_at or now - job.checked_at >= 30):
            linked = account(row, session)
            result = await client.status(await access(linked, session, client), job.publish_id)
            new_status = str(result.get("status", job.status))[:40]
            new_fail_reason = str(result.get("fail_reason", ""))[:160] or None
            if new_status != job.status or new_fail_reason != job.fail_reason:
                job.updated_at = now
            job.status = new_status
            job.fail_reason = new_fail_reason
            job.checked_at = now
            session.commit()
        return {
            "job_id": job.id,
            "status": job.status,
            "fail_reason": job.fail_reason,
            "mode": job.mode,
        }

    @app.post("/api/disconnect")
    async def disconnect(
        request: Request,
        row: BrowserSession = Depends(current),
        session: Session = Depends(db),
        client: TikTokClient = Depends(tiktok),
    ):
        require_csrf(request, row)
        found = account(row, session)
        try:
            token = await access(found, session, client)
            await client.revoke(token)
        except HTTPException:
            # Local disconnect must remain possible when the upstream authorization has expired.
            pass
        session.delete(found)
        session.commit()
        return {"disconnected": True}

    @app.delete("/api/my-data")
    async def delete_my_data(
        request: Request,
        row: BrowserSession = Depends(current),
        session: Session = Depends(db),
        client: TikTokClient = Depends(tiktok),
    ):
        require_csrf(request, row)
        found = session.scalar(select(LinkedAccount).where(LinkedAccount.session_id == row.id))
        if found:
            try:
                await client.revoke(await access(found, session, client))
            except HTTPException:
                # Data deletion must not be blocked by an expired or unavailable upstream token.
                pass
            session.delete(found)
        jobs = session.scalars(select(PublishJob).where(PublishJob.session_id == row.id)).all()
        for job in jobs:
            session.delete(job)
        assets = session.scalars(select(MediaAsset).where(MediaAsset.session_id == row.id)).all()
        for asset in assets:
            if not remove_local_upload(s.media_dir, asset.path):
                LOGGER.error("user_data_delete_file_failed")
                raise HTTPException(503, "Local media could not be deleted; retry the request")
            session.delete(asset)
        states = session.scalars(select(OAuthRequest).where(OAuthRequest.session_id == row.id)).all()
        for state in states:
            session.delete(state)
        session.delete(row)
        session.commit()
        response = JSONResponse({"deleted": True})
        response.delete_cookie(COOKIE)
        response.delete_cookie(CSRF)
        return response

    return app


app = create_app()
