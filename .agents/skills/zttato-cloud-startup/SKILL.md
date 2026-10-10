---
name: zttato-cloud-startup
description: Bootstrap, start, and verify the isolated zTTato Codex Cloud checkout without modifying tracked application files.
---

# zTTato Codex Cloud Startup

Use the existing `/workspace/zttato-platform` checkout. Do not create a Git worktree unless explicitly requested. Explain developer-facing work in Thai.

## Preflight (mandatory)

1. Read `AGENTS.md` and all four referenced contracts before changes.
2. Record `git status --short`; do not modify tracked source, tests, requirements or lockfiles during setup.
3. Check `.venv/bin/python`, `data/cloud-dev.db`, `data/cloud-dev.key`, `media/`. If missing, run the saved Codex Cloud `install_script`; do not overwrite existing `.env` or provider configuration.
4. Do not log, print or commit keys. No live TikTok/ZWallet credentials or operations, production database access, or redirect modification.

## Startup and lifecycle

Run `bash .agents/skills/zttato-cloud-startup/scripts/start_cloud_dev.sh` from a managed background terminal and retain the process/session handle. The helper refuses to start when port 8000 is already occupied, **even if the service is healthy**: liveness cannot establish which revision a process loaded. Reuse an existing process only through a managed session whose recorded revision is positively verified against the current checkout. Never stop an unowned service; stop only the service you started. Live processes do not survive snapshot restoration. Do not offer localhost preview URLs to users.

## Verification

Run `bash .agents/skills/zttato-cloud-startup/scripts/verify_cloud_dev.sh`. Checks cover HTTP liveness/readiness, public routes and security headers, disconnected cookie-jar session, Python tests on isolated in-memory SQLite, compilation, Ruff lint/format, pip dependency consistency, JavaScript syntax/tests, local SQLite Alembic head, and unchanged Git status. Report exact results; failed checks must not be hidden. No tracked source modifications during setup.

Prior onboarding reported 96 Python tests, 10 JavaScript tests and SQLite revision `20260927_04`; these are historical, not current evidence. A Starlette HTTPX deprecation warning may be informational. PostgreSQL, Docker, actual TikTok OAuth/posting, legal signoff and production release readiness are unverified separate gates.
