# FSx for ONTAP S3 Access Points Specification

🌐 [日本語](../ja/s3ap-fsxn-specification.md) | **English** (this page)

## Overview

Specification, constraints, and troubleshooting knowledge for FSx for ONTAP S3 Access Points used in this project.

---

## 1. Network Constraints (Most Critical)

### Root Cause

**Internet-origin FSx for ONTAP S3 Access Points timed out when accessed from VPC Lambda with only a Gateway Endpoint (observed in our environment).**

AWS [documents](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/configuring-network-access-for-s3-access-points.html) that VPC-origin access points work with Gateway Endpoints for traffic originating within the bound VPC. For Internet-origin APs, NAT Gateway or VPC-external Lambda is required.

### Lambda Placement Patterns

| Lambda Placement | S3 AP Access | ONTAP REST API | Recommended Use |
|-----------------|-------------|---------------|----------------|
| Outside VPC | ✅ Works | ❌ Cannot | S3 AP read-only (primary pattern for this project) |
| In VPC + S3 Gateway EP (Internet-origin AP) | ⚠️ **TIMEOUT** | ✅ Works | Use NAT or VPC-origin AP |
| In VPC + NAT Gateway | ✅ Works | ✅ Works | Production recommended |
| In VPC + VPC-origin AP + Gateway EP | ✅ Expected per AWS docs | ✅ Works | Requires VPC-origin AP |

### Design Decision for This Project

```
[Recommended]
Lambda (no VPC) → S3 AP (via internet) → Read logs → Ship to Vendor API

[Production]
Lambda (VPC + NAT GW) → S3 AP (via NAT) → Read logs → Ship to Vendor API
```

**Important**: The S3 Access Point in `shared/templates/prerequisites.yaml` is created with `NetworkOrigin: Internet`. If VPC restriction is required, a NAT Gateway is mandatory.

---

## 2. ARN Format and IAM Policy

### Correct ARN Format
```
arn:aws:s3:{region}:{account-id}:accesspoint/{access-point-name}
```

### IAM Policy Resource
```yaml
# Object operations (GetObject, PutObject)
Resource: !Sub 'arn:aws:s3:${AWS::Region}:${AWS::AccountId}:accesspoint/${AccessPointName}/object/*'

# Bucket-level operations (ListBucket)
Resource: !Sub 'arn:aws:s3:${AWS::Region}:${AWS::AccountId}:accesspoint/${AccessPointName}'
```

### Common Mistakes
```yaml
# ❌ Wrong: standard S3 bucket ARN
Resource: arn:aws:s3:::my-bucket/*

# ❌ Wrong: missing /object/* suffix
Resource: arn:aws:s3:ap-northeast-1:123456789012:accesspoint/my-ap/*

# ✅ Correct: with /object/* suffix
Resource: arn:aws:s3:ap-northeast-1:123456789012:accesspoint/my-ap/object/*
```

---

## 3. S3 AP Resource Policy

In addition to IAM, the S3 Access Point itself requires a resource policy:

```bash
aws s3control put-access-point-policy \
  --account-id 123456789012 \
  --name fsxn-audit-ap \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "AllowLambdaRead",
      "Effect": "Allow",
      "Principal": {"AWS": "arn:aws:iam::123456789012:role/fsxn-datadog-lambda-role"},
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
        "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap/object/*"
      ]
    }]
  }'
```

---

## 4. boto3 Usage

```python
import boto3

s3_client = boto3.client("s3")

# Use S3 AP ARN as the Bucket parameter
response = s3_client.get_object(
    Bucket="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
    Key="audit/svm-prod-01/2026/01/15/audit_log.json"
)

# ListObjectsV2 works the same way
response = s3_client.list_objects_v2(
    Bucket="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
    Prefix="audit/svm-prod-01/"
)
```

---

## 5. Unsupported S3 APIs / Features

