import os
from dataclasses import dataclass
import ipaddress
from urllib.parse import unquote, urlparse

APP_ENVIRONMENTS = frozenset({"development", "test", "sandbox", "staging", "production"})
SUPPORTED_TIKTOK_SCOPES = frozenset({"user.info.basic", "video.upload", "video.publish"})


def _decoded_path(path: str) -> str:
    decoded = path
    for _ in range(8):
        unquoted = unquote(decoded)
        if unquoted == decoded:
            return decoded
        decoded = unquoted
    raise ValueError("URL path uses excessive percent encoding")


def _has_unsafe_path_segments(path: str) -> bool:
    try:
        decoded = _decoded_path(path)
    except ValueError:
        return True
    return "\\" in decoded or any(segment in {".", ".."} for segment in decoded.split("/"))


@dataclass(frozen=True)
class Settings:
    env: str
    base_url: str
    allowed_hosts: tuple[str, ...]
    database_url: str
    encryption_key: str
    client_key: str
    client_secret: str
    scopes: tuple[str, ...]
    app_audited: bool
    legal_entity: str
    legal_email: str
    legal_address: str
    media_dir: str
    max_video_bytes: int
    zwallet_adapter_url: str = ""
    z_platform_service_token: str = ""
    max_requests_per_minute: int = 120
    max_uploads_per_day: int = 10
    max_upload_bytes_per_day: int = 268_435_456
    media_retention_days: int = 30
    record_retention_days: int = 90
    cleanup_interval_seconds: int = 3600
    publish_worker_interval_seconds: int = 2
    metrics_bearer_token: str = ""
    tiktok_verified_media_url_prefixes: tuple[str, ...] = ()

    @property
    def redirect_uri(self) -> str:
        return self.base_url.rstrip("/") + "/tiktok/callback"

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


def validate_host_config(s: Settings) -> None:
    """Reject untrusted and inconsistent public host configuration."""
    u = urlparse(s.base_url)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("APP_BASE_URL must be an absolute URL without credentials or query")
    if not s.allowed_hosts:
        raise ValueError("APP_ALLOWED_HOSTS must contain at least one host")
    normalized_hosts = {host.strip().lower().rstrip(".") for host in s.allowed_hosts}
    base_host = u.hostname.lower().rstrip(".")
    if any("://" in host or "/" in host or "@" in host for host in normalized_hosts if host != "*"):
        raise ValueError("APP_ALLOWED_HOSTS must contain hostnames only")
    if base_host not in normalized_hosts and "*" not in normalized_hosts:
        raise ValueError("APP_ALLOWED_HOSTS must include the APP_BASE_URL hostname")


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized or normalized.startswith("replace_"):
        return True
    return normalized in {
        "legal operator details pending",
        "contact details pending",
        "address pending legal review",
        "example operator",
        "example address",
        "pending",
    }


