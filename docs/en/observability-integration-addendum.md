# Observability Integration Addendum — Advanced Patterns & Reference

🌐 [日本語](../ja/observability-integration-addendum.md) | **English** (this page)

## Purpose

Advanced observability integration patterns, decision guides, and reference tables that complement the main documentation. Addresses: detection strategy selection, MITRE ATT&CK mapping, OTel semantic conventions, cost modeling, cross-account patterns, and vendor portability.

---

## 1. Detection Strategy Decision Guide

### CloudWatch Log Alarm vs Custom Metric + Anomaly Detection

| Criterion | Log Alarm | Custom Metric + Anomaly Detection |
|-----------|-----------|-----------------------------------|
| Pattern type | Known patterns (specific strings/thresholds) | Unknown patterns (behavioral drift) |
| Example | "10+ failed logins in 5 min" | "Login rate is 3σ above 7-day baseline" |
| Setup complexity | Low (1 query, 1 alarm) | Medium (metric filter + anomaly band) |
| False positive rate | Low (explicit threshold) | Higher (ML model may overfit) |
| Detection coverage | Only what you explicitly define | Catches novel anomalies |
| Cost | ~$0.30/alarm/month | ~$0.30/metric/month + $3/anomaly evaluation |
| Best for | Security (deterministic), compliance | Performance monitoring, capacity planning |

**Recommendation**: Use Log Alarms for security detection (ARP, failed auth, bulk deletion) where you know the exact pattern. Use Anomaly Detection on FSx for ONTAP CloudWatch metrics (DataWriteIOPS) for early-warning signals that complement ARP.

### Pre-ARP Early Warning via CloudWatch Anomaly Detection

FSx for ONTAP publishes `DataWriteIOPS` to CloudWatch. Ransomware causes a dramatic IOPS spike from file 1 (before ARP detects at file 20+). An Anomaly Detection alarm on this metric provides 10-30 seconds earlier warning:

```yaml
# CloudFormation snippet
WriteIopsAnomalyAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: fsxn-write-iops-anomaly
    MetricName: DataWriteIOps
    Namespace: AWS/FSx
    Dimensions:
      - Name: FileSystemId
        Value: !Ref FileSystemId
      - Name: VolumeId
        Value: !Ref VolumeId
    Statistic: Sum
    Period: 60
    EvaluationPeriods: 2
    ThresholdMetricId: ad1
    ComparisonOperator: GreaterThanUpperThreshold
    Metrics:
      - Id: m1
        MetricStat:
          Metric:
            MetricName: DataWriteIOps
            Namespace: AWS/FSx
          Period: 60
          Stat: Sum
      - Id: ad1
        Expression: ANOMALY_DETECTION_BAND(m1, 3)
```

---

## 2. MITRE ATT&CK Mapping

### FSx for ONTAP Events → ATT&CK Techniques

| Detection Source | Event | MITRE Technique | Tactic | Auto-Response? |
|-----------------|-------|-----------------|--------|---------------|
| ARP alert | `arw.volume.state` (alert) | T1486 Data Encrypted for Impact | Impact | ✅ Auto-block (storage layer) |
| FPolicy | Mass file deletion (>50/5min) | T1485 Data Destruction | Impact | ✅ Auto-block (storage layer) |
| FPolicy | Mass file rename (.encrypted) | T1486 Data Encrypted for Impact | Impact | ✅ Auto-block (storage layer) |
| Admin audit | Failed management login (>10) | T1110 Brute Force | Credential Access | ⚠️ Notify + investigate |
| Admin audit | Unauthorized export-policy change | T1562.001 Disable or Modify Tools | Defense Evasion | ⚠️ Notify |
| FPolicy | Access from unusual IP | T1021.002 SMB/Windows Admin Shares | Lateral Movement | ⚠️ Investigate |
| Admin audit | Snapshot deletion | T1490 Inhibit System Recovery | Impact | ⚠️ Critical notify |
| EMS | ARP disabled | T1562.001 Disable or Modify Tools | Defense Evasion | ⚠️ Critical notify |
| Admin audit | Name-mapping modified | T1098 Account Manipulation | Persistence | ⚠️ Notify |
| EMS | SnapMirror broken | T1490 Inhibit System Recovery | Impact | ⚠️ Notify |

> **Scope note**: "Auto-block" rows trigger the [Automated Response module](automated-response-guide.md)'s containment-phase actions (block user/IP, snapshot, session disconnect) at the storage layer only. Eradication and recovery (host isolation, malware removal, credential rotation) are not automated and still require human follow-up or a separate IR tool.

> **SOC Integration**: For SIEM platforms with MITRE ATT&CK integration (Splunk ES, Elastic SIEM, Datadog Cloud SIEM), tag each detection rule with the corresponding technique ID. This enables ATT&CK Navigator coverage visualization.

---

## 3. OpenTelemetry Semantic Convention Mapping

### ONTAP EMS Fields → OTel Semantic Conventions

| ONTAP EMS Field | OTel Semantic Convention | Attribute Key | Example |
|----------------|------------------------|---------------|---------|
| SVM name | `host.name` | `host.name` | `svm-prod-01` |
| Event name | `event.name` | `event.name` | `arw.volume.state` |
| Severity | `severity_text` / `severity_number` | `severity_text` | `alert` |
| Username | `user.name` | `enduser.id` | `CORP\jdoe` |
| Client IP | `client.address` | `client.address` | `203.0.113.99` |
| Volume | `service.name` | `service.name` | `vol_data` |
| File path | `file.path` | `file.path` | `/data/confidential/report.pdf` |
| Operation | `event.action` | custom: `fsxn.operation` | `read` / `write` / `delete` |
| Timestamp | `timestamp` | (built-in) | RFC 3339 |

