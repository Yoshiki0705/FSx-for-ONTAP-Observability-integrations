# S3 Access Points for FSx for ONTAP — Knowledge Base

🌐 [日本語](../ja/s3-access-points-knowledge.md) | **English** (this page)

**Created**: 2026-05-16
**Purpose**: Consolidate knowledge about S3 Access Points configuration for FSx for ONTAP

---

## Overview

Amazon S3 Access Points for FSx for ONTAP attaches an S3-compatible access point to an FSx for ONTAP volume. This enables S3 API access to data that is also accessible via NFS/SMB (no data copy required).

## User Mapping Mechanism

### Dual-Layer Authentication Model

S3 Access Points use two layers of authentication:

1. **AWS IAM Layer**: S3 Access Point policy controls IAM principal access
2. **File System Layer**: The "file system user ID" associated with the Access Point authorizes file access

### File System User ID

The "file system user ID" specified when creating the Access Point is used for authorization of all S3 API requests.

- **UNIX ID**: For volumes with UNIX security style (UID/GID based)
- **Windows ID**: For volumes with NTFS security style (domain\username)

**Important**: Specifying the `root` user (UID 0) grants access to all files. Specifying a restricted user limits access to that user's permissions.

### Security Style Mapping

| Volume Security Style | Recommended ID Type | Permission Model |
|------|------|------|
| UNIX | UNIX ID | mode-bits / NFSv4 ACL |
| NTFS | Windows ID | Windows ACL |
| Mixed | Case-by-case | Both models apply |

## S3 Access Point Creation Procedure

### Prerequisites

- FSx for ONTAP volume exists and is mounted (junction path is configured)
- Volume is in AVAILABLE state

### CLI Creation

```bash
aws fsx create-and-attach-s3-access-point \
  --name <access-point-name> \
  --type ONTAP \
  --ontap-configuration 'VolumeId=<volume-id>,FileSystemIdentity={Type=UNIX,UnixUser={Name=<username>}}' \
  --s3-access-point 'VpcConfiguration={VpcId=<vpc-id>}' \
  --region ap-northeast-1
```

### Response Example

```json
{
  "S3AccessPointAttachment": {
    "Lifecycle": "CREATING",
    "Name": "fsxn-audit-observability",
    "S3AccessPoint": {
      "ResourceARN": "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-observability",
      "Alias": "fsxn-audit-obser-cbsi8mwwgahuh7sans3bbtxijig4sapn1b-ext-s3alias",
      "VpcConfiguration": {
        "VpcId": "vpc-0123456789abcdef0"
      }
    }
  }
}
```

## Using S3 Access Points from Lambda

### Key Points

Use the S3 Access Point ARN as the `Bucket` parameter (instead of a regular S3 bucket name):

```python
s3_client.get_object(
    Bucket="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-observability",
    Key="path/to/audit-log.json"
)
```

### IAM Policy

The Lambda IAM role requires the following permissions:

```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject"],
  "Resource": ["arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-observability/object/*"]
}
```

## VPC Restrictions

- When an Access Point is restricted to a VPC, it is only accessible from within that VPC
- Lambda must run in the same VPC or access via VPC endpoint
- Setting to Internet origin allows access from outside the VPC (controlled by IAM policy)

## MISCONFIGURED State

An Access Point enters MISCONFIGURED state when:
- The file system user ID can no longer be resolved on the file system
- The attached volume goes offline or is unmounted

→ Automatically returns to AVAILABLE once the cause is resolved.

## Configuration for This Project

| Item | Value |
|------|-------|
| File System ID | `fs-0123456789abcdef0` |
| Volume ID | `fsvol-0123456789abcdef0` |
| Volume Name | `audit_logs_observability` |
| Junction Path | `/audit_logs_observability` |
| SVM | `svm-0123456789abcdef0` (FSxN_OnPre) | <!-- allow:naming: SVM resource name -->
| Access Point Name | `fsxn-audit-observability` |
| Access Point ARN | `arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-observability` |
| Access Point Alias | `fsxn-audit-obser-cbsi8mwwgahuh7sans3bbtxijig4sapn1b-ext-s3alias` |
| VPC | `vpc-0123456789abcdef0` |
| File System User | `root` (UNIX) |

## References

- [AWS Docs: Creating access points](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/create-access-points.html)
- [AWS Docs: Managing access point access](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/s3-ap-manage-access-fsxn.html)
- [NetApp Blog: User access mapping with S3 Access Points](https://community.netapp.com/t5/Tech-ONTAP-Blogs/User-access-mapping-with-Amazon-S3-Access-Points-for-Amazon-FSx-for-NetApp-ONTAP/ba-p/467120)
- [AWS Blog: Enabling AI-powered analytics on enterprise file data](https://aws.amazon.com/blogs/storage/enabling-ai-powered-analytics-on-enterprise-file-data-configuring-s3-access-points-for-amazon-fsx-for-netapp-ontap-with-active-directory/)
