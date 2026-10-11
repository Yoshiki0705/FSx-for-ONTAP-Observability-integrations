# S3 Access Points capacity pool reads at exchanged elapsed times, direct ALL and NONE-then-ALL tiering, 2026-10-11 (raw data)

English only, like the tool directory it belongs to (see
[`../../README.md`](../../README.md)). The bilingual write-up, with the result
tables, the interpretation fixed before the first invocation, and the limits of
this experiment, is in the section "Tiering path: direct ALL and NONE-then-ALL
(2026-10-11)", under the headings that start with "Experiment 3", in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../../../docs/ja/s3ap-throughput-benchmark.md)).

The three experiments of 2026-10-11 on this file system are called experiment 1,
2, and 3 in the write-up:

| Experiment | Directory | Volumes |
|------------|-----------|---------|
| 1, the tier comparison | [`../2026-10-11-tier/`](../2026-10-11-tier/README.md) | A1, A2 (policy NONE), B (policy ALL from creation) |
| 2, the tiering path | [`../2026-10-11-tier-path/`](../2026-10-11-tier-path/README.md) | K1, S3p (created with ALL), S2, S3, S4 (created with NONE, changed to ALL) |
| 3, this directory | `./` | DIR (created with ALL), SW (created with NONE, changed to ALL) |

The earlier directories of 2026-10-10 are
[`../2026-10-10/`](../2026-10-10/README.md),
[`../2026-10-10-pooled/`](../2026-10-10-pooled/README.md), and
[`../2026-10-10-position/`](../2026-10-10-position/README.md). Like the two
directories above, this one holds the analysis scripts, so that the derived
tables can be recomputed from the committed files.

## Files

Each file under `raw/` is the benchmark Lambda's response for one invocation.
No value was edited or masked when the files were added to the repository. A
search of every file in this directory found no account ID, ARN, access point
alias, bucket name, resource ID, or IP address. The files hold object keys,
sizes, timings, run IDs, the Region, and per-object samples.

