---
name: zttato-cloud-startup
description: Bootstrap, reuse/start, and verify the isolated zTTato Codex Cloud development checkout without modifying tracked application files.
---

# zTTato Codex Cloud Startup

Use the existing `/workspace/zttato-platform` checkout. Do not create a Git worktree unless explicitly requested. Explain developer-facing work in Thai.

## Preflight (mandatory)

1. Confirm the checkout location and read `AGENTS.md`, `docs/AI_MASTER_PRODUCTION_PROMPT.md`, `docs/ARCHITECTURE_BOUNDARIES.md`, `docs/SCOPE_AND_RESPONSIBILITY_MATRIX.md`, and `docs/PRODUCTION_GATES.md` before any changes. Obey more restrictive instructions.
2. Record `git status --short`. Setup must not modify tracked source, tests, requirements or lockfiles.
3. Verify `.venv/bin/python`, `data/cloud-dev.db`, `data/cloud-dev.key`, and `media/`; verify `.gitignore` excludes those local files. If state is missing, run the **saved Codex Cloud install_script** configured in the environment. Do not invent a replacement and do not overwrite `.env` or provider settings.
4. Never print, copy to logs or commit the development key. No TikTok/ZWallet credentials, production databases, registered redirect updates or real provider requests.

## Startup

Run `bash .agents/skills/zttato-cloud-startup/scripts/start_cloud_dev.sh` in a **managed background terminal**, keeping its process/session handle. The helper checks health before launching and refuses to take over an unhealthy occupied port. Reuse healthy existing processes, stop only the process started by this task. Live processes do not survive snapshot restoration. Never provide localhost URLs as user-facing preview links.

## Verification

Run `.venv/bin/python .agents/skills/zttato-cloud-startup/scripts/verify_cloud_dev.py` for HTTP: `/health/live` 200 status=ok; `/health/ready` 200 status=ready; public routes `/`, `/dashboard`, `/review-preview`, `/privacy-policy`, `/terms-of-service` 200 with security headers; `/api/session` disconnected, access token absent, session cookie issued once and reused.

Run `bash .agents/skills/zttato-cloud-startup/scripts/verify_cloud_dev.sh` to run HTTP plus Python tests, compile, Ruff lint/format, pip check, JS syntax/tests, SQLite Alembic head and Git status. Report each check's exit code and pre/post Git status. Never modify tracked files to make tests pass during setup.

## Scope

Prior onboarding reported 96 Python tests, 10 JavaScript tests and SQLite revision `20260927_04`; treat as historical, not current evidence. Starlette/HTTPX deprecation warnings can be informational. PostgreSQL, Docker, actual TikTok OAuth/posting, legal signoff and production readiness are separate evidence gates. Do not claim provider approval or production readiness from offline checks.
