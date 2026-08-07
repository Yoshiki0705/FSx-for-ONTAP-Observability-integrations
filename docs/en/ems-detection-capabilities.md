# EMS Event Detection Capabilities — Reference Guide

🌐 [日本語](../ja/ems-detection-capabilities.md) | **English** (this page)

## Executive Summary

ONTAP EMS (Event Management System) provides near-real-time event notifications from FSx for ONTAP. This guide catalogs the detectable events, delivery latency, delivery mechanisms, and integration patterns available in this project.

**Key points:**
- EMS delivery is **Push-based** (event-driven), not polling
- Two push paths: EMS Webhook (~30s) and Syslog VPCE (seconds)
- EventBridge Scheduler polling (5 min) is used only for file access audit logs on S3 — NOT for EMS
- 100+ event categories available, most relevant ones cataloged below

---

## Delivery Mechanisms

### Push Path 1: EMS Webhook (HTTPS POST) — Recommended for targeted alerts

```
ONTAP EMS event → HTTPS POST (immediate)
  → API Gateway → Lambda → Observability Platform
```

| Attribute | Value |
|-----------|-------|
| Delivery model | Push (event-driven) |
| Latency | **~30 seconds** (E2E verified, Tokyo region) |
| Protocol | HTTPS POST (TLS 1.2+) |
| Format | JSON (configurable fields) |
| Filtering | ONTAP-side filter (event name, severity) |
| Reliability | At-least-once (ONTAP retries on failure) |
| Configuration | `event notification destination create -rest-api-url <url>` |

**Best for**: Critical alerts requiring immediate action (ARP, HA failover, capacity critical).

### Push Path 2: Syslog VPCE — Recommended for comprehensive logging

```
ONTAP log-forwarding → VPC Endpoint (TCP+TLS:6514)
  → CloudWatch Logs (managed syslog ingestion)
```

| Attribute | Value |
|-----------|-------|
| Delivery model | Push (stream) |
| Latency | **Seconds to tens of seconds** |
| Protocol | TCP+TLS (6514), TCP (1514), or UDP (514) |
| Format | Syslog (RFC 5424 / RFC 3164) |
| Filtering | Facility-level (local0-local7) |
| Reliability | TCP = reliable; UDP = best-effort |
| Configuration | `cluster log-forwarding create -destination <vpce-ip> -port 6514 -protocol tcp-encrypted` |

**Best for**: Comprehensive admin audit trail + EMS events in CloudWatch Logs for analysis and alarming.

### Pull Path: EventBridge Scheduler (S3 AP) — File access audit only

```
EventBridge Scheduler (5 min) → Lambda
  → S3 AP → Read EVTX/XML files → Process
```

| Attribute | Value |
|-----------|-------|
| Delivery model | Pull (polling) |
| Latency | **5 minutes** (configurable, 1-60 min) |
| Scope | File access audit logs only (EVTX/XML on S3) |
| NOT used for | EMS events |

> **Important**: The EventBridge Scheduler path is exclusively for file access audit logs (NFS/SMB file operations stored as EVTX/XML on S3). EMS events use the push paths above.

---

## EMS Event Catalog — Security & Operations

### Ransomware / Data Protection

| EMS Event | Severity | Description | Detection Use Case |
|-----------|----------|-------------|-------------------|
| `arw.volume.state` | alert | ARP detected ransomware-like activity | Immediate containment trigger |
| `arw.volume.state` | warning | ARP suspects anomalous behavior | Investigation trigger |
| `arw.vserver.state` | notice | ARP mode changed (learning/active/disabled) | Configuration drift |

**ARP detection details:**
- Entropy changes (file content becoming random → encryption indicator)
- Mass file extension changes (20+ files with unusual new extensions)
- Abnormal IOPS surge with encrypted data characteristics
- Learning period: 30 days (dry-run mode, establishes baseline)

**Automatic actions on `arw.volume.state` alert:**
1. ONTAP creates `Anti_ransomware_backup` snapshot automatically
2. EMS event emitted → Webhook delivers to Observability platform
3. (This project) Automated response Lambda can block user/IP

### Capacity & Quota

