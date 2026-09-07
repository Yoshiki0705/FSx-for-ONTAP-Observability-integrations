# FSx for ONTAP Lakehouse Long-Term Retention

🌐 [日本語](../../docs/ja/lakehouse-long-term-retention.md) | [English](../../docs/en/lakehouse-long-term-retention.md)

## Overview

Ships Amazon FSx for NetApp ONTAP audit logs to Amazon S3 in Apache Parquet format via Kinesis Data Firehose, catalogued in AWS Glue (Partition Projection, no crawler required), queryable with Amazon Athena and/or Snowflake. This complements — it does not replace — the 9 observability vendor integrations in this project: those are built for search and alerting; this pipeline is built for multi-year retention and SQL analytics over the same audit log data.

**PoC time estimate**: ~5 minutes to deploy the stack, then up to 5 minutes (the default Firehose buffer interval) for the first Parquet file to appear in S3.

> ✅ **E2E verified** (2026-07-20, ap-northeast-1): 500 synthetic audit log records sent through the real pipeline. Both Amazon Athena and Snowflake (External Table) returned exactly 500 rows and matching `GROUP BY operation, result` aggregations against the same underlying Parquet data. See [docs/en/lakehouse-long-term-retention.md](../../docs/en/lakehouse-long-term-retention.md) for full verification details, including three CloudFormation gotchas found and fixed during validation.

## Architecture

```
FSx for ONTAP → S3 (audit log JSON, same source as the vendor pipelines)
             → Kinesis Data Firehose (JSON → Parquet, Snappy compression)
             → S3 (retention bucket, partitioned by year/month/day)
             → AWS Glue Data Catalog (Partition Projection)
                 ├── Amazon Athena (pay-per-query SQL)
                 └── Snowflake External Table (Storage Integration)
```

Full architecture, table schema, cost comparison, and query examples: [docs/en/lakehouse-long-term-retention.md](../../docs/en/lakehouse-long-term-retention.md) / [docs/ja/lakehouse-long-term-retention.md](../../docs/ja/lakehouse-long-term-retention.md).

## What You Need Before Starting

