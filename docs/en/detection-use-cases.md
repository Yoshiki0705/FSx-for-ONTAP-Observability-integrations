# Detection Use Cases

🌐 [日本語](../ja/detection-use-cases.md) | **English** (this page)

## Event Source Selection Matrix

| Detection Use Case | Best Source | Why | Latency |
|-------------------|-------------|-----|---------|
| Ransomware encryption behavior | EMS (ARP) | ONTAP native ML-based detection | Real-time (webhook) |
| Bulk file deletion | Audit Logs or FPolicy | Audit for near-real-time, FPolicy for event-driven | Minutes / Seconds |
| Unusual read volume | Audit Logs | Policy-dependent, volume-heavy | Minutes |
| Unauthorized access attempts | Audit Logs | Failed access events (Result: Failure) | Minutes |
| Real-time file blocking / DLP | FPolicy | Protocol-level interception | Sub-second |
| Quota threshold exceeded | EMS | ONTAP native quota monitoring | Real-time (webhook) |
| Suspicious user behavior | FPolicy + Audit Logs | Correlate event-driven ops with historical pattern | Seconds + Minutes |
| Permission changes | Audit Logs | SACL/ACL modification events | Minutes |

## Source Characteristics

| Source | Latency Model | Volume | Best For |
|--------|--------------|--------|----------|
| File Access Audit Logs | Near-real-time (Scheduler frequency + rotation interval) | High (especially with read auditing) | Compliance, forensics, pattern analysis |
| EMS Webhooks | Real-time (HTTPS push) | Low (critical events only) | Security alerting, operational monitoring |
| FPolicy | Event-driven (TCP stream) | Medium-High (all file operations) | DLP, event-driven monitoring, suspicious behavior |

## Datadog Monitor Examples by Use Case

### Ransomware Detection (ARP + EMS)

```
Query: source:fsxn-ems @attributes.event_name:arw.volume.state @attributes.parameters.state:attack-detected
Threshold: critical > 0
Action: PagerDuty + Slack + snapshot affected volume
```

### Bulk Failed Access (Audit Logs)

```
Query: source:fsxn @attributes.result:Failure
Threshold: critical > 10 in 5 minutes
Action: Investigate user + client IP
```

### Unusual File Deletion Rate (FPolicy)

```
Query: source:fsxn-fpolicy @attributes.operation_type:delete
Threshold: warning > 50 in 5 minutes, critical > 200 in 5 minutes
Action: Correlate with user identity
```

### Sensitive Path Access (Audit Logs)

```
Query: source:fsxn @attributes.path:"/vol/data/confidential/*"
Threshold: warning > 0 (any access to sensitive path)
Action: Log review + user verification
```

## Correlation Patterns

For advanced detection, correlate across sources:

1. **ARP alert + FPolicy bulk operations** — Confirms ransomware activity with file-level detail
2. **Failed access spike + successful access from new IP** — Potential credential compromise
3. **Quota warning + bulk write from single user** — Possible data exfiltration or abuse

---

## CloudWatch Log Alarm Native Detection (GA 2026-07)

CloudWatch Log Alarm enables creating alarms directly from CloudWatch Logs without metric filters.

### Scope

- **Admin audit logs** (Syslog VPC Endpoint → CloudWatch Logs): ✅ Directly usable
- **File access audit logs** (FSx for ONTAP S3 AP → EventBridge Scheduler → Lambda): Requires separate pipeline to CloudWatch Logs

### Detection Patterns

| Pattern | Query | Threshold | Use Case |
|---------|-------|-----------|----------|
| Sensitive path access | `filter @message like /\/vol\/data\/confidential/` | > 0 | Compliance |
| Authorization-denial spike | `filter @message like /Error: not authorized/` | > 10 | Commands run without the required role |
| Bulk deletion | `filter @message like /DELETE.*::\sSuccess/ or @message like /DELETE.*::\sError/ or ...` (6 terms) | > 50 | Ransomware indicator |
| Privileged user ops | `filter @message like /fsxadmin:fsxadmin/ and @message not like /Pending/` | > 0 | Internal controls |

The last three rows are the filters of the template's `failed-access-attempts`, `bulk-delete-operations` and `specific-user-activity` queries, translated from the patterns checked in the 2026-10-09 run and not run as LogAlarm queries (`unverified`). The full bulk-deletion filter and the status of each are in [Built-in Detection Queries](./cloudwatch-log-alarm.md#built-in-detection-queries).

### Deploy

```bash
DETECTION_TYPE=sensitive-file-access \
TARGET_PATTERN="/vol/data/confidential" \
CREATE_SNS_TOPIC=true \
  bash shared/scripts/deploy-log-alarm.sh
```

Details: [CloudWatch Log Alarm Setup Guide](./cloudwatch-log-alarm.md)
