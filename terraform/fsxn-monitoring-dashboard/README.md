# fsxn-monitoring-dashboard (Terraform)

🌐 [日本語](README.ja.md) | **English**

Terraform equivalent of `shared/templates/fsxn-monitoring-dashboard.yaml`: a CloudWatch dashboard and alarms for one Amazon FSx for NetApp ONTAP file system. This is phase T1 of the Terraform plan in [monitoring-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)), which also covers when to use it and the opt-in alarm table.

## Verification status

Offline: `make terraform` runs `terraform fmt -check`, `terraform init -lockfile=readonly`, `terraform validate`, and `terraform test` with a mock provider and `command = plan`. It also runs `init` and `validate` on [`examples/basic/`](examples/basic/). It needs no AWS credentials and creates nothing.

Live, first: on 2026-10-05 the module was planned and applied in `ap-northeast-1` against a first-generation `SINGLE_AZ_1` file system with one HA pair, with the three opt-in file-server alarms enabled (`file_server_names` empty) and one entry in `volume_ids`. It created the dashboard and 7 alarms. All 9 dashboard series returned data, every alarm left INSUFFICIENT_DATA and reached OK, and the per-volume capacity and inode alarms were driven to ALARM and back to OK. See [CloudWatch monitoring verification results](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md)).

