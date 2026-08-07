# Vendor Comparison

🌐 [日本語](../ja/vendor-comparison.md) | **English** (this page)

## Supported Vendors

| Vendor | Delivery Method | Auth Method | Max Batch Size | Firehose Support |
|--------|----------------|-------------|----------------|-----------------|
| Datadog | Logs API v2 | API Key (Header) | 5MB/request | ✅ |
| New Relic | Log API | License Key (Header) | 1MB/request | ✅ |
| Grafana Cloud | OTLP Gateway | Basic Auth | No limit (4MB recommended) | ❌ |
| Splunk | HEC | HEC Token (Header) | No limit | ✅ (Built-in) |
| CrowdStrike Falcon LogScale | HEC (Splunk-compatible) | Bearer Token (Ingest Token) | No limit | ❌ |
| Elastic | Bulk API | API Key / Basic Auth | No limit (10MB recommended) | ❌ |
| Dynatrace | Log Ingest API | API Token (Header) | 1MB/request | ✅ |
| Sumo Logic | HTTP Source | Embedded in URL | 1MB/request | ❌ |
| Honeycomb | Events API | API Key (Header) | 5MB/request | ❌ |
| OTel Collector | OTLP/HTTP | Configurable | Configurable | ❌ |

## Cost Comparison

Estimated monthly costs for the **observability platform ingestion** (excludes AWS infrastructure costs which are ~$5-50/month depending on volume):

| Vendor | Free Tier | 1 GB/month | 10 GB/month | 100 GB/month | Pricing Model |
|--------|-----------|-----------|------------|-------------|---------------|
| New Relic | 100 GB/month | $0 | $0 | $0 | Per-GB beyond free tier ($0.35/GB) |
| Grafana Cloud | 50 GB/month | $0 | $0 | ~$40 | Per-GB beyond free tier ($0.50/GB) |
| Sumo Logic | 1.25 credits/day (~20 credits/month) | $0 | $0 | ~$300 | Credit-based (Flex) |
| Honeycomb | 20M events/month | $0 | $0 | ~$100 | Per-event based |
| Datadog | None (trial only) | ~$10 | ~$100 | ~$1,000 | $0.10/GB ingested + retention |
| Splunk | None (license-based) | License-dependent | License-dependent | License-dependent | Daily indexing volume license |
| Dynatrace | None (DDU-based) | ~$1 DDU/day | ~$10 DDU/day | ~$100 DDU/day | Davis Data Units |
| Elastic Cloud | 14-day trial | ~$30 (min deployment) | ~$95 | ~$300+ | Storage + compute |
| CrowdStrike Falcon LogScale | Community: 1 GB/day | $0 | License-dependent | License-dependent | Per-GB/day or Falcon bundle |
| OTel Collector | N/A (self-hosted) | $0 (infra only) | $0 (infra only) | $0 (infra only) | Backend cost only |

> **Note**: Prices are approximate and may vary by region, contract, and commitment. Always verify with the vendor's current pricing page. AWS infrastructure costs (Lambda, EventBridge, S3, Secrets Manager) are typically $5-50/month for most audit log volumes.

### AWS Infrastructure Cost Estimate

| Component | 1 GB/month | 10 GB/month | 100 GB/month |
|-----------|-----------|------------|-------------|
| Lambda (256MB, 5min interval) | ~$1 | ~$5 | ~$30 |
| EventBridge Scheduler | ~$0.01 | ~$0.01 | ~$0.01 |
| Secrets Manager | ~$0.40 | ~$0.40 | ~$0.40 |
| CloudWatch Logs | ~$0.50 | ~$2 | ~$10 |
| SQS (DLQ) | ~$0 | ~$0 | ~$0 |
| **Total AWS** | **~$2** | **~$8** | **~$41** |

## Selection Guide

### Cost-Focused
- **New Relic**: Largest free tier (100 GB/month, permanent)
- **Grafana Cloud**: Good free tier (50 GB/month) + OSS ecosystem
- **Sumo Logic**: Free tier available (1.25 credits/day (Flex model))
- **Elastic**: Self-hosted option (no ingestion cost)

### Existing Environment Integration
- **Datadog**: Already using Datadog for APM/infrastructure
- **Splunk**: Existing Splunk environment (serverless migration from EC2 UF)
- **Dynatrace**: Want AI-powered root cause analysis with APM correlation

### Vendor Lock-in Avoidance
- **OTel Collector**: Vendor-neutral, switch backends without code changes
- **Grafana Cloud**: OSS-based stack (Loki, Grafana)
- **Honeycomb**: Strong via OTel Collector path

### Enterprise / Compliance
- **Splunk**: Established SIEM, compliance reporting
- **CrowdStrike Falcon LogScale**: Next-Gen SIEM, integrated with Falcon XDR ecosystem
- **Elastic**: Self-hosted for data sovereignty
- **Datadog**: SOC 2, HIPAA, FedRAMP options

## Architecture Pattern Comparison

