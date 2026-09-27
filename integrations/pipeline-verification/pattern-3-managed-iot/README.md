# Pattern 3: managed IoT → Timestream/S3 (reuse only)

Net-new: none. The E2E-verified path is not rebuilt.
Reused: `integrations/lakehouse-retention/` (Firehose → S3 Parquet → Glue →
Athena/Snowflake, E2E verified) and
`ontap-edge-to-cloud-ai:cloud/iot_ingestion/` (IoT Core → Lambda → FSx for ONTAP
S3 Access Point).

Stage 0 delivers only the scaffolding. This pattern has no `template.yaml`; the
later reuse-only tasks (spec tasks 20.x) add the doc pointers and a verification
record capturing the existing verified result.
