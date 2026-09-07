# Workshop Hands-On Guide (Half-Day, 3.5 Hours)

🌐 [日本語](../ja/workshop-hands-on-half-day.md) | **English** (this page)

## Target Audience

- Partners delivering FSx for ONTAP observability PoCs
- AWS SA-led organization workshops
- Self-paced hands-on for technical decision-makers

## Prerequisites

- AWS account with admin access (sandbox recommended)
- FSx for ONTAP file system with audit logging enabled (or willingness to use sample data)
- One observability vendor account (free tier is sufficient)
- AWS CLI v2 configured
- Basic familiarity with CloudFormation

## Agenda

| Time | Duration | Module | Outcome |
|------|----------|--------|---------|
| 0:00 | 15 min | **Module 0**: Environment Setup | CLI verified, repo cloned |
| 0:15 | 30 min | **Module 1**: Architecture Overview | Understand the 3 event sources |
| 0:45 | 45 min | **Module 2**: Deploy Audit Log Poller | First log arrives in vendor |
| 1:30 | 15 min | Break | — |
| 1:45 | 30 min | **Module 3**: Verify & Query | Dashboard + first query |
| 2:15 | 30 min | **Module 4**: Add EMS Webhook | Ransomware alert path working |
| 2:45 | 30 min | **Module 5**: Production Readiness | SLO, security, Go/No-Go |
| 3:15 | 15 min | **Module 6**: Wrap-Up & Next Steps | PoC plan, cleanup |

---

## Module 0: Environment Setup (15 min)

### Verify AWS CLI

```bash
aws sts get-caller-identity
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE --region ap-northeast-1
```

### Clone Repository

```bash
git clone https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
cd FSx-for-ONTAP-Observability-integrations
```

### Choose Your Vendor

Select one vendor for the workshop. Recommended for first-timers:
- **Sumo Logic** — Generous free tier, JP region, simplest auth
- **Datadog** — Most complete reference implementation
- **Grafana Cloud** — OTLP-native, good for OTel-familiar teams

### Prepare Vendor Credentials

Follow the vendor-specific setup in `integrations/<vendor>/README.md` to:
1. Create a vendor account (free tier)
2. Generate API credentials
3. Store in AWS Secrets Manager

---

## Module 1: Architecture Overview (30 min)

### Presentation (15 min)

Cover these key points:
1. FSx for ONTAP S3 Access Points — what they are and what they cannot do
2. Three event sources: Audit logs, EMS webhooks, FPolicy
3. Why EventBridge Scheduler polling (not S3 Event Notifications)
4. Checkpoint pattern (SSM Parameter Store)
5. Production Readiness Levels (0-4)

### Discussion (15 min)

Ask participants:
- Which event source is most relevant to your use case?
- What is your current audit log visibility?
- What observability platform are you using today?

---

## Module 2: Deploy Audit Log Poller (45 min)

### Step 1: Deploy Prerequisites (if not already deployed)

```bash
aws cloudformation deploy \
  --template-file shared/templates/prerequisites.yaml \
  --stack-name fsxn-observability-prerequisites \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

> **No FSx for ONTAP?** Use sample data mode — upload test files to a regular S3 bucket and point the template at that bucket.

### Step 2: Upload Sample Data (if using sample mode)

```bash
# Generate and upload sample audit log
python3 shared/scripts/generate-sample-audit.py --count 10 --output /tmp/sample-audit.json
aws s3 cp /tmp/sample-audit.json s3://<your-bucket>/audit/svm-prod-01/2026/05/24/sample-001.json
```

### Step 3: Deploy Vendor Integration

```bash
aws cloudformation deploy \
  --template-file integrations/<vendor>/template.yaml \
  --stack-name fsxn-<vendor>-integration \
  --parameter-overrides \
    S3AccessPointArn=<your-s3-ap-arn> \
    <VendorCredentialParam>=<your-secret-arn> \
    S3BucketName=<your-bucket> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

### Step 4: Trigger First Run

```bash
# Manual invoke to test immediately (don't wait for scheduler)
aws lambda invoke \
  --function-name fsxn-<vendor>-integration-shipper \
  --payload '{"source": "scheduler", "s3_access_point_arn": "<arn>", "prefix": "audit/"}' \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  /tmp/response.json

cat /tmp/response.json
```

### Success Criteria

- [ ] Lambda returns `statusCode: 200`
- [ ] `total_shipped > 0`
- [ ] `errors: []`

---

## Module 3: Verify & Query (30 min)

### Step 1: Verify in Vendor Platform

