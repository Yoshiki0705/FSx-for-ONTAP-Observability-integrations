# S3 Access Point Read Throughput Benchmark

🌐 [日本語](../ja/s3ap-throughput-benchmark.md) | **English** (this page)

## Purpose

This document provides a benchmark methodology and reference results for reading FSx for ONTAP audit logs via S3 Access Points. Use these results as a **sizing reference, not a service limit**.

> **Caveat**: Results are specific to the test environment described below. Your throughput will vary based on FSx throughput capacity, object size distribution, network path, concurrency, and workload mix. Always validate in your own environment.

## Test Environment

| Parameter | Value |
|-----------|-------|
| FSx for ONTAP throughput capacity | 512 MB/s |
| SVM count | 1 |
| S3 Access Point type | Internet-origin |
| Lambda memory | 256 MB |
| Lambda placement | Outside VPC (no VPC config) |
| AWS Region | ap-northeast-1 |
| Intended benchmark period | 2026-05 |
| Benchmark run ID (planned) | `bench-s3ap-2026-05` (no measurement record committed for this ID) |

## Methodology

### Test Script

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

## Reference Results

> **Where these numbers come from** — the ListObjectsV2 and GetObject values are transcribed from measurements recorded in sibling repositories. They were not measured in this repository. "Effective Processing Rate" remains an unverified estimate; no reproducible measurement record (raw data or run logs) is committed to this repository, and the benchmark Lambda in the methodology above is not provided. None of these is a service limit or guarantee.

### Environment Differences

The two transcribed measurements differ from the test environment above as follows.

| Item | Test environment above | Transcribed measurements |
|---|---|---|
| FSx for ONTAP throughput capacity | 512 MBps | 128 MBps (Single-AZ) |
| Client | Lambda outside a VPC (256 MB) | Local workstation over the internet |
| S3 Access Points NetworkOrigin | Internet | Internet |

A measurement at 512 MBps from a Lambda outside a VPC has not been confirmed (tracked in [#98](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/98)). The source S3 Access Points benchmark records that over the internet, client-side bandwidth became the limit and the effect of 512 MBps was not visible.

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

```bash
# 1. Deploy the benchmark Lambda (template not yet available)
# Use the test script above in a Lambda function

# 2. Invoke with different object sizes
aws lambda invoke \
  --function-name fsxn-s3ap-benchmark \
  --payload '{"test": "list", "iterations": 20}' \
  response.json

aws lambda invoke \
  --function-name fsxn-s3ap-benchmark \
  --payload '{"test": "get", "prefix": "audit/svm-prod-01/2026/05/", "max_keys": 10}' \
  response.json

# 3. Record results with environment context
cat response.json | jq '.body'
```

## Related Documents

- [S3 AP Specification & Troubleshooting](s3ap-fsxn-specification.md)
- [Pipeline SLO](pipeline-slo.md)
- [Operational Guide](operational-guide.md)
