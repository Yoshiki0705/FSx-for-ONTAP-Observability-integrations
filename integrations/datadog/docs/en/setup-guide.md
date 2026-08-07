# Datadog Setup Guide

🌐 [日本語](../ja/setup-guide.md) | **English** (this page)

## Overview

Setup guide for the serverless integration that ships Amazon FSx for NetApp ONTAP audit logs to Datadog Logs.

### How it works

```
FSx for ONTAP audit volume
  └─ FSx for ONTAP S3 AP ──┐
                            │  (ListObjectsV2 + GetObject)
   EventBridge Scheduler ───┴─→ Lambda shipper ──→ Datadog Logs Intake v2
     (every 5 min)                  │
                                    └─→ SSM Parameter (checkpoint)
```

FSx for ONTAP S3 Access Points do not support S3 Event Notifications or
EventBridge object-level events. The shipper is therefore invoked on a schedule,
lists objects under the audit prefix, and records the last processed key in an
SSM Parameter Store checkpoint. Only keys lexicographically greater than the
checkpoint are processed, so audit logs written under a date-based prefix
(`YYYY/MM/DD/`) are handled in order, exactly once per rotation.

This guide covers the audit log path. Two other event sources are optional:

| Source | Latency | Guide |
|--------|---------|-------|
| Audit logs (this guide) | Minutes (rotation + schedule) | — |
| EMS webhooks | Seconds | [ems-fpolicy-setup.md](ems-fpolicy-setup.md) |
| FPolicy file events | Sub-second | [ems-fpolicy-setup.md](ems-fpolicy-setup.md) |

## Prerequisites

- AWS Account with an FSx for ONTAP file system running
- Datadog account with the Logs feature enabled
- AWS CLI v2 configured
- FSx for ONTAP audit logging enabled on the SVM
  (`bash shared/scripts/ontap-audit-setup.sh --endpoint <ip> --svm <name> --dry-run`)

### Values to gather before you start

Collect these first — every later step needs at least one of them.

| Value | How to get it |
|-------|---------------|
| Datadog API key | Datadog console → Organization Settings → API Keys |
| Datadog site | The domain you log in to (e.g. `ap1.datadoghq.com`) |
| FSx file system ID | `aws fsx describe-file-systems --query 'FileSystems[].FileSystemId'` |
| Audit volume ID | `aws fsx describe-volumes --query 'Volumes[].{Id:VolumeId,Name:Name}' --output table` — a file system typically has many volumes; you want the one ONTAP writes audit logs to, i.e. the `-destination` of `vserver audit show`, not the SVM root volume |
| VPC ID | The VPC containing the FSx for ONTAP file system |
| AWS account ID | `aws sts get-caller-identity --query Account --output text` |

## Step 1: Prepare Datadog API Key

### 1.1 Get API Key from Datadog

1. Log in to Datadog console
2. Navigate to **Organization Settings** → **API Keys**
3. Click **New Key** to create a new API Key
4. Key name: `fsxn-audit-log-shipper`
5. Copy the generated API Key

### 1.2 Store in AWS Secrets Manager

```bash
aws secretsmanager create-secret \
  --name "fsxn-datadog-api-key" \
  --description "Datadog API Key for FSx for ONTAP audit log integration" \
  --secret-string '{"api_key":"YOUR_DATADOG_API_KEY"}' \
  --region ap-northeast-1
```

The handler accepts either a plain string or JSON (`{"api_key": ...}` or
`{"DD_API_KEY": ...}`), so both formats work. Note the returned ARN — it ends
with a 6-character suffix that AWS adds, and the full ARN is what you pass to
CloudFormation.

> The scripts in `scripts/` default to the secret **name**
> `fsxn-datadog-api-key`. Use a different name only if you also set
> `DD_API_KEY_SECRET_ID` when running them.

## Step 2: Create the FSx for ONTAP S3 Access Point

This is an **FSx for ONTAP S3 Access Point**, created with the `fsx` API and
attached to a volume. It is not the same thing as a standard S3 Access Point
created with `aws s3control` — that API points at an S3 bucket and cannot expose
an FSx volume.

