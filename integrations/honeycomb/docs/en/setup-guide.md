# Honeycomb Setup Guide

🌐 [日本語](../ja/setup-guide.md)

## Overview

Setup guide for shipping FSx for ONTAP audit logs to Honeycomb Events API.

## Prerequisites

- Honeycomb account
- [Prerequisites stack](../../../../docs/en/prerequisites.md) deployed

## Step 1: Prepare Honeycomb API Key

1. Honeycomb → **Team Settings** → **API Keys**
2. **Create API Key** → Permissions: `Send Events`
3. Dataset: `fsxn-audit` (auto-created on first event)

```bash
aws secretsmanager create-secret \
  --name "honeycomb/fsxn-api-key" \
  --secret-string '{"api_key":"YOUR_HONEYCOMB_API_KEY"}' \
  --region ap-northeast-1
```

## Step 2: Deploy CloudFormation

### Recommended: use the deploy script

The script deploys the stack **and** uploads the real Lambda code. The
CloudFormation template cannot carry the handler inline, so this is the only
one-step path to a working integration.

```bash
export HONEYCOMB_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export S3_BUCKET_NAME="..."

bash integrations/honeycomb/scripts/deploy.sh
```

First run takes **3-5 minutes**, almost all of it CloudFormation creating the
IAM role, Lambda, scheduler and alarms. Re-runs of an unchanged stack finish in
seconds. Run `--help` for every supported variable.

Add `--all` to also deploy the EMS and FPolicy stacks. Set
`FPOLICY_SQS_QUEUE_ARN` to the ingestion queue ARN from
`shared/templates/fpolicy-apigw.yaml` to enable the primary FPolicy trigger path
(Fargate → SQS → Lambda); without it the FPolicy stack uses the secondary
EventBridge rule only. See the
[telemetry path coverage matrix](../../../../README.md#telemetry-path-coverage).

> Set `ALARM_TOPIC_ARN` to an SNS topic ARN before running the script to make
> the CloudWatch alarms actionable. Left unset, the alarms are created without
> notification actions: visible in the console, paging nobody.

### Alternative: deploy CloudFormation by hand

```bash
aws cloudformation deploy \
  --template-file integrations/honeycomb/template.yaml \
  --stack-name fsxn-honeycomb-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    HoneycombApiKeySecretArn=arn:aws:secretsmanager:... \
    HoneycombDataset=fsxn-audit \
    S3BucketName=$BUCKET_NAME \
  --capabilities CAPABILITY_IAM
```

### Upload the real Lambda code (required)

**The stack alone is not functional.** `template.yaml` ships a placeholder that
raises `NotImplementedError`, because CloudFormation cannot inline a handler this
size. `scripts/deploy.sh` already does this step; if you deployed by hand, do it
now:

```bash
cd integrations/honeycomb/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-honeycomb-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-honeycomb-integration-shipper \
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
| `S3AccessPointArn` | FSx for ONTAP S3 Access Point ARN |
| `HoneycombApiKeySecretArn` | Secrets Manager ARN for Honeycomb API key |
| `S3BucketName` | S3 bucket name for EventBridge rule matching |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `HoneycombDataset` | `fsxn-audit` | Honeycomb dataset name |
| `HoneycombApiUrl` | `https://api.honeycomb.io` | Honeycomb API base URL |
| `S3KeyPrefix` | `''` (empty) | S3 key prefix filter for audit log objects |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 3: Verify in Honeycomb

1. Dataset: `fsxn-audit`
2. Query: `GROUP BY operation, VISUALIZE COUNT`
3. Filter: `result = Failure`

## Why Honeycomb

- Excellent for high-cardinality data exploration
- BubbleUp auto-detects anomalous patterns
- SLO tracking for file access success rates

## Verify the deployment

```bash
bash integrations/honeycomb/scripts/verify.sh
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
bash integrations/honeycomb/scripts/cleanup.sh          # stacks only
bash integrations/honeycomb/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/honeycomb/scripts/cleanup.sh --all -y  # non-interactive
```

Shared resources (S3 access point, audit log bucket, FPolicy Fargate stack,
prerequisites stack) are not touched. See
[Deploying a vendor integration](../../../../docs/en/vendor-deployment-common.md)
for the deletion order and what is retained on purpose.

## Related Documents

- [Deploying a vendor integration](../../../../docs/en/vendor-deployment-common.md) — steps shared by every vendor
- [Prerequisites](../../../../docs/en/prerequisites.md) — FSx for ONTAP, audit logging, S3 access point
- [Deployment guide](../../../../docs/en/deployment-guide.md) — stack catalog, VPC endpoint conflicts, cost
