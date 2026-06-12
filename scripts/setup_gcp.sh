#!/usr/bin/env bash
# One-time GCP infrastructure provisioning for Ethiogram.
# Prereqs: gcloud CLI authenticated, billing enabled on the project.
#
# Usage:
#   PROJECT_ID=my-project REGION=us-central1 ./scripts/setup_gcp.sh

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SQL_INSTANCE="${SQL_INSTANCE:-ethiogram-pg}"
SQL_TIER="${SQL_TIER:-db-custom-1-3840}"   # 1 vCPU / 3.75 GB — scale up later
REDIS_INSTANCE="${REDIS_INSTANCE:-ethiogram-redis}"

gcloud config set project "$PROJECT_ID"

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  vpcaccess.googleapis.com

echo "==> Artifact Registry repo"
gcloud artifacts repositories create ethiogram \
  --repository-format=docker --location="$REGION" \
  --description="Ethiogram images" 2>/dev/null || echo "  (exists)"

echo "==> Cloud SQL (Postgres 16 + pgvector)"
gcloud sql instances create "$SQL_INSTANCE" \
  --database-version=POSTGRES_16 \
  --tier="$SQL_TIER" \
  --region="$REGION" \
  --storage-auto-increase \
  --backup-start-time=02:00 \
  --database-flags=cloudsql.iam_authentication=on 2>/dev/null || echo "  (exists)"

gcloud sql databases create ethiogram --instance="$SQL_INSTANCE" 2>/dev/null || echo "  (db exists)"

echo "  Set a password for the postgres user:"
echo "    gcloud sql users set-password postgres --instance=$SQL_INSTANCE --password=<STRONG_PASSWORD>"

echo "==> Memorystore Redis (basic, 1GB)"
gcloud redis instances create "$REDIS_INSTANCE" \
  --size=1 --region="$REGION" --redis-version=redis_7_0 2>/dev/null || echo "  (exists)"

echo "==> VPC connector (Cloud Run -> Memorystore private IP)"
gcloud compute networks vpc-access connectors create ethiogram-vpc \
  --region="$REGION" --range=10.8.0.0/28 2>/dev/null || echo "  (exists)"

echo "==> GCS buckets"
gcloud storage buckets create "gs://${PROJECT_ID}-ethiogram-uploads" \
  --location="$REGION" --uniform-bucket-level-access 2>/dev/null || echo "  (exists)"
gcloud storage buckets create "gs://${PROJECT_ID}-ethiogram-public" \
  --location="$REGION" --uniform-bucket-level-access 2>/dev/null || echo "  (exists)"

echo "==> Secrets (created empty — add versions next)"
for s in ethiogram-secret-key ethiogram-encryption-key ethiogram-database-url \
         ethiogram-redis-url ethiogram-master-bot-token ethiogram-openai-key \
         ethiogram-anthropic-key ethiogram-chapa-key ethiogram-webhook-salt \
         ethiogram-admin-header; do
  gcloud secrets create "$s" --replication-policy=automatic 2>/dev/null || echo "  ($s exists)"
done

echo ""
echo "==> NEXT STEPS (manual):"
cat <<EOF
1. Add secret values, e.g.:
     python -c "import secrets; print(secrets.token_hex(32))" | \\
       gcloud secrets versions add ethiogram-secret-key --data-file=-
     echo -n "postgresql+asyncpg://postgres:<PW>@/ethiogram?host=/cloudsql/${PROJECT_ID}:${REGION}:${SQL_INSTANCE}" | \\
       gcloud secrets versions add ethiogram-database-url --data-file=-
     REDIS_IP=\$(gcloud redis instances describe ${REDIS_INSTANCE} --region=${REGION} --format='value(host)')
     echo -n "redis://\${REDIS_IP}:6379/0" | \\
       gcloud secrets versions add ethiogram-redis-url --data-file=-

2. Enable pgvector + run migrations:
     gcloud sql connect ${SQL_INSTANCE} --user=postgres --database=ethiogram
     CREATE EXTENSION IF NOT EXISTS vector;
     \\q
     ./scripts/migrate_prod.sh

3. Grant Cloud Run service account access:
     PROJECT_NUMBER=\$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
     SA="\${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
     gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:\${SA}" --role=roles/cloudsql.client
     gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:\${SA}" --role=roles/secretmanager.secretAccessor
     gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:\${SA}" --role=roles/aiplatform.user
     gcloud projects add-iam-policy-binding ${PROJECT_ID} --member="serviceAccount:\${SA}" --role=roles/storage.objectAdmin

4. Deploy:
     ./scripts/deploy.sh
EOF
