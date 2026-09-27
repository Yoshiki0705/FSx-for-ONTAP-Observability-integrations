# パターン 3 セットアップガイド: マネージド IoT → S3/分析テールの再利用記録

🌐 **日本語**(このページ) | [English](../en/setup-guide.md)

マネージド IoT テレメトリ経路の検証を、既存の E2E 検証済み実装の再利用として記録する手順。パターン 3 は再利用と記録のみを対象とし、新規テンプレートを作らず、既存経路を再構築しない。これは検証環境と検証記録を対象とし、本番グレードデプロイを対象としない。

## 概要

マネージド IoT テレメトリは、IoT Core → Lambda → FSx for ONTAP の S3 Access Points で取り込み、Firehose → S3 Parquet → Glue → Athena/Snowflake で保存・分析する。この経路はリポジトリ内で既に E2E 検証済みであり(`integrations/lakehouse-retention/`)、本パターンはそれを再構築せず、検証結果を検証記録として残すだけである。

Amazon Timestream for LiveAnalytics は 2025-06-20 に新規顧客の受付を終了している。再利用する経路は Timestream に依存せず、Firehose → S3 Parquet → Athena/Snowflake の分析テールを用いる。Timestream・Athena・Snowflake はいずれも用途に応じた選択肢であり、本記録は特定の分析エンジンを推奨しない。

## 既存カバレッジと net-new の区別

