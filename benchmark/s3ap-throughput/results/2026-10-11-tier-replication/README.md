# S3 Access Points capacity pool reads, a replication of experiment 3 (two directly written and switched volumes), 2026-10-11 (raw data)

English only, like the tool directory it belongs to (see
[`../../README.md`](../../README.md)). The bilingual write-up, with the result
tables, the interpretation fixed before the first invocation, and the limits of
this experiment, is in the section "Tiering path: direct ALL and NONE-then-ALL
(2026-10-11)", under the headings that start with "Experiment 5", in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../../../docs/ja/s3ap-throughput-benchmark.md)).

The five experiments of 2026-10-11 on this file system are called experiment 1,
2, 3, 4, and 5 in the write-up:

| Experiment | Directory | Volumes |
|------------|-----------|---------|
| 1, the tier comparison | [`../2026-10-11-tier/`](../2026-10-11-tier/README.md) | A1, A2 (policy NONE), B (policy ALL from creation) |
| 2, the tiering path | [`../2026-10-11-tier-path/`](../2026-10-11-tier-path/README.md) | K1, S3p (created with ALL), S2, S3, S4 (created with NONE, changed to ALL) |
| 3, elapsed times exchanged | [`../2026-10-11-tier-elapsed/`](../2026-10-11-tier-elapsed/README.md) | DIR (created with ALL), SW (created with NONE, changed to ALL) |
| 4, reads of other volumes | [`../2026-10-11-tier-other-reads/`](../2026-10-11-tier-other-reads/README.md) | Q, P1, P2 (created with ALL), CHN (policy NONE, read to produce reads of another volume) |
| 5, this directory | `./` | DIR (created with ALL), SW (created with NONE, changed to ALL): a replication of experiment 3 |

The earlier directories of 2026-10-10 are
[`../2026-10-10/`](../2026-10-10/README.md),
[`../2026-10-10-pooled/`](../2026-10-10-pooled/README.md), and
[`../2026-10-10-position/`](../2026-10-10-position/README.md). Like the earlier
directories, this one holds the analysis scripts, so that the derived tables can
be recomputed from the committed files.

Experiment 5 repeats experiment 3's design: two volumes, DIR created with policy
ALL and written directly, and SW created with policy NONE and changed to ALL
after the writes, each 1024 MiB and 127,205,376 B logical, with no large write
before them. Only the volume names and the byte sequences differ. Experiment 3
is in [`../2026-10-11-tier-elapsed/`](../2026-10-11-tier-elapsed/README.md).

## Files

Each file under `raw/` is the benchmark Lambda's response for one invocation.
No value was edited or masked when the files were added to the repository. A
search of every file in this directory found no account ID, ARN, access point
alias, bucket name, resource ID, or IP address. The files hold object keys,
sizes, timings, run IDs, the Region, and per-object samples.

