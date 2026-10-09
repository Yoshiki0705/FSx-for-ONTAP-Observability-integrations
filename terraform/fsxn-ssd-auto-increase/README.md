# fsxn-ssd-auto-increase (Terraform)

🌐 [日本語](README.ja.md) | **English**

A non-VPC Lambda function that raises the SSD capacity of one Amazon FSx for NetApp ONTAP file system, only inside a required absolute ceiling and only after every guard passes. This is phase T4 of the Terraform plan in [monitoring-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)). The behaviour is specified in [capacity-automation-t4-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/capacity-automation-t4-design.md); the option comparison and the irreversibility facts are in [capacity-automation.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/capacity-automation.md).

This module shares no runtime code with the T2 custom-metrics module. T2 is an ONTAP-REST poller inside a VPC; T4 calls AWS APIs only and runs outside any VPC, so it has no security groups and no interface endpoints, and no per-AZ ENI charge.

## Verification status

Offline: `make terraform` runs `terraform fmt -check`, `terraform init -lockfile=readonly`, `terraform validate`, and `terraform test` (a mock `aws` provider and `command = plan`; the `archive` provider builds the real Lambda zip). It also runs `init` and `validate` on [`examples/basic/`](examples/basic/). The Lambda source in `shared/lambda/ssd_auto_increase/` has pytest unit tests against mocked boto3 (`fsx`, `cloudwatch`, `sns`, `s3`, `dynamodb`, `logs`) covering the guards and lock-state transitions in the design test plan, including the second pre-call snapshot (both guards reran), post-call archive-write failures pended as for `accepted`, the idempotent pending replay, the per-mode bucket-default retention matrix, blocked-report retry, the administrative-action status matrix, the `GetMetricData` utilization query (its dimensions, the value carried into reports and archive events, and the classification of a missing value), and multi-invocation chains (take-over with a new token, ceiling-reached release, delayed visibility over several runs, and a new token only after `not_accepted`). These are offline tests over mocks; the live rows in the design test plan (real file system, bucket, policy simulator) remain unrun.

