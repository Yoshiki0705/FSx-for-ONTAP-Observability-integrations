# Cyber Resilience Capability Map — NIST CSF 2.0 Function Mapping

🌐 [日本語](../ja/cyber-resilience-capability-map.md) | **English** (this page)

## Why This Document Exists

The core question this repository answers is a cyber-resilience question: **for each of the six NIST CSF 2.0 functions, what does this repository provide for FSx for ONTAP workloads, what requires assembling existing pieces, and what remains a genuine gap?**

Earlier revisions of this document were organized around one particular vendor tool (NetApp DII (Data Infrastructure Insights) Storage Workload Security), comparing this repository's capabilities against it phase by phase. That framing put the comparison target at the center and the framework in the background, which inverted the actual priority: the [NIST Cybersecurity Framework (CSF) 2.0](https://www.nist.gov/cyberframework) is the organizing structure this repository is built to satisfy. DII SWS, AWS's own published ransomware-response guidance, and other tools are each one *reference point* among several for how a given CSF function gets implemented, not the yardstick everything else is measured against.

This document now uses the six CSF 2.0 functions (**Govern, Identify, Protect, Detect, Respond, Recover**) as its structure. Each function section covers:

1. **What the function requires**, per CSF 2.0 and (for ransomware specifically) [NIST IR 8374r1 — Ransomware Risk Management: A Cybersecurity Framework 2.0 Community Profile](https://csrc.nist.gov/pubs/ir/8374/r1/final)
2. **How this repository implements it for FSx for ONTAP**: the primary content, since that is this repository's actual scope
3. **Alternative implementation paths**: where a different AWS-native service or a SaaS/third-party product can satisfy the same CSF function requirement, presented as options suited to different contexts (data residency constraints, existing tooling investment, team skill set), not as competitors to this repository's approach
4. **Status**: ✅ Full, ⚠️ Requires assembly, or ❌ Gap, with the same discipline as before. This document does not overstate parity with any referenced tool

> **Evidence tier**: Claims about third-party tools (DII SWS, Splunk, Datadog, etc.) are drawn from each vendor's public documentation, linked per claim. Claims about AWS services (Macie, GuardDuty, AWS Backup, Config) are drawn from AWS's public documentation, linked per claim and verified against current service behavior as of this writing. This repo's own capability claims reflect functionality that exists and is E2E-verified in this codebase unless marked otherwise.

> **Onboarding note**: If you're new to this repository, read the six function sections top-to-bottom before jumping into any linked implementation guide — the ✅/⚠️/❌ status markers tell you upfront whether a capability is ready to deploy, needs assembly work, or isn't available. Once you've picked a section that interests you, the fastest path to a working deployment is usually: clone the repo, read that section's linked guide's own Prerequisites and Deployment sections, then come back here only if you need the broader CSF 2.0 context.

## NIST CSF 2.0 Overview

CSF 2.0 organizes an organization's *entire* cybersecurity risk-management program into six functions. Cloud and storage vendors commonly publish their own CSF mappings to show users which functions their tooling covers and which remain an organizational responsibility — this document follows that same convention. AWS publishes [Ransomware Risk Management on AWS Using the NIST CSF](https://docs.aws.amazon.com/whitepapers/latest/ransomware-risk-management-on-aws-using-nist-csf/technical-capabilities.html), mapping AWS services to CSF-organized technical capabilities (Backup, Event detection, Forensics and analytics, Mitigation and containment, and others). NetApp separately publishes a walk-through mapping BlueXP<!-- allow:naming -->, ONTAP, and DII SWS to each CSF function (source: [Fortify your cybersecurity defenses with NIST framework](https://www.netapp.com/it/blog/fortify-cybersecurity-nist-framework/)).

| CSF 2.0 Function | What It Requires |
|-------------------|-------------------|
| **Govern (GV)** | Risk-management strategy, roles, policy, and oversight are established and communicated |
| **Identify (ID)** | Assets, data, and dependencies that matter are inventoried and understood |
| **Protect (PR)** | Safeguards limit the likelihood and impact of an event |
| **Detect (DE)** | Anomalies and adverse events are found through continuous monitoring |
| **Respond (RS)** | Actions are taken on a detected incident: analysis, mitigation, and reporting |
| **Recover (RC)** | Systems and data are restored, and recovery is coordinated with stakeholders (CSF 2.0 splits this into RC.RP — Incident Recovery Plan Execution — and RC.CO — Incident Recovery Communication) |

**CSF 2.0 and NIST SP 800-61 operate at different altitudes, not as competing frameworks.** CSF 2.0 is the organization-wide risk-management wheel that Govern sits above; SP 800-61 is the tactical incident-handling lifecycle (Protect → Detect → Contain/Respond → Recover) that CSF's Detect/Respond/Recover functions delegate to during an actual event. Where a function section below needs SP 800-61-level operational detail (e.g., the specific containment mechanics under Respond), that detail is nested inside the relevant CSF function rather than treated as a separate top-level structure.

---

## Govern (GV)

**Requirement**: Risk-management strategy, roles, policy, and oversight are established and communicated.

**This repo's approach**: Not provided. This repository is an observability/response *pipeline*, not a governance program, and no storage-layer tool can substitute for organizational risk governance. CloudFormation-as-code and CloudWatch Logs audit trails supply evidence artifacts (who deployed what, when a block fired and why) that a Govern program can consume as input — see [Governance & Compliance](governance-and-compliance.md) and [Compliance Evidence Pack](compliance-evidence-pack.md) for that evidence layer.

**Status**: ⚠️ Out of scope by design.

> **Reference point**: Every vendor's CSF mapping — DII SWS's, AWS's own whitepaper, this repository's — marks Govern as out of scope for the same structural reason: strategy, roles, and board-level reporting are organizational decisions no tool can make for you. Treat any tool claiming to "solve" Govern with skepticism.

---

## Identify (ID)

**Requirement**: Assets, data, and dependencies that matter are inventoried and understood.

### FSx for ONTAP implementation (this repo)

[Data Classification Guide](data-classification.md) defines a field-level classification matrix (PII/Sensitive/Internal) for the `user`/`path`/`client_ip` fields this repository's audit-log and FPolicy pipelines emit. This is schema-level (field-name) classification, not content classification.

[Content-Level PII Classification Scanner](content-classification-scanner.md) adds content-level discovery: it reads files through an FSx for ONTAP S3 Access Point and calls Amazon Comprehend's `DetectPiiEntities` to find PII-shaped content, scoped to plain-text/structured-data formats (`.txt`, `.csv`, `.json`, `.log`, and similar — no document-format parsing; see that guide's Remaining Limitations).

