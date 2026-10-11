# S3 Access Points read-latency comparison of SSD and capacity pool volumes, 2026-10-11 (raw data)

English only, like the tool directory it belongs to (see
[`../../README.md`](../../README.md)). The bilingual write-up, with the result
tables, the decision rule, and the limits of this measurement, is the section
"Tier comparison: SSD and capacity pool (2026-10-11)" in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../../../docs/ja/s3ap-throughput-benchmark.md)). The earlier
directories from 2026-10-10 are [`../2026-10-10/`](../2026-10-10/README.md),
[`../2026-10-10-pooled/`](../2026-10-10-pooled/README.md), and
[`../2026-10-10-position/`](../2026-10-10-position/README.md). Unlike those,
this directory also holds the two analysis scripts, because the derived tables
follow a decision rule that is easier to check by running it than by reading it.

## Files

Each file under `raw/` is the benchmark Lambda's response for one invocation.
No value was edited or masked when the files were added to the repository. A
search of every file in this directory found no account ID, ARN, access point
alias, bucket name, resource ID, or IP address. The files hold object keys,
sizes, timings, run IDs, the Region, and per-object samples.

| File | Content |
|------|---------|
| `raw/tier-r<run>-<condition>-<size>.json` | 27 files, one per measured invocation. Run `1` to `3`, condition `a1`, `a2`, or `b`, size `s64k`, `s1m`, or `s5m`. Each holds 10 objects with 4 samples each (`samples_ms`, in read order) |
| `raw/tier-warm-<condition>.json` | 3 warm-up invocations (one 64 KiB object read 4 times on each volume). Kept, not analysed |
| `invocations.jsonl` | 30 lines, one per invocation: `tag`, `cond`, `prefix`, `start_utc` and `end_utc` (client side), `elapsed_s`, `status`, `function_error`, `bytes` (size of the response) |
| `cw-all.tsv` | 684 lines of per-volume CloudWatch data, tab separated: volume label (`a1`, `a2`, `b`), metric, timestamp, Sum. Metrics `CapacityPoolReadOperations`, `CapacityPoolReadBytes`, `CapacityPoolWriteOperations`, `CapacityPoolWriteBytes`; 57 one-minute points per volume and metric, 08:30 to 09:26 UTC. Timestamps carry the `+09:00` offset |
| `fs-DataReadBytes.tsv`, `fs-DataReadOperations.tsv`, `fs-DataWriteBytes.tsv` | File-system-level CloudWatch Sum, 50 one-minute points each, 08:36 to 09:25 UTC (`+09:00` offset). Tab separated: timestamp, value |
| `tier-polls.log` | 4 polls of the volume-level `StorageUsed` metric (bytes) per tier. Each line: volume label, `SSD`, timestamp, value, `CPOOL`, timestamp, value. The `CPOOL` value is the capacity pool tier. Poll times are in the `##` lines; timestamps in the data columns use the `+09:00` offset |
| `analyze_latency.py`, `analysis-latency.txt` | Script and its output: per-run medians, the decision rule, and the pooled figures |
| `analyze_capacity_pool.py`, `analysis-capacity-pool.txt` | Script and its output: capacity pool reads per invocation, CloudWatch buckets outside any invocation, and the position check |

## Run IDs and schedule

`benchmark_run_id` is `tier-r<run>-<condition>-<size>` for measured invocations
and `tier-warm-<condition>` for warm-up invocations. Condition `a1` and `a2` are
volumes with tiering policy NONE; `b` is a volume with tiering policy ALL.

| Run | Order inside each size block | First start to last end (UTC) | Invocations |
|-----|------------------------------|-------------------------------|-------------|
| warm-up | A1, A2, B | 08:37:30 to 08:37:34 | 3 |
| 1 | A1, A2, B | 08:38:00 to 08:46:07 | 9 |
| 2 | A2, B, A1 | 08:56:45 to 09:04:49 | 9 |
| 3 | B, A1, A2 | 09:15:43 to 09:23:48 | 9 |

Times are from `invocations.jsonl`, truncated to whole seconds. Inside a run,
the size blocks run in the order 64 KiB, 1 MiB, 5 MiB, and consecutive
invocations start 60 s apart whatever the condition. The gap from the end of one
run to the start of the next is 637.9 s (run 1 to 2) and 653.8 s (run 2 to 3).
Run 3 was started a second time: the first attempt stopped before any
invocation because a credential refresh failed, and the schedule was not changed.

