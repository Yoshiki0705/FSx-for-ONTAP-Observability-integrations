# FSx for ONTAP Splunk Serverless Integration

🌐 [日本語](docs/ja/setup-guide.md) | [English](docs/en/setup-guide.md)

> 📖 **Shared docs**: [Delivery Guarantee Patterns](../../docs/en/delivery-guarantees.md) | [Webhook Security](../../docs/en/webhook-security.md)

## Overview

Serverless alternative to the [EC2-based Splunk integration](https://aws.amazon.com/jp/blogs/news/auditing-user-and-administrative-actions-on-amazon-fsx-for-netapp-ontap-using-splunk/) that uses syslog-ng + Universal Forwarder on EC2 instances.

This integration ships FSx for ONTAP audit logs directly to Splunk via HTTP Event Collector (HEC), eliminating the need for EC2 instances.

**PoC time estimate**: ~30 minutes from deploy to first queryable event in Splunk (assumes HEC is already configured).

## Prerequisites

See [Prerequisites Guide](../../docs/en/prerequisites.md) for ONTAP audit logging setup and S3 Access Point configuration.

## Architecture Comparison

```
[Existing: EC2-based]
FSx for ONTAP → syslog-ng (EC2) → Splunk UF (EC2) → Splunk Enterprise

[This project: Serverless]
FSx for ONTAP → S3 Access Point → EventBridge → Lambda → Splunk HEC
```

### FPolicy Path (real-time file operations)

`template-fpolicy.yaml` adds real-time file operation events. FPolicy speaks a
proprietary binary protocol over TCP, not HTTP, so a Fargate server sits in
front of it (see `shared/templates/fpolicy-apigw.yaml`):

```
FSx for ONTAP → TCP 9898 → ECS Fargate → SQS → Lambda (-fpolicy) → Splunk HEC
                                          (ReportBatchItemFailures)
```

> **Delivery note**: the SQS event source mapping declares
> `FunctionResponseTypes: [ReportBatchItemFailures]` and the handler returns
> `batchItemFailures`, so only messages that were not delivered are retried and
> eventually redriven to the DLQ. Without this pairing, SQS deletes every
> message in the batch as soon as the invocation returns, losing up to
> `BatchSize` events per HEC failure. The secondary EventBridge path keeps the
> `statusCode` response contract instead, because EventBridge has no per-item
> failure protocol.

### Alternative: Firehose Path (High Volume)

For sustained high-volume logs (>1000 events/sec), use Kinesis Data Firehose with its built-in Splunk destination:

```
FSx for ONTAP → S3 AP → Lambda (transform) → Kinesis Data Firehose → Splunk HEC
```

## Quick Deploy

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name fsxn-splunk-integration \
  --parameter-overrides \
    S3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit \
    SplunkHecTokenSecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk-hec-token \
    SplunkHecEndpoint=https://splunk.example.com:8088 \
    S3BucketName=my-fsxn-audit-bucket \
    SplunkIndex=fsxn_audit \
  --capabilities CAPABILITY_IAM
```

## Splunk HEC Configuration

### Create HEC Token in Splunk

1. Splunk Web → **Settings** → **Data Inputs** → **HTTP Event Collector**
2. Click **New Token**
3. Configure:
   - Name: `fsxn-audit-log-shipper`
   - Source type: `fsxn:ontap:audit`
   - Index: `fsxn_audit`
4. Copy the generated token

### Store Token in Secrets Manager

```bash
aws secretsmanager create-secret \
  --name "splunk/fsxn-hec-token" \
  --secret-string '{"hec_token":"YOUR_HEC_TOKEN"}' \
  --region ap-northeast-1
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `SplunkHecEndpoint` | ✅ | - | HEC URL (e.g., `https://splunk:8088`) |
| `SplunkHecTokenSecretArn` | ✅ | - | Secrets Manager ARN for HEC token |
| `SplunkIndex` | ❌ | `fsxn_audit` | Target Splunk index |
| `SplunkSourcetype` | ❌ | `fsxn:ontap:audit` | Splunk sourcetype |
| `VerifySSL` | ❌ | `true` | Set `false` for self-signed certs |

## HEC Event Format

```json
{
  "time": 1705315200,
  "host": "svm-prod-01",
  "source": "fsxn-observability",
  "sourcetype": "fsxn:ontap:audit",
  "index": "fsxn_audit",
  "event": {
    "event_type": "4663",
    "user": "admin@corp.local",
    "operation": "ReadData",
    "path": "/vol/data/file.txt",
    "result": "Success"
  }
}
```

## Network Considerations

- Lambda must be able to reach the Splunk HEC endpoint
- If Splunk is in a private VPC, deploy Lambda in the same VPC with NAT Gateway
- For Splunk Cloud, ensure the HEC endpoint is publicly accessible or use AWS PrivateLink

## Query Examples (SPL)

```spl
# Find all failed access attempts
index=fsxn_audit sourcetype=fsxn:ontap:audit result=Failure
| stats count by user, path

# Top operations by volume
index=fsxn_audit sourcetype=fsxn:ontap:audit
| stats count by operation
| sort -count

# Access timeline
index=fsxn_audit sourcetype=fsxn:ontap:audit
| timechart span=5m count by operation

# Specific user investigation
index=fsxn_audit sourcetype=fsxn:ontap:audit user="admin@corp.local"
| table _time, operation, path, result, client_ip
```

## Monitoring

- **CloudWatch Alarm**: Lambda errors > 5 in 10 minutes
- **Dead Letter Queue**: Failed events preserved for 14 days (KMS encrypted)
- **Splunk**: Monitor HEC token health via `_internal` index

## Limits & Known Issues

- No hard batch size limit for HEC, but recommended < 1MB per event
- `VerifySSL=false` disables TLS verification (⚠️ do NOT use in production)
- Splunk Cloud HEC endpoints require allowlisting of Lambda's outbound IP (NAT Gateway EIP)
- For sustained >1000 events/sec, use the Firehose path (`template-firehose.yaml`)
- **Splunk Cloud free trial**: HEC DNS records (`http-inputs-<stack>.splunkcloud.com`) are not provisioned for free trial accounts. This is a [known Community issue](https://community.splunk.com/t5/Getting-Data-In/HEC-with-Splunk-Cloud-trial/td-p/596680). Use Splunk Enterprise (Docker) for local E2E validation.
- **Data residency**: For Splunk Cloud, data is stored in the region of your Splunk Cloud deployment. For self-managed Splunk, data stays in your infrastructure. Evaluate cross-border data transfer requirements with your compliance team.

## Cost Estimate

| Monthly Log Volume | AWS Lambda Cost | Splunk License Impact |
|-------------------|----------------|----------------------|
| 1 GB | ~$0.50 | Minimal (within most license allocations) |
| 10 GB | ~$5 | Check daily indexing volume limit |
| 100 GB | ~$50 | Significant — consider Firehose path |

> Splunk pricing is license-based (daily indexing volume). This integration does not change your Splunk license cost — it only changes the delivery mechanism from EC2 to Lambda.

## E2E Verification Evidence

Verified with Splunk Enterprise 10.4.0 (Docker, local):

| Item | Result |
|------|--------|
| XML audit log parsing | ✅ 5 events parsed (EventID 4663/4656/4660) |
| HEC delivery | ✅ HTTP 200 (`{"text":"Success","code":0}`) |
| Splunk indexing | ✅ `fsxn_audit` index, 5 events confirmed |
| Field extraction | ✅ user, path, client_ip, event_type, result, svm, timestamp |
| Splunk Search UI | ✅ All events searchable and field-parsed |

**Verification method**: Splunk Enterprise (Docker `splunk/splunk:latest`, `--platform linux/amd64`) with HEC token pre-configured via `SPLUNK_HEC_TOKEN` environment variable. Test executed using `shared/scripts/test-xml-e2e.py --vendor splunk`.

**Splunk Cloud trial note**: Free trial accounts do not provision HEC DNS records (`http-inputs-<stack>.splunkcloud.com`) reliably. Use Splunk Enterprise (Docker) for local validation or Splunk Cloud paid tier for production.

Screenshot: [`screenshots/splunk-e2e-search-fsxn-audit-xml.png`](screenshots/splunk-e2e-search-fsxn-audit-xml.png)

## Related Documents

- [Migration from EC2 (English)](docs/en/migration-from-ec2.md) — Step-by-step guide to replace EC2 UF
- [EC2 からの移行ガイド (日本語)](docs/ja/migration-from-ec2.md)
- [Vendor Comparison](../../docs/en/vendor-comparison.md)
- [PoC Success Criteria](../../docs/en/poc-success-criteria.md)
