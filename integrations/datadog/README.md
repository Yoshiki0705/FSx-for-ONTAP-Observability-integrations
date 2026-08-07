# FSx for ONTAP Datadog Integration

🌐 [日本語](docs/ja/setup-guide.md) | [English](docs/en/setup-guide.md)

> 📖 **Shared docs**: [Delivery Guarantee Patterns](../../docs/en/delivery-guarantees.md) | [Webhook Security](../../docs/en/webhook-security.md)
>
> 📋 **Datadog docs**: [Setup Guide](docs/en/setup-guide.md) | [EMS / FPolicy Setup](docs/en/ems-fpolicy-setup.md) | [Log Archive Setup](docs/en/log-archive-setup.md) | [Snapshot Remediation](docs/en/snapshot-remediation-setup.md) | [Field Mapping](docs/en/field-mapping.md) | [Production Checklist](docs/en/production-checklist.md) | [SPL vs CQL Comparison](docs/en/spl-cql-comparison.md)

## Overview

EC2-free integration that ships Amazon FSx for NetApp ONTAP audit logs to Datadog. Lambda reads audit log files from the FSx volume via an FSx for ONTAP S3 Access Point and ships them to the Datadog Logs API v2.

**PoC time estimate**: ~30 minutes from deploy to first queryable log in Datadog.

> ⚠️ Datadog has no free tier for log ingestion. PoC will incur costs (~$0.10/GB ingested). Consider using the [OTel Collector integration](../otel-collector/) with a free-tier backend (Grafana/Honeycomb) for initial validation if budget is a concern.

## Architecture

```
FSx for ONTAP audit volume
  └─ FSx for ONTAP S3 AP ──┐
                            │  (ListObjectsV2 + GetObject)
   EventBridge Scheduler ───┴─→ Lambda shipper ──→ Datadog Logs API v2
     (every 5 min)                  │
                                    └─→ SSM Parameter (checkpoint)
```

FSx for ONTAP S3 Access Points do not emit S3 Event Notifications, so the shipper
polls on a schedule and tracks the last processed key in an SSM Parameter Store
checkpoint. Only keys after the checkpoint are processed, so each rotated audit
file is shipped once.

Two optional real-time sources close the rotation-latency gap:

| Source | Latency | Stack | Guide |
|--------|---------|-------|-------|
| Audit logs | Minutes (rotation + schedule) | `template.yaml` | [Setup Guide](docs/en/setup-guide.md) |
| EMS webhooks | Seconds | `template-ems-fpolicy.yaml` | [EMS / FPolicy](docs/en/ems-fpolicy-setup.md) |
| FPolicy | Sub-second | `template-ems-fpolicy.yaml` | [EMS / FPolicy](docs/en/ems-fpolicy-setup.md) |

## Quick Deploy

The deploy script deploys the stack **and** uploads the real Lambda code.
CloudFormation cannot inline a multi-hundred-line handler, so `template.yaml`
ships a placeholder that raises `NotImplementedError` — deploying the template
alone leaves a non-functional pipeline.

```bash
export DATADOG_API_KEY_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX"
export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
export DATADOG_SITE="ap1.datadoghq.com"

bash scripts/deploy.sh          # audit log path only
bash scripts/deploy.sh --all    # + EMS and FPolicy
bash scripts/verify.sh          # confirm end to end
```

