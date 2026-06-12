#!/usr/bin/env bash
# Deploy Ethiogram to Cloud Run via Cloud Build.
#
# Usage:
#   PROJECT_ID=my-project ./scripts/deploy.sh
#   PROJECT_ID=my-project REGION=europe-west1 ./scripts/deploy.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SQL_INSTANCE_NAME="${SQL_INSTANCE_NAME:-ethiogram-pg}"
SQL_INSTANCE="${PROJECT_ID}:${REGION}:${SQL_INSTANCE_NAME}"

gcloud config set project "$PROJECT_ID"

echo "==> Submitting Cloud Build (region=$REGION, sql=$SQL_INSTANCE)"
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions "_REGION=${REGION},_SQL_INSTANCE=${SQL_INSTANCE}"

API_URL=$(gcloud run services describe ethiogram-api \
  --region="$REGION" --format='value(status.url)')

echo ""
echo "==> Deployed: $API_URL"
echo "==> Health check:"
curl -fsS "${API_URL}/health" && echo ""

echo ""
echo "Remember: bot webhooks must point at ${API_URL}/webhook/{token_hash}."
echo "If BASE_URL changed, re-run webhook refresh for all bots:"
echo "  POST ${API_URL}/api/v1/bots/{bot_id}/refresh-webhook"
