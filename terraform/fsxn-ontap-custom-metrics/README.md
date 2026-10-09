# fsxn-ontap-custom-metrics (Terraform)

🌐 [日本語](README.ja.md) | **English**

A VPC Lambda function that polls the ONTAP REST API of one Amazon FSx for NetApp ONTAP file system on a schedule and publishes CloudWatch custom metrics for qtree quotas and SnapMirror health and lag, with alarms in Terraform state. FSx for ONTAP publishes neither of these as a native CloudWatch metric. This is phase T2 of the Terraform plan in [monitoring-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)), whose metric catalog and alert design this module implements. The qtree collector is the same source file as the inline code of `shared/templates/qtree-quota-monitor.yaml`.

## Verification status

Offline: `make terraform` runs `terraform fmt -check`, `terraform init -lockfile=readonly`, `terraform validate`, and `terraform test` (a mock `aws` provider and `command = plan`; the `archive` provider builds the real Lambda zip). It also runs `init` and `validate` on [`examples/basic/`](examples/basic/). The Lambda source in `shared/lambda/ontap_metrics/` has pytest unit tests against mocked urllib3 and boto3, with SnapMirror records shaped like the example response in the ONTAP 9.18.1 REST API reference. One test file asserts that every alarm reads a (namespace, metric, dimension names) tuple from `shared/lambda/ontap_metrics/tests/fixtures/metric_contract.json`, and the Python tests assert that the collectors emit exactly the series in that file.

