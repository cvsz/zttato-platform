"""HTTP smoke checks for isolated zTTato Codex Cloud development."""
import json
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, build_opener

cookies = CookieJar()
client = build_opener(HTTPCookieProcessor(cookies))


def require(condition, message):
    if not condition:
        raise RuntimeError(f"Smoke verification failed: {message}")


def get(path):
    with client.open("http://127.0.0.1:8000" + path, timeout=8) as response:
        return response.status, response.headers, response.read()


def has_token_key(value):
    if isinstance(value, dict):
        return any(key.lower() == "access_token" or has_token_key(v) for key, v in value.items())
    if isinstance(value, list):
        return any(has_token_key(v) for v in value)
    return False


for path, status_value in (("/health/live", "ok"), ("/health/ready", "ready")):
    code, _, body = get(path)
    require(code == 200 and json.loads(body).get("status") == status_value, path)
    print("PASS", path)

for path in ("/", "/dashboard", "/review-preview", "/privacy-policy", "/terms-of-service"):
    code, headers, _ = get(path)
    require(code == 200, path + " status")
    require(bool(headers.get("X-Content-Type-Options") and headers.get("X-Frame-Options")), path + " headers")
    print("PASS", path)

code, _, body = get("/api/session")
payload = json.loads(body)
require(code == 200 and payload.get("connected") is False and not has_token_key(payload), "session payload")
sessions = [cookie for cookie in cookies if cookie.name == "zttato_session"]
require(len(sessions) == 1, "session cookie issuance")
original_value = sessions[0].value

code, headers, body = get("/api/session")
payload = json.loads(body)
require(code == 200 and payload.get("connected") is False and not has_token_key(payload), "session reuse payload")
require(not headers.get_all("Set-Cookie"), "unexpected cookie reissue")
require(any(cookie.name == "zttato_session" and cookie.value == original_value for cookie in cookies), "cookie reuse")
print("PASS /api/session")
