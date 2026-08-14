#!/usr/bin/env bash
# generate-otlp-payload.sh — Generate an OTLP payload with current timestamps
#
# Useful for testing without stale timestamp issues.
# Datadog rejects logs older than ~18 hours.
# Grafana Cloud and Honeycomb also prefer recent timestamps.
#
# Usage:
#   cd integrations/otel-collector
#   bash scripts/generate-otlp-payload.sh > /tmp/payload.json
#   curl -X POST http://localhost:4318/v1/logs \
#     -H "Content-Type: application/json" \
#     -d @/tmp/payload.json
#
# Options:
#   --output FILE   Write to file instead of stdout
#   --records N     Number of log records to generate (default: 4)

set -euo pipefail

OUTPUT=""
NUM_RECORDS=4

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --records)
            NUM_RECORDS="$2"
            shift 2
            ;;
        *)
            echo "Usage: $0 [--output FILE] [--records N]" >&2
            exit 1
            ;;
    esac
done

# Generate current timestamp in nanoseconds
CURRENT_NANO=$(python3 -c "import time; print(str(int(time.time() * 1_000_000_000)))")

# Sample audit log data for generating records
OPERATIONS=("ReadData" "WriteData" "CreateFile" "DeleteFile" "RenameFile" "ListDirectory")
USERS=("admin" "user01" "backup-svc" "unknown-user")
PATHS=("/vol1/reports/quarterly.xlsx" "/vol1/data/export.csv" "/vol1/confidential/secret.docx" "/vol1/shared/readme.txt" "/vol1/archive/old-report.pdf" "/vol1/temp/scratch.tmp")
RESULTS=("Success" "Success" "Success" "Access Denied" "Failure" "Success")
EVENT_IDS=("4663" "4656" "4660" "4663" "4656" "4663")
SVMS=("svm-prod-01" "svm-prod-01" "svm-dev-01")
CLIENT_IPS=("198.51.100.100" "198.51.100.101" "198.51.100.150" "198.51.100.200")

# Build log records JSON array
RECORDS=""
for i in $(seq 0 $((NUM_RECORDS - 1))); do
    OP_IDX=$((i % ${#OPERATIONS[@]}))
    USER_IDX=$((i % ${#USERS[@]}))
    PATH_IDX=$((i % ${#PATHS[@]}))
    RESULT_IDX=$((i % ${#RESULTS[@]}))
    EID_IDX=$((i % ${#EVENT_IDS[@]}))
    SVM_IDX=$((i % ${#SVMS[@]}))
    CIP_IDX=$((i % ${#CLIENT_IPS[@]}))

    OP="${OPERATIONS[$OP_IDX]}"
    USER="${USERS[$USER_IDX]}"
    FPATH="${PATHS[$PATH_IDX]}"
    RESULT="${RESULTS[$RESULT_IDX]}"
    EID="${EVENT_IDS[$EID_IDX]}"
    SVM="${SVMS[$SVM_IDX]}"
    CIP="${CLIENT_IPS[$CIP_IDX]}"

    # Determine severity
    SEV_NUM=9
    SEV_TEXT="INFO"
    if [[ "${RESULT}" == *"Denied"* ]] || [[ "${RESULT}" == *"Fail"* ]]; then
        SEV_NUM=13
        SEV_TEXT="WARN"
    fi

    # Add comma separator between records
    if [[ -n "${RECORDS}" ]]; then
        RECORDS="${RECORDS},"
    fi

    RECORDS="${RECORDS}
        {
          \"timeUnixNano\": \"${CURRENT_NANO}\",
          \"severityNumber\": ${SEV_NUM},
          \"severityText\": \"${SEV_TEXT}\",
          \"body\": {\"stringValue\": \"{\\\"EventID\\\":\\\"${EID}\\\",\\\"UserName\\\":\\\"${USER}\\\",\\\"ClientIP\\\":\\\"${CIP}\\\",\\\"Operation\\\":\\\"${OP}\\\",\\\"ObjectName\\\":\\\"${FPATH}\\\",\\\"Result\\\":\\\"${RESULT}\\\",\\\"SVMName\\\":\\\"${SVM}\\\"}\"},
          \"attributes\": [
            {\"key\": \"event.type\", \"value\": {\"stringValue\": \"${EID}\"}},
            {\"key\": \"user.name\", \"value\": {\"stringValue\": \"${USER}\"}},
            {\"key\": \"client.address\", \"value\": {\"stringValue\": \"${CIP}\"}},
            {\"key\": \"fsxn.operation\", \"value\": {\"stringValue\": \"${OP}\"}},
            {\"key\": \"fsxn.path\", \"value\": {\"stringValue\": \"${FPATH}\"}},
            {\"key\": \"fsxn.result\", \"value\": {\"stringValue\": \"${RESULT}\"}},
            {\"key\": \"fsxn.svm\", \"value\": {\"stringValue\": \"${SVM}\"}}
          ]
        }"
done

PAYLOAD="{
  \"resourceLogs\": [{
    \"resource\": {
      \"attributes\": [
        {\"key\": \"service.name\", \"value\": {\"stringValue\": \"fsxn-audit\"}},
        {\"key\": \"cloud.provider\", \"value\": {\"stringValue\": \"aws\"}},
        {\"key\": \"cloud.platform\", \"value\": {\"stringValue\": \"aws_fsx\"}},
        {\"key\": \"deployment.environment\", \"value\": {\"stringValue\": \"e2e-verification\"}}
      ]
    },
    \"scopeLogs\": [{
      \"scope\": {\"name\": \"fsxn-otel-shipper\", \"version\": \"1.0.0\"},
      \"logRecords\": [${RECORDS}
      ]
    }]
  }]
}"

if [[ -n "${OUTPUT}" ]]; then
    echo "${PAYLOAD}" > "${OUTPUT}"
    echo "Payload written to ${OUTPUT} (${NUM_RECORDS} records, timestamp: ${CURRENT_NANO})" >&2
else
    echo "${PAYLOAD}"
fi
