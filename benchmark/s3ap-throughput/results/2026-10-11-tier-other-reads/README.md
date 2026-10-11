# S3 Access Points capacity pool reads after reads of other volumes, directly written ALL volumes, 2026-10-11 (raw data)

English only, like the tool directory it belongs to (see
[`../../README.md`](../../README.md)). The bilingual write-up, with the result
tables, the interpretation fixed before the first invocation, and the limits of
this experiment, is in the section "Tiering path: direct ALL and NONE-then-ALL
(2026-10-11)", under the headings that start with "Experiment 4", in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../../../docs/ja/s3ap-throughput-benchmark.md)).

The four experiments of 2026-10-11 on this file system are called experiment 1,
2, 3, and 4 in the write-up:

| Experiment | Directory | Volumes |
|------------|-----------|---------|
| 1, the tier comparison | [`../2026-10-11-tier/`](../2026-10-11-tier/README.md) | A1, A2 (policy NONE), B (policy ALL from creation) |
| 2, the tiering path | [`../2026-10-11-tier-path/`](../2026-10-11-tier-path/README.md) | K1, S3p (created with ALL), S2, S3, S4 (created with NONE, changed to ALL) |
| 3, elapsed times exchanged | [`../2026-10-11-tier-elapsed/`](../2026-10-11-tier-elapsed/README.md) | DIR (created with ALL), SW (created with NONE, changed to ALL) |
| 4, this directory | `./` | Q, P1, P2 (created with ALL), CHN (policy NONE, read to produce reads of another volume) |

The earlier directories of 2026-10-10 are
[`../2026-10-10/`](../2026-10-10/README.md),
[`../2026-10-10-pooled/`](../2026-10-10-pooled/README.md), and
[`../2026-10-10-position/`](../2026-10-10-position/README.md). Like the three
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
| `raw/x4-<kind>-<volume>[...].json` | 11 files, one per invocation: warm-up 3 (`x4-warm-*`), reads of 30 objects 6 (`x4-<volume>-c<cohort>`), reads of the churn volume 2 (`x4-chn-a`, `x4-chn-b`). `samples_ms` holds the latencies of an object in read order: 4 per object in a warm-up file, 1 in the others |
| `invocations.jsonl` | 11 lines, one per invocation: `tag`, `vol`, `prefix`, `max_keys`, `iterations`, `start_utc` and `end_utc` (client side), `elapsed_s`, `cli_rc`, `cli_out` (status code and function error), `cli_err` |
| `cw-all.tsv` | 48 lines of per-volume CloudWatch data, tab separated: volume label (`q`, `p1`, `p2`, `chn`), metric, timestamp, Sum. Metrics `CapacityPoolReadOperations`, `CapacityPoolReadBytes`, `CapacityPoolWriteOperations`, `CapacityPoolWriteBytes` (only for `q`, `p1`, `p2`), `DataReadOperations`, `DataReadBytes`; one-minute points. The file holds only datapoints whose Sum is above 0. Timestamps carry the `+09:00` offset |
| `cw-storageused.tsv` | 200 lines of the volume-level `StorageUsed` metric, tab separated: volume label, tier (`SSD` or `StandardCapacityPool`), timestamp, value in bytes. One-minute points from 18:03 to 18:27 UTC. The statistic and the call that produced the file were not recorded in it. Timestamps carry the `+09:00` offset |
| `manifest.txt` | 318 lines: volume label, object key, size in bytes. It lists every object written to each volume: 61 each for `q`, `p1`, `p2` (including the `warm/` object) and 135 for `chn` |
| `upload-times.tsv` | 319 lines (a header and one per object written): volume label, key, time of the `put-object` call in UTC, to the second |
| `run-log-times.tsv` | 12 lines (a header and 11 events) of times from the run log, tab separated: event, volume label (`all`, or the labels of the volumes the event covers), time in UTC. The events are the freezing of the plan, the volume creation (a range), the volumes becoming settled, the stack deployment, the S3 Access Points becoming available, the start and end of the writes to `chn`, the start and end of the writes to `q`, `p1`, `p2` (end = U), the listing check, and the end of the cleanup |
| `tier-polls.log` | The one poll made during the tiering of the volumes: time of the poll, the tiering policy the Amazon FSx API returned, and the last one-minute `StorageUsed` points of the SSD and capacity pool tiers |
| `fs-DataReadBytes.tsv` | File-system-level `DataReadBytes` (all volumes on the file system) at one-minute points, two columns: timestamp with the `+09:00` offset and Sum in bytes, 18:00 to 18:26 UTC. Same format as `../2026-10-11-tier/fs-DataReadBytes.tsv`. Pulled after the experiment. Not edited |
| `node-counters.tsv` | The `get` and `put` rows of node 1 of the ONTAP `object_store_client_op` counter table at two times (operations and bytes). The dumps themselves are not committed because their row IDs contain a file system identifier |
| `analyze_capacity_pool.py`, `analysis-capacity-pool.txt` | Script and its output: inputs, tiering writes, capacity pool reads per read and window, the row of the pre-registered table, outside-window buckets, the node counter differences, read accounting, tier state, and the elapsed time between the data entering the capacity pool and each read (the rule of the experiment 2 script, block 8; the run-log times are printed separately and are not used as bounds) |
| `analyze_latency.py`, `analysis-latency.txt` | Script and its output: p50, p90, smallest, and largest per group and size, with the check against the tool's own p50, and the 5 MiB samples in read order and sorted |
| `analyze_other_reads.py`, `analysis-other-reads.txt` | Script and its output (post-hoc): the bytes that the other volumes of the run read before each read of `q`, `p1`, `p2`, counted by the one-minute bucket rule of experiments 1 to 3 and per invocation, and the difference between the two |
| `analyze_eleven_volumes.py`, `analysis-eleven-volumes.txt` | Script and its output (post-hoc): the first reads of the eleven volumes of the four experiments (it imports the rules of `../2026-10-11-tier-elapsed/analyze_cross_experiment.py`), the write path against the result, DIR against Q, and the test of one-sided elapsed-time thresholds |

