# Lakehouse Monitoring Patterns

🌐 [日本語](../ja/lakehouse-monitoring-patterns.md) | **English** (this page)

## Overview

This document defines five operational monitoring patterns for FSx for ONTAP environments integrated with lakehouse architectures (referenced from [fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations)). All patterns are vendor-neutral, built on Lambda + CloudWatch, with delivery paths to any observability backend via the OTLP/vendor-specific pipelines established in this project.

> **Relationship to audit log shipping**: The main project ships file access audit logs, EMS events, and FPolicy notifications to observability platforms. These lakehouse monitoring patterns complement that by providing **infrastructure-level operational metrics** — sync delays, latency, cache efficiency, anomaly detection, and cost visibility.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ Metric Sources                                                       │
├──────────────┬──────────────┬──────────────┬────────────┬───────────┤
│ CloudWatch   │ ONTAP REST   │ CloudTrail   │ Cost       │ FSx for ONTAP S3 AP │
│ Metrics      │ API          │ Data Events  │ Explorer   │ Latency   │
│ (DataSync/   │ (FlexCache)  │ (Anomaly)    │ (Cost)     │ (Custom)  │
│  SnapMirror) │              │              │            │           │
└──────┬───────┴──────┬───────┴──────┬───────┴─────┬──────┴─────┬─────┘
       │              │              │             │            │
       ▼              ▼              ▼             ▼            ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ Lambda: lakehouse-monitor (Python 3.12)                           │
  │   • Collects metrics from all sources                            │
  │   • Publishes to CloudWatch Custom Metrics                       │
  │   • Formats for OTLP / vendor-specific delivery                  │
  └──────────────────────────────────┬───────────────────────────────┘
                                     │
                        ┌────────────┼────────────┐
                        ▼            ▼            ▼
                  CloudWatch    CloudWatch    Observability
                  Alarms        Dashboard    Backend (OTLP)
                  (SNS)         (Cost/Perf)  (Grafana/Datadog/
                                              Splunk/Elastic)
```

## Pattern 1: DataSync / SnapMirror Sync Delay Monitoring

### Problem

When FSx for ONTAP data is replicated to S3 (via DataSync) or to another FSx file system (via SnapMirror), sync delays can cause lakehouse queries to operate on stale data. Without monitoring, staleness goes undetected until users report incorrect results.

### Data Sources

| Source | Metric | Access Method |
|--------|--------|---------------|
| AWS DataSync | `BytesTransferred`, `FilesTransferred`, task execution status | CloudWatch Metrics (`AWS/DataSync`) |
| SnapMirror | `lag-time`, `state`, `healthy` | ONTAP REST API (`/api/snapmirror/relationships`) |
| FSx CloudWatch | `DataReadBytes`, `DataWriteBytes` | CloudWatch Metrics (`AWS/FSx`) |

### Alarm Thresholds

| Metric | Warning | Critical | Rationale |
|--------|---------|----------|-----------|
| DataSyncLagSeconds | > 1800s (30 min) | > 3600s (1 hour) | Depends on the freshness requirement of your lakehouse queries |
| SnapMirrorLagSeconds | > 900s (15 min) | > 1800s (30 min) | Based on a typical SnapMirror RPO |

### Implementation

```python
# Lambda: collect_sync_metrics.py
import boto3
import urllib3
import json
import os
from datetime import datetime, timezone, timedelta

cw_client = boto3.client("cloudwatch")
http = urllib3.PoolManager()

ONTAP_MGMT_ENDPOINT = os.environ["ONTAP_MGMT_ENDPOINT"]
ONTAP_CREDENTIALS_SECRET_ARN = os.environ["ONTAP_CREDENTIALS_SECRET_ARN"]
NAMESPACE = "FSxONTAP/Lakehouse"


def lambda_handler(event, context):
    """Collect DataSync task status and SnapMirror lag metrics."""
    # 1. DataSync task execution lag
    datasync_lag = _check_datasync_lag()

    # 2. SnapMirror relationship lag (via ONTAP REST API)
    snapmirror_metrics = _check_snapmirror_lag()

    # 3. Publish to CloudWatch
    metrics = []

    if datasync_lag is not None:
        metrics.append({
            "MetricName": "DataSyncLagSeconds",
            "Value": datasync_lag,
            "Unit": "Seconds",
            "Dimensions": [{"Name": "Source", "Value": "DataSync"}],
        })

    for sm in snapmirror_metrics:
        metrics.append({
            "MetricName": "SnapMirrorLagSeconds",
            "Value": sm["lag_seconds"],
            "Unit": "Seconds",
            "Dimensions": [
                {"Name": "Source", "Value": "SnapMirror"},
                {"Name": "Relationship", "Value": sm["relationship"]},
            ],
        })

    if metrics:
        cw_client.put_metric_data(Namespace=NAMESPACE, MetricData=metrics)

    return {"collected": len(metrics)}
