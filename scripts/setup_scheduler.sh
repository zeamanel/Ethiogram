#!/usr/bin/env bash
# One-time Cloud Scheduler setup for the worker Cloud Run Jobs.
#
# cloudbuild.yaml deploys the JOBS (run-to-completion). This script wires up the
# cron triggers that actually invoke them. Run it ONCE per project (re-running is
# safe-ish, but `scheduler jobs create` errors if the trigger already exists —
# use the `update` variant shown at the bottom to change a schedule later).
#
# Prereqs: gcloud auth, and the APIs below enabled.
set -euo pipefail

PROJECT_ID="$(gcloud config get-value project)"
REGION="us-central1"
TZ="Africa/Addis_Ababa"
INVOKER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

# ── 0. Enable the APIs (no-op if already enabled) ───────────────────────────
gcloud services enable cloudscheduler.googleapis.com run.googleapis.com \
  --project "${PROJECT_ID}"

# ── 1. Service account Cloud Scheduler uses to invoke the jobs ──────────────
gcloud iam service-accounts create scheduler-invoker \
  --display-name="Cloud Scheduler -> Cloud Run Jobs invoker" \
  --project "${PROJECT_ID}" 2>/dev/null || echo "SA already exists, continuing"

# Cloud Run Invoker grants run.jobs.run (needed to execute a Job).
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INVOKER_SA}" \
  --role="roles/run.invoker"

# ── 2. Helper: create one scheduler trigger for a Cloud Run Job ─────────────
# Usage: make_trigger <scheduler-job-name> <cloud-run-job-name> "<cron>"
make_trigger () {
  local name="$1" job="$2" cron="$3"
  local uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${job}:run"
  gcloud scheduler jobs create http "${name}" \
    --project "${PROJECT_ID}" \
    --location "${REGION}" \
    --schedule "${cron}" \
    --time-zone "${TZ}" \
    --uri "${uri}" \
    --http-method POST \
    --oauth-service-account-email "${INVOKER_SA}" \
    --attempt-deadline 600s
}

# ── 3. Create the triggers ──────────────────────────────────────────────────
# Polling workers -> every minute. Daily sweeps -> once a day (staggered).
make_trigger ethiogram-embedding-worker-trigger  ethiogram-embedding-worker  "* * * * *"
make_trigger ethiogram-notification-worker-trigger ethiogram-notification-worker "* * * * *"
make_trigger ethiogram-trial-monitor-trigger     ethiogram-trial-monitor     "0 6 * * *"
make_trigger ethiogram-escrow-release-trigger    ethiogram-escrow-release    "0 7 * * *"

echo "Scheduler triggers created."
echo
echo "To change a schedule later, use update instead of create, e.g.:"
echo "  gcloud scheduler jobs update http ethiogram-embedding-worker-trigger \\"
echo "    --location ${REGION} --schedule '*/2 * * * *'"
echo
echo "To run a job immediately for testing:"
echo "  gcloud run jobs execute ethiogram-embedding-worker --region ${REGION}"
