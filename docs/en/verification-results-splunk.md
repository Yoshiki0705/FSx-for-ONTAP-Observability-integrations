# Splunk Serverless Integration Verification Results

🌐 [日本語](../ja/verification-results-splunk.md) | **English** (this page)

> **This record is an unfilled template.** Every judgment cell below still reads
> `<PASS/FAIL>` and the environment fields are placeholders — no verification run
> has been written up here yet. The audit log path itself was exercised: see
> `integrations/splunk-serverless/screenshots/splunk-e2e-search-fsxn-audit-xml.png`
> for the Splunk search showing FSx for ONTAP audit events. The EMS and FPolicy
> paths have handlers and stacks but no recorded end-to-end run, which is why the
> [telemetry path coverage](README.md#telemetry-path-coverage) matrix marks them
> 🔧 rather than ✅.

- **Verification Date**: <verification-date>
- **Verifier**: <verifier-name> / <role>

### Verification Environment

- **AWS Region**: ap-northeast-1
- **CloudFormation Stack Name**: <stack-name>
- **Lambda Function Name**: fsxn-splunk-log-shipper
- **Splunk HEC Endpoint**: <HEC endpoint URL>
- **Splunk Index**: fsxn_audit
- **FSx for ONTAP File System**: <file-system-id>
- **S3 Access Point**: <S3 Access Point ARN>
- **HEC Token Secret ARN**: <Secrets Manager ARN>

---

## Verification Steps

| Step # | Step Name | Command | Expected Result | Actual Result | Judgment |
|:---:|---|---|---|---|:---:|
| 1 | CloudFormation Stack Deployment | `aws cloudformation deploy --template-file integrations/splunk-serverless/template.yaml --stack-name <stack-name> ...` | CREATE_COMPLETE | <actual-result> | <PASS/FAIL> |
| 2 | HEC Token Validation | `python3 scripts/verification/splunk_token_validator.py --secret-arn <ARN>` | UUID format match | <actual-result> | <PASS/FAIL> |
| 3 | Lambda Test Event Invocation | `aws lambda invoke --function-name fsxn-splunk-log-shipper --payload file://integrations/splunk-serverless/tests/test_data/sample_s3_event.json response.json` | statusCode: 200, total_shipped > 0 | <actual-result> | <PASS/FAIL> |
| 4 | CloudWatch Logs Verification | `aws logs filter-log-events --log-group-name /aws/lambda/fsxn-splunk-log-shipper --filter-pattern "Successfully shipped"` | "Successfully shipped" log output | <actual-result> | <PASS/FAIL> |
| 5 | Splunk Search Log Arrival Confirmation | SPL query execution (see below) | At least 1 event returned | <actual-result> | <PASS/FAIL> |
| 6 | Field Validation | Expand event in Splunk Search | All required fields non-empty | <actual-result> | <PASS/FAIL> |
| 7 | Screenshot Verification | `python3 scripts/verification/splunk_screenshot_validator.py docs/screenshots/splunk/` | 3 files, naming convention compliant, ≤500KB | <actual-result> | <PASS/FAIL> |
| 8 | Setup Guide Bilingual Verification | `python3 scripts/verification/bilingual_comparator.py --ja integrations/splunk-serverless/docs/ja/setup-guide.md --en integrations/splunk-serverless/docs/en/setup-guide.md` | Heading structure match | <actual-result> | <PASS/FAIL> |

---

## Detailed Verification Steps

### Step 1: CloudFormation Stack Deployment

- **Result**: <PASS/FAIL>

```bash
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template.yaml \
  --stack-name <stack-name> \
  --parameter-overrides \
    S3AccessPointArn=<S3 Access Point ARN> \
    HecTokenSecretArn=<Secrets Manager ARN> \
    SplunkHecEndpoint=<HEC endpoint URL> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

- **Stack Status**: <CREATE_COMPLETE / FAILED>
- **Created Resources**: Lambda Function, IAM Role, DLQ, CloudWatch Alarms, EventBridge Rule

---

### Step 2: HEC Token Validation

- **Result**: <PASS/FAIL>

```bash
python3 scripts/verification/splunk_token_validator.py \
  --secret-arn <Secrets Manager ARN>
```

- **Token Format**: <UUID format match / mismatch>
- **Validation Output**: <output>

---

### Step 3: Lambda Test Event Invocation

- **Result**: <PASS/FAIL>

```bash
aws lambda invoke \
  --function-name fsxn-splunk-log-shipper \
  --payload file://integrations/splunk-serverless/tests/test_data/sample_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json
```

- **Response**:
```json
{"statusCode": <status-code>, "body": {"total_logs": <count>, "total_shipped": <count>, "errors": []}}
```

- **Checklist**:
  - [ ] statusCode: 200
  - [ ] total_logs > 0
  - [ ] total_shipped == total_logs
  - [ ] errors: [] (empty)

---

### Step 4: CloudWatch Logs Verification

- **Result**: <PASS/FAIL>

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-splunk-log-shipper \
  --filter-pattern "Successfully shipped" \
  --start-time $(date -d '15 minutes ago' +%s000) \
  --region ap-northeast-1
```

- **Checklist**:
  - [ ] Log line containing "Successfully shipped" exists
  - [ ] Timestamp is after the test event invocation

---

### Step 5: Splunk Search Log Arrival Confirmation

- **Result**: <PASS/FAIL>

Execute the following SPL query in Splunk Search:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m
```

- **Returned Events**: <count>
- **Time to Arrival**: <seconds>

- **Checklist**:
  - [ ] At least 1 event returned
  - [ ] sourcetype is `fsxn:ontap:audit`
  - [ ] index is `fsxn_audit`

---

### Step 6: Field Validation

- **Result**: <PASS/FAIL>

Expand an event in Splunk Search and verify the following fields:

| Field Name | Expected Value | Actual Value | Required Non-empty | Result |
|---|---|---|:---:|:---:|
| host | SVM name | <actual-value> | ✅ | <PASS/FAIL> |
| source | fsxn-observability | <actual-value> | ✅ | <PASS/FAIL> |
| sourcetype | fsxn:ontap:audit | <actual-value> | ✅ | <PASS/FAIL> |
| index | fsxn_audit | <actual-value> | ✅ | <PASS/FAIL> |
| event_type | Event type | <actual-value> | ✅ | <PASS/FAIL> |
| user | Username | <actual-value> | ✅ | <PASS/FAIL> |
| operation | Operation type | <actual-value> | ✅ | <PASS/FAIL> |
| path | File path | <actual-value> | ✅ | <PASS/FAIL> |
| result | Success/Failure | <actual-value> | ✅ | <PASS/FAIL> |
| svm | SVM name | <actual-value> | ✅ | <PASS/FAIL> |

---

## Screenshot Evidence

Save the following screenshots to `docs/screenshots/splunk/`:

| # | Filename | Content | Checklist |
|---|---|---|---|
| 1 | `splunk-cloudwatch-logs-<YYYYMMDD>.png` | Lambda CloudWatch Logs showing "Successfully shipped" log line with timestamp | [ ] ≤500KB, PNG format |
| 2 | `splunk-search-results-<YYYYMMDD>.png` | Splunk Search results showing `index`, `sourcetype`, `host`, `source` fields | [ ] ≤500KB, PNG format |
| 3 | `splunk-dashboard-<YYYYMMDD>.png` | Splunk dashboard with at least one panel containing FSx for ONTAP audit log data | [ ] ≤500KB, PNG format |

![Lambda CloudWatch Logs](../screenshots/splunk/splunk-cloudwatch-logs-<YYYYMMDD>.png)

![Splunk Search Results](../screenshots/splunk/splunk-search-results-<YYYYMMDD>.png)

![Splunk Dashboard](../screenshots/splunk/splunk-dashboard-<YYYYMMDD>.png)

---

## Setup Guide Bilingual Verification

- **Result**: <PASS/FAIL>

```bash
python3 scripts/verification/bilingual_comparator.py \
  --ja integrations/splunk-serverless/docs/ja/setup-guide.md \
  --en integrations/splunk-serverless/docs/en/setup-guide.md
```

- **Heading Count**: <count> (match / mismatch)
- **Code Block Count**: <count> (match / mismatch)
- **Table Count**: <count> (match / mismatch)
- **Differences**: <count>

| # | Section | Diff Type | Details |
|---|---------|-----------|---------|
| - | - | - | - |

---

## E2E Latency Measurement

| Measurement | Value |
|---|---|
| S3 Object Creation Timestamp | <timestamp> |
| Lambda Invocation Timestamp | <timestamp> |
| Splunk `_indextime` | <timestamp> |
| **E2E Latency (S3 creation → Splunk searchable)** | **<latency> seconds** |

### Latency Breakdown

| Segment | Duration |
|---|---|
| S3 Object Creation → EventBridge Trigger | <seconds> s |
| EventBridge → Lambda Invocation | <seconds> s |
| Lambda Processing (S3 read + HEC submission) | <seconds> s |
| HEC Receipt → Splunk Index Complete | <seconds> s |
| **Total** | **<latency> seconds** |

---

## Detected Issues and Resolutions

| # | Issue | Severity | Resolution | Status |
|---|-------|----------|------------|--------|
| 1 | <issue> | <High/Medium/Low> | <resolution> | <✅ Resolved / 📝 Documented / 🔄 In Progress> |

---

## Troubleshooting Log

Items to check if no events are returned by SPL query within 15 minutes:

| # | Check Item | Command/Procedure | Result |
|---|-----------|-------------------|--------|
| 1 | Lambda invocation confirmation | Check log streams in CloudWatch Logs | <result> |
| 2 | HEC endpoint connectivity | `curl -k https://<HEC_ENDPOINT>:8088/services/collector/health` | <result> |
| 3 | HEC token validity | `curl -k -H "Authorization: Splunk <TOKEN>" https://<HEC_ENDPOINT>:8088/services/collector/event -d '{"event":"test"}'` | <result> |
| 4 | Lambda IAM permissions | Check for access denied errors in CloudWatch Logs | <result> |
| 5 | S3 Access Point connectivity | `aws s3api list-objects-v2 --bucket <AP_ARN> --max-items 1` | <result> |

---

## Verification Summary

| Step | Name | Result |
|------|------|--------|
| 1 | CloudFormation Stack Deployment | <PASS/FAIL> |
| 2 | HEC Token Validation | <PASS/FAIL> |
| 3 | Lambda Test Event Invocation | <PASS/FAIL> |
| 4 | CloudWatch Logs Verification | <PASS/FAIL> |
| 5 | Splunk Search Log Arrival Confirmation | <PASS/FAIL> |
| 6 | Field Validation | <PASS/FAIL> |
| 7 | Screenshot Verification | <PASS/FAIL> |
| 8 | Setup Guide Bilingual Verification | <PASS/FAIL> |

**Overall Judgment**: <✅ PASS / ❌ FAIL> (E2E verification <complete / incomplete>)
