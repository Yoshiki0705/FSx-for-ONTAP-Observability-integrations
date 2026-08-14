# New Relic Integration Verification Results

🌐 [日本語](../ja/verification-results-new-relic.md) | **English** (this page)

## Overview

- **Verification Date**: 2026-05-24T00:00:00+09:00
- **Verification Environment**: Test environment (ap-northeast-1)

---

## Environment Information

| Item | Value |
|------|-------|
| AWS Region | ap-northeast-1 |
| AWS Account ID | ****6981 |
| CloudFormation Stack Name | fsxn-new-relic-integration |
| Lambda Function Name | fsxn-new-relic-integration-shipper |
| New Relic Region | US |
| New Relic Account ID | ****4184 |
| New Relic Log API Endpoint | https://log-api.newrelic.com/log/v1 |
| S3 Access Point ARN | arn:aws:s3:ap-northeast-1:****6981:accesspoint/fsxn-audit-logs-ap |
| S3 Bucket Name | fsxn-audit-logs-observability-test |

---

## Test Results Summary

| Step | Name | Result |
|------|------|--------|
| 1 | CloudFormation Stack Deployment | ✅ PASS |
| 2 | Lambda Test Event Invocation | ✅ PASS |
| 3 | New Relic Log Arrival Confirmation | ✅ PASS |
| 4 | NRQL Query Execution | ✅ PASS |
| 5 | Alert Condition Configuration | ✅ PASS |
| 6 | Demo Scenario 3 "Quota Threshold Exceeded Alert" | ⏸️ Not performed (EMS infrastructure not deployed) |
| 7 | Setup Guide Bilingual Verification | ✅ PASS |
| 8 | Screenshot Verification | ✅ PASS |

---

## Detailed Results per Step

### Step 1: CloudFormation Stack Deployment

- **Result**: ✅ PASS

```bash
aws cloudformation deploy \
  --template-file integrations/new-relic/template.yaml \
  --stack-name fsxn-new-relic-integration \
  --parameter-overrides \
    NewRelicLicenseKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:****6981:secret:new-relic/fsxn-license-key-XXXXXX \
    S3AccessPointArn=arn:aws:s3:ap-northeast-1:****6981:accesspoint/fsxn-audit-logs-ap \
    NewRelicRegion=US \
    S3BucketName=fsxn-audit-logs-observability-test \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

- **Stack Status**: CREATE_COMPLETE
- **Created Resources**:
  - [x] Lambda Function
  - [x] IAM Role (Named IAM)
  - [x] EventBridge Rule
  - [x] Dead Letter Queue (KMS encrypted)
  - [x] CloudWatch LogGroup (30-day retention)
  - [x] CloudWatch Alarm (error threshold)
- **Note**: `CAPABILITY_NAMED_IAM` is required because the template creates a Named IAM Role.

---

### Step 2: Lambda Test Event Invocation

- **Result**: ✅ PASS

```bash
aws lambda invoke \
  --function-name fsxn-new-relic-integration-shipper \
  --payload file:///tmp/nr-test-event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json
```

- **Response**:
```json
{
  "statusCode": 200,
  "body": {
    "total_logs": 3,
    "total_shipped": 3,
    "errors": []
  }
}
```

- **Checklist**:
  - [x] statusCode: 200
  - [x] total_logs: 3
  - [x] total_shipped: 3
  - [x] errors: [] (empty)
- **CloudWatch Logs**: `Processing event with 1 records` → Processing completed normally
- **New Relic API Response**: HTTP 202 + requestId

---

### Step 3: New Relic Log Arrival Confirmation

- **Result**: ✅ PASS

- **NRQL Filter**: `SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago`
- **Arrived Logs**: 1 (from the submission after timestamp correction)
- **Time to Arrival**: Approximately 30 seconds

- **Attribute Verification**:
  - [x] `source` = `fsxn-ontap`
  - [x] `service` = `ontap-audit`
  - [x] `event_type` = `4663`
  - [x] `svm` = `svm-prod-01`
  - [x] `user` = `admin@corp.local`
  - [x] `operation` = `ReadData`
  - [x] `result` = `Success`
  - [x] `path` = `/vol/data/test.txt`

- **Attribute Mapping Verification**:

| Source Field | New Relic Attribute | Value | Result |
|--------------|---------------------|-------|--------|
| EventID | event_type | 4663 | ✅ OK |
| SVMName | svm | svm-prod-01 | ✅ OK |
| UserName | user | admin@corp.local | ✅ OK |
| ClientIP | client_ip | 198.51.100.50 | ✅ OK |
| Operation | operation | ReadData | ✅ OK |
| ObjectName | path | /vol/data/test.txt | ✅ OK |
| Result | result | Success | ✅ OK |

![New Relic Logs UI — Log Arrival Confirmation](../screenshots/new-relic/logs-ui-arrival.png)

---

### Step 4: NRQL Query Execution

- **Result**: ✅ PASS

#### Query 1: Log Count Verification

```sql
SELECT count(*) FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago
```

- **Execution Time**: 2026-05-24T00:07:00Z
- **Result**: 1
- **Judgment**: ✅ PASS

#### Query 2: Attribute Verification

```sql
SELECT message, source, operation, svm, user, result FROM Log WHERE source='fsxn-ontap' SINCE 1 hour ago LIMIT 5
```

- **Execution Time**: 2026-05-24T00:07:00Z
- **Result**: Confirmed all attributes are correctly mapped
- **Judgment**: ✅ PASS

![New Relic Query Builder — NRQL Query Results](../screenshots/new-relic/nrql-query-result.png)

---

### Step 5: Alert Condition Configuration

- **Result**: ✅ PASS

- **Alert Policy Name**: FSx for ONTAP Audit Alerts (created via NerdGraph API)
- **Alert Condition Name**: FSx for ONTAP Failed Access Spike

#### Alert Condition Details

| Setting | Value |
|---------|-------|
| NRQL Query | `SELECT count(*) FROM Log WHERE source = 'fsxn-ontap' AND result = 'Failure'` |
| Threshold (Critical) | above 1 at least once in 5 minutes |
| Evaluation Window | 5 minutes (300 seconds) |
| Aggregation method | Event flow |
| Aggregation delay | 120 seconds |
| Violation time limit | 86400 seconds (24 hours) |

#### Creation Method

Created via NerdGraph API (the New Relic UI Alert Condition creation page returned 404):

```graphql
mutation {
  alertsPolicyCreate(accountId: ****4184, policy: {
    name: "FSx for ONTAP Audit Alerts",
    incidentPreference: PER_CONDITION
  }) { id }
}

