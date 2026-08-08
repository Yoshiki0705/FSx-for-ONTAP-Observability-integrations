# Lakehouse Long-Term Retention for FSx for ONTAP Audit Logs

🌐 [日本語](../ja/lakehouse-long-term-retention.md) | **English** (this page)

## TL;DR

Observability vendors typically keep FSx for ONTAP audit logs for weeks to months. When compliance requires multi-year retention, or when the question is a SQL join across years of data rather than a log search, this guide adds a second, parallel path: the same audit log stream, converted to Apache Parquet and landed in a standard Amazon S3 bucket, queryable with Amazon Athena or Snowflake. This is not a replacement for the vendor pipelines in this project — it is a long-term retention and SQL-analytics complement to them.

**Verified end-to-end** (2026-07-19, ap-northeast-1): 500 synthetic audit log records → Kinesis Data Firehose (JSON → Parquet conversion) → S3 (Snappy-compressed Parquet, partitioned by date) → Glue Data Catalog (Partition Projection, no crawler) → Amazon Athena. `SELECT COUNT(*)` returned exactly 500; a `GROUP BY operation, result` aggregation query matched the input distribution; query execution scanned 556 bytes and completed in 417ms.

## Why a Second Path

The 9 vendor integrations in this project (Datadog, Splunk, Elastic, and others) are built for **search and alerting** — finding the log line that matters, right now, and firing an alert within seconds. They are not built for **multi-year SQL analytics** — "how many failed delete operations happened per SVM per quarter for the last 3 years" is a different kind of question, and most observability platforms' retention windows (30–90 days on standard tiers) and per-GB ingest pricing make them a poor fit for that question at scale.

