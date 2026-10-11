# S3 Access Points capacity pool reads after direct ALL and NONE-then-ALL tiering, 2026-10-11 (raw data)

English only, like the tool directory it belongs to (see
[`../../README.md`](../../README.md)). The bilingual write-up, with the result
tables, the interpretation fixed before the first invocation, and the limits of
this experiment, is the section "Tiering path: direct ALL and NONE-then-ALL
(2026-10-11)" in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../../../docs/ja/s3ap-throughput-benchmark.md)). The earlier
directories are [`../2026-10-11-tier/`](../2026-10-11-tier/README.md) (the tier
comparison, on three other volumes of the same file system),
[`../2026-10-10/`](../2026-10-10/README.md),
[`../2026-10-10-pooled/`](../2026-10-10-pooled/README.md), and
[`../2026-10-10-position/`](../2026-10-10-position/README.md). Like the tier
comparison directory, this one holds the analysis scripts, so that the derived
tables can be recomputed from the committed files.

## Files

Each file under `raw/` is the benchmark Lambda's response for one invocation.
No value was edited or masked when the files were added to the repository. A
search of every file in this directory found no account ID, ARN, access point
alias, bucket name, resource ID, or IP address. The files hold object keys,
sizes, timings, run IDs, the Region, and per-object samples.

| File | Content |
|------|---------|
| `raw/x2-<kind>-<volume>[...].json` | 47 files, one per invocation: warm-up 5 (`x2-warm-*`), pre-reads 8 (`x2-pre-*`), first reads after tiering of S2, S3, S3p, S4 4 (`x2-cold-*`), K1 first reads 12 (`x2-k1first-*`), K1 re-reads 12 (`x2-k1re-*`), and re-reads of S2, S3, S4 at about 30 and 60 minutes 6 (`x2-coldre-*`). `samples_ms` holds the latencies of an object in read order: 4 per object in a warm-up file, 8 in a pre-read file, 1 in the others |
| `invocations.jsonl` | 47 lines, one per invocation: `tag`, `vol`, `prefix`, `max_keys`, `iterations`, `start_utc` and `end_utc` (client side), `elapsed_s`, `cli_rc`, `cli_out` (status code and function error), `cli_err` |
| `cw-all.tsv` | 88 lines of per-volume CloudWatch data, tab separated: volume label (`k1`, `s2`, `s3`, `s3p`, `s4`), metric, timestamp, Sum. Metrics `CapacityPoolReadOperations`, `CapacityPoolReadBytes`, `CapacityPoolWriteOperations`, `CapacityPoolWriteBytes`, `DataReadOperations`, `DataReadBytes`; one-minute points. The file holds only datapoints whose Sum is above 0, and it is de-duplicated. Timestamps carry the `+09:00` offset |
| `cw-storageused.tsv` | 806 lines of the volume-level `StorageUsed` metric, tab separated: volume label, tier (`SSD` or `StandardCapacityPool`), timestamp, value in bytes. One-minute points from 12:05 to 13:25 UTC (S3 and S3p have no point at 12:05). The statistic and the call that produced the file were not recorded in it. Timestamps carry the `+09:00` offset |
| `manifest.txt` | 245 lines: volume label, object key, size in bytes. It lists every object written to each volume, including the `warm/` object |
| `analyze_capacity_pool.py`, `analysis-capacity-pool.txt` | Script and its output: inputs, tiering writes, capacity pool reads per read and window, the stored-bytes ratio, buckets outside every window, totals, read accounting, and tier state |
| `analyze_latency.py`, `analysis-latency.txt` | Script and its output: p50, p90, smallest, and largest per group and size, with the check against the tool's own p50 |

## Run IDs and schedule

`benchmark_run_id` is `x2-<kind>-<volume>[...]`. The `x2` prefix is part of the
IDs as recorded.

| Run ID pattern | Meaning | Invocations |
|----------------|---------|-------------|
| `x2-warm-<volume>` | Warm-up of the 64 KiB `warm/` object, volume `k1`, `s2`, `s3`, `s3p`, `s4` | 5 |
| `x2-pre-<volume>-r<n>` | Reads before the switch, volume `s2` or `s3`, n 1 to 4 | 8 |
| `x2-cold-<volume>` | First read after tiering, volume `s2`, `s3`, `s3p`, `s4` | 4 |
| `x2-k1first-c<cohort>-<size>` | K1 first read, cohort 1 to 4, size `s64k`, `s1m`, `s5m` | 12 |
| `x2-k1re-c<cohort>-<size>` | K1 re-read of the same prefixes | 12 |
| `x2-coldre-m<minutes>-<volume>` | Re-read of the 30 objects of `s2`, `s3`, `s4`, minutes `30` or `60` | 6 |

Volumes `k1` and `s3p` were created with tiering policy ALL and written
directly. Volumes `s2`, `s3`, and `s4` were created with policy NONE and changed
to ALL after the writes. Volumes `s3` and `s3p` hold same-byte content; the others
hold random bytes.

