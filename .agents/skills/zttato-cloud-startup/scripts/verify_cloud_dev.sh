#!/usr/bin/env bash
set -uo pipefail
cd /workspace/zttato-platform || exit 1
fail=0
check() {
  local name="$1" rc
  shift
  printf '\n==> %s\n' "$name"
  "$@"
  rc=$?
  printf '%s exit=%d\n' "$name" "$rc"
  if (( rc != 0 )); then fail=1; fi
}
check 'HTTP' .venv/bin/python .agents/skills/zttato-cloud-startup/scripts/verify_cloud_dev.py
check 'Python tests' .venv/bin/python -m pytest
check 'Compile' .venv/bin/python -m compileall -q app
check 'Lint' .venv/bin/ruff check app tests
check 'Format' .venv/bin/ruff format --check app tests
check 'Dependencies' .venv/bin/python -m pip check
check 'JS syntax' node --check web/dashboard.js
check 'JS tests' node --test tests/dashboard.test.mjs tests/i18n_frontend.test.mjs
check 'Alembic' env DATABASE_URL=sqlite:///./data/cloud-dev.db .venv/bin/alembic current --check-heads
check 'Git status' git status --short
exit "$fail"
