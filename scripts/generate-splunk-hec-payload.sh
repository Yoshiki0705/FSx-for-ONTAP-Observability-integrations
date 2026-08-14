#!/usr/bin/env bash
# generate-splunk-hec-payload.sh — Generate Splunk HEC JSON payload with current timestamps
#
# Produces newline-delimited HEC JSON events suitable for the
# /services/collector/event endpoint. Pipe output directly to curl:
#
#   bash scripts/generate-splunk-hec-payload.sh | \
#     curl -X POST "https://<host>:8088/services/collector/event" \
#       -H "Authorization: Splunk <token>" \
#       -H "Content-Type: application/json" \
#       -d @-
#
# Options:
#   --count N         Number of events to generate (default: 3)
#   --sourcetype ST   Sourcetype: fsxn:ontap:audit or fsxn:ontap:ems (default: fsxn:ontap:audit)
#   --output FILE     Write to file instead of stdout
#   --host HOSTNAME   Override host field (default: svm-prod-01)
#   -h, --help        Show this help message

set -euo pipefail

# --- Defaults ---
COUNT=3
SOURCETYPE="fsxn:ontap:audit"
OUTPUT=""
HOST_OVERRIDE=""

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --count)
      COUNT="$2"
      shift 2
      ;;
    --sourcetype)
      SOURCETYPE="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    --host)
      HOST_OVERRIDE="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--count N] [--sourcetype ST] [--output FILE] [--host HOSTNAME]"
      echo ""
      echo "Generate Splunk HEC JSON payload with current timestamps."
      echo ""
      echo "Options:"
      echo "  --count N         Number of events to generate (default: 3)"
      echo "  --sourcetype ST   fsxn:ontap:audit or fsxn:ontap:ems (default: fsxn:ontap:audit)"
      echo "  --output FILE     Write to file instead of stdout"
      echo "  --host HOSTNAME   Override host field (default: svm-prod-01)"
      echo "  -h, --help        Show this help message"
      echo ""
      echo "Examples:"
      echo "  # Generate 5 audit events"
      echo "  $0 --count 5"
      echo ""
      echo "  # Generate EMS events and save to file"
      echo "  $0 --sourcetype fsxn:ontap:ems --output /tmp/hec-payload.json"
      echo ""
      echo "  # Pipe directly to Splunk HEC"
      echo "  $0 --count 10 | curl -X POST 'https://splunk:8088/services/collector/event' \\"
      echo "    -H 'Authorization: Splunk <token>' -H 'Content-Type: application/json' -d @-"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      echo "Run '$0 --help' for usage information." >&2
      exit 1
      ;;
  esac
done

# --- Validate arguments ---
if ! [[ "${COUNT}" =~ ^[0-9]+$ ]] || [[ "${COUNT}" -lt 1 ]]; then
  echo "ERROR: --count must be a positive integer, got: ${COUNT}" >&2
  exit 1
fi

if [[ "${SOURCETYPE}" != "fsxn:ontap:audit" && "${SOURCETYPE}" != "fsxn:ontap:ems" ]]; then
  echo "ERROR: --sourcetype must be 'fsxn:ontap:audit' or 'fsxn:ontap:ems', got: ${SOURCETYPE}" >&2
  exit 1
fi

# --- Determine index from sourcetype ---
if [[ "${SOURCETYPE}" == "fsxn:ontap:audit" ]]; then
  INDEX="fsxn_audit"
else
  INDEX="fsxn_ems"
fi

# --- Current epoch timestamp (seconds with millisecond precision) ---
EPOCH_TIME=$(python3 -c "import time; print(f'{time.time():.3f}')")

