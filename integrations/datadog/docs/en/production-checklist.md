# Datadog Integration — Production Deployment Checklist

Use this checklist before promoting the FSx for ONTAP Datadog integration from PoC to production.

## Pre-Deployment

- [ ] FSx audit configuration confirmed (XML format, rotation schedule, volume location)
- [ ] S3 Access Point created with read-only access for Lambda role
- [ ] S3 Access Point resource policy grants Lambda execution role
- [ ] Datadog API Key stored in Secrets Manager (`logs_write` scope only)
- [ ] Datadog APP Key stored in Secrets Manager (admin scope, CI/CD use only)
- [ ] Datadog site region confirmed (AP1/US1/EU1/US3/US5)
- [ ] Network path validated (VPC-external Lambda or NAT Gateway for S3 AP)
- [ ] Data classification sign-off: audit logs approved for external transmission

## Security and Governance

- [ ] IAM Permissions Boundary applied to Lambda role
- [ ] API Key scope limited to `logs_write` (no admin access)
- [ ] APP Key restricted to Terraform/CI pipeline only (not Lambda runtime)
- [ ] S3 Archive bucket encrypted with SSE-KMS (customer-managed key)
- [ ] Datadog Log Management CMK configured (if regulated environment)
- [ ] Sensitive Data Scanner rules active (Employee ID, Phone, Email, CC, My Number)
- [ ] OTel edge-side PII redaction configured (if boundary governance required)
- [ ] Secrets rotation procedure tested (API Key + APP Key)

## Observability and Alerting

- [ ] Log Pipeline verified (6 processors: Category, Status, Date, Attribute x2, GeoIP)
- [ ] Security Monitors active (4 threshold + 1 anomaly)
- [ ] Cloud SIEM Detection Rules active (4 rules with MITRE mapping)
- [ ] Saved Views created (5 investigation patterns)
- [ ] Facets configured (8 custom facets)
- [ ] Dashboard verified (10 widgets, data flowing)
- [ ] Log-based Metrics created (4 metrics with appropriate cardinality)
- [ ] CloudWatch Alarms: DLQ depth > 0, Lambda errors > 1%, HEC 401/403

## Response Automation

- [ ] Workflow created (`fsxn-security-alert-response`)
- [ ] Monitors linked to Workflow via `@workflow-` mention
- [ ] Case Management project created (FSXN)
- [ ] SOC Triage Runbook accessible (Notebook)
- [ ] Snapshot remediation Lambda deployed with cooldown (15 min)
      — `bash scripts/deploy-snapshot-remediation.sh`
      (stack: `template-snapshot-remediation.yaml`, guide:
      [snapshot-remediation-setup.md](snapshot-remediation-setup.md))
- [ ] Snapshot Lambda TLS configured — set `CA_CERT_PATH` and `CA_CERT_LAYER_ARN`.
      Without them the function connects with `CERT_NONE`, which is acceptable
      for a PoC only.
- [ ] Snapshot Lambda VPC config verified — it must reach the ONTAP management
      LIF on TCP 443. Confirm with a test invocation, not just a successful deploy.
- [ ] `AlarmNotificationTopicArn` set on the remediation stack — a silently
      failing containment action is worse than a noisy one.

## Operational

- [ ] Service accounts identified and excluded from monitors (`svc-*`)
- [ ] Detection rules in Warning mode for initial 2-week tuning period
- [ ] On-call/escalation path defined for Critical signals
- [ ] DLQ replay procedure documented
- [ ] Retention requirements defined (Datadog index + S3 archive lifecycle)
- [ ] Monthly cost estimate validated
- [ ] Recovery procedure tested (4-hour outage simulation)

## Infrastructure as Code

- [ ] CloudFormation template validated (cfn-lint + cfn-guard)
- [ ] setup-full-observability.sh tested in clean environment
- [ ] Terraform migration planned (for multi-org/enterprise)
- [ ] All configurations version-controlled in Git

## Compliance (Regulated Environments)

- [ ] S3 Object Lock (COMPLIANCE mode) applied to audit archive
- [ ] Log Archive configured in Datadog (source:fsxn to S3 to Glacier)
- [ ] Retention period aligned with regulatory requirement
- [ ] Rehydration procedure documented and tested
- [ ] Audit trail for remediation actions verified (CloudTrail + ONTAP + Case)
- [ ] Hash salt managed in Secrets Manager (if FIELD_MAPPING masking used)

---

## Related Documents

- [Setup Guide](setup-guide.md)
- [SPL vs CQL Comparison](spl-cql-comparison.md)
- [Field Mapping](field-mapping.md)
- [README (main)](../../README.md)
- [Pipeline SLO](../../../../docs/en/pipeline-slo.md)
- [DLQ Replay Runbook](../../../../docs/en/runbooks/dlq-replay.md)
- [Existing Audit Tool Coexistence](../../../../docs/en/existing-audit-tool-coexistence.md)