| EMS Event | Severity | Description | Detection Use Case |
|-----------|----------|-------------|-------------------|
| `wafl.vol.autoSize.done` | notice | Volume auto-resized | Capacity trending |
| `wafl.vol.autoSize.fail` | error | Volume auto-resize failed | Urgent: space exhaustion imminent |
| `wafl.quota.softlimit.exceeded` | warning | Qtree/user quota soft limit exceeded | Warn user/admin |
| `wafl.quota.hardlimit.exceeded` | error | Qtree/user quota hard limit exceeded (writes blocked) | Critical: production impact |
| `monitor.volume.full` | alert | Volume at 100% capacity | Emergency |
| `monitor.volume.nearlyFull` | warning | Volume approaching full (configurable %) | Proactive alert |

### HA / Availability

| EMS Event | Severity | Description | Detection Use Case |
|-----------|----------|-------------|-------------------|
| `cf.takeover.general` | alert | HA takeover occurred | Availability incident |
| `cf.giveback.started` | notice | HA giveback started | Recovery tracking |
| `cf.giveback.completed` | notice | HA giveback completed | Recovery confirmed |
| `cf.hwassist.takeover` | alert | Hardware-assisted takeover | Hardware failure |

### SnapMirror / Replication

| EMS Event | Severity | Description | Detection Use Case |
|-----------|----------|-------------|-------------------|
| `snapmirror.relationship.status` | warning | Replication relationship unhealthy | DR integrity risk |
| `snapmirror.relationship.transfer.failed` | error | Transfer failed | Data protection gap |
| `snapmirror.relationship.out.of.sync` | warning | Lag exceeds threshold | RPO at risk |

### Network / LIF

| EMS Event | Severity | Description | Detection Use Case |
|-----------|----------|-------------|-------------------|
| `net.ifgrp.link.down` | warning | Interface group link down | Connectivity degradation |
| `lif.up` | notice | LIF came online | Recovery tracking |
| `lif.down` | warning | LIF went offline | Access interruption |

### Security / Authentication

| EMS Event | Severity | Description | Detection Use Case |
|-----------|----------|-------------|-------------------|
| `mgwd.login.failed` | warning | Management login failed | Brute force detection |
| `mgwd.login.succeeded` | notice | Management login succeeded | Audit trail |
| `secd.cifsAuth.problem` | warning | CIFS/SMB authentication failure | AD integration issues |
| `secd.nfsAuth.problem` | warning | NFS authentication failure | Export policy issues |

### FPolicy

| EMS Event | Severity | Description | Detection Use Case |
|-----------|----------|-------------|-------------------|
| `fpolicy.server.connect.error` | warning | FPolicy server connection failed | Monitoring gap |
| `fpolicy.server.connected` | notice | FPolicy server connected | Recovery confirmation |
| `fpolicy.policy.disabled` | warning | FPolicy policy disabled | Security gap |

### Disk / Storage Health

| EMS Event | Severity | Description | Detection Use Case |
|-----------|----------|-------------|-------------------|
| `raid.rg.disk.missing` | error | Disk missing from RAID group | Hardware failure |
| `disk.failmsg` | alert | Disk failure | Data protection risk |
| `aggr.check.failed` | error | Aggregate check failed | Data integrity |

---

## Integration Patterns

### Pattern 1: EMS Webhook → Observability Platform (Direct)

```bash
# ONTAP CLI: Configure webhook destination
event notification destination create \
  -name datadog-webhook \
  -rest-api-url https://xxxxx.execute-api.ap-northeast-1.amazonaws.com/prod/ems

# Create filter for critical events
event filter create -filter-name critical-events
event filter rule add -filter-name critical-events \
  -type include -message-name arw.volume.state
event filter rule add -filter-name critical-events \
  -type include -message-name wafl.quota.hardlimit.exceeded
event filter rule add -filter-name critical-events \
  -type include -message-name cf.takeover.general

# Bind filter to destination
event notification create \
  -filter-name critical-events \
  -destinations datadog-webhook
```

**Latency**: ~30 seconds (verified)
**Use case**: Critical alerts requiring immediate action

### Pattern 2: Syslog VPCE → CloudWatch Logs → Log Alarm

```bash
# ONTAP CLI: Configure syslog forwarding
cluster log-forwarding create \
  -destination <syslog-vpce-eni-ip> \
  -port 6514 \
  -protocol tcp-encrypted \
  -facility local7

# AWS: CloudWatch Log Alarm detects patterns
# (configured via CloudFormation, see shared/templates/cloudwatch-log-alarm.yaml)
```

