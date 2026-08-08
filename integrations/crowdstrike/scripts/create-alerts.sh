#!/bin/bash
# CrowdStrike Falcon LogScale — Create FSx for ONTAP Security Alerts
#
# Creates 3 alerts:
#   1. Sensitive Path Access        (filter alert    — fires per matching event)
#   2. Mass File Deletion           (aggregate alert — >100 deletes in 5min per user)
#   3. Failed Access Spike          (aggregate alert — >50 failures in 5min)
#
# WHY NO RANSOMWARE/ARP ALERT HERE:
#   The other vendor integrations open with an ARP ransomware alert keyed on the
#   EMS event name. That detection needs the EMS webhook pipeline, and this
#   integration ships `template.yaml` only — no template-ems.yaml, no
#   template-fpolicy.yaml (compare integrations/honeycomb/, which has both). An
#   ARP alert created here would sit at zero forever because no EMS event ever
#   reaches LogScale. The three alerts below are all derivable from file access
#   audit logs, which is what this pipeline actually delivers. See
#   docs/en/detection-use-cases.md § Event Source Selection Matrix for which
#   detections need which source.
#
# TOKEN REQUIREMENT — this is NOT the ingest token:
#   The Lambda ships logs with a LogScale *ingest* token, which can only write
#   events. Creating alerts and actions goes through the GraphQL management API
#   and needs a separate token with alert/action management permissions on the
#   repository. Passing the ingest token here returns an authorization error.
#   https://library.humio.com/falcon-logscale-cloud/security-apitokens.html
#
# WHY GraphQL AND NOT THE REST ALERT API:
#   The REST API for actions was removed in LogScale 1.81 and replaced by
#   GraphQL, and current LogScale alert types (filter / aggregate / scheduled
#   search) are managed through GraphQL as well. The older
#   POST /api/v1/repositories/{repo}/alerts endpoint only creates legacy alerts.
#   https://library.humio.com/logscale-graphql-reference/gql-mutations.html
#
# Threshold customization:
#   DELETE_THRESHOLD / FAILURE_THRESHOLD / SENSITIVE_PATH_REGEX below.
#   Detection rationale: docs/en/detection-use-cases.md
#
# Prerequisites:
#   - LogScale management API token (see TOKEN REQUIREMENT above)
#   - Logs flowing into the repository from integrations/crowdstrike/lambda/handler.py
#   - An action to fire. Either set LOGSCALE_ALERT_EMAIL to have this script
#     create an email action, or set LOGSCALE_ACTION_NAME to an existing one.
#
# Usage:
#   export LOGSCALE_URL="https://cloud.us.humio.com"
#   export LOGSCALE_API_TOKEN="<management-token>"
#   export LOGSCALE_REPOSITORY="fsxn-audit"
#   export LOGSCALE_ALERT_EMAIL="soc@example.com"   # or LOGSCALE_ACTION_NAME
#   bash integrations/crowdstrike/scripts/create-alerts.sh

set -euo pipefail

LOGSCALE_URL="${LOGSCALE_URL:-https://cloud.us.humio.com}"
LOGSCALE_API_TOKEN="${LOGSCALE_API_TOKEN:-}"
LOGSCALE_REPOSITORY="${LOGSCALE_REPOSITORY:-fsxn-audit}"
LOGSCALE_ALERT_EMAIL="${LOGSCALE_ALERT_EMAIL:-}"
LOGSCALE_ACTION_NAME="${LOGSCALE_ACTION_NAME:-}"

# Base filter. SOURCE in the Lambda defaults to "fsxn-ontap"; keep the two in
# sync if you override it. Field extraction (user/operation/result/path) depends
# on the parser assigned to the repository — the handler sends sourcetype
# "fsxn:audit" so a matching parser will surface these as top-level fields.
BASE_FILTER="${LOGSCALE_BASE_FILTER:-source=\"fsxn-ontap\"}"

