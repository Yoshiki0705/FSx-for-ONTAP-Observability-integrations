# FPolicy Operational Notes

🌐 [日本語](../ja/operational-notes-fpolicy.md) | **English** (this page)

## Overview

This document summarizes operational knowledge gained from running the
FPolicy External Engine (ECS Fargate + SQS + EventBridge) architecture.

---

## Architecture Overview

```
ONTAP FPolicy → TCP:9898 → ECS Fargate → SQS (FPolicy_Q) → Bridge Lambda → EventBridge → Vendor Lambda
```

### Verified Configuration

| Component | Value |
|-----------|-------|
| Compute Mode | ECS Fargate (ARM64) |
| CPU / Memory | 256 CPU / 512 MB |
| Container Image | `123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:v2-timeout-fix` |
| Listen Port | TCP 9898 |
| VPC | `vpc-0123456789abcdef0` |
| Subnet | `subnet-xxxxxxxxxxxxxxxx1` (private) |
| FPolicy Server SG | `sg-xxxxxxxxxxxxxxxxx` (TCP 9898 inbound) |
| FSx for ONTAP SVM SG | `sg-0123456789abcdef0` |
| SVM | `FPolicySMB` (svm-0123456789abcdef0) |
| SVM UUID | `<svm-uuid>` |
| SVM Management IP | `10.0.x.x` |
| Secrets | `fsx-ontap-fsxadmin-credentials` |

---

## Fargate Task IP Auto-Update

### Problem
Fargate tasks receive a new IP address on restart.
ONTAP FPolicy External Engine specifies `primary-servers` by IP address,
so the connection breaks after a task restart.

### Solution: IP Auto-Updater Lambda

An EventBridge rule detects ECS Task State Change events, and a Lambda function
automatically calls the ONTAP REST API to update `primary-servers`.

```
ECS Task State Change (RUNNING) → EventBridge Rule → IP Updater Lambda → ONTAP REST API (PATCH)
```

**ONTAP REST API Endpoint:**
```
PATCH https://<mgmt-ip>/api/protocols/fpolicy/<svm-uuid>/engines/<engine-name>
Body: {"primary_servers": ["<new-task-ip>"]}
```

**Authentication**: Basic Auth retrieved from Secrets Manager (`fsx-ontap-fsxadmin-credentials`)

### Manual IP Update

If the IP Auto-Updater fails, follow these manual steps:

```bash
# 1. Get the current Fargate task IP
aws ecs list-tasks --cluster fsxn-fpolicy --service-name fsxn-fpolicy-server
aws ecs describe-tasks --cluster fsxn-fpolicy --tasks <task-arn> \
  --query 'tasks[0].attachments[?type==`ElasticNetworkInterface`].details[?name==`privateIPv4Address`].value'

# 2. Update the external engine IP via ONTAP CLI
vserver fpolicy policy external-engine modify -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <new-ip>

# 3. Verify connection status
vserver fpolicy show-engine -vserver FPolicySMB -engine-name fpolicy_lambda_engine
```

---

## KeepAlive Messages

ONTAP sends KeepAlive messages to the FPolicy server every 120 seconds, matching the
engine's `keep_alive_interval` (ONTAP's default is `PT2M`). This is an indicator that
the connection is healthy.

An earlier version of this page said "approximately every 6 seconds". That figure did
not reproduce and has been retracted; see
[FPolicy S3 Access Point and session verification](verification-results-fpolicy-s3ap-and-session.md).

### KeepAlive is not STATUS_REQ

Two different messages arrive on the same connection, and confusing them produces the
same misdiagnosis by another route.

| Message | Interval | Log level | Appears in ECS logs by default |
|---------|----------|-----------|-------------------------------|
| `KEEP_ALIVE` | `keep_alive_interval`, default `PT2M` (120 s) | INFO | Yes, as `[KeepAlive] seq=N \| since_prev=Xs` |
| `STATUS_REQ` | `status_request_interval`, default `PT10S` | DEBUG | No |

If you raise `LOG_LEVEL` to `DEBUG` you will see `[StatusReq]` lines about every 10
seconds. Those are not KeepAlives and say nothing about KeepAlive health. The 10-second
cadence is the origin of the retracted 6-second figure.

### Verification

The query window has to be wider than `keep_alive_interval`, or a healthy connection
reports as a broken one. 300 seconds covers the 120-second default with margin.

```bash
# Check KeepAlive in ECS logs.
# The window must exceed keep_alive_interval (default PT2M); at 30 seconds a
# healthy connection shows nothing on roughly three runs in four.
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server \
  --filter-pattern "KeepAlive" \
  --start-time $(date -d '300 seconds ago' +%s000) \
  --limit 5
```

### Expected Output
```
[KeepAlive] seq=42 | since_prev=120.1s
```

Read `since_prev` rather than trusting the interval quoted here. It is the observed
interval, so it stays correct if the engine is reconfigured.

If KeepAlive messages are not visible, this is not by itself evidence that ONTAP is
disconnected. Check in this order:
1. The query window is narrower than the engine's `keep_alive_interval`
   (`vserver fpolicy policy external-engine show -fields keep-alive-interval`)
2. ONTAP cannot connect to the Fargate task IP
3. Security group is blocking TCP 9898
4. Fargate task restarted and IP changed

---

## Resolving SQS AccessDenied Errors

