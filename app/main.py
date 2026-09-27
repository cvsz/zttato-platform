"""zTTato API. All TikTok secrets stay server-side; a browser session is not a TikTok access token."""

import asyncio
import html
import hashlib
import hmac
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

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings, load_settings, validate_host_config, validate_zwallet_config
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
from app.maintenance import remove_local_upload, run_cleanup
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
    caption: str = Field(default="", max_length=2200)
    privacy: str | None = None
    consent: bool
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    brand_content_toggle: bool = False
    brand_organic_toggle: bool = False
    is_aigc: bool = False
    media_type: str = "video"
    photo_images: list[str] | None = None
    photo_cover_index: int | None = None

    @field_validator("caption")
    @classmethod
    def caption_fits_tiktok_utf16_limit(cls, value: str) -> str:
        if len(value.encode("utf-16-le")) // 2 > 2200:
            raise ValueError("Caption must not exceed 2200 UTF-16 code units")
        return value


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


def _prometheus_metrics(media_dir: str | None = None) -> str:
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
    validate_host_config(s)
    validate_zwallet_config(s)
    Path(s.media_dir).mkdir(parents=True, exist_ok=True)
    if s.database_url.startswith("sqlite:///"):
        Path(s.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine, session_factory = make_session_factory(s.database_url, bootstrap=s.env != "production")

    @asynccontextmanager
    async def lifespan(_app):
        stop = asyncio.Event()

        async def cleanup_loop():
            while not stop.is_set():
                try:
                    if s.env == "production":
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

        task = asyncio.create_task(cleanup_loop(), name="zttato-retention-cleanup")
        _app.state.cleanup_task = task
        try:
            yield
        finally:
            stop.set()
            await task
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
        return PlainTextResponse(_prometheus_metrics(s.media_dir), media_type="text/plain; version=0.0.4")

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
            if s.env == "production":
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
        payload: dict,
        session: Session = Depends(db),
        row: BrowserSession = Depends(current),
    ):
        require_csrf(request, row)
        account(row, session)
        photo_images = payload.get("photo_images", [])
        photo_cover_index = payload.get("photo_cover_index", 0)
        if not photo_images or not isinstance(photo_images, list):
            raise HTTPException(422, "photo_images array is required")
        if len(photo_images) > 35:
            raise HTTPException(422, "Maximum 35 photos allowed")
        if photo_cover_index < 0 or photo_cover_index >= len(photo_images):
            raise HTTPException(422, "Invalid photo_cover_index")
        for url in photo_images:
            if not url.startswith("https://"):
                raise HTTPException(422, "Photo URLs must use HTTPS")
        item_id = str(uuid.uuid4())
        asset = MediaAsset(
            id=item_id,
            session_id=row.id,
            filename="photo_set.json",
            size=len(json.dumps({"images": photo_images, "cover_index": photo_cover_index})),
            path=json.dumps({"images": photo_images, "cover_index": photo_cover_index}),
        )
        session.add(asset)
        session.commit()
        return {"media_id": item_id, "filename": asset.filename, "size": asset.size}

    @app.post("/api/publish")
    async def publish(
        request: Request,
        payload: PublishInput,
        row: BrowserSession = Depends(current),
        session: Session = Depends(db),
        client: TikTokClient = Depends(tiktok),
    ):
        require_csrf(request, row)
        if not payload.consent:
            raise HTTPException(422, "Explicit confirmation is required")
        if payload.mode not in ("draft", "direct"):
            raise HTTPException(422, "Choose draft or direct")
        earlier = session.scalar(
            select(PublishJob).where(
                PublishJob.session_id == row.id, PublishJob.idempotency_key == payload.idempotency_key
            )
        )
        if earlier:
            if earlier.media_id != payload.media_id or earlier.mode != payload.mode:
                raise HTTPException(409, "Idempotency key already belongs to a different request")
            return {
                "job_id": earlier.id,
                "status": earlier.status,
                "publish_id": earlier.publish_id,
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
            try:
                photo_data = json.loads(media.path)
            except (json.JSONDecodeError, TypeError):
                raise HTTPException(422, "Invalid photo media data")

        required_scope = "video.publish" if payload.mode == "direct" else "video.upload"
        linked = account(row, session, required_scope)
        token = await access(linked, session, client)
        privacy = None
        if payload.mode == "direct":
            creator = await client.creator_info(token)
            options = creator.get("privacy_level_options", [])
            if payload.privacy not in options:
                raise HTTPException(422, "Privacy selection is unavailable for this creator")
            max_duration = creator.get("max_video_post_duration_sec")
            if not is_photo:
                if isinstance(max_duration, bool) or not isinstance(max_duration, int) or max_duration <= 0:
                    raise HTTPException(502, "TikTok did not return the creator's current video duration limit")
                duration_ms = media.duration_ms
                if duration_ms is None:
                    try:
                        duration_ms = mp4_duration_ms(media.path)
                    except (InvalidMP4, OSError) as exc:
                        raise HTTPException(422, "Uploaded MP4 duration could not be validated") from exc
                if duration_ms > max_duration * 1000:
                    raise HTTPException(422, "Video exceeds this creator's current TikTok duration limit")
            if not s.app_audited and payload.privacy != "SELF_ONLY":
                raise HTTPException(422, "Unaudited clients must use SELF_ONLY")
            if creator.get("comment_disabled") and not payload.disable_comment:
                raise HTTPException(422, "This creator has disabled comments")
            privacy = payload.privacy
        job = PublishJob(
            id=str(uuid.uuid4()),
            session_id=row.id,
            idempotency_key=payload.idempotency_key,
            media_id=media.id,
            mode=payload.mode,
            status="INITIATING",
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
                return {
                    "job_id": earlier.id,
                    "status": earlier.status,
                    "publish_id": earlier.publish_id,
                    "idempotent_replay": True,
                }
            raise
        try:
            if is_photo:
                if not photo_data:
                    raise HTTPException(422, "Invalid photo media data")
                publish_id, _ = await client.init_photo(
                    token,
                    mode=payload.mode,
                    caption=payload.caption,
                    privacy=privacy,
                    disable_comment=payload.disable_comment,
                    brand_content_toggle=payload.brand_content_toggle,
                    brand_organic_toggle=payload.brand_organic_toggle,
                    is_aigc=payload.is_aigc,
                    photo_images=photo_data.get("images", []),
                    photo_cover_index=photo_data.get("cover_index", 0),
                )
                job.publish_id = publish_id
                job.status = "PROCESSING"
            else:
                if not media or not Path(media.path).is_file():
                    raise HTTPException(404, "Upload a video first")
                publish_id, upload_url = await client.init_video(
                    token,
                    mode=payload.mode,
                    media_size=media.size,
                    caption=payload.caption,
                    privacy=privacy,
                    disable_comment=payload.disable_comment,
                    disable_duet=payload.disable_duet,
                    disable_stitch=payload.disable_stitch,
                    brand_content_toggle=payload.brand_content_toggle,
                    brand_organic_toggle=payload.brand_organic_toggle,
                    is_aigc=payload.is_aigc,
                )
                job.publish_id = publish_id
                job.status = "TRANSFER_PENDING"
            job.updated_at = int(time.time())
            session.commit()
            if not is_photo:
                await client.upload_video(upload_url, media.path, media.size)
                job.status = "PROCESSING"
                job.updated_at = int(time.time())
                session.commit()
        except Exception:
            job.status = "RECONCILIATION_REQUIRED" if job.publish_id else "INITIATION_FAILED"
            job.updated_at = int(time.time())
            session.commit()
            raise
        return {
            "job_id": job.id,
            "status": job.status,
            "publish_id": job.publish_id,
            "note": "Draft uploads require the creator to finish posting inside TikTok."
            if payload.mode == "draft"
            else "TikTok is processing your consented direct post.",
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
            "publish_id": job.publish_id,
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
