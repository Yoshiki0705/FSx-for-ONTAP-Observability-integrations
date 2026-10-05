# AWS-Native Alternative Matrix — System Manager / Workload Factory / DII

🌐 [日本語](../ja/native-alternative-matrix.md) | **English** (this page)

## Purpose

This document maps every major feature of ONTAP System Manager, NetApp Workload Factory, and DII Storage Workload Security to its AWS-native equivalent implementation in this repository. The goal: demonstrate that production-grade FSx for ONTAP operations, monitoring, and security can be achieved without proprietary management consoles.

> **Positioning note**: This is not a "competitor comparison." Each tool serves different contexts. This matrix helps teams that have already chosen AWS-native operations to verify full feature coverage, and helps teams evaluating options to understand what's available without additional licensing.

---

## ONTAP System Manager — Feature Coverage

| System Manager Feature | AWS-Native Equivalent | This Repo | Status |
|----------------------|----------------------|-----------|:------:|
| **Performance: IOPS** | CloudWatch `DataReadOperations` + `DataWriteOperations` | `fsxn-monitoring-dashboard.yaml` | ✅ |
| **Performance: Throughput** | CloudWatch `DataReadBytes` + `DataWriteBytes` | `fsxn-monitoring-dashboard.yaml` | ✅ |
| **Performance: Latency** | CloudWatch `DataReadOperationTime`/`DataWriteOperationTime` (derived latency = `OperationTime * 1000 / Operations`) | Not yet in `fsxn-monitoring-dashboard.yaml` | ⚠️ Metrics available, widget not implemented |
| **Performance: Network Utilization** | CloudWatch `NetworkThroughputUtilization` | `fsxn-monitoring-dashboard.yaml` | ✅ |
| **Capacity: Storage Used** | CloudWatch `StorageUsed` + `StorageCapacityUtilization` | `fsxn-monitoring-dashboard.yaml` | ✅ |
| **Capacity: Alerts** | CloudWatch Alarm on `StorageCapacityUtilization` | `fsxn-monitoring-dashboard.yaml` (threshold alarm) | ⚠️ Dimension corrected; live alarm firing unverified |
| **Qtree: Quota Management** | ONTAP REST API `/storage/quota/rules` | CLI scripts / manual | ⚠️ Management via API, no GUI |
| **Qtree: Quota Monitoring** | Lambda → ONTAP REST API → CloudWatch Custom Metric | `qtree-quota-monitor.yaml` | ⚠️ Template implemented, operational publication unverified |
| **Qtree: Quota Alerts** | CloudWatch Alarm on `QtreeQuotaUsedPercent` | `qtree-quota-monitor.yaml` | ⚠️ Metric publication path implemented (code-inspected; operational publication unverified); shipped alarm not usable as shipped (see Qtree note below) |
| **Volume: Create/Delete/Resize** | FSx Console + ONTAP REST API | Demo templates + FSx Console (no general-purpose volume management template) | ⚠️ |
| **Snapshot: Create/Schedule** | FSx Backup + ONTAP REST API | `ontap_response.py` + FSx native | ✅ |
| **Snapshot: Restore** | FSx Console + ONTAP REST API | `restore-verification.yaml` (verify before restore) | ✅ |
| **NFS Export Management** | ONTAP REST API | `ontap_response.py` (export-policy deny rules for blocking) | ⚠️ Blocking only |
| **SMB Share Management** | ONTAP REST API | `ontap_response.py` (name-mapping deny for blocking) | ⚠️ Blocking only |
| **EMS Event Viewer** | CloudWatch Logs (syslog VPC EP) | `syslog-vpce-cloudwatch.yaml` | ✅ |
| **ARP Status** | EMS → Observability pipeline | 9 vendor integrations + EMS webhook | ✅ |
| **SnapMirror Management** | FSx Console + ONTAP REST API | Docs (manual procedure) | ⚠️ No automation |
| **QoS Policy** | ONTAP REST API | — | ❌ Out of scope |
| **Network (LIF/DNS)** | FSx Console + ONTAP REST API | — | ❌ Infrastructure management |
| **FPolicy Configuration** | ONTAP REST API | FPolicy server (Fargate) + scripts | ✅ |
| **Audit Configuration** | ONTAP CLI/REST API | Setup scripts + docs | ✅ |

---

## Workload Factory — Feature Coverage

