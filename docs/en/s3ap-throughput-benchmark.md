# S3 Access Point Read Throughput Benchmark

🌐 [日本語](../ja/s3ap-throughput-benchmark.md) | **English** (this page)

## Purpose

This document provides a benchmark methodology and reference results for reading Amazon FSx for NetApp ONTAP audit logs via S3 Access Points. Use these results as a **sizing reference, not a service limit**.

The two sections "Measured in this repository (2026-10-10)" and "Pooled re-measurement (2026-10-10)" hold the only S3 Access Points read-latency measurements in this repository. Their raw data is committed under `benchmark/s3ap-throughput/results/2026-10-10/` (averaged class percentiles) and `benchmark/s3ap-throughput/results/2026-10-10-pooled/` (pooled class percentiles). The position check is a subsection group of the second section, and its raw data is committed under `benchmark/s3ap-throughput/results/2026-10-10-position/`. The values under "Reference Results" are transcribed from sibling repositories.

> **Caveat**: Results are specific to the test environment described below. Your throughput will vary based on FSx throughput capacity, object size distribution, network path, concurrency, and workload mix. Always validate in your own environment.

## Test Environment

The table describes the 2026-10-10 measurement. The pooled re-measurement and the position check the same day used the same file system, volume, and Lambda settings; their differences are listed under "Pooled re-measurement (2026-10-10)" and "Position check: question, design, and differences from the pooled runs".

| Parameter | Value |
|-----------|-------|
| Measurement date | 2026-10-10 |
| File system | SINGLE_AZ_1 (first generation), 1 HA pair, 1024 GiB SSD |
| FSx for ONTAP throughput capacity | 128 MBps |
| SVM and volume | One volume of one SVM (the file system has several SVMs) |
| Volume tiering policy | AUTO, cooling period 31 days (read from the FSx API on 2026-10-10) |
| Data tier at read time | SSD (primary tier). Inferred from the policy and the age of the objects (written minutes before the reads, cooling period 31 days). Not verified from a per-tier capacity metric; none was taken during the run |
| ONTAP version | 9.18.1P6, as read on 2026-10-06 through the ONTAP REST API on this file system (not re-read on the measurement day) |
| S3 Access Points type | ONTAP, internet origin, file system identity is UNIX user `root` |
| Client | The tool in `benchmark/s3ap-throughput/`, deployed as a Lambda function |
| Lambda memory and timeout | 256 MB, 300 s |
| Lambda placement | Outside VPC (no VPC config) |
| AWS Region | ap-northeast-1 |
| Test objects | Random bytes, 3 objects each of 1 KB, 100 KB, 1 MB, and 5 MB, one prefix per size |
| Benchmark run IDs | `bench-s3ap-2026-10-10` (run 1), `bench-s3ap-2026-10-10-r2` (run 2) |
| Pooled re-measurement run IDs | `bench-s3ap-2026-10-10-pooled-r1`, `bench-s3ap-2026-10-10-pooled-r2`, `bench-s3ap-2026-10-10-pooled-r3` |
| Position check run IDs | `position-r1-<series>`, `position-seq-r2-<series>`, `position-seq-r3-<series>` (sequential, counted); `position-r2-<series>`, `position-r3-<series>` (overlapped side set); the series IDs are listed in the results README |

## Methodology

### Test Script

The script below illustrates the method. The version of the tool in `benchmark/s3ap-throughput/` that produced the 2026-10-10 figures differs from it in two ways that affect how the figures read. It computes p50 and p99 by nearest rank instead of `statistics.median` and `int(iterations * 0.99)` indexing, and it returns per-size-class summaries in addition to per-object results.

```python
"""S3 AP throughput benchmark for FSx for ONTAP audit logs.

Run from Lambda or EC2 in the same region as the S3 Access Point.
"""

import time
import statistics
import boto3

s3 = boto3.client("s3")

S3_AP_ARN = "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
PREFIX = "audit/svm-prod-01/2026/05/"


def benchmark_list_objects(iterations: int = 10) -> dict:
    """Measure ListObjectsV2 latency."""
    latencies = []
    for _ in range(iterations):
        start = time.perf_counter()
        s3.list_objects_v2(Bucket=S3_AP_ARN, Prefix=PREFIX, MaxKeys=100)
        latencies.append((time.perf_counter() - start) * 1000)
    return {
        "operation": "ListObjectsV2",
        "iterations": iterations,
        "p50_ms": statistics.median(latencies),
        "p99_ms": sorted(latencies)[int(iterations * 0.99)],
        "mean_ms": statistics.mean(latencies),
    }


def benchmark_get_object(keys: list[str], iterations: int = 5) -> dict:
    """Measure GetObject latency and throughput by object size."""
    results = []
    for key in keys:
        latencies = []
        sizes = []
        for _ in range(iterations):
            start = time.perf_counter()
            resp = s3.get_object(Bucket=S3_AP_ARN, Key=key)
            body = resp["Body"].read()
            latencies.append((time.perf_counter() - start) * 1000)
            sizes.append(len(body))
        avg_size = statistics.mean(sizes)
        avg_latency = statistics.mean(latencies)
        throughput_mbps = (avg_size / 1024 / 1024) / (avg_latency / 1000) if avg_latency > 0 else 0
        results.append({
            "key": key,
            "size_bytes": int(avg_size),
            "p50_ms": statistics.median(latencies),
            "p99_ms": sorted(latencies)[min(int(iterations * 0.99), iterations - 1)],
            "throughput_mbps": round(throughput_mbps, 2),
        })
    return results
```

### Object Size Categories

| Category | Typical Size | Description |
|----------|-------------|-------------|
| Small | 1-10 KB | Single audit event (JSON) |
| Medium | 100 KB - 1 MB | Rotated audit log file (typical) |
| Large | 1-5 MB | High-activity period log file |

## Measured in this repository (2026-10-10)

> **Measurement note**: This section is the earlier 2026-10-10 run. It used the averaging formula, so its p99 figures are not comparable with the p99 figures in "Pooled re-measurement (2026-10-10)", which are nearest-rank values over 120 pooled samples. Each class p99 in this section is the mean of three per-object p99 values, and each per-object p99 is the largest of 10 samples. For tail latency, take the figures from the pooled section and read them as indicative.

