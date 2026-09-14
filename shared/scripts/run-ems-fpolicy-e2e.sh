#!/bin/bash
set -euo pipefail

# =============================================================================
# EMS/FPolicy E2E Verification — Master Orchestrator Script
# =============================================================================
#
# Orchestrates the full end-to-end verification sequence for EMS Webhook and
# FPolicy External Engine event delivery paths.
#
# Execution order:
#   1. cfn-lint validation (both templates)
#   2. ARP ransomware detection E2E test
#   3. Quota threshold exceeded E2E test
#   4. FPolicy External Engine E2E test
#   5. event-sources.md documentation verification
#   6. Bilingual code block comparison (ja/en)
#   7. Generate verification results document
#
# Exit codes:
#   0 — All steps passed
#   1 — One or more steps failed
#
# Usage:
#   ./run-ems-fpolicy-e2e.sh \
#     --region ap-northeast-1 \
#     --stack-ems fsxn-ems-webhook \
#     --stack-fpolicy fsxn-fp-srv \
#     --svm-name svm-prod-01 \
#     --output ./results
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# =============================================================================
# Default values
# =============================================================================
REGION="${AWS_DEFAULT_REGION:-ap-northeast-1}"
STACK_EMS=""
STACK_FPOLICY=""
SVM_NAME=""
OUTPUT_DIR="./e2e-results"

# =============================================================================
# Result tracking
# =============================================================================
declare -a STEP_NAMES=()
declare -a STEP_RESULTS=()
TOTAL_PASS=0
TOTAL_FAIL=0

# =============================================================================
# Usage / Help
# =============================================================================
usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Orchestrate the full EMS/FPolicy E2E verification sequence.

Options:
  --region          AWS region (default: ap-northeast-1 or \$AWS_DEFAULT_REGION)
  --stack-ems       CloudFormation stack name for EMS Webhook
  --stack-fpolicy   CloudFormation stack name for FPolicy APIGW
  --svm-name        FSx for ONTAP SVM name
  --output          Output directory for results (default: ./e2e-results)
  --help            Show this help message and exit

Execution Order:
  1. cfn-lint validation (ems-webhook-apigw.yaml, fpolicy-apigw.yaml)
  2. ARP ransomware detection E2E test
  3. Quota threshold exceeded E2E test
  4. FPolicy External Engine E2E test
  5. event-sources.md documentation verification
  6. Bilingual code block comparison (ja/en)
  7. Generate verification results document

Examples:
  $(basename "$0") \\
    --region ap-northeast-1 \\
    --stack-ems fsxn-ems-webhook \\
    --stack-fpolicy fsxn-fp-srv \\
    --svm-name svm-prod-01 \\
    --output ./results

  $(basename "$0") --help
EOF
}

# =============================================================================
# Utility functions
# =============================================================================
timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

print_header() {
  local step_num="$1"
  local step_name="$2"
  echo ""
  echo "========================================================================"
  echo "[$(timestamp)] Step ${step_num}: ${step_name}"
  echo "========================================================================"
}

record_result() {
  local step_name="$1"
  local result="$2"  # PASS or FAIL

  STEP_NAMES+=("$step_name")
  STEP_RESULTS+=("$result")

  if [[ "$result" == "PASS" ]]; then
    TOTAL_PASS=$((TOTAL_PASS + 1))
    echo "[$(timestamp)] ✓ PASS: ${step_name}"
  else
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
    echo "[$(timestamp)] ✗ FAIL: ${step_name}"
  fi
}

print_summary() {
  local total=$((TOTAL_PASS + TOTAL_FAIL))
  echo ""
  echo "========================================================================"
  echo "[$(timestamp)] E2E VERIFICATION SUMMARY"
  echo "========================================================================"
  echo ""
  echo "  Total steps:  ${total}"
  echo "  Passed:       ${TOTAL_PASS}"
  echo "  Failed:       ${TOTAL_FAIL}"
  echo ""

  for i in "${!STEP_NAMES[@]}"; do
    local mark
    if [[ "${STEP_RESULTS[$i]}" == "PASS" ]]; then
      mark="✓"
    else
      mark="✗"
    fi
    echo "  [${mark}] ${STEP_NAMES[$i]}"
  done

  echo ""
  if [[ $TOTAL_FAIL -eq 0 ]]; then
    echo "  Overall: ALL PASS (合格)"
  else
    echo "  Overall: FAIL (不合格) — ${TOTAL_FAIL} step(s) failed"
  fi
  echo "========================================================================"
}