```bash
aws fsx create-and-attach-s3-access-point \
  --name fsxn-audit-ap \
  --type ONTAP \
  --ontap-configuration 'VolumeId=fsvol-0123456789abcdef0,FileSystemIdentity={Type=UNIX,UnixUser={Name=root}}' \
  --region ap-northeast-1
```

Confirm it reached `AVAILABLE` and note the ARN:

```bash
aws fsx describe-s3-access-point-attachments \
  --names fsxn-audit-ap \
  --region ap-northeast-1 \
  --query 'S3AccessPointAttachments[0].{Lifecycle:Lifecycle,Arn:S3AccessPoint.ResourceARN}'
```

### Network origin matters

Omitting `--s3-access-point 'VpcConfiguration={VpcId=...}'` creates an
**Internet-origin** access point, which is what this integration expects: the
shipper Lambda runs **outside** the VPC and reaches the access point over the
internet path. This is the simplest and lowest-cost configuration.

If you add `VpcConfiguration`, the access point becomes VPC-origin and the
Lambda must run inside that VPC (`VpcEnabled=true`). **The network origin cannot
be changed after creation.**

### Reusing an existing access point

If you did not just create the access point, check its origin before choosing
`VpcEnabled` — this is the single most common cause of a failed first deployment:

```bash
aws s3control get-access-point \
  --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --name fsxn-audit-ap --region ap-northeast-1 \
  --query '{Origin:NetworkOrigin,Vpc:VpcConfiguration.VpcId}'
```

| Result | Deploy with |
|--------|-------------|
| `"Origin": "Internet"` | `VpcEnabled=false` (the default) |
| `"Origin": "VPC"` | `VpcEnabled=true`, subnets in the reported VPC, **and** internet egress for the Datadog API |

A VPC-origin access point rejects requests from outside its VPC with
`AccessDenied ... explicit deny in a resource-based policy`, even though no
access point policy exists. That wording points at IAM, but the cause is the
network origin.

| Lambda placement | Internet-origin AP | VPC-origin AP |
|-----------------|-------------------|---------------|
| Outside VPC (default) | ✅ Works | ❌ No route |
| In VPC + Gateway Endpoint only | ⚠️ Timed out in our testing | ✅ Works |
| In VPC + NAT Gateway | ✅ Works | ✅ Works |

> **AD-joined SVM note**: if the SVM has CIFS enabled, **every** S3 AP data
> operation requires the AD domain controllers to be reachable from the SVM. A
> successful `HeadBucket` with `AccessDenied` on `ListObjectsV2` is the signature
> of unreachable AD DCs — not an IAM or policy problem.

## Step 3: Deploy

### 3.1 Recommended: use the deploy script

The script deploys the stack **and** uploads the real Lambda code, which the
CloudFormation template cannot inline. Use it unless you have a reason not to.

```bash
export DATADOG_API_KEY_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX"
export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
export DATADOG_SITE="ap1.datadoghq.com"

bash integrations/datadog/scripts/deploy.sh
```

First run takes **3-5 minutes** — most of it is CloudFormation creating the IAM
role, Lambda, scheduler and alarms. The script prints each step as it completes;
it is not hung. Re-runs of an unchanged stack finish in seconds.

Add `--all` to also deploy the EMS and FPolicy stack. Run `--help` for the full
list of environment variables. Use `--code-only` to re-upload the handler
without touching the stack.

### 3.2 Alternative: deploy CloudFormation by hand

```bash
cd integrations/datadog

aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX \
    DatadogSite=ap1.datadoghq.com \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

### 3.3 Upload the real Lambda code (required)

**The stack alone is not functional.** CloudFormation cannot inline a
multi-hundred-line handler, so `template.yaml` ships a placeholder that raises
`NotImplementedError`. If you used `scripts/deploy.sh` this is already done. If
you deployed by hand, do it now:

```bash
cd integrations/datadog/lambda
zip function.zip handler.py

aws lambda update-function-code \
  --function-name fsxn-datadog-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-datadog-integration-shipper \
  --region ap-northeast-1