Display defect in tag `terraform-fsxn-monitoring-dashboard-v0.1.0`: four widgets (Network Throughput, IOPS, Network Sent/Received, Storage Used) draw their raw input metrics on the same axis as the converted MB/s, IOPS and GB series, so the converted lines sit near zero. It was found on 2026-10-07 when the deployed dashboard was screenshotted. The data and all alarms are not affected. The fix sets `visible = false` on those raw rows; it is on `main` and not yet in a tag. Until a fixed tag exists, pin a commit that contains the fix, or keep v0.1.0 and read those four widgets with this in mind. See the [dashboard display note](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#findings) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#所見)).

Live, second: the file-system `storage_capacity` alarm could not be driven to ALARM on 2026-10-05, because its 50–95 range is above the 3.5% utilization of the test file system. From 2026-10-06 to 2026-10-07 (UTC), in the Asia Pacific (Tokyo) Region (`ap-northeast-1`), the module was applied again to a first-generation `SINGLE_AZ_1` file system with one HA pair, with the default alarms only and `capacity_threshold_percent = 50`. Real data written to a test volume raised SSD utilization to 58.6%, and the capacity alarms of both the CloudFormation template and this module went from OK to ALARM and back to OK. They read `StorageCapacityUtilization` in namespace `AWS/FSx` with `FileSystemId` + `StorageTier=SSD` + `DataType=All`. The alarms returned to OK after the deleted test volume was purged from the ONTAP recovery queue; deleting the volume alone did not lower utilization. See the [capacity alarm real-data run](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#capacity-alarm-real-data-run-on-2026-10-06) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-06-の容量アラームの実データによる実行)).

Still `unverified`: SNS delivery (no `notification_email` was set), second-generation file systems with `file_server_names`, multi-HA-pair file systems, and behavior under load. On a different file-system shape, run `terraform plan` in a non-production account first.

## What it creates

- `aws_cloudwatch_dashboard`: the template's seven widgets (title, throughput, IOPS, network throughput utilization, storage capacity utilization, network sent/received, storage used).
- Two `aws_cloudwatch_metric_alarm` resources, as in the template: storage capacity utilization and network throughput utilization. The capacity alarm and widget select `FileSystemId` + `StorageTier=SSD` + `DataType=All`, the same set as the template.
- `aws_sns_topic` and an email `aws_sns_topic_subscription`, only when `notification_email` is not empty. Both alarms then notify on ALARM and OK. The recipient must confirm the subscription before email arrives.
- Opt-in alarms, all off by default: `CPUUtilization`, `FileServerDiskIopsUtilization`, `FileServerDiskThroughputUtilization` (one each, or one per entry in `file_server_names`), and two per entry in `volume_ids` (volume `StorageCapacityUtilization`, and inode utilization as metric math `100 * FilesUsed / FilesCapacity`).

All metrics are in namespace `AWS/FSx`, with names, dimensions, and statistics from the AWS metric pages: [file system (first generation)](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-system-metrics.html), [file system (second generation)](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/so-file-system-metrics.html), [volume](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-metrics.html).

## Obtaining the module

The module is versioned with git tags of the form `terraform-fsxn-monitoring-dashboard-vX.Y.Z`. The first is `terraform-fsxn-monitoring-dashboard-v0.1.0`, published as a [GitHub Release](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/releases/tag/terraform-fsxn-monitoring-dashboard-v0.1.0). The module is not on the Terraform Registry, because it is a subdirectory of a larger repository. The sizes below were measured on 2026-10-07 at commit `4b27a84`, before the first tag was created, and grow with the repository.

| Method | What is downloaded (measured) | Version pinning | Needs git | Status today |
|---|---|---|---|---|
| Git source with a tag and `?ref=<tag>&depth=1` | Shallow clone of the whole working tree, about 50 MB (measured with `ref=main&depth=1`, not with the tag) | Tag | Yes | Works. First tag: `terraform-fsxn-monitoring-dashboard-v0.1.0` |
| Git source with a commit SHA and `?ref=<commit-sha>` | Full clone of the repository, about 62 MB. Adding `&depth=1` with a SHA fails with `fatal: Remote branch <sha> not found`, because `depth` works only with a branch or tag name | Commit SHA | Yes | Works |
| Archive URL with a commit SHA | About 20 MB download. The subdirectory path must start with `FSx-for-ONTAP-Observability-integrations-<commit-sha>/` | Commit SHA | No | Works |
| `git sparse-checkout` of the module directory, then a local `source` path | About 912 KB (the module and the repository's top-level files, including `LICENSE`) | Tag or commit SHA, in your copy | Yes | Works |

The `source` values for the git source with the tag, the git source with a commit SHA, and the archive URL:

```hcl
# Git source pinned to the first tag (shallow clone)
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-monitoring-dashboard?ref=terraform-fsxn-monitoring-dashboard-v0.1.0&depth=1"

# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-monitoring-dashboard?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-monitoring-dashboard"
```

The steps below check out only the module directory at the tag with a sparse checkout. Then set `source` to the local path of `terraform/fsxn-monitoring-dashboard` in this copy. To pin a commit instead, replace the tag name with a commit SHA.

```bash
git init fsx-ontap-monitoring && cd fsx-ontap-monitoring
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-monitoring-dashboard
git fetch --depth 1 --filter=blob:none origin terraform-fsxn-monitoring-dashboard-v0.1.0
git checkout FETCH_HEAD
```

> **Download-size note**
>
> With a `//subdirectory` source, Terraform downloads and extracts the whole package and then reads the module from the subdirectory ([module block reference](https://developer.hashicorp.com/terraform/language/block/module)). That is why the git and archive sources fetch far more than the module itself.

> **Subversion note**
>
> GitHub removed Subversion support in 2024 ([GitHub changelog](https://github.blog/changelog/2024-01-07-subversion-has-been-sunset/)), so `svn export` of a single directory is not an option.

How to choose: each option trades download size, the git dependency, the update path, and pinning differently. The archive URL needs no git and pins a commit; an update means changing the SHA in two places of one long URL. The git source with a SHA pins a commit and an update changes one `ref` value; it needs git and clones the whole repository on every `init` in a new working directory. The tag form pins a readable version and allows a shallow clone, and an update changes the tag name in `ref`; it needs git and still downloads the whole working tree. The sparse checkout downloads the least and lets you review the code before use; you maintain the copy and pull updates into it yourself.

## Usage

The sections below follow the order of a first deployment: prerequisites, permissions, input values, deployment, checks after apply, and removal.

### Prerequisites

- Terraform `>= 1.11.0`.
- `hashicorp/aws` `>= 6.67.0`. Tested with 6.67.0; releases 6.0 to 6.66 are untested.
- AWS credentials from IAM Identity Center or an IAM role, and the Region of the file system set in your `provider "aws"` block. The module has no `provider` block.

### Estimated IAM permissions (unverified)

The list below is estimated and unverified: it was derived by reading the module's resource types, and the module has never been applied with a role limited to these actions.

| Resource | Actions |
|---|---|
| `aws_cloudwatch_dashboard` | `cloudwatch:PutDashboard`, `cloudwatch:GetDashboard`, `cloudwatch:DeleteDashboards` |
| `aws_cloudwatch_metric_alarm` | `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, `cloudwatch:DeleteAlarms`, `cloudwatch:ListTagsForResource`, `cloudwatch:TagResource`, `cloudwatch:UntagResource` |
| `aws_sns_topic` (only with `notification_email`) | `sns:CreateTopic`, `sns:GetTopicAttributes`, `sns:SetTopicAttributes`, `sns:ListTagsForResource`, `sns:TagResource`, `sns:UntagResource`, `sns:DeleteTopic` |
| `aws_sns_topic_subscription` (only with `notification_email`) | `sns:Subscribe`, `sns:GetSubscriptionAttributes`, `sns:Unsubscribe` |
| Read-only checks in the sections below | `cloudwatch:ListMetrics`, `fsx:DescribeFileSystems` (plus `cloudwatch:DescribeAlarms` and `cloudwatch:GetDashboard` from above) |

`data "aws_region"` is not expected to need an action (unverified). Whether these actions can be scoped to resource ARNs by `name_prefix` (for example `arn:aws:cloudwatch:ap-northeast-1:123456789012:alarm:fsxn-monitoring-*`) is not verified.

### Finding dimension values

The file system ID comes from the Amazon FSx API (`aws fsx describe-file-systems`). The `VolumeId` values for `volume_ids` and the `FileServer` values for `file_server_names` can be read from the metrics CloudWatch already has for the file system. The last command prints the distinct values.

```bash
aws fsx describe-file-systems --query 'FileSystems[].{Id:FileSystemId,Type:FileSystemType}' --output table
aws cloudwatch list-metrics --namespace AWS/FSx --dimensions Name=FileSystemId,Value=fs-0123456789abcdef0
aws cloudwatch list-metrics --namespace AWS/FSx --dimensions Name=FileSystemId,Value=fs-0123456789abcdef0 \
  --query 'Metrics[].Dimensions[?Name==`VolumeId` || Name==`FileServer`].Value | []' --output text | tr '\t' '\n' | sort -u
```

### Deploying from examples/basic

Run these commands from the directory that contains `terraform/`, either the `fsx-ontap-monitoring/` sparse checkout from [Obtaining the module](#obtaining-the-module) or a full clone. There `examples/basic/` calls the module with `source = "../.."`, so the checked-out copy is used. In `terraform.tfvars`, set `region`, `file_system_id`, and any optional inputs.

```bash
cd terraform/fsxn-monitoring-dashboard/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: region, file_system_id, and any optional inputs
terraform init
terraform plan
terraform apply
```

In your own root configuration, copy [`examples/basic/`](examples/basic/) and replace `source = "../.."` with one of the sources in [Obtaining the module](#obtaining-the-module). That root needs no clone or sparse checkout: `terraform init` downloads the module from `source`. With the tag, the module block looks like this:

```hcl
module "fsx_ontap_monitoring" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-monitoring-dashboard?ref=terraform-fsxn-monitoring-dashboard-v0.1.0&depth=1"

  file_system_id             = "fs-0123456789abcdef0"
  file_system_name           = "fsx-for-ontap-prod"
  capacity_threshold_percent = 80
  notification_email         = "ops@example.com"

  # Opt-in alarms (all off by default)
  enable_cpu_utilization_alarm = true
  volume_ids                   = ["fsvol-0123456789abcdef0"]
}
```

### Verifying after apply

Replace `fsxn-monitoring` with your `name_prefix`, and the dashboard name with the `dashboard_name` output:

```bash
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-monitoring \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table
aws cloudwatch get-dashboard --dashboard-name fsxn-monitoring-fsx-for-ontap
```

Alarms stay in INSUFFICIENT_DATA until enough datapoints have arrived for their evaluation periods. With `notification_email` set, the email subscription stays pending until the recipient confirms it.

### Removing

```bash
terraform destroy
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-monitoring \
  --query 'MetricAlarms[].AlarmName' --output text
```

The second command should print nothing once every alarm is gone.

## Inputs

| Name | Type | Default | Notes |
|---|---|---|---|
| `file_system_id` | string | required | `^fs-[0-9a-f]{17}$` (template `FileSystemId`) |
| `file_system_name` | string | `"fsx-for-ontap"` | Dashboard name and title (template `FileSystemName`) |
| `name_prefix` | string | `"fsxn-monitoring"` | Plays the role of the stack name in resource names |
| `capacity_threshold_percent` | number | `80` | 50–95 (template `CapacityThresholdPercent`) |
| `throughput_threshold_percent` | number | `80` | 1–100. The template hardcodes 80 |
| `notification_email` | string | `""` | Empty skips SNS (template `NotificationEmail`) |
| `enable_cpu_utilization_alarm` | bool | `false` | With `cpu_utilization_threshold_percent` (80) |
| `enable_disk_iops_utilization_alarm` | bool | `false` | With `disk_iops_utilization_threshold_percent` (80) |
| `enable_disk_throughput_utilization_alarm` | bool | `false` | With `disk_throughput_utilization_threshold_percent` (80) |
| `file_server_names` | list(string) | `[]` | `FileServer` values for second-generation file systems, for example `FsxId0123456789abcdef0-01` |
| `volume_ids` | list(string) | `[]` | `^fsvol-[0-9a-f]{17}$`, no duplicates |
| `volume_capacity_threshold_percent` | number | `80` | 1–100 |
| `volume_inode_threshold_percent` | number | `80` | 1–100 |
| `tags` | map(string) | `{}` | Alarms and SNS topic. Dashboards do not take tags |

## Outputs

| Name | Description |
|---|---|
| `dashboard_name`, `dashboard_arn`, `dashboard_url` | Dashboard identity and console URL (same shape as the template's `DashboardUrl`) |
| `capacity_alarm_arn`, `throughput_alarm_arn` | The two parity alarms |
| `sns_topic_arn` | Topic ARN, or `null` without `notification_email` |
| `file_server_alarm_arns` | Map keyed by `<metric_key>` or `<metric_key>/<file_server>` |
| `volume_alarm_arns` | `{ capacity = { <volume_id> = arn }, inode = { <volume_id> = arn } }` |

## Deliberate differences from the CloudFormation template

- `throughput_threshold_percent` exposes the threshold the template hardcodes at 80.
- `ok_actions` is set alongside `alarm_actions`.
- Default `file_system_name` is `fsx-for-ontap`.
- No `aws_fsx_ontap_file_system` data source: it would add `fsx:DescribeFileSystems` to plan-time permissions and prevent planning before the file system exists. The ID regex validates the shape instead.
- The SNS topic is unencrypted, as in the template. There is no KMS input in T1.

## Second-generation file systems

AWS documents the file-server metrics on second-generation file systems with `FileSystemId` + `FileServer`, and capacity with an optional `Aggregate`. Whether the `FileSystemId`-only network throughput series, the capacity series without `Aggregate`, and the file-server series without `FileServer` exist there is `unverified`. Set `file_server_names` on second-generation file systems so the opt-in alarms use a documented dimension set.

## Provider version constraint

The module declares `hashicorp/aws` `>= 6.67.0`, the lowest release it was tested with, and no upper bound. [`examples/basic/`](examples/basic/) carries the exact pin `= 6.67.0` and its own `.terraform.lock.hcl`. Callers do not use this module's `.terraform.lock.hcl`: Terraform reads the lock file of the root configuration ([Dependency Lock File](https://developer.hashicorp.com/terraform/language/files/dependency-lock)). The module's lock file serves only this repository's `make terraform` and CI. Terraform `>= 1.11.0` is required because the tests use `override_during = plan`.
