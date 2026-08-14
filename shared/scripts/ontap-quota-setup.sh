#!/bin/bash
# =============================================================================
# ontap-quota-setup.sh — Qtree quota configuration via ONTAP REST API
#
# Creates a Qtree and applies quota rules (soft/hard limits) without requiring
# NetApp Console or System Manager. Uses only ONTAP REST API (fsxadmin auth).
#
# Usage:
#   # Dry-run (preview commands only)
#   ./ontap-quota-setup.sh --endpoint <mgmt-ip> --svm <svm-name> \
#     --volume <vol-name> --qtree <qtree-name> \
#     --hard-limit 100GB --soft-limit 80GB --dry-run
#
#   # Execute
#   ./ontap-quota-setup.sh --endpoint <mgmt-ip> --svm <svm-name> \
#     --volume <vol-name> --qtree <qtree-name> \
#     --hard-limit 100GB --soft-limit 80GB
#
# Prerequisites:
#   - curl, jq
#   - ONTAP_USER and ONTAP_PASS environment variables (or prompt)
#   - Network access to ONTAP management endpoint (TCP 443)
#
# What this script does:
#   1. Creates a Qtree (if it doesn't exist)
#   2. Creates a tree quota rule (hard + soft limits)
#   3. Resizes quotas on the volume (applies the new rule)
#   4. Shows quota status
# =============================================================================
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"

# Defaults
ENDPOINT=""
SVM_NAME=""
VOLUME_NAME=""
QTREE_NAME=""
HARD_LIMIT=""
SOFT_LIMIT=""
SECURITY_STYLE="ntfs"
DRY_RUN=false

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [options]

Options:
  --endpoint IP       ONTAP management endpoint IP (required)
  --svm NAME          SVM name (required)
  --volume NAME       Volume name (required)
  --qtree NAME        Qtree name to create (required)
  --hard-limit SIZE   Hard limit (e.g., 100GB, 50MB) (required)
  --soft-limit SIZE   Soft limit (e.g., 80GB, 40MB) (optional)
  --security-style S  Security style: ntfs|unix|mixed (default: ntfs)
  --dry-run           Preview API calls without executing
  -h, --help          Show this help

Environment variables:
  ONTAP_USER          ONTAP admin username (default: fsxadmin)
  ONTAP_PASS          ONTAP admin password (prompted if not set)

Examples:
  # Create qtree with 100GB hard limit, 80GB soft limit
  ${SCRIPT_NAME} --endpoint 198.51.100.100 --svm svm-prod \\
    --volume vol_data --qtree dept-sales \\
    --hard-limit 100GB --soft-limit 80GB

  # Dry-run to preview
  ${SCRIPT_NAME} --endpoint 198.51.100.100 --svm svm-prod \\
    --volume vol_data --qtree dept-sales \\
    --hard-limit 50GB --dry-run
EOF
    exit 0
}

# Parse size string to bytes (e.g., "100GB" -> 107374182400)
parse_size() {
    local input="$1"
    local num unit
    num=$(echo "$input" | grep -oE '^[0-9]+')
    unit=$(echo "$input" | grep -oE '[A-Za-z]+$' | tr '[:lower:]' '[:upper:]')

    case "$unit" in
        KB) echo $((num * 1024)) ;;
        MB) echo $((num * 1024 * 1024)) ;;
        GB) echo $((num * 1024 * 1024 * 1024)) ;;
        TB) echo $((num * 1024 * 1024 * 1024 * 1024)) ;;
        *)  echo "$num" ;;  # Assume bytes if no unit
    esac
}

# API call helper
api_call() {
    local method="$1"
    local path="$2"
    local data="${3:-}"

    local url="https://${ENDPOINT}/api${path}"
    local args=(-sk -u "${ONTAP_USER}:${ONTAP_PASS}" -X "$method")
    args+=(-H "Content-Type: application/json")
    args+=(-w "\n%{http_code}")

    if [[ -n "$data" ]]; then
        args+=(-d "$data")
    fi

    if [[ "$DRY_RUN" == true ]]; then
        echo "[DRY-RUN] $method $url"
        [[ -n "$data" ]] && echo "  Body: $data"
        echo "  (skipped)"
        return 0
    fi

    local response
    response=$(curl "${args[@]}" "$url")
    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" -ge 400 ]]; then
        echo "ERROR: HTTP $http_code on $method $path" >&2
        echo "$body" | jq . 2>/dev/null || echo "$body" >&2
        return 1
    fi

    echo "$body"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --endpoint)      ENDPOINT="$2"; shift 2 ;;
        --svm)           SVM_NAME="$2"; shift 2 ;;
        --volume)        VOLUME_NAME="$2"; shift 2 ;;
        --qtree)         QTREE_NAME="$2"; shift 2 ;;
        --hard-limit)    HARD_LIMIT="$2"; shift 2 ;;
        --soft-limit)    SOFT_LIMIT="$2"; shift 2 ;;
        --security-style) SECURITY_STYLE="$2"; shift 2 ;;
        --dry-run)       DRY_RUN=true; shift ;;
        -h|--help)       usage ;;
        *)               echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# Validate required params
