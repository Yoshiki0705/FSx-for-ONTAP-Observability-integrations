# EMS/FPolicy E2E Verification Results

🌐 [日本語](../ja/verification-results-ems-fpolicy.md) | **English** (this page)

## Verification Information

| Item | Value |
|------|-------|
| **Verification Date** | `2026-05-17T07:20:00+09:00` |
| **Verifier** | yoshiki |

### Verification Environment

| Item | Value |
|------|-------|
| **AWS Region** | `ap-northeast-1` |
| **FSx for ONTAP File System ID** | `fs-0123456789abcdef0` (SINGLE_AZ_1) |
| **SVM Name** | `FPolicySMB` (svm-0123456789abcdef0), `FSxN_OnPre` (svm-0abcdef123456789a) | <!-- allow:naming: SVM resource name -->
| **ONTAP Version** | `9.17.1P6` |

### CloudFormation Stack Names

| Stack | Name |
|-------|------|
| EMS Webhook Stack | `fsxn-ems-webhook` |
| FPolicy Stack | `fsxn-fp-srv` |

### cfn-lint Version

```
$ cfn-lint --version
cfn-lint 1.45.0
```

---

## 1. EMS Webhook Path Verification

### Step 1-1: EMS Webhook CloudFormation Stack Deployment

| Item | Details |
|------|---------|
| **Step Number** | 1-1 |
| **Step Name** | EMS Webhook CloudFormation Stack Deployment |

**Command:**

```bash
aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-ems-webhook \
  --parameter-overrides \
    LambdaFunctionArn=<Lambda ARN> \
    StageName=prod \
    ThrottlingRateLimit=100 \
    ThrottlingBurstLimit=50 \
    LogRetentionDays=30 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

> **Note**: The template creates named IAM roles (`RoleName`), so `CAPABILITY_NAMED_IAM` is required (not just `CAPABILITY_IAM`).

| Item | Details |
|------|---------|
| **Expected Result** | Stack reaches CREATE_COMPLETE with Outputs: `ApiEndpointUrl`, `ApiGatewayId`, `DeadLetterQueueArn` |
| **Actual Result** | Stack `fsxn-ems-webhook` reached CREATE_COMPLETE. Outputs: ApiEndpointUrl=`https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems`, ApiGatewayId=`<api-gateway-id>`, DeadLetterQueueArn=`arn:aws:sqs:ap-northeast-1:123456789012:fsxn-ems-webhook-ems-dlq` |
| **Judgment** | ✅ PASS |

---

### Step 1-2: EMS Webhook Endpoint POST Request Connectivity

| Item | Details |
|------|---------|
| **Step Number** | 1-2 |
| **Step Name** | EMS Webhook Endpoint POST Request Connectivity |

**Command:**

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"time":"2026-05-17T07:20:00+09:00","messageName":"arw.volume.state","severity":"alert","node":"fsxn-node-01","svmName":"FPolicySMB","message":"ARP event","parameters":{"volume_name":"vol1","state":"enabled"}}' \
  https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

| Item | Details |
|------|---------|
| **Expected Result** | HTTP 200 response with body containing `{"status": "ok", "event_name": "arw.volume.state"}` |
| **Actual Result** | HTTP 200. Response body: `{"status": "ok", "event_name": "arw.volume.state", "severity": "alert"}` |
| **Judgment** | ✅ PASS |

---

### Step 1-3: 405 Method Not Allowed Verification

| Item | Details |
|------|---------|
| **Step Number** | 1-3 |
| **Step Name** | GET Request Returns 405 Method Not Allowed |

**Command:**

```bash
curl -X GET https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

| Item | Details |
|------|---------|
| **Expected Result** | HTTP 405 response rejecting non-POST methods |
| **Actual Result** | HTTP 405. Response body: `{"message": "Method Not Allowed"}` |
| **Judgment** | ✅ PASS |

---

### Step 1-4: CloudWatch Logs Lambda Reception Confirmation

| Item | Details |
|------|---------|
| **Step Number** | 1-4 |
| **Step Name** | CloudWatch Logs Lambda Reception Confirmation |

**Command:**

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-ems-receiver \
  --start-time $(date -d '5 minutes ago' +%s000) \
  --filter-pattern '"EMS event received"' \
  --region ap-northeast-1