> **Privacy note**: "Full" for content scanning covers *discovery* — finding PII-shaped content and recording entity type/confidence, never the matched values themselves (data-minimizing by design). It does not cover the judgment calls a DPO/privacy program still owns: which confidence threshold counts as "found" for a given regulation, whether a false positive needs a compliance-log correction, or how discovered PII maps to a legal basis for processing. Treat the scanner's report as an input to that program, not a replacement for it.

> **Data-pipeline note**: If you plan to build reporting or dashboards on top of the PII scanner's DynamoDB output (e.g., a fleet-wide "where is PII" view aggregating multiple scan reports), read the [Content-Level PII Classification Scanner](content-classification-scanner.md)'s own Data-pipeline note first — confidence scores are persisted as DynamoDB strings, not numbers, due to a `parse_float=str` workaround in the Lambda code, which breaks numeric filtering/sorting on that field unless you cast explicitly at query time.

### Alternative implementation paths

| Approach | How it works | Fit |
|----------|---------------|-----|
| **Amazon Macie** (AWS-native) | Macie's automated and job-based sensitive-data discovery operates against S3 general-purpose buckets, selected by bucket name or bucket-level criteria (source: [Scope options for sensitive data discovery jobs](https://docs.aws.amazon.com/macie/latest/user/discovery-jobs-scope.html)). Macie does not accept an S3 Access Point ARN as a scan target the way this repo's Comprehend-based scanner does — to use Macie against FSx for ONTAP data, you would first need to copy or sync the data into a standard S3 bucket, which reintroduces the double-storage/staleness problem this repo's S3-Access-Point-based scanner was built specifically to avoid. | Best suited when the data already lives in (or is regularly synced to) S3, and when Macie's broader entity-type catalog and native Security Hub/EventBridge integration outweigh the cost of that sync step. Not a drop-in replacement for scanning FSx for ONTAP volumes directly. |
| **NetApp BlueXP<!-- allow:naming --> data classification / DII SWS** (vendor SaaS) | Scans storage directly (including on-prem and multi-cloud NetApp systems) and maps findings to workload importance, without an S3-facing intermediate step. | Best suited for organizations already using BlueXP<!-- allow:naming --> across a mixed on-prem/cloud NetApp fleet, or that want classification to span systems beyond what this repo's AWS-native pipeline covers (see the Multi-cloud-scope note under Recover below for the same fleet-scope tradeoff). |

**Status**: ✅ Full for schema-level classification and text/structured-data content scanning via the FSx for ONTAP-native path; document-format (Office/PDF) content extraction remains unimplemented in this repo's scanner (see that guide's Remaining Limitations for the Textract extension point).

---

## Protect (PR)

**Requirement**: Safeguards limit the likelihood and impact of an event.

