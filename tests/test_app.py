import json
import re
import time
from dataclasses import replace
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.config import Settings, load_settings
from app.db import LinkedAccount, make_session_factory
from app.main import create_app, safe_avatar_url
from app.security import digest


def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        base_url="http://testserver",
        allowed_hosts=("testserver",),
        database_url="sqlite:///" + str(tmp_path / "test.db"),
        encryption_key="",
        client_key="REPLACE_TEST",
        client_secret="REPLACE_TEST",
        scopes=("user.info.basic", "video.upload", "video.publish"),
        app_audited=False,
        legal_entity="Example Operator",
        legal_email="privacy@example.test",
        legal_address="Example Address",
        media_dir=str(tmp_path / "media"),
        max_video_bytes=1024 * 1024,
    )


def production_settings(tmp_path: Path) -> Settings:
    return replace(
        settings(tmp_path),
        env="production",
        base_url="https://zttato.zeaz.dev",
        allowed_hosts=("zttato.zeaz.dev",),
        database_url="postgresql+psycopg://synthetic:synthetic@db:5432/zttato",
        encryption_key=Fernet.generate_key().decode(),
        client_key="synthetic-client-key",
        client_secret="synthetic-client-secret",
        legal_entity="ZTTato Operator Group",
        legal_email="support@zttato.zeaz.dev",
        legal_address="1 Service Road, Bangkok 10110, Thailand",
        metrics_bearer_token="synthetic-metrics-bearer-token-123456789",
    )