**Latency**: Seconds (syslog) + 1 min (Log Alarm evaluation)
**Use case**: Broad monitoring with Logs Insights query flexibility

### Pattern 3: EMS Webhook → Automated Response (This Project)

```bash
# Detection: EMS → Webhook → Datadog Monitor → SNS
# Response: SNS → Lambda → ONTAP REST API (block user / snapshot)

# Or: EMS → Webhook → CloudWatch Log Alarm → SNS → Lambda
```

**Latency**: ~30s (detection) + ~5s (response) = ~35s total
**Use case**: Automated containment (ransomware, insider threat)

---

## Delivery Latency Comparison

| Path | Detection Latency | E2E to Alert | Best For |
|------|------------------|-------------|----------|
| EMS Webhook → Datadog Monitor | ~30s | ~60s (with monitor evaluation) | Targeted critical alerts |
| Syslog VPCE → CW Log Alarm | seconds | ~90s (with alarm evaluation period) | Broad admin audit monitoring |
| EMS Webhook → Auto-Response | ~30s | ~35s (immediate action) | Automated containment |
| EventBridge Scheduler → S3 AP | 5 min | 5-10 min | File access audit (compliance) |

---

## ONTAP EMS Filter Configuration Guide

### List Available Events

```bash
# SSH to FSx for ONTAP
ssh fsxadmin@<management-ip>

# List all EMS event categories
event catalog show

# Search for specific events
event catalog show -message-name *arw*
event catalog show -message-name *quota*
event catalog show -message-name *snapmirror*

# View event details
event catalog show -message-name arw.volume.state -instance
```

### Create Custom Filters

```bash
# Create filter for security events
event filter create -filter-name security-events
event filter rule add -filter-name security-events \
  -type include -message-name arw.*
event filter rule add -filter-name security-events \
  -type include -message-name mgwd.login.failed
event filter rule add -filter-name security-events \
  -type include -message-name fpolicy.*

# Create filter for capacity alerts
event filter create -filter-name capacity-events
event filter rule add -filter-name capacity-events \
  -type include -message-name wafl.quota.*
event filter rule add -filter-name capacity-events \
  -type include -message-name monitor.volume.*
event filter rule add -filter-name capacity-events \
  -type include -message-name wafl.vol.autoSize.*
```

### Verify Configuration

```bash
# Show notification destinations
event notification destination show

# Show active notifications
event notification show

# Show recent EMS events
event log show -time >1h
```

---

## FAQ

**Q: Is EMS delivery real-time or batch?**
A: Real-time (push). ONTAP delivers EMS events immediately when they occur via Webhook (HTTPS POST) or syslog stream. There is no batching or scheduled delivery for EMS events.

**Q: What happens if the webhook destination is unavailable?**
A: ONTAP retries delivery. The exact retry behavior depends on ONTAP version, but events are buffered temporarily. For guaranteed delivery, use the syslog path to CloudWatch Logs (persistent storage) combined with webhook for low-latency alerting.

**Q: Can I filter events at the ONTAP level?**
A: Yes. ONTAP event filters allow include/exclude rules by event name pattern and severity. This reduces noise and Lambda invocations. Only events matching the filter are sent to the destination.

**Q: How many webhook destinations can I configure?**
A: ONTAP supports multiple notification destinations. You can send the same events to multiple destinations (e.g., Datadog + CloudWatch) simultaneously.

**Q: Does the Syslog VPCE path include EMS events or only CLI audit?**
A: Both. The `cluster log-forwarding` command sends management audit logs (CLI/API operations) AND EMS events as syslog messages. The facility code helps distinguish them.

---

## Related Documents

- [Architecture Evolution: Syslog VPCE](architecture-evolution-syslog-vpce.md)
- [ARP Incident Response Guide](arp-incident-response-guide.md)
- [Automated Response Guide](automated-response-guide.md)
- [EMS Webhook Setup (Datadog)](../../integrations/datadog/docs/en/ems-fpolicy-setup.md)
- [AWS Docs: Monitoring EMS Events](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/ems-events.html)
- [NetApp: EMS Configuration](https://docs.netapp.com/us-en/ontap/error-messages/index.html)
