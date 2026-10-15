#!/bin/bash
# One-time Google Cloud setup for the hosted dashboard (safe to re-run; existing resources are kept).
#
#   bucket   gs://<project>-live-sessions   private; the VM uploads live_sessions/ to it
#   images   Artifact Registry repository `midfreq` (asia-southeast1)
#   runtime  service account dashboard-runtime: may only READ that bucket
#
# The VM's uploader account (write access to the bucket only) is created at the cutover;
# see ops/README.md.
# Usage: ops/dashboard/setup_gcp.sh

set -euo pipefail
PROJECT=$(gcloud config get-value project 2>/dev/null)
REGION=asia-southeast1
BUCKET=${BUCKET:-$PROJECT-live-sessions}
RUNTIME=dashboard-runtime@$PROJECT.iam.gserviceaccount.com

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com iam.googleapis.com

gcloud artifacts repositories describe midfreq --location "$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create midfreq --location "$REGION" --repository-format docker \
       --description "midfreq images (the hosted dashboard)"

gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$BUCKET" --location "$REGION" \
       --uniform-bucket-level-access --public-access-prevention

gcloud iam service-accounts describe "$RUNTIME" >/dev/null 2>&1 \
  || gcloud iam service-accounts create dashboard-runtime --display-name "Dashboard (Cloud Run): reads the bot's record"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$RUNTIME" --role roles/storage.objectViewer >/dev/null

echo "project $PROJECT, bucket gs://$BUCKET, runtime $RUNTIME"
