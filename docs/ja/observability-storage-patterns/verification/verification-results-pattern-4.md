# 検証記録: パターン 4(Kafka + AutoMQ WAL-on-FSx-for-ONTAP)

🌐 **日本語**(このページ) | [English](../../../en/observability-storage-patterns/verification/verification-results-pattern-4.md)

パターン 4(Kafka + AutoMQ WAL-on-FSx-for-ONTAP)検証環境の検証記録。リポジトリ既存の `docs/en/verification-results-*.md` の形式を踏襲する。実 IP・アカウント ID・リソース ID はプレースホルダを用いる。

> ライブ AWS を要するステップ(NFS 上 Kafka の silly-rename クラッシュ有無の挙動)は spec タスク 16 であり、稼働中の FSx for ONTAP を要し課金が発生する。CI では実行しない。現時点では該当行の Actual を未記入とし、Verdict を `⬜`、Claim_Tier を silly-rename の対象について `hypothesis`、デプロイ各ステップについて `unverified` とする。AutoMQ WAL のレイテンシ/コストの行は `documented`(AWS 自身が公開したベンチマーク、出典明記)であり、`documented` のままとし `verified` や `sample-run` に貼り替えない。

## メタデータ

| 項目 | 値 |
|---|---|
| パターン | 4(Kafka + AutoMQ WAL-on-FSx-for-ONTAP) |
| 検証日(測定日) | `<YYYY-MM-DDTHH:MM:SS+09:00>`(ライブ実行時に記入) |
| AWS リージョン | `ap-northeast-1`(既定。`AWS_REGION` で上書き可) |
| CloudFormation スタック名 | `fsxn-pattern-4-kafka-automq` |
| FSx for ONTAP ファイルシステム | `fs-0123456789abcdef0`(プレースホルダ) |
| SVM | `svm-0123456789abcdef0`(プレースホルダ) |
| コンポーネントのバージョン | `<AutoMQ x.y / Kafka x.y / ONTAP x.y / RHEL x.y>`(ライブ実行時に記入) |

## Claim_Tier の凡例

この記録中の全主張は Claim_Tier をちょうど 1 つ持つ。測定していない性能値・コスト値を事実として提示せず、未測定である旨を明示する。引用した公開ベンチマークは `documented` のままとし、この環境の自身の測定値と区別する。

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
| 1 | `bash shared/scripts/preflight-check.sh --profile pipeline-pattern-4 --vpc-id <vpc>` | 全チェック通過(VPC EP 競合なし) | `<記入>` | ⬜ | unverified |
| 2 | `bash scripts/deploy.sh --profile pipeline-pattern-4 --mode automq-wal` | スタック `CREATE_COMPLETE` | `<記入>` | ⬜ | unverified |
| 3 | `bash scripts/verify.sh` | スタック健全・broker が SSM でオンライン・Gen2 高スループット WAL 意図の記録 | `<記入>` | ⬜ | unverified |
| 4 | NFS 上 Kafka の silly-rename: `-is-preserve-unlink-enabled=true` の FSx for ONTAP NFSv4.1 上の broker ログディレクトリでパーティション再割り当て(spec タスク 16、ライブ・課金) | silly-rename のクラッシュなく再割り当てが完了 | `<記入>` | ⬜ | hypothesis |

## silly-rename の測定対象

パターン 4 の**名指しの測定対象は NFS 上 Kafka の silly-rename 挙動**である。`-is-preserve-unlink-enabled=true` の FSx for ONTAP NFSv4.1 上の Kafka broker ログディレクトリが、silly-rename のクラッシュなくパーティション再割り当てを完了するかを問う。2 つの事実を区別して扱い、どちらも過大に述べない。

- ONTAP の**バージョン前提は FSx for ONTAP で満たされている**(ONTAP 9.18.1 以降。設定を導入した 9.12.1 より後)。これは **AWS サービスチームとの現場での直接確認であり、公開されたバージョン番号ではない** — 公開の FSx for ONTAP ドキュメントだけからは検証できない。
- **特定の FSx for ONTAP ボリュームが設定され E2E で検証されたことは未確認。** 元の検証は NetApp が **NetApp Cloud Volumes ONTAP**(ONTAP 9.12.1、NFSv4.1、Confluent 7.2.1、下記に出典)に対して実施したものであり、それ自体は FSx for ONTAP のエビデンスにはならない。

