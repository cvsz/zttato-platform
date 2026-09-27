import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import media_url_matches_verified_prefix
from app.db import (
    BrowserSession,
    LinkedAccount,
    MediaAsset,
    OAuthRequest,
    PublishJob,
    QuotaBucket,
    make_session_factory,
)
from app.main import (
    PHOTO_MEDIA_PREFIX,
    PhotoMediaInput,
    PublishInput,
    _increment_quota,
    _publish_request_fingerprint,
    create_app,
)
from app.media import InvalidMP4, mp4_duration_ms
from app.maintenance import remove_local_upload, run_cleanup
from app.security import TokenCipher, digest
from tests.test_app import connected_profile_client, settings
from app.tiktok import TikTokClient


def _box(kind: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def _mp4(duration_ms: int, *, version: int = 0) -> bytes:
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
    if version == 0:
        header = b"\x00\x00\x00\x00" + b"\x00" * 8 + (1000).to_bytes(4, "big") + duration_ms.to_bytes(4, "big")
    else:
        header = b"\x01\x00\x00\x00" + b"\x00" * 16 + (1000).to_bytes(4, "big") + duration_ms.to_bytes(8, "big")
    return ftyp + _box(b"moov", _box(b"mvhd", header))


@pytest.mark.parametrize("version", [0, 1])
def test_mp4_duration_reads_movie_header_without_loading_media(tmp_path, version):
    video = tmp_path / "duration.mp4"
    video.write_bytes(_mp4(12_345, version=version))
    assert mp4_duration_ms(video) == 12_345


def test_mp4_duration_rejects_missing_or_invalid_movie_metadata(tmp_path):
    video = tmp_path / "invalid.mp4"
    video.write_bytes(_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2"))
    with pytest.raises(InvalidMP4):
        mp4_duration_ms(video)


def test_caption_limit_counts_utf16_code_units():
    base = {
        "media_id": "media",
        "mode": "direct",
        "idempotency_key": "idempotency-key-0001",
        "consent": True,
    }
    assert PublishInput(**base, caption="😀" * 1100).caption == "😀" * 1100
    with pytest.raises(ValueError, match="UTF-16"):
        PublishInput(**base, caption="😀" * 1101)


def test_photo_caption_uses_4000_utf16_code_unit_limit():
    base = {
        "media_id": "photo-media",
        "mode": "draft",
        "idempotency_key": "photo-idempotency-0001",
        "consent": True,
        "media_type": "photo",
    }
    assert PublishInput(**base, caption="😀" * 2000).caption == "😀" * 2000
    with pytest.raises(ValueError, match="4000 UTF-16"):
        PublishInput(**base, caption="😀" * 2001)


@pytest.mark.parametrize(
    "values",
    [
        {"photo_images": ["http://media.example/photo.jpg"], "photo_cover_index": 0},
        {"photo_images": ["https://media.example/photo.png"], "photo_cover_index": 0},
        {"photo_images": ["https://media.example/photo name.webp"], "photo_cover_index": 0},
        {"photo_images": ["https://user:pass@media.example/photo.webp"], "photo_cover_index": 0},
        {"photo_images": ["https://media.example/photo.webp#fragment"], "photo_cover_index": 0},
        {"photo_images": ["https://media.example/photo.webp"], "photo_cover_index": True},
        {"photo_images": ["https://media.example/photo.webp"], "photo_cover_index": "0"},
        {"photo_images": ["https://media.example/photo.webp"], "photo_cover_index": 1},
        {"photo_images": ["https://media.example/photo.webp", 42], "photo_cover_index": 0},
    ],
)
def test_photo_media_input_rejects_invalid_urls_and_cover_indexes(values):
    with pytest.raises(ValueError):
        PhotoMediaInput(**values)


def test_photo_media_input_accepts_jpeg_and_webp_urls():
    media = PhotoMediaInput(
        photo_images=["https://media.example/photo.jpg", "https://media.example/photo.webp?version=2"],
        photo_cover_index=1,
    )
    assert len(media.photo_images) == 2
    assert media.photo_cover_index == 1


def test_photo_post_requires_configured_tiktok_verified_media_prefix(tmp_path):
    _, client = connected_profile_client(tmp_path)
    response = client.post(
        "/api/media/photo",
        headers={"x-csrf-token": client.cookies["zttato_csrf"]},
        json={"photo_images": ["https://media.example/photos/one.webp"], "photo_cover_index": 0},
    )
    assert response.status_code == 503
    assert "TikTok-verified media URL prefix" in response.json()["detail"]


def test_photo_post_accepts_only_configured_verified_url_prefix(tmp_path):
    configuration = replace(
        settings(tmp_path),
        tiktok_verified_media_url_prefixes=("https://media.example/photos/",),
    )
    app, client = connected_profile_client(tmp_path, configuration=configuration)
    headers = {"x-csrf-token": client.cookies["zttato_csrf"]}
    accepted = client.post(
        "/api/media/photo",
        headers=headers,
        json={
            "photo_images": ["https://media.example/photos/one.webp?signature=synthetic-canary"],
            "photo_cover_index": 0,
        },
    )
    rejected = client.post(
        "/api/media/photo",
        headers=headers,
        json={"photo_images": ["https://media.example/other/one.webp"], "photo_cover_index": 0},
    )
    assert accepted.status_code == 200, accepted.text
    assert rejected.status_code == 422
    session_id = digest(client.cookies["zttato_session"])
    with app.state.session_factory() as session:
        stored_media = session.get(MediaAsset, accepted.json()["media_id"])
        assert stored_media is not None
        assert stored_media.session_id == session_id
        assert stored_media.path.startswith(PHOTO_MEDIA_PREFIX)
        assert "media.example" not in stored_media.path
        assert "synthetic-canary" not in stored_media.path
    assert media_url_matches_verified_prefix(
        "https://media.example/photos/one.webp", ("https://media.example/photos/",)
    )
    assert not media_url_matches_verified_prefix(
        "https://media.example/photos-public/one.webp", ("https://media.example/photos/",)
    )


def test_photo_url_prefix_match_rejects_encoded_path_escape():
    prefixes = ("https://media.example/photos/",)
    assert not media_url_matches_verified_prefix("https://media.example/photos/../private/image.webp", prefixes)
    assert not media_url_matches_verified_prefix("https://media.example/photos/%252e%252e/private/image.webp", prefixes)


def test_photo_publish_rechecks_prefix_before_tiktok_initialization(tmp_path):
    app, client = connected_profile_client(tmp_path)
    session_id = digest(client.cookies["zttato_session"])
    with app.state.session_factory() as session:
        session.add(
            MediaAsset(
                id="photo-media-with-stale-property",
                session_id=session_id,
                filename="photo_set.json",
                size=64,
                path=json.dumps(
                    {
                        "images": ["https://media.example/photos/one.webp"],
                        "cover_index": 0,
                    }
                ),
            )
        )
        session.commit()

    async def unexpected_request(request):
        raise AssertionError("Photo publish must reject an unverified URL before calling TikTok")

    app.state.tiktok.transport = httpx.MockTransport(unexpected_request)
    response = client.post(
        "/api/publish",
        headers={"x-csrf-token": client.cookies["zttato_csrf"]},
        json={
            "media_id": "photo-media-with-stale-property",
            "media_type": "photo",
            "mode": "draft",
            "idempotency_key": "photo-prefix-check-0001",
            "consent": True,
        },
    )
    assert response.status_code == 503
    assert "TikTok-verified media URL prefix" in response.json()["detail"]


def test_persistent_request_quota_rejects_excess_requests(tmp_path):
    conf = replace(settings(tmp_path), max_requests_per_minute=1)
    client = TestClient(create_app(conf))
    assert client.get("/api/session").status_code == 200
    assert client.get("/api/session").status_code == 200
    rejected = client.get("/api/session")
    assert rejected.status_code == 429
    assert rejected.headers["retry-after"].isdigit()


def test_upload_quota_enforces_count_and_daily_bytes_atomically(tmp_path):
    _, factory = make_session_factory("sqlite:///" + str(tmp_path / "quota.db"))
    now = 1_800_000_000
    with factory() as session:
        assert (
            _increment_quota(
                session,
                subject_hash="c" * 64,
                scope="upload_daily",
                limit=10,
                window_seconds=86_400,
                request_bytes=4_000,
                byte_limit=5_000,
                now=now,
            )
            is None
        )
        retry = _increment_quota(
            session,
            subject_hash="c" * 64,
            scope="upload_daily",
            limit=10,
            window_seconds=86_400,
            request_bytes=2_000,
            byte_limit=5_000,
            now=now,
        )
        assert retry is not None
    with factory() as session:
        bucket = session.get(QuotaBucket, ("c" * 64, "upload_daily", now - now % 86_400))
        assert bucket is not None
        assert bucket.request_count == 1
        assert bucket.request_bytes == 4_000


def test_metrics_are_private_and_expose_request_and_cleanup_metrics(tmp_path):
    conf = replace(settings(tmp_path), metrics_bearer_token="metrics-test-token-32-bytes-long")
    app = create_app(conf)
    with TestClient(app) as client:
        assert client.get("/api/session").status_code == 200
        unauthorized = client.get("/metrics")
        assert unauthorized.status_code == 404
        response = client.get("/metrics", headers={"Authorization": "Bearer metrics-test-token-32-bytes-long"})
        assert response.status_code == 200
        assert "zttato_http_requests_total" in response.text
        assert "zttato_publish_queue_depth 0" in response.text
        assert "zttato_publish_queue_oldest_age_seconds 0" in response.text
        assert "zttato_cleanup_runs_total" in response.text
        assert "zttato_cleanup_last_success_timestamp_seconds" in response.text


def test_media_upload_persists_validated_duration(tmp_path):
    _, client = connected_profile_client(tmp_path)
    response = client.post(
        "/api/media",
        files={"file": ("clip.mp4", _mp4(8000), "video/mp4")},
        headers={"x-csrf-token": client.cookies["zttato_csrf"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["duration_ms"] == 8000


def _linked_video(tmp_path, *, duration_ms: int, scopes: str = "video.publish,video.upload,user.info.basic"):
    key = Fernet.generate_key().decode()
    conf = replace(settings(tmp_path), encryption_key=key)
    app = create_app(conf)
    client = TestClient(app)
    client.get("/api/session")
    _, factory = make_session_factory(conf.database_url, bootstrap=False)
    session_id = digest(client.cookies["zttato_session"])
    token_cipher = Fernet(key.encode())
    media_path = Path(conf.media_dir) / "publish-fixture.mp4"
    media_path.write_bytes(_mp4(duration_ms))
    with factory() as session:
        session.add(
            LinkedAccount(
                session_id=session_id,
                open_id="synthetic-open-id",
                scopes=scopes,
                access_cipher=token_cipher.encrypt(b"synthetic-access-token").decode(),
                refresh_cipher=token_cipher.encrypt(b"synthetic-refresh-token").decode(),
                access_expires_at=int(time.time()) + 3600,
                refresh_expires_at=int(time.time()) + 86400,
            )
        )
        session.add(
            MediaAsset(
                id="publish-media-fixture",
                session_id=session_id,
                filename="clip.mp4",
                size=media_path.stat().st_size,
                path=str(media_path),
                duration_ms=duration_ms,
            )
        )
        session.commit()
    return app, client, factory, session_id, media_path


def test_direct_post_checks_fresh_creator_duration_before_initializing(tmp_path):
    app, client, _, _, _ = _linked_video(tmp_path, duration_ms=6001)
    observed_paths = []

    async def provider(request):
        observed_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "error": {"code": "ok"},
                "data": {
                    "privacy_level_options": ["SELF_ONLY"],
                    "max_video_post_duration_sec": 6,
                },
            },
        )

    app.state.tiktok.transport = httpx.MockTransport(provider)
    response = client.post(
        "/api/publish",
        headers={"x-csrf-token": client.cookies["zttato_csrf"]},
        json={
            "media_id": "publish-media-fixture",
            "mode": "direct",
            "idempotency_key": "duration-check-key-0001",
            "caption": "test",
            "privacy": "SELF_ONLY",
            "consent": True,
        },
    )
    assert response.status_code == 202
    assert observed_paths == ["/v2/post/publish/creator_info/query/"]
    with app.state.session_factory() as session:
        job = session.scalar(select(PublishJob).where(PublishJob.idempotency_key == "duration-check-key-0001"))
        assert job is not None
        assert job.status == "INITIATION_FAILED"


@pytest.mark.parametrize("mode", ["draft", "direct"])
def test_tiktok_video_api_ten_minute_limit_is_enforced_before_init(tmp_path, mode):
    app, client, _, _, _ = _linked_video(tmp_path, duration_ms=600_001)
    observed_paths = []

    async def provider(request):
        observed_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "error": {"code": "ok"},
                "data": {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 3600},
            },
        )

    app.state.tiktok.transport = httpx.MockTransport(provider)
    response = client.post(
        "/api/publish",
        headers={"x-csrf-token": client.cookies["zttato_csrf"]},
        json={
            "media_id": "publish-media-fixture",
            "mode": mode,
            "idempotency_key": "api-duration-limit-0001",
            "caption": "test",
            "privacy": "SELF_ONLY" if mode == "direct" else None,
            "consent": True,
        },
    )
    assert response.status_code == 422
    assert "10 minutes" in response.json()["detail"]
    assert observed_paths == []


def test_uncertain_publish_initialization_persists_job_and_replay_does_not_duplicate(tmp_path):
    app, client, factory, session_id, _ = _linked_video(tmp_path, duration_ms=5000)
    provider_calls = []

    async def provider(request):
        provider_calls.append(request.url.path)
        if request.url.path == "/v2/post/publish/creator_info/query/":
            return httpx.Response(
                200,
                json={
                    "error": {"code": "ok"},
                    "data": {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 10},
                },
            )
        if request.url.path == "/v2/post/publish/video/init/":
            return httpx.Response(502, text="synthetic upstream gateway failure")
        raise AssertionError(f"unexpected TikTok request: {request.url.path}")

    app.state.tiktok.transport = httpx.MockTransport(provider)
    payload = {
        "media_id": "publish-media-fixture",
        "mode": "direct",
        "idempotency_key": "uncertain-init-key-0001",
        "caption": "synthetic uncertain initiation",
        "privacy": "SELF_ONLY",
        "consent": True,
    }
    headers = {"x-csrf-token": client.cookies["zttato_csrf"]}
    response = client.post("/api/publish", headers=headers, json=payload)
    assert response.status_code == 202
    detail = response.json()

    with factory() as session:
        job = session.scalar(
            select(PublishJob).where(
                PublishJob.session_id == session_id,
                PublishJob.idempotency_key == payload["idempotency_key"],
            )
        )
        assert job is not None
        assert job.id == detail["job_id"]
        assert job.publish_id is None
        assert job.status == "INITIATION_UNCERTAIN"
        assert len(job.request_fingerprint) == 64
        assert job.consented_at > 0
        assert job.consent_version == "publish-consent-v1"
        assert payload["caption"] not in job.request_fingerprint

    replay = client.post("/api/publish", headers=headers, json=payload)
    assert replay.status_code == 202
    assert replay.json()["job_id"] == detail["job_id"]
    assert replay.json()["idempotent_replay"] is True
    altered_replay = client.post(
        "/api/publish",
        headers=headers,
        json={**payload, "caption": "different caption"},
    )
    assert altered_replay.status_code == 409
    assert provider_calls == [
        "/v2/post/publish/creator_info/query/",
        "/v2/post/publish/video/init/",
    ]
    refreshed = client.get(f"/api/jobs/{detail['job_id']}")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "INITIATION_UNCERTAIN"
    assert "Keep the same request key" in refreshed.json()["fail_reason"]
    assert provider_calls == [
        "/v2/post/publish/creator_info/query/",
        "/v2/post/publish/video/init/",
    ]


def test_publish_worker_recovers_a_persisted_encrypted_queue_job(tmp_path):
    app, client, factory, session_id, media_path = _linked_video(tmp_path, duration_ms=5000)
    provider_calls = []

    async def provider(request):
        provider_calls.append(request.url.path)
        if request.url.path == "/v2/post/publish/inbox/video/init/":
            return httpx.Response(
                200,
                json={
                    "error": {"code": "ok"},
                    "data": {
                        "publish_id": "synthetic-queued-publish",
                        "upload_url": "https://open-upload.tiktokapis.com/video/?upload_token=synthetic",
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(201)
        raise AssertionError(f"unexpected TikTok request: {request.url.path}")

    app.state.tiktok.transport = httpx.MockTransport(provider)
    payload = PublishInput(
        media_id="publish-media-fixture",
        mode="draft",
        idempotency_key="persisted-queue-job-key-0001",
        caption="synthetic private queue caption",
        consent=True,
    )
    request_data = payload.model_dump(
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
    request_cipher = TokenCipher(app.state.settings.encryption_key).encrypt(json.dumps(request_data))
    assert "synthetic private queue caption" not in request_cipher
    with factory() as session:
        session.add(
            PublishJob(
                id="persisted-queue-job",
                session_id=session_id,
                idempotency_key=payload.idempotency_key,
                media_id="publish-media-fixture",
                mode="draft",
                status="QUEUED",
                request_fingerprint=_publish_request_fingerprint(payload, app.state.settings),
                consented_at=int(time.time()),
                consent_version="publish-consent-v1",
                request_cipher=request_cipher,
            )
        )
        session.commit()
    with factory() as session:
        queued = session.get(PublishJob, "persisted-queue-job")
        assert queued is not None and queued.status == "QUEUED"
        assert queued.request_cipher is not None
        assert "synthetic private queue caption" not in queued.request_cipher
    client.close()

    with TestClient(app):
        deadline = time.monotonic() + 5
        status = "QUEUED"
        while time.monotonic() < deadline:
            with factory() as session:
                saved = session.get(PublishJob, "persisted-queue-job")
                status = saved.status if saved else "MISSING"
            if status == "PROCESSING":
                break
            time.sleep(0.02)

    with factory() as session:
        saved = session.get(PublishJob, "persisted-queue-job")
        assert saved is not None
        assert saved.status == "PROCESSING"
        assert saved.publish_id == "synthetic-queued-publish"
        assert saved.request_cipher is None
        assert saved.consented_at is not None
        assert saved.consent_version == "publish-consent-v1"
        assert "synthetic private queue caption" not in (saved.request_cipher or "")
    assert media_path.is_file()
    assert provider_calls == ["/v2/post/publish/inbox/video/init/", "/video/"]


def test_ambiguous_upload_failure_persists_publish_id_for_reconciliation(tmp_path):
    app, client, factory, session_id, _ = _linked_video(tmp_path, duration_ms=5000)
    status_calls = []

    async def provider(request):
        if request.url.path == "/v2/post/publish/creator_info/query/":
            return httpx.Response(
                200,
                json={
                    "error": {"code": "ok"},
                    "data": {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 10},
                },
            )
        if request.url.path == "/v2/post/publish/video/init/":
            return httpx.Response(
                200,
                json={
                    "error": {"code": "ok"},
                    "data": {
                        "publish_id": "synthetic-publish-id",
                        "upload_url": "https://open-upload.tiktokapis.com/video/?upload_token=synthetic",
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(429, headers={"Retry-After": "12"})
        if request.url.path == "/v2/post/publish/status/fetch/":
            status_calls.append(request.url.path)
            return httpx.Response(200, json={"error": {"code": "ok"}, "data": {"status": "PROCESSING"}})
        raise AssertionError("unexpected provider endpoint")

    app.state.tiktok.transport = httpx.MockTransport(provider)
    payload = {
        "media_id": "publish-media-fixture",
        "mode": "direct",
        "idempotency_key": "reconcile-test-key-0001",
        "caption": "test",
        "privacy": "SELF_ONLY",
        "consent": True,
    }
    response = client.post("/api/publish", headers={"x-csrf-token": client.cookies["zttato_csrf"]}, json=payload)
    assert response.status_code == 202
    with factory() as session:
        job = session.scalar(select(PublishJob).where(PublishJob.session_id == session_id))
        assert job is not None
        assert job.publish_id == "synthetic-publish-id"
        assert job.status == "RECONCILIATION_REQUIRED"
        job_id = job.id

    reconciled = client.get(f"/api/jobs/{job_id}")
    assert reconciled.status_code == 200
    assert "publish_id" not in reconciled.json()
    assert reconciled.json()["status"] == "PROCESSING"
    assert status_calls == ["/v2/post/publish/status/fetch/"]


def test_tiktok_rate_limit_error_classification_and_retry_after():
    async def http_429(_request):
        return httpx.Response(429, headers={"Retry-After": "7200"})

    client = TikTokClient(None, transport=httpx.MockTransport(http_429))
    with pytest.raises(HTTPException) as error:
        asyncio.run(client.creator_info("synthetic-token"))
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "3600"


def test_cleanup_deletes_expired_oauth_tokens_sessions_uploads_and_old_records(tmp_path, monkeypatch):
    import app.maintenance as maintenance

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    engine, factory = make_session_factory("sqlite:///" + str(tmp_path / "cleanup.db"))
    now = 2_000_000_000
    old = now - 100 * 86_400
    expired_file = media_dir / "expired.mp4"
    expired_file.write_bytes(b"synthetic-media")
    keep_file = media_dir / "keep.mp4"
    keep_file.write_bytes(b"synthetic-media")
    pending_file = media_dir / "pending.mp4"
    pending_file.write_bytes(b"synthetic-media")
    with factory() as session:
        session.add_all(
            [
                BrowserSession(id="expired", csrf_hash="x", expires_at=now - 1, created_at=old),
                BrowserSession(id="blocked", csrf_hash="x", expires_at=now - 1, created_at=old),
                BrowserSession(id="pending", csrf_hash="x", expires_at=now - 1, created_at=old),
                BrowserSession(id="active", csrf_hash="x", expires_at=now + 1000, created_at=old),
            ]
        )
        session.flush()
        session.add_all(
            [
                OAuthRequest(state_hash="expired-oauth", session_id="expired", expires_at=now - 1),
                OAuthRequest(state_hash="active-oauth", session_id="active", expires_at=now + 1000),
                LinkedAccount(
                    session_id="expired",
                    open_id="synthetic-1",
                    scopes="video.upload",
                    access_cipher="cipher-a",
                    refresh_cipher="cipher-b",
                    access_expires_at=now,
                    refresh_expires_at=now,
                ),
                LinkedAccount(
                    session_id="blocked",
                    open_id="synthetic-2",
                    scopes="video.upload",
                    access_cipher="cipher-c",
                    refresh_cipher="cipher-d",
                    access_expires_at=now,
                    refresh_expires_at=now,
                ),
                MediaAsset(
                    id="expired-media",
                    session_id="expired",
                    filename="expired.mp4",
                    size=15,
                    path=str(expired_file),
                    created_at=old,
                ),
                MediaAsset(
                    id="blocked-media",
                    session_id="blocked",
                    filename="keep.mp4",
                    size=15,
                    path=str(keep_file),
                    created_at=old,
                ),
                MediaAsset(
                    id="old-active-media",
                    session_id="active",
                    filename="old.mp4",
                    size=1,
                    path=str(media_dir / "not-present.mp4"),
                    created_at=old,
                ),
                MediaAsset(
                    id="pending-media",
                    session_id="pending",
                    filename="pending.mp4",
                    size=15,
                    path=str(pending_file),
                    created_at=old,
                ),
                PublishJob(
                    id="blocked-job",
                    session_id="blocked",
                    idempotency_key="blocked-job-idem",
                    media_id="blocked-media",
                    mode="draft",
                    publish_id="synthetic-pub",
                    status="FAILED",
                    checked_at=0,
                    created_at=old,
                ),
                PublishJob(
                    id="pending-job",
                    session_id="pending",
                    idempotency_key="pending-job-idempotency",
                    media_id="pending-media",
                    mode="direct",
                    publish_id="synthetic-pending-publish",
                    status="PROCESSING",
                    checked_at=0,
                    created_at=now - 86_400,
                    updated_at=now - 86_400,
                ),
                PublishJob(
                    id="stale-init-job",
                    session_id="active",
                    idempotency_key="stale-init-job-idem",
                    media_id="old-active-media",
                    mode="draft",
                    status="INITIATING",
                    checked_at=0,
                    created_at=now - 3600,
                    updated_at=now - 901,
                ),
                PublishJob(
                    id="stale-transfer-job",
                    session_id="active",
                    idempotency_key="stale-transfer-job-idem",
                    media_id="old-active-media",
                    mode="direct",
                    publish_id="synthetic-transfer-publish",
                    status="TRANSFER_PENDING",
                    checked_at=0,
                    created_at=now - 3600,
                    updated_at=now - 901,
                ),
                QuotaBucket(
                    subject_hash="a" * 64,
                    scope="old",
                    window_start=now - 3 * 86_400,
                    request_count=1,
                    request_bytes=0,
                ),
            ]
        )
        session.commit()

    original_remove = maintenance.remove_local_upload

    def selectively_fail(root, stored_path):
        if stored_path == str(keep_file):
            return False
        return original_remove(root, stored_path)

    monkeypatch.setattr(maintenance, "remove_local_upload", selectively_fail)
    result = run_cleanup(factory, media_dir, media_retention_days=30, record_retention_days=90, now=now)
    assert result["sessions"] == 1
    assert result["linked_accounts"] == 1
    assert result["file_delete_failures"] == 1
    assert result["jobs_marked_uncertain"] == 1
    assert result["jobs_marked_reconciliation"] == 1
    assert result["sessions_deferred_for_publish"] == 1
    assert not expired_file.exists()
    assert keep_file.exists()
    assert pending_file.exists()
    with factory() as session:
        assert session.get(BrowserSession, "expired") is None
        assert session.get(LinkedAccount, 2) is not None
        assert session.get(BrowserSession, "blocked") is not None
        assert session.get(MediaAsset, "blocked-media") is not None
        assert session.get(PublishJob, "blocked-job") is not None
        assert session.get(BrowserSession, "pending") is not None
        assert session.get(PublishJob, "pending-job") is not None
        stale = session.get(PublishJob, "stale-init-job")
        assert stale.status == "INITIATION_UNCERTAIN"
        assert "before retrying" in stale.fail_reason
        stale_transfer = session.get(PublishJob, "stale-transfer-job")
        assert stale_transfer.status == "RECONCILIATION_REQUIRED"
        assert "refresh provider status" in stale_transfer.fail_reason
        assert session.get(OAuthRequest, "active-oauth") is not None
        assert session.scalar(select(QuotaBucket).where(QuotaBucket.scope == "old")) is None
    engine.dispose()


def test_upload_path_guard_never_deletes_outside_media_root(tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    external = tmp_path / "outside.txt"
    external.write_text("keep", encoding="utf-8")
    assert remove_local_upload(media_root, str(external)) is True
    assert external.read_text(encoding="utf-8") == "keep"


def test_user_data_deletion_removes_local_upload_and_preserves_outside_file(tmp_path):
    app, client = connected_profile_client(tmp_path)
    media_root = Path(app.state.settings.media_dir)
    local_file = media_root / "user-upload.mp4"
    external_file = tmp_path / "outside-upload.mp4"
    local_file.write_bytes(b"synthetic-media")
    external_file.write_bytes(b"must-stay-outside-media-root")
    session_id = digest(client.cookies["zttato_session"])
    factory = app.state.session_factory
    with factory() as session:
        session.add_all(
            [
                MediaAsset(
                    id="local-user-media",
                    session_id=session_id,
                    filename="local.mp4",
                    size=15,
                    path=str(local_file),
                ),
                MediaAsset(
                    id="external-user-media",
                    session_id=session_id,
                    filename="external.mp4",
                    size=15,
                    path=str(external_file),
                ),
            ]
        )
        session.add(
            OAuthRequest(state_hash=digest("pending-state"), session_id=session_id, expires_at=int(time.time()) + 900)
        )
        session.commit()

    async def revoke(request):
        assert request.url.path == "/v2/oauth/revoke/"
        return httpx.Response(200, json={"error": {"code": "ok"}, "data": {}})

    app.state.tiktok.transport = httpx.MockTransport(revoke)
    response = client.delete("/api/my-data", headers={"x-csrf-token": client.cookies["zttato_csrf"]})
    assert response.status_code == 200
    assert not local_file.exists()
    assert external_file.read_bytes() == b"must-stay-outside-media-root"
    with factory() as session:
        assert session.get(BrowserSession, session_id) is None
        assert session.scalar(select(LinkedAccount).where(LinkedAccount.session_id == session_id)) is None
        assert session.scalar(select(MediaAsset).where(MediaAsset.session_id == session_id)) is None
        assert session.scalar(select(OAuthRequest).where(OAuthRequest.session_id == session_id)) is None


def test_tiktok_json_rate_limit_error_is_classified_as_429():
    async def handler(_request):
        return httpx.Response(200, json={"error": {"code": "rate_limit_exceeded"}})

    client = TikTokClient(None, transport=httpx.MockTransport(handler))
    with pytest.raises(HTTPException) as error:
        asyncio.run(client.creator_info("synthetic-token"))
    assert error.value.status_code == 429