| Feature | Status | Impact | Workaround |
|---------|--------|--------|-----------|
| GetBucketNotificationConfiguration | ❌ | Cannot use event-driven triggers | Use standard S3 bucket EventBridge notifications |
| S3 Event Notifications | ❌ | Cannot directly trigger Lambda | Use EventBridge Rule |
| Object Lifecycle | ❌ | No automatic deletion/transition | Custom Lambda for periodic cleanup |
| Object Versioning | ❌ | No version management | DynamoDB version tracking |
| Presigned URLs | ❌ | No time-limited sharing | Copy to standard S3 + presign |
| SSE-KMS | ❌ | No custom KMS keys | FSx volume-level KMS encryption |
| PutObject > 5GB | ❌ | Cannot write large files | Multipart Upload (within 5GB) |

---

## 6. Troubleshooting

### Symptom: Lambda timeout reading from S3 AP

**Cause**: Lambda is in a VPC with only an S3 Gateway VPC Endpoint configured

**Fix**:
1. Move Lambda outside VPC (recommended)
2. Or add a NAT Gateway

**Verification commands**:
```bash
# Check Lambda VPC configuration
aws lambda get-function-configuration \
  --function-name fsxn-datadog-integration-shipper \
  --query 'VpcConfig'

# Check VPC Endpoints
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=vpc-xxx" \
  --query 'VpcEndpoints[*].{Service:ServiceName,Type:VpcEndpointType}'
```

### Symptom: AccessDenied

**Check 3 authorization layers**:
1. **IAM policy**: Lambda role has `s3:GetObject` + correct ARN format
2. **S3 AP resource policy**: Allows the Lambda role
3. **FSx file system permissions**: UNIX/NTFS ACLs for the user associated with the S3 AP

```bash
# Check IAM policy
aws iam get-role-policy --role-name <lambda-role> --policy-name S3AccessPointRead

# Check S3 AP policy
aws s3control get-access-point-policy --account-id <account> --name <ap-name>
```

### Symptom: ListObjectsV2 returns empty results

**Possible causes**:
- Incorrect prefix (FSx for ONTAP path structure does not start with `/`)
- S3 AP network origin is `VPC` and Lambda is in a different VPC

---

## 7. Test Design Considerations

### Unit Tests
- Verify that S3 AP ARN is passed as the `Bucket` parameter in tests
- Set `S3_ACCESS_POINT_ARN` environment variable in `conftest.py`

### Integration Tests
- Prioritize testing with Lambda outside VPC (avoids network issues)
- Run VPC-based tests only after confirming NAT Gateway is available

### CloudFormation Tests
- Validate ARN patterns with `cfn-lint`
- Verify `/object/*` suffix on IAM resources

---

## References

