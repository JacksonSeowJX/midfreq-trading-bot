#!/bin/bash
# Sign-in for the hosted dashboard: Google Identity Platform (Firebase Authentication) with the
# Google provider only, and the Cloud Run service switched to require it (safe to re-run).
#
#   1. the APIs: Identity Toolkit, Token Service, API Keys
#   2. Identity Platform initialised on the project
#   3. sign-in providers: Google on (with the OAuth client below); email/password, phone and
#      anonymous off
#   4. authorised domains: the Cloud Run hostnames added to the defaults
#   5. a browser API key restricted to sign-in (public by design: it names the project, it grants
#      nothing) and to the dashboard's own pages
#   6. the service's environment: MIDFREQ_AUTH=firebase, the project, the key and, when given,
#      the access list. The access list lives only there, never in git.
#
# Once, by hand, before the first run (the Google provider needs an OAuth client, which gcloud
# cannot create): Google Cloud console, project midfreq-trading-bot,
#   - Google Auth Platform > Branding: app name "midfreq dashboard", a support email;
#     Audience: External, and Publish app (In production; only the email and profile scopes are
#     used, so no verification is needed; the access list below is what admits people);
#   - Google Auth Platform > Clients > Create client > Web application, "midfreq dashboard sign-in",
#     Authorised redirect URI  https://<project>.firebaseapp.com/__/auth/handler
#   and pass its client ID and secret to this script (environment only; never commit them).
#
# Usage:
#   GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... \
#   ALLOWED_EMAILS=a@gmail.com,b@gmail.com ALLOWED_DOMAINS=ntu.edu.sg,e.ntu.edu.sg \
#   ops/dashboard/setup_auth.sh
#
# Later, to change only who may sign in (the other settings stay):
#   gcloud run services update midfreq-dashboard --region asia-southeast1 \
#     --update-env-vars '^;^MIDFREQ_AUTH_ALLOWED_EMAILS=a@gmail.com,b@gmail.com;MIDFREQ_AUTH_ALLOWED_DOMAINS=ntu.edu.sg'
#   (the ^;^ makes ; the separator, so the lists may hold commas; an empty list admits nobody)
#
# Requires: gcloud (logged in, project set, Owner or Identity Platform Admin + API Keys Admin +
# Cloud Run Admin), curl, python3. Run after ops/dashboard/setup_gcp.sh and a deploy.

set -euo pipefail
PROJECT=$(gcloud config get-value project 2>/dev/null)
REGION=asia-southeast1
SERVICE=midfreq-dashboard
KEY_NAME=midfreq-dashboard-auth
ADMIN=https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT
: "${GOOGLE_CLIENT_ID:?set GOOGLE_CLIENT_ID (the Web OAuth client, see the top of this script)}"
: "${GOOGLE_CLIENT_SECRET:?set GOOGLE_CLIENT_SECRET (the secret of the same client)}"
export GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET

TOKEN=$(gcloud auth print-access-token)
# A REST call to Identity Platform; prints the body, fails on an HTTP error. With user
# credentials the project must be named as the quota project.
api() {   # api METHOD URL [json body on stdin]
  local out code
  out=$(mktemp)
  code=$(curl -sS -o "$out" -w '%{http_code}' -X "$1" "$2" \
           -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT" \
           -H "Content-Type: application/json" ${3:+--data-binary @-})
  cat "$out"; rm -f "$out"
  [ "${code:0:1}" = 2 ] || { echo >&2; echo "HTTP $code from $1 $2" >&2; return 1; }
}

echo "== 1. APIs"
gcloud services enable identitytoolkit.googleapis.com securetoken.googleapis.com apikeys.googleapis.com

echo "== 2. Identity Platform"
if api GET "$ADMIN/config" 2>/dev/null | python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("subtype") != "IDENTITY_PLATFORM")'; then
  echo "already initialised"
else
  echo '{}' | api POST "https://identitytoolkit.googleapis.com/v2/projects/$PROJECT/identityPlatform:initializeAuth" body >/dev/null
  echo "initialised"
fi

echo "== 3. providers: Google only"
GOOGLE_BODY=$(python3 -c 'import json,os; print(json.dumps({"enabled": True, "clientId": os.environ["GOOGLE_CLIENT_ID"], "clientSecret": os.environ["GOOGLE_CLIENT_SECRET"]}))')
if api GET "$ADMIN/defaultSupportedIdpConfigs/google.com" >/dev/null 2>&1; then
  printf '%s' "$GOOGLE_BODY" | api PATCH "$ADMIN/defaultSupportedIdpConfigs/google.com?updateMask=enabled,clientId,clientSecret" body >/dev/null
