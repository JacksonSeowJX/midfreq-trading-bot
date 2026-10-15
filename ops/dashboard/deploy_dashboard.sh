#!/bin/bash
# Build the dashboard image from COMMITTED code (git archive HEAD, so local edits never reach
# the public site) with Cloud Build, and deploy it to Cloud Run as a public, read-only service.
#
# Usage: ops/dashboard/deploy_dashboard.sh        (after ops/dashboard/setup_gcp.sh, once)
# Requires: gcloud, logged in to the project.

set -euo pipefail
cd "$(dirname "$0")/../.."
PROJECT=$(gcloud config get-value project 2>/dev/null)
REGION=asia-southeast1
SERVICE=midfreq-dashboard
BUCKET=${BUCKET:-$PROJECT-live-sessions}
TAG=$(git rev-parse --short HEAD)
IMAGE=$REGION-docker.pkg.dev/$PROJECT/midfreq/dashboard:$TAG

[ -z "$(git status --porcelain -- apps packages ops/dashboard pyproject.toml uv.lock)" ] \
  || echo "note: uncommitted changes are NOT deployed; this builds $TAG as committed" >&2

SRC=$(mktemp -d); trap 'rm -rf "$SRC"' EXIT
git archive HEAD pyproject.toml uv.lock packages apps/api apps/web config results ops/dashboard | tar -x -C "$SRC"
gcloud builds submit "$SRC" --region "$REGION" --config ops/dashboard/cloudbuild.yaml \
    --substitutions "_IMAGE=$IMAGE"

gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" \
    --service-account "dashboard-runtime@$PROJECT.iam.gserviceaccount.com" \
    --allow-unauthenticated --port 8080 --memory 1Gi --cpu 1 --min-instances 0 --max-instances 2 \
    --set-env-vars "MIDFREQ_LIVE_SESSIONS=gs://$BUCKET/live_sessions"
gcloud run services describe "$SERVICE" --region "$REGION" --format "value(status.url)"