```

### CloudWatch Alarm

```yaml
DataSyncLagAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub '${AWS::StackName}-datasync-lag'
    AlarmDescription: 'DataSync sync lag exceeds threshold — lakehouse data may be stale'
    Namespace: FSxONTAP/Lakehouse
    MetricName: DataSyncLagSeconds
    Statistic: Maximum
    Period: 300
    EvaluationPeriods: 2
    Threshold: 3600  # 1 hour
    ComparisonOperator: GreaterThanThreshold
    AlarmActions:
      - !Ref AlertSNSTopic

SnapMirrorLagAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub '${AWS::StackName}-snapmirror-lag'
    AlarmDescription: 'SnapMirror lag exceeds threshold — replica may be stale'
    Namespace: FSxONTAP/Lakehouse
    MetricName: SnapMirrorLagSeconds
    Statistic: Maximum
    Period: 300
    EvaluationPeriods: 2
    Threshold: 1800  # 30 minutes
    ComparisonOperator: GreaterThanThreshold
    AlarmActions:
      - !Ref AlertSNSTopic
```

---

## Pattern 2: FSx for ONTAP S3 AP Latency Monitoring (p50/p99)

### Problem

FSx for ONTAP S3 Access Point read latency directly impacts lakehouse query performance. Unlike standard S3, FSx for ONTAP S3 AP latency depends on the file system's provisioned throughput and current load.

### Implementation

```python
# Lambda: measure_s3ap_latency.py
import boto3
import time
import os
import statistics

cw_client = boto3.client("cloudwatch")
s3_client = boto3.client("s3")

S3_AP_ARN = os.environ["S3_ACCESS_POINT_ARN"]
PROBE_KEY = os.environ.get("PROBE_OBJECT_KEY", ".latency-probe")
NAMESPACE = "FSxONTAP/Lakehouse"
SAMPLE_COUNT = 10


def lambda_handler(event, context):
    """Measure S3 AP read latency with multiple samples."""
    latencies = []

    for _ in range(SAMPLE_COUNT):
        start = time.perf_counter()
        try:
            s3_client.head_object(Bucket=S3_AP_ARN, Key=PROBE_KEY)
        except s3_client.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                pass  # Still measures API round-trip latency
            else:
                raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[-1]  # With 10 samples, last = worst case
    avg = statistics.mean(latencies)

    cw_client.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
            {"MetricName": "S3APLatencyP50Ms", "Value": p50, "Unit": "Milliseconds"},
            {"MetricName": "S3APLatencyP99Ms", "Value": p99, "Unit": "Milliseconds"},
            {"MetricName": "S3APLatencyAvgMs", "Value": avg, "Unit": "Milliseconds"},
        ],
    )

    return {"p50_ms": round(p50, 2), "p99_ms": round(p99, 2), "avg_ms": round(avg, 2)}
