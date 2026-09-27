"""Regression coverage for TikTok's published media-transfer contract."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from app.tiktok import MAX_VIDEO_SIZE, TikTokClient, plan_video_chunks


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (4 * 1024**2, (4 * 1024**2, 1)),
        (64_000_000, (64_000_000, 1)),
        (64_000_001, (32_000_000, 2)),
        (70 * 1024**2, (32_000_000, 2)),
        (MAX_VIDEO_SIZE, (32_000_000, 125)),
    ],
)
def test_chunk_plan_uses_tiktok_floor_rule(size, expected):
    assert plan_video_chunks(size) == expected


@pytest.mark.parametrize("size", (0, -1, MAX_VIDEO_SIZE + 1))
def test_chunk_plan_rejects_invalid_size(size):
    with pytest.raises(HTTPException) as error:
        plan_video_chunks(size)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "https://open-upload.tiktokapis.com/video/?upload_id=1&upload_token=fixture",
        "https://upload.us.tiktokapis.com/video/?upload_id=1&upload_token=fixture",
        "https://open-upload.tiktokapis.com/upload/?upload_id=1&upload_token=fixture",
    ],
)
def test_official_upload_destinations_allowed(url):
    TikTokClient.validate_upload_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://open-upload.tiktokapis.com/video/?upload_token=fixture",
        "https://open-upload.tiktokapis.com.evil.example/video/?upload_token=fixture",
        "https://127.0.0.1/video/?upload_token=fixture",
        "https://open-upload.tiktokapis.com@evil.example/video/?upload_token=fixture",
        "https://open-upload.tiktokapis.com/video/?upload_token=fixture#fragment",
        "https://open-upload.tiktokapis.com/video/",
        "https://open-upload.tiktokapis.com:9999/video/?upload_token=fixture",
    ],
)
def test_untrusted_upload_destinations_rejected(url):
    with pytest.raises(HTTPException) as error:
        TikTokClient.validate_upload_url(url)
    assert error.value.status_code == 502


def test_init_video_declares_correct_chunk_count():
    captured = []

    async def handler(request):
        captured.append(json.loads(await request.aread()))
        return httpx.Response(
            200,
            json={
                "error": {"code": "ok"},
                "data": {
                    "publish_id": "fixture-publish-id",
                    "upload_url": "https://upload.us.tiktokapis.com/video/?upload_token=fixture",
                },
            },
        )

    client = TikTokClient(None, transport=httpx.MockTransport(handler))
    publish_id, _ = asyncio.run(
        client.init_video(
            "fixture-access-token",
            mode="draft",
            media_size=70 * 1024**2,
            caption="",
            privacy=None,
            disable_comment=False,
            disable_duet=False,
            disable_stitch=False,
        )
    )
    assert publish_id == "fixture-publish-id"
    assert captured[0]["source_info"] == {
        "source": "FILE_UPLOAD",
        "video_size": 70 * 1024**2,
        "chunk_size": 32_000_000,
        "total_chunk_count": 2,
    }


@pytest.mark.parametrize("mode", ("draft", "direct"))
def test_init_photo_sends_description_for_both_modes_and_top_level_ai_flag(mode):
    captured = []

    async def handler(request):
        captured.append(json.loads(await request.aread()))
        return httpx.Response(200, json={"error": {"code": "ok"}, "data": {"publish_id": "photo-job"}})

    client = TikTokClient(None, transport=httpx.MockTransport(handler))
    publish_id, upload_url = asyncio.run(
        client.init_photo(
            "fixture-access-token",
            mode=mode,
            caption="A photo caption 😀",
            privacy="SELF_ONLY" if mode == "direct" else None,
            disable_comment=True,
            brand_content_toggle=False,
            brand_organic_toggle=True,
            is_aigc=True,
            photo_images=["https://media.example/photo.webp"],
            photo_cover_index=0,
        )
    )

    assert publish_id == "photo-job"
    assert upload_url == ""
    request = captured[0]
    assert request["post_info"]["description"] == "A photo caption 😀"
    assert "title" not in request["post_info"]
    assert request["is_aigc"] is True
    assert request["media_type"] == "PHOTO"
    assert request["post_mode"] == ("DIRECT_POST" if mode == "direct" else "MEDIA_UPLOAD")
    assert request["source_info"] == {
        "source": "PULL_FROM_URL",
        "photo_images": ["https://media.example/photo.webp"],
        "photo_cover_index": 0,
    }
    if mode == "direct":
        assert request["post_info"]["privacy_level"] == "SELF_ONLY"
        assert request["post_info"]["brand_organic_toggle"] is True


def test_upload_streams_sequential_chunks_without_losing_trailing_bytes(tmp_path: Path):
    size = 64 * 1024**2 + 123
    media = tmp_path / "fixture.mp4"
    with media.open("wb") as stream:
        stream.truncate(size)
    observed = []

    async def handler(request):
        length = 0
        async for part in request.stream:
            length += len(part)
        observed.append(
            (
                request.headers["content-range"],
                int(request.headers["content-length"]),
                length,
            )
        )
        return httpx.Response(206 if len(observed) == 1 else 201)

    client = TikTokClient(None, transport=httpx.MockTransport(handler))
    asyncio.run(
        client.upload_video(
            "https://open-upload.tiktokapis.com/video/?upload_token=fixture",
            str(media),
            size,
        )
    )
    assert observed == [
        (f"bytes 0-{32_000_000 - 1}/{size}", 32_000_000, 32_000_000),
        (f"bytes {32_000_000}-{size - 1}/{size}", size - 32_000_000, size - 32_000_000),
    ]


def test_unexpected_chunk_acknowledgement_stops_transfer(tmp_path: Path):
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"some-mp4-bytes")

    async def handler(request):
        await request.aread()
        return httpx.Response(206)

    client = TikTokClient(None, transport=httpx.MockTransport(handler))
    with pytest.raises(HTTPException, match="reconcile"):
        asyncio.run(
            client.upload_video(
                "https://open-upload.tiktokapis.com/video/?upload_token=fixture",
                str(media),
                media.stat().st_size,
            )
        )


def test_user_info_queries_only_basic_fields_and_rejects_malformed_data():
    captured = []

    async def handler(request):
        captured.append((request.method, str(request.url)))
        return httpx.Response(200, json={"error": {"code": "ok"}, "data": None})

    client = TikTokClient(None, transport=httpx.MockTransport(handler))
    with pytest.raises(HTTPException) as error:
        asyncio.run(client.user_info("fixture-token"))
    assert error.value.status_code == 502
    assert captured == [
        ("GET", "https://open.tiktokapis.com/v2/user/info/?fields=open_id,avatar_url,display_name"),
    ]
