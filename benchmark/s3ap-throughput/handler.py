"""S3 Access Points read-throughput benchmark Lambda for FSx for ONTAP.

This handler measures ListObjectsV2 and GetObject latency against an FSx for
ONTAP S3 Access Point and reports percentile latency and derived throughput.
It performs the measurement only: deployment and invocation are driven
separately (see template.yaml and run-benchmark.sh), and the handler issues no
credential handling of its own, relying on the Lambda execution role through
the default boto3 provider chain.

The ``Bucket`` parameter passed to boto3 is the S3 Access Points alias OR ARN;
both are accepted directly as the ``Bucket`` argument by the S3 API.

Measurement semantics mirror docs/en/s3ap-throughput-benchmark.md:
  - "list": ListObjectsV2 latency over N iterations (p50/p99/mean/min/max ms).
  - "get": GetObject latency and throughput per object, grouped into size
    classes.

The statistics math is isolated in pure helpers (``percentile``,
``summarize_latencies``, ``throughput_mbps``) that take no AWS dependency, so it
is unit-testable offline.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

# Measurements are taken with time.perf_counter and reported in milliseconds.
_MS_PER_SECOND = 1000.0
_BYTES_PER_MB = 1024 * 1024


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Return the nearest-rank percentile of an already-sorted list.

    Uses a nearest-rank method that never indexes out of range on small N:
    for a sorted list of length ``n`` and a fraction ``p`` in [0, 1], the
    chosen index is ``min(n - 1, ceil(p * n) - 1)`` and is clamped to 0 at the
    low end. This avoids the off-by-one risk of ``sorted(xs)[int(n * p)]``,
    which indexes out of range when ``int(n * p) == n`` (for example p=0.99 at
    n>=100 would select index n and raise IndexError).

    Args:
        sorted_values: Latency samples sorted in ascending order. Must be
            non-empty.
        fraction: Percentile as a fraction, e.g. 0.5 for p50, 0.99 for p99.

    Returns:
        The sample at the nearest-rank position.

    Raises:
        ValueError: If ``sorted_values`` is empty.
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError("percentile requires at least one value")
    index = math.ceil(fraction * n) - 1
    if index < 0:
        index = 0
    if index > n - 1:
        index = n - 1
    return sorted_values[index]


def summarize_latencies(latencies_ms: list[float]) -> dict[str, float]:
    """Summarize a list of latency samples (milliseconds).

    Args:
        latencies_ms: Raw per-iteration latencies in milliseconds. Must be
            non-empty.

    Returns:
        A dict with p50_ms, p99_ms, mean_ms, min_ms and max_ms, each rounded to
        three decimal places.

    Raises:
        ValueError: If ``latencies_ms`` is empty.
    """
    if not latencies_ms:
        raise ValueError("summarize_latencies requires at least one sample")
    ordered = sorted(latencies_ms)
    return {
        "p50_ms": round(percentile(ordered, 0.5), 3),
        "p99_ms": round(percentile(ordered, 0.99), 3),
        "mean_ms": round(sum(ordered) / len(ordered), 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
    }


def throughput_mbps(size_bytes: float, mean_latency_ms: float) -> float:
    """Derive throughput in MB/s from mean latency and object size.

    Args:
        size_bytes: Mean object size in bytes.
        mean_latency_ms: Mean GetObject latency in milliseconds.

    Returns:
        Throughput in MB/s (binary megabytes), rounded to two decimals, or 0.0
        when latency is non-positive.
    """
    if mean_latency_ms <= 0:
        return 0.0
    megabytes = size_bytes / _BYTES_PER_MB
    seconds = mean_latency_ms / _MS_PER_SECOND
    return round(megabytes / seconds, 2)


def _size_label(size_bytes: int) -> str:
    """Group an object size into a rounded, human-readable size label.

    Args:
        size_bytes: Object size in bytes.

    Returns:
        A label such as "1KB", "100KB", "1MB", or "0B" for an empty object.
    """
    if size_bytes <= 0:
        return "0B"
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < _BYTES_PER_MB:
        return f"{round(size_bytes / 1024)}KB"
    return f"{round(size_bytes / _BYTES_PER_MB)}MB"


def _run_list(
    s3: Any, bucket: str, prefix: str, max_keys: int, iterations: int
) -> dict[str, Any]:
    """Measure ListObjectsV2 latency over N iterations.

    Args:
        s3: A boto3 S3 client.
        bucket: S3 Access Points alias or ARN.
        prefix: Key prefix to list under.
        max_keys: MaxKeys per ListObjectsV2 call.
        iterations: Number of calls to time.

    Returns:
        A dict with operation, iterations and the latency summary fields.
    """
    latencies_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=max_keys)
        latencies_ms.append((time.perf_counter() - start) * _MS_PER_SECOND)
    summary = summarize_latencies(latencies_ms)
    return {"operation": "ListObjectsV2", "iterations": iterations, **summary}


def _list_keys(s3: Any, bucket: str, prefix: str, max_keys: int) -> list[str]:
    """List up to ``max_keys`` object keys under a prefix (one call).

    Args:
        s3: A boto3 S3 client.
        bucket: S3 Access Points alias or ARN.
        prefix: Key prefix to list under.
        max_keys: Maximum number of keys to return.

    Returns:
        The object keys (excluding zero-length directory markers).
    """
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=max_keys)
    return [item["Key"] for item in resp.get("Contents", []) if not item["Key"].endswith("/")]


def _run_get(
    s3: Any,
    bucket: str,
    prefix: str,
    max_keys: int,
    iterations: int,
    size_labels: dict[str, str] | None,
) -> dict[str, Any]:
    """Measure GetObject latency and throughput per object and per size class.

    Lists up to ``max_keys`` keys under ``prefix`` and reads each key
    ``iterations`` times, fully draining the response body each time.

    Args:
        s3: A boto3 S3 client.
        bucket: S3 Access Points alias or ARN.
        prefix: Key prefix to list objects under.
        max_keys: Maximum number of keys to benchmark.
        iterations: GetObject repetitions per key.
        size_labels: Optional caller-supplied map of object key to size label.
            Keys not present fall back to a rounded-size label.

    Returns:
        A dict with a per-object array and a per-size-class summary.
    """
    keys = _list_keys(s3, bucket, prefix, max_keys)
    per_object: list[dict[str, Any]] = []
    for key in keys:
        latencies_ms: list[float] = []
        sizes: list[int] = []
        for _ in range(iterations):
            start = time.perf_counter()
            resp = s3.get_object(Bucket=bucket, Key=key)
            body = resp["Body"].read()
            latencies_ms.append((time.perf_counter() - start) * _MS_PER_SECOND)
            sizes.append(len(body))
        summary = summarize_latencies(latencies_ms)
        mean_size = sum(sizes) / len(sizes)
        label = (size_labels or {}).get(key) or _size_label(round(mean_size))
        per_object.append({
            "key": key,
            "size_bytes": int(round(mean_size)),
            "size_label": label,
            "iterations": iterations,
            "throughput_mbps": throughput_mbps(mean_size, summary["mean_ms"]),
            **summary,
        })
    return {
        "operation": "GetObject",
        "objects_measured": len(per_object),
        "per_object": per_object,
        "per_size_class": _summarize_size_classes(per_object),
    }


def _summarize_size_classes(per_object: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate per-object results into per-size-class summaries.

    Args:
        per_object: Per-object measurement dicts produced by ``_run_get``.

    Returns:
        One summary per size label, each with object_count, mean size,
        aggregate p50/p99/mean latency, and mean throughput.
    """
    classes: dict[str, list[dict[str, Any]]] = {}
    for obj in per_object:
        classes.setdefault(obj["size_label"], []).append(obj)
    summaries: list[dict[str, Any]] = []
    for label, objs in classes.items():
        p50s = [o["p50_ms"] for o in objs]
        p99s = [o["p99_ms"] for o in objs]
        means = [o["mean_ms"] for o in objs]
        tputs = [o["throughput_mbps"] for o in objs]
        sizes = [o["size_bytes"] for o in objs]
        summaries.append({
            "size_label": label,
            "object_count": len(objs),
            "mean_size_bytes": int(round(sum(sizes) / len(sizes))),
            "p50_ms": round(sum(p50s) / len(p50s), 3),
            "p99_ms": round(sum(p99s) / len(p99s), 3),
            "mean_ms": round(sum(means) / len(means), 3),
            "mean_throughput_mbps": round(sum(tputs) / len(tputs), 2),
        })
    summaries.sort(key=lambda s: s["mean_size_bytes"])
    return summaries


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point: route a benchmark event to the list or get test.

    Args:
        event: One of::

            {"test": "list", "bucket": "<alias-or-arn>", "prefix": "...",
             "iterations": 20, "max_keys": 100}
            {"test": "get", "bucket": "<alias-or-arn>", "prefix": "...",
             "max_keys": 10, "iterations": 5, "size_labels": {"<key>": "1MB"}}

            ``benchmark_run_id`` is echoed if present, otherwise generated.
        context: Lambda context (unused).

    Returns:
        A JSON-serializable dict with the test result plus benchmark_run_id,
        a UTC ISO-8601 timestamp, and the region.

    Raises:
        ValueError: For a missing ``bucket`` or an unknown ``test`` value.
    """
    test = event.get("test")
    bucket = event.get("bucket")
    if not bucket:
        raise ValueError("event must include 'bucket' (S3 Access Points alias or ARN)")

    prefix = event.get("prefix", "")
    iterations = int(event.get("iterations", 5))
    region = os.environ.get("AWS_REGION", "")
    s3 = boto3.client("s3")

    if test == "list":
        max_keys = int(event.get("max_keys", 100))
        result = _run_list(s3, bucket, prefix, max_keys, iterations)
    elif test == "get":
        max_keys = int(event.get("max_keys", 10))
        size_labels = event.get("size_labels")
        result = _run_get(s3, bucket, prefix, max_keys, iterations, size_labels)
    else:
        raise ValueError(f"unknown test {test!r}: expected 'list' or 'get'")

    return {
        "benchmark_run_id": event.get("benchmark_run_id") or f"bench-{uuid.uuid4().hex[:12]}",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "region": region,
        "result": result,
    }
