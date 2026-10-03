# Sumo Logic Integration Verification Results

🌐 [日本語](../ja/verification-results-sumo-logic.md) | **English** (this page)

## Overview

- **Verification Date**: 2026-05-24T13:13:00+09:00
- **Verification Environment**: Test environment (ap-northeast-1)

---

## Environment Information

| Item | Value |
|------|-------|
| AWS Region | ap-northeast-1 |
| AWS Account ID | `****6981` |
| CloudFormation Stack Name | fsxn-sumo-logic-integration |
| Lambda Function Name | fsxn-sumo-logic-integration-shipper |
| Sumo Logic Region | JP (Tokyo) |
| Sumo Logic Endpoint | https://collectors.jp.sumologic.com/receiver/v1/http/... |
| Source Category | aws/fsxn/audit |
| Source Name | fsxn-ontap-audit |
| Source Host | fsxn-ontap |
| Collector Name | fsxn-audit-collector |
| Trial Days Remaining | 29 days |
| S3 Access Point ARN | `arn:aws:s3:ap-northeast-1:****6981:accesspoint/fsxn-audit-logs-ap` |


---

## Test Results Summary

| Step | Name | Result |
|------|------|--------|
| 1 | Sumo Logic Account Creation | ✅ PASS |
| 2 | Hosted Collector + HTTP Source Creation | ✅ PASS |
| 3 | CloudFormation Stack Deployment | ✅ PASS |
| 4 | Lambda Test Event Invocation | ✅ PASS |
| 5 | Sumo Logic Search Log Arrival Confirmation | ✅ PASS |
| 6 | Field Mapping Verification | ✅ PASS |
| 7 | Setup Guide Bilingual Verification | ✅ PASS |
| 8 | Screenshot Verification | ✅ PASS |

---

## Detailed Results per Step

### Step 1: Sumo Logic Account Creation

- **Result**: ✅ PASS

- **Method**: Google OAuth + manual form submission
- **Region**: APAC: Tokyo (JP)
- **Plan**: Free Tier (Cloud Flex Credits: 1.25 credits/day, 7-day retention)
- **URL**: https://service.jp.sumologic.com

---

### Step 2: Hosted Collector + HTTP Source Creation

- **Result**: ✅ PASS

- **Collector Name**: fsxn-audit-collector (Hosted Collector)
- **Source Type**: HTTP Logs & Metrics
- **Source Name**: fsxn-ontap-audit
- **Source Category**: aws/fsxn/audit
- **Generated URL**: `https://collectors.jp.sumologic.com/receiver/v1/http/<TOKEN>`

```bash
# Register HTTP Source URL in Secrets Manager
aws secretsmanager create-secret \
  --name "sumo-logic/fsxn-http-source" \
  --secret-string '{"url":"https://collectors.jp.sumologic.com/receiver/v1/http/<TOKEN>"}' \
  --region ap-northeast-1
```


---

### Step 3: CloudFormation Stack Deployment

- **Result**: ✅ PASS

```bash
aws cloudformation deploy \
  --template-file integrations/sumo-logic/template.yaml \
  --stack-name fsxn-sumo-logic-integration \
  --parameter-overrides \
    S3AccessPointArn=arn:aws:s3:ap-northeast-1:****6981:accesspoint/fsxn-audit-logs-ap \
    SumoLogicHttpSourceSecretArn=arn:aws:secretsmanager:ap-northeast-1:****6981:secret:sumo-logic/fsxn-http-source-XXXXXX \
    S3BucketName=fsxn-audit-logs-observability-test \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

- **Stack Status**: CREATE_COMPLETE
- **Created Resources**:
  - [x] Lambda Function
  - [x] IAM Role
  - [x] EventBridge Rule
  - [x] Dead Letter Queue (KMS encrypted)
  - [x] CloudWatch LogGroup (30-day retention)
  - [x] CloudWatch Alarm

---

### Step 4: Lambda Test Event Invocation

- **Result**: ✅ PASS

```bash
aws lambda invoke \
  --function-name fsxn-sumo-logic-integration-shipper \
  --payload file:///tmp/test-event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json
```

- **Response**:
```json
{
  "statusCode": 200,
  "body": {
    "total_logs": 2,
    "total_shipped": 2,
    "errors": []
  }
}
```

- **Checklist**:
  - [x] statusCode: 200
  - [x] total_logs: 2
  - [x] total_shipped: 2
  - [x] errors: [] (empty)
- **Sumo Logic HTTP Source Response**: HTTP 200


---

### Step 5: Sumo Logic Search Log Arrival Confirmation

- **Result**: ✅ PASS

- **Search Query**: `_sourceCategory=aws/fsxn/audit`
- **Arrived Logs**: 1 (displayed after initial indexing)
- **Time to Arrival**: Approximately 10 minutes (initial indexing lag for new accounts in JP region)

- **Search Result Metadata**:
  - HOST: `fsxn-ontap`
  - NAME: `fsxn-ontap-audit`
  - CATEGORY: `aws/fsxn/audit`
  - INDEX: `sumologic_default`

![Sumo Logic Search Results — Log Arrival Confirmation](../screenshots/sumo-logic/sumo-logic-log-arrival.png)

---

### Step 6: Field Mapping Verification

- **Result**: ✅ PASS

Fields confirmed in Sumo Logic search results:

| Field Name | Value | Result |
|------------|-------|--------|
| timestamp | 2026-05-24T04:15:58Z | ✅ OK |
| EventID | 4663 | ✅ OK |
| SVMName | svm-prod-01 | ✅ OK |
| UserName | admin@corp.local | ✅ OK |
| Operation | ReadData | ✅ OK |
| ObjectName | /vol/data/test.txt | ✅ OK |
| Result | Success | ✅ OK |

- **X-Sumo Header Verification**:
  - [x] X-Sumo-Category: `aws/fsxn/audit`
  - [x] X-Sumo-Name: `fsxn-ontap-audit`
  - [x] X-Sumo-Host: `fsxn-ontap`

---

### Step 7: Setup Guide Bilingual Verification

- **Result**: ✅ PASS

- **Japanese**: `integrations/sumo-logic/docs/ja/setup-guide.md` — Confirmed
- **English**: `integrations/sumo-logic/docs/en/setup-guide.md` — Confirmed

---

### Step 8: Screenshot Verification

- **Result**: ✅ PASS

| # | Filename | Content | Result |
|---|----------|---------|--------|
| 1 | `sumo-logic-search.png` | Search screen (before initial indexing) | ✅ |
| 2 | `sumo-logic-log-arrival.png` | Search results — log arrival confirmation (field display) | ✅ |


---

## Known Issues and Resolutions

| # | Issue | Severity | Resolution | Status |
|---|-------|----------|------------|--------|
| 1 | Initial indexing lag of approximately 10 minutes for new accounts in JP region | Medium | Wait required on first deployment. Subsequent ingestion is immediate. | 📝 Documented |
| 2 | HTTP Source URL has an authentication token embedded in it | Medium | Stored in Secrets Manager; not output to logs. | ✅ Resolved |
| 3 | Search queries require `_sourceCategory` (with underscore prefix) | Low | Documented in README. | ✅ Resolved |

---

## Overall Judgment

- **Judgment**: ✅ Audit log path production-ready
- **Passing Criteria**: 8 / 8
- **Failing Criteria**: None

---

## Verification Completion Checklist

- [x] All step results recorded
- [x] Screenshots placed (`docs/screenshots/sumo-logic/`)
- [x] Field mapping confirmed
- [x] Known issues and resolutions recorded
- [x] Setup guide bilingual parity confirmed