# =============================================================================
# Parse arguments
# =============================================================================
while [[ $# -gt 0 ]]; do
  case $1 in
    --region)
      REGION="$2"
      shift 2
      ;;
    --stack-ems)
      STACK_EMS="$2"
      shift 2
      ;;
    --stack-fpolicy)
      STACK_FPOLICY="$2"
      shift 2
      ;;
    --svm-name)
      SVM_NAME="$2"
      shift 2
      ;;
    --output)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Error: Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

# =============================================================================
# Validate required parameters
# =============================================================================
if [[ -z "$STACK_EMS" || -z "$STACK_FPOLICY" || -z "$SVM_NAME" ]]; then
  echo "Error: --stack-ems, --stack-fpolicy, and --svm-name are required."
  echo ""
  usage
  exit 1
fi

# =============================================================================
# Setup
# =============================================================================
mkdir -p "$OUTPUT_DIR"

echo "========================================================================"
echo "[$(timestamp)] EMS/FPolicy E2E Verification — Starting"
echo "========================================================================"
echo ""
echo "  Region:         ${REGION}"
echo "  EMS Stack:      ${STACK_EMS}"
echo "  FPolicy Stack:  ${STACK_FPOLICY}"
echo "  SVM Name:       ${SVM_NAME}"
echo "  Output Dir:     ${OUTPUT_DIR}"
echo "  Project Root:   ${PROJECT_ROOT}"
echo ""

# Template paths
EMS_TEMPLATE="${PROJECT_ROOT}/shared/templates/ems-webhook-apigw.yaml"
FPOLICY_TEMPLATE="${PROJECT_ROOT}/shared/templates/fpolicy-apigw.yaml"

# =============================================================================
# Step 1: cfn-lint validation
# =============================================================================
print_header "1" "cfn-lint CloudFormation Template Validation"

# Check cfn-lint is available
if ! command -v cfn-lint &> /dev/null; then
  echo "  ERROR: cfn-lint is not installed. Install with: pip install cfn-lint"
  record_result "cfn-lint validation" "FAIL"
else
  cfn_lint_version=$(cfn-lint --version 2>&1 || true)
  echo "  cfn-lint version: ${cfn_lint_version}"
  echo ""

  step1_pass=true

  # Validate EMS Webhook template
  echo "  Validating: ${EMS_TEMPLATE}"
  if cfn-lint "$EMS_TEMPLATE" > "${OUTPUT_DIR}/cfn-lint-ems.txt" 2>&1; then
    echo "    ✓ ems-webhook-apigw.yaml — PASS (0 errors, 0 warnings)"
  else
    echo "    ✗ ems-webhook-apigw.yaml — FAIL"
    echo "    Output:"
    sed 's/^/      /' "${OUTPUT_DIR}/cfn-lint-ems.txt"
    step1_pass=false
  fi

  # Validate FPolicy template
  echo "  Validating: ${FPOLICY_TEMPLATE}"
  if cfn-lint "$FPOLICY_TEMPLATE" > "${OUTPUT_DIR}/cfn-lint-fpolicy.txt" 2>&1; then
    echo "    ✓ fpolicy-apigw.yaml — PASS (0 errors, 0 warnings)"
  else
    echo "    ✗ fpolicy-apigw.yaml — FAIL"
    echo "    Output:"
    sed 's/^/      /' "${OUTPUT_DIR}/cfn-lint-fpolicy.txt"
    step1_pass=false
  fi

  if [[ "$step1_pass" == true ]]; then
    record_result "cfn-lint validation" "PASS"
  else
    record_result "cfn-lint validation" "FAIL"
  fi
fi

# =============================================================================
# Step 2: ARP Ransomware Detection E2E Test
# =============================================================================
print_header "2" "ARP Ransomware Detection E2E Test"

ARP_SCRIPT="${SCRIPT_DIR}/e2e-test-arp.py"

if [[ ! -f "$ARP_SCRIPT" ]]; then
  echo "  ERROR: ARP test script not found: ${ARP_SCRIPT}"
  record_result "ARP E2E test" "FAIL"
