# Roadmap

## Current State (June 2026)

All 9 vendor integrations are E2E verified. Datadog paid plan verification complete (Pipeline, Monitors, Dashboard, Saved Views). CrowdStrike Falcon LogScale blog article (Part 15) ready for publication. Existing audit tool coexistence guide published.

## Phase 1: Foundation (Completed)

- [x] 9 vendor integrations (Datadog, New Relic, Splunk, OTel Collector, Grafana, Elastic, Dynatrace, Sumo Logic, Honeycomb)
- [x] NetApp Console / System Manager GUI management (FSA, audit, quotas verified)
- [x] 3 event sources (Audit logs, EMS webhooks, FPolicy)
- [x] CloudFormation templates for all integrations
- [x] Bilingual documentation (ja/en)
- [x] CI/CD pipeline (lint, test, cfn-lint, security scan)
- [x] dev.to blog series (Parts 1-12)
- [x] Partner assets (Solution Brief, PoC Proposal, Workshop Agenda)

## Phase 2: Production Hardening (Completed)

- [x] Pipeline SLO definitions with Go/No-Go criteria
- [x] Data Classification Guide (PII field mapping + handling patterns)
- [x] Operational Runbooks (DLQ replay, Lambda errors, checkpoint staleness)
- [x] Workshop Hands-On Guide (half-day)
- [x] Full CI coverage (all 9 vendors + shared layers + coverage report)
- [x] S3 AP read throughput benchmark (methodology + reference results)
- [x] Retention policy matrix (regulation-to-vendor mapping)
- [x] Secrets Manager auto-rotation sample
- [x] cfn-guard rules refinement (critical rules blocking)
- [x] Japanese documentation sync verification script
- [x] Cost validation template (estimated vs actual)
- [x] Datadog paid plan E2E verification (Pipeline, Monitors, Dashboard, Saved Views)
- [x] Existing audit tool coexistence guide (ONTAP format constraint documented)
- [ ] Cost validation data (requires 1 month of production billing)

## Phase 3: Enterprise Features (Completed)

- [x] Multi-account deployment pattern (AWS Organizations + StackSets)
- [x] Cross-region replication for audit log DR
- [x] DynamoDB object ledger (per-object processing state)
- [x] SQS buffering pattern (backpressure handling)
- [x] Poison-pill auto-skip with alerting
- [x] OTel Collector PII redaction cookbook (per-regulation)
- [x] Compliance evidence pack template (ISMAP, FISC, SOC2)
- [x] Automated incident response module (ONTAP REST API user/IP blocking, snapshot, session disconnect)
- [ ] Cost model validation (estimated vs actual billing comparison)

## Phase 4: Community & Ecosystem (In Progress)

Target: 2027 H1

- [ ] AWS Solutions Library submission
- [ ] SAM (Serverless Application Model) packaging
- [ ] AWS Marketplace listing (partner-delivered)
- [ ] Terraform module equivalents
- [ ] CDK construct library
- [x] Community contribution guidelines (CONTRIBUTING.md)
- [x] [GitHub Discussions](https://github.com/Yoshiki0705/fsxn-observability-integrations/discussions) for Q&A
- [ ] Integration test suite with LocalStack
- [x] CrowdStrike Falcon LogScale integration (handler + template + tests + docs)
- [x] Parser v1.1.0 (FIELD_MAPPING, Strategy pattern, defusedxml, 178K events/sec)
- [x] Blog Part 15: CrowdStrike Falcon LogScale via HEC (ready for publication)

## Blog Series Plan

| Part | Title | Status |
|------|-------|--------|
| 1-6 | Foundation series | Published |
| 7 | OTel Collector Multi-Backend | Draft |
| 8 | Grafana Cloud OTLP Gateway | Draft |
| 9 | Data Sovereignty with Elastic | Draft |
| 10 | High-Cardinality Analysis with Honeycomb | Draft |
| 11 | AI-Powered Root Cause with Dynatrace | Draft |
| 12 | JP Region with Sumo Logic | Draft |
| 13 | 9 Vendors, One Architecture: Lessons Learned | Draft |
| 14 | System Manager Reality Check: GUI vs CLI vs Observability | Draft |
| 15 | CrowdStrike Falcon LogScale via HEC — Parser v1.1.0 | Ready for publication |
| 16 | Datadog Paid Plan: Pipeline, Monitors, and Detection Workflows | Planned |
| 17 | CloudWatch Log Alarms: No Metric Filter Required | Published |
| 18 | Automated Incident Response: Block Users/IPs via ONTAP REST API | Ready for publication |

## Contributing

This project welcomes contributions. Priority areas:
- Additional vendor integrations (Axiom, Mezmo, Coralogix)
- Terraform equivalents of CloudFormation templates
- Localization (Korean, Chinese)
- Benchmark data from different FSx for ONTAP configurations

See the repository issues for specific tasks.