Live: on 2026-10-09 the module was applied to one first-generation `SINGLE_AZ_1` file system with one HA pair and 1,024 GiB of SSD storage, with a compliance-mode archive bucket with a 1-day default retention ([record](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#terraform-ssd-auto-increase-module-run-on-2026-10-09)). Verified there: `notify_only` on a real OK → ALARM transition, `approve`, the alarm-OK branch, `auto` behind an explicit IAM deny on `fsx:UpdateFileSystem` (one denied call, the `blocked` latch reported once, silence on the next run, an operator clear), at most one call from two concurrent invocations, lease contention and expired-lease take-over, the deploy-time `auto` + `GOVERNANCE` precondition, run-time `archive_retention_unproven` on a too-short bucket retention and on a denied intent write, compliance-mode retention of the archived versions read, and an IAM policy simulation of the execution role. The function made 4 `UpdateFileSystem` calls, all denied, and the capacity did not change. Still `unverified`: the one real increase and the cooldown after it, second generation and aggregate alarms, email delivery, the deployer IAM policy (the run used administrator access), and the positive controls of the decision-archive test. Three behaviours differ from the design wording (F1–F3 in the record); they are described under [Testing and operating a deployment](#testing-and-operating-a-deployment). Apply the module in a non-production account first.

## What it creates

- `aws_lambda_function` `<name_prefix>-evaluator` (Python 3.12, 256 MB, 300 s timeout, reserved concurrency 1, handler `ssd_auto_increase_handler.lambda_handler`), outside any VPC, packaged by `data "archive_file"` from `shared/lambda/ssd_auto_increase/` (tests excluded).
- `aws_cloudwatch_event_rule` with `reevaluation_schedule` (default `rate(1 hour)`), its target and `aws_lambda_permission`. The schedule is needed because CloudWatch alarm actions fire only on a state change, so an alarm staying in ALARM would not re-trigger the function.
- `aws_sqs_queue` `<name_prefix>-dlq` (14-day retention, `alias/aws/sqs`) as the function's dead-letter queue.
- `aws_dynamodb_table` `<name_prefix>-lock` (`PAY_PER_REQUEST`, hash key `file_system_id`, no TTL) as the single-flight lock store. `expires_at` is the lease-takeover comparison value, decided in the function by comparing it to the current time; the table carries no TTL, so a durable state (`submitted`, `optimizing`, `indeterminate`, `manual_disposition_required`, `blocked`) is never deleted by the service before its designed release.
- `aws_cloudwatch_log_group` `/fsx/ssd-auto-increase/<file-system-id>` (`log_retention_days`) as the decision log. This is operational history, not the audit record.
- `aws_cloudwatch_log_group` `/aws/lambda/<name_prefix>-evaluator` (`log_retention_days`) as the function's own log group, created and retention-managed by the module rather than left to Lambda's default never-expire group.
- `aws_iam_role` `<name_prefix>-role` with an inline policy (table below).
- `aws_sns_topic` `<name_prefix>-trigger` with one Lambda subscription (the alarm invokes the function through it) and `<name_prefix>-notify` for reports and approve emails, with an email subscription only when `notification_email` is set. The function never publishes to the trigger topic, so a report cannot invoke the function again.
- `aws_cloudwatch_metric_alarm` `<name_prefix>-ssd-utilization` on `AWS/FSx` `StorageCapacityUtilization` (`StorageTier=SSD`, `DataType=All`), plus one per `aggregate_names` entry on second generation.

It does **not** create the decision-archive S3 bucket. That is an existing Object Lock bucket (`decision_archive_bucket`) administered outside the module; the function gets `s3:PutObject` on the prefix plus read-only retention checks and never sets or changes retention. Compliance-mode retention cannot be shortened, so it is a long-lived commitment the operator owns.

## Guards

| Guard | Behaviour |
|---|---|
| Ceiling | `max_storage_capacity_gib` is a required absolute ceiling, validated at deploy time (variable validation plus a precondition against the shape maximum) and re-checked at run time against `DescribeFileSystems` |
| Mode | `notify_only` (default) computes and reports; `approve` emails the command; `auto` calls the API. `auto` requires `decision_archive_required_mode = COMPLIANCE` |
| Alarm state | Every invocation reads the trigger alarm with `DescribeAlarms` and acts only while at least one is in ALARM |
| Target | `target = min(ceiling, max(ceil(current × 1.10), ceil(current × (1 + increase_percent / 100))))`; no call when the ceiling leaves no room for the 10% minimum |
| Administrative actions | No call while a `FILE_SYSTEM_UPDATE` is active or a `STORAGE_OPTIMIZATION` is not completed, read before and again immediately before the call |
| Cooldown | Defers when the last SSD, IOPS or throughput change is less than 6 hours ago |
| IOPS mode | `AUTOMATIC`: no IOPS argument. `USER_PROVISIONED`: `Iops = max(current, 3 × target)`, latching `blocked` when it exceeds the Region maximum |
| Single-flight | A DynamoDB conditional put keyed on the file system ID, plus reserved concurrency 1 on the function |
| Audit record | One S3 Object Lock object per event, retention proven at run time; fail-closed in `auto`, fail-open reporting the gap in `notify_only` and `approve` |

> **Plan-time note**
>
> Whether the provider reads `ha_pairs` at plan time for every deployment type is `open` (design "Ceiling note"). The run-time ceiling re-check in the Lambda covers the case where the precondition could not evaluate, so it is kept regardless.

## Obtaining the module

The planned tag is `terraform-fsxn-ssd-auto-increase-v0.1.0`. It is **not yet created**. The 2026-10-09 live run in [Verification status](#verification-status) left the decision-archive row of the T4 completion criteria open, so the tag is planned after that row is recorded. Until then, pin a commit SHA with the git source or the archive URL below. The module is not on the Terraform Registry, because it is a subdirectory of a larger repository.

The Lambda source is outside the module directory, in `shared/lambda/ssd_auto_increase/`. With a `//subdirectory` source, Terraform downloads and extracts the whole package and then reads the module from the subdirectory ([module block reference](https://developer.hashicorp.com/terraform/language/block/module)), so `../../shared` resolves for the sources below.

```hcl
# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ssd-auto-increase?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-ssd-auto-increase"
```

The steps below check out the module directory at a commit with a sparse checkout. Then set `source` to the local path of `terraform/fsxn-ssd-auto-increase` in this copy. The sparse checkout must include both directories, or `archive_file` fails at plan time because `shared/lambda/ssd_auto_increase` does not exist:

```bash
git init fsx-ssd-auto-increase && cd fsx-ssd-auto-increase
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-ssd-auto-increase shared/lambda/ssd_auto_increase
git fetch --depth 1 --filter=blob:none origin <commit-sha>
git checkout FETCH_HEAD
```

> **Fetch note**
>
> These steps were run with a commit SHA, and `terraform init -backend=false` and `terraform validate` passed in `examples/basic/` of that checkout. A git source with `?ref=main&depth=1` also placed `shared/lambda/ssd_auto_increase/` next to the module in the copy that `terraform init` downloaded; `plan` was not run from it. With the git source in `source`, `depth=1` cannot be combined with a SHA, as recorded in the [dashboard module README](../fsxn-monitoring-dashboard/README.md#obtaining-the-module).

## Usage

The sections below follow the order of a first deployment: prerequisites, permissions, input values, deployment, testing and operating, and removal.

### Prerequisites

- Terraform `>= 1.11.0`; `hashicorp/aws` `>= 6.67.0` and `hashicorp/archive` `>= 2.8.1` (tested with 6.67.0 and 2.8.1).
- An existing S3 bucket with Object Lock default retention, administered outside this module. For `auto` the bucket must be in compliance mode with at least `decision_archive_min_retention_days`; `notify_only` and `approve` accept a governance bucket and the retention check reports the gap without blocking.
- The file system's deployment type and HA-pair count must be one this design lists (`SINGLE_AZ_1`, `MULTI_AZ_1`, `MULTI_AZ_2`, `SINGLE_AZ_2`). A map lookup on an unknown type fails the plan on purpose.

> **Mode note**
>
> `mode` defaults to `notify_only`, so deploying the module changes nothing on the file system. Move to `approve` and then `auto` after observing the decisions. An SNS email subscription stays pending until the recipient confirms it, so confirm it before relying on `approve`.

### Required IAM permissions (estimated, unverified)

These are the permissions for the identity that runs `terraform apply`, derived from the resources the module creates. They have not been run with a role limited to them; expect to add actions after a first apply.

| Resource | Actions |
|---|---|
| `aws_lambda_function`, `aws_lambda_permission` | `lambda:CreateFunction`, `lambda:GetFunction`, `lambda:GetFunctionConfiguration`, `lambda:UpdateFunctionCode`, `lambda:UpdateFunctionConfiguration`, `lambda:PutFunctionConcurrency`, `lambda:DeleteFunctionConcurrency`, `lambda:DeleteFunction`, `lambda:AddPermission`, `lambda:RemovePermission`, `lambda:GetPolicy`, `lambda:ListVersionsByFunction`, `lambda:GetFunctionCodeSigningConfig`, `lambda:TagResource`, `lambda:UntagResource`, `lambda:ListTags` |
| `aws_iam_role` and its policy | `iam:CreateRole`, `iam:GetRole`, `iam:DeleteRole`, `iam:PassRole`, `iam:PutRolePolicy`, `iam:GetRolePolicy`, `iam:DeleteRolePolicy`, `iam:ListRolePolicies`, `iam:ListAttachedRolePolicies`, `iam:ListInstanceProfilesForRole`, `iam:TagRole`, `iam:UntagRole` |
| `aws_sqs_queue` | `sqs:CreateQueue`, `sqs:GetQueueAttributes`, `sqs:SetQueueAttributes`, `sqs:DeleteQueue`, `sqs:TagQueue`, `sqs:UntagQueue`, `sqs:ListQueueTags` |
| `aws_dynamodb_table` | `dynamodb:CreateTable`, `dynamodb:DescribeTable`, `dynamodb:DeleteTable`, `dynamodb:UpdateTimeToLive`, `dynamodb:DescribeTimeToLive`, `dynamodb:TagResource`, `dynamodb:UntagResource`, `dynamodb:ListTagsOfResource` |
| `aws_cloudwatch_log_group` (scoped to the account's log groups) | `logs:CreateLogGroup`, `logs:DeleteLogGroup`, `logs:PutRetentionPolicy`, `logs:ListTagsForResource`, `logs:TagResource`, `logs:UntagResource` |
| `aws_cloudwatch_event_rule`, `aws_cloudwatch_event_target` | `events:PutRule`, `events:DescribeRule`, `events:DeleteRule`, `events:PutTargets`, `events:RemoveTargets`, `events:ListTargetsByRule`, `events:ListTagsForResource`, `events:TagResource`, `events:UntagResource` |
| `aws_cloudwatch_metric_alarm` | `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, `cloudwatch:DeleteAlarms`, `cloudwatch:ListTagsForResource`, `cloudwatch:TagResource`, `cloudwatch:UntagResource` |
| `aws_sns_topic`, `aws_sns_topic_subscription` | `sns:CreateTopic`, `sns:GetTopicAttributes`, `sns:SetTopicAttributes`, `sns:ListTagsForResource`, `sns:TagResource`, `sns:UntagResource`, `sns:DeleteTopic`, `sns:Subscribe`, `sns:GetSubscriptionAttributes`, `sns:Unsubscribe` |
| Reading the file system at plan time (`Resource: "*"`) | `fsx:DescribeFileSystems` |

The policy is in [`examples/basic/iam-policy.json`](examples/basic/iam-policy.json). Scoped statements use the names the module builds from `name_prefix` (`fsxn-ssd-auto-increase-*` by default); replace `123456789012`, `ap-northeast-1` and the prefix before use. `fsx:DescribeFileSystems` sits on `Resource: "*"` because the [service authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_fsx.html) lists it without a resource type. The deployer never calls `fsx:UpdateFileSystem`; only the function's execution role does, and the module scopes that grant to the one caller-supplied file-system ARN (`arn:aws:fsx:...:file-system/fs-...`). The `logs` statement is scoped to the account's log groups because the decision log group is built from the file-system ID, not from `name_prefix`.

### Deploying from examples/basic

Run these from the directory that contains `terraform/` (a full clone or the sparse checkout above). In `terraform.tfvars`, set the required values and any optional inputs.

```bash
cd terraform/fsxn-ssd-auto-increase/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: region, file_system_id, max_storage_capacity_gib, bucket
terraform init
terraform plan
terraform apply
```

In your own root configuration, copy [`examples/basic/`](examples/basic/) and replace `source = "../.."` with one of the sources in [Obtaining the module](#obtaining-the-module):

```hcl
module "ssd_auto_increase" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ssd-auto-increase?ref=<commit-sha>"

  file_system_id           = "fs-0123456789abcdef0"
  max_storage_capacity_gib = 2048
  decision_archive_bucket  = "<object-lock-bucket-name>"
}
```

### Testing and operating a deployment

These steps were used in the 2026-10-09 run. Run them from `examples/basic/` and replace the placeholders.

Drive a test decision without changing the file system. Keep `mode = "notify_only"`, read the current SSD utilization, and set `trigger_threshold_percent` below it. In the run, the alarm went from OK to ALARM on real data 50 seconds after the threshold update and invoked the function once. To make the only computable target the minimum increase, set `max_storage_capacity_gib` to `ceil(current × 1.1)` (1,127 GiB for 1,024 GiB). Restore the threshold afterwards. `aws cloudwatch set-alarm-state` also works, but only until the next evaluation.

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/FSx --metric-name StorageCapacityUtilization \
  --dimensions Name=FileSystemId,Value=fs-0123456789abcdef0 Name=StorageTier,Value=SSD Name=DataType,Value=All \
  --start-time 2026-01-01T00:00:00Z --end-time 2026-01-01T01:00:00Z \
  --period 300 --statistics Average
terraform apply -var trigger_threshold_percent=3
# Afterwards
terraform apply -var trigger_threshold_percent=80
```

Test `auto` without a real call. Attach an explicit deny on `fsx:UpdateFileSystem` to the execution role before applying `mode = "auto"`, and remove it only after `mode` is back to `notify_only`. The module's role defines no inline policy of that name, so Terraform plans leave it alone. The policy simulator reads the stored policy, so its `explicitDeny` does not show that the change has reached every endpoint; IAM changes are eventually consistent ([IAM troubleshooting](https://docs.aws.amazon.com/IAM/latest/UserGuide/troubleshoot_general.html#troubleshoot_general_eventual-consistency)). Wait before the first `auto` run. In the run, the deny had been in place for 4 minutes before the first call, and that call was denied.

```bash
aws iam put-role-policy --role-name fsxn-ssd-auto-increase-role \
  --policy-name deny-update-file-system \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Deny","Action":"fsx:UpdateFileSystem","Resource":"*"}]}'
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/fsxn-ssd-auto-increase-role \
  --action-names fsx:UpdateFileSystem \
  --resource-arns arn:aws:fsx:ap-northeast-1:123456789012:file-system/fs-0123456789abcdef0 \
  --query 'EvaluationResults[0].EvalDecision'
# After mode is back to notify_only
aws iam delete-role-policy --role-name fsxn-ssd-auto-increase-role \
  --policy-name deny-update-file-system
```

What each run leaves behind, as observed in the run:

- An evaluation that makes no call (alarm OK, administrative action in progress, cooldown, ceiling reached) sends one report every time. With the default `rate(1 hour)` that is up to 24 reports a day while the alarm stays OK.
- An `auto` call sends two reports: the pre-call report, then the result. A denied call ends in `blocked` and its report names the error code.
- A run stopped by the `blocked` latch makes no call, sends no report, and writes no decision log line or archive object. Only the function's own log, `/aws/lambda/<name_prefix>-evaluator`, records it with `blocked latch holds`.
- An `archive_retention_unproven` report carries `"lock_state": "calling"` although no call was made and the lock was released. Read `decision` and `detail` for this outcome.

Clear a `blocked` latch after fixing its cause. Either change the configuration fingerprint (`max_storage_capacity_gib`, `increase_percent`, `mode` or `decision_archive_required_mode`) and apply, or record an operator disposition on the lock item with the evidence of the fix. The function applies the clear on its next invocation, archives a `reconciled` event under the original correlation ID, and re-evaluates in the same invocation. In `auto` with the alarm still in ALARM, that re-evaluation can call `UpdateFileSystem`.

```bash
aws dynamodb update-item --table-name fsxn-ssd-auto-increase-lock \
  --key '{"file_system_id":{"S":"fs-0123456789abcdef0"}}' \
  --update-expression 'SET disposition = :d, evidence = :e' \
  --condition-expression '#s = :blocked' \
  --expression-attribute-names '{"#s":"state"}' \
  --expression-attribute-values '{":d":{"S":"cleared"},":e":{"S":"<what was fixed>"},":blocked":{"S":"blocked"}}'
```

Read the archive retention per object version. Each version's retain-until date is its creation time plus the bucket's default period; in the run, every version read showed `COMPLIANCE` and creation time + 1 day. The bucket cannot be emptied, and so cannot be deleted, before the latest retain-until date across its versions.

```bash
aws s3api list-object-versions --bucket <object-lock-bucket-name> \
  --prefix fsx-ssd-auto-increase/fs-0123456789abcdef0/ \
  --query 'Versions[].[Key,VersionId,LastModified]' --output text
aws s3api get-object-retention --bucket <object-lock-bucket-name> \
  --key fsx-ssd-auto-increase/fs-0123456789abcdef0/<correlation-id>/1-decision.json \
  --version-id <version-id>
```

### Removing

```bash
terraform destroy
```

The decision-archive bucket is outside this module and is not removed. A compliance-mode bucket's objects cannot be deleted before their retain-until date.

## Inputs

| Name | Type | Default | Notes |
|---|---|---|---|
| `file_system_id` | string | required | `^fs-[0-9a-f]{17}$` |
| `max_storage_capacity_gib` | number | required | Whole number 1024–1048576; validated against the shape maximum |
| `mode` | string | `notify_only` | `notify_only` / `approve` / `auto`; `auto` requires COMPLIANCE |
| `trigger_threshold_percent` | number | `80` | 1–100 |
| `increase_percent` | number | `10` | Whole number 10–100; never below the 10% minimum |
| `reevaluation_schedule` | string | `rate(1 hour)` | `rate(...)` or `cron(...)` |
| `log_retention_days` | number | `365` | A value CloudWatch Logs accepts |
| `indeterminate_reconcile_hours` | number | `6` | Whole number 1–24 |
| `decision_archive_bucket` | string | required | Existing Object Lock bucket name |
| `decision_archive_prefix` | string | `fsx-ssd-auto-increase/` | Key prefix for archive objects |
| `decision_archive_required_mode` | string | `COMPLIANCE` | `COMPLIANCE` / `GOVERNANCE` |
| `decision_archive_min_retention_days` | number | `365` | Minimum retain-until period in days |
| `aggregate_names` | list(string) | `[]` | Second-generation Aggregate names; one trigger alarm each |
| `notification_email` | string | `""` | Empty skips the email subscription |
| `name_prefix` | string | `"fsxn-ssd-auto-increase"` | 1–48 characters, plays the role of the stack name |
| `tags` | map(string) | `{}` | Every taggable resource |

## Outputs

| Name | Description |
|---|---|
| `lambda_function_name`, `lambda_function_arn`, `lambda_role_arn` | Function and execution role |
| `trigger_topic_arn`, `notification_topic_arn` | The two SNS topics |
| `lock_table_name`, `lock_table_arn` | The DynamoDB single-flight lock table |
| `decision_log_group_name` | The decision log group (operational history) |
| `dead_letter_queue_url`, `dead_letter_queue_arn` | The DLQ for failed invocations |
| `schedule_rule_arn` | The EventBridge schedule rule |
| `trigger_alarm_arns` | Trigger alarm ARNs keyed by file-system and aggregate/<name> |
| `config_fingerprint` | Hash of the ceiling, increase percent, mode and archive mode |
