# Workshop Agenda: FSx for ONTAP Serverless Observability

🌐 [日本語](../ja/workshop-agenda.md) | **English** (this page)

## Workshop Overview

| Item | Detail |
|------|--------|
| Duration | 2 hours (30 min lecture + 90 min hands-on) |
| Audience | Storage admins, platform engineers, security teams |
| Prerequisites | AWS account with FSx for ONTAP (or sandbox account) |
| Outcome | Working audit log pipeline delivering to chosen vendor |

## Agenda

### Part 1: Lecture (30 minutes)

| Time | Topic | Materials |
|------|-------|-----------|
| 0:00-0:05 | Introduction and goals | This agenda |
| 0:05-0:15 | Architecture overview: 3 event sources (Audit/EMS/FPolicy) | Architecture diagram |
| 0:15-0:20 | S3 Access Point constraints and design decisions | S3AP specification doc |
| 0:20-0:25 | Vendor selection guide: cost, features, data residency | Vendor comparison |
| 0:25-0:30 | Q&A | — |

### Part 2: Hands-On (90 minutes)

| Time | Activity | Success Criteria |
|------|----------|-----------------|
| 0:30-0:40 | **Lab 1**: Deploy prerequisites stack | S3 AP accessible, audit logs visible |
| 0:40-0:55 | **Lab 2**: Deploy vendor integration (choose one) | CloudFormation deploy succeeds |
| 0:55-1:10 | **Lab 3**: Trigger audit event + verify delivery | Log appears in vendor UI |
| 1:10-1:25 | **Lab 4**: Configure dashboard and alert | Dashboard shows data, alert fires on test |
| 1:25-1:40 | **Lab 5**: Test failure path (DLQ + replay) | DLQ receives message, replay succeeds |
| 1:40-1:55 | **Lab 6**: Cleanup + cost review | Stack deleted cleanly, cost estimate produced |
| 1:55-2:00 | Wrap-up: Go/No-Go discussion | Next steps documented |

## Pre-Workshop Checklist (Facilitator)

- [ ] AWS sandbox account provisioned (or organization provides)
- [ ] FSx for ONTAP file system running with audit logging enabled
- [ ] S3 bucket + Access Point deployed (prerequisites stack)
- [ ] Vendor account created (free tier recommended)
- [ ] API key / token stored in Secrets Manager
- [ ] Workshop guide printed or shared (this document)
- [ ] Sample audit log files available in S3 for immediate testing

## Pre-Workshop Checklist (Participants)

- [ ] AWS Console access (IAM user or SSO)
- [ ] AWS CLI configured (`aws sts get-caller-identity` works)
- [ ] Vendor account access (Datadog/Grafana/Splunk/etc.)
- [ ] Terminal with `git`, `python3`, `aws` CLI available
- [ ] Repository cloned: `git clone https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git`

## Lab Instructions Summary

### Lab 1: Deploy Prerequisites

```bash
aws cloudformation deploy \
  --template-file shared/templates/prerequisites.yaml \
  --stack-name fsxn-observability-prerequisites \
  --parameter-overrides \
    FsxS3AccessPointArn=<your-s3-ap-arn> \
  --capabilities CAPABILITY_IAM
```

### Lab 2: Deploy Vendor Integration

Choose your vendor and run the Quick Deploy command from the vendor README.

### Lab 3: Trigger and Verify

```bash
# Check Lambda logs for successful processing
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-<vendor>-integration-shipper \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))")

# Verify in vendor UI (vendor-specific query)
```

### Lab 4: Dashboard and Alert

Run the vendor's `create-dashboard.sh` script (if available) or manually create in vendor UI.

### Lab 5: Failure Path

```bash
# Temporarily break the secret to simulate failure
# (restore immediately after testing)
# Check DLQ for messages
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessages
```

### Lab 6: Cleanup

```bash
aws cloudformation delete-stack --stack-name fsxn-<vendor>-integration
aws cloudformation delete-stack --stack-name fsxn-observability-prerequisites
```

## Post-Workshop Deliverables

| Deliverable | Owner | Due |
|-------------|-------|-----|
| Workshop feedback form | Facilitator | Same day |
| PoC Report (if organization engagement) | Partner/SA | +3 days |
| Go/No-Go recommendation | Organization + Partner | +1 week |
| Production deployment plan (if Go) | Platform team | +2 weeks |

## Customization Notes

- For **security-focused** workshops: Add EMS webhook lab (ARP detection scenario)
- For **multi-vendor** workshops: Use OTel Collector with 2 backends
- For **migration** workshops: Start with Splunk EC2 comparison, then deploy serverless
- For **executive** audiences: Shorten hands-on to 45 min, expand business value discussion
