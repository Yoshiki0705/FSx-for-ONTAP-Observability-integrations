# CloudWatch Monitoring Verification Results (Dashboard Template, Terraform Module, and Qtree Quota Monitor)

🌐 [日本語](../ja/verification-results-cloudwatch-monitoring.md) | **English** (this page)

## Overview

This page records three runs. The 2026-10-05 run of the dashboard template and the Terraform module is described first. The first 2026-10-06 run of the qtree quota monitor stopped after one successful poll and is kept as history in [Qtree Quota Monitor Run on 2026-10-06](#qtree-quota-monitor-run-on-2026-10-06). The re-run later that day completed 4 polls and drove `QtreeQuotaAlarm` from OK to ALARM and back to OK: [Qtree Quota Monitor Re-run on 2026-10-06](#qtree-quota-monitor-re-run-on-2026-10-06).

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

Defects in the dashboard template or the Terraform module: none found. All 9 dashboard series and all 9 alarm metric sets (2 CloudFormation, 7 Terraform) matched existing series and returned data on this file system.

---

## What Remains Unverified

| Item | Status | Reason |
|------|--------|--------|
| `shared/templates/qtree-quota-monitor.yaml` | Not run in this run | Out of scope for the 2026-10-05 run; no qtree resource was deployed. It was run separately on 2026-10-06: the first run stopped after one successful poll ([Qtree Quota Monitor Run on 2026-10-06](#qtree-quota-monitor-run-on-2026-10-06)), and the re-run completed ([Qtree Quota Monitor Re-run on 2026-10-06](#qtree-quota-monitor-re-run-on-2026-10-06)) |
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

## Qtree Quota Monitor Run on 2026-10-06

On 2026-10-06 (UTC), `shared/templates/qtree-quota-monitor.yaml` was deployed against a first-generation `SINGLE_AZ_1` FSx for ONTAP file system with one HA pair. The run stopped at 02:16:26Z, when ONTAP began answering the poller with HTTP 401; a 401 or 403 from ONTAP was one of the run's stop conditions. Before the stop, one scheduled poll succeeded. It published 5 datapoints to `FSxONTAP/Qtree`, each value matched the ONTAP quota report, and `QtreeQuotaAlarm` left INSUFFICIENT_DATA and reached OK on that datapoint. The planned threshold change that would drive `QtreeQuotaAlarm` to ALARM was not run. The failing polls that followed exercised the DLQ path end to end. Two template-level issues were found (no back-off on 401/403, and a stack-deletion order that leaves an orphan log group); see [Findings (Qtree Run)](#findings-qtree-run).

| Item | Value |
|------|-------|
| Verification date | 2026-10-06T01:56Z to 03:03Z (UTC) |
| Verification environment | Test environment (`ap-northeast-1`), sample run with one SVM, one test volume, and one qtree with a tree quota |
| Scope | Stack deployment, per-qtree and SVM-level metric publication, initial alarm evaluation, and cleanup. ONTAP REST calls were made from a bastion host to prepare the qtree and read the quota report |
| Result | Stopped. 1 of the 2 planned successful poll cycles completed before ONTAP returned HTTP 401. `QtreeQuotaAlarm` ALARM path not run |

The values below come from one poll on one qtree. They show that the series are published with the dimension sets the template documents and that the alarm evaluates the SVM-level series. They do not show behavior across cycles, at scale, or on second-generation or multi-HA-pair file systems.

### Environment and Deployment (Qtree Run)

| Item | Value |
|------|-------|
| AWS Region | `ap-northeast-1` |
| File system | `fs-0123456789abcdef0` (placeholder), `SINGLE_AZ_1` (first generation), 1 HA pair, 128 MBps |
| ONTAP version | NetApp Release 9.18.1P6 (`GET /api/cluster`) |
| SVM | `<svm-name>` (placeholder), NFS enabled |
| Template revision | `qtree-quota-monitor.yaml` from main at `54e4c5d` (#104), identical to the working tree |
| Test volume | `zz_mon_verify_qtree`, 1024 MiB, UNIX security style, snapshot policy none, tiering policy NONE. Created through the Amazon FSx for NetApp ONTAP management API (`aws fsx create-volume`) for this run and deleted afterward |
| Qtree and quota | Qtree `qt_mon_verify` with a tree quota rule, hard limit 104857600 bytes (100 MiB); quotas enabled on the volume |
| Data written | 60 MiB over a temporary NFSv3 mount from the bastion host, then unmounted. ONTAP quota report afterward: used 63168512 bytes, hard limit 104857600, `hard_limit_percent` 60. The mount worked without a policy change because the volume was assigned the SVM's `default` export policy, whose rule allows clients `0.0.0.0/0` with read/write and superuser access (test-environment configuration, see the security note below) |
| Lambda placement | The file system's subnet. Its route table sends `0.0.0.0/0` to an internet gateway and has no NAT gateway |
| CloudWatch route | A `com.amazonaws.ap-northeast-1.monitoring` interface endpoint (private DNS on), created for this run in the Lambda subnet |
| Secrets Manager route | An existing interface endpoint in the VPC, so `CreateSecretsManagerEndpoint=false` |
| Security group | The file system's existing security group for both the Lambda and the new endpoint. Its inbound rules allow all protocols from `0.0.0.0/0` and its egress allows all traffic (test-environment configuration, see the security note below), so 443 was already allowed; no security group or IAM change was made |
| ONTAP credential | The `fsxadmin` credential stored in Secrets Manager |
| Authentication | AWS IAM Identity Center (SSO) session |

> **Security note**: The security group and export policy above are the pre-existing configuration of a test environment, not recommended settings. Because both were permissive, this run shows that the monitor works when the network and NFS paths are open. It does not validate a least-privilege configuration. A least-privilege setup would allow only 443 from the Lambda's security group to the management interface and to the two interface endpoints, and an export policy limited to the clients that need to mount. That setup was not tested in this run.

`shared/scripts/preflight-check.sh --profile automated-response` exited 0 with one warning, for the existing Secrets Manager endpoint. The script has no profile for this template and does not check for the `monitoring` endpoint (QF4).

```bash
aws cloudformation create-stack \
  --stack-name fsxn-verify-qtree-quota \
  --template-body file://shared/templates/qtree-quota-monitor.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=OntapMgmtIp,ParameterValue=<management-ip> \
    ParameterKey=OntapCredentialsSecretArn,ParameterValue=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:<secret-name>-XXXXXX \
    ParameterKey=SvmName,ParameterValue=<svm-name> \
    ParameterKey=VpcId,ParameterValue=vpc-0123456789abcdef0 \
    ParameterKey=SubnetIds,ParameterValue=subnet-0123456789abcdef0 \
    ParameterKey=SecurityGroupId,ParameterValue=sg-0123456789abcdef0 \
    ParameterKey=PollIntervalMinutes,ParameterValue=5 \
    ParameterKey=QuotaThresholdPercent,ParameterValue=85 \
    ParameterKey=NotificationEmail,ParameterValue='' \
    ParameterKey=CreateSecretsManagerEndpoint,ParameterValue=false \
    ParameterKey=CaCertPath,ParameterValue='' \
    ParameterKey=CaCertLayerArn,ParameterValue='' \
  --region ap-northeast-1
```

The stack created the IAM role, the Lambda function, its log group (retention 30 days), the DLQ, the EventBridge schedule rule, and two alarms: `fsxn-verify-qtree-quota-quota-high` (`QtreeQuotaAlarm`) and `fsxn-verify-qtree-quota-dlq-depth`. `NotificationEmail` was empty, so no SNS topic was created.

### Check Results (Qtree Run)

| # | Check | Result | Time (UTC) |
|---|-------|--------|------------|
| Q0 | Preflight: endpoints, routes, security group, ONTAP read-only (cluster, SVM, quota reports and rules, volumes) | ✅ PASS. No tree quota existed on the SVM, so Q1 was needed | 01:56:09Z → 01:59:22Z |
| Q1 | Qtree preparation: volume, qtree, 100 MiB tree quota rule, quotas on, 60 MiB written | ✅ PASS | 02:00:04Z → 02:02:28Z |
| Q2-1 | `monitoring` interface endpoint | ✅ PASS. Available | 02:03:11Z → 02:04:26Z |
| Q2-2 | Stack creation | ✅ PASS. CREATE_COMPLETE | 02:04:00Z → 02:06:48Z |
| Q2-3 | Poll cycle 1: series published, values match the ONTAP report | ✅ PASS. 5 datapoints, all matching | 02:11:22Z |
| Q2-4 | Poll cycle 2 (the second required successful cycle) | ❌ FAIL. ONTAP HTTP 401 (stop condition) | 02:16:22Z |
| Q3 | `QuotaThresholdPercent` 85 → 50 to drive `QtreeQuotaAlarm` to ALARM at 60.24%, then restore | ⏹️ Not run (stopped before it) | — |
| Q4 | Cleanup, with re-read | ✅ PASS for every AWS resource. ONTAP-side read-back not possible (401) | 02:24:36Z → 03:03:54Z |

### Observed Metrics and Dimension Sets

Poll cycle 1 (02:11:22Z) was a cold start. The function logged `Found 2 qtree quota reports for SVM <svm-name> in 1 page(s)` and `Published 5 metric data points`, with a duration of 629.89 ms and a maximum memory of 95 MB. At initialization it logged the warning that TLS certificate verification is disabled (`cert_reqs=CERT_NONE`) and acceptable for a PoC only, because `CaCertPath` was empty.

`list-metrics` on `FSxONTAP/Qtree` then returned these dimension sets:

| Metric | Dimensions |
|--------|------------|
| `QtreeQuotaUsedPercent`, `QtreeQuotaUsedBytes`, `QtreeQuotaLimitBytes` | `SvmName`, `VolumeName=zz_mon_verify_qtree`, `QtreeName=qt_mon_verify` |
| `QtreeQuotaUsedPercentMax`, `QtreeQuotaReportTruncated` | `SvmName` only |

No series was published for the volume's default qtree (empty name, no hard limit), which ONTAP also returns as a tree quota report record.

| Metric | Published (one datapoint each) | ONTAP quota report (02:02:28Z) | Match |
|--------|-------------------------------|--------------------------------|-------|
| `QtreeQuotaUsedBytes` | 63168512 Bytes | used 63168512 | Yes |
| `QtreeQuotaLimitBytes` | 104857600 Bytes | hard limit 104857600 | Yes |
| `QtreeQuotaUsedPercent` | 60.2421875 Percent | 63168512 / 104857600 × 100 = 60.2421875 (ONTAP's `hard_limit_percent` shows 60) | Yes |
| `QtreeQuotaUsedPercentMax` | 60.2421875 Percent | Maximum over 1 qtree | Yes |
| `QtreeQuotaReportTruncated` | 0 Count | 1 page, no next link | Yes |

> **Pagination note**: This was the single-page case: 2 records, one page, no next link, so `QtreeQuotaReportTruncated` was 0. Following a next link, the 50-page cap, and the truncation value 1 were not exercised.

### Alarm State Transitions (Qtree Stack)

From `describe-alarm-history` (`StateUpdate`) and `describe-alarms`, UTC.

| Alarm | Transition | Time | Values in the state reason |
|-------|-----------|------|----------------------------|
| `fsxn-verify-qtree-quota-dlq-depth` | INSUFFICIENT_DATA → OK | 02:05:51Z | Initial evaluation |
| `fsxn-verify-qtree-quota-quota-high` | INSUFFICIENT_DATA → OK | 02:12:50Z | 1 datapoint, 60.2421875, not > 85 |
| `fsxn-verify-qtree-quota-dlq-depth` | OK → ALARM | 02:22:51Z | 1 datapoint, 1.0, > 0 |

`QtreeQuotaAlarm` reached OK on a single datapoint although `EvaluationPeriods` is 2 (QF8). It has no ALARM entry, because Q3 was not run.

The DLQ path was verified end to end as a side effect of the stop: the failed invocation at 02:16:22Z was retried twice asynchronously (02:17:19Z, 02:19:12Z), a message reached the DLQ at 02:19:16Z with the error text as its attribute, and the DLQ-depth alarm moved to ALARM at 02:22:51Z. There was no SNS action, because `NotificationEmail` was empty.

### Poller Stop on ONTAP HTTP 401

| Time (UTC) | Invocation | Result |
|------------|-----------|--------|
| 02:11:22Z | Cycle 1, cold start | Success (0.63 s for the ONTAP call) |
| 02:16:22Z | Cycle 2, same warm container and cached credential as cycle 1 | HTTP 401 |
| 02:17:19Z, 02:19:12Z | Asynchronous retries of cycle 2 | HTTP 401; DLQ message at 02:19:16Z |
| 02:21:22Z | Cycle 3, warm container | HTTP 401 |
| 02:22:24Z | Retry in a new container that read the secret again | HTTP 401 |
| 02:24:42Z | Retry | HTTP 401 |

Each 401 response took about 4.1–4.5 seconds. The log lines carry only the request path and the status; no password or secret value was logged.

Read-only diagnosis, without further ONTAP login attempts after the stop:

- The secret's value was last changed before the run (01:48:18Z) and did not change during it.
- The last `fsxadmin` password reset through the FSx for ONTAP management API (`UpdateFileSystem`) was requested at 01:41:33Z and completed. CloudTrail shows no other `UpdateFileSystem` call between 01:00Z and 03:00Z.
- So the credential that worked from the bastion host (01:58Z to 02:02Z) and from the poller (02:11:23Z) was rejected from 02:16:26Z, with no reset through the FSx for ONTAP management API and no secret change in between.
- Besides this run's principals, one further principal read the same secret at 02:22:30Z. What it connects to was not determined.

The cause of the 401 was not determined. Two hypotheses remain, neither confirmed from the ONTAP side:

- Account lockout by another client. Another function in the same VPC was invoked at about 02:02Z, twice at about 02:06Z, and at about 02:12Z: after the 01:41Z password reset and before the first 401 at 02:16:22Z. Its 02:06Z invocations each took about 4 seconds, the same duration as the poller's 401 responses. This is consistent with that function authenticating with the pre-reset password and `fsxadmin` being locked by repeated failed logins.
- A password change made inside ONTAP, outside the FSx for ONTAP management API.

Confirming either needs an ONTAP-side read (login and lockout state, or EMS events) or a new password reset through the FSx for ONTAP management API. Neither was done in this run. A re-run later the same day throttled that other function, then reset the password, and completed 4 polls with no 401; see [Credential Handling Before the Re-run](#credential-handling-before-the-re-run).

> **Credential-sharing note**: Any client that stores the `fsxadmin` password must be updated before, or together with, a password reset. A client still holding the old password can keep failing to log in, and if ONTAP locks the account, every client sharing it fails, this poller included. The poller makes only `GET` requests to `/api/storage/quota/reports` (confidence: `code-inspected`), so a dedicated ONTAP account with a read-only role avoids sharing `fsxadmin` with other clients. A read-only account was not tested in this run.

### Findings (Qtree Run)

| # | Finding | Kind | Effect on this record |
|---|---------|------|-----------------------|
| QF1 | The `fsxadmin` credential was rejected (HTTP 401) from 02:16:26Z, about 5 minutes after it worked, with no reset through the FSx for ONTAP management API and no secret change in between | Environment, cause undetermined | Blocked the second poll cycle and Q3. ONTAP access must be restored and the cause identified before a rerun |
| QF2 | The poller has no back-off on 401/403. The 5-minute schedule plus 2 asynchronous retries per invocation sent 6 failing basic-auth requests in about 8 minutes (02:16:26Z to 02:24:46Z). Under an ONTAP lockout policy, this would keep the account locked | Template design trade-off | Not raising on 401 would avoid the retries but would also keep the failure out of the DLQ. No fix is proposed in this record |
| QF3 | Stack deletion removed the log group (02:24:40Z) before the function (02:25:18Z), and the role still allowed `logs:CreateLogGroup` until 02:25:33Z. An in-flight retry recreated the log group without retention at 02:24:51Z | Template defect (deletion order) | A user who deletes the stack can be left with an orphan log group that never expires. Possible fixes, untested: `DependsOn` on the function so it is deleted first, or removing `logs:CreateLogGroup` from the role |
| QF4 | `preflight-check.sh` has no profile for this template and does not check for `com.amazonaws.<region>.monitoring`. The template does not create that endpoint | Tooling and documentation gap | In a subnet without NAT, as here, the endpoint had to be created by hand before the Lambda could call `PutMetricData` |
| QF5 | `OntapMgmtIp` accepts only an IPv4 literal; the management DNS name fails the `AllowedPattern` | Parameter constraint | Worked here with one management IP. Whether the DNS name is the safer input when the IP can change was not evaluated |
| QF6 | The log line `Found 2 qtree quota reports` and the return field `qtrees_monitored` (2) count the default-qtree record that is then skipped; 1 qtree was published | Wording of a count | The counts are report records, not monitored qtrees |
| QF7 | The published percent (60.2421875) is computed from bytes; ONTAP's `hard_limit_percent` rounds to 60 | Behavior note | Alarm thresholds compare against the unrounded value |
| QF8 | `QtreeQuotaAlarm` went from INSUFFICIENT_DATA to OK on one datapoint with `EvaluationPeriods` 2 | CloudWatch behavior, observed once | Consistent with CloudWatch evaluating partial data. Not reproduced |

### Cleanup (Qtree Run)

| Step | Result | Time (UTC) |
|------|--------|------------|
| `aws cloudformation delete-stack` | DELETE_COMPLETE | 02:24:37Z → 02:26:35Z |
| Delete the `monitoring` interface endpoint | Accepted (no unsuccessful items) | 02:25:02Z |
| ONTAP REST: delete the quota rule, disable quotas, delete the qtree | Not attempted. ONTAP was answering 401, and more failed logins could extend a lockout | — |
| `aws fsx delete-volume` with `SkipFinalBackup=true` | The volume read back as not found | 02:56:00Z → 02:57:18Z |
| Delete the orphan log group recreated by a retry (QF3) | Deleted. Its only stream, the 02:24:42Z retry, was saved first | 02:59:35Z |
| Re-read of every AWS resource | Stack does not exist (DELETE_COMPLETE by stack ID); endpoint, volume, alarms, function, IAM role, DLQ, schedule rule, Lambda network interfaces, and log group all return nothing; the bastion host has no remaining mount | 02:58:59Z → 03:03:54Z |

Two items remain open:

- Whether ONTAP removed the tree quota rule from the SVM's quota policy when the volume was deleted is unverified. Re-check with `GET /api/storage/quota/rules?svm.name=<svm-name>` once ONTAP access works. The re-run closed this item: at 05:29:25Z that call listed only the 2 rules the re-run had just created, so the rule from this run was no longer on the SVM.
- The 5 custom metric series in `FSxONTAP/Qtree` cannot be deleted and were still listed at 02:59:04Z. CloudWatch stops listing a metric after about two weeks without new data and keeps its data for 15 months ([CloudWatch concepts](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html), [re:Post](https://repost.aws/knowledge-center/cloudwatch-delete-metric)).

### What Remains Unverified (Qtree Run)

| Item | Status | Reason |
|------|--------|--------|
| Second and later successful poll cycles, and consistency across cycles | Not verified | Only cycle 1 succeeded (QF1) |
| `QtreeQuotaAlarm` ALARM transition and return to OK | Not run | Q3 was not run |
| Behavior at 0% usage | Not run | Data was written before the first poll |
| Cause of the 401, and the ONTAP-side read-back of the quota rule and qtree | Not verified | ONTAP access was not restored during the run |
| Second-generation file systems and more than one HA pair | Not run | The test file system was first generation with one HA pair |
| TLS with a CA certificate (`CaCertPath`, `CaCertLayerArn`) | Not run | Only `CERT_NONE` ran |
| SNS notification delivery | Not exercised | `NotificationEmail` was empty, so no topic was created |
| Pagination beyond one page, the truncation path (`QtreeQuotaReportTruncated=1`), and SVMs with more than 200 qtrees | Not run | The SVM returned 2 records on one page |
| A dedicated read-only ONTAP account for the poller | Not tested | The run used `fsxadmin` |
| Least-privilege security group and export policy | Not tested | The run reused a security group open to `0.0.0.0/0` and the `default` export policy, which allows `0.0.0.0/0` read/write and superuser access |
| Fixes for QF2 and QF3 | Not tested | Proposals only |

### Judgment (Qtree Run)

| Item | Value |
|------|-------|
| Judgment | ⚠️ Partial. Per-qtree and SVM-level metric publication verified for one poll cycle on a first-generation, single-HA-pair file system, with values matching the ONTAP quota report. `QtreeQuotaAlarm` OK evaluation observed on real data; its ALARM path not verified. DLQ path verified end to end. The run stopped on an ONTAP HTTP 401 with the cause undetermined |
| Passing checks | 6 of 8 (Q0, Q1, Q2-1, Q2-2, Q2-3, Q4) |
| Failed | 1 of 8 (Q2-4, the second poll cycle) |
| Not run | 1 of 8 (Q3) |
| Template-level issues found | 2 (QF2 design trade-off, QF3 deletion order) |

---

## Qtree Quota Monitor Re-run on 2026-10-06

Later on 2026-10-06 (UTC), the same template was deployed again on the same file system. Before the re-run, the other function named in the first run's lockout hypothesis was throttled, and the `fsxadmin` password was then reset. The re-run completed every planned phase. ONTAP answered every call with no 401 or 403. Four consecutive scheduled polls succeeded, each publishing 5 datapoints whose values matched the ONTAP quota report. `QtreeQuotaAlarm` left INSUFFICIENT_DATA and reached OK, moved to ALARM when the threshold was lowered to 50, and returned to OK when it was restored to 85. Cleanup removed every resource on the AWS side and the ONTAP side.

| Item | Value |
|------|-------|
| Verification date | 2026-10-06T05:25Z to 06:02Z (UTC) |
| Verification environment | Test environment (`ap-northeast-1`), sample run with one SVM, one test volume, and one qtree with a tree quota |
| Scope | Stack deployment, 4 poll cycles, per-qtree and SVM-level metric publication, the `QtreeQuotaAlarm` OK → ALARM → OK path, and cleanup with re-read on AWS and ONTAP |
| Result | Pass. 12 of 12 checks passed |

These values come from 4 polls on one qtree on a first-generation, single-HA-pair file system. They do not show behavior at scale, with more than one page of quota records, or on second-generation or multi-HA-pair file systems.

### Environment and Deployment (Re-run)

The file system, ONTAP version (9.18.1P6), SVM, subnet, security group, Secrets Manager endpoint, stack name, and parameters were the same as in [the first run](#qtree-quota-monitor-run-on-2026-10-06), with `QuotaThresholdPercent=85`, `PollIntervalMinutes=5`, and `NotificationEmail`, `CaCertPath`, and `CaCertLayerArn` empty. The permissive security group and `default` export policy described in the first run's security note were reused unchanged. Differences:

| Item | Value |
|------|-------|
| Template revision | `qtree-quota-monitor.yaml` from main at `76f643f` (#110). The file is unchanged since `54e4c5d`, the revision of the first run |
| ONTAP credential | `fsxadmin`, after a password reset through the FSx for ONTAP management API (`UpdateFileSystem` at 05:18:26Z). The secret was updated at 05:20:41Z |
| Test volume | `zz_mon_verify_qtree`, created again (`aws fsx create-volume` at 05:27:31Z, `CREATED` at 05:28:31Z) with the same settings as the first run |
| Data written | 60 MiB over a temporary NFSv4.1 mount from the bastion host, then unmounted and the mount point removed. ONTAP quota report afterward: used 63168512 bytes, hard limit 104857600 |
| CloudWatch route | A new `com.amazonaws.ap-northeast-1.monitoring` interface endpoint (private DNS on), created for the re-run. The VPC had no such endpoint beforehand |

### Credential Handling Before the Re-run

The first run's leading hypothesis was a lockout caused by another function in the same VPC that still used a previous `fsxadmin` password. Before the re-run:

- That function's reserved concurrency was set to 0. Its last invocation was at about 04:59Z, and from then on its trigger was throttled (3 throttles per 10-minute window, 0 invocations, 0 errors through the end of the re-run). The setting was left at 0 and read back as 0 at about 06:01Z.
- The password was then reset through the FSx for ONTAP management API at 05:18:26Z, and the secret was updated at 05:20:41Z.
- From the first ONTAP call (05:25:31Z) through the last cleanup read (06:01:49Z), no call from the bastion host or the poller received 401 or 403.

The probable cause of the first run's 401 is therefore that other client still logging in with a previous password. Throttling it before the reset was followed by 4 polls with no 401 (observed). This is consistent with the lockout hypothesis, but it does not prove an ONTAP-side lockout: the ONTAP login and lockout state and the EMS events were not read in either run, and a password change made inside ONTAP was not ruled out from the ONTAP side.

> **Password-reset note**: Before resetting a shared `fsxadmin` password, find every client that stores it and stop or update each one first. In this environment, throttling the one known client before the reset was enough for the re-run. A dedicated read-only ONTAP account for the poller, which avoids sharing `fsxadmin`, was still not tested.

### Check Results (Re-run)

| # | Check | Result | Time (UTC) |
|---|-------|--------|------------|
| R0-1 | ONTAP read-only preflight: `GET /api/cluster` from the bastion host | ✅ PASS. HTTP 200, 9.18.1P6 | 05:25:31Z |
| R0-2 | `preflight-check.sh --profile automated-response`, and a check for an existing `monitoring` endpoint | ✅ PASS. Exit 0; the existing Secrets Manager endpoint gives `CreateSecretsManagerEndpoint=false`; no `monitoring` endpoint existed | About 05:26Z |
| R0-3 | The other client that stores the `fsxadmin` password stays throttled | ✅ PASS. 0 invocations during the run, reserved concurrency 0 at the end | 05:20Z → 06:01Z |
| R1 | Qtree preparation: volume, qtree, 100 MiB tree quota rule, quotas on, 60 MiB written | ✅ PASS. ONTAP also created a default tree rule (RF1) | 05:27:31Z → 05:31Z |
| R2-1 | `monitoring` interface endpoint | ✅ PASS. Available | 05:31:02Z → 05:32:03Z |
| R2-2 | Stack creation | ✅ PASS. CREATE_COMPLETE | 05:32:11Z → 05:35:19Z |
| R2-3 | Poll cycles 1–4: no error, 5 datapoints each, values match the ONTAP report | ✅ PASS. 4 of 4 | 05:39:27Z, 05:44:27Z, 05:49:27Z, 05:54:27Z |
| R2-4 | Lambda `Errors` and `Throttles`, DLQ depth | ✅ PASS. 0, 0, and 0 messages | 05:37Z → 05:54Z |
| R3-1 | `QtreeQuotaAlarm` INSUFFICIENT_DATA → OK | ✅ PASS | 05:40:27Z |
| R3-2 | `QuotaThresholdPercent` 85 → 50 (`update-stack`); `QtreeQuotaAlarm` OK → ALARM at 60.24% | ✅ PASS | Update 05:51:52Z → 05:52:24Z; ALARM 05:52:31Z |
| R3-3 | `QuotaThresholdPercent` 50 → 85; `QtreeQuotaAlarm` ALARM → OK | ✅ PASS | Update 05:52:52Z → 05:53:24Z; OK 05:54:45Z |
| R4 | Cleanup, with re-read on AWS and ONTAP | ✅ PASS for every resource | 05:55:13Z → 06:01:49Z |

### Observed Metrics (Re-run)

Each poll logged `Found 2 qtree quota reports for SVM <svm-name> in 1 page(s)` and `Published 5 metric data points`. Poll 1 was a cold start (618 ms); polls 2–4 took 322–334 ms. At cold start the function logged the PoC-only warning for `cert_reqs=CERT_NONE` once, together with urllib3's `InsecureRequestWarning`.

| Metric | Dimensions | Datapoints | Value at each poll | ONTAP quota report | Match |
|--------|------------|:---:|--------------------|--------------------|-------|
| `QtreeQuotaUsedPercent` | `SvmName`, `VolumeName`, `QtreeName` | 4 | 60.2421875 | 63168512 / 104857600 × 100 | Yes |
| `QtreeQuotaUsedBytes` | `SvmName`, `VolumeName`, `QtreeName` | 4 | 63168512 | used 63168512 | Yes |
| `QtreeQuotaLimitBytes` | `SvmName`, `VolumeName`, `QtreeName` | 4 | 104857600 | hard limit 104857600 | Yes |
| `QtreeQuotaUsedPercentMax` | `SvmName` | 4 | 60.2421875 | Maximum over 1 qtree | Yes |
| `QtreeQuotaReportTruncated` | `SvmName` | 4 | 0 | 1 page, no next link | Yes |

An ONTAP re-read of the quota report at about 05:51Z returned the same used and limit values, so the values did not change across the 4 polls.

### Alarm State Transitions (Re-run)

From `describe-alarm-history` (`StateUpdate`), UTC. `EvaluationPeriods` is 2 and the statistic is `Maximum` over 300 seconds.

| Alarm | Transition | Time | Values in the state reason |
|-------|-----------|------|----------------------------|
| `fsxn-verify-qtree-quota-dlq-depth` | INSUFFICIENT_DATA → OK | 05:33:02Z | No datapoints, `notBreaching`. Stayed OK for the whole run |
| `fsxn-verify-qtree-quota-quota-high` | INSUFFICIENT_DATA → OK | 05:40:27Z | 1 datapoint, not > 85 |
| `fsxn-verify-qtree-quota-quota-high` | OK → ALARM | 05:52:31Z | 2 datapoints, 60.2421875 (05:47:00Z) and 60.2421875 (05:42:00Z), > 50 |
| `fsxn-verify-qtree-quota-quota-high` | ALARM → OK | 05:54:45Z | 2 datapoints, 60.2421875 (05:49:00Z) and 60.2421875 (05:44:00Z), not > 85 |

The return to OK came about 81 seconds after the stack update to 85 completed. `describe-alarm-history` for the same alarm name also returns the first run's 02:12:50Z entry, because history is keyed by alarm name and both runs used the same stack name. No SNS action ran, because `NotificationEmail` was empty.

### Findings (Re-run)

| # | Finding | Kind | Effect on this record |
|---|---------|------|-----------------------|
| RF1 | Creating the first explicit tree quota rule on the volume made ONTAP add a default tree rule (empty qtree name, no limit). The poller reports it (`Found 2 qtree quota reports`) and skips it, so it publishes 5 datapoints, not 8. Cleanup must delete both rules. Deleting the default rule returned HTTP 409 with a message that the delete succeeded but the rule was still enforced until quotas were turned off and on; the rule list then read 0 | ONTAP behavior, observed once | Cleanup procedures need a step for the default rule. The 409 does not mean the delete failed; re-read the rule list instead |
| RF2 | Disabling the schedule rule before deleting the stack left no orphan log group: after deletion, no `/aws/lambda/` log group for the stack remained | Workaround for QF3, observed once | QF3 is not fixed in the template. This order avoids it in this run only |
| RF3 | `QtreeQuotaAlarm` again reached OK from INSUFFICIENT_DATA on 1 datapoint; ALARM and the return to OK each cited 2 datapoints | CloudWatch behavior, second observation of QF8 | The first OK evaluation can come one period earlier than `EvaluationPeriods` suggests |
| RF4 | The security group allows all inbound traffic from `0.0.0.0/0`, and the export policy allows `0.0.0.0/0` read/write and superuser access (both pre-existing in this test VPC) | Test-environment configuration | Connectivity in this run does not show that a least-privilege configuration works. A least-privilege configuration needs 443 from the Lambda's security group to the management interface and to the `monitoring` and Secrets Manager endpoints |
| RF5 | `CaCertPath` was empty, so the poller ran with `CERT_NONE` | Scope limit | CA certificate verification was not exercised |

### Cleanup (Re-run)

| Step | Result | Time (UTC) |
|------|--------|------------|
| Disable the EventBridge schedule rule | DISABLED | 05:55:13Z |
| `aws cloudformation delete-stack` | Complete | 05:55:14Z → 05:57:16Z |
| Delete the `monitoring` interface endpoint | Accepted (no unsuccessful items) | 05:57:21Z |
| ONTAP REST: delete the explicit tree quota rule | HTTP 200 (job) | 05:57:32Z |
| ONTAP REST: delete the default tree rule (RF1) | HTTP 409, rule list then 0 | 05:57:58Z |
| ONTAP REST: disable quotas on the volume | Quota state off | 05:58:18Z |
| ONTAP REST: delete the qtree | HTTP 200 (job); only the volume's implicit qtree remained | 05:58:44Z |
| `aws fsx delete-volume` with `SkipFinalBackup=true` | The volume read back as not found | 05:59:14Z → 06:00:07Z |
| Re-read on AWS | The stack, endpoint, volume, both alarms, function, log group, schedule rule, DLQ, IAM role, and network interfaces for the stack or the endpoint all return nothing | 06:01:22Z → 06:01:41Z |
| Re-read on ONTAP | 0 quota rules on the SVM; no `qt_mon_verify` qtree; volume `zz_mon_verify_qtree` not found | 06:01:41Z → 06:01:49Z |

The 5 custom metric series in `FSxONTAP/Qtree` cannot be deleted and are left to expire under CloudWatch retention, as in the first run. Their listing after the re-run was not re-checked.

### What Remains Unverified (Re-run)

| Item | Status | Reason |
|------|--------|--------|
| Second-generation file systems and more than one HA pair | Not run | The test file system was first generation with one HA pair |
| TLS with a CA certificate (`CaCertPath`, `CaCertLayerArn`) | Not run | Only `CERT_NONE` ran (RF5) |
| SNS notification delivery | Not exercised | `NotificationEmail` was empty, so the alarms had no actions |
| Pagination beyond one page, the truncation path (`QtreeQuotaReportTruncated=1`), and SVMs with more than 200 qtrees | Not run | The SVM returned 2 records on one page |
| Least-privilege security group and export policy | Not tested | The run reused the permissive test configuration (RF4) |
| A dedicated read-only ONTAP account for the poller | Not tested | The run used `fsxadmin` |
| ONTAP-side proof of the first run's lockout | Not verified | ONTAP login and lockout state and EMS events were not read |
| Behavior at 0% usage, and under changing usage | Not run | Data was written before the first poll and did not change during the run |
| Fixes for QF2 and QF3 in the template | Not tested | Proposals only. RF2 is an operating-order workaround |

### Judgment (Re-run)

| Item | Value |
|------|-------|
| Judgment | ✅ On a first-generation, single-HA-pair file system: per-qtree and SVM-level metric publication verified across 4 consecutive poll cycles with values matching the ONTAP quota report, and the `QtreeQuotaAlarm` OK → ALARM → OK path verified on real data. No 401 or 403 occurred |
| Passing checks | 12 of 12 |
| Failed or not run | 0 |
| Template-level issues found | None new. QF2 and QF3 from the first run remain in the template |

---

## Related Documents

- [Monitoring Design](monitoring-design.md): the dashboard template, the Terraform T1 module, and the qtree quota monitor, with confidence tiers that cite this record
- [AWS-Native Alternative Matrix](native-alternative-matrix.md): System Manager view → CloudWatch metric → template mapping
- [Terraform module: fsxn-monitoring-dashboard](../../terraform/fsxn-monitoring-dashboard/README.md): inputs, outputs, and verification status
- [CloudWatch Log Alarm](cloudwatch-log-alarm.md): the separate log-alarm template and its 2026-07-02 E2E record
