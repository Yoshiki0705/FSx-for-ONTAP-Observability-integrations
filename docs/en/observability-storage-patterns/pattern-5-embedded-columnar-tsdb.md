# Pattern 5: Embedded/Columnar TSDB (QuestDB, ClickHouse, TimescaleDB)

🌐 [日本語](../../ja/observability-storage-patterns/pattern-5-embedded-columnar-tsdb.md) | **English** (this page)

⬅️ [Back to overview](README.md)

## Typical Stack

A purpose-built time-series or columnar database (QuestDB, ClickHouse, TimescaleDB) 
ingests high-throughput metrics or events directly, without a separate broker, and serves
queries for dashboards (often Grafana) from its own storage engine. Most of these engines
ship a built-in tiered storage model, with a hot local tier and a cold object-store tier.

**Public references**: [QuestDB's cold storage operations documentation](https://questdb.com/docs/operations/cold-storage/);
[ClickHouse's external disk storage documentation](https://clickhouse.com/docs/concepts/features/configuration/server-config/storing-data).

## Why the Hot Path Stays Local

These engines document their own hot-tier storage model as local-disk-oriented, and
independently ship a native S3-compatible tier for cold data rather than recommending a
network file share for either tier:

- **QuestDB**: [cold storage is a distinct, Enterprise-only, disabled-by-default
  feature](https://questdb.com/docs/operations/cold-storage/) layered on top of its
  local-disk hot storage engine — not a replacement for it.
- **ClickHouse**: [its documentation describes data as "usually stored in the local file
  system of the machine on which ClickHouse server is running"](https://clickhouse.com/docs/concepts/features/configuration/server-config/storing-data),
  with external disks (including S3-compatible ones) as an explicit, separate
  configuration for less latency-sensitive data.
- **TimescaleDB**, as a PostgreSQL extension, inherits PostgreSQL's own local-disk-oriented
  storage model for its hot hypertables.

This is the third independent confirmation of the same hot-local/cold-object-store split
observed in [Pattern 1](pattern-1-mqtt-tsdb-live-dashboard.md) (InfluxDB 3) and
[Pattern 2](pattern-2-prometheus-remote-write.md) (Thanos/Mimir/Cortex) — three unrelated
projects converging on the same answer independently.

## Where FSx for ONTAP Fits

| FSx for ONTAP pattern | Applies? | Notes |
|---|:---:|---|
| [Pattern A: Archive](README.md#pattern-a-long-term-archive-via-s3-access-points) | ✅ | An alternative destination for the engine's own cold tier, chosen when the archived data also needs NFS/SMB access outside the database's own tooling — not a required addition if the built-in cold tier already meets the need |
| [Pattern B: FlexClone](README.md#pattern-b-devtest-acceleration-via-flexclone) | ✅ | Clone the Pattern A archive to test query or schema changes against realistic historical data |
| [Pattern C: Snapshot/SnapLock](README.md#pattern-c-forensic-protection-via-snapshot--snaplock) | ✅ | Protects the archived cold-tier data a compliance or forensic review depends on |

## Pattern-Specific Notes

Unlike Patterns 1, 2, and 4, this pattern's hot/cold split is not a constraint FSx for
ONTAP works around — it is a design choice these engines already made on their own,
independently arriving at the same S3-compatible object-store answer this document
recommends for Pattern A. FSx for ONTAP's S3 Access Points are one possible implementation
of that same cold tier, not a novel capability being added on top of an engine that lacks
one. Choosing FSx for ONTAP over the engine's built-in cold-tier feature is a call to make
based on whether NFS/SMB access to that same archived data (outside the database's own
tooling) is actually needed — if it isn't, the built-in feature is simpler.

## Related Documents

- [Overview: Consolidating Observability Storage on FSx for ONTAP](README.md)
- [On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md)
- [S3 AP Specification & Constraints](../s3ap-fsxn-specification.md)
