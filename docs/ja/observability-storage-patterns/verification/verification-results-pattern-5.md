# 検証記録: パターン 5(QuestDB / TimescaleDB)

🌐 **日本語**(このページ) | [English](../../../en/observability-storage-patterns/verification/verification-results-pattern-5.md)

パターン 5(QuestDB / TimescaleDB)検証環境の検証記録。リポジトリ既存の `docs/en/verification-results-*.md` の形式を踏襲する。実 IP・アカウント ID・リソース ID はプレースホルダを用いる。

> ライブ AWS を要するステップ(iSCSI LUN ホット層の read/write/ロック正当性)は spec タスク 13 であり、稼働中の FSx for ONTAP を要し課金が発生する。CI では実行しない。現時点では該当行の Actual を未記入とし、Verdict を `⬜`、Claim_Tier を iSCSI ホット層の対象について `hypothesis`、デプロイ各ステップについて `unverified` とする。

## メタデータ

| 項目 | 値 |
|---|---|
| パターン | 5(QuestDB / TimescaleDB) |
| 検証日(測定日) | `<YYYY-MM-DDTHH:MM:SS+09:00>`(ライブ実行時に記入) |
| AWS リージョン | `ap-northeast-1`(既定。`AWS_REGION` で上書き可) |
| CloudFormation スタック名 | `fsxn-pattern-5-questdb-timescaledb` |
| FSx for ONTAP ファイルシステム | `fs-0123456789abcdef0`(プレースホルダ) |
| SVM | `svm-0123456789abcdef0`(プレースホルダ) |
| コンポーネントのバージョン | `<QuestDB x.y / TimescaleDB x.y / ClickHouse x.y / ONTAP x.y>`(ライブ実行時に記入) |

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
| 1 | `bash shared/scripts/preflight-check.sh --profile pipeline-pattern-5 --vpc-id <vpc>` | 全チェック通過(VPC EP 競合なし) | `<記入>` | ⬜ | unverified |
| 2 | `bash scripts/deploy.sh --profile pipeline-pattern-5 --engine questdb` | スタック `CREATE_COMPLETE` | `<記入>` | ⬜ | unverified |
| 3 | `bash scripts/verify.sh` | スタック健全・TSDB サーバが SSM でオンライン・コールド層アーカイブ先解決 | `<記入>` | ⬜ | unverified |
| 4 | iSCSI LUN ホット層の read → write → ロック正当性(spec タスク 13、ライブ・課金) | iSCSI LUN 上で QuestDB/ClickHouse の read・write・ロック取得が成功 | `<記入>` | ⬜ | hypothesis |

## ホット層ストレージ仮説

パターン 5 のホット層は既定で EBS ローカルブロックストレージ上にあり、NFS/EFS を用いない。ここでの**名指しの測定対象は iSCSI LUN ホット層**である。iSCSI LUN をローカルブロックデバイスとして用いる構成がホット層として成立するかは断定せず、追跡するのみとする。read → write → ロック取得の正当性を確認するサンプル実行 1 回で `hypothesis` から `sample-run` へ格上げする。ECS Fargate は iSCSI initiator を持てないため、実行は EC2 前提。負荷/スケールの測定はスコープ外。

| 測定対象 | 初期 Tier | サンプル実行後 |
|---|---|---|
| iSCSI LUN 上の QuestDB/ClickHouse の read/write/ロック正当性 | hypothesis | sample-run |

## 引用ベンチマーク

公開されたベンダー/AWS のベンチマークは出典を明記し、この環境の自身の測定値と区別する。引用を根拠に `verified` へ格上げせず `documented` のままとする。

| 主張 | 出典 | Claim_Tier |
|---|---|---|
| QuestDB はデータディレクトリのネットワークファイルシステム(NFS)非対応を明記 | [QuestDB capacity planning](https://questdb.io/docs/operations/capacity-planning/) | documented |
| consumer 経路の ClickHouse スキーマ/DDL(raw イベント・ソーステーブル・ロールアップ・エクスポート) | `ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/`(sibling リポジトリ、文書化ポインタ) | documented |

## コスト要因

全コスト値に測定日・リージョン・構成条件を併記する。下表は 2026-07(`ap-northeast-1`、単一 AZ、最小構成)時点の公開単価に基づく目安であり、この環境での実測ではない。

| 要因 | 補足 | Claim_Tier |
|---|---|---|
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金)。本環境は S3 Gateway Endpoint を用いるため通常は不要 | documented |
| FSx for ONTAP ファイルシステム | 稼働中は継続課金。共有基盤として再利用し、この環境が作成・削除しない | documented |
| EC2 TSDB サーバ | 稼働中は継続課金(既定 `t3.medium`) | documented |

## スコープ境界

この記録は検証環境とその結果を対象とし、本番グレードデプロイを対象としない。マルチリージョン HA、およびサンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。

## 判定サマリ

| ステップ | 名称 | Verdict |
|---|---|---|
| 1 | プリフライト | ⬜ |
| 2 | スタックデプロイ | ⬜ |
| 3 | デプロイ後検証 | ⬜ |
| 4 | iSCSI LUN ホット層の正当性(ライブ) | ⬜ |

総合: `未実行`(ライブ AWS を要するステップは spec タスク 13)