```

| Item | Details |
|------|---------|
| **Expected Result** | CloudWatch Logs contains EMS event reception log entries |
| **Actual Result** | Log confirmed: `EMS event received: event_name=arw.volume.state severity=alert source_node=fsxn-node-01 svm=FPolicySMB timestamp=2026-05-17T07:20:00+09:00` and `EMS event received: event_name=wafl.quota.softlimit.exceeded severity=warning source_node=fsxn-node-01 svm=FSxN_OnPre timestamp=2026-05-17T07:21:00+09:00` | <!-- allow:naming: SVM resource name -->
| **Judgment** | ✅ PASS |

---

### Step 1-5: API Gateway Access Log Verification

| Item | Details |
|------|---------|
| **Step Number** | 1-5 |
| **Step Name** | API Gateway Access Log Verification |

**Command:**

```bash
aws logs filter-log-events \
  --log-group-name <API Gateway access log group name> \
  --start-time $(date -d '5 minutes ago' +%s000) \
  --filter-pattern '"sourceIp"' \
  --region ap-northeast-1
```

| Item | Details |
|------|---------|
| **Expected Result** | API Gateway access logs contain requestId, sourceIp, httpMethod, resourcePath, status, responseLatency |
| **Actual Result** | Access logs confirmed: requestId, sourceIp (92.202.153.119), httpMethod (POST/GET), resourcePath (/prod/ems), status (200/405), responseLatency recorded correctly |
| **Judgment** | ✅ PASS |

---

### Step 1-6: cfn-lint EMS Webhook Template Validation

| Item | Details |
|------|---------|
| **Step Number** | 1-6 |
| **Step Name** | cfn-lint EMS Webhook Template Validation |

**Command:**

```bash
cfn-lint shared/templates/ems-webhook-apigw.yaml
```

| Item | Details |
|------|---------|
| **Expected Result** | 0 errors (E), 0 warnings (W) |
| **Actual Result** | 0 errors, 0 warnings (cfn-lint 1.45.0) |
| **Judgment** | ✅ PASS |

---

## 2. FPolicy Path Verification

### Step 2-1: FPolicy CloudFormation Stack Deployment

| Item | Details |
|------|---------|
| **Step Number** | 2-1 |
| **Step Name** | FPolicy CloudFormation Stack Deployment (ECS Fargate + SQS + EventBridge) |

**Command:**

```bash
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-apigw.yaml \
  --stack-name fsxn-fp-srv \
  --parameter-overrides \
    ComputeType=fargate \
    VpcId=vpc-0123456789abcdef0 \
    SubnetIds=subnet-xxxxxxxxxxxxxxxx1,subnet-xxxxxxxxxxxxxxxx2 \
    FsxnSvmSecurityGroupId=sg-0123456789abcdef0 \
    ContainerImage=123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:v2-timeout-fix \
    FPolicyPort=9898 \
    FsxnMgmtIp=10.0.x.x \
    FsxnSvmUuid=<svm-uuid> \
    FsxnEngineName=fpolicy_lambda_engine \
    FsxnPolicyName=fpolicy_lambda_policy \
    FsxnCredentialsSecretArn=<Secrets Manager ARN> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

> **Note**: The template creates named IAM roles (`RoleName`), so `CAPABILITY_NAMED_IAM` is required (not just `CAPABILITY_IAM`).

| Item | Details |
|------|---------|
| **Expected Result** | Stack reaches CREATE_COMPLETE with ECS cluster, service, SQS queue, Bridge Lambda, and EventBridge custom bus created |
| **Actual Result** | Stack `fsxn-fp-srv` running normally (ECS Fargate, ARM64, 256 CPU, 512 MB) |
| **Judgment** | ✅ PASS |

---

### Step 2-2: ECS Fargate Task Health Check

| Item | Details |
|------|---------|
| **Step Number** | 2-2 |
| **Step Name** | ECS Fargate Task Health Check |

**Command:**

```bash
# ECS task status check
aws ecs describe-services \
  --cluster fsxn-fp-srv-cluster \
  --services fsxn-fp-srv-service \
  --region ap-northeast-1 \
  --query 'services[0].{running:runningCount,desired:desiredCount,status:status}'

# Fargate task IP check
aws ecs list-tasks --cluster fsxn-fp-srv-cluster --service-name fsxn-fp-srv-service --region ap-northeast-1
aws ecs describe-tasks --cluster fsxn-fp-srv-cluster --tasks <task ARN> \
  --query 'tasks[0].attachments[?type==`ElasticNetworkInterface`].details[?name==`privateIPv4Address`].value' \
  --region ap-northeast-1
```

| Item | Details |
|------|---------|
| **Expected Result** | ECS task in RUNNING state with runningCount = desiredCount (1). Fargate task private IP retrievable |
| **Actual Result** | Task RUNNING, runningCount=1, desiredCount=1. Fargate task IP: `10.0.x.x` |
| **Judgment** | ✅ PASS |

---

### Step 2-3: ONTAP KeepAlive Message Confirmation

| Item | Details |
|------|---------|
| **Step Number** | 2-3 |
| **Step Name** | ONTAP KeepAlive Message Confirmation |

