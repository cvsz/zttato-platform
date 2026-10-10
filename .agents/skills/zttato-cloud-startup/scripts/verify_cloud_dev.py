"""HTTP smoke checks for isolated zTTato Codex Cloud development."""
import json
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, build_opener

cookies = CookieJar()
client = build_opener(HTTPCookieProcessor(cookies))


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
    assert code == 200 and json.loads(body).get("status") == status_value, path
    print("PASS", path)

for path in ("/", "/dashboard", "/review-preview", "/privacy-policy", "/terms-of-service"):
    code, headers, _ = get(path)
    assert code == 200, path
    assert headers.get("X-Content-Type-Options") and headers.get("X-Frame-Options"), path
    print("PASS", path)

code, headers, body = get("/api/session")
payload = json.loads(body)
assert code == 200 and payload.get("connected") is False and not has_token_key(payload)
sessions = [cookie for cookie in cookies if cookie.name == "zttato_session"]
assert len(sessions) == 1
original_value = sessions[0].value

code, headers, body = get("/api/session")
payload = json.loads(body)
assert code == 200 and payload.get("connected") is False and not has_token_key(payload)
assert not headers.get_all("Set-Cookie")
assert any(cookie.name == "zttato_session" and cookie.value == original_value for cookie in cookies)
print("PASS /api/session")
