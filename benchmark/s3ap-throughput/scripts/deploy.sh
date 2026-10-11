#!/bin/bash
# Bundle the S3 Access Points benchmark handler WITH the ONTAP audit parser and
# upload it to the deployed benchmark Lambda.
#
# Why this script exists
# ----------------------
# benchmark/s3ap-throughput/template.yaml inlines handler.py in Code.ZipFile and
# attaches no Layers, so the deployed function has no copy of
# shared/python/ontap_audit_parser.py. The "list" and "get" tests do not need
# it, but the "process" test calls parse_audit_log and so does. This script
# closes that gap the same way integrations/datadog/scripts/deploy.sh does for
# the audit shipper: it zips handler.py together with ontap_audit_parser.py and
# runs `aws lambda update-function-code`, overwriting the inline body with one
# that can import the parser.
#
# The inline body in the template keeps the parser import guarded, so the stack
# still deploys and loads with just the inline code; only the "process" test
# needs this upload to have run first. Deploy the stack
# (benchmark/s3ap-throughput/template.yaml) before running this.
#
# Prerequisites:
#   - AWS CLI v2 configured with Lambda permissions for the function
#   - The benchmark stack already deployed (default name: fsxn-s3ap-benchmark)
#
# Usage:
#   bash benchmark/s3ap-throughput/scripts/deploy.sh
#
# Override via environment variables:
#   AWS_REGION      AWS Region of the function (default ap-northeast-1)
#   STACK_NAME      Stack name; the function is ${STACK_NAME}-fn
#                   (default fsxn-s3ap-benchmark)
#   FUNCTION_NAME   Function name directly (default ${STACK_NAME}-fn)
set -euo pipefail

# SECURITY: never enable xtrace — resource identifiers would leak. Do not add
# 'set -x' to this script.

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
STACK_NAME="${STACK_NAME:-fsxn-s3ap-benchmark}"
FUNCTION_NAME="${FUNCTION_NAME:-${STACK_NAME}-fn}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_DIR="$(dirname "$SCRIPT_DIR")"
# benchmark/s3ap-throughput/scripts -> repo root is three levels up.
REPO_ROOT="$(cd "${TOOL_DIR}/../.." && pwd)"
SHARED_PYTHON_DIR="${REPO_ROOT}/shared/python"

HANDLER="${TOOL_DIR}/handler.py"
PARSER="${SHARED_PYTHON_DIR}/ontap_audit_parser.py"

echo "============================================================"
echo "FSx for ONTAP S3 Access Points benchmark — code upload"
echo "============================================================"
echo "Region:        ${AWS_REGION}"
echo "Function:      ${FUNCTION_NAME}"
echo "Bundled files: handler.py + ontap_audit_parser.py"
echo "============================================================"
echo ""

for f in "${HANDLER}" "${PARSER}"; do
  if [ ! -f "$f" ]; then
    echo "  ❌ expected source file not found: $f"
    exit 2
  fi
done

if ! aws lambda get-function --function-name "${FUNCTION_NAME}" \
     --region "${AWS_REGION}" >/dev/null 2>&1; then
  echo "  ❌ function ${FUNCTION_NAME} not found. Deploy the stack first:"
  echo "       aws cloudformation deploy --template-file \\"
  echo "         benchmark/s3ap-throughput/template.yaml \\"
  echo "         --stack-name ${STACK_NAME} --capabilities CAPABILITY_NAMED_IAM"
  exit 2
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

ZIP_PATH="${WORK_DIR}/benchmark-handler.zip"
# The template declares Handler: index.lambda_handler and CloudFormation writes
# the inline body to index.py, so the uploaded archive must also expose the
# handler as `index`. Copy handler.py to index.py, then zip index.py together
# with the parser at the archive root (`index` + `ontap_audit_parser`).
cp "${HANDLER}" "${WORK_DIR}/index.py"
zip -q -j "${ZIP_PATH}" "${WORK_DIR}/index.py" "${PARSER}"

aws lambda update-function-code \
  --function-name "${FUNCTION_NAME}" \
  --zip-file "fileb://${ZIP_PATH}" \
  --region "${AWS_REGION}" >/dev/null

# Block until the new code is active so a "process" invocation right after this
# does not still hit the inline body without the parser.
aws lambda wait function-updated \
  --function-name "${FUNCTION_NAME}" \
  --region "${AWS_REGION}"

echo "  ✅ Uploaded handler.py + ontap_audit_parser.py to ${FUNCTION_NAME}"
echo ""
echo "The 'process' test can now run; the inline template body is overwritten."
echo "Invoke it with benchmark/s3ap-throughput/run-benchmark.sh."