To deploy by hand, see [Setup Guide Step 3.2](docs/en/setup-guide.md#32-alternative-deploy-cloudformation-by-hand)
— and do not skip Step 3.3, which uploads the handler code.

## Scripts

| Script | Purpose |
|--------|---------|
| `deploy.sh` | Deploy stacks + upload real Lambda code (`--all`, `--code-only`) |
| `verify.sh` | 4-stage post-deployment check: stack, code, invocation, intake |
| `deploy-snapshot-remediation.sh` | Deploy the optional snapshot remediation Lambda |
| `setup-full-observability.sh` | Configure Datadog: pipeline, facets, monitors, metrics, SDS |
| `setup-facets.sh` | Facets only |
| `create-alerts.sh` | Monitors only |
| `create-dashboard.sh` | Dashboard only |
| `cleanup.sh` | Delete stacks in dependency-safe order (`--all`, `--delete-log-archive`) |

## Parameters

`template.yaml` — audit log shipper:

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| FsxS3AccessPointArn | ✅ | - | FSx for ONTAP S3 Access Point ARN (attached to audit volume) |
| DatadogApiKeySecretArn | ✅ | - | Secrets Manager ARN for the DD API key |
| DatadogSite | ❌ | ap1.datadoghq.com | Datadog site; determines the intake endpoint |
| AuditLogPrefix | ❌ | audit/ | Key prefix scanned within the access point |
| ScheduleRate | ❌ | rate(5 minutes) | How often to poll for new audit logs |
| MaxKeysPerRun | ❌ | 100 | Files per invocation; a larger backlog drains over several runs |
| Environment | ❌ | production | Value of the `env:` tag and `DD_ENV` |
| EnableGzip | ❌ | false | Gzip the payload — see the known issue below |
| LogLevel | ❌ | INFO | Lambda log level |
| LambdaMemorySize | ❌ | 256 | Lambda memory (MB) |
| LambdaTimeout | ❌ | 300 | Lambda timeout (seconds) |
| AlarmNotificationTopicArn | ❌ | `''` | SNS topic for the alarms. **Empty means nobody is notified** |
| VpcEnabled | ❌ | false | Set `true` only for a VPC-origin access point |
| VpcSubnetIds | ❌ | `''` | Required when `VpcEnabled=true` |
| VpcSecurityGroupIds | ❌ | `''` | Required when `VpcEnabled=true` |

Other stacks are documented in their own guides:
[EMS / FPolicy](docs/en/ems-fpolicy-setup.md#parameter-reference),
[Log Archive](docs/en/log-archive-setup.md#parameter-reference),
[Snapshot Remediation](docs/en/snapshot-remediation-setup.md#parameter-reference).

## Datadog Sites

| Site | Domain | Region |
|------|--------|--------|
| US1 | datadoghq.com | US East |
| US3 | us3.datadoghq.com | US |
| US5 | us5.datadoghq.com | US West |
| EU1 | datadoghq.eu | EU (Frankfurt) |
| AP1 | ap1.datadoghq.com | Asia Pacific (Tokyo) |
| AP2 | ap2.datadoghq.com | Asia Pacific |
| US1-FED | ddog-gov.com | US Government |

## Tags Applied

- `source:fsxn`
- `service:ontap-audit`
- `env:<environment>`

## Monitoring

AWS-side pipeline health (Datadog-side monitors are covered further down):

| Stack | Alarm | Condition |
|-------|-------|-----------|
| `template.yaml` | errors | Lambda errors > 5 in 10 minutes |
| `template.yaml` | throttles | Lambda throttling detected |
| `template.yaml` | dlq-messages | Messages appearing in the DLQ |
| `template-ems-fpolicy.yaml` | ems-errors | EMS webhook Lambda failing (ONTAP receives 5xx) |
| `template-ems-fpolicy.yaml` | fpolicy-errors / fpolicy-throttles / fpolicy-dlq-messages | FPolicy delivery problems |

Dead Letter Queues retain failed events for 14 days. Replay procedure:
[dlq-replay.md](../../docs/en/runbooks/dlq-replay.md).

> **Alarms do not notify anyone unless `AlarmNotificationTopicArn` is set.**
> Without it they are visible in the CloudWatch console only. This matters most
> for the EMS path: EMS is invoked synchronously by API Gateway, so a Lambda DLQ
> never applies and the alarm is the only signal that events are being lost.

A CloudWatch dashboard (`<stack>-health`) is created with the main stack for
Lambda errors, duration, invocations and DLQ depth.

## E2E Verification Results

✅ **Verified on paid Datadog AP1 plan** (June 2026, re-verified August 2026)

### August 2026 re-verification — full documented path

Ran the [Setup Guide](docs/en/setup-guide.md) start to finish against a live FSx
for ONTAP file system to confirm a first-time reader can reproduce it:

| Step | Result |
|------|--------|
| `aws fsx create-and-attach-s3-access-point` (Step 2, verbatim) | ✅ `AVAILABLE` in ~20s, `NetworkOrigin: Internet` |
| `scripts/deploy.sh` (Step 3.1) | ✅ Stack + real Lambda code, 3-5 min |
| `scripts/verify.sh` (Step 5.1) | ✅ 4/4 checks passed |
| Namespaced ONTAP XML → Datadog | ✅ 2 files / 3 events shipped and indexed |
| SSM checkpoint advance + idempotent re-run | ✅ Re-run is a no-op (no duplicates) |
| EventBridge Scheduler self-firing | ✅ Automatic invocations at 5-min intervals |
| Lambda errors during the run | ✅ 0 |
| Access point resource policy required? | ✅ No — same-account IAM was sufficient |

Two defects were found and fixed during this run:

- **Namespaced XML collapsed into one record.** ONTAP writes the Windows Event
  Log XML schema, so every element carries an `xmlns`. `iter("Event")` matched
  nothing, and the fallback merged the whole file into a single log entry —
  silently discarding every event but the last. Confirmed against live data: a
  2-event file delivered 1 event before the fix and 2 after. The same defect was
  present in `shared/lambda-layers/log-parser` (DOM path only) and
  `integrations/crowdstrike`; all three are fixed with regression tests.
- **Reusing an existing VPC-origin access point** produced
  `AccessDenied ... explicit deny in a resource-based policy`, which reads like
  an IAM problem. The guide now shows how to detect the origin before deploying.

| Component | Status | Evidence |
|-----------|--------|----------|
| XML audit log parsing (5 events) | ✅ | EventID 4663/4656/4660 |
| Datadog Logs API v2 delivery | ✅ HTTP 202 | 10 events in Log Explorer |
| Field extraction | ✅ | user, path, client_ip, event_type, result, svm, operation |
| Log Pipeline (EventID→Operation) | ✅ | Category processor applied |
| Monitors (mass delete, abnormal access, failure spike) | ✅ | 3 monitors active |
| Dashboard | ✅ | FSx for ONTAP Audit Log Overview |

### Screenshots

| Screenshot | Description |
|-----------|-------------|
| ![Log Explorer](screenshots/datadog-log-explorer-fsxn-xml.png) | Log Explorer showing FSx for ONTAP audit events with full field extraction |
| ![Dashboard](screenshots/datadog-dashboard-fsxn-overview.png) | FSx for ONTAP Audit Log Overview dashboard |
| ![Pipeline](screenshots/datadog-log-pipeline-config.png) | Log Pipeline configuration (EventID→Operation Name mapping) |
| ![Monitors](screenshots/datadog-monitors-fsxn.png) | Security monitors for mass deletion, abnormal access, and access failures |

## Log Pipeline Configuration

The pipeline (`FSx for ONTAP Audit Logs`) applies to logs matching `source:fsxn` and includes:

1. **Category Processor** — Maps EventID to human-readable operation names:
   - 4663 → Object Access
   - 4656 → Handle Request
   - 4660 → Object Delete
   - 4670 → Permission Change
   - 5140 → Share Access
   - 4624 → Logon / 4634 → Logoff

2. **Status Remapper** — Maps `result` field to Datadog log status
3. **Date Remapper** — Uses `timestamp` field as the log timestamp
4. **Attribute Remapper** — Maps `user` → `usr.id`, `client_ip` → `network.client.ip`

> **Two refinements to the above**, which the [Setup Guide](docs/en/setup-guide.md#42-create-the-log-pipeline)
> covers in full:
> - The Date Remapper (3) is redundant. The handler already sets the top-level
>   `date` field, which Datadog uses natively.
> - A Status Remapper (2) applied straight to `result` leaves failures at `info`,
>   because ONTAP emits `Success` / `Failure` rather than Datadog status values.
>   Map them with a Category Processor first, then remap — otherwise the events
>   you most want to alert on are not marked as errors.
>
> No Grok Parser is needed: the handler ships structured JSON, so
> `@attributes.*` fields are available without one.

## Security Monitors

| Monitor | Threshold | Severity | Description |
|---------|-----------|----------|-------------|
| Mass File Deletion | >50 deletes/5min per user | Critical | Detects bulk file deletion (ransomware, accidental) |
| Abnormal Access Volume | >1000 accesses/1h per user | High | Detects potential data exfiltration |
| Access Failure Spike | >10 failures/15min per user | Medium | Detects unauthorized access attempts |

## Saved Views

Pre-configured views for common investigation scenarios:

| View Name | Query | Use Case |
|-----------|-------|----------|
| FSx for ONTAP File Deletions | `source:fsxn @event_type:4660` | Track all file deletion events |
| FSx for ONTAP Access Failures | `source:fsxn @result:"Audit Failure"` | Permission denied / unauthorized access |
| FSx for ONTAP All Events | `source:fsxn` | Full audit log stream |
| FSx for ONTAP Sensitive Share Access | `source:fsxn (@path:*finance* OR @path:*hr* OR @path:*legal*)` | Access to sensitive file shares |
| FSx for ONTAP After-Hours Access | `source:fsxn` | Filter by time for off-hours monitoring |

## Forensic Investigation Notebook

> 🔍 For a user/IP/path-centric investigation workflow (who accessed what, from where, doing what — similar to DII Storage Workload Security's Forensics dashboards), build a Datadog **Notebook** with the following query cells using [Notebook variables](https://docs.datadoghq.com/notebooks/) (`{{user}}`, `{{client_ip}}`, `{{path}}`) so the same notebook is reusable per incident:

```
# Cell 1 — User Overview
source:fsxn @user:"{{user}}"
# group by @operation, visualize as timeseries + top list

# Cell 2 — All Activity for that user (chronological)
source:fsxn @user:"{{user}}"
# Log Stream view, sorted by time ascending

# Cell 3 — IP-centric drill-down (lateral movement / credential compromise)
source:fsxn @client_ip:"{{client_ip}}"

# Cell 4 — Entity/file drill-down
source:fsxn @path:"{{path}}"
```

Export findings via Log Explorer's CSV export, scoped to your investigation time range. See [Cyber Resilience Capability Map](../../docs/en/cyber-resilience-capability-map.md#respond-rs) for how this maps to the CSF 2.0 Respond function's forensic-investigation coverage and what data-source caveats apply (FPolicy vs audit log coverage, PII handling).

![Saved Views](screenshots/datadog-saved-views.png)

## Facets Setup

After deploying and sending initial logs, add custom Facets for faster filtering in Log Explorer:

1. Open Log Explorer → Click any log entry to expand it
2. Hover over a field (e.g., `event_type`) → Click the gear icon → "Create facet"
3. Repeat for: `@event_type`, `@user`, `@svm`, `@path`, `@client_ip`, `@operation`, `@result`, `@operation_name`

![Facets in Log Explorer](screenshots/datadog-log-explorer-facets.png)

These facets enable:
- Left sidebar filtering by user, SVM, operation type
- One-click drill-down from dashboard widgets
- Saved View facet panels for team-specific workflows

## Important Notes

- **FSx for ONTAP S3 APs do NOT support S3 Event Notifications.** Lambda is invoked on a schedule (EventBridge Scheduler) and uses checkpointing to process only newly rotated files.
- **Internet-origin S3 APs** timed out with only a Gateway Endpoint in our environment. If Lambda is in a VPC, use NAT Gateway or create a VPC-origin AP.
- Audit log format: EVTX or XML (configured via `vserver audit create -format {evtx|xml}`)
- **Datadog region**: This integration is verified on AP1 (ap1.datadoghq.com). Adjust `DatadogSite` parameter for other regions.

## Log-based Metrics

Custom metrics generated from logs — enables cost-efficient long-term trending without retaining all raw logs.

| Metric ID | Source Filter | Group By | Use Case |
|-----------|--------------|----------|----------|
| `fsxn.audit.delete_count` | `@event_type:4660` | user, svm | Delete rate per user/SVM for dashboards and anomaly detection |
| `fsxn.audit.access_failure_count` | `@result:"Audit Failure"` | user, svm, client_ip | Failed access trends by source IP |
| `fsxn.audit.event_count` | `source:fsxn` | event_type, svm | Overall event volume by type |
| `fsxn.audit.unique_users` | `source:fsxn` | user | Active user tracking |

![Log-based Metrics](screenshots/datadog-log-based-metrics.png)

These metrics appear in Datadog Metrics Explorer as `fsxn.audit.*` and can be used in dashboards, monitors, and anomaly detection without log retention costs.

## Sensitive Data Scanner

PII auto-detection and redaction for audit log content. Protects against accidental exposure of personal data in file paths and usernames.

| Rule | Pattern | Example Match | Action |
|------|---------|---------------|--------|
| Employee ID | `EMP-\d{6}` | `/hr/EMP-123456-review.xlsx` | Partial redact |
| JP Phone Number | `0[789]0-?\d{4}-?\d{4}` | `090-1234-5678` | Partial redact |
| Email Address | `[a-zA-Z0-9._%+-]+@...` | `user@example.com` | Partial redact |
| Credit Card | `4[0-9]{12}...` | `4111111111111111` | Partial redact |
| My Number (JP) | `\d{4}\s?\d{4}\s?\d{4}` | `1234 5678 9012` | Partial redact |

![Sensitive Data Scanner](screenshots/datadog-sensitive-data-scanner.png)

## Enhanced Dashboard (10 Widgets)

The FSx for ONTAP Audit Log Overview dashboard includes:

| Widget | Type | Purpose |
|--------|------|---------|
| Log Volume Over Time | Timeseries | Overall ingestion trend |
| Operations Breakdown | Top List | EventID distribution |
| User Activity | Top List | Most active users |
| Error Rate | Timeseries | Failure rate over time |
| 🔴 File Deletions by User | Top List | Who is deleting the most files |
| 🟡 Access Failures Timeline | Timeseries (bars) | Failure events by user over time |
| 📊 Operation Distribution | Sunburst | Operation types grouped by SVM |
| 🌐 Client IP Activity | Top List | Most active source IPs |
| 📁 Most Accessed Paths | Top List | Hot file paths |
| ⚡ Log-based Metrics: Delete Rate | Timeseries | Custom metric trend |

![Enhanced Dashboard](screenshots/datadog-dashboard-enhanced.png)

## Datadog-side Setup (One Script)

Configure the Datadog side — Pipeline, Facets, Monitors, Log-based Metrics and
Sensitive Data Scanner rules — with a single script:

```bash
export DD_API_KEY_SECRET_ID="fsxn-datadog-api-key"
export DD_APP_KEY_SECRET_ID="datadog/fsxn-app-key"   # Application key, not the API key
export DD_SITE="ap1.datadoghq.com"
bash scripts/setup-full-observability.sh
```

This creates everything in ~30 seconds via the Datadog API. No manual UI clicks
needed.

> This script configures **Datadog only**. It does not deploy any AWS resources —
> run `scripts/deploy.sh` first, or there will be no logs for the pipeline to
> process.

## Cleanup

```bash
bash scripts/cleanup.sh                        # stacks only
bash scripts/cleanup.sh --all                  # + secret, layer, S3 test data
bash scripts/cleanup.sh --delete-log-archive   # + log archive stack (bucket is retained)
```

Stacks are deleted in dependency-safe order: the EMS API Gateway stack must go
before the stack that owns the EMS Lambda. The FSx for ONTAP S3 Access Point, the
audit bucket and the shared FPolicy Fargate stack (`fsxn-fp-srv`) are shared
resources and are not removed.

## Optional Components

| Component | Stack | Purpose |
|-----------|-------|---------|
| [EMS / FPolicy](docs/en/ems-fpolicy-setup.md) | `template-ems-fpolicy.yaml` | Real-time ONTAP system and file events |
| [Log Archive](docs/en/log-archive-setup.md) | `template-log-archive.yaml` | S3 archival + rehydration for multi-year retention |
| [Snapshot Remediation](docs/en/snapshot-remediation-setup.md) | `template-snapshot-remediation.yaml` | Datadog Workflow creates an ONTAP snapshot for evidence preservation |

Snapshot remediation takes an action against production storage. It runs **inside
the VPC** (the ONTAP management LIF has no internet path), unlike the audit
shipper which runs outside it — do not copy network settings between the two.

## Testing

```bash
python3 -m pytest integrations/datadog/tests/ -v
cfn-lint integrations/datadog/template*.yaml
cfn-guard validate -d integrations/datadog/template.yaml \
  -r guard/rules/critical-security.guard --show-summary fail
```