### Pattern A: Lambda Direct Delivery
```
S3 AP → EventBridge → Lambda → Vendor API
```
- ✅ Simple, low cost (for low-volume logs)
- ❌ Throttling risk with high-volume logs
- ❌ Vendor-specific code per backend

### Pattern B: Via Firehose
```
S3 AP → Lambda (Transform) → Firehose → Vendor API
```
- ✅ Automatic buffering, high throughput
- ✅ Built-in retry and backpressure
- ❌ Firehose-compatible vendors only (Datadog, Splunk, New Relic, Dynatrace)
- ❌ Additional Firehose cost

### Pattern C: Via OTel Collector
```
S3 AP → Lambda (OTLP) → OTel Collector → Multiple Backends
```
- ✅ Vendor-neutral Lambda code (unchanged across backends)
- ✅ Multi-backend fan-out from single pipeline
- ✅ Routing, filtering, redaction in Collector config
- ❌ Requires Collector infrastructure (ECS Fargate recommended)
- ❌ Additional operational complexity

## Emerging / Beta Vendors Under Evaluation

The vendors below are **not yet part of the "Supported Vendors" table above** — no real end-to-end send has been confirmed against a live account for them yet, even where code and config already exist in this repo. They are tracked here because their platform has recently added a relevant capability, and a documentation-first feasibility assessment (at minimum) has been completed.

| Vendor | Delivery Method | Auth Method | Status |
|--------|-----------------|-------------|--------|
| [Mackerel](../../integrations/mackerel/) | OTLP/HTTP only (`https://otlp-vaxila.mackerelio.com`) | `Mackerel-Api-Key` header (Write scope) | ✅ E2E verified (2026-07-18) against a live Mackerel organization — Mackerel's log feature is itself still an **open beta** (opened 2026-07-16; GA planned for fall 2026 with ingest-volume pricing already published), so this listing remains separate from "Supported Vendors" above. |

**Why Mackerel is listed separately, not in the main table**: a real end-to-end send has now been confirmed against a live Mackerel organization (Free plan), so that readiness gate is cleared. What remains is that Mackerel's own log feature needs to reach GA with a stated data-retention SLA before it can be treated on equal footing with the 9 GA-verified vendors above. Until GA, treat Mackerel as **defense-in-depth alongside**, not a replacement for, one of those 9 vendors — particularly for ransomware-detection alerting or other production security use cases where Mackerel's current "no data retention guarantee" beta caveat is unacceptable.

See [integrations/mackerel/README.md](../../integrations/mackerel/README.md) for the full beta-constraint list and implementation roadmap.

## Trial & Verification Notes

### Splunk Cloud Platform

