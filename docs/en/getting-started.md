# Getting Started

🌐 [日本語](../ja/getting-started.md) | **English** (this page)

## Prerequisites

- AWS Account
- AWS CLI v2 configured
- Amazon FSx for NetApp ONTAP file system (audit logging enabled)
- Node.js 18+ (for development)
- Python 3.12+ (for Lambda functions)

## Setup Steps

### 1. Enable FSx for ONTAP Audit Logging

Enable audit logging on the FSx for ONTAP console or CLI, and configure output to an S3 bucket.

```bash
# Enable audit logging via ONTAP CLI
vserver audit create -vserver <svm-name> \
  -destination /vol/audit_logs \
  -format evtx \
  -rotate-size 100MB
```

### 2. Create the FSx for ONTAP S3 Access Point

This is an **FSx for ONTAP** S3 Access Point, created with the `fsx` API and
attached to a volume. Do not use `aws s3control create-access-point` — that API
fronts an S3 bucket and cannot expose an FSx volume.

```bash
aws fsx create-and-attach-s3-access-point \
  --name fsxn-audit-ap \
  --type ONTAP \
  --ontap-configuration 'VolumeId=fsvol-0123456789abcdef0,FileSystemIdentity={Type=UNIX,UnixUser={Name=root}}' \
  --region ap-northeast-1
```

`VolumeId` is the volume ONTAP writes audit logs to — the `-destination` of
`vserver audit show`, not the SVM root volume.

Omitting `--s3-access-point 'VpcConfiguration={VpcId=...}'` creates an
**Internet-origin** access point, which lets the shipper Lambda run outside the
VPC. That is the simplest setup and what the commands below assume. **The network
origin cannot be changed after creation** — see the
[Datadog Setup Guide](../../integrations/datadog/docs/en/setup-guide.md#step-2-create-the-fsx-for-ontap-s3-access-point)
for the VPC-origin variant.

### 3. Deploy Vendor Integration

Use the vendor's deploy script. CloudFormation cannot inline a multi-hundred-line
handler, so `template.yaml` ships a placeholder that raises
`NotImplementedError`; the script deploys the stack **and** uploads the real code.

```bash
# Example: Datadog integration
export DATADOG_API_KEY_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX"
export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
export DATADOG_SITE="ap1.datadoghq.com"

bash integrations/datadog/scripts/deploy.sh
```

First run takes 3-5 minutes, most of it CloudFormation. To deploy by hand
instead, note two things the older version of this page got wrong: the parameter
is `FsxS3AccessPointArn` (not `S3AccessPointArn`), and the template creates named
IAM roles so it needs `CAPABILITY_NAMED_IAM` (not `CAPABILITY_IAM`).

```bash
aws cloudformation deploy \
  --template-file integrations/datadog/template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX \
    DatadogSite=ap1.datadoghq.com \
  --capabilities CAPABILITY_NAMED_IAM

# Required: replace the placeholder with the real handler
cd integrations/datadog/lambda && zip function.zip handler.py
aws lambda update-function-code \
  --function-name fsxn-datadog-integration-shipper \
  --zip-file fileb://function.zip
```

### 4. Verify Operation

Run the vendor's verification script. It checks the stack, confirms the
placeholder was replaced, invokes the shipper, and sends one synthetic log to the
vendor API — so a failure tells you which layer broke.

```bash
export DD_API_KEY_SECRET_ID="fsxn-datadog-api-key"
export DD_SITE="ap1.datadoghq.com"
bash integrations/datadog/scripts/verify.sh
```

Then perform file operations on FSx for ONTAP and confirm the events arrive.
ONTAP only exposes audit records after it rotates the staging file, so expect
**rotation interval + schedule interval**, not seconds.

## Next Steps

- [Architecture Details](architecture.md)
- [Vendor Comparison](vendor-comparison.md)
- [Datadog Setup Guide](../../integrations/datadog/docs/en/setup-guide.md)
