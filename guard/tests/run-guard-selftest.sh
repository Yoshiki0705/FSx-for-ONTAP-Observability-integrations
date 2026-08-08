#!/usr/bin/env bash
# Self-test for the cfn-guard rule files in guard/rules/.
#
# Why this exists: `cfn-guard validate` exits 0 when a rule file fails to parse,
# and a rule that selects nothing reports no findings. Both failure modes are
# indistinguishable from "everything is compliant", and both happened here --
# two rule files were unparseable and five individual rules silently matched
# nothing, including one in the blocking rule set.
#
# Three checks, each targeting one of those failure modes:
#
#   1. parse     every rule file loads without a parsing error
#   2. negative  every rule declared in every file reports a finding against
#                guard/tests/negative-control.yaml
#   3. positive  no rule reports a finding against
#                guard/tests/positive-control.yaml
#
# Check 2 is what catches a rule that has stopped selecting anything. Check 3 is
# what catches a rule so broad that its findings get dismissed by habit.
#
# Exit codes follow sysexits.h: 0 pass, 69 a check failed, 78 cfn-guard missing.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RULES_DIR="${REPO_ROOT}/guard/rules"
PARSE_PROBE="${SCRIPT_DIR}/parse-probe.yaml"
NEGATIVE="${SCRIPT_DIR}/negative-control.yaml"
POSITIVE="${SCRIPT_DIR}/positive-control.yaml"

if ! command -v cfn-guard >/dev/null 2>&1; then
  echo "cfn-guard not found on PATH."
  echo "Install: https://github.com/aws-cloudformation/cloudformation-guard"
  exit 78
fi

FAILURES=0

note_failure() {
  echo "  FAIL: $1"
  FAILURES=$((FAILURES + 1))
}

echo "cfn-guard $(cfn-guard --version 2>&1 | head -1)"
echo

# ---------------------------------------------------------------------------
# 1. Every rule file parses.
# ---------------------------------------------------------------------------
echo "[1/3] rule files parse"
for rule_file in "${RULES_DIR}"/*.guard; do
  name="$(basename "${rule_file}")"
  output="$(cfn-guard validate -d "${PARSE_PROBE}" -r "${rule_file}" 2>&1)"
  if printf '%s' "${output}" | grep -q "Parsing error"; then
    note_failure "${name} does not parse"
    printf '%s\n' "${output}" | grep -A2 "Parsing error" | sed 's/^/        /'
  else
    echo "  ok: ${name}"
  fi
done
echo

# ---------------------------------------------------------------------------
# 2. Every declared rule fires against the non-compliant fixture.
# ---------------------------------------------------------------------------
echo "[2/3] every rule fires on negative-control.yaml"
for rule_file in "${RULES_DIR}"/*.guard; do
  name="$(basename "${rule_file}")"

  # Rule names as declared in the file: `rule <name> ...`, ignoring comments.
  declared="$(grep -oE '^rule[[:space:]]+[A-Za-z0-9_]+' "${rule_file}" \
    | awk '{print $2}' | sort -u)"
  if [ -z "${declared}" ]; then
    note_failure "${name} declares no rules"
    continue
  fi

  fired="$(cfn-guard validate -d "${NEGATIVE}" -r "${rule_file}" 2>&1 \
    | grep -oE "${name}/[A-Za-z0-9_]+[[:space:]]+FAIL" \
    | sed -E "s|^${name}/||; s|[[:space:]]+FAIL$||" | sort -u)"

  while IFS= read -r rule; do
    [ -z "${rule}" ] && continue
    if printf '%s\n' "${fired}" | grep -qx "${rule}"; then
      echo "  ok: ${name}/${rule}"
    else
      note_failure "${name}/${rule} reported nothing against the non-compliant fixture"
      echo "        Either the rule selects nothing (check the filter syntax), or"
      echo "        negative-control.yaml is missing the violation it looks for."
    fi
  done <<< "${declared}"
done
echo

# ---------------------------------------------------------------------------
# 3. No rule fires against the compliant fixture.
# ---------------------------------------------------------------------------
echo "[3/3] no rule fires on positive-control.yaml"
for rule_file in "${RULES_DIR}"/*.guard; do
  name="$(basename "${rule_file}")"
  output="$(cfn-guard validate -d "${POSITIVE}" -r "${rule_file}" 2>&1)"
  unexpected="$(printf '%s' "${output}" \
    | grep -oE "${name}/[A-Za-z0-9_]+[[:space:]]+FAIL" \
    | sed -E "s|^${name}/||; s|[[:space:]]+FAIL$||" | sort -u)"
  if [ -n "${unexpected}" ]; then
    while IFS= read -r rule; do
      [ -z "${rule}" ] && continue
      note_failure "${name}/${rule} fired on the compliant fixture"
    done <<< "${unexpected}"
    printf '%s\n' "${output}" | grep -E "PropertyPath" | sed 's/^/        /' | head -10
  else
    echo "  ok: ${name}"
  fi
done
echo

if [ "${FAILURES}" -gt 0 ]; then
  echo "guard self-test: ${FAILURES} failure(s)"
  exit 69
fi

echo "guard self-test: all checks passed"
