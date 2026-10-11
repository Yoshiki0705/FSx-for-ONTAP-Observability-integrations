# S3 Access Point Read Throughput Benchmark

🌐 [日本語](../ja/s3ap-throughput-benchmark.md) | **English** (this page)

## Purpose

This document provides a benchmark methodology and reference results for reading Amazon FSx for NetApp ONTAP audit logs via S3 Access Points. Use these results as a **sizing reference, not a service limit**.

The three sections "Measured in this repository (2026-10-10)", "Pooled re-measurement (2026-10-10)", and "Tier comparison: SSD and capacity pool (2026-10-11)" hold the only S3 Access Points read-latency measurements in this repository. Their raw data is committed under `benchmark/s3ap-throughput/results/2026-10-10/` (averaged class percentiles), `benchmark/s3ap-throughput/results/2026-10-10-pooled/` (pooled class percentiles), and `benchmark/s3ap-throughput/results/2026-10-11-tier/` (volumes with the NONE and ALL tiering policies, with the analysis scripts). The position check is a subsection group of the second section, and its raw data is committed under `benchmark/s3ap-throughput/results/2026-10-10-position/`. The values under "Reference Results" are transcribed from sibling repositories.

> **Caveat**: Results are specific to the test environment described below. Your throughput will vary based on FSx throughput capacity, object size distribution, network path, concurrency, and workload mix. Always validate in your own environment.

## Test Environment

The table describes the 2026-10-10 measurement. The pooled re-measurement and the position check the same day used the same file system, volume, and Lambda settings; their differences are listed under "Pooled re-measurement (2026-10-10)" and "Position check: question, design, and differences from the pooled runs". The tier comparison of 2026-10-11 used three other volumes on a file system of the same type and sizing (SINGLE_AZ_1, 128 MBps, 1024 GiB SSD); its environment is listed in its own section.

