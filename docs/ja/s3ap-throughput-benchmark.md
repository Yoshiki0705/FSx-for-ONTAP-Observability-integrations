# S3 Access Point 読み取りスループットベンチマーク

🌐 **日本語**（このページ） | [English](../en/s3ap-throughput-benchmark.md)

## 目的

本ドキュメントは、S3 Access Points 経由で FSx for ONTAP 監査ログを読み取る際のベンチマーク手法と参考結果を提供します。これらの結果は**サイジングの参考値であり、サービス上限ではありません**。

> **注意**
>
> 結果は以下に記載するテスト環境に固有のものです。実際のスループットは、FSx スループットキャパシティ、オブジェクトサイズ分布、ネットワーク経路、同時実行数、ワークロード構成によって異なります。必ず自身の環境で検証してください。

## テスト環境

| パラメータ | 値 |
|-----------|-------|
| FSx for ONTAP スループットキャパシティ | 512 MB/s |
| SVM 数 | 1 |
| S3 Access Point タイプ | Internet-origin |
| Lambda メモリ | 256 MB |
| Lambda 配置 | VPC 外（VPC 設定なし） |
| AWS リージョン | ap-northeast-1 |
| 想定ベンチマーク時期 | 2026-05 |
| ベンチマーク実行 ID（計画） | `bench-s3ap-2026-05`（この ID に対応する測定記録は未収録） |

## 測定方法

### テストスクリプト

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

### オブジェクトサイズカテゴリ

| カテゴリ | 一般的なサイズ | 説明 |
|----------|-------------|-------------|
| Small | 1-10 KB | 単一監査イベント（JSON） |
| Medium | 100 KB - 1 MB | ローテーション済み監査ログファイル（典型的） |
| Large | 1-5 MB | 高アクティビティ期間のログファイル |

## 参考結果

> **この節の出所** — ListObjectsV2 と GetObject の値は、姉妹リポジトリが記録した実測値の転記です。このリポジトリ自身の測定ではありません。「実効処理レート」は未確認の推定値のままで、再現可能な測定記録（生データ・実行ログ）はこのリポジトリに未収録です。上記「測定方法」のベンチマーク Lambda テンプレートも未提供です。いずれもサービス上限でも保証値でもありません。

### 測定環境の差

転記した 2 つの実測は、上記「テスト環境」と次の点で条件が違います。

| 項目 | 上記「テスト環境」 | 転記した実測 |
|---|---|---|
| FSx for ONTAP のスループットキャパシティ | 512 MBps | 128 MBps（Single-AZ） |
| クライアント | VPC 外の Lambda（256 MB） | ローカルのワークステーションからインターネット経由 |
| S3 Access Points の NetworkOrigin | Internet | Internet |