Evidence tier: `verified`, for one statement only. These latencies were observed in the environment under "Test Environment" on 2026-10-10. The tier does not cover other file systems, concurrency levels, network paths, or dates. Two runs were taken on the same day, about 20 minutes apart (raw timestamps 08:09 UTC and 08:29 UTC), so the figures show an observed range and not a stable value.

The volume's tiering policy was AUTO with a 31-day cooling period, and the test objects were written minutes before they were read. The reads are therefore inferred to have been served from the SSD (primary) tier. This is an inference from the policy and the object age. No per-tier capacity metric was captured during the run.

### Aggregation method

Each object was read 10 times in sequence (concurrency 1). ListObjectsV2 was called 20 times with no MaxKeys override, so the tool's default of 100 applied. The version of the tool that took these measurements computed nearest-rank p50 and p99 and the mean per object. The class-level p50 and p99 in the tables below are the simple mean of the three objects' own p50 and p99 values. They are not percentiles over the 30 pooled samples. Later versions of the tool compute the class figures over the pooled samples and mark each class with `percentile_method: "pooled_nearest_rank"` ([#147](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/147)); the raw files in `results/2026-10-10/` have no such key. The pooled re-measurement below used such a version. Class figures from a later version are therefore not directly comparable with the tables in this section. Throughput for one object is derived from that object's mean latency and size, and the class figure is the mean of the three. MB/s means MiB/s here (size divided by 1,048,576 bytes), as in the tool.

### Results of run 1 and run 2

Run 1 (`bench-s3ap-2026-10-10`, averaged class percentiles):

| Operation | p50 (ms) | p99 (ms) | Mean (ms) | Mean per-stream throughput (MB/s) |
|-----------|---------|---------|-----------|------------------------------------|
| ListObjectsV2 (20 iterations, 13 keys) | 29.788 | 278.046 | 44.2 | n/a |
| GetObject 1 KB | 40.577 | 43.14 | 40.753 | 0.02 |
| GetObject 100 KB | 49.89 | 58.157 | 50.798 | 1.93 |
| GetObject 1 MB | 60.388 | 74.535 | 62.883 | 15.94 |
| GetObject 5 MB | 119.753 | 191.688 | 131.909 | 38.13 |

Run 2 (`bench-s3ap-2026-10-10-r2`, averaged class percentiles):

| Operation | p50 (ms) | p99 (ms) | Mean (ms) | Mean per-stream throughput (MB/s) |
|-----------|---------|---------|-----------|------------------------------------|
| ListObjectsV2 (20 iterations, 13 keys) | 27.415 | 402.188 | 48.501 | n/a |
| GetObject 1 KB | 40.987 | 44.969 | 41.473 | 0.02 |
| GetObject 100 KB | 38.267 | 49.412 | 39.68 | 2.46 |
| GetObject 1 MB | 48.464 | 69.393 | 51.494 | 19.53 |
| GetObject 5 MB | 106.884 | 444.951 | 154.394 | 34.85 |

Between the two runs, p50 changed by 0.4 to 12.9 ms (largest change: 5 MB, 119.753 to 106.884 ms). p99 changed by 1.8 to 253.3 ms (ListObjectsV2 278.046 to 402.188 ms; 5 MB 191.688 to 444.951 ms).

### Limits of this measurement

- The p99 values are indicative only. Each per-object p99 is the maximum of 10 samples, and the ListObjectsV2 p99 is the maximum of 20 samples. In run 2, the 5 MB class p99 of 444.951 ms is the mean of three per-object maxima (152.683, 361.481, and 820.689 ms), so one slow read in one object sets most of it. The raw files keep summaries, not per-iteration samples, so the cause of a slow sample cannot be checked afterwards.
- Two runs do not establish statistical stability. No variance, confidence interval, or stable p99 is claimed.
- The ListObjectsV2 runs listed a prefix that held one pre-existing large file (about 103 MiB) in addition to the 12 test objects, so 13 keys were listed. The GetObject runs used one prefix per size and were not affected. A 13-key listing says nothing about directories with more keys.
- Throughput is per stream at concurrency 1, derived from mean latency and object size. It is not the file system's throughput limit and says nothing about concurrent reads. At 1 KB the figure reflects request latency, not bandwidth.
- The tool does not separate the Lambda cold-start effect. The first ListObjectsV2 sample, and the first request of each invocation, may include connection setup. The stored summaries do not show whether the maximum was the first call.
- Not covered: concurrency above 1, a VPC-attached Lambda or a NAT path (the latency differences in "Network Path" below were not derived from this measurement), other Lambda memory sizes, other throughput capacities, second-generation or multi-HA-pair file systems, and larger directories.
- Not covered: volumes with the ALL tiering policy, or data that has already been tiered to the capacity pool. Reads served from the capacity pool go to object storage and are expected to differ in latency and in cost; this has not been measured here.

## Pooled re-measurement (2026-10-10)

Evidence tier: `verified`, for one statement only. These latencies were observed in the environment under "Test Environment" on 2026-10-10, in three runs between 14:55 and 14:59 UTC, later the same day than the earlier run (08:09 to 08:29 UTC), with a version of the tool that computes class percentiles over pooled samples. The tier does not cover other file systems, Regions, concurrency levels, network paths, or dates. Tables show tool output unless the heading says derived. Derived values were computed from the committed raw files and are not tool output. This is a sample run in one environment: a sizing reference, not a production estimate and not a service limit.

### Environment and differences from the earlier run

The file system, volume, Region, and Lambda settings are those under "Test Environment" (SINGLE_AZ_1, 128 MBps throughput capacity, 1 HA pair, 1024 GiB; the client is a 256 MB Lambda outside a VPC in the same Region). The volume's tiering policy was AUTO with a 31-day cooling period, read before and after the runs with identical values. SSD residency is inferred from the policy and the object age; no per-tier capacity metric was captured. The ONTAP version was not re-read for this re-measurement. The differences from the earlier run are:

| Item | Earlier run (averaged) | Pooled re-measurement |
|------|------------------------|-----------------------|
| Tool version | The version that averaged per-object percentiles per class | The tool at `main` commit `6808382` ([#147](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/147)) |
| Runs | 2, about 20 minutes apart | 3, within about 4 minutes |
| Reads per object | 10 | 40 |
| Samples per class | 30, not kept in the files | 120, kept as `samples_ms` |
| ListObjectsV2 | 20 iterations, 13 keys | 120 iterations, 12 keys |
| Test objects | Random bytes, 3 each of 1 KB, 100 KB, 1 MB, and 5 MB, one prefix per size; the ListObjectsV2 prefix also held a pre-existing 103 MiB file | Same layout under a new prefix that held only the 12 test objects |
| S3 Access Points | Type ONTAP, internet origin, UNIX user `root` | Same type; a new one created for this re-measurement and deleted afterwards |

The ListObjectsV2 figures of the two runs are therefore not strictly comparable (12 keys against 13, and 120 iterations against 20).

### Pooled aggregation and sampling

- Each class value is a nearest-rank figure over the pooled samples: 3 objects x 40 reads = 120 samples per class per run. Each class entry carries `percentile_method: "pooled_nearest_rank"` and `sample_count: 120`.
- Each object's raw latencies are stored in `per_object[].samples_ms` (40 per object).
- ListObjectsV2 is one series of 120 calls per run. Its result keeps no raw samples, so its p99 cannot be recomputed from the file.
- With 120 samples, the nearest-rank p99 is the second-largest sample.
- Reads were sequential in a single stream (concurrency 1). Throughput is per stream, not the file system limit. MB/s means MiB/s.
- In total there were 1,800 reads (360 ListObjectsV2 and 1,440 GetObject), and GetObject read about 2.1 GiB.

### Results of pooled runs 1 to 3

Each GetObject class value is a nearest-rank figure over 120 pooled samples (3 objects x 40 reads). ListObjectsV2 is one series of 120 calls. Max is the largest single read.

Pooled run 1 (`bench-s3ap-2026-10-10-pooled-r1`):

| Operation | p50 (ms) | p99 (ms) | Mean (ms) | Max (ms) | Mean per-stream throughput (MB/s) |
|-----------|---------|---------|-----------|----------|------------------------------------|
| ListObjectsV2 (120 iterations, 12 keys) | 28.11 | 267.198 | 34.352 | 349.378 | n/a |
| GetObject 1 KB | 44.008 | 229.291 | 46.56 | 235.698 | 0.02 |
| GetObject 100 KB | 44.939 | 169.818 | 48.671 | 244.485 | 2.01 |
| GetObject 1 MB | 60.024 | 291.514 | 65.402 | 376.875 | 15.36 |
| GetObject 5 MB | 120.898 | 263.822 | 129.447 | 545.569 | 38.63 |

Pooled run 2 (`bench-s3ap-2026-10-10-pooled-r2`):

| Operation | p50 (ms) | p99 (ms) | Mean (ms) | Max (ms) | Mean per-stream throughput (MB/s) |
|-----------|---------|---------|-----------|----------|------------------------------------|
| ListObjectsV2 (120 iterations, 12 keys) | 28.665 | 285.43 | 35.169 | 394.441 | n/a |
| GetObject 1 KB | 41.542 | 82.878 | 43.956 | 275.072 | 0.02 |
| GetObject 100 KB | 44.759 | 218.75 | 48.397 | 242.08 | 2.03 |
| GetObject 1 MB | 50.072 | 176.486 | 53.637 | 291.189 | 18.69 |
| GetObject 5 MB | 120.4 | 410.531 | 135.077 | 582.888 | 37.02 |

Pooled run 3 (`bench-s3ap-2026-10-10-pooled-r3`):

| Operation | p50 (ms) | p99 (ms) | Mean (ms) | Max (ms) | Mean per-stream throughput (MB/s) |
|-----------|---------|---------|-----------|----------|------------------------------------|
| ListObjectsV2 (120 iterations, 12 keys) | 28.58 | 234.278 | 33.887 | 310.04 | n/a |
| GetObject 1 KB | 44.786 | 208.932 | 48.404 | 318.009 | 0.02 |
| GetObject 100 KB | 38.132 | 59.016 | 42.305 | 328.127 | 2.34 |
| GetObject 1 MB | 51.964 | 203.847 | 56.805 | 315.515 | 17.62 |
| GetObject 5 MB | 118.597 | 501.195 | 135.154 | 518.805 | 37.06 |

### Class figures across the three runs (derived)

These figures are derived from the per-object samples of the three runs. They are not tool output. The pool is 3 runs x 3 objects x 40 reads = 360 samples per class, processed by nearest rank, so the p99 is the 4th-largest of 360. ListObjectsV2 is excluded because its result keeps no samples. A slow sample here is a sample at or above 3 times the class p50 in this table. The factor 3 is a chosen cut-off: the 5 MB run 2 sample at the 50th read (358.623 ms) is 1.2 ms below its threshold and is not counted.

| Size class | Samples | p50 (ms) | p99 (ms) | Max (ms) | Samples at or above 3 x p50 | Range of those samples (ms) |
|------------|---------|---------|---------|----------|------------------------------|------------------------------|
| 1 KB | 360 | 43.486 | 229.291 | 318.009 | 5 of 360 (1.4 %) | 208.932 to 318.009 |
| 100 KB | 360 | 43.833 | 218.75 | 328.127 | 5 of 360 (1.4 %) | 169.818 to 328.127 |
| 1 MB | 360 | 55.313 | 291.189 | 376.875 | 6 of 360 (1.7 %) | 176.486 to 376.875 |
| 5 MB | 360 | 119.944 | 501.195 | 582.888 | 7 of 360 (1.9 %) | 389.651 to 582.888 |

The 3 x p50 thresholds are 130.458, 131.499, 165.939, and 359.832 ms for 1 KB, 100 KB, 1 MB, and 5 MB.

The slow reads are concentrated at two positions in these 120-read invocations. This is derived from `samples_ms`, and these three runs do not isolate the cause. In all 12 GetObject invocations (4 size classes x 3 runs), the 100th read (object 3, read 20) took at least 3.8 times the class p50 in the table above (169.818 to 582.888 ms), and the 50th read (object 2, read 10) did so in 7 of 12. These two positions hold 19 of the 23 slow reads. The other 1,416 reads include 4 (0.3 %), all in the 5 MB class. One key listing precedes the first read of each invocation. In the position check taken later the same day (1 KB and 100 KB objects only), slow reads occurred only at multiples of 50, and the cause was not isolated there either.

### Comparison with the averaging formula (derived)

The earlier run reported each class p99 as the mean of three per-object p99 values (the averaging formula). The table applies the same formula to the per-object p99 values in the pooled-run files (40 samples per object) and sets it next to the pooled class p99 that the tool reported. The middle column is derived; the right column is tool output.

| Size class | Earlier run, averaged, 10 samples per object (run 1 / run 2) | Same formula on the pooled-run files, 40 samples per object (pooled run 1 / 2 / 3) | Pooled class p99, 120 samples (pooled run 1 / 2 / 3) |
|------------|------------------------------------------------|----------------------------------------------------|------------------------------------------|
| 1 KB | 43.14 / 44.969 | 172.5 / 130.2 / 199.1 | 229.291 / 82.878 / 208.932 |
| 100 KB | 58.157 / 49.412 | 156.9 / 171.7 / 146.1 | 169.818 / 218.75 / 59.016 |
| 1 MB | 74.535 / 69.393 | 251.7 / 191.2 / 201.2 | 291.514 / 176.486 / 203.847 |
| 5 MB | 191.688 / 444.951 | 336.9 / 450.7 / 399.9 | 263.822 / 410.531 / 501.195 |

The formula on 40-sample objects gives 130.2 to 251.7 ms for 1 KB to 1 MB, where the earlier run reported 43.14 to 74.535 ms from 10-sample objects. For 5 MB the two are in the same range (191.688 and 444.951 ms earlier; 336.9, 450.7, and 399.9 ms on the pooled-run files). A difference between the earlier p99 and the pooled p99 can come from the method, the sample count per object (10 earlier, 40 now), and the reads per invocation (30 earlier, 120 now); by itself it is not evidence that the file system changed.

Hypothesis, partly tested by the position check and not a re-test of the earlier run: each earlier invocation read 3 objects x 10 times = 30 times, so it never reached the 50th or the 100th read. If slow reads are tied to those positions in the invocation rather than to reads in general, a 30-read invocation would hold few of them. That would be consistent with the p99 values of 43 to 75 ms for 1 KB to 1 MB in the earlier run, and with 10-read objects that mostly held no slow read. The position check is consistent with this reading for invocations of similar length: invocations of 40 and 49 reads had no slow read (0 of 6), and no invocation had a slow read before its 50th read. It did not replay the earlier shape (it had no 30-read invocation, read 1 KB and 100 KB objects only, used a new S3 Access Points, and ran at a later time of day), and the earlier raw files keep no samples, so the earlier run itself cannot be checked. The hypothesis does not account for the 5 MB class: within its 30 reads, earlier run 2 had per-object largest values of 361.481 and 820.689 ms, and the position check did not read 5 MB objects.

### What the three runs support

- The class p50 varied by 2 to 20 % between runs. The spread within each class (largest class p50 divided by smallest, minus 1) is 2 % for 5 MB (118.597 to 120.898 ms), 8 % for 1 KB (41.542 to 44.786 ms), 18 % for 100 KB (38.132 to 44.939 ms), and 20 % for 1 MB (50.072 to 60.024 ms). With three runs, this statement is limited to these runs.
- A small share of reads took at least 3 times the class p50, in every size class. Of 360 samples, 5 (1.4 %) for 1 KB and 100 KB, 6 (1.7 %) for 1 MB, and 7 (1.9 %) for 5 MB. The ranges per class are in the derived table. These reads are not spread evenly: 19 of the 23 are the 50th or the 100th GetObject of an invocation (the 100th in all 12 invocations, the 50th in 7 of 12), and the other 1,416 reads include 4. These three runs do not isolate the cause of the slow reads or of the positional pattern: the client network path, the S3 Access Points front end, ONTAP, and Lambda were not separated in them. The position check below separates some candidates for the 100th-read event and leaves others open.
- A single run's class p99 is indicative only. With 120 pooled samples the class p99 is the second-largest sample, and it moved between runs. The largest p99 divided by the smallest is 1.7 for 1 MB (176.486 to 291.514 ms), 1.9 for 5 MB (263.822 to 501.195 ms), 2.8 for 1 KB (82.878 to 229.291 ms), and 3.7 for 100 KB (59.016 to 218.75 ms). The across-run p99 (4th-largest of 360) rests on a larger pool, but in every class that value is itself a 50th-read or 100th-read sample, and it is derived from three runs in one environment on one day.
- Across the three ListObjectsV2 series (12 keys), p50 ranged from 28.11 to 28.665 ms and p99 from 234.278 to 285.43 ms (the second-largest of 120).

### Limits of the pooled re-measurement

- Not covered: concurrency above 1, other file systems, other Regions, VPC-origin S3 Access Points, other Lambda memory sizes, and data not on SSD (capacity pool reads, tiering policy ALL). The overlapped side set of the position check ran at about concurrency 2 by mistake; it is not a designed concurrency measurement.
- Not covered: other times of day. Only 08:09 to 08:29 UTC, 14:55 to 14:59 UTC, and (in the position check) 16:17 to 16:20 UTC on 2026-10-10 were sampled, and the sets use different aggregation or designs, so no time-of-day comparison is made.
- Throughput is per stream at concurrency 1. It is not the file system's throughput limit.
- That the reads were served from SSD is inferred from the tiering policy and was not measured.
- The tool does not separate the Lambda cold-start effect.
- The cause of the concentration of slow reads at the 50th and 100th read of an invocation is not isolated. The position check found the 100th-read event on a standard S3 bucket too and found it again at the 200th and 300th read. It did not separate an invocation-wide request counter from a connection-wide counter or from the lifetime of the client object, and it did not isolate the cause of the 50th-read events (see "Position check: what was and was not separated").
- The ListObjectsV2 p99 cannot be recomputed, because its result keeps no samples.
- Three runs do not establish statistical stability. No confidence interval is claimed.
- Request charges for this run were not estimated, and no cost figure is given.

### Position check: question, design, and differences from the pooled runs

Evidence tier: `verified`, for one statement only. These latencies were observed in the environment under "Test Environment" on 2026-10-10, in 40 invocations between 16:17 and 16:20 UTC, later the same day than the pooled re-measurement (14:55 to 14:59 UTC). The tier does not cover other file systems, Regions, clients, connection handling, concurrency levels, network paths, or dates. This is a sample run in one environment: a sizing reference, not a production estimate and not a service limit. Every count, ratio, and difference in this group is derived from `samples_ms` in the committed raw files under `benchmark/s3ap-throughput/results/2026-10-10-position/`; none of it is tool output.

The pooled re-measurement found that 19 of the 23 slow reads were the 50th or the 100th GetObject of an invocation. This check asks what ties slow reads to those positions. The predictions, the run order, and the slow-read criterion were written down before the first invocation.

The file system, volume, Region, and Lambda memory and VPC settings are those under "Test Environment" (256 MB, no VPC). The volume's tiering policy was AUTO with a 31-day cooling period, read before and after the runs with identical values. SSD residency is inferred from the policy and the object age; no per-tier capacity metric was captured. The ONTAP version was not re-read. The differences from the pooled re-measurement are:

| Item | Pooled re-measurement | Position check |
|------|-----------------------|----------------|
| Tool version | The tool at `main` commit `6808382` | The tool at `main` commit `d23f381`; `handler.py` is unchanged since `6808382` |
| Client | One boto3 S3 client per invocation, one key listing, then sequential GetObject calls at concurrency 1 | The same |
| S3 Access Points | A new one created for the re-measurement | A new one of the same type (ONTAP, internet origin, UNIX user `root`), created for this check and deleted afterwards |
| Test objects | 3 each of 1 KB, 100 KB, 1 MB, and 5 MB | 3 of 1 KB and 3 of 100 KB under a new prefix |
| Reads per invocation | 120 | 40 to 300, set per series |
| Diagnostic control | None | A temporary standard S3 bucket in the same Region, read by the same function: 3 objects of 1 KB, Block Public Access on, default encryption |

The series, each run as sequential invocations:

| Series | Target | Object size | Objects x reads per object | Reads per invocation | Sequential invocations |
|--------|--------|-------------|----------------------------|----------------------|------------------------|
| S0, replay of the pooled shape | S3 Access Points | 1 KB | 3 x 40 | 120 | 3 |
| S1, one object | S3 Access Points | 1 KB | 1 x 40, 1 x 49, 1 x 50, 1 x 100 | 40, 49, 50, 100 | 3 of each length (12) |
| S2, long invocation | S3 Access Points | 1 KB | 3 x 100 | 300 | 2 |
| S3, object size | S3 Access Points | 100 KB | 3 x 50 | 150 | 3 |
| S4, diagnostic control | Standard S3 bucket | 1 KB | 3 x 40, and 3 x 100 | 120, and 300 | 3, and 1 |

The invocations ran in three passes. The order inside a pass was fixed before the first invocation and interleaves the series. Each pass holds one invocation of S0, of each S1 length, of S3, and of S4 at 120 reads. Passes 1 and 2 also hold S2; pass 3 holds the 300-read S4 invocation instead. A read is slow when it is at least 3 times the median of that invocation's reads (all objects of the invocation, in measurement order). The pooled section used 3 times the class p50 over three runs, so counts in the two sections are not directly comparable. The ratio is recorded at every multiple of 50 whether or not it reaches 3.

> **Measurement note**: Passes 2 and 3 were first started at the same time by mistake, so about two invocations ran at once on the same S3 Access Points (concurrency about 2). Both passes were then re-run one invocation at a time in the planned order. The 24 sequential invocations (pass 1 and the two re-runs) are the primary set, and they are the only ones counted below. The 16 overlapped invocations are kept as a labelled side set; analysed afterwards with the same criterion, they show the same shape. All 40 invocations returned without a function error, and no read or invocation was dropped.

### Position check: slow reads occurred only at multiples of 50

Counts are from the 24 sequential invocations (20 on FSx for ONTAP S3 Access Points, 4 on the standard S3 control). Each cell is the number of invocations in which the read at that position was slow, out of the invocations that reached that position. In these invocations 32 of 2,787 reads were slow, and all 32 were at the 50th, 100th, 150th, 200th, 250th, or 300th read.

| Read position | FSx for ONTAP S3 Access Points (20 invocations) | Standard S3 bucket, diagnostic control (4 invocations) |
|---------------|--------------------------------------------------|--------------------------------------------------------|
| 50th | 9 of 14 | 0 of 4 |
| 100th | 11 of 11 | 4 of 4 |
| 150th | 2 of 5 | 0 of 1 |
| 200th | 2 of 2 | 1 of 1 |
| 250th | 0 of 2 | 0 of 1 |
| 300th | 2 of 2 | 1 of 1 |

- No read at a position that is not a multiple of 50 was slow, in any of the 24 sequential invocations or the 16 overlapped ones. The pooled runs had 4 slow reads outside the 50th and 100th read, all in the 5 MB class; this check read 1 KB and 100 KB only, so it neither confirms nor contradicts those 4.
- Invocations of 40 and 49 reads had no slow read (0 of 6). Invocations of 50 reads had exactly one, at the 50th read (3 of 3).
- The 100th, 200th, and 300th reads were slow in every invocation that reached them, on S3 Access Points (11 of 11, 2 of 2, 2 of 2) and on the standard S3 control (4 of 4, 1 of 1, 1 of 1). The repeating event at multiples of 100 is therefore not specific to S3 Access Points.
- The 50th, 150th, and 250th reads were intermittent on S3 Access Points (11 of 21 reads at those positions) and were not seen on the control (0 of 6). Of the 10 reads at those positions that were not slow, 8 were at most 1.68 times the invocation median and 2 were 2.67 and 2.79 times, just under the chosen cut-off of 3. Six control reads do not establish absence. In the overlapped side set the 250th read was slow in its one invocation, so absence at the 250th read is not established either.
- The 100 KB invocations (3) had slow reads at the same positions as the 1 KB ones: the 100th in 3 of 3, the 50th in 2 of 3, the 150th in 0 of 3, and none elsewhere. Three invocations do not establish equal rates.
- The first read of an invocation was not slow: its ratio to the invocation median had a median of 1.14 and a largest value of 1.90. Reads next to the multiples of 50 (the 49th and 51st, the 99th and 101st, and so on) were near the median: median ratio 1.02, largest 1.44.
- At the 100th, 200th, and 300th read the slow read was 3.49 to 7.59 times the invocation median on S3 Access Points (15 reads; +107.5 to +250.8 ms over invocation medians of 36.7 to 46.1 ms) and 3.42 to 4.55 times on the control (6 reads; +69.3 to +102.0 ms over medians of 27.9 to 29.3 ms). "+" means the read minus the invocation median.

> **Measurement note**: The standard S3 bucket is a diagnostic control for locating a cause. It is a different service path with no file system, so its medians (27.9 to 29.3 ms per invocation, and 36.7 to 46.1 ms on S3 Access Points) are listed to size the slow reads. They are not a like-for-like comparison, and no claim is made about either path's latency in general.

### Position check: predictions scored, and the one that did not hold as stated

The predictions were written before the first invocation. "Held" is judged against the primary set (the 24 sequential invocations).

| Prediction | Result |
|------------|--------|
| A slow read at every multiple of 50 that the invocation reaches | Did not hold as stated. It held for multiples of 100 (S3 Access Points 11 of 11, 2 of 2, 2 of 2; control 4 of 4, 1 of 1, 1 of 1) and failed for the odd multiples of 50 (the 50th, 150th, and 250th read: 11 of 21 on S3 Access Points, 0 of 6 on the control) |
| No slow read in invocations of 40 or 49 reads | Held: 0 of 6 |
| A slow read at the 50th read of a 50-read invocation | Held: 3 of 3 |
| No slow read at a position that is not a multiple of 50 | Held: none in the 24 sequential or the 16 overlapped invocations |
| The replay (S0) reproduces slow reads at the 50th and 100th read | Held: 100th read 3 of 3, 50th read 2 of 3 |
| At least one slow read at the 150th to 300th read of a 300-read invocation (the pre-registered falsifier was its absence) | Held. The falsifier did not occur: the data contradict "no slow read at the 150th to 300th read". On S3 Access Points the 150th, 200th, and 300th reads were slow in both 300-read invocations (the 250th in neither). The control's one 300-read invocation had the 200th and 300th reads slow and the 150th and 250th not. The reading "only two events per invocation (nothing after the 100th)" is not supported |
| The positions are the same at 100 KB as at 1 KB | Held for the positions, in 3 invocations |
| A standard S3 bucket read by the same function shows the same pattern if the cause is not specific to S3 Access Points | Partly: the multiples of 100 appeared on the control; the odd multiples of 50 did not (0 of 6) |

### Position check: a third-party report consistent with the 100th-read shape

Hypothesis, not checked at the connection level here. A public issue for the AWS SDK for Go v1 ([aws/aws-sdk-go issue 2825](https://github.com/aws/aws-sdk-go/issues/2825), 2019) reports that, for PutObject and UploadPart calls against standard S3, one HTTP connection was reused for 100 requests and the 101st request opened a new connection. The tool here lists keys once before the reads on the same client, so the key listing is request 1 and the 100th GetObject is request 101. If the same happens here, the repeating slow reads at the 100th, 200th, and 300th read would be the first request on a new connection. That reading is consistent with the shape and is not verified: it is a user report for a different SDK and different operations, it is not AWS documentation, no connection or packet trace was taken in this check, and the boto3 and urllib3 versions in the Lambda runtime were not recorded. The report does not address the 50th, 150th, and 250th-read events on S3 Access Points. No source for them was found; the search was not exhaustive.

### Position check: what was and was not separated

Separated by this series:

- The multiples-of-100 event is not specific to S3 Access Points: the standard S3 control, read by the same function in the same Region, showed it in every invocation that reached it.
- It is not tied to an object boundary. In the 120-read series (S0 and the control at 120 reads) the 100th read is the 20th read of object 3, and it was slow in 6 of 6 invocations; the last read of each object (40th, 80th, 120th) and the first read of objects 2 and 3 were not slow.
- It repeats: slow reads occurred at the 200th and 300th read, not only at the 100th.
- Slow reads did not occur before the 50th read of any invocation, and the first read of an invocation was not slow.
- The positions were the same at 1 KB and 100 KB (3 invocations at 100 KB).
- The overlapped side set (about concurrency 2) showed the same positions. It was an unplanned overlap and is not a concurrency measurement.

Not separated:

- An invocation-wide request counter, a connection-wide request counter, and the lifetime of the client object. In this tool they coincide (one client, one connection, sequential reads). Separating them needs a tool option that recreates the client or the connection every N reads. That option has not been built.
- The cause of the 50th, 150th, and 250th-read events on S3 Access Points. The client network path, the S3 Access Points front end, ONTAP, and Lambda were not separated. The control did not show these events in 6 reads, which is too few to rule them out on the standard S3 path.
- Whether the connection was in fact replaced at the 100th request. No connection-level observation was made.

### Position check: practical reading and its bounds

In this tool's invocations of 100 or more reads (one client object, sequential reads, connection reuse not observed), about one read in 100 took 3.4 to 7.6 times the invocation median. On S3 Access Points it was 3.49 to 7.59 times (+107.5 to +250.8 ms over invocation medians of 36.7 to 46.1 ms; 15 reads), and on the standard S3 control 3.42 to 4.55 times (+69.3 to +102.0 ms; 6 reads). On S3 Access Points a further 11 of 21 reads at the 50th, 150th, and 250th positions were slow. These values come from this tool: one client per invocation, concurrency 1, 1 KB and 100 KB objects, a 256 MB Lambda outside a VPC, one environment on one day. They are not extrapolated to other clients, other connection handling, or concurrency above 1. A class p99 computed from fewer than 100 reads in one invocation cannot contain the 100th-read event, because such an invocation never reaches that position. The earlier statement stands that a single run's class p99 is indicative only.

### Limits of the position check

- Per-position counts range from 1 to 14 invocations (S3 Access Points: 14 at the 50th read, 11 at the 100th, 5 at the 150th, 2 at each of the 200th to 300th; control: 4 at the 50th and 100th, 1 at each of the 150th to 300th). No rate, variance, or confidence interval is claimed.
- One environment on one day (16:17 to 16:20 UTC), one S3 Access Points, one Lambda configuration. Objects of 1 KB and 100 KB only (100 KB in 3 invocations); 1 MB and 5 MB were not read.
- Concurrency 1 for the primary set. The overlapped side set ran at about concurrency 2 by mistake and is not a concurrency measurement. Concurrency above 1 is not covered.
- The factor 3 is a chosen cut-off. Two reads at the odd multiples were at 2.67 and 2.79 times the median, just below it. At positions that are not multiples of 50, the largest ratio in the 24 sequential invocations was 2.69 (the 102nd read of one control invocation); 3 of those 2,739 reads were at 2.0 or more, all on the control, and the largest on S3 Access Points was 1.94. In the overlapped side set the largest was 2.46 (control) and 1.95 (S3 Access Points).
- The boto3 and urllib3 versions in the Lambda runtime, and the cold or warm state of each invocation, were not recorded. No warm-up reads were excluded.
- No connection-level or packet-level observation was made, and the tool has no option to recreate the client every N reads.
- The standard S3 bucket is a diagnostic control for locating a cause, on a different service path with no file system. It is not a comparison of the two services.
- Not covered: other clients and SDKs, other Regions, a VPC-attached Lambda, and data not on SSD (capacity pool reads, tiering policy ALL).
- Request charges were not estimated, and no cost figure is given.

## Reference Results

> **Where these numbers come from**: the ListObjectsV2 (100 keys) and GetObject by Size values below are transcribed from measurements recorded in sibling repositories. They were not measured in this repository, and no raw data for them is committed here. For this repository's own measurements, see "Measured in this repository (2026-10-10)" and "Pooled re-measurement (2026-10-10)". "Effective Processing Rate" remains an unverified estimate with no measurement record. None of these is a service limit or guarantee.

### Environment Differences

The measurement in this repository, the transcribed measurements, and the earlier planned configuration differ as follows.

| Item | Measured here (2026-10-10) | Transcribed measurements | Earlier planned row |
|---|---|---|---|
| FSx for ONTAP throughput capacity | 128 MBps | 128 MBps | 512 MBps |
| Deployment type | SINGLE_AZ_1 (first generation), 1 HA pair | Single-AZ | Not specified |
| Client | Lambda outside a VPC (256 MB) | Local workstation over the internet | Lambda outside a VPC (256 MB) |
| S3 Access Points NetworkOrigin | Internet | Internet | Internet |
| Status | Measured, raw data committed | Transcribed, not measured here | Never measured |

The earlier planned row (512 MBps) appeared in a previous version of the Test Environment table as a plan. No measurement exists for it. A Lambda outside a VPC at 128 MBps has now been measured; a measurement at 512 MBps from a Lambda outside a VPC has not been taken (tracked in [#98](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/98)).

The transcribed GetObject p50 values below (30.5, 34.1, 48.5, and 111.0 ms) are within about 16 ms of the p50 values measured here at every size. Both sets come from 128 MBps Single-AZ file systems, so they differ in client and network path (a local workstation over the internet versus a Lambda in the same Region), not in file system capacity. Repetition counts and percentile aggregation also differ (5-10 repetitions in the source versus 10 per object in the earlier run and 40 per object in the pooled re-measurement), so the data do not isolate a cause for the remaining gaps. The source S3 Access Points benchmark records that over the internet, client-side bandwidth became the limit and the effect of 512 MBps was not visible; the measurement here does not test that statement.

### ListObjectsV2 (100 keys)

| Metric | Value |
|--------|-------|
| Median latency | 52.0 ms (min 49.2 ms, max 62.1 ms) |
| p99 latency | Unverified (not computed from 5 trials) |
| Keys per request | 100 (one call) |

Source: [BENCH-S3AP-LIST-001](https://github.com/Yoshiki0705/FSx-for-ONTAP-Lakehouse-Integrations/blob/main/verification-pack/s3ap-list-latency/evidence/2026-08-05/benchmark-result.yaml) (2026-08-05, ap-northeast-1, SINGLE_AZ_1, 128 MBps, over the internet, 5 trials, all objects under one prefix).

### GetObject by Size

| Object Size | P50 Latency | Max Latency | Mean Throughput |
|-------------|-------------|-------------|-----------|
| 1 KB | 30.5 ms | 117.1 ms | 0.03 MB/s |
| 100 KB | 34.1 ms | 59.2 ms | 2.7 MB/s |
| 1 MB | 48.5 ms | 83.7 ms | 18.1 MB/s |
| 5 MB | 111.0 ms | 172.3 ms | 41.8 MB/s |

Source: the GetObject table in [S3 Access Points benchmark results](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/docs/s3ap-benchmark-results.en.md) (2026-05-22, ap-northeast-1, Single-AZ (First-generation), 128 MBps, macOS + boto3 1.34.x, over the internet, concurrency 1, 5-10 repetitions). The audit-log size classes above (about 5 KB, 200 KB, and 2 MB) were not measured as such. p99 is unverified under these conditions.

### Effective Processing Rate

> **Unverified estimate** — the table below is an order-of-magnitude sizing figure with no measurement record.

The GetObject latencies measured on 2026-10-10 cover only the S3 read part of these durations. They do not include parsing or delivery to a vendor, and this table was not re-derived from them.

For the audit log poller Lambda (256 MB, outside VPC):

| Scenario | Files/Invocation | Duration | Notes |
|----------|-----------------|----------|-------|
| 10 small files (5 KB each) | 10 | ~3-5 s | Well within 5-min timeout |
| 50 medium files (200 KB each) | 50 | ~15-30 s | Comfortable |
| 100 medium files (200 KB each) | 100 | ~30-60 s | MAX_KEYS_PER_RUN default |
| 100 large files (2 MB each) | 100 | ~60-120 s | May need timeout increase |

## Factors Affecting Throughput

### FSx Throughput Capacity

FSx for ONTAP throughput capacity is shared across NFS, SMB, and S3 AP access. If production workloads are consuming throughput, S3 AP reads will be slower.

| FSx Throughput Capacity | Expected S3 AP Impact |
|------------------------|----------------------|
| 128 MB/s | Audit reads may compete with production |
| 512 MB/s | Audit reads unlikely to impact production |
| 2048 MB/s | No measurable impact |

### Network Path

| Lambda Placement | S3 AP Access | Latency Impact |
|-----------------|-------------|----------------|
| Outside VPC | Direct (internet-origin AP) | Lowest latency |
| VPC + NAT Gateway | Via NAT | +10-30 ms per request |
| VPC + Gateway EP only | TIMEOUT (internet-origin AP) | Does not work |

### Concurrency

The audit poller uses `ReservedConcurrentExecutions: 1` to prevent overlapping runs. This means sequential file processing within each invocation. For higher throughput:
- Increase Lambda memory (more CPU = faster processing)
- Use `ThreadPoolExecutor` for parallel GetObject calls within a single invocation
- Move to SQS-based fan-out for parallel file processing

### Tiering Policy and Data Tier

The figures measured in this repository were taken on a volume with the AUTO tiering policy, on data inferred to be resident on the SSD (primary) tier. This page does not restate the tiering conditions of the transcribed values. Reads of data that has been tiered to the capacity pool were not measured, and this page gives no capacity pool figure.

## Recommendations

### For Typical Deployments (< 100 files/5 min)

Default settings are sufficient:
- `MAX_KEYS_PER_RUN=100`
- `SAFETY_THRESHOLD_MS=30000`
- Lambda memory: 256 MB
- Lambda timeout: 300 s

### For High-Volume Deployments (> 100 files/5 min)

Options:
1. **Increase schedule frequency**: `rate(1 minute)` instead of `rate(5 minutes)`
2. **Increase Lambda memory**: 512 MB or 1024 MB for more CPU
3. **Parallel GetObject**: Use ThreadPoolExecutor (concurrency 5-10)
4. **SQS fan-out**: List files in one Lambda, process in parallel workers

### Monitoring Throughput

Add these CloudWatch custom metrics to track pipeline throughput:

```python
import boto3

cloudwatch = boto3.client("cloudwatch")

cloudwatch.put_metric_data(
    Namespace="Custom/FSxONTAPPipeline",
    MetricData=[
        {
            "MetricName": "FilesProcessedPerInvocation",
            "Value": files_processed,
            "Unit": "Count",
        },
        {
            "MetricName": "ProcessingDurationMs",
            "Value": duration_ms,
            "Unit": "Milliseconds",
        },
        {
            "MetricName": "BytesReadPerInvocation",
            "Value": bytes_read,
            "Unit": "Bytes",
        },
    ],
)
```

## Running Your Own Benchmark

The tool lives in [`benchmark/s3ap-throughput/`](../../benchmark/s3ap-throughput/README.md): `template.yaml` (CloudFormation stack, one Lambda with 256 MB memory, a 300 s timeout, and no VPC config), `run-benchmark.sh` (invoke helper), and `handler.py` (the function, with unit tests under `tests/`).

```bash
# 1. Deploy the benchmark Lambda. Replace the placeholder with your
#    S3 Access Points ARN.
aws cloudformation deploy \
  --template-file benchmark/s3ap-throughput/template.yaml \
  --stack-name fsxn-s3ap-benchmark \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    S3AccessPointArn=<s3-access-point-arn> \
    BenchmarkRegion=ap-northeast-1

# 2. Run the ListObjectsV2 and GetObject tests. Every value comes from an
#    environment variable. S3AP accepts the alias or the ARN.
FUNCTION_NAME=fsxn-s3ap-benchmark-fn \
S3AP=<s3-access-point-alias-or-arn> \
PREFIX=<key-prefix>/ \
AWS_REGION=ap-northeast-1 \
  benchmark/s3ap-throughput/run-benchmark.sh

# 3. Remove the stack when the measurement is done.
aws cloudformation delete-stack --stack-name fsxn-s3ap-benchmark
```

`run-benchmark.sh` applies one `PREFIX` to both tests and writes one combined JSON file. Optional overrides are `LIST_ITERATIONS` (default 20), `GET_ITERATIONS` (default 5), `MAX_KEYS` (default 10), `BENCHMARK_RUN_ID`, and `OUT_FILE`. The 2026-10-10 raw files are the function's per-test responses, one file per size prefix, not that combined file.

Record the result together with its environment context: FSx for ONTAP throughput capacity, S3 Access Points network origin, Region, date, and run ID. The tool's [README](../../benchmark/s3ap-throughput/README.md) has the deployment details.

## Related Documents

- [Benchmark tool (README)](../../benchmark/s3ap-throughput/README.md)
- [Raw data of the earlier 2026-10-10 measurement (averaged class percentiles)](../../benchmark/s3ap-throughput/results/2026-10-10/README.md)
- [Raw data of the 2026-10-10 pooled re-measurement](../../benchmark/s3ap-throughput/results/2026-10-10-pooled/README.md)
- [Raw data of the 2026-10-10 position check](../../benchmark/s3ap-throughput/results/2026-10-10-position/README.md)
- [S3 AP Specification & Troubleshooting](s3ap-fsxn-specification.md)
- [Pipeline SLO](pipeline-slo.md)
- [Operational Guide](operational-guide.md)