The Splunk Cloud Platform **free trial does NOT provision HEC DNS records** (`http-inputs-<stack>.splunkcloud.com`). This is a [widely reported issue](https://community.splunk.com/t5/Getting-Data-In/HEC-with-Splunk-Cloud-trial/td-p/596680) in the Splunk Community (2020–2025). Port 8088 is also blocked on the trial instance.

**Workaround**: Use Splunk Enterprise (Docker) for local E2E validation at $0 cost. The `splunk/splunk:latest` image includes a fully functional HEC. Our E2E verification was completed with Splunk Enterprise 10.4.0 (Docker, linux/amd64).

### CrowdStrike Falcon LogScale

The CrowdStrike Falcon **EDR trial** includes read-only access to the Next-Gen SIEM UI (log search, repository list) but **does NOT include the Data Connectors / HEC ingest functionality**. The "Add data connector" page returns "Page not found" on the trial. A paid Falcon Next-Gen SIEM license is required for external data ingestion via HEC.

**Protocol verification**: Since Falcon LogScale uses a Splunk HEC-compatible endpoint (`/api/v1/ingest/hec`), the Splunk Enterprise E2E test validates the identical HEC payload format used by this integration.

### Verification Summary

| Vendor | E2E Method | Cost |
|--------|-----------|------|
| Datadog | Cloud trial (14-day) | $0 |
| New Relic | Free tier (100 GB/month) | $0 |
| Grafana Cloud | Free tier (50 GB/month) | $0 |
| Splunk | Docker local (Enterprise trial) | $0 |
| Elastic | Cloud trial (14-day) | $0 |
| Dynatrace | Free tier (15-day trial) | $0 |
| Sumo Logic | Free tier (1.25 credits/day (Flex model)) | $0 |
| Honeycomb | Free tier (20M events/month) | $0 |
| CrowdStrike | HEC protocol verified via Splunk | $0 |
| OTel Collector | Self-hosted (Docker Compose) | $0 |

---

## Dashboard Integration — How Events Appear in Each Vendor

Once events flow into your observability platform, here's how FSx for ONTAP audit logs, FPolicy file operations, and ARP alerts appear in each vendor's dashboard.

### Datadog

**Audit Logs (S3 AP path)**:

![Datadog Logs Explorer showing FSx audit logs](../screenshots/datadog-logs-arrival.png)

Logs arrive with `source:fsxn-ems` and structured attributes. Use facets for filtering by SVM, volume, and operation type.

**FPolicy File Operations**:

![Datadog FPolicy event detail](../screenshots/datadog-fpolicy-detail.png)

FPolicy events include the full file path, operation type (create/write/rename/delete), client IP, and volume name. Create a Datadog Monitor to alert on suspicious patterns (e.g., high file rename rate from a single client).

**ARP Ransomware Detection**:

![Datadog ARP detection alert](../screenshots/datadog-arp-detection.png)

ARP `arw.volume.state` events appear as `severity:alert`. Configure a Monitor with `@sns-<trigger-topic>` in the notification to auto-trigger containment:

```
source:fsxn-ems @attributes.event_name:arw.volume.state @attributes.severity:alert
```

![Datadog ARP log detail](../screenshots/datadog-arp-log-detail.png)

**Dashboard recommendations**:
- Create a "FSx for ONTAP Security" dashboard with:
  - Timeseries: file operations/minute by type
  - Top list: most active client IPs
  - Event timeline: ARP alerts and automated response actions
  - Table: recent unauthorized access attempts

---

### Grafana Cloud (via OTel Collector)

![Grafana Cloud OTel logs](../screenshots/06-grafana-cloud-otel-logs.png)

Logs arrive via the OTLP gateway endpoint with structured labels. Query in Loki:

```logql
{service_name="fsxn-observability"} | json | operation_type="create"
```

**Dashboard recommendations**:
- Use Loki as data source in Grafana dashboards
- Create panels for: event rate, top volumes, client IP distribution
- Alert rules via Grafana Alerting → Contact point → AWS SNS (for auto-containment)

---

### Honeycomb (via OTel Collector)

![Honeycomb OTel logs](../screenshots/07-honeycomb-otel-logs.png)

Events arrive as structured traces/logs with dataset `fsxn-fpolicy` or `fsxn-ems`. Use Honeycomb's query builder to:
- Group by `file_extension` to detect ransomware-like patterns
- Heatmap on `file_size` to detect anomalous encryption activity
- BubbleUp on `client_ip` when ARP triggers

---

### Splunk (HEC)

Events arrive at the `fsxn_audit` or `fsxn_fpolicy` index with sourcetype `fsxn:ontap:audit` / `fsxn:ontap:fpolicy` / `fsxn:ontap:ems`.

**Search queries**:
```spl
index=fsxn_ems sourcetype="fsxn:ontap:ems" message-name="arw.volume.state"
| table _time, parameters.vserver-name, parameters.volume-name, parameters.state

index=fsxn_fpolicy sourcetype="fsxn:ontap:fpolicy" operation_type="create"
| stats count by file_path, client_ip
| sort -count
```

**Dashboard recommendations**:
- Create an "FSx for ONTAP Security Operations" dashboard
- Panel: ARP events timeline (single value + time chart)
- Panel: FPolicy file operations by type (pie chart)
- Panel: Top talkers by client IP (table)
- Alert action: Use "AWS SNS notification" action (Splunk Add-on for AWS) to trigger auto-containment

> **Note**: Splunk Cloud trial does not provision HEC DNS. Use Splunk Enterprise (Docker) for local validation. See [Trial & Verification Notes](#trial--verification-notes).

---

### Elastic (Bulk API)

![Kibana Discover view](../screenshots/elastic/kibana-discover.png)

Events are indexed with the `fsxn-audit-*` or `fsxn-fpolicy-*` index pattern. In Kibana:

```kql
event.dataset: "fsxn.fpolicy" AND operation_type: "create" AND NOT file_path: *~$*
```

**Dashboard recommendations**:
- Kibana Lens: file operations over time, grouped by operation_type
- SIEM Detection Rule: "Ransomware-like file rename burst" (>50 renames in 60s from same client)
- Alert action: SNS connector to trigger auto-containment

---

### Auto-Containment Integration Pattern (All Vendors)

Regardless of which vendor you use, the containment trigger flow is:

```
Vendor Dashboard Alert
  → SNS Publish (TriggerTopicArn from automated-response stack)
    → Lambda (fsxn-automated-response-handler)
      → ONTAP REST API (block user / block IP / snapshot)
        → SNS Notification (result to security team)
```

**Configuration per vendor**:

| Vendor | Alert → SNS Method |
|--------|-------------------|
| Datadog | `@sns-<topic-name>` in Monitor notification |
| Splunk | Alert action → AWS SNS (Splunk Add-on for AWS) |
| Grafana | Alert rule → Contact point → AWS SNS |
| Elastic | Kibana Alert → SNS connector |
| New Relic | Workflow → Destination → AWS SNS |
| Honeycomb | Trigger → Webhook → Lambda → SNS |
| Dynatrace | Problem notification → AWS SNS integration |
| PagerDuty | Event Orchestration → Custom Action → SNS |

---

## Related Documents

- [ONTAP REST API Quick Reference](ontap-rest-api-reference.md)
- [ARP Incident Response Guide](arp-incident-response-guide.md)
- [Automated Response Guide](automated-response-guide.md)
- [EMS Detection Capabilities](ems-detection-capabilities.md)
- [OTel Collector PII Redaction Cookbook](../../integrations/otel-collector/docs/en/pii-redaction-cookbook.md)
