"""zTTato API. All TikTok secrets stay server-side; a browser session is not a TikTok access token."""

import html
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, text
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
    MIGRATION_HEAD,
    make_session_factory,
)
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


class InvoiceIntentInput(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    amount_minor: int = Field(gt=0, le=9_007_199_254_740_991, strict=True)

    model_config = {"extra": "forbid"}


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or load_settings()
    validate_host_config(s)
    validate_zwallet_config(s)
    app = FastAPI(title="zTTato Creator", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(s.allowed_hosts))
    app.include_router(i18n_router)
    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    Path(s.media_dir).mkdir(parents=True, exist_ok=True)
    if s.database_url.startswith("sqlite:///"):
        Path(s.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine, session_factory = make_session_factory(s.database_url, bootstrap=s.env != "production")
    cipher = TokenCipher(s.encryption_key)
    app.state.tiktok = TikTokClient(s)
    app.state.zwallet = ZWalletBillingClient(s.zwallet_adapter_url, s.z_platform_service_token)
    app.state.settings = s

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

    @app.middleware("http")
    async def hardened_headers(request: Request, call_next):
        response = await call_next(request)
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
        return response

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
        )
        session.add(asset)
        session.commit()
        return {"media_id": item_id, "filename": asset.filename, "size": size}

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
            session.commit()
            if not is_photo:
                await client.upload_video(upload_url, media.path, media.size)
                job.status = "PROCESSING"
                session.commit()
        except Exception:
            job.status = "RECONCILIATION_REQUIRED" if job.publish_id else "INITIATION_FAILED"
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
            job.status = str(result.get("status", job.status))[:40]
            job.fail_reason = str(result.get("fail_reason", ""))[:160] or None
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
            Path(asset.path).unlink(missing_ok=True)
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