else
  echo "  Script: ${ARP_SCRIPT}"
  echo "  NOTE: This test requires manual ONTAP CLI execution."
  echo "        Run the following command via SSH before or during this step:"
  echo ""
  echo "    security anti-ransomware volume attack simulate -vserver ${SVM_NAME} -volume <vol>"
  echo ""

  # Retrieve Lambda log group from CloudFormation stack outputs
  EMS_LOG_GROUP=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_EMS" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='LambdaLogGroupName'].OutputValue" \
    --output text 2>/dev/null || echo "")

  if [[ -z "$EMS_LOG_GROUP" || "$EMS_LOG_GROUP" == "None" ]]; then
    EMS_LOG_GROUP="/aws/lambda/fsxn-ems-receiver"
    echo "  WARNING: Could not retrieve log group from stack outputs."
    echo "           Using default: ${EMS_LOG_GROUP}"
  fi

  if python3 "$ARP_SCRIPT" \
    --region "$REGION" \
    --log-group "$EMS_LOG_GROUP" \
    --svm-name "$SVM_NAME" \
    --volume-name "vol1" \
    --management-ip "0.0.0.0" \
    > "${OUTPUT_DIR}/e2e-arp-results.txt" 2>&1; then
    record_result "ARP E2E test" "PASS"
  else
    echo "  See details: ${OUTPUT_DIR}/e2e-arp-results.txt"
    record_result "ARP E2E test" "FAIL"
  fi
fi

# =============================================================================
# Step 3: Quota Threshold Exceeded E2E Test
# =============================================================================
print_header "3" "Quota Threshold Exceeded E2E Test"

QUOTA_SCRIPT="${SCRIPT_DIR}/e2e-test-quota.py"

if [[ ! -f "$QUOTA_SCRIPT" ]]; then
  echo "  ERROR: Quota test script not found: ${QUOTA_SCRIPT}"
  record_result "Quota E2E test" "FAIL"
else
  echo "  Script: ${QUOTA_SCRIPT}"
  echo "  NOTE: This test requires manual ONTAP CLI execution."
  echo "        Configure quota and write data exceeding soft limit."
  echo ""

  if [[ -z "$EMS_LOG_GROUP" || "$EMS_LOG_GROUP" == "None" ]]; then
    EMS_LOG_GROUP="/aws/lambda/fsxn-ems-receiver"
  fi

  if python3 "$QUOTA_SCRIPT" \
    --region "$REGION" \
    --log-group "$EMS_LOG_GROUP" \
    --svm-name "$SVM_NAME" \
    --volume-name "vol_test_quota" \
    > "${OUTPUT_DIR}/e2e-quota-results.txt" 2>&1; then
    record_result "Quota E2E test" "PASS"
  else
    echo "  See details: ${OUTPUT_DIR}/e2e-quota-results.txt"
    record_result "Quota E2E test" "FAIL"
  fi
fi

# =============================================================================
# Step 4: FPolicy External Engine E2E Test
# =============================================================================
print_header "4" "FPolicy External Engine E2E Test"

FPOLICY_SCRIPT="${SCRIPT_DIR}/e2e_test_fpolicy.py"

if [[ ! -f "$FPOLICY_SCRIPT" ]]; then
  echo "  ERROR: FPolicy test script not found: ${FPOLICY_SCRIPT}"
  record_result "FPolicy E2E test" "FAIL"
