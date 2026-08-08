#!/bin/bash
# setup-full-observability.sh — Deploy the complete OTel Collector pipeline for FSx for ONTAP
#
# Orchestrates: Validate configs → Deploy stack → Wire alarm notifications → Verify E2E
#
# SCOPE NOTE — the collector is not a backend:
#   The other orchestrators end with "create vendor monitors + import dashboard".
#   Neither applies here: the OTel Collector fans telemetry out to backends, so
#   detection rules and dashboards live in those backends. Step 3 wires this
#   pipeline's own CloudWatch alarms to a notification channel instead, which is
#   the part this integration actually owns. Run the backend's own
#   create-alerts.sh for detections — see docs/en/detection-use-cases.md.
#
# Prerequisites:
#   - AWS CLI configured
#   - OTLP_ENDPOINT (and API_KEY_SECRET_ARN / AUTH_MODE if the backend needs auth)
#   - Docker, only for the optional config validation step
#
# Usage:
#   export OTLP_ENDPOINT="https://<collector-or-backend>:4318"
#   export ALARM_EMAIL="soc@example.com"
#   bash integrations/otel-collector/scripts/setup-full-observability.sh
#
#   Skip the Docker-based config validation:
#   SKIP_CONFIG_VALIDATION=1 bash integrations/otel-collector/scripts/setup-full-observability.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "============================================================"
echo " FSx for ONTAP — OTel Collector Full Pipeline Setup"
echo "============================================================"
echo ""

ALARM_STATUS="skipped"

# ─── Step 1/4: Validate Collector Configs ────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 1/4: Validate collector configs"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ -n "${SKIP_CONFIG_VALIDATION:-}" ]; then
  echo "  ⏭️  SKIP_CONFIG_VALIDATION set — skipping."
elif ! command -v docker >/dev/null 2>&1; then
  echo "  ⚠️  docker not found — skipping config validation."
  echo "     validate-configs.sh runs the collector image to check component"
  echo "     references, which cannot be done without a container runtime."
else
  # Non-fatal: this step validates the reference configs in configs/, which are
  # not what the Lambda ships with. A failure here should not block deployment.
  ( cd "${INTEGRATION_DIR}" && bash scripts/validate-configs.sh ) || \
    echo "  ⚠️  Config validation reported problems — review before relying on those configs."
fi

echo ""

# ─── Step 2/4: Deploy CloudFormation Stack ───────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 2/4: Deploy telemetry pipeline"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash "${SCRIPT_DIR}/deploy.sh"

echo ""

# ─── Step 3/4: Wire Alarm Notifications ──────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 3/4: Wire pipeline alarms to notifications"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ -n "${ALARM_EMAIL:-}" ] || [ -n "${ALARM_TOPIC_ARN:-}" ]; then
  bash "${SCRIPT_DIR}/create-alerts.sh"
  ALARM_STATUS="configured"
else
  echo "  ⚠️  Skipping — set ALARM_EMAIL or ALARM_TOPIC_ARN."
  echo "     Without a topic the stack's four alarms still deploy, but they have"
  echo "     no notification action: they turn red in the CloudWatch console and"
  echo "     page nobody."
fi

echo ""

# ─── Step 4/4: Verify End-to-End ─────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Step 4/4: E2E verification"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
bash "${SCRIPT_DIR}/verify.sh"

echo ""
echo "============================================================"
echo "✅ Setup complete"
echo ""
echo "What was configured:"
echo "  • Telemetry pipeline (Lambda → OTLP → backend)"
echo "  • Pipeline alarms: ${ALARM_STATUS}"
echo "  • E2E verification passed"
echo ""
echo "Next steps:"
echo "  1. Create detection rules in your backend, not here:"
echo "       bash integrations/<backend>/scripts/create-alerts.sh"
echo "  2. Build dashboards in that backend"
echo "  3. Enable FSx for ONTAP audit logging if not already active"
echo "     (bash shared/scripts/ontap-audit-setup.sh --help)"
echo "============================================================"
