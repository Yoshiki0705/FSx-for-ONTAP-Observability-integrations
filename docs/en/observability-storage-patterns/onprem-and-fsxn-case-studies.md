# On-Premises and Multi-Cloud ONTAP Case Studies for Observability Storage

🌐 [日本語](../../ja/observability-storage-patterns/onprem-and-fsxn-case-studies.md) | **English** (this page)

⬅️ [Back to overview](README.md)

## Scope of This Page

The rest of this document set focuses on Amazon FSx for NetApp ONTAP. NetApp ONTAP itself
runs in several other delivery models — on-premises AFF/FAS systems, Azure NetApp Files,
Google Cloud NetApp Volumes, and NetApp Cloud Volumes ONTAP — and public evidence for using
ONTAP as an observability-platform data store exists across all of them, not only on AWS.
This page collects that evidence and states plainly which delivery model each example used,
since a capability confirmed on one ONTAP delivery model is not automatically confirmed on
another (see the [Kafka case](#kafka-on-ontap-nfs--cloud-volumes-ontap-test-fsx-for-ontap-version-confirmed-separately)
below for why this distinction matters).

**This page does not evaluate any cited company's implementation.** Every example below is
a publicly documented case study or product-support statement, cited as evidence that a
capability exists somewhere, not as a recommendation to replicate a specific
architecture.

## Infor Cloverleaf — FSx for ONTAP via NetApp Trident

[NetApp's customer story](https://www.netapp.com/customers/infor/) describes Infor's
Cloverleaf healthcare data-integration platform running on Amazon EKS with FSx for ONTAP
provisioned through NetApp Trident, supporting **embedded RaimaDB and SQLite databases**.
The story reports handling up to 70 million messages/day, storage cost reduction of up to
65% from deduplication/compression, and multi-AZ availability — with a named, quoted
source (Jesse Evans, Principal Solution Architect, Infor).

**What is and isn't confirmed**: The story confirms SQLite and RaimaDB run on FSx for
ONTAP in production. It does **not** state which [NetApp Trident backend driver](https://docs.netapp.com/us-en/trident/trident-use/trident-fsx.html) —
`ontap-nas` (NFS) or `ontap-san` (iSCSI) — the deployment uses. This document's
[iSCSI hypothesis](README.md#sqliteinfluxdb-over-iscsi-a-hypothesis-not-a-verified-pattern)
cannot claim this case as supporting evidence for iSCSI specifically, only as evidence
that the SQLite-on-FSx-for-ONTAP combination is deployed in production somewhere.

## OpenSearch on NetApp ONTAP — Cross-Platform (AWS, Azure, Google Cloud, On-Premises)

[A NetApp blog post](https://www.netapp.com/blog/opensearch-on-netapp-ontap-in-the-cloud/)
explicitly addresses this document's central question for OpenSearch: "OpenSearch is a
distributed database designed to run effectively on local disks. However, many
organizations have deployed NetApp ONTAP storage in AWS, Azure, or Google Cloud." The post
walks through deploying OpenSearch (explicitly named as "search, analytics, and
observability" software) on NFS or iSCSI volumes across **Cloud Volumes ONTAP, Azure
NetApp Files, Google Cloud NetApp Volumes, and on-premises NetApp AFF systems**. FSx for
ONTAP was included in the article's benchmark, which reports a favorable performance result
for NetApp LUN (iSCSI) and NFS (FSx for ONTAP) storage volumes among the options measured.

This is the strongest single piece of public evidence found for this document set that
**both NFS and iSCSI**, across **all major ONTAP delivery models including FSx for ONTAP**,
have been benchmarked for an observability workload (OpenSearch) by NetApp itself, not
inferred from unrelated documentation. It directly supports the reasoning (not the
untested hypothesis) that network-attached ONTAP storage is a viable choice for a
document-store-shaped observability engine, distinct from the TSDB engines this document's
five patterns otherwise focus on. Sizing guidance from the same post: keep shard size
between 10–50GB, and networked storage's non-disruptive volume growth reduces the local-SSD
rebalancing operations OpenSearch would otherwise require.

## Kafka on ONTAP NFS — Cloud Volumes ONTAP Test, FSx for ONTAP Version Confirmed Separately

Covered in detail in [Pattern 4](pattern-4-kafka-otel-collector.md#why-this-pattern-is-the-exception-not-the-rule).
Summarized here because it is the clearest example in this document set of why "NetApp
ONTAP" and "FSx for ONTAP" require separate confirmation even when the underlying software
is shared: [NetApp's own functional validation](https://docs.netapp.com/us-en/netapp-solutions/data-analytics/kafka-nfs-functional-validation-silly-rename-fix.html)
of the Kafka-over-NFS "silly rename" fix was performed against **NetApp Cloud Volumes
ONTAP running ONTAP 9.12.1**, not FSx for ONTAP. Separately, this document's author
confirmed with the AWS service team that FSx for ONTAP runs **ONTAP 9.18.1 or later**
(field confirmation, 2026-09-26, not publicly documented) — well past the 9.12.1 release
that introduced the fix's underlying volume setting. See [Pattern 4](pattern-4-kafka-otel-collector.md#what-is-and-isnt-confirmed-for-fsx-for-ontap-specifically)
for what remains unconfirmed even with the version question resolved (specifically,
whether that volume setting has been configured and end-to-end validated on an actual FSx
for ONTAP volume), and for [AutoMQ's independently AWS-confirmed WAL use case](pattern-4-kafka-otel-collector.md#a-second-independent-public-case-automqs-diskless-kafka-on-fsx-for-ontap)
of Kafka directly on FSx for ONTAP.

## NetApp Harvest — Monitoring ONTAP Itself, Not a Data Store for Observability Tools

[NetApp Harvest](https://netapp.github.io/harvest/latest/) is an open-source tool that
collects performance, capacity, and hardware metrics from ONTAP, StorageGRID, E-Series, and
Cisco Nexus switches, and exports them to Prometheus or InfluxDB, with included Grafana
dashboards. **This is the inverse of what this document set otherwise covers**: Harvest
uses ONTAP as a monitored *target*, not as the *data store* backing an observability
platform. It is included here to avoid confusion — a reader searching for "NetApp
observability" will encounter Harvest frequently, and it answers a different question than
this document set does. [The repository's own guide to monitoring FSx for ONTAP using
Harvest](https://www.netapp.com/learn/aws-fsx-blg-how-to-monitor-amazon-fsx-for-netapp-ontap-using-netapp-harvest/)
covers this monitoring-of-ONTAP use case in more detail.

## SQL Server on FSx for ONTAP — iSCSI Performance, Not Observability, but Directly Relevant

Not an observability workload itself, but the most rigorous public benchmark this document
set found comparing FSx for ONTAP's storage protocols for a database-shaped workload:
[an AWS blog post (published 2025-05)](https://aws.amazon.com/blogs/modernizing-with-aws/optimizing-protocol-selection-when-using-amazon-fsx-for-netapp-ontap-for-microsoft-sql-server/)
ran HammerDB OLTP benchmarks against SQL Server on FSx for ONTAP configured for iSCSI vs.
SMB, on an r5dn.24xlarge instance, Multi-AZ, 80,000 IOPS/2GB/s, across four file-system
sizes (7/14/21/28TB). Result: iSCSI outperformed SMB by roughly 20% on initial runs and
roughly 10% at steady state. This is cited in the [iSCSI hypothesis section](README.md#sqliteinfluxdb-over-iscsi-a-hypothesis-not-a-verified-pattern)
as directional evidence for FSx for ONTAP's iSCSI implementation generally, not as an
observability-specific measurement.

## What This Page Does Not Cover

- **Azure NetApp Files– or Google Cloud NetApp Volumes–specific observability case
  studies beyond the OpenSearch post above.** This document's search did not surface a
  case study naming Azure NetApp Files or Google Cloud NetApp Volumes specifically as an
  observability-platform data store outside that cross-platform OpenSearch post. If such
  a case study exists and is found later, it belongs on this page.
- **A performance comparison between ONTAP delivery models** (e.g., is Cloud Volumes
  ONTAP's Kafka-NFS fix faster or slower than an equivalent on FSx for ONTAP). No such
  comparison was found, and none is claimed here.

## Related Documents

- [Overview: Consolidating Observability Storage on FSx for ONTAP](README.md)
- [Pattern 1: MQTT → TSDB → Live Dashboard](pattern-1-mqtt-tsdb-live-dashboard.md)
- [Pattern 4: Kafka + OTel Collector + TSDB](pattern-4-kafka-otel-collector.md)
- [Native Alternative Matrix](../native-alternative-matrix.md)
