#!/bin/bash
# Import Harvest Grafana dashboards into Amazon Managed Grafana (AMG) workspace.
#
# This script imports pre-built NetApp Harvest dashboard JSON files into an AMG
# workspace, configures AMP as the data source, and outputs panel embed URLs
# for ToolJet integration.
#
# Usage:
#   ./import-dashboards.sh --workspace-url <AMG_URL> --api-key <API_KEY> [OPTIONS]
#
# Required:
#   --workspace-url   AMG workspace URL (e.g., https://g-abc123.grafana-workspace.ap-northeast-1.amazonaws.com)
#   --api-key         Grafana API key with Admin permissions
#
# Optional:
#   --amp-workspace-id    AMP workspace ID (default: from CloudFormation stack output)
#   --dashboard-dir       Directory containing dashboard JSON files (default: ../harvest/dashboards/)
#   --datasource-name     Name for the AMP data source (default: "Amazon Managed Prometheus")
#   --output-file         File to write panel embed URLs (default: ./panel-embed-urls.json)
#   --region              AWS region (default: ap-northeast-1)
#   --dry-run             Validate dashboards without importing
#   --help                Show this help message
#
# Requirements:
#   - curl
#   - jq
#   - AWS CLI (for AMP workspace ID resolution)
#
# Dashboard Categories:
#   - Volume Performance: IOPS, throughput, latency, top volumes
#   - Aggregate Utilization: capacity, used space, available space, growth
#   - SVM Health: state, protocols, connections, operations
#   - Network Interfaces: throughput, errors, status, packets
#
# There is no Disk Status category. It was here until 2026-09-07, importing four disk
# dashboards into a folder named for them. AWS classifies `ONTAP: Disk` as unsupported on
# FSx for ONTAP -- the physical layer is not exposed -- so those panels have no metrics
# behind them. A folder that exists and stays empty reads as a broken import rather than as
# a capability the platform does not offer.
# See management-console/harvest/dashboards/README.md for the full 19 / 8 / 10 split.
#
set -euo pipefail

# --- Constants ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_DASHBOARD_DIR="${SCRIPT_DIR}/../harvest/dashboards"
DEFAULT_DATASOURCE_NAME="Amazon Managed Prometheus"
DEFAULT_OUTPUT_FILE="${SCRIPT_DIR}/panel-embed-urls.json"
DEFAULT_REGION="${AWS_REGION:-ap-northeast-1}"

# Dashboard definitions by category
# These correspond to Harvest's pre-built Grafana dashboards for ONTAP
CATEGORIES="volume_performance aggregate_utilization svm_health network_interfaces"

# Dashboards per category (space-separated filenames)
DASHBOARDS_volume_performance="volume_performance.json volume_iops.json volume_throughput.json volume_latency.json volume_top_n.json"
DASHBOARDS_aggregate_utilization="aggregate_capacity.json aggregate_utilization.json aggregate_growth.json aggregate_space_savings.json"
DASHBOARDS_svm_health="svm_overview.json svm_nfs_operations.json svm_cifs_operations.json svm_iscsi_operations.json"
DASHBOARDS_network_interfaces="network_lif_throughput.json network_lif_errors.json network_port_status.json network_lif_packets.json"

# Helper: get dashboards for a category
get_dashboards_for_category() {
  local category="$1"
  local var_name="DASHBOARDS_${category}"
  eval echo "\$$var_name"
}

# Minimum required dashboards
MIN_DASHBOARDS=20

# --- Functions ---

usage() {
  head -35 "$0" | grep -E "^#" | sed 's/^# \?//'
  exit 0
}

log_info() {
  echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*"
}

log_error() {
  echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2
}

log_warn() {
  echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2
}

check_dependencies() {
  local missing=()
  for cmd in curl jq aws; do
    if ! command -v "$cmd" &>/dev/null; then
      missing+=("$cmd")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    log_error "Missing required commands: ${missing[*]}"
    exit 1
  fi
}