| File | Content |
|------|---------|
| `raw/x5-<kind>-<volume>.json` | 6 files, one per invocation: warm-up 2 (`x5-warm-dir`, `x5-warm-sw`), reads of 30 objects 4 (`x5-<volume>-c<cohort>`). `samples_ms` holds the latencies of an object in read order: 4 per object in a warm-up file, 1 in the others |
| `invocations.jsonl` | 6 lines, one per invocation: `tag`, `vol`, `prefix`, `max_keys`, `iterations`, `start_utc` and `end_utc` (client side), `elapsed_s`, `cli_rc`, `cli_out` (status code and function error), `cli_err` |
| `cw-all.tsv` | 20 lines of per-volume CloudWatch data, tab separated: volume label (`dir`, `sw`), metric, timestamp, Sum. Metrics `CapacityPoolReadOperations`, `CapacityPoolReadBytes`, `CapacityPoolWriteOperations`, `CapacityPoolWriteBytes`, `DataReadOperations`, `DataReadBytes`; one-minute points. The file holds only datapoints whose Sum is above 0. Timestamps carry the `+09:00` offset |
| `cw-storageused.tsv` | 172 lines of the volume-level `StorageUsed` metric, tab separated: volume label, tier (`SSD` or `StandardCapacityPool`), timestamp, value in bytes. One-minute points. The statistic and the call that produced the file were not recorded in it. Timestamps carry the `+09:00` offset |
| `manifest.txt` | 122 lines: volume label, object key, size in bytes. It lists every object written to each volume: 61 each for `dir` and `sw` (including the `warm/` object) |
| `upload-times.tsv` | 123 lines (a header and one per object written): volume label, key, time of the `put-object` call in UTC, to the second |
| `run-log-times.tsv` | 12 lines (a header and 11 events) of times from the run log, tab separated: event, volume label (`all`, or the labels of the volumes the event covers), time in UTC. The events are the freezing of the plan, the volume creation (a range), the stack deployment, the S3 Access Points becoming available, the start and end of the writes, the listing check, the switch request for SW, the warm-up, and the end of the cleanup |
| `tier-polls.log` | The polls made during the tiering of the volumes: time of the poll, the tiering policy the Amazon FSx API returned, and the last one-minute `StorageUsed` points of the SSD and capacity pool tiers |
| `fs-DataReadBytes.tsv` | File-system-level `DataReadBytes` (all volumes on the file system) at one-minute points, two columns: timestamp with the `+09:00` offset and Sum in bytes. Same format as `../2026-10-11-tier/fs-DataReadBytes.tsv`. Pulled after the experiment. Not edited |
| `node-counters.tsv` | The `get` and `put` rows of node 1 of the ONTAP `object_store_client_op` counter table at two times (operations and bytes). The dumps themselves are not committed because their row IDs contain a file system identifier |
| `analyze_capacity_pool.py`, `analysis-capacity-pool.txt` | Script and its output: inputs, tiering writes, capacity pool reads per read and window, the row of the pre-registered table, outside-window buckets, the node counter differences, read accounting, tier state, and the elapsed time between the data entering the capacity pool and each read (the rule of the experiment 2 script, block 8; the run-log times, including the from-first-PUT and from-switch-request references, are printed separately and are not used as bounds) |
| `analyze_latency.py`, `analysis-latency.txt` | Script and its output: p50, p90, smallest, and largest per group and size, with the check against the tool's own p50, and the 5 MiB samples in read order and sorted. DIR cohort 1 at 5 MiB (p50 373.6 ms) is the one slow cohort |
| `analyze_thirteen_volumes.py`, `analysis-thirteen-volumes.txt` | Script and its output (post-hoc): the first reads of the thirteen volumes of the five experiments (it imports `../2026-10-11-tier-other-reads/analyze_eleven_volumes.py` and, through it, `../2026-10-11-tier-elapsed/analyze_cross_experiment.py`), the write path against the result with the thirteen-volume counts, the two directly written volumes that read zero (experiment 3 DIR and experiment 5 DIR), and the test of one-sided elapsed-time thresholds |

## Run IDs and schedule

`benchmark_run_id` is `x5-<kind>-<volume>`. The `x5` prefix is part of the IDs
as recorded.

| Run ID pattern | Meaning | Invocations |
|----------------|---------|-------------|
| `x5-warm-<volume>` | Warm-up of the 64 KiB `warm/` object, volume `dir` or `sw` | 2 |
| `x5-<volume>-c<cohort>` | Read of the 30 objects under `cold/c<cohort>/`, volume `dir` or `sw`, cohort `1` (primary) or `2` (auxiliary) | 4 |

Volume `dir` was created with tiering policy ALL and written directly. Volume
`sw` was created with policy NONE and changed to ALL after the writes. Both hold
random bytes.

