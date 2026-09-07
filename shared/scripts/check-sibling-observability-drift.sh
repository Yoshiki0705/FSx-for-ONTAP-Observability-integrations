#!/usr/bin/env bash
#
# Report drift between this repository's instrumentation module and the copy in
# fsxn-s3ap-serverless-patterns.
#
# Why a script and not a submodule or a subtree: the two repositories are
# published separately and a reader of either should not need the other checked
# out to use it. What actually needs guarding is narrower than "keep the trees in
# sync" -- it is that the *contract* the two halves of the correlation agree on
# does not diverge silently. A join key derived slightly differently on each side
# does not fail; it silently never matches.
#
# Not wired into CI. CI here has no access to the sibling repository, and a check
# that cannot run is worse than one that is run deliberately: it reports success
# by not executing. Run it when either side's instrumentation changes.
#
# Usage:
#   bash shared/scripts/check-sibling-observability-drift.sh [/path/to/sibling]
#
# Exit codes (BSD sysexits.h, matching preflight-check.sh):
#   0  in sync, or the sibling's module is absent in a way that is expected
#   2  drift found in something that would break the correlation
#  78  the sibling checkout could not be located (EX_CONFIG)
#
set -uo pipefail

THIS_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SIBLING="${1:-${SIBLING_REPO:-$(cd "$THIS_REPO/.." && pwd)/fsxn-s3ap-serverless-patterns}}"

OURS="$THIS_REPO/shared/python/observability.py"
THEIRS="$SIBLING/shared/observability.py"

RED=$'\033[0;31m'; YEL=$'\033[0;33m'; GRN=$'\033[0;32m'; NC=$'\033[0m'
FINDINGS=0

note()  { printf '  %s\n' "$1"; }
warn()  { printf '  %s%s%s\n' "$YEL" "$1" "$NC"; }
fail()  { printf '  %s%s%s\n' "$RED" "$1" "$NC"; FINDINGS=$((FINDINGS + 1)); }
ok()    { printf '  %s%s%s\n' "$GRN" "$1" "$NC"; }

echo "=== Sibling instrumentation drift check ==="
echo "  this repo : $THIS_REPO"
echo "  sibling   : $SIBLING"
echo

if [[ ! -d "$SIBLING" ]]; then
  echo "${RED}Sibling checkout not found.${NC}"
  echo "Pass the path as an argument or set SIBLING_REPO."
  exit 78
fi

[[ -f "$OURS" ]] || { echo "${RED}Missing $OURS${NC}"; exit 78; }

# ─── 1. The join key contract ───────────────────────────────────────────────
# This is the only thing whose divergence is silent. Everything else fails loudly.

echo "--- join key contract ---"
if [[ -f "$THEIRS" ]] && grep -q 's3ap_join_key' "$THEIRS" 2>/dev/null; then
  note "the sibling defines a join key; comparing the derivation"
  for symbol in s3ap_join_key audit_path_to_object_key ONTAP_EVENT_TO_S3_VERB \
                is_joinable_audit_event principal_token; do
    if ! grep -q "$symbol" "$THEIRS"; then
      fail "sibling is missing '$symbol' while producing a join key -- the two sides can disagree"
    fi
  done
  if ! diff -q <(sed -n '/^ONTAP_EVENT_TO_S3_VERB = {/,/^}/p' "$OURS") \
               <(sed -n '/^ONTAP_EVENT_TO_S3_VERB = {/,/^}/p' "$THEIRS") >/dev/null 2>&1; then
    fail "the ONTAP EventName -> S3 verb tables differ; a GET or DELETE will not match"
    diff <(sed -n '/^ONTAP_EVENT_TO_S3_VERB = {/,/^}/p' "$OURS") \
         <(sed -n '/^ONTAP_EVENT_TO_S3_VERB = {/,/^}/p' "$THEIRS") | sed 's/^/      /'
  else
    ok "verb tables identical"
  fi
else
  ok "the sibling does not derive a join key yet, so there is no contract to diverge"
  note "adoption is tracked in ROADMAP.md under Application-Layer Signals"
fi

# ─── 2. Two implementations of the same idea ────────────────────────────────

echo
echo "--- duplicate implementations ---"
if [[ -f "$THEIRS" ]]; then
  warn "both repositories define an observability module:"
  note "  ours   : shared/python/observability.py    ($(wc -l < "$OURS" | tr -d ' ') lines)"
  note "  theirs : shared/observability.py           ($(wc -l < "$THEIRS" | tr -d ' ') lines)"
  note "This repository is the source of truth (see ROADMAP.md). The sibling's copy"
  note "predates that decision and is expected to be replaced by an import of the"
  note "layer built from ours, not kept in step by hand."

  if grep -q 'aws_lambda_powertools' "$THEIRS" 2>/dev/null; then
    fail "the sibling imports aws_lambda_powertools; ours is standard-library only"
    note "  a module that needs a layer attached is materially harder to drop in"
  fi
else
  ok "the sibling has no separate observability module"
fi

# ─── 3. Prerequisites the correlation depends on ───────────────────────────
# The portal's access point identity decides whether its operations are audited
# at all. A UNIX identity means no audit records, which looks like the pipeline
# being broken rather than like a configuration choice.

echo
echo "--- access point identity in the sibling's portal ---"
PORTAL_CONFIG="$SIBLING/solutions/amplify-portal/amplify/portal-config.ts"
if [[ -f "$PORTAL_CONFIG" ]]; then
  if grep -qi 'WINDOWS' "$PORTAL_CONFIG"; then
    ok "portal config mentions a WINDOWS identity"
  else
    warn "portal config does not mention a WINDOWS identity"
    note "  A UNIX identity cannot coexist with an audit ACE: applying the ACE makes"
    note "  the effective style ntfs and every S3 operation returns AccessDenied."
    note "  See integrations/amplify-portal/docs/en/setup-guide.md"
  fi
else
  note "portal config not present (gitignored in the sibling); identity unchecked"
fi

# ─── 4. The checkpoint hazard, in both repositories ─────────────────────────

echo
echo "--- audit-log checkpointing ---"
OFFENDERS=0
# Matches StartAfter being *set* -- as a dict key or a keyword argument -- and not
# merely named. A grep for the bare word flagged
# integrations/amplify-portal/lambda/handler.py, which mentions StartAfter only in
# the comment explaining why it does not use it. A checker that reports the file
# that fixed the problem trains people to ignore it.
while IFS= read -r f; do
  if grep -nE '^[^#]*(\["StartAfter"\]|StartAfter"?\s*[:=])' "$f" >/dev/null 2>&1 \
     && grep -qiE 'audit' "$f" 2>/dev/null; then
    warn "key-based StartAfter over audit logs: ${f#"$THIS_REPO"/}"
    grep -nE '^[^#]*(\["StartAfter"\]|StartAfter"?\s*[:=])' "$f" | sed 's/^/        /'
    OFFENDERS=$((OFFENDERS + 1))
  fi
done < <(find "$THIS_REPO/integrations" -name '*.py' -path '*lambda*' 2>/dev/null | sort)

if (( OFFENDERS > 0 )); then
  note "ONTAP's active audit file has a fixed key that sorts after every rotated"
  note "one, so a key high-water mark stops listing new files permanently."
  note "Tracked in ROADMAP.md; integrations/amplify-portal uses a record timestamp."
else
  ok "no vendor handler pairs StartAfter with an audit-log prefix"
fi

echo
if (( FINDINGS > 0 )); then
  echo "${RED}${FINDINGS} finding(s) that would break the correlation.${NC}"
  exit 2
fi
echo "${GRN}No correlation-breaking drift.${NC}"
exit 0
