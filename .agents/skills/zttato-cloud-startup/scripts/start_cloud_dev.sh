#!/usr/bin/env bash
set -Eeuo pipefail
cd /workspace/zttato-platform
[[ -x .venv/bin/uvicorn && -f data/cloud-dev.key && -f data/cloud-dev.db && -d media ]] || { echo 'Missing local state: run saved Codex install_script' >&2; exit 1; }
if .venv/bin/python -c 'import urllib.request,json; r=urllib.request.urlopen("http://127.0.0.1:8000/health/live",timeout=2); assert r.status==200 and json.load(r)["status"]=="ok"' 2>/dev/null; then
  echo 'Existing healthy service detected; reuse it'
  exit 0
fi
if .venv/bin/python -c 'import socket; s=socket.socket(); exit(0 if s.connect_ex(("127.0.0.1",8000))==0 else 1)'; then
  echo 'Port 8000 is occupied by an unhealthy service; refusing to replace it' >&2
  exit 1
fi
export APP_ENV=development APP_BASE_URL=http://localhost:8000 APP_ALLOWED_HOSTS=localhost,127.0.0.1
export DATABASE_URL=sqlite:///./data/cloud-dev.db MEDIA_DIR=./media
export APP_ENCRYPTION_KEY="$(cat data/cloud-dev.key)"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
