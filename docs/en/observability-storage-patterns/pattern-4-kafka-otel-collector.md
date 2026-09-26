# Pattern 4: Kafka + OTel Collector + TSDB

🌐 [日本語](../../ja/observability-storage-patterns/pattern-4-kafka-otel-collector.md) | **English** (this page)

⬅️ [Back to overview](README.md)

## Typical Stack

A Kafka cluster ingests high-volume telemetry, an OpenTelemetry Collector (or a
Kafka-native consumer) transforms and routes it, and a time-series database or search
engine stores it for querying via Grafana or a similar tool.

**Public reference**: [Apache Kafka's own file-system design documentation](https://kafka.apache.org/090/design/design/)
describes the broker's reliance on local disk for its log-segment files.

## Why This Pattern Is the Exception, Not the Rule

Kafka is the one pattern in this document's five where "the hot path requires local disk"
is not an absolute constraint, and it is worth stating precisely why, because the reasons
for the other four patterns (Prometheus, InfluxDB, SQLite, Timestream) do not carry over.

**The historical problem**: running a Kafka broker's log directory on NFS caused crashes
during partition reassignment or cluster resizing, due to what's called the "silly rename"
issue. NFS does not allow unlinking a file that still has open references; the NFS client
works around this by renaming the file to a special temporary name and deleting it only on
last close. Kafka's rebalance workflow deletes files that still have open references,
which triggered this workaround — and, before the fix described below, could crash the
broker. [Multiple independent reports confirm this was a real, reproducible problem](https://stackoverflow.com/questions/60900481/kafka-doesnt-work-with-external-nfs-volume),
not a theoretical one.

**NetApp's fix**: NetApp's own blog post, ["ONTAP is ready for streaming applications" (2023)](https://www.netapp.com/blog/ontap-ready-for-streaming-applications/),
describes implementing the NFSv4.x "delete on last close" feature on both the ONTAP NFS
server side and the Linux NFS client side, and contributing the client-side changes
upstream. The blog states the client-side fix became generally available in **RHEL 8.7 and
RHEL 9.1**. On the ONTAP server side, [a corresponding GitHub issue on NetApp's Trident
project](https://github.com/NetApp/trident/issues/808) documents a new ONTAP volume
setting, `-is-preserve-unlink-enabled`, introduced in **ONTAP 9.12.1**, which must be set
to `true` to enable this behavior on the volume.

**NetApp's own functional validation** (not this document's testing): [a NetApp solutions
document dated 2025-09-15](https://docs.netapp.com/us-en/netapp-solutions/data-analytics/kafka-nfs-functional-validation-silly-rename-fix.html)
describes running two parallel Confluent Platform 7.2.1 Kafka clusters on AWS — one on a
generic NFSv3 server, one on a **NetApp Cloud Volumes ONTAP** instance running **ONTAP
9.12.1** mounted as NFSv4.1 with the fix enabled — and triggering partition reassignment on
both. The result, as reported by NetApp: the NFSv3 cluster crashed with the silly-rename
failure; the ONTAP NFSv4.1 cluster with the fix completed the reassignment without
disruption. [NetApp's Confluent-branded companion post](https://www.netapp.com/pt/blog/simplify-apache-kafka-confluent/)
adds operational claims beyond the crash fix: faster broker recovery (data lives on shared
storage, so a restarted broker doesn't need to rebuild from replicas) and reduced broker
compute requirements (data processing is offloaded to the storage system).

## What Is and Isn't Confirmed for FSx for ONTAP Specifically

All of the validation described above — the functional test, the crash-vs-no-crash
comparison, the specific ONTAP version (9.12.1) and volume setting
(`-is-preserve-unlink-enabled`) — was performed by NetApp against **NetApp Cloud Volumes
ONTAP**, not Amazon FSx for NetApp ONTAP directly. That test result is not, by itself,
evidence for FSx for ONTAP.

**FSx for ONTAP's software version**: this document's author confirmed directly with the
AWS service team that FSx for ONTAP file systems run **ONTAP 9.18.1 or later** (as of
2026-09-26; ONTAP itself has released up to 9.19.1 as of the same date). This is a
**direct field confirmation, not a publicly published version number** — AWS does not
state the running ONTAP software version in the FSx for ONTAP documentation checked for
this file, so readers cannot verify this independently from public documentation alone.
Because 9.18.1 is well past the 9.12.1 release that introduced
`-is-preserve-unlink-enabled`, and ONTAP does not remove volume-level settings introduced
in an earlier release when it isn't specifically documented as deprecated, this document
treats **the Kafka-over-NFS silly-rename fix as available on FSx for ONTAP's underlying
ONTAP version** — a change from this document's earlier draft, which could not confirm
the version and stated the fix as unconfirmed for FSx for ONTAP.

**What remains genuinely unconfirmed**: [AWS's own documentation for updating FSx for
ONTAP volumes](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/updating-volumes.html)
confirms that FSx for ONTAP volumes are reachable via "the NetApp ONTAP command line
interface (CLI) and REST API" **in addition to** the FSx console/CLI/API — so the
mechanism to set an ONTAP-native volume attribute like `-is-preserve-unlink-enabled`
exists in principle. This document did not find a specific FSx for ONTAP walkthrough
setting that exact attribute, so treat "the ONTAP version supports the fix" (confirmed via
field observation, above) as distinct from "a specific FSx for ONTAP volume has been
configured with the fix and validated end-to-end" (not confirmed for this document — no
functional test like NetApp's NFSv3-vs-NFSv4.1 partition-reassignment comparison has been
run against FSx for ONTAP specifically that this document could find).

## A Second, Independent Public Case: AutoMQ's Diskless Kafka on FSx for ONTAP

[An AWS Storage blog post (published 2026)](https://aws.amazon.com/blogs/storage/achieving-sub-10ms-latency-and-94-cost-savings-with-diskless-kafka-using-automq-and-amazon-fsx-for-netapp-ontap/)
describes a materially different — and directly relevant — use of FSx for ONTAP with
Kafka: AutoMQ, an S3-backed "Diskless Kafka" implementation, uses FSx for ONTAP as a
**Multi-AZ shared write-ahead log (WAL) layer** in front of S3, rather than as the primary
log-segment storage the silly-rename fix addresses. This is not the same scenario as the
NFS/silly-rename problem above — it is a distinct, and independently confirmed, way FSx for
ONTAP and Kafka combine.

**What AWS measured** (their benchmark, not this document's): 3x m7g.4xlarge brokers, FSx
for ONTAP Generation 2 in Multi-AZ mode (1,024 GiB capacity, 3,072 provisioned IOPS, 736
MBps throughput), us-east-1, 4:1 read/write ratio at 64 KB message size, sustaining 300
MBps writes and 1.2 GiBps reads. Result: average write latency of **5.98 ms** (P99 12.87
ms), average end-to-end latency of **7.79 ms** (P99 18.04 ms) — approaching local-disk
Kafka performance while keeping S3 as the durable long-term tier. AWS also reports a cost
comparison (traditional three-replica Multi-AZ Kafka at roughly $317,000/month vs. AutoMQ
BYOC with FSx for ONTAP at roughly $18,345/month for the same P99 write-latency target),
which this document cites as AWS's own published figure, not an independently reproduced
measurement.

This is meaningfully stronger evidence than the silly-rename fix for "Kafka and FSx for
ONTAP work together in production," because it is AWS's own published benchmark against
FSx for ONTAP directly, not an inference from a Cloud Volumes ONTAP test. It does not,
however, confirm the silly-rename fix specifically — AutoMQ's WAL is described as "a
fixed-size circular buffer," a usage pattern that may not exercise the partition-rebalance
delete-while-open code path the silly-rename issue depends on.

## Where FSx for ONTAP Fits

| FSx for ONTAP pattern | Applies? | Notes |
|---|:---:|---|
| [Pattern A: Archive](README.md#pattern-a-long-term-archive-via-s3-access-points) | ✅ | Applies to a downstream export — a Kafka Connect S3 sink's output, or the TSDB's own cold-tier export — not to the broker's log directory itself |
| [Pattern B: FlexClone](README.md#pattern-b-devtest-acceleration-via-flexclone) | ✅ | Clone the Pattern A archive to test collector/consumer logic against realistic data |
| [Pattern C: Snapshot/SnapLock](README.md#pattern-c-forensic-protection-via-snapshot--snaplock) | ✅ | Protects the archived stream export a post-incident investigation depends on |

## Pattern-Specific Notes

A reader wanting to place the Kafka broker's own log directory on FSx for ONTAP NFS
(rather than only using Patterns A/B/C for a downstream export) can treat the ONTAP
version requirement for the silly-rename fix as met (confirmed via field observation,
above), but should still validate the specific volume configuration
(`-is-preserve-unlink-enabled`) end-to-end before depending on it in production — that
configuration step has not been independently confirmed against FSx for ONTAP by this
document. For a WAL-style usage instead of primary log storage, the AutoMQ case above is
directly confirmed by AWS.

## Related Documents

- [Overview: Consolidating Observability Storage on FSx for ONTAP](README.md)
- [On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md)
- [S3 AP Specification & Constraints](../s3ap-fsxn-specification.md)
