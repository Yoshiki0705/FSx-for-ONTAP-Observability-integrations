# レイクハウス監視パターン

🌐 **日本語**（このページ） | [English](../en/lakehouse-monitoring-patterns.md)

## 概要

本ドキュメントは、レイクハウスアーキテクチャと統合された FSx for ONTAP 環境向けの5つの運用監視パターンを定義します（[fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations) から参照）。全パターンはベンダー中立で、Lambda + CloudWatch をベースに、本プロジェクトで確立した OTLP/ベンダー固有パイプライン経由で任意のオブザーバビリティバックエンドに配信可能です。

> **監査ログ配信との関係**
>
> メインプロジェクトはファイルアクセス監査ログ、EMS イベント、FPolicy 通知をオブザーバビリティプラットフォームに配信します。これらのレイクハウス監視パターンは、**インフラストラクチャレベルの運用メトリクス**（同期遅延、レイテンシー、キャッシュ効率、異常検知、コスト可視化）を提供することで補完します。

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────────┐
│ メトリクスソース                                                      │
├──────────────┬──────────────┬──────────────┬────────────┬───────────┤
│ CloudWatch   │ ONTAP REST   │ CloudTrail   │ Cost       │ FSx for ONTAP S3 AP │
│ メトリクス    │ API          │ データイベント │ Explorer   │ レイテンシー│
│ (DataSync/   │ (FlexCache)  │ (異常検知)    │ (コスト)    │ (カスタム) │
│  SnapMirror) │              │              │            │           │
└──────┬───────┴──────┬───────┴──────┬───────┴─────┬──────┴─────┬─────┘
       │              │              │             │            │
       ▼              ▼              ▼             ▼            ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ Lambda: lakehouse-monitor (Python 3.12)                           │
  │   • 全ソースからメトリクスを収集                                    │
  │   • CloudWatch カスタムメトリクスに発行                             │
  │   • OTLP / ベンダー固有配信用にフォーマット                         │
  └──────────────────────────────────┬───────────────────────────────┘
                                     │
                        ┌────────────┼────────────┐
                        ▼            ▼            ▼
                  CloudWatch    CloudWatch    オブザーバビリティ
                  アラーム      ダッシュボード  バックエンド (OTLP)
                  (SNS)         (コスト/性能)  (Grafana/Datadog/
                                              Splunk/Elastic)