| 層 | 区分 | 実体 |
|---|---|---|
| IoT 取り込み(IoT Core → Lambda → S3 Access Point) | 再利用 | ontap-edge-to-cloud-ai `cloud/iot_ingestion/`(文書化ポインタ) |
| 保存・分析テール(Firehose → S3 Parquet → Glue → Athena/Snowflake) | 再利用 | `integrations/lakehouse-retention/`(文書化ポインタ、E2E 検証済み) |
| FSx for ONTAP ファイルシステム + SVM | 再利用 | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml`(文書化ポインタ) |
| net-new(新規作成する層) | なし | — |

net-new が「なし」なのは、この経路が既に E2E 検証済みで存在するため。既存経路を新規ファイルへ複製せず、正確なパスを記した文書化ポインタで参照する。`shared/reuse-references.yaml` のパターン 3 は `net_new` を空 `[]` とし、再利用のみを宣言する。

## 再利用ポインタ

コピーせず、次の既存資産を正確なパスで参照する。

- **保存・分析テール**: [`integrations/lakehouse-retention/`](../../../lakehouse-retention/) — Firehose → S3 Parquet → Glue → Athena/Snowflake。リポジトリ内で E2E 検証済み(AGENTS.md 記載)。
- **IoT 取り込み**: ontap-edge-to-cloud-ai の `cloud/iot_ingestion/` — IoT Core → Lambda → FSx for ONTAP の S3 Access Point。sibling リポジトリを指す文書化ポインタであり、sibling が存在する環境でのみ実在確認する。

これらは設定・スクリプト・デプロイ済み実装でありネストスタックの対象にならないため、IaC レベル参照ではなく文書化ポインタとする。

## 着手前に用意するもの

パターン 3 は新規リソースを立ち上げないため、集めるべき新規パラメータはない。既存の E2E 検証済み経路を確認・記録するために、次の既存資産が手元にあることを確かめる。いずれもこの記録が作成するものではない。

| 用意するもの | 取得方法 |
|---|---|
| 稼働中の FSx for ONTAP ファイルシステム(共有基盤) | `aws fsx describe-file-systems --query 'FileSystems[?FileSystemType==\`ONTAP\`].FileSystemId' --output text` |
| 保存・分析テール実装(E2E 検証済み) | `integrations/lakehouse-retention/` を参照(コピーしない) |
| IoT 取り込み例 | ontap-edge-to-cloud-ai の `cloud/iot_ingestion/` を参照(sibling 存在時のみ実在確認) |

## パラメータ

このパターンは新規テンプレートを持たないため、設定すべきパラメータはない。既存経路のパラメータは各再利用先(`integrations/lakehouse-retention/` および `cloud/iot_ingestion/`)のドキュメントに従う。

## VPC エンドポイント競合マトリクス

このパターンは新規リソースを立ち上げないため、VPC エンドポイントを追加作成せず、独自の競合マトリクスは該当しない。再利用先の `integrations/lakehouse-retention/` を検証目的で一時的に立ち上げる場合は、そのドキュメントの VPC 前提に従う。プリフライトプロファイル `pipeline-pattern-3` は VPC チェックを持たず、S3 Access Point アクセスのみを確認する(再利用のみのため)。

## デプロイ所要時間の目安

このパターンは新規デプロイを行わないため、デプロイ所要時間は該当しない。既存経路を一時的に立ち上げる場合の所要時間は `integrations/lakehouse-retention/` のドキュメントに従う。

## 想定デプロイ経路

このパターンは新規デプロイ経路を持たない。記録するのは既存の E2E 検証済み経路(IoT→S3 Access Point→Parquet→Athena/Snowflake)の結果であり、Claim_Tier は `verified`(既存)である。新規のライブ実行は伴わない。

## FSx for ONTAP ガードレール

再利用経路は S3 Access Points へオブジェクトを書き込む。S3 Access Points は条件付き書き込み(If-None-Match)非対応であり、トランザクショナルなテーブルフォーマットの直接の書き込み先には用いない。Parquet オブジェクトは Firehose が追記ではなく新規オブジェクトとして出力するため、この制約は保存経路の設計に整合する。

AD 参加済み SVM の S3 Access Points に対するデータ操作は、全操作で AD ドメインコントローラ到達性を要する。HeadBucket の成功は到達性の証拠にはならない。

## コスト要因

すべてのコスト値に測定日・リージョン・構成条件を併記する。下表は 2026-07(`ap-northeast-1`、単一 AZ)時点の公開単価に基づく目安であり、この環境での実測ではない。この経路は既存実装の再利用であり、本パターンが新規に作成・削除するリソースはない。

| 要因 | 補足 |
|---|---|
| FSx for ONTAP ファイルシステム | 稼働中は継続課金。共有基盤として再利用し、この記録が作成・削除する対象ではない |
| Firehose / S3 / Glue / Athena | 取り込み量・保存量・スキャン量に比例する従量課金。`integrations/lakehouse-retention/` のコストモデルに従う |
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金)。S3 Gateway Endpoint を用いる場合は不要 |

## Ephemeral 構成

本パターンは新規リソースを立ち上げないため、専用の Ephemeral 構成を持たない。再利用先の `integrations/lakehouse-retention/` を検証目的で一時的に立ち上げる場合は、そのテアダウン手順に従って撤去する。

## Day 2 運用

このパターンは新規リソースを立ち上げないため、独自の Day 2 運用対象はない。既存の E2E 検証済み経路を本番運用する場合の確認・監視は、`integrations/lakehouse-retention/` のドキュメントに従う。取り込み・保存・分析の各段(Firehose・S3・Glue・Athena/Snowflake)の監視とアラームはそちらの運用ガイドを参照点とする。

## ロールバックとクリーンアップ

このパターンは新規リソースを作成しないため、独自のロールバックやクリーンアップはない。既存経路を一時的に立ち上げて失敗した場合の復旧・撤去は、`integrations/lakehouse-retention/` のテアダウンおよびロールバック手順に従う。

## ONTAP バージョン要件

再利用経路が FSx for ONTAP に触れるのは S3 Access Points への書き込みのみであり、標準的な S3 対応バージョンで動作する。silly-rename のような特定バージョン依存の前提を持たない。自環境の FSx for ONTAP が S3 Access Points をサポートするバージョンであることを確認すれば足りる。

## スコープ境界

この記録は検証環境と検証記録を対象とし、本番グレードデプロイを対象としない。各分析エンジンのマルチリージョン HA はスコープ外。サンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。既存経路の再構築もスコープ外である(再利用と記録のみ)。

## 関連ドキュメント

- [パターン 3 概説(ストレージ統合パターン)](../../../../docs/ja/observability-storage-patterns/pattern-3-managed-iot-timestream.md)
- [検証記録: パターン 3](../../../../docs/ja/observability-storage-patterns/verification/verification-results-pattern-3.md)
- [統合の入口](../../README.md)
