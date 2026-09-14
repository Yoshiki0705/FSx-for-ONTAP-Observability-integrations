# FPolicy → Grafana Cloud Loki Integration Setup

🌐 [日本語](../ja/fpolicy-setup.md) | **English** (this page)

## Overview

Setup procedure for forwarding real-time file operation events from ONTAP FPolicy External Engine to Grafana Cloud Loki.

**Architecture:**
```
ONTAP SVM (FPolicy)
    | TCP:9898 (async, no TLS)
ECS Fargate Task (FPolicy Server)
    | SQS Queue
Bridge Lambda (SQS -> EventBridge)
    | EventBridge (source: fpolicy.fsxn)
Grafana Vendor Lambda
    | OTLP Gateway / Loki Push API
Grafana Cloud Loki
```

**Label configuration:** `{job="fsxn-fpolicy", source="ontap", operation="<op>"}`

## Prerequisites

- Grafana Cloud account (API Key with `logs:write` scope)
- Credentials registered in AWS Secrets Manager
- CIFS protocol enabled on FSx for ONTAP SVM
- Private subnets available in VPC

## Step 1: Deploy FPolicy Shared Infrastructure

Deploy the FPolicy shared infrastructure (ECS Fargate + SQS + EventBridge).

```bash
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-apigw.yaml \
  --stack-name fsxn-fp-srv \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ComputeType=fargate \
    VpcId=<vpc-id> \
    SubnetIds=<private-subnet-1>,<private-subnet-2> \
    FsxnSvmSecurityGroupId=<fsxn-svm-sg-id> \
    ContainerImage=<account-id>.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:v2-timeout-fix \
  --region ap-northeast-1
```

**Verify:**
```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-fp-srv \
  --query "Stacks[0].StackStatus" \
  --region ap-northeast-1 \
  --output text
# Expected: CREATE_COMPLETE
```

## Step 2: Deploy Grafana Vendor Lambda

Deploy the Lambda that forwards events from EventBridge to Grafana Cloud.

```bash
aws cloudformation deploy \
  --template-file integrations/grafana/template-fpolicy.yaml \
  --stack-name fsxn-grafana-fpolicy \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    GrafanaCredentialsSecretArn=<secret-arn> \
    LokiEndpoint=https://otlp-gateway-prod-ap-northeast-0.grafana.net/otlp \
    EventBusName=fsxn-fpolicy-events \
  --region ap-northeast-1
```

**Deploy Lambda code:**
```bash
cd integrations/grafana/lambda
zip fpolicy.zip fpolicy_handler.py
aws lambda update-function-code \
  --function-name fsxn-grafana-fpolicy-handler \
  --zip-file fileb://fpolicy.zip \
  --region ap-northeast-1
rm fpolicy.zip
```

## Step 3: ONTAP FPolicy External Engine Configuration

### 3.1 Get Fargate Task IP

```bash
TASK_ARN=$(aws ecs list-tasks \
  --cluster fsxn-fp-srv-fpolicy \
  --service-name fsxn-fp-srv-fpolicy-server \
  --region ap-northeast-1 \
  --query 'taskArns[0]' \
  --output text)

FARGATE_IP=$(aws ecs describe-tasks \
  --cluster fsxn-fp-srv-fpolicy \
  --tasks "$TASK_ARN" \
  --region ap-northeast-1 \
  --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value' \
  --output text)

echo "Fargate Task IP: $FARGATE_IP"
```

### 3.2 Configure FPolicy via ONTAP CLI

Connect to the ONTAP CLI via SSH and execute the following commands.

```bash
# Connect to ONTAP CLI
ssh admin@<management-ip>
```

#### Create External Engine

```
vserver fpolicy policy external-engine create \
  -vserver <svm-name> \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

#### Create FPolicy Event

```
vserver fpolicy policy event create \
  -vserver <svm-name> \
  -event-name fpolicy_cifs_events \
  -protocol cifs \
  -file-operations create,write,rename,delete
```

#### Create FPolicy Policy

```
vserver fpolicy policy create \
  -vserver <svm-name> \
  -policy-name fpolicy_lambda_policy \
  -events fpolicy_cifs_events \
  -engine fpolicy_lambda_engine
```

#### Enable FPolicy Policy

```
vserver fpolicy enable \
  -vserver <svm-name> \
  -policy-name fpolicy_lambda_policy \
  -sequence-number 1
```

### 3.3 Verify Configuration

```
vserver fpolicy show -vserver <svm-name>
```

**Expected output:**
```
Vserver    Policy Name              Sequence  Status   Engine
---------- ------------------------ --------- -------- ------
<svm-name> fpolicy_lambda_policy    1         on       fpolicy_lambda_engine
```

## Step 4: Connection Health Check

### 4.1 Verify KeepAlive in ECS CloudWatch Logs

```bash
aws logs tail \
  /ecs/fsxn-fp-srv-fpolicy-server \
  --since 5m \
  --region ap-northeast-1 \
  --format short
```

`--since` has to exceed the engine's `keep_alive_interval` (ONTAP's default is `PT2M`,
so 120 seconds). At `--since 1m` a healthy connection shows nothing.

**Expected output (every 120 seconds):**
```
[KeepAlive] seq=42 | since_prev=120.1s
```

Read `since_prev` rather than the interval quoted here — it is the observed value. An
empty result is not by itself evidence that ONTAP is disconnected; widen `--since` and
confirm `keep_alive_interval` before investigating the engine IP.

### 4.2 Verify ONTAP Connection Status

```
vserver fpolicy policy external-engine show-connected \
  -vserver <svm-name> \
  -engine-name fpolicy_lambda_engine
```

## Step 5: Verify Logs in Grafana Explore

After performing file operations, verify in Grafana Explore.

**LogQL query:**
```
{job="fsxn-fpolicy"} | json
```

**Filter by operation:**
```
{job="fsxn-fpolicy"} | json | operation="create"
```

**Expected fields:** `operation`, `file_path`, `user`, `client_ip`

## Troubleshooting

### KeepAlive Messages Not Appearing

1. Verify Fargate task is in Running state
2. Verify security group allows TCP:9898 inbound
3. Verify connectivity from ONTAP SVM to Fargate task IP

### Fargate Task IP Changed

```bash
# Use the auto-update script
bash shared/scripts/fpolicy-update-engine-ip.sh --auto
```

### Lambda Errors

```bash
aws logs tail \
  /aws/lambda/fsxn-grafana-fpolicy-handler \
  --since 5m \
  --filter-pattern "ERROR" \
  --region ap-northeast-1
```