Live: one sample run on 2026-10-08 (UTC) in `ap-northeast-1` ([record](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#terraform-custom-metrics-module-run-on-2026-10-08), [日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-08-の-terraform-カスタムメトリクスモジュールの実行)), on one first-generation `SINGLE_AZ_1` file system with one HA pair (ONTAP 9.18.1P6), with both collectors on, a 1-minute poll, and a 300-second lag threshold chosen for the test. The file system already had the documented maximum of 6 SVMs, so the SnapMirror relationship ran between two test volumes in one SVM. Within that scope, these are `verified`: deployment, both collectors' series against real ONTAP responses, the heartbeat alarms going to ALARM before the first poll and to OK after it, the `snapmirror-unhealthy` alarm going OK → ALARM → OK (a failed manual transfer, then a recovering one), and the `snapmirror-lag-high` alarm going OK → ALARM → OK. Finding F1: an uninitialized relationship reported `healthy: true` with no `lag_time`, so neither SnapMirror alarm fired for it (observed once). Closing that gap needs a new signal, which is a metric-catalog change and is not implemented. Still `unverified`: SnapMirror between two SVMs and between two file systems (cluster peering, polling the destination file system across clusters), the `qtree-quota-high` ALARM path (usage reached 40.16%, below the threshold of 85), the deployer IAM policy (the run used administrator access), the ingress-rule and revoke-before-destroy steps, SNS notification, CA-verified TLS, the default 5-minute poll and 10800-second lag threshold, and second-generation and multi-HA-pair file systems. Since the revision that ran, only the module READMEs have changed. This is a sample run, not a production estimate; on another file-system shape, apply the module in a non-production account first.

## What it creates

- `aws_lambda_function` `<name_prefix>-poller` (Python 3.12, 256 MB, 300 s timeout, reserved concurrency 1, handler `ontap_metrics_handler.lambda_handler`) in your subnets, packaged by `data "archive_file"` from `shared/lambda/ontap_metrics/` (tests excluded).
- `aws_cloudwatch_event_rule` with `rate(N minutes)` (`rate(1 minute)` for 1), its target and `aws_lambda_permission`.
- `aws_sqs_queue` `<name_prefix>-dlq` (14-day retention, `alias/aws/sqs`) as the function's dead-letter queue.
- `aws_cloudwatch_log_group` `/aws/lambda/<name_prefix>-poller` (`log_retention_days`).
- `aws_iam_role` `<name_prefix>-role` with `AWSLambdaVPCAccessExecutionRole` and an inline policy (table below).
- `aws_security_group` `<name_prefix>-lambda` with egress TCP 443 to `ontap_management_ip/32` and to each `aws_api_egress_cidr_blocks` entry. The file system's own security group is not modified.
- Optional: interface endpoints for `monitoring` and `secretsmanager` with an endpoint security group (see [Network options](#network-options)).
- Optional: `aws_sns_topic` and an email subscription when `notification_email` is set. Every alarm then notifies on ALARM and OK.
- Up to 7 `aws_cloudwatch_metric_alarm` resources (table below).

Each collector runs fetch, build and publish in its own `try`/`except` and then publishes `CollectorSucceeded` (1 or 0) into its own namespace. A failing collector does not stop the other and does not fail the invocation; the heartbeat alarm reports it. An ONTAP HTTP 401 or 403 drops the cached credentials and skips the remaining collectors in that run without another ONTAP request. If publishing a heartbeat fails, the invocation fails, so the Lambda errors alarm and the DLQ see it.

`FSxONTAP/Qtree` (qtree collector, same series as the CloudFormation template plus the heartbeat):

| Metric | Dimensions | Unit | Published | Read by an alarm |
|---|---|---|---|---|
| `QtreeQuotaUsedPercent`, `QtreeQuotaUsedBytes`, `QtreeQuotaLimitBytes` | `SvmName`, `VolumeName`, `QtreeName` | Percent, Bytes, Bytes | Per qtree with a name and a hard limit above 0 | No |
| `QtreeQuotaUsedPercentMax` | `SvmName` | Percent | Once per run; not published when no qtree is usable | `qtree_quota` |
| `QtreeQuotaReportTruncated` | `SvmName` | Count | Once per run, 1 when the 50-page cap stopped the read | No |
| `CollectorSucceeded` | `FileSystemId`, `Collector=qtree` | Count | Once per run, 1 or 0 | `heartbeat` |

`FSxONTAP/SnapMirror` (SnapMirror collector, `GET /api/snapmirror/relationships` on this file system as the destination):

| Metric | Dimensions | Unit | Published | Read by an alarm |
|---|---|---|---|---|
| `SnapMirrorRelationshipHealthy` | `FileSystemId`, `SourcePath`, `DestinationPath` | Count | Per relationship up to `snapmirror_max_relationships`: 1 when `healthy` is true, else 0 | No |
| `SnapMirrorLagSeconds` | `FileSystemId`, `SourcePath`, `DestinationPath` | Seconds | Per relationship up to the cap, when `lag_time` parses | No |
| `SnapMirrorUnhealthyCount` | `FileSystemId` | Count | Once per run over every relationship read, also 0 with none | `snapmirror_unhealthy` |
| `SnapMirrorLagSecondsMax` | `FileSystemId` | Seconds | Once per run; not published when no `lag_time` parsed | `snapmirror_lag` |
| `SnapMirrorRelationshipsTruncated` | `FileSystemId` | Count | Once per run, 1 when any relationship read got no per-relationship series (the relationship cap applied or a path was empty) or the 50-page cap applied | No |
| `CollectorSucceeded` | `FileSystemId`, `Collector=snapmirror` | Count | Once per run, 1 or 0 | `heartbeat` |

The SnapMirror value rules: a missing or non-boolean `healthy` counts as unhealthy (0) with a warning in the log. A missing `lag_time` (for example on an `uninitialized` relationship) or one that does not parse as an ISO 8601 duration without year or month gives no lag datum for that relationship. A relationship with an empty source or destination path gets no per-relationship series, still counts in the aggregates, and sets `SnapMirrorRelationshipsTruncated` to 1, so an unhealthy relationship counted in `SnapMirrorUnhealthyCount` but missing from the drill-down is flagged. For unhealthy relationships the log lists `uuid`, `state`, both paths, and the `unhealthy_reason` codes and messages. `SnapMirrorUnhealthyCount` = 0 cannot tell "all healthy" from "no relationship visible to this ONTAP user"; the log line `SnapMirror on <fs>: N relationship(s)` records the count. Field names are from the [ONTAP 9.18.1 REST API reference](https://docs.netapp.com/us-en/ontap-restapi-9181/get-snapmirror-relationships.html).

Alarms (`period` is `max(300, 60 × poll_interval_minutes)` except for the DLQ alarm):

| Key in `alarm_arns` | Name | Metric | Statistic | Evaluation periods | Condition | Missing data | Created when |
|---|---|---|---|---|---|---|---|
| `qtree_quota` | `<prefix>-qtree-quota-high` | `QtreeQuotaUsedPercentMax` | Maximum | 2 | > `qtree_quota_threshold_percent` (85) | `missing` | qtree on |
| `snapmirror_unhealthy` | `<prefix>-snapmirror-unhealthy` | `SnapMirrorUnhealthyCount` | Maximum | 2 | > 0 | `missing` | SnapMirror on |
| `snapmirror_lag` | `<prefix>-snapmirror-lag-high` | `SnapMirrorLagSecondsMax` | Maximum | 1 | > `snapmirror_lag_threshold_seconds` (10800) | `missing` | SnapMirror on |
| `heartbeat/<collector>` | `<prefix>-<collector>-heartbeat` | `CollectorSucceeded` | Minimum | 2 | < 1 | `breaching` | Per enabled collector |
| `dlq_depth` | `<prefix>-dlq-depth` | `AWS/SQS` `ApproximateNumberOfMessagesVisible` (period 300) | Maximum | 1 | > 0 | `notBreaching` | Always |
| `lambda_errors` | `<prefix>-lambda-errors` | `AWS/Lambda` `Errors` | Sum | 1 | > 0 | `notBreaching` | Always |

> **Concurrency note**
>
> The function has a reserved concurrency of 1, so only one poller runs at a time. A run can last up to the 300-second timeout, and Lambda retries a failed asynchronous run, so without the limit a short `poll_interval_minutes` could start a second run while the first is still running. Each execution environment caches credentials separately and would send its own login, so a rejected password would be tried in parallel. With the limit, an overlapping invocation is throttled, and Lambda returns it to the queue and retries it for up to 6 hours before discarding it to the DLQ ([asynchronous error handling](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html)). How that shows up in the alarms: a throttle is counted in `AWS/Lambda` `Throttles`, not in `Errors` ([Lambda metrics](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html)), so `lambda_errors` does not fire on it and the module has no throttle alarm. A run delayed past two alarm periods leaves `CollectorSucceeded` missing, and `heartbeat/<collector>` goes to ALARM. An event discarded after 6 hours lands in the DLQ, and `dlq_depth` fires. Lambda lets you reserve concurrency only while at least 100 units stay unreserved in the account ([reserved concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html)); in an account with a lower concurrency quota, `apply` fails on this setting. None of this was observed with this module (`unverified`).

Lambda execution role, created by the module (the deployer needs `iam:PassRole` on it):

| Statement | Actions | Resource |
|---|---|---|
| Managed `AWSLambdaVPCAccessExecutionRole` | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`, and the ENI actions for the VPC attachment, on `*` ([AWS managed policy reference](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AWSLambdaVPCAccessExecutionRole.html)) | `*` |
| `SecretsRead` | `secretsmanager:GetSecretValue` | `ontap_credentials_secret_arn` only |
| `SecretKms` (only with `ontap_credentials_kms_key_arn`) | `kms:Decrypt` with `kms:ViaService = secretsmanager.<region>.amazonaws.com` | That key |
| `CloudWatchPublish` | `cloudwatch:PutMetricData` with `cloudwatch:namespace` limited to the enabled collectors' namespaces | `*` (the action has no resource-level permission) |
| `Logs` | `logs:CreateLogStream`, `logs:PutLogEvents` | The module's log group |
| `DeadLetterQueue` | `sqs:SendMessage` | The module's DLQ |

## Obtaining the module

The module is versioned with git tags of the form `terraform-fsxn-ontap-custom-metrics-vX.Y.Z`. The current version is `terraform-fsxn-ontap-custom-metrics-v0.1.0`, published as a [GitHub Release](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/releases/tag/terraform-fsxn-ontap-custom-metrics-v0.1.0). A tag pins a version; it does not change the scope of verification described in [Verification status](#verification-status). The module is not on the Terraform Registry, because it is a subdirectory of a larger repository. Download sizes were not measured for this module; [the dashboard module README](../fsxn-monitoring-dashboard/README.md#obtaining-the-module) records them for the same repository.

The Lambda source is outside the module directory, in `shared/lambda/ontap_metrics/`. With a `//subdirectory` source, Terraform downloads and extracts the whole package and then reads the module from the subdirectory ([module block reference](https://developer.hashicorp.com/terraform/language/block/module)), so `../../shared` resolves for the sources below.

```hcl
# Git source pinned to a tag (shallow clone)
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ontap-custom-metrics?ref=terraform-fsxn-ontap-custom-metrics-v0.1.0&depth=1"

# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ontap-custom-metrics?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-ontap-custom-metrics"
```

The steps below check out the module directory at the tag with a sparse checkout. Then set `source` to the local path of `terraform/fsxn-ontap-custom-metrics` in this copy. To pin a commit instead, replace the tag name with a commit SHA. The sparse checkout must include both directories, or `archive_file` fails at plan time because `shared/lambda/ontap_metrics` does not exist:

```bash
git init fsx-ontap-custom-metrics && cd fsx-ontap-custom-metrics
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-ontap-custom-metrics shared/lambda/ontap_metrics
git fetch --depth 1 --filter=blob:none origin terraform-fsxn-ontap-custom-metrics-v0.1.0
git checkout FETCH_HEAD
```

> **Fetch note**
>
> Before the tag was created, these steps were run with a commit SHA in place of the tag name, and `terraform init -backend=false` and `terraform validate` passed in `examples/basic/` of that checkout. The fetch of the tag itself has not been run for this module. A git source with `?ref=main&depth=1` also placed `shared/lambda/ontap_metrics/` next to the module in the copy that `terraform init` downloaded; `plan` was not run from it. With the git source in `source`, `depth=1` cannot be combined with a SHA, as recorded in the [dashboard module README](../fsxn-monitoring-dashboard/README.md#obtaining-the-module).

## Usage

The sections below follow the order of a first deployment: prerequisites, network, permissions, input values, deployment, checks after apply, and removal.

### Prerequisites

- Terraform `>= 1.11.0`; `hashicorp/aws` `>= 6.67.0` and `hashicorp/archive` `>= 2.8.1` (tested with 6.67.0 and 2.8.1).
- A subnet in the file system's VPC (or a peered VPC) with a route to the file system management endpoint on TCP 443.
- An ONTAP user for the poller. The AWS documentation describes the built-in file-system role `fsxadmin-readonly` as able to view everything at the file system level without making changes and suited to monitoring applications, and says you cannot create new file-system roles ([roles and users](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/roles-and-users.html)). Create the user with `security login create` over SSH as `fsxadmin` ([Creating ONTAP users](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/create-new-ontap-users.html)). That page lists `http`, `ontapi` and `ssh` as `-application` values and shows `ssh` examples; the command below uses `http` for REST API access. It has not been run for this module (`unverified`). ONTAP prompts for the password twice.

```bash
ssh fsxadmin@<management-endpoint-ip>
security login create -user-or-group-name ontap-monitor -application http -authentication-method password -role fsxadmin-readonly
```

A Secrets Manager secret holds that user's credentials as `{"username": "...", "password": "..."}`. Only the ARN goes into Terraform and the Lambda environment. Writing the JSON to a file keeps the password out of the shell history; delete the file afterwards.

```bash
aws secretsmanager create-secret --name ontap-monitor \
  --secret-string file://ontap-monitor-secret.json
rm ontap-monitor-secret.json
```

After apply, add an ingress rule to the file system's security group that allows TCP 443 from the module's Lambda security group (output `lambda_security_group_id`). The module does not change that group, so the rule is outside Terraform state and must be removed before `terraform destroy` (see [Removing](#removing)).

```bash
aws ec2 authorize-security-group-ingress --group-id sg-0123456789abcdef0 \
  --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=$(terraform output -raw lambda_security_group_id)}]"
```

> **Credential note**
>
> The poller reads the secret again every 300 seconds, so a rotated password is used without redeploying. Repeated basic-authentication failures can lock an ONTAP account, so a 401 or 403 is not retried within a run and the next attempt waits for the next schedule. The SSM Parameter Store `SecureString` alternative was considered and is not implemented: the handler reads only `ONTAP_CREDENTIALS_SECRET_ARN`.

> **TLS and topic encryption note**
>
> With `ca_cert_path` empty, the default and the CloudFormation template's behaviour, the poller does not verify the management endpoint's TLS certificate and logs a warning. For production, put the CA certificate that signs the management endpoint's certificate in a Lambda layer and set both `ca_cert_path` and `ca_cert_layer_arn`. The SNS topic created for `notification_email` is not encrypted at rest; its messages carry alarm names, descriptions and the file system ID, not credentials. Where every topic must be encrypted, leave `notification_email` empty and route the alarm state changes from your own configuration to an encrypted topic.

### Network options

The function needs three routes: TCP 443 to the management endpoint (always the module's `/32` egress rule), and HTTPS to the CloudWatch monitoring API and to Secrets Manager. Pick one of these for the last two:

| Option | Settings | Trade-off |
|---|---|---|
| NAT gateway in the subnet route table | Defaults (`aws_api_egress_cidr_blocks = ["0.0.0.0/0"]`, both `create_*_endpoint = false`) | No endpoint charge from this module; the NAT gateway is billed and the traffic leaves the VPC |
| Endpoints created by this module | `create_monitoring_endpoint = true`, `create_secretsmanager_endpoint = true`, `aws_api_egress_cidr_blocks = []` | No internet path; each endpoint is billed per hour per AZ (see [Cost](#cost)) |
| Existing endpoints in the VPC | Both `create_*_endpoint = false`, `aws_api_egress_cidr_blocks` = the VPC CIDR | Reuses what exists; their security groups must allow 443 from `lambda_security_group_id` |

> **VPC endpoint conflict note**
>
> Two interface endpoints with private DNS for the same service cannot coexist in one VPC, so set `create_*_endpoint = false` for any service that already has one. `shared/scripts/preflight-check.sh` reports existing endpoints but has no profile for this module and does not check `monitoring`; the command below lists both services.

```bash
aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values=vpc-0123456789abcdef0 \
  --query 'VpcEndpoints[].{Service:ServiceName,PrivateDns:PrivateDnsEnabled,State:State}' --output table
```

CloudWatch Logs needs no endpoint or NAT in one observed case: on 2026-10-06 the CloudFormation qtree template ran in a subnet with no NAT gateway and only `monitoring` and `secretsmanager` interface endpoints, and its log lines were read from the log group ([record](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#qtree-quota-monitor-run-on-2026-10-06)). The 2026-10-08 run of this module was in a subnet with no NAT gateway as well, with the module's `monitoring` endpoint and an existing `secretsmanager` endpoint, and the function's log lines were read from its log group ([record](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#terraform-custom-metrics-module-run-on-2026-10-08)). No AWS page read for this module states it, so this rests on those two observations.

### Required IAM permissions (estimated, unverified)

These are the permissions for the identity that runs `terraform apply`, derived from the resources the module creates. They have not been run with a role limited to them; the dashboard module showed that tag actions are easy to miss ([its CloudTrail note](../fsxn-monitoring-dashboard/README.md#required-iam-permissions-verified)). Expect to add actions after a first apply.

| Resource | Actions |
|---|---|
| `aws_lambda_function`, `aws_lambda_permission` | `lambda:CreateFunction`, `lambda:GetFunction`, `lambda:GetFunctionConfiguration`, `lambda:UpdateFunctionCode`, `lambda:UpdateFunctionConfiguration`, `lambda:PutFunctionConcurrency`, `lambda:DeleteFunctionConcurrency`, `lambda:DeleteFunction`, `lambda:AddPermission`, `lambda:RemovePermission`, `lambda:GetPolicy`, `lambda:ListVersionsByFunction`, `lambda:GetFunctionCodeSigningConfig`, `lambda:TagResource`, `lambda:UntagResource`, `lambda:ListTags` |
| `aws_iam_role` and its policies | `iam:CreateRole`, `iam:GetRole`, `iam:DeleteRole`, `iam:PassRole`, `iam:PutRolePolicy`, `iam:GetRolePolicy`, `iam:DeleteRolePolicy`, `iam:AttachRolePolicy`, `iam:DetachRolePolicy`, `iam:ListRolePolicies`, `iam:ListAttachedRolePolicies`, `iam:ListInstanceProfilesForRole`, `iam:TagRole`, `iam:UntagRole` |
| `aws_sqs_queue` | `sqs:CreateQueue`, `sqs:GetQueueAttributes`, `sqs:SetQueueAttributes`, `sqs:DeleteQueue`, `sqs:TagQueue`, `sqs:UntagQueue`, `sqs:ListQueueTags` |
| `aws_cloudwatch_log_group` | `logs:CreateLogGroup`, `logs:DeleteLogGroup`, `logs:PutRetentionPolicy`, `logs:ListTagsForResource`, `logs:TagResource`, `logs:UntagResource` |
| Reading the log group back (`Resource: "*"`) | `logs:DescribeLogGroups` |
| `aws_cloudwatch_event_rule`, `aws_cloudwatch_event_target` | `events:PutRule`, `events:DescribeRule`, `events:DeleteRule`, `events:PutTargets`, `events:RemoveTargets`, `events:ListTargetsByRule`, `events:ListTagsForResource`, `events:TagResource`, `events:UntagResource` |
| `aws_cloudwatch_metric_alarm` | `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, `cloudwatch:DeleteAlarms`, `cloudwatch:ListTagsForResource`, `cloudwatch:TagResource`, `cloudwatch:UntagResource` |
| `aws_sns_topic`, `aws_sns_topic_subscription` (only with `notification_email`) | `sns:CreateTopic`, `sns:GetTopicAttributes`, `sns:SetTopicAttributes`, `sns:ListTagsForResource`, `sns:TagResource`, `sns:UntagResource`, `sns:DeleteTopic`, `sns:Subscribe`, `sns:GetSubscriptionAttributes`, `sns:Unsubscribe` |
| Security groups, rules, endpoints (`Resource: "*"`) | `ec2:CreateSecurityGroup`, `ec2:DeleteSecurityGroup`, `ec2:AuthorizeSecurityGroupEgress`, `ec2:RevokeSecurityGroupEgress`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:RevokeSecurityGroupIngress`, `ec2:CreateTags`, `ec2:DeleteTags`, `ec2:CreateVpcEndpoint`, `ec2:DeleteVpcEndpoints`, `ec2:ModifyVpcEndpoint` |
| Reads for the VPC attachment and the rules (`Resource: "*"`) | `ec2:DescribeSecurityGroups`, `ec2:DescribeSecurityGroupRules`, `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeNetworkInterfaces`, `ec2:DescribeVpcEndpoints`, `ec2:DescribePrefixLists`, `ec2:DescribeVpcAttribute` |

The policy is in [`examples/basic/iam-policy.json`](examples/basic/iam-policy.json). Scoped statements use the names the module builds from `name_prefix` (`fsxn-ontap-metrics-*` by default); replace `123456789012`, `ap-northeast-1` and the prefix before use. Three statements use `Resource: "*"`. `LogsRead` holds only `logs:DescribeLogGroups`, which the [service authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_logs.html) lists without a resource type, so it cannot be scoped to the log group. The two EC2 statements use `Resource: "*"` because the create actions touch several resource types (security group, VPC, subnet, endpoint); scoping them is left open. The read-only commands in [Finding input values](#finding-input-values) and [Verifying after apply](#verifying-after-apply) are meant for your own credentials.

### Finding input values

`ontap_management_ip`, `vpc_id` and candidate `subnet_ids` come from the file system; `qtree_svm_name` from its SVMs:

```bash
aws fsx describe-file-systems --file-system-ids fs-0123456789abcdef0 \
  --query 'FileSystems[].OntapConfiguration.Endpoints.Management.IpAddresses'
aws fsx describe-file-systems --file-system-ids fs-0123456789abcdef0 \
  --query 'FileSystems[].{Vpc:VpcId,Subnets:SubnetIds}'
aws fsx describe-storage-virtual-machines --filters Name=file-system-id,Values=fs-0123456789abcdef0 \
  --query 'StorageVirtualMachines[].Name'
```

### Deploying from examples/basic

Run these from the directory that contains `terraform/` (a full clone or the sparse checkout above). In `terraform.tfvars`, set the required values and any optional inputs.

```bash
cd terraform/fsxn-ontap-custom-metrics/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: region, file_system_id, ontap_management_ip, secret ARN, network, SVM
terraform init
terraform plan
terraform apply
```

In your own root configuration, copy [`examples/basic/`](examples/basic/) and replace `source = "../.."` with one of the sources in [Obtaining the module](#obtaining-the-module):

```hcl
module "ontap_custom_metrics" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ontap-custom-metrics?ref=terraform-fsxn-ontap-custom-metrics-v0.1.0&depth=1"

  file_system_id               = "fs-0123456789abcdef0"
  ontap_management_ip          = "198.51.100.10"
  ontap_credentials_secret_arn = "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ontap-monitor-XXXXXX"
  vpc_id                       = "vpc-0123456789abcdef0"
  subnet_ids                   = ["subnet-0123456789abcdef0"]
  qtree_svm_name               = "svm-prod-01"
}
```

### Verifying after apply

Replace `fsxn-ontap-metrics` with your `name_prefix`. The first command invokes the function once instead of waiting for the schedule; `response.json` shows `succeeded` per collector.

```bash
aws lambda invoke --function-name fsxn-ontap-metrics-poller --payload '{}' \
  --cli-binary-format raw-in-base64-out response.json
aws logs tail /aws/lambda/fsxn-ontap-metrics-poller --since 15m
aws cloudwatch list-metrics --namespace FSxONTAP/SnapMirror
aws cloudwatch list-metrics --namespace FSxONTAP/Qtree
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-ontap-metrics \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table
```

The heartbeat alarms treat missing data as breaching, so they may reach ALARM before the first scheduled poll publishes `CollectorSucceeded` (not observed; the timing depends on when the schedule first fires). Other alarms stay in INSUFFICIENT_DATA until enough datapoints arrive.

### Removing

Remove the rules you added outside Terraform first. A security group cannot be deleted while another security group in the same VPC references it; the deletion fails with `DependencyViolation` ([delete-security-group](https://docs.aws.amazon.com/cli/latest/reference/ec2/delete-security-group.html)). The ingress rule from [Prerequisites](#prerequisites) references the module's Lambda security group, so `terraform destroy` cannot delete that group while the rule exists, and the reference does not clear by itself.

```bash
terraform output -raw lambda_security_group_id
aws ec2 revoke-security-group-ingress --group-id sg-0123456789abcdef0 \
  --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=$(terraform output -raw lambda_security_group_id)}]"
terraform destroy
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-ontap-metrics \
  --query 'MetricAlarms[].AlarmName' --output text
```

The first command prints the Lambda security group ID; keep it for the checks below, because the output is gone after `destroy`. The second removes the same rule the Prerequisites command added (`sg-0123456789abcdef0` is the file system's security group). If you chose existing endpoints in [Network options](#network-options), remove the rules that allow 443 from the Lambda security group on their security groups too. The last command should print nothing.

If `destroy` waits on or fails at `aws_security_group.lambda`, these two commands tell the causes apart (replace `<lambda-security-group-id>` with the ID printed above):

```bash
aws ec2 describe-network-interfaces --filters Name=group-id,Values=<lambda-security-group-id> \
  --query 'NetworkInterfaces[].{Id:NetworkInterfaceId,Type:InterfaceType,Status:Status}' --output table
aws ec2 describe-security-groups --filters Name=ip-permission.group-id,Values=<lambda-security-group-id> \
  --query 'SecurityGroups[].{Id:GroupId,Name:GroupName}' --output table
```

Rows from the first command are Lambda network interfaces that are still being released after the function was deleted. That clears by itself; in the 2026-10-08 run, `terraform destroy` waited 22 minutes 3 seconds on the Lambda security group for it (observed once, [record](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#terraform-custom-metrics-module-run-on-2026-10-08)). Run `terraform destroy` again afterwards. Rows from the second command are security groups that still reference the Lambda security group in an inbound rule. That does not clear by itself: remove the rule from each listed group, then run `terraform destroy` again.

## Inputs

| Name | Type | Default | Notes |
|---|---|---|---|
| `file_system_id` | string | required | `^fs-[0-9a-f]{17}$`; for SnapMirror, the destination file system |
| `ontap_management_ip` | string | required | IPv4, octets 0–255 (template `OntapMgmtIp`) |
| `ontap_credentials_secret_arn` | string | required | Secrets Manager ARN (template `OntapCredentialsSecretArn`) |
| `ontap_credentials_kms_key_arn` | string | `null` | Customer managed key of the secret; adds `SecretKms` |
| `vpc_id` | string | required | VPC for the module-created security group |
| `subnet_ids` | list(string) | required | At least one, no duplicates (template `SubnetIds`) |
| `aws_api_egress_cidr_blocks` | list(string) | `["0.0.0.0/0"]` | TCP 443 egress for CloudWatch and Secrets Manager |
| `create_monitoring_endpoint` | bool | `false` | See [Network options](#network-options) |
| `create_secretsmanager_endpoint` | bool | `false` | See [Network options](#network-options) |
| `enable_qtree_collector` | bool | `true` | At least one collector must be on |
| `enable_snapmirror_collector` | bool | `true` | At least one collector must be on |
| `qtree_svm_name` | string | `null` | Required with the qtree collector, 1–66 characters (template `SvmName`) |
| `poll_interval_minutes` | number | `5` | Whole number 1–60 (template `PollIntervalMinutes`); one run at a time at any value (see the [concurrency note](#what-it-creates)) |
| `qtree_quota_threshold_percent` | number | `85` | 50–99 (template `QuotaThresholdPercent`) |
| `snapmirror_lag_threshold_seconds` | number | `10800` | 60–2592000 |
| `snapmirror_max_relationships` | number | `100` | Whole number 1–1000 |
| `ca_cert_path`, `ca_cert_layer_arn` | string | `""` | Both or neither (template `CaCertPath`, `CaCertLayerArn`); empty disables TLS verification |
| `log_retention_days` | number | `30` | A value CloudWatch Logs accepts |
| `notification_email` | string | `""` | Empty skips SNS (template `NotificationEmail`) |
| `name_prefix` | string | `"fsxn-ontap-metrics"` | 1–48 characters, plays the role of the stack name |
| `tags` | map(string) | `{}` | Every taggable resource |

## Outputs

| Name | Description |
|---|---|
| `lambda_function_name`, `lambda_function_arn`, `lambda_role_arn` | Function and execution role |
| `lambda_security_group_id` | Source for the ingress rule on the file system's security group |
| `log_group_name` | Poller log group |
| `dead_letter_queue_url`, `dead_letter_queue_arn` | DLQ |
| `schedule_rule_arn` | EventBridge rule |
| `alarm_arns` | Map keyed as in the alarm table (`heartbeat/<collector>`) |
| `sns_topic_arn` | Topic ARN, or `null` without `notification_email` |
| `metric_namespaces` | Namespaces of the enabled collectors |
| `vpc_endpoint_ids` | Module-created endpoints keyed by service; empty by default |

## Deliberate differences from the CloudFormation template

- Per-collector `CollectorSucceeded` heartbeat with a `breaching` alarm. The template has none and relies on the DLQ alarm, which does not see a poller that is never invoked.
- ONTAP requests retry three times with exponential backoff on connection errors, 429 and 5xx; 401 and 403 are never retried and skip the remaining collectors. The template sends each request once.
- Credentials are re-read every 300 seconds; the template caches them for the container lifetime.
- Reserved concurrency is 1, so polls never overlap; the template sets no reserved concurrency (recorded as a template follow-up).
- A collector failure does not fail the invocation (the heartbeat reports it); a heartbeat publish failure does. In the template any failure fails the invocation.
- Alarm periods follow the poll interval (`max(300, 60 × poll_interval_minutes)`); the template uses 300 regardless of `PollIntervalMinutes`.
- `rate(1 minute)` is used for an interval of 1. The template builds `rate(1 minutes)`, which the [EventBridge rate syntax](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-scheduled-rule-pattern.html) does not accept (recorded as a template follow-up).
- The module creates the Lambda security group and, optionally, the `monitoring` endpoint; the template takes a security group ID and creates only the Secrets Manager endpoint.
- The function depends on its log group, so destroy deletes the function first. In a template run the log group was deleted first and recreated without retention by an in-flight retry ([QF3](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#findings-qtree-run)).
- `ok_actions` is set alongside `alarm_actions`.
- SnapMirror has no CloudFormation equivalent yet (recorded as a follow-up in [CHANGELOG.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/CHANGELOG.md)).

## Cross-file-system SnapMirror

Poll the destination file system. The ONTAP reference runs its lag example on the cluster containing the destination endpoint, and which health and lag fields the source-side `list_destinations_only` listing returns is `open`. With SnapMirror from file system A to file system B, deploy one module instance for B, with B's management IP, a credential for an ONTAP user on B, and a network path to B. Several destination file systems need one instance each with distinct `name_prefix` values. The module never calls the source file system.

Qtree series carry `SvmName` but no `FileSystemId`, as in the CloudFormation template. Two file systems with the same SVM name in one account and Region therefore write into the same qtree series and the same `QtreeQuotaUsedPercentMax`. Enable the qtree collector on only one of them, or keep SVM names distinct. SnapMirror series carry `FileSystemId` and do not have this limitation.

## Cost

No dollar total is given for the metrics, because it depends on Region, date and counts. Formulas at the defaults:

- Custom metric series: qtree `3 × N + 2` per SVM plus 1 heartbeat (N qtrees with a hard limit); SnapMirror `2 × R + 3` plus 1 heartbeat (R relationships with series, at most `snapmirror_max_relationships`). Rates: [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/).
- Alarms: up to 7 standard-resolution alarms.
- Lambda: 8,640 invocations per 30 days at the 5-minute interval, each up to 300 seconds at 256 MB. Rates: [Lambda pricing](https://aws.amazon.com/lambda/pricing/).
- `PutMetricData` calls: one per 20 datums per run, plus one per heartbeat.

Interface endpoints created by this module are billed per endpoint per AZ per hour plus per GB processed ([AWS PrivateLink pricing](https://aws.amazon.com/privatelink/pricing/)). The repository's [deployment guide](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/deployment-guide.md) uses about $7.20 per month per endpoint per AZ, which matches the US East (N. Virginia) rate of $0.01 per hour over 720 hours. The AWS Price List API on 2026-10-07 (price list published 2026-09-17) returned $0.014 per hour for Asia Pacific (Tokyo), about $10.08 per 720 hours, and $0.01 per GB processed in both Regions. Both endpoints in one AZ are therefore two of those charges; check the pricing page for your Region and date.

## Provider version constraint

The module declares `hashicorp/aws` `>= 6.67.0` and `hashicorp/archive` `>= 2.8.1`, the releases it was tested with, and no upper bound. On 2026-10-07 the Terraform Registry listed 6.67.0 and 2.8.1 (published 2026-09-11) as the latest releases. [`examples/basic/`](examples/basic/) carries the exact pins `= 6.67.0` and `= 2.8.1` and its own `.terraform.lock.hcl`. Callers do not use this module's `.terraform.lock.hcl`: Terraform reads the lock file of the root configuration ([Dependency Lock File](https://developer.hashicorp.com/terraform/language/files/dependency-lock)). Terraform `>= 1.11.0` is required because the tests use `override_during = plan`.