else
  printf '%s' "$GOOGLE_BODY" | api POST "$ADMIN/defaultSupportedIdpConfigs?idpId=google.com" body >/dev/null
fi
unset GOOGLE_BODY
echo "Google on"

echo "== 4. authorised domains"
# both of the service's URLs (https://<service>-<project number>.<region>.run.app and the older
# https://<service>-<hash>-<region code>.a.run.app)
HOSTS=$(gcloud run services describe "$SERVICE" --region "$REGION" --format json | python3 -c '
import json, sys
from urllib.parse import urlparse
s = json.load(sys.stdin)
urls = [s.get("status", {}).get("url", "")]
urls += json.loads(s.get("metadata", {}).get("annotations", {}).get("run.googleapis.com/urls", "[]"))
print(" ".join(sorted({urlparse(u).hostname for u in urls if u})))')
[ -n "$HOSTS" ] || { echo "no URL for $SERVICE in $REGION: deploy it first (deploy_dashboard.sh)" >&2; exit 1; }
api GET "$ADMIN/config" | HOSTS="$HOSTS" python3 -c '
import json, os, sys
config = json.load(sys.stdin)
domains = list(config.get("authorizedDomains", []))
domains += [h for h in os.environ["HOSTS"].split() if h not in domains]
print(json.dumps({"signIn": {"email": {"enabled": False}, "phoneNumber": {"enabled": False},
                             "anonymous": {"enabled": False}},
                  "authorizedDomains": domains}))' \
  | api PATCH "$ADMIN/config?updateMask=signIn.email.enabled,signIn.phoneNumber.enabled,signIn.anonymous.enabled,authorizedDomains" body \
  | python3 -c 'import json,sys; print("authorised:", ", ".join(json.load(sys.stdin)["authorizedDomains"]))'

echo "== 5. browser API key"
REFERRERS="https://$PROJECT.firebaseapp.com/*"
for h in $HOSTS; do REFERRERS="$REFERRERS,https://$h/*"; done
KEY=$(gcloud services api-keys list --filter "displayName=$KEY_NAME" --format "value(name)" | head -1)
if [ -z "$KEY" ]; then
  gcloud services api-keys create --display-name "$KEY_NAME" \
    --api-target service=identitytoolkit.googleapis.com --api-target service=securetoken.googleapis.com \
    --allowed-referrers "$REFERRERS" >/dev/null
  KEY=$(gcloud services api-keys list --filter "displayName=$KEY_NAME" --format "value(name)" | head -1)
else
  gcloud services api-keys update "$KEY" --allowed-referrers "$REFERRERS" >/dev/null
fi
WEB_API_KEY=$(gcloud services api-keys get-key-string "$KEY" --format "value(keyString)")
echo "web API key: $WEB_API_KEY   (key $KEY; referrers $REFERRERS)"

echo "== 6. the service's environment"
VARS="MIDFREQ_AUTH=firebase;MIDFREQ_AUTH_PROJECT=$PROJECT;MIDFREQ_AUTH_WEB_API_KEY=$WEB_API_KEY"
[ -n "${ALLOWED_EMAILS:-}" ] && VARS="$VARS;MIDFREQ_AUTH_ALLOWED_EMAILS=$ALLOWED_EMAILS"
[ -n "${ALLOWED_DOMAINS:-}" ] && VARS="$VARS;MIDFREQ_AUTH_ALLOWED_DOMAINS=$ALLOWED_DOMAINS"
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "^;^$VARS" >/dev/null
gcloud run services describe "$SERVICE" --region "$REGION" --format json | python3 -c '
import json, sys
env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env", [])
for e in env:
    if e["name"].startswith("MIDFREQ_AUTH"):
        print("  %s=%s" % (e["name"], e.get("value", "")))'
[ -n "${ALLOWED_EMAILS:-}${ALLOWED_DOMAINS:-}" ] \
  || echo "note: no ALLOWED_EMAILS or ALLOWED_DOMAINS given, so the access list was left as it was (empty admits nobody)"
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format "value(status.url)")
echo "check: curl -s $URL/api/auth/config   (enabled: true)   and   curl -s -o /dev/null -w '%{http_code}\n' $URL/api/sessions   (401)"
