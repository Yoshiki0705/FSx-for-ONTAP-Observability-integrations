🌐 [日本語](../ja/agent-fpolicy-correlation-pattern.md) | **English**

# AI Agent Access Log × ONTAP FPolicy Audit Log Correlation Pattern

> **Status**: Design document (implementation in a subsequent phase)
> **Prerequisite**: Agent infrastructure (Omnigent / AgentCore) must be built first
> **Related**: [FSx-for-ONTAP-Lakehouse-Integrations Cross-Repo Integration Strategy](https://github.com/Yoshiki0705/FSx-for-ONTAP-Lakehouse-Integrations/blob/main/docs/en/cross-repo-integration-strategy.md)

---

## Overview

This document defines a pattern for tracking "which agent, in which session, at what time, accessed which file-derived information" when AI agents access data on FSx for ONTAP.

Agent-side OpenTelemetry spans are joined with ONTAP FPolicy file access events on a time axis to build an end-to-end audit trail.

### Problems Solved

| Problem | Description |
|---------|-------------|
| Agent transparency | Which files did the agent read? What was passed to the LLM context? |
| Permission-aware auditing | Proof that the agent did not access data beyond its authorization |
| Incident investigation | Identifying the source file for incorrect answers or information leaks |
| Compliance | Tracking "who triggered which AI to read what, and when" |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Agent Layer                            │
│  Omnigent / AgentCore / Bedrock Agent                       │
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌──────────────┐   │
│  │ Supervisor  │───▶│ Sub-Agent   │───▶│ Tool: FSx    │   │
│  │   Agent     │    │ (Quality)   │    │ File Reader  │   │
│  └─────────────┘    └─────────────┘    └──────┬───────┘   │
│                                                │            │
│         OTel Spans (tool_call, file_access)    │            │
└─────────────────────────────────────────────┬──┼────────────┘
                                              │  │
                    ┌─────────────────────────┘  │
                    ▼                            ▼
┌──────────────────────────┐    ┌──────────────────────────────┐
│   ADOT Collector          │    │   FSx for ONTAP              │
│   (OTel → CloudWatch/     │    │   FPolicy → SQS → Lambda    │
│    X-Ray / S3 / SIEM)     │    │   → SIEM / S3 / OpenSearch  │
└────────────┬─────────────┘    └─────────────┬────────────────┘
             │                                 │
             ▼                                 ▼
┌──────────────────────────────────────────────────────────────┐
│                   Analytics / Correlation Layer               │
│   OpenSearch / Athena / CloudWatch Logs Insights             │
│                                                              │
│   JOIN ON: service_account + time_window + file_path         │
│                                                              │
│   ┌─────────────────────────────────────────────────────┐   │
│   │           Correlation Record (join result)            │   │
│   └─────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

---

## Log Schema Design

### 1. Agent Access Span (OTel Span Attributes)

Records the OTel span when an agent accesses a file on FSx for ONTAP via a tool call.

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `trace_id` | string | W3C Trace Context | `4bf92f3577b34da6a3ce929d0e0e4736` |
| `span_id` | string | Span ID | `00f067aa0ba902b7` |
| `parent_span_id` | string | Parent span (orchestrator) | `a1b2c3d4e5f6a7b8` |
| `agent_id` | string | Agent identifier | `quality-supervisor` |
| `session_id` | string | Session ID (Omnigent/AgentCore) | `sess_2026061812345` |
| `tool_name` | string | Executed tool name | `read_file`, `list_directory` |
| `file_path` | string | Requested file path (normalized POSIX) | `/vol1/shared/reports/Q2.xlsx` |
| `svm_name` | string | Target SVM | `svm-prod-01` |
| `volume_name` | string | Target volume | `vol_shared_docs` |
| `service_account` | string | Identity used for file access | `CORP\svc-agent-quality` |
| `operation` | enum | Operation type | `read` / `write` / `list` / `delete` |
| `timestamp_start` | ISO 8601 | Span start | `2026-06-18T10:30:00.000Z` |
| `timestamp_end` | ISO 8601 | Span end | `2026-06-18T10:30:01.234Z` |
| `status` | enum | Result | `ok` / `error` |
| `bytes_read` | int | Bytes read | `524288` |
| `user_principal` | string | Human user who triggered the agent | `tanaka@corp.example.com` |
| `purpose` | string | Access purpose (optional) | `rag_context_retrieval` |

### 2. FPolicy Event (ONTAP Side)

File access events recorded by ONTAP FPolicy.

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `event_id` | string | ONTAP internal event ID | `fp-00001234` |
| `timestamp` | ISO 8601 | Event time | `2026-06-18T10:30:00.456Z` |
| `svm_name` | string | SVM name | `svm-prod-01` |
| `volume_name` | string | Volume name | `vol_shared_docs` |
| `path` | string | File path | `/vol1/shared/reports/Q2.xlsx` |
| `user` | string | Accessing user (SMB: DOMAIN\user) | `CORP\svc-agent-quality` |
| `client_ip` | string | Client IP | `<agent-host-ip>` |
| `operation` | enum | File operation | `open` / `read` / `write` / `close` / `delete` / `rename` |
| `protocol` | enum | Protocol | `nfs` / `smb` |
| `result` | enum | Result | `success` / `failure` |
| `handle_id` | string | File handle | `0x000001A4` |

### 3. Correlation Record (Computed)

Join result generated in the analytics layer.

| Field | Type | Description |
|-------|------|-------------|
| `correlation_id` | string | Unique correlation ID (generated) |
| `trace_id` | string | Agent span trace_id |
| `span_id` | string | Agent span span_id |
| `fpolicy_event_id` | string | FPolicy event ID |
| `agent_id` | string | Agent identifier |
| `session_id` | string | Session ID |
| `user_principal` | string | Human user who triggered the agent |
| `file_path` | string | Normalized file path |
| `correlation_confidence` | enum | `high` / `medium` / `low` |
| `correlation_method` | string | Correlation method used |
| `time_delta_ms` | int | FPolicy timestamp − span_start (milliseconds) |
| `created_at` | ISO 8601 | Correlation record creation time |

---

## Correlation Logic (Time-Axis Join)

### Join Conditions

```sql
SELECT
  agent.trace_id,
  agent.span_id,
  agent.agent_id,
  agent.session_id,
  agent.user_principal,
  fp.event_id AS fpolicy_event_id,
  fp.path AS file_path,
  fp.operation AS fpolicy_operation,
  DATEDIFF(ms, agent.timestamp_start, fp.timestamp) AS time_delta_ms,
  CASE
    WHEN agent.service_account = fp.user
     AND fp.timestamp BETWEEN agent.timestamp_start
                          AND DATEADD(s, 5, agent.timestamp_end)
     AND normalize_path(agent.file_path) = normalize_path(fp.path)
    THEN 'high'
    WHEN agent.service_account = fp.user
     AND fp.timestamp BETWEEN agent.timestamp_start
                          AND DATEADD(s, 5, agent.timestamp_end)
    THEN 'medium'
    WHEN fp.timestamp BETWEEN agent.timestamp_start
                          AND DATEADD(s, 5, agent.timestamp_end)
     AND normalize_path(agent.file_path) = normalize_path(fp.path)
    THEN 'low'
  END AS correlation_confidence
FROM agent_access_spans agent
JOIN fpolicy_events fp
  ON fp.timestamp BETWEEN agent.timestamp_start
                      AND DATEADD(s, 5, agent.timestamp_end)
WHERE correlation_confidence IS NOT NULL
```

### Confidence Scoring

| Confidence | Conditions | Use Case |
|-----------|-----------|----------|
| **HIGH** | Service account match + within time window + path match | Audit reports, compliance evidence |
| **MEDIUM** | Service account match + within time window (path mismatch/partial) | Directory listing or metadata retrieval cases |
| **LOW** | Time window + path match only (account mismatch) | Requires manual review (possible shared account) |

### Time Window Buffer

```
span_start ─────────── span_end ──── +5s buffer
                │                          │
                ▼                          ▼
FPolicy events in this window are candidates for correlation
```

Reason for +5s buffer:
- NFS/SMB close operations may occur asynchronously after span end
- Absorbs network latency and ONTAP internal processing delay

---

## Service Account Strategy

Service account design for agent access to FSx for ONTAP. Directly impacts correlation accuracy.

| Strategy | Granularity | Correlation Accuracy | Operational Overhead | Recommended Scenario |
|----------|------------|---------------------|---------------------|---------------------|
| Per agent type | `svc-agent-quality`, `svc-agent-cataloger` | High | Medium | Standard recommendation |
| Per session | `svc-agent-sess-{session_id}` | Highest | High | High-security environments |
| Shared account | `svc-agent-common` | Low | Low | PoC only |

**Recommendation**: Per-agent-type service accounts. Place them in an Active Directory group `AG-FSx-ONTAP-Agents` and explicitly include them in FPolicy monitoring scope.

---

## Query Examples

### Q1: List All Files Accessed in a Specific Session

```sql
-- "All files read during this agent session"
SELECT DISTINCT
  cr.file_path,
  cr.correlation_confidence,
  fp.operation AS fpolicy_operation,
  fp.timestamp AS access_time,
  agent.tool_name,
  agent.bytes_read
FROM correlation_records cr
JOIN agent_access_spans agent ON cr.trace_id = agent.trace_id
                             AND cr.span_id = agent.span_id
JOIN fpolicy_events fp ON cr.fpolicy_event_id = fp.event_id
WHERE agent.session_id = 'sess_2026061812345'
  AND cr.correlation_confidence IN ('high', 'medium')
ORDER BY fp.timestamp ASC
```

### Q2: List All Agent Sessions That Accessed a Specific File

```sql
-- "All agents and sessions that read this file"
SELECT
  agent.agent_id,
  agent.session_id,
  agent.user_principal AS triggered_by,
  agent.tool_name,
  agent.timestamp_start,
  cr.correlation_confidence
FROM correlation_records cr
JOIN agent_access_spans agent ON cr.trace_id = agent.trace_id
                             AND cr.span_id = agent.span_id
WHERE cr.file_path = '/vol1/shared/reports/Q2-2026-financial.xlsx'
  AND cr.correlation_confidence IN ('high', 'medium')
ORDER BY agent.timestamp_start DESC
```

### Q3: Detect Unauthorized Access Attempts

```sql
-- "Agent attempted access that resulted in FPolicy failure = unauthorized"
SELECT
  agent.agent_id,
  agent.session_id,
  agent.user_principal,
  fp.path,
  fp.timestamp,
  fp.result
FROM fpolicy_events fp
JOIN agent_access_spans agent
  ON fp.user = agent.service_account
  AND fp.timestamp BETWEEN agent.timestamp_start
                       AND DATEADD(s, 5, agent.timestamp_end)
WHERE fp.result = 'failure'
  AND fp.user LIKE '%svc-agent-%'
ORDER BY fp.timestamp DESC
```

### Q4: Agent Access Frequency Time Series (Anomaly Detection)

```sql
-- "File access count trend per agent type"
SELECT
  agent.agent_id,
  DATE_TRUNC('hour', fp.timestamp) AS hour_bucket,
  COUNT(DISTINCT fp.path) AS unique_files_accessed,
  COUNT(*) AS total_operations,
  SUM(agent.bytes_read) AS total_bytes_read
FROM correlation_records cr
JOIN agent_access_spans agent ON cr.trace_id = agent.trace_id
JOIN fpolicy_events fp ON cr.fpolicy_event_id = fp.event_id
WHERE cr.correlation_confidence = 'high'
  AND fp.timestamp >= DATEADD(day, -7, CURRENT_TIMESTAMP)
GROUP BY agent.agent_id, DATE_TRUNC('hour', fp.timestamp)
ORDER BY hour_bucket DESC, total_operations DESC
```

### Q5: Per-Session Data Access Scope Summary

```sql
-- "Volume and path scope summary for each session"
SELECT
  agent.session_id,
  agent.agent_id,
  agent.user_principal,
  MIN(fp.timestamp) AS first_access,
  MAX(fp.timestamp) AS last_access,
  COUNT(DISTINCT fp.path) AS files_accessed,
  COUNT(DISTINCT agent.volume_name) AS volumes_touched,
  ARRAY_AGG(DISTINCT SPLIT_PART(fp.path, '/', 3)) AS directories
FROM correlation_records cr
JOIN agent_access_spans agent ON cr.trace_id = agent.trace_id
JOIN fpolicy_events fp ON cr.fpolicy_event_id = fp.event_id
WHERE cr.correlation_confidence IN ('high', 'medium')
GROUP BY agent.session_id, agent.agent_id, agent.user_principal
ORDER BY files_accessed DESC
```

---

## Path Normalization

Absorbs notation differences between FPolicy paths and agent request paths.

| Case | FPolicy Side | Agent Side | Normalized |
|------|-------------|-----------|-----------|
| SMB → POSIX | `\vol1\shared\reports\Q2.xlsx` | `/vol1/shared/reports/Q2.xlsx` | `/vol1/shared/reports/Q2.xlsx` |
| Case sensitivity | `/Vol1/Shared/Reports/Q2.xlsx` | `/vol1/shared/reports/Q2.xlsx` | `/vol1/shared/reports/q2.xlsx` (SMB: case-insensitive) |
| Trailing slash | `/vol1/shared/reports/` | `/vol1/shared/reports` | `/vol1/shared/reports` |
| Share name prefix | `\\server\share\reports\Q2.xlsx` | `/vol1/shared/reports/Q2.xlsx` | Converted via SVM share mapping table |

```python
def normalize_path(path: str, protocol: str = "smb") -> str:
    """Normalize file path for correlation matching."""
    # Backslash → forward slash
    normalized = path.replace("\\", "/")
    # Remove trailing slash
    normalized = normalized.rstrip("/")
    # Case-insensitive for SMB
    if protocol == "smb":
        normalized = normalized.lower()
    return normalized
```

---

## Implementation Notes

### Prerequisites

| Item | Requirement |
|------|-------------|
| Agent infrastructure | Omnigent / AgentCore emitting OTel spans |
| FPolicy | Enabled on FSx for ONTAP, monitoring agent service account operations |
| Time synchronization | Agent hosts and FSx for ONTAP synced via NTP (critical for correlation accuracy) |
| OTel Collector | ADOT or OTel Collector collecting spans and delivering to analytics layer |
| Analytics layer | OpenSearch / Athena / CloudWatch Logs Insights available |

### Implementation Phases

| Phase | Scope | Prerequisite |
|-------|-------|-------------|
| Phase 1 | Service account design + FPolicy filter configuration | FSx for ONTAP running |
| Phase 2 | Agent OTel span definition + ADOT delivery | Agent infrastructure built |
| Phase 3 | Correlation logic implementation (Lambda / Step Functions) | Phase 1 + 2 complete |
| Phase 4 | Dashboard + alerts + anomaly detection | Phase 3 complete |

### Data Flow Options

| Pattern | Correlation Timing | Latency | Applicable Scenario |
|---------|-------------------|---------|-------------------|
| Batch correlation (Athena) | Periodic (5min – 1hr) | Minutes to hours | Daily reports, compliance |
| Stream correlation (Kinesis + Lambda) | Real-time | Seconds | Security alerts, anomaly detection |
| Hybrid | Real-time detection + batch aggregation | Seconds (detection) / minutes (aggregation) | Production recommended |

---

## Security Considerations

| Concern | Design Approach |
|---------|----------------|
| Correlation record access control | Viewable only by audit personnel; agents themselves cannot access correlation results |
| Log tamper protection | S3 Object Lock (WORM) + CloudTrail Integrity |
| PII redaction | Mask user names in file paths when displaying on dashboards |
| Service account least privilege | Allow only minimum required volumes/paths per agent |
| Correlation record retention | Set according to compliance requirements (e.g., 7 years) |

---

### Databricks Platform Security Updates (as of 2026-06)

> **Evidence tier**: Public ([DAIS 2026 announcement blog](https://www.databricks.com/blog/whats-new-databricks-platform-security-and-compliance-data-ai-summit-2026))
> **Status**: Unverified — assessing design impact

The following Databricks Platform Security features announced at DAIS 2026 (2026-06-17) may affect this pattern's agent audit design.

| Feature | Summary | Impact on This Pattern |
|---------|---------|----------------------|
| Private Network Gateway / Lakebase Private Link | Private connectivity from serverless/AI workloads to Lakebase | If agents access Lakebase, verify that Private Link traffic is visible in CloudTrail / VPC Flow Logs. May require adding VPC Flow Logs as a data source for correlation logic |
| Automatic Identity Management (AIM) for Entra ID — GA (AWS/GCP) | Automated user/group sync between Entra ID and Databricks | Agent service account group memberships may be automatically synced to the Databricks workspace. Verify compatibility with the AD group design and FPolicy filters defined in this pattern's "Service Account Strategy" section |
| Context-Based Ingress Policies | Access control based on context (device, network, identity attributes) | If agent execution environment context is evaluated by Ingress Policies, denial reasons may be recorded in audit logs. Consider including Databricks-side access denial events in correlation results |

**Region constraint**:

> ⚠️ Lakebase is **not available in ap-northeast-1 (Tokyo)** as of 2026-06-18. Since this project's FSx for ONTAP environment is deployed in the Tokyo region, Lakebase Private Link validation will be deferred until the service becomes available in this region. Tokyo region GA timeline is unknown.

**Open items to verify**:

- Once Lakebase becomes available in ap-northeast-1, how agent access via Private Link is recorded in Databricks Unity Catalog audit logs (mapping to the `service_account` field)
- When group memberships are auto-synced by AIM, verification methods to confirm that ONTAP SVM AD group resolution results for agent service principals align with the Databricks side
- Naming patterns for service principals auto-created/synced by AIM and compatibility with this pattern's FPolicy filter configuration (`%svc-agent-%`)
- Methods to incorporate Context-Based Ingress Policy denial events into OTel spans or Correlation Records

> **Note**: The above are considerations based on publicly available DAIS 2026 information and do not change this pattern's existing design (OTel × FPolicy correlation). Correlation logic extensions will be evaluated when Lakebase becomes available in the Tokyo region and integration becomes concrete.

---

## Related Documents

- [FPolicy Server Design](../../shared/templates/fpolicy-server-fargate.yaml)
- [Pipeline SLO](pipeline-slo.md)
- [Data Classification Guide](data-classification.md)
- [Security Best Practices](security-best-practices.md)
- [FSx-for-ONTAP-Lakehouse-Integrations: Cross-Repo Integration Strategy](https://github.com/Yoshiki0705/FSx-for-ONTAP-Lakehouse-Integrations/blob/main/docs/en/cross-repo-integration-strategy.md)
- [FSx-for-ONTAP-Lakehouse-Integrations: Omnigent Evaluation (Observability Design Section)](https://github.com/Yoshiki0705/FSx-for-ONTAP-Lakehouse-Integrations/blob/main/docs/en/omnigent-multi-agent-evaluation.md)
