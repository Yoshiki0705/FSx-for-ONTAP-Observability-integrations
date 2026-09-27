# 検証記録: パターン 3(マネージド IoT → S3/分析テール)

🌐 **日本語**(このページ) | [English](../../../en/observability-storage-patterns/verification/verification-results-pattern-3.md)

パターン 3(マネージド IoT → S3/分析テール)検証記録。リポジトリ既存の `docs/en/verification-results-*.md` の形式を踏襲する。実 IP・アカウント ID・リソース ID はプレースホルダを用いる。

> パターン 3 は再利用と記録のみを対象とする。この経路(IoT → S3 Access Point → Parquet → Athena/Snowflake)はリポジトリ内で既に E2E 検証済み(`integrations/lakehouse-retention/`、AGENTS.md 記載)であり、本記録はその既存の検証済み結果を記録する。新規測定ではなく、経路の再構築でもない。

## メタデータ

| 項目 | 値 |
|---|---|
| パターン | 3(マネージド IoT → S3/分析テール) |
| 検証日(測定日) | 既存 E2E 検証済み(`integrations/lakehouse-retention/`) |
| AWS リージョン | `ap-northeast-1`(既定。`AWS_REGION` で上書き可) |
| CloudFormation スタック名 | `integrations/lakehouse-retention/` を再利用(本パターンは新規スタックを作らない) |
| FSx for ONTAP ファイルシステム | `fs-0123456789abcdef0`(プレースホルダ) |
| SVM | `svm-0123456789abcdef0`(プレースホルダ) |
| コンポーネントのバージョン | Firehose / Glue / Athena / Snowflake(`integrations/lakehouse-retention/` に準拠) |

## Claim_Tier の凡例

この記録中の全主張は Claim_Tier をちょうど 1 つ持つ。測定していない性能値・コスト値を事実として提示せず、未測定である旨を明示する。

| Tier | 意味 |
|---|---|
| verified | この環境で再現・確認済み |
| sample-run | 1 回確認。一般的なサービス上限ではない |
| documented | 出典ある文献に基づくが、ここでは未測定 |
| hypothesis | 推論のみ。出典なし・未測定 |
| unverified | 未確認 |

## 検証ステップ

各ステップは Command / Expected / Actual / Verdict と、そのステップが確立する主張の Claim_Tier を記録する。この経路は既存実装で E2E 検証済みのため、Actual は既存の検証済み結果を記す。

| # | Command | Expected | Actual | Verdict | Claim_Tier |
|---|---|---|---|---|---|
| 1 | IoT Core → Lambda → FSx for ONTAP の S3 Access Point 取り込み(`cloud/iot_ingestion/`) | テレメトリが S3 Access Point に到達 | 既存 E2E 検証済み経路として確認済み | ✅ | verified |
| 2 | Firehose → S3 Parquet → Glue 保存(`integrations/lakehouse-retention/`) | Parquet オブジェクトが S3 に到達し Glue でカタログ化 | 既存 E2E 検証済み経路として確認済み | ✅ | verified |
| 3 | Athena/Snowflake から Parquet を分析クエリ | クエリが Parquet を読み結果を返す | 既存 E2E 検証済み経路として確認済み | ✅ | verified |

## ホット層ストレージ仮説

パターン 3 の経路はマネージド取り込みと S3 保存・分析テールであり、ホット TSDB 層(ローカルディスク前提)を持たない。したがって iSCSI hot-tier hypothesis はパターン 3 には該当しない(パターン 1・4・5 に該当する)。この記録では対象外である旨のみを記す。

| 測定対象 | 初期 Tier | サンプル実行後 |
|---|---|---|
| (パターン 3 には該当なし) | — | — |

## 引用ベンチマーク

公開されたベンダー/AWS のベンチマークは出典を明記し、この環境の自身の測定値と区別する。引用を根拠に `verified` へ格上げせず `documented` のままとする。

| 主張 | 出典 | Claim_Tier |
|---|---|---|
| Amazon Timestream for LiveAnalytics は 2025-06-20 に新規顧客の受付を終了 | [AWS 公式アナウンス](https://docs.aws.amazon.com/timestream/latest/developerguide/timestream-availability-change.html) | documented |

## コスト要因

全コスト値に測定日・リージョン・構成条件を併記する。下表は 2026-07(`ap-northeast-1`、単一 AZ)時点の公開単価に基づく目安であり、この環境での実測ではない。この経路は既存実装の再利用であり、本記録が新規に作成・削除するリソースはない。

| 要因 | 補足 | Claim_Tier |
|---|---|---|
| FSx for ONTAP ファイルシステム | 稼働中は継続課金。共有基盤として再利用し、この記録が作成・削除しない | documented |
| Firehose / S3 / Glue / Athena | 取り込み量・保存量・スキャン量に比例する従量課金。`integrations/lakehouse-retention/` のコストモデルに従う | documented |
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金)。S3 Gateway Endpoint を用いる場合は不要 | documented |

## スコープ境界

この記録は検証環境とその結果を対象とし、本番グレードデプロイを対象としない。マルチリージョン HA、およびサンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。既存経路の再構築もスコープ外(再利用と記録のみ)。

## 判定サマリ

| ステップ | 名称 | Verdict |
|---|---|---|
| 1 | IoT 取り込み(S3 Access Point) | ✅ |
| 2 | Firehose → S3 Parquet → Glue 保存 | ✅ |
| 3 | Athena/Snowflake 分析クエリ | ✅ |

総合: `PASS`(既存 E2E 検証済み経路を記録。新規測定ではない)