DELETE_THRESHOLD="${DELETE_THRESHOLD:-100}"
FAILURE_THRESHOLD="${FAILURE_THRESHOLD:-50}"
SENSITIVE_PATH_REGEX="${SENSITIVE_PATH_REGEX:-/(finance|hr|legal|payroll)/i}"
SEARCH_INTERVAL_SECONDS="${SEARCH_INTERVAL_SECONDS:-300}"
THROTTLE_SECONDS="${THROTTLE_SECONDS:-900}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Falcon LogScale — Create FSx for ONTAP Security Alerts"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -z "${LOGSCALE_API_TOKEN}" ]; then
  echo "❌ ERROR: LOGSCALE_API_TOKEN must be set (management token, not the ingest token)."
  exit 1
fi

if [ -z "${LOGSCALE_ALERT_EMAIL}" ] && [ -z "${LOGSCALE_ACTION_NAME}" ]; then
  echo "❌ ERROR: set LOGSCALE_ALERT_EMAIL (creates an email action) or"
  echo "         LOGSCALE_ACTION_NAME (uses an existing action)."
  echo "   LogScale alerts require at least one action to fire; an alert with an"
  echo "   empty action list is created successfully and then notifies nobody."
  exit 1
fi

GRAPHQL_URL="${LOGSCALE_URL%/}/graphql"

# Escape a value for interpolation into a GraphQL string literal. LQL filters
# contain double quotes (source="fsxn-ontap"), and those quotes would otherwise
# close the surrounding GraphQL string early — producing a document that is
# valid JSON but broken GraphQL, which fails as an opaque syntax error rather
# than as anything pointing at the filter.
gql_escape() {
  printf '%s' "$1" | python3 -c 'import sys; sys.stdout.write(sys.stdin.read().replace("\\", "\\\\").replace("\"", "\\\""))'
}

BASE_FILTER_ESC=$(gql_escape "${BASE_FILTER}")
SENSITIVE_PATH_REGEX_ESC=$(gql_escape "${SENSITIVE_PATH_REGEX}")

# GraphQL documents are embedded in JSON, so quotes need two levels of escaping.
# Building the request body with python3 instead of hand-escaping avoids the
# class of bug where a mis-escaped quote produces a syntactically valid but
# semantically different query.
gql() {
  local query="$1" label="$2"
  local body response errors
  body=$(printf '%s' "${query}" | python3 -c 'import json,sys; print(json.dumps({"query": sys.stdin.read()}))')

  response=$(curl -sS -X POST "${GRAPHQL_URL}" \
    -H "Authorization: Bearer ${LOGSCALE_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${body}")

  # GraphQL returns HTTP 200 even for failures; the "errors" array is the only
  # reliable signal. Checking the status code alone reports success on failure.
  errors=$(printf '%s' "${response}" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f"unparseable response: {e}"); raise SystemExit
errs = d.get("errors") or []
if errs:
    print("; ".join(str(e.get("message", e)) for e in errs))
')

  if [ -n "${errors}" ]; then
    case "${errors}" in
      *[Ee]xists*|*duplicate*|*already*) echo "    ⚠️  Already exists (${label})" ;;
      *) echo "    ❌ Failed (${label}): ${errors}"; return 1 ;;
    esac
  else
    echo "    ✅ Created (${label})"
  fi
}

echo ""

# ─── Action ──────────────────────────────────────────────────────
ACTION_NAME="${LOGSCALE_ACTION_NAME}"
if [ -z "${ACTION_NAME}" ]; then
  ACTION_NAME="fsxn-security-email"
  echo "  Creating email action '${ACTION_NAME}' → ${LOGSCALE_ALERT_EMAIL}..."
  gql "mutation {
  createEmailAction(input: {
    viewName: \"${LOGSCALE_REPOSITORY}\"
    name: \"${ACTION_NAME}\"
    recipients: [\"${LOGSCALE_ALERT_EMAIL}\"]
    useProxy: false
  }) { id }
}" "email action" || true
else
  echo "  Using existing action: ${ACTION_NAME}"
