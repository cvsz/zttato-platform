SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# Environment files
ENV_FILE ?= .env

# Override variables for test execution to isolate from host environment
override DATABASE_URL := sqlite:///:memory:

.PHONY: help setup format lint test test-unit test-frontend test-migrations test-tiktok \
        build security ci up down restart logs status health ps clean \
        migrate migrate-head migrate-rollback migrate-status migrate-create \
        i18n i18n-check i18n-dry-run restore-drill

help: ## Show list of available Makefile targets with descriptions
	@echo "======================================================================"
	@echo "                     zTTato Platform Makefile                         "
	@echo "======================================================================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

## ── Development & Dependencies ──────────────────────────────────────────────
setup: ## Install development and testing dependencies
	python3 -m pip install -r requirements-dev.txt

clean: ## Clean Python cache files, test cache and temporary data
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	rm -rf .coverage htmlcov/

## ── Quality, Linting & Formatting ────────────────────────────────────────────
format: ## Format Python source files using ruff
	ruff format app tests scripts

lint: ## Check lint rules and formatting consistency
	ruff check app tests scripts
	ruff format --check app tests scripts

security: ## Audit the project dependency manifest for known vulnerabilities
	python3 -m pip_audit --strict --requirement requirements-dev.txt

## ── Testing ──────────────────────────────────────────────────────────────────
test: ## Run complete test suite (unit + frontend)
	DATABASE_URL=$(DATABASE_URL) python3 -m pytest
	node --test tests/dashboard.test.mjs tests/i18n_frontend.test.mjs

test-unit: ## Run Python backend tests with isolated SQLite database
	DATABASE_URL=$(DATABASE_URL) python3 -m pytest

test-frontend: ## Run frontend JavaScript unit tests (Node.js test runner)
	node --test tests/dashboard.test.mjs tests/i18n_frontend.test.mjs

test-migrations: ## Test schema migrations on temporary database
	python3 -m pytest tests/test_migrations.py

test-tiktok: ## Run TikTok API and media transfer regression tests
	python3 -m pytest tests/test_tiktok_transfer.py

## ── CI / CD Pipeline ─────────────────────────────────────────────────────────
ci: lint test security build ## Run full CI pipeline (lint, test, security, build)

## ── Database & Migrations ───────────────────────────────────────────────────
migrate: migrate-head ## Alias for migrate-head

migrate-head: ## Upgrade database to latest migration head via docker compose
	docker compose run --rm migrate

migrate-status: ## Show current database revision
	docker compose exec -T app alembic current

migrate-rollback: ## Downgrade database schema by 1 revision
	docker compose exec -T app alembic downgrade -1

migrate-create: ## Create a new migration revision (Usage: make migrate-create msg="add_foo")
	@test -n "$(msg)" || (echo "Usage: make migrate-create msg=\"description\"" && exit 1)
	docker compose exec -T app alembic revision -m "$(msg)"

restore-drill: ## Execute isolated PostgreSQL backup and restore drill rehearsal
	bash scripts/restore_drill.sh

## ── Internationalization (i18n) ──────────────────────────────────────────────
i18n: ## Auto-translate missing keys in all locales using AI pipeline
	python3 scripts/translate_i18n.py

i18n-dry-run: ## Preview missing i18n keys without writing files
	python3 scripts/translate_i18n.py --dry-run

i18n-check: ## Verify 100% key parity and integrity across all supported locales
	python3 -m pytest tests/test_i18n.py

## ── Docker & Container Operations ───────────────────────────────────────────
build: ## Compile Python code and build local container images
	python3 -m compileall -q app
	docker compose build

up: ## Start all services in background (PostgreSQL, migrations, app)
	docker compose up -d

down: ## Stop all services
	docker compose down

restart: ## Rebuild and restart all containers
	docker compose down
	docker compose build
	docker compose up -d

logs: ## Tail real-time logs from application container
	docker compose logs -f app

status: ps ## Alias for ps

ps: ## Display container status and ports
	docker compose ps

health: ## Check live and ready HTTP health endpoints of the running app
	@echo "Checking /health/live..."
	@curl -s -o /dev/null -w "Live: %{http_code}\n" -H "Host: zttato.zeaz.dev" http://127.0.0.1:8000/health/live || echo "Live check failed"
	@echo "Checking /health/ready..."
	@curl -s -o /dev/null -w "Ready: %{http_code}\n" -H "Host: zttato.zeaz.dev" http://127.0.0.1:8000/health/ready || echo "Ready check failed"
