# S3 Access Points slow-read position check, 2026-10-10 (raw data)

English only, like the tool directory it belongs to (see
[`../../README.md`](../../README.md)). The bilingual write-up, with the design,
the result table by position, the scored predictions, and the limits, is in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../../../docs/ja/s3ap-throughput-benchmark.md)). Related raw data
from the same day: [`../2026-10-10/`](../2026-10-10/README.md) (averaged class
percentiles) and [`../2026-10-10-pooled/`](../2026-10-10-pooled/README.md)
(pooled class percentiles). The pooled runs showed that 19 of 23 slow reads were
the 50th or the 100th GetObject of an invocation
([#147](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/147),
[#98](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/98));
this check asks what ties slow reads to those positions. It was taken on the
same Amazon FSx for NetApp ONTAP file system (FSx for ONTAP below) and volume as
the pooled runs.

## Files

Two directories hold 40 files. `sequential/` holds the 24 invocations that are
analysed. `overlapped/` holds 16 invocations that ran at about concurrency 2
(a side set, explained under "Primary and overlapped sets"). Each file is the
benchmark Lambda's response for one invocation, copied byte for byte from the
saved response and only renamed; no value was edited or masked, and a sweep of all 40 files
found no account ID, ARN, access point alias, bucket name, resource ID, or IP
address. It is not the combined file that `run-benchmark.sh` writes. The files
hold object keys, sizes, timings, run IDs, the Region, and per-object samples.

File names are `r<pass>-<series id>.json`, where `r1`, `r2`, and `r3` mean
pass 1, 2, and 3. The `benchmark_run_id` inside a file keeps the name it had
when it was taken, so the file name and the run ID differ in the prefix.

| Files | Count | `benchmark_run_id` inside the file | Raw timestamps (UTC) |
|-------|-------|------------------------------------|----------------------|
| `sequential/r1-*.json` | 8 | `position-r1-<series id>` | 16:17:37 to 16:18:15 |
| `sequential/r2-*.json` | 8 | `position-seq-r2-<series id>` | 16:19:25 to 16:20:04 |
| `sequential/r3-*.json` | 8 | `position-seq-r3-<series id>` | 16:20:17 to 16:20:49 |
| `overlapped/r2-*.json` | 8 | `position-r2-<series id>` | 16:18:28 to 16:19:06 |
| `overlapped/r3-*.json` | 8 | `position-r3-<series id>` | 16:18:33 to 16:19:07 |

## Primary and overlapped sets

Passes 2 and 3 were first started at the same time by mistake, so about two
invocations ran at once on the same S3 Access Points (`overlapped/`). Both
passes were then re-run one invocation at a time, in the planned order
(`sequential/r2-*` and `sequential/r3-*`). The primary analysis uses
`sequential/` only: pass 1 and the two re-runs, 24 invocations. The overlapped
files were analysed afterwards with the same criterion, and they show the same
shape: no slow read outside a multiple of 50, and the 100th read slow in every
invocation that reached it. They are kept because they were measured. The
overlapped set is not a concurrency measurement, and the counts in the
write-up's main tables do not depend on it. All 40 invocations returned without
a function error, and no read or invocation was dropped.

## Design

The question: what ties slow reads to the 50th and the 100th GetObject of an
invocation? The series below were run through the unchanged tool, with event
parameters only (`test` = `get`). The Lambda function creates one boto3 S3
client per invocation, lists the keys once, and then reads sequentially on that
client. In every series one key listing precedes the first read.

| Series | Target | Prefix | `max_keys` | `iterations` | Reads per invocation |
|--------|--------|--------|-----------|--------------|----------------------|
| S0, replay of the pooled shape | S3 Access Points | `benchmark-r4/1k/` | 3 | 40 | 120 |
| S1, one object | S3 Access Points | `benchmark-r4/1k/` | 1 | 40, 49, 50, 100 | 40, 49, 50, 100 |
| S2, long invocation | S3 Access Points | `benchmark-r4/1k/` | 3 | 100 | 300 |
| S3, object size | S3 Access Points | `benchmark-r4/100k/` | 3 | 50 | 150 |
| S4, diagnostic control | Standard S3 bucket | `ctl/1k/` | 3 | 40 or 100 | 120 or 300 |

The run order was fixed before the first invocation and interleaves the series.
Each pass holds one invocation per row of the table below. The order inside a
pass is the row order.

| Order | Pass 1 | Pass 2 | Pass 3 |
|-------|--------|--------|--------|
| 1 | S0a | S0b | S0c |
| 2 | S1-R40a | S1-R40b | S1-R40c |
| 3 | S1-R49a | S1-R49b | S1-R49c |
| 4 | S1-R50a | S1-R50b | S1-R50c |
| 5 | S1-R100a | S1-R100b | S1-R100c |
| 6 | S2a | S2b | S3c |
| 7 | S3a | S3b | S4-120c |
| 8 | S4-120a | S4-120b | S4-300 |

Pass 3 has no S2 invocation. It holds the 300-read control invocation (S4-300)
instead, and its rows 6 to 8 are S3c, S4-120c, and S4-300. S4-120 means a control
invocation of 120 reads.

## Environment

The file system, volume, Region, and Lambda settings are those listed in
[`../2026-10-10/README.md`](../2026-10-10/README.md#environment); that text is
not repeated here. Only the differences and additions are listed below.

- The tool was at `main` commit `d23f381`. `handler.py` is unchanged since
  `6808382`, the commit of the pooled runs. The Lambda settings are those of the
  template at `d23f381`: Python 3.12, 256 MB memory, 300 s timeout, no VPC
  config.
- A new S3 Access Points of the same type (ONTAP, internet origin, file system
  identity UNIX user `root`) was created for this check.
- The test objects are random bytes: `benchmark-r4/1k/obj-{1,2,3}.bin` (1,024
  bytes each) and `benchmark-r4/100k/obj-{1,2,3}.bin` (102,400 bytes each). The
  prefix was empty before the upload, and the listing after the upload equalled
  the planned set.
- The diagnostic control is a temporary standard S3 bucket in the same Region,
  read by the same function. It holds `ctl/1k/obj-{1,2,3}.bin` (1,024 bytes
  each), has Block Public Access on and default encryption, and allows only
  object reads and listing to the benchmark function's role. The bucket is on a
  different service path with no file system. It is a diagnostic control for
  locating a cause, not a like-for-like comparison.
- The volume's tiering policy was AUTO with a 31-day cooling period, read before
  and after the runs with identical values. SSD residency of the freshly written
  objects is inferred from the policy and the object age; no per-tier capacity
  metric was captured.
- The ONTAP version was not re-read. The boto3 and urllib3 versions in the
  Lambda runtime were not recorded, and the cold or warm state of each
  invocation was not recorded.
- The stack, the test objects, the S3 Access Points, and the control bucket were
  deleted afterwards. The file system, the volume, and the pre-existing file
  were not changed.

## Parameters and totals

- Concurrency 1 for `sequential/`. About 2 for `overlapped/` (see above).
- One key listing per invocation, 40 in total. No warm-up reads were excluded.
- GetObject reads, summed from the object sizes and iteration counts in the
  files: 2,787 reads and 48,473,088 bytes in `sequential/`, 1,858 reads and
  32,315,392 bytes in `overlapped/`, and 4,645 reads and 80,788,480 bytes in
  total. Every `iterations` value is 100 or less, so `samples_ms` (capped at 100
  per object) holds every read.
- Request charges were not estimated, and no cost figure is given.

## Slow-read criterion

Flatten `per_object[].samples_ms` in measurement order. A read is slow when it
is at least 3 times the median of that invocation's reads (all objects of the
invocation). The factor 3 is a chosen cut-off. Two reads at the odd multiples of
50 were at 2.67 and 2.79 times the median, just under it. At positions that are
not multiples of 50, the largest ratio in the 24 sequential invocations was 2.69
(the 102nd read of one control invocation); 3 of those 2,739 reads were at 2.0 or
more, all on the control, and the largest on S3 Access Points was 1.94. In the
overlapped set the largest was 2.46 (control) and 1.95 (S3 Access Points). The
ratio is reported at every multiple of 50 and next to it, whether or not it
reaches 3. The pooled write-up used 3 times the class p50 over three runs, so
counts in the two sections are not directly comparable. A "position" is the index of the read within the
invocation, counted from 1 across all objects in order; the key listing is not a
read.

## Recomputing a count

Run this from the directory that holds the files. It prints, for every file in
`sequential/`, the number of reads, the invocation median in milliseconds, and
the slow reads as `(position, ratio to the median)`. Use `overlapped/*.json` to
check the side set.

```bash
python3 - <<'PY'
import glob, json, statistics

for path in sorted(glob.glob("sequential/*.json")):
    with open(path, encoding="utf-8") as fh:
        result = json.load(fh)["result"]
    reads = [s for o in result["per_object"] for s in o["samples_ms"]]
    median = statistics.median(reads)
    slow = [(i + 1, round(r / median, 2)) for i, r in enumerate(reads) if r / median >= 3.0]
    print(path, len(reads), round(median, 1), slow)
PY
```

## Results

All tables in this section are derived from the raw files with the criterion
above. They are not tool output.

### Primary set, by series and position

Each cell is the number of invocations in which the read at that position was
slow, out of the invocations that reached that position.

| Series | Reads per invocation | Invocations | Slow reads by position (slow / reached) | Invocations with any slow read |
|--------|----------------------|-------------|------------------------------------------|--------------------------------|
| S0, 3 x 40 reads, 1 KB | 120 | 3 | 50th 2/3, 100th 3/3 | 3 of 3 |
| S1, 1 object, 40 reads, 1 KB | 40 | 3 | none (50th not reached) | 0 of 3 |
| S1, 1 object, 49 reads, 1 KB | 49 | 3 | none (50th not reached) | 0 of 3 |
| S1, 1 object, 50 reads, 1 KB | 50 | 3 | 50th 3/3 | 3 of 3 |
| S1, 1 object, 100 reads, 1 KB | 100 | 3 | 50th 1/3, 100th 3/3 | 3 of 3 |
| S2, 3 x 100 reads, 1 KB | 300 | 2 | 50th 1/2, 100th 2/2, 150th 2/2, 200th 2/2, 250th 0/2, 300th 2/2 | 2 of 2 |
| S3, 3 x 50 reads, 100 KB | 150 | 3 | 50th 2/3, 100th 3/3, 150th 0/3 | 3 of 3 |
| S4 control, 3 x 40 reads, 1 KB | 120 | 3 | 50th 0/3, 100th 3/3 | 3 of 3 |
| S4 control, 3 x 100 reads, 1 KB | 300 | 1 | 50th 0/1, 100th 1/1, 150th 0/1, 200th 1/1, 250th 0/1, 300th 1/1 | 1 of 1 |

By position and path, over the same 24 invocations (20 on S3 Access Points, 4 on
the standard S3 control):

| Read position | S3 Access Points (20 invocations) | Standard S3 bucket, diagnostic control (4 invocations) |
|---------------|-----------------------------------|--------------------------------------------------------|
| 50th | 9 of 14 | 0 of 4 |
| 100th | 11 of 11 | 4 of 4 |
| 150th | 2 of 5 | 0 of 1 |
| 200th | 2 of 2 | 1 of 1 |
| 250th | 0 of 2 | 0 of 1 |
| 300th | 2 of 2 | 1 of 1 |

In the primary set 32 of 2,787 reads were slow, and all 32 were at the 50th,
100th, 150th, 200th, 250th, or 300th read.

### Overlapped side set, by series and position

| Series | Reads per invocation | Invocations | Slow reads by position (slow / reached) | Invocations with any slow read |
|--------|----------------------|-------------|------------------------------------------|--------------------------------|
| S0, 3 x 40 reads, 1 KB | 120 | 2 | 50th 2/2, 100th 2/2 | 2 of 2 |
| S1, 1 object, 40 reads, 1 KB | 40 | 2 | none (50th not reached) | 0 of 2 |
| S1, 1 object, 49 reads, 1 KB | 49 | 2 | none (50th not reached) | 0 of 2 |
| S1, 1 object, 50 reads, 1 KB | 50 | 2 | 50th 1/2 | 1 of 2 |
| S1, 1 object, 100 reads, 1 KB | 100 | 2 | 50th 0/2, 100th 2/2 | 2 of 2 |
| S2, 3 x 100 reads, 1 KB | 300 | 1 | 50th 1/1, 100th 1/1, 150th 0/1, 200th 1/1, 250th 1/1, 300th 1/1 | 1 of 1 |
| S3, 3 x 50 reads, 100 KB | 150 | 2 | 50th 1/2, 100th 2/2, 150th 1/2 | 2 of 2 |
| S4 control, 3 x 40 reads, 1 KB | 120 | 2 | 50th 0/2, 100th 2/2 | 2 of 2 |
| S4 control, 3 x 100 reads, 1 KB | 300 | 1 | 50th 0/1, 100th 1/1, 150th 0/1, 200th 1/1, 250th 0/1, 300th 1/1 | 1 of 1 |

By position and path, over the same 16 invocations (13 on S3 Access Points, 3 on
the control): on S3 Access Points the 50th read was slow in 5 of 9, the 100th in
7 of 7, the 150th in 1 of 3, the 200th in 1 of 1, the 250th in 1 of 1, and the
300th in 1 of 1. On the control the 50th was slow in 0 of 3, the 100th in 3 of
3, the 150th in 0 of 1, the 200th in 1 of 1, the 250th in 0 of 1, and the 300th
in 1 of 1. In the overlapped set 21 of 1,858 reads were slow, all at a multiple
of 50. The 250th read was slow in the one overlapped S2 invocation and in neither
of the two primary ones.

### Per invocation

Sorted by directory and file name. "Median" is the invocation median in
milliseconds; "ratio" is the read divided by that median.

| File | Reads | Median (ms) | Slow reads, position (ratio) |
|------|-------|-------------|------------------------------|
| `sequential/r1-S0a.json` | 120 | 43.0 | 50 (3.87), 100 (4.71) |
| `sequential/r1-S1-R100a.json` | 100 | 44.2 | 50 (3.40), 100 (6.36) |
| `sequential/r1-S1-R40a.json` | 40 | 41.7 | none |
| `sequential/r1-S1-R49a.json` | 49 | 40.4 | none |
| `sequential/r1-S1-R50a.json` | 50 | 41.3 | 50 (3.86) |
| `sequential/r1-S2a.json` | 300 | 38.0 | 50 (3.96), 100 (6.68), 150 (4.41), 200 (6.53), 300 (7.59) |
| `sequential/r1-S3a.json` | 150 | 43.6 | 50 (4.80), 100 (6.63) |
| `sequential/r1-S4-120a.json` | 120 | 28.8 | 100 (4.55) |
| `sequential/r2-S0b.json` | 120 | 36.7 | 50 (5.11), 100 (4.54) |
| `sequential/r2-S1-R100b.json` | 100 | 38.5 | 100 (7.13) |
| `sequential/r2-S1-R40b.json` | 40 | 46.0 | none |
| `sequential/r2-S1-R49b.json` | 49 | 37.5 | none |
| `sequential/r2-S1-R50b.json` | 50 | 38.7 | 50 (4.42) |
| `sequential/r2-S2b.json` | 300 | 43.1 | 100 (3.49), 150 (4.38), 200 (6.58), 300 (6.14) |
| `sequential/r2-S3b.json` | 150 | 46.1 | 100 (4.86) |
| `sequential/r2-S4-120b.json` | 120 | 28.6 | 100 (3.42) |
| `sequential/r3-S0c.json` | 120 | 42.6 | 100 (5.06) |
| `sequential/r3-S1-R100c.json` | 100 | 44.2 | 100 (3.60) |
| `sequential/r3-S1-R40c.json` | 40 | 38.7 | none |
| `sequential/r3-S1-R49c.json` | 49 | 39.0 | none |
| `sequential/r3-S1-R50c.json` | 50 | 41.0 | 50 (3.14) |
| `sequential/r3-S3c.json` | 150 | 39.1 | 50 (3.02), 100 (6.40) |
| `sequential/r3-S4-120c.json` | 120 | 29.3 | 100 (3.98) |
| `sequential/r3-S4-300.json` | 300 | 27.9 | 100 (3.98), 200 (4.08), 300 (3.74) |
| `overlapped/r2-S0b.json` | 120 | 40.7 | 50 (4.05), 100 (4.41) |
| `overlapped/r2-S1-R100b.json` | 100 | 40.1 | 100 (5.35) |
| `overlapped/r2-S1-R40b.json` | 40 | 47.1 | none |
| `overlapped/r2-S1-R49b.json` | 49 | 41.1 | none |
| `overlapped/r2-S1-R50b.json` | 50 | 42.1 | 50 (3.65) |
| `overlapped/r2-S2b.json` | 300 | 43.1 | 50 (3.47), 100 (6.55), 200 (7.68), 250 (3.70), 300 (6.06) |
| `overlapped/r2-S3b.json` | 150 | 38.7 | 100 (4.86) |
| `overlapped/r2-S4-120b.json` | 120 | 27.6 | 100 (3.81) |
| `overlapped/r3-S0c.json` | 120 | 47.2 | 50 (4.05), 100 (4.82) |
| `overlapped/r3-S1-R100c.json` | 100 | 50.8 | 100 (6.06) |
| `overlapped/r3-S1-R40c.json` | 40 | 44.9 | none |
| `overlapped/r3-S1-R49c.json` | 49 | 42.8 | none |
| `overlapped/r3-S1-R50c.json` | 50 | 40.4 | none |
| `overlapped/r3-S3c.json` | 150 | 40.1 | 50 (4.99), 100 (3.80), 150 (3.15) |
| `overlapped/r3-S4-120c.json` | 120 | 26.8 | 100 (4.32) |
| `overlapped/r3-S4-300.json` | 300 | 28.1 | 100 (3.96), 200 (3.97), 300 (3.95) |

## How to read the figures

The per-invocation `p50_ms`, `p99_ms`, `mean_ms`, `min_ms`, and `max_ms` that the
tool reports are in each file's `per_size_class`; p90, p95, and the standard
deviation are not repeated here. With 120 pooled samples the nearest-rank p99 is
the second-largest sample, and with fewer than 100 it is the largest.
`throughput_mbps` is per stream, in MiB/s. The standard S3 control is a
diagnostic control on a different service path with no file system; it is not a
like-for-like comparison, and the invocation medians (27.9 to 29.3 ms for the
control, 36.7 to 46.1 ms on S3 Access Points) are listed only to size the slow
reads. These are latencies observed in one environment on one day, not a
service limit.

## Limits

The limits of this check are listed in "Limits of the position check" in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md).
In short: per-position counts range from 1 to 14 invocations, so no rate or
confidence interval is claimed; the check used 1 KB and 100 KB objects, one
environment, and one day; no connection-level observation was made; and the
tool cannot yet separate an invocation-wide request counter from a
connection-wide counter or from the lifetime of the client object.