| File | Content |
|------|---------|
| `raw/x3-<kind>-<volume>[...].json` | 6 files, one per invocation: warm-up 2 (`x3-warm-*`) and reads of 30 objects 4 (`x3-<volume>-c<cohort>`). `samples_ms` holds the latencies of an object in read order: 4 per object in a warm-up file, 1 in the others |
| `invocations.jsonl` | 6 lines, one per invocation: `tag`, `vol`, `prefix`, `max_keys`, `iterations`, `start_utc` and `end_utc` (client side), `elapsed_s`, `cli_rc`, `cli_out` (status code and function error), `cli_err` |
| `cw-all.tsv` | 20 lines of per-volume CloudWatch data, tab separated: volume label (`dir`, `sw`), metric, timestamp, Sum. Metrics `CapacityPoolReadOperations`, `CapacityPoolReadBytes`, `CapacityPoolWriteOperations`, `CapacityPoolWriteBytes`, `DataReadOperations`, `DataReadBytes`; one-minute points. The file holds only datapoints whose Sum is above 0. Timestamps carry the `+09:00` offset |
| `cw-storageused.tsv` | 180 lines of the volume-level `StorageUsed` metric, tab separated: volume label, tier (`SSD` or `StandardCapacityPool`), timestamp, value in bytes. One-minute points from 15:50 to 16:34 UTC. The statistic and the call that produced the file were not recorded in it. Timestamps carry the `+09:00` offset |
| `manifest.txt` | 122 lines: volume label, object key, size in bytes. It lists every object written to each volume, including the `warm/` object |
| `upload-times.tsv` | 123 lines (a header and one per object written): volume label, key, time of the `put-object` call in UTC, to the second |
| `run-log-times.tsv` | 14 lines (a header and 13 events) of times from the run log, tab separated: event, volume label (`all`, or `sw`, `dir`; `sw,dir` for the one event that covers both), time in UTC. The events are the volume creation, the stack deployment, the S3 Access Points becoming available, the start and ends of the writes, the listing check, the switch request, the first Amazon FSx API observation of ALL and of the full capacity pool tier of SW (both polled observations, so the change may have happened earlier), the warm-up start, and the end of the cleanup |
| `tier-polls.log` | The five polls made during the tiering of the two volumes: time of the poll, the tiering policy the Amazon FSx API returned, and the last one-minute `StorageUsed` points of the SSD and capacity pool tiers |
| `node-counters.tsv` | The `get` and `put` rows of node 1 of the ONTAP `object_store_client_op` counter table at three times (operations and bytes). The dumps themselves are not committed because their row IDs contain a file system identifier |
| `tier-comparison-cw-dataread.tsv` | 58 lines of per-volume `DataReadOperations` and `DataReadBytes` of experiment 1 (labels `a1`, `a2`, `b`; one-minute points, Sum above 0, `+09:00` offset). Source: a CloudWatch pull made after experiment 1 and kept outside the repository until now; `../2026-10-11-tier/` holds only the file-system-level `fs-DataReadBytes.tsv`. Not edited. It is used by `analyze_cross_experiment.py` only |
| `fs-DataReadBytes-experiment-2.tsv`, `fs-DataReadBytes-experiment-3.tsv` | File-system-level `DataReadBytes` (all volumes on the file system) at one-minute points, two columns: timestamp with the `+09:00` offset and Sum in bytes. Experiment 2: 12:00 to 13:29 UTC; experiment 3: 15:50 to 16:34 UTC. Same format as `../2026-10-11-tier/fs-DataReadBytes.tsv`. Pulled after the experiments. Not edited |
| `analyze_capacity_pool.py`, `analysis-capacity-pool.txt` | Script and its output: inputs, tiering writes, capacity pool reads per read and window, outside-window buckets, the node counter differences, read accounting, tier state, and the elapsed time between the data entering the capacity pool and each read (the rule of the experiment 2 script, block 8; the run-log times are printed separately and are not used as bounds) |
| `analyze_latency.py`, `analysis-latency.txt` | Script and its output: p50, p90, smallest, and largest per group and size, with the check against the tool's own p50, and the 5 MiB samples in read order and sorted |
| `analyze_cross_experiment.py`, `analysis-cross-experiment.txt` | Script and its output for the eight volumes of the three experiments: the write path against the result of the first read, the elapsed-time ranges and their overlap, and (post-hoc) the bytes read between a volume's last tiering write bucket and its first read, from other volumes of the experiment, from the volume itself, and at the file system level |

## Run IDs and schedule

`benchmark_run_id` is `x3-<kind>-<volume>[...]`. The `x3` prefix is part of the
IDs as recorded.

| Run ID pattern | Meaning | Invocations |
|----------------|---------|-------------|
| `x3-warm-<volume>` | Warm-up of the 64 KiB `warm/` object, volume `dir` or `sw` | 2 |
| `x3-<volume>-c<cohort>` | Read of the 30 objects under `cold/c<cohort>/`, volume `dir` or `sw`, cohort `1` (primary) or `2` (auxiliary) | 4 |

Volume `dir` was created with tiering policy ALL and written directly. Volume
`sw` was created with policy NONE and changed to ALL after the writes. Both
hold random bytes.

| Step | Time (UTC, 2026-10-11) |
|------|------------------------|
| Plan fixed (interpretation table included) | 15:45:11 |
| Volumes created | 15:45:39 (`sw`), 15:45:40 (`dir`) |
| S3 Access Points available | 15:54:29 |
| Writes to the two volumes, in parallel | 15:54:51 to 15:55:26 (the last PUT of `dir` 15:55:26, of `sw` 15:55:25) |
| Listing check against the manifest (61 objects each) | 15:55:36 |
| Switch request for `sw` | 15:55:36.1. The Amazon FSx API showed ALL at the first poll, 15:56:30 |
| Warm-up invocations (`sw`, then `dir`) | 15:56:11 to 15:56:14 |
| First one-minute `StorageUsed` point with the full logical size in the capacity pool tier | 15:56 (`dir`), 15:57 (`sw`; the poll of 15:57:10 still showed 0, the poll of 15:57:31 the full size) |
| `dir` cohort 1, `dir` cohort 2 | Start 16:02:26, 16:11:26 |
| `sw` cohort 1, `sw` cohort 2 | Start 16:13:36, 16:28:36 |
| Node counter reads | 13:26:13 (the end of experiment 2), 16:17:10, 16:29:33 |
| Cleanup done | 16:36:54 |

