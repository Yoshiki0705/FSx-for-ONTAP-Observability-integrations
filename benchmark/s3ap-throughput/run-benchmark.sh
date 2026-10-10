#!/bin/bash
set -euo pipefail

# FSx for ONTAP S3 Access Points throughput benchmark — invoke helper.
#
# Invokes the deployed benchmark Lambda for the "list" and "get" tests and
# writes the raw JSON responses to an operator-named output file. This script
# makes AWS calls (aws lambda invoke) and is intended to be run by an operator
# against an already-deployed stack. It contains no account IDs, ARNs, or IPs;
# every environment-specific value comes from an environment variable.
#
# Required environment variables:
#   FUNCTION_NAME   Name (or ARN) of the deployed benchmark Lambda.
#   S3AP            S3 Access Points alias or ARN to benchmark (the Lambda's
#                   "bucket" parameter; both forms work).
#
# Optional environment variables:
#   AWS_REGION      AWS Region (default: ap-northeast-1).
#   PREFIX          Key prefix to list/read under (default: empty — whole AP).
#   LIST_ITERATIONS ListObjectsV2 repetitions (default: 20).
#   GET_ITERATIONS  GetObject repetitions per object (default: 5).
#   MAX_KEYS        Max objects to GetObject-benchmark (default: 10).
#   BENCHMARK_RUN_ID  Run identifier echoed into the result (default: unset,
#                   the Lambda generates one).
#   OUT_FILE        Output file for the combined JSON (default:
#                   s3ap-benchmark-<timestamp>.json in the current directory).
#
# Usage:
#   FUNCTION_NAME=fsxn-s3ap-benchmark-fn \
#   S3AP=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
#   PREFIX=audit/svm-prod-01/2026/05/ \
#     ./run-benchmark.sh

REGION="${AWS_REGION:-ap-northeast-1}"
PREFIX="${PREFIX:-}"
LIST_ITERATIONS="${LIST_ITERATIONS:-20}"
GET_ITERATIONS="${GET_ITERATIONS:-5}"
MAX_KEYS="${MAX_KEYS:-10}"
OUT_FILE="${OUT_FILE:-s3ap-benchmark-$(date -u +%Y%m%dT%H%M%SZ).json}"

if [[ -z "${FUNCTION_NAME:-}" ]]; then
  echo "Error: set FUNCTION_NAME to the deployed benchmark Lambda name/ARN." >&2
  exit 2
fi
if [[ -z "${S3AP:-}" ]]; then
  echo "Error: set S3AP to the S3 Access Points alias or ARN to benchmark." >&2
  exit 2
fi

RUN_ID_FIELD=""
if [[ -n "${BENCHMARK_RUN_ID:-}" ]]; then
  RUN_ID_FIELD=",\"benchmark_run_id\":\"${BENCHMARK_RUN_ID}\""
fi

LIST_PAYLOAD="{\"test\":\"list\",\"bucket\":\"${S3AP}\",\"prefix\":\"${PREFIX}\",\"iterations\":${LIST_ITERATIONS}${RUN_ID_FIELD}}"
GET_PAYLOAD="{\"test\":\"get\",\"bucket\":\"${S3AP}\",\"prefix\":\"${PREFIX}\",\"max_keys\":${MAX_KEYS},\"iterations\":${GET_ITERATIONS}${RUN_ID_FIELD}}"

LIST_RESP="$(mktemp)"
GET_RESP="$(mktemp)"
trap 'rm -f "${LIST_RESP}" "${GET_RESP}"' EXIT

echo "Running ListObjectsV2 benchmark (${LIST_ITERATIONS} iterations)..."
aws lambda invoke \
  --function-name "${FUNCTION_NAME}" \
  --region "${REGION}" \
  --cli-binary-format raw-in-base64-out \
  --payload "${LIST_PAYLOAD}" \
  "${LIST_RESP}" > /dev/null

echo "Running GetObject benchmark (${MAX_KEYS} keys x ${GET_ITERATIONS} iterations)..."
aws lambda invoke \
  --function-name "${FUNCTION_NAME}" \
  --region "${REGION}" \
  --cli-binary-format raw-in-base64-out \
  --payload "${GET_PAYLOAD}" \
  "${GET_RESP}" > /dev/null

# Combine the two raw responses under one object so the output carries both
# tests and the operator can diff runs without re-pairing files.
python3 - "${LIST_RESP}" "${GET_RESP}" "${OUT_FILE}" <<'PY'
import json
import sys

list_path, get_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(list_path, encoding="utf-8") as fh:
    list_result = json.load(fh)
with open(get_path, encoding="utf-8") as fh:
    get_result = json.load(fh)
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump({"list": list_result, "get": get_result}, fh, indent=2)
    fh.write("\n")
PY

echo "Wrote combined results to ${OUT_FILE}"
