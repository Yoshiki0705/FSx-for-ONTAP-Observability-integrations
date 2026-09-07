# Partner Solution Brief: FSx for ONTAP Serverless Observability

🌐 [日本語](../ja/partner-solution-brief.md) | **English** (this page)

## Organization Challenge

Organizations running Amazon FSx for NetApp ONTAP need to ship file access audit logs to their existing SIEM/Observability platform. The current approach requires EC2 instances running syslog-ng and vendor-specific forwarders — adding operational overhead, patching burden, and fixed monthly costs.

**Common questions**:
- "How do I get FSx for ONTAP audit logs into Splunk/Datadog/Grafana without managing EC2?"
- "Can I detect ransomware activity on my file shares in real-time?"
- "How do I meet compliance requirements for file access auditing?"

## Solution Overview

Serverless log shipping pipeline using Lambda + EventBridge + S3 Access Points. No EC2 instances to manage.

```
FSx for ONTAP → S3 Access Point → EventBridge Scheduler → Lambda → Vendor API
                                                                (Datadog/Splunk/Grafana/etc.)
```

**Three event sources** for comprehensive coverage:
1. **Audit Logs** (S3 AP polling): File access events (who accessed what, when)
2. **EMS Webhooks** (API Gateway): System events (ARP ransomware detection, quota alerts)
3. **FPolicy** (ECS Fargate → SQS): Real-time file operation stream (sub-second latency)

## Business Value

| Metric | Before (EC2-based) | After (Serverless) |
|--------|--------------------|--------------------|
| Monthly infrastructure cost | ~$66 (2x t3.medium + EBS) | ~$6 (Lambda + EventBridge) |
| Patching frequency | Monthly (OS + agent) | Zero (managed services) |
| Scaling | Manual (instance resize) | Automatic (Lambda concurrency) |
| Time to first log | Hours (provision + configure) | 30 minutes (CloudFormation deploy) |
| Vendor switching cost | High (rewrite forwarder config) | Low (change Lambda target or use OTel Collector) |

## Target User Profiles

### Profile A: Splunk Modernization
- **Current state**: EC2 Universal Forwarder shipping FSx for ONTAP logs to Splunk
- **Pain point**: EC2 patching, agent updates, fixed cost
- **Recommended path**: Splunk Serverless integration + EC2 migration guide
- **PoC scope**: Deploy serverless stack in parallel, compare event parity for 48h

### Profile B: New Observability Platform
- **Current state**: FSx for ONTAP audit logs not shipped anywhere
- **Pain point**: No visibility into file access patterns, compliance gaps
- **Recommended path**: Start with free-tier vendor (New Relic 100GB/month or Grafana 50GB/month)
- **PoC scope**: Deploy audit poller, confirm logs queryable, build first dashboard

### Profile C: Multi-Vendor / Vendor-Neutral
- **Current state**: Multiple observability tools across teams
- **Pain point**: Duplicate pipelines, vendor lock-in
- **Recommended path**: OTel Collector (single Lambda, fan-out to multiple backends)
- **PoC scope**: Local Docker validation (5 min), then AWS deployment with 2+ backends

### Profile D: Security / Ransomware Detection
- **Current state**: No real-time alerting on file system anomalies
- **Pain point**: Ransomware detection relies on periodic scans
- **Recommended path**: EMS webhook (ARP events) + FPolicy (file operation stream)
- **PoC scope**: Simulate ARP trigger, confirm alert in <30 seconds

### Profile E: CrowdStrike Falcon LogScale Integration
- **Current state**: CrowdStrike Falcon deployed for endpoint protection; LogScale available but no file storage logs ingested
- **Pain point**: File access audit logs are not correlated with endpoint telemetry in Falcon
- **Recommended path**: Lambda → LogScale HEC (Splunk-compatible endpoint, zero migration friction)
- **PoC scope**: Deploy audit log shipper, confirm structured events in LogScale repository, build first search query

## PoC Engagement Model

### Scope (1-week engagement)

| Day | Activity | Deliverable |
|-----|----------|-------------|
| 1 | Requirements gathering + environment assessment | PoC Plan document |
| 2 | Deploy prerequisites (S3 AP, audit logging) | Working S3 AP + audit logs flowing |
| 3 | Deploy vendor integration + validate delivery | First log in backend |
| 4 | Dashboard + alerts + operational validation | Dashboard screenshot + DLQ empty |
| 5 | Documentation + Go/No-Go decision | PoC Report + Next Steps |

### Success Criteria (Level 1 — Minimum)

