# FPolicy Pipeline — Quick Deploy Guide

🌐 [日本語](../ja/fpolicy-quick-deploy.md) | **English** (this page)

Deploy the complete FPolicy file activity pipeline in 4 steps.

## Prerequisites

- AWS CLI configured with appropriate permissions
- Docker with buildx support (for linux/amd64 image build)
- FSx for ONTAP file system with a CIFS-enabled SVM
- Datadog account with API key

## Step 1: Deploy Prerequisites (ECR + Secret)

```bash
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-prerequisites.yaml \
  --stack-name fsxn-fpolicy-prerequisites \
  --parameter-overrides \
    DatadogApiKey=<your-datadog-api-key> \
  --region <your-region>
```

Note the outputs:
```bash
aws cloudformation describe-stacks --stack-name fsxn-fpolicy-prerequisites \
  --query "Stacks[0].Outputs" --output table
```

## Step 2: Build and Push FPolicy Server Image

```bash
# Get ECR URI from Step 1 output
ECR_URI=$(aws cloudformation describe-stacks --stack-name fsxn-fpolicy-prerequisites \
  --query "Stacks[0].Outputs[?OutputKey=='ECRRepositoryUri'].OutputValue" --output text)

# Authenticate to ECR
aws ecr get-login-password | docker login --username AWS --password-stdin \
  $(echo $ECR_URI | cut -d/ -f1)

# Build and push (MUST be linux/amd64 for Fargate)
docker buildx build --platform linux/amd64 \
  -t ${ECR_URI}:latest --push shared/fpolicy-server/
```

## Step 3: Deploy Fargate Stack

```bash
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-server-fargate.yaml \
  --stack-name fsxn-fpolicy-server \
  --parameter-overrides \
    VpcId=<your-vpc-id> \
    SubnetIds=<your-private-subnet> \
    FsxnSvmSecurityGroupId=<fsx-svm-security-group-id> \
    ContainerImage=${ECR_URI}:latest \
    AlarmNotificationTopicArn=<sns-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

This stack creates the ingestion queue **and** its dead letter queue. A message a
vendor Lambda cannot process is redriven to the DLQ after `MaxReceiveCount`
receives (default 5) instead of being retried forever, and `FPolicyDLQAlarm`
fires when anything lands there.

`AlarmNotificationTopicArn` is optional but recommended — without it the DLQ and
backlog alarms are visible in the CloudWatch console only and will not page
anyone.

| Output | Use |
|--------|-----|
| `FPolicyQueueArn` | Pass as `FPolicySqsQueueArn` to the vendor stack (Step 4) |
| `FPolicyDeadLetterQueueUrl` | Inspect events that never reached the backend |

> **Queue timing note**: the queue's visibility timeout is 360s — 6x the 60s
> default timeout of the vendor FPolicy Lambdas, as AWS recommends. If you raise
> a vendor Lambda's timeout above 360s, raise this too: Lambda rejects an event
> source mapping whose function timeout exceeds the queue's visibility timeout.

## Step 4: Deploy Datadog Shipping Lambda

```bash
# Get SQS ARN and Secret ARN from previous stacks
SQS_ARN=$(aws cloudformation describe-stacks --stack-name fsxn-fpolicy-server \
  --query "Stacks[0].Outputs[?OutputKey=='FPolicyQueueArn'].OutputValue" --output text)

SECRET_ARN=$(aws cloudformation describe-stacks --stack-name fsxn-fpolicy-prerequisites \
  --query "Stacks[0].Outputs[?OutputKey=='ApiKeySecretArn'].OutputValue" --output text)

aws cloudformation deploy \
  --template-file integrations/datadog/template-ems-fpolicy.yaml \
  --stack-name fsxn-datadog-ems-fpolicy \
  --parameter-overrides \
    DatadogApiKeySecretArn=${SECRET_ARN} \
    DatadogSite=<your-datadog-site> \
    FPolicySqsQueueArn=${SQS_ARN} \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

## Step 5: Configure ONTAP FPolicy

Get the Fargate task IP:
```bash
TASK_ARN=$(aws ecs list-tasks --cluster fsxn-fpolicy-server-cluster \
  --service-name fsxn-fpolicy-server-service --query "taskArns[0]" --output text)
TASK_IP=$(aws ecs describe-tasks --cluster fsxn-fpolicy-server-cluster \
  --tasks $TASK_ARN \
  --query "tasks[0].containers[0].networkInterfaces[0].privateIpv4Address" --output text)
echo "Fargate Task IP: $TASK_IP"
```

Configure ONTAP (via CLI):
```
vserver fpolicy policy external-engine create -vserver <svm-name> \
  -engine-name fpolicy_aws_engine \
  -primary-servers <task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous \
  -ssl-option no-auth

vserver fpolicy policy event create -vserver <svm-name> \
  -event-name cifs_file_events \
  -protocol cifs \
  -file-operations create,write,rename,delete

vserver fpolicy policy create -vserver <svm-name> \
  -policy-name fpolicy_aws \
  -events cifs_file_events \
  -engine fpolicy_aws_engine \
  -is-mandatory false

vserver fpolicy enable -vserver <svm-name> \
  -policy-name fpolicy_aws \
  -sequence-number 1
```

## Verify

1. Check ECS logs for KeepAlive:
```bash
aws logs tail /ecs/fsxn-fpolicy-server --follow
```

2. Create a test file on the SMB share

3. Check Datadog: `source:fsxn-fpolicy`

## Cleanup

```bash
# Disable FPolicy on ONTAP first, then:
aws cloudformation delete-stack --stack-name fsxn-datadog-ems-fpolicy
aws cloudformation delete-stack --stack-name fsxn-fpolicy-server
aws cloudformation delete-stack --stack-name fsxn-fpolicy-prerequisites
```

## Stack Dependency Order

```
fsxn-fpolicy-prerequisites (ECR + Secret)
  ↓
fsxn-fpolicy-server (Fargate + SQS)
  ↓
fsxn-datadog-ems-fpolicy (Lambda + SQS mapping)
```

Delete in reverse order.
