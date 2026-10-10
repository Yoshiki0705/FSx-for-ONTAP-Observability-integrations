# S3 Access Point 読み取りスループットベンチマーク

🌐 **日本語**（このページ） | [English](../en/s3ap-throughput-benchmark.md)

## 目的

本ドキュメントは、S3 Access Points 経由で Amazon FSx for NetApp ONTAP の監査ログを読み取る際のベンチマーク手法と参考結果を提供します。これらの結果は**サイジングの参考値であり、サービス上限ではありません**。

「本リポジトリでの実測（2026-10-10）」の節が、このリポジトリにある、S3 Access Points 読み取りレイテンシの唯一の測定です。生データは `benchmark/s3ap-throughput/results/2026-10-10/` にコミットしています。「参考結果」の節の値は、姉妹リポジトリからの転記です。

> **注意**
>
> 結果は以下に記載するテスト環境に固有のものです。実際のスループットは、FSx スループットキャパシティ、オブジェクトサイズ分布、ネットワーク経路、同時実行数、ワークロード構成によって異なります。必ず自身の環境で検証してください。

## テスト環境

下表は 2026-10-10 の測定の環境です。

| パラメータ | 値 |
|-----------|-------|
| 測定日 | 2026-10-10 |
| ファイルシステム | SINGLE_AZ_1（第 1 世代）、HA ペア 1 組、SSD 1024 GiB |
| FSx for ONTAP スループットキャパシティ | 128 MBps |
| SVM とボリューム | 1 つの SVM の 1 つのボリューム（ファイルシステムには複数の SVM がある） |
| ボリュームの階層化ポリシー | AUTO、クーリング期間 31 日（2026-10-10 に FSx API で読み取り） |
| 読み取り時のデータ階層 | SSD（プライマリ階層）。ポリシーとオブジェクトの経過時間からの推定（読み取りの数分前に書き込み、クーリング期間は 31 日）。階層別の容量メトリクスでの確認はしておらず、実行中にも取得していない |
| ONTAP バージョン | 9.18.1P6。2026-10-06 に、このファイルシステムの ONTAP REST API で読み取った値（測定当日には再確認していない） |
| S3 Access Points のタイプ | ONTAP、インターネットオリジン、ファイルシステム ID は UNIX ユーザー `root` |
| クライアント | `benchmark/s3ap-throughput/` のツールを Lambda 関数としてデプロイ |
| Lambda メモリとタイムアウト | 256 MB、300 秒 |
| Lambda 配置 | VPC 外（VPC 設定なし） |
| AWS リージョン | ap-northeast-1 |
| テストオブジェクト | ランダムバイト列。1 KB・100 KB・1 MB・5 MB を各 3 個、サイズごとに別のプレフィックス |
| ベンチマーク実行 ID | `bench-s3ap-2026-10-10`（実行 1）、`bench-s3ap-2026-10-10-r2`（実行 2） |

## 測定方法

### テストスクリプト

下のスクリプトは測定方法の例示です。2026-10-10 の数値を出したのは `benchmark/s3ap-throughput/` に収録したツールの当時の版で、スクリプトとは、数値の読み方に関わる 2 点が違います。p50 と p99 を `statistics.median` と `int(iterations * 0.99)` の添字ではなく nearest-rank 法で求めること、オブジェクトごとの結果に加えてサイズ区分ごとの集計を返すことです。

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

## 本リポジトリでの実測（2026-10-10）

根拠区分は `verified` で、対象は次の 1 文だけです。上記「テスト環境」で、2026-10-10 に、これらのレイテンシを観測しました。他のファイルシステム、同時実行数、ネットワーク経路、日付には及びません。測定は同じ日に約 20 分あけて 2 回取りました（生データの時刻は UTC の 08:09 と 08:29）。したがって数値は安定値ではなく、観測した幅を示します。

ボリュームの階層化ポリシーは AUTO、クーリング期間は 31 日で、テストオブジェクトは読み取りの数分前に書き込んでいます。このため、読み取りは SSD（プライマリ階層）から返されたと推定しています。これはポリシーとオブジェクトの経過時間からの推定で、実行中に階層別の容量メトリクスは取得していません。

### 集計方法