| Requirement | Observability vendor (this project's 9 integrations) | Lakehouse retention (this guide) |
|---|---|---|
| Search a specific error in the last hour | ✅ Best fit | ⚠️ Works, but not built for real-time search UX |
| Alert within seconds of an anomaly | ✅ Best fit | ❌ Not designed for real-time alerting |
| Multi-year compliance retention | ⚠️ Possible, often costly at scale | ✅ Best fit (S3 storage tiers are cost-efficient at scale) |
| Ad hoc SQL joins across years of data | ❌ Most platforms don't support this well | ✅ Best fit (Athena/Snowflake are SQL-native) |
| BI dashboard / reporting tool integration | ⚠️ Varies by vendor | ✅ Best fit (standard SQL/JDBC/ODBC) |

Choose based on the question being asked, not as a vendor-versus-vendor decision — the two paths are complementary, and most production deployments run both from the same source data.

## Architecture

```
FSx for ONTAP (audit-enabled SVM)
        │
        ▼
S3 (standard bucket, audit log JSON — same source as the vendor pipelines)
        │
        ▼
Kinesis Data Firehose
  • Input:  OpenX JSON deserializer
  • Output: Parquet (Snappy compression), via DataFormatConversionConfiguration
  • Buffer: 64 MB / 300s (64 MB is the Firehose-enforced minimum once Parquet
    conversion is enabled)
        │
        ▼
S3 (retention bucket)
  audit-logs/year=YYYY/month=MM/day=DD/*.parquet
        │
        ├──────────────────────────┐
        ▼                          ▼
  AWS Glue Data Catalog      Snowflake External Stage
  (Partition Projection,     (Storage Integration,
   no crawler required)      two-phase IAM trust)
        │                          │
        ▼                          ▼
   Amazon Athena              Snowflake External Table
   (pay-per-query SQL)        (SQL + Snowflake governance features)
```

This guide does not touch the FSx for ONTAP S3 Access Point pattern used by the vendor pipelines in this project. The Firehose stream reads from a **standard S3 bucket** that already receives audit log JSON (the same source the vendor Lambdas read from). This is a deliberate scope boundary: it keeps this guide's constraints independent of the FSx for ONTAP S3 AP-specific limitations documented elsewhere in this project (no S3 Event Notifications, AD DC reachability requirements, etc.) — those constraints do not apply here, because the Firehose stream and its downstream S3 Event Notifications (used for Snowpipe auto-ingest in the Snowflake section below) operate against a bucket that supports them natively.

## Glue Table Schema

The same field names used by the vendor Lambda handlers in this project (`integrations/otel-collector/lambda/handler.py`'s `FIELD_MAPPING`, and `integrations/otel-collector/tests/test_data/sample_audit_logs.json`) are reused here for consistency:

| Column | Type | Source field | Notes |
|---|---|---|---|
| `timestamp` | string | `Timestamp` | ISO 8601, kept as string (cast in queries as needed) |
| `eventid` | string | `EventID` | ONTAP audit event ID |
| `svmname` | string | `SVMName` | Storage Virtual Machine name |
| `username` | string | `UserName` | May be empty for anonymous/system operations |
| `clientip` | string | `ClientIP` | May be empty |
| `operation` | string | `Operation` | e.g. `ReadData`, `WriteData`, `Delete` |
| `objectname` | string | `ObjectName` | File path |
| `result` | string | `Result` | `Success`, `Failure`, `Access Denied` |
| `year` / `month` / `day` | string (partition) | derived from Firehose delivery timestamp | Partition Projection — no crawler needed |

> **Data classification note**: `username`, `clientip`, and `objectname` may contain personally identifiable information depending on your organization's file naming and user directory conventions. See [Data Classification Guide](data-classification.md) for handling patterns before granting broad Athena/Snowflake query access to this table.

## Deploying the Pipeline

```bash
aws cloudformation deploy \
  --template-file integrations/lakehouse-retention/template.yaml \
  --stack-name fsxn-lakehouse-retention \
  --parameter-overrides \
    RetentionBucketName=<globally-unique-bucket-name> \
    AthenaResultsBucketName=<globally-unique-results-bucket-name> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

### What You Need Before Starting

- [ ] An AWS account with permission to create S3 buckets, a Glue database/table, a Kinesis Data Firehose delivery stream, IAM roles, and an Athena workgroup
- [ ] Two globally-unique S3 bucket names (retention bucket, Athena results bucket)
- [ ] If your account has **AWS Lake Formation** enabled (check via `aws lakeformation get-data-lake-settings`): be prepared for the Lake Formation gotcha below — this is the single most common deployment failure for this template
- [ ] (Optional, Snowflake path) A Snowflake account (Standard edition or higher; trial accounts work) with `ACCOUNTADMIN` access

### Deployment Time Estimate

| Task | Approximate time |
|---|---|
| CloudFormation stack deploy | ~2–3 minutes |
| First Parquet file to appear in S3 | Up to `BufferIntervalSeconds` (default 300s) after the first record is sent, or when the 64 MB buffer fills, whichever comes first |
| Snowflake Storage Integration two-phase trust setup | ~5–10 minutes (create integration → `DESCRIBE INTEGRATION` → redeploy IAM role with Snowflake's account/external ID → re-verify) |

### Full Parameter Reference

**`template.yaml`** (`fsxn-lakehouse-retention` stack):

| Parameter | Required | Default | Allowed values | Description |
|---|---|---|---|---|
| `RetentionBucketName` | ✅ | — | Valid S3 bucket name (3–63 chars, lowercase/digits/hyphens/periods) | Globally-unique new bucket for Parquet-converted audit logs. Must not already exist. |
| `AthenaResultsBucketName` | ✅ | — | Valid S3 bucket name | Globally-unique new bucket for Athena query results. Must not already exist. |
| `GlueDatabaseName` | ❌ | `fsxn_audit_lakehouse` | Any valid Glue database name | Change to avoid colliding with an existing database, or to namespace environments. |
| `GlueTableName` | ❌ | `audit_logs` | Any valid Glue table name | — |
| `BufferSizeMBs` | ❌ | `64` | `64`–`128` | 64 MB is the Firehose-enforced minimum once Parquet conversion is enabled. Larger = fewer, bigger Parquet files (better scan efficiency) at the cost of delivery latency. |
| `BufferIntervalSeconds` | ❌ | `300` | `60`–`900` | Lower for fresher data in Athena/Snowflake sooner, at the cost of more, smaller Parquet files. |
| `AthenaWorkgroupName` | ❌ | `fsxn-lakehouse-retention` | Any unique workgroup name in this account/region | — |
| `AthenaScanLimitGB` | ❌ | `10` | `1`/`5`/`10`/`20`/`50`/`100`/`200`/`500` | Per-query scan cap (Athena bills per TB scanned). Fixed set of values because CloudFormation has no arithmetic intrinsic to convert an arbitrary GB figure to bytes — see the `AthenaScanLimitBytes` mapping in the template. Raise for production queries that legitimately scan more (e.g. a full-table scan across several years). |

**`snowflake-role.yaml`** (`fsxn-lakehouse-retention-snowflake` stack, optional):

| Parameter | Required | Default | Allowed values | Description |
|---|---|---|---|---|
| `RetentionBucketName` | ✅ | — | Must match the bucket created by `template.yaml` | The existing retention bucket to grant Snowflake read access to. |
| `SnowflakeAccountId` | ❌ (Phase 1) / ✅ (Phase 2) | `''` (empty) | Empty, or exactly 12 digits | Leave empty for Phase 1 (own-account placeholder trust). Set to the 12-digit account ID portion of `STORAGE_AWS_IAM_USER_ARN` (from `DESCRIBE INTEGRATION`) for Phase 2. |
| `SnowflakeExternalId` | ❌ (Phase 1) / ✅ (Phase 2) | `'snowflake-placeholder'` | Any string | Phase 1: leave at default (ignored, since own-account trust doesn't check it). Phase 2: set to the exact `STORAGE_AWS_EXTERNAL_ID` value from `DESCRIBE INTEGRATION`, copied verbatim including trailing `=` characters. |

Every parameter above also has an inline description and `ParameterLabels` entry in the templates themselves, visible in the CloudFormation console's parameter form — the tables here are a quick-reference copy, not the sole source of truth.

### Caveat Discovered During Validation: Lake Formation

If your AWS account has Lake Formation enabled (common in accounts that have ever used Lake Formation for any other Glue table), the Firehose delivery role's IAM policy granting `glue:GetTable` is **not sufficient by itself**. Firehose's `DataFormatConversionConfiguration` also requires Lake Formation permissions on the same principal, or the delivery stream creation fails with:

```
Access was denied when calling Glue. Please ensure that the role specified in the
data format conversion configuration has the necessary permissions. Insufficient
Lake Formation permission(s): Required Describe on audit_logs
```

This is not obvious from the Firehose or Glue documentation, because IAM and Lake Formation permissions are evaluated independently and additively — having one without the other is not visible until you try to actually create the resource that depends on both. The template already includes the required `AWS::LakeFormation::PrincipalPermissions` resources (`DESCRIBE` on the database, `DESCRIBE`/`SELECT`/`ALTER`/`INSERT` on the table, granted to the Firehose role), with explicit `DependsOn` ordering so the delivery stream is not created until these permissions exist. If you fork this template for your own use case, keep these resources — removing them silently breaks the pipeline only in Lake Formation-enabled accounts, which makes the failure easy to miss in a non-Lake-Formation test account and then hit unexpectedly in production.

### Two Other CloudFormation Gotchas Found During Validation

- `AWS::Glue::Table` must have an explicit `DependsOn: GlueDatabase`. Without it, CloudFormation may attempt to create the table before the database exists (`Database <name> not found`), since there is no implicit dependency inferred from `DatabaseName: !Ref GlueDatabaseName` (a plain string reference, not a `!GetAtt`/`!Ref` to the database resource itself).
- `CloudWatchLoggingOptions.LogStreamName` on `AWS::KinesisFirehose::DeliveryStream`'s `ExtendedS3DestinationConfiguration` must be `!Ref FirehoseLogStream` (a plain string), not `!GetAtt FirehoseLogStream.LogStreamName`. Using `!GetAtt` here produces a `cfn-lint` `E1010` type-mismatch error, because the property expects a `String`, and `!Ref` on an `AWS::Logs::LogStream` resource already resolves to the log stream name (unlike most other resource types where `!Ref` returns an ARN or ID).

## Querying with Athena

```sql
-- Total record count for a specific day
SELECT COUNT(*) AS total_records
FROM fsxn_audit_lakehouse.audit_logs
WHERE year = '2026' AND month = '07' AND day = '19';

-- Operation/result distribution (partition pruning via year/month/day)
SELECT operation, result, COUNT(*) AS cnt
FROM fsxn_audit_lakehouse.audit_logs
WHERE year = '2026' AND month = '07' AND day = '19'
GROUP BY operation, result
ORDER BY cnt DESC;

-- Failed/denied operations by SVM, across a date range
SELECT svmname, operation, COUNT(*) AS cnt
FROM fsxn_audit_lakehouse.audit_logs
WHERE year = '2026' AND month = '07'
  AND result IN ('Failure', 'Access Denied')
GROUP BY svmname, operation
ORDER BY cnt DESC;
```

**Verified query performance** (500-record test dataset, single Parquet file, 2,970 bytes Snappy-compressed): `DataScannedInBytes=556`, `EngineExecutionTimeInMillis=417`. At this scale the numbers are not meaningful as a production benchmark — they confirm the pipeline is correct, not that it is fast at volume. Partition Projection means Athena only scans the `year=2026/month=07/day=19` prefix for the queries above, rather than listing the entire bucket; this matters increasingly as the number of partitions grows into the thousands (multi-year retention).

## Querying with Snowflake (External Table)

Snowflake support reuses the two-phase Storage Integration trust pattern already established in [fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations)'s Snowflake integration — adapted here for a standard S3 bucket target rather than an FSx for ONTAP S3 Access Point.

```bash
# Phase 1: deploy the IAM role with a placeholder (own-account) trust policy
aws cloudformation deploy \
  --template-file integrations/lakehouse-retention/snowflake-role.yaml \
  --stack-name fsxn-lakehouse-retention-snowflake \
  --parameter-overrides RetentionBucketName=<your-retention-bucket> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

```sql
-- In Snowflake (see integrations/lakehouse-retention/sql/01_storage_integration_and_stage.sql
-- for the full script)
CREATE OR REPLACE STORAGE INTEGRATION fsxn_lakehouse_retention_integration
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = '<IAMRoleArn output from snowflake-role.yaml>'
  STORAGE_ALLOWED_LOCATIONS = ('s3://<your-retention-bucket>/audit-logs/');

DESCRIBE INTEGRATION fsxn_lakehouse_retention_integration;
-- Copy STORAGE_AWS_IAM_USER_ARN and STORAGE_AWS_EXTERNAL_ID, then redeploy
-- snowflake-role.yaml with SnowflakeAccountId/SnowflakeExternalId set to
-- those values (Phase 2 trust).

CREATE OR REPLACE STAGE audit_logs_stage
  STORAGE_INTEGRATION = fsxn_lakehouse_retention_integration
  URL = 's3://<your-retention-bucket>/audit-logs/'
  FILE_FORMAT = (TYPE = 'PARQUET');

LIST @audit_logs_stage;

CREATE OR REPLACE EXTERNAL TABLE audit_logs_ext (
    "timestamp"  VARCHAR AS (value:"timestamp"::VARCHAR),
    eventid      VARCHAR AS (value:eventid::VARCHAR),
    svmname      VARCHAR AS (value:svmname::VARCHAR),
    username     VARCHAR AS (value:username::VARCHAR),
    clientip     VARCHAR AS (value:clientip::VARCHAR),
    operation    VARCHAR AS (value:operation::VARCHAR),
    objectname   VARCHAR AS (value:objectname::VARCHAR),
    result       VARCHAR AS (value:result::VARCHAR)
)
  LOCATION = @audit_logs_stage
  FILE_FORMAT = (TYPE = 'PARQUET')
  AUTO_REFRESH = FALSE;

ALTER EXTERNAL TABLE audit_logs_ext REFRESH;

SELECT COUNT(*) AS total_records FROM audit_logs_ext;
```

> **Verification status for this section: E2E verified** (2026-07-20). Using the same 500-record Parquet dataset produced by the Firehose pipeline above, the Storage Integration two-phase trust setup succeeded, `LIST @audit_logs_stage` returned the real Parquet file from S3, and `SELECT COUNT(*) FROM audit_logs_ext` returned exactly 500 — matching the Athena result exactly. A `GROUP BY operation, result` query against the same External Table returned 15 rows matching the same Operation x Result distribution seen in the Athena verification above.

### Architectural Difference from the FSx for ONTAP S3 AP Snowflake Path

Because this pipeline's data lands in a **standard S3 bucket** rather than an FSx for ONTAP S3 Access Point, real Snowpipe auto-ingest (triggered by S3 Event Notifications) is expected to work directly — the `fsxn-lakehouse-integrations` project's Snowflake integration could not use auto-ingest against FSx for ONTAP S3 APs for exactly this reason (S3 Event Notifications are not supported on FSx for ONTAP S3 APs) and had to fall back to FPolicy + Lambda + SNS + Snowpipe REST API, or scheduled `COPY INTO`. This guide's architecture removes that constraint, since the standard S3 destination bucket supports S3 Event Notifications natively. (Snowpipe auto-ingest itself was not exercised in this verification — the External Table path above was — but the underlying S3 Event Notification capability this would depend on is a standard S3 bucket feature, unlike the FSx for ONTAP S3 AP case.)

## Verified Deployment Paths

Two deployment paths were run end-to-end during validation (2026-07-19/20, ap-northeast-1). Both started from a clean AWS account state (no pre-existing stacks) and both are safe to run in either order relative to each other, since `snowflake-role.yaml` only depends on `template.yaml`'s bucket, not the reverse.

**Path A — Athena only** (no Snowflake):
1. Run `bash integrations/lakehouse-retention/scripts/preflight-check.sh --region <your-region>` — confirms AWS credentials and reports whether Lake Formation is active in the account.
2. `aws cloudformation deploy --template-file integrations/lakehouse-retention/template.yaml ...` (see [Deploying the Pipeline](#deploying-the-pipeline)).
3. Send audit log JSON records into the same S3 bucket the vendor pipelines already write to (or, for a smoke test, `PutRecord`/`PutRecordBatch` directly to the Firehose delivery stream named in the `DeliveryStreamName` output).
4. Wait up to `BufferIntervalSeconds` for the first Parquet file, then query with Athena using the `AthenaWorkgroupName` output.

**Path B — Athena + Snowflake**:
1. Steps 1–4 above (Path A), confirming Athena results are correct first — this isolates any Firehose/Glue issue from any Snowflake-specific issue.
2. Deploy `snowflake-role.yaml` Phase 1 (own-account placeholder trust).
3. In Snowflake, run the Phase 1 statements of `sql/01_storage_integration_and_stage.sql`, then `DESCRIBE INTEGRATION`.
4. Redeploy `snowflake-role.yaml` Phase 2 with `SnowflakeAccountId`/`SnowflakeExternalId` set from the `DESCRIBE INTEGRATION` output.
5. Run the remaining statements in `sql/01_storage_integration_and_stage.sql` (`CREATE STAGE`, `LIST`, `CREATE EXTERNAL TABLE`, `REFRESH`).
6. Compare `SELECT COUNT(*)` between Athena and the Snowflake External Table against the same underlying Parquet data — they should match exactly, since both read the same S3 objects.

## Day 2 Operations

- **Verify new partitions are queryable**: Partition Projection means new `year=/month=/day=` partitions do not need a Glue Crawler run or `MSCK REPAIR TABLE` — they become queryable in Athena as soon as the corresponding S3 prefix has at least one Parquet object. Confirm this periodically (e.g. `SELECT COUNT(*) WHERE year='<current year>' AND month='<current month>'`) rather than assuming it silently.
- **Monitor Firehose delivery health**: Check the `FirehoseLogGroup` (`/aws/kinesisfirehose/<stack-name>`) for `DataFormatConversionConfiguration` failures — malformed source JSON records that don't match the Glue table's column definitions land in the `errors/` S3 prefix (`ErrorOutputPrefix`) rather than failing silently. Review that prefix periodically; a growing error count usually indicates a schema drift in the upstream audit log format.
- **Review the Athena scan cap periodically**: If legitimate queries start hitting `AthenaScanLimitGB`'s `BytesScannedCutoffPerQuery` limit (query fails with an Athena `QUERY_TIMEOUT`/scan-limit error) as the dataset grows across years, raise `AthenaScanLimitGB` to the next allowed value and redeploy — this is an expected operational adjustment, not a failure to plan for.
- **Confirm lifecycle transitions are happening**: The retention bucket's lifecycle policy moves objects to S3 Standard-IA at 90 days and S3 Glacier Instant Retrieval at 365 days. Check `aws s3api list-objects-v2 --bucket <RetentionBucketName> --query "Contents[].StorageClass"` periodically (e.g. quarterly) to confirm older partitions have actually transitioned, since a lifecycle policy misconfiguration would otherwise only surface as an unexpectedly high storage bill.
- **Snowflake External Table freshness** (if used): `AUTO_REFRESH = FALSE` in this guide's example means the External Table's metadata must be refreshed manually (`ALTER EXTERNAL TABLE audit_logs_ext REFRESH;`) or on a schedule (a Snowflake Task, or an external scheduler calling the same statement) to pick up newly-arrived Parquet files. This is a deliberate choice to avoid depending on Snowpipe auto-ingest infrastructure for this initial guide; see [Architectural Difference from the FSx for ONTAP S3 AP Snowflake Path](#architectural-difference-from-the-fsx-for-ontap-s3-ap-snowflake-path) for the auto-ingest option.
- **Periodic access review**: Since this pipeline exposes potentially sensitive fields (`username`, `clientip`, `objectname`) to anyone with Athena/Snowflake query access to this table, periodically review who has that access against your organization's data classification policy (see [Data Classification Guide](data-classification.md)).

## Rollback and Cleanup

Cleanup order matters — deleting resources out of order produces avoidable CloudFormation `DELETE_FAILED` states.

```bash
# 1. Delete the Athena workgroup FIRST if any queries were ever run against it.
#    AWS::Athena::WorkGroup deletion through CloudFormation fails with query
#    history still attached; --recursive-delete-option is a CLI-only flag,
#    not available as a CloudFormation stack-level setting, so this step
#    must be run manually before stack deletion.
aws athena delete-work-group \
  --work-group <AthenaWorkgroupName> \
  --recursive-delete-option \
  --region <your-region>

# 2. Empty both S3 buckets. RetentionBucket has versioning enabled, so a
#    plain `aws s3 rm --recursive` deletes current objects but leaves old
#    versions and delete markers behind — CloudFormation stack deletion of a
#    non-empty (even if "logically empty") versioned bucket fails.
aws s3api list-object-versions --bucket <RetentionBucketName> --output json | \
  python3 -c "
import json, sys
d = json.load(sys.stdin)
objs = [{'Key': v['Key'], 'VersionId': v['VersionId']}
        for v in d.get('Versions', []) + d.get('DeleteMarkers', [])]
print(json.dumps({'Objects': objs}))
" > /tmp/lakehouse-versions.json
aws s3api delete-objects --bucket <RetentionBucketName> --delete file:///tmp/lakehouse-versions.json
rm -f /tmp/lakehouse-versions.json

aws s3 rm s3://<AthenaResultsBucketName> --recursive --region <your-region>

# 3. Delete the Snowflake role stack (if deployed) and the main stack. Order
#    between these two does not matter -- snowflake-role.yaml has no
#    CloudFormation-level dependency on template.yaml (it only references
#    the bucket NAME as a parameter string, not a cross-stack !ImportValue),
#    so either can be deleted first or independently of the other.
aws cloudformation delete-stack --stack-name fsxn-lakehouse-retention-snowflake --region <your-region>
aws cloudformation delete-stack --stack-name fsxn-lakehouse-retention --region <your-region>

# 4. (Snowflake side, if used) Drop the Snowflake-side objects independently
#    -- they are not managed by CloudFormation and will not be cleaned up by
#    deleting the AWS stacks above.
#    In Snowflake: DROP EXTERNAL TABLE audit_logs_ext; DROP STAGE audit_logs_stage;
#    DROP STORAGE INTEGRATION fsxn_lakehouse_retention_integration;
```

If a stack deletion still fails after following the above (e.g. a `DELETE_FAILED` state persists), check the specific resource named in the CloudFormation console's "Status reason" column before retrying — retrying a `delete-stack` without addressing the underlying resource-in-use condition (an S3 bucket that still has objects, a Lake Formation permission that was independently revoked out-of-band, etc.) will fail again with the same error.

## Cost Comparison

| Component | Approximate monthly cost driver | Notes |
|---|---|---|
| S3 storage (retention bucket) | Standard S3 pricing, transitioning to S3 Standard-IA at 90 days and S3 Glacier Instant Retrieval at 365 days (this template's lifecycle policy) | Parquet + Snappy compression reduces stored bytes significantly vs. raw JSON (illustrative: the 500-record test produced a 2,970-byte Parquet file; the equivalent raw JSON is roughly 25x larger) |
| Kinesis Data Firehose | Per-GB ingested + per-GB format conversion charge | Charged on the volume of audit log data shipped, independent of query volume |
| Athena | Per-TB-scanned ($5/TB as of this writing) | Partition Projection + Parquet columnar format minimize bytes scanned per query; the 10 GB per-query cap configured in this template's Athena workgroup bounds a single runaway query's cost |
| Snowflake (if used) | Snowflake compute credits (warehouse) + optional Snowflake-side storage if using `COPY INTO` instead of an External Table | External Table avoids duplicate storage cost (data stays in S3); `COPY INTO` duplicates storage but enables Snowflake-native performance features (clustering, Time Travel) |

**Compared to keeping the same data in an observability vendor for multi-year retention**: most vendor platforms charge per-GB-ingested-per-day-retained, which compounds with retention length. S3 storage cost does not compound with retention length in the same way (lifecycle transitions reduce it further over time). For a compliance requirement measured in years rather than months, this cost structure difference is usually the deciding factor — but for a use case genuinely requiring seconds-latency alerting, the vendor platforms remain the right tool, since this lakehouse path is not designed for that latency.

## Related Documents

- [integrations/lakehouse-retention/README.md](../../integrations/lakehouse-retention/README.md) — quick-start pointer, deployment time estimates, and the pre-flight check script
- [Vendor Comparison](vendor-comparison.md)
- [Data Classification Guide](data-classification.md)
- [Pipeline SLO Definitions](pipeline-slo.md)
- [Lakehouse Monitoring Patterns](lakehouse-monitoring-patterns.md) — operational metrics for FSx for ONTAP + lakehouse integrations (a different concern: monitoring the pipeline's health, not querying the audit data itself)
- [fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations) — the sibling project this guide's Snowflake pattern and Athena/Glue IAM conventions are adapted from