## Run IDs and schedule

`benchmark_run_id` is `x4-<kind>-<volume>[...]`. The `x4` prefix is part of the
IDs as recorded.

| Run ID pattern | Meaning | Invocations |
|----------------|---------|-------------|
| `x4-warm-<volume>` | Warm-up of the 64 KiB `warm/` object, volume `q`, `p1`, or `p2` | 3 |
| `x4-<volume>-c<cohort>` | Read of the 30 objects under `cold/c<cohort>/`, volume `q`, `p1`, or `p2`, cohort `1` (primary) or `2` (auxiliary) | 6 |
| `x4-chn-<a or b>` | Read of the 25 objects under `a/` or the 110 objects under `b/` of the churn volume `chn` (5 MiB each) | 2 |

Volumes `q`, `p1`, and `p2` were created with tiering policy ALL and written
directly; they hold random bytes. Volume `chn` was created with policy NONE and
holds random bytes; it is read to produce reads of another volume.

| Step | Time (UTC, 2026-10-11) |
|------|------------------------|
| Plan fixed (interpretation table included) | 18:01:24 |
| Volumes created | 18:01:25 to 18:01:26 |
| Volumes settled | 18:02:17 |
| Benchmark stack deployed | 18:02:41 |
| S3 Access Points available | 18:02:51 |
| Writes to `chn` (4 in parallel) | 18:03:00 to 18:03:39 |
| Node counter read | 18:03:49 |
| Writes to `q`, `p1`, `p2` (in parallel; end = U) | 18:03:51 to 18:04:29 |
| Listing check against the manifest | 18:04:31 |
| Warm-up invocations (`q`, `p1`, `p2`) | 18:05:14 to 18:05:18 |
| Tiering write bucket of `q`, `p1`, `p2` | 18:05 (all three) |
| Poll (capacity pool tier full on the three volumes) | 18:08:02 |
| `q` cohort 1, `chn` `a/`, `p1` cohort 1, `chn` `b/`, `p2` cohort 1 | Start 18:09:59, 18:10:59, 18:11:59, 18:12:59, 18:14:29 |
| `q`, `p1`, `p2` cohort 2 | Start 18:19:29, 18:21:29, 18:23:29 |
| Node counter read | 18:24:05 |
| Cleanup done | 18:30:37 |

The plan time (18:01:24) is from a plan file that is not in this repository; the
volume creation time is in `run-log-times.tsv`. The order of the plan and the
results cannot be checked from the repository. The times of the volume
creation, the writes, the listing check, and the cleanup are from a run log
that is not in this repository; the same times are in `run-log-times.tsv` and
`upload-times.tsv`. The other times are in `invocations.jsonl`,
`node-counters.tsv`, `tier-polls.log`, and `cw-storageused.tsv`.