def test_public_pages_and_headers(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    for path in ("/", "/dashboard", "/review-preview", "/privacy-policy", "/terms-of-service"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_tiktok_site_verification_endpoint(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    response = client.get("/tiktok/uploading/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "tiktok-developers-site-verification=9WurARgpbnJkdus0r4bfvSZydNYNxKUC"


def test_session_is_created_without_exposing_tiktok_token(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    response = client.get("/api/session")
    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert "zttato_session" in response.cookies
    assert "access_token" not in response.text


def test_existing_session_cookie_is_not_reflected_into_response(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    created = client.get("/api/session")
    assert "set-cookie" in created.headers

    reused = client.get("/api/session")
    assert reused.status_code == 200
    assert "set-cookie" not in reused.headers


def test_oauth_start_only_sets_server_generated_session_cookies(tmp_path):
    app_settings = replace(settings(tmp_path), client_key="client-key")
    client = TestClient(create_app(app_settings))

    created = client.get("/auth/tiktok/start", follow_redirects=False)
    assert created.status_code == 302
    assert "set-cookie" in created.headers

    reused = client.get("/auth/tiktok/start", follow_redirects=False)
    assert reused.status_code == 302
    assert "set-cookie" not in reused.headers


def test_oauth_start_fails_closed_without_real_client_key(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    response = client.get("/auth/tiktok/start", follow_redirects=False)
    assert response.status_code == 503


def test_readiness_uses_database(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_production_readiness_fails_without_migration(tmp_path, monkeypatch):
    engine, factory = make_session_factory("sqlite:///" + str(tmp_path / "production-no-migration.db"))
    monkeypatch.setattr("app.main.make_session_factory", lambda *_args, **_kwargs: (engine, factory))
    app = create_app(production_settings(tmp_path))
    response = TestClient(app).get("/health/ready", headers={"host": "zttato.zeaz.dev"})
    assert response.status_code == 503
    engine.dispose()


def test_staging_disables_schema_bootstrap_and_fails_readiness_without_migration(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.validate_deployment_config", lambda _settings: None)
    conf = replace(settings(tmp_path), env="staging")
    app = create_app(conf)
    assert inspect(app.state.engine).get_table_names() == []
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    app.state.engine.dispose()


def test_production_rejects_wildcard_hosts_and_missing_metrics_secret(tmp_path):
    for invalid in (
        replace(production_settings(tmp_path), allowed_hosts=("*",)),
        replace(production_settings(tmp_path), metrics_bearer_token=""),
    ):
        try:
            create_app(invalid)
        except ValueError:
            continue
        raise AssertionError("unsafe production settings must fail closed")


def test_production_rejects_placeholder_legal_data_and_unsupported_scope(tmp_path):
    for invalid in (
        replace(production_settings(tmp_path), legal_entity="Legal operator details pending"),
        replace(production_settings(tmp_path), scopes=("user.info.basic", "user.info.stats")),
        replace(production_settings(tmp_path), database_url="mysql://user:pass@db/zttato"),
    ):
        try:
            create_app(invalid)
        except ValueError:
            continue
        raise AssertionError("unsupported production settings must fail closed")


def test_unknown_app_environment_fails_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    try:
        load_settings()
    except ValueError as exc:
        assert "APP_ENV" in str(exc)
    else:
        raise AssertionError("unknown APP_ENV must fail closed")


def test_production_rejects_host_mismatch(tmp_path):
    bad = replace(
        settings(tmp_path),
        env="production",
        base_url="https://zttato.zeaz.dev",
        allowed_hosts=("localhost",),
        database_url="postgresql+psycopg://user:password@db:5432/zttato",
        encryption_key="valid-test-key",
        client_key="client-key",
        client_secret="client-secret",
    )
    try:
        create_app(bad)
    except ValueError as exc:
        assert "APP_ALLOWED_HOSTS" in str(exc)
    else:
        raise AssertionError("host mismatch must fail closed")


def test_production_rejects_url_values_in_allowed_hosts(tmp_path):
    bad = replace(
        settings(tmp_path),
        env="production",
        base_url="https://zttato.zeaz.dev",
        allowed_hosts=("https://zttato.zeaz.dev",),
        database_url="postgresql+psycopg://user:password@db:5432/zttato",
        encryption_key="valid-test-key",
        client_key="client-key",
        client_secret="client-secret",
    )
    try:
        create_app(bad)
    except ValueError as exc:
        assert "hostnames only" in str(exc)
    else:
        raise AssertionError("URL-shaped allowed host must fail closed")


def test_profile_requires_authenticated_session_and_authorized_scope(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    assert client.get("/api/profile").status_code == 401
    client.get("/api/session")
    assert client.get("/api/profile").status_code == 401


def connected_profile_client(tmp_path, scopes="user.info.basic,video.upload", configuration=None):
    key = Fernet.generate_key().decode()
    conf = replace(configuration or settings(tmp_path), encryption_key=key)
    app = create_app(conf)
    client = TestClient(app)
    assert client.get("/api/session").status_code == 200

    engine, factory = make_session_factory(conf.database_url, bootstrap=False)
    token_cipher = Fernet(key.encode())
    with factory() as session:
        session.add(
            LinkedAccount(
                session_id=digest(client.cookies["zttato_session"]),
                open_id="fixture-open-id",
                scopes=scopes,
                access_cipher=token_cipher.encrypt(b"fixture-access-token").decode(),
                refresh_cipher=token_cipher.encrypt(b"fixture-refresh-token").decode(),
                access_expires_at=int(time.time()) + 3600,
                refresh_expires_at=int(time.time()) + 86400,
            )
        )
        session.commit()
    engine.dispose()
    return app, client


def test_profile_reads_tiktok_user_info_without_exposing_identifiers_or_tokens(tmp_path):
    app, client = connected_profile_client(tmp_path)
    avatar = "https://p16-sign.tiktokcdn-us.com/avatar.jpeg?expires=123&signature=fixture"
    observed = []

    async def provider(request):
        observed.append((request.method, request.url.path, request.url.params.get("fields")))
        assert request.headers["authorization"] == "Bearer fixture-access-token"
        return httpx.Response(
            200,
            json={
                "error": {"code": "ok"},
                "data": {
                    "user": {
                        "open_id": "fixture-open-id",
                        "display_name": "Fixture Creator",
                        "avatar_url": avatar,
                        "access_token": "must-not-be-returned",
                    }
                },
            },
        )

    app.state.tiktok.transport = httpx.MockTransport(provider)
    response = client.get("/api/profile")
    assert response.status_code == 200
    assert response.json() == {"display_name": "Fixture Creator", "avatar_url": avatar}
    assert response.headers["cache-control"] == "no-store"
    assert "fixture-open-id" not in response.text
    assert "fixture-access-token" not in response.text
    assert "must-not-be-returned" not in response.text
    assert observed == [
        ("GET", "/v2/user/info/", "open_id,avatar_url,display_name"),
    ]


def test_profile_requires_user_info_basic_even_when_video_scope_is_granted(tmp_path):
    app, client = connected_profile_client(tmp_path, scopes="video.publish,video.upload")

    async def unexpected_request(request):
        raise AssertionError("Profile endpoint should reject missing scope before reaching TikTok")

    app.state.tiktok.transport = httpx.MockTransport(unexpected_request)
    response = client.get("/api/profile")
    assert response.status_code == 403


def test_profile_rejects_mismatched_tiktok_identity(tmp_path):
    app, client = connected_profile_client(tmp_path)

    async def provider(request):
        return httpx.Response(
            200,
            json={"error": {"code": "ok"}, "data": {"user": {"open_id": "someone-else", "display_name": "Other"}}},
        )

    app.state.tiktok.transport = httpx.MockTransport(provider)
    response = client.get("/api/profile")
    assert response.status_code == 502
    assert "someone-else" not in response.text


def test_profile_avatar_rejects_untrusted_image_urls():
    assert safe_avatar_url("https://p16-sign.tiktokcdn-us.com/avatar.jpeg") == (
        "https://p16-sign.tiktokcdn-us.com/avatar.jpeg"
    )
    for url in (
        "http://p16-sign.tiktokcdn-us.com/avatar.jpeg",
        "https://tiktokcdn-us.com.evil.example/avatar.jpeg",
        "https://127.0.0.1/avatar.jpeg",
        "https://user:password@p16-sign.tiktokcdn-us.com/avatar.jpeg",
        "https://p16-sign.tiktokcdn-us.com:8080/avatar.jpeg",
        "https://p16-sign.tiktokcdn-us.com/avatar.jpeg#fragment",
    ):
        assert safe_avatar_url(url) is None


def test_dashboard_markup_and_scripts_have_matching_ids_and_decoded_separators():
    web = Path(__file__).resolve().parent.parent / "web"
    html = (web / "dashboard.html").read_text("utf-8")
    javascript = (web / "dashboard.js").read_text("utf-8")
    identifiers = set(re.findall(r'\bid="([^"]+)"', html))
    references = set(re.findall(r'\$\("([a-z][a-z0-9-]+)"\)', javascript))
    assert references <= identifiers, f"Missing dashboard elements: {references - identifiers}"
    for required in ("profile", "profile-avatar", "profile-name", "profile-message"):
        assert required in identifiers
    assert "&middot;" in html
    assert r"\u00b7" not in html
    assert r"\u00b7" not in javascript


def billing_client(tmp_path, handler):
    conf = replace(
        settings(tmp_path),
        zwallet_adapter_url="https://zwallet.example.test",
        z_platform_service_token="service-token-test-value",
    )
    app = create_app(conf)
    client = TestClient(app)
    client.get("/api/session")
    app.state.zwallet.transport = httpx.MockTransport(handler)
    return app, client


def post_invoice_intent(client, *, csrf=True, payload=None):
    headers = {"x-csrf-token": client.cookies["zttato_csrf"]} if csrf else {}
    return client.post(
        "/api/billing/invoice-intents",
        json=payload
        or {
            "idempotency_key": "intent-key-00000001",
            "currency": "USD",
            "amount_minor": 500,
        },
        headers=headers,
    )


def test_zwallet_invoice_intent_is_session_scoped_and_server_authenticated(tmp_path):
    observed = []

    async def adapter(request):
        body = json.loads(request.content)
        observed.append(
            (request.url.path, request.headers.get("authorization"), request.headers.get("idempotency-key"), body)
        )
        return httpx.Response(
            201,
            json={
                "intent_id": "intent-123",
                "idempotency_key": body["idempotency_key"],
                "tenant_id": body["tenant_id"],
                "currency": body["currency"],
                "amount_minor": body["amount_minor"],
                "status": "requires_payment_processor",
                "created_at": "2026-09-27T12:00:00Z",
            },
        )

    app, client = billing_client(tmp_path, adapter)
    response = post_invoice_intent(client)

    assert response.status_code == 200
    assert response.json() == {
        "intent_id": "intent-123",
        "currency": "USD",
        "amount_minor": 500,
        "status": "requires_payment_processor",
        "created_at": "2026-09-27T12:00:00Z",
    }
    assert response.headers["cache-control"] == "no-store"
    assert len(observed) == 1
    path, authorization, idempotency_key, body = observed[0]
    assert path == "/api/invoice-intents"
    assert authorization == "Bearer service-token-test-value"
    assert idempotency_key == "intent-key-00000001"
    assert body == {
        "tenant_id": digest(client.cookies["zttato_session"]),
        "idempotency_key": "intent-key-00000001",
        "currency": "USD",
        "amount_minor": 500,
    }
    assert "tenant_id" not in response.json()
    assert "service-token-test-value" not in response.text
    assert client.get("/api/session").json()["connected"] is False


def test_zwallet_invoice_intent_requires_csrf_and_rejects_client_tenant_id(tmp_path):
    async def unexpected_adapter_call(request):
        raise AssertionError("invalid requests must not reach the adapter")

    _, client = billing_client(tmp_path, unexpected_adapter_call)
    assert post_invoice_intent(client, csrf=False).status_code == 403
    response = post_invoice_intent(
        client,
        payload={
            "tenant_id": "attacker-tenant",
            "idempotency_key": "intent-key-00000001",
            "currency": "USD",
            "amount_minor": 500,
        },
    )
    assert response.status_code == 422


def test_zwallet_invoice_intent_retries_transient_failure_with_same_key(tmp_path):
    calls = []

    async def adapter(request):
        calls.append(request.headers["idempotency-key"])
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "intent_id": "intent-replayed",
                "idempotency_key": body["idempotency_key"],
                "tenant_id": body["tenant_id"],
                "currency": body["currency"],
                "amount_minor": body["amount_minor"],
                "status": "requires_payment_processor",
                "created_at": "2026-09-27T12:00:00Z",
            },
        )

    _, client = billing_client(tmp_path, adapter)
    response = post_invoice_intent(client)
    assert response.status_code == 200
    assert response.json()["intent_id"] == "intent-replayed"
    assert calls == ["intent-key-00000001", "intent-key-00000001"]


def test_zwallet_invoice_intent_caps_transient_retries(tmp_path):
    calls = []

    async def unavailable_adapter(request):
        calls.append(request.headers["idempotency-key"])
        return httpx.Response(503, json={"error": "temporary"})

    _, client = billing_client(tmp_path, unavailable_adapter)
    response = post_invoice_intent(client)
    assert response.status_code == 503
    assert len(calls) == 3
    assert "temporary" not in response.text


def test_zwallet_invoice_intent_rejects_oversized_adapter_response(tmp_path):
    async def oversized_adapter(request):
        return httpx.Response(
            201,
            content=b"x" * (64 * 1024 + 1),
            headers={"content-type": "application/json"},
        )

    _, client = billing_client(tmp_path, oversized_adapter)
    response = post_invoice_intent(client)
    assert response.status_code == 502
    assert "service-token-test-value" not in response.text


def test_zwallet_invoice_intent_fails_closed_when_unconfigured_or_response_mismatches(tmp_path):
    client = TestClient(create_app(settings(tmp_path)))
    client.get("/api/session")
    assert post_invoice_intent(client).status_code == 503

    async def mismatched_adapter(request):
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "intent_id": "intent-123",
                "idempotency_key": body["idempotency_key"],
                "tenant_id": "different-tenant",
                "currency": body["currency"],
                "amount_minor": body["amount_minor"],
                "status": "requires_payment_processor",
                "created_at": "2026-09-27T12:00:00Z",
            },
        )

    app, client = billing_client(tmp_path / "mismatch", mismatched_adapter)
    response = post_invoice_intent(client)
    assert response.status_code == 502
    assert "different-tenant" not in response.text
    assert "service-token-test-value" not in response.text


def test_production_zwallet_adapter_requires_https_off_loopback(tmp_path):
    bad = replace(
        settings(tmp_path),
        env="production",
        base_url="https://testserver",
        database_url="postgresql+psycopg://user:password@db:5432/zttato",
        encryption_key="test-encryption-key",
        client_key="client-key",
        client_secret="client-secret",
        zwallet_adapter_url="http://zwallet.internal:3040",
        z_platform_service_token="service-token-test-value",
    )
    try:
        create_app(bad)
    except ValueError as exc:
        assert "must use HTTPS" in str(exc)
    else:
        raise AssertionError("production adapter traffic must use HTTPS off loopback")
