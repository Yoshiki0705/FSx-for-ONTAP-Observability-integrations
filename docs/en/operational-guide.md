# Operational Guide

🌐 [日本語](../ja/operational-guide.md) | **English** (this page)

## Overview

This guide covers day-to-day operations of the FSx for ONTAP observability pipeline, including monitoring, troubleshooting, and maintenance procedures.

## Monitoring

### Key CloudWatch Metrics

| Metric | Alarm Threshold | Action |
|--------|----------------|--------|
| Lambda Errors | > 5 in 10 minutes | Check CloudWatch Logs for error details |
| Lambda Throttles | ≥ 1 | Increase concurrency limit or reduce schedule frequency |
| DLQ Messages Visible | ≥ 1 | Investigate failed events, replay after fix |
| Lambda Duration | > 80% of timeout | Increase timeout or optimize batch size |

### Operational Health Dashboard

Monitor these metrics for pipeline health:
- Lambda errors and duration
- Checkpoint lag (time since last processed file)
- DLQ depth
- Vendor API response codes (via Lambda logs)
- Logs shipped per invocation

## DLQ Replay Procedure

When events fail processing and land in the DLQ:

This stack uses an SQS queue as the Lambda asynchronous invocation DLQ. Because the DLQ is attached to Lambda (not an SQS source queue), `sqs start-message-move-task` cannot redrive messages automatically.

```bash
# 1. Check DLQ message count
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessages

# 2. Inspect a sample message
aws sqs receive-message \
  --queue-url <dlq-url> \
  --max-number-of-messages 1 \
  --attribute-names All \
  --message-attribute-names All

# 3. After fixing the root cause, re-invoke Lambda manually
aws lambda invoke \
  --function-name <lambda-function-name> \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  --region ap-northeast-1 \
  replay-output.json

# 4. Delete processed DLQ messages
aws sqs delete-message \
  --queue-url <dlq-url> \
  --receipt-handle <receipt-handle-from-step-2>
```

## Checkpoint Management

The pipeline uses checkpointing to track processed audit log files.

### Reset Checkpoint (reprocess all files)

```bash
# Delete checkpoint entries for a specific SVM
aws dynamodb delete-item \
  --table-name fsxn-observability-audit-checkpoint \
  --key '{"svm_name": {"S": "svm-prod-01"}, "file_key": {"S": "LATEST"}}'
```

### Replay a Specific File

```bash
# Invoke Lambda with a specific file key
aws lambda invoke \
  --function-name fsxn-datadog-integration-shipper \
  --payload '{"Records":[{"s3":{"bucket":{"name":"<fsx-s3-ap-arn>"},"object":{"key":"audit/svm-prod-01/audit_2026.evtx"}}}]}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response.json
```

## S3 Access Point Health Monitoring

FSx for ONTAP S3 Access Points can enter a `MISCONFIGURED` state when:
- The associated file system identity cannot be resolved
- The attached volume is offline or unmounted

FSx will automatically restore the access point once the underlying issue is fixed. Monitor the access point state periodically:

```bash
# Check S3 Access Point state
aws fsx describe-data-repository-associations \
  --region ap-northeast-1 \
  --query 'Associations[*].[ResourceARN,Lifecycle]' \
  --output table
```

If the access point is MISCONFIGURED, Lambda invocations will fail with AccessDenied or timeout errors. Check:
1. Volume is online and mounted
2. File system identity (UNIX/Windows user) is resolvable
3. SVM is operational

## Secrets Manager Rotation

API keys should be rotated periodically:

```bash
# Update the secret value
aws secretsmanager put-secret-value \
  --secret-id <secret-arn> \
  --secret-string '{"api_key": "<new-key>"}'
```

Lambda caches the API key per execution context. After rotation, the new key will be picked up on the next cold start (typically within minutes).

## Cost Optimization

### Key Cost Variables

| Component | Cost Driver | Optimization |
|-----------|-------------|--------------|
| Lambda | Invocations × duration | Increase schedule interval for low-volume SVMs |
| NAT Gateway | $0.045/hr + $0.045/GB | Deploy Lambda outside VPC if possible |
| EventBridge Scheduler | $1/million invocations | Minimal cost |
| DynamoDB (checkpoint) | Pay-per-request | Minimal cost |
| Vendor ingest | Per GB/event | Filter unnecessary events at audit policy level |

### Cost Reduction Tips