## Environment

The Region is ap-northeast-1. The Lambda settings (256 MB memory, 300 s timeout,
no VPC config) are those listed in
[`../2026-10-10/README.md`](../2026-10-10/README.md#environment). The file
system, SVM, and Lambda stack are those of experiments 2 and 3, described in
[`../2026-10-11-tier-path/README.md`](../2026-10-11-tier-path/README.md) and in
the "Environment of the tiering path experiment" table of the write-up; the
points that matter for reading the files are:

- Four volumes on one SVM of the same SINGLE_AZ_1 (first generation) file system
  as experiments 1 to 3, with 128 MBps throughput capacity and 1024 GiB SSD:
  `q`, `p1`, `p2` of 1024 MiB with policy ALL, and `chn` of 2048 MiB with policy
  NONE. Snapshot policy none, UNIX security style, and
  `StorageEfficiencyEnabled` false were specified at creation and were not read
  back through ONTAP. The cloud retrieval policy was not read. The ONTAP
  version was 9.19.1P2, read through the ONTAP REST API earlier on 2026-10-11,
  before the volumes were created, and not read again for experiment 4.
- One S3 Access Point per volume, four in total, internet origin.
- The test objects are, per volume of `q`, `p1`, `p2`, one 64 KiB object under
  `warm/` and, under `cold/c1/` and `cold/c2/`, `<size>/obj-00` to `obj-09` for
  `s64k` (65,536 bytes), `s1m` (1,048,576 bytes), and `s5m` (5,242,880 bytes):
  61 objects and 127,205,376 bytes per volume, the same as `dir` and `sw` of
  experiment 3. On `chn`, 25 objects of 5,242,880 bytes under `a/` and 110 under
  `b/`: 135 objects and 707,788,800 bytes. Random bytes are `os.urandom` output,
  a different byte sequence for each volume and object. The `size_label` values
  `64KB`, `1MB`, and `5MB` in the files are these sizes.
- The listing of each volume after its writes matched `manifest.txt`; the
  listings are not committed.
- The four volumes, four S3 Access Points, and the benchmark stack were deleted
  afterwards, after their names, IDs, file system, and tag had been matched
  against the recorded list. The file system and its other volumes and S3
  Access Points were not changed. After that, the ONTAP volume recovery queue
  was emptied: it held 14 entries (the deleted volumes of this and the three
  earlier runs; all names start with `zz_tier`, all on the SVM of the file
  system), each purged by name through the ONTAP REST API (a POST to the
  private CLI path of `volume recovery-queue purge`, with the SVM and the
  volume name), and a read of the queue afterwards returned no entry. This
  supersedes the statements in the READMEs of the earlier directories that the
  deleted volumes were not purged, which were true when written. AWS documents
  that deleted FSx for ONTAP volumes are retained in the recovery queue for at
  least 12 hours
  ([`documented`](https://repost.aws/knowledge-center/fsx-ontap-recover-deleted-volume)),
  so these purges came before the automatic end of the retention.
- The benchmark stack was deployed again for this experiment (18:02:41 UTC,
  `run-log-times.tsv`) from the same stack definition as experiments 2 and 3;
  the deployed commit of the tool was not recorded. The boto3 and urllib3
  versions and whether each invocation was a cold or a warm start were not
  recorded.

## Parameters

Every invocation used the event `test: get` with the prefix, `max_keys`, and
`iterations` below, and its tag as `benchmark_run_id`. One invocation lists the
prefix once (not timed) and then reads each listed object `iterations` times in
a row, on one new boto3 client, at concurrency 1.

| Invocation type | Prefix | `max_keys` | `iterations` | Requests per invocation | GetObject reads in total |
|-----------------|--------|-----------:|-------------:|------------------------:|-------------------------:|
| Warm-up (`q`, `p1`, `p2`) | `warm/` | 1 | 4 | 5 | 12 |
| Read of 30 objects (`q`, `p1`, `p2`) | `cold/c1/` or `cold/c2/` | 30 | 1 | 31 | 180 |
| Read of `chn`, part `a/` | `a/` | 25 | 1 | 26 | 25 |
| Read of `chn`, part `b/` | `b/` | 110 | 1 | 111 | 110 |

In total there were 11 invocations (11 ListObjectsV2 and 327 GetObject) and
1,089,994,752 bytes. In `cold/c<cohort>/` the listing returns the keys in
order, so one invocation reads the ten 1 MiB objects, then the ten 5 MiB
objects, then the ten 64 KiB objects.

## How to read the figures

The window rule is that of experiments 2 and 3: for an invocation,
`analyze_capacity_pool.py` takes the one-minute CloudWatch buckets from its
start minute through the minute after its end. The operations of each
cohort read are in the bucket one minute after its start minute (for example
the 30 reads of `q` cohort 1, started 18:09:59, are in the 18:10 bucket, and the
reads of `p2` cohort 1, started 18:14:29, in the 18:15 bucket), and 15 of the 110
reads of `chn` `b/`, which ended at 18:13:13.7, are labelled 18:14. The cause
was not examined. The window rule includes the minute after the end, so the
operations of each invocation are inside its window.

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
  the start of the first such bucket. The rule is that of experiment 2: minutes
  in "at least" are rounded down and in "at most" up, so each stays a bound. For
  example `q` cohort 1 started at 18:09:59.0, 3.984 minutes after the end of its
  last bucket (18:06:00) and 4.984 minutes after the start of its first
  (18:05:00): 3.9 to 5.0. The run-log times are printed separately and are not
  used as bounds. A rounding to the nearest tenth would print 4.0 for the first
  figure; the rounding down keeps it a bound.
- The pre-registered reading of the three main reads (cohort 1 of `q`, `p1`,
  `p2`) is printed by `analyze_capacity_pool.py` (block 2); cohort 2 of each
  volume is auxiliary and is not used for that reading.

`samples_ms` holds the latencies of an object in read order. The latency script
uses the nearest-rank percentile, `sorted(samples)[ceil(p * n) - 1]`, the same
definition as the tool's `percentile_method` `"pooled_nearest_rank"`. For a
single invocation its p50 equals the tool's `per_size_class` `p50_ms`; the script
checks this for all 23 single-invocation values. `MB/s` in the files means MiB/s.

## Recomputing

The scripts need Python 3 and read only committed files: the first three only the
files in this directory, the fourth also the files that
`../2026-10-11-tier-elapsed/analyze_cross_experiment.py` reads (it imports that
script and does not write bytecode). Run them from any directory:

```bash
python3 analyze_capacity_pool.py > /tmp/analysis-capacity-pool.txt
python3 analyze_latency.py > /tmp/analysis-latency.txt
python3 analyze_other_reads.py > /tmp/analysis-other-reads.txt
python3 analyze_eleven_volumes.py > /tmp/analysis-eleven-volumes.txt
diff /tmp/analysis-capacity-pool.txt analysis-capacity-pool.txt
diff /tmp/analysis-latency.txt analysis-latency.txt
diff /tmp/analysis-other-reads.txt analysis-other-reads.txt
diff /tmp/analysis-eleven-volumes.txt analysis-eleven-volumes.txt
```

The `diff` commands print nothing when the committed outputs match.

## Node-level counters (derived)

The ONTAP `object_store_client_op` counter table was read through the ONTAP REST
API (GET only) at 18:03:49 UTC, before the first PUT to `q`, `p1`, `p2`, and
again at 18:24:05 UTC, after the last read. The counters are cumulative from an
unknown start, so only differences are used. `node-counters.tsv` holds the
values; `analyze_capacity_pool.py` (block 4) computes the differences.

| Counter row | Increase, operations | Increase, bytes | CloudWatch sum over the volumes (`cw-all.tsv`) |
|-------------|---------------------:|----------------:|------------------------------------------------|
| node 1 `get` | 2,398 | 368,928,864 | `CapacityPoolReadOperations` 2,398; `CapacityPoolReadBytes` 368,928,864 |
| node 1 `put` | 90 | 380,805,120 | `CapacityPoolWriteOperations` 90; `CapacityPoolWriteBytes` 380,805,120 (126,935,040 x 3) |

Of the 2,398 `get` operations, 90 (30 per volume, 4,096 B each) are in the
minute of the tiering writes of each volume, equal to the reads that follow a
write, and 2,308 are in the windows of the six cohort reads. `chn` had no
capacity pool operation (policy NONE). The counters are those of node 1. The
interval starts after the creation of the volumes and after the writes to
`chn`, so a GET or PUT to the capacity pool by another volume of the file system
in the interval would be in it; the increases equal the sums of the three
volumes.

## Tier state (derived from `cw-storageused.tsv`)

Volume-level `StorageUsed`, in bytes. Individual object locations were not
checked. The capacity pool tier equals the logical size (127,205,376 B) at every
point from the first full-size point to 18:27; each of the three volumes had one
earlier point of 262,144 B (18:04). `chn` held 0 B in the capacity pool tier at
every point.

| Volume | First point at the full logical size (UTC) | SSD one point before | SSD at that point | SSD at 18:27 |
|--------|--------------------------------------------|---------------------:|------------------:|-------------:|
| `q` | 18:05 | 34,959,360 | 15,933,440 | 16,744,448 |
| `p1` | 18:05 | 31,473,664 | 15,089,664 | 16,756,736 |
| `p2` | 18:05 | 34,967,552 | 15,941,632 | 16,752,640 |

## Reads of other volumes (derived, post-hoc)

`analyze_other_reads.py` computes, for each read of `q`, `p1`, `p2`, the bytes
read by the other volumes of the run after the end of the volume's last
tiering write bucket and before the read started, in two ways. Both are
post-hoc: the plan fixed the doses (about 0.5, 200, and 800 MB) before the
first invocation, and the bucket rule and the per-invocation recount were
chosen or added after the results were seen.

- The bucket rule of experiments 1 to 3: the one-minute CloudWatch
  `DataReadBytes` buckets t with E <= t < m, labels as recorded, where E is the
  end of the last bucket with tiering writes (18:06) and m is the start minute
  of the read. Cohort 1 results: `q` 0.5, `p1` 64.1, `p2` 756.8 MB.
- The per-invocation recount: the bytes each invocation of another volume read
  (the GetObject sizes in `raw/`), for the invocations that started after U and
  ended before the read started. Cohort 1 results: `q` 0.5, `p1` 195.2, `p2`
  835.5 MB (835,452,928 B).

The two differ for `p1` cohort 1 (131,072,000 B, the `chn` `a/` read, labelled
18:11 = m) and `p2` cohort 1 (78,643,200 B, the last 15 reads of `chn` `b/`,
labelled 18:14 = m). The invocations finished before the later read started,
but CloudWatch labels their operations in a later minute, and the rule
`t < m` leaves the start minute of the later read out. For the cohort 2 reads
the two agree. The file-system-level `DataReadBytes` equal the sum of the four
volumes in every bucket counted for the cohort 1 reads of `q` and `p1`; for
`p2` cohort 1 the two metrics place 4 reads of `chn` (20,971,520 B) in
adjacent minutes (18:13 and 18:14) and the sum of the differences over the
file is 0 B. No read on a volume outside the run shows in the file-system-level
metric.

## Eleven volumes (derived, post-hoc)

`analyze_eleven_volumes.py` lists the first reads of the eleven volumes of the
four experiments by the rules of `analyze_cross_experiment.py`, with the bytes
read by other volumes by the bucket rule, the write path against the result,
DIR (experiment 3) against Q (experiment 4), and which one-sided elapsed-time
thresholds the bounds exclude. It was written after the results of experiment 4
were seen.

## Deviations and limits

The limits are listed in the write-up under "Experiment 4: deviations and
limits". The deviations from the plan that the files do not show by
themselves:

- The design has three directly written volumes (Q, P1, P2) and a churn volume
  (`chn`), so that a zero on one dosed volume could be told from too small a
  dose. It was fixed in the plan file before the first invocation.
- The other-reads doses were planned as about 0.5, 200, and 800 MB. The
  per-invocation figures are 0.5, 195.2, and 835.5 MB; the bucket rule gives
  0.5, 64.1, and 756.8 MB (see above).
- The read times are U (18:04:29) plus the planned offsets (5.5, 6.5, 7.5, 8.5,
  and 10.0 minutes for the first five reads; 15.0, 17.0, and 19.0 for cohort 2),
  which put four starts at hh:mm:59 and four at hh:mm:29. The bucket labels of
  the operations of these invocations are described above and under "Reads of
  other volumes".
- The recovery queue was purged after the cleanup, not during the experiment,
  as planned.
