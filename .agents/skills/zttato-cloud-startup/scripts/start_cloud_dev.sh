#!/usr/bin/env bash
set -Eeuo pipefail
cd /workspace/zttato-platform
[[ -x .venv/bin/uvicorn && -f data/cloud-dev.key && -f data/cloud-dev.db && -d media ]] || { echo 'Missing local state: run saved Codex install_script' >&2; exit 1; }
# Fail closed: a healthy listener alone does not prove the running code matches this checkout.
# The agent may reuse its own managed session only after verifying its recorded revision.
if .venv/bin/python -c 'import socket; s=socket.socket(); s.settimeout(2); exit(0 if s.connect_ex(("127.0.0.1",8000))==0 else 1)'; then
  echo 'Port 8000 occupied; verify owning managed session and matching revision manually. Refusing to reuse or stop it.' >&2
  exit 1
fi
export APP_ENV=development APP_BASE_URL=http://localhost:8000 APP_ALLOWED_HOSTS=localhost,127.0.0.1
export DATABASE_URL=sqlite:///./data/cloud-dev.db MEDIA_DIR=./media
export APP_ENCRYPTION_KEY="$(cat data/cloud-dev.key)"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