クラッシュ有無のサンプル実行 1 回で、この対象を `hypothesis` から `sample-run` へ格上げする(spec タスク 16。EC2 前提、課金あり)。1 回の実行で `verified` に格上げせず、一般的なサービス上限としても提示しない。

| 測定対象 | 初期 Tier | サンプル実行後 |
|---|---|---|
| FSx for ONTAP 上の NFS 上 Kafka silly-rename(NFSv4.1 + `-is-preserve-unlink-enabled`) | hypothesis | sample-run |

## 引用ベンチマーク

公開されたベンダー/AWS のベンチマークは出典を明記し、この環境の自身の測定値と区別する。引用を根拠に `verified` へ格上げせず `documented` のままとする。AutoMQ WAL の事例は上記 silly-rename の対象とは**別の**事例である — AutoMQ の WAL は固定サイズのリングバッファであり、silly-rename が依拠するパーティション再バランス時の open 状態での削除パスを実行しない可能性があるため、silly-rename 修正を確認するものではない。

| 主張 | 出典 | Claim_Tier |
|---|---|---|
| AutoMQ diskless-Kafka WAL-on-FSx-for-ONTAP Gen2 マルチ AZ: 平均エンドツーエンドレイテンシー 7.79 ms(P99 18.04 ms)、平均書き込みレイテンシー 5.98 ms(P99 12.87 ms)。m7g.4xlarge 3 台、1,024 GiB / 3,072 IOPS / 736 MBps、us-east-1 | [AWS Storage ブログ(2026年)](https://aws.amazon.com/blogs/storage/achieving-sub-10ms-latency-and-94-cost-savings-with-diskless-kafka-using-automq-and-amazon-fsx-for-netapp-ontap/) | documented |
| コスト比較: 従来型 3 レプリカ・マルチ AZ Kafka が月額約 $317,000 に対し、FSx for ONTAP を使う AutoMQ BYOC が同じ P99 書き込みレイテンシー目標で月額約 $18,345(AWS 自身が公開した数値。独立に再現した測定ではない) | [AWS Storage ブログ(2026年)](https://aws.amazon.com/blogs/storage/achieving-sub-10ms-latency-and-94-cost-savings-with-diskless-kafka-using-automq-and-amazon-fsx-for-netapp-ontap/) | documented |
| silly-rename 修正の機能検証(NFSv3 はクラッシュ、修正を有効化した ONTAP 9.12.1 NFSv4.1 は再割り当てを完了) — **NetApp Cloud Volumes ONTAP** に対して実施、FSx for ONTAP ではない | [NetApp ソリューションドキュメント(2025-09-15)](https://docs.netapp.com/us-en/netapp-solutions/data-analytics/kafka-nfs-functional-validation-silly-rename-fix.html); [Trident issue #808](https://github.com/NetApp/trident/issues/808) | documented |

## コスト要因

全コスト値に測定日・リージョン・構成条件を併記する。下表は 2026-07(`ap-northeast-1`、最小構成)時点の公開単価に基づく目安であり、この環境での実測ではない。AutoMQ の値は AWS 自身の公開ベンチマーク(上記に出典)であり、ここでの実測ではない。

| 要因 | 補足 | Claim_Tier |
|---|---|---|
| FSx for ONTAP Gen2 高スループット WAL | 標準構成より高コスト。稼働中は継続課金。本パターン最大の定常コスト要因。共有基盤として再利用し、この環境が作成・削除しない | documented |
| EC2 Kafka broker | 稼働中は継続課金(既定 `m7g.large`。AWS ベンチは `m7g.4xlarge` を使用) | documented |
| S3(AutoMQ 耐久層) | S3 バックエンドの耐久層に対するストレージとリクエストの課金 | documented |
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金)。本環境は S3 Gateway Endpoint を用いるため通常は不要 | documented |

## スコープ境界

この記録は検証環境とその結果を対象とし、本番グレードデプロイを対象としない。マルチリージョン HA、およびサンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。AutoMQ のレイテンシ/コスト値は AWS の引用ベンチマークであり、この環境での再現ではない。

## 判定サマリ

| ステップ | 名称 | Verdict |
|---|---|---|
| 1 | プリフライト | ⬜ |
| 2 | スタックデプロイ | ⬜ |
| 3 | デプロイ後検証 | ⬜ |
| 4 | NFS 上 Kafka silly-rename(ライブ) | ⬜ |

総合: `未実行`(ライブ AWS を要するステップは spec タスク 16)