```

### Alarm Thresholds

| Metric | Warning | Critical | Rationale |
|--------|---------|----------|-----------|
| S3APLatencyP50Ms | > 100ms | > 500ms | Normal FSx for ONTAP S3 AP reads are 20-80ms |
| S3APLatencyP99Ms | > 500ms | > 2000ms | Tail latency indicates throughput saturation |

> **Note**: Baseline these thresholds against your FSx file system's provisioned throughput tier. Higher throughput tiers will have lower baseline latency.

---

## Pattern 3: FlexCache Hit Rate Monitoring

### Problem

FlexCache accelerates read access to remote volumes. A declining hit rate indicates cache warming issues, working set changes, or capacity constraints.

### Data Source

ONTAP REST API: `GET /api/storage/flexcache/flexcaches/{uuid}?fields=**`

### Alarm Thresholds

| Metric | Warning | Critical | Rationale |
|--------|---------|----------|-----------|
| FlexCacheHitRatePercent | < 70% | < 50% | Below 70% starts to affect query performance |

> **ONTAP REST API access**
>
> The Lambda has to reach the FSx for ONTAP management endpoint. Deploy it in a VPC that can route to the management IP.

### Implementation

```python
# Lambda: collect_flexcache_metrics.py
def _collect_flexcache_metrics():
    """Query ONTAP REST API for FlexCache statistics."""
    creds = _get_ontap_credentials()
    url = f"https://{ONTAP_MGMT_ENDPOINT}/api/storage/flexcache/flexcaches"
    headers = urllib3.make_headers(basic_auth=f"{creds['username']}:{creds['password']}")
    headers["Accept"] = "application/json"

    response = http.request("GET", url, headers=headers, timeout=10.0)
    if response.status != 200:
        return []

    data = json.loads(response.data)
    metrics = []

    for cache in data.get("records", []):
        uuid = cache["uuid"]
        detail_url = f"https://{ONTAP_MGMT_ENDPOINT}/api/storage/flexcache/flexcaches/{uuid}?fields=**"
        detail_resp = http.request("GET", detail_url, headers=headers, timeout=10.0)

        if detail_resp.status == 200:
            detail = json.loads(detail_resp.data)
            hit_rate = 100.0 - detail.get("cache_miss_percent", 0)
            metrics.append({
                "MetricName": "FlexCacheHitRatePercent",
                "Value": hit_rate,
                "Unit": "Percent",
                "Dimensions": [
                    {"Name": "CacheName", "Value": detail.get("name", uuid)},
                    {"Name": "SVM", "Value": detail.get("svm", {}).get("name", "unknown")},
                ],
            })

    return metrics
```

> **ONTAP REST API access**: The Lambda must reach the FSx for ONTAP management endpoint. Deploy in a VPC with access to the management IP. Credentials stored in Secrets Manager with `shared/python/auth_cache.py` pattern.

---

## Pattern 4: Unstructured Data Access Anomaly Detection

### Problem

Unusual access patterns on FSx S3 Access Points (bulk downloads, new principals, off-hours access) may indicate data exfiltration or misconfigured jobs.

### Data Source

CloudTrail S3 data events for the FSx S3 Access Point.

### Detection Rules

| Check | Condition | Severity |
|-------|-----------|----------|
| Out-of-hours access | Access outside 06:00-22:00 | Warning |
| Volume spike | More than 1000 objects/hour per principal | Warning |
| New principal | A principal absent from the baseline | Info |

### Implementation

```python
# Lambda: detect_access_anomalies.py
# Triggered by EventBridge rule on CloudTrail S3 data events

ANOMALY_CHECKS = [
    "off_hours_access",      # Access outside business hours
    "volume_spike",          # Principal exceeds N objects/hour
    "new_principal",         # Principal not seen in baseline
]

def lambda_handler(event, context):
    """Detect anomalous S3 AP access patterns from CloudTrail events."""
    anomalies = []
    detail = event.get("detail", {})
    principal = detail.get("userIdentity", {}).get("arn", "unknown")
    event_time = detail.get("eventTime", "")

    # Check 1: Off-hours access
    if _is_off_hours(event_time):
        anomalies.append({"type": "off_hours_access", "principal": principal})

    # Check 2: Volume spike (DynamoDB counter)
    count = _increment_access_count(principal)
    if count > MAX_OBJECTS_PER_PRINCIPAL_PER_HOUR:
        anomalies.append({"type": "volume_spike", "principal": principal, "count": count})

    # Check 3: New principal
    if not _is_known_principal(principal):
        anomalies.append({"type": "new_principal", "principal": principal})

    # Publish metric
    cw_client.put_metric_data(
        Namespace="FSxONTAP/Lakehouse",
        MetricData=[{"MetricName": "AccessAnomalyCount", "Value": len(anomalies), "Unit": "Count"}],
    )

    return {"anomalies_detected": len(anomalies)}
```

### EventBridge Rule

```yaml
CloudTrailS3APRule:
  Type: AWS::Events::Rule
  Properties:
    EventPattern:
      source: ["aws.s3"]
      detail-type: ["AWS API Call via CloudTrail"]
      detail:
        eventSource: ["s3.amazonaws.com"]
        eventName: ["GetObject", "ListObjects", "ListObjectsV2"]
    Targets:
      - Arn: !GetAtt AnomalyDetectorFunction.Arn
        Id: anomaly-detector