else
  echo "  Script: ${FPOLICY_SCRIPT}"
  echo "  NOTE: This test requires FPolicy configured on the SVM."
  echo "  Architecture: ONTAP → TCP:9898 → ECS Fargate → SQS → EventBridge"
  echo ""

  # Retrieve ECS cluster and service info from stack outputs
  FPOLICY_ECS_LOG_GROUP=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_FPOLICY" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='FPolicyServerLogGroupName'].OutputValue" \
    --output text 2>/dev/null || echo "")

  FPOLICY_CLUSTER_NAME=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_FPOLICY" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" \
    --output text 2>/dev/null || echo "")

  FPOLICY_SERVICE_NAME=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_FPOLICY" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ServiceName'].OutputValue" \
    --output text 2>/dev/null || echo "")

  if [[ -z "$FPOLICY_ECS_LOG_GROUP" || "$FPOLICY_ECS_LOG_GROUP" == "None" ]]; then
    FPOLICY_ECS_LOG_GROUP="/ecs/${STACK_FPOLICY}-fpolicy-server"
    echo "  WARNING: Could not retrieve ECS log group from stack outputs."
    echo "           Using default: ${FPOLICY_ECS_LOG_GROUP}"
  fi

  if [[ -z "$FPOLICY_CLUSTER_NAME" || "$FPOLICY_CLUSTER_NAME" == "None" ]]; then
    FPOLICY_CLUSTER_NAME="${STACK_FPOLICY}-fpolicy"
    echo "  WARNING: Could not retrieve ECS cluster name from stack outputs."
    echo "           Using default: ${FPOLICY_CLUSTER_NAME}"
  fi

  if [[ -z "$FPOLICY_SERVICE_NAME" || "$FPOLICY_SERVICE_NAME" == "None" ]]; then
    FPOLICY_SERVICE_NAME="${STACK_FPOLICY}-fpolicy-server"
    echo "  WARNING: Could not retrieve ECS service name from stack outputs."
    echo "           Using default: ${FPOLICY_SERVICE_NAME}"
  fi

  if python3 "$FPOLICY_SCRIPT" \
    --region "$REGION" \
    --ecs-log-group "$FPOLICY_ECS_LOG_GROUP" \
    --cluster-name "$FPOLICY_CLUSTER_NAME" \
    --service-name "$FPOLICY_SERVICE_NAME" \
    --svm-name "$SVM_NAME" \
    > "${OUTPUT_DIR}/e2e-fpolicy-results.txt" 2>&1; then
    record_result "FPolicy E2E test" "PASS"
  else
    echo "  See details: ${OUTPUT_DIR}/e2e-fpolicy-results.txt"
    record_result "FPolicy E2E test" "FAIL"
  fi
fi

# =============================================================================
# Step 5: event-sources.md Documentation Verification
# =============================================================================
print_header "5" "event-sources.md Documentation Verification"

VERIFY_SCRIPT="${SCRIPT_DIR}/verify-event-sources.py"
DOCS_DIR="${PROJECT_ROOT}/docs"

if [[ ! -f "$VERIFY_SCRIPT" ]]; then
  echo "  ERROR: Verification script not found: ${VERIFY_SCRIPT}"
  record_result "event-sources.md verification" "FAIL"
else
  echo "  Script: ${VERIFY_SCRIPT}"
  echo "  Docs dir: ${DOCS_DIR}"
  echo ""

  if python3 "$VERIFY_SCRIPT" \
    --docs-dir "$DOCS_DIR" \
    --output "${OUTPUT_DIR}/verify-event-sources.json" \
    > "${OUTPUT_DIR}/verify-event-sources-stdout.txt" 2>&1; then
    record_result "event-sources.md verification" "PASS"
  else
    echo "  See details: ${OUTPUT_DIR}/verify-event-sources-stdout.txt"
    record_result "event-sources.md verification" "FAIL"
  fi
fi

# =============================================================================
# Step 6: Bilingual Code Block Comparison
# =============================================================================
print_header "6" "Bilingual Code Block Comparison (ja/en)"

JA_EVENT_SOURCES="${DOCS_DIR}/ja/event-sources.md"
EN_EVENT_SOURCES="${DOCS_DIR}/en/event-sources.md"

if [[ ! -f "$JA_EVENT_SOURCES" ]]; then
  echo "  ERROR: Japanese event-sources.md not found: ${JA_EVENT_SOURCES}"
  record_result "Bilingual comparison" "FAIL"
elif [[ ! -f "$EN_EVENT_SOURCES" ]]; then
  echo "  ERROR: English event-sources.md not found: ${EN_EVENT_SOURCES}"
  record_result "Bilingual comparison" "FAIL"
