import os
from dataclasses import dataclass
from urllib.parse import urlparse


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
    if s.env == "production" and parsed.scheme != "https" and host.lower() not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Production ZWallet adapter URLs must use HTTPS")


def load_settings() -> Settings:
    get = os.environ.get
    s = Settings(
        env=get("APP_ENV", "development").lower(),
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
    )
    validate_host_config(s)
    validate_zwallet_config(s)
    u = urlparse(s.base_url)
    if s.env == "production":
        if u.scheme != "https":
            raise ValueError("Production APP_BASE_URL must use HTTPS")
        if "sqlite" in s.database_url or not s.encryption_key or s.encryption_key.startswith("REPLACE_"):
            raise ValueError("Production requires PostgreSQL and a persistent APP_ENCRYPTION_KEY")
        required = [s.client_key, s.client_secret, s.legal_entity, s.legal_email, s.legal_address]
        if any(not x or x.startswith("REPLACE_") for x in required):
            raise ValueError("Production TikTok and legal operator fields must be configured")
    if s.max_video_bytes < 1024 or s.max_video_bytes > 67108864:
        raise ValueError("MAX_VIDEO_BYTES must be between 1 KiB and 64 MiB")
    return s