```

---

## Pattern 5: Storage Cost Trend Dashboard

### Problem

Lakehouse architectures need visibility into cost distribution between FSx for ONTAP and S3 to optimize tiering decisions.

### Implementation

```python
# Lambda: collect_cost_metrics.py
def _collect_storage_costs():
    """Query Cost Explorer for FSx and S3 costs."""
    ce_client = boto3.client("ce")
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

    response = ce_client.get_cost_and_usage(
        TimePeriod={"Start": start_date, "End": end_date},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        Filter={
            "Or": [
                {"Dimensions": {"Key": "SERVICE", "Values": ["Amazon FSx"]}},
                {"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Simple Storage Service"]}},
                {"Dimensions": {"Key": "SERVICE", "Values": ["AWS DataSync"]}},
            ]
        },
    )

    # Publish daily costs as CloudWatch metrics
    for result in response.get("ResultsByTime", []):
        for group in result.get("Groups", []):
            service = group["Keys"][0]
            cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
            cw_client.put_metric_data(
                Namespace="FSxONTAP/Lakehouse",
                MetricData=[{
                    "MetricName": "DailyStorageCost",
                    "Value": cost,
                    "Unit": "None",
                    "Dimensions": [{"Name": "Service", "Value": service}],
                }],
            )
```

### CloudWatch Dashboard (5 panels)

| Panel | Metric | Visualization |
|-------|--------|---------------|
| Daily Storage Cost | `DailyStorageCost` by Service | Time series (FSx vs S3 vs DataSync) |
| FSx for ONTAP S3 AP Latency | `S3APLatencyP50Ms`, `S3APLatencyP99Ms` | Time series with threshold annotations |
| FlexCache Hit Rate | `FlexCacheHitRatePercent` | Gauge with 70% warning line |
| Sync Lag | `DataSyncLagSeconds`, `SnapMirrorLagSeconds` | Time series |
| Access Anomalies | `AccessAnomalyCount` | Time series (should be 0 normally) |

---

## Vendor Delivery Paths

All patterns publish to CloudWatch Custom Metrics (`FSxONTAP/Lakehouse` namespace):

| Backend | Integration Method | Setup |
|---------|-------------------|-------|
| **Grafana Cloud** | CloudWatch data source (native) | Add AWS CloudWatch data source → select namespace |
| **Datadog** | AWS Integration (auto-collects custom namespaces) | Enable AWS Integration |
| **Splunk** | Splunk Add-on for AWS → CloudWatch inputs | Configure `aws_cloudwatch` input |
| **Elastic** | Metricbeat `aws` module | Configure `cloudwatch` metricset |
| **OTel Collector** | `awscloudwatchreceiver` | Scrape `FSxONTAP/Lakehouse` namespace |

---

## Deployment

```bash
aws cloudformation deploy \
  --template-file shared/templates/lakehouse-monitoring.yaml \
  --stack-name fsxn-lakehouse-monitoring \
  --parameter-overrides \
    OntapMgmtEndpoint=<management-ip> \
    OntapCredentialsSecretArn=<secret-arn> \
    S3AccessPointArn=<s3-ap-arn> \
    DataSyncTaskArn=<task-arn> \
    AlertEmail=<email> \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

> **This stack does not ship a collector.** The template creates the schedule,
> the alarms, the dashboard and the DLQ, but its Lambda code is a placeholder that
> raises `NotImplementedError`. The metrics depend on your lakehouse layout —
> which SnapMirror relationships, which FlexCache volumes, which DataSync task —
> so there is no single implementation to ship.
>
> Upload a collector after deploying:
>
> ```bash
> aws lambda update-function-code \
>   --function-name fsxn-lakehouse-monitoring-collector \
>   --zip-file fileb://collector.zip
> ```
>
> It must publish `SnapMirrorLagSeconds`, `FlexCacheHitRate` and
> `AccessAnomalyCount` to the `FSxONTAP/Lakehouse` namespace, which is what the
> three alarms read.
>
> The placeholder raises rather than returning success so the gap is visible: the
> failed invocation lands in the DLQ, `-dlq-depth` fires on the first schedule
> tick, and you get an email. A stub returning `200` would have left the alarms in
> `INSUFFICIENT_DATA` and the dashboard empty with nothing explaining why.

## Prerequisites

- FSx for ONTAP with management endpoint accessible from Lambda VPC
- ONTAP REST API credentials in Secrets Manager (read-only access)
- S3 Access Point for the lakehouse data path
- A collector implementation to upload (see the note above)
- (Optional) DataSync task ARN for sync lag monitoring
- (Optional) CloudTrail with S3 data events for anomaly detection

## Related Documents

- [Pipeline SLO Definitions](pipeline-slo.md)
- [S3 AP Specification](s3ap-fsxn-specification.md)
- [Vendor Comparison](vendor-comparison.md)
- [fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations)
