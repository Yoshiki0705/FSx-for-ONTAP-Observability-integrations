#!/bin/bash
# setup-full-observability.sh — Deploy complete Falcon LogScale observability for FSx for ONTAP
#
# Orchestrates: Deploy stack → Create alerts → Verify E2E
#
# SCOPE NOTE — three steps, not four:
#   The other vendor orchestrators have a dashboard import step. This one does
#   not, because there is no integrations/crowdstrike/dashboards/ directory to
#   import from (compare integrations/sumo-logic/dashboards/). A step that
#   silently skipped a non-existent file would read as "dashboard configured"
#   in the summary. Build LogScale dashboards in the UI, or add a
#   dashboards/ directory and extend this script.
#
# SCOPE NOTE — audit logs only:
#   This integration ships template.yaml only (no EMS webhook, no FPolicy), so
#   ransomware/ARP detection is out of reach here. create-alerts.sh explains
#   this and creates three audit-derived alerts instead.
#
# Prerequisites:
#   - AWS CLI configured
#   - LogScale ingest token in Secrets Manager (for log delivery — used by deploy.sh)
#   - LogScale management API token (for alerts — a DIFFERENT token, see create-alerts.sh)
#
# Usage:
#   export LOGSCALE_URL="https://cloud.us.humio.com"
#   export LOGSCALE_API_TOKEN="<management-token>"
#   export LOGSCALE_REPOSITORY="fsxn-audit"
#   export LOGSCALE_ALERT_EMAIL="soc@example.com"
#   bash integrations/crowdstrike/scripts/setup-full-observability.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================================"
echo " FSx for ONTAP — Falcon LogScale Full Observability Setup"
echo "============================================================"
echo ""

ALERTS_STATUS="skipped"

# ─── Step 1/3: Deploy CloudFormation Stack ───────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 1/3: Deploy audit log pipeline"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash "${SCRIPT_DIR}/deploy.sh"

echo ""

# ─── Step 2/3: Create Security Alerts ────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 2/3: Create security alerts"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ -n "${LOGSCALE_API_TOKEN:-}" ] && { [ -n "${LOGSCALE_ALERT_EMAIL:-}" ] || [ -n "${LOGSCALE_ACTION_NAME:-}" ]; }; then
  bash "${SCRIPT_DIR}/create-alerts.sh"
  ALERTS_STATUS="created"
else
  echo "  ⚠️  Skipping alert creation."
  echo "     Needs LOGSCALE_API_TOKEN (management token, not the ingest token)"
  echo "     plus LOGSCALE_ALERT_EMAIL or LOGSCALE_ACTION_NAME."
fi

echo ""

# ─── Step 3/3: Verify End-to-End ─────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 3/3: E2E verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash "${SCRIPT_DIR}/verify.sh"

echo ""
echo "============================================================"
echo "✅ Setup complete"
echo ""
echo "What was configured:"
echo "  • Audit log pipeline (Lambda → LogScale HEC)"
echo "  • Security alerts: ${ALERTS_STATUS}"
echo "  • E2E verification passed"
echo ""
echo "Next steps:"
echo "  1. Build LogScale dashboards (none are shipped with this integration)"
echo "  2. Run each alert's query manually to confirm it matches real events"
echo "  3. Enable FSx for ONTAP audit logging if not already active"
echo "     (bash shared/scripts/ontap-audit-setup.sh --help)"
echo "============================================================"
