# S3 Access Points read-latency re-measurement with pooled class percentiles, 2026-10-10 (raw data)

English only, like the tool directory it belongs to (see
[`../../README.md`](../../README.md)). The bilingual write-up, with the result
tables and the limits of this measurement, is in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../../../docs/ja/s3ap-throughput-benchmark.md)). The earlier files
from the same day, taken with a tool version that averaged per-object
percentiles, are in [`../2026-10-10/`](../2026-10-10/README.md).

## Files

Each file is the benchmark Lambda's response for one invocation. No value was
edited or masked when the files were added to the repository. It is not the
combined file that `run-benchmark.sh` writes. The files hold object keys, sizes,
timings, run IDs, the Region, and per-object samples.

| File | Pooled run | Test |
|------|------------|------|
| `bench-r1-list.json` | 1 | ListObjectsV2, 120 iterations |
| `bench-r1-get-1k.json` | 1 | GetObject, 3 objects of 1 KB, 40 reads each |
| `bench-r1-get-100k.json` | 1 | GetObject, 3 objects of 100 KB, 40 reads each |
| `bench-r1-get-1m.json` | 1 | GetObject, 3 objects of 1 MB, 40 reads each |
| `bench-r1-get-5m.json` | 1 | GetObject, 3 objects of 5 MB, 40 reads each |
| `bench-r2-list.json` | 2 | ListObjectsV2, 120 iterations |
| `bench-r2-get-1k.json` | 2 | GetObject, 3 objects of 1 KB, 40 reads each |
| `bench-r2-get-100k.json` | 2 | GetObject, 3 objects of 100 KB, 40 reads each |
| `bench-r2-get-1m.json` | 2 | GetObject, 3 objects of 1 MB, 40 reads each |
| `bench-r2-get-5m.json` | 2 | GetObject, 3 objects of 5 MB, 40 reads each |
| `bench-r3-list.json` | 3 | ListObjectsV2, 120 iterations |
| `bench-r3-get-1k.json` | 3 | GetObject, 3 objects of 1 KB, 40 reads each |
| `bench-r3-get-100k.json` | 3 | GetObject, 3 objects of 100 KB, 40 reads each |
| `bench-r3-get-1m.json` | 3 | GetObject, 3 objects of 1 MB, 40 reads each |
| `bench-r3-get-5m.json` | 3 | GetObject, 3 objects of 5 MB, 40 reads each |

## Run IDs

| Run | `benchmark_run_id` | Raw timestamps (UTC) |
|-----|--------------------|----------------------|
| 1 | `bench-s3ap-2026-10-10-pooled-r1` | 14:55:20 to 14:55:57 |
| 2 | `bench-s3ap-2026-10-10-pooled-r2` | 14:56:29 to 14:57:05 |
| 3 | `bench-s3ap-2026-10-10-pooled-r3` | 14:58:49 to 14:59:26 |

The three runs were taken one after another, within about 4 minutes. The files
were produced by the benchmark tool as it was at `main` commit `6808382`.

## Environment

The file system, volume, Region, and Lambda settings (256 MB memory, 300 s
timeout, no VPC config) are those listed in
[`../2026-10-10/README.md`](../2026-10-10/README.md#environment); that text is
not repeated here. Only the differences are listed below.

- A new S3 Access Points of the same type (ONTAP, internet origin, file system
  identity UNIX user `root`) was created for this re-measurement.
- The test objects are random bytes, 3 each of 1,024, 102,400, 1,048,576, and
  5,242,880 bytes, under the prefix `benchmark-r3/` in the sub-prefixes `1k/`,
  `100k/`, `1m/`, and `5m/`. The prefix held exactly these 12 keys, so
  ListObjectsV2 listed 12 keys. The earlier files list 13 because their prefix
  also held a pre-existing 103 MiB file, so the list figures of the two sets
  are not strictly comparable.
- The volume's tiering policy was AUTO with a 31-day cooling period, read before
  and after the runs with identical values. SSD residency of the freshly written
  objects is inferred from the policy and the object age; no per-tier capacity
  metric was captured.
- The ONTAP version was not re-read for this re-measurement.
- The test objects and the S3 Access Points were deleted afterwards. The file
  system, the volume, and the pre-existing file were not changed.

## Parameters

- ListObjectsV2: prefix `benchmark-r3/`, 120 iterations, one series per run.
- GetObject: one invocation per size class, 3 objects, 40 iterations per
  object, so 120 pooled samples per class per run.
- Concurrency 1: each invocation reads sequentially in a single stream.
- Totals: 1,800 reads (360 ListObjectsV2 and 1,440 GetObject), and about
  2.1 GiB read by GetObject (2,302,156,800 bytes, summed from the object sizes
  and iteration counts in the files).

## How to read the figures

`percentile_method` is `"pooled_nearest_rank"` on every `per_size_class` entry.
It marks the class `p50_ms`, `p99_ms`, `mean_ms`, `min_ms`, and `max_ms` as
computed over the pooled samples of all objects in the class, using nearest
rank. Files without this key (the `../2026-10-10/` directory) hold class `p50_ms`,
`p99_ms`, and `mean_ms` values that are the simple mean of the per-object values,
and no class `min_ms` or `max_ms`. `sample_count` is the size of the pool, 120 in
these files.

`per_object[].samples_ms` holds each object's raw latencies in measurement
order, 40 per object here. The tool stores at most 100 samples per object, and
an object's own figures use every iteration. The ListObjectsV2 result stores no
raw samples, so its p99 cannot be recomputed from the file.

With 120 pooled samples, the nearest-rank p99 is the second-largest sample. With
fewer than 100 samples it would be the largest sample. `throughput_mbps` is per
stream at concurrency 1, in MiB/s. Class figures from these files are not
comparable with the class p99 figures in `../2026-10-10/`. Three runs in one
environment on one day do not establish statistical stability. These are
latencies observed in this environment, not a service limit.

## Recomputing a class figure

Run this from the directory that holds the files. It pools the samples of one
get file and prints the sample count, the nearest-rank p99 computed here, and the
`p99_ms` stored in `per_size_class`.

```bash
python3 - <<'PY'
import json, math

with open("bench-r1-get-5m.json") as fh:
    result = json.load(fh)["result"]
samples = sorted(s for o in result["per_object"] for s in o["samples_ms"])
rank = min(len(samples) - 1, math.ceil(0.99 * len(samples)) - 1)
print(len(samples), samples[rank], result["per_size_class"][0]["p99_ms"])
PY
```