| Parameter | Value |
|-----------|-------|
| Measurement date | 2026-10-10 |
| File system | SINGLE_AZ_1 (first generation), 1 HA pair, 1024 GiB SSD |
| FSx for ONTAP throughput capacity | 128 MBps |
| SVM and volume | One volume of one SVM (the file system has several SVMs) |
| Volume tiering policy | AUTO, cooling period 31 days (read from the FSx API on 2026-10-10) |
| Data tier at read time | SSD (primary tier). Inferred from the policy and the age of the objects (written minutes before the reads, cooling period 31 days). Not verified from a per-tier capacity metric; none was taken during the run |
| ONTAP version | 9.18.1P6, as read on 2026-10-06 through the ONTAP REST API on this file system (not re-read on the measurement day). A later read on 2026-10-11 returned 9.19.1P2. The date of the change is unknown, so the version in effect on the measurement day is not known ([record](verification-results-cloudwatch-monitoring.md#qtree-quota-monitor-run-with-a-read-only-ontap-account-on-2026-10-11)) |
| S3 Access Points type | ONTAP, internet origin, file system identity is UNIX user `root` |
| Client | The tool in `benchmark/s3ap-throughput/`, deployed as a Lambda function |
| Lambda memory and timeout | 256 MB, 300 s |
| Lambda placement | Outside VPC (no VPC config) |
| AWS Region | ap-northeast-1 |
| Test objects | Random bytes, 3 objects each of 1 KB, 100 KB, 1 MB, and 5 MB, one prefix per size |
| Benchmark run IDs | `bench-s3ap-2026-10-10` (run 1), `bench-s3ap-2026-10-10-r2` (run 2) |
| Pooled re-measurement run IDs | `bench-s3ap-2026-10-10-pooled-r1`, `bench-s3ap-2026-10-10-pooled-r2`, `bench-s3ap-2026-10-10-pooled-r3` |
| Position check run IDs | `position-r1-<series>`, `position-seq-r2-<series>`, `position-seq-r3-<series>` (sequential, counted); `position-r2-<series>`, `position-r3-<series>` (overlapped side set); the series IDs are listed in the results README |
| Tier comparison run IDs | `tier-r<run>-<condition>-<size>` (run 1 to 3; condition `a1`, `a2`, `b`; size `s64k`, `s1m`, `s5m`) and `tier-warm-<condition>` |

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
- Not covered by this run: volumes with the ALL tiering policy, and data already tiered to the capacity pool. A later measurement covers them for three object sizes at concurrency 1 on one file system; see "Tier comparison: SSD and capacity pool (2026-10-11)". The figures in this section are not capacity pool figures.

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

- Not covered: concurrency above 1, other file systems, other Regions, VPC-origin S3 Access Points, other Lambda memory sizes, and data not on SSD (capacity pool reads, tiering policy ALL; only "Tier comparison: SSD and capacity pool (2026-10-11)" covers these). The overlapped side set of the position check ran at about concurrency 2 by mistake; it is not a designed concurrency measurement.
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
| The positions are the same at 100 KB as at 1 KB | Held for the positions, in 3 invocations. A size effect at the 150th read is not excluded: it was slow in 2 of 2 invocations at 1 KB (300 reads) and in 0 of 3 at 100 KB (150 reads), and the two groups differ in invocation length as well as in size |
| A standard S3 bucket read by the same function shows the same pattern if the cause is not specific to S3 Access Points | Partly: the multiples of 100 appeared on the control; the odd multiples of 50 did not (0 of 6) |

### Position check: a third-party report consistent with the 100th-read shape

Hypothesis, not checked at the connection level here. A public issue for the AWS SDK for Go v1 ([aws/aws-sdk-go issue 2825](https://github.com/aws/aws-sdk-go/issues/2825), 2019) reports that, for PutObject and UploadPart calls against standard S3, one HTTP connection was reused for 100 requests and the 101st request opened a new connection. In the same thread, a contributor to that repository wrote, after testing, that this "looks to be a behavior of S3 rather than the AWS SDK for Go": S3 closes a connection after it has received 100 requests on it. The reporter then saw a `Connection: close` header on the 100th response in a Wireshark capture, but not in the headers the Go client printed, and another contributor suggested that the Go HTTP client or transport removes that header. The tool here lists keys once before the reads on the same client, so the key listing is request 1 and the 100th GetObject is request 101. If the same happens here, the repeating slow reads at the 100th, 200th, and 300th read would be the first request on a new connection. That reading is consistent with the shape and is not verified: it is a user report and a contributor's comment in a public issue (2019) for a different SDK and different operations, it is not AWS documentation, no connection or packet trace was taken in this check, and the boto3 and urllib3 versions in the Lambda runtime were not recorded. The report does not address the 50th, 150th, and 250th-read events on S3 Access Points. No source for them was found; the search was not exhaustive.

### Position check: what was and was not separated

Separated by this series:

- The multiples-of-100 event is not specific to S3 Access Points: the standard S3 control, read by the same function in the same Region, showed it in every invocation that reached it.
- It is not tied to an object boundary. In the 120-read series (S0 and the control at 120 reads) the 100th read is the 20th read of object 3, and it was slow in 6 of 6 invocations; the last read of each object (40th, 80th, 120th) and the first read of objects 2 and 3 were not slow.
- It repeats: slow reads occurred at the 200th and 300th read, not only at the 100th.
- Slow reads did not occur before the 50th read of any invocation, and the first read of an invocation was not slow.
- The positions were the same at 1 KB and 100 KB (3 invocations at 100 KB).
- The overlapped side set (about concurrency 2) showed the same positions. It was an unplanned overlap and is not a concurrency measurement.

Not separated:

- An invocation-wide request counter, a connection-wide request counter, and the lifetime of the client object. In this tool they predict the same positions (one client, sequential reads, and a pooled connection that is expected, but not observed, to be reused). Separating them needs a tool option that recreates the client or the connection every N reads. That option has not been built.
- The cause of the 50th, 150th, and 250th-read events on S3 Access Points. The client network path, the S3 Access Points front end, ONTAP, and Lambda were not separated. The control did not show these events in 6 reads, which is too few to rule them out on the standard S3 path.
- Whether the connection was in fact replaced at the 100th request. No connection-level observation was made.

### Position check: practical reading and its bounds

In this tool's invocations of 100 or more reads (one client object, sequential reads, connection reuse not observed), all 21 reads at the 100th, 200th, and 300th positions took 3.4 to 7.6 times the invocation median, which is 21 slow reads out of 2,370 reads in those invocations (0.9 %, about one in 113, because the reads after the last multiple of 100 dilute the share). On S3 Access Points it was 3.49 to 7.59 times (+107.5 to +250.8 ms over invocation medians of 36.7 to 46.1 ms; 15 reads), and on the standard S3 control 3.42 to 4.55 times (+69.3 to +102.0 ms; 6 reads). On S3 Access Points, a further 8 of 18 reads at the 50th, 150th, and 250th positions in those invocations were slow (11 of 21 when the three 50-read invocations are included). These values come from this tool: one client per invocation, concurrency 1, 1 KB and 100 KB objects, a 256 MB Lambda outside a VPC, one environment on one day. They are not extrapolated to other clients, other connection handling, or concurrency above 1. A class p99 computed from fewer than 100 reads in one invocation cannot contain the 100th-read event, because such an invocation never reaches that position. The earlier statement stands that a single run's class p99 is indicative only.

### Limits of the position check

- Per-position counts range from 1 to 14 invocations (S3 Access Points: 14 at the 50th read, 11 at the 100th, 5 at the 150th, 2 at each of the 200th to 300th; control: 4 at the 50th and 100th, 1 at each of the 150th to 300th). No rate, variance, or confidence interval is claimed.
- One environment on one day (16:17 to 16:20 UTC), one S3 Access Points, one Lambda configuration. Objects of 1 KB and 100 KB only (100 KB in 3 invocations); 1 MB and 5 MB were not read.
- Concurrency 1 for the primary set. The overlapped side set ran at about concurrency 2 by mistake and is not a concurrency measurement. Concurrency above 1 is not covered.
- The factor 3 is a chosen cut-off. Two reads at the odd multiples were at 2.67 and 2.79 times the median, just below it. At positions that are not multiples of 50, the largest ratio in the 24 sequential invocations was 2.69 (the 102nd read of one control invocation); 3 of those 2,739 reads were at 2.0 or more, all on the control, and the largest on S3 Access Points was 1.94. In the overlapped side set the largest was 2.46 (control) and 1.95 (S3 Access Points).
- The boto3 and urllib3 versions in the Lambda runtime, and the cold or warm state of each invocation, were not recorded. No warm-up reads were excluded.
- No connection-level or packet-level observation was made, and the tool has no option to recreate the client every N reads.
- The standard S3 bucket is a diagnostic control for locating a cause, on a different service path with no file system. It is not a comparison of the two services.
- Not covered: other clients and SDKs, other Regions, a VPC-attached Lambda, and data not on SSD (capacity pool reads, tiering policy ALL; only "Tier comparison: SSD and capacity pool (2026-10-11)" covers these).
- Request charges were not estimated, and no cost figure is given.

## Tier comparison: SSD and capacity pool (2026-10-11)

Evidence tier: the measured values are `verified` for the environment, date, and sample size shown. Every other statement carries its own tier under "Evidence tiers of the statements in this section". These latencies and operation counts were observed in the environment under "Environment" below, on 2026-10-11, in 27 measured invocations between 08:38 and 09:24 UTC, with the tool at `main` commit `6808382`. The tier does not cover other file systems, Regions, ONTAP versions, object sizes, concurrency levels, network paths, or dates. This is a sample run in one environment: a sizing reference, not a production estimate and not a service limit. Tables show tool output unless the heading says derived. Derived values were computed from the committed raw files under `benchmark/s3ap-throughput/results/2026-10-11-tier/` and are not tool output.

On the volume with tiering policy ALL (B), the first read of an object took longer than on the two volumes with tiering policy NONE (A1 and A2) at 1 MiB and at 5 MiB, in all three runs. The per-run median difference D was +67.8 to +74.0 ms at 1 MiB (largest A1-A2 difference 14.0 ms) and +150.8 to +167.1 ms at 5 MiB (largest A1-A2 difference 21.5 ms). No difference is established at 64 KiB, or at any size when first and repeat reads are taken together. The B invocations caused 56, 56, and 54 capacity pool read operations at 1 MiB and 332, 323, and 298 at 5 MiB (5.4 to 5.6 and 29.8 to 33.2 per object), and none at 64 KiB. These are results for this environment and are not a general property of the service.

### Question, design, and differences from the earlier runs

The question: when one procedure reads both, is the first read of an object that already sits in the capacity pool slower than the same read on SSD? Billing, other object sizes, concurrency above 1, and second-generation or Multi-AZ file systems are outside it.

- Three volumes on one SVM: A1 and A2 with tiering policy NONE, B with tiering policy ALL. A2 is there to show how far two volumes with the same policy differ. That difference is the noise floor for the A-versus-B difference.
- Each run reads objects that no earlier invocation had read (`run<run>/<size>/obj-00` to `obj-09`), so the first read of each object is its first read after the write. The bytes are random and differ per volume.
- One invocation lists a prefix once and reads each of its 10 objects 4 times in a row: 1 ListObjectsV2 and 40 GetObject requests on one new client, at concurrency 1. It makes 40 reads, so it does not reach the 50th read, where the position check first saw slow reads.
- Three read classes: first reads (read 1 of each object, n = 10 per invocation), repeat reads (reads 2 to 4, n = 30, each right after the first read of the same object), and all reads (n = 40).
- Three warm-up invocations ran before run 1: the `warm/` 64 KiB object read 4 times on each volume. They are kept in the raw data and not analysed. The tool has no warm-up option.
- The order of the conditions was rotated by Latin square, the size blocks ran in the order 64 KiB, 1 MiB, 5 MiB, invocations started 60 s apart, and the gap between runs was 637.9 s (run 1 to 2) and 653.8 s (run 2 to 3).

| Run | Order inside each size block | First start to last end (UTC) |
|-----|------------------------------|-------------------------------|
| 1 | A1, A2, B | 08:38:00 to 08:46:07 |
| 2 | A2, B, A1 | 08:56:45 to 09:04:49 |
| 3 | B, A1, A2 | 09:15:43 to 09:23:48 |

Run 3 was restarted from its first invocation after a credential refresh failure stopped the first attempt before any invocation; the schedule was not changed. The differences from the pooled re-measurement:

| Item | Pooled re-measurement (2026-10-10) | Tier comparison (2026-10-11) |
|------|------------------------------------|------------------------------|
| Volumes | 1, tiering policy AUTO | 3: NONE, NONE, ALL |
| Object sizes | 1 KB, 100 KB, 1 MB, 5 MB | 64 KiB, 1 MiB, 5 MiB |
| Objects per size in one invocation | 3 | 10 |
| Reads per object in one invocation | 40 | 4 |
| Requests in one GetObject invocation | 121 (1 listing, 120 reads) | 41 (1 listing, 40 reads) |
| Objects read in each run | The same objects in every run | A new set of objects in each run |
| Order of the conditions | One condition | Rotated per run |
| Decision rule | None, descriptive | Fixed before the first invocation |
| Warm-up | None | 3 dedicated invocations |
| ONTAP version | Not re-read | 9.19.1P2, read after the runs |

### Environment

The table lists what this measurement used and read back.

| Parameter | Value |
|-----------|-------|
| Measurement date and time | 2026-10-11. The 27 measured invocations ran from 08:38:00 to 09:23:48 UTC; the 3 warm-up invocations from 08:37:30 to 08:37:34 UTC |
| AWS Region | ap-northeast-1 |
| File system | SINGLE_AZ_1 (first generation), throughput capacity 128 MBps, 1024 GiB SSD |
| SSD utilization | About 3.5 % in a CloudWatch `StorageCapacityUtilization` reading taken before the volumes were created. It was not re-read afterwards and is not in the committed raw data. At or below 50 % only volumes with tiering policy ALL tier data ([documented](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html)) |
| ONTAP version | 9.19.1P2, read through the ONTAP REST API after the runs. The 2026-10-10 tables record 9.18.1P6 (see "Test Environment"), so the versions differ and no comparison with those tables is claimed |
| Volumes | Three on one SVM, 1024 MiB each, UNIX security style, snapshot policy none (set at creation). A1 and A2: tiering policy NONE. B: tiering policy ALL. The policies were set at creation and read back through the ONTAP REST API after the runs |
| Cloud retrieval policy | `default` on all three volumes, read through the ONTAP REST API after the runs |
| Storage efficiency | Inline compression and deduplication `both` on all three volumes. The test data are random bytes |
| Tier state at the start | B held 190,775,296 B in the capacity pool tier at three polls (08:31:55, 08:34:20, and 08:37:14 UTC) and A1 and A2 held 0 B. 190,775,296 B is the combined size of the 91 objects written to B (65,536 + 30 x 6,356,992 B) |
| Write to first read | Writes (`put-object` through the S3 Access Points) finished at 08:31:31 UTC. B invocations started 8.5 to 50.2 minutes later and all 27 invocations 6.5 to 52.2 minutes later. The earliest B invocation started 8.1 minutes after the first poll that showed the data in the capacity pool |
| S3 Access Points | Three, one per volume. Type ONTAP, internet origin, file system identity is UNIX user `root` |
| Client | The tool in `benchmark/s3ap-throughput/` at `main` commit `6808382` (`handler.py` and `template.yaml` unchanged since). Lambda Python 3.12, 256 MB, 300 s timeout, no VPC config. A new boto3 client per invocation. The boto3 and urllib3 versions and the cold or warm start of each invocation were not recorded |
| Test | `GetObject` with `max_keys` 10 and `iterations` 4 (`max_keys` 1 for warm-up). Concurrency 1: reads are sequential in a single stream |
| Test objects | Random bytes, different on each volume. Per run, 10 objects each of 64 KiB, 1 MiB, and 5 MiB (65,536, 1,048,576, and 5,242,880 bytes; labelled `64KB`, `1MB`, and `5MB` in the raw files), plus one 64 KiB `warm/` object |
| Percentiles | Nearest rank over the samples of a class, the tool's `pooled_nearest_rank`. The analysis uses the same definition. MB/s in the raw files means MiB/s |
| Benchmark run IDs | `tier-r<run>-<condition>-<size>` with run 1 to 3, condition `a1`, `a2`, or `b`, and size `s64k`, `s1m`, or `s5m`; warm-up `tier-warm-<condition>` |
| Totals | 27 measured invocations (27 ListObjectsV2 and 1,080 GetObject) and 3 warm-up invocations (3 and 12); 1,092 GetObject reads, 2,289,303,552 bytes |
| Raw data | `benchmark/s3ap-throughput/results/2026-10-11-tier/` |
| Cleanup | The three volumes and the three S3 Access Points were deleted afterwards. The file system and its other volumes and S3 Access Points were not changed. The deleted volumes were still in ONTAP's volume recovery queue at the end; their retention period was not read and they were not purged |

### Decision rule fixed before the first invocation

For a size and a read class, let `m_A1(r)`, `m_A2(r)`, and `m_B(r)` be the medians of the invocations of run `r`. Then:

- `D(r) = m_B(r) - (m_A1(r) + m_A2(r)) / 2`
- `N(r) = |m_A1(r) - m_A2(r)|`
- `N_largest` is the largest `N(r)` over the three runs.

B is called slower for that size and class only if `D(r) > 0` and `D(r) > N_largest` in all three runs. Otherwise no difference is established (`open`). The rule covers the first-read class and the all-reads class. Repeat reads are reported without a rule. The rule is one-sided: it does not test whether B is faster, and no such claim is made. The rule text was written before the first invocation, in a plan file that is not in this repository, so the order of rule and data cannot be checked from the repository.

### Results by run (derived)

Rule outcome per size and class:

| Size | Class | D(1) / D(2) / D(3) (ms) | N_largest (ms) | Rule |
|------|-------|-------------------------|----------------|------|
| 64 KiB | first reads | -1.80 / -0.84 / -4.32 | 7.40 | not met, `open` |
| 64 KiB | all reads | -2.80 / -0.08 / -3.27 | 7.73 | not met, `open` |
| 1 MiB | first reads | +72.33 / +67.80 / +73.97 | 14.01 | met |
| 1 MiB | all reads | -11.69 / +7.89 / +12.41 | 14.98 | not met, `open` |
| 5 MiB | first reads | +165.43 / +167.09 / +150.78 | 21.47 | met |
| 5 MiB | all reads | +7.52 / +10.82 / +17.90 | 19.56 | not met (D below N_largest in all three runs), `open` |

At 64 KiB first reads, D is negative in all three runs, but its absolute value (0.84 to 4.32 ms) is below N_largest, so neither direction is claimed.

Medians per run (ms). D = B - (A1 + A2) / 2 and N = |A1 - A2|.

| Size | Class (n per invocation) | Run | A1 | A2 | B | D | N |
|------|--------------------------|-----|----|----|---|---|---|
| 64 KiB | first reads (10) | 1 | 38.75 | 46.15 | 40.65 | -1.80 | 7.40 |
| 64 KiB | first reads (10) | 2 | 43.06 | 46.29 | 43.84 | -0.84 | 3.23 |
| 64 KiB | first reads (10) | 3 | 44.49 | 45.46 | 40.66 | -4.32 | 0.97 |
| 64 KiB | repeat reads (30) | 1 | 37.88 | 45.79 | 39.27 | -2.57 | 7.91 |
| 64 KiB | repeat reads (30) | 2 | 40.79 | 45.34 | 43.20 | +0.13 | 4.55 |
| 64 KiB | repeat reads (30) | 3 | 44.36 | 43.00 | 40.51 | -3.17 | 1.35 |
| 64 KiB | all reads (40) | 1 | 38.42 | 46.15 | 39.49 | -2.80 | 7.73 |
| 64 KiB | all reads (40) | 2 | 41.91 | 45.74 | 43.75 | -0.08 | 3.82 |
| 64 KiB | all reads (40) | 3 | 44.49 | 43.12 | 40.54 | -3.27 | 1.37 |
| 1 MiB | first reads (10) | 1 | 52.61 | 46.44 | 121.86 | +72.33 | 6.17 |
| 1 MiB | first reads (10) | 2 | 46.19 | 60.20 | 120.99 | +67.80 | 14.01 |
| 1 MiB | first reads (10) | 3 | 53.14 | 44.32 | 122.70 | +73.97 | 8.82 |
| 1 MiB | repeat reads (30) | 1 | 51.45 | 54.92 | 41.05 | -12.14 | 3.47 |
| 1 MiB | repeat reads (30) | 2 | 44.41 | 58.92 | 59.74 | +8.08 | 14.51 |
| 1 MiB | repeat reads (30) | 3 | 50.48 | 42.49 | 59.79 | +13.31 | 7.99 |
| 1 MiB | all reads (40) | 1 | 51.73 | 54.92 | 41.63 | -11.69 | 3.19 |
| 1 MiB | all reads (40) | 2 | 44.62 | 59.60 | 60.00 | +7.89 | 14.98 |
| 1 MiB | all reads (40) | 3 | 51.36 | 43.66 | 59.92 | +12.41 | 7.70 |
| 5 MiB | first reads (10) | 1 | 122.72 | 101.25 | 277.41 | +165.43 | 21.47 |
| 5 MiB | first reads (10) | 2 | 119.98 | 101.47 | 277.82 | +167.09 | 18.51 |
| 5 MiB | first reads (10) | 3 | 118.79 | 100.95 | 260.65 | +150.78 | 17.83 |
| 5 MiB | repeat reads (30) | 1 | 120.23 | 103.35 | 118.07 | +6.28 | 16.89 |
| 5 MiB | repeat reads (30) | 2 | 101.37 | 117.23 | 119.72 | +10.42 | 15.86 |
| 5 MiB | repeat reads (30) | 3 | 101.01 | 101.45 | 118.83 | +17.60 | 0.44 |
| 5 MiB | all reads (40) | 1 | 121.29 | 101.73 | 119.03 | +7.52 | 19.56 |
| 5 MiB | all reads (40) | 2 | 115.43 | 103.11 | 120.09 | +10.82 | 12.32 |
| 5 MiB | all reads (40) | 3 | 101.85 | 101.45 | 119.55 | +17.90 | 0.39 |

First reads at 1 MiB and 5 MiB meet the rule. No other size and class does. The rule does not cover repeat reads: their D is 12.14 ms below zero in run 1 at 1 MiB and 17.60 ms above zero in run 3 at 5 MiB.

### Pooled figures across the three runs (derived)

Descriptive only. A is A1 and A2 pooled, so n is 60, 180, or 240 for A and 30, 90, or 120 for B. The percentile is nearest rank over the pooled samples.

| Size | Class | A: n, p50, p90 (ms) | B: n, p50, p90 (ms) | p50 difference (ms) |
|------|-------|---------------------|---------------------|---------------------|
| 64 KiB | first reads | 60, 45.46, 51.19 | 30, 41.08, 47.49 | -4.39 |
| 64 KiB | repeat reads | 180, 43.35, 47.17 | 90, 40.81, 44.83 | -2.54 |
| 64 KiB | all reads | 240, 43.72, 48.31 | 120, 40.87, 45.39 | -2.85 |
| 1 MiB | first reads | 60, 53.70, 66.48 | 30, 122.70, 163.98 | +69.00 |
| 1 MiB | repeat reads | 180, 52.00, 60.02 | 90, 54.65, 60.26 | +2.65 |
| 1 MiB | all reads | 240, 52.61, 60.51 | 120, 59.79, 135.38 | +7.18 |
| 5 MiB | first reads | 60, 118.15, 140.90 | 30, 277.82, 363.60 | +159.66 |
| 5 MiB | repeat reads | 180, 103.02, 138.66 | 90, 118.73, 137.57 | +15.71 |
| 5 MiB | all reads | 240, 116.69, 139.26 | 120, 119.78, 296.62 | +3.09 |

First reads are one quarter of all reads. The all-reads p90 gap (1 MiB: 60.51 against 135.38 ms; 5 MiB: 139.26 against 296.62 ms) comes from them, while the all-reads p50 gap is +7.18 and +3.09 ms. The first-read distributions overlap. At 1 MiB, B first reads ranged from 89.963 to 218.758 ms and A from 41.352 to 108.572 ms; 6 of the 30 B first reads were not above the largest A first read. At 5 MiB, B ranged from 200.668 to 532.045 ms and A from 82.329 to 251.076 ms; 5 of 30 were not above it. Because the distributions overlap, the result concerns the median, and an individual B read can be as fast as an A read.

### Capacity pool reads per invocation (derived)

Each B invocation is assigned the CloudWatch 1-minute `CapacityPoolReadOperations` and `CapacityPoolReadBytes` buckets from its start minute through the minute after its end. CloudWatch put some operations from the second half of a minute into the next minute's bucket (first seen in run 2). B invocations are at least 3 minutes apart, so the windows do not overlap. The window affects this table only, not the decision rule.

| Invocation | Operations | Bytes | One pass of the objects (10 x size) | Bytes as share of one pass |
|------------|-----------:|------:|------------------------------------:|---------------------------:|
| run 1, 64 KiB | 0 | 0 | 655,360 | 0 % |
| run 1, 1 MiB | 56 | 9,907,200 | 10,485,760 | 94.5 % |
| run 1, 5 MiB | 332 | 52,243,968 | 52,428,800 | 99.6 % |
| run 2, 64 KiB | 0 | 0 | 655,360 | 0 % |
| run 2, 1 MiB | 56 | 9,907,200 | 10,485,760 | 94.5 % |
| run 2, 5 MiB | 323 | 52,227,456 | 52,428,800 | 99.6 % |
| run 3, 64 KiB | 0 | 0 | 655,360 | 0 % |
| run 3, 1 MiB | 54 | 9,948,480 | 10,485,760 | 94.9 % |
| run 3, 5 MiB | 298 | 50,151,072 | 52,428,800 | 95.7 % |

- A1 and A2 show 0 in every bucket of the CloudWatch window (08:30 to 09:26 UTC), for all four capacity pool metrics.
- The 64 KiB invocations caused no capacity pool reads, although the capacity pool tier of B held the combined size of all objects written to it.
- Each invocation reads every object 4 times, but the bytes read from the capacity pool are about one pass of the object bytes (94.5 % to 99.6 %), not four. At 5 MiB they are 23.9 % to 24.9 % of the 209,715,200 B that four passes would be. In total, the capacity pool served about one read of each object for four reads by the client.
- Per object, that is 5.4 to 5.6 operations at 1 MiB (176,914 to 184,231 bytes per operation) and 29.8 to 33.2 operations at 5 MiB (157,361 to 168,292 bytes per operation).
- Two buckets outside any invocation window have B read operations: 2 operations (8,192 B) at 08:30 UTC and 1 operation (4,096 B) at 08:31 UTC, which is 4,096 B each. They coincide with B's capacity pool writes in the same buckets. AWS documents that ONTAP performs a read on the capacity pool right after a write (`documented`, [Volume metrics](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-metrics.html)).

Per-run sums of the per-invocation CloudWatch values, next to the increase of the node-level `get` row of the ONTAP `object_store_client_op` counter table, read through the ONTAP REST API (GET only) before and after each run:

| Run | Counter reads (UTC) | CloudWatch B operations | Node 1 `get` increase, operations | CloudWatch B bytes | Node 1 `get` increase, bytes | Equal |
|-----|---------------------|------------------------:|----------------------------------:|-------------------:|-----------------------------:|-------|
| 1 | 08:37:47 to 08:46:18 | 388 | 388 | 62,151,168 | 62,151,168 | yes |
| 2 | 08:56:42 to 09:05:01 | 379 | 379 | 62,134,656 | 62,134,656 | yes |
| 3 | 09:15:40 to 09:24:08 | 352 | 352 | 60,099,552 | 60,099,552 | yes |

Over the same intervals the node 1 `put` increase was 0 in all three runs, and the `head` increase was 16, 15, and 15 on node 1 and 16, 16, and 16 on node 2. Node 2 had no `get` or `put` row. In a 60 s window with no benchmark reads (08:34:58 to 08:35:58 UTC), `get` and `put` did not change and `head` rose by 2 on each node. The counters are cumulative from an unknown start, so only differences are used. The counter dumps are not committed because their row IDs contain a file system identifier; the table gives the differences.

Whether the CloudWatch operation count equals the number of capacity pool read requests that are billed is `open`. AWS describes the metric as read operations from the capacity pool tier that translate to a capacity pool read request ([Volume metrics](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-metrics.html)). No billing data was compared, and no cost figure is derived from these counts.

### Setup checks and the A1-A2 spread (derived)

- Tier state: B held 190,775,296 B in the capacity pool tier at all four polls (08:31:55, 08:34:20, 08:37:14, and 09:24:10 UTC), so the volume's capacity pool size did not shrink during the runs. A1 and A2 held 0 B at all four. This is a volume-level metric; the location of individual objects was not checked. B's SSD figure went from 24,326,144 B to 15,732,736 B; the change was not examined.
- File-system-wide reads between 08:36 and 09:25 UTC: the `DataReadBytes` sum is 2,289,303,552 B and the `DataReadOperations` sum is 1,092, the same as the benchmark's own reads (2,288,517,120 B measured plus 786,432 B warm-up; 1,080 plus 12 GetObject reads). The `DataWriteBytes` sum is 0. These metrics do not count metadata operations.
- Position check: 4 samples were above 3 times their invocation median outside B's first reads, all in run 1 at 5 MiB (A1: reads 11 and 34, at 381.1 and 416.6 ms; B: reads 10 and 18, at 364.2 and 390.4 ms; invocation medians 121.3 and 119.3 ms). Runs 2 and 3 had none, and the four positions differ, so no fixed position is involved. An invocation makes 41 requests, so the 50th and 100th request are never reached.
- A1-A2 spread (the noise floor): N for first reads is 0.97 to 7.40 ms at 64 KiB, 6.17 to 14.01 ms at 1 MiB, and 17.83 to 21.47 ms at 5 MiB. At 5 MiB first reads, A1 was slower than A2 in all three runs (118.79 to 122.72 against 100.95 to 101.47 ms) whichever condition ran first; the cause is unknown. For 5 MiB repeat reads, five medians lie between 117.23 and 120.23 ms and four between 101.01 and 103.35 ms. Which of A1 and A2 sits in which group changes between runs (run 1: 120.23 and 103.35; run 2: 101.37 and 117.23; run 3: 101.01 and 101.45), and B is in the upper group in all three runs (118.07, 119.72, and 118.83 ms). The all-reads class at 5 MiB is therefore not called by the rule: D is +7.52, +10.82, and +17.90 ms, and N_largest is 19.56 ms.

### What the numbers support and what they do not

The numbers support, for this environment and these three runs:

- At 1 MiB and 5 MiB, the first read of an object on the ALL volume has a higher median than on the NONE volumes, by more than the largest A1-A2 difference, in all three runs. The per-run D is +67.80 to +73.97 ms at 1 MiB and +150.78 to +167.09 ms at 5 MiB. Pooled over the three runs, the first-read p50 is 53.70 ms for A and 122.70 ms for B at 1 MiB (+69.00 ms, 2.3 times), and 118.15 ms for A and 277.82 ms for B at 5 MiB (+159.66 ms, 2.4 times).
- The bytes read from the capacity pool are about one pass of the object bytes per invocation, not four, which is consistent with only first reads reaching the capacity pool.
- For B, the per-volume `CapacityPoolReadOperations` equals the increase of the node-level `get` counter in each run.

The numbers do not support:

- Any difference at 64 KiB, and any difference for first and repeat reads taken together at any size.
- A statement that repeat reads of B are as fast as on SSD. Across the nine run-by-size cells, the repeat-read D ranges from -12.14 to +17.60 ms against A1-A2 differences of 0.44 to 16.89 ms, and the rule is not defined for the class. Pooled repeat-read p50 differences are +2.65 ms at 1 MiB and +15.71 ms at 5 MiB, against +69.00 and +159.66 ms for first reads.
- A cost or billing figure.
- The reason repeat reads are fast, or the reason 64 KiB first reads caused no capacity pool reads (`hypothesis`). One candidate is a cache in ONTAP. It was not tested, and other causes are not excluded. The capacity pool size of B equals everything written to it, so the 64 KiB objects were presumably tiered too.

For a reader that fetches each object once, the first-read figures are the ones that correspond; the all-reads figures include repeat reads that this reader does not make. Whether such a reader finds its objects already in the capacity pool depends on the volume's tiering policy and on the time between the write and the read; here that time was 6.5 to 52.2 minutes. For orientation only, the all-reads p50 of the A volumes was 52.61 ms at 1 MiB and 116.69 ms at 5 MiB, and the class p50 values of the three 2026-10-10 pooled runs were 50.07 to 60.02 ms at 1 MB and 118.60 to 120.90 ms at 5 MB. Sizes, read counts, volumes, and ONTAP versions differ, so no comparison is claimed.

### Limits of the tier comparison

- One file system, one Region, about 46 minutes on 2026-10-11 (08:38 to 09:24 UTC). Three object sizes. Concurrency 1 with sequential reads, from a 256 MB Lambda outside a VPC to internet-origin S3 Access Points.
- First reads happened 6.5 to 52.2 minutes after the writes finished and at least 8.1 minutes after the capacity pool was first seen holding the data. The ONTAP cache state at that time is unknown. Only the volume-level capacity pool size was checked, not the location of individual objects.
- There are 10 objects per size in one invocation and 30 per condition and size over three runs. No significance test was run, and three runs do not establish stability.
- A1 and A2 have the same configuration and differ by up to 21.47 ms (5 MiB first reads). The cause is unknown.
- Not covered: second-generation or Multi-AZ file systems, other ONTAP versions, concurrency above 1, the AUTO policy, cloud retrieval policies other than `default`, a VPC-attached Lambda, and a billing comparison.
- The rule text was written before the first invocation in a plan file that is not in this repository.
- The boto3 and urllib3 versions and the cold or warm start of each invocation were not recorded. No request charge or cost figure was estimated.

### Evidence tiers of the statements in this section

| Statement | Tier | Basis |
|-----------|------|-------|
| The measured latencies, counts, and byte totals in this section, each with the environment, date, and n shown | `verified` | Sample run in one environment. Not a service limit and not a production estimate |
| At 1 MiB and 5 MiB, the first read of an object on the ALL volume is slower than on the NONE volumes | `verified` | This environment and these three runs; the rule was met in 3 of 3 runs. Not a general property of the service |
| A difference for first reads at 64 KiB, and for all reads (first and repeat together) at any size | `open` | The rule was not met (see the rule outcome table) |
| Repeat reads (reads 2 to 4) of the ALL volume are as fast as on SSD | `open` | Repeat-read D was -12.14 to +17.60 ms against A1-A2 differences of 0.44 to 16.89 ms, and the rule is not defined for this class |
| For the ALL volume, per-volume `CapacityPoolReadOperations` equals the increase of the node-level `get` counter of `object_store_client_op` in each run | `verified` | This environment, 3 of 3 runs. Counters read through the ONTAP REST API |
| The capacity pool size of B did not change from before run 1 to after run 3 | `verified` | Volume-level metric at four points. The location of individual objects was not checked |
| No other data reads reached the file system between 08:36 and 09:25 UTC | `verified` | File-system-wide `DataReadBytes` and `DataReadOperations` equal the benchmark's own reads. These metrics do not count metadata operations |
| Under tiering policy ALL, written data goes to SSD first and is tiered to the capacity pool by a background process, and data blocks that are read stay cold instead of moving to SSD | `documented` | [Volume storage capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html), tiering policies |
| At or below 50 % SSD utilization only volumes with the ALL policy tier data; the default cloud retrieval policy follows the volume's tiering policy | `documented` | Same page, tiering thresholds and cloud retrieval policies |
| `CapacityPoolReadOperations` and `CapacityPoolReadBytes` count reads from the volume's capacity pool tier, and ONTAP reads from the capacity pool right after a write | `documented` | [Volume metrics](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-metrics.html) |
| Why repeat reads are fast, and why 64 KiB first reads caused no capacity pool reads | `hypothesis` | One candidate is a cache in ONTAP. Not tested; other causes are not excluded |
| The relation between the CloudWatch operation count and billed capacity pool read requests | `open` | No billing data was compared |
| The cause of the A1-A2 difference (up to 21.47 ms at 5 MiB first reads) | `open` | Same configuration, different volumes and S3 Access Points; not isolated |

## Reference Results

> **Where these numbers come from**: the ListObjectsV2 (100 keys) and GetObject by Size values below are transcribed from measurements recorded in sibling repositories. They were not measured in this repository, and no raw data for them is committed here. For this repository's own measurements, see "Measured in this repository (2026-10-10)", "Pooled re-measurement (2026-10-10)", and "Tier comparison: SSD and capacity pool (2026-10-11)". "Effective Processing Rate" remains an unverified estimate with no measurement record. None of these is a service limit or guarantee.

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

The figures measured on 2026-10-10 were taken on a volume with the AUTO tiering policy, on data inferred to be resident on the SSD (primary) tier. This page does not restate the tiering conditions of the transcribed values. Reads of data tiered to the capacity pool were measured once, on 2026-10-11, in "Tier comparison: SSD and capacity pool (2026-10-11)": 64 KiB, 1 MiB, and 5 MiB objects, concurrency 1, one file system, volumes with the NONE and ALL policies. Do not extend those figures to other object sizes, concurrency levels, or tiering policies.

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
- [Raw data of the 2026-10-11 tier comparison](../../benchmark/s3ap-throughput/results/2026-10-11-tier/README.md)
- [S3 AP Specification & Troubleshooting](s3ap-fsxn-specification.md)
- [Pipeline SLO](pipeline-slo.md)
- [Operational Guide](operational-guide.md)
