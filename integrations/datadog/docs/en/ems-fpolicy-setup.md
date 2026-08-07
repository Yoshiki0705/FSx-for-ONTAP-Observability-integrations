# EMS and FPolicy Setup (Datadog)

🌐 [日本語](../ja/ems-fpolicy-setup.md) | **English** (this page)

## Overview

The audit log path in the [Setup Guide](setup-guide.md) delivers events in
minutes, because ONTAP has to rotate the audit staging file before anything is
readable. Two additional sources close that latency gap.

| Source | Latency | Content | Trigger |
|--------|---------|---------|---------|
| Audit logs | Minutes | Every audited file operation, after rotation | EventBridge Scheduler |
| **EMS webhooks** | Seconds | ONTAP system events (ARP/ransomware, quota, failover) | ONTAP → API Gateway |
| **FPolicy** | Sub-second | File operations, as they happen | ONTAP → Fargate → SQS |

Both are optional. Deploy EMS if you want ransomware and system alerts fast;
deploy FPolicy if you need per-operation visibility with no rotation delay.

Datadog deploys both Lambdas from **one** stack
(`template-ems-fpolicy.yaml`, stack name `fsxn-datadog-ems-fpolicy`) — other
vendors in this repository use two separate stacks. This matters when cleaning
up; `scripts/cleanup.sh` handles the difference for you.

## Architecture

```
EMS:      ONTAP EMS ──HTTPS──→ API Gateway ──→ Lambda (-ems)     ──→ Datadog
                               (+ Lambda Authorizer)

FPolicy:  ONTAP ──TCP 9898──→ ECS Fargate ──→ SQS ──→ Lambda (-fpolicy) ──→ Datadog
                              (binary protocol)         (ReportBatchItemFailures)
```

FPolicy uses a proprietary binary protocol over TCP, not HTTP, which is why a
Fargate server sits in front of it rather than API Gateway. ONTAP connects
directly to the Fargate task IP, so **the task IP must be re-registered with
ONTAP after every task restart**.

## Prerequisites

- The audit log stack deployed and verified ([Setup Guide](setup-guide.md))
- Datadog API key in Secrets Manager (same secret as the audit path)
- For FPolicy: the shared FPolicy infrastructure deployed. Either template
  works and both provide the ECS Fargate service, the ingestion SQS queue and
  its DLQ:
  - `shared/templates/fpolicy-apigw.yaml` — Fargate or EC2, custom EventBridge bus
  - `shared/templates/fpolicy-server-fargate.yaml` — Fargate only, simpler
- For EMS: optionally the EMS parser Lambda Layer. Without it the handler falls
  back to a built-in stub parser that extracts fewer fields

### Gather these values

| Value | Source |
|-------|--------|
| `FPolicySqsQueueArn` | `fsxn-fp-srv` stack output — `IngestionQueueArn` (fpolicy-apigw.yaml) or `FPolicyQueueArn` (fpolicy-server-fargate.yaml) |
| `EventBridgeBusName` | `fsxn-fpolicy-events` if you use a custom bus, else `default` |
| `EmsParserLayerArn` | Output of the EMS parser layer build, if used |

## Step 1: Deploy the EMS + FPolicy stack

```bash
export DATADOG_API_KEY_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX"
export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
# Read the queue ARN from the shared FPolicy stack rather than hand-writing it.
# Output key: IngestionQueueArn (fpolicy-apigw.yaml) or
#             FPolicyQueueArn   (fpolicy-server-fargate.yaml)
export FPOLICY_SQS_QUEUE_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-fp-srv --region ap-northeast-1 \
  --query "Stacks[0].Outputs[?OutputKey=='IngestionQueueArn'].OutputValue" \
  --output text)
export EMS_PARSER_LAYER_ARN="arn:aws:lambda:ap-northeast-1:123456789012:layer:fsxn-ems-parser:3"

bash integrations/datadog/scripts/deploy.sh --all
```

This deploys both stacks and uploads all three handlers. To deploy only this
stack by hand:

```bash
aws cloudformation deploy \
  --template-file integrations/datadog/template-ems-fpolicy.yaml \
  --stack-name fsxn-datadog-ems-fpolicy \
  --parameter-overrides \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX \
    DatadogSite=ap1.datadoghq.com \
    FPolicySqsQueueArn=arn:aws:sqs:ap-northeast-1:123456789012:fsxn-fp-srv-fpolicy-ingestion \
    EmsParserLayerArn=arn:aws:lambda:ap-northeast-1:123456789012:layer:fsxn-ems-parser:3 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

`CAPABILITY_NAMED_IAM` is required — the template creates named IAM roles.

Remember to upload the real handler code afterwards
(`bash integrations/datadog/scripts/deploy.sh --all --code-only`), or both
functions will only raise `NotImplementedError`.

### Parameter Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DatadogApiKeySecretArn` | — | Required. Same secret as the audit path |
| `DatadogSite` | `ap1.datadoghq.com` | Determines the intake endpoint |
| `FPolicySqsQueueArn` | `''` | **Primary** FPolicy trigger. Empty ⇒ no SQS trigger is created and only the EventBridge path works |
| `EventBridgeBusName` | `default` | Bus carrying `fpolicy.fsxn` events (secondary path) |
| `EmsParserLayerArn` | `''` | EMS parser layer. Empty ⇒ built-in stub parser, fewer fields extracted |
| `SqsBatchSize` | `10` | Messages per invocation. Safe to raise: failures are reported per message |
| `AlarmNotificationTopicArn` | `''` | SNS topic for the four alarms. **Empty means nobody is notified** |
| `Environment` | `production` | Value of `DD_ENV` |
| `EnableGzip` | `false` | See the gzip known issue in the Setup Guide |
| `LogLevel` | `INFO` | |
| `LambdaMemorySize` | `256` | MB |
| `LambdaTimeout` | `60` | Seconds |

### What the stack creates

| Resource | Purpose |
|----------|---------|
| Lambda `-ems` | EMS webhook → Datadog |
| Lambda `-fpolicy` | FPolicy events → Datadog |
| `-fpolicy-dlq` | SQS DLQ for the **asynchronous EventBridge path** |
| EventBridge rule + target DLQ + retry policy | Secondary FPolicy path |
| SQS event source mapping | Primary FPolicy path, with `ReportBatchItemFailures` |
| 4 CloudWatch alarms | EMS errors, FPolicy errors, FPolicy throttles, DLQ depth |

### Where failed events actually go

This trips people up, so it is worth being precise. There are three delivery
paths and each fails differently:

| Path | Invocation | On failure |
|------|-----------|-----------|
| FPolicy via SQS (primary) | Event source mapping | The handler reports the failing messages individually; the **ingestion queue's** redrive policy moves them to that queue's own DLQ after `maxReceiveCount` receives. This DLQ belongs to the shared FPolicy stack, not to this one |
| FPolicy via EventBridge | Asynchronous | Retried, then written to `-fpolicy-dlq` |
| EMS webhook | **Synchronous** (API Gateway) | A Lambda DLQ never applies to synchronous invocations. ONTAP receives a 5xx and `EmsErrorAlarm` fires |

So: an EMS delivery failure produces no DLQ message anywhere. The alarm is the
only signal, which is why setting `AlarmNotificationTopicArn` matters more here
than on the audit path.

## Step 2: EMS — deploy API Gateway and configure ONTAP

### 2.1 Deploy the webhook API Gateway

The shared template provides the endpoint and a Lambda Authorizer.

```bash
EMS_LAMBDA_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-datadog-ems-fpolicy --region ap-northeast-1 \
  --query "Stacks[0].Outputs[?OutputKey=='EmsLambdaFunctionArn'].OutputValue" \
  --output text)

aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-datadog-ems-webhook \
  --parameter-overrides LambdaFunctionArn="${EMS_LAMBDA_ARN}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

Note the invoke URL and the authorization token from the stack outputs.

### 2.2 Register the destination on ONTAP

```bash
# ONTAP CLI
event notification destination create \
  -name datadog-webhook \
  -rest-api-url https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems \
  -certificate-authority <ca-name>

event notification create \
  -filter-name important-events \
  -destinations datadog-webhook
```

### 2.3 Choose which events to forward

Forwarding every EMS event is noisy. The high-value ones for security work are
the anti-ransomware and availability events:

| Event | Meaning |
|-------|---------|
| `arw.volume.state` | Anti-ransomware state change, including `attack-detected` |
| `arw.vserver.state` | ARP enabled/disabled on an SVM |
| `wafl.vol.full` | Volume full — can be a side effect of a snapshot storm |
| `mgmtgwd.rootvol.space.low` | Root volume space low |

The full catalog with delivery patterns is in
[ems-detection-capabilities.md](../../../../docs/en/ems-detection-capabilities.md).

### 2.4 Verify

```bash
# Trigger a test event from ONTAP
event generate -message-name arw.volume.state -values "test"