def validate_deployment_config(s: Settings) -> None:
    """Fail closed on incomplete, placeholder or overbroad deployment settings."""
    if s.env not in APP_ENVIRONMENTS:
        raise ValueError("APP_ENV must be development, test, sandbox, staging, or production")
    validate_host_config(s)
    validate_tiktok_media_url_prefixes(s)
    validate_zwallet_config(s)

    if not s.scopes or len(s.scopes) != len(set(s.scopes)):
        raise ValueError("TIKTOK_SCOPES must contain unique supported scopes")
    if set(s.scopes) - SUPPORTED_TIKTOK_SCOPES:
        raise ValueError("TIKTOK_SCOPES includes a capability that this application does not implement")
    if not 1 <= s.publish_worker_interval_seconds <= 60:
        raise ValueError("PUBLISH_WORKER_INTERVAL_SECONDS must be between 1 and 60")

    deployment_env = s.env in {"staging", "production"}
    if deployment_env:
        if s.env == "production" and "*" in {host.strip().lower() for host in s.allowed_hosts}:
            raise ValueError("Production APP_ALLOWED_HOSTS must not contain a wildcard")
        parsed_base = urlparse(s.base_url)
        if parsed_base.scheme != "https":
            raise ValueError("Staging and production APP_BASE_URL must use HTTPS")
        try:
            base_host = (parsed_base.hostname or "").lower().rstrip(".")
            loopback = ipaddress.ip_address(base_host).is_loopback
        except ValueError:
            loopback = False
        if loopback or base_host in {"localhost", "example.com", "example.net", "example.org"}:
            raise ValueError("Staging and production APP_BASE_URL must use a real public hostname")

        database_scheme = urlparse(s.database_url).scheme
        if database_scheme not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
            raise ValueError("Staging and production require PostgreSQL")
        if not s.encryption_key or s.encryption_key.startswith("REPLACE_"):
            raise ValueError("Staging and production require a persistent APP_ENCRYPTION_KEY")
        if (
            not s.client_key
            or not s.client_secret
            or s.client_key.startswith("REPLACE_")
            or s.client_secret.startswith("REPLACE_")
        ):
            raise ValueError("Staging and production require TikTok client credentials")
        if any(_is_placeholder(value) for value in (s.legal_entity, s.legal_email, s.legal_address)):
            raise ValueError("Staging and production require reviewed legal operator details")
        email_domain = s.legal_email.rsplit("@", 1)[-1].lower() if "@" in s.legal_email else ""
        if not email_domain or email_domain.endswith((".test", ".example", ".invalid", ".localhost")):
            raise ValueError("Staging and production require a real legal contact email domain")
        if s.env == "production" and (
            len(s.metrics_bearer_token) < 32 or s.metrics_bearer_token.startswith("REPLACE_")
        ):
            raise ValueError("Production requires a private metrics bearer token of at least 32 characters")


def validate_tiktok_media_url_prefixes(s: Settings) -> None:
    """Validate URL prefixes configured as TikTok-verified media properties."""
    for prefix in s.tiktok_verified_media_url_prefixes:
        try:
            parsed = urlparse(prefix)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
            ipaddress.ip_address(host)
            is_ip_address = True
        except ValueError:
            try:
                parsed = urlparse(prefix)
                host = (parsed.hostname or "").lower().rstrip(".")
                port = parsed.port
            except ValueError as exc:
                raise ValueError("TIKTOK_VERIFIED_MEDIA_URL_PREFIXES contains an invalid URL") from exc
            is_ip_address = False
        if (
            not prefix
            or prefix != prefix.strip()
            or any(char.isspace() for char in prefix)
            or parsed.scheme != "https"
            or not host
            or "." not in host
            or is_ip_address
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or port not in (None, 443)
            or not parsed.path.endswith("/")
            or _has_unsafe_path_segments(parsed.path)
        ):
            raise ValueError(
                "TikTok verified media URL prefixes must be HTTPS domain prefixes ending in / without credentials, query, or fragment"
            )


def media_url_matches_verified_prefix(url: str, prefixes: tuple[str, ...]) -> bool:
    """Return whether a TikTok media URL is under a configured verified URL prefix."""
    try:
        media = urlparse(url)
        media_port = media.port or 443
    except ValueError:
        return False
    for prefix in prefixes:
        try:
            verified = urlparse(prefix)
            verified_port = verified.port or 443
        except ValueError:
            continue
        if (
            media.scheme == "https"
            and (media.hostname or "").lower().rstrip(".") == (verified.hostname or "").lower().rstrip(".")
            and media_port == verified_port
            and not _has_unsafe_path_segments(media.path)
            and media.path.startswith(verified.path)
        ):
            return True
    return False


def validate_zwallet_config(s: Settings) -> None:
    """Validate the optional server-to-server ZWallet adapter connection."""
    adapter_url = s.zwallet_adapter_url.strip()
    service_token = s.z_platform_service_token
    if not adapter_url:
        return
    if adapter_url != s.zwallet_adapter_url:
        raise ValueError("ZWALLET_ADAPTER_URL must not contain surrounding whitespace")
    if service_token and service_token != service_token.strip():
        raise ValueError("Z_PLATFORM_SERVICE_TOKEN must not contain surrounding whitespace")
    try:
        parsed = urlparse(adapter_url)
        host = parsed.hostname
        _ = parsed.port  # Validate malformed port values.
    except ValueError as exc:
        raise ValueError("ZWALLET_ADAPTER_URL must be a valid absolute URL") from exc
    if (
        parsed.scheme not in ("http", "https")
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ZWALLET_ADAPTER_URL must be an absolute HTTP(S) URL without credentials or query")
    if not service_token or service_token.startswith("REPLACE_"):
        return
    if len(service_token) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in service_token):
        raise ValueError("Z_PLATFORM_SERVICE_TOKEN must contain printable ASCII without spaces")
    if (
        s.env in {"staging", "production"}
        and parsed.scheme != "https"
        and host.lower()
        not in (
            "localhost",
            "127.0.0.1",
            "::1",
        )
    ):
        raise ValueError("Production ZWallet adapter URLs must use HTTPS")