| Workload Factory Feature | AWS-Native Equivalent | This Repo | Status |
|-------------------------|----------------------|-----------|:------:|
| **File System Creation Wizard** | FSx Console / CloudFormation | `demo-ad-environment.yaml` + templates | ✅ |
| **Cost Optimization Recommendations** | AWS Cost Explorer + CloudWatch metrics | — | ❌ Future |
| **FabricPool Tiering Recommendations** | CloudWatch capacity metrics + ONTAP tiering API | Docs (manual guidance) | ⚠️ |
| **Backup Management** | FSx Backup (AWS managed) | AWS native (no template needed) | ✅ |
| **Replication Configuration** | FSx Console + SnapMirror API | Docs (manual procedure) | ⚠️ |
| **Security Posture Scan** | AWS Security Hub + cfn-guard | `guard/rules/` + CI | ✅ |
| **GenAI Data Preparation** | Bedrock Knowledge Bases + S3 AP | S3AP Patterns repo | ✅ |
| **Data Migration** | AWS DataSync | — | ❌ Out of scope |
| **Compliance Templates** | CloudFormation + cfn-guard | `compliance-evidence-pack.md` | ✅ |

---

## DII Storage Workload Security — Feature Coverage

> **DII** = Data Infrastructure Insights (formerly Cloud Insights). Storage Workload Security is its ransomware detection and response module.

> **Response latency note**: DII blocks users in-band (sub-second, integrated into ONTAP data path). This repository's Lambda-based response takes 2–5 seconds for a single containment action (block + snapshot), or 25–50 minutes for full recovery-point verification (dominated by FSx for ONTAP sync delay). Choose DII when sub-second response is a hard requirement; choose this approach when your detection is already in your SIEM and you need customizable response logic.

