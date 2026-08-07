# CrowdStrike Falcon LogScale Setup Guide

🌐 [日本語](../ja/setup-guide.md) | **English** (this page)

## Prerequisites

- CrowdStrike Falcon LogScale account (Cloud or Self-hosted)
- A LogScale repository created for FSx audit logs
- An Ingest Token associated with the repository
- AWS account with FSx for ONTAP (audit logging enabled)
- S3 Access Point configured for audit log access

## Step 1: Create a LogScale Repository

1. Log in to your LogScale instance
2. Navigate to **Repositories** → **New Repository**
3. Name: `fsxn-audit` (or your preferred name)
4. Retention: Configure based on compliance requirements

## Step 2: Create an Ingest Token

1. Navigate to your repository → **Settings** → **Ingest tokens**
2. Click **Add token**
3. Name: `fsxn-lambda-shipper`
4. Parser: `json` (recommended) or create a custom parser
5. Copy the token value

## Step 3: Store Token in AWS Secrets Manager

```bash
aws secretsmanager create-secret \
  --name crowdstrike/fsxn-logscale-token \
  --secret-string "<your-ingest-token>" \
  --region ap-northeast-1
```

## Step 4: Deploy CloudFormation Stack

### Recommended: use the deploy script

The script deploys the stack **and** uploads the real Lambda code. The
CloudFormation template cannot carry the handler inline, so this is the only
one-step path to a working integration.

```bash
export FSX_S3_ACCESS_POINT_ARN="..."
export LOGSCALE_INGEST_TOKEN_SECRET_ARN="..."

bash integrations/crowdstrike/scripts/deploy.sh
```

First run takes **3-5 minutes**, almost all of it CloudFormation creating the
IAM role, Lambda, scheduler and alarms. Re-runs of an unchanged stack finish in
seconds. Run `--help` for every supported variable.

This vendor has no EMS or FPolicy handler yet, so `--all` deploys the audit log
path only and reports the other two as skipped. See the
[telemetry path coverage matrix](../../../../README.md#telemetry-path-coverage).

> Set `ALARM_TOPIC_ARN` to an SNS topic ARN before running the script to make
> the CloudWatch alarms actionable. Left unset, the alarms are created without
> notification actions: visible in the console, paging nobody.

### Alternative: deploy CloudFormation by hand

```bash
aws cloudformation deploy \
  --template-file integrations/crowdstrike/template.yaml \
  --stack-name fsxn-crowdstrike-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    LogScaleIngestTokenSecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:crowdstrike/fsxn-logscale-token \
    LogScaleUrl=https://cloud.us.humio.com \
  --capabilities CAPABILITY_NAMED_IAM
```

### Upload the real Lambda code (required)

**The stack alone is not functional.** `template.yaml` ships a placeholder that
raises `NotImplementedError`, because CloudFormation cannot inline a handler this
size. `scripts/deploy.sh` already does this step; if you deployed by hand, do it
now:

```bash
cd integrations/crowdstrike/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-crowdstrike-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-crowdstrike-integration-shipper \
  --region ap-northeast-1
```

The `-j` flag flattens paths so `ontap_audit_parser` resolves at runtime. Without
that file the handler silently falls back to JSON-only parsing, and ONTAP audit
logs — which are always XML or EVTX — arrive with no parsed fields.

### Parameter Reference

<!-- generated from template.yaml; keep in sync when parameters change -->

Required:

| Parameter | Description |
|-----------|-------------|
| `FsxS3AccessPointArn` | ARN of the S3 Access Point for FSx for ONTAP audit logs |
| `LogScaleIngestTokenSecretArn` | ARN of the Secrets Manager secret containing the LogScale Ingest Token |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LogScaleUrl` | `https://cloud.us.humio.com` | LogScale base URL (e.g., https://cloud.us.humio.com) |
| `ScheduleInterval` | `rate(5 minutes)` | EventBridge Scheduler interval for audit log polling |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `HecPath` | `/api/v1/ingest/hec` | HEC endpoint path (LogScale default /api/v1/ingest/hec, Splunk /services/collector/event) |
| `AuditLogPrefix` | `audit/` | Key prefix scanned within the FSx for ONTAP S3 Access Point (e.g. audit/ for the /audit_log directory) |
| `MaxKeysPerRun` | `100` | Maximum audit log files processed per scheduled invocation. Bounds the work per run so a large backlog drains over several runs instead of timing out mid-file; the remainder is picked up on the next schedule. |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when any alarm in this stack fires. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 5: Verify

```bash
# Check Lambda logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-crowdstrike-integration-shipper \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))") \
  --region ap-northeast-1

# Check DLQ is empty
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessages
```

In LogScale, search:
```
source = "fsxn-ontap"
```

## LogScale Parser (Optional)

For richer field extraction, create a custom parser in LogScale:

```
parseJson()
| rename(field=event_type, as=EventID)
| rename(field=client_ip, as=ClientIP)
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| HTTP 401 | Invalid ingest token | Verify token in Secrets Manager matches LogScale |
| HTTP 403 | Token lacks permissions | Check token is associated with correct repository |
| No logs in LogScale | Wrong URL or parser issue | Verify LogScale URL region matches your account |
| Lambda timeout | Network issue | Ensure Lambda has internet access (NAT GW or non-VPC) |

## References

- [LogScale Ingest API](https://library.humio.com/logscale-api/api-ingest.html)
- [LogScale HEC Endpoint](https://library.humio.com/logscale-api/log-shippers-hec.html)
- [CrowdStrike Developer Center](https://developer.crowdstrike.com/ngsiem/data-ingestion/)

## Verify the deployment

```bash
bash integrations/crowdstrike/scripts/verify.sh
```

Two layers run in sequence. First the shared AWS checks: the stack is healthy,
the deployed Lambda is the real handler rather than the placeholder, and the
schedule and checkpoint (where the stack creates them) are in place. Then a
synthetic log is sent to the vendor endpoint to prove credentials and network
reach it.

Both matter. A vendor endpoint that accepts a test log tells you nothing about
whether the pipeline is running, which is why the script exits non-zero on an
AWS-side failure even when the endpoint responded fine.

Exit codes follow `sysexits.h`: `0` pass, `69` a check failed, `78` required
configuration missing. Set `SKIP_AWS_CHECKS=1` to test only vendor reachability
before deploying.

## Cleanup

```bash
bash integrations/crowdstrike/scripts/cleanup.sh          # stacks only
bash integrations/crowdstrike/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/crowdstrike/scripts/cleanup.sh --all -y  # non-interactive
```

Shared resources (S3 access point, audit log bucket, FPolicy Fargate stack,
prerequisites stack) are not touched. See
[Deploying a vendor integration](../../../../docs/en/vendor-deployment-common.md)
for the deletion order and what is retained on purpose.

## Related Documents

- [Deploying a vendor integration](../../../../docs/en/vendor-deployment-common.md) — steps shared by every vendor
- [Prerequisites](../../../../docs/en/prerequisites.md) — FSx for ONTAP, audit logging, S3 access point
- [Deployment guide](../../../../docs/en/deployment-guide.md) — stack catalog, VPC endpoint conflicts, cost