1. **Disable read auditing** unless specifically needed — it generates the most volume
2. **Deploy Lambda outside VPC** to avoid NAT Gateway costs (if S3 AP is internet-accessible)
3. **Increase schedule interval** for low-activity SVMs (e.g., `rate(15 minutes)`)
4. **Filter at source** — configure ONTAP audit policies to capture only needed events

## Multi-Account Deployment

This pattern supports two deployment models:

### Per-Account (Decentralized)
- Each workload account deploys its own stack
- Audit logs stay within the account boundary
- Simpler IAM, no cross-account access needed

### Centralized (Logging Account)
- All audit logs are processed in a dedicated logging/security account
- Cross-account S3 Access Point access required
- Better for centralized security monitoring

## Upgrade Strategy

The deploy script does both steps and waits for the new code to become active,
which is what you want for an upgrade:

```bash
# Stack + code, zero-downtime
export DATADOG_API_KEY_SECRET_ARN=<secret-arn>
export FSX_S3_ACCESS_POINT_ARN=<ap-arn>
bash integrations/datadog/scripts/deploy.sh

# Handler change only — leaves the stack untouched
bash integrations/datadog/scripts/deploy.sh --code-only

# Confirm the upgrade landed
bash integrations/datadog/scripts/verify.sh
```

Equivalent manual sequence:

```bash
# Update the CloudFormation stack (zero-downtime)
aws cloudformation deploy \
  --template-file integrations/datadog/template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides <same-params> \
  --capabilities CAPABILITY_NAMED_IAM

# Update Lambda code separately
cd integrations/datadog/lambda
zip function.zip handler.py
aws lambda update-function-code \
  --function-name fsxn-datadog-integration-shipper \
  --zip-file fileb://function.zip
aws lambda wait function-updated \
  --function-name fsxn-datadog-integration-shipper
```

> **The checkpoint survives an upgrade.** It lives in SSM Parameter Store, outside
> the Lambda, so shipping resumes from the last processed key rather than
> re-sending the whole prefix. If an upgrade changes parsing behaviour and you
> want previously shipped files re-processed, reset the checkpoint to `__INIT__`
> deliberately — and expect duplicates for anything already delivered.

## Security Review Checklist

- [ ] IAM roles follow least-privilege (S3 AP ARN only, specific Secret ARN only)
- [ ] S3 Access Point policy restricts access to Lambda execution role
- [ ] Secrets Manager secret is encrypted with KMS CMK
- [ ] DLQ is encrypted with KMS
- [ ] Lambda is not in a public subnet
- [ ] CloudWatch Logs retention is configured
- [ ] No secrets in Lambda environment variables (ARN references only)
- [ ] VPC Security Groups allow minimal outbound only


## Source Health Checks

Beyond pipeline health (Lambda, DLQ, checkpoint), monitor the audit source itself:

| Check | How | Failure Indicator |
|-------|-----|-------------------|
| ONTAP audit enabled | `vserver audit show -vserver <svm> -fields state` | state != enabled |
| Rotated files exist | `list-objects-v2` via S3 AP | No new files in expected interval |
| Audit volume capacity | `volume show -vserver <svm> -volume <audit-vol> -fields used` | >80% used |
| S3 AP available | `aws fsx describe-data-repository-associations` | MISCONFIGURED state |
| Last processed file age | Check checkpoint timestamp | Stale (>2x scheduler interval) |

### Stale Pipeline Detection

If no new audit files appear for longer than expected (e.g., 2x the rotation interval), investigate:

1. Is `vserver audit` still enabled?
2. Are SACLs / NFSv4 ACL audit flags still configured on target directories?
3. Is the audit volume full?
4. Is the FSx S3 Access Point in MISCONFIGURED state?
5. Has the file system identity become unresolvable?


## EMS Pipeline Health Checks

For the event-driven EMS webhook path (Part 3), monitor:

| Check | How | Failure Indicator |
|-------|-----|-------------------|
| API Gateway 4xx/5xx | CloudWatch API Gateway metrics | Spike in error responses |
| Lambda errors | CloudWatch Lambda Errors metric | > 0 |
| EMS events shipped | Lambda logs (shipped count) | 0 when events expected |
| Datadog API failures | Lambda logs (batch failures) | RuntimeError raised |
| DLQ depth | SQS ApproximateNumberOfMessagesVisible | > 0 |
| Last webhook received | API Gateway access logs | No requests in expected window |

> Note: Absence of EMS events is often normal (no ARP alerts = good). Use a synthetic heartbeat event or periodic test invocation if you need positive confirmation that the pipeline is alive.