| Step | Time (UTC, 2026-10-11) |
|------|------------------------|
| Volumes created | About 12:03:50 to 12:04:48 |
| S3 Access Points available | 12:05:28 |
| Writes to the five volumes | 12:05:56 to 12:07:14 |
| Warm-up invocations | 12:09:16 to 12:09:22 |
| Pre-read invocations | 12:09:30 to 12:12:21 |
| Switch requests for `s2`, `s3`, `s4` | 12:13:21.8, 12:13:22.3, 12:13:22.7. The FSx API showed ALL from 12:14:29 |
| First reads of `s2`, `s3`, `s4` | Start 12:22:15.9, 12:22:20.5, 12:22:23.2; 534.1, 538.2, and 540.5 s after the switch requests (the plan was 503 s) |
| First read of `s3p` | 12:22:33.5 to 12:22:38.2 |
| First reads of `k1` | 12:22:38.2 to 12:23:01.9 |
| K1 re-reads, cohorts 1 to 4 | Start 12:26:01, 12:31:25, 12:53:01, 13:23:01 |
| Re-reads of `s2`, `s3`, `s4` | 12:52:16 to 12:52:27 and 13:22:16 to 13:22:27 |

The times of the volume creation, the writes, and the switch requests are from a
run log that is not in this repository. The other times are in
`invocations.jsonl` and `cw-storageused.tsv`. The K1 re-read start times were set
from 12:23:01 UTC, the end of the first-read sweep, plus 3, 8.4, 30, and 60
minutes. The re-reads of `s2`, `s3`, and `s4` were set at 12:52:16 and 13:22:16
UTC, 30 and 60 minutes after the first read of `s2` started.

## Environment

