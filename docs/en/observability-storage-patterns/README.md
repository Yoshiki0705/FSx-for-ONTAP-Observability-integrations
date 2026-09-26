# Consolidating Observability Storage on FSx for ONTAP

🌐 [日本語](../../ja/observability-storage-patterns/README.md) | **English** (this page)

## Executive Summary

Real-time telemetry pipelines — MQTT/IoT brokers feeding a time-series database and a
live dashboard, Prometheus-style scrape-and-alert stacks, managed IoT-to-Timestream
pipelines, and Kafka/OTel-based streaming stacks — all share a hot ingestion path that
public documentation for every one of the underlying tools describes as requiring local
block storage, not a network file share (with one documented exception — see
[Pattern 4](pattern-4-kafka-otel-collector.md)). FSx for ONTAP does not replace that hot
path in most of these architectures. What it adds is a **shared storage layer around the
hot path**: a long-term archive that multiple teams can read concurrently without copying
files, instant zero-copy clones of production telemetry for development and testing, and
immutable snapshots for post-incident forensics. This document separates what is verified
from what is a documented AWS/NetApp capability applied to a new context, and states
plainly which claims are unverified reasoning (hypothesis) rather than measurement.

**This document does not evaluate any specific company's or individual's implementation.**
Every real-world pipeline cited below or in the linked pattern files illustrates a
publicly documented pattern; the storage-consolidation options here are a general proposal
for readers who operate a similarly shaped pipeline, not a critique or endorsement of any
cited implementation.

## How This Is Organized

This topic outgrew a single file. It is now split so each pipeline pattern's specifics
live next to each other, and public case studies (including non-AWS ONTAP deployments)
have their own home instead of being folded into the AWS-specific reasoning:

| File | Contents |
|---|---|
| **README.md** (this page) | Cross-pattern findings, the three FSx for ONTAP patterns (defined once), what's excluded and why, the iSCSI hypothesis, decision flowchart, FAQ |
| [Pattern 1: MQTT → TSDB → Live Dashboard](pattern-1-mqtt-tsdb-live-dashboard.md) | MQTT broker/decoder/Telegraf/Grafana Live-shaped pipelines |
| [Pattern 2: Prometheus + Remote-Write](pattern-2-prometheus-remote-write.md) | Scrape-based metrics with Thanos/Mimir/Cortex long-term storage |
| [Pattern 3: Managed IoT → Timestream](pattern-3-managed-iot-timestream.md) | AWS IoT Core / Kinesis / Timestream fully managed pipelines |
| [Pattern 4: Kafka + OTel Collector + TSDB](pattern-4-kafka-otel-collector.md) | Streaming broker + collector + time-series database |
| [Pattern 5: Embedded/Columnar TSDB](pattern-5-embedded-columnar-tsdb.md) | QuestDB, ClickHouse, TimescaleDB with tiered cold storage |
| [On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md) | Public evidence for NetApp ONTAP (on-premises AFF/FAS, Azure NetApp Files, Google Cloud NetApp Volumes, Cloud Volumes ONTAP), not limited to FSx for ONTAP |

## Five Real-Time Telemetry Pipeline Patterns

Public documentation and case studies converge on a small number of pipeline shapes for
real-time telemetry. This document uses these five as coverage for "real-time telemetry
pipeline," not as an exhaustive list:

| # | Pattern | Typical stack | Detail |
|---|---|---|---|
| 1 | MQTT broker → decoder → time-series DB → live dashboard | Mosquitto/EMQX/HiveMQ, Telegraf, InfluxDB, Grafana Live (WebSocket) | [Pattern 1](pattern-1-mqtt-tsdb-live-dashboard.md) |
| 2 | Scrape-based metrics + remote-write to long-term storage | Prometheus, remote_write, Thanos/Mimir/Cortex, object storage backend | [Pattern 2](pattern-2-prometheus-remote-write.md) |
| 3 | Managed IoT ingestion to a managed time-series store | AWS IoT Core, Kinesis Data Streams/Firehose, Amazon Timestream, S3 | [Pattern 3](pattern-3-managed-iot-timestream.md) |
| 4 | Streaming broker + collector + TSDB | Kafka, OpenTelemetry Collector, a TSDB, Grafana | [Pattern 4](pattern-4-kafka-otel-collector.md) |
| 5 | High-throughput embedded/columnar TSDB with tiered cold storage | QuestDB, ClickHouse, TimescaleDB, with S3-compatible cold-tier | [Pattern 5](pattern-5-embedded-columnar-tsdb.md) |