- [AWS Docs — FSx for ONTAP S3 AP API Support](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/access-points-for-fsxn-object-api-support.html)
- [AWS Docs — Managing S3 AP Access](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/s3-ap-manage-access-fsxn.html)
- [AWS Blog — S3 Access Points for FSx](https://aws.amazon.com/blogs/storage/bridge-legacy-and-modern-applications-with-amazon-s3-access-points-for-amazon-fsx/)
- [AWS Docs — Process files with Lambda](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/tutorial-process-files-with-lambda.html)
- [AWS Blog — AI-powered analytics with S3 AP + AD](https://aws.amazon.com/blogs/storage/enabling-ai-powered-analytics-on-enterprise-file-data-configuring-s3-access-points-for-amazon-fsx-for-netapp-ontap-with-active-directory/)

---

## 8. FSx for ONTAP S3 Access Points — Constraints & Validated Patterns

For the comprehensive compatibility matrix, validated patterns, and known constraints (confirmed with AWS Support, May 2026), refer to:

📋 **[FSx for ONTAP S3 AP Compatibility Matrix](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/docs/en/compatibility-matrix.md)**

Key constraints relevant to this project:

| Constraint | Impact | Workaround |
|-----------|--------|-----------|
| No conditional writes (If-None-Match) | Delta Lake/Iceberg/Hudi transactional writes blocked | Read-only analytics or DataSync → S3 for write workloads |
| No S3 Event Notifications | Snowpipe auto-ingest, Auto Loader file notification mode unavailable | FPolicy → Lambda, scheduled polling, or Snowpipe REST API |
| No SnapMirror S3 | Cannot replicate ONTAP S3 bucket to AWS S3 | Use DataSync (NFS → S3) as validated sync mechanism |
| ListObjectsV2 latency | 1.3-1.4x native S3 at 10-5,000 objects (measured 2026-08-05); untested beyond 5,000 | No mitigation needed at this scale. For large directories, consolidate files and partition the keyspace — see the note below |
| SSE-FSX encryption only | SSE-S3, SSE-KMS, SSE-C not supported | Use default SSE-FSX (transparent, AWS KMS managed) |
| No Object Versioning | S3 versioning not available | Use ONTAP Snapshot for point-in-time recovery |
| Presigned URLs: Not officially supported | Works in practice but not guaranteed | Use for non-critical paths only; prefer IAM-based access |
| ONTAP 9.17.1+ required | Minimum version for S3 Access Points | Verify FSx file system ONTAP version before deployment |

For the full matrix including platform-specific compatibility (Athena, Glue, EMR, Databricks, Snowflake, Bedrock), see the [complete document](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/docs/en/compatibility-matrix.md).

### ListObjectsV2 Latency — the "30-80x" Figure Is Retracted

A re-measurement on 2026-08-05 did not reproduce the previously documented "30-80x slower than native S3 for small directories" penalty. Measured medians:

| Objects | FSx for ONTAP S3 AP | Native S3 | Ratio |
|--------:|--------------------:|----------:|------:|
| 10 | 38 ms | 27 ms | 1.4x |
| 100 | 52 ms | 39 ms | 1.3x |
| 1,000 | 162 ms | 128 ms | 1.3x |
| 5,000 | 665 ms | 704 ms | 0.9x |

**Measurement conditions**: ap-northeast-1; SINGLE_AZ_1 file system, SSD 1024 GB, 128 MBps throughput; UNIX security style volume; Internet-origin access point. Medians over 5 recorded trials per data point, with one warm-up call discarded beforehand. The timed region covers only the paginated `ListObjectsV2` loop — client construction, credential resolution, and object seeding are excluded. Retries were disabled (`max_attempts=1`) so that a slow call is measured rather than silently retried. Flat and nested layouts (two directory levels, 10 objects per leaf) produced the same ratios.

- Evidence record: [`verification-pack/s3ap-list-latency/evidence/2026-08-05/benchmark-result.yaml`](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/verification-pack/s3ap-list-latency/evidence/2026-08-05/benchmark-result.yaml)
- Reproduction script: [`shared/scripts/benchmark_list_objects.py`](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/shared/scripts/benchmark_list_objects.py)

**Scope limit — larger directories are untested.** Measurement stopped at 5,000 objects. Behaviour at hundreds of thousands to millions of keys under a single prefix has not been measured. ONTAP sorts directory entries in memory to produce the lexicographic ordering `ListObjectsV2` requires, so listing cost grows with directory size. File consolidation and keyspace partitioning therefore remain the recommended design for large datasets — but base that decision on directory size and the in-memory sort, not on a small-scale latency penalty that did not reproduce.

**Origin of the original figure: unexplained.** The number entered this documentation from the May 2026 AWS Support discussion referenced at the top of this section, where it was described as a product-level characteristic. No measurement record or set of conditions was retained alongside it, so there is nothing to compare the new run against and the discrepancy cannot be attributed to a specific cause. Possible contributors, listed as possibilities only: measurement through a CLI wrapper where process startup time dominates short calls; a file system in a degraded state at the time of the original observation; or platform-side changes landing between then and this run.

**Secondary finding — keys returned per page differ.** With `MaxKeys=1000` requested, native S3 returned 1,000 keys in a single API call while the access point needed 2. At 5,000 objects the counts were 6 calls versus 5. Total wall time stayed comparable, so this is not a throughput bottleneck, but do not assume identical API call counts when estimating request costs or when a client caps pagination depth.