```

`scripts/verify.sh` check 2 detects a forgotten upload by inspecting the
deployed code size.

### Parameter Reference

Required:

| Parameter | Description |
|-----------|-------------|
| `FsxS3AccessPointArn` | FSx for ONTAP S3 Access Point ARN (attached to the audit volume) |
| `DatadogApiKeySecretArn` | Secrets Manager ARN for the API key |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DatadogSite` | `ap1.datadoghq.com` | Datadog site; determines the intake endpoint (see below) |
| `AuditLogPrefix` | `audit/` | Key prefix scanned within the access point |
| `ScheduleRate` | `rate(5 minutes)` | How often the shipper polls for new files |
| `MaxKeysPerRun` | `100` | Files processed per invocation; a larger backlog drains over several runs |
| `Environment` | `production` | Value of the `env:` tag and `DD_ENV` |
| `EnableGzip` | `false` | Gzip the payload. See the known issue in Troubleshooting before enabling |
| `LogLevel` | `INFO` | Lambda log level (`DEBUG` for troubleshooting) |
| `LambdaMemorySize` | `256` | MB. Raise if you process large EVTX files |
| `LambdaTimeout` | `300` | Seconds. Must exceed the time to process `MaxKeysPerRun` files |
| `AlarmNotificationTopicArn` | `''` | SNS topic for the error/throttle/DLQ alarms. **Empty means nobody is notified** |
| `VpcEnabled` | `false` | Set `true` only for a VPC-origin access point |
| `VpcSubnetIds` | `''` | Required when `VpcEnabled=true` |
| `VpcSecurityGroupIds` | `''` | Required when `VpcEnabled=true` |

### Stack outputs you will need

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-datadog-integration \
  --region ap-northeast-1 \
  --query 'Stacks[0].Outputs' --output table
```

| Output | Use |
|--------|-----|
| `LambdaFunctionName` | Target for `update-function-code` and manual invokes |
| `CheckpointParameterName` | Reset to `__INIT__` to re-process the whole prefix |
| `DeadLetterQueueUrl` | Inspect undelivered batches |
| `DashboardName` | CloudWatch dashboard for pipeline health |

### Datadog Sites

| Site | Domain | Use Case | Logs Intake Endpoint |
|------|--------|----------|---------------------|
| US1 | `datadoghq.com` | US East (default) | `http-intake.logs.datadoghq.com` |
| US3 | `us3.datadoghq.com` | US (Azure integration) | `http-intake.logs.us3.datadoghq.com` |
| US5 | `us5.datadoghq.com` | US West | `http-intake.logs.us5.datadoghq.com` |
| EU1 | `datadoghq.eu` | EU (Frankfurt) | `http-intake.logs.datadoghq.eu` |
| AP1 | `ap1.datadoghq.com` | Asia Pacific (Tokyo) | `http-intake.logs.ap1.datadoghq.com` |
| AP2 | `ap2.datadoghq.com` | Asia Pacific (Sydney) | `http-intake.logs.ap2.datadoghq.com` |
| US1-FED | `ddog-gov.com` | US Government (FedRAMP) | `http-intake.logs.ddog-gov.com` |

> **Region selection guide**:
> - APAC (Japan, Australia, etc.): `ap1.datadoghq.com` or `ap2.datadoghq.com`
> - EMEA (Europe, Middle East, Africa): `datadoghq.eu`
> - AMERICAS (North/South America): `datadoghq.com`, `us3.datadoghq.com`, `us5.datadoghq.com`
> - US Government: `ddog-gov.com`

## Step 4: Datadog Configuration

### 4.0 Fastest path: run the setup script

`setup-full-observability.sh` creates the log pipeline, facets, monitors,
log-based metrics and Sensitive Data Scanner rules through the Datadog API. It
configures **Datadog only** — it does not deploy any AWS resources, so run it
after Step 3.

```bash
export DD_API_KEY_SECRET_ID="fsxn-datadog-api-key"
export DD_APP_KEY_SECRET_ID="datadog/fsxn-app-key"   # Application key, not the API key
export DD_SITE="ap1.datadoghq.com"