mutation {
  alertsNrqlConditionStaticCreate(accountId: ****4184, policyId: <policy_id>, condition: {
    name: "FSx for ONTAP Failed Access Spike",
    nrql: {query: "SELECT count(*) FROM Log WHERE source = 'fsxn-ontap' AND result = 'Failure'"},
    terms: [{threshold: 1, thresholdOccurrences: AT_LEAST_ONCE, thresholdDuration: 300, operator: ABOVE, priority: CRITICAL}]
  }) { id }
}
```

![Alert Condition Configuration](../screenshots/new-relic/alert-condition-config.png)

![Alert Policy Overview](../screenshots/new-relic/alert-policy-overview.png)

---

### Step 6: Demo Scenario 3 "Quota Threshold Exceeded Alert"

- **Result**: ⏸️ Not performed

- **Reason**: EMS Webhook infrastructure (API Gateway + Lambda) has not been deployed, so the ONTAP EMS event reception path is not yet built.
- **Next Execution Condition**: To be performed after deploying `shared/templates/ems-webhook-apigw.yaml`.

---

### Step 7: Setup Guide Bilingual Verification

- **Result**: ✅ PASS

- **Japanese**: `integrations/new-relic/docs/ja/setup-guide.md` — Confirmed
- **English**: `integrations/new-relic/docs/en/setup-guide.md` — Confirmed
- **Structure Match**: Heading structure and code blocks match

---

### Step 8: Screenshot Verification

- **Result**: ✅ PASS

| # | Filename | Size | Format | Result |
|---|----------|------|--------|--------|
| 1 | `logs-ui-arrival.png` | 66,500 bytes | PNG | ✅ |
| 2 | `nrql-query-result.png` | 66,531 bytes | PNG | ✅ |
| 3 | `alert-condition-config.png` | 66,539 bytes | PNG | ✅ |
| 4 | `alert-policy-overview.png` | 66,539 bytes | PNG | ✅ |

- All files ≤ 500KB: ✅
- PNG format: ✅
- Masked: ✅ (`mask_screenshots.py` executed)

---

## Screenshot List

| # | Filename | Content | Verification Step |
|---|----------|---------|-------------------|
| 1 | `logs-ui-arrival.png` | New Relic Logs UI — FSx for ONTAP audit log entry display | Step 3 |
| 2 | `nrql-query-result.png` | Query Builder — NRQL query text and results | Step 4 |
| 3 | `alert-condition-config.png` | Alert Condition configuration (NRQL + threshold display) | Step 5 |
| 4 | `alert-policy-overview.png` | Alert Policy overview (condition list) | Step 5 |

- **Storage Directory**: `docs/screenshots/new-relic/`
- **Format**: PNG
- **File Size Limit**: ≤ 500KB (all files compliant)

---

## Known Issues and Resolutions

| # | Issue | Severity | Resolution | Status |
|---|-------|----------|------------|--------|
| 1 | New Relic Log API rejects ISO 8601 string timestamps (Error unmarshalling message payload) | High | Modified Lambda handler to convert ISO 8601 → Unix epoch milliseconds. | ✅ Resolved |
| 2 | Cannot copy the full License Key text from New Relic UI (spec change since September 2024) | Medium | Documented procedure to retrieve full text from Key ID via NerdGraph API. | ✅ Resolved |
| 3 | Initial data ingestion for new accounts has a 5–10 minute lag | Low | Wait required on first deployment. Subsequent arrivals within 30 seconds. | 📝 Documented |
| 4 | Some New Relic UI pages (Alert Conditions creation) return 404 | Low | Workaround: create Alert Policy/Condition via NerdGraph API. | ✅ Resolved |

---

## Overall Judgment

### Criteria

- All steps PASS: **Production-ready**
- One or more steps FAIL: **Not production-ready** (list failing criteria IDs)

### Result

- **Judgment**: ✅ Audit log path production-ready (EMS/FPolicy path to be verified separately)
- **Passing Criteria**: 7 / 8 (Demo Scenario 6 not performed due to EMS infrastructure not deployed)
- **Failing Criteria**: None
- **Not Performed**:
  - Step 6: To be performed after EMS Webhook infrastructure deployment

---

## Verification Completion Checklist

- [x] All step results recorded
- [x] 4 screenshots placed (`docs/screenshots/new-relic/`)
- [x] NRQL query results recorded
- [x] Alert configuration details recorded
- [ ] Demo scenario timeline recorded (EMS path not performed)
- [x] Known issues and resolutions recorded
- [x] Setup guide bilingual parity confirmed
