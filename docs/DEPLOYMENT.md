# Ethiogram — Google Cloud Deployment

Target architecture: **Cloud Run** (API + 2 workers, one shared image),
**Cloud SQL Postgres 16** (pgvector), **Memorystore Redis**, **GCS**,
**Secret Manager**, **Vertex AI**. Estimated baseline cost ≈ $70–120/mo at
low traffic (dominated by Cloud SQL + Memorystore + min-instances).

---

## Step 0 — Prerequisites

- GCP project with billing enabled
- `gcloud` CLI ≥ 470 authenticated (`gcloud auth login`)
- `psql` and `cloud-sql-proxy` installed locally (for migrations)
- Master bot token from @BotFather

```bash
export PROJECT_ID=your-project-id
export REGION=us-central1
```

## Step 1 — Provision infrastructure (one-time)

```bash
./scripts/setup_gcp.sh
```

This enables APIs and creates: Artifact Registry repo, Cloud SQL instance
(`ethiogram-pg`, Postgres 16), the `ethiogram` database, Memorystore Redis,
a VPC connector (Cloud Run → Redis private IP), two GCS buckets, and ten
empty Secret Manager secrets.

Then set the Postgres password:

```bash
gcloud sql users set-password postgres --instance=ethiogram-pg --password='<STRONG_PASSWORD>'
```

## Step 2 — Populate secrets

```bash
# Random keys
python -c "import secrets; print(secrets.token_hex(32))" | gcloud secrets versions add ethiogram-secret-key --data-file=-
python -c "import secrets; print(secrets.token_hex(32))" | gcloud secrets versions add ethiogram-encryption-key --data-file=-
python -c "import secrets; print(secrets.token_hex(16))" | gcloud secrets versions add ethiogram-webhook-salt --data-file=-
python -c "import secrets; print(secrets.token_hex(16))" | gcloud secrets versions add ethiogram-admin-header --data-file=-

# Database URL (Cloud SQL unix socket form)
echo -n "postgresql+asyncpg://postgres:<PW>@/ethiogram?host=/cloudsql/${PROJECT_ID}:${REGION}:ethiogram-pg" \
  | gcloud secrets versions add ethiogram-database-url --data-file=-

# Redis URL (private IP via VPC connector)
REDIS_IP=$(gcloud redis instances describe ethiogram-redis --region=$REGION --format='value(host)')
echo -n "redis://${REDIS_IP}:6379/0" | gcloud secrets versions add ethiogram-redis-url --data-file=-

# Provider keys
echo -n "<botfather-token>"  | gcloud secrets versions add ethiogram-master-bot-token --data-file=-
echo -n "sk-..."             | gcloud secrets versions add ethiogram-openai-key --data-file=-
echo -n "sk-ant-..."         | gcloud secrets versions add ethiogram-anthropic-key --data-file=-
echo -n "CHASECK_..."        | gcloud secrets versions add ethiogram-chapa-key --data-file=-
```

> ⚠️ `ENCRYPTION_KEY` derives the Fernet key for bot tokens, payment keys, and
> agent prompts. **Losing or rotating it makes all encrypted rows
> unrecoverable.** Back it up out-of-band.

## Step 3 — IAM for the Cloud Run service account

```bash
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for role in roles/cloudsql.client roles/secretmanager.secretAccessor \
            roles/aiplatform.user roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SA}" --role="$role"
done
```

## Step 4 — Database migrations

```bash
PGPASSWORD='<PW>' ./scripts/migrate_prod.sh
```

Starts the Cloud SQL Auth Proxy, enables pgvector, runs
`migrations/000_run_all.sql` (idempotent — safe to re-run on every release
that adds a migration).

## Step 5 — Deploy

```bash
./scripts/deploy.sh
```

Cloud Build builds the production image once and deploys three Cloud Run
services from it:

| Service | Command | Scaling |
|---|---|---|
| `ethiogram-api` | uvicorn | 1–20 instances, public |
| `ethiogram-embedding-worker` | `workers.embedding_worker continuous` | 1–2, private, no CPU throttling |
| `ethiogram-notification-worker` | `workers.notification_worker continuous` | 1–2, private, no CPU throttling |

The script prints the API URL and curls `/health`.

> Note: the workers and API attach the VPC connector for Redis. If deploy
> fails on Redis connectivity, add `--vpc-connector=ethiogram-vpc
> --vpc-egress=private-ranges-only` to the three deploy steps in
> `cloudbuild.yaml`.

## Step 6 — Scheduled jobs (trial monitor, escrow release)

These run periodically, not continuously — use Cloud Run **Jobs** +
Cloud Scheduler:

```bash
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/ethiogram/ethiogram-api:latest"

for job in trial_monitor escrow_release; do
  name=$(echo $job | tr _ -)
  gcloud run jobs create "ethiogram-${name}" --image=$IMAGE --region=$REGION \
    --command=python --args=-m,workers.${job},once \
    --set-cloudsql-instances=${PROJECT_ID}:${REGION}:ethiogram-pg \
    --set-env-vars=ENVIRONMENT=production \
    --set-secrets=SECRET_KEY=ethiogram-secret-key:latest,ENCRYPTION_KEY=ethiogram-encryption-key:latest,DATABASE_URL=ethiogram-database-url:latest,REDIS_URL=ethiogram-redis-url:latest,MASTER_BOT_TOKEN=ethiogram-master-bot-token:latest

  gcloud scheduler jobs create http "run-ethiogram-${name}" \
    --schedule="0 */6 * * *" --location=$REGION \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/ethiogram-${name}:run" \
    --oauth-service-account-email="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
done
```

## Step 7 — Domain + webhooks

```bash
gcloud beta run domain-mappings create --service=ethiogram-api \
  --domain=api.ethiogram.com --region=$REGION
```

Add the DNS records it prints. Then set `BASE_URL=https://api.ethiogram.com`
on the API service (env var) and refresh every bot's webhook:
`POST /api/v1/bots/{bot_id}/refresh-webhook`. Telegram requires HTTPS with a
valid cert — Cloud Run domains satisfy this automatically.

## Step 8 — Post-deploy smoke test

```bash
API_URL=$(gcloud run services describe ethiogram-api --region=$REGION --format='value(status.url)')
curl -fsS $API_URL/health                          # {"status":"healthy"}
curl -s -o /dev/null -w "%{http_code}" $API_URL/docs   # 404 in production (docs disabled)
```

Then run Phase 4 of `docs/TESTING.md` (real Telegram flow) against production.

---

## Continuous deployment

`.github/workflows/ci.yml` deploys on merge to `main` using Workload Identity
Federation. Configure once:

1. Create a WIF pool/provider and a deploy service account with
   `roles/cloudbuild.builds.editor` + `roles/viewer`
   ([guide](https://github.com/google-github-actions/auth#setup)).
2. Repo secrets: `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`; repo variable
   `GCP_REGION`.
3. In the Cloud Build trigger settings, set the `_SQL_INSTANCE` substitution
   to `PROJECT:REGION:ethiogram-pg`.

## Rollback

```bash
gcloud run revisions list --service=ethiogram-api --region=$REGION
gcloud run services update-traffic ethiogram-api --region=$REGION \
  --to-revisions=<GOOD_REVISION>=100
```

Migrations are additive-only; never roll back the database — fix forward.