Use the vendor-specific query from the README:
- **Datadog**: `source:fsxn`
- **Grafana**: `{service_name="fsxn-audit"}`
- **Splunk**: `index=fsxn_audit`
- **Sumo Logic**: `_sourceCategory=aws/fsxn/audit`
- **Elastic**: `fsxn.result: *` in Kibana Discover
- **Honeycomb**: `WHERE service = "ontap-audit"`
- **Dynatrace**: `fetch logs | filter log.source == "fsxn-ontap"`
- **New Relic**: `SELECT * FROM Log WHERE source = 'fsxn-ontap'`

### Step 2: Verify Pipeline Health

```bash
# Checkpoint advanced?
aws ssm get-parameter --name "/fsxn/<vendor>/audit-checkpoint" --region ap-northeast-1

# DLQ empty?
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessagesVisible
```

### Step 3: Build First Query

Write a query that answers: "Which users accessed files in the last hour?"

---

## Module 4: Add EMS Webhook (30 min)

### Step 1: Deploy EMS Template

```bash
aws cloudformation deploy \
  --template-file integrations/<vendor>/template-ems.yaml \
  --stack-name fsxn-<vendor>-ems \
  --parameter-overrides \
    <VendorCredentialParam>=<your-secret-arn> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

### Step 2: Test with Sample EMS Event

```bash
# Get API Gateway URL from stack outputs
API_URL=$(aws cloudformation describe-stacks \
  --stack-name fsxn-<vendor>-ems \
  --query 'Stacks[0].Outputs[?OutputKey==`WebhookUrl`].OutputValue' \
  --output text)

# Send test EMS event (ransomware simulation)
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{"messageName":"arw.volume.state","severity":"alert","parameters":[{"name":"volumeName","value":"vol_data"}]}'
```

### Step 3: Verify Alert Arrives

Query for EMS events in your vendor platform.

---

## Module 5: Production Readiness (30 min)

### Discussion: Where Are You Now?

Review the Production Readiness Levels:
- Level 1: Quickstart (what we just deployed)
- Level 2: Operational PoC (dashboards, alerts, replay tested)
- Level 3: Production Baseline (DynamoDB ledger, security review)
- Level 4: Enterprise Pipeline (OTel Collector, redaction, multi-backend)

### Review: Pipeline SLO

Walk through the [Pipeline SLO document](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/pipeline-slo.md):
- Delivery latency targets
- Data loss rate targets
- Go/No-Go criteria for Level 1 to Level 2

### Review: Data Classification

Walk through the [Data Classification Guide](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/data-classification.md):
- Which fields are PII?
- What handling pattern fits your requirements?
- Does your vendor support the needed data residency?

### Exercise: Define Your PoC Success Criteria

Each participant fills in:
1. Business outcome this PoC supports
2. Success metric (measurable)
3. Timeline (typically 2-4 weeks)
4. Go/No-Go decision owner

---

## Module 6: Wrap-Up & Next Steps (15 min)

### Cleanup (if using sandbox)

```bash
bash integrations/<vendor>/scripts/cleanup.sh --all
```

### Take-Home Materials

- [ ] Repository link: github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations
- [ ] PoC Success Criteria template (filled in during Module 5)
- [ ] Pipeline SLO document
- [ ] Data Classification Guide
- [ ] DLQ Replay Runbook

### Next Steps for Participants

1. Deploy in your own account with real FSx for ONTAP audit logs
2. Run for 7 days and verify SLOs
3. Present Go/No-Go to business sponsor
4. If Go: proceed to Level 2 (dashboards + alerts)

---

## Facilitator Notes

### Common Issues During Workshop

| Issue | Resolution |
|-------|-----------|
| CloudFormation CREATE_FAILED | Check IAM capabilities, parameter values |
| Lambda timeout | Verify S3 AP network path (VPC vs non-VPC) |
| No logs in vendor | Check credentials in Secrets Manager, verify Lambda logs |
| DLQ has messages | Check Lambda error logs for root cause |

### Timing Adjustments

- If participants are fast: Add FPolicy module (template-fpolicy.yaml)
- If participants are slow: Skip Module 4 (EMS), focus on audit path
- If no FSx for ONTAP: Use sample data mode throughout

### Required Preparation (Facilitator)

1. Pre-deploy prerequisites stack in workshop account
2. Pre-upload sample audit data to S3
3. Pre-create vendor accounts (one per participant or shared)
4. Test the full flow end-to-end before the workshop
5. Print/share the PoC Success Criteria template