validate_url() {
  local url="$1"
  if [[ ! "$url" =~ ^https:// ]]; then
    log_error "Workspace URL must start with https://"
    exit 1
  fi
}

# Create or update AMP data source in Grafana
configure_datasource() {
  local workspace_url="$1"
  local api_key="$2"
  local datasource_name="$3"
  local amp_workspace_id="$4"
  local region="$5"

  local amp_endpoint="https://aps-workspaces.${region}.amazonaws.com/workspaces/${amp_workspace_id}"

  log_info "Configuring AMP data source: ${datasource_name}"

  local datasource_payload
  datasource_payload=$(jq -n \
    --arg name "$datasource_name" \
    --arg url "${amp_endpoint}/api/v1/query" \
    --arg region "$region" \
    '{
      "name": $name,
      "type": "prometheus",
      "access": "proxy",
      "url": $url,
      "isDefault": true,
      "jsonData": {
        "httpMethod": "POST",
        "sigV4Auth": true,
        "sigV4AuthType": "default",
        "sigV4Region": $region
      }
    }')

  # Check if data source already exists
  local existing_ds
  existing_ds=$(curl -s -w "\n%{http_code}" \
    -H "Authorization: Bearer ${api_key}" \
    -H "Content-Type: application/json" \
    "${workspace_url}/api/datasources/name/${datasource_name}" 2>/dev/null || true)

  local http_code
  http_code=$(echo "$existing_ds" | tail -1)
  local response_body
  response_body=$(echo "$existing_ds" | sed '$d')

  if [[ "$http_code" == "200" ]]; then
    # Update existing data source
    local ds_id
    ds_id=$(echo "$response_body" | jq -r '.id')
    log_info "Updating existing data source (ID: ${ds_id})"

    curl -s -X PUT \
      -H "Authorization: Bearer ${api_key}" \
      -H "Content-Type: application/json" \
      -d "$datasource_payload" \
      "${workspace_url}/api/datasources/${ds_id}" >/dev/null

  else
    # Create new data source
    log_info "Creating new data source"

    local create_response
    create_response=$(curl -s -w "\n%{http_code}" \
      -H "Authorization: Bearer ${api_key}" \
      -H "Content-Type: application/json" \
      -d "$datasource_payload" \
      "${workspace_url}/api/datasources")

    http_code=$(echo "$create_response" | tail -1)
    if [[ "$http_code" != "200" && "$http_code" != "201" ]]; then
      log_error "Failed to create data source. HTTP ${http_code}"
      log_error "Response: $(echo "$create_response" | sed '$d')"
      exit 1
    fi
  fi

  log_info "Data source configured successfully"
}

# Import a single dashboard JSON file
import_dashboard() {
  local workspace_url="$1"
  local api_key="$2"
  local dashboard_file="$3"
  local datasource_name="$4"
  local folder_id="$5"
  local dry_run="$6"

  local filename
  filename=$(basename "$dashboard_file")

  if [[ ! -f "$dashboard_file" ]]; then
    log_warn "Dashboard file not found: ${dashboard_file}"
    return 1
  fi

  # Validate JSON
  if ! jq empty "$dashboard_file" 2>/dev/null; then
    log_error "Invalid JSON in: ${dashboard_file}"
    return 1
  fi

  # Read dashboard JSON and patch data source references
  local dashboard_json
  dashboard_json=$(jq --arg ds "$datasource_name" '
    # Replace all datasource references with AMP data source
    walk(
      if type == "object" and has("datasource") then
        if .datasource | type == "string" then
          .datasource = $ds
        elif .datasource | type == "object" then
          .datasource.type = "prometheus" | .datasource.uid = "${DS_PROMETHEUS}"
        else .
        end
      else .
      end
    ) |
    # Remove dashboard ID to allow import as new
    del(.id) |
    # Set null UID to auto-generate
    .uid = null
  ' "$dashboard_file")

  if [[ "$dry_run" == "true" ]]; then
    log_info "[DRY-RUN] Would import: ${filename}"
    return 0
  fi

  # Wrap in import payload
  local import_payload
  import_payload=$(jq -n \
    --argjson dashboard "$dashboard_json" \
    --arg folder_id "$folder_id" \
    '{
      "dashboard": $dashboard,
      "folderId": ($folder_id | tonumber),
      "overwrite": true,
      "message": "Imported by import-dashboards.sh"
    }')

  # Import via Grafana API
  local response
  response=$(curl -s -w "\n%{http_code}" \
    -H "Authorization: Bearer ${api_key}" \
    -H "Content-Type: application/json" \
    -d "$import_payload" \
    "${workspace_url}/api/dashboards/db")

  local http_code
  http_code=$(echo "$response" | tail -1)
  local response_body
  response_body=$(echo "$response" | sed '$d')

  if [[ "$http_code" == "200" ]]; then
    local dashboard_uid
    dashboard_uid=$(echo "$response_body" | jq -r '.uid // empty')
    local dashboard_url
    dashboard_url=$(echo "$response_body" | jq -r '.url // empty')
    log_info "Imported: ${filename} (uid: ${dashboard_uid})"
    echo "$response_body"
    return 0
  else
    log_error "Failed to import ${filename}. HTTP ${http_code}"
    log_error "Response: ${response_body}"
    return 1
  fi
}

# Create a folder in Grafana for organizing dashboards
create_folder() {
  local workspace_url="$1"
  local api_key="$2"
  local folder_title="$3"

  # Check if folder exists
  local folders_response
  folders_response=$(curl -s \
    -H "Authorization: Bearer ${api_key}" \
    "${workspace_url}/api/folders")

  local existing_id
  existing_id=$(echo "$folders_response" | jq -r --arg title "$folder_title" \
    '.[] | select(.title == $title) | .id // empty')

  if [[ -n "$existing_id" ]]; then
    log_info "Folder '${folder_title}' already exists (ID: ${existing_id})"
    echo "$existing_id"
    return 0
  fi

  # Create folder
  local create_response
  create_response=$(curl -s \
    -H "Authorization: Bearer ${api_key}" \
    -H "Content-Type: application/json" \
    -d "{\"title\": \"${folder_title}\"}" \
    "${workspace_url}/api/folders")

  local folder_id
  folder_id=$(echo "$create_response" | jq -r '.id // empty')

  if [[ -z "$folder_id" ]]; then
    log_error "Failed to create folder: ${folder_title}"
    log_error "Response: ${create_response}"
    exit 1
  fi

  log_info "Created folder '${folder_title}' (ID: ${folder_id})"
  echo "$folder_id"
}

# Generate panel embed URLs for ToolJet integration
generate_embed_urls() {
  local workspace_url="$1"
  local api_key="$2"
  local output_file="$3"

  log_info "Generating panel embed URLs for ToolJet integration..."

  # Get all dashboards
  local dashboards_response
  dashboards_response=$(curl -s \
    -H "Authorization: Bearer ${api_key}" \
    "${workspace_url}/api/search?type=dash-db")

  local embed_urls="[]"

  # For each dashboard, get panels and generate embed URLs
  while IFS= read -r dashboard; do
    local uid title
    uid=$(echo "$dashboard" | jq -r '.uid')
    title=$(echo "$dashboard" | jq -r '.title')

    if [[ -z "$uid" || "$uid" == "null" ]]; then
      continue
    fi

    # Get dashboard details
    local detail_response
    detail_response=$(curl -s \
      -H "Authorization: Bearer ${api_key}" \
      "${workspace_url}/api/dashboards/uid/${uid}")

    # Extract panels and generate embed URLs
    local panels
    panels=$(echo "$detail_response" | jq -r --arg url "$workspace_url" --arg uid "$uid" --arg title "$title" '
      [.dashboard.panels // [] | to_entries[] |
      {
        "dashboard_title": $title,
        "dashboard_uid": $uid,
        "panel_id": .value.id,
        "panel_title": .value.title,
        "embed_url": "\($url)/d-solo/\($uid)?orgId=1&panelId=\(.value.id)&from=now-1h&to=now&refresh=1m",
        "iframe_html": "<iframe src=\"\($url)/d-solo/\($uid)?orgId=1&panelId=\(.value.id)&from=now-1h&to=now&refresh=1m\" width=\"100%\" height=\"300\" frameborder=\"0\"></iframe>"
      }]
    ' 2>/dev/null || echo "[]")

    if [[ "$panels" != "[]" && -n "$panels" ]]; then
      embed_urls=$(echo "$embed_urls" | jq --argjson new_panels "$panels" '. + $new_panels')
    fi

  done < <(echo "$dashboards_response" | jq -c '.[]')

  # Write output file
  local output
  output=$(jq -n \
    --arg workspace_url "$workspace_url" \
    --arg generated_at "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --argjson panels "$embed_urls" \
    '{
      "workspace_url": $workspace_url,
      "generated_at": $generated_at,
      "embed_url_format": "<workspace_url>/d-solo/<dashboard_uid>?orgId=1&panelId=<panel_id>&from=now-1h&to=now&refresh=1m",
      "tooljet_iframe_template": "<iframe src=\"{embed_url}\" width=\"100%\" height=\"300\" frameborder=\"0\"></iframe>",
      "panels": $panels
    }')

  echo "$output" | jq '.' > "$output_file"
  log_info "Panel embed URLs written to: ${output_file}"
  log_info "Total panels documented: $(echo "$output" | jq '.panels | length')"
}

# --- Main ---

main() {
  local workspace_url=""
  local api_key=""
  local amp_workspace_id=""
  local dashboard_dir="$DEFAULT_DASHBOARD_DIR"
  local datasource_name="$DEFAULT_DATASOURCE_NAME"
  local output_file="$DEFAULT_OUTPUT_FILE"
  local region="$DEFAULT_REGION"
  local dry_run="false"

  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --workspace-url)
        workspace_url="$2"
        shift 2
        ;;
      --api-key)
        api_key="$2"
        shift 2
        ;;
      --amp-workspace-id)
        amp_workspace_id="$2"
        shift 2
        ;;
      --dashboard-dir)
        dashboard_dir="$2"
        shift 2
        ;;
      --datasource-name)
        datasource_name="$2"
        shift 2
        ;;
      --output-file)
        output_file="$2"
        shift 2
        ;;
      --region)
        region="$2"
        shift 2
        ;;
      --dry-run)
        dry_run="true"
        shift
        ;;
      --help|-h)
        usage
        ;;
      *)
        log_error "Unknown option: $1"
        usage
        ;;
    esac
  done

  # Validate required parameters
  if [[ -z "$workspace_url" ]]; then
    log_error "Missing required parameter: --workspace-url"
    usage
  fi
  if [[ -z "$api_key" ]]; then
    log_error "Missing required parameter: --api-key"
    usage
  fi

  validate_url "$workspace_url"
  # Remove trailing slash from URL
  workspace_url="${workspace_url%/}"

  check_dependencies

  # Resolve AMP workspace ID if not provided
  if [[ -z "$amp_workspace_id" ]]; then
    log_info "Resolving AMP workspace ID from CloudFormation stack output..."
    amp_workspace_id=$(aws cloudformation describe-stacks \
      --stack-name fsxn-mgmt-observability \
      --region "$region" \
      --query "Stacks[0].Outputs[?OutputKey=='AmpWorkspaceId'].OutputValue" \
      --output text 2>/dev/null || true)

    if [[ -z "$amp_workspace_id" ]]; then
      log_error "Could not resolve AMP workspace ID. Provide --amp-workspace-id or deploy fsxn-mgmt-observability stack first."
      exit 1
    fi
    log_info "Resolved AMP workspace ID: ${amp_workspace_id}"
  fi

  # Validate dashboard directory
  if [[ ! -d "$dashboard_dir" ]]; then
    log_error "Dashboard directory not found: ${dashboard_dir}"
    log_info "Download Harvest dashboards first. See: management-console/harvest/dashboards/README.md"
    exit 1
  fi

  # Count available dashboards
  local dashboard_count
  dashboard_count=$(find "$dashboard_dir" -name "*.json" -type f | wc -l | tr -d ' ')
  log_info "Found ${dashboard_count} dashboard JSON files in ${dashboard_dir}"

  if [[ "$dashboard_count" -lt "$MIN_DASHBOARDS" ]]; then
    log_warn "Found ${dashboard_count} dashboards, minimum recommended is ${MIN_DASHBOARDS}"
    log_warn "Download additional dashboards from Harvest GitHub repo"
  fi

  # Step 1: Configure AMP data source
  if [[ "$dry_run" == "false" ]]; then
    configure_datasource "$workspace_url" "$api_key" "$datasource_name" "$amp_workspace_id" "$region"
  else
    log_info "[DRY-RUN] Would configure AMP data source: ${datasource_name}"
  fi

  # Step 2: Create folders for each category
  local folder_id_volume_performance=""
  local folder_id_aggregate_utilization=""
  local folder_id_svm_health=""
  local folder_id_network_interfaces=""

  for category in $CATEGORIES; do
    local folder_title
    folder_title="FSx for ONTAP - $(echo "$category" | sed 's/_/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"

    if [[ "$dry_run" == "false" ]]; then
      local fid
      fid=$(create_folder "$workspace_url" "$api_key" "$folder_title")
      eval "folder_id_${category}=\"${fid}\""
    else
      log_info "[DRY-RUN] Would create folder: ${folder_title}"
      eval "folder_id_${category}=\"0\""
    fi
  done

  # Step 3: Import dashboards by category
  local imported=0
  local failed=0

  for category in $CATEGORIES; do
    log_info "--- Importing category: ${category} ---"
    local dashboards
    dashboards=$(get_dashboards_for_category "$category")
    local folder_id
    eval "folder_id=\"\$folder_id_${category}\""

    for dashboard_file in $dashboards; do
      local full_path="${dashboard_dir}/${dashboard_file}"

      if import_dashboard "$workspace_url" "$api_key" "$full_path" "$datasource_name" "$folder_id" "$dry_run"; then
        imported=$((imported + 1))
      else
        failed=$((failed + 1))
      fi
    done
  done

  log_info "=== Import Summary ==="
  log_info "Imported: ${imported}"
  log_info "Failed: ${failed}"
  log_info "Total: $((imported + failed))"

  # Step 4: Generate panel embed URLs for ToolJet
  if [[ "$dry_run" == "false" && "$imported" -gt 0 ]]; then
    generate_embed_urls "$workspace_url" "$api_key" "$output_file"
  fi

  # Final status
  if [[ "$failed" -gt 0 ]]; then
    log_warn "Some dashboards failed to import. Check logs above."
    exit 1
  fi

  log_info "Dashboard import complete!"
  if [[ "$dry_run" == "false" ]]; then
    log_info "Panel embed URLs: ${output_file}"
    log_info ""
    log_info "Next steps:"
    log_info "  1. Verify dashboards in AMG: ${workspace_url}/dashboards"
    log_info "  2. Configure ToolJet iframe components using URLs in: ${output_file}"
    log_info "  3. Test embedded panels load within 10 seconds"
  fi
}

main "$@"