各オブジェクトを 10 回、順番に読みました（同時実行数 1）。ListObjectsV2 は 20 回呼び、MaxKeys は上書きしていないのでツールの既定値 100 が効いています。この測定を行った版のツールは、オブジェクトごとに nearest-rank 法の p50・p99 と平均を計算しました。下表のサイズ区分ごとの p50 と p99 は、3 個のオブジェクトそれぞれの p50 と p99 の単純平均です。プールした 30 サンプルのパーセンタイルではありません。後続の版のツールは、区分ごとの値をプールしたサンプルから計算し、各区分に `percentile_method: "pooled_nearest_rank"` を付けます（[#147](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/147)）。2026-10-10 の生データにこのキーはありません。そのため、後続の版が出す区分の値は、下表と直接は比較できません。オブジェクト 1 個のスループットはそのオブジェクトの平均レイテンシとサイズから求め、区分の値は 3 個の平均です。ここでの MB/s は、ツールと同じく MiB/s（サイズを 1,048,576 バイトで割った値）です。

### 実行 1 と実行 2 の結果

実行 1（`bench-s3ap-2026-10-10`）:

| 操作 | p50 (ms) | p99 (ms) | 平均 (ms) | ストリームあたり平均スループット (MB/s) |
|-----------|---------|---------|-----------|------------------------------------|
| ListObjectsV2（20 回、13 キー） | 29.788 | 278.046 | 44.2 | 該当なし |
| GetObject 1 KB | 40.577 | 43.14 | 40.753 | 0.02 |
| GetObject 100 KB | 49.89 | 58.157 | 50.798 | 1.93 |
| GetObject 1 MB | 60.388 | 74.535 | 62.883 | 15.94 |
| GetObject 5 MB | 119.753 | 191.688 | 131.909 | 38.13 |

実行 2（`bench-s3ap-2026-10-10-r2`）:

| 操作 | p50 (ms) | p99 (ms) | 平均 (ms) | ストリームあたり平均スループット (MB/s) |
|-----------|---------|---------|-----------|------------------------------------|
| ListObjectsV2（20 回、13 キー） | 27.415 | 402.188 | 48.501 | 該当なし |
| GetObject 1 KB | 40.987 | 44.969 | 41.473 | 0.02 |
| GetObject 100 KB | 38.267 | 49.412 | 39.68 | 2.46 |
| GetObject 1 MB | 48.464 | 69.393 | 51.494 | 19.53 |
| GetObject 5 MB | 106.884 | 444.951 | 154.394 | 34.85 |

2 回の間で、p50 の変化は 0.4〜12.9 ms でした（最大は 5 MB の 119.753 から 106.884 ms）。p99 の変化は 1.8〜253.3 ms でした（ListObjectsV2 は 278.046 から 402.188 ms、5 MB は 191.688 から 444.951 ms）。

### この測定の限界

- p99 は参考値にとどまります。オブジェクトごとの p99 は 10 サンプルの最大値で、ListObjectsV2 の p99 は 20 サンプルの最大値です。実行 2 の 5 MB 区分の p99（444.951 ms）は、3 個のオブジェクトの最大値（152.683、361.481、820.689 ms）の平均で、1 個のオブジェクトの 1 回の遅い読み取りが大半を決めています。生データは要約のみでイテレーションごとのサンプルを残していないため、遅いサンプルの原因は後から確認できません。
- 2 回の測定では統計的な安定性は示せません。分散、信頼区間、安定した p99 のいずれも主張しません。
- ListObjectsV2 の測定では、12 個のテストオブジェクトに加えて、既存の大きなファイル（約 103 MiB）が 1 個同じプレフィックスにあり、13 キーを列挙しました。GetObject の測定はサイズごとに別のプレフィックスを使ったため影響を受けていません。13 キーの列挙結果は、キー数がもっと多いディレクトリについては何も示しません。
- スループットは同時実行数 1 の 1 ストリームあたりの値で、平均レイテンシとオブジェクトサイズから求めています。ファイルシステムのスループット上限ではなく、並列読み取りについても何も示しません。1 KB の値は帯域ではなくリクエストのレイテンシを反映しています。
- ツールは Lambda のコールドスタートの影響を分けていません。最初の ListObjectsV2 のサンプルや各呼び出しの最初のリクエストには、接続の確立が含まれている可能性があります。保存した要約からは、最大値が最初の呼び出しだったかどうかは分かりません。
- 対象外: 同時実行数 2 以上、VPC 内の Lambda や NAT 経由の経路（後述「ネットワーク経路」のレイテンシ差はこの測定から導いたものではありません）、他の Lambda メモリサイズ、他のスループットキャパシティ、第 2 世代やマルチ HA ペアのファイルシステム、より大きなディレクトリ。
- 対象外: 階層化ポリシーが ALL のボリューム、およびすでにキャパシティプールへ階層化されたデータ。キャパシティプールから返される読み取りはオブジェクトストレージに向かい、レイテンシもコストも異なると予想されますが、ここでは測定していません。

## 参考結果

> **この節の出所**: 以下の ListObjectsV2（100 キー）と GetObject（サイズ別）の値は、姉妹リポジトリが記録した実測値の転記です。このリポジトリ自身の測定ではなく、生データもここにはコミットしていません。このリポジトリ自身の測定は「本リポジトリでの実測（2026-10-10）」を参照してください。「実効処理レート」は未確認の推定値のままで、測定記録はありません。いずれもサービス上限でも保証値でもありません。

### 測定環境の差

このリポジトリでの測定、転記した実測、以前の計画行の 3 つは、次の点で条件が違います。

| 項目 | 本リポジトリでの測定（2026-10-10） | 転記した実測 | 以前の計画行 |
|---|---|---|---|
| FSx for ONTAP のスループットキャパシティ | 128 MBps | 128 MBps | 512 MBps |
| デプロイタイプ | SINGLE_AZ_1（第 1 世代）、HA ペア 1 組 | Single-AZ | 記載なし |
| クライアント | VPC 外の Lambda（256 MB） | ローカルのワークステーションからインターネット経由 | VPC 外の Lambda（256 MB） |
| S3 Access Points の NetworkOrigin | Internet | Internet | Internet |
| 状態 | 測定済み、生データをコミット | 転記、ここでは未測定 | 未測定 |

以前の計画行（512 MBps）は、テスト環境の表の以前の版に計画として載っていたもので、測定記録はありません。VPC 外の Lambda からの 128 MBps の測定は取りました。VPC 外の Lambda からの 512 MBps の測定は取っていません（[#98](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/98) で追跡）。

下の転記した GetObject の p50（30.5、34.1、48.5、111.0 ms）は、どのサイズでも、ここで測った p50 から約 16 ms 以内です。どちらも 128 MBps の Single-AZ ファイルシステムの値なので、違うのはファイルシステムの容量ではなく、クライアントとネットワーク経路（ローカルのワークステーションからインターネット経由か、同じリージョンの Lambda か）です。反復回数やパーセンタイルの集計方法も違い（転記元は 5〜10 回、ここではオブジェクトごとに 10 回）、残る差の原因はこのデータからは切り分けられません。転記元の S3 Access Points のベンチマークは、インターネット経由ではクライアント側の帯域が律速になり、512 MBps の効果が見えなかったと記録しています。ここでの測定は、この記述を検証するものではありません。

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

2026-10-10 に測った GetObject のレイテンシは、下の所要時間のうち S3 の読み取り部分だけに当たります。パースやベンダーへの送信は含まず、この表を測定値から導き直してもいません。

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

### 階層化ポリシーとデータ階層

このリポジトリで測った数値は、階層化ポリシーが AUTO のボリュームで、SSD（プライマリ階層）にあると推定されるデータを読んだ結果です。転記した値の階層化条件は、このページでは再掲していません。キャパシティプールへ階層化されたデータの読み取りは測定しておらず、キャパシティプールの数値は載せていません。

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

ツールは [`benchmark/s3ap-throughput/`](../../benchmark/s3ap-throughput/README.md) にあります。構成は、`template.yaml`（CloudFormation スタック。メモリ 256 MB・タイムアウト 300 秒・VPC 設定なしの Lambda 1 つ）、`run-benchmark.sh`（呼び出しヘルパー）、`handler.py`（関数本体。単体テストは `tests/`）です。

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

`run-benchmark.sh` は 1 つの `PREFIX` を 2 つのテストの両方に使い、結合した JSON を 1 ファイル書き出します。任意の上書きは `LIST_ITERATIONS`（既定 20）、`GET_ITERATIONS`（既定 5）、`MAX_KEYS`（既定 10）、`BENCHMARK_RUN_ID`、`OUT_FILE` です。2026-10-10 の生データは、この結合ファイルではなく、関数のテストごとの応答で、サイズのプレフィックスごとに 1 ファイルです。

結果は環境の情報とあわせて記録してください。FSx for ONTAP のスループットキャパシティ、S3 Access Points のネットワークオリジン、リージョン、日付、実行 ID です。デプロイの詳細はツールの [README](../../benchmark/s3ap-throughput/README.md) にあります。

## 関連ドキュメント

- [ベンチマークツール（README）](../../benchmark/s3ap-throughput/README.md)
- [2026-10-10 の測定の生データ](../../benchmark/s3ap-throughput/results/2026-10-10/README.md)
- [S3 AP 仕様 & トラブルシューティング](s3ap-fsxn-specification.md)
- [パイプライン SLO](pipeline-slo.md)
- [運用ガイド](operational-guide.md)