bash integrations/datadog/scripts/setup-full-observability.sh
```

Individual pieces can be run on their own: `setup-facets.sh`,
`create-alerts.sh`, `create-dashboard.sh`.

The rest of this section describes the same configuration manually.

### 4.1 What is already structured (no Grok parser needed)

The Lambda sends each event as JSON with a nested `attributes` object, and sets
the top-level `date`, `ddsource`, `service`, `hostname` and `ddtags` fields that
Datadog uses natively. Datadog parses the JSON automatically, so:

- **No Grok Parser is required.** Fields are available immediately as
  `@attributes.user`, `@attributes.operation`, and so on.
- **No Date Remapper is required.** The handler sets `date` at the top level.

Send one test log and confirm the attributes appear before adding processors:

```bash
bash integrations/datadog/scripts/verify.sh
```

### 4.2 Create the log pipeline

A pipeline is only needed for the processors below (status mapping and PII
handling). Create it even if you skip the processors, so later additions have a
home.

1. Datadog console → **Logs** → **Configuration** → **Pipelines**
2. Click **New Pipeline**
3. Configuration:
   - **Filter**: `source:fsxn`
   - **Name**: `FSx for ONTAP Audit Logs`

#### Category Processor → status

ONTAP emits `Success` / `Failure`, which are not Datadog log statuses. Map them
first, then remap:

| Setting | Value |
|---------|-------|
| Processor | Category Processor |
| Target attribute | `status_category` |
| Category `error` | `@attributes.result:Failure` |
| Category `info` | `@attributes.result:Success` |

Then add a **Status Remapper** with the status attribute `status_category`.

Mapping `@attributes.result` directly with a Status Remapper leaves failed
access attempts at `info`, which is exactly the signal you want to alert on.

#### Sensitive Data Scanner (recommended)

Audit logs contain user names and full file paths. See
[data-classification.md](../../../../docs/en/data-classification.md) for the
field-by-field classification and which rules to enable.

### 4.3 Create Facets

`setup-facets.sh` creates these. To add them by hand, open a log in the Log
Explorer, click the field, and choose **Create facet**:

| Facet | Path | Type |
|-------|------|------|
| SVM | `@attributes.svm` | String |
| User | `@attributes.user` | String |
| Operation | `@attributes.operation` | String |
| Client IP | `@attributes.client_ip` | String |
| Result | `@attributes.result` | String |
| File Path | `@attributes.path` | String |
| Event Type | `@attributes.event_type` | String |

The full attribute-to-ONTAP-field mapping is in
[field-mapping.md](field-mapping.md).

### 4.4 Create Monitors

`create-alerts.sh` creates three monitors:

| Monitor | Condition |
|---------|-----------|
| Failed Access Spike | more than 10 failures in 5 minutes |
| Pipeline Health | Lambda errors detected |
| DLQ Alert | messages appearing in the Dead Letter Queue |

Exclude service accounts (`svc-*`) from the failed-access monitor before going
live, or normal automation will page you.

### 4.5 Create Dashboard (Recommended)

`create-dashboard.sh` creates it, or build it manually:

- **Log Volume Trend**: Time series of `source:fsxn` log count
- **Operations Breakdown**: Top list by `@attributes.operation`
- **User Activity**: Top list by `@attributes.user`
- **Error Rate**: Percentage of `@attributes.result:Failure`

A forensics dashboard is also available at
`dashboards/forensics-dashboard.json` — import it via **Dashboards** → **New**
→ **Import dashboard JSON**.

## Step 5: Verification

### 5.1 Run the verification script

This is the fastest way to find where the pipeline breaks. It checks the stack
state, confirms the placeholder code was replaced, invokes the shipper, and
sends one synthetic log to the intake API.

```bash
export DD_API_KEY_SECRET_ID="fsxn-datadog-api-key"
export DD_SITE="ap1.datadoghq.com"