| DII Feature | AWS-Native Equivalent | This Repo | Status |
|------------|----------------------|-----------|:------:|
| **ML-Based Anomaly Detection** | ONTAP ARP/AI (built-in) + SIEM ML | EMS → Datadog/9 vendors (pipeline only; ML detection config is your SIEM's responsibility) | ✅ Pipeline |
| **User Auto-Block** | ONTAP REST API (name-mapping deny) | `ontap_response.py` + `automated-response.yaml` | ✅ E2E verified |
| **IP Auto-Block** | ONTAP REST API (export-policy) + VPC NACL | `ontap_response.py` + `automated-response.yaml` | ✅ E2E verified |
| **Protective Snapshot** | ONTAP REST API | `ontap_response.py` `create_snapshot` | ✅ E2E verified |
| **Session Disconnect** | ONTAP REST API (CIFS sessions) | `ontap_response.py` `disconnect_smb_sessions` | ✅ E2E verified |
| **Forensics Dashboard** | Vendor dashboard (Datadog / Grafana / Splunk / Elastic / Sumo Logic) | Per-vendor dashboard definitions — see table below | ✅ Reproducible |
| **User Activity Timeline** | Timeseries / Log panel | Forensics dashboard (all supported vendors) | ✅ |
| **File Access Audit Trail** | Log stream / Discover / Log Explorer | Forensics dashboard (all supported vendors) | ✅ |
| **Affected Volume Visualization** | TopList / Bar chart | Forensics dashboard (all supported vendors) | ✅ |
| **Alert Severity Distribution** | Aggregation widget | Forensics dashboard (all supported vendors) | ✅ |
| **Recovery Point Verification** | Step Functions (FlexClone + S3 AP + Scan) | `restore-verification.yaml` | ✅ E2E verified |
| **Auto-Unblock (TTL)** | EventBridge Scheduler | `automated-response-ttl.yaml` | ✅ |
| **Multi-SVM Containment** | Step Functions fan-out / multi-SVM CLI | `automated-response-multi-svm-cli.sh` | ✅ |

---

### Forensics Dashboard — Per-Vendor Reference

All vendors that receive audit/EMS/FPolicy logs can build equivalent forensics views. The table below shows what's already provided in this repository:

| Vendor | Format | Artifact | Deploy Method |
|--------|--------|----------|---------------|
| **Datadog** | Dashboard JSON | [`integrations/datadog/dashboards/forensics-dashboard.json`](../../integrations/datadog/dashboards/) | Datadog REST API (`POST /api/v1/dashboard`) |
| **Grafana** | Dashboard JSON (Loki + LogQL) | [`integrations/grafana/dashboards/forensics-investigation.json`](../../integrations/grafana/dashboards/forensics-investigation.json) | [`deploy-forensics-dashboard.sh`](../../integrations/grafana/scripts/deploy-forensics-dashboard.sh) or UI import |
| **Elastic** | Kibana NDJSON (Saved Searches + Dashboard) | [`integrations/elastic/dashboards/forensics-investigation.ndjson`](../../integrations/elastic/dashboards/) | Kibana Saved Objects API or UI import |
| **Splunk** | SPL Saved Searches + Dashboard Studio | [`integrations/splunk-serverless/searches/`](../../integrations/splunk-serverless/searches/) | Splunk UI or REST API |
| **Sumo Logic** | Dashboard JSON (DashboardV2) | [`integrations/sumo-logic/dashboards/forensics-investigation.json`](../../integrations/sumo-logic/dashboards/) | Sumo Logic Content API (`POST /v2/dashboards`) |
| **New Relic** | Dashboard JSON (NRQL) | [`integrations/new-relic/dashboards/forensics-investigation.json`](../../integrations/new-relic/dashboards/) | NerdGraph API (`dashboardCreate` mutation) |
| **Dynatrace** | Dashboard JSON (DQL) | [`integrations/dynatrace/dashboards/forensics-investigation.json`](../../integrations/dynatrace/dashboards/) | Dynatrace Dashboards API (`POST /api/config/v1/dashboards`) |

> **Vendor-neutral principle**: The forensics capability is not tied to any single vendor. Choose the vendor where your audit logs already land. The investigation workflow (user timeline → all activity → IP drill-down → file entity history) is identical across all vendors — only the query language differs.

> **Honeycomb note**: Honeycomb is optimized for distributed tracing and high-cardinality event exploration. While it receives FSx for ONTAP audit logs via the pipeline, it does not provide traditional dashboard/saved-search artifacts for forensic investigation workflows. Use Honeycomb for tracing correlation; use one of the 7 vendors above for forensics dashboards.

> **CrowdStrike Falcon LogScale note**: CrowdStrike Falcon LogScale receives FSx for ONTAP audit logs via HEC (see [vendor-comparison.md](vendor-comparison.md)), but this repository does not yet include a forensics dashboard artifact for it — the 7 vendors in the table above are what's currently provided, not the full list of vendors this repository supports overall.

---

## Summary: Coverage Status

| Product | Features Mapped | ✅ Covered | ⚠️ Partial | ❌ Out of Scope |
|---------|:--------------:|:----------:|:----------:|:--------------:|
| System Manager | 21 | 10 | 9 | 2 |
| Workload Factory | 9 | 5 | 2 | 2 |
| DII SWS | 13 | 13 | 0 | 0 |

**Key insight**: Security/incident-response features (DII equivalent) are **100% covered**. Operations monitoring (System Manager equivalent) is **48% fully covered + 43% partial** (10/21 and 9/21 of the mapped features, respectively) — partial items are the latency widget not yet in the dashboard, the capacity alarm (dimension corrected, live firing unverified), qtree quota management (API only), qtree quota monitoring and alerts (template implemented, operational publication unverified, shipped alarm not usable as shipped), security-blocking-only implementations of export/share management, demo-only volume templates, and manual-only SnapMirror procedures. The remaining **10%** (QoS, LIF/DNS) are infrastructure-management tasks suited to the FSx Console.

> **Reading this table correctly**: "100% covered" describes feature-level parity for the specific containment/detection-response actions this repository implements — it is not a claim that this approach is a superior or complete substitute for DII. DII's ML detection, agent-based collection, and vendor-managed operations are capabilities this repository doesn't build from scratch; the coverage number reflects that this repository's narrower, AWS-native mechanism reaches the same *containment actions* via a different path. See [How to Choose](#how-to-choose) below for which context favors which approach.

---

## Deployment Quick Reference

| Capability | Template | Deploy Order |
|-----------|----------|:------------:|
| Performance & Capacity | `fsxn-monitoring-dashboard.yaml` | Any time |
| Qtree Quota Monitoring | `qtree-quota-monitor.yaml` | After VPC EP exists |
| Incident Response | `automated-response.yaml` | Tier 2 |
| Recovery Verification | `restore-verification.yaml` | After Tier 2 |
| Forensics | Per-vendor dashboard JSON/queries (see table above) | After log pipeline |

### Qtree Quota Metrics — Identifying the Offending Qtree

The threshold alarm shipped in `qtree-quota-monitor.yaml` is scoped to the `SvmName` dimension alone, so it matches none of the three-dimension series the Lambda emits and is not usable as shipped (see the alarm note in [monitoring-design.md](monitoring-design.md)). Until it is corrected, find an over-quota qtree by reading the `FSxONTAP/Qtree` metrics directly. The Lambda publishes `QtreeQuotaUsedPercent` with the full dimension set `SvmName` + `VolumeName` + `QtreeName`, and CloudWatch identifies a metric by its complete dimension set — so a query scoped to `SvmName` alone matches none of the emitted series. Enumerate the complete identities first, then query each one.

Step 1 — list every emitted qtree identity (full `SvmName`/`VolumeName`/`QtreeName` dimensions):

```bash
aws cloudwatch list-metrics \
  --namespace "FSxONTAP/Qtree" \
  --metric-name "QtreeQuotaUsedPercent" \
  --dimensions Name=SvmName,Value=<your-svm-name> \
  --query 'Metrics[].Dimensions' \
  --output json
```

Step 2 — query each complete identity for its recent usage (substitute the `VolumeName`/`QtreeName` pairs from Step 1; add one query object per qtree):

```bash
aws cloudwatch get-metric-data \
  --metric-data-queries '[{
    "Id": "q1",
    "MetricStat": {
      "Metric": {
        "Namespace": "FSxONTAP/Qtree",
        "MetricName": "QtreeQuotaUsedPercent",
        "Dimensions": [
          {"Name": "SvmName", "Value": "<your-svm-name>"},
          {"Name": "VolumeName", "Value": "<volume-name>"},
          {"Name": "QtreeName", "Value": "<qtree-name>"}
        ]
      },
      "Period": 300,
      "Stat": "Maximum"
    }
  }]' \
  --start-time "$(date -u -v-1H +%Y-%m-%dT%H:%M:%S)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%S)"
```

---

## How to Choose

| Your situation | Recommendation |
|---------------|----------------|
| Already invested in AWS observability (Datadog, Grafana, Splunk, etc.) + want storage-layer IR | **This approach** — extends your existing stack to storage-layer containment |
| Need turnkey ML-based anomaly detection without SIEM configuration | **DII Storage Workload Security** — built-in per-user baselines, no external SIEM needed |
| Need GUI-driven daily storage operations (create volumes, manage shares) | **FSx Console + ONTAP System Manager** — purpose-built for interactive administration |
| Need automated infrastructure provisioning at scale | **CloudFormation/Terraform** — IaC is the right pattern regardless of monitoring choice |
| Need all of the above across a large fleet | Consider a hybrid: this approach for IR + DII for detection + FSx Console for ad-hoc operations |

> **There is no single tool that does everything.** The matrix above maps what this repository covers. Use FSx Console for interactive operations, DII if you want vendor-managed ML detection, and this repository when your detection is already in your SIEM and you want automated containment + forensic evidence without additional licensing.

> **Cost model difference**: DII uses per-TB licensing (contact NetApp for pricing). This repository's operational cost is primarily Lambda invocations + CloudWatch metrics + VPC Endpoints (~$15–30/month baseline for a typical single-SVM deployment, illustrative only — not yet broken down component-by-component in this repo's docs). This is not an apples-to-apples TCO comparison — the AWS-side figure is infrastructure cost only and excludes the ongoing work of tuning detection rules, investigating false positives, and running quarterly drills, all of which apply to this approach and to DII alike. DII's licensing figure would similarly need to be weighed against its own setup and operational overhead. See [Cost Model](cost-model.md) for the log-shipping-pipeline cost breakdown this repository does document (a related but distinct cost surface from the incident-response stacks discussed here).

