# fsxn-monitoring-dashboard (Terraform)

Terraform equivalent of `shared/templates/fsxn-monitoring-dashboard.yaml`: a CloudWatch dashboard and alarms for one Amazon FSx for NetApp ONTAP file system. This is phase T1 of the Terraform plan in [docs/en/monitoring-design.md](../../docs/en/monitoring-design.md) ([日本語](../../docs/ja/monitoring-design.md)), which also covers when to use it and the opt-in alarm table.

## Verification status

Offline only. `make terraform` runs `terraform fmt -check`, `terraform init -lockfile=readonly`, `terraform validate`, and `terraform test` with a mock provider and `command = plan`. It needs no AWS credentials and creates nothing. Nothing has been planned against a real account or applied, so real-environment behavior is `unverified`. Run `terraform plan` in a non-production account first.

## What it creates

- `aws_cloudwatch_dashboard`: the template's seven widgets (title, throughput, IOPS, network throughput utilization, storage capacity utilization, network sent/received, storage used).
- Two `aws_cloudwatch_metric_alarm` resources, as in the template: storage capacity utilization and network throughput utilization. The capacity alarm and widget select `FileSystemId` + `StorageTier=SSD` + `DataType=All`, the same set as the template.
- `aws_sns_topic` and an email `aws_sns_topic_subscription`, only when `notification_email` is not empty. Both alarms then notify on ALARM and OK. The recipient must confirm the subscription before email arrives.
- Opt-in alarms, all off by default: `CPUUtilization`, `FileServerDiskIopsUtilization`, `FileServerDiskThroughputUtilization` (one each, or one per entry in `file_server_names`), and two per entry in `volume_ids` (volume `StorageCapacityUtilization`, and inode utilization as metric math `100 * FilesUsed / FilesCapacity`).

All metrics are in namespace `AWS/FSx`, with names, dimensions, and statistics from the AWS metric pages: [file system (first generation)](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-system-metrics.html), [file system (second generation)](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/so-file-system-metrics.html), [volume](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-metrics.html).

## Usage

The module has no `provider` block. Configure the AWS provider and Region in your root module.

```hcl
module "fsx_ontap_monitoring" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-monitoring-dashboard?ref=<commit-sha>"

  file_system_id             = "fs-0123456789abcdef0"
  file_system_name           = "fsx-for-ontap-prod"
  capacity_threshold_percent = 80
  notification_email         = "ops@example.com"

  # Opt-in alarms (all off by default)
  enable_cpu_utilization_alarm = true
  volume_ids                   = ["fsvol-0123456789abcdef0"]
}
```

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

## Provider pin

`hashicorp/aws` is pinned exactly to `6.67.0`, per the repository's dependency rule, and `.terraform.lock.hcl` carries hashes for linux and darwin on amd64 and arm64. Callers on a different 6.x release must change the pin. Terraform `>= 1.11.0` is required because the tests use `override_during = plan`.