> **Usage**: When using OTel Collector, apply the `attributes` processor to remap ONTAP field names to OTel conventions before export. This enables cross-platform correlation (e.g., correlate K8s pod events with FSx file operations by matching `client.address`).

---

## 4. Total Cost of Ownership

### Full Stack Comparison (100 users, 10 GB logs/month)

| Component | AWS-Native Only | AWS + SIEM (Datadog) | DII License | EC2 syslog (legacy) |
|-----------|----------------|---------------------|-------------|-------------------|
| Syslog VPCE | $8 | $8 | — | — |
| CloudWatch Logs (storage) | $5 | $5 | — | — |
| Log Alarm (5 alarms) | $1.50 | — | — | — |
| Response Lambda | $0.51 | $0.51 | — | — |
| TTL Lambda | $0.10 | $0.10 | — | — |
| Datadog log ingestion | — | ~$15 (10 GB × $1.50/GB retention) | — | — |
| DII SWS license | — | — | $5,000-15,000/year (per-node) | — |
| EC2 instances (2×t3.medium) | — | — | — | $66 |
| EC2 management overhead | — | — | — | ~$20 (patches, monitoring) |
| **Monthly total** | **~$15** | **~$29** | **~$400-1,250** | **~$86** |
| **Annual total** | **~$180** | **~$350** | **$5,000-15,000** | **~$1,030** |

> **Note**: DII pricing varies by contract. Figures are indicative only. The comparison is for the monitoring/response stack cost, not the FSx for ONTAP infrastructure itself.

> **Trade-off**: Lower cost (AWS-native) vs richer ML detection (DII) vs broader SIEM context (Datadog/Splunk). The choice depends on existing investments and security maturity.

---

## 5. Cross-Account Observability Pattern

### Architecture: FSx in Workload Account, Monitoring in Central Account

```
Workload Account (FSx for ONTAP)
  ├── Syslog VPCE → CloudWatch Logs (source)
  ├── Response Lambda (same VPC as FSx)
  └── CloudWatch Logs Subscription Filter
            │
            ▼ (Cross-account destination)
Central Security Account
  ├── CloudWatch Logs Destination
  ├── Log Alarms + Dashboards
  ├── SIEM integration (Datadog/Splunk/Elastic)
  └── SNS → Response trigger (cross-account publish back to workload)
```

**Key configuration**: CloudWatch Logs resource policy on the source account must allow `logs:PutSubscriptionFilter` from the central account's delivery role.

---

## 6. Vendor Portability Matrix

### What Changes When Switching Components

| If you switch... | What needs to change | What stays the same |
|-----------------|---------------------|-------------------|
| Log destination (Datadog → Grafana) | Exporter config in Lambda/OTel | Detection logic, response pipeline, ONTAP config |
| Detection platform (Datadog → Elastic) | SIEM rule syntax, SNS trigger method | Response Lambda, ONTAP config, notification chain |
| Response action target (this module → DII) | Entire response stack | Detection pipeline, log destination |
| CloudWatch → Vendor-native (no CW) | Remove syslog VPCE, use direct Lambda | EMS webhook stays, response pipeline stays |
| Single vendor → OTel multi-backend | Add OTel Collector, reconfigure exporters | ONTAP config, detection thresholds |

**Key insight**: The SNS trigger topic is the "universal interface" between detection and response. Any system that can publish JSON to SNS can trigger a storage-layer block. This is the portability layer.

---

## 7. Observability Health Monitoring (Canary Pattern)

Monitor the monitoring pipeline itself:

```
EventBridge Schedule (hourly)
  → SNS publish: {"action": "health_check", "svm_name": "svm-prod"}
  → Response Lambda
  → Result published to notification topic
  → CloudWatch Metric: custom/ResponsePipeline/HealthStatus (1=healthy, 0=unhealthy)
  → CloudWatch Alarm: if HealthStatus < 1 for 2 consecutive checks → alert
```

This ensures you discover pipeline failures (ONTAP unreachable, Lambda misconfigured, credentials expired) BEFORE a real incident requires the pipeline to work.

---

## 8. Log Volume Estimation Guide

### Expected Log Volume by Source

| Source | Typical Volume (100 users) | Factors |
|--------|--------------------------|---------|
| Admin audit (syslog) | 50-200 MB/month | Admin activity frequency |
| EMS events | 1-10 MB/month | Infrastructure event frequency |
| File access audit (EVTX) | 1-10 GB/month | File operation intensity |
| FPolicy (real-time) | 5-50 GB/month | File creation/deletion rate |
| Response Lambda logs | <1 MB/month | Incident frequency |

### CloudWatch Logs Cost Estimation

| Log Volume | Ingestion ($0.50/GB) | Storage ($0.03/GB/month, 30d) | Total/month |
|-----------|---------------------|-------------------------------|-------------|
| 500 MB/month | $0.25 | $0.02 | $0.27 |
| 5 GB/month | $2.50 | $0.15 | $2.65 |
| 50 GB/month | $25.00 | $1.50 | $26.50 |
| 200 GB/month | $100.00 | $6.00 | $106.00 |

> **Cost optimization**: Use S3 export for logs older than 30 days ($0.023/GB vs $0.03/GB). Set CloudWatch Logs retention to 30 days and export to S3 for long-term.

---

## Related Documents

- [Automated Response Guide](automated-response-guide.md)
- [Security Addendum](automated-response-security-addendum.md)
- [EMS Detection Capabilities](ems-detection-capabilities.md)
- [Pipeline SLO](pipeline-slo.md)
- [OTel Collector PII Redaction Cookbook](../../integrations/otel-collector/docs/en/pii-redaction-cookbook.md)
