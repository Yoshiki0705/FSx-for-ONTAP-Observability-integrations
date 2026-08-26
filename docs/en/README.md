# FSx for ONTAP Observability Integrations

[![CI](https://github.com/Yoshiki0705/fsxn-observability-integrations/actions/workflows/ci.yaml/badge.svg)](https://github.com/Yoshiki0705/fsxn-observability-integrations/actions/workflows/ci.yaml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Yoshiki0705/fsxn-observability-integrations/badge)](https://scorecard.dev/viewer/?uri=github.com/Yoshiki0705/fsxn-observability-integrations)

🌐 [日本語](../ja/README.md) | **English**

> Ship Amazon FSx for NetApp ONTAP audit logs to 9 observability vendors — plus EMS events and FPolicy file operations on 9 of them (3 of those paths E2E verified so far) — EC2-free, serverless, via FSx for ONTAP S3 Access Points. Community reference implementation for AWS + storage operations teams. See the [telemetry path coverage](#telemetry-path-coverage) matrix for the per-vendor breakdown.

## Get Started

| I want to... | Guide | Time |
|---|---|---|
| Validate the pipeline end-to-end (first time) | [Minimum Test Path](quick-start-minimum.md) | 15 min |
| Deploy a vendor integration to production | [Deployment Guide](deployment-guide.md) | 30 min |
| Respond to ransomware at the storage layer | [Automated Incident Response](automated-response-guide.md) | 20 min |
| Route logs to multiple backends with redaction | [OTel Collector](../../integrations/otel-collector/) | 45 min |
| Manage FSx for ONTAP via browser GUI | [Management Console](../../management-console/) · [Decision Tree](decision-tree-management-monitoring.md) | 30 min |
| Run a partner PoC with success criteria | [PoC Success Criteria](poc-success-criteria.md) · [Solution Brief](partner-solution-brief.md) | — |

> **One-command setup** per vendor: `bash integrations/<vendor>/scripts/setup-full-observability.sh`

## Architecture

```
               ┌─────────────────────────────────────────────────┐
               │              FSx for ONTAP                      │
               │  audit volume ──► S3 Access Point (S3 API)      │
               └────────┬──────────────┬──────────────┬──────────┘
                        │              │              │
            Audit Logs (poll)    EMS (webhook)   FPolicy (TCP)
                        │              │              │
                        ▼              ▼              ▼
              EventBridge       API Gateway      ECS Fargate
              Scheduler              │           → SQS
                   │                 │              │
                   ▼                 ▼              ▼
               Lambda ──────────► Vendor API / OTel Collector
```

**Trigger model**: FSx for ONTAP S3 Access Points do not support S3 Event Notifications. This project uses EventBridge Scheduler polling with SSM checkpoint. See [Architecture](architecture.md) for details.

<details><summary>📂 Supported Integrations (14 vendors)</summary>

| Vendor | Status | Path |
|--------|--------|------|
| [Datadog](../../integrations/datadog/) | ✅ E2E verified | Logs API v2 via Lambda |
| [New Relic](../../integrations/new-relic/) | ✅ E2E verified | Log API v1 via Lambda |
| [Splunk (Serverless)](../../integrations/splunk-serverless/) | ✅ E2E verified | HEC via Lambda |
| [OTel Collector](../../integrations/otel-collector/) | ✅ E2E verified | Vendor-neutral OTLP/HTTP (multi-backend) |
| [Grafana Cloud](../../integrations/grafana/) | ✅ E2E verified | OTLP Gateway (Loki fallback) |
| [Elastic](../../integrations/elastic/) | ✅ E2E verified | Bulk API |
| [Dynatrace](../../integrations/dynatrace/) | ✅ E2E verified | Log Ingest API v2 |
| [Sumo Logic](../../integrations/sumo-logic/) | ✅ E2E verified | HTTP Source |
| [Honeycomb](../../integrations/honeycomb/) | ✅ E2E verified | Events Batch API |
| [CrowdStrike Falcon LogScale](../../integrations/crowdstrike/) | ✅ HEC verified | Splunk HEC compatible |
| [NetApp Console<!-- allow:naming -->](../../integrations/netapp-console/) | ✅ Verified | GUI management (SaaS) |
| [Self-hosted Management Console](../../management-console/) | ✅ Validated | AWS-native GUI (Cognito/IAM) |
| [Automated Incident Response](automated-response-guide.md) | ✅ E2E verified | Storage-layer block/snapshot |
| [Mackerel](../../integrations/mackerel/) | ✅ E2E verified (open beta) | OTLP/HTTP logs |

### Telemetry path coverage

FSx for ONTAP emits three kinds of telemetry and each needs its own handler.
Audit logs are covered and verified everywhere. EMS and FPolicy handlers ship for
nine vendors, but only three of those paths have been observed end-to-end against
a live vendor account — so this matrix separates "implemented" from "verified"
rather than marking both with the same tick.

| Vendor | Audit logs | EMS events | FPolicy file ops |
|--------|:----------:|:----------:|:----------------:|
| [Datadog](../../integrations/datadog/) | ✅ | ✅ | ✅ |
| [OTel Collector](../../integrations/otel-collector/) | ✅ | ✅ | ✅ |
| [Grafana Cloud](../../integrations/grafana/) | ✅ | ✅ | ✅ |
| [Splunk (Serverless)](../../integrations/splunk-serverless/) | ✅ | 🔧 | 🔧 |
| [New Relic](../../integrations/new-relic/) | ✅ | 🔧 | 🔧 |
| [Elastic](../../integrations/elastic/) | ✅ | 🔧 | 🔧 |
| [Dynatrace](../../integrations/dynatrace/) | ✅ | 🔧 | 🔧 |
| [Sumo Logic](../../integrations/sumo-logic/) | ✅ | 🔧 | 🔧 |
| [Honeycomb](../../integrations/honeycomb/) | ✅ | 🔧 | 🔧 |
| [CrowdStrike Falcon LogScale](../../integrations/crowdstrike/) | ✅ | — | — |

| Mark | Meaning |
|:----:|---------|
| ✅ | **E2E verified.** Telemetry was observed arriving in the vendor UI, with a screenshot or a filled-in record under `verification-results-*.md` in this directory. |
| 🔧 | **Implemented, not yet E2E verified.** The handler and its CloudFormation stack ship and unit tests pass, but no run against a live vendor account has been recorded for that path. Treat it as untested, not as broken. |
| — | **Not implemented.** No handler exists for that path. |

Evidence behind the ✅ marks in the EMS and FPolicy columns: Datadog (verification
record, steps E1–E4 against a live ONTAP file system), Grafana Cloud (screenshot
evidence, indexed in its [verification record](verification-results-grafana.md)),
OTel Collector (verification record — note its EMS step was exercised with a sample
OTLP payload against a local collector rather than a live ONTAP webhook).

Where a path is marked 🔧, the vendor's own record says so explicitly rather than
staying silent — see for example the
[Splunk record](verification-results-splunk.md), which separates the verified
audit path from the unrecorded EMS, FPolicy and Firehose paths.

For a `—` path, `scripts/deploy.sh` skips the corresponding stack and prints why,
rather than deploying a placeholder Lambda that would accept events and discard
every one of them.

CrowdStrike has no `template-ems.yaml` or `template-fpolicy.yaml` at all, so
there is nothing to skip. To route EMS or FPolicy events there today, use the
[OTel Collector](../../integrations/otel-collector/) integration as the ingestion
point and configure LogScale as an OTLP exporter backend.

The nine vendors that ship EMS and FPolicy handlers share one implementation of the plumbing:
`shared/python/ems_event.py` handles API Gateway extraction and parser
delegation, `shared/python/fpolicy_event.py` handles SQS batch bookkeeping and
`batchItemFailures`, and `shared/python/vendor_shipper.py` owns the retry policy,
credential caching and batching. Each vendor supplies only its payload format and
endpoint.

</details>

<details><summary>⚠️ Constraints & Caveats</summary>

| Constraint | Impact | Workaround |
|---|---|---|
| S3 AP does not support Event Notifications | No push-based trigger | EventBridge Scheduler polling |
| S3 AP does not support presigned URLs | Cannot share direct links | Copy to standard S3 bucket |
| AD-joined SVM requires AD DC reachability for S3 AP data ops | `AccessDenied` if AD is down | Pre-flight AD connectivity check |
| VPC Lambda + Gateway Endpoint may timeout on Internet-origin AP | Deploy fails silently | Use VPC-external Lambda or NAT |
| PutObject limit 5 GB on S3 AP | Large file writes rejected | Multipart within 5 GB |

Full details: [S3 AP Specification](s3ap-fsxn-specification.md) · [Deployment Guide — VPC Endpoint Matrix](deployment-guide.md)

</details>

<details><summary>📚 Documentation & Related Resources</summary>

### Documentation

| Category | Key Documents |
|----------|--------------|
| Getting Started | [Prerequisites](prerequisites.md) · [Deploying a Vendor Integration](vendor-deployment-common.md) · [Deployment Guide](deployment-guide.md) · [ONTAP Audit Setup](ontap-audit-setup.md) |
| Architecture | [Architecture](architecture.md) · [Event Sources](event-sources.md) · [S3 AP Spec](s3ap-fsxn-specification.md) |
| Operations | [Pipeline SLO](pipeline-slo.md) · [Operational Guide](operational-guide.md) · [Runbooks](runbooks/) |
| Security | [Cyber Resilience Map](cyber-resilience-capability-map.md) · [Automated Response](automated-response-guide.md) · [Data Classification](data-classification.md) |
| Enterprise | [Multi-Account](multi-account-deployment.md) · [Cross-Region DR](cross-region-replication.md) · [PII Redaction](../../integrations/otel-collector/docs/en/pii-redaction-cookbook.md) |
| Monitoring | [CloudWatch Log Alarm](cloudwatch-log-alarm.md) · [EMS Detection](ems-detection-capabilities.md) · [Detection Use Cases](detection-use-cases.md) |

<!-- docs-index:start -->

### All documents

Every document in this directory, by category. Generated by `shared/scripts/generate-docs-index.py` from a single category map, so the
English and Japanese indexes always list the same set.

**Getting Started**

- [Getting Started](getting-started.md)
- [Prerequisites and Resource Deployment Guide](prerequisites.md)
- [Minimum Test Path](quick-start-minimum.md)
- [Deploying a vendor integration](vendor-deployment-common.md)
- [Deployment Guide — Integrating with Existing FSx for ONTAP Environments](deployment-guide.md)
- [ONTAP Audit Setup Guide](ontap-audit-setup.md)

**Architecture & Reference**

- [Architecture](architecture.md)
- [Architecture Evolution: Admin Audit Log Delivery via CloudWatch Logs Syslog VPCE](architecture-evolution-syslog-vpce.md)
- [Event Sources Guide](event-sources.md)
- [Normalized Event Schema](normalized-event-schema.md)
- [FSx for ONTAP S3 Access Points Specification](s3ap-fsxn-specification.md)
- [S3 Access Points for FSx for ONTAP — Knowledge Base](s3-access-points-knowledge.md)
- [ONTAP REST API Quick Reference for FSx for ONTAP](ontap-rest-api-reference.md)

**Operations**

- [Operational Guide](operational-guide.md)
- [Pipeline SLO Definitions](pipeline-slo.md)
- [Delivery Guarantee Patterns](delivery-guarantees.md)
- [Retention Policy Matrix](retention-policy-matrix.md)
- [PagerDuty Escalation Integration Guide](pagerduty-escalation-guide.md)
- [Syslog VPC Endpoint Setup Guide — FSx for ONTAP Admin Audit Logs → CloudWatch Logs](syslog-vpce-setup-guide.md)
- [CloudWatch Log Alarm — Direct Alarms from FSx for ONTAP Audit Logs](cloudwatch-log-alarm.md)

**Runbooks**

- [Runbook: DLQ Replay](runbooks/dlq-replay.md)
- [Runbook: Lambda Errors Alarm](runbooks/lambda-errors.md)
- [Runbook: Checkpoint Staleness](runbooks/checkpoint-stale.md)
- [Runbook: CloudWatch Log Alarm Triggered](runbooks/log-alarm-triggered.md)

**Security & Detection**

- [Security Best Practices for FSx for ONTAP Observability Integrations](security-best-practices.md)
- [Security Review Checklist](security-review-checklist.md)
- [Security Monitoring & Incident Response — Document Navigation Index](security-monitoring-index.md)
- [Detection Use Cases](detection-use-cases.md)
- [EMS Event Detection Capabilities — Reference Guide](ems-detection-capabilities.md)
- [Cyber Resilience Capability Map — NIST CSF 2.0 Function Mapping](cyber-resilience-capability-map.md)
- [EMS Webhook Security Guide](webhook-security.md)

**Automated Response**

- [Automated Incident Response Guide — User/IP Blocking via ONTAP REST API](automated-response-guide.md)
- [Automated Response — Security & Incident Response Addendum](automated-response-security-addendum.md)
- [ARP (Autonomous Ransomware Protection) Incident Response Guide](arp-incident-response-guide.md)
- [Verified-Clean Recovery Point Guide — Closing the CSF 2.0 RC.RP Gap](verified-recovery-point-guide.md)
- [Content-Level PII Classification Scanner — Closing the CSF 2.0 Identify Gap](content-classification-scanner.md)

**FPolicy**

- [FPolicy Pipeline — Quick Deploy Guide](fpolicy-quick-deploy.md)
- [FPolicy Pipeline Operational Guide](fpolicy-operational-guide.md)
- [FPolicy Production Architecture Patterns](fpolicy-production-architecture-patterns.md)
- [FPolicy PoC Checklist](fpolicy-poc-checklist.md)
- [FPolicy Operational Notes](operational-notes-fpolicy.md)
- [AI Agent Access Log × ONTAP FPolicy Audit Log Correlation Pattern](agent-fpolicy-correlation-pattern.md)

**Governance & Compliance**

- [Governance and Compliance Considerations](governance-and-compliance.md)
- [Compliance Evidence Pack Template](compliance-evidence-pack.md)
- [Data Classification Guide for FSx for ONTAP Audit Logs](data-classification.md)
- [Data Residency Matrix](data-residency.md)

**Enterprise & Scale**

- [Multi-Account Deployment with AWS Organizations](multi-account-deployment.md)
- [Cross-Region Replication for Audit Log DR](cross-region-replication.md)
- [Lakehouse Long-Term Retention for FSx for ONTAP Audit Logs](lakehouse-long-term-retention.md)
- [Lakehouse Monitoring Patterns](lakehouse-monitoring-patterns.md)

**Choosing an Approach**

- [FSx for ONTAP Management & Monitoring Decision Tree](decision-tree-management-monitoring.md)
- [AWS-Native Alternative Matrix — System Manager / Workload Factory / DII](native-alternative-matrix.md)
- [Vendor Comparison](vendor-comparison.md)
- [Comparison: EC2-Based Pattern vs Serverless Pattern](ec2-comparison.md)
- [Existing Audit Tool Coexistence Guide](existing-audit-tool-coexistence.md)
- [File Access Audit Log — Format Comparison & Architecture Options](file-access-audit-format-comparison.md)
- [ONTAP System Manager GUI Operations Guide](system-manager-gui-guide.md)
- [Observability Integration Addendum — Advanced Patterns & Reference](observability-integration-addendum.md)

**Cost**

- [Cost Model — Direct Send vs Collector vs Firehose](cost-model.md)
- [Cost Validation: Estimated vs Actual](cost-validation.md)
- [S3 Access Point Read Throughput Benchmark](s3ap-throughput-benchmark.md)

**Partner & Workshop**

- [Partner Solution Brief: FSx for ONTAP Serverless Observability](partner-solution-brief.md)
- [Partner FAQ: FSx for ONTAP Observability Integrations](partner-faq.md)
- [PoC Success Criteria](poc-success-criteria.md)
- [PoC Proposal Template: FSx for ONTAP Observability Integration](poc-proposal-template.md)
- [Workshop Agenda: FSx for ONTAP Serverless Observability](workshop-agenda.md)
- [Workshop Hands-On Guide (Half-Day, 3.5 Hours)](workshop-hands-on-half-day.md)

**Demos & Screenshots**

- [Demo Scenarios](demo-scenarios.md)
- [Automated Response Demo Runbook](demo-automated-response.md)
- [ARP Incident Response Demo Runbook](demo-arp-incident-response.md)
- [Content Classification Scanner Demo Runbook](demo-content-classification.md)
- [EMS/FPolicy Screenshot Capture Guide](screenshot-capture-guide-ems-fpolicy.md)

**Verification Results**

- [Datadog Integration Verification Results](verification-results-datadog.md)
- [Splunk Serverless Integration Verification Results](verification-results-splunk.md)
- [OTel Collector Integration E2E Verification Results](verification-results-otel-collector.md)
- [Grafana Cloud Integration Verification Results](verification-results-grafana.md)
- [New Relic Integration Verification Results](verification-results-new-relic.md)
- [Elastic Integration Verification Results](verification-results-elastic.md)
- [Dynatrace Integration Verification Results](verification-results-dynatrace.md)
- [Sumo Logic Integration Verification Results](verification-results-sumo-logic.md)
- [Honeycomb Integration Verification Results](verification-results-honeycomb.md)
- [EMS/FPolicy E2E Verification Results](verification-results-ems-fpolicy.md)
- [Create an event per candidate, deleting the ones that succeed to leave the cluster as found](verification-results-fpolicy-s3ap-and-session.md)
- [support-inquiry-s3ap-audit-coverage](support-inquiry-s3ap-audit-coverage.md)
- [s3ap-monitoring-coverage-implications](s3ap-monitoring-coverage-implications.md)

**Project**

- [CI Policy and Quality Gates](ci-policy.md)

<!-- docs-index:end -->
### Related Repositories

| Repository | Description |
|-----------|-------------|
| [FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns) | 17 industry use cases with FPolicy pipeline |
| [fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations) | Data Lake / Lakehouse integrations via S3 AP |
| [FSx-for-ONTAP-Agentic-Access-Aware-RAG](https://github.com/Yoshiki0705/FSx-for-ONTAP-Agentic-Access-Aware-RAG) | Access-aware Agentic RAG with Bedrock |

### Articles

- [AWS Blog: Auditing FSx for ONTAP using Splunk](https://aws.amazon.com/blogs/storage/auditing-user-and-administrative-actions-on-amazon-fsx-for-netapp-ontap-using-splunk/) (EC2 approach — this project provides the EC2-free alternative)

</details>

<details><summary>🔧 For Developers</summary>

```bash
npm install                  # Install dependencies
npm test                     # TypeScript tests
python -m pytest integrations/*/tests/ shared/lambda-layers/ems-parser/tests/ -v  # All Python tests
cfn-lint integrations/*/template.yaml   # Validate CloudFormation
```

- **Tech stack**: CloudFormation (YAML) · Python 3.12 Lambda · TypeScript · GitHub Actions CI
- **Contributing**: See [CONTRIBUTING.md](../../CONTRIBUTING.md)
- **Changelog**: See [CHANGELOG.md](../../CHANGELOG.md)
- **Roadmap**: See [ROADMAP.md](../../ROADMAP.md)

</details>

## License

MIT

---

🌐 [日本語](../ja/README.md) | **English**
