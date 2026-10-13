# Configure CloudWatch Monitoring for FSx for ONTAP with Terraform

🌐 [日本語](../ja/terraform-monitoring-guide.md) | **English** (this page)

## Executive summary

To deploy the CloudWatch monitoring design for Amazon FSx for NetApp ONTAP with Terraform, apply the three monitoring modules from `terraform/` in this order.

- Pick your thresholds before T1.
- T1 `fsxn-monitoring-dashboard` creates a dashboard and alarms on the native `AWS/FSx` metrics and calls AWS APIs only.
- T2 `fsxn-ontap-custom-metrics` publishes qtree quota usage and SnapMirror health and lag, which CloudWatch does not publish natively.
- T3 `fsxn-log-alarm` raises alarms on admin audit events.
- The fourth module, T4 `fsxn-ssd-auto-increase`, is a guarded sample that raises SSD capacity in response to an SSD capacity alarm; monitoring does not need it.

Prerequisites per module.

- T2 needs:
  - a subnet that reaches the file system's management endpoint on TCP 443
  - an ONTAP user with the `fsxadmin-readonly` role whose credentials are in a Secrets Manager secret
  - a path to CloudWatch and Secrets Manager through a NAT gateway or interface endpoints
- T3 needs a CloudWatch Logs log group that receives the audit log through the syslog VPC endpoint path, which this repository provides as a CloudFormation template.
  - That path carries the audit log only.
  - EMS events such as `wafl.vol.autoSize.fail` reach the same log group only through a separately configured EMS notification destination.

If you use the SSD auto-increase module:

- Add it after the capacity alarms, in the default `notify_only` mode.
- On a first-generation file system an SSD increase cannot be undone, and each change starts a 6-hour cooldown.

Live verification status.

