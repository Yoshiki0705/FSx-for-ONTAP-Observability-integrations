# FSx for ONTAP Observability Integrations

[![CI](https://github.com/Yoshiki0705/fsxn-observability-integrations/actions/workflows/ci.yaml/badge.svg)](https://github.com/Yoshiki0705/fsxn-observability-integrations/actions/workflows/ci.yaml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Yoshiki0705/fsxn-observability-integrations/badge)](https://scorecard.dev/viewer/?uri=github.com/Yoshiki0705/fsxn-observability-integrations)

🌐 [日本語](docs/ja/README.md) | **English**

> Ship Amazon FSx for NetApp ONTAP audit logs to 9 observability vendors — plus EMS events and FPolicy file operations on 9 of them — EC2-free, serverless, via FSx for ONTAP S3 Access Points. Community reference implementation for AWS + storage operations teams. See the [telemetry path coverage](#telemetry-path-coverage) matrix for the per-vendor breakdown.

## Get Started

| I want to... | Guide | Time |
|---|---|---|
| Validate the pipeline end-to-end (first time) | [Minimum Test Path](docs/en/quick-start-minimum.md) | 15 min |
| Deploy a vendor integration to production | [Deployment Guide](docs/en/deployment-guide.md) | 30 min |
| Respond to ransomware at the storage layer | [Automated Incident Response](docs/en/automated-response-guide.md) | 20 min |
| Route logs to multiple backends with redaction | [OTel Collector](integrations/otel-collector/) | 45 min |
| Manage FSx for ONTAP via browser GUI | [Management Console](management-console/) · [Decision Tree](docs/en/decision-tree-management-monitoring.md) | 30 min |
| Run a partner PoC with success criteria | [PoC Success Criteria](docs/en/poc-success-criteria.md) · [Solution Brief](docs/en/partner-solution-brief.md) | — |

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

**Trigger model**: FSx for ONTAP S3 Access Points do not support S3 Event Notifications. This project uses EventBridge Scheduler polling with SSM checkpoint. See [Architecture](docs/en/architecture.md) for details.

<details><summary>📂 Supported Integrations (14 vendors)</summary>

| Vendor | Status | Path |
|--------|--------|------|
| [Datadog](integrations/datadog/) | ✅ E2E verified | Logs API v2 via Lambda |
| [New Relic](integrations/new-relic/) | ✅ E2E verified | Log API v1 via Lambda |
| [Splunk (Serverless)](integrations/splunk-serverless/) | ✅ E2E verified | HEC via Lambda |
| [OTel Collector](integrations/otel-collector/) | ✅ E2E verified | Vendor-neutral OTLP/HTTP (multi-backend) |
| [Grafana Cloud](integrations/grafana/) | ✅ E2E verified | OTLP Gateway (Loki fallback) |
| [Elastic](integrations/elastic/) | ✅ E2E verified | Bulk API |
| [Dynatrace](integrations/dynatrace/) | ✅ E2E verified | Log Ingest API v2 |
| [Sumo Logic](integrations/sumo-logic/) | ✅ E2E verified | HTTP Source |
| [Honeycomb](integrations/honeycomb/) | ✅ E2E verified | Events Batch API |
| [CrowdStrike Falcon LogScale](integrations/crowdstrike/) | ✅ HEC verified | Splunk HEC compatible |
| [NetApp Console<!-- allow:naming -->](integrations/netapp-console/) | ✅ Verified | GUI management (SaaS) |
| [Self-hosted Management Console](management-console/) | ✅ Validated | AWS-native GUI (Cognito/IAM) |
| [Automated Incident Response](docs/en/automated-response-guide.md) | ✅ E2E verified | Storage-layer block/snapshot |
| [Mackerel](integrations/mackerel/) | ✅ E2E verified (open beta) | OTLP/HTTP logs |

### Telemetry path coverage

FSx for ONTAP emits three kinds of telemetry and each needs its own handler.
Audit logs are covered everywhere; EMS and FPolicy are implemented on a subset.

| Vendor | Audit logs | EMS events | FPolicy file ops |
|--------|:----------:|:----------:|:----------------:|
| [Datadog](integrations/datadog/) | ✅ | ✅ | ✅ |
| [Splunk (Serverless)](integrations/splunk-serverless/) | ✅ | ✅ | ✅ |
| [OTel Collector](integrations/otel-collector/) | ✅ | ✅ | ✅ |
| [Grafana Cloud](integrations/grafana/) | ✅ | ✅ | ✅ |
| [New Relic](integrations/new-relic/) | ✅ | ✅ | ✅ |
| [Elastic](integrations/elastic/) | ✅ | ✅ | ✅ |
| [Dynatrace](integrations/dynatrace/) | ✅ | ✅ | ✅ |
| [Sumo Logic](integrations/sumo-logic/) | ✅ | ✅ | ✅ |
| [Honeycomb](integrations/honeycomb/) | ✅ | ✅ | ✅ |
| [CrowdStrike Falcon LogScale](integrations/crowdstrike/) | ✅ | — | — |

`—` means no handler exists for that path yet. `scripts/deploy.sh` skips the
corresponding stack and prints why, rather than deploying a placeholder Lambda
that would accept events and discard every one of them.

CrowdStrike has no `template-ems.yaml` or `template-fpolicy.yaml` at all, so
there is nothing to skip. To route EMS or FPolicy events there today, use the
[OTel Collector](integrations/otel-collector/) integration as the ingestion point
and configure LogScale as an OTLP exporter backend.

The nine vendors with full coverage share one implementation of the plumbing:
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

Full details: [S3 AP Specification](docs/en/s3ap-fsxn-specification.md) · [Deployment Guide — VPC Endpoint Matrix](docs/en/deployment-guide.md)

</details>

<details><summary>📚 Documentation & Related Resources</summary>

### Documentation

Full docs available in [English](docs/en/README.md) and [日本語](docs/ja/README.md).

| Category | Key Documents |
|----------|--------------|
| Getting Started | [Prerequisites](docs/en/prerequisites.md) · [Deploying a Vendor Integration](docs/en/vendor-deployment-common.md) · [Deployment Guide](docs/en/deployment-guide.md) · [ONTAP Audit Setup](docs/en/ontap-audit-setup.md) |
| Architecture | [Architecture](docs/en/architecture.md) · [Event Sources](docs/en/event-sources.md) · [S3 AP Spec](docs/en/s3ap-fsxn-specification.md) |
| Operations | [Pipeline SLO](docs/en/pipeline-slo.md) · [Operational Guide](docs/en/operational-guide.md) · [Runbooks](docs/en/runbooks/) |
| Security | [Cyber Resilience Map](docs/en/cyber-resilience-capability-map.md) · [Automated Response](docs/en/automated-response-guide.md) · [Data Classification](docs/en/data-classification.md) |
| Enterprise | [Multi-Account](docs/en/multi-account-deployment.md) · [Cross-Region DR](docs/en/cross-region-replication.md) · [PII Redaction](integrations/otel-collector/docs/en/pii-redaction-cookbook.md) |
| Monitoring | [CloudWatch Log Alarm](docs/en/cloudwatch-log-alarm.md) · [EMS Detection](docs/en/ems-detection-capabilities.md) · [Detection Use Cases](docs/en/detection-use-cases.md) |

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
- **Contributing**: See [CONTRIBUTING.md](CONTRIBUTING.md)
- **Changelog**: See [CHANGELOG.md](CHANGELOG.md)
- **Roadmap**: See [ROADMAP.md](ROADMAP.md)

</details>

## License

MIT

---

🌐 [日本語](docs/ja/README.md) | **English**
