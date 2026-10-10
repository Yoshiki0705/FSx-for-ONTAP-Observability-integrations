# S3 Access Points read-latency measurement, 2026-10-10 (raw data)

English only, like the tool directory it belongs to (see
[`../../README.md`](../../README.md)). The bilingual write-up, with the result
tables and the limits of this measurement, is in
[docs/en/s3ap-throughput-benchmark.md](../../../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../../../docs/ja/s3ap-throughput-benchmark.md)).

## Files

Each file is the benchmark Lambda's response for one invocation, unmodified. It
is not the combined file that `run-benchmark.sh` writes. The files hold object
keys, sizes, and timings only. Per-iteration samples were not kept.

| File | Run | Test |
|------|-----|------|
| `bench-list.json` | 1 | ListObjectsV2, 20 iterations |
| `bench-get-1k.json` | 1 | GetObject, 3 objects of 1 KB, 10 reads each |
| `bench-get-100k.json` | 1 | GetObject, 3 objects of 100 KB, 10 reads each |
| `bench-get-1m.json` | 1 | GetObject, 3 objects of 1 MB, 10 reads each |
| `bench-get-5m.json` | 1 | GetObject, 3 objects of 5 MB, 10 reads each |
| `bench-r2-list.json` | 2 | ListObjectsV2, 20 iterations |
| `bench-r2-get-1k.json` | 2 | GetObject, 3 objects of 1 KB, 10 reads each |
| `bench-r2-get-100k.json` | 2 | GetObject, 3 objects of 100 KB, 10 reads each |
| `bench-r2-get-1m.json` | 2 | GetObject, 3 objects of 1 MB, 10 reads each |
| `bench-r2-get-5m.json` | 2 | GetObject, 3 objects of 5 MB, 10 reads each |

## Run IDs

| Run | `benchmark_run_id` | Raw timestamps (UTC) |
|-----|--------------------|----------------------|
| 1 | `bench-s3ap-2026-10-10` | 08:09 to 08:10 |
| 2 | `bench-s3ap-2026-10-10-r2` | 08:29 |

## Environment

Date 2026-10-10, Region ap-northeast-1. An Amazon FSx for NetApp ONTAP file
system of type SINGLE_AZ_1 (first generation) with 1 HA pair, throughput
capacity 128 MBps, and 1024 GiB of SSD. The measurement used one volume of one
SVM (the file system has several SVMs). ONTAP 9.18.1P6, as read on 2026-10-06
through the ONTAP REST API (not re-read on the measurement day). The volume's
tiering policy was AUTO with a 31-day cooling period. The test objects were
written minutes before they were read, so the reads are inferred to have been
served from the SSD (primary) tier; no per-tier capacity metric was captured.
The S3 Access Points type was ONTAP with internet origin and file system
identity UNIX user `root`. The client was the
[benchmark tool](../../README.md), deployed as a Lambda function with 256 MB memory
and a 300 s timeout, outside a VPC. Test objects were random bytes, 3 each of
1 KB, 100 KB, 1 MB, and 5 MB, one prefix per size. ListObjectsV2 listed a
prefix that also held one pre-existing large file, so 13 keys were listed.

## How to read the figures

The class-level `p50_ms` and `p99_ms` in `per_size_class` are the simple mean
of each object's own p50 and p99, not percentiles over pooled samples. The tool
aggregated this way when these files were taken. The aggregation was changed in
a later tool version
([#147](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/147)),
and [`../2026-10-10-pooled/`](../2026-10-10-pooled/README.md) holds a
re-measurement with pooled class percentiles. The class p99 figures in these
files are not comparable with the pooled ones. With 10 reads per object, each
per-object p99 is the maximum of 10 samples; the ListObjectsV2 p99 is the
maximum of 20 samples. `throughput_mbps` is per stream at concurrency 1,
derived from mean latency and object size, in MiB/s. Two runs do not establish
statistical stability. These are latencies observed in this environment on this
date, not a service limit.
