# CloudWatch Monitoring Verification Results (Dashboard Template and Terraform Module)

🌐 [日本語](../ja/verification-results-cloudwatch-monitoring.md) | **English** (this page)

## Overview

On 2026-10-05 (UTC), the CloudFormation dashboard template `shared/templates/fsxn-monitoring-dashboard.yaml` and the Terraform module `terraform/fsxn-monitoring-dashboard/` were deployed against one real Amazon FSx for NetApp ONTAP file system: first generation, `SINGLE_AZ_1`, one HA pair. Every dashboard series returned data and every alarm left INSUFFICIENT_DATA and reached OK. The two Terraform per-volume alarms were also driven to ALARM and back to OK. The file-system capacity alarm (CloudFormation and Terraform) could not be driven to ALARM, because its lowest allowed threshold (50%) is above the file system's observed utilization (about 3.5%); see [F1](#findings). No defect was found in the template or the module.

| Item | Value |
|------|-------|
| Verification date | 2026-10-05T16:03Z to 2026-10-06T00:06Z (UTC), including a pause of about 7.5 hours (see Environment Information) |
| Verification environment | Test environment (`ap-northeast-1`), sample run on an idle file system |
| Scope | AWS-side CloudWatch metrics, alarms, and dashboards only. No ONTAP REST call was made in this run |
| Result | Deploy, series, and OK evaluation: pass. Per-volume ALARM path: pass. File-system capacity ALARM path: not achievable (F1) |

The values below show that the series exist and that the alarms evaluate them on this file system. They say nothing about behavior under load, on second-generation file systems, or on file systems with more than one HA pair.

---

## Environment Information

