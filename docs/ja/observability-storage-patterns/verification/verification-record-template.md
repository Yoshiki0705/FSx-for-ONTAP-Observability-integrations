# 検証記録テンプレート(パイプラインパターン)

🌐 **日本語**(このページ) | [English](../../../en/observability-storage-patterns/verification/verification-record-template.md)

このテンプレートを `verification-results-pattern-<N>.md` にコピーし、1 パターン分を記入する。リポジトリ既存の `docs/en/verification-results-*.md` の形式を踏襲する。実 IP・アカウント ID・リソース ID はプレースホルダを用いる。

> これは完成した記録ではなくテンプレートである。例の行は形式を示すもので、実際の実行結果に置き換える。

## メタデータ

| 項目 | 値 |
|---|---|
| パターン | `<1-5>`(例: Prometheus + remote_write) |
| 検証日(測定日) | `<YYYY-MM-DDTHH:MM:SS+09:00>` |
| AWS リージョン | `<リージョン。例: ap-northeast-1>` |
| CloudFormation スタック名 | `<stack-name>` |
| FSx for ONTAP ファイルシステム | `fs-0123456789abcdef0`(プレースホルダ) |
| SVM | `svm-0123456789abcdef0`(プレースホルダ) |
| コンポーネントのバージョン | `<Prometheus x.y / QuestDB x.y / Kafka x.y / ONTAP x.y>` |

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

各ステップは Command / Expected / Actual / Verdict と、そのステップが確立する主張の Claim_Tier を記録する。

| # | Command | Expected | Actual | Verdict | Claim_Tier |
|---|---|---|---|---|---|
| 1 | `bash scripts/deploy.sh` | スタック `CREATE_COMPLETE` | `<記入>` | ⬜ | unverified |
| 2 | `bash shared/scripts/preflight-check.sh --profile pipeline-pattern-<N>` | 全チェック通過 | `<記入>` | ⬜ | unverified |
| 3 | `<パターン固有の検証コマンド>` | `<期待値>` | `<記入>` | ⬜ | unverified |

## ホット層ストレージ仮説

パターン 1・4・5 に該当する。名指しの測定対象と Tier の遷移をここに記録する。

| 測定対象 | 初期 Tier | サンプル実行後 |
|---|---|---|
| iSCSI LUN 上のホット TSDB(InfluxDB / QuestDB / ClickHouse)の read/write/ロック正当性 | hypothesis | sample-run |
| NFS 上 Kafka の silly-rename 挙動(NFSv4.1 + `-is-preserve-unlink-enabled`、ONTAP 9.12.1 以降) | hypothesis | sample-run |

サンプル実行は read → write → ロック取得の正当性を確認する 1 回に限る。負荷/スケールの測定は、明示的に追求する場合を除きスコープ外。

## 引用ベンチマーク

公開されたベンダー/AWS のベンチマークは出典を明記し、この環境の自身の測定値と区別する。引用を根拠に `verified` へ格上げせず `documented` のままとする。

| 主張 | 出典 | Claim_Tier |
|---|---|---|
| `<例: AutoMQ WAL-on-FSx-for-ONTAP レイテンシ>` | `<AWS Storage ブログ URL>` | documented |

## コスト要因

全コスト値に測定日・リージョン・構成条件を併記する。

| 要因 | 補足 |
|---|---|
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金) |
| FSx for ONTAP ファイルシステム | 稼働中は継続課金 |
| FSx for ONTAP Gen2 高スループット(パターン 4 / AutoMQ) | 標準構成より高コスト |

## スコープ境界

この記録は検証環境とその結果を対象とし、本番グレードデプロイを対象としない。マルチリージョン HA、およびサンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。

## 判定サマリ

| ステップ | 名称 | Verdict |
|---|---|---|
| 1 | スタックデプロイ | ⬜ |
| 2 | プリフライト | ⬜ |
| 3 | パターン固有の検証 | ⬜ |

総合: `<PASS / PARTIAL / 未実行>`