[[ -z "$ENDPOINT" ]] && { echo "ERROR: --endpoint required" >&2; exit 1; }
[[ -z "$SVM_NAME" ]] && { echo "ERROR: --svm required" >&2; exit 1; }
[[ -z "$VOLUME_NAME" ]] && { echo "ERROR: --volume required" >&2; exit 1; }
[[ -z "$QTREE_NAME" ]] && { echo "ERROR: --qtree required" >&2; exit 1; }
[[ -z "$HARD_LIMIT" ]] && { echo "ERROR: --hard-limit required" >&2; exit 1; }

# Credentials
ONTAP_USER="${ONTAP_USER:-fsxadmin}"
if [[ -z "${ONTAP_PASS:-}" ]]; then
    echo -n "ONTAP password for ${ONTAP_USER}: "
    read -rs ONTAP_PASS
    echo
fi

# Convert sizes
HARD_BYTES=$(parse_size "$HARD_LIMIT")
SOFT_BYTES=""
[[ -n "$SOFT_LIMIT" ]] && SOFT_BYTES=$(parse_size "$SOFT_LIMIT")

echo "=== Qtree Quota Setup ==="
echo "  Endpoint: ${ENDPOINT}"
echo "  SVM:      ${SVM_NAME}"
echo "  Volume:   ${VOLUME_NAME}"
echo "  Qtree:    ${QTREE_NAME}"
echo "  Hard:     ${HARD_LIMIT} (${HARD_BYTES} bytes)"
[[ -n "$SOFT_LIMIT" ]] && echo "  Soft:     ${SOFT_LIMIT} (${SOFT_BYTES} bytes)"
echo "  Style:    ${SECURITY_STYLE}"
echo ""

# Step 1: Get volume UUID
echo "--- Step 1: Find volume UUID ---"
VOL_INFO=$(api_call GET "/storage/volumes?svm.name=${SVM_NAME}&name=${VOLUME_NAME}&fields=uuid")
if [[ "$DRY_RUN" == true ]]; then
    VOL_UUID="<volume-uuid>"
else
    VOL_UUID=$(echo "$VOL_INFO" | jq -r '.records[0].uuid // empty')
    if [[ -z "$VOL_UUID" ]]; then
        echo "ERROR: Volume '${VOLUME_NAME}' not found in SVM '${SVM_NAME}'" >&2
        exit 1
    fi
    echo "  Volume UUID: ${VOL_UUID}"
fi

# Step 2: Create Qtree (if not exists)
echo ""
echo "--- Step 2: Create Qtree '${QTREE_NAME}' ---"
QTREE_BODY=$(jq -n \
    --arg name "$QTREE_NAME" \
    --arg style "$SECURITY_STYLE" \
    --arg vol "$VOL_UUID" \
    --arg svm "$SVM_NAME" \
    '{name: $name, security_style: $style, volume: {uuid: $vol}, svm: {name: $svm}}')
api_call POST "/storage/qtrees" "$QTREE_BODY" || {
    echo "  (Qtree may already exist — continuing)"
}

# Step 3: Create quota rule
echo ""
echo "--- Step 3: Create quota rule ---"
QUOTA_BODY=$(jq -n \
    --arg svm "$SVM_NAME" \
    --arg vol "$VOL_UUID" \
    --arg qtree "$QTREE_NAME" \
    --argjson hard "$HARD_BYTES" \
    '{
        type: "tree",
        svm: {name: $svm},
        volume: {uuid: $vol},
        qtree: {name: $qtree},
        space: {hard_limit: $hard}
    }')

# Add soft limit if specified
if [[ -n "$SOFT_BYTES" ]]; then
    QUOTA_BODY=$(echo "$QUOTA_BODY" | jq --argjson soft "$SOFT_BYTES" '.space.soft_limit = $soft')
fi

api_call POST "/storage/quota/rules" "$QUOTA_BODY"

# Step 4: Resize quotas (apply rules)
echo ""
echo "--- Step 4: Resize quotas on volume ---"
api_call PATCH "/storage/volumes/${VOL_UUID}" '{"quota":{"enabled":true}}'

# Step 5: Show status
echo ""
echo "--- Step 5: Quota status ---"
if [[ "$DRY_RUN" == false ]]; then
    sleep 5  # Wait for quota initialization
    api_call GET "/storage/quota/reports?volume.uuid=${VOL_UUID}&qtree.name=${QTREE_NAME}" | \
        jq '.records[] | {qtree: .qtree.name, space_used: .space.used.total, space_hard: .space.hard_limit, space_soft: .space.soft_limit}' 2>/dev/null || \
        echo "  (Quota report may take a few seconds to populate)"
fi

echo ""
echo "=== Done ==="
[[ "$DRY_RUN" == true ]] && echo "(Dry-run mode — no changes were made)"