| Item | Value |
|------|-------|
| AWS Region | `ap-northeast-1` |
| File system | `fs-0123456789abcdef0` (placeholder), AVAILABLE |
| Deployment type / HA pairs | `SINGLE_AZ_1` (first generation) / 1 |
| Throughput capacity / SSD capacity | 128 MBps / 1024 GiB |
| Target volume | `fsvol-0123456789abcdef0` (placeholder), non-root, Lifecycle `CREATED`, on `svm-0123456789abcdef0` (placeholder) |
| Source revision | `1a2414b` (main, after #105). Tracked files were not modified |
| Terraform / provider | Terraform v1.15.8 (darwin_arm64), `hashicorp/aws` v6.67.0 from the lock file (`terraform init -lockfile=readonly`) |
| Other tools | AWS CLI, boto3 1.43.93 (read-only CloudWatch calls) |
| Authentication | AWS IAM Identity Center (SSO) session |
| ONTAP version | Not recorded. No ONTAP REST call was made in this run |
| Pre-existing names | Before deployment: no stack `fsxn-verify-monitoring-dashboard`, 0 alarms and 0 dashboards with prefix `fsxn-verify` |

The SSO session expired at about 2026-10-05T16:11Z, during the first alarm-history read. The run paused without any workaround and resumed at 2026-10-05T23:47:34Z after a new sign-in. Only the CloudFormation stack existed during the pause.

`list-metrics` returned 760 series for the file system. The sets the two artifacts use all exist: `CPUUtilization`, `FileServerDiskIopsUtilization`, `FileServerDiskThroughputUtilization`, `NetworkThroughputUtilization`, `DataReadBytes`/`DataWriteBytes`, `DataReadOperations`/`DataWriteOperations`, `NetworkSentBytes`/`NetworkReceivedBytes`, and `StorageUsed` with `FileSystemId` only, and `StorageCapacityUtilization` with `FileSystemId` + `StorageTier=SSD` + `DataType=All`. No `StorageCapacityUtilization` series exists with `FileSystemId` alone. At volume level, `StorageCapacityUtilization` exists as `FileSystemId` + `VolumeId` (and with `StorageTier=SSD` + `DataType=All` added), and `FilesUsed` and `FilesCapacity` exist as `FileSystemId` + `VolumeId`.

> **Dimension note**: The absence of a `FileSystemId`-only `StorageCapacityUtilization` series on this file system is consistent with the dimension correction in #105. Before that change, the capacity alarm and widget selected `FileSystemId` alone and would have matched no series here.

---

## What Was Deployed

Both artifacts targeted the same file system and were deployed one after the other. Neither set a notification email, so neither created an SNS topic.

### CloudFormation dashboard stack

Stack `fsxn-verify-monitoring-dashboard` from `shared/templates/fsxn-monitoring-dashboard.yaml`, with `CapacityThresholdPercent=80`, `FileSystemName=verify-fs`, and no `NotificationEmail`. It created one dashboard (`fsxn-verify-monitoring-dashboard-verify-fs`) and the two alarms the template always creates, `StorageCapacityAlarm` and `ThroughputUtilizationAlarm`. No SNS topic was created.

```bash
aws cloudformation deploy \
  --template-file shared/templates/fsxn-monitoring-dashboard.yaml \
  --stack-name fsxn-verify-monitoring-dashboard \
  --parameter-overrides \
    FileSystemId=fs-0123456789abcdef0 \
    FileSystemName=verify-fs \
    CapacityThresholdPercent=80 \
  --region ap-northeast-1
```

### Terraform module

The module was applied from a local root configuration with `name_prefix = "fsxn-verify-tf"`. All three opt-in file-server alarms were enabled with `file_server_names` empty, so they select `FileSystemId` only, which is the first-generation form. One volume was listed in `volume_ids`. No `notification_email` was set, so no SNS topic was created.

```hcl
file_system_id                           = "fs-0123456789abcdef0"
file_system_name                         = "verify-fs"
name_prefix                              = "fsxn-verify-tf"
enable_cpu_utilization_alarm             = true
enable_disk_iops_utilization_alarm       = true
enable_disk_throughput_utilization_alarm = true
file_server_names                        = []
volume_ids                               = ["fsvol-0123456789abcdef0"]
```

`terraform plan` reported 8 to add. `terraform apply` created 1 dashboard (`fsxn-verify-tf-verify-fs`) and 7 alarms:

| Alarm (resource) | Source | Exercised |
|------------------|--------|-----------|
| `storage_capacity` | Parity with the template | OK evaluation |
| `network_throughput` | Parity with the template | OK evaluation |
| `file_server["cpu_utilization"]` | Opt-in | OK evaluation |
| `file_server["disk_iops_utilization"]` | Opt-in | OK evaluation |
| `file_server["disk_throughput_utilization"]` | Opt-in | OK evaluation |
| `volume_capacity["fsvol-0123456789abcdef0"]` | Opt-in, `volume_ids` | OK, ALARM, and back to OK |
| `volume_inode["fsvol-0123456789abcdef0"]` | Opt-in, `volume_ids` (metric math `100 * FilesUsed / FilesCapacity`) | OK, ALARM, and back to OK |

---

## Test Results Summary

| # | Check | Result | Time (UTC) |
|---|-------|--------|------------|
| P4-1 | CloudFormation stack deploy | ✅ PASS. 1 dashboard, 2 alarms, no SNS | 2026-10-05T16:03:06Z → 16:03:43Z |
| P4-2 | Every dashboard widget series returns datapoints (3 h window) | ✅ PASS. 9 of 9 series non-zero | 16:10:56Z, again 23:55:05Z |
| P4-3 | `StorageCapacityAlarm` INSUFFICIENT_DATA → OK | ✅ PASS | 16:04:09Z |
| P4-4 | `ThroughputUtilizationAlarm` INSUFFICIENT_DATA → OK | ✅ PASS | 16:04:26Z |
| P4-5 | Update `CapacityThresholdPercent=1` | ⛔ Rejected by the template (`MinValue` 50). Stack unchanged at 80 | 23:47:43Z |
| P4-6 | Update to 50 (lowest allowed); `StorageCapacityAlarm` → ALARM | ⚠️ Not achievable. Threshold 50.0 applied, alarm stayed OK at 3.45–3.51% (F1) | 23:47:54Z → 23:48:30Z, re-read after 90 s |
| P4-7 | Restore 80; both alarms OK | ✅ PASS | 23:54:25Z → 23:55:02Z |
| P5-1 | `terraform init -lockfile=readonly` | ✅ PASS (`hashicorp/aws` v6.67.0) | 23:55:37Z |
| P5-2 | `terraform plan` | ✅ PASS. 8 to add, 0 to change, 0 to destroy | 23:55:37Z |
| P5-3 | `terraform apply` | ✅ PASS. 8 added (1 dashboard, 7 alarms) | 23:55:54Z → 23:55:57Z |
| P5-4 | All 7 alarms INSUFFICIENT_DATA → OK | ✅ PASS (all OK by 23:57:16Z) | See [Alarm State Transitions](#alarm-state-transitions) |
| P5-5 | Dashboard widgets return data (3 h window) | ✅ PASS. 9 of 9 series non-zero | 2026-10-06T00:00:26Z |
| P5-6 | `capacity_threshold_percent=1` | ⛔ Rejected by module validation (range 50–95), `plan` exit 1 | 00:00:34Z |
| P5-7 | Lowest allowed thresholds: capacity 50, volume capacity 1, volume inode 1 → ALARM | ⚠️ Partial. Both per-volume alarms reached ALARM. The file-system capacity alarm took threshold 50 and stayed OK at 3.45% (F1) | apply 00:00:42Z → 00:00:46Z |
| P5-8 | Restore defaults; all alarms OK | ✅ PASS. All 7 OK at 00:03:56Z; follow-up `plan -detailed-exitcode` exit 0, no changes | apply 00:02:09Z → 00:02:17Z |
| P6 | Cleanup, with re-read about 65 s later | ✅ PASS for every resource | 00:04:08Z → 00:06:10Z |

---

## Alarm State Transitions

From `describe-alarm-history` (`StateUpdate`), UTC. Volume IDs in alarm names are replaced with the placeholder.

| Alarm | Transition | Time | Values in the state reason |
|-------|-----------|------|----------------------------|
| `fsxn-verify-monitoring-dashboard-capacity-high` | INSUFFICIENT_DATA → OK | 2026-10-05T16:04:09Z | 3.49, 3.49, 3.49, not > 80 |
| `fsxn-verify-monitoring-dashboard-throughput-high` | INSUFFICIENT_DATA → OK | 2026-10-05T16:04:26Z | 0.337, 0.273, 0.271, not > 80 |
| `fsxn-verify-tf-capacity-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:56:34Z | 3.45 ×3, not > 80 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-capacity-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:56:42Z | 20.23 ×3, not > 80 |
| `fsxn-verify-tf-cpu-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:56:51Z | 13.64, 12.81, 13.25, not > 80 |
| `fsxn-verify-tf-throughput-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:56:55Z | 0.280, 0.282, 0.315, not > 80 |
| `fsxn-verify-tf-disk-iops-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:57:03Z | 0.367, 0.318, 0.414, not > 80 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-inode-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:57:11Z | 34.908 ×3, not > 80 |
| `fsxn-verify-tf-disk-throughput-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:57:16Z | 0.367, 0.337, 0.393, not > 80 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-inode-high` | OK → ALARM | 2026-10-06T00:01:45Z | 34.908 ×3, > 1 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-capacity-high` | OK → ALARM | 2026-10-06T00:01:46Z | 20.23 ×3, > 1 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-inode-high` | ALARM → OK | 2026-10-06T00:03:20Z | 34.908 ×3, not > 80 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-capacity-high` | ALARM → OK | 2026-10-06T00:03:47Z | 20.23 ×3, not > 80 |

Every alarm reached OK within about 1.5 minutes of creation, because CloudWatch evaluated the three 300-second periods from existing metric history. No alarm stayed in INSUFFICIENT_DATA. The CloudFormation capacity alarm and the Terraform file-system capacity alarm have no ALARM entry (F1).

---

## Dashboard Widget Datapoints

`get-metric-data` over the last 3 hours, for the 9 metric series that both dashboards draw. Expression rows (MB/s and IOPS conversions) are computed from these series and are not counted separately. Status was `Complete` for every query.

| Widget | Metric (stat / period) | Dimensions | CFN 13:10–16:10Z | CFN 20:55–23:55Z | TF 21:00–00:00Z | Range seen |
|--------|------------------------|------------|:---:|:---:|:---:|------------|
| Network Throughput (MB/s) | `DataReadBytes` Sum / 60 | `FileSystemId` | 180 | 180 | 180 | 0–3098 bytes/min |
| Network Throughput (MB/s) | `DataWriteBytes` Sum / 60 | `FileSystemId` | 180 | 180 | 180 | 0–1128 bytes/min |
| IOPS (Operations/s) | `DataReadOperations` Sum / 60 | `FileSystemId` | 180 | 180 | 180 | 0–6 /min |
| IOPS (Operations/s) | `DataWriteOperations` Sum / 60 | `FileSystemId` | 180 | 180 | 180 | 0–1 /min |
| Network Throughput Utilization (%) | `NetworkThroughputUtilization` Average / 60 | `FileSystemId` | 180 | 179 | 179 | 0.217–0.540% |
| Storage Capacity Utilization (%) | `StorageCapacityUtilization` Average / 300 | `FileSystemId`, `StorageTier=SSD`, `DataType=All` | 36 | 36 | 36 | 3.45–3.51% |
| Network Sent/Received (MB/s) | `NetworkSentBytes` Sum / 60 | `FileSystemId` | 180 | 179 | 179 | 1.95e7–4.86e7 bytes/min |
| Network Sent/Received (MB/s) | `NetworkReceivedBytes` Sum / 60 | `FileSystemId` | 180 | 179 | 179 | 1.53e7–3.81e7 bytes/min |
| Storage Used (GB) | `StorageUsed` Average / 300 | `FileSystemId` | 36 | 36 | 36 | 3.58e9–3.79e9 bytes |

179 instead of 180 is the most recent 1-minute bucket, not yet published at query time. No series returned zero datapoints.

> **Load note**: The file system was idle and no load was generated. These counts show that the series exist and are drawn; they are not performance figures.

---

## Findings

| # | Finding | Kind | Effect on this record |
|---|---------|------|-----------------------|
| F1 | The capacity threshold range blocks an ALARM-path test on a lightly used file system. `CapacityThresholdPercent` (CloudFormation, `MinValue` 50 / `MaxValue` 95) and `capacity_threshold_percent` (Terraform, the same 50–95 validation) both rejected 1. At 50, the lowest allowed value, utilization of 3.45–3.51% cannot cross the threshold | Verification limit, not a code defect | OK → ALARM → OK is **not verified** for `StorageCapacityAlarm` and Terraform `storage_capacity`. What is verified for them: the dimension set returns data, and the alarm evaluates it to OK. The ALARM path was verified on the two per-volume alarms, whose thresholds accept 1–100. `set-alarm-state` was not used, because it exercises notification wiring, not metric evaluation |
| F2 | After a threshold-only change that does not cross state (CloudFormation at 50), CloudWatch adds no history entry, and the alarm's `StateReason` keeps the text of the last transition ("threshold (80.0)"). The `Threshold` field reads 50.0 | CloudWatch behavior, not a code defect | Evaluation at 50 is inferred from the absence of ALARM, not observed directly |
| O1 | `shared/scripts/preflight-check.sh` does not check for the `com.amazonaws.<region>.monitoring` interface endpoint, which `qtree-quota-monitor.yaml` needs for `PutMetricData` in a VPC without a NAT gateway, and no preflight profile covers the qtree stack | Observation from reading the script during preparation, not exercised | None on the dashboard results. Recorded for the qtree verification that has not run |

Defects in the dashboard template or the Terraform module: none found. All 9 dashboard series and all 9 alarm metric sets (2 CloudFormation, 7 Terraform) matched existing series and returned data on this file system.

---

## What Remains Unverified

| Item | Status | Reason |
|------|--------|--------|
| `shared/templates/qtree-quota-monitor.yaml` | Not run, entirely | In a separate attempt earlier the same day (2026-10-05T15:44:32Z), the single read-only credential check against the ONTAP management endpoint returned HTTP 401, so the run stopped before any qtree resource was deployed. Per-qtree metric publication and `QtreeQuotaAlarm` firing remain unverified |
| ALARM path of the file-system capacity alarm (CloudFormation and Terraform) | Not verified | F1 |
| Second-generation file systems (`file_server_names`, `FileServer` and `Aggregate` dimensions) | Not run | The test file system is first generation |
| File systems with more than one HA pair | Not run | The test file system has one HA pair |
| SNS notification delivery | Not exercised | No `NotificationEmail` / `notification_email` was set, so no topic, subscription, or alarm/OK action existed |
| Latency widget | Not applicable | The dashboard does not implement a latency widget |
| Behavior under load | Not run | Idle file system, no load generated |

---

## Cleanup

| Step | Result | Time (UTC) |
|------|--------|------------|
| `terraform plan -detailed-exitcode` before destroy | Exit 0, no changes | 2026-10-06T00:04Z |
| `terraform destroy -auto-approve` | Exit 0, 8 destroyed; `terraform state list` empty | 00:04:08Z → 00:04:13Z |
| `aws cloudformation delete-stack` + `wait stack-delete-complete` | Both exit 0; `list-stacks` shows DELETE_COMPLETE | 00:04:20Z → 00:04:51Z |
| Re-read of every resource | Stack does not exist; both dashboards `ResourceNotFound`; 0 dashboards and 0 alarms (metric and composite) with either prefix; each of the 9 alarm names returns 0; 0 SNS topics | 00:06:10Z (about 65 s after stack deletion finished) |
| Local Terraform working directory (state, plan files, tfvars) | Removed | After the re-read |

No custom metrics were emitted: every metric reference in both artifacts uses namespace `AWS/FSx`, neither artifact contains a Lambda function or `PutMetricData`, and the run created only CloudWatch alarms and dashboards. No VPC endpoint, security group, IAM, volume, or Lambda resource was created or changed. The CloudFormation stack existed for about 8 hours including the SSO pause; the Terraform resources existed for about 8 minutes.

---

## Overall Judgment

| Item | Value |
|------|-------|
| Judgment | ✅ Deployment, series selection, and OK evaluation verified for both artifacts on a first-generation, single-HA-pair file system. Per-volume ALARM path verified (Terraform). File-system capacity ALARM path not verified (F1) |
| Passing checks | 12 of 16 (P4-1–P4-4, P4-7, P5-1–P5-5, P5-8, P6) |
| Partial or not achievable | 2 of 16 (P4-6, P5-7), both from F1 |
| Rejected as designed | 2 of 16 (P4-5, P5-6): out-of-range threshold requests refused by the template and the module |
| Defects found | None |

---

## Related Documents

- [Monitoring Design](monitoring-design.md): the dashboard template and the Terraform T1 module, with confidence tiers that cite this record
- [AWS-Native Alternative Matrix](native-alternative-matrix.md): System Manager view → CloudWatch metric → template mapping
- [Terraform module: fsxn-monitoring-dashboard](../../terraform/fsxn-monitoring-dashboard/README.md): inputs, outputs, and verification status
- [CloudWatch Log Alarm](cloudwatch-log-alarm.md): the separate log-alarm template and its 2026-07-02 E2E record