| Step | Time (UTC, 2026-10-11) |
|------|------------------------|
| Plan fixed (interpretation table included) | 18:42:26 |
| Volumes created | 18:42:26 to 18:42:30 |
| Benchmark stack deployed | 18:43:44 |
| S3 Access Points available | 18:44:07 |
| Writes to `dir` and `sw` (in parallel; end = U) | 18:44:14 to 18:44:48 |
| Listing check against the manifest | 18:44:49 |
| Switch request for `sw` | 18:44:49.6 |
| Warm-up invocations (`dir`, `sw`) | 18:46:09.7 |
| Tiering write bucket | `dir` 18:45, `sw` 18:48 |
| DIR cohort 1 | Start 18:51:47 |
| DIR cohort 2 (auxiliary) | Start 19:00:47 |
| SW cohort 1 | Start 19:02:49 |
| SW cohort 2 (auxiliary, run late and by hand) | Start 19:25:20 |
| Node counter read | 18:24:05 and 19:27:31 |
| Cleanup done | 19:30:39 |

The plan time (18:42:26) is from a plan file that is not in this repository; the
volume creation time is in `run-log-times.tsv`. The order of the plan and the
results cannot be checked from the repository. The times of the volume creation,
the writes, the listing check, the switch request, the warm-up, and the cleanup
are from a run log that is not in this repository; the same times are in
`run-log-times.tsv` and `upload-times.tsv`. The other times are in
`invocations.jsonl`, `node-counters.tsv`, `tier-polls.log`, and
`cw-storageused.tsv`.

## Environment

