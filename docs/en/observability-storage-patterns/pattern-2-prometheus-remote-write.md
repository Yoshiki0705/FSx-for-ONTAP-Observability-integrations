# Pattern 2: Prometheus + Remote-Write

🌐 [日本語](../../ja/observability-storage-patterns/pattern-2-prometheus-remote-write.md) | **English** (this page)

⬅️ [Back to overview](README.md)

## Typical Stack

Prometheus scrapes metrics from targets and writes them to its own local TSDB (blocks +
WAL), while `remote_write` forwards samples to a long-term storage backend — Thanos,
Mimir, Cortex, or a vendor platform — that itself typically persists to an S3-compatible
object store. Grafana or a similar tool queries either the local Prometheus or the
long-term backend for dashboards and alerts.

**Public references**: [Prometheus's own remote-write documentation](https://oneuptime.com/blog/post/2026-02-20-prometheus-remote-write-storage/view);
[Thanos remote-write scaling](https://medium.com/@mohitverma160288/thanos-remote-write-scaling-metrics-with-ease-part1-eb861b9aefa9).

## Why the Hot Path Stays Local

**Prometheus explicitly documents NFS/EFS as unsupported for its local TSDB.** A
maintainer response in [prometheus/prometheus#10611](https://github.com/prometheus/prometheus/issues/10611)
states: "NFS filesystems (including AWS's EFS) are not supported. NFS could be
POSIX-compliant, but most implementations are not. It is strongly recommended to use a
local filesystem for reliability." This is corroborated independently by
[SUSE's Rancher Monitoring documentation](https://www.suse.com/support/kb/doc?id=000021332),
which repeats the same guidance verbatim in a different product's context — two
independent sources agreeing this is a project-wide constraint, not an edge case.

This means the local Prometheus TSDB itself is never a candidate for any of the three FSx
for ONTAP patterns below. What FSx for ONTAP can touch is downstream of `remote_write`.

## Where FSx for ONTAP Fits

| FSx for ONTAP pattern | Applies? | Notes |
|---|:---:|---|
| [Pattern A: Archive](README.md#pattern-a-long-term-archive-via-s3-access-points) | ✅ | Applies to the `remote_write` destination's raw export (e.g., a Thanos/Mimir object-store bucket's export, or a downstream ETL job's output), never to Prometheus's own local TSDB blocks |
| [Pattern B: FlexClone](README.md#pattern-b-devtest-acceleration-via-flexclone) | ✅ | Clone the Pattern A archive to test alert-rule or recording-rule changes against realistic historical data |
| [Pattern C: Snapshot/SnapLock](README.md#pattern-c-forensic-protection-via-snapshot--snaplock) | ✅ | Protects the archived long-term metrics data an SLO post-mortem or capacity-planning exercise depends on |

## Pattern-Specific Notes

The archive target for Pattern A is the `remote_write` destination's export, not
Prometheus's own storage directory. Attempting to point Prometheus's `--storage.tsdb.path`
at an FSx for ONTAP NFS mount directly is the one thing this pattern explicitly does not
recommend, per Prometheus's own documentation cited above.

Thanos, Mimir, and Cortex all persist their long-term block storage to an S3-compatible
object store by design — the same hot-local/cold-object-store split independently observed
in [Pattern 1](pattern-1-mqtt-tsdb-live-dashboard.md) (InfluxDB 3) and
[Pattern 5](pattern-5-embedded-columnar-tsdb.md) (QuestDB, ClickHouse). FSx for ONTAP S3
Access Points are one possible destination for that object-store layer, chosen when the
archived metrics data also needs NFS/SMB access outside the metrics stack's own tooling.

## Related Documents

- [Overview: Consolidating Observability Storage on FSx for ONTAP](README.md)
- [On-Premises and Multi-Cloud ONTAP Case Studies](onprem-and-fsxn-case-studies.md)
- [S3 AP Specification & Constraints](../s3ap-fsxn-specification.md)
