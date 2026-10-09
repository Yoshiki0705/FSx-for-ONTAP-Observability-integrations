# fsxn-log-alarm (Terraform)

🌐 [日本語](README.ja.md) | **English**

Terraform equivalent of `shared/templates/cloudwatch-log-alarm.yaml`: alarms on the content of a CloudWatch Logs log group that receives one Amazon FSx for NetApp ONTAP file system's EMS and audit events. This is phase T3 of the Terraform plan in [monitoring-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)), whose metric catalog and alert design this module implements.

## Verification status

Offline: `make terraform` runs `terraform fmt -check`, `terraform init -lockfile=readonly`, `terraform validate`, and `terraform test` with a mock `aws` provider and `command = plan`. It also runs `init` and `validate` on [`examples/basic/`](examples/basic/). It needs no AWS credentials and creates nothing. The tests assert that the `autosize-fail` recipe produces a filter whose pattern contains `wafl.vol.autoSize.fail` and an alarm wired to fire on any single occurrence, that `failed-access`, `privileged-operations` and `bulk-delete` ship the patterns checked against real audit lines, and that for every detection the alarm reads the metric name and namespace its own filter emits.

Live: one sample run on 2026-10-09 at `50f1f18` ([record](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#terraform-log-alarm-module-run-on-2026-10-09)), on one first-generation, single-HA-pair file system whose admin audit log reached CloudWatch Logs through the syslog VPC endpoint path on port 1514. With a 60-second period and 1-of-1 evaluation, `terraform apply` created 6 metric filters and 6 alarms, every alarm left INSUFFICIENT_DATA, and `bulk-delete` (pattern shipped at `50f1f18`), `privileged-operations` (with the pattern that is now the default) and `failed-access-rest403` (an added `"Error: not authorized"` detection, now the `failed-access` default) went from OK to ALARM 13 to 74 seconds after the first matching operation, and back to OK in a later minute without a match. AWS accepted every pattern used (confidence: `verified` for that scope). Resource creation and alarm wiring showed no defect. Two shipped default patterns were defects, and three defaults were replaced after the run:

- `privileged-operations`: the default at `50f1f18`, `"admin"`, matched 5,150 of 5,156 real lines, because the file system's own management traffic (user `fsx-control-plane`, role `admin`) and every `fsxadmin` line contain it. With threshold 0 the alarm stayed in ALARM without any operator activity. The default is now `"fsxadmin:fsxadmin" -"Pending"`, which matched 1 line per completed `fsxadmin` operation (13 of 5,156) and drove the live alarm in the run. Replace `fsxadmin:fsxadmin` with the `<user>:<role>` token of the user to watch.
- `failed-access`: the default at `50f1f18` matched none of the 5 real REST authorization failures, whose lines end `Error: not authorized for that command`. The default is now `"Error: not authorized"`, which matched all 5, 1 line per rejected request, and drove the live alarm in the run. A wrong-password login (HTTP 401) wrote no audit line, so neither pattern detects it.
- `bulk-delete`: the default at `50f1f18` fired as intended on 4 REST deletes with threshold 2, but each change operation is logged twice (`Pending`, then the result), so it counted about 2 lines per operation. The default now matches the same three terms only on the result line (`:: Success` or `:: Error`), 1 line per completed delete (5 of 5,156), so the threshold counts operations. It was checked with `aws logs test-metric-filter` only, not on a live alarm, and only REST deletes were captured.
- `autosize-fail`: not fired by a real `wafl.vol.autoSize.fail` event (`unverified`). The syslog setup guide forwards only the audit log; EMS events reach the log group only through a separate EMS notification destination, which was neither set up nor tested. The pattern matched a line built from the ONTAP 9.18.1 EMS reference with an assumed header.
- `unauthorized-access`: not run; its pattern is a placeholder path.

The new defaults were checked on 2026-10-09 with `aws logs test-metric-filter` against the masked sample lines in the record and all 5,156 captured lines; the counts are in the [record](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#filter-pattern-matches-on-real-lines-t3-run). Still `unverified`: the default 300-second period, thresholds and N/M values, SNS notification, the deployer IAM policy, and second-generation and multi-HA-pair file systems. The delivery path lost the first operation after a node's connection had been idle for about 4–5 minutes, three times in the run ([setup guide](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/syslog-vpce-setup-guide.md#first-operation-lost-after-an-idle-connection)); a detection that depends on a single line can miss that operation.

## What it creates

- One `aws_cloudwatch_log_metric_filter` per detection, on `log_group_name`, turning each detection's log pattern into a count metric in `metric_namespace`. The transformation sets `default_value = "0"`, so the metric reports 0 in windows with no match.
- One `aws_cloudwatch_metric_alarm` per detection, on that metric, with `statistic = "Sum"`, `treat_missing_data = "notBreaching"` (the template's `TreatMissingData: notBreaching`), and the detection's threshold, comparison operator, period, evaluation periods, and M-out-of-N datapoints.
- `aws_sns_topic` and an email `aws_sns_topic_subscription`, only when `notification_email` is set and no `alarm_sns_topic_arn` is given. Every alarm then notifies on ALARM and OK, and the recipient must confirm the subscription before email arrives. The module-created topic is unencrypted by default (the console default for a new topic); set `sns_kms_master_key_id` to encrypt it. To match the CloudFormation template, which creates no topic and takes a caller-owned `AlarmSnsTopicArn`, set `alarm_sns_topic_arn` instead: the module then creates no topic and leaves ownership, encryption, subscriptions, and delivery policy to you.

The mechanism is metric filter plus metric alarm, not a native log alarm resource. The CloudFormation template uses `AWS::CloudWatch::LogAlarm`, which alarms directly off a scheduled CloudWatch Logs Insights query. The pinned `hashicorp/aws` 6.67.0 ships no equivalent resource (no `aws_cloudwatch_log_alarm` and no scheduled-query resource), checked against the [provider's v6.67.0 resource index](https://github.com/hashicorp/terraform-provider-aws/tree/v6.67.0/website/docs/r) (confidence: `verified-in-repo`). This module therefore uses the alternative that [monitoring-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md) names, with the trade-offs stated in [Deliberate differences from the CloudFormation template](#deliberate-differences-from-the-cloudformation-template).

## Detection recipes

`detections` is a map; each key names one metric filter and one alarm. The default ships five recipes. A caller can override a recipe, drop one, or add a key to cover the template's `custom` type. Patterns are [CloudWatch Logs filter-pattern](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html) strings (quoted substrings that match anywhere in the event; `?"a" ?"b"` is an OR), not Logs Insights queries.

| Key | Maps to template `DetectionType` | Filter pattern | Threshold / N / M |
|---|---|---|---|
| `autosize-fail` | new in T3: EMS `wafl.vol.autoSize.fail` | `"wafl.vol.autoSize.fail"` | 0 / 1 / 1 |
| `failed-access` | `failed-access-attempts` | `"Error: not authorized"` | 10 / 3 / 3 |
| `bulk-delete` | `bulk-delete-operations` | `%DELETE.*::\sSuccess\|DELETE.*::\sError\|delete.*::\sSuccess\|delete.*::\sError\|remove.*::\sSuccess\|remove.*::\sError%` | 50 / 3 / 2 |
| `privileged-operations` | `specific-user-activity` | `"fsxadmin:fsxadmin" -"Pending"` (replace with the `<user>:<role>` to watch) | 0 / 3 / 1 |
| `unauthorized-access` | `sensitive-file-access` | `"/vol/data/confidential"` (replace with the path) | 0 / 3 / 1 |

Each audited ONTAP change operation writes two lines, one ending `:: Pending` and one ending with the result (`:: Success:` or `:: Error: ...`). The `failed-access`, `privileged-operations` and `bulk-delete` defaults match only the result line, so their thresholds count operations. They replaced `?"Failure" ?"denied" ?"DENIED"`, `"admin"` and `?"DELETE" ?"delete" ?"remove"` after the 2026-10-09 run ([Verification status](#verification-status)). `bulk-delete` is a regular-expression pattern, because filter-pattern OR terms cannot be combined with an exclusion; CloudWatch Logs allows at most 5 metric or subscription filters with regular-expression patterns per log group ([filter pattern syntax](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html)). `"delete"` and `"remove"` are substrings, so any completed command whose text contains them is counted.

`autosize-fail` is the EMS event this phase names explicitly: severity `error`, space exhaustion imminent, per [ems-detection-capabilities.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/ems-detection-capabilities.md). The [ONTAP 9.18.1 EMS reference](https://docs.netapp.com/us-en/ontap-ems-9181/wafl-vol-events.html) lists its severity as NOTICE; the two sources have not been reconciled. Any single occurrence fires. The template's `custom` type has no shipped key because `detections` is caller-extensible: add `{ pattern = "..." }` under a new key.

## Obtaining the module

The planned tag is `terraform-fsxn-log-alarm-v0.1.0`. It is **not yet created**, and when to create it has not been decided. The default patterns were replaced after the 2026-10-09 run, and the new `bulk-delete` pattern has not run on a live alarm. Until then, pin a commit SHA with the git source or the archive URL below. The module is not on the Terraform Registry, because it is a subdirectory of a larger repository. Download sizes were not measured for this module; [the dashboard module README](../fsxn-monitoring-dashboard/README.md#obtaining-the-module) records them for the same repository.

```hcl
# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-log-alarm?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-log-alarm"
```

The steps below check out only the module directory at a commit with a sparse checkout. Then set `source` to the local path of `terraform/fsxn-log-alarm` in this copy. Once the tag exists, use it in place of `<commit-sha>`.

```bash
git init fsx-ontap-log-alarm && cd fsx-ontap-log-alarm
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-log-alarm
git fetch --depth 1 --filter=blob:none origin <commit-sha>
git checkout FETCH_HEAD
```

## Usage

The sections below follow the order of a first deployment: prerequisites, permissions, input values, deployment, checks after apply, and removal.

### Prerequisites

- Terraform `>= 1.11.0`.
- `hashicorp/aws` `>= 6.67.0`. Tested with 6.67.0; releases 6.0 to 6.66 are untested.
- AWS credentials from IAM Identity Center or an IAM role, and the Region of the log group set in your `provider "aws"` block. The module has no `provider` block.
- A CloudWatch Logs log group that already receives the file system's EMS and audit events. This module reads that group; it does not create it or the delivery path. Pass that group's name as `log_group_name`. [syslog-vpce-setup-guide.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/syslog-vpce-setup-guide.md) sets up the syslog VPC endpoint and the ONTAP audit destination only, so the group receives the command history (`kern_audit`) and no EMS events. `autosize-fail` additionally needs a separately configured ONTAP EMS notification destination that sends to the same endpoint and log group; that step is not documented here and is `unverified`.

### Required IAM permissions (estimated, unverified)

These are the permissions for the identity that runs `terraform apply`, derived from the resources the module creates. They have not been run with a role limited to them; the dashboard module showed that tag actions are easy to miss ([its CloudTrail note](../fsxn-monitoring-dashboard/README.md#required-iam-permissions-verified)). Expect to add actions after a first apply.

| Resource | Actions |
|---|---|
| `aws_cloudwatch_log_metric_filter` (scoped to the account's log groups) | `logs:PutMetricFilter`, `logs:DeleteMetricFilter`, `logs:DescribeMetricFilters` |
| `aws_cloudwatch_metric_alarm` | `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, `cloudwatch:DeleteAlarms`, `cloudwatch:ListTagsForResource`, `cloudwatch:TagResource`, `cloudwatch:UntagResource` |
| `aws_sns_topic`, `aws_sns_topic_subscription` (only when the module creates the topic) | `sns:CreateTopic`, `sns:GetTopicAttributes`, `sns:SetTopicAttributes`, `sns:ListTagsForResource`, `sns:TagResource`, `sns:UntagResource`, `sns:DeleteTopic`, `sns:Subscribe`, `sns:GetSubscriptionAttributes`, `sns:Unsubscribe` |

The policy is in [`examples/basic/iam-policy.json`](examples/basic/iam-policy.json). The `Alarms` and `Topic` statements are scoped to the names the module builds from `name_prefix` (`fsxn-log-alarm-*` by default); replace `123456789012`, `ap-northeast-1` and the prefix before use. The `Filters` statement is scoped to the account's log groups (`log-group:*`), because the log group is a caller-supplied input, not a name the module builds from the prefix; narrow it to your log group's ARN if you prefer. All three metric-filter actions, including `logs:DescribeMetricFilters`, take the log-group resource type per the [service authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_logs.html) (confidence: `documented`, verified 2026-10-09 from that page). With an existing `alarm_sns_topic_arn`, the `Topic` statement is not needed. The read-only commands in [Verifying after apply](#verifying-after-apply) are meant for your own credentials.

### Deploying from examples/basic

Run these from the directory that contains `terraform/` (a full clone or the sparse checkout above). In `terraform.tfvars`, set the required values and any optional inputs.

```bash
cd terraform/fsxn-log-alarm/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: region, log_group_name, and any optional inputs
terraform init
terraform plan
terraform apply
```

In your own root configuration, copy [`examples/basic/`](examples/basic/) and replace `source = "../.."` with one of the sources in [Obtaining the module](#obtaining-the-module):

```hcl
module "fsx_ontap_log_alarm" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-log-alarm?ref=<commit-sha>"

  log_group_name     = "/syslog/fsxn-admin-audit"
  notification_email = "ops@example.com"

  # Omit detections to use the five shipped recipes, or override and extend:
  detections = {
    autosize-fail = { pattern = "\"wafl.vol.autoSize.fail\"" }
    my-custom     = { pattern = "\"ERROR\"", threshold = 0 }
  }
}
```

### Verifying after apply

Replace `fsxn-log-alarm` with your `name_prefix`, and `/syslog/fsxn-admin-audit` with your log group:

```bash
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-log-alarm \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table
aws logs describe-metric-filters --log-group-name /syslog/fsxn-admin-audit \
  --query 'metricFilters[].{Name:filterName,Pattern:filterPattern}' --output table
```

Alarms stay in INSUFFICIENT_DATA until enough datapoints have arrived for their evaluation periods; with `treat_missing_data = "notBreaching"`, a window with no match counts as not breaching. In the 2026-10-09 run, with a 60-second period, every alarm left INSUFFICIENT_DATA within about 2 minutes of `apply`. A window in which no log line arrives at all has no datapoint, because `default_value` is emitted only for lines that arrive and do not match. With `notification_email` set, the email subscription stays pending until the recipient confirms it.

### Removing

```bash
terraform destroy
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-log-alarm \
  --query 'MetricAlarms[].AlarmName' --output text
```

The second command should print nothing once every alarm is gone.

## Inputs

| Name | Type | Default | Notes |
|---|---|---|---|
| `log_group_name` | string | required | `^[A-Za-z0-9_./#-]{1,512}$` (template `LogGroupName`) |
| `name_prefix` | string | `"fsxn-log-alarm"` | Plays the role of the stack name in resource names |
| `metric_namespace` | string | `"FSxONTAP/LogAlarm"` | Namespace for the count metrics |
| `notification_email` | string | `""` | Module creates and subscribes a topic when set and `alarm_sns_topic_arn` is empty |
| `alarm_sns_topic_arn` | string | `""` | Existing caller-owned topic (template `AlarmSnsTopicArn`); when set, no topic is created |
| `sns_kms_master_key_id` | string | `""` | KMS key for the module-created topic; empty leaves it unencrypted |
| `detections` | map(object) | five recipes | One filter and one alarm per key; see [Detection recipes](#detection-recipes) |
| `tags` | map(string) | `{}` | Alarms and SNS topic. Metric filters do not take tags |

Each `detections` value is an object: `pattern` (required), `threshold` (0), `comparison_operator` (`GreaterThanThreshold`), `evaluation_periods` (1), `datapoints_to_alarm` (1), `period_seconds` (300), `metric_value` (`"1"`), `alarm_description` (`""`, generated when empty). `period_seconds` must be one of 60, 300, 600, 900, 1800, 3600 (the template's `EvaluationFrequencyMinutes` set, in seconds). `evaluation_periods` (N) and `datapoints_to_alarm` (M) must each be an integer in 1-100 (the API minimum is 1; the template caps both at 100), and M must not exceed N. The combined `name_prefix-detectionkey` must stay within 255 characters, the CloudWatch alarm-name and metric-name limit.

## Outputs

| Name | Description |
|---|---|
| `metric_filter_names`, `alarm_names`, `alarm_arns` | Maps keyed by detection |
| `metric_namespace` | The namespace the filters emit into |
| `sns_topic_arn` | Effective topic ARN (existing or created), or `null` when neither is configured |
| `sns_topic_created` | `true` when the module owns the topic, `false` for an existing topic or none |

## Deliberate differences from the CloudFormation template

The template uses `AWS::CloudWatch::LogAlarm`; this module uses metric filter plus metric alarm, because the pinned provider has no native log alarm resource. The trade-offs are symmetric:

- Metric filter plus metric alarm (this module): no scheduled-query execution role and no log-line role; the count metric is usable on dashboards and with anomaly detection; it evaluates continuously on `period`. It cannot include matching log lines in the notification (the template's `ActionLogLineCount` has no equivalent); it matches on filter-pattern terms, not full Logs Insights query syntax (no aggregations or time math); and it counts only events ingested after the filter is created, not retroactively.
- LogAlarm (the CloudFormation template): a single resource; a full Logs Insights query; it can include up to 50 matching log lines in the notification; it is retroactive over existing logs. It needs a scheduled-query execution role (and an optional log-line role); and it is not available as a native Terraform resource in the pinned provider.

Cost: both paths bill on their own CloudWatch meters, so compare them per account rather than treating one as free. This module creates one custom metric and one metric alarm per detection; a custom metric is billed per metric per month and a metric alarm is billed per alarm per month, and the metric filter itself adds no charge beyond the metric it emits. LogAlarm has no custom metric, but its scheduled Logs Insights query is billed per gigabyte scanned on each run, so its cost scales with log volume and query frequency rather than with a per-metric count. Check the current rates and free-tier allowances for your Region on the [Amazon CloudWatch pricing page](https://aws.amazon.com/cloudwatch/pricing/) before sizing either path (rates were not re-measured for this document; last read 2026-10-09).

How to choose: for a count metric on dashboards or anomaly detection and no new IAM roles, use this module (T3). For matching log lines in the alert or a retroactive Logs Insights query, use the CloudFormation [`cloudwatch-log-alarm.yaml`](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/shared/templates/cloudwatch-log-alarm.yaml) template.

Other differences from the template's defaults: `ok_actions` is set alongside `alarm_actions`; `detections` is a data-driven map rather than a `DetectionType` enum, so several detections can run at once and callers can add their own. For notification, set `alarm_sns_topic_arn` to match the template exactly (caller-owned topic, no resource created here); the module can instead own a topic when you set `notification_email`, in which case `sns_kms_master_key_id` controls its encryption.

## Provider version constraint

The module declares `hashicorp/aws` `>= 6.67.0`, the lowest release it was tested with, and no upper bound. [`examples/basic/`](examples/basic/) carries the exact pin `= 6.67.0` and its own `.terraform.lock.hcl`. Callers do not use this module's `.terraform.lock.hcl`: Terraform reads the lock file of the root configuration ([Dependency Lock File](https://developer.hashicorp.com/terraform/language/files/dependency-lock)). The module's lock file serves only this repository's `make terraform` and CI. Terraform `>= 1.11.0` is required because the tests use `override_during = plan`.
