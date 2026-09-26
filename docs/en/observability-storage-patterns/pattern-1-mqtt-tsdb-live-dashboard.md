# Pattern 1: MQTT → TSDB → Live Dashboard

🌐 [日本語](../../ja/observability-storage-patterns/pattern-1-mqtt-tsdb-live-dashboard.md) | **English** (this page)

⬅️ [Back to overview](README.md)

## Typical Stack

An MQTT broker (Mosquitto, EMQX, HiveMQ) receives telemetry from edge devices or
publishers, a decoder/collector (often Telegraf) normalizes it, a time-series database
(commonly InfluxDB) stores it, and a live dashboard (Grafana, often over Grafana Live's
WebSocket transport) renders it for operators watching in real time. A parallel archive
path frequently exists alongside the live path.

**Public examples**: [Hashimoto, 2026](https://speakerdeck.com/hashimoto_kei/jinkou-eisei-kaihatsu-o-sasaeru-grafana)
describes this shape for satellite telemetry visualization (MQTT broker → decoder →
Telegraf → Grafana Live, with a parallel IoT Core → Lambda → InfluxDB archive path).
[HiveMQ's MQTT+Grafana guide](https://www.hivemq.com/blog/mqtt-data-visualization-with-grafana/)
and [EMQX's IoT visualization guide](https://www.emqx.com/en/blog/building-an-iot-visualization-platform-with-emqx-tables-and-grafana)
describe the same general shape independently, for unrelated use cases. This document does
not evaluate any of these implementations — each is cited as an example of a publicly
documented pipeline shape, not a subject for critique or endorsement.

## Why the Hot Path Stays Local

- **InfluxDB v1/v2 (TSM storage engine) has a documented NFS locking failure.**
  [influxdata/influxdb#9047](https://github.com/influxdata/influxdb/issues/9047) reports
  "stale NFS file handle" errors from running the TSM data directory on NFS.
- **InfluxDB 3 (Core/Enterprise) requires an S3-compatible object store with
  conditional-PUT semantics for its catalog.** [InfluxData's own documentation](https://docs.influxdata.com/influxdb3/core/object-storage/s3/)
  states this explicitly. FSx for ONTAP S3 Access Points do not support conditional writes
  (`If-None-Match`) — see [S3 AP Specification & Constraints](../s3ap-fsxn-specification.md#8-fsx-for-ontap-s3-access-points--constraints--validated-patterns) —
  so InfluxDB 3's catalog cannot run correctly against them.
- **Grafana's own dashboard/metadata store (SQLite) is documented as unsafe over NFS.**
  [Grafana Community forum](https://community.grafana.com/t/two-grafana-servers-with-single-sqlite3-databse-on-nfs/2734):
  "NOT SAFE — A SQLite DB should not be shared from an NFS share, corruption/data
  integrity issues will ensue."
- **Amazon ECS Fargate has no native FSx for ONTAP mount**, which matters if the
  decoder/Telegraf/Grafana layer runs on ECS. See the [overview's exclusion section](README.md#excluded-the-real-time-path)
  for the AWS documentation citation. ECS on EC2 can mount FSx for ONTAP; Fargate cannot.

## Where FSx for ONTAP Fits

| FSx for ONTAP pattern | Applies? | Notes |
|---|:---:|---|
| [Pattern A: Archive](README.md#pattern-a-long-term-archive-via-s3-access-points) | ✅ | The archive-path Lambda/consumer that already writes into the hot TSDB (e.g., an IoT-Core-to-Lambda-to-InfluxDB path) can write the same raw payload to an FSx for ONTAP volume via S3 Access Points |
| [Pattern B: FlexClone](README.md#pattern-b-devtest-acceleration-via-flexclone) | ✅ | Clone the Pattern A archive volume to test decoder/Telegraf logic changes against realistic data |
| [Pattern C: Snapshot/SnapLock](README.md#pattern-c-forensic-protection-via-snapshot--snaplock) | ✅ | Protects the archive that root-cause analysis after an anomaly depends on |

## Pattern-Specific Notes

No caveats beyond the general ones in the [overview](README.md). This is the pipeline
shape the cross-pattern findings in this document were originally checked against, before
being confirmed to hold for Patterns 2 through 5 as well.

## The iSCSI Hypothesis, Applied Here

If a SQLite-backed dashboard store (Grafana's own metadata) or an InfluxDB v1/v2 instance
in this pipeline needs to move off local EBS/instance storage for operational reasons, the
[iSCSI hypothesis in the overview](README.md#sqliteinfluxdb-over-iscsi-a-hypothesis-not-a-verified-pattern)
is the relevant — but unverified — option to evaluate, not NFS. This has not been tested
for this document.

## Related Documents

- [Overview: Consolidating Observability Storage on FSx for ONTAP](README.md)
- [On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md)
- [S3 AP Specification & Constraints](../s3ap-fsxn-specification.md)