# --- Sample data for audit events ---
OPERATIONS=("ReadData" "WriteData" "CreateFile" "DeleteFile" "RenameFile" "ListDirectory")
USERS=("admin" "user01" "backup-svc" "auditor")
PATHS=("/vol1/reports/quarterly.xlsx" "/vol1/data/export.csv" "/vol1/confidential/secret.docx" "/vol1/shared/readme.txt" "/vol1/archive/old-report.pdf" "/vol1/temp/scratch.tmp")
RESULTS=("Success" "Success" "Success" "Access Denied" "Failure" "Success")
EVENT_IDS=("4663" "4656" "4660" "4663" "4656" "4663")
SVMS=("svm-prod-01" "svm-prod-02" "svm-dev-01")
CLIENT_IPS=("198.51.100.100" "198.51.100.101" "198.51.100.150" "198.51.100.200")

# --- Sample data for EMS events ---
EMS_NAMES=("arw.volume.state" "wafl.vol.autoSize.done" "callhome.battery.low" "monitor.volume.full" "arw.volume.state" "scsiblade.san.active")
EMS_SEVERITIES=("alert" "notice" "warning" "error" "alert" "notice")
EMS_STATES=("attack-detected" "grow-successful" "battery-low" "volume-full" "learning-complete" "san-active")

# --- Generate events ---
generate_audit_event() {
  local idx=$1
  local op_idx=$((idx % ${#OPERATIONS[@]}))
  local user_idx=$((idx % ${#USERS[@]}))
  local path_idx=$((idx % ${#PATHS[@]}))
  local result_idx=$((idx % ${#RESULTS[@]}))
  local eid_idx=$((idx % ${#EVENT_IDS[@]}))
  local svm_idx=$((idx % ${#SVMS[@]}))
  local cip_idx=$((idx % ${#CLIENT_IPS[@]}))

  local host="${HOST_OVERRIDE:-${SVMS[$svm_idx]}}"

  cat <<EOF
{"time":${EPOCH_TIME},"host":"${host}","source":"fsxn-observability","sourcetype":"${SOURCETYPE}","index":"${INDEX}","event":{"event_type":"${EVENT_IDS[$eid_idx]}","user":"${USERS[$user_idx]}","client_ip":"${CLIENT_IPS[$cip_idx]}","operation":"${OPERATIONS[$op_idx]}","path":"${PATHS[$path_idx]}","result":"${RESULTS[$result_idx]}","svm":"${SVMS[$svm_idx]}"}}
EOF
}

generate_ems_event() {
  local idx=$1
  local name_idx=$((idx % ${#EMS_NAMES[@]}))
  local sev_idx=$((idx % ${#EMS_SEVERITIES[@]}))
  local state_idx=$((idx % ${#EMS_STATES[@]}))
  local svm_idx=$((idx % ${#SVMS[@]}))

  local host="${HOST_OVERRIDE:-${SVMS[$svm_idx]}}"

  cat <<EOF
{"time":${EPOCH_TIME},"host":"${host}","source":"fsxn-observability","sourcetype":"${SOURCETYPE}","index":"${INDEX}","event":{"message-name":"${EMS_NAMES[$name_idx]}","message-severity":"${EMS_SEVERITIES[$sev_idx]}","message-timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","parameters":{"vserver-name":"${SVMS[$svm_idx]}","state":"${EMS_STATES[$state_idx]}","volume-name":"vol_data"}}}
EOF
}

# --- Build payload (newline-delimited JSON) ---
PAYLOAD=""
for i in $(seq 0 $((COUNT - 1))); do
  if [[ "${SOURCETYPE}" == "fsxn:ontap:audit" ]]; then
    EVENT=$(generate_audit_event "$i")
  else
    EVENT=$(generate_ems_event "$i")
  fi

  if [[ -n "${PAYLOAD}" ]]; then
    PAYLOAD="${PAYLOAD}
${EVENT}"
  else
    PAYLOAD="${EVENT}"
  fi
done

# --- Output ---
if [[ -n "${OUTPUT}" ]]; then
  echo "${PAYLOAD}" > "${OUTPUT}"
  echo "Payload written to ${OUTPUT} (${COUNT} events, sourcetype: ${SOURCETYPE}, time: ${EPOCH_TIME})" >&2
else
  echo "${PAYLOAD}"
fi
