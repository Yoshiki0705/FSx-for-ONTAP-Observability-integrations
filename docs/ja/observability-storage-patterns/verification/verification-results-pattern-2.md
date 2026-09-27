# 検証記録: パターン 2(Prometheus + remote_write)

🌐 **日本語**(このページ) | [English](../../../en/observability-storage-patterns/verification/verification-results-pattern-2.md)

パターン 2(Prometheus + remote_write)検証環境の検証記録。リポジトリ既存の `docs/en/verification-results-*.md` の形式を踏襲する。実 IP・アカウント ID・リソース ID はプレースホルダを用いる。

> ライブ AWS を要するステップ(remote_write → S3 Access Points の疎通)は spec タスク 10 であり、稼働中の FSx for ONTAP を要し課金が発生する。CI では実行しない。現時点では該当行の Actual を未記入とし、Verdict を `⬜`、Claim_Tier を `unverified` とする。

## メタデータ

| 項目 | 値 |
|---|---|
| パターン | 2(Prometheus + remote_write) |
| 検証日(測定日) | `<YYYY-MM-DDTHH:MM:SS+09:00>`(ライブ実行時に記入) |
| AWS リージョン | `ap-northeast-1`(既定。`AWS_REGION` で上書き可) |
| CloudFormation スタック名 | `fsxn-pattern-2-prometheus` |
| FSx for ONTAP ファイルシステム | `fs-0123456789abcdef0`(プレースホルダ) |
| SVM | `svm-0123456789abcdef0`(プレースホルダ) |
| コンポーネントのバージョン | `<Prometheus x.y / ONTAP x.y>`(ライブ実行時に記入) |

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
| 1 | `bash shared/scripts/preflight-check.sh --profile pipeline-pattern-2 --vpc-id <vpc>` | 全チェック通過(VPC EP 競合なし) | `<記入>` | ⬜ | unverified |
| 2 | `bash scripts/deploy.sh --profile pipeline-pattern-2` | スタック `CREATE_COMPLETE` | `<記入>` | ⬜ | unverified |
| 3 | `bash scripts/verify.sh` | スタック健全・Prometheus が SSM でオンライン・アーカイブ先解決 | `<記入>` | ⬜ | unverified |
| 4 | remote_write → S3 Access Points 疎通(spec タスク 10、ライブ・課金) | アーカイブ先にオブジェクトが到達 | `<記入>` | ⬜ | unverified |

## ホット層ストレージ仮説

パターン 2 のローカル TSDB は EBS ローカルブロックストレージ上にあり、NFS/EFS を用いない。したがって iSCSI hot-tier hypothesis はパターン 2 には該当しない(パターン 1・4・5 に該当する)。この記録では対象外である旨のみを記す。

| 測定対象 | 初期 Tier | サンプル実行後 |
|---|---|---|
| (パターン 2 には該当なし) | — | — |

## 引用ベンチマーク

公開されたベンダー/AWS のベンチマークは出典を明記し、この環境の自身の測定値と区別する。引用を根拠に `verified` へ格上げせず `documented` のままとする。

| 主張 | 出典 | Claim_Tier |
|---|---|---|
| Prometheus のローカル TSDB は NFS/EFS 非対応 | [prometheus/prometheus#10611](https://github.com/prometheus/prometheus/issues/10611)、[SUSE Rancher Monitoring KB](https://www.suse.com/support/kb/doc?id=000021332) | documented |

## コスト要因

全コスト値に測定日・リージョン・構成条件を併記する。下表は 2026-07(`ap-northeast-1`、単一 AZ、最小構成)時点の公開単価に基づく目安であり、この環境での実測ではない。

| 要因 | 補足 | Claim_Tier |
|---|---|---|
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金)。本環境は S3 Gateway Endpoint を用いるため通常は不要 | documented |
| FSx for ONTAP ファイルシステム | 稼働中は継続課金。共有基盤として再利用し、この環境が作成・削除しない | documented |
| EC2 Prometheus サーバ | 稼働中は継続課金(既定 `t3.medium`) | documented |

## スコープ境界

この記録は検証環境とその結果を対象とし、本番グレードデプロイを対象としない。マルチリージョン HA、およびサンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。

## 判定サマリ

| ステップ | 名称 | Verdict |
|---|---|---|
| 1 | プリフライト | ⬜ |
| 2 | スタックデプロイ | ⬜ |
| 3 | デプロイ後検証 | ⬜ |
| 4 | remote_write → S3 Access Points 疎通(ライブ) | ⬜ |

総合: `未実行`(ライブ AWS を要するステップは spec タスク 10)