---

## Related Documents

- [Adoption Playbook — Observability](https://github.com/Yoshiki0705/FSx-for-ONTAP-Adoption-Playbook/blob/main/docs/en/domains/observability/README.md) — **route selection, and the limits of what this matrix maps.** The matrix answers "can this capability be reached AWS-natively"; the hub answers "should the collection route be this one at all", and records that the Harvest route already covers `ONTAP: Qtree`
- [NetApp FSx-ONTAP-monitoring (CloudWatch-Monitoring-FSx subtree)](https://github.com/NetApp/FSx-ONTAP-monitoring/tree/main/CloudWatch-Monitoring-FSx) — the NetApp-published CloudWatch monitoring reference for FSx for ONTAP; like the templates above it is a CloudFormation-based serverless solution. Alongside NetApp Harvest, the NetApp reference covers a broader scope in one region-wide stack (volume/LUN/SnapMirror/EMS) while this repo's templates cover a narrower, fixed scope split across separate stacks — neither replaces the other.
- [Deployment Guide](deployment-guide.md) — Full stack deployment paths and VPC Endpoint management
- [Cyber Resilience Capability Map](cyber-resilience-capability-map.md) — NIST CSF 2.0 mapping
- [Automated Response Guide](automated-response-guide.md) — DII-equivalent containment actions
- [Verified Recovery Point Guide](verified-recovery-point-guide.md) — Step Functions verification workflow
- [Compliance Evidence Pack](compliance-evidence-pack.md) — ISMAP/FISC/SOC2 audit evidence template
