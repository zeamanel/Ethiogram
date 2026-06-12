#!/usr/bin/env bash
# Run SQL migrations against Cloud SQL via the Cloud SQL Auth Proxy.
#
# Prereqs: cloud-sql-proxy installed (https://cloud.google.com/sql/docs/postgres/sql-proxy)
#          PGPASSWORD exported or .pgpass configured.
#
# Usage:
#   PROJECT_ID=my-project PGPASSWORD=... ./scripts/migrate_prod.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SQL_INSTANCE_NAME="${SQL_INSTANCE_NAME:-ethiogram-pg}"
INSTANCE="${PROJECT_ID}:${REGION}:${SQL_INSTANCE_NAME}"
PORT="${PORT:-5433}"

echo "==> Starting Cloud SQL Auth Proxy on :${PORT}"
cloud-sql-proxy --port "$PORT" "$INSTANCE" &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null || true' EXIT
sleep 3

echo "==> Ensuring pgvector extension"
psql "host=127.0.0.1 port=$PORT user=postgres dbname=ethiogram" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "==> Running migrations"
psql "host=127.0.0.1 port=$PORT user=postgres dbname=ethiogram" \
  -v ON_ERROR_STOP=1 -f migrations/000_run_all.sql

echo "==> Done."
