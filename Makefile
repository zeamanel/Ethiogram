.PHONY: help dev stop logs shell migrate test lint fmt typecheck clean

# ── Variables ─────────────────────────────────────────────────────────────────
COMPOSE  = docker compose
API      = $(COMPOSE) exec api
PSQL     = $(COMPOSE) exec postgres psql -U postgres ethiogram

help:          ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Docker ────────────────────────────────────────────────────────────────────
dev:           ## Start all services in dev mode
	$(COMPOSE) up --build -d

stop:          ## Stop all services
	$(COMPOSE) down

restart:       ## Restart API container only
	$(COMPOSE) restart api

logs:          ## Tail logs (API + workers)
	$(COMPOSE) logs -f api embedding-worker notification-worker

logs-api:      ## Tail API logs only
	$(COMPOSE) logs -f api

shell:         ## Open a shell in the API container
	$(API) bash

# ── Database ──────────────────────────────────────────────────────────────────
migrate:       ## Run all SQL migrations against the local Postgres container
	$(PSQL) -f migrations/000_run_all.sql

migrate-fresh: ## Drop and recreate database, then run migrations
	$(COMPOSE) exec postgres psql -U postgres -c "DROP DATABASE IF EXISTS ethiogram; CREATE DATABASE ethiogram;"
	$(PSQL) -c "CREATE EXTENSION IF NOT EXISTS vector;"
	$(PSQL) -f migrations/000_run_all.sql

psql:          ## Open interactive psql session
	$(PSQL)

# ── Testing ───────────────────────────────────────────────────────────────────
test:          ## Run test suite
	$(API) pytest tests/ -v --tb=short -q

test-cov:      ## Run tests with coverage report
	$(API) pytest tests/ --cov=app --cov-report=term-missing --cov-report=html -q

test-unit:     ## Run only unit tests (no async DB)
	$(API) pytest tests/test_utils.py tests/test_security.py -v

# ── Code quality ──────────────────────────────────────────────────────────────
lint:          ## Run ruff linter
	$(API) ruff check app/ workers/ tests/

fmt:           ## Format with ruff
	$(API) ruff format app/ workers/ tests/

typecheck:     ## Run mypy type checker
	$(API) mypy app/ --ignore-missing-imports --no-strict-optional

# ── Workers (local, no Docker) ────────────────────────────────────────────────
run-embedding: ## Run embedding worker once (local)
	python -m workers.embedding_worker once

run-trials:    ## Run trial monitor once (local)
	python -m workers.trial_monitor once

run-escrow:    ## Run escrow release once (local)
	python -m workers.escrow_release once

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:         ## Remove __pycache__ and .pyc files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