```

## パターン 1: DataSync / SnapMirror 同期遅延監視

### 課題

FSx for ONTAP のデータが S3（DataSync 経由）または別の FSx ファイルシステム（SnapMirror 経由）にレプリケートされる場合、同期遅延によりレイクハウスクエリが古いデータで動作する可能性があります。

### データソース

| ソース | メトリクス | アクセス方法 |
|--------|--------|---------------|
| AWS DataSync | `BytesTransferred`, タスク実行ステータス | CloudWatch メトリクス (`AWS/DataSync`) |
| SnapMirror | `lag-time`, `state`, `healthy` | ONTAP REST API (`/api/snapmirror/relationships`) |
| FSx CloudWatch | `DataReadBytes`, `DataWriteBytes` | CloudWatch メトリクス (`AWS/FSx`) |

### アラーム閾値

| メトリクス | 警告 | 重大 | 根拠 |
|-----------|------|------|------|
| DataSyncLagSeconds | > 1800秒 (30分) | > 3600秒 (1時間) | レイクハウスクエリの鮮度要件に依存 |
| SnapMirrorLagSeconds | > 900秒 (15分) | > 1800秒 (30分) | SnapMirror の通常 RPO に基づく |

### 実装

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

### CloudWatch アラーム

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

## パターン 2: FSx for ONTAP S3 AP レイテンシー監視 (p50/p99)

### 課題

FSx for ONTAP S3 Access Point の読み取りレイテンシーは、レイクハウスクエリのパフォーマンスに直接影響します。標準 S3 とは異なり、FSx for ONTAP S3 AP のレイテンシーはファイルシステムのプロビジョンドスループットと現在の負荷に依存します。

### アラーム閾値

| メトリクス | 警告 | 重大 | 根拠 |
|-----------|------|------|------|
| S3APLatencyP50Ms | > 100ms | > 500ms | 通常の FSx for ONTAP S3 AP 読み取りは 20-80ms |
| S3APLatencyP99Ms | > 500ms | > 2000ms | テールレイテンシーはスループット飽和を示す |

> **注意**
>
> FSx ファイルシステムのプロビジョンドスループットティアに基づいてベースラインを設定してください。

### 実装

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

---

## パターン 3: FlexCache ヒット率監視

### 課題

FlexCache はリモートボリュームへの読み取りアクセスを高速化します。ヒット率の低下は、キャッシュウォーミングの問題、ワーキングセットの変化、または容量制約を示します。

### データソース

ONTAP REST API: `GET /api/storage/flexcache/flexcaches/{uuid}?fields=**`

### アラーム閾値

| メトリクス | 警告 | 重大 | 根拠 |
|-----------|------|------|------|
| FlexCacheHitRatePercent | < 70% | < 50% | 70% 未満はクエリパフォーマンスに影響 |

> **ONTAP REST API アクセス**
>
> Lambda は FSx for ONTAP 管理エンドポイントに到達する必要があります。管理 IP にアクセス可能な VPC にデプロイしてください。

### 実装

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

---

## パターン 4: 非構造化データアクセス異常検知

### 課題

レイクハウスクエリが S3 Access Point 経由で FSx for ONTAP データにアクセスする際、異常なアクセスパターン（大量ダウンロード、新規プリンシパル、営業時間外アクセス）はデータ流出や不正アクセスを示す可能性があります。

### データソース

FSx S3 Access Point に対する CloudTrail S3 データイベント。

### 検知ルール

| チェック | 条件 | 重大度 |
|---------|------|--------|
| 営業時間外アクセス | 6:00-22:00 以外のアクセス | Warning |
| ボリュームスパイク | プリンシパルあたり 1000 オブジェクト/時間超過 | Warning |
| 新規プリンシパル | ベースラインに存在しないプリンシパル | Info |

### 実装

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

### EventBridge ルール

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

## パターン 5: ストレージコスト推移ダッシュボード

### 課題

FSx for ONTAP + S3 を使用するレイクハウスアーキテクチャでは、コスト配分の可視化が必要です。

### 実装

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

### CloudWatch ダッシュボード（5パネル）

| パネル | メトリクス | 可視化 |
|-------|--------|--------|
| 日次ストレージコスト | `DailyStorageCost` (Service別) | 時系列 (FSx vs S3 vs DataSync) |
| FSx for ONTAP S3 AP レイテンシー | `S3APLatencyP50Ms`, `S3APLatencyP99Ms` | 時系列 + 閾値アノテーション |
| FlexCache ヒット率 | `FlexCacheHitRatePercent` | ゲージ + 70% 警告ライン |
| 同期遅延 | `DataSyncLagSeconds`, `SnapMirrorLagSeconds` | 時系列 |
| アクセス異常 | `AccessAnomalyCount` | 時系列（通常は 0） |

---

## ベンダー配信パス

全パターンは CloudWatch カスタムメトリクス（`FSxONTAP/Lakehouse` 名前空間）に発行します：

| バックエンド | 統合方法 | セットアップ |
|------------|---------|------------|
| **Grafana Cloud** | CloudWatch データソース（ネイティブ） | AWS CloudWatch データソース追加 → 名前空間選択 |
| **Datadog** | AWS Integration（カスタム名前空間自動収集） | AWS Integration 有効化 |
| **Splunk** | Splunk Add-on for AWS → CloudWatch 入力 | `aws_cloudwatch` 入力設定 |
| **Elastic** | Metricbeat `aws` モジュール | `cloudwatch` metricset 設定 |
| **OTel Collector** | `awscloudwatchreceiver` | `FSxONTAP/Lakehouse` 名前空間をスクレイプ |

---

## デプロイ

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

> **このスタックはコレクターを同梱していません。** テンプレートはスケジュール、
> アラーム、ダッシュボード、DLQ を作成しますが、Lambda のコードは
> `NotImplementedError` を送出するプレースホルダーです。メトリクスはレイクハウスの
> 構成（どの SnapMirror 関係か、どの FlexCache ボリュームか、どの DataSync タスクか）
> に依存するため、同梱できる単一の実装が存在しません。
>
> デプロイ後にコレクターをアップロードしてください:
>
> ```bash
> aws lambda update-function-code \
>   --function-name fsxn-lakehouse-monitoring-collector \
>   --zip-file fileb://collector.zip
> ```
>
> 3 つのアラームが読む `SnapMirrorLagSeconds` / `FlexCacheHitRate` /
> `AccessAnomalyCount` を `FSxONTAP/Lakehouse` 名前空間に発行する必要があります。
>
> プレースホルダーが成功を返さず送出するのは、この欠落を可視化するためです。失敗した
> 呼び出しは DLQ に入り、最初のスケジュール実行で `-dlq-depth` が発火してメールが
> 届きます。`200` を返すスタブだと、アラームは `INSUFFICIENT_DATA` のまま、
> ダッシュボードは空のまま、理由を示すものが何もない状態になります。

## 前提条件

- Lambda VPC からアクセス可能な管理エンドポイントを持つ FSx for ONTAP
- Secrets Manager に保存された ONTAP REST API 認証情報（読み取り専用アクセス）
- レイクハウスデータパス用の S3 Access Point
- アップロードするコレクターの実装（上記の注記を参照）
- （オプション）同期遅延監視用の DataSync タスク ARN
- （オプション）異常検知用の CloudTrail S3 データイベント有効化

## 関連ドキュメント

- [パイプライン SLO 定義](pipeline-slo.md)
- [S3 AP 仕様](s3ap-fsxn-specification.md)
- [ベンダー比較](vendor-comparison.md)
- [fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations)