- T1 and T2 have dated live runs.
- The [2026-10-09 record](verification-results-cloudwatch-monitoring.md#terraform-log-alarm-module-run-on-2026-10-09) verified three T3 audit detections with test settings.
- T4 is implemented and offline-verified.
  - The [2026-10-09 T4 record](verification-results-cloudwatch-monitoring.md#terraform-ssd-auto-increase-module-run-on-2026-10-09) verified its reversible paths with no capacity change.
- The [2026-10-12 record](verification-results-cloudwatch-monitoring.md#terraform-monitoring-modules-live-run-on-2026-10-12) applied T1 to T3 from their tags in one root module (44 resources; T3 at `v0.1.0`).
  - The four default T3 detections, `failed-access`, `bulk-delete`, `privileged-operations` and `autosize-fail`, ran at the default 300-second period.
- One EMS notification destination definition that delivered a real `wafl.vol.autoSize.fail` event and fired the alarm is in the [2026-10-12 record](verification-results-cloudwatch-monitoring.md#ems-destination-recipe-2026-10-12-run) (`verified`, 2026-10-12, first-generation file system with one HA pair).
- T4 made one real increase, on a throwaway file system, in the 2026-10-12 record (`verified`, once).
- The [module map](#module-map) links each dated record.

Verification environment.

- Every live result comes from first-generation, single-HA-pair `SINGLE_AZ_1` file systems.
- The 2026-10-12 file system (`ap-northeast-1`, ONTAP 9.19.1P3, 128 MBps) was a throwaway, since deleted.

CloudFormation templates in `shared/templates/` exist for:

- T1 (`fsxn-monitoring-dashboard.yaml`)
- the qtree part of T2 (`qtree-quota-monitor.yaml`)
- T3 (`cloudwatch-log-alarm.yaml`)
  - The T3 template's detection queries were later translated from the patterns T3 replaced after the 2026-10-09 run, and have not been run as LogAlarm queries (`unverified`; [Built-in Detection Queries](cloudwatch-log-alarm.md#built-in-detection-queries)).

Choosing between Terraform and CloudFormation.

- The SnapMirror collector and T4 exist only in Terraform.
- Which one to use depends on how your environment already manages infrastructure, not on the monitoring itself; see [Relation to the CloudFormation templates](#faq-and-common-misconceptions) in the FAQ.

> **Scope note**
>
> This page routes and orders. Inputs, IAM permissions, and the deploy, verify and remove commands are in each module README, linked from the module map. Whether CloudWatch is the right collection route at all (compared with Harvest + Prometheus, a SaaS platform, or the ONTAP REST API) is decided in the Hub: [Adoption Playbook — Observability](https://github.com/Yoshiki0705/FSx-for-ONTAP-Adoption-Playbook/blob/main/docs/en/domains/observability/README.md).

## Audience and scope

This page is for FSx for ONTAP users who have chosen CloudWatch as the collection route and manage infrastructure with Terraform. It covers which module produces which signal, the order to apply them, the prerequisite that most often blocks each step, per-module verification status, and a minimal root module.

It does not cover building the file system itself, choosing the collection route, the full input reference of each module, the Harvest and SaaS vendor paths, or CloudFormation deployment steps (those are in the [Deployment Guide](deployment-guide.md)).

## Design layers and where each is decided

The modules implement decisions made elsewhere. Make those decisions first; the layers are defined in [Monitoring design layers](monitoring-design.md#monitoring-design-layers).

| Layer | Document | What you decide there |
|---|---|---|
| Sizing and headroom | [sizing-and-headroom.md](sizing-and-headroom.md#threshold-table) | Throughput sizing, the warning, critical and emergency SSD thresholds, and the value for `capacity_threshold_percent` |
| Metric catalog | [monitoring-design.md](monitoring-design.md#metric-catalog) | Which series are native (T1), custom through the ONTAP REST API (T2), or log-based (T3) |
| Alert design | [monitoring-design.md](monitoring-design.md#alert-design) | Severity routing, missing-data policy, aggregate maximum plus drill-down, heartbeat alarms |
| Monitoring-driven automation | [capacity-automation.md](capacity-automation.md) and [capacity-automation-t4-design.md](capacity-automation-t4-design.md) | Whether anything acts on an SSD capacity alarm: the AWS sample, T4, or a runbook |

## Module map

The dated records behind each module's status are linked from this table.

| Phase | Purpose | Module path | What it creates | Prerequisites | Live verification | How to pin |
|---|---|---|---|---|---|---|
| T1 | Dashboard and alarms on native metrics | [`terraform/fsxn-monitoring-dashboard`](../../terraform/fsxn-monitoring-dashboard/README.md) | Dashboard (7 widgets), file system capacity and network-throughput-utilization alarms, opt-in CPU, disk and per-volume alarms, optional SNS topic | AWS APIs only; the file system ID | `verified`: [2026-10-05](verification-results-cloudwatch-monitoring.md#test-results-summary), [2026-10-06 to 07](verification-results-cloudwatch-monitoring.md#capacity-alarm-real-data-run-on-2026-10-06) (capacity alarm on real data), [2026-10-07](verification-results-cloudwatch-monitoring.md#dashboard-and-alarm-screenshots-on-2026-10-07) (minimum IAM policy); first generation, one HA pair. Also `verified` on 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps; [record](verification-results-cloudwatch-monitoring.md#check-results-2026-10-12-run)): applied from the tag inside the three-module root, and the T1 alarms that run created left INSUFFICIENT_DATA. T1 alarm e-mail delivery is `unverified` | Tag `terraform-fsxn-monitoring-dashboard-v0.1.1` (`v0.1.0` has a dashboard display defect); [Obtaining the module](../../terraform/fsxn-monitoring-dashboard/README.md#obtaining-the-module) |
| T2 | Qtree quota and SnapMirror health and lag | [`terraform/fsxn-ontap-custom-metrics`](../../terraform/fsxn-ontap-custom-metrics/README.md) | VPC Lambda, schedule, dead-letter queue, log group, IAM role, Lambda security group, optional interface endpoints, up to 7 alarms | Subnet reaching the management endpoint on TCP 443; ingress rule on the file system security group; `fsxadmin-readonly` user and Secrets Manager secret; NAT gateway or `monitoring` and `secretsmanager` endpoints | `verified`: [2026-10-08](verification-results-cloudwatch-monitoring.md#terraform-custom-metrics-module-run-on-2026-10-08), SnapMirror between two volumes in one SVM. Also `verified` on 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps; [record](verification-results-cloudwatch-monitoring.md#check-results-2026-10-12-run)): the heartbeat alarms (ALARM after apply, OK after the first poll), the default 5-minute poll (299–300 s) and `qtree-quota-high` OK → ALARM → OK on a real quota (90.36 against 85). That run had no SnapMirror relationship, so the SnapMirror alarms were not driven. The ingress-rule step was not exercised | Tag `terraform-fsxn-ontap-custom-metrics-v0.1.0`; [Obtaining the module](../../terraform/fsxn-ontap-custom-metrics/README.md#obtaining-the-module) |
| T3 | Alarms on admin audit events (EMS events need a separate forwarding setup) | [`terraform/fsxn-log-alarm`](../../terraform/fsxn-log-alarm/README.md) | One metric filter and one metric alarm per detection (4 default recipes: volume auto-resize failure, requests ONTAP rejected as not authorized, bulk deletes, activity by a monitored privileged user), optional SNS topic | An existing log group that the syslog VPC endpoint path writes the audit log to; `autosize-fail` also needs a separate EMS notification destination to the same log group (recipe `verified` on 2026-10-12: [EMS destination recipe](verification-results-cloudwatch-monitoring.md#ems-destination-recipe-2026-10-12-run)) | Three audit detections `verified`: [2026-10-09](verification-results-cloudwatch-monitoring.md#terraform-log-alarm-module-run-on-2026-10-09); with a 60-second test period and test thresholds, `bulk-delete`, `privileged-operations` and a failed-access detection counting REST 403s went OK → ALARM → OK. Also `verified` on 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps; T3 at `v0.1.0`; [record](verification-results-cloudwatch-monitoring.md#check-results-2026-10-12-run)): at the default 300-second period and the default thresholds, `failed-access`, `bulk-delete`, `privileged-operations` and `autosize-fail` (from a real EMS event through an EMS destination) went OK → ALARM → OK, and e-mail arrived for three notifications. Details: [Verified and not verified](#verified-and-not-verified) | Tag `terraform-fsxn-log-alarm-v0.1.1`; [Obtaining the module](../../terraform/fsxn-log-alarm/README.md#obtaining-the-module) |
| T4 | Guarded SSD auto-increase (optional; not needed for monitoring) | [`terraform/fsxn-ssd-auto-increase`](../../terraform/fsxn-ssd-auto-increase/README.md) | Lambda outside any VPC, SSD utilization trigger alarm (plus one per `aggregate_names` entry on second generation), trigger and notification SNS topics, hourly re-evaluation schedule, dead-letter queue, DynamoDB lock table, decision and function log groups, IAM role | An S3 Object Lock bucket for T4's own decision record, administered outside the module (compliance mode for `auto`; not file system data, and T1 to T3 do not use it: [why S3 Object Lock, not SnapLock](capacity-automation-t4-design.md#faq)); the required absolute ceiling `max_storage_capacity_gib`; `ignore_changes` on `storage_capacity` for a Terraform-managed file system. On first generation an increase cannot be undone, and each change starts a 6-hour cooldown | Implemented and offline-verified (unit tests, `make terraform`, `terraform test`). Live 2026-10-09 ([record](verification-results-cloudwatch-monitoring.md#terraform-ssd-auto-increase-module-run-on-2026-10-09)): `notify_only`, `approve` and `auto` behind an explicit IAM deny, with no capacity change. The real increase is `verified` once on 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps): 1,024 → 1,127 GiB through exactly one `UpdateFileSystem` ([record](verification-results-cloudwatch-monitoring.md#real-ssd-increase-2026-10-12-run)). The cooldown after it and the notify-topic e-mail are `unverified` | Commit SHA; the tag `terraform-fsxn-ssd-auto-increase-v0.1.0` is not yet created and is planned after the open decision-archive item of the T4 completion criteria is recorded (no T4 tag existed when this page was updated). `?ref=<commit-sha>&depth=1` failed on 2026-10-12 with "setting depth requires ref to be a branch or tag name", so omit `depth` for a commit SHA. [Obtaining the module](../../terraform/fsxn-ssd-auto-increase/README.md#obtaining-the-module); design in [capacity-automation-t4-design.md](capacity-automation-t4-design.md) |

> **Record note**
>
> If another page states a different status for a module, the dated records linked in this table are the reference.

> **Verification scope note**
>
> Each run is a sample run on one file system in one Region, not a production estimate. The 2026-10-12 run used one throwaway file system, since deleted. On a different shape (second generation, more than one HA pair, another Region), apply in a non-production account first.

## Recommended deployment order

1. Read the sizing page and pick thresholds: [How to choose thresholds](sizing-and-headroom.md#how-to-choose-thresholds). Most common blocker: `capacity_threshold_percent` in T1 accepts 50 to 95 only.
2. Apply T1 ([`fsxn-monitoring-dashboard`](../../terraform/fsxn-monitoring-dashboard/README.md#prerequisites)). Most common blocker: provider constraints intersect, so a root that pins `hashicorp/aws` below 6.67.0 (for example `~> 6.60.0`) fails `terraform init` with "no available releases match the given constraints".
3. Apply T2 ([`fsxn-ontap-custom-metrics`](../../terraform/fsxn-ontap-custom-metrics/README.md#prerequisites)). Most common blocker: TCP 443 from the Lambda subnets to the management endpoint. The ingress rule on the file system security group is added outside Terraform after apply. This step could not be exercised on 2026-10-12: the file system's default security group already allowed TCP 443 from the VPC CIDR.
4. Build the path that delivers the audit log to T3 with the [syslog VPC endpoint setup guide](syslog-vpce-setup-guide.md) and the CloudFormation template `shared/templates/syslog-vpce-cloudwatch.yaml`. Use the current template: it writes the security group description as `GroupDescription: >-`. A copy taken before that fix writes `GroupDescription: >`, a folded scalar that keeps a trailing newline; EC2 rejects it with "Invalid security group description" and the stack ends in `ROLLBACK_COMPLETE` (observed on 2026-10-09). If you deploy such a copy, change that one line to `>-` first. The note in [Step 1](syslog-vpce-setup-guide.md#step-1-deploy-cloudformation-stack) also covers deleting the log group a failed stack leaves behind. Once the stack exists, no event arrives until ONTAP audit forwarding points at the syslog endpoint ([Step 3](syslog-vpce-setup-guide.md#step-3-configure-ontap-log-forwarding)). These steps deliver the audit log only, not EMS events. EMS events need their own destination: see the [EMS event note](#recommended-deployment-order).
5. Apply T3 ([`fsxn-log-alarm`](../../terraform/fsxn-log-alarm/README.md#prerequisites)). Most common blocker: `log_group_name` must equal the log group the delivery path writes to (template default `/syslog/fsxn-admin-audit`). Before apply, check the user that `privileged-operations` watches; the default `"fsxadmin:fsxadmin" -"Pending"` counts completed `fsxadmin` operations (see the [detection note](#minimal-root-module)).
6. Optionally, add T4 after you have operated the T1 capacity alarms ([`fsxn-ssd-auto-increase`](../../terraform/fsxn-ssd-auto-increase/README.md#prerequisites)). Monitoring does not need it. The default `notify_only` reports decisions and does not change the file system. Most common blockers: two prerequisites, an S3 Object Lock bucket for T4's decision record, prepared outside the module, and `ignore_changes` on storage capacity first for a Terraform-managed file system ([Terraform-managed file systems](capacity-automation.md#terraform-managed-file-systems)).

> **Provider version note**
>
> Each module declares only a lower bound (`hashicorp/aws >= 6.67.0`, plus `hashicorp/archive >= 2.8.1` for T2) and was tested with 6.67.0. Pin exact versions in your root and keep its own `.terraform.lock.hcl` under version control; Terraform does not read a module's lock file on behalf of the caller.

> **Network note**
>
> The T2 ingress rule on the file system security group lives outside Terraform state. Revoke it before `terraform destroy`, or deleting the Lambda security group fails with `DependencyViolation` ([Removing](../../terraform/fsxn-ontap-custom-metrics/README.md#removing)). Destroy waited about 22 minutes on the Lambda security group (observed twice: 22 minutes in the 2026-10-08 run, and 22 minutes 13 seconds on 2026-10-12, when no ingress rule had been added).

> **VPC endpoint conflict note**
>
> Two interface endpoints for the same service cannot coexist in one VPC with private DNS. If the VPC already has a `monitoring` or `secretsmanager` interface endpoint, leave `create_monitoring_endpoint` and `create_secretsmanager_endpoint` at `false` and set `aws_api_egress_cidr_blocks` to the VPC CIDR instead ([Network options](../../terraform/fsxn-ontap-custom-metrics/README.md#network-options)). On 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps), in a subnet without NAT, `create_monitoring_endpoint = true` with `create_secretsmanager_endpoint = false` (an endpoint already existed) and `aws_api_egress_cidr_blocks` set to the VPC CIDR applied and polled (`verified`, [record](verification-results-cloudwatch-monitoring.md#environment-and-deployment-2026-10-12-run)).

> **ONTAP user role note**
>
> Two AWS pages disagree about file-system roles (`documented`, read on 2026-10-11): [ONTAP roles and users](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/roles-and-users.html) lists `fsxadmin-readonly` as assignable to a file-system user, while [Creating ONTAP users](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/create-new-ontap-users.html) says the only file-system role that can be specified is `fsxadmin`. Over the ONTAP REST API, `POST /api/security/accounts` with role `fsxadmin-readonly` returned HTTP 201 on 2026-10-08 ([T2 record](verification-results-cloudwatch-monitoring.md#terraform-custom-metrics-module-run-on-2026-10-08)) and on 2026-10-11 ([record](verification-results-cloudwatch-monitoring.md#qtree-quota-monitor-run-with-a-read-only-ontap-account-on-2026-10-11)), and with role `readonly` it was rejected on 2026-10-09 ([T3 record](verification-results-cloudwatch-monitoring.md#terraform-log-alarm-module-run-on-2026-10-09)). The T2 record shows the 2026-10-08 account created through that call and serving every collector request, but does not print the status code. T1 and T3 hold no ONTAP credential; of the three monitoring modules, only T2 logs in to ONTAP. Details: [Prerequisites](../../terraform/fsxn-ontap-custom-metrics/README.md#prerequisites).

> **Delivery path note**
>
> This repository has no Terraform module for the syslog delivery path. Deploy the CloudFormation template (a copy taken before the fix needs the `GroupDescription` change in step 4), or build an equivalent in your own Terraform code. The template's log group has `DeletionPolicy: Retain`, so it remains when the stack is deleted. In the 2026-10-09 run, the first operation after a node's connection had been idle for about 4–5 minutes was lost three times; a detection that depends on a single line can miss that operation ([First operation lost after an idle connection](syslog-vpce-setup-guide.md#first-operation-lost-after-an-idle-connection)).

> **EMS event note**
>
> The `autosize-fail` recipe matches lines of the EMS event `wafl.vol.autoSize.fail`, which the audit destination does not carry. Getting EMS lines into the same log group needs a separate ONTAP EMS notification destination that sends to the same syslog endpoint. On 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps), one definition did this and delivered a real `wafl.vol.autoSize.fail` event, and `autosize-fail` went OK → ALARM 42 seconds after the event (`verified`). The destination type defaults to port 514 and `udp_unencrypted`, which do not match the syslog endpoint's TCP ports, so the definition sets port 1514 and `tcp_unencrypted` explicitly and refers to an EMS filter that includes `wafl.vol.autoSize.*`. The delivered line carries the event name in brackets, so the shipped pattern matched without a change, and its severity is `notice`, both in the line and in the ONTAP 9.19.1P3 EMS catalog. The definition, the delivered line and the alarm screenshot are in the [EMS destination recipe](verification-results-cloudwatch-monitoring.md#ems-destination-recipe-2026-10-12-run). This repository still has no setup step for it; the setup guide covers only the audit destination.

> **Irreversibility note**
>
> On a first-generation file system an SSD capacity increase cannot be undone, and any SSD, IOPS or throughput change starts a 6-hour cooldown before the next one. Read [capacity-automation.md](capacity-automation.md) before switching T4 to `approve` or `auto`, or before enabling anything else that changes capacity.

## Selection flowchart

```mermaid
flowchart TD
    A["CloudWatch chosen as the collection route?"] -->|No| H["Decide the route in the Hub: Adoption Playbook - Observability"]
    A -->|Yes| B["What do you need to watch?"]
    B --> C["File system capacity, throughput, CPU, disk, per-volume capacity and inodes"]
    C --> T1["T1 fsxn-monitoring-dashboard"]
    B --> D["Qtree quota usage"]
    D --> T2Q["T2 fsxn-ontap-custom-metrics, qtree collector"]
    B --> E["SnapMirror health and lag"]
    E --> T2S["T2 fsxn-ontap-custom-metrics, SnapMirror collector on the destination file system"]
    B --> F["Admin audit operations: authorization denials, deletes, privileged-user activity"]
    F --> S["Syslog delivery path for the audit log (CloudFormation template)"]
    S --> T3["T3 fsxn-log-alarm"]
    B --> E2["EMS events such as wafl.vol.autoSize.fail"]
    E2 --> EMS["Separate EMS notification destination to the same log group (recipe verified 2026-10-12)"]
    EMS --> T3
    B --> G["Act on an SSD capacity alarm"]
    G --> CA["Compare the options in capacity-automation.md: AWS sample, T4, or runbook"]
    CA -->|Guarded sample chosen| T4["T4 fsxn-ssd-auto-increase, optional, from notify_only"]
```

## Minimal root module

All values below are placeholders; replace them before use. The example calls the monitoring modules T1 to T3 only and leaves out the optional T4 (how to call T4 is in the [T4 README](../../terraform/fsxn-ssd-auto-increase/README.md#deploying-from-examplesbasic)). The T2 network defaults assume a NAT gateway. A root module of this shape was applied for real from tags on 2026-10-12 (`verified`, 2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps; T3 at `v0.1.0`, and `v0.1.1` was not run live; [record](verification-results-cloudwatch-monitoring.md#environment-and-deployment-2026-10-12-run)), with the three T2 network inputs from the VPC endpoint conflict note, because the subnet had no NAT. As written, the three SNS topics are not encrypted; the notification note after the block lists what to change.

```hcl
terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.67.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "= 2.8.1"
    }
  }
}

provider "aws" {
  region = "ap-northeast-1"
}

# T1: dashboard and alarms on native AWS/FSx metrics (AWS APIs only)
module "fsx_ontap_dashboard" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-monitoring-dashboard?ref=terraform-fsxn-monitoring-dashboard-v0.1.1&depth=1"

  file_system_id             = "fs-0123456789abcdef0"
  file_system_name           = "fsx-for-ontap-prod"
  capacity_threshold_percent = 80
  notification_email         = "ops@example.com"
  volume_ids                 = ["fsvol-0123456789abcdef0"]
}

# T2: qtree quota and SnapMirror metrics from the ONTAP REST API (VPC Lambda)
# Defaults assume a NAT gateway; see "Network options" in the module README.
module "fsx_ontap_custom_metrics" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ontap-custom-metrics?ref=terraform-fsxn-ontap-custom-metrics-v0.1.0&depth=1"

  file_system_id               = "fs-0123456789abcdef0"
  ontap_management_ip          = "198.51.100.10"
  ontap_credentials_secret_arn = "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ontap-monitor-XXXXXX"
  vpc_id                       = "vpc-0123456789abcdef0"
  subnet_ids                   = ["subnet-0123456789abcdef0"]
  qtree_svm_name               = "svm-prod-01"
  notification_email           = "ops@example.com"
}

# T3: alarms on the admin audit log that the syslog VPC endpoint path writes
module "fsx_ontap_log_alarm" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-log-alarm?ref=terraform-fsxn-log-alarm-v0.1.1&depth=1"

  log_group_name     = "/syslog/fsxn-admin-audit"
  notification_email = "ops@example.com"

  # No detections block: the four shipped recipes apply. autosize-fail also
  # needs an EMS notification destination (see the EMS event note).
}
```

The ingress rule on the file system security group for T2 (source: the `lambda_security_group_id` output) is added outside this configuration, as the [T2 prerequisites](../../terraform/fsxn-ontap-custom-metrics/README.md#prerequisites) describe.

> **Module source note**
>
> Each module accepts a git source, an archive URL, or a relative local path. An absolute local path for T2 failed in the 2026-10-08 run, because its Lambda source (`shared/lambda/ontap_metrics/`) sits outside the module directory. A sparse checkout for T2 must include `shared/lambda/ontap_metrics`.

> **Notification note**
>
> This example creates three SNS topics and sends three email confirmations. None of the three topics is encrypted at rest. T1 and T2 accept neither a KMS key nor an existing topic ARN; where every topic must be encrypted, leave `notification_email` empty on T1 and T2 and route the alarm state changes to an encrypted topic from your own configuration, as the T2 README describes. T3 encrypts the topic it creates when `sns_kms_master_key_id` is set, or takes a topic you own through `alarm_sns_topic_arn` and creates none. A CloudWatch alarm cannot publish to a topic encrypted with the AWS managed key `alias/aws/sns`; use a customer managed key whose key policy allows CloudWatch to use it ([AWS re:Post Knowledge Center](https://repost.aws/knowledge-center/cloudwatch-configure-alarm-sns)). The T2 heartbeat alarms go to ALARM right after apply and to OK after the first poll (observed on 2026-10-08 and 2026-10-12). E-mail from the T3 topic arrived for three notifications on 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps; arrival times not recorded); delivery from the T1, T2 and T4 topics is `unverified`.

> **Detection note**
>
> The example omits `detections`, so the four default recipes apply. The three audit defaults were replaced after the 2026-10-09 run. `failed-access` is `"Error: not authorized"`; it counts requests that ONTAP rejected at authorization, not failed logins. `privileged-operations` is `"fsxadmin:fsxadmin" -"Pending"`; it counts one line per completed `fsxadmin` operation. To watch another user, replace `fsxadmin:fsxadmin` with that user's `<user>:<role>` token. `bulk-delete` is a regular expression that counts its three terms on the result line only (`:: Success` or `:: Error`). All three patterns drove live alarms at the default 300-second period, with the default thresholds and evaluation windows, on 2026-10-12 (`verified`, 2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps; T3 at `v0.1.0`; [record](verification-results-cloudwatch-monitoring.md#check-results-2026-10-12-run)), and the new `bulk-delete` pattern counted 1 line per completed delete (56 deletes, 56 lines). No EMS lines reach `autosize-fail` until an EMS destination is added (see the [EMS event note](#recommended-deployment-order)). Setting `detections` replaces all four, so copy the values of the recipes you keep from [Detection recipes](../../terraform/fsxn-log-alarm/README.md#detection-recipes).

> **Naming note**
>
> The default `name_prefix` differs per module, so calling all four from one root does not collide. For several file systems, give each module instance its own `name_prefix`. Qtree series carry `SvmName` but no `FileSystemId`, so two file systems with the same SVM name in one account and Region share those series ([cross-file-system SnapMirror](../../terraform/fsxn-ontap-custom-metrics/README.md#cross-file-system-snapmirror)).

> **Cost note**
>
> This page gives no dollar total. Interface endpoints are billed per endpoint, per Availability Zone, per hour; T2 adds Lambda invocations and custom metric series. The formulas are in the [T2 cost section](../../terraform/fsxn-ontap-custom-metrics/README.md#cost).

## Verified and not verified

Verified, on a first-generation `SINGLE_AZ_1` file system with one HA pair in `ap-northeast-1` (dates in the [module map](#module-map)):

- T1 dashboard and alarms, including per-volume alarms and the capacity alarm on real data
- T2 qtree and SnapMirror series against real ONTAP responses, the heartbeat alarms (ALARM before the first poll, OK after it), and the SnapMirror unhealthy and lag alarms going OK → ALARM → OK
- T3 deployment, every alarm leaving INSUFFICIENT_DATA, and OK → ALARM → OK on real audit lines for `bulk-delete`, `privileged-operations` and a failed-access detection counting REST 403s, with a 60-second test period and test thresholds ([2026-10-09 record](verification-results-cloudwatch-monitoring.md#terraform-log-alarm-module-run-on-2026-10-09)). The `privileged-operations` and failed-access patterns used are the current defaults; the `bulk-delete` pattern used is the one from before the replacement
- T4 `notify_only` on a real alarm transition, `approve`, the alarm-OK branch, and `auto` behind an explicit IAM deny on `fsx:UpdateFileSystem` (the `blocked` latch, an operator clear, at most one call per evaluation, lock lease contention), deploy-time and run-time fail-closed checks, and compliance-mode retention of the archived versions read, with no capacity change ([2026-10-09 record](verification-results-cloudwatch-monitoring.md#terraform-ssd-auto-increase-module-run-on-2026-10-09))

Verified on 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps), one throwaway file system, since deleted, all times UTC ([record](verification-results-cloudwatch-monitoring.md#terraform-monitoring-modules-live-run-on-2026-10-12)):

- The root module on this page applied from tags (44 resources; T3 at `v0.1.0`), with the T2 network inputs for a subnet without NAT
- T2 heartbeat alarms, the default 5-minute poll, and `qtree-quota-high` going OK → ALARM → OK on a real quota
- T3 `failed-access`, `bulk-delete` and `privileged-operations` going OK → ALARM → OK at the default 300-second period with the default thresholds and evaluation windows, and the new `bulk-delete` default counting 1 line per completed delete
- T3 `autosize-fail` firing on a real EMS event delivered through an EMS notification destination to the same log group
- SNS e-mail for three T3 notifications (arrival times not recorded)
- T4's real increase from 1,024 to 1,127 GiB with exactly one `UpdateFileSystem` call, and three decision-archive objects under COMPLIANCE retention for the `auto` run

These results come from the environments above. Other configurations (second-generation file systems, more than one HA pair, SnapMirror across SVMs or file systems) and the least-privilege IAM policies of T2 to T4 were not verified.

> **SnapMirror coverage note**
>
> In the 2026-10-08 run, an uninitialized SnapMirror relationship reported `healthy: true` and raised no alarm (observed once); see the [record](verification-results-cloudwatch-monitoring.md#terraform-custom-metrics-module-run-on-2026-10-08).

## FAQ and common misconceptions

**Q** Is this one module or several?

**A** Four independent modules, of which monitoring uses three, T1 to T3. You can call them from one root, as in the example above, or from separate roots with separate state. T1 alone is a valid start. T3 depends on the syslog delivery path, not on T1 or T2. T4 creates its own trigger alarm and does not depend on T1, but the recommended order adds it only after the capacity alarms have been in operation.

**Q** How do these modules relate to the CloudFormation templates?

**A** T1 corresponds to `shared/templates/fsxn-monitoring-dashboard.yaml`, the T2 qtree collector to `shared/templates/qtree-quota-monitor.yaml` (the SnapMirror collector exists only in T2), and T3 to `shared/templates/cloudwatch-log-alarm.yaml`. T4 has no template counterpart either. The CloudFormation log-alarm template uses the native `AWS::CloudWatch::LogAlarm` with a Logs Insights query; T3 uses a metric filter pattern plus a metric alarm ([deliberate differences](../../terraform/fsxn-log-alarm/README.md#deliberate-differences-from-the-cloudformation-template)). The template's failed-access, bulk-delete and specific-user queries were translated from T3's replaced patterns and are `unverified` as LogAlarm queries. Terraform suits teams that already review and hold state in Terraform; it needs a state backend and provider pinning. CloudFormation suits teams that already deploy with stacks or StackSets; it needs one stack per template. The syslog delivery path is a CloudFormation template in both cases.

> **Choice note**
>
> Choose by where your infrastructure state already lives. Neither option replaces the other. Mixing them works too: for example, the syslog delivery path as a CloudFormation stack and T1 to T3 in Terraform, which is the order on this page.

**Q** Does this work on a second-generation file system?

**A** It has not been tested. On T1, set `file_server_names` so the opt-in alarms use the documented dimension set ([second-generation file systems](../../terraform/fsxn-monitoring-dashboard/README.md#second-generation-file-systems)). Apply in a non-production account first.

**Q** Which file system does the SnapMirror collector poll?

**A** The destination. Use one T2 instance per destination file system, each with its own management IP, credential, network path and `name_prefix`. The module never calls the source file system. A relationship across two file systems is untested ([cross-file-system SnapMirror](../../terraform/fsxn-ontap-custom-metrics/README.md#cross-file-system-snapmirror)).

**Q** Is SSD auto-increase recommended?

**A** Not as a default. T4 is implemented and offline-verified, and its reversible paths were live-verified on 2026-10-09. One real increase on a throwaway file system on 2026-10-12 (2026-10-12, ap-northeast-1, ONTAP 9.19.1P3, first-generation SINGLE_AZ_1 with one HA pair, 128 MBps) took it from 1,024 to 1,127 GiB through one `UpdateFileSystem` call; the cooldown after it was not observed ([record](verification-results-cloudwatch-monitoring.md#real-ssd-increase-2026-10-12-run)). Start in the default `notify_only`, observe its decisions, then move to `approve` and `auto`. An alarm plus a runbook remains a valid option. Compare the options in [capacity-automation.md](capacity-automation.md).

**Q** My file system itself is managed by Terraform. Does that change anything?

**A** The monitoring modules take the file system ID as an input and do not manage the file system. If any automation changes `storage_capacity`, add `ignore_changes` in the code that builds the file system ([Terraform-managed file systems](capacity-automation.md#terraform-managed-file-systems)).

**Q** Where do I ask a question this page does not answer?

**A** In [GitHub Discussions, Q&A category](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/discussions/categories/q-a). Answered threads stay searchable for the next person with the same question. Report defects in a module or document as Issues.

## Related Documents

- [Monitoring design](monitoring-design.md): metric catalog, alert design, and the [Terraform implementation phases](monitoring-design.md#terraform-implementation-phases)
- [Sizing and headroom](sizing-and-headroom.md): sizing rules and the threshold table
- [Capacity automation](capacity-automation.md): options for acting on SSD capacity alarms
- [T4 design](capacity-automation-t4-design.md): state machine, archive, IAM and test plan of the guarded SSD auto-increase module
- [CloudWatch monitoring verification results](verification-results-cloudwatch-monitoring.md): the dated runs behind the module map
- [Syslog VPC endpoint setup guide](syslog-vpce-setup-guide.md): the delivery path that brings the audit log to T3
- [CloudWatch Log Alarm](cloudwatch-log-alarm.md): the CloudFormation counterpart of T3
- [T1 module README](../../terraform/fsxn-monitoring-dashboard/README.md): inputs, IAM, deploy, verify, remove
- [T2 module README](../../terraform/fsxn-ontap-custom-metrics/README.md): inputs, network options, IAM, deploy, verify, remove
- [T3 module README](../../terraform/fsxn-log-alarm/README.md): inputs, detection recipes, deploy, verify, remove
- [T4 module README](../../terraform/fsxn-ssd-auto-increase/README.md): guards, inputs, IAM, deploy, remove
- [Deployment Guide](deployment-guide.md): CloudFormation deployment of the vendor integrations
- [Adoption Playbook — Observability](https://github.com/Yoshiki0705/FSx-for-ONTAP-Adoption-Playbook/blob/main/docs/en/domains/observability/README.md): choosing the collection route
- [GitHub Discussions, Q&A](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/discussions/categories/q-a): questions this page does not answer
