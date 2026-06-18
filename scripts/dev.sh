#!/usr/bin/env bash
# Local dev runner — no Cloud deploys needed.
#
# Talks to the prod DB through the Cloud SQL Auth Proxy (start it in a SEPARATE
# terminal first), uses in-memory fakeredis, and routes all AI through OpenRouter.
#
#   Terminal 1:  ./cloud-sql-proxy.exe odaflux-api:us-central1:ethiogram-pg --port 5433
#   Terminal 2:  source venv/Scripts/activate && ./scripts/dev.sh
#
# Then drive it with simulated Telegram updates (no Telegram needed):
#   bash scripts/send_test.sh "who is nikola tesla"
set -euo pipefail

export ENVIRONMENT=development
export USE_FAKE_REDIS=1

# Dev-only admin secret so local admin endpoints don't 403. The gate now fails
# closed when unset (see verify_admin_secret_header). NEVER use this value in
# prod — set a real ADMIN_SECRET_VALUE there. Send it as the X-Ethiogram-Admin
# header on local admin calls. Override by exporting ADMIN_SECRET_VALUE first.
export ADMIN_SECRET_VALUE="${ADMIN_SECRET_VALUE:-dev-admin-secret-local-only}"

# AI via OpenRouter (same as prod)
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export DEFAULT_MODEL_ID=openai/gpt-4o-mini
export FALLBACK_MODEL_ID=anthropic/claude-3.5-haiku
export EMERGENCY_MODEL_ID=meta-llama/llama-3.1-8b-instruct

# Secrets pulled from Secret Manager (matches prod so token decryption works)
export ENCRYPTION_KEY="$(gcloud secrets versions access latest --secret=ethiogram-encryption-key)"
export SECRET_KEY="$(gcloud secrets versions access latest --secret=ethiogram-secret-key)"
export OPENAI_API_KEY="$(gcloud secrets versions access latest --secret=ethiogram-openai-key)"

# DB via the proxy on 127.0.0.1:5433 (reuse the prod password)
DB_PASS="$(gcloud secrets versions access latest --secret=ethiogram-database-url | sed -E 's#.*://[^:]+:([^@]+)@.*#\1#')"
export DATABASE_URL="postgresql+asyncpg://postgres:${DB_PASS}@127.0.0.1:5433/ethiogram"

# Longer connect timeout for local sends over a VPN (override as needed)
export TELEGRAM_CONNECT_TIMEOUT="${TELEGRAM_CONNECT_TIMEOUT:-20}"
export TELEGRAM_READ_TIMEOUT="${TELEGRAM_READ_TIMEOUT:-20}"

echo "[dev] DATABASE_URL=postgresql+asyncpg://postgres:***@127.0.0.1:5433/ethiogram"
echo "[dev] USE_FAKE_REDIS=1  OPENAI_BASE_URL=${OPENAI_BASE_URL}  DEFAULT_MODEL_ID=${DEFAULT_MODEL_ID}"
echo "[dev] admin header -> X-Ethiogram-Admin: ${ADMIN_SECRET_VALUE}"
exec uvicorn app.main:app --reload --port 8000
