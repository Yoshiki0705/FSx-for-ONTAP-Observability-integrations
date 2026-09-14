# FPolicy Pipeline Operational Guide

🌐 [日本語](../ja/fpolicy-operational-guide.md) | **English** (this page)

## Health Model

The FPolicy pipeline has four health layers. Monitor all four for production readiness.

### 1. Connection Health

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Fargate task running | ECS RunningTaskCount | < 1 for > 1 minute |
| ONTAP engine connected | ECS logs (KeepAlive) | No KeepAlive for > 5 minutes |
| KeepAlive freshness | Custom metric from ECS logs | Age > 300 seconds |

### 2. Queue Health

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| SQS visible messages | ApproximateNumberOfMessagesVisible | > 100 for > 5 minutes |
| SQS oldest message age | ApproximateAgeOfOldestMessage | > 300 seconds |

### 3. Shipping Health

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Lambda errors | Lambda Errors metric | > 0 for > 5 minutes |
| Lambda duration | Lambda Duration metric | p99 > 10 seconds |
| DLQ depth | SQS DLQ ApproximateNumberOfMessagesVisible | > 0 |

### 4. Data Freshness

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Last FPolicy event | Custom metric or Datadog query | No events for > 10 minutes (during business hours) |
| Datadog log arrival | `source:fsxn-fpolicy` count | 0 for > 10 minutes |

## Runbooks

### Fargate Task Restarted

1. Check ECS service events: `aws ecs describe-services --cluster <cluster> --services <service>`
2. Get new task IP: `aws ecs describe-tasks --cluster <cluster> --tasks <task-arn>`
3. Update ONTAP engine: `bash shared/scripts/fpolicy-update-engine-ip.sh --auto`
4. Verify KeepAlive in ECS logs, allowing up to 5 minutes. The first KeepAlive
   after a handshake can take a full `keep_alive_interval` (120 s by default), so a
   60-second check reports a healthy reconnection as a failed one

### ONTAP Engine Disconnected

1. Check Fargate task is running
2. Check security group allows inbound TCP:9898 from FSx SG
3. Check ONTAP engine status: `vserver fpolicy show-engine -vserver <svm>`
4. If task IP changed, update engine
5. If task is running and IP is correct, check network connectivity

### SQS Backlog Growing

1. Check Lambda errors in CloudWatch
2. Check Lambda concurrency (throttling)
3. Check Datadog API status
4. If Lambda is failing, check DLQ for error details
5. If Datadog is down, events will buffer safely in SQS

### Datadog Logs Missing

1. Verify `source:fsxn-fpolicy` in Datadog Log Explorer
2. Check Lambda CloudWatch Logs for shipping errors
3. Check SQS queue depth (events may be buffered)
4. Check Fargate ECS logs for FPolicy events
5. Check ONTAP engine connection status

### NFS Client Hang Observed

1. Check if FPolicy is causing the hang: `vserver fpolicy show -vserver <svm>`
2. Disable FPolicy temporarily: `vserver fpolicy disable -vserver <svm> -policy-name <policy>`
3. Verify NFS operations resume
4. Investigate FPolicy scope (reduce monitored operations or volumes)
5. Re-enable with narrower scope after investigation

## FPolicy Engine IP Reconciliation

### Desired State

```
ONTAP external engine primary-servers = current healthy Fargate task private IP
```

### Reconciliation Flow

```
ECS Task State Change (RUNNING)
  → EventBridge Rule (detail-type: "ECS Task State Change", lastStatus: "RUNNING")
    → Reconciliation Lambda
      → Get current task IP from ECS API
      → Get current engine IP from ONTAP REST API
      → If different: disable policy → update engine → enable policy
      → Emit CloudWatch metric (success/failure/no-change)
```

### Prerequisites for Auto-Reconciliation

- Lambda in VPC with NAT Gateway (for ONTAP management endpoint access)
- ONTAP credentials in Secrets Manager
- IAM permissions for ECS DescribeTasks, Secrets Manager GetSecretValue
- Network reachability to ONTAP management LIF

## Synthetic Health Check

For proactive monitoring, schedule a synthetic file creation test:

1. EventBridge Scheduler triggers a Lambda every 15 minutes
2. Lambda creates a test file on the SMB share via ONTAP REST API
3. Lambda waits 30 seconds, then queries Datadog for the expected log
4. If not found, emit CloudWatch alarm

This validates the entire pipeline end-to-end without manual intervention.
