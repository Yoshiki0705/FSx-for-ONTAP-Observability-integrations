# Prerequisites and Resource Deployment Guide

🌐 [日本語](../ja/prerequisites.md) | **English** (this page)

## Overview

Before deploying any vendor integration from this project, the following prerequisite resources are required.

```
┌─────────────────────────────────────────────────────────────────┐
│              Prerequisite Resources (this guide)                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  FSx for ONTAP        S3 Bucket           S3 Access Point       │
│  (audit enabled)  →  (log storage)    →   (Lambda access)      │
│                                                                 │
│  EventBridge notifications enabled                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              Vendor Integration Stack (integrations/)            │
├─────────────────────────────────────────────────────────────────┤
│  EventBridge Rule → Lambda → Vendor API                         │
└─────────────────────────────────────────────────────────────────┘
```

## Two Deployment Patterns

### Pattern A: Add to Existing FSx for ONTAP Environment (Recommended)

Add the audit log delivery pipeline to an environment where FSx for ONTAP is already running.

**Prerequisites**:
- FSx for ONTAP file system exists
- SVM is created
- VPC, subnets, and security groups are configured

**Steps**:
1. [Step 1: Deploy Prerequisites Stack](#step-1-deploy-prerequisites-stack)
2. [Step 2: Enable FSx for ONTAP Audit Logging](#step-2-enable-fsx-ontap-audit-logging)
3. [Step 3: Verify Log Delivery](#step-3-verify-log-delivery)
4. [Step 4: Deploy Vendor Integration](#step-4-deploy-vendor-integration)

### Pattern B: Build from Scratch (Testing/Demo)

Create all resources including FSx for ONTAP from scratch.

**Steps**:
1. [Step 0: Create FSx for ONTAP](#step-0-create-fsx-for-ontap-new-builds-only)
2. Continue with Pattern A steps

---

## Step 0: Create FSx for ONTAP (New Builds Only)

> ⚠️ FSx for ONTAP is billed hourly. Remember to delete after testing.

### Create via AWS CLI

```bash
# Assumes VPC and subnets already exist
# Preferred subnet: primary
# Standby subnet: secondary (for Multi-AZ)

aws fsx create-file-system \
  --file-system-type ONTAP \
  --storage-capacity 1024 \
  --storage-type SSD \
  --subnet-ids subnet-xxxxxxxx subnet-yyyyyyyy \
  --ontap-configuration '{
    "DeploymentType": "MULTI_AZ_1",
    "ThroughputCapacity": 128,
    "PreferredSubnetId": "subnet-xxxxxxxx",
    "FsxAdminPassword": "YourSecurePassword123!",
    "EndpointIpAddressRange": "198.19.0.0/24"
  }' \
  --tags Key=Project,Value=fsxn-observability Key=Environment,Value=dev \
  --region ap-northeast-1
```

### Create SVM

```bash
# Get file system ID
FS_ID=$(aws fsx describe-file-systems \
  --query "FileSystems[?Tags[?Key=='Project' && Value=='fsxn-observability']].FileSystemId" \
  --output text --region ap-northeast-1)

# Create SVM
aws fsx create-storage-virtual-machine \
  --file-system-id $FS_ID \
  --name svm-audit-demo \
  --root-volume-security-style NTFS \
  --region ap-northeast-1
```

### Create via CloudFormation

```bash
aws cloudformation deploy \
  --template-file shared/templates/fsxn-filesystem.yaml \
  --stack-name fsxn-demo-filesystem \
  --parameter-overrides \
    VpcId=vpc-xxxxxxxx \
    PrimarySubnetId=subnet-xxxxxxxx \
    StandbySubnetId=subnet-yyyyyyyy \
    FsxAdminPassword=YourSecurePassword123! \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

> 📝 `shared/templates/fsxn-filesystem.yaml` is a large template and is not included in this project. Refer to the [AWS documentation](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/creating-file-systems.html).

---

## Step 1: Deploy Prerequisites Stack

Creates S3 bucket, S3 Access Point, and enables EventBridge notifications.

```bash
aws cloudformation deploy \
  --template-file shared/templates/prerequisites.yaml \
  --stack-name fsxn-observability-prerequisites \
  --parameter-overrides \
    AuditLogBucketName=my-company-fsxn-audit-logs-ap-northeast-1 \
    AccessPointName=fsxn-audit-ap \
    VpcId=vpc-xxxxxxxx \
    RetentionDays=90 \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

### Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `AuditLogBucketName` | ✅ | - | S3 bucket name (globally unique) |
| `AccessPointName` | ❌ | `fsxn-audit-ap` | S3 Access Point name |
| `VpcId` | ❌ | - | Specify for VPC restriction |
| `RetentionDays` | ❌ | 90 | Days before Glacier transition |
| `EnableGlacierTransition` | ❌ | true | Enable/disable Glacier transition |
| `KmsKeyArn` | ❌ | - | Custom KMS key (defaults to aws/s3) |

### Verify Stack Outputs

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-observability-prerequisites \
  --query "Stacks[0].Outputs" \
  --output table \
  --region ap-northeast-1
```

Key outputs:
- `AccessPointArn` — Use as `S3AccessPointArn` parameter in vendor stacks
- `AuditLogBucketName` — Configure as FSx for ONTAP audit log destination

---

## Step 2: Enable FSx for ONTAP Audit Logging

### Method A: Use the Setup Script (Recommended)

```bash
# Dry run (preview commands only)
bash shared/scripts/ontap-audit-setup.sh \
  --endpoint 10.0.1.100 \
  --svm svm-prod-01 \
  --format evtx \
  --dry-run

# Execute
bash shared/scripts/ontap-audit-setup.sh \
  --endpoint 10.0.1.100 \
  --svm svm-prod-01 \
  --format evtx
```

### Method B: ONTAP System Manager (GUI)

1. FSx Console → File System → Open management endpoint URL in browser
2. **Storage** → **SVMs** → Select target SVM
3. **Settings** → **Audit** → **Enable**
4. Configure:
   - Destination: `/vol/audit_logs`
   - Format: EVTX or JSON
   - Rotation: Size-based, 100MB

### Method C: Manual SSH

```bash
# SSH to FSx for ONTAP management endpoint
ssh admin@<management-endpoint-ip>

# Execute in ONTAP CLI
vserver audit create -vserver svm-prod-01 \
  -destination /vol/audit_logs \
  -format evtx \
  -rotate-size 100MB

vserver audit enable -vserver svm-prod-01

# Verify
vserver audit show -vserver svm-prod-01
```

### Audit Log S3 Delivery Options

There are multiple ways to deliver FSx for ONTAP audit logs to an S3 bucket:

#### Option 1: FSx Automatic Backups + S3 Export

Use FSx automatic backups and export to S3. Low real-time capability but simple setup.

#### Option 2: DataSync Periodic Sync

```bash
# Create a DataSync task for periodic S3 sync
aws datasync create-task \
  --source-location-arn arn:aws:datasync:ap-northeast-1:123456789012:location/loc-xxxxx \
  --destination-location-arn arn:aws:datasync:ap-northeast-1:123456789012:location/loc-yyyyy \
  --schedule "ScheduleExpression=rate(5 minutes)" \
  --name fsxn-audit-sync
```

#### Option 3: FSx for ONTAP S3 Access Point (Recommended, Latest)

FSx for ONTAP S3 Access Points allow direct S3 API access to volume data. Attach an S3 Access Point to the audit log volume for direct Lambda read access.

```bash
aws fsx create-and-attach-s3-access-point \
  --name fsxn-audit-ap \
  --type ONTAP \
  --ontap-configuration 'VolumeId=fsvol-0123456789abcdef0,FileSystemIdentity={Type=UNIX,UnixUser={Name=root}}' \
  --region ap-northeast-1
```

Confirm it reached `AVAILABLE` and note the ARN — this is the value every vendor
stack takes as `S3AccessPointArn` / `FsxS3AccessPointArn`:

```bash
aws fsx describe-s3-access-point-attachments \
  --names fsxn-audit-ap \
  --region ap-northeast-1 \
  --query 'S3AccessPointAttachments[0].{Lifecycle:Lifecycle,Arn:S3AccessPoint.ResourceARN}'
```

In our testing this reached `AVAILABLE` in about 20 seconds.

> 📝 FSx for ONTAP S3 Access Points differ from regular S3 Access Points. They attach directly to FSx volumes, providing S3 API access to NFS/SMB data.
>
> Use the `fsx` API shown above. Neither `aws s3control create-access-point`
> (which points at an S3 bucket) nor `aws fsx create-data-repository-association`
> (which is FSx for Lustre S3 linkage) can create one.

**Network origin is decided at creation and cannot be changed.** Omitting
`--s3-access-point 'VpcConfiguration={VpcId=...}'` — as above — creates an
Internet-origin access point, which is what these integrations expect: the
shipper Lambda runs outside the VPC and reaches the access point over the
internet path. See [Deploying a vendor integration](vendor-deployment-common.md)
for the full origin/placement matrix.

---

## Step 3: Verify Log Delivery

### Verify logs are arriving in S3 bucket

```bash
# List objects in bucket
aws s3 ls s3://my-company-fsxn-audit-logs-ap-northeast-1/audit/ --recursive

# Check latest log files
aws s3 ls s3://my-company-fsxn-audit-logs-ap-northeast-1/audit/ \
  --recursive --human-readable | tail -5
```

### Verify EventBridge events are firing

```bash
# Check S3 events via CloudTrail
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=PutObject \
  --max-results 5 \
  --region ap-northeast-1
```

### Test with a sample file

```bash
# Upload a test audit log file
aws s3 cp integrations/datadog/tests/test_data/sample_audit_logs.json \
  s3://my-company-fsxn-audit-logs-ap-northeast-1/audit/svm-prod-01/2026/01/15/test_audit.json
```

---

## Step 4: Deploy Vendor Integration

Once prerequisites are ready, deploy the vendor integration stack.

```bash
# Get prerequisite stack outputs
AP_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-observability-prerequisites \
  --query "Stacks[0].Outputs[?OutputKey=='AccessPointArn'].OutputValue" \
  --output text --region ap-northeast-1)

BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name fsxn-observability-prerequisites \
  --query "Stacks[0].Outputs[?OutputKey=='AuditLogBucketName'].OutputValue" \
  --output text --region ap-northeast-1)

# Deploy Datadog integration
aws cloudformation deploy \
  --template-file integrations/datadog/template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:datadog-api-key \
    DatadogSite=datadoghq.com \
    S3BucketName=$BUCKET_NAME \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

---

## Resource Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│ AWS Account                                                             │
│                                                                         │
│  ┌──────────────────┐                                                   │
│  │ FSx for ONTAP    │                                                   │
│  │                  │    Audit log output                                │
│  │  SVM: svm-prod   │──────────────┐                                    │
│  │  Audit: enabled  │              │                                    │
│  └──────────────────┘              ▼                                    │
│                           ┌──────────────────┐                          │
│                           │ S3 Bucket        │                          │
│                           │ (audit logs)     │                          │
│                           │                  │◀── EventBridge enabled    │
│                           └────────┬─────────┘                          │
│                                    │                                    │
│                           ┌────────┴─────────┐                          │
│                           │ S3 Access Point  │                          │
│                           │ (for Lambda)     │                          │
│                           └────────┬─────────┘                          │
│                                    │                                    │
│  ┌──────────────────┐              │    ┌──────────────────┐            │
│  │ EventBridge Rule │──────────────┼───▶│ Lambda           │            │
│  │ (Object Created) │              │    │ (log shipper)    │────────┐   │
│  └──────────────────┘              │    └──────────────────┘        │   │
│                                    │                                │   │
│                                    │    ┌──────────────────┐        │   │
│                                    └───▶│ Secrets Manager  │        │   │
│                                         │ (API Key)        │        │   │
│                                         └──────────────────┘        │   │
└─────────────────────────────────────────────────────────────────────┼───┘
                                                                      │
                                                                      ▼
                                                          ┌──────────────────┐
                                                          │ Vendor API       │
                                                          │ (Datadog, etc.)  │
                                                          └──────────────────┘
```

---

## Troubleshooting

### Audit logs not arriving in S3

1. **Check FSx for ONTAP side**:
   ```
   ssh admin@<endpoint>
   vserver audit show -vserver <svm-name> -fields state
   # Verify state is "true"
   ```

2. **Check volume**:
   ```
   volume show -vserver <svm-name> -volume audit_logs
   # Verify volume exists and has sufficient free space
   ```

3. **Check S3 delivery configuration**:
   - Verify DataSync task is running successfully
   - Verify FSx S3 Access Point is configured correctly

### EventBridge events not firing

1. Verify EventBridge notifications are enabled on the bucket:
   ```bash
   aws s3api get-bucket-notification-configuration \
     --bucket <bucket-name> --region ap-northeast-1
   ```
   Confirm `EventBridgeConfiguration` is present.

2. Redeploy the prerequisites stack (includes notification config).

### S3 Access Point read errors

1. Verify Lambda IAM role has `s3:GetObject` permission
2. Confirm resource ARN uses `<access-point-arn>/object/*` format
3. If VPC-restricted, ensure Lambda is in the same VPC

### FPolicy Fargate: ECR image pull timeout

**Symptom**: ECS task fails to start with `CannotPullContainerError` or `dial tcp ...ecr...: i/o timeout`.

**Cause**: The template defaults to `AssignPublicIp: DISABLED`. Without a NAT Gateway or ECR VPC Endpoints, Fargate cannot reach ECR.

**Solutions** (choose one):
1. Set `AssignPublicIp=ENABLED` parameter (simplest, suitable for testing)
2. Add a NAT Gateway to the subnet (production recommended)
3. Create Interface VPC Endpoints: `com.amazonaws.<region>.ecr.api`, `com.amazonaws.<region>.ecr.dkr`, plus a Gateway Endpoint for S3

### FPolicy Fargate: ONTAP cannot connect to FPolicy server

**Symptom**: No `[+] Connection from` entries in the Fargate container log.

**Cause**: Security Group misconfiguration or incorrect FPolicy engine IP.

**Solution**:
1. Verify the FPolicy server Security Group allows inbound TCP on port 9898 from the FSx SVM Security Group
2. If the Fargate task restarted, the IP changed — check that the ONTAP FPolicy engine `primary_servers` matches the current task IP:
   ```bash
   # Get current task IP
   TASK_ARN=$(aws ecs list-tasks --cluster <cluster> --service <service> --query 'taskArns[0]' --output text)
   aws ecs describe-tasks --cluster <cluster> --tasks $TASK_ARN \
     --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value' --output text
   ```
3. The template includes an IP Updater Lambda that automatically updates the engine IP on task restart (configure `FsxnMgmtIp`, `FsxnSvmUuid`, `FsxnEngineName`, `FsxnPolicyName`, `FsxnCredentialsSecret` parameters)

### Automated Response Lambda: ModuleNotFoundError

**Symptom**: Lambda invocation fails with `ModuleNotFoundError: No module named 'ontap_response'`

**Cause**: The `fsxn-shared-python` Lambda Layer is not attached to the function.

**Solution**: Provide the `SharedPythonLayerArn` parameter during stack deployment, or attach manually:
```bash
# Build and publish layer
bash shared/python/build-layer.sh
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name fsxn-shared-python \
  --zip-file fileb://shared/python/dist/fsxn-shared-python-layer.zip \
  --compatible-runtimes python3.12 \
  --query 'LayerVersionArn' --output text)

# Attach to function
aws lambda update-function-configuration \
  --function-name <stack-name>-handler \
  --layers $LAYER_ARN
```

### Automated Response Lambda: Timeout (60s)

**Symptom**: Lambda times out without completing the action.

**Cause**: Lambda in VPC cannot reach Secrets Manager or SNS APIs.

**Solution**:
1. Deploy with `CreateVpcEndpoints=true` (default), or
2. Ensure the VPC has Interface VPC Endpoints for `secretsmanager` and `sns`
3. Verify the VPC Endpoint Security Group allows TCP 443 from the Lambda Security Group

---

## References

- [FSx for ONTAP File Access Auditing](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html)
- [S3 Access Points for FSx](https://aws.amazon.com/blogs/storage/bridge-legacy-and-modern-applications-with-amazon-s3-access-points-for-amazon-fsx/)
- [Process Files Serverlessly with Lambda](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/tutorial-process-files-with-lambda.html)
- [Using EventBridge with S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventBridge.html)
- [NetApp Workload Factory - Journal Table](https://docs.netapp.com/us-en/workload-fsx-ontap/setup-journal-table.html)


## FSx for ONTAP S3 Access Point Permission Checklist

Before deploying the vendor integration, verify the following for your FSx for ONTAP S3 Access Point:

- [ ] Access point is attached to the correct audit volume
- [ ] File system identity has read permission on the audit directory
- [ ] Lambda execution role has `s3:GetObject` and `s3:ListBucket` through the access point ARN
- [ ] Access point policy permits the Lambda execution role principal
- [ ] Network path is validated (Lambda outside VPC, or VPC with NAT Gateway)
- [ ] Access point is not in MISCONFIGURED state (volume online, identity resolvable)

Verify access with:

```bash
aws s3api list-objects-v2 \
  --bucket <fsx-s3-access-point-arn-or-alias> \
  --max-keys 5 \
  --region ap-northeast-1
```

If this returns audit log files, the access point is correctly configured for Lambda.

## Deployment Topologies

This project supports multiple deployment patterns:

| Pattern | Description | When to Use |
|---------|-------------|-------------|
| Same-account local | FSx + Lambda + vendor integration in one account | Single workload, simplest setup |
| Centralized logging | Workload accounts expose telemetry to a central observability account | Enterprise with shared security/logging account |
| Partner/MSP managed | User workload account + partner-operated integration | Managed service offerings |

For multi-account deployments, cross-account S3 Access Point access and IAM trust relationships are required. See the [operational guide](operational-guide.md) for details.


---

## See Also

- [**Deployment Guide**](deployment-guide.md) — Comprehensive guide for integrating all stacks with existing FSx for ONTAP environments (parameter mapping, VPC Endpoint conflict matrix, verified paths, cost estimates)
