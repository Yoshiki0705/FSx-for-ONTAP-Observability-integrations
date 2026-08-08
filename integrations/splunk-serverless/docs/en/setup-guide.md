# Splunk Serverless Setup Guide

🌐 [日本語](../ja/setup-guide.md)

## Overview

Setup guide for the serverless integration that ships Amazon FSx for NetApp ONTAP audit logs to Splunk via HEC (HTTP Event Collector).

> **Difference from existing pattern**: Replaces the [EC2-based approach](https://aws.amazon.com/jp/blogs/news/auditing-user-and-administrative-actions-on-amazon-fsx-for-netapp-ontap-using-splunk/) (syslog-ng + Universal Forwarder) with a fully serverless architecture.

## Prerequisites

Ensure the following are ready before proceeding:

- **AWS Account**: FSx for ONTAP running
- **Splunk Account**: Splunk Enterprise or Splunk Cloud (HEC enabled)
- **AWS CLI v2**: Configured (`aws configure` completed)
- **FSx for ONTAP Audit Logs**: Outputting to an S3 bucket
- **Prerequisites Stack**: [Prerequisites](../../../../docs/en/prerequisites.md) deployed

## Step 1: Create Splunk HEC Token

### 1.1 Issue HEC Token in Splunk

1. Log in to Splunk Web
2. Navigate to **Settings** → **Data Inputs** → **HTTP Event Collector**
3. Confirm HEC is enabled in **Global Settings**
4. Click **New Token**
5. Configuration:
   - Name: `fsxn-audit-log-shipper`
   - Source type: `fsxn:ontap:audit`
   - Index: `fsxn_audit`
6. Copy the generated HEC token (UUID format)

> **Token format**: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` (8-4-4-4-12 hexadecimal string)

### 1.2 Create Splunk Index

If the Index specified during HEC token creation does not exist, create it:

```bash
# Splunk CLI（Splunk Enterprise の場合）
splunk add index fsxn_audit -maxDataSize auto_high_volume
```

For Splunk Cloud, create the Index from the admin console.

## Step 2: Register in AWS Secrets Manager

Store the HEC token securely in AWS Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name "splunk/fsxn-hec-token" \
  --description "Splunk HEC Token for FSx for ONTAP audit log integration" \
  --secret-string "YOUR_HEC_TOKEN" \
  --region ap-northeast-1
```

### Verify Registration

Confirm the token was stored correctly:

```bash
aws secretsmanager get-secret-value \
  --secret-id "splunk/fsxn-hec-token" \
  --region ap-northeast-1 \
  --query 'SecretString' \
  --output text
```

Verify the output is in UUID format (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).

## Step 3: Deploy CloudFormation

### Recommended: use the deploy script

The script deploys the stack **and** uploads the real Lambda code. The
CloudFormation template cannot carry the handler inline, so this is the only
one-step path to a working integration.

```bash
export SPLUNK_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export SPLUNK_HEC_ENDPOINT="..."
export S3_BUCKET_NAME="..."

bash integrations/splunk-serverless/scripts/deploy.sh
```

First run takes **3-5 minutes**, almost all of it CloudFormation creating the
IAM role, Lambda, scheduler and alarms. Re-runs of an unchanged stack finish in
seconds. Run `--help` for every supported variable.

Add `--all` to also deploy the EMS and FPolicy stacks.

> Set `ALARM_TOPIC_ARN` to an SNS topic ARN before running the script to make
> the CloudWatch alarms actionable. Left unset, the alarms are created without
> notification actions: visible in the console, paging nobody.

### Alternative: deploy CloudFormation by hand

### 3.1 Deploy the Stack

```bash
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template.yaml \
  --stack-name fsxn-splunk-integration \
  --parameter-overrides \
    S3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    SplunkHecTokenSecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk/fsxn-hec-token-XXXXXX \
    SplunkHecEndpoint=https://your-splunk-instance:8088 \
    S3BucketName=your-audit-log-bucket \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

### 3.2 Verify Deployment

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-splunk-integration \
  --region ap-northeast-1 \
  --query 'Stacks[0].StackStatus' \
  --output text
```

Verify the output is `CREATE_COMPLETE` or `UPDATE_COMPLETE`.

### Upload the real Lambda code (required)

**The stack alone is not functional.** `template.yaml` ships a placeholder that
raises `NotImplementedError`, because CloudFormation cannot inline a handler this
size. `scripts/deploy.sh` already does this step; if you deployed by hand, do it
now:

```bash
cd integrations/splunk-serverless/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-splunk-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-splunk-integration-shipper \
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
| `S3AccessPointArn` | ARN of the S3 Access Point for FSx for ONTAP audit logs |
| `SplunkHecTokenSecretArn` | ARN of the Secrets Manager secret containing the Splunk HEC token |
| `SplunkHecEndpoint` | Splunk HEC endpoint URL (e.g., https://splunk.example.com:8088) |
| `S3BucketName` | S3 bucket name for event notification |
| `EmsApiKeySecretArn` | ARN of the Secrets Manager secret containing the EMS webhook API key |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `S3KeyPrefix` | `''` (empty) | S3 key prefix filter for audit log objects |
| `SplunkIndex` | `fsxn_audit` | Splunk index for audit logs |
| `SplunkSourcetype` | `fsxn:ontap:audit` | Splunk sourcetype assigned to every shipped audit event |
| `VerifySSL` | `true` | Verify SSL certificates (set false for self-signed certs) |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 4: Send Test Events

### 4.1 Manually Invoke the Lambda Function

Invoke Lambda using a sample S3 event:

```bash
aws lambda invoke \
  --function-name fsxn-splunk-integration-shipper \
  --payload file://integrations/splunk-serverless/tests/test_data/sample_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json

cat response.json
```

Expected output:

```json
{"statusCode": 200, "body": {"total_logs": 5, "total_shipped": 5}}
```

### 4.2 Check CloudWatch Logs

```bash
aws logs tail \
  /aws/lambda/fsxn-splunk-integration-shipper \
  --since 5m \
  --region ap-northeast-1
```

Verify that the logs contain `Successfully shipped`.

## Step 5: Verify Log Arrival in Splunk Search

### 5.1 Execute SPL Query

Run the following query in Splunk Search:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m
```

### 5.2 Field Verification Checklist

Verify that arrived events contain the following fields:

| Field | Description | Required |
|-------|-------------|----------|
| `host` | SVM name | ✅ |
| `source` | Source identifier | ✅ |
| `sourcetype` | `fsxn:ontap:audit` | ✅ |
| `index` | `fsxn_audit` | ✅ |
| `event_type` | Event type | ✅ |
| `user` | Operating user | ✅ |
| `operation` | Operation type | ✅ |
| `path` | File path | ✅ |
| `result` | Operation result | ✅ |
| `svm` | SVM name | ✅ |

## Step 6: E2E Verification Procedure

Follow these steps to verify end-to-end operation. Maximum wait time per step is **5 minutes**.

### 6.1 Send Test Event

```bash
aws lambda invoke \
  --function-name fsxn-splunk-integration-shipper \
  --payload file://integrations/splunk-serverless/tests/test_data/sample_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json
```

**Expected result**: `statusCode` is `200`

### 6.2 Verify CloudWatch Logs (max wait: 1 minute)

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-splunk-integration-shipper \
  --start-time $(date -d '5 minutes ago' +%s000 2>/dev/null || date -v-5M +%s000) \
  --filter-pattern "Successfully shipped" \
  --region ap-northeast-1
```

**Expected result**: Log entries containing `Successfully shipped` are displayed

### 6.3 Verify Splunk Search (max wait: 5 minutes)

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m
```

**Expected result**: At least 1 event is returned

### 6.4 Measure Latency

Record the difference between the S3 object creation time and Splunk's `_indextime`. Logs typically become searchable within 30–120 seconds.

### 6.5 Capture Screenshots

Capture the following screenshots and save them to `docs/screenshots/splunk/`:

- Lambda CloudWatch Logs (showing `Successfully shipped`)
- Splunk Search results (SPL query, result count, expanded event)
- Splunk dashboard (panel displaying FSx for ONTAP audit log data)

![Splunk search showing FSx for ONTAP audit events](../../screenshots/splunk-e2e-search-fsxn-audit-xml.png)

> Captured against Splunk Enterprise in Docker. See
> [Splunk Verification Results](../../../../docs/en/verification-results-splunk.md)
> for the scope of that run.

## Troubleshooting

### Network Connectivity Issues

**Symptom**: Lambda cannot connect to the Splunk HEC endpoint

**Diagnosis**:

```bash
# HEC エンドポイントへの接続テスト
curl -k -s -o /dev/null -w "%{http_code}" \
  https://your-splunk-instance:8088/services/collector/health
```

**Expected result**: HTTP `200` is returned

**Resolution**:
- If Splunk is in a VPC: Deploy Lambda in the same VPC with a NAT Gateway
- If Splunk Cloud: Verify the HEC endpoint is publicly accessible
- Verify the security group allows outbound traffic from Lambda to port 8088

### Invalid Token

**Symptom**: Lambda logs show `Invalid token format` or Splunk returns HTTP 403

**Diagnosis**:

```bash
# トークン形式の確認（UUID: 8-4-4-4-12）
aws secretsmanager get-secret-value \
  --secret-id "splunk/fsxn-hec-token" \
  --region ap-northeast-1 \
  --query 'SecretString' \
  --output text | grep -E '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
```

**Resolution**:
- Verify the token is in UUID format (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
- Verify the HEC token is enabled in Splunk
- Verify the Index assigned to the token is correct

### SSL Certificate Issues

**Symptom**: Lambda logs show `SSL: CERTIFICATE_VERIFY_FAILED` error

**Diagnosis**:

```bash
# SSL 証明書の確認
openssl s_client -connect your-splunk-instance:8088 -showcerts </dev/null 2>/dev/null | openssl x509 -noout -dates
```

**Resolution**:
- If using a self-signed certificate: Set `VerifySSL=false` in the CloudFormation parameters

```bash
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template.yaml \
  --stack-name fsxn-splunk-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    SplunkHecTokenSecretArn=$SECRET_ARN \
    SplunkHecEndpoint=https://your-splunk-instance:8088 \
    S3BucketName=$BUCKET_NAME \
    VerifySSL=false \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

> **Note**: Using proper SSL certificates in production is strongly recommended. Use `VerifySSL=false` only in test environments.

### IAM Permission Issues

**Symptom**: Lambda logs show `AccessDenied` error

**Diagnosis**:

```bash
# CloudWatch Logs で AccessDenied を検索
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-splunk-integration-shipper \
  --filter-pattern "AccessDenied" \
  --region ap-northeast-1
```

**Resolution**:
- Verify the Lambda execution role has the following permissions:
  - `secretsmanager:GetSecretValue` (HEC token retrieval)
  - `s3:GetObject` (object read via S3 Access Point)
  - `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` (CloudWatch Logs)
- Verify the S3 Access Point resource policy allows the Lambda role
- Verify the IAM policy resource ARN includes the `/object/*` suffix:

```
arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap/object/*
```

### Comprehensive Checklist When Logs Don't Arrive in Splunk

1. Is Lambda being invoked successfully? (Check CloudWatch Logs)
2. Can the HEC endpoint be reached? (curl test)
3. Is the HEC token valid? (UUID format + Enabled in Splunk)
4. Is the SSL certificate valid? (Use `VerifySSL=false` for self-signed)
5. Are IAM permissions correct? (Check for AccessDenied)
6. Are messages accumulating in the DLQ?

```bash
aws sqs get-queue-attributes \
  --queue-url $(aws cloudformation describe-stack-resource \
    --stack-name fsxn-splunk-integration \
    --logical-resource-id DeadLetterQueue \
    --query 'StackResourceDetail.PhysicalResourceId' \
    --output text) \
  --attribute-names ApproximateNumberOfMessages \
  --region ap-northeast-1
```

## Verify the deployment

```bash
bash integrations/splunk-serverless/scripts/verify.sh
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
bash integrations/splunk-serverless/scripts/cleanup.sh          # stacks only
bash integrations/splunk-serverless/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/splunk-serverless/scripts/cleanup.sh --all -y  # non-interactive
```

Shared resources (S3 access point, audit log bucket, FPolicy Fargate stack,
prerequisites stack) are not touched. See
[Deploying a vendor integration](../../../../docs/en/vendor-deployment-common.md)
for the deletion order and what is retained on purpose.

## Related Documents

- [Deploying a vendor integration](../../../../docs/en/vendor-deployment-common.md) — steps shared by every vendor
- [Prerequisites](../../../../docs/en/prerequisites.md) — FSx for ONTAP, audit logging, S3 access point
- [Deployment guide](../../../../docs/en/deployment-guide.md) — stack catalog, VPC endpoint conflicts, cost
