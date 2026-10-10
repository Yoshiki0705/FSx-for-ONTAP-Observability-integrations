# S3 Access Point Read Throughput Benchmark

🌐 [日本語](../ja/s3ap-throughput-benchmark.md) | **English** (this page)

## Purpose

This document provides a benchmark methodology and reference results for reading Amazon FSx for NetApp ONTAP audit logs via S3 Access Points. Use these results as a **sizing reference, not a service limit**.

The section "Measured in this repository (2026-10-10)" holds the only S3 Access Points read-latency measurement in this repository. Its raw data is committed under `benchmark/s3ap-throughput/results/2026-10-10/`. The values under "Reference Results" are transcribed from sibling repositories.

> **Caveat**: Results are specific to the test environment described below. Your throughput will vary based on FSx throughput capacity, object size distribution, network path, concurrency, and workload mix. Always validate in your own environment.

## Test Environment

The table describes the 2026-10-10 measurement.

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

Evidence tier: `verified`, for one statement only. These latencies were observed in the environment under "Test Environment" on 2026-10-10. The tier does not cover other file systems, concurrency levels, network paths, or dates. Two runs were taken on the same day, about 20 minutes apart (raw timestamps 08:09 UTC and 08:29 UTC), so the figures show an observed range and not a stable value.

The volume's tiering policy was AUTO with a 31-day cooling period, and the test objects were written minutes before they were read. The reads are therefore inferred to have been served from the SSD (primary) tier. This is an inference from the policy and the object age. No per-tier capacity metric was captured during the run.

### Aggregation method

Each object was read 10 times in sequence (concurrency 1). ListObjectsV2 was called 20 times with no MaxKeys override, so the tool's default of 100 applied. The version of the tool that took these measurements computed nearest-rank p50 and p99 and the mean per object. The class-level p50 and p99 in the tables below are the simple mean of the three objects' own p50 and p99 values. They are not percentiles over the 30 pooled samples. Later versions of the tool compute the class figures over the pooled samples and mark each class with `percentile_method: "pooled_nearest_rank"` ([#147](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/147)); the 2026-10-10 raw files have no such key. Class figures from a later version are therefore not directly comparable with the tables below. Throughput for one object is derived from that object's mean latency and size, and the class figure is the mean of the three. MB/s means MiB/s here (size divided by 1,048,576 bytes), as in the tool.

### Results of run 1 and run 2

Run 1 (`bench-s3ap-2026-10-10`):

| Operation | p50 (ms) | p99 (ms) | Mean (ms) | Mean per-stream throughput (MB/s) |
|-----------|---------|---------|-----------|------------------------------------|
| ListObjectsV2 (20 iterations, 13 keys) | 29.788 | 278.046 | 44.2 | n/a |
| GetObject 1 KB | 40.577 | 43.14 | 40.753 | 0.02 |
| GetObject 100 KB | 49.89 | 58.157 | 50.798 | 1.93 |
| GetObject 1 MB | 60.388 | 74.535 | 62.883 | 15.94 |
| GetObject 5 MB | 119.753 | 191.688 | 131.909 | 38.13 |

Run 2 (`bench-s3ap-2026-10-10-r2`):

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

## Reference Results

> **Where these numbers come from**: the ListObjectsV2 (100 keys) and GetObject by Size values below are transcribed from measurements recorded in sibling repositories. They were not measured in this repository, and no raw data for them is committed here. For this repository's own measurement, see "Measured in this repository (2026-10-10)". "Effective Processing Rate" remains an unverified estimate with no measurement record. None of these is a service limit or guarantee.

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

The transcribed GetObject p50 values below (30.5, 34.1, 48.5, and 111.0 ms) are within about 16 ms of the p50 values measured here at every size. Both sets come from 128 MBps Single-AZ file systems, so they differ in client and network path (a local workstation over the internet versus a Lambda in the same Region), not in file system capacity. Repetition counts and percentile aggregation also differ (5-10 repetitions in the source versus 10 per object here), so the data do not isolate a cause for the remaining gaps. The source S3 Access Points benchmark records that over the internet, client-side bandwidth became the limit and the effect of 512 MBps was not visible; the measurement here does not test that statement.

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
- [Raw data of the 2026-10-10 measurement](../../benchmark/s3ap-throughput/results/2026-10-10/README.md)
- [S3 AP Specification & Troubleshooting](s3ap-fsxn-specification.md)
- [Pipeline SLO](pipeline-slo.md)
- [Operational Guide](operational-guide.md)