def load_settings() -> Settings:
    get = os.environ.get
    s = Settings(
        env=get("APP_ENV", "development").strip().lower(),
        base_url=get("APP_BASE_URL", "http://localhost:8000").rstrip("/"),
        allowed_hosts=tuple(x.strip() for x in get("APP_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x.strip()),
        database_url=get("DATABASE_URL", "sqlite:///./data/zttato.db"),
        encryption_key=get("APP_ENCRYPTION_KEY", ""),
        client_key=get("TIKTOK_CLIENT_KEY", ""),
        client_secret=get("TIKTOK_CLIENT_SECRET", ""),
        scopes=tuple(
            x.strip()
            for x in get("TIKTOK_SCOPES", "user.info.basic,video.upload,video.publish").split(",")
            if x.strip()
        ),
        app_audited=get("TIKTOK_APP_AUDITED", "false").lower() == "true",
        legal_entity=get("LEGAL_ENTITY", ""),
        legal_email=get("LEGAL_CONTACT_EMAIL", ""),
        legal_address=get("LEGAL_POSTAL_ADDRESS", ""),
        media_dir=get("MEDIA_DIR", "./media"),
        max_video_bytes=int(get("MAX_VIDEO_BYTES", "67108864")),
        zwallet_adapter_url=get("ZWALLET_ADAPTER_URL", "").strip().rstrip("/"),
        z_platform_service_token=get("Z_PLATFORM_SERVICE_TOKEN", ""),
        max_requests_per_minute=int(get("MAX_REQUESTS_PER_MINUTE", "120")),
        max_uploads_per_day=int(get("MAX_UPLOADS_PER_DAY", "10")),
        max_upload_bytes_per_day=int(get("MAX_UPLOAD_BYTES_PER_DAY", str(256 * 1024 * 1024))),
        media_retention_days=int(get("MEDIA_RETENTION_DAYS", "30")),
        record_retention_days=int(get("RECORD_RETENTION_DAYS", "90")),
        cleanup_interval_seconds=int(get("CLEANUP_INTERVAL_SECONDS", "3600")),
        publish_worker_interval_seconds=int(get("PUBLISH_WORKER_INTERVAL_SECONDS", "2")),
        metrics_bearer_token=get("METRICS_BEARER_TOKEN", ""),
        tiktok_verified_media_url_prefixes=tuple(
            value.strip() for value in get("TIKTOK_VERIFIED_MEDIA_URL_PREFIXES", "").split(",") if value.strip()
        ),
    )
    validate_deployment_config(s)
    if not 1 <= s.max_requests_per_minute <= 10_000:
        raise ValueError("MAX_REQUESTS_PER_MINUTE must be between 1 and 10000")
    if not 1 <= s.max_uploads_per_day <= 10_000:
        raise ValueError("MAX_UPLOADS_PER_DAY must be between 1 and 10000")
    if not s.max_video_bytes <= s.max_upload_bytes_per_day <= 10 * 1024**3:
        raise ValueError("MAX_UPLOAD_BYTES_PER_DAY must be at least MAX_VIDEO_BYTES and at most 10 GiB")
    if not 1 <= s.media_retention_days <= 3650 or not 1 <= s.record_retention_days <= 3650:
        raise ValueError("Media and record retention must be between 1 and 3650 days")
    if not 60 <= s.cleanup_interval_seconds <= 86_400:
        raise ValueError("CLEANUP_INTERVAL_SECONDS must be between 60 and 86400")
    if len(s.metrics_bearer_token) > 512:
        raise ValueError("METRICS_BEARER_TOKEN must not exceed 512 characters")
    if s.max_video_bytes < 1024 or s.max_video_bytes > 67108864:
        raise ValueError("MAX_VIDEO_BYTES must be between 1 KiB and 64 MiB")
    return s