bash integrations/datadog/scripts/verify.sh
```

Exit code 0 means all four checks passed. Each check reports independently, so a
passing intake check with a failing invocation tells you credentials are fine
but the Lambda cannot read the access point.

### 5.2 Generate real audit events

`new_files=0` from the script is expected when the checkpoint is already current.
To produce a new audit log file, generate activity on the audited volume:

```bash
# On a client with the FSx for ONTAP volume mounted.
# Mount first if needed:
#   sudo mkdir -p /mnt/fsxn
#   sudo mount -t nfs <svm-nfs-endpoint>:/vol_data /mnt/fsxn
echo "test" > /mnt/fsxn/test-audit.txt
cat /mnt/fsxn/test-audit.txt
rm /mnt/fsxn/test-audit.txt
```

ONTAP writes audit records to a staging file and only rotates it to the audit
volume periodically. Expect **rotation interval + schedule interval** before the
events are visible in Datadog, not seconds. To force a rotation:

```bash
# Via the ONTAP CLI
vserver audit rotate -vserver <svm-name>
```

### 5.3 Optional: validate without waiting for ONTAP rotation

ONTAP only makes audit records readable after it rotates the staging file, which
can take a while on a quiet system. To exercise the whole pipeline immediately,
write one representative audit file through the access point yourself — the
shipper cannot tell the difference:

```bash
AP="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
TS=$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))")

cat > /tmp/audit_check.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<Events>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>4663</EventID>
    <TimeCreated SystemTime="${TS}"/>
    <Computer>svm-prod-01</Computer>
  </System>
  <EventData>
    <Data Name="SubjectUserName">CORP\\pipeline-check</Data>
    <Data Name="ObjectName">/vol/data/pipeline-check.txt</Data>
    <Data Name="ObjectType">ReadData</Data>
    <Data Name="IpAddress">198.51.100.1</Data>
    <Data Name="Keywords">Audit Success</Data>
  </EventData>
</Event>
</Events>
EOF

aws s3api put-object --bucket "$AP" \
  --key "audit/$(date -u +%Y/%m/%d)/pipeline_check.xml" \
  --body /tmp/audit_check.xml

bash integrations/datadog/scripts/verify.sh
```

Expect `new_files=1, shipped=1`. Note the `xmlns` — ONTAP writes the Windows
Event Log XML schema, so a fixture without it does not represent real input.

Re-running immediately should report `new_files=0`: the checkpoint has advanced,
which is also how you confirm the pipeline will not duplicate logs.

> **Cleanup note**: files written this way live on the FSx volume, not in the
> access point. Delete them through a mounted client (or via the access point
> **before** you delete it) — removing the access point leaves them behind.

### 5.4 Verify in Datadog

1. Datadog console → **Logs** → **Search**
2. Search query: `source:fsxn`
3. Confirm `@attributes.user`, `@attributes.operation` and `@attributes.path`
   are populated, not just that logs arrived

### 5.5 Check Lambda in CloudWatch

```bash
aws logs tail /aws/lambda/fsxn-datadog-integration-shipper --follow
```

A healthy scheduled run logs `Scheduler mode: prefix=..., checkpoint=...`
followed by either `No new audit log files to process` or
`Found N new audit log file(s) to process`.

## Troubleshooting

### The Lambda raises NotImplementedError

The placeholder code is still deployed. See [Step 3.3](#33-upload-the-real-lambda-code-required),
or run:

```bash
bash integrations/datadog/scripts/deploy.sh --code-only
```

### AccessDenied with "explicit deny in a resource-based policy"

The access point is VPC-origin and the Lambda is outside that VPC (or vice
versa). No access point policy is involved despite the wording. Check the origin
with the command in [Step 2](#reusing-an-existing-access-point) and set
`VpcEnabled` to match. The origin cannot be changed — create a new access point
if you need the other one.

### Logs Not Appearing in Datadog

1. **Check Lambda errors**:
   ```bash
   aws logs filter-log-events \
     --log-group-name /aws/lambda/fsxn-datadog-integration-shipper \
     --filter-pattern "ERROR"
   ```

2. **Check DLQ messages**:
   ```bash
   aws sqs get-queue-attributes \
     --queue-url https://sqs.ap-northeast-1.amazonaws.com/123456789012/fsxn-datadog-integration-dlq \
     --attribute-names ApproximateNumberOfMessages
   ```

3. **Verify API Key**: Confirm the Secrets Manager value is correct

4. **Check timestamps**: Datadog rejects logs with a `date` more than **18 hours**
   in the past, silently. Backfilling an old audit volume will appear to succeed
   (HTTP 202) while nothing is indexed. Use current data when testing.

5. **Verify Datadog site**: Ensure the Lambda environment variable `DATADOG_SITE`
   points to the correct site. For the Japan region, use `ap1.datadoghq.com`.

### The checkpoint is not advancing

The shipper stops at the first file it cannot ship, deliberately — advancing past
it would drop those audit records permanently. Find the failing key in the
Lambda logs and fix the underlying cause; the run retries from the same point.

```bash
# Read the current checkpoint
aws ssm get-parameter \
  --name /fsxn-datadog/fsxn-datadog-integration/last-processed-key \
  --region ap-northeast-1 --query 'Parameter.Value' --output text
