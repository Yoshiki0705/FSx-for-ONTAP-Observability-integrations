# Architecture Evolution: Admin Audit Log Delivery via CloudWatch Logs Syslog VPCE

🌐 [日本語](../ja/architecture-evolution-syslog-vpce.md) | **English** (this page)

> **Status**: Under verification (2026-06-28)
> **Related**: [AWS Announcement — CloudWatch Logs supports managed syslog ingestion](https://aws.amazon.com/about-aws/whats-new/2026/06/amazon-cloudwatch-syslog-ingestion/)
> **Reference**: [Classmethod Blog](https://dev.classmethod.jp/articles/amazon-fsx-for-netapp-ontap-security-audit-log-syslog-to-cw-logs/)

---

## Executive Summary

In June 2026, AWS announced managed syslog ingestion for CloudWatch Logs. This enables FSx for ONTAP **admin activity audit logs** to be delivered directly to CloudWatch Logs without an EC2 syslog server.

**Conclusion**: The project architecture is restructured into two layers: an "AWS-native layer" and a "vendor delivery layer."

---

## Before / After Comparison

### Before (Pre-June 2026)

```
Admin audit logs: FSx for ONTAP → EC2 (syslog-ng) → Splunk/SIEM
File access audit: FSx for ONTAP → S3 AP → Lambda → Vendor
EMS: FSx for ONTAP → Webhook → API Gateway → Lambda → Vendor
FPolicy: FSx for ONTAP → Fargate (TCP) → SQS → Lambda → Vendor
```

### After (New Architecture)

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: AWS Native (always-on, vendor-independent)          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Admin audit logs → Syslog VPCE → CloudWatch Logs            │
│  File access audit → S3 AP → Lambda → CloudWatch Logs        │
│  EMS → EventBridge (managed) → CloudWatch Logs               │
│  FPolicy → Fargate → SQS → Lambda → CloudWatch Logs         │
│                                                              │
│  * CloudWatch Logs = central hub (search, retention, alarms) │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Vendor Delivery (optional, choose per use case)     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  CloudWatch Logs → Subscription Filter → Lambda → Vendor     │
│  CloudWatch Logs → Subscription Filter → Firehose → Vendor   │
│  CloudWatch Logs → Subscription Filter → OTel Collector       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Four Event Sources — Updated Delivery Paths

| # | Event Source | AWS Native Path | Vendor Path |
|---|-------------|-----------------|-------------|
| 1 | **Admin audit logs** (CLI/API operations) | Syslog VPCE → CW Logs | CW Logs → Subscription → Lambda → Vendor |
| 2 | **File access audit** (EVTX/XML) | S3 AP → Lambda → CW Logs | S3 AP → Lambda → Vendor (direct) |
| 3 | **EMS** (Event Management) | EventBridge → CW Logs | API GW → Lambda → Vendor |
| 4 | **FPolicy** (File operations) | Fargate → SQS → Lambda → CW Logs | SQS → Lambda → Vendor (direct) |

---

## Admin Audit Logs: New Syslog VPCE Path

### Components

| Resource | Role |
|----------|------|
| VPC Endpoint (`com.amazonaws.{region}.syslog-logs`) | PrivateLink ENI for syslog reception |
| Security Group | Allows ingress from FSx for ONTAP → VPCE |
| CloudWatch Logs Log Group | Log storage destination |
| Resource Policy | Grants write access to syslog.logs.amazonaws.com |
| Syslog Configuration | Mapping from VPCE → Log Group |
| ONTAP `cluster log-forwarding` | Forwarding configuration on the ONTAP side |

### ONTAP CLI Configuration

```bash
# SSH to FSx for ONTAP management endpoint
ssh fsxadmin@<management-ip>

# Create log forwarding destination
cluster log-forwarding create \
  -destination syslog-logs.ap-northeast-1.amazonaws.com \
  -port 6514 \
  -protocol tcp-encrypted \
  -facility local7

# Verify
cluster log-forwarding show
```

### Supported Protocols

| Protocol | Port | Recommended Use |
|----------|------|-----------------|
| TCP + TLS | 6514 | **Production recommended** (encrypted) |
| TCP Plaintext | 1514 | When traffic stays within PrivateLink |
| UDP | 514 | Best-effort (not recommended) |

---

## Selection Guide: AWS Native vs Direct Vendor Delivery

### When AWS Native Alone Is Sufficient

- CloudWatch Logs Insights provides adequate search capability
- Retention requirements fit within CloudWatch Logs max retention
- S3 export covers long-term archival needs
- CloudWatch Alarms provide basic alerting
- Prefer to avoid sending data to external vendors

### When Vendor Delivery Is Required

- Advanced SIEM correlation (Splunk SPL, Elastic KQL)
- Unified APM + storage log view (Datadog, Dynatrace)
- ML-based anomaly detection (Datadog Anomaly, Davis AI)
- Integration with existing SOC workflows
- High-cardinality analysis (Honeycomb BubbleUp)

### Hybrid Pattern (Recommended)

```
FSx for ONTAP
    |
    |-> [Admin audit] Syslog VPCE -> CW Logs --> (always stored)
    |                                         \-> Subscription -> Vendor (optional)
    |
    |-> [File access] S3 AP -> Lambda --> CW Logs (always stored)
    |                                 \-> Vendor (direct delivery)
    |
    |-> [EMS] EventBridge -> CW Logs --> (always stored + Alarm)
    |                                \-> Lambda -> Vendor (optional)
    |
    \-> [FPolicy] Fargate -> SQS -> Lambda --> CW Logs (always stored)
                                           \-> Vendor (direct delivery)
```

---

## Cost Comparison

| Path | Estimated Monthly Cost (10 GB/month) | Operational Overhead |
|------|--------------------------------------|---------------------|
| Syslog VPCE → CW Logs only | ~$8 (VPCE + CW Logs storage) | Zero |
| Above + Subscription → Vendor | ~$8 + vendor fees | Low (Lambda management only) |
| Legacy EC2 syslog-ng | ~$66+ (EC2 + EBS) | High (OS patching, agent updates) |

---

## CloudFormation Template

Deploy the following with `shared/templates/syslog-vpce-cloudwatch.yaml`:

```bash
aws cloudformation deploy \
  --template-file shared/templates/syslog-vpce-cloudwatch.yaml \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --parameter-overrides \
    VpcId=<FSx-VPC> \
    SubnetIds=<FSx-Subnet> \
    FsxSecurityGroupId=<FSx-SG> \
  --region ap-northeast-1
```

Post-deployment manual steps:
1. AWS Console → CloudWatch → Logs → Syslog configurations → Create
2. Associate the VPCE with the Log Group
3. SSH to FSx for ONTAP → `cluster log-forwarding create`

---

## Verification Status

| Step | Status | Notes |
|------|--------|-------|
| VPC Endpoint creation | ✅ Complete | `vpce-010e49474d23c7172`, ENI IP: `198.51.100.28` |
| Security Group creation | ✅ Complete | VPC CIDR (10.0.0.0/16) → Port 6514 allowed |
| Log Group creation | ✅ Complete | `/syslog/fsxn-admin-audit` |
| Resource Policy configuration | ✅ Complete | `syslog.logs.amazonaws.com` → Log Group |
| ONTAP log-forwarding configuration | ✅ Complete | `198.51.100.28:6514` (tcp_encrypted, local7) |
| Syslog Configuration (Console) | 🔲 Next step | CLI not yet supported; use Console |
| Log arrival confirmation | 🔲 After Syslog Configuration | |
| CW Logs Insights query | 🔲 After log arrival | |

### Creating Syslog Configuration (AWS Console)

> **Note**: As of June 2026, the `PutSyslogConfiguration` API is not supported in AWS CLI v2.35.x. Use the Console instead.

1. AWS Console → **CloudWatch** → **Logs** → Left menu **Syslog configurations**
2. Click **Create syslog configuration**
3. Settings:
   - **VPC endpoint**: `vpce-010e49474d23c7172`
   - **Log group**: `/syslog/fsxn-admin-audit`
   - **Allow all sources**: Yes
4. Click **Create**

After creation, run an admin operation on ONTAP (e.g., `volume show`) and logs will arrive in CloudWatch Logs within seconds.

---

## Related Documents

- [AWS Docs: Syslog ingestion](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Syslog.html)
- [AWS Docs: Setting up syslog ingestion](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Syslog_Setup.html)
- [NetApp: cluster log-forwarding create](https://docs.netapp.com/us-en/ontap-cli/cluster-log-forwarding-create.html)
- [Classmethod: FSx for ONTAP admin audit logs → CW Logs](https://dev.classmethod.jp/articles/amazon-fsx-for-netapp-ontap-security-audit-log-syslog-to-cw-logs/)
- [This project: Event sources guide](event-sources.md)
