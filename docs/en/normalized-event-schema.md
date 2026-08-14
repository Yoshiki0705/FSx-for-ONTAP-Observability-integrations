# Normalized Event Schema

🌐 [日本語](../ja/normalized-event-schema.md) | **English** (this page)

## Overview

All vendor integrations normalize ONTAP audit events into a common internal schema before mapping to vendor-specific formats. This ensures consistent field naming across all observability platforms.

## Internal Normalized Schema

```json
{
  "event_type": "file_access",
  "source": "fsxn",
  "timestamp": "2026-05-17T01:30:00.000Z",
  "svm": "svm-prod-01",
  "user": "admin@corp.local",
  "client_ip": "198.51.100.50",
  "operation": "ReadData",
  "path": "/vol/data/reports/quarterly.xlsx",
  "result": "Success",
  "raw": {}
}
```

## Field Definitions

| Field | Type | Description | Source |
|-------|------|-------------|--------|
| `event_type` | string | Event category (`file_access`, `ems_alert`, `fpolicy_op`) | Derived |
| `source` | string | Event source identifier (`fsxn`, `fsxn-ems`, `fsxn-fpolicy`) | Configured |
| `timestamp` | ISO 8601 | Event timestamp from ONTAP | EVTX record / XML TimeCreated |
| `svm` | string | Storage Virtual Machine name | EVTX SVMName / XML Computer |
| `user` | string | User who performed the operation | EVTX UserName / XML SubjectUserName |
| `client_ip` | string | Client IP address | EVTX ClientIP / XML IpAddress |
| `operation` | string | Operation type | EVTX Operation / XML ObjectType |
| `path` | string | File or directory path | EVTX ObjectName / XML ObjectName |
| `result` | string | Success or Failure | EVTX Result / XML Keywords |
| `raw` | object | Original parsed fields (vendor-specific use) | Full parsed event |

## Vendor Mapping Matrix

| Internal Field | Datadog | Splunk HEC | Elastic (ECS) | Loki | New Relic | OTel (OTLP) |
|---------------|---------|------------|---------------|------|-----------|-------------|
| `source` | `source` tag | `source` | `event.dataset` | `source` label | `logtype` | `event.name` |
| `svm` | `@attributes.svm` | `svm` field | `netapp.ontap.svm` | `svm` label | `svm` attribute | `netapp.ontap.svm` |
| `user` | `@attributes.user` | `user` field | `user.name` | JSON body | `user` attribute | `user.name` |
| `client_ip` | `@attributes.client_ip` | `client_ip` field | `source.ip` | JSON body | `client_ip` attribute | `source.address` |
| `operation` | `@attributes.operation` | `action` field | `event.action` | `operation` label | `operation` attribute | `event.action` |
| `path` | `@attributes.path` | `file_path` field | `file.path` | JSON body | `path` attribute | `file.path` |
| `result` | `@attributes.result` | `result` field | `event.outcome` | `result` label | `result` attribute | `event.outcome` |
| `timestamp` | `timestamp` | `_time` | `@timestamp` | timestamp | `timestamp` | `TimeUnixNano` |

## Vendor-Specific Considerations

### Datadog
- Use `source` and `service` tags for pipeline routing
- Custom attributes under `@attributes.*` namespace
- Index routing via Datadog Log Pipeline

### Splunk
- Map to `sourcetype=fsxn:audit:evtx` or `fsxn:audit:xml`
- Use `index=fsxn_audit` for dedicated retention
- Consider CIM `Authentication` and `Change` data models

### Elastic
- Follow Elastic Common Schema (ECS) where possible
- Use `netapp.ontap.*` namespace for ONTAP-specific fields
- Data streams with ILM for retention management

### Grafana / Loki
- Keep labels low-cardinality: `source`, `svm`, `operation`, `result`
- Store high-cardinality fields (`path`, `user`, `client_ip`) in JSON log body
- Do NOT put file paths or usernames into Loki labels

### New Relic
- Map to Log API attributes
- Use NRQL for querying: `FROM Log WHERE source = 'fsxn'`
- Entity relationship via `aws.account.id` + `fsx.filesystem.id`

### Honeycomb
- All fields as event attributes (high-cardinality is fine)
- Add pipeline observability fields: `processing_latency_ms`, `batch_size`
- Use derived columns for path prefix analysis

### OpenTelemetry (OTLP)
- Follow OpenTelemetry Semantic Conventions for Logs
- Use `event.*`, `file.*`, `user.*`, `source.*` namespaces
- Resource attributes: `cloud.provider`, `cloud.region`, `cloud.account.id`

## Design Principles

1. **Normalize once, map per vendor** — Parsing and normalization happen in a shared layer; vendor-specific formatting is the only per-vendor code.

2. **Vendor-specific Lambdas are optimized for quick adoption and native API behavior**, while the OpenTelemetry integration provides a vendor-neutral path for organizations standardizing on OTLP.

3. **Treat the audit pipeline itself as an observable system** — Emit processing latency, batch size, retry count, and vendor response metadata alongside audit events where the vendor supports it.


## EMS / ARP Event Mapping

| Internal Field | Datadog | OpenTelemetry | Splunk | Elastic ECS |
|---------------|---------|---------------|--------|-------------|
| `event_name` | `@attributes.event_name` | `event.name` | `event_name` | `event.action` |
| `severity` | `@attributes.severity` | `severity_text` | `severity` | `event.severity` |
| `svm` | `@attributes.svm` | `netapp.ontap.svm` | `svm` | `netapp.ontap.svm` |
| `source_node` | `host` | `host.name` | `host` | `host.name` |
| `parameters.volume_name` | `@attributes.parameters.volume_name` | `netapp.ontap.volume` | `volume_name` | `netapp.ontap.volume` |
| `parameters.state` | `@attributes.parameters.state` | `netapp.ontap.arp.state` | `arp_state` | `netapp.ontap.arp.state` |
| `timestamp` | `date` | `time_unix_nano` | `_time` | `@timestamp` |
| `message` | `message` | `body` | `_raw` | `message` |


## FPolicy File Operation Event Mapping

| Internal Field | Datadog | OpenTelemetry | Splunk | Elastic ECS |
|---------------|---------|---------------|--------|-------------|
| `operation_type` | `@attributes.operation_type` | `event.action` | `operation_type` | `event.action` |
| `file_path` | `@attributes.file_path` | `file.path` | `file_path` | `file.path` |
| `client_ip` | `@attributes.client_ip` | `source.ip` | `client_ip` | `source.ip` |
| `volume_name` | `@attributes.volume_name` | `netapp.ontap.volume` | `volume_name` | `netapp.ontap.volume` |
| `svm` | `@attributes.svm` | `netapp.ontap.svm` | `svm` | `netapp.ontap.svm` |
| `user` | `@attributes.user` | `user.name` | `user` | `user.name` |
| `protocol` | `@attributes.protocol` | `network.protocol` | `protocol` | `network.protocol` |
| `timestamp` | `date` | `time_unix_nano` | `_time` | `@timestamp` |
| `event_id` | `@attributes.event_id` | `event.id` | `event_id` | `event.id` |

### Notes

- FPolicy uses `operation_type` (create, write, rename, delete) while audit logs use `operation` (ReadData, WriteData, etc.). This is intentional — they represent different abstraction levels.
- The `svm` field may show "unknown" if the FPolicy server cannot resolve the SVM name from the handshake context.
- The `user` field may be empty for some operations depending on ONTAP's FPolicy notification content.