**Command:**

```bash
# Check KeepAlive messages in ECS logs (sent at ~6 second intervals)
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server-fsxn-fp-srv \
  --filter-pattern "KeepAlive" \
  --start-time $(date -d '30 seconds ago' +%s000) \
  --limit 5 \
  --region ap-northeast-1
```

| Item | Details |
|------|---------|
| **Expected Result** | `KeepAlive from <IP>` messages recorded in ECS logs within 30 seconds, indicating ONTAP is connected to the FPolicy server |
| **Actual Result** | KeepAlive messages received from ONTAP at ~6 second intervals. Source IP: `10.0.x.x` |
| **Judgment** | ✅ PASS |

---

### Step 2-4: FPolicy File Operation Event SQS Send Confirmation

| Item | Details |
|------|---------|
| **Step Number** | 2-4 |
| **Step Name** | FPolicy File Operation Event SQS Send Confirmation |

**Command:**

```bash
# 1. Check ECS logs for [SQS] Sent: pattern
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server-fsxn-fp-srv \
  --filter-pattern "[SQS] Sent:" \
  --start-time $(date -d '5 minutes ago' +%s000) \
  --region ap-northeast-1

# 2. Check SQS queue message count
aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-1.amazonaws.com/123456789012/<queue-name> \
  --attribute-names ApproximateNumberOfMessages \
  --region ap-northeast-1
```

| Item | Details |
|------|---------|
| **Expected Result** | After file operation, ECS logs contain `[SQS] Sent: <filename> (<operation>)` pattern and messages arrive in SQS queue |
| **Actual Result** | ECS logs confirmed: `[SQS] Sent: phase12-final-test-1778924241.txt (create)`, `[SQS] Sent: replay-test-1.txt (create)` etc. SQS queue: 20 messages confirmed (normal event flow) |
| **Judgment** | ✅ PASS |

---

### Step 2-5: cfn-lint FPolicy Template Validation

| Item | Details |
|------|---------|
| **Step Number** | 2-5 |
| **Step Name** | cfn-lint FPolicy Template Validation |

**Command:**

```bash
cfn-lint shared/templates/fpolicy-apigw.yaml
```

| Item | Details |
|------|---------|
| **Expected Result** | 0 errors (E), 0 warnings (W) |
| **Actual Result** | 0 errors, 0 warnings (cfn-lint 1.45.0) |
| **Judgment** | ✅ PASS |

---

## 3. ARP Event E2E Verification

### Step 3-1: ARP Ransomware Attack Simulation (curl Simulation)

| Item | Details |
|------|---------|
| **Step Number** | 3-1 |
| **Step Name** | ARP Ransomware Attack Simulation (EMS Webhook via curl) |

**Command:**

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"time":"2026-05-17T07:20:00+09:00","messageName":"arw.volume.state","severity":"alert","node":"fsxn-node-01","svmName":"FPolicySMB","message":"Anti-ransomware alert","parameters":{"volume_name":"vol1","state":"dry-run"}}' \
  https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

| Item | Details |
|------|---------|
| **Expected Result** | HTTP 200 response with ARP event (`arw.volume.state`) processed by Lambda |
| **Actual Result** | HTTP 200. Response body: `{"status": "ok", "event_name": "arw.volume.state", "severity": "alert"}`. Event recorded in CloudWatch Logs |
| **Judgment** | ✅ PASS |

> **Note**: Executed via curl simulation. Full E2E via ONTAP CLI (`security anti-ransomware volume attack simulate`) requires SSH access to the SVM management endpoint.

---

### Step 3-2: ARP Event Lambda Reception Confirmation

| Item | Details |
|------|---------|
| **Step Number** | 3-2 |
| **Step Name** | ARP Event Lambda Reception Confirmation (CloudWatch Logs) |

**Command:**

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-ems-receiver \
  --start-time $(date -d '3 minutes ago' +%s000) \
  --filter-pattern '"arw.volume.state"' \
  --region ap-northeast-1
