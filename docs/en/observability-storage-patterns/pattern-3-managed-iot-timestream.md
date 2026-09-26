# Pattern 3: Managed IoT → Timestream

🌐 [日本語](../../ja/observability-storage-patterns/pattern-3-managed-iot-timestream.md) | **English** (this page)

⬅️ [Back to overview](README.md)

## Typical Stack

AWS IoT Core receives device telemetry, an IoT rule routes it (directly or via Kinesis
Data Streams/Firehose) into Amazon Timestream for near-real-time querying, and optionally
into S3 for longer-term retention. Grafana or Amazon Managed Grafana queries Timestream for
dashboards; Lambda can perform real-time anomaly detection on the same stream.

**Public references**: [AWS reference architecture: patterns for IoT time-series data
ingestion with Timestream](https://aws.amazon.com/blogs/database/patterns-for-aws-iot-time-series-data-ingestion-with-amazon-timestream/);
[AWS smart-home IoT guidance](https://docs.aws.amazon.com/solutions/building-smart-home-solutions-on-aws-iot/),
which explicitly describes routing to Timestream for near-real-time monitoring and to S3
(as an Iceberg table) for long-term retention via Firehose buffering.

> **Service lifecycle note**: **Amazon Timestream for LiveAnalytics closed new-customer
> access effective 2025-06-20** ([source](https://docs.aws.amazon.com/timestream/latest/developerguide/AmazonTimestreamForLiveAnalytics-availability-change.html)).
> Existing customer workloads continue, but new builds cannot choose it. AWS directs new
> customers to Amazon Timestream for InfluxDB as the recommended alternative. This
> pattern's title uses "Timestream" as a conventional label; readers building new should
> evaluate Timestream for InfluxDB, or a self-managed time-series database covered in
> [Pattern 1](pattern-1-mqtt-tsdb-live-dashboard.md) or [Pattern 5](pattern-5-embedded-columnar-tsdb.md).

## Why This Pattern Is Different From the Other Four

Amazon IoT Core and Amazon Timestream are fully managed AWS services. There is no compute
host running a broker or database process, and no attached block or file volume for FSx
for ONTAP to connect to anywhere in the hot path. This is not a protocol constraint like
the other four patterns — it's the absence of a volume to consolidate in the first place.

## Where FSx for ONTAP Fits

| FSx for ONTAP pattern | Applies? | Notes |
|---|:---:|---|
| [Pattern A: Archive](README.md#pattern-a-long-term-archive-via-s3-access-points) | ⚠️ Conditional | Only if the pipeline *also* lands a raw copy in a self-managed store somewhere (e.g., a Lambda consumer of the IoT rule, or a Kinesis Firehose transform Lambda). If the pipeline already routes to S3 natively — as [AWS's own smart-home guidance](https://docs.aws.amazon.com/solutions/building-smart-home-solutions-on-aws-iot/) does — FSx for ONTAP's incremental value over that existing S3 path is smaller |
| Pattern B: FlexClone | N/A | No volume exists to clone; Timestream and IoT Core manage their own storage internally |
| Pattern C: Snapshot/SnapLock | N/A | Same reason — no FSx for ONTAP volume is in this pipeline's hot path to protect |

## Pattern-Specific Notes

This pattern is flagged in the [overview](README.md) as the one case where this
document's general recommendation ("FSx for ONTAP adds an archive/clone/protection layer")
does not meaningfully apply beyond an optional archive of a self-managed export. Readers
running a fully managed IoT-to-Timestream pipeline should not expect FSx for ONTAP to have
a larger role here than that — this document does not overstate applicability to make the
pattern coverage look more uniform than it is.

If the pipeline evolves to include a self-managed component (e.g., a Kafka or MQTT layer
added alongside IoT Core), re-evaluate against [Pattern 1](pattern-1-mqtt-tsdb-live-dashboard.md)
or [Pattern 4](pattern-4-kafka-otel-collector.md) instead, whichever matches the added
component.

## Related Project: ONTAP Edge-to-Cloud AI

[ontap-edge-to-cloud-ai](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai) is a
reference implementation connecting edge/IoT devices to AWS analytics and AI services
(Athena, Glue, SageMaker, Bedrock) with FSx for ONTAP as the storage layer. Two points
connect directly to this pattern:

- **A matching implementation exists as deployable code.** That project's
  [`cloud/iot_ingestion/`](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/cloud/iot_ingestion/template.yaml)
  implements the AWS IoT Core → Lambda ingestion path described here, but writes directly
  to an **FSx for ONTAP S3 Access Point** instead of Timestream. This is a concrete example
  of this pattern's conditional Pattern A applicability — an IoT rule's Lambda consumer
  also landing data in a self-managed store, here FSx for ONTAP itself.
- **The Timestream for LiveAnalytics new-customer closure noted above is independently
  confirmed by that project's [Pattern 07: Digital Twin](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/docs/en/aws-patterns/07-digital-twin.md)**,
  which compares new-build time-series database options (Timestream for InfluxDB,
  streaming + object storage + Iceberg, or a columnar database). Where this pattern refers
  generically to a "time-series store," readers building new should consult that
  comparison.

Project-specific design decisions (device identifier validation, S3 Access Point
compatibility constraints, local-demo verification steps) live in
[ontap-edge-to-cloud-ai's README](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/README_en.md)
and [S3 AP Compatibility Matrix](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/docs/en/s3ap-compatibility-matrix.md) —
this document does not duplicate them.

## Related Documents

- [Overview: Consolidating Observability Storage on FSx for ONTAP](README.md)
- [On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md)
- [ontap-edge-to-cloud-ai](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai) — edge/IoT
  data aggregation onto FSx for ONTAP; [`cloud/iot_ingestion/`](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/cloud/iot_ingestion/)
  is a working implementation of this pattern's IoT Core ingestion path
- [Pattern 07: Digital Twin (ontap-edge-to-cloud-ai)](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/docs/en/aws-patterns/07-digital-twin.md) —
  Timestream for LiveAnalytics's new-customer closure and the new-build time-series
  database comparison