```

To re-process the whole prefix (this **duplicates** logs already in Datadog):

```bash
aws ssm put-parameter \
  --name /fsxn-datadog/fsxn-datadog-integration/last-processed-key \
  --value '__INIT__' --type String --overwrite --region ap-northeast-1
```

See [checkpoint-stale.md](../../../../docs/en/runbooks/checkpoint-stale.md).

### The same logs appear twice in Datadog

The checkpoint is not being persisted. Check the Lambda logs for
`Failed to update checkpoint` — usually a missing `ssm:PutParameter` permission —
and confirm the `CHECKPOINT_PARAM_NAME` environment variable is set.

### A large backlog drains slowly

`MaxKeysPerRun` (default 100) bounds each run so a backlog cannot exhaust the
Lambda timeout mid-file. To drain faster, raise it together with `LambdaTimeout`,
or temporarily shorten `ScheduleRate`.

### Using VPC-Restricted S3 Access Points

When the S3 Access Point is VPC-origin, Lambda must run in the same VPC. Add the
following parameters during CloudFormation deployment:

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX \
    DatadogSite=ap1.datadoghq.com \
    VpcEnabled=true \
    VpcSubnetIds=subnet-0123456789abcdef0,subnet-0123456789abcdef1 \
    VpcSecurityGroupIds=sg-0123456789abcdef0 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

> **Note**: Lambda in a VPC requires a NAT Gateway or VPC endpoint to reach the
> Datadog API. A VPC endpoint for Secrets Manager
> (`com.amazonaws.ap-northeast-1.secretsmanager`) is also required, otherwise the
> function times out reading its own API key.

Run `bash shared/scripts/preflight-check.sh --vpc-id vpc-xxx` first — a
conflicting VPC endpoint is the most common deployment failure in this project.

### Known Issue with gzip Compression

Currently, gzip-compressed payloads may not be correctly indexed on the Datadog
AP1 site (`ap1.datadoghq.com`). The payload is accepted (HTTP 202) but never
appears in search, which makes this look like a different problem entirely.
`EnableGzip` therefore defaults to `false`. If payload size becomes an issue in a
high-volume environment, contact Datadog support regarding gzip support status.

### Rate Limiting Errors

When Datadog API rate limits are hit, Lambda automatically retries with
exponential backoff. If this occurs frequently, limit Lambda concurrency:

```bash
aws lambda put-function-concurrency \
  --function-name fsxn-datadog-integration-shipper \
  --reserved-concurrent-executions 5
```

## Cleanup

```bash
bash integrations/datadog/scripts/cleanup.sh          # stacks only
bash integrations/datadog/scripts/cleanup.sh --all    # + secret, layer, S3 test data
```

The FSx for ONTAP S3 Access Point and the audit bucket are shared resources and
are **not** removed. Detach the access point separately if it is no longer needed:

```bash
aws fsx detach-and-delete-s3-access-point \
  --name fsxn-audit-ap --region ap-northeast-1
```

## Related Documents

- [Field Mapping](field-mapping.md) — Datadog attribute ↔ ONTAP field reference
- [EMS / FPolicy Setup](ems-fpolicy-setup.md) — real-time event sources
- [Log Archive Setup](log-archive-setup.md) — long-term retention in S3
- [Snapshot Remediation](snapshot-remediation-setup.md) — automated containment
- [Production Checklist](production-checklist.md) — pre-go-live verification
- [SPL / CQL Comparison](spl-cql-comparison.md) — query translation reference
- [Pipeline SLO](../../../../docs/en/pipeline-slo.md) — SLO definitions and Go/No-Go criteria
- [DLQ Replay Runbook](../../../../docs/en/runbooks/dlq-replay.md)
