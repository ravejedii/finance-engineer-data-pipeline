#!/usr/bin/env bash
# One-time GCP setup for keyless CI: GitHub Actions -> Workload Identity
# Federation -> a least-privilege service account -> BigQuery.
#
# Run in Cloud Shell (it is already authenticated as you):
#   bash setup_gcp_wif.sh
#
# Safe to re-run: every step checks whether its resource already exists.
# No service-account key is created at any point.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-finance-engineer-data-pipeline}"
GITHUB_REPO="${GITHUB_REPO:-ravejedii/finance-engineer-data-pipeline}"
SA_NAME="kiln-ci"
POOL_ID="github"
PROVIDER_ID="github-repo"

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "==> Enabling APIs"
gcloud services enable \
  bigquery.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  --project "$PROJECT_ID"

echo "==> Service account ${SA_EMAIL}"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT_ID" \
    --display-name "Kiln CI (dbt on BigQuery)"
fi

echo "==> BigQuery roles for the service account (project level)"
for role in roles/bigquery.dataEditor roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:${SA_EMAIL}" --role "$role" --condition=None >/dev/null
done

echo "==> Workload identity pool '${POOL_ID}'"
if ! gcloud iam workload-identity-pools describe "$POOL_ID" \
    --project "$PROJECT_ID" --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL_ID" \
    --project "$PROJECT_ID" --location global --display-name "GitHub Actions"
fi

echo "==> OIDC provider '${PROVIDER_ID}' (only accepts tokens from ${GITHUB_REPO})"
if ! gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
    --project "$PROJECT_ID" --location global --workload-identity-pool "$POOL_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --project "$PROJECT_ID" --location global --workload-identity-pool "$POOL_ID" \
    --display-name "GitHub repo" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository == '${GITHUB_REPO}'"
fi

echo "==> Allow workflows from ${GITHUB_REPO} to act as the service account"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --project "$PROJECT_ID" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}" \
  >/dev/null

cat <<OUT

Done. Paste these three lines back to Claude (none of them are secrets):

GCP_PROJECT_ID=${PROJECT_ID}
GCP_WIF_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}
GCP_SERVICE_ACCOUNT=${SA_EMAIL}
OUT