The plan time (15:45:11) is from a plan file that is not in this repository; the
volume creation time is in `run-log-times.tsv`. The order of the plan and the
results cannot be checked from the repository. The times of the volume
creation, the writes, the switch request, the Amazon FSx API observations, and
the cleanup are from a run log that is not in this repository; the same times
are in `run-log-times.tsv` and `upload-times.tsv`. The other times are in
`invocations.jsonl` and `cw-storageused.tsv`.

## Environment

The Region is ap-northeast-1. The Lambda settings (256 MB memory, 300 s timeout,
no VPC config) are those listed in
[`../2026-10-10/README.md`](../2026-10-10/README.md#environment). The file
system, SVM, and Lambda stack are those of experiment 2, described in
[`../2026-10-11-tier-path/README.md`](../2026-10-11-tier-path/README.md) and in
the "Environment of the tiering path experiment" table of the write-up; the
points that matter for reading the files are:

- Two volumes of 1024 MiB on one SVM of the same SINGLE_AZ_1 (first generation)
  file system as experiments 1 and 2, with 128 MBps throughput capacity and
  1024 GiB SSD. Snapshot policy none, UNIX security style, and
  `StorageEfficiencyEnabled` false were specified at creation and were not read
  back through ONTAP. The cloud retrieval policy was not read. The ONTAP version
  was 9.19.1P2, read through the ONTAP REST API before experiment 2 started and
  not read again for experiment 3.
- One S3 Access Point per volume, two in total, internet origin.
- The test objects are, per volume, one 64 KiB object under `warm/` and, under
  `cold/c1/` and `cold/c2/`, `<size>/obj-00` to `obj-09` for `s64k` (65,536
  bytes), `s1m` (1,048,576 bytes), and `s5m` (5,242,880 bytes): 61 objects and
  127,205,376 bytes per volume. Random bytes are `os.urandom` output, a
  different byte sequence for each volume and object. The `size_label` values
  `64KB`, `1MB`, and `5MB` in the files are these sizes.
- The listing of each volume at 15:55:36 UTC matched `manifest.txt` (61
  objects each); the listings are not committed.
- The two volumes, two S3 Access Points, and the benchmark stack were deleted
  afterwards, after their names, IDs, file system, and tag had been matched
  against the recorded list. The file system and its other volumes and S3
  Access Points were not changed. (Whether the deleted volumes entered ONTAP's
  volume recovery queue was not checked, and the queue was not purged.)
- The benchmark stack was deployed again for this experiment (15:46:53 UTC,
  `run-log-times.tsv`) from the same stack definition as experiment 2; the
  deployed commit of the tool was not recorded. The boto3 and urllib3 versions
  and whether each invocation was a cold or a warm start were not recorded.

## Parameters

Every invocation used the event `test: get` with the prefix, `max_keys`, and
`iterations` below, and its tag as `benchmark_run_id`. One invocation lists the
prefix once (not timed) and then reads each listed object `iterations` times in
a row, on one new boto3 client, at concurrency 1.

| Invocation type | Prefix | `max_keys` | `iterations` | Requests per invocation | GetObject reads in total |
|-----------------|--------|-----------:|-------------:|------------------------:|-------------------------:|
| Warm-up | `warm/` | 1 | 4 | 5 | 8 |
| Read of 30 objects | `cold/c1/` or `cold/c2/` | 30 | 1 | 31 | 120 |

In total there were 6 invocations (6 ListObjectsV2 and 128 GetObject) and
254,803,968 bytes: 2 warm-up invocations (8 reads) and 4 measured invocations
(120 reads). In `cold/c<cohort>/` the listing returns the keys in order, so one
invocation reads the ten 1 MiB objects, then the ten 5 MiB objects, then the ten
64 KiB objects.

## How to read the figures

The window rule is that of experiment 2: for an invocation,
`analyze_capacity_pool.py` takes the one-minute CloudWatch buckets from its
start minute through the minute after its end. In this experiment the
operations of all six invocations are in the bucket one minute after their
start minute: the 30 reads of `dir` cohort 1 (started 16:02:26) are in the 16:03
bucket, and the four warm-up reads of each volume are in the 15:57 bucket
although the warm-ups ran at 15:56:11 to 15:56:14. The cause was not examined.
The window rule includes the minute after the end, so the operations of each
invocation are inside its window.

`analyze_cross_experiment.py` sums with the labels as recorded. The 0.3 MB that
the other volume read before the first read of `dir` is the four warm-up reads
of `sw` (262,144 B). The script counts them only because their bucket is
labelled 15:57, the first bucket it counts for `dir` (the last bucket with
tiering writes of `dir` is 15:56); labelled 15:56, they would not be counted
and the figure would be 0. The comparison of 0.3 MB with the 89.7 MB of the
smallest read of B with capacity pool reads is unaffected.

- `f`, the capacity pool read ratio: `CapacityPoolReadBytes` of the volume in the
  window divided by the logical size of the objects the invocation read
  (63,569,920 B for one 30-object pass). Fixed before the first invocation:
  below 0.10, the reads did not reach the capacity pool; above 0.80, nearly all
  of the data was read from it; in between, partial.
- The tiering write ratio: `CapacityPoolWriteBytes` over all rows divided by the
  logical size of all objects on the volume, including the 64 KiB `warm/` object
  (127,205,376 B).
- A zero: `cw-all.tsv` holds only datapoints above 0, so "0" means "no positive
  datapoint" in the window. The control is `DataReadOperations` in the same
  window, which equals the number of GetObject reads (30 per pass).
- The elapsed time: "at least" from the end of the last one-minute bucket with
  `CapacityPoolWriteOperations` above 0 to the start of the read; "at most" from
  the start of the first such bucket. The rule is that of experiment 2. The
  run-log times are printed separately and are not used as bounds.
- The pre-registered reading of the two main reads (cohort 1 of each volume) is
  printed by `analyze_capacity_pool.py` (block 2); cohort 2 of each volume is
  auxiliary and is not used for that reading.

`samples_ms` holds the latencies of an object in read order. The latency script
uses the nearest-rank percentile, `sorted(samples)[ceil(p * n) - 1]`, the same
definition as the tool's `percentile_method` `"pooled_nearest_rank"`. For a
single invocation its p50 equals the tool's `per_size_class` `p50_ms`; the script
checks this for all 14 single-invocation values. `MB/s` in the files means MiB/s.

## Recomputing

The scripts need Python 3 and read only committed files: the first two only the
files in this directory, the third also files in `../2026-10-11-tier/` and
`../2026-10-11-tier-path/`. Run them from any directory:

```bash
python3 analyze_capacity_pool.py > /tmp/analysis-capacity-pool.txt
python3 analyze_latency.py > /tmp/analysis-latency.txt
python3 analyze_cross_experiment.py > /tmp/analysis-cross-experiment.txt
diff /tmp/analysis-capacity-pool.txt analysis-capacity-pool.txt
diff /tmp/analysis-latency.txt analysis-latency.txt
diff /tmp/analysis-cross-experiment.txt analysis-cross-experiment.txt
```

The `diff` commands print nothing when the committed outputs match.

## Node-level counters (derived)

The ONTAP `object_store_client_op` counter table was read through the ONTAP REST
API (GET only) at 13:26:13 UTC, the last counter read of experiment 2, and
again at 16:17:10 and 16:29:33 UTC. The counters are cumulative from an unknown
start, so only differences are used. `node-counters.tsv` holds the values;
`analyze_capacity_pool.py` (block 4) computes the differences.

| Counter row | Interval | Increase, operations | Increase, bytes | CloudWatch sum over the two volumes (`cw-all.tsv`) |
|-------------|----------|---------------------:|----------------:|-----------------------------------------------------|
| node 1 `get` | 13:26:13 to 16:17:10 | 60 | 245,760 | `CapacityPoolReadOperations` 60; `CapacityPoolReadBytes` 245,760 |
| node 1 `get` | 16:17:10 to 16:29:33 | 0 | 0 | none |
| node 1 `put` | 13:26:13 to 16:17:10 | 60 | 253,870,080 | `CapacityPoolWriteOperations` 60; `CapacityPoolWriteBytes` 253,870,080 |
| node 1 `put` | 16:17:10 to 16:29:33 | 0 | 0 | none |

The first interval contains the writes and tiering of both volumes, the
cohort 1 and 2 reads of `dir`, and the cohort 1 read of `sw`; the second
contains the cohort 2 read of `sw`. The increase of `get` in the first interval
(60 operations of 4,096 B) equals the 30 + 30 capacity pool read operations that
CloudWatch recorded in the minutes of the tiering writes; the reads of the four
cohorts added none. The first interval also covers the time between experiment 2
and the creation of these volumes, so it would include a GET to the capacity
pool by any other volume of the file system in that time; the increase was the
60 operations only. The counters are those of node 1.

## Tier state (derived from `cw-storageused.tsv`)

Volume-level `StorageUsed`, in bytes. Individual object locations were not
checked. The capacity pool tier equals the logical size (127,205,376 B) at every
point from the first full-size point to 16:34.

| Volume | First point at the full logical size (UTC) | SSD one point before | SSD at that point | SSD at 16:34 |
|--------|--------------------------------------------|---------------------:|------------------:|-------------:|
| `dir` | 15:56 | 66,867,200 | 14,753,792 | 15,253,504 |
| `sw` | 15:57 | 141,955,072 | 15,351,808 | 15,691,776 |

## Cross-experiment script and its inputs

`analyze_cross_experiment.py` reads this directory, `../2026-10-11-tier/`, and
`../2026-10-11-tier-path/`. Its block 4 is post-hoc: the table of the bytes read
between a volume's last tiering write bucket and its first read was made after
the results of experiment 3 were seen. It prints, per first read:

- (a) the `DataReadBytes` of the other volumes of the same experiment (the column
  used in the write-up);
- (b) the `DataReadBytes` of the volume itself in the same buckets;
- (c) the file-system-level `DataReadBytes` over the same buckets, which counts
  every volume on the file system, and the difference (c) - (a) - (b).

The difference is 0 B in every row. For experiment 1, `fs-DataReadBytes.tsv` of
`../2026-10-11-tier/` starts at 08:36 UTC, four buckets after the end of B's last
tiering write bucket (08:32), so those four buckets are compared by the
per-volume file only; it has no positive `DataReadBytes` datapoint of A1, A2, or
B before 08:38. The resolution is one minute. `DataReadBytes` does not measure
writes or non-data activity, so a write by another volume that overlapped is not
counted.

## Deviations and limits

The limits are listed in the write-up under "Experiment 3: deviations and
limits". The deviations from the plan that the files do not show by
themselves:

- The wait after the volume creation waited for the Amazon FSx API lifecycle
  state AVAILABLE, but an ONTAP volume reports CREATED as its settled state. The wait
  therefore ran for 7 minutes (about 15:47 to 15:54) before the S3 Access Points
  were requested. The reads were scheduled from the end of the writes, so their
  times were not affected.
- The warm-up was made at 15:56:11, 0.75 minutes after the end of the writes,
  instead of the planned 2.5 minutes, so that `sw` was read before its tiering
  finished (the switch had been requested at 15:55:36).
- The elapsed time of `dir` cohort 1 was 5.4 to 6.5 minutes, not the planned
  about 7: the tiering writes of `dir` fell into one bucket (15:56).
- The first attempt to create the volumes, typed in an interactive zsh shell,
  failed because the shell did not split the volume specification into two
  words. No resource was created (checked: no volume of this experiment
  existed). The volumes were then created with a bash script.