512 MBps・VPC 外の Lambda の条件での測定は未確認です（[#98](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/98) で追跡）。転記元の S3 Access Points のベンチマークは、インターネット経由ではクライアント側の帯域が律速になり、512 MBps の効果が見えなかったと記録しています。

### ListObjectsV2（100 キー）

| メトリクス | 値 |
|--------|-------|
| 中央値レイテンシ | 52.0 ms（最小 49.2 ms、最大 62.1 ms） |
| p99 レイテンシ | 未確認（5 試行のため算出していない） |
| リクエストあたりキー数 | 100（1 回の呼び出し） |

出典: [BENCH-S3AP-LIST-001](https://github.com/Yoshiki0705/FSx-for-ONTAP-Lakehouse-Integrations/blob/main/verification-pack/s3ap-list-latency/evidence/2026-08-05/benchmark-result.yaml)（2026-08-05、ap-northeast-1、SINGLE_AZ_1、128 MBps、インターネット経由、5 試行、1 つのプレフィックスにすべてのオブジェクトを置いた配置）。

### GetObject（サイズ別）

| オブジェクトサイズ | P50 レイテンシ | 最大レイテンシ | 平均スループット |
|-------------|-------------|-------------|-----------|
| 1 KB | 30.5 ms | 117.1 ms | 0.03 MB/s |
| 100 KB | 34.1 ms | 59.2 ms | 2.7 MB/s |
| 1 MB | 48.5 ms | 83.7 ms | 18.1 MB/s |
| 5 MB | 111.0 ms | 172.3 ms | 41.8 MB/s |

出典: [S3 Access Points のベンチマーク結果](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/docs/s3ap-benchmark-results.md)の GetObject の表（2026-05-22、ap-northeast-1、Single-AZ（First-generation）、128 MBps、macOS + boto3 1.34.x、インターネット経由、同時実行数 1、5〜10 回反復）。上の監査ログのサイズ区分（約 5 KB・約 200 KB・約 2 MB）そのものは測定していません。p99 は上記の条件では未確認です。

### 実効処理レート

> **未確認の推定値** — 下表はサイジングの桁感で、測定記録はありません。

監査ログポーラー Lambda（256 MB、VPC 外）の場合:

| シナリオ | ファイル数/呼び出し | 所要時間 | 備考 |
|----------|-----------------|----------|-------|
| 10 small ファイル（各 5 KB） | 10 | ~3-5 秒 | 5 分タイムアウト内で十分 |
| 50 medium ファイル（各 200 KB） | 50 | ~15-30 秒 | 余裕あり |
| 100 medium ファイル（各 200 KB） | 100 | ~30-60 秒 | MAX_KEYS_PER_RUN デフォルト |
| 100 large ファイル（各 2 MB） | 100 | ~60-120 秒 | タイムアウト延長が必要な場合あり |

## スループットに影響する要因

### FSx スループットキャパシティ

FSx for ONTAP のスループットキャパシティは NFS、SMB、S3 AP アクセスで共有されます。本番ワークロードがスループットを消費している場合、S3 AP の読み取りは遅くなります。

| FSx スループットキャパシティ | S3 AP への影響 |
|------------------------|----------------------|
| 128 MB/s | 監査読み取りが本番と競合する可能性あり |
| 512 MB/s | 監査読み取りが本番に影響する可能性は低い |
| 2048 MB/s | 測定可能な影響なし |

### ネットワーク経路

| Lambda 配置 | S3 AP アクセス | レイテンシへの影響 |
|-----------------|-------------|----------------|
| VPC 外 | 直接（Internet-origin AP） | 最小レイテンシ |
| VPC 内 + NAT Gateway | NAT 経由 | リクエストあたり +10-30 ms |
| VPC 内 + Gateway EP のみ | タイムアウト（Internet-origin AP） | 動作しない |

### 同時実行

監査ポーラーは `ReservedConcurrentExecutions: 1` を使用して実行の重複を防止しています。これは各呼び出し内でファイルを逐次処理することを意味します。より高いスループットが必要な場合:
- Lambda メモリを増加（CPU 増加 = 処理高速化）
- 単一呼び出し内で `ThreadPoolExecutor` を使用して並列 GetObject
- SQS ベースのファンアウトで並列ファイル処理

## 推奨事項

### 一般的なデプロイ（< 100 ファイル/5 分）

デフォルト設定で十分です:
- `MAX_KEYS_PER_RUN=100`
- `SAFETY_THRESHOLD_MS=30000`
- Lambda メモリ: 256 MB
- Lambda タイムアウト: 300 秒

### 大量デプロイ（> 100 ファイル/5 分）

選択肢:
1. **スケジュール頻度を上げる**: `rate(5 minutes)` の代わりに `rate(1 minute)`
2. **Lambda メモリを増加**: 512 MB または 1024 MB で CPU 増強
3. **並列 GetObject**: ThreadPoolExecutor を使用（同時実行数 5-10）
4. **SQS ファンアウト**: 1 つの Lambda でファイル一覧取得、並列ワーカーで処理

### スループットの監視

パイプラインスループットを追跡するために以下の CloudWatch カスタムメトリクスを追加:

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

## 自環境でのベンチマーク実行

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

## 関連ドキュメント

- [S3 AP 仕様 & トラブルシューティング](s3ap-fsxn-specification.md)
- [パイプライン SLO](pipeline-slo.md)
- [運用ガイド](operational-guide.md)