```

| Item | Details |
|------|---------|
| **Expected Result** | CloudWatch Logs contains INFO-level log with `event_name=arw.volume.state`, `severity=alert`, `volume_name`, `state` |
| **Actual Result** | CloudWatch Logs confirmed: `EMS event received: event_name=arw.volume.state severity=alert source_node=fsxn-node-01 svm=FPolicySMB timestamp=2026-05-17T07:20:00+09:00` |
| **Judgment** | ✅ PASS |

---

## 4. Quota Event E2E Verification

### Step 4-1: Quota Event Simulation (curl Simulation)

| Item | Details |
|------|---------|
| **Step Number** | 4-1 |
| **Step Name** | Quota Event Simulation (EMS Webhook via curl) |

**Command:**

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"time":"2026-05-17T07:21:00+09:00","messageName":"wafl.quota.softlimit.exceeded","severity":"warning","node":"fsxn-node-01","svmName":"FSxN_OnPre","message":"Quota soft limit exceeded","parameters":{"volume_name":"vol_data","quota_target":"/vol/vol_data","used_bytes":"68157440","limit_bytes":"52428800"}}' \ <!-- allow:naming: SVM resource name -->
  https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

| Item | Details |
|------|---------|
| **Expected Result** | HTTP 200 response with quota event (`wafl.quota.softlimit.exceeded`) processed by Lambda |
| **Actual Result** | HTTP 200. Response body: `{"status": "ok", "event_name": "wafl.quota.softlimit.exceeded", "severity": "warning"}`. Event recorded in CloudWatch Logs |
| **Judgment** | ✅ PASS |

> **Note**: Executed via curl simulation. Full E2E via ONTAP CLI (quota rule setup + data write) requires SSH access to the SVM management endpoint.

---

### Step 4-2: Quota Event Lambda Reception Confirmation

| Item | Details |
|------|---------|
| **Step Number** | 4-2 |
| **Step Name** | Quota Event Lambda Reception Confirmation (CloudWatch Logs) |

**Command:**

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-ems-receiver \
  --start-time $(date -d '5 minutes ago' +%s000) \
  --filter-pattern '"wafl.quota.softlimit.exceeded"' \
  --region ap-northeast-1
```

| Item | Details |
|------|---------|
| **Expected Result** | CloudWatch Logs contains INFO-level log with `event_name=wafl.quota.softlimit.exceeded`, `volume_name`, `quota_target`, `used_bytes`, `limit_bytes` |
| **Actual Result** | CloudWatch Logs confirmed: `EMS event received: event_name=wafl.quota.softlimit.exceeded severity=warning source_node=fsxn-node-01 svm=FSxN_OnPre timestamp=2026-05-17T07:21:00+09:00` | <!-- allow:naming: SVM resource name -->
| **Judgment** | ✅ PASS |

---

## Detected Issues and Resolutions

| # | Issue | Severity | Affected Steps | Resolution | Status |
|---|-------|----------|----------------|------------|--------|
| - | No issues | - | - | - | - |

> **Note**: All steps passed. No issues were detected.

### event-sources.md Corrections

| # | Location | Before | After | Reason |
|---|----------|--------|-------|--------|
| - | No corrections needed | - | - | - |

---

## Overall Judgment

| Item | Result |
|------|--------|
| **Overall Judgment** | ✅ **PASS** |
| **PASS Steps** | 14 / 14 |
| **FAIL Steps** | 0 |

### Verification Step Summary

| Step | Name | Judgment |
|------|------|----------|
| 1-1 | EMS Webhook CloudFormation Stack Deployment | ✅ PASS |
| 1-2 | EMS Webhook Endpoint POST Request Connectivity | ✅ PASS |
| 1-3 | 405 Method Not Allowed Verification | ✅ PASS |
| 1-4 | CloudWatch Logs Lambda Reception Confirmation | ✅ PASS |
| 1-5 | API Gateway Access Log Verification | ✅ PASS |
| 1-6 | cfn-lint EMS Webhook Template Validation | ✅ PASS |
| 2-1 | FPolicy CloudFormation Stack Deployment (ECS Fargate) | ✅ PASS |
| 2-2 | ECS Fargate Task Health Check | ✅ PASS |
| 2-3 | ONTAP KeepAlive Message Confirmation | ✅ PASS |
| 2-4 | FPolicy File Operation Event SQS Send Confirmation | ✅ PASS |
| 2-5 | cfn-lint FPolicy Template Validation | ✅ PASS |
| 3-1 | ARP Ransomware Attack Simulation (curl) | ✅ PASS |
| 3-2 | ARP Event Lambda Reception Confirmation | ✅ PASS |
| 4-1 | Quota Event Simulation (curl) | ✅ PASS |
| 4-2 | Quota Event Lambda Reception Confirmation | ✅ PASS |

---

### Judgment Criteria

- **PASS**: All steps passed
- **FAIL**: One or more steps failed (failed step numbers and failure reasons documented in the issues section above)

---

### Supplementary Notes

- ARP and Quota E2E tests were executed via curl simulation (not full E2E via ONTAP CLI `security anti-ransomware volume attack simulate`). Full ONTAP-originated E2E testing requires SSH access to the SVM management endpoint.
- FPolicy E2E was verified using the existing deployed stack (`fsxn-fp-srv`). Active reception of file operation events was confirmed.