# Watch the Lambda
aws logs tail /aws/lambda/fsxn-datadog-ems-fpolicy-ems --follow
```

Then search `source:fsxn-ems` in Datadog.

## Step 3: FPolicy — start the server and point ONTAP at it

### 3.1 Start the Fargate service

```bash
bash shared/scripts/fpolicy-fargate-control.sh start
bash shared/scripts/fpolicy-fargate-control.sh status
```

### 3.2 Register the task IP with ONTAP

The Fargate task IP changes on every restart, and ONTAP stores it statically.
This script reads the current task IP and updates the ONTAP external engine:

```bash
bash shared/scripts/fpolicy-update-engine-ip.sh --auto
```

Re-run it after **every** task restart. A stale IP is the most common cause of
"FPolicy events stopped arriving".

### 3.3 Create the FPolicy policy on ONTAP

```bash
# ONTAP CLI. The `vserver` prefix is deprecated in ONTAP 9.11+ but still works.
vserver fpolicy policy event create -vserver <svm> -event-name file-ops \
  -protocol cifs -file-operations create,write,rename,delete

vserver fpolicy policy create -vserver <svm> -policy-name datadog-audit \
  -events file-ops -engine datadog-engine

vserver fpolicy enable -vserver <svm> -policy-name datadog-audit -sequence-number 1
```

### 3.4 Verify

```bash
# Generate activity on the volume, then:
aws logs tail /aws/lambda/fsxn-datadog-ems-fpolicy-fpolicy --follow
```

A healthy invocation logs `FPolicy handler invoked: SQS batch of N record(s)`
followed by `Shipped N/N FPolicy event(s)`. Then search `source:fsxn-fpolicy`
in Datadog.

## Troubleshooting

### EMS events are not arriving

1. ONTAP requires a **trusted CA** for the webhook destination. A self-signed
   certificate is rejected silently at the ONTAP end.
2. Check the API Gateway execution logs before the Lambda logs — an authorizer
   rejection never reaches the function.
3. `EmsErrorAlarm` firing with no DLQ messages is expected: EMS is a synchronous
   path and has no DLQ (see the table above).

### FPolicy events stopped arriving

Almost always a stale Fargate task IP:

```bash
bash shared/scripts/fpolicy-fargate-control.sh status
bash shared/scripts/fpolicy-update-engine-ip.sh --auto
```

If the task is running and the IP is current, check whether messages are piling
up in the ingestion queue (Lambda side problem) or not arriving at all (ONTAP or
network side problem):

```bash
aws sqs get-queue-attributes \
  --queue-url <ingestion-queue-url> \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

### The same FPolicy events are delivered repeatedly

A message that cannot be parsed is reported as failed so the queue's redrive
policy moves it to the DLQ rather than dropping it. Until `maxReceiveCount` is
reached it is retried, which looks like duplication. Check the ingestion DLQ
(`FPolicyDeadLetterQueueUrl` in the shared FPolicy stack outputs):

```bash
aws sqs receive-message --queue-url <ingestion-dlq-url> --max-number-of-messages 1
```

### Partial batch failures are not working

`FunctionResponseTypes: [ReportBatchItemFailures]` must be set on the event
source mapping. Without it, the handler's `batchItemFailures` response is
ignored and one bad message re-delivers the whole batch:

```bash
aws lambda list-event-source-mappings \
  --function-name fsxn-datadog-ems-fpolicy-fpolicy \
  --query 'EventSourceMappings[].FunctionResponseTypes'
```

### Events arrive but fields are empty

For EMS, this usually means `EmsParserLayerArn` was left empty and the built-in
stub parser is in use. Build and attach the layer, then redeploy.

See [field-mapping.md](field-mapping.md) for the attribute names each source
produces.

## Cleanup

`scripts/cleanup.sh` deletes these stacks in dependency-safe order (the API
Gateway stack must go before the stack owning the EMS Lambda):

```bash
bash integrations/datadog/scripts/cleanup.sh
```

Disable the FPolicy policy on ONTAP first, or ONTAP will keep retrying
connections to a server that no longer exists:

```bash
vserver fpolicy disable -vserver <svm> -policy-name datadog-audit
```

The shared FPolicy Fargate stack (`fsxn-fp-srv`) is used by all vendors and is
**not** deleted. Stop the service if nothing else needs it:

```bash
bash shared/scripts/fpolicy-fargate-control.sh stop
```

## Related Documents

- [Setup Guide](setup-guide.md) — the audit log path
- [Field Mapping](field-mapping.md) — attributes per source
- [Log Archive Setup](log-archive-setup.md) — long-term retention
- [Snapshot Remediation](snapshot-remediation-setup.md) — automated containment
- [EMS Detection Capabilities](../../../../docs/en/ems-detection-capabilities.md) — event catalog
- [FPolicy Quick Deploy](../../../../docs/en/fpolicy-quick-deploy.md) — shared infrastructure
