#!/usr/bin/env bash
set -uo pipefail
cd /workspace/zttato-platform || exit 1
fail=0
baseline="$(mktemp)"
after="$(mktemp)"
trap 'rm -f "$baseline" "$after"' EXIT
git status --porcelain=v1 --untracked-files=all > "$baseline" || exit 1
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
check 'Python tests (isolated in-memory SQLite)' env APP_ENV=testing DATABASE_URL=sqlite:///:memory: .venv/bin/python -m pytest
check 'Compile' .venv/bin/python -m compileall -q app
check 'Lint' .venv/bin/ruff check app tests
check 'Format' .venv/bin/ruff format --check app tests
check 'Dependencies' .venv/bin/python -m pip check
check 'JS syntax' node --check web/dashboard.js
check 'JS tests' node --test tests/dashboard.test.mjs tests/i18n_frontend.test.mjs
check 'Alembic' env DATABASE_URL=sqlite:///./data/cloud-dev.db .venv/bin/alembic current --check-heads
git status --porcelain=v1 --untracked-files=all > "$after" || fail=1
if ! cmp -s "$baseline" "$after"; then
  printf 'FAIL: Git working-tree status changed during verification\n' >&2
  diff -u "$baseline" "$after" || true
  fail=1
else
  printf 'PASS: Git working-tree status unchanged\n'
fi
exit "$fail"