### FSx for ONTAP implementation (this repo)

Proactive ONTAP-native controls (export-policy, name-mapping) plus the underlying Snapshot/SnapLock immutability ONTAP itself provides — this is a shared ONTAP platform capability, not specific to this repo or to any particular monitoring tool layered on top of it. This repo's own pipeline attack surface (the Lambdas, IAM roles, and Secrets Manager credentials that call the ONTAP REST API) is covered separately by IAM least-privilege and Secrets Manager rotation — see [Security Considerations](automated-response-guide.md#security-considerations).

### Alternative implementation paths

| Approach | How it works | Fit |
|----------|---------------|-----|
| **AWS Backup Vault Lock** (AWS-native) | Applies WORM (write-once-read-many) immutability to backup vaults independently of the underlying storage service's own immutability feature, with a compliance mode that even the account root user cannot override once locked. | Useful as a second, AWS-managed immutability layer on top of (not instead of) ONTAP's own SnapLock, particularly if your compliance program specifically requires an AWS-side attestation of immutability rather than relying solely on the storage vendor's mechanism. |
| **DII SWS per-user access baseline** (vendor SaaS) | Passive, ML-established per-user access monitoring as a Protect-phase input — this is descriptive of normal behavior, not a blocking control by itself. | This repo does not ship an equivalent passive ML baseline (see Detect below for what fills the closest role) — if passive behavioral baselining specifically is a hard requirement independent of active blocking, DII SWS or an equivalent SIEM feature remains necessary. |

**Status**: ✅ Full for storage-layer safeguards (shared ONTAP mechanism).

---

## Detect (DE)

**Requirement**: Anomalies and adverse events are found through continuous monitoring.

### FSx for ONTAP implementation (this repo)

ONTAP ARP (Autonomous Ransomware Protection) provides native file-content entropy/extension-change detection — see [ARP Incident Response Guide](arp-incident-response-guide.md). ONTAP EMS provides a broader event catalog including quota/capacity anomalies — see [EMS Detection Capabilities](ems-detection-capabilities.md). Neither of these requires a third-party SIEM; both are ONTAP-native and this repo's Lambda-based EMS webhook delivers ARP alerts in ~30 seconds.