The Region is ap-northeast-1. The Lambda settings (256 MB memory, 300 s timeout,
no VPC config) are those listed in
[`../2026-10-10/README.md`](../2026-10-10/README.md#environment). The file
system, volumes, and S3 Access Points of this experiment are described in the
"Environment of the tiering path experiment" table of the write-up; the points
that matter for reading the files are:

- Five volumes of 1024 MiB on one SVM of the same SINGLE_AZ_1 (first generation)
  file system as the tier comparison, with 128 MBps throughput capacity and
  1024 GiB SSD. Snapshot policy none, UNIX security style, and
  `StorageEfficiencyEnabled` false were specified at creation and were not read
  back through ONTAP. The cloud retrieval policy was not read. The ONTAP version
  was 9.19.1P2, read through the ONTAP REST API before the volumes were created.
- One S3 Access Point per volume, five in total, internet origin.
- The test objects are, per volume, one 64 KiB object under `warm/` and, under
  `cold/` (K1: `c1/` to `c4/`), `<size>/obj-00` to `obj-09` for `s64k` (65,536
  bytes), `s1m` (1,048,576 bytes), and `s5m` (5,242,880 bytes). Random bytes are
  `os.urandom` output, a different byte sequence for each volume and object.
  Same-byte content is the byte `r` repeated, so all objects of one size are
  identical. The `size_label` values `64KB`, `1MB`, and `5MB` in the files are
  these sizes.
- The listing of each volume at 12:07:28 UTC matched `manifest.txt`; the
  listings are not committed.
- The five volumes, five S3 Access Points, and the benchmark stack were deleted
  afterwards. The file system and its other volumes and S3 Access Points were
  not changed. (The deleted volumes were in ONTAP's volume recovery queue at the
  end. Their retention period was not read, and they were not purged.)
- The tool was the one at `main` commit `6808382`; `handler.py` and
  `template.yaml` have not changed on `main` since. The commit deployed for this
  experiment was not recorded. The boto3 and urllib3 versions and whether each
  invocation was a cold or a warm start were not recorded.

## Parameters

Every invocation used the event `test: get` with the prefix, `max_keys`, and
`iterations` below, and its tag as `benchmark_run_id`. One invocation lists the
prefix once (not timed) and then reads each listed object `iterations` times in
a row, on one new boto3 client, at concurrency 1.

| Invocation type | Prefix | `max_keys` | `iterations` | Requests per invocation | GetObject reads in total |
|-----------------|--------|-----------:|-------------:|------------------------:|-------------------------:|
| Warm-up | `warm/` | 1 | 4 | 5 | 20 |
| Pre-read | `cold/` | 30 | 8 | 241 | 1,920 |
| First read after tiering, re-read of `s2`, `s3`, `s4` | `cold/` | 30 | 1 | 31 | 300 |
| K1 first read and re-read | `c<cohort>/<size>/` | 10 | 1 | 11 | 240 |

The 300 reads of the third row are 4 first-read and 6 re-read invocations of
30 objects (120 + 180); the 240 reads of the fourth row are 12 first-read and 12 re-read
invocations of 10 objects. In `cold/` the listing returns the keys in order, so
one invocation reads the ten 1 MiB objects, then the ten 5 MiB objects, then the
ten 64 KiB objects.

In total there were 47 invocations (47 ListObjectsV2 and 2,480 GetObject) and
5,214,044,160 bytes: 5 warm-up invocations (20 reads), 8 pre-read invocations
(1,920 reads), and 34 measured invocations (540 reads).

## How to read the figures

The window rule: for an invocation, `analyze_capacity_pool.py` takes the
one-minute CloudWatch buckets from its start minute through the minute after its
end. CloudWatch can label the operations of an invocation that runs late in a
minute with the next minute; the tier comparison directory records the same
effect. Here, all 1,896 K1 first-read operations are in the 12:23 bucket, though
the 12 invocations started between 12:22:38 and 12:22:59. For K1 first reads the
window is the union of the 12 invocations.

- `f`, the capacity pool read ratio: `CapacityPoolReadBytes` of the volume in the
  window divided by the logical size of the objects the invocation or invocations
  read (63,569,920 B for one 30-object pass, 254,279,680 B for the 12 K1
  prefixes).
- `g`, the stored-bytes ratio (added after the S3p first-read result was seen):
  `CapacityPoolReadBytes` of a first read divided by `CapacityPoolWriteBytes` of
  the volume's tiering.
- The tiering write ratio: `CapacityPoolWriteBytes` over all rows divided by the
  logical size of all objects on the volume, including the 64 KiB `warm/` object
  (63,635,456 B; K1 254,345,216 B).
- A zero: `cw-all.tsv` holds only datapoints above 0, so "0" means "no positive
  datapoint" in the window. The control is `DataReadOperations` in the same
  window, which equals the number of GetObject reads (30 per pass).

`samples_ms` holds the latencies of an object in read order. The latency script
uses the nearest-rank percentile, `sorted(samples)[ceil(p * n) - 1]`, the same
definition as the tool's `percentile_method` `"pooled_nearest_rank"`. For a
single invocation its p50 equals the tool's `per_size_class` `p50_ms`; the script
checks this for all 54 single-invocation values. `MB/s` in the files means MiB/s.

## Recomputing

The scripts need Python 3 and read only the files in this directory. Run them
from any directory:

```bash
python3 analyze_capacity_pool.py > /tmp/analysis-capacity-pool.txt
python3 analyze_latency.py > /tmp/analysis-latency.txt
diff /tmp/analysis-capacity-pool.txt analysis-capacity-pool.txt
diff /tmp/analysis-latency.txt analysis-latency.txt
```

Both `diff` commands print nothing when the committed outputs match.

## Node-level counters (derived)

The ONTAP `object_store_client_op` counter table was read through the ONTAP REST
API (GET only) at 09:24:08 UTC, the last counter read of the tier comparison, and
again at 13:26:13 UTC, 4 h 2 min later. The dumps are not committed because their
row IDs contain the file system identifier. The counters are cumulative from an
unknown start, so only differences are used.

| Counter row | Increase, operations | Increase, bytes | CloudWatch sum over the five volumes (`cw-all.tsv`) |
|-------------|---------------------:|----------------:|-----------------------------------------------------|
| node 1 `get` | 2,401 | 248,866,720 | `CapacityPoolReadOperations` 2,401; `CapacityPoolReadBytes` 248,866,720 |
| node 1 `put` | 120 | 382,525,440 | `CapacityPoolWriteOperations` 120; `CapacityPoolWriteBytes` 382,525,440 |

The differences equal the CloudWatch sums. The interval also covers the time
before this experiment's volumes existed, and no counter read was taken just
before this experiment.

## Tier state (derived from `cw-storageused.tsv`)

Volume-level `StorageUsed`, in bytes. Individual object locations were not
checked. The capacity pool tier equals the logical size at every point from the
first full-size point to 13:25.

| Volume | First point at the full logical size (UTC) | Capacity pool tier at 13:25 | SSD at 12:14 | SSD at 12:17 | SSD at 13:25 |
|--------|--------------------------------------------|----------------------------:|-------------:|-------------:|-------------:|
| `k1` | 12:08 | 254,345,216 | 19,316,736 | 19,316,736 | 21,131,264 |
| `s2` | 12:15 | 63,635,456 | 80,560,128 | 17,219,584 | 17,874,944 |
| `s3` | 12:15 | 63,635,456 | 80,248,832 | 16,908,288 | 17,563,648 |
| `s3p` | 12:08 | 63,635,456 | 15,380,480 | 15,380,480 | 15,855,616 |
| `s4` | 12:15 | 63,635,456 | 78,262,272 | 14,921,728 | 16,224,256 |

`analysis-capacity-pool.txt` lists the minute-by-minute SSD changes after 12:17
and whether each lies in an invocation window of the same volume.

## Deviations and limits

The limits are listed in the write-up under "Limits of the tiering path
experiment". Three deviations from the plan are not visible in the files alone:

- The first reads of `s2`, `s3`, and `s4` started 534 to 541 s after the switch
  requests instead of 503 s. A display error in the polling output delayed the
  start by about 30 s.
- A repeated update request for `s4` was rejected because an update was in
  progress; it had no effect.
- Duplicate rows in an analysis pull briefly doubled the operation counts. They
  were removed by de-duplication, and `DataReadOperations` returned to 30 per
  window. `cw-all.tsv` is the de-duplicated file.
