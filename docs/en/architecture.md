# Architecture

🌐 [日本語](../ja/architecture.md) | **English** (this page)

## Overview

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  FSx for ONTAP  │────▶│  FSx for ONTAP       │────▶│  EventBridge    │
│  (Audit Logs)   │     │  S3 Access Point │     │  Scheduler      │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                           │
                                                           ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Observability  │◀────│     Lambda       │◀────│  Periodic       │
│  Vendor API     │     │  (Transform/Ship)│     │  + checkpoint   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

> **What "serverless" means in this series**: Minimizing server management and undifferentiated collector operations — not forcing every component into Lambda. FPolicy requires a persistent TCP listener, so we use ECS Fargate (serverless containers). Event decoupling uses SQS. Short-lived processing uses Lambda. Each AWS service is chosen for its operational characteristics, not to satisfy a "Lambda-only" constraint.

## Four Telemetry Paths

This project supports four ONTAP telemetry sources:

### Path 1: File Access Audit Logs (S3 AP + EventBridge Scheduler)
```
ONTAP Audit Logs → S3 Access Point → EventBridge Scheduler → Lambda → Vendor API
```
- **Use case**: Compliance file access history
- **Latency**: Near-real-time (depends on Scheduler interval, typically 5 minutes)
- **Format**: EVTX / XML

### Path 2: Admin Audit Logs (Syslog VPC Endpoint → CloudWatch Logs)
```
ONTAP log-forwarding → Syslog (TCP 1514/6514) → VPC Endpoint → CloudWatch Logs
```
- **Use case**: Management activity tracking (CLI/API operations)
- **Latency**: Real-time (seconds)
- **Format**: RFC 3164 syslog (auto-parsed by CloudWatch)
- **Compute**: None (fully managed, no Lambda/EC2)
- **Optional fan-out**: CloudWatch Logs → Subscription Filter → Lambda/Firehose → Vendor

### Path 3: EMS Events (Webhook)
```
ONTAP EMS → Webhook (HTTPS) → API Gateway → Lambda → Vendor API
```
- **Use case**: ARP ransomware detection, quota exceeded, HA failover, etc.
- **Latency**: Real-time (~30 seconds)
- **Format**: JSON

### Path 4: FPolicy File Operations (ECS Fargate)
```
ONTAP FPolicy → ECS Fargate (TCP:9898) → SQS → Lambda → Vendor API
```
- **Use case**: Real-time file operation monitoring (create, write, rename, delete)
- **Latency**: Real-time (~6-8 seconds)
- **Format**: FPolicy binary protocol → JSON normalization
- **Note**: FPolicy uses a proprietary binary protocol, so Lambda is not viable — ECS Fargate is required

## ONTAP Telemetry Source Selection Guide

| Requirement | Best Source | Latency | Compute |
|-------------|------------|---------|---------|
| Compliance file access history | File access audit logs | Near-real-time (Scheduler interval) | Lambda |
| Admin activity audit | Admin audit logs (syslog) | Real-time (seconds) | None (managed) |
| Ransomware detection | ARP via EMS | Real-time (webhook) | Lambda |
| Real-time file operation detail | FPolicy | Real-time (TCP) | Fargate + Lambda |
| Operational alerting (quota, HA, volume full) | EMS | Real-time (webhook) | Lambda |
| Vendor-neutral pipeline | OpenTelemetry | Configuration-dependent | Collector + Lambda |

## Component Details

### 1. FSx for ONTAP Audit Logs

Enable audit logging on FSx for ONTAP to output logs to an audit volume inside the SVM.

- **Log Format**: EVTX (Windows Event Log) or XML — configurable via `-format {evtx|xml}`
- **Destination**: Audit volume inside the SVM (`vserver audit create -destination /audit_log`)
- **Log Content**: File access (SMB/NFS), authentication events
- **Access Method**: Read via FSx for ONTAP S3 Access Point using S3 APIs

> **Important**: Audit logs are stored on the FSx volume. They are NOT written to an S3 bucket. Lambda reads the log files through an FSx for ONTAP S3 Access Point using S3 APIs.

#### Audit Log Format: EVTX vs XML

Both formats contain the same event fields — they are different serializations of the same underlying audit data. ONTAP writes events to intermediate staging files, then converts them to the configured output format.

| Aspect | EVTX | XML |
|--------|------|-----|
| Content | Same fields (EventID, TimeCreated, UserName, ObjectName, etc.) | Same fields |
| Encoding | Binary (Windows Event Log format) | Text (human-readable) |
| Parse complexity | High (requires binary struct parsing or `python-evtx` library) | Low (standard XML parser, included in Python stdlib) |
| External viewer | Microsoft Event Viewer | Any text editor / XML tool |
| Lambda compatibility | ⚠️ Simplified parser (header only) or heavy library dependency | ✅ Full parsing with stdlib `xml.etree.ElementTree` |
| Lambda Layer size impact | Large (if using `python-evtx`) | None (stdlib only) |
| Default | ✅ (ONTAP default) | Must specify `-format xml` |

**Recommendation for serverless pipelines**: Use **XML format** (`-format xml`). It enables complete field extraction in Lambda without third-party dependencies.

```bash
# Create audit config with XML format (recommended for pipeline delivery)
vserver audit create -vserver <svm-name> -destination /audit_log -format xml -rotate-size 200MB

# Create audit config with EVTX format (default, for Windows Event Viewer)
vserver audit create -vserver <svm-name> -destination /audit_log -rotate-size 200MB
```

**Fields extracted (both formats)**:

| Field | Description | Example |
|-------|------------|---------|
| EventID | Windows security event ID | 4663 (Read/Write Object) |
| TimeCreated | Event timestamp (UTC) | 2026-05-31T10:00:00Z |
| Computer | SVM name | FPolicySMB |
| SubjectUserName | User who performed the action | DOMAIN\username |
| IpAddress | Client IP address | 198.51.100.50 |
| ObjectName | File/directory path | /share/folder/report.xlsx |
| ObjectType | File or Directory | File |
| HandleID | Object handle | — |
| Keywords | Success or Failure | Audit Success |

**Parser implementation status in this project**:

| Format | Parser | Fields Extracted |
|--------|--------|-----------------|
| XML | ✅ Complete (`_parse_xml_logs`) | All fields above |
| JSON | ✅ Complete (`_parse_json_logs`) | All fields |
| EVTX | ⚠️ Simplified (`_parse_evtx`) | Timestamp only (EventID = "unknown") |

> For production EVTX parsing, consider the [`python-evtx`](https://github.com/williballenthin/python-evtx) library packaged as a Lambda Layer (~15 MB unzipped). Alternatively, switch to XML format at the ONTAP audit configuration level — this is the recommended approach for serverless pipelines as it requires zero additional dependencies.

Reference: [AWS Docs — File access auditing](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html) | [NetApp Docs — Create auditing config](https://docs.netapp.com/us-en/ontap/nas-audit/create-auditing-config-task.html)

> **NFS audit note**: NFS file access auditing (ONTAP 9.13.1+) may include additional fields not present in SMB audit events. The field mapping above is based on SMB access events (EventID 4656/4660/4663). Verify actual field availability in your ONTAP version when configuring NFS auditing.

### 2. FSx for ONTAP S3 Access Point

An S3 Access Point attached to an FSx for ONTAP volume.

- **Purpose**: Serverless access boundary for Lambda to read audit logs without NFS/SMB mounts
- **Characteristics**: Data remains on the FSx file system, accessible via S3 API
- **Limitation**: S3 Event Notifications / EventBridge notifications are NOT supported
- **VPC Constraint**: Internet-origin S3 AP timed out with only Gateway Endpoint in our environment (NAT Gateway or VPC-origin AP required)

### 3. Trigger Mechanism

Because FSx for ONTAP S3 Access Points do not support S3 event notifications, we use EventBridge Scheduler for periodic invocation.

**EventBridge Scheduler + Checkpointing**
- EventBridge Scheduler invokes Lambda periodically (e.g., every 5 minutes)
- Lambda tracks processed files via checkpointing (DynamoDB)
- Only newly rotated log files are processed

### 4. Lambda Function

Retrieves and parses audit logs, then ships to vendor APIs.

- **Runtime**: Python 3.12
- **Processing Flow**:
  1. List log files via FSx for ONTAP S3 Access Point
  2. Compare with checkpoint to identify unprocessed files
  3. Parse EVTX/XML
  4. Transform to vendor-specific format
  5. Batch send to API endpoint
  6. Retry on failure (exponential backoff)
  7. Update checkpoint

### 5. Alternative Pattern: Kinesis Data Firehose

For high-volume logs, deliver to vendors via Firehose.

```
FSx for ONTAP S3 AP → Lambda (Transform) → Kinesis Data Firehose → Vendor API
```

- **Benefits**: Automatic buffering, retry, scaling
- **Supported Vendors**: Splunk (HEC), Datadog, New Relic, any HTTP endpoint

### 6. AWS-Native Alternatives Comparison

| Approach | Best For | Trade-off |
|----------|----------|-----------|
| Lambda → Vendor API direct | Vendor-specific mapping, EVTX/XML parsing | Custom retry/backoff needed |
| Kinesis Data Firehose | Managed buffering | Transformation flexibility limited |
| CloudWatch Logs first | AWS-native operations | Extra routing to external tools |
| SQS buffer (between parser and shipper) | Decoupling, backpressure | More components |
| OpenTelemetry Collector | Vendor-neutral standard | Schema/mapping decisions needed |
| Security Lake / OCSF | Security analytics, long-term storage | OCSF schema transformation needed |

This project uses Lambda → Vendor API direct because:
- Full control over EVTX/XML parsing is required
- Vendor-specific API semantics (batch size, auth, retry) must be handled natively
- For organizations prioritizing AWS-native logging, CloudWatch Logs or S3 archive can be added as parallel outputs

## Security Design

### IAM Least Privilege

```yaml
# Lambda execution role
- s3:GetObject (FSx for ONTAP S3 Access Point ARN only)
- s3:ListBucket (FSx for ONTAP S3 Access Point ARN only)
- secretsmanager:GetSecretValue (API Key Secret only)
- dynamodb:GetItem, dynamodb:PutItem (checkpoint table only)
- logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
```

### Secret Management

- API keys stored in AWS Secrets Manager
- Only ARN set in Lambda environment variables
- KMS customer-managed key encryption recommended

### Network

- Access FSx for ONTAP S3 Access Point via NAT Gateway (when Lambda is in VPC)
- **Note**: Internet-origin S3 APs require NAT Gateway or VPC-external Lambda for VPC-internal access
- Lambda placed outside VPC can access FSx for ONTAP S3 AP without issues (recommended for read-only)
- Security groups allow minimal outbound only

## Monitoring & Alerting

- **CloudWatch Metrics**: Lambda error rate, duration, throttling
- **CloudWatch Alarms**: SNS notification on delivery failure threshold
- **Dead Letter Queue**: Failed events sent to SQS DLQ
- **X-Ray**: Distributed tracing for bottleneck identification
