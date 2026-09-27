# Verification Record: Pattern 3 (Managed IoT → S3/Analytics Tail)

🌐 [日本語](../../../ja/observability-storage-patterns/verification/verification-results-pattern-3.md) | **English** (this page)

Verification record for the Pattern 3 (managed IoT → S3/analytics tail)
verification. It follows the format of the repository's existing
`docs/en/verification-results-*.md` records. Use placeholders for real IPs,
account IDs, and resource IDs.

> Pattern 3 covers reuse and recording only. This path (IoT → S3 Access Point →
> Parquet → Athena/Snowflake) is already E2E-verified in the repository
> (`integrations/lakehouse-retention/`, per AGENTS.md), and this record captures
> that existing verified result. It is not a new measurement, and the path is not
> rebuilt.

## Metadata

| Field | Value |
|---|---|
| Pattern | 3 (managed IoT → S3/analytics tail) |
| Verification date (measured) | Existing E2E-verified (`integrations/lakehouse-retention/`) |
| AWS Region | `ap-northeast-1` (default; override with `AWS_REGION`) |
| CloudFormation stack name | Reuses `integrations/lakehouse-retention/` (this pattern creates no new stack) |
| FSx for ONTAP file system | `fs-0123456789abcdef0` (placeholder) |
| SVM | `svm-0123456789abcdef0` (placeholder) |
| Component versions | Firehose / Glue / Athena / Snowflake (per `integrations/lakehouse-retention/`) |

## Claim Tier Legend

Every claim in this record carries exactly one Claim_Tier. Do not present an
unmeasured performance or cost value as fact; mark it as not measured.

| Tier | Meaning |
|---|---|
| verified | Reproduced and confirmed in this environment |
| sample-run | Confirmed once; not a general service limit |
| documented | From a cited source; not measured here |
| hypothesis | Inferred, no source, not measured |
| unverified | Not yet checked |

## Verification Steps

Each step records Command / Expected / Actual / Verdict, plus the Claim_Tier of
the claim the step establishes. Because this path is E2E-verified in the existing
implementation, Actual records the existing verified result.

| # | Command | Expected | Actual | Verdict | Claim_Tier |
|---|---|---|---|---|---|
| 1 | IoT Core → Lambda → FSx for ONTAP S3 Access Point ingestion (`cloud/iot_ingestion/`) | Telemetry reaches the S3 Access Point | Confirmed as an existing E2E-verified path | ✅ | verified |
| 2 | Firehose → S3 Parquet → Glue retention (`integrations/lakehouse-retention/`) | Parquet objects reach S3 and are cataloged in Glue | Confirmed as an existing E2E-verified path | ✅ | verified |
| 3 | Analytics query over Parquet from Athena/Snowflake | The query reads Parquet and returns results | Confirmed as an existing E2E-verified path | ✅ | verified |

## Hot Tier Storage Hypothesis

Pattern 3's path is managed ingestion plus an S3 retention/analytics tail; it has
no hot TSDB layer (which would assume local disk). The iSCSI hot-tier hypothesis
therefore does not apply to Pattern 3 (it applies to patterns 1, 4, and 5). This
record notes only that it is out of scope here.

| Target | Initial tier | After sample run |
|---|---|---|
| (not applicable to Pattern 3) | — | — |

## Cited Benchmarks

Public vendor/AWS benchmarks are cited with their source and kept distinct from
this environment's own measurements. They stay `documented`; they are not
relabeled `verified` on the strength of a citation.

| Claim | Source | Claim_Tier |
|---|---|---|
| Amazon Timestream for LiveAnalytics closed to new customers on 2025-06-20 | [AWS official announcement](https://docs.aws.amazon.com/timestream/latest/developerguide/timestream-availability-change.html) | documented |

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, single AZ), based on published
unit prices and not measured in this environment. This path reuses an existing
implementation; this record creates and deletes no new resources.

| Driver | Note | Claim_Tier |
|---|---|---|
| FSx for ONTAP file system | Billed continuously while running. Reused as a shared foundation; not created or deleted by this record | documented |
| Firehose / S3 / Glue / Athena | Usage-based, proportional to ingested/stored/scanned volume. Follows the cost model in `integrations/lakehouse-retention/` | documented |
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). Not needed when using an S3 Gateway Endpoint | documented |

## Scope Boundary

This record covers a verification environment and its results, not a
production-grade deployment. Multi-region HA and load/scale benchmarks beyond a
single sample run are out of scope unless explicitly pursued. Rebuilding the
existing path is also out of scope (reuse and recording only).

## Verdict Summary

| Step | Name | Verdict |
|---|---|---|
| 1 | IoT ingestion (S3 Access Point) | ✅ |
| 2 | Firehose → S3 Parquet → Glue retention | ✅ |
| 3 | Athena/Snowflake analytics query | ✅ |

Overall: `PASS` (records the existing E2E-verified path; not a new measurement)
