# Log Archive Setup (Datadog → S3 Rehydration)

🌐 [日本語](../ja/log-archive-setup.md) | **English** (this page)

## Overview

Datadog log retention is typically 15 or 30 days, while audit log retention
requirements are often measured in years. This stack creates the S3 bucket and
IAM role that let Datadog archive FSx for ONTAP audit logs to your own account,
and rehydrate them later for a compliance investigation.

```
Lambda shipper ──→ Datadog Logs (hot, 15-30 days, searchable)
                        │
                        └─ Log Archive ──→ your S3 bucket ──→ Glacier
                                              (rehydrate on demand)
```

Rehydration is the point of this: archived logs are not searchable until you ask
Datadog to restore a time range back into the index. Budget hours, not minutes,
when planning an investigation.

## Why not just query S3 directly

You can — the objects are compressed JSON — but you lose the facets, saved views
and detection rules built in this integration. Rehydration exists so an
investigation uses the same queries as live monitoring. Direct S3 access is the
right tool when you need bulk analysis (Athena) rather than incident triage.

## Prerequisites

- The audit log stack deployed and shipping logs
  ([Setup Guide](setup-guide.md))
- A Datadog **Application key** in addition to the API key (archive
  configuration is an admin operation)
- Your Datadog **AWS External ID** — Datadog console →
  **Integrations** → **Amazon Web Services** → the AWS account tile
- Knowledge of your required retention period, in days

## Step 1: Deploy the archive stack

```bash
aws cloudformation deploy \
  --template-file integrations/datadog/template-log-archive.yaml \
  --stack-name fsxn-datadog-log-archive \
  --parameter-overrides \
    DatadogExternalId=<external-id-from-datadog-console> \
    RetentionDays=30 \
    GlacierRetentionDays=2555 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

`CAPABILITY_NAMED_IAM` is required — the stack creates a named IAM role that
Datadog assumes.

### Parameter Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DatadogExternalId` | — | Required, `NoEcho`. From the Datadog AWS integration page. This is the only thing preventing another Datadog customer from assuming your role |
| `ArchiveBucketName` | `''` | Empty ⇒ `fsxn-datadog-archive-<account>-<region>` |
| `DatadogAwsAccountId` | `464622532012` | `464622532012` for US1/AP1, `417141415827` for EU1 |
| `RetentionDays` | `30` | Days in S3 Standard before transitioning to Glacier |
| `GlacierRetentionDays` | `2555` | Total retention in days. 2555 ≈ 7 years |
| `KmsKeyArn` | `''` | Empty ⇒ SSE-S3 (AES256). Set a CMK ARN for SSE-KMS |

### Choosing a retention period

| Period | Days | Typical driver |
|--------|------|----------------|
| 1 year | 365 | Internal policy baseline |
| 3 years | 1095 | Common contractual minimum |
| 7 years | 2555 | Financial services record keeping (default) |
| 10 years | 3650 | Some public sector requirements |

`GlacierRetentionDays` is the **total** age at which objects expire, not an
additional period after `RetentionDays`.

> **Cost note**: transitioning to Glacier reduces storage cost but adds a
> retrieval cost and delay. Objects smaller than 128 KB are not worth
> transitioning — S3 charges a minimum billable size per object in Glacier
> tiers, so many tiny audit files can cost more archived than in Standard.
> Datadog writes reasonably large batched objects, so this is usually fine, but
> verify with your own volume before assuming savings.

### What the stack creates

| Resource | Notes |
|----------|-------|
| S3 bucket | `DeletionPolicy: Retain` — survives stack deletion by design |
| Bucket policy | Allows only the archive role; denies non-TLS requests |
| Lifecycle rule | Standard → Glacier at `RetentionDays`, expiry at `GlacierRetentionDays` |
| IAM role `<stack>-archive-role` | Assumed by Datadog, gated by the external ID |

> **Security note**: the trust relationship is enforced on the **role trust
> policy** via `sts:ExternalId`, not on the bucket policy. `sts:ExternalId` is
> only present in the request context of `sts:AssumeRole` and never appears in an
> S3 request, so a bucket policy condition on it can never match. The bucket
> policy here names the archive role explicitly instead.

