# Pattern 3 Setup Guide: Managed IoT → S3/Analytics Tail Reuse Record

🌐 [日本語](../ja/setup-guide.md) | **English** (this page)

How to record verification of the managed IoT telemetry path as a reuse of the
existing E2E-verified implementation. Pattern 3 covers reuse and recording only:
it creates no net-new template and does not rebuild the existing path. This
covers a verification environment and a verification record, not a
production-grade deployment.

## Overview

Managed IoT telemetry is ingested through IoT Core → Lambda → FSx for ONTAP S3
Access Points, then retained and analyzed through Firehose → S3 Parquet → Glue →
Athena/Snowflake. This path is already E2E-verified in the repository
(`integrations/lakehouse-retention/`), so this pattern does not rebuild it; it
only captures the verified result as a verification record.

Amazon Timestream for LiveAnalytics closed to new customers on 2025-06-20. The
reused path does not depend on Timestream; it uses the Firehose → S3 Parquet →
Athena/Snowflake analytics tail. Timestream, Athena, and Snowflake are each
options suited to different contexts, and this record does not recommend a
particular analytics engine.

## Existing Coverage vs Net-New

| Layer | Category | Where |
|---|---|---|
| IoT ingestion (IoT Core → Lambda → S3 Access Point) | reused | ontap-edge-to-cloud-ai `cloud/iot_ingestion/` (doc pointer) |
| Retention and analytics tail (Firehose → S3 Parquet → Glue → Athena/Snowflake) | reused | `integrations/lakehouse-retention/` (doc pointer, E2E verified) |
| FSx for ONTAP file system + SVM | reused | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml` (doc pointer) |
| net-new (layers created here) | none | — |

Net-new is "none" because this path already exists and is E2E-verified. The
existing path is not copied into a new file; it is referenced by exact path as a
documentation pointer. Pattern 3 in `shared/reuse-references.yaml` keeps
`net_new` empty (`[]`) and declares reuse only.

## Reuse Pointers

Reference these existing assets by exact path rather than copying them.

- **Retention and analytics tail**: [`integrations/lakehouse-retention/`](../../../lakehouse-retention/) — Firehose → S3 Parquet → Glue → Athena/Snowflake. E2E-verified in the repository (per AGENTS.md).
- **IoT ingestion**: ontap-edge-to-cloud-ai `cloud/iot_ingestion/` — IoT Core → Lambda → FSx for ONTAP S3 Access Point. A documentation pointer into the sibling repository; existence is checked only where the sibling checkout is present.

These are configuration, scripts, and a deployed implementation, not nested-stack
targets, so they are referenced as documentation pointers rather than IaC-level
references.

## What You Need Before Starting

Pattern 3 stands up no new resources, so there are no new parameters to gather. To
verify and record the existing E2E-verified path, confirm the following existing
assets are available. None of them are created by this record.

| What to gather | How to obtain |
|---|---|
| A running FSx for ONTAP file system (shared foundation) | `aws fsx describe-file-systems --query 'FileSystems[?FileSystemType==\`ONTAP\`].FileSystemId' --output text` |
| The retention/analytics tail implementation (E2E-verified) | Reference `integrations/lakehouse-retention/` (do not copy) |
| The IoT ingestion example | Reference ontap-edge-to-cloud-ai `cloud/iot_ingestion/` (existence checked only where the sibling checkout is present) |

## Parameters

This pattern has no net-new template, so there are no parameters to set. The
existing path's parameters follow the documentation of each reuse target
(`integrations/lakehouse-retention/` and `cloud/iot_ingestion/`).

## VPC Endpoint Conflict Matrix

This pattern stands up no new resources, so it adds no VPC endpoints and has no
conflict matrix of its own. If you temporarily stand up the reused
`integrations/lakehouse-retention/` for verification, follow its VPC assumptions in
that documentation. The preflight profile `pipeline-pattern-3` has no VPC checks
and validates S3 Access Point access only (reuse-only).

## Deployment Time Estimates

This pattern performs no new deployment, so deployment-time estimates do not apply.
If you temporarily stand up the existing path, its timing follows the
`integrations/lakehouse-retention/` documentation.

## Intended Deployment Paths

This pattern has no new deployment path. It records the result of the existing
E2E-verified path (IoT → S3 Access Point → Parquet → Athena/Snowflake), whose
Claim_Tier is `verified` (existing). No new live run is involved.

## FSx for ONTAP Guardrail

The reused path writes objects to S3 Access Points. S3 Access Points do not
support conditional writes (If-None-Match), so they are not used as the write
target for transactional table formats. Firehose emits Parquet as new objects
rather than appends, which is consistent with this constraint on the retention
path.

Data operations against an AD-joined SVM's S3 Access Points require AD domain
controller reachability for every operation. A successful HeadBucket is not
evidence of that reachability.

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, single AZ), based on published
unit prices and not measured in this environment. This path reuses an existing
implementation; this pattern creates and deletes no new resources.

| Driver | Note |
|---|---|
| FSx for ONTAP file system | Billed continuously while running. Reused as a shared foundation; not created or deleted by this record |
| Firehose / S3 / Glue / Athena | Usage-based, proportional to ingested/stored/scanned volume. Follows the cost model in `integrations/lakehouse-retention/` |
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). Not needed when using an S3 Gateway Endpoint |

## Ephemeral Configuration

This pattern stands up no new resources, so it has no dedicated ephemeral
configuration. If you temporarily stand up the reused `integrations/lakehouse-retention/`
for verification, remove it with that integration's teardown procedure.

## Day 2 Operations

This pattern stands up no new resources, so it has no Day 2 operations of its own.
For operating the existing E2E-verified path in production, verification and
monitoring follow the `integrations/lakehouse-retention/` documentation. Use that
operations guide as the reference point for monitoring and alarms across the
ingestion/retention/analytics stages (Firehose, S3, Glue, Athena/Snowflake).

## Rollback and Cleanup

This pattern creates no new resources, so it has no rollback or cleanup of its own.
If you temporarily stand up the existing path and a deploy fails, recovery and
removal follow the teardown and rollback procedures in
`integrations/lakehouse-retention/`.

## ONTAP Version Requirements

The reused path touches FSx for ONTAP only through writing to S3 Access Points and
works on standard S3-capable versions. It carries no version-specific prerequisite
such as the silly-rename fix. Confirm only that your FSx for ONTAP runs a version
that supports S3 Access Points.

## Scope Boundary

This record covers a verification environment and a verification record, not a
production-grade deployment. Multi-region HA for the analytics engines is out of
scope. Load and scale benchmarks beyond a single sample run are out of scope
unless explicitly pursued. Rebuilding the existing path is also out of scope
(reuse and recording only).

## Related Documents

- [Pattern 3 overview (storage consolidation patterns)](../../../../docs/en/observability-storage-patterns/pattern-3-managed-iot-timestream.md)
- [Verification record: Pattern 3](../../../../docs/en/observability-storage-patterns/verification/verification-results-pattern-3.md)
- [Integration entry point](../../README.md)