The Region is ap-northeast-1. The Lambda settings (256 MB memory, 300 s timeout,
no VPC config) are those listed in
[`../2026-10-10/README.md`](../2026-10-10/README.md#environment). The file
system, SVM, and Lambda stack are those of experiments 2 to 4, described in
[`../2026-10-11-tier-path/README.md`](../2026-10-11-tier-path/README.md) and in
the "Environment of the tiering path experiment" table of the write-up; the
points that matter for reading the files are:

- Two volumes on one SVM of the same SINGLE_AZ_1 (first generation) file system
  as experiments 1 to 4, with 128 MBps throughput capacity and 1024 GiB SSD:
  `dir` and `sw`, each 1024 MiB. `dir` was created with policy ALL; `sw` was
  created with policy NONE and changed to ALL after the writes. Snapshot policy
  none, UNIX security style, and `StorageEfficiencyEnabled` false were specified
  at creation and were not read back through ONTAP. The cloud retrieval policy
  was not read. The ONTAP version was 9.19.1P2, read through the ONTAP REST API
  earlier on 2026-10-11, and not read again for experiment 5.
- One S3 Access Point per volume, two in total, internet origin.
- The test objects are, per volume, one 64 KiB object under `warm/` and, under
  `cold/c1/` and `cold/c2/`, `<size>/obj-00` to `obj-09` for `s64k` (65,536
  bytes), `s1m` (1,048,576 bytes), and `s5m` (5,242,880 bytes): 61 objects and
  127,205,376 bytes per volume, the same as `dir` and `sw` of experiment 3.
  Random bytes are `os.urandom` output, a different byte sequence for each volume
  and object. The `size_label` values `64KB`, `1MB`, and `5MB` in the files are
  these sizes.
- The listing of each volume after its writes matched `manifest.txt`; the
  listings are not committed.
- The two volumes, two S3 Access Points, and the benchmark stack were deleted
  afterwards, after their names, IDs, file system, and tag had been matched
  against the recorded list. The file system and its other volumes and S3 Access
  Points were not changed. After that, the ONTAP volume recovery queue was
  emptied of the two deleted volumes of this run by name through the ONTAP REST
  API (a POST to the private CLI path of `volume recovery-queue purge`, with the
  SVM and the volume name), and a read of the queue afterwards returned no entry.
  AWS documents that deleted FSx for ONTAP volumes are retained in the recovery
  queue for at least 12 hours
  ([`documented`](https://repost.aws/knowledge-center/fsx-ontap-recover-deleted-volume)),
  so these purges came before the automatic end of the retention.
- The benchmark stack was deployed again for this experiment (18:43:44 UTC,
  `run-log-times.tsv`) from the same stack definition as experiments 2 to 4; the
  deployed commit of the tool was not recorded. The boto3 and urllib3 versions
  and whether each invocation was a cold or a warm start were not recorded.

## Parameters

Every invocation used the event `test: get` with the prefix, `max_keys`, and
`iterations` below, and its tag as `benchmark_run_id`. One invocation lists the
prefix once (not timed) and then reads each listed object `iterations` times in
a row, on one new boto3 client, at concurrency 1.

| Invocation type | Prefix | `max_keys` | `iterations` | Requests per invocation | GetObject reads in total |
|-----------------|--------|-----------:|-------------:|------------------------:|-------------------------:|
| Warm-up (`dir`, `sw`) | `warm/` | 1 | 4 | 5 | 8 |
| Read of 30 objects (`dir`, `sw`) | `cold/c1/` or `cold/c2/` | 30 | 1 | 31 | 120 |

In total there were 6 invocations (6 ListObjectsV2 and 128 GetObject) and
254,803,968 bytes. In `cold/c<cohort>/` the listing returns the keys in order,
so one invocation reads the ten 1 MiB objects, then the ten 5 MiB objects, then
the ten 64 KiB objects.

## How to read the figures

The window rule is that of experiments 2 to 4: for an invocation,
`analyze_capacity_pool.py` takes the one-minute CloudWatch buckets from its start
minute through the minute after its end. All four cohort reads had 0 capacity
pool read operations in their windows; `DataReadOperations` in each window was
30, the control that the reads were recorded.

- `f`, the capacity pool read ratio: `CapacityPoolReadBytes` of the volume in the
  window divided by the logical size of the objects the invocation read
  (63,569,920 B for one 30-object pass). Fixed before the first invocation:
  below 0.10, the reads did not reach the capacity pool; above 0.80, nearly all
  of the data was read from it; in between, partial. All four reads were f 0.000.
- The tiering write ratio: `CapacityPoolWriteBytes` over all rows divided by the
  logical size of all objects on the volume, including the 64 KiB `warm/` object
  (127,205,376 B). Both volumes are 0.9979.
- A zero: `cw-all.tsv` holds only datapoints above 0, so "0" means "no positive
  datapoint" in the window. The control is `DataReadOperations` in the same
  window, which equals the number of GetObject reads (30 per pass).
- The elapsed time: "at least" from the end of the last one-minute bucket with
  `CapacityPoolWriteOperations` above 0 to the start of the read; "at most" from
  the start of the first such bucket. The rule is that of experiment 2: minutes
  in "at least" are rounded down and in "at most" up, so each stays a bound. For
  example DIR cohort 1 started at 18:51:47.0, 5.784 minutes after the end of its
  one write bucket (18:46:00) and 6.784 minutes after its start (18:45:00): 5.7
  to 6.8. The run-log times are printed separately and are not used as bounds;
  for DIR cohort 1 the from-first-PUT reference is about 7.5 minutes.
- The pre-registered reading of the two main reads (cohort 1 of `dir` and `sw`)
  is printed by `analyze_capacity_pool.py` (block 2); cohort 2 of each volume is
  auxiliary and is not used for that reading.

`samples_ms` holds the latencies of an object in read order. The latency script
uses the nearest-rank percentile, `sorted(samples)[ceil(p * n) - 1]`, the same
definition as the tool's `percentile_method` `"pooled_nearest_rank"`. For a
single invocation its p50 equals the tool's `per_size_class` `p50_ms`; the script
checks this for all 14 single-invocation values. `MB/s` in the files means MiB/s.

## Recomputing

The scripts need Python 3 and read only committed files: the first two only the
files in this directory, the third also the files that
`../2026-10-11-tier-other-reads/analyze_eleven_volumes.py` and
`../2026-10-11-tier-elapsed/analyze_cross_experiment.py` read (it imports those
scripts and does not write bytecode). Run them from any directory:

```bash
python3 analyze_capacity_pool.py > /tmp/analysis-capacity-pool.txt
python3 analyze_latency.py > /tmp/analysis-latency.txt
python3 analyze_thirteen_volumes.py > /tmp/analysis-thirteen-volumes.txt
diff /tmp/analysis-capacity-pool.txt analysis-capacity-pool.txt
diff /tmp/analysis-latency.txt analysis-latency.txt
diff /tmp/analysis-thirteen-volumes.txt analysis-thirteen-volumes.txt
```

The `diff` commands print nothing when the committed outputs match.

## Node-level counters (derived)

The ONTAP `object_store_client_op` counter table was read through the ONTAP REST
API (GET only) at 18:24:05 UTC and again at 19:27:31 UTC, after the last read.
The counters are cumulative from an unknown start, so only differences are used.
`node-counters.tsv` holds the values; `analyze_capacity_pool.py` (block 4)
computes the differences.

| Counter row | Increase, operations | Increase, bytes | Reading |
|-------------|---------------------:|----------------:|---------|
| node 1 `get` | 60 | 245,760 | All 60 are the write-following reads (30 per volume, 4,096 B each; `dir` in the 18:46 bucket, `sw` in the 18:48 bucket), equal to the sum of `CapacityPoolReadOperations` in `cw-all.tsv`. No data GET occurred during the four cohort reads |
| node 1 `put` | 60 | 253,870,080 | The two volumes' 30 tiering write operations each, equal to the `CapacityPoolWriteOperations` sum (60) and the two volumes' `CapacityPoolWriteBytes` (126,935,040 x 2) |

The counters are those of node 1. The interval holds this run's writes, tiering,
and reads, so a GET or PUT to the capacity pool by another volume of the file
system in the interval would be in it.

## Tier state (derived from `cw-storageused.tsv`)

Volume-level `StorageUsed`, in bytes. Individual object locations were not
checked. The capacity pool tier equals the logical size (127,205,376 B) at every
point from the first full-size point to the last point. `dir` reached it at
18:45, one minute after its creation and writes; `sw` reached it at 18:48, after
the switch to ALL (one earlier point, 18:47, held 3,563,520 B).

| Volume | First point at the full logical size (UTC) | SSD one point before | SSD at that point |
|--------|--------------------------------------------|---------------------:|------------------:|
| `dir` | 18:45 | 12,947,456 | 14,913,536 |
| `sw` | 18:48 | 138,698,752 | 15,204,352 |

The poll in `tier-polls.log` agrees: `dir` policy ALL with the full size from
18:45; `sw` policy NONE until 18:46, then ALL, with the capacity pool tier
reaching the full size at 18:48.

## The thirteen volumes (derived, post-hoc)

`analyze_thirteen_volumes.py` lists the first reads of the thirteen volumes of
the five experiments by the rules of `analyze_cross_experiment.py`, with the
bytes read by other volumes by the bucket rule, the write path against the
result and its counts, the two directly written volumes that read zero
(experiment 3 DIR and experiment 5 DIR), and which one-sided elapsed-time
thresholds the bounds exclude. It was written after the results of experiment 5
were seen. Counts: 8 volumes written directly (6 with capacity pool reads at
their first read, 2 with none: experiment 3 DIR and experiment 5 DIR), 5 switched
from NONE to ALL (all with none), 13 in total.

## Deviations and limits

The limits are listed in the write-up under "Experiment 5: deviations and
limits". The deviations from the plan that the files do not show by themselves:

- The first scheduler start fired before the uploads finished and failed (a
  partial log was read); no resource was created, and the run restarted at about
  18:46:0x UTC with a complete log. U and the switch request are unchanged. The
  warm-up ran about 80 seconds after the switch request (18:46:09.7) instead of
  the planned 35 seconds; experiment 3's warm-up was 35 seconds after.
- The scheduler process was lost after SW cohort 1 (19:02:49) and before SW
  cohort 2 (planned 19:17:49) because the terminal closed. SW cohort 2
  (auxiliary, not used for the reading) was run by hand at 19:25:20; its elapsed
  time is the measured value (40.5 minutes from the switch request).
- The wait after the volumes were created was shorter than experiment 3's. In
  experiment 3 a wait-loop error left about 7 minutes between creation and
  upload; the error was fixed for experiment 5, so creation (18:42:30) to upload
  (18:44:14) was about 1.7 minutes.
- The recovery queue was purged after the cleanup, not during the experiment, as
  planned.