**The consistent finding across four of the five**: the hot ingestion/WAL path is
documented by its own project as requiring local disk semantics, and the long-term/cold
storage layer converges on an S3-compatible object store. This is not a conclusion drawn
from pattern 1 alone — the same split shows up independently across message brokers
(Kafka, with one documented NFS exception), metrics systems (Prometheus), and
purpose-built time-series engines (QuestDB, ClickHouse). The FSx for ONTAP consolidation
options below apply this cross-pattern finding rather than one implementation's specifics.

## What Changes and What Doesn't

The framing "replace EBS with FSx for ONTAP" is inaccurate for pipelines shaped like these,
and this document does not use it. EBS is a block volume attached to one instance; FSx for
ONTAP is shared storage reachable concurrently from multiple compute types over NFS, SMB,
iSCSI, or S3 Access Points. The accurate framing is **adding a shared storage layer**, not
replacing the hot-path storage compute already uses.

| Pipeline layer (across all five patterns) | Recommendation | Why |
|---|---|---|
| Message broker / ingestion (MQTT broker, Kafka, IoT Core) | No change (Kafka has a documented NFS exception — see [Pattern 4](pattern-4-kafka-otel-collector.md)) | Local or managed broker state; not a shared-storage problem in most cases |
| Live/streaming delivery (WebSocket push, dashboard scrape) | No change | See [Excluded: The Real-Time Path](#excluded-the-real-time-path) |
| Hot time-series storage (InfluxDB, Prometheus TSDB, QuestDB, ClickHouse, Timestream) | No change | Every engine's own documentation requires local disk or is fully managed; see [Excluded: The Real-Time Path](#excluded-the-real-time-path) |
| Raw telemetry after hot-storage retention expiry | **Candidate**: archive via S3 Access Points | [Pattern A](#pattern-a-long-term-archive-via-s3-access-points) |
| Pipeline logic development and testing (decoders, transforms, alert rules) | **Candidate**: FlexClone of production data | [Pattern B](#pattern-b-devtest-acceleration-via-flexclone) |
| Post-incident root-cause data | **Candidate**: Snapshot / SnapLock | [Pattern C](#pattern-c-forensic-protection-via-snapshot--snaplock) |
| A dashboard/metadata store built on SQLite (e.g., Grafana's own store) | **Hypothesis, unverified** | [SQLite/InfluxDB over iSCSI](#sqliteinfluxdb-over-iscsi-a-hypothesis-not-a-verified-pattern) |

## FSx for ONTAP Patterns Across Pipeline Types

Rather than repeating the same archive/clone/protection reasoning once per pipeline
pattern, the matrix below states which of the three FSx for ONTAP patterns applies to each
of the five pipeline types. Per-pattern specifics and caveats live in each pattern's own
file.

| Pipeline pattern | Pattern A: Archive | Pattern B: FlexClone | Pattern C: Snapshot/SnapLock | Detail |
|---|:---:|:---:|:---:|---|
| 1. MQTT → TSDB → live dashboard | ✅ | ✅ | ✅ | [Pattern 1](pattern-1-mqtt-tsdb-live-dashboard.md) |
| 2. Prometheus + remote-write | ✅ | ✅ | ✅ | [Pattern 2](pattern-2-prometheus-remote-write.md) |
| 3. Managed IoT → Timestream | ⚠️ | N/A | N/A | [Pattern 3](pattern-3-managed-iot-timestream.md) |
| 4. Kafka + OTel Collector + TSDB | ✅ | ✅ | ✅ | [Pattern 4](pattern-4-kafka-otel-collector.md) |
| 5. QuestDB/ClickHouse/TimescaleDB | ✅ | ✅ | ✅ | [Pattern 5](pattern-5-embedded-columnar-tsdb.md) |

**Reading the matrix**: Patterns A, B, and C are defined once, below, and apply with the
same reasoning regardless of which pipeline pattern produced the raw telemetry. Pattern 3
(fully managed) is the one case where FSx for ONTAP adds little beyond what the managed
service already provides — flagged here so this document does not overstate applicability.

## Pattern A: Long-Term Archive via S3 Access Points

**Problem**: Every hot storage engine in the five patterns above enforces some form of
retention or is cost-prohibitive to keep everything in forever. If root-cause analysis,
compliance, or a team without hot-store query access needs data older than that window, or
needs to share raw data without each team keeping its own copy, the data is gone once the
hot store's retention expires it (or exists only as a fragmented per-team export).

**Pattern**: Wherever the pipeline already has an ingestion Lambda, consumer, or export job
writing into the hot store, the same payload can also land on an FSx for ONTAP volume
through an [S3 Access Point](../s3ap-fsxn-specification.md), using the same
[Lambda file-processing pattern AWS documents](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/tutorial-process-files-with-lambda.html)
for reading and writing files via the S3 API against an NFS/SMB-backed volume. Once
archived, the data is reachable concurrently over NFS/SMB by any team — no per-team copy,
no second data store to keep in sync.

**Constraint that matters here**: FSx for ONTAP S3 Access Points do not support
conditional writes (`If-None-Match`), documented in
[s3ap-fsxn-specification.md](../s3ap-fsxn-specification.md#8-fsx-for-ontap-s3-access-points--constraints--validated-patterns).
This rules out transactional table formats (Delta Lake, Iceberg, Hudi) built on
conditional-PUT semantics for this archive — it is a write-once-per-object append/archive
target, not a transactional data lake. For raw JSON/binary telemetry payloads, this
constraint does not block the pattern; it would block building a transactional table
format directly on top of the archived files without an intermediate step (e.g., DataSync
to native S3, or a batch ETL job) that AWS documents as the validated workaround.

## Pattern B: Dev/Test Acceleration via FlexClone

**Problem**: Testing changes to decoder, transform, or alert-rule logic against realistic
data volumes normally means copying a production dataset — time-consuming at scale, and a
second copy to keep secure and eventually delete.

**Pattern**: [FlexClone](https://aws.amazon.com/blogs/storage/accelerate-development-refresh-cycles-and-optimize-cost-with-amazon-fsx-for-netapp-ontap-cloning/)
creates an instant, space-efficient, writable clone of a volume. If the raw telemetry
archive from Pattern A lives on an FSx for ONTAP volume, a clone of that volume can be
attached to a test environment in seconds rather than however long a full copy takes, and
it consumes storage only for the blocks that diverge from the source after cloning. This
is the "zero-copy" mechanism referenced in this document's scope: no file-level copy
operation (no S3 PUT/GET round trip, no `scp`) is needed to produce the clone.

## Pattern C: Forensic Protection via Snapshot / SnapLock

**Problem**: Root-cause analysis after an anomaly is a stated purpose across most of the
five pipeline patterns. If the archived telemetry backing that analysis can be modified or
deleted (accidentally, or by a compromised credential), the forensic record itself becomes
unreliable.

**Pattern**: ONTAP [Snapshot](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/using-backups.html)
provides point-in-time, read-only copies of a volume. Where a compliance or governance
requirement calls for immutability rather than just point-in-time recovery, [SnapLock](../governance-and-compliance.md)
enforces write-once-read-many (WORM) retention on top of that. This repository's existing
[Automated Response Guide](../automated-response-guide.md) and
[ARP Incident Response Guide](../arp-incident-response-guide.md) already cover the
automated response and ransomware-protection mechanics for FSx for ONTAP volumes; this
document does not repeat that content — it adds only the telemetry-archive-specific
framing: apply the same snapshot/SnapLock protection to the Pattern A archive volume, so
the data root-cause analysis depends on cannot be silently altered.

> **Irreversibility note**: SnapLock retention locks are not always reversible before
> their retention period expires, depending on the lock mode chosen (Compliance mode is
> not reversible by design). Before applying SnapLock to a volume, confirm the retention
> period, the blast radius (which volume, which data), and the cost of that data being
> undeletable for the chosen duration. See [Governance and Compliance](../governance-and-compliance.md)
> for the compliance-mode vs. enterprise-mode distinction — this is not a decision to make
> without reading that distinction first.

## Excluded: The Real-Time Path

This document deliberately excludes the live/streaming delivery layer and every hot
storage engine listed in the matrix above from FSx for ONTAP consolidation, with one
documented exception (Kafka over NFS, see [Pattern 4](pattern-4-kafka-otel-collector.md)).
The reasons are protocol and architecture constraints documented by each project
independently, not a preference:

- **Amazon ECS Fargate has no native FSx for ONTAP mount.** [AWS documents FSx for ONTAP
  mounting for ECS on EC2 launch type only](https://docs.aws.amazon.com/us_en/fsx/latest/ONTAPGuide/mount-ontap-ecs-containers.html) —
  mount the NFS/SMB share on the EC2 host, then bind-mount that host path into the
  container. Fargate task definitions [support only bind-mount host volumes](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html)
  and have no host to pre-mount a share on. Any pipeline running its decoder/collector/TSDB
  layer on Fargate cannot mount FSx for ONTAP directly there; ECS-on-EC2 can. This is a
  real fork readers need to check for their own deployment.
- **Prometheus explicitly documents NFS/EFS as unsupported for its local TSDB.** See
  [Pattern 2](pattern-2-prometheus-remote-write.md) for the specific citation.
- **Apache Kafka's broker design assumed local disk for log segments — until a 2023 NetApp
  fix changed that for NFSv4.x.** See [Pattern 4](pattern-4-kafka-otel-collector.md) for
  the full detail; this is the one pattern where "hot path stays local" is not an absolute
  rule.
- **InfluxDB v1/v2 (TSM storage engine) has documented NFS locking failures.** See
  [Pattern 1](pattern-1-mqtt-tsdb-live-dashboard.md).
- **InfluxDB 3, QuestDB, and ClickHouse converge on the same object-store answer for cold
  data, not NFS/SMB.** See [Pattern 1](pattern-1-mqtt-tsdb-live-dashboard.md) and
  [Pattern 5](pattern-5-embedded-columnar-tsdb.md).
- **A managed pipeline (Pattern 3) has no volume to mount at all.** Amazon Timestream and
  AWS IoT Core are fully managed; there is no compute host and no attached storage for FSx
  for ONTAP to connect to in the hot path.
- **A SQLite-backed dashboard metadata store is documented as unsafe over NFS.** See the
  next section for the iSCSI-specific hypothesis this document offers instead of NFS.

## SQLite/InfluxDB over iSCSI: A Hypothesis, Not a Verified Pattern

**This section is reasoning from documented properties, not a measured result. No
benchmark or read/write/locking test was run for this document.** Readers who need a
verified answer should run their own test before depending on this pattern in production.

FSx for ONTAP supports the [iSCSI protocol](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/mount-iscsi-luns-linux.html),
which presents a LUN as a raw block device that the client formats with a local filesystem
(ext4, xfs, NTFS) and mounts like a local disk — distinct from NFS/SMB, which are
network *file-sharing* protocols. SQLite's own documentation on
[file locking and concurrency](https://sqlite.org/lockingv3.html) describes its locking
model as relying on OS-level file locks, and multiple independent reports
([Sonarr/Sonarr#1886](https://github.com/Sonarr/Sonarr/issues/1886),
[Grafana Community forum](https://community.grafana.com/t/two-grafana-servers-with-single-sqlite3-databse-on-nfs/2734))
describe SQLite corruption or lock failures specifically on NFS and CIFS network shares —
not on local block devices. The reasoning this document offers, stated as a hypothesis:
**an iSCSI LUN, once formatted and mounted, presents to the OS and to SQLite as a local
disk, not a network file share — so the class of locking failure documented for NFS/CIFS
does not apply to the same degree.** This has not been measured for this document.

The same reasoning extends to InfluxDB v1/v2, whose TSM engine expects a local
filesystem: an iSCSI-backed local filesystem is a plausible fit where NFS is documented as
unsafe. InfluxDB 3, QuestDB's object-store cold tier, and ClickHouse's S3-backed external
disks are excluded from this hypothesis — iSCSI's block interface does not satisfy any of
their object-store dependencies at all, regardless of the locking question.

**What is verified, and what is not, for this specific claim:**

| Claim | Status | Source |
|---|---|---|
| SQLite locking is unreliable over NFS/CIFS | Verified (documented) | [SQLite docs](https://sqlite.org/lockingv3.html), [Grafana Community](https://community.grafana.com/t/two-grafana-servers-with-single-sqlite3-databse-on-nfs/2734) |
| FSx for ONTAP iSCSI presents as a raw block device | Verified (documented) | [AWS iSCSI provisioning docs](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/mount-iscsi-luns-linux.html) |
| SQLite/InfluxDB v1v2 on FSx for ONTAP iSCSI avoids the NFS locking failure class | **Hypothesis — not measured for this document** | Reasoning only |
| iSCSI outperforms SMB for a database workload on FSx for ONTAP | Verified (measured, different workload) | [AWS blog, SQL Server/HammerDB, r5dn.24xlarge, Multi-AZ, 80,000 IOPS/2GB/s, published 2025-05](https://aws.amazon.com/blogs/modernizing-with-aws/optimizing-protocol-selection-when-using-amazon-fsx-for-netapp-ontap-for-microsoft-sql-server/) — cited as directional evidence that FSx for ONTAP's iSCSI implementation performs adequately for OLTP-shaped I/O, not as a SQLite or InfluxDB measurement |
| A public case combining SQLite and FSx for ONTAP exists | Verified — but protocol not confirmed | See [On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md#infor-cloverleaf--fsx-for-ontap-via-netapp-trident) |

**Constraint shared with the real-time path exclusion**: ECS Fargate cannot mount an iSCSI
LUN either — iSCSI initiator configuration happens on the host OS, which Fargate does not
expose. This pattern, like the FSx for ONTAP NFS/SMB mount, requires ECS on EC2 launch
type (or a plain EC2 instance running the dashboard/database directly).

## Kubernetes Path (EKS/Trident): A Reference Note

Any of the five pipeline patterns above can run on Amazon EKS instead of ECS or EC2; this
section is informational only and does not change the recommendations above.

[NetApp Trident](https://docs.netapp.com/us-en/trident/trident-use/trident-fsx.html)
provisions FSx for ONTAP-backed persistent volumes for Amazon EKS, using either
`ontap-nas` (NFS) or `ontap-san` (iSCSI) backend drivers depending on configuration. See
[On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md#infor-cloverleaf--fsx-for-ontap-via-netapp-trident)
for a public production example of SQLite and RaimaDB running through Trident. This is
presented there as **public evidence that the combination is deployed in production
somewhere** — not as a recommendation to migrate an ECS or EC2-based pipeline to EKS.

If a reader's pipeline already runs on EKS rather than ECS/EC2, Trident is the integration
point to evaluate rather than the ECS bind-mount path described above; this document does
not go further into Trident configuration, as that is a distinct compute platform from the
ECS/EC2-based patterns this document is scoped to.

## Decision Flowchart

```
┌───────────────────────────────────────────┐
│ What are you trying to store on           │
│ FSx for ONTAP?                            │
└──────────────────┬────────────────────────┘
                    │
   ┌────────────────┼─────────────────┬──────────────────────┐
   ▼                ▼                 ▼                      ▼
Raw telemetry   Dev/test copy    Forensic/compliance   A SQLite-backed
after hot-store  of production   record of past         dashboard store, or
retention expiry data            incidents              a hot TSDB/broker
   │                │                 │                      │
   ▼                ▼                 ▼                      ▼
Pattern A:       Pattern B:        Pattern C:          Are you on ECS
S3 Access        FlexClone         Snapshot /          Fargate, or is
Points archive                     SnapLock            this a fully
   │                                                    managed pipeline
   ▼                                                    (pattern 3)?
Using Delta Lake /                              ┌────────┴────────┐
Iceberg / Hudi on                               │ Yes              │ No
top of the archive?                             ▼                  ▼
┌────┴────┐                              Not supported /      Hypothesis
│ Yes      │ No                          not applicable —     section above
▼          ▼                            use ECS on EC2 or     applies (iSCSI,
Add DataSync  Direct S3 API              plain EC2            unverified) —
→ native S3   read/write via                                  unless it's
step first    Lambda (Pattern A)                              Kafka on NFS
                                                                (Pattern 4)
```

## FAQ

**Q: Does this mean FSx for ONTAP replaces EBS for these kinds of pipelines?**
A: No. EBS (or the managed service's own storage) remains attached to the compute running
the hot ingestion, hot storage, and live-delivery layers. FSx for ONTAP adds a shared
archive/clone/protection layer around that hot path — see
[What Changes and What Doesn't](#what-changes-and-what-doesnt).

**Q: Why does this document cover five pipeline patterns instead of one?**
A: A recommendation built from a single implementation's specifics risks being an artifact
of that one architecture rather than a general property of real-time telemetry pipelines.
Cross-checking against Prometheus, Kafka, managed IoT pipelines, and purpose-built TSDBs
independently confirmed the same hot-local/cold-object-store split (with one documented
exception) — see [Five Real-Time Telemetry Pipeline Patterns](#five-real-time-telemetry-pipeline-patterns).

**Q: Can Lambda write directly into a live/streaming delivery path (e.g., a WebSocket push)?**
A: No connection is proposed here between Lambda/S3 Access Points and any live delivery
path. The S3 Access Points pattern in this document (Pattern A) applies to the archive
path (wherever an ingestion or export job already writes into hot storage), not to
real-time delivery to a browser or client.

**Q: Is the "zero-copy" claim about FlexClone or about S3 Access Points?**
A: FlexClone (Pattern B) is the zero-copy mechanism — an instant, space-efficient volume
clone with no file-level copy operation. S3 Access Points (Pattern A) eliminate a
different kind of copying: once data is on a shared volume, multiple teams read it over
NFS/SMB/S3 without each team maintaining its own copy. Both are described in this document
as "zero-copy" in the sense that no file-level duplication step (PUT/GET, `scp`) occurs,
but they are two distinct ONTAP mechanisms addressing two distinct problems.

**Q: Is Kafka really an exception to "hot path stays local"?**
A: Yes, documented by NetApp itself, not by this document's own testing. See
[Pattern 4](pattern-4-kafka-otel-collector.md) for the fix, the ONTAP version it requires,
a field-confirmed answer on FSx for ONTAP's own ONTAP version, and a second, independently
AWS-confirmed Kafka-on-FSx-for-ONTAP case (AutoMQ's WAL usage).

**Q: Has anyone actually run SQLite or InfluxDB on FSx for ONTAP iSCSI and confirmed it
works?**
A: Not to a verified standard for this document. See
[On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md) for what
is and isn't confirmed. The locking-safety reasoning in this document is a hypothesis
derived from SQLite's own documentation, not a reproduced test. Run your own
read/write/locking validation before depending on this in production.

**Q: Why does InfluxDB 3 (and QuestDB's/ClickHouse's cold tiers) get excluded from the
iSCSI hypothesis, when InfluxDB v1/v2 doesn't?**
A: These systems' cold/catalog paths require an S3-compatible object store with
conditional-PUT or strong consistency semantics — a completely different storage model
from a local filesystem. iSCSI presents a block device, not an object store, so it cannot
satisfy that requirement regardless of the locking question that applies to a local-disk
engine like InfluxDB v1/v2. See [Excluded: The Real-Time Path](#excluded-the-real-time-path).

## Related Documents

- [Pattern 1: MQTT → TSDB → Live Dashboard](pattern-1-mqtt-tsdb-live-dashboard.md)
- [Pattern 2: Prometheus + Remote-Write](pattern-2-prometheus-remote-write.md)
- [Pattern 3: Managed IoT → Timestream](pattern-3-managed-iot-timestream.md)
- [Pattern 4: Kafka + OTel Collector + TSDB](pattern-4-kafka-otel-collector.md)
- [Pattern 5: Embedded/Columnar TSDB](pattern-5-embedded-columnar-tsdb.md)
- [On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md)
- [S3 AP Specification & Constraints](../s3ap-fsxn-specification.md) — the conditional-write
  and other constraints Pattern A and the object-store exclusions both depend on
- [Governance and Compliance](../governance-and-compliance.md) — SnapLock compliance-mode
  vs. enterprise-mode distinction referenced in Pattern C
- [Automated Response Guide](../automated-response-guide.md) / [ARP Incident Response Guide](../arp-incident-response-guide.md) —
  existing ransomware-protection mechanics this document's Pattern C builds on rather than
  duplicates
- [Lakehouse Monitoring Patterns](../lakehouse-monitoring-patterns.md) — a related but
  distinct set of patterns for monitoring FSx for ONTAP itself in lakehouse integrations
- [Native Alternative Matrix](../native-alternative-matrix.md) — AWS-native equivalents to
  proprietary management tooling, relevant if evaluating the Kubernetes/Trident path's
  operational tooling
