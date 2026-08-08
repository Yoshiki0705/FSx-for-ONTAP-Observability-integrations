# PagerDuty Escalation Integration Guide

🌐 **English** (this page) | [日本語](../ja/pagerduty-escalation-guide.md)

## Overview

Route CloudWatch Alarms from the FSx for ONTAP Observability pipeline to PagerDuty for on-call escalation.

## Architecture

```
CloudWatch Alarm (ARP detection / DLQ / poison-pill / admin audit anomaly)
    │
    ▼ AlarmAction
SNS Topic (fsxn-pagerduty-critical)
    │
    ▼ HTTPS Subscription
PagerDuty Events API v2
    │
    ▼ Escalation Policy
On-call responder (SMS / Phone / Push / Slack)
```

## Prerequisites

- PagerDuty account (Free tier works for validation)
- PagerDuty service created
- Events API v2 Integration Key obtained

## Deployment

### 1. PagerDuty Setup

1. Log in to PagerDuty → **Services** → **+ New Service**
2. Service name: `FSx for ONTAP Observability`
3. Escalation Policy: select existing or default
4. Integration: select **Events API V2**
5. Copy the Integration URL (`https://events.pagerduty.com/integration/<key>/enqueue`)

### 2. Deploy CloudFormation Stack

```bash
aws cloudformation deploy \
  --template-file shared/templates/pagerduty-escalation.yaml \
  --stack-name fsxn-pagerduty-escalation \
  --parameter-overrides \
    PagerDutyIntegrationUrl="https://events.pagerduty.com/integration/<your-key>/enqueue" \
    EscalationLevel=critical \
  --region ap-northeast-1
```

### 3. Connect Existing Alarms

Add the output `PagerDutyTopicArn` as an `AlarmAction` to existing alarms:

```bash
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-pagerduty-escalation \
  --query 'Stacks[0].Outputs[?OutputKey==`PagerDutyTopicArn`].OutputValue' \
  --output text)

aws cloudwatch put-metric-alarm \
  --alarm-name fsxn-arp-ransomware-detected \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN" \
  # ... (keep existing parameters)
```

## Recommended Alarm Connections

| Alarm | Trigger | Severity | PagerDuty |
|-------|---------|----------|-----------|
| ARP ransomware detection | `arw.volume.state` severity:alert | Critical | ✅ Required |
| DLQ depth | DLQ messages > 0 | Critical | ✅ Required |
| Poison-pill detected | 3 consecutive failures | High | ✅ Recommended |
| Buffer backpressure | Queue depth > 100 (15 min) | High | ○ Optional |
| Checkpoint stale | No progress for 30 min | High | ○ Optional |
| Mass snapshot deletion | > 5 deletes / 5 min | Critical | ✅ Recommended |
| Lambda error rate | Errors > 5% (5 min) | High | ○ Optional |

## Escalation Policy Example

```
Level 1 (immediate):  Storage administrator  — Push notification + Slack
Level 2 (after 15m):  Security team          — Phone + SMS
Level 3 (after 30m):  Infrastructure owner   — Phone
```

## Alert Lifecycle

```
[Alarm -> ALARM]  ->  PagerDuty incident created (trigger)
[Alarm -> OK]     ->  PagerDuty incident auto-resolved (resolve)
```

- When the CloudWatch alarm returns to OK, the PagerDuty incident resolves automatically
- Auto-resolution requires the same SNS topic to be set on `OKActions`

## Severity Mapping

> **Important**: The `EscalationLevel` parameter in this template **only names the SNS topic** (e.g., `fsxn-pagerduty-critical`). It does NOT set the PagerDuty incident severity.

With the standard CloudWatch Alarm → SNS → PagerDuty Events API v2 integration:

| CloudWatch | PagerDuty |
|-----------|-----------|
| ALARM transition | Incident trigger (severity from PagerDuty service default) |
| OK transition | Incident resolve |

**To vary severity per alarm**, choose one of:

1. **PagerDuty Event Rules** — route severity/urgency based on the incoming payload (e.g., AlarmName) on the PagerDuty side. Recommended, no extra infra.
2. **EventBridge Input Transformer** — CloudWatch Alarm → EventBridge → transform to inject a `severity` field before PagerDuty. More control, more moving parts.
3. **Per-tier SNS topics** — deploy separate stacks with different `EscalationLevel` values and bind each topic to a different PagerDuty service/escalation policy.

This template assumes approach 3 (per-tier topics) as the minimal setup. Combine with 1 or 2 for fine-grained payload-driven severity.

## Security Notes

- **The integration URL is a secret**: it embeds the PagerDuty integration key. The CloudFormation parameter uses `NoEcho: true` to hide it from the console/API, but it is still stored on the SNS subscription endpoint. Treat the deploying principal and the SNS topic policy as sensitive.
- **Key rotation**: if you rotate the PagerDuty integration key, update the stack to apply the new URL.

## Cost

| Component | Monthly Estimate |
|-----------|-----------------|
| SNS Topic + HTTPS delivery | ~$0 (negligible at alert volumes) |
| PagerDuty | Free tier: up to 5 users |
| **Total** | **~$0** (with Free tier) |

## Testing

```bash
# Publish a test message to SNS and confirm an incident is raised in PagerDuty
aws sns publish \
  --topic-arn "$TOPIC_ARN" \
  --subject "ALARM: fsxn-test-pagerduty" \
  --message '{"AlarmName":"fsxn-test","NewStateValue":"ALARM","NewStateReason":"Test escalation"}'
```

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------|-----------|
| No incident created in PagerDuty | SNS subscription still Pending | The subscription needs confirming on the PagerDuty side (Events API v2 confirms automatically) |
| Subscription stuck in ConfirmationPending | Wrong integration URL | Check that the URL ends in `/enqueue` |
| Incidents never auto-resolve | `OKActions` not configured | Add `--ok-actions "$TOPIC_ARN"` |
| Duplicate incidents | The same alarm entered ALARM more than once | PagerDuty deduplicates on the dedup key (AlarmName) |

## Vendor Alert vs AWS Alarm Escalation

| Path | Use Case | Latency |
|------|----------|---------|
| **Vendor Monitor → PagerDuty** | Content-based detection (ML anomaly, log pattern) | Log arrival + evaluation window |
| **CloudWatch Alarm → SNS → PagerDuty** (this template) | Infrastructure health (delivery failure, processing lag) | Immediate (5 min eval) |

**Recommendation**: Use both in combination.

## Related

- [ARP Incident Response Guide](./arp-incident-response-guide.md)
- [DLQ Replay Runbook](./runbooks/dlq-replay.md)
- [Pipeline SLO Definitions](./pipeline-slo.md)
- [PagerDuty Events API v2 Documentation](https://developer.pagerduty.com/docs/events-api-v2/overview/)