## Step 2: Configure the archive in Datadog

Read the values to enter from the stack output:

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-datadog-log-archive \
  --region ap-northeast-1 \
  --query "Stacks[0].Outputs[?OutputKey=='DatadogArchiveConfig'].OutputValue" \
  --output text
```

Then:

1. Datadog console → **Logs** → **Configuration** → **Archives**
2. Click **Add a new archive**
3. Fill in:
   - **Name**: `fsxn-audit-logs`
   - **Filter**: `source:fsxn source:fsxn-ems source:fsxn-fpolicy` (a query
     matching everything you want archived)
   - **Archive type**: AWS S3
   - **AWS Account**: the account containing the bucket
   - **Bucket**: from the stack output
   - **Path**: `fsxn-audit-logs`
   - **Role**: the role name from the stack output
4. Save

Datadog validates the configuration immediately and shows an error on the
archive if it cannot assume the role.

### Ordering matters

Archives are evaluated **top to bottom** and a log goes to the **first** matching
archive only. If you already have a catch-all archive, place this one above it,
or FSx for ONTAP logs will land in the catch-all and its retention rules — not
these.

## Step 3: Verify

Archive uploads are batched, so nothing appears instantly. Wait 15 minutes, then:

```bash
aws s3 ls s3://fsxn-datadog-archive-123456789012-ap-northeast-1/fsxn-audit-logs/ \
  --recursive --human-readable | head
```

Expect objects under a `dt=<date>/hour=<hour>/` style prefix. In the Datadog
console the archive tile shows the last successful upload time — an archive with
a red state and no uploads is almost always an external ID or role name mismatch.

## Step 4: Rehydrate for an investigation

1. Datadog console → **Logs** → **Historical Views**
2. Click **New Historical View**
3. Select the archive, the time range, and a query
4. Start the rehydration and wait — this takes minutes to hours depending on the
   range and whether the objects are in Glacier

Rehydrated logs are indexed and billed like live logs for the duration of the
view, so scope the time range to what the investigation actually needs.

> **Cost note**: rehydration is billed on the volume of data scanned and
> indexed. A broad range over a 7-year archive can be expensive. Narrow by time
> first, then by query.

## Troubleshooting

### The archive shows an error in Datadog

1. **External ID mismatch** — the most common cause. Re-copy it from the Datadog
   AWS integration page and update the stack.
2. **Role name mismatch** — the role is now stack-scoped
   (`<stack-name>-archive-role`), not the fixed `DatadogLogArchiveRole` an older
   version of this template created. Use the value from the stack output.
3. **Wrong Datadog account ID** — EU1 uses `417141415827`, not the default.

Verify the trust relationship directly:

```bash
aws iam get-role --role-name fsxn-datadog-log-archive-archive-role \
  --query 'Role.AssumeRolePolicyDocument'
```

### Objects are being written but rehydration finds nothing

Check that the archive **filter** matched the logs you are looking for. A log is
archived by the first matching archive, so a broader archive higher in the list
may have taken them.

### Bucket deletion fails

The bucket is `DeletionPolicy: Retain` on purpose — it holds compliance data.
Deleting the stack leaves it in place. To remove it deliberately:

```bash
# Irreversible. Confirm your retention obligations first.
aws s3 rm s3://<bucket-name> --recursive
aws s3api delete-bucket --bucket <bucket-name> --region ap-northeast-1
```

## Cleanup

```bash
bash integrations/datadog/scripts/cleanup.sh --delete-log-archive
```

Remove the archive configuration in the Datadog console first, otherwise Datadog
keeps attempting uploads to a role that no longer exists and the archive enters
an error state.

## Related Documents

- [Setup Guide](setup-guide.md) — the audit log pipeline
- [EMS / FPolicy Setup](ems-fpolicy-setup.md) — additional sources to archive
- [Production Checklist](production-checklist.md) — retention verification items
- [Data Classification](../../../../docs/en/data-classification.md) — what is in these logs
- [Compliance Evidence Pack](../../../../docs/en/compliance-evidence-pack.md) — ISMAP/FISC/SOC2 template
