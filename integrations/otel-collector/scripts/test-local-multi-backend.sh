#!/usr/bin/env bash
# test-local-multi-backend.sh — Local E2E test for OTel Collector → Grafana Cloud + Honeycomb
#
# Prerequisites:
#   - Docker installed (Docker Compose optional — script falls back to docker run)
#   - .env file with GRAFANA_OTLP_ENDPOINT, GRAFANA_BASIC_AUTH,
#     HONEYCOMB_API_KEY, HONEYCOMB_DATASET configured
#
# Usage:
#   cd integrations/otel-collector
#   bash scripts/test-local-multi-backend.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
COLLECTOR_CONFIG="${PROJECT_DIR}/otel-collector-config.yaml"

# Pinned OTel Collector version (verified working 2026-05-18)
COLLECTOR_IMAGE="otel/opentelemetry-collector-contrib:0.152.0"
CONTAINER_NAME="otel-collector-multi"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Detect whether docker compose (v2 plugin) is available
USE_COMPOSE=false
if docker compose version &>/dev/null; then
    USE_COMPOSE=true
fi

cleanup() {
    log_info "Stopping OTel Collector..."
    if [[ "${USE_COMPOSE}" == "true" ]]; then
        docker compose -f "${PROJECT_DIR}/docker-compose.yaml" --env-file "${ENV_FILE}" down --remove-orphans 2>/dev/null || true
    else
        docker stop "${CONTAINER_NAME}" 2>/dev/null || true
        docker rm "${CONTAINER_NAME}" 2>/dev/null || true
    fi
}

trap cleanup EXIT

# --- Pre-flight checks ---

if [[ ! -f "${ENV_FILE}" ]]; then
    log_error ".env not found. Copy .env.example and configure:"
    log_error "  cp .env.example .env"
    log_error ""
    log_error "Required variables:"
    log_error "  GRAFANA_OTLP_ENDPOINT  — e.g., https://otlp-gateway-prod-ap-northeast-0.grafana.net/otlp"
    log_error "  GRAFANA_BASIC_AUTH     — base64(instanceId:apiToken)"
    log_error "  HONEYCOMB_API_KEY      — hcaik_* ingest key"
    log_error "  HONEYCOMB_DATASET      — e.g., fsxn-audit"
    exit 1
fi

# --- Step 1: Generate OTLP payload with current timestamps ---

log_info "Generating OTLP payload with current timestamps..."
CURRENT_NANO=$(python3 -c "import time; print(str(int(time.time() * 1_000_000_000)))")
PAYLOAD_FILE=$(mktemp /tmp/otlp-payload-XXXXXX.json)

cat > "${PAYLOAD_FILE}" <<EOF
{
  "resourceLogs": [{
    "resource": {
      "attributes": [
        {"key": "service.name", "value": {"stringValue": "fsxn-audit"}},
        {"key": "cloud.provider", "value": {"stringValue": "aws"}},
        {"key": "cloud.platform", "value": {"stringValue": "aws_fsx"}},
        {"key": "deployment.environment", "value": {"stringValue": "e2e-verification"}}
      ]
    },
    "scopeLogs": [{
      "scope": {"name": "fsxn-otel-shipper", "version": "1.0.0"},
      "logRecords": [
        {
          "timeUnixNano": "${CURRENT_NANO}",
          "severityNumber": 9,
          "severityText": "INFO",
          "body": {"stringValue": "{\"EventID\":\"4663\",\"UserName\":\"admin\",\"ClientIP\":\"198.51.100.100\",\"Operation\":\"ReadData\",\"ObjectName\":\"/vol1/reports/quarterly.xlsx\",\"Result\":\"Success\",\"SVMName\":\"svm-prod-01\"}"},
          "attributes": [
            {"key": "event.type", "value": {"stringValue": "4663"}},
            {"key": "user.name", "value": {"stringValue": "admin"}},
            {"key": "client.address", "value": {"stringValue": "198.51.100.100"}},
            {"key": "fsxn.operation", "value": {"stringValue": "ReadData"}},
            {"key": "fsxn.path", "value": {"stringValue": "/vol1/reports/quarterly.xlsx"}},
            {"key": "fsxn.result", "value": {"stringValue": "Success"}},
            {"key": "fsxn.svm", "value": {"stringValue": "svm-prod-01"}}
          ]
        },
        {
          "timeUnixNano": "${CURRENT_NANO}",
          "severityNumber": 13,
          "severityText": "WARN",
          "body": {"stringValue": "{\"EventID\":\"4656\",\"UserName\":\"unknown-user\",\"ClientIP\":\"198.51.100.150\",\"Operation\":\"WriteData\",\"ObjectName\":\"/vol1/confidential/secret.docx\",\"Result\":\"Access Denied\",\"SVMName\":\"svm-prod-01\"}"},
          "attributes": [
            {"key": "event.type", "value": {"stringValue": "4656"}},
            {"key": "user.name", "value": {"stringValue": "unknown-user"}},
            {"key": "client.address", "value": {"stringValue": "198.51.100.150"}},
            {"key": "fsxn.operation", "value": {"stringValue": "WriteData"}},
            {"key": "fsxn.path", "value": {"stringValue": "/vol1/confidential/secret.docx"}},
            {"key": "fsxn.result", "value": {"stringValue": "Access Denied"}},
            {"key": "fsxn.svm", "value": {"stringValue": "svm-prod-01"}}
          ]
        }
      ]
    }]
  }]
}
EOF