fi

echo ""

# ─── 1. Sensitive Path Access (filter alert) ──────────────────────
# Filter alert = evaluated per event, so it must not contain aggregate
# functions. Suits "any access to this path" detections.
echo "  Creating: Sensitive Path Access..."
gql "mutation {
  createFilterAlert(input: {
    viewName: \"${LOGSCALE_REPOSITORY}\"
    name: \"FSx for ONTAP: Sensitive Path Access\"
    description: \"Access recorded against a sensitive path. Confirm the principal is expected for this share.\"
    queryString: \"${BASE_FILTER_ESC} | path = ${SENSITIVE_PATH_REGEX_ESC}\"
    actionIdsOrNames: [\"${ACTION_NAME}\"]
    labels: [\"fsxn\", \"audit\"]
    throttleTimeSeconds: ${THROTTLE_SECONDS}
    queryOwnershipType: Organization
    enabled: true
  }) { id }
}" "filter alert"

# ─── 2. Mass File Deletion (aggregate alert) ──────────────────────
echo "  Creating: Mass File Deletion (>${DELETE_THRESHOLD} in $((SEARCH_INTERVAL_SECONDS / 60))min)..."
gql "mutation {
  createAggregateAlert(input: {
    viewName: \"${LOGSCALE_REPOSITORY}\"
    name: \"FSx for ONTAP: Mass File Deletion\"
    description: \"A single user deleted more than ${DELETE_THRESHOLD} files within the search interval.\"
    queryString: \"${BASE_FILTER_ESC} | operation = \\\"Delete\\\" | groupBy(user, function=count(as=delete_count)) | delete_count > ${DELETE_THRESHOLD}\"
    actionIdsOrNames: [\"${ACTION_NAME}\"]
    labels: [\"fsxn\", \"audit\"]
    searchIntervalSeconds: ${SEARCH_INTERVAL_SECONDS}
    throttleTimeSeconds: ${THROTTLE_SECONDS}
    queryTimestampType: EventTimestamp
    queryOwnershipType: Organization
    enabled: true
  }) { id }
}" "aggregate alert"

# ─── 3. Failed Access Spike (aggregate alert) ─────────────────────
echo "  Creating: Failed Access Spike (>${FAILURE_THRESHOLD} in $((SEARCH_INTERVAL_SECONDS / 60))min)..."
gql "mutation {
  createAggregateAlert(input: {
    viewName: \"${LOGSCALE_REPOSITORY}\"
    name: \"FSx for ONTAP: Failed Access Spike\"
    description: \"More than ${FAILURE_THRESHOLD} failed access attempts within the search interval.\"
    queryString: \"${BASE_FILTER_ESC} | result = \\\"Failure\\\" | groupBy(svm, function=count(as=failure_count)) | failure_count > ${FAILURE_THRESHOLD}\"
    actionIdsOrNames: [\"${ACTION_NAME}\"]
    labels: [\"fsxn\", \"audit\"]
    searchIntervalSeconds: ${SEARCH_INTERVAL_SECONDS}
    throttleTimeSeconds: ${THROTTLE_SECONDS}
    queryTimestampType: EventTimestamp
    queryOwnershipType: Organization
    enabled: true
  }) { id }
}" "aggregate alert"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Alert creation complete"
echo ""
echo "View alerts: LogScale → ${LOGSCALE_REPOSITORY} → Alerts"
echo ""
echo "Next steps:"
echo "  - Run each alert's query manually first; a query that matches nothing"
echo "    looks identical to a correctly configured alert that never fires."
echo "  - Confirm the repository parser extracts user / operation / result / path."
echo "    Override LOGSCALE_BASE_FILTER if your parser nests them differently."
echo "  - For ransomware (ARP) detection, ship EMS events too — this integration"
echo "    has no EMS stack. See docs/en/ems-detection-capabilities.md."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