- [ ] An AWS account with permission to create S3 buckets, a Glue database/table, a Kinesis Data Firehose delivery stream, IAM roles, and an Athena workgroup
- [ ] Two globally-unique S3 bucket names (see [Parameters](#parameters) below)
- [ ] **Run the pre-flight check first** — this account may have AWS Lake Formation enabled, which changes what permissions Firehose needs:
  ```bash
  bash scripts/preflight-check.sh --region <your-region>
  ```
- [ ] (Optional, Snowflake path only) A Snowflake account — any edition, a free trial works — with `ACCOUNTADMIN` access

## Quick Deploy

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name fsxn-lakehouse-retention \
  --parameter-overrides \
    RetentionBucketName=<your-org>-fsxn-audit-lakehouse-<region> \
    AthenaResultsBucketName=<your-org>-fsxn-athena-results-<region> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|--------------|
| `RetentionBucketName` | ✅ | — | Globally-unique new S3 bucket name for Parquet-converted audit logs |
| `AthenaResultsBucketName` | ✅ | — | Globally-unique new S3 bucket name for Athena query results |
| `GlueDatabaseName` | ❌ | `fsxn_audit_lakehouse` | Glue Data Catalog database name |
| `GlueTableName` | ❌ | `audit_logs` | Glue Data Catalog table name |
| `BufferSizeMBs` | ❌ | `64` | Firehose buffer size in MB (64 MB is the Firehose-enforced minimum once Parquet conversion is enabled) |
| `BufferIntervalSeconds` | ❌ | `300` | Firehose buffer interval in seconds before flushing to S3 |
| `AthenaWorkgroupName` | ❌ | `fsxn-lakehouse-retention` | Athena workgroup name |
| `AthenaScanLimitGB` | ❌ | `10` | Per-query data scan cap for the Athena workgroup, in GB (one of `1/5/10/20/50/100/200/500`) |

Every parameter has an inline description in `template.yaml`'s `Metadata.AWS::CloudFormation::Interface` block, visible in the CloudFormation console's parameter form.

## Deployment Time Estimate

| Task | Approximate time |
|---|---|
| CloudFormation stack deploy | ~2-3 minutes |
| First Parquet file to appear in S3 | Up to `BufferIntervalSeconds` (default 300s) after the first record is sent, or when the buffer fills, whichever comes first |
| Snowflake Storage Integration two-phase trust setup (optional) | ~5-10 minutes |

## Snowflake Path (Optional)

```bash
# Phase 1: deploy the IAM role with a placeholder (own-account) trust policy
aws cloudformation deploy \
  --template-file snowflake-role.yaml \
  --stack-name fsxn-lakehouse-retention-snowflake \
  --parameter-overrides RetentionBucketName=<same-bucket-as-above> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

Then run `sql/01_storage_integration_and_stage.sql` in Snowflake (Phase 1 statements), copy the `DESCRIBE INTEGRATION` output values, and redeploy `snowflake-role.yaml` with `SnowflakeAccountId`/`SnowflakeExternalId` set (Phase 2). Full walkthrough: [docs/en/lakehouse-long-term-retention.md](../../docs/en/lakehouse-long-term-retention.md#querying-with-snowflake-external-table).

## Rollback and Cleanup

```bash
# 1. Delete the Athena workgroup FIRST if any queries were run against it —
#    AWS::Athena::WorkGroup deletion via CloudFormation fails if query history
#    exists, and --recursive-delete-option is only available via the CLI, not
#    as a CloudFormation stack-level option.
aws athena delete-work-group --work-group <AthenaWorkgroupName> --recursive-delete-option --region <your-region>

# 2. Empty both S3 buckets (including all object versions -- RetentionBucket
#    has versioning enabled, so a plain `aws s3 rm --recursive` leaves
#    versions behind and blocks stack deletion)
aws s3api list-object-versions --bucket <RetentionBucketName> --output json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({'Objects':[{'Key':v['Key'],'VersionId':v['VersionId']} for v in d.get('Versions',[])+d.get('DeleteMarkers',[])]}))" > /tmp/versions.json
aws s3api delete-objects --bucket <RetentionBucketName> --delete file:///tmp/versions.json
aws s3 rm s3://<AthenaResultsBucketName> --recursive --region <your-region>

# 3. Delete the stacks (Snowflake role stack has no dependency on the other,
#    order between the two does not matter)
aws cloudformation delete-stack --stack-name fsxn-lakehouse-retention-snowflake --region <your-region>
aws cloudformation delete-stack --stack-name fsxn-lakehouse-retention --region <your-region>
```

## Testing

```bash
# cfn-lint and cfn-guard (this repo's blocking critical-security ruleset)
cfn-lint template.yaml snowflake-role.yaml
cfn-guard validate -d template.yaml snowflake-role.yaml -r ../../guard/rules/critical-security.guard --show-summary fail

# gitleaks
gitleaks detect --config ../../.gitleaks.toml --no-git --source .
```

## Related Documents

- [Full guide (EN)](../../docs/en/lakehouse-long-term-retention.md) / [full guide (JA)](../../docs/ja/lakehouse-long-term-retention.md) — architecture, table schema, three CloudFormation gotchas found during validation (including the Lake Formation permission requirement), cost comparison, query examples
- [Vendor Comparison](../../docs/en/vendor-comparison.md)
- [Data Classification Guide](../../docs/en/data-classification.md)
- [FSx-for-ONTAP-Lakehouse-Integrations](https://github.com/Yoshiki0705/FSx-for-ONTAP-Lakehouse-Integrations) — the sibling project this integration's Snowflake pattern and Athena/Glue IAM conventions are adapted from