Per-user *behavioral* anomaly detection (distinguishing this user's current access pattern from their own historical baseline, as opposed to matching a known ransomware signature) is delegated to whichever SIEM you already ship audit/FPolicy events to — Datadog Watchdog, Elastic ML Jobs, or Splunk MLTK — via the [Detection Use Cases](detection-use-cases.md) guide. This is not built into this repository; it requires SIEM-side configuration and training data.

> **Threat-intelligence note**: Entropy/extension-change analysis (ARP) and behavioral ML sit on opposite ends of a detection tradeoff worth naming explicitly: entropy-based detection generalizes well to *novel* ransomware families it has never seen (encryption inherently raises file entropy regardless of which malware caused it), while behavioral ML generalizes well to detecting *anomalous access patterns* even from an attacker using no encryption at all (e.g., mass exfiltration without encryption, a growing tactic in double-extortion campaigns). Neither approach alone covers both cases well — this is precisely why this repo pairs ARP (signature/entropy) with SIEM-delegated ML (behavioral) rather than treating either as sufficient on its own. The [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s own extension-list scan adds a third, narrower signature-list-based check layered on top of ARP — see that guide's own Threat-intelligence note for why that third layer has the most evasion-prone blind spot of the three.

### Alternative implementation paths

| Approach | How it works | Fit |
|----------|---------------|-----|
| **Amazon GuardDuty Malware Protection for S3** (AWS-native) | Automatically scans newly uploaded S3 objects for malware using AWS-developed and third-party scanning engines. Per AWS's own documentation, this feature **supports only general purpose S3 buckets** (source: [Supportability of Amazon S3 features](https://docs.aws.amazon.com/guardduty/latest/ug/supported-s3-features-malware-protection-s3.html)) — it does not scan objects exposed via an FSx for ONTAP S3 Access Point, so it cannot be pointed directly at FSx for ONTAP file data the way it can at a standard S3 bucket. | Relevant if you're already staging or exporting FSx for ONTAP content into a standard S3 bucket as part of another pipeline (e.g., a backup export or a data lake ingest) — GuardDuty can scan that S3 copy, but this is a different data path than scanning the live FSx volume. |
| **DII SWS behavioral ML** (vendor SaaS) | Trained, per-user anomaly-detection model running in NetApp's SaaS backend — no signatures needed, flags deviation from a user's normal/seasonal access pattern. | The closest like-for-like equivalent to the SIEM-ML-delegation path above, packaged as a turnkey model rather than a SIEM feature you configure and train yourself. Preferred when you want behavioral detection without your own SIEM's ML tuning effort, at the cost of a separate SaaS dependency. |

**Status**: ✅ Full for signature/entropy-based and quota-anomaly detection (native ONTAP mechanisms, no third-party dependency); ⚠️ Requires assembly for behavioral ML (SIEM configuration and training data, or a vendor SaaS alternative).

---

## Respond (RS)

**Requirement**: Actions are taken on a detected incident: analysis, mitigation, and reporting.

### FSx for ONTAP implementation (this repo)

The [Automated Response Guide](automated-response-guide.md) implements ONTAP-native containment — `name-mapping` denial (SMB user block), export-policy deny rules (NFS IP block), and `create_snapshot` (protective snapshot with storm-prevention cooldown) — triggerable from *any* detection source via SNS, not tied to one particular SIEM or ML model. Admin alerting uses an SNS topic (any downstream: email, Slack, PagerDuty), and time-limited access restriction uses an EventBridge Scheduler auto-unblock companion stack.

Forensic investigation (the RS.AN analysis sub-function) is built from the same normalized `user`/`client_ip`/`path`/`operation` fields already present in the audit-log and FPolicy pipelines. Per-vendor dashboard implementation for this exists for four SIEMs today:

| SIEM | Implementation | Reference |
|------|-----------------|-----------|
| Splunk | Four `.spl` searches (user timeline, all-activity, IP-centric drill-down, file-entity history) composed into a Dashboard Studio dashboard with input tokens | [`integrations/splunk-serverless/searches/`](../../integrations/splunk-serverless/searches/) |
| Datadog | Dashboard JSON (8 widgets: ARP timeline, response actions, affected volumes, severity, user activity, audit trail, client IPs, recovery verification) | [`integrations/datadog/dashboards/`](../../integrations/datadog/dashboards/) |
| Grafana | A provided dashboard JSON using LogQL with `\| json` parsing (Loki's label-cardinality constraint means `user`/`client_ip`/`path` stay in the log body, not as labels) | [`integrations/grafana/dashboards/forensics-investigation.json`](../../integrations/grafana/dashboards/forensics-investigation.json) |
| Elastic | Kibana Discover + Lens, since ECS field mapping (`user.name`, `source.ip`, `file.path`, `event.action`) is already defined in the [Normalized Event Schema](normalized-event-schema.md#vendor-mapping-matrix) | [Elastic Setup Guide](../../integrations/elastic/docs/en/setup-guide.md#forensic-investigation-kibana-discoverlens) |

> **PII/compliance cross-reference**: Before building any forensic dashboard, review the [Data Classification Guide](data-classification.md) — `user`/`UserName` is classified PII (High risk) and `path`/`ObjectName` is classified Sensitive. A forensics dashboard is, by definition, displaying these fields in raw form to investigators; restrict dashboard access via vendor RBAC accordingly.

> **Data source decision**: Two independent pipelines exist for forensic investigation: **FPolicy** (`operation_type`/`file_path`/`client_ip`/`user`/`protocol`, action-level, sub-second/event-driven — "what action, right now") and **Audit Logs** (`operation`/`path`/`user`/`client_ip`/`result`, access-check level, minutes-latency — "what happened over the last N days," and the only source with `result: Failure` for access-denied forensics). Because they're independent, you can cross-check FPolicy gaps against the audit-log pipeline's `EventID`-based records for the same file operation.

### Alternative implementation paths

| Approach | How it works | Fit |
|----------|---------------|-----|
| **DII SWS automated response** (vendor SaaS) | Automated user/IP block + protective snapshot + admin alert, driven by DII's own ML detection specifically (not triggerable from an arbitrary third-party detection source). | Preferred if you want response tightly coupled to DII's own detection model in a single vendor-managed workflow. This repo's approach trades that tight coupling for source-agnostic triggering (any SIEM, any detection rule, via SNS). |
| **AWS Security Hub + Systems Manager Automation** (AWS-native) | Route a GuardDuty/Security Hub finding to an SSM Automation runbook that performs a containment action. | A viable pattern for AWS-centric detection sources that don't call the ONTAP REST API directly; would need a custom runbook step to invoke the same `name-mapping`/export-policy ONTAP actions this repo's Lambda already implements. |

**Status**: ✅ Full for mitigation tooling (ONTAP-native, source-agnostic). ✅ Forensics dashboards provided for Datadog (JSON), Grafana (JSON), Elastic (KQL saved searches), and Splunk (SPL queries) — see the [AWS-Native Alternative Matrix](native-alternative-matrix.md#forensics-dashboard--per-vendor-reference) for per-vendor artifacts and deploy methods.

---

## Recover (RC)

**Requirement**: Systems and data are restored, and recovery is coordinated with stakeholders. CSF 2.0 splits this into **RC.RP** (Incident Recovery Plan Execution) and **RC.CO** (Incident Recovery Communication).

> **Resilience-maturity note**: Industry analysis of CSF 2.0's RECOVER function (e.g., [Elastio's mapping of ransomware recovery to CSF 2.0](https://elastio.com/blog/mapping-ransomware-recovery-to-nist-csf-20)) makes a point worth internalizing before reading the rest of this section: a snapshot or backup existing is a **Protect** artifact, not evidence that RC.RP is operationally credible. RC.RP is only credible once you can point to a recovery point that has actually been tested and confirmed free of compromise — not merely a completed snapshot job. Treat "snapshot created" and "verified clean recovery point, tested" as two different maturity levels.

### FSx for ONTAP implementation (this repo)

A protective snapshot is created during Respond (see above) — that alone only delivers the first maturity level. The [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) closes the RC.RP verification gap: it clones the candidate snapshot (FlexClone), scans it via an isolated S3 Access Point for ransomware-associated file extensions, and records a clean/suspicious/error verdict — before a human commits to a restore. This is a fast pre-filter, not a full forensic-grade scan or an end-to-end restore rehearsal (see that guide's Comparison section for the precise boundary).

> **Restore-testing note**: "RC.RP verification: Full" means the automated extension-pattern pre-filter is fully implemented — it does not mean recoverability itself is fully proven. Running an actual restore (mounting the recovered volume, validating application-level data integrity, timing the operation against your RTO) is still a separate, periodic exercise this repo does not automate. Treat the verification workflow's "clean" verdict as the gate that decides *whether* a snapshot is worth spending a restore-rehearsal cycle on, not as a substitute for running that rehearsal.

> **Timing-expectation note**: "Full" here also does not mean *fast*. This workflow's `AttachAccessPoint` step waits on Amazon FSx's own asynchronous discovery of a volume created via ONTAP REST API — AWS's documentation describes this sync as taking "up to several minutes," but this project's own continuous, gap-free measurements across three separate runs against the same idle file system came in at ~12, ~24, and ~36 minutes, an increasing pattern with too few data points to confirm an upper bound. If you're setting an RC.RP-verification SLA (e.g., "verified within 15 minutes of snapshot creation" for an incident runbook), confirm that target against your own environment's measured delay rather than assuming AWS's "several minutes" guidance or this project's own range applies unchanged — see the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md#step-2-s3-access-point-attachment)'s own measurement notes for the methodology.

> **DR-runbook sequencing note**: The RC.RP capability described here is a technical building block, not a DR runbook step — deploying the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) does not by itself change how your organization decides to fail over or restore during an actual incident, unless you explicitly update your DR runbook to gate on this workflow's verdict (see that guide's own DR-runbook sequencing note for the specific sequencing question).

RC.CO (stakeholder-level recovery communication) is covered only minimally — the SNS notification described under Respond is a signal that something happened, not a coordinated communication plan.

### Alternative implementation paths

| Approach | How it works | Fit |
|----------|---------------|-----|
| **AWS Backup restore testing** (AWS-native) | AWS Backup's restore testing feature performs *automated, periodic, scheduled* restore jobs against real recovery points, and its supported resource types explicitly include **Amazon FSx (Lustre, ONTAP, OpenZFS, Windows)** alongside Aurora, DynamoDB, EBS, EC2, EFS, Neptune, RDS, and S3 (source: [Restore testing — AWS Backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/restore-testing.html)). Unlike this repo's FlexClone-based scan, AWS Backup restore testing performs an *actual restore* and then deletes the restored resource after the validation window. This is a genuinely different, complementary capability, not a duplicate of the FlexClone scan. AWS Backup Audit Manager can further turn on a control confirming a restore test met specified restore objectives, directly supporting an RC.RP compliance assertion. | If you already use AWS Backup to protect your FSx for ONTAP file system (as opposed to relying solely on ONTAP-native Snapshot), restore testing is a strong complement to this repo's FlexClone-based pre-filter: run the pre-filter as the fast, frequent first gate, and AWS Backup restore testing as the periodic, deeper "does this actually restore end-to-end" exercise the Restore-testing note above says this repo does not automate. |
| **DII SWS automatic Snapshot restore** (vendor SaaS) | Automatic snapshots taken at detection time simplify restoration, but the restore itself remains a manual ONTAP operation — DII SWS does not publish RC.CO tooling beyond alerts either. | Comparable maturity level to this repo's Respond-phase snapshot alone (i.e., without the RC.RP verification layer this repo adds) — DII SWS does not appear to publish an equivalent FlexClone-based pre-restore verification step as of this writing. |

**Status**: ✅ Full for RC.RP verification via the FlexClone pre-filter (fast pre-filter, not a full forensic scan). ⚠️ Requires assembly for an actual end-to-end restore rehearsal — AWS Backup restore testing is the recommended AWS-native complement for FSx for ONTAP specifically. ⚠️ RC.CO stakeholder coordination remains a minimal SNS signal in this repo.

---

## Remaining Gaps (Not Yet Addressed)

Being direct about what this repo does **not** provide, so this document doesn't overstate coverage:

1. **No built-in per-user behavioral ML model.** Covered under Detect above — this repo delegates to your SIEM's ML feature or a vendor SaaS alternative, both of which have different false-positive characteristics than an in-house trained model.
2. **No pre-built unified dashboard across all data — buildable, not shipped.** This repo is inherently multi-vendor: if you ship to more than one SIEM, forensic investigation (Respond, above) happens per-vendor today, not in one pane of glass. This is a packaging gap, not a technical ceiling. Two paths close it: (a) route every vendor through the [OTel Collector integration](../../integrations/otel-collector/) into a single OTLP-native backend; or (b) since every vendor pipeline emits the same [Normalized Event Schema](normalized-event-schema.md) fields (`source`, `svm`, `user`, `path`, `operation`), a query layer that reads across vendor-specific stores (e.g., Athena over exported logs) can reconstruct a unified view without picking one vendor as the source of truth.
3. **No pre-built cross-storage-system view — buildable, not shipped.** This repo is scoped to FSx for ONTAP by default; other NetApp systems (on-prem ONTAP, other FSx for ONTAP file systems, other regions/accounts) are not correlated out of the box. This is a scope-of-delivery gap: the same audit-log/FPolicy pipelines can be deployed against any ONTAP-based system exposing an FPolicy/audit-log path, and the [Multi-Account Deployment](multi-account-deployment.md) StackSets pattern already fans this pipeline out across AWS accounts and regions.

> **Multi-cloud-scope note**: The "any ONTAP-based system" claim above is scoped to ONTAP running on AWS (FSx for ONTAP) or on-prem ONTAP with a network path reachable from this repo's Lambdas/ECS tasks — it does not extend to ONTAP offerings on other hyperscalers (e.g., Azure NetApp Files, Google Cloud NetApp Volumes) without adapting the network/IAM layer to that platform's equivalents, since this repo's pipelines are built on AWS-native services rather than a cloud-agnostic collector.

> **Data-residency note**: The [Multi-Account Deployment](multi-account-deployment.md) StackSets pattern fans this repo's pipelines out across AWS accounts *and Regions* — directly useful for a residency requirement that data stay within a specific Region's boundary, since each StackSet-deployed instance operates independently within its own target Region. This repo does not verify that a given deployment's data actually stayed within its intended Region boundary — that remains a deployment-time configuration discipline you own.

> **Resource-tagging note**: Fanning these pipelines out across accounts/Regions via StackSets multiplies any existing tagging gaps — both the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) and [Content-Level PII Classification Scanner](content-classification-scanner.md) templates only tag their DynamoDB tables, leaving Lambda functions (and, for the recovery guide, the Step Functions state machine) untagged. Add the missing `Tags` blocks before fanning these templates out at scale.

> **Patch-management note**: This repo's Lambda functions each pin `python3.12` as the runtime but do not pin exact `boto3`/`botocore` package versions in their inline CloudFormation `ZipFile` code. If your organization requires pinned, auditable dependency versions, package these Lambdas with a `requirements.txt`-pinned deployment (Lambda Layers or a container image) instead.

> **Security note**: The [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s ONTAP-facing Lambdas disable TLS certificate verification (`cert_reqs="CERT_NONE"`) unconditionally in their inline CloudFormation code — see that guide's own security note under Prerequisites for the specific fix required before production use.

> **Retry-policy note**: Both the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s Step Functions state machine and the notification path in the [Content-Level PII Classification Scanner](content-classification-scanner.md) share a pattern: neither implements automatic retry for transient errors, and both stacks' SNS `publish` calls fail silently into a log line rather than surfacing as an alarm-able error.

> **Encryption-at-rest note**: The DynamoDB tables in both the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) and [Content-Level PII Classification Scanner](content-classification-scanner.md) enable encryption but use the AWS-owned DynamoDB key rather than a customer-managed KMS key, and neither template exposes a parameter to change this.

> **ONTAP-lifecycle note**: Repeated FlexClone create/delete cycles against the same parent volume (the pattern the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) runs on every verification) can hit an ONTAP-internal retention mechanism — the "volume recovery queue" — that blocks deletion of the *parent* volume with an error referencing "one or more clones," even after `aws fsx describe-volumes` no longer lists any child clone. This is not a gap in this repo's own workflow (it only ever deletes the clone it created, never a parent), but it is a gap worth knowing about for whoever manages the underlying parent volumes this workflow runs against repeatedly — see that guide's own Operational-finding note under Step 5 for the diagnosis and the ONTAP-side (not FSx-side) resolution.

> **Cost-accumulation note**: If you fork or extend the FSx-facing Lambdas referenced throughout this document (this repo's own `Cleanup` Lambda in the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) already handles this), be aware that `fsx.delete_volume()` takes a final backup of the deleted volume by default unless `OntapConfiguration.SkipFinalBackup=True` is passed explicitly. For a throwaway or intermediate volume (a FlexClone built only to be scanned and discarded, for example), omitting that flag silently accumulates one retained, never-expiring backup per deletion — a cost trap that compounds specifically on the kind of scheduled, frequent-run automation this document describes across multiple capabilities.

> **CI-coverage note**: This repo's E2E-verified claims (see the Evidence tier note at the top of this document) reflect functionality validated in the codebase — but "E2E-verified" and "covered by an automated CI merge gate" are not the same claim. As of this writing, `.github/workflows/ci.yaml` runs the per-vendor `tests/` suites and `shared/lambda-layers/ems-parser/tests/` on every push and pull request, but not `shared/python/tests/` — the unit tests for the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s `restore_verification.py` (see that guide's own CI-coverage note under Testing) and the other `shared/python/` modules referenced elsewhere in this document run only when a contributor invokes `pytest` locally. A regression in one of those modules would not fail CI today.

4. **Content-level PII discovery covers text/structured-data formats only — not a hard ceiling.** Covered under Identify above — Office documents and PDFs are not text-extracted; feeding extracted text from Amazon Textract into the scanner's existing `classify_object` logic would cover those formats too.
5. **No Govern-function tooling.** Covered under Govern above — this is a structural gap shared by every storage-layer tool referenced in this document.

## FAQ

**Q: Why did this document change from a DII SWS comparison to a CSF 2.0 structure?**
A: The comparison-first framing put a single vendor's product at the center of a document whose actual purpose is describing this repository's own cyber-resilience coverage. CSF 2.0 is the framework this repository is designed against; DII SWS, AWS Backup, Macie, GuardDuty, and other tools are each referenced within the relevant function section as one implementation option among several — not as the organizing structure itself.

**Q: Do I need all four vendor implementations (Splunk, Datadog, Grafana, Elastic) under Respond?**
A: No — build the Forensics dashboard only for whichever SIEM(s) you already ship audit/FPolicy events to. This document covers all four because the repo supports all four as ingestion targets, not because you need all four simultaneously.

**Q: Does this repo replace a per-user behavioral ML model (DII SWS's or otherwise)?**
A: No. See Detect above — the ML behavioral baseline is a remaining gap this repo delegates to your SIEM's ML feature or a vendor SaaS alternative, either of which has different false-positive characteristics than an in-house trained model.

**Q: Where does CSF 2.0's Govern function fit, since this document doesn't implement it?**
A: It doesn't — deliberately. Govern (risk-management strategy, roles, policy, board oversight) is an organizational responsibility that no storage-layer tool can automate on your behalf. See [Governance & Compliance](governance-and-compliance.md) and the [Compliance Evidence Pack](compliance-evidence-pack.md) for the evidence artifacts a Govern program would consume as input, not as a substitute for the program itself.

**Q: The document marks RC.RP verification and content-level PII discovery as "✅ Full" — does that mean those problems are solved end-to-end?**
A: "Full" means the specific capability described is fully implemented and E2E-verified — it does not mean the broader problem the function addresses is closed. RC.RP verification is a fast, automated pre-filter; an actual restore rehearsal is a separate exercise (see AWS Backup restore testing under Recover, above, for the AWS-native complement). Content-level PII discovery similarly stops at reporting entity type/confidence; deciding what confidence threshold constitutes "found" PII for your regulatory context remains your data protection program's responsibility.

**Q: How should this document be positioned to a reader or in an engagement conversation?**
A: As a transparent capability-and-gap analysis against a recognized framework, not a marketing comparison against any single vendor — the ✅/⚠️/❌ markers exist specifically so this repo doesn't overstate its own coverage, and the alternative-implementation-path tables exist to help a reader choose the right tool for their context rather than assume this repo's approach is the only option. Lead with the ⚠️/❌ rows honestly in any reader-facing summary.

**Q: Can this document's ✅ markers be cited directly as evidence in an internal audit or SOX control matrix?**
A: Not directly — a ✅ mark here means a capability exists and is E2E-verified in this codebase, which is a different claim than "this control operated effectively during the audit period at your organization." See the Audit-evidence notes in the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) and [Content-Level PII Classification Scanner](content-classification-scanner.md) for what additional evidence (execution history, human review/attestation) each capability needs beyond its own existence to support a formal control assertion.

**Q: If a "suspicious" or "PII found" notification never arrives from either the recovery-verification workflow or the PII scanner, does that mean the result was clean?**
A: Not necessarily — both capabilities swallow SNS publish failures into a log line rather than an alarm, per the SNS-delivery notes in each guide (see the Retry-policy note under Remaining Gaps for the cross-cutting pattern). Check the underlying DynamoDB record directly (`verdict`/`files_with_pii`) rather than treating "no notification arrived" as equivalent to "nothing was found."

**Q: Do the ✅/⚠️/❌ markers in this document map to a specific compliance program (FedRAMP, ISMAP, HIPAA, PCI DSS)?**
A: No — this document maps to NIST CSF 2.0 function coverage only, which is a risk-management framework, not an accreditation program. A ✅ here means a CSF function is technically implemented and E2E-verified in this codebase; it says nothing about whether your specific deployment has completed a formal assessment against FedRAMP, ISMAP, HIPAA, PCI DSS, or any other regime with its own control catalog and audit process. Treat this document as one input a compliance program can use when mapping its own control requirements to available technical capabilities — not as a substitute for that program's own accreditation work. See [Governance & Compliance](governance-and-compliance.md) and the [Compliance Evidence Pack](compliance-evidence-pack.md) for the evidence-collection layer a formal assessment would actually draw on.

## Related Documents

- [Automated Response Guide](automated-response-guide.md) — Respond-phase implementation
- [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) — RC.RP verification (FlexClone + isolated scan + recorded verdict), closing Remaining Gaps item 4's counterpart under Recover
- [Content-Level PII Classification Scanner](content-classification-scanner.md) — Content-level PII discovery via Amazon Comprehend, closing Remaining Gaps item 4 for text/structured-data formats
- [ARP Incident Response Guide](arp-incident-response-guide.md) — Protect/Detect via ONTAP native ransomware detection
- [EMS Detection Capabilities](ems-detection-capabilities.md) — Detect-phase event catalog
- [Detection Use Cases](detection-use-cases.md) — Source selection for Detect-phase configuration
- [Normalized Event Schema](normalized-event-schema.md) — The shared field definitions underlying every Forensics implementation above
- [Data Classification Guide](data-classification.md) — PII handling for the user/IP/path fields, and the schema-level classification the Content-Level PII Classification Scanner complements
- [Governance & Compliance](governance-and-compliance.md) — The Govern-function evidence layer this document defers to
- [Compliance Evidence Pack](compliance-evidence-pack.md) — Audit-trail evidence artifacts for Govern/RC.CO reporting
- [Security Monitoring Index](security-monitoring-index.md) — Role-based navigation across all security docs

## External References

- [NIST Cybersecurity Framework (CSF) 2.0](https://www.nist.gov/cyberframework)
- [NIST IR 8374r1 — Ransomware Risk Management: A Cybersecurity Framework 2.0 Community Profile](https://csrc.nist.gov/pubs/ir/8374/r1/final)
- [AWS — Ransomware Risk Management on AWS Using the NIST CSF](https://docs.aws.amazon.com/whitepapers/latest/ransomware-risk-management-on-aws-using-nist-csf/technical-capabilities.html)
- [AWS Backup — Restore testing](https://docs.aws.amazon.com/aws-backup/latest/devguide/restore-testing.html)
- [Amazon Macie — Scope options for sensitive data discovery jobs](https://docs.aws.amazon.com/macie/latest/user/discovery-jobs-scope.html)
- [Amazon GuardDuty — Supportability of Amazon S3 features for Malware Protection](https://docs.aws.amazon.com/guardduty/latest/ug/supported-s3-features-malware-protection-s3.html)
- [NetApp — Fortify your cybersecurity defenses with NIST framework](https://www.netapp.com/it/blog/fortify-cybersecurity-nist-framework/)
- [NetApp — Data Infrastructure Insights Storage Workload Security](https://docs.netapp.com/us-en/ontap-technical-reports/ransomware-solutions/ransomware-DII-workload-security.html)
- [NetApp — Forensics Activity FAQ](https://kb.netapp.com/Cloud/BlueXP/Cloud_Insights/FAQ:_Storage_Workload_Security_Forensics_Activity) <!-- allow:naming -->
- [Elastio — Mapping Ransomware Recovery to NIST CSF 2.0](https://elastio.com/blog/mapping-ransomware-recovery-to-nist-csf-20)