### Symptoms
The following error appears in ECS logs:
```
AccessDenied: User: arn:aws:sts::123456789012:assumed-role/xxx is not authorized
to perform: sqs:SendMessage on resource: arn:aws:sqs:ap-northeast-1:123456789012:FPolicy_Q
```

### Cause
The ECS task role does not have `sqs:SendMessage` permission.

### Solution
Add the following policy to the ECS task role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:GetQueueUrl"
      ],
      "Resource": "arn:aws:sqs:ap-northeast-1:123456789012:*-fpolicy-ingestion"
    }
  ]
}
```

> **Note**: The CloudFormation template (`fpolicy-apigw.yaml`) includes this permission
> in the `EcsTaskRole`. This is an easy-to-miss point during manual deployments.

---

## Container Image Versioning

### Current Image
```
123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:v2-timeout-fix
```

### Tag Descriptions

| Tag | Description |
|-----|-------------|
| `v1` | Initial version |
| `v2-timeout-fix` | Timeout handling fix (currently in use) |

### Issues Fixed in `v2-timeout-fix`
- Timeout handling when ONTAP connections become idle for extended periods
- Proper TCP socket cleanup
- Reliable KeepAlive response delivery

### Image Update Procedure
```bash
# 1. ECR authentication
aws ecr get-login-password --region ap-northeast-1 | \
  docker login --username AWS --password-stdin \
  123456789012.dkr.ecr.ap-northeast-1.amazonaws.com

# 2. Build & push (ARM64)
docker buildx build --platform linux/arm64 \
  -t 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:<new-tag> \
  --push .

# 3. Update ECS service (restart task with new image)
aws ecs update-service --cluster fsxn-fpolicy \
  --service fsxn-fpolicy-server \
  --force-new-deployment
```

---

## NFSv3 Write-Complete Delay

### Behavior
NFSv3 write operations have a default 5-second delay before ONTAP confirms
write-complete. This is controlled by the FPolicy server's `WRITE_COMPLETE_DELAY_SEC`
environment variable.

### Impact
- Up to 5 seconds delay between file write and SQS event arrival
- CIFS/SMB create operations are not affected (notified immediately)

### Adjustment
```yaml
# CloudFormation parameter
WriteCompleteDelaySec:
  Type: Number
  Default: 5
  MinValue: 0
  MaxValue: 60
```

---

## Monitoring

### Required Monitoring

| Target | Metric/Log | Alert Condition |
|--------|-----------|-----------------|
| SQS queue depth | `ApproximateNumberOfMessagesVisible` | > 100 (5 minutes) |
| ECS task health | Task count < DesiredCount | More than 1 minute |
| ECS log `[SQS] Sent:` | Log output frequency | Zero for 10 minutes |
| Bridge Lambda errors | `Errors` metric | > 0 |
| DLQ message count | `ApproximateNumberOfMessagesVisible` | > 0 |

### CloudWatch Logs Queries

```bash
# Check FPolicy events (ECS logs)
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server \
  --filter-pattern "[SQS] Sent:" \
  --start-time $(date -d '5 minutes ago' +%s000)

# Check KeepAlive (window must exceed keep_alive_interval, default PT2M)
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server \
  --filter-pattern "KeepAlive" \
  --start-time $(date -d '300 seconds ago' +%s000) \
  --limit 5

# Check Bridge Lambda errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-fpolicy-bridge \
  --filter-pattern "ERROR" \
  --start-time $(date -d '10 minutes ago' +%s000)

# Check IP Updater Lambda execution
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-fpolicy-ip-updater \
  --filter-pattern "updated to" \
  --start-time $(date -d '1 hour ago' +%s000)
```

### SQS Queue Check

```bash
# Check queue status
aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-1.amazonaws.com/123456789012/FPolicy_Q \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

---

## NLB Role (Important)

**The NLB is NOT used for routing FPolicy traffic.**

The NLB's sole purpose is ECS Fargate task health checking (TCP port 9898).
ONTAP connects directly to the Fargate task's ENI private IP via TCP.

This is because FPolicy uses a proprietary binary protocol.
Routing through the NLB could cause connection stability issues.

---

## Troubleshooting Flowchart

```
FPolicy events not arriving
│
├─ Is the ECS task RUNNING?
│  └─ No → Check ECS service, verify task definition
│
├─ Are KeepAlive messages present?
│  └─ No → ONTAP cannot connect
│     ├─ Is the Fargate task IP correct?
│     ├─ Does SG (sg-xxxxxxxxxxxxxxxxx) allow TCP 9898?
│     └─ Check ONTAP external engine configuration
│
├─ Are [SQS] Sent: messages present?
│  └─ No → File operations are not being triggered
│     ├─ Is the FPolicy policy enabled?
│     ├─ Are the monitored protocols/operations correct?
│     └─ Check FPolicy event configuration
│
├─ Are messages in SQS?
│  └─ No → SQS SendMessage is failing
│     └─ Check ECS task role IAM permissions
│
├─ Is Bridge Lambda executing?
│  └─ No → Check Event Source Mapping
│
└─ Are events arriving in EventBridge?
   └─ No → Check Bridge Lambda error logs
```

---

## Related Resources

- Template: `shared/templates/fpolicy-apigw.yaml`
- E2E Test: `shared/scripts/e2e_test_fpolicy.py`
- Event Sources Guide: `docs/en/event-sources.md`
- Verification Results: `docs/ja/verification-results-ems-fpolicy.md`