else
  echo "  Japanese: ${JA_EVENT_SOURCES}"
  echo "  English:  ${EN_EVENT_SOURCES}"
  echo ""

  # Use the verify-event-sources.py output to check bilingual comparison
  # The script already performs bilingual comparison as part of its report
  if [[ -f "${OUTPUT_DIR}/verify-event-sources.json" ]]; then
    bilingual_status=$(python3 -c "
import json, sys
try:
    with open('${OUTPUT_DIR}/verify-event-sources.json') as f:
        report = json.load(f)
    status = report.get('bilingual_comparison', {}).get('status', 'UNKNOWN')
    diffs = report.get('bilingual_comparison', {}).get('differences', [])
    print(f'Status: {status}')
    if diffs:
        print(f'Differences found: {len(diffs)}')
        for d in diffs[:5]:
            print(f'  Block {d[\"block_index\"]}: ja != en')
    sys.exit(0 if status == 'PASS' else 1)
except Exception as e:
    print(f'Error reading report: {e}')
    sys.exit(1)
" 2>&1) || bilingual_exit=$?
    bilingual_exit=${bilingual_exit:-0}

    echo "  ${bilingual_status}"

    if [[ $bilingual_exit -eq 0 ]]; then
      record_result "Bilingual comparison" "PASS"
    else
      record_result "Bilingual comparison" "FAIL"
    fi
  else
    echo "  WARNING: verify-event-sources.json not found. Running standalone comparison."

    # Fallback: run Python inline comparison
    if python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
from importlib.util import spec_from_file_location, module_from_spec
spec = spec_from_file_location('verify', '${VERIFY_SCRIPT}')
mod = module_from_spec(spec)
spec.loader.exec_module(mod)
diffs = mod.compare_code_blocks('${JA_EVENT_SOURCES}', '${EN_EVENT_SOURCES}')
if diffs:
    print(f'FAIL: {len(diffs)} code block difference(s) found')
    for d in diffs[:5]:
        print(f'  Block {d[\"block_index\"]}: content differs')
    sys.exit(1)
else:
    print('PASS: All code blocks match between ja and en')
    sys.exit(0)
" > "${OUTPUT_DIR}/bilingual-comparison.txt" 2>&1; then
      record_result "Bilingual comparison" "PASS"
    else
      echo "  See details: ${OUTPUT_DIR}/bilingual-comparison.txt"
      record_result "Bilingual comparison" "FAIL"
    fi
  fi
fi

# =============================================================================
# Step 7: Generate Verification Results Document
# =============================================================================
print_header "7" "Generate Verification Results Document"

RESULTS_DOC="${OUTPUT_DIR}/verification-results-ems-fpolicy.md"

echo "  Generating: ${RESULTS_DOC}"
echo ""

cat > "$RESULTS_DOC" <<EOF
# EMS/FPolicy E2E 動作確認結果

## 検証情報

| 項目 | 値 |
|------|-----|
| 検証日時 | $(date -u +"%Y-%m-%dT%H:%M:%SZ") |
| AWS リージョン | ${REGION} |
| SVM 名 | ${SVM_NAME} |
| EMS Webhook スタック | ${STACK_EMS} |
| FPolicy スタック | ${STACK_FPOLICY} |

## 検証結果サマリー

| ステップ | 結果 |
|----------|------|
EOF

for i in "${!STEP_NAMES[@]}"; do
  local_mark="PASS"
  if [[ "${STEP_RESULTS[$i]}" != "PASS" ]]; then
    local_mark="FAIL"
  fi
  echo "| ${STEP_NAMES[$i]} | ${local_mark} |" >> "$RESULTS_DOC"
done

cat >> "$RESULTS_DOC" <<EOF

## 総合判定

EOF

if [[ $TOTAL_FAIL -eq 0 ]]; then
  echo "**合格** — 全ステップ PASS" >> "$RESULTS_DOC"
else
  echo "**不合格** — ${TOTAL_FAIL} ステップが FAIL" >> "$RESULTS_DOC"
  echo "" >> "$RESULTS_DOC"
  echo "### 失敗ステップ" >> "$RESULTS_DOC"
  echo "" >> "$RESULTS_DOC"
  for i in "${!STEP_NAMES[@]}"; do
    if [[ "${STEP_RESULTS[$i]}" != "PASS" ]]; then
      echo "- ${STEP_NAMES[$i]}" >> "$RESULTS_DOC"
    fi
  done
fi

cat >> "$RESULTS_DOC" <<EOF

## 詳細ログ

各ステップの詳細出力は以下のファイルを参照:

- \`cfn-lint-ems.txt\` — cfn-lint EMS テンプレート検証結果
- \`cfn-lint-fpolicy.txt\` — cfn-lint FPolicy テンプレート検証結果
- \`e2e-arp-results.txt\` — ARP E2E テスト結果
- \`e2e-quota-results.txt\` — Quota E2E テスト結果
- \`e2e-fpolicy-results.txt\` — FPolicy E2E テスト結果
- \`verify-event-sources.json\` — event-sources.md 検証レポート (JSON)
- \`bilingual-comparison.txt\` — バイリンガル比較結果
EOF

echo "  ✓ Results document generated: ${RESULTS_DOC}"
record_result "Generate results document" "PASS"

# =============================================================================
# Final Summary
# =============================================================================
print_summary

# Exit with appropriate code
if [[ $TOTAL_FAIL -eq 0 ]]; then
  exit 0
else
  exit 1
fi