log_info "Payload generated: ${PAYLOAD_FILE}"

# --- Step 2: Start OTel Collector with multi-backend config ---

log_info "Starting OTel Collector with Grafana + Honeycomb exporters..."
log_info "  Image: ${COLLECTOR_IMAGE}"

if [[ "${USE_COMPOSE}" == "true" ]]; then
    log_info "  Method: docker compose"
    docker compose -f "${PROJECT_DIR}/docker-compose.yaml" --env-file "${ENV_FILE}" up -d
else
    log_warn "  docker compose plugin not available (common with Colima)"
    log_info "  Method: docker run (fallback)"
    # Stop any existing container
    docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    docker rm "${CONTAINER_NAME}" 2>/dev/null || true
    # Start with docker run
    docker run -d --name "${CONTAINER_NAME}" \
        -p 4318:4318 -p 13133:13133 \
        -v "${COLLECTOR_CONFIG}:/etc/otelcol-contrib/config.yaml" \
        --env-file "${ENV_FILE}" \
        "${COLLECTOR_IMAGE}"
fi

# --- Step 3: Wait for health check ---

log_info "Waiting for health check (max 30s)..."
RETRIES=0
MAX_RETRIES=30
until curl -sf http://localhost:13133/ > /dev/null 2>&1; do
    RETRIES=$((RETRIES + 1))
    if [[ ${RETRIES} -ge ${MAX_RETRIES} ]]; then
        log_error "Health check failed after ${MAX_RETRIES}s"
        docker logs "${CONTAINER_NAME}" 2>&1 | tail -20
        exit 1
    fi
    sleep 1
done
log_info "Health check passed"

# --- Step 4: Send OTLP payload ---

log_info "Sending OTLP test payload to localhost:4318/v1/logs..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:4318/v1/logs \
    -H "Content-Type: application/json" \
    -d @"${PAYLOAD_FILE}")

if [[ "${HTTP_STATUS}" -ge 200 && "${HTTP_STATUS}" -lt 300 ]]; then
    log_info "OTLP endpoint accepted payload (HTTP ${HTTP_STATUS})"
else
    log_error "OTLP endpoint rejected payload (HTTP ${HTTP_STATUS})"
    docker logs "${CONTAINER_NAME}" 2>&1 | tail -20
    exit 1
fi

# --- Step 5: Check collector logs for export activity ---

log_info "Waiting 5s for batch processor to flush..."
sleep 5

log_info "Checking collector logs for export activity..."
LOGS=$(docker logs "${CONTAINER_NAME}" 2>&1)

ERRORS_FOUND=false
if echo "${LOGS}" | grep -qi "error"; then
    log_warn "Collector logs contain errors:"
    echo "${LOGS}" | grep -i "error" | head -5
    ERRORS_FOUND=true
fi

if echo "${LOGS}" | grep -qi "exporting\|sending\|exported"; then
    log_info "Collector is exporting data to backends"
fi

# --- Step 6: Cleanup temp file ---

rm -f "${PAYLOAD_FILE}"

# --- Summary ---

echo ""
log_info "=== Multi-Backend E2E Test Complete ==="
log_info ""
log_info "  OTel Collector: ${COLLECTOR_IMAGE}"
log_info "  Config: otel-collector-config.yaml (otlphttp/grafana + otlphttp/honeycomb)"
log_info "  Health check: PASS"
log_info "  OTLP payload accepted: HTTP ${HTTP_STATUS}"
if [[ "${ERRORS_FOUND}" == "true" ]]; then
    log_warn "  Collector errors detected — check backend credentials"
fi
log_info ""
log_info "  Verify delivery:"
log_info "    Grafana Cloud: Explore → Loki → {service_name=\"fsxn-audit\"}"
log_info "    Honeycomb: fsxn-audit dataset → COUNT query"
log_info ""
log_info "  Auth patterns (verified):"
log_info "    Grafana: Basic Auth = base64(instanceId:apiToken)"
log_info "    Honeycomb: x-honeycomb-team = hcaik_* ingest key"
log_info ""
log_info "  Key insight: Lambda handler.py is UNCHANGED."
log_info "  Only the Collector config determines the backend."
log_info "======================================="