- [ ] One audit log record arrives in the observability backend
- [ ] SSM checkpoint advances after successful delivery
- [ ] DLQ remains empty (zero failed deliveries)
- [ ] CloudFormation deploy and delete complete cleanly
- [ ] Cost estimate produced for expected production volume

### Go/No-Go Decision Inputs

| Question | Owner |
|----------|-------|
| Does delivery latency meet requirements? | Platform team |
| Is the cost acceptable for production volume? | Finance / FinOps |
| Does the data classification allow external delivery? | Security / Compliance |
| Who owns the pipeline operationally? | Operations |
| Is the vendor's data residency acceptable? | Legal / DPO |

## Pricing Guidance

### AWS Infrastructure (all vendors)

| Log Volume | Monthly AWS Cost | Notes |
|-----------|-----------------|-------|
| 1 GB/month | ~$2 | Lambda + EventBridge + Secrets Manager |
| 10 GB/month | ~$8 | Typical enterprise file server |
| 100 GB/month | ~$41 | Large-scale deployment |

### Vendor Platform Costs (varies)

See [Vendor Comparison](vendor-comparison.md) for detailed cost tables per vendor.

**Free tier options for PoC**:
- New Relic: 100 GB/month (best for PoC)
- Grafana Cloud: 50 GB/month
- Sumo Logic: 1.25 credits/day (7-day retention)
- Honeycomb: 20M events/month

## Approach Comparison

This serverless pattern is one option among several. Each approach suits a different context — the right choice depends on your team's operating model, log volume, and existing investments. Trade-offs are listed symmetrically, including for this pattern.

| Approach | Suited for | Trade-offs to consider |
|----------|-----------|------------------------|
| This serverless pattern (Lambda / Firehose) | Variable or low-to-medium volume, teams minimizing infrastructure management, multi-vendor delivery | Cold starts and per-invocation limits; sustained very high throughput may call for Firehose or a streaming design |
| EC2-based (e.g., AWS Blog: syslog-ng + Universal Forwarder) | Existing EC2/forwarder investments, very high sustained throughput, full host control | Fixed cost regardless of volume; OS and agent patching/capacity are your responsibility |
| CloudWatch Logs only | Teams standardized on AWS-native console and tooling | Query and dashboards stay within the CloudWatch ecosystem; cross-vendor routing needs extra work |
| Host-based vendor agents | Environments already operating a vendor agent fleet | An agent must run where the data lives; FSx for ONTAP does not host third-party agents, so this fits host-based sources better |
| Manual log download | One-off investigations and ad hoc audits | No automation, scheduling, or alerting; not suited to continuous pipelines |

### How to choose

- Prioritize low operational overhead with variable volume → this serverless pattern
- Already invested in EC2 forwarders, or need very high sustained throughput → EC2-based
- Staying entirely within AWS-native tooling → CloudWatch Logs
- Standardized on a host-based vendor agent fleet → vendor agents

This solution does not aim to replace these options; it offers a serverless alternative for teams whose context favors it.

## Resources

- [GitHub Repository](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations)
- [PoC Success Criteria](poc-success-criteria.md)
- [Vendor Comparison](vendor-comparison.md)
- [Security Best Practices](security-best-practices.md)
- [Demo Scenarios](demo-scenarios.md)

## Next Steps for Partners

1. Identify an organization with FSx for ONTAP + observability need
2. Use this Solution Brief in the initial conversation
3. Propose a 1-week PoC engagement using the model above
4. Deploy using the CloudFormation templates (30-minute Quick Start)
5. Deliver PoC Report with Go/No-Go recommendation

## Setup Time Reference

| Component | Time to Deploy | Prerequisites |
|-----------|---------------|---------------|
| Serverless pipeline (CloudFormation) | 30 minutes | AWS account, FSx for ONTAP with audit enabled |
| NetApp Console + System Manager (GUI) | 1-2 business days | NSS account creation (free, 1 business day for approval) |
| EMS Webhook (quota alerts) | 1 hour | ONTAP CLI access (fsxadmin) |
| FPolicy (real-time file ops) | 2-4 hours | VPC networking, ECS Fargate, ONTAP CLI |
| Full PoC (pipeline + dashboard + alerts) | 1 week | All above + vendor account |

> **Note**: NetApp Console account creation requires 1 business day for end-user-level access approval (the NetApp registration UI labels this tier "Customer/End User" — see [system-manager-gui-guide.md](system-manager-gui-guide.md) for details). Plan accordingly when scheduling PoC engagements that include GUI-based management demonstrations.