The tool was the one at `main` commit `6808382`; `handler.py` and `template.yaml`
were not modified for this measurement.

## Environment

The Region is ap-northeast-1. The Lambda settings (256 MB memory, 300 s timeout,
no VPC config) are those listed in
[`../2026-10-10/README.md`](../2026-10-10/README.md#environment). The file
system, volumes, and S3 Access Points of this measurement are described in the
"Environment" table of the write-up; the points that matter for reading the files
are:

- Three volumes of 1024 MiB on one SVM of one SINGLE_AZ_1 (first generation)
  file system with 128 MBps throughput capacity and 1024 GiB SSD: A1 and A2 with
  tiering policy NONE, B with tiering policy ALL. Cloud retrieval policy
  `default` on all three. The ONTAP version was 9.19.1P2, read through the ONTAP
  REST API after the runs.
- One S3 Access Point per volume, three in total (type ONTAP, internet origin,
  file system identity UNIX user `root`).
- The test objects are random bytes, a different byte sequence on each volume:
  one 64 KiB object under `warm/` and, per run, 10 objects each of 64 KiB,
  1 MiB, and 5 MiB under `run<run>/<size>/obj-00` to `obj-09`. Each run reads a
  set that no earlier invocation had read. The `size_label` values `64KB`, `1MB`,
  and `5MB` in the files are 65,536, 1,048,576, and 5,242,880 bytes.
- The last upload finished at 08:31:31 UTC (recorded by the upload script; not
  part of the committed files). The first poll in `tier-polls.log`, at
  08:31:55 UTC, already shows 190,775,296 B in the capacity pool tier of B and
  0 B for A1 and A2. B invocations started 8.5 to 50.2 minutes after 08:31:31 UTC
  and all 27 invocations 6.5 to 52.2 minutes after it.
- The three volumes and the three S3 Access Points were deleted afterwards. The
  file system and its other volumes and S3 Access Points were not changed.
  (The deleted volumes were still in ONTAP's volume recovery queue at the end.
  Their retention period was not read, and they were not purged.)
- The boto3 and urllib3 versions in the Lambda runtime, and whether each
  invocation was a cold or a warm start, were not recorded.

## Parameters

Each measured invocation used the event `test: get`, a prefix of the form
`run<run>/<size>/`, `max_keys` 10, and `iterations` 4, with the invocation's tag
as `benchmark_run_id`. The warm-up invocations used the prefix `warm/` and
`max_keys` 1. One invocation lists the prefix once (not timed) and then reads each
listed object 4 times in a row, so it makes 1 ListObjectsV2 and 40 GetObject
requests on one new boto3 client, at concurrency 1.

In total there were 27 measured invocations (27 ListObjectsV2 and 1,080
GetObject) and 3 warm-up invocations (3 ListObjectsV2 and 12 GetObject), so 1,092
GetObject reads and 2,289,303,552 bytes (2,288,517,120 measured plus 786,432
warm-up).

## How to read the figures

`samples_ms` holds an object's 4 latencies in read order. The analysis forms three
classes per invocation:

| Class | Samples | n per invocation |
|-------|---------|------------------|
| first | `samples_ms[0]` of each of the 10 objects | 10 |
| repeat | `samples_ms[1:]` of each object | 30 |
| all | every sample | 40 |

The statistic is the median by nearest rank over the class samples, the same
definition as the tool's `percentile_method` `"pooled_nearest_rank"`. For a size
and class, `m_A1(r)`, `m_A2(r)`, and `m_B(r)` are the medians of run `r`;
`D(r) = m_B(r) - (m_A1(r) + m_A2(r)) / 2` and `N(r) = |m_A1(r) - m_A2(r)|`.
`N_largest` is the largest `N(r)` over the three runs. B is called slower for a
size and class only if `D(r) > 0` and `D(r) > N_largest` in all three runs;
otherwise no difference is established. The rule applies to the classes first and
all. The repeat class is reported without a rule. `analysis-latency.txt` also
prints `all_x1`, the all class without the first sample of the invocation; its
medians equal those of the all class in every run, size, and condition.

`MB/s` in the files means MiB/s. Throughput is per stream at concurrency 1.

For capacity pool reads, `analyze_capacity_pool.py` assigns each B invocation the
1-minute `CapacityPoolReadOperations` and `CapacityPoolReadBytes` buckets from its
start minute through the minute after its end. In runs 2 and 3, all four B
invocations that had capacity pool reads (1 MiB and 5 MiB, started 43 to 45 seconds
past the minute) had every operation in the next minute's bucket; the two such
invocations in run 1 (started at :00) did not. The cause was not investigated, and
the window was extended after run 2. The extension affects only the capacity
pool table, not the latency rule. B invocations are at least 3 minutes apart, so
the windows do not overlap.

## Recomputing

The scripts need Python 3 and read only the files in this directory. Run them
from any directory:

```bash
python3 analyze_latency.py > /tmp/analysis-latency.txt
python3 analyze_capacity_pool.py > /tmp/analysis-capacity-pool.txt
diff /tmp/analysis-latency.txt analysis-latency.txt
diff /tmp/analysis-capacity-pool.txt analysis-capacity-pool.txt
```

Both `diff` commands print nothing when the committed outputs match.

## Node-level counters (derived)

The ONTAP `object_store_client_op` counter table was read through the ONTAP REST
API (GET only) before and after each run. The dumps are not committed because
their row IDs contain the file system identifier. The table gives the increase
over each run next to the sums of the per-invocation CloudWatch values from
`analysis-capacity-pool.txt`. The counters are cumulative from an unknown start,
so only differences are used.

| Run | Counter reads (UTC) | CloudWatch B operations | Node 1 `get` increase, operations | CloudWatch B bytes | Node 1 `get` increase, bytes | Equal |
|-----|---------------------|-------------------------|-----------------------------------|--------------------|------------------------------|-------|
| 1 | 08:37:47 to 08:46:18 | 388 | 388 | 62,151,168 | 62,151,168 | yes |
| 2 | 08:56:42 to 09:05:01 | 379 | 379 | 62,134,656 | 62,134,656 | yes |
| 3 | 09:15:40 to 09:24:08 | 352 | 352 | 60,099,552 | 60,099,552 | yes |

Over the same intervals the node 1 `put` increase was 0 in all three runs, the
`head` increase was 16, 15, and 15 on node 1 and 16, 16, and 16 on node 2, and
node 2 had no `get` or `put` row. In a 60 s window with no benchmark reads
(08:34:58 to 08:35:58 UTC, before the warm-up), `get` and `put` did not change
and `head` rose by 2 on each node.

## Tier state (derived from `tier-polls.log`)

Volume-level `StorageUsed`, in bytes, SSD tier / capacity pool tier. Individual
object locations were not checked.

| Poll (UTC) | A1 | A2 | B |
|------------|----|----|---|
| 08:31:55 | 212,529,152 / 0 | 214,056,960 / 0 | 24,326,144 / 190,775,296 |
| 08:34:20 | 212,529,152 / 0 | 214,056,960 / 0 | 24,326,144 / 190,775,296 |
| 08:37:14 | 212,529,152 / 0 | 214,056,960 / 0 | 24,326,144 / 190,775,296 |
| 09:24:10 (after the runs) | 215,019,520 / 0 | 215,711,744 / 0 | 15,732,736 / 190,775,296 |

The capacity pool tier of B did not change between the first poll and the last,
and A1 and A2 held 0 B in it. The change of B's SSD figure was not examined.

## File-system-level reads and writes

Between 08:36 and 09:25 UTC the sum of `DataReadBytes` is 2,289,303,552 B and the
sum of `DataReadOperations` is 1,092, the same as the benchmark's own reads
(1,080 + 12 GetObject reads). The sum of `DataWriteBytes` is 0. In `cw-all.tsv`,
B's `CapacityPoolWriteOperations` and `CapacityPoolWriteBytes` are non-zero only
at 08:30 and 08:31 UTC, before the warm-up, and A1 and A2 have no non-zero value
in any of the four capacity pool metrics.

## Limits

The limits of the measurement are listed in the write-up under "Limits of the
tier comparison". Two points about the run are not visible in the files alone:

- Run 3 was started a second time after a credential refresh failure stopped the
  first attempt before any invocation. The schedule was not changed.
- The CloudWatch attribution window was extended by one minute after run 2, as
  described under "How to read the figures". The decision rule does not use
  CloudWatch data.
