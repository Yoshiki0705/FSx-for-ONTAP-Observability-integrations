# パターン 4 セットアップガイド: Kafka + AutoMQ WAL-on-FSx-for-ONTAP 検証環境

🌐 **日本語**(このページ) | [English](../en/setup-guide.md)

Kafka + AutoMQ diskless-Kafka パイプラインの検証環境を、既存資産の再利用を土台に立ち上げる手順。これは検証環境と検証記録を対象とし、本番グレードデプロイを対象としない。

## 概要

Kafka クラスタが高ボリュームのテレメトリを取り込み、OpenTelemetry Collector(または Kafka ネイティブのコンシューマ)がルーティングし、時系列/列指向ストアが保存する。この検証環境が焦点とするのは 1 つの net-new 層 — AutoMQ diskless-Kafka を動かす Kafka broker フリートである。AutoMQ は FSx for ONTAP Generation 2 のボリュームを、S3 の手前に置くマルチ AZ 共有 write-ahead log(WAL)として使う。OpenTelemetry Collector 層と ClickHouse consumer スキーマは文書化ポインタで再利用し、ここにはコピーしない。

この検証環境が扱うのは broker 層とその WAL 構成であり、クラスタ化された本番デプロイではない。

## 既存カバレッジと net-new の区別

| 層 | 区分 | 実体 |
|---|---|---|
| Kafka broker フリート + AutoMQ WAL-on-FSx-for-ONTAP Gen2 | net-new | `template.yaml`(本パターン) |
| OpenTelemetry Collector(ルーティング/変換) | 再利用 | `integrations/otel-collector/`(文書化ポインタ) |
| ClickHouse consumer DDL | 再利用 | `ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/`(文書化ポインタ) |
| VPC エンドポイント(S3 Gateway + Secrets Manager Interface) | 再利用 | `shared/templates/vpc-endpoints.yaml`(ネストスタック) |
| IAM 最小権限パターン | 再利用 | `shared/templates/iam-base-roles.yaml`(コピーペースト参照。各スタックがインラインで実装) |
| FSx for ONTAP ファイルシステム + SVM | 再利用 | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml`(文書化ポインタ) |

Kafka broker フリートと Gen2 WAL 構成が net-new なのは、リポジトリに OTel Collector 統合と汎用アーカイブ層はあるが Kafka broker 層が存在しないため。VPC エンドポイントは既存の共有テンプレートをネストスタックで参照し、コピーしない。broker 自身の EC2・セキュリティグループ・インスタンスロール/プロファイルは、既存のどのテンプレートともリソース形状を共有しない。

## Kafka ストレージモデルの選び方

AutoMQ diskless-Kafka と従来型の 3 レプリカ・マルチ AZ Kafka は用途に応じて選ぶものであり、優劣を競うものではない。トレードオフは、AutoMQ 自身の制約も含めて対称に記載する。AutoMQ は BYOC 依存(自アカウントで運用する)であり、既定の推奨ではなく 1 つの選択肢として提示する。

| モデル | 適する用途 | トレードオフ・考慮点 |
|---|---|---|
| AutoMQ diskless-Kafka + FSx for ONTAP Gen2 WAL(本パターンの net-new) | S3 の耐久性とローカルディスクに近いレイテンシ、より低い定常コストを求め、BYOC プロダクトの運用を許容できるチーム | BYOC の運用責任を負う。FSx for ONTAP Gen2 高スループット構成に依存し、これは標準より高コスト。従来の broker より新しい組み合わせ |
| 従来型 3 レプリカ・マルチ AZ Kafka | 最も枯れて広く運用され、ツールやマネージドサービスの選択肢が最も広いモデルを求めるチーム | ストレージコストが高い(AZ をまたぐ 3 つのフルレプリカ)。ローカルディスクのログストレージを運用・リバランスする。復旧はレプリカを peer から再構築する |

どちらのモデルも同じ OTel Collector と ClickHouse consumer の前段に置ける。この選択は、broker ログディレクトリを NFS に置く場合の下記 FSx for ONTAP silly-rename ガードレールを変えない。

## OTel Collector と ClickHouse DDL 参照

OpenTelemetry Collector 層は `integrations/otel-collector/`(collector の設定・スクリプト・compose ファイル)を文書化ポインタで再利用し、コピーしない。consumer ストアに ClickHouse を用いる場合、`ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/`(sibling リポジトリ。ローカルチェックアウト名は `edge-to-cloud-ai`)のスキーマを再利用する。SQL を本リポジトリにコピーせず、単一の真実の源として保つためその正確なパスで参照する。

## FSx for ONTAP silly-rename ガードレール

Kafka broker のログディレクトリを NFS 上に置くと、以前はパーティション再割り当て中にクラッシュしていた。原因は "silly rename" 挙動である。NFS はまだ開いている参照が残るファイルをリネームし最後のクローズ時に削除するが、Kafka のリバランスはまだ開いている参照が残るファイルを削除する。修正は ONTAP ボリューム設定 `-is-preserve-unlink-enabled=true` で、**ONTAP 9.12.1** で導入された(クライアント側の変更は RHEL 8.7 / 9.1)。

ここでは 2 つを区別して扱う。

- **ONTAP バージョンの前提は満たされている。** FSx for ONTAP ファイルシステムは **ONTAP 9.18.1 以降**を実行する。これは AWS サービスチームとの現場での直接確認であり、**公開されたバージョン番号ではない** — 読者は公開の FSx for ONTAP ドキュメントだけからこれを検証できない。9.18.1 は 9.12.1 よりかなり後なので、バージョンは修正をサポートする。
- **特定の FSx for ONTAP ボリュームが設定され E2E で検証されたことは未確認。** NetApp の NFSv3 対 NFSv4.1 パーティション再割り当て比較のような機能テストが FSx for ONTAP に対して直接実行された例は、本プロジェクトの調査では見つからなかった。元の silly-rename 検証は NetApp が **NetApp Cloud Volumes ONTAP**(ONTAP 9.12.1、NFSv4.1、Confluent 7.2.1)に対して実施したものであり、それ自体は FSx for ONTAP のエビデンスにはならない。

この理由から `deploy.sh` は WAL/ログディレクトリを NFS に**自動マウントしない**。先にボリュームで `-is-preserve-unlink-enabled=true` を設定・検証し(FSx for ONTAP ボリュームが公開する ONTAP CLI または REST API 経由)、その後マウントする。ライブのクラッシュ有無のサンプル実行は spec タスク 16(課金あり)。なお AutoMQ の WAL は固定サイズのリングバッファであり、silly-rename 問題が依拠するパーティション再バランス時の open 状態でのファイル削除というコードパスを実行しない可能性があるため、AutoMQ の事例はこの特定の修正を確認するものではない。

## 前提条件

- 稼働中の FSx for ONTAP ファイルシステム(SVM を含む)。WAL には Generation 2
- 配置先 VPC と、2 つ以上の AZ にまたがるプライベートサブネット(WAL はマルチ AZ)
- CloudFormation / EC2 / IAM / S3 権限を持つ AWS CLI v2
- 耐久層として AutoMQ が書き込む S3 バケット(broker ロールのスコープ付きプレフィックスに沿って命名)

## 着手前に用意するもの

初回に最もつまずくのは、自環境の既存リソース ID をデプロイ中に探し始めることである。下表の値を**先に**手元に集めておく。いずれもこの検証環境が作成するものではなく、既存の共有基盤から参照する。WAL には Generation 2 のファイルシステムを用いる。

| 用意するもの | 取得方法 |
|---|---|
| `VpcId`(配置先 VPC) | `aws ec2 describe-vpcs --query 'Vpcs[].VpcId' --output text` |
| `SubnetIds`(2 つ以上の AZ) | `aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc-id> --query 'Subnets[].[SubnetId,AvailabilityZone]' --output text` |
| `FileSystemId`(FSx for ONTAP Gen2) | `aws fsx describe-file-systems --query 'FileSystems[?FileSystemType==\`ONTAP\`].FileSystemId' --output text` |
| `OntapMgmtIp`(ONTAP 管理 IP) | `aws fsx describe-file-systems --query 'FileSystems[].OntapConfiguration.Endpoints.Management.IpAddresses' --output text` |
| `SvmId`(SVM 識別子。任意) | `aws fsx describe-storage-virtual-machines --query 'StorageVirtualMachines[].StorageVirtualMachineId' --output text` |
| AutoMQ 耐久層 S3 バケット | `aws s3 ls`(broker ロールのスコープ付きプレフィックスに沿って命名) |
| `PACKAGE_BUCKET`(ネストスタックテンプレート配置用 S3 バケット) | `aws s3 ls`(CloudFormation がネストスタックテンプレートを読める同一リージョンのバケット。既存 VPC エンドポイントを再利用する場合は不要) |

## パラメータ

`shared/templates/restore-verification.yaml` とパターン 2 / パターン 5 の AllowedPattern 規約を踏襲する。アカウント ID はハードコードせず、`deploy.sh` が実行時に `aws sts get-caller-identity` で解決する。リージョンは `AWS_REGION` 環境変数で上書きする(既定 `ap-northeast-1`)。例値はプレースホルダであり、自環境の値に置き換える。

| パラメータ | 説明 | 取得方法・例値 | 必須 |
|---|---|---|---|
| `VpcId` | broker フリートを配置する VPC | `aws ec2 describe-vpcs`。例 `vpc-0123456789abcdef0` | 必須 |
| `SubnetIds` | 2 つ以上の AZ のプライベートサブネット(WAL はマルチ AZ) | `aws ec2 describe-subnets`。例 `subnet-0123456789abcdef0,subnet-0123456789abcdef1` | 必須 |
| `FileSystemId` | FSx for ONTAP Gen2 ファイルシステム ID(`^fs-[0-9a-f]{17}$`) | `aws fsx describe-file-systems`。例 `fs-0123456789abcdef0` | 必須 |
| `OntapMgmtIp` | ONTAP 管理エンドポイント IP | `aws fsx describe-file-systems`。例 `198.51.100.10` | 必須 |
| `SvmId` | SVM 識別子 | `aws fsx describe-storage-virtual-machines`。例 `svm-0123456789abcdef0` | 任意 |
| `KafkaMode` | `automq-wal`(既定)または `traditional` | 選び方は下記の「Kafka ストレージモデルの選び方」参照。例 `automq-wal` | 任意(既定 `automq-wal`) |
| `Gen2HighThroughput` | `true`(既定)または `false` — コスト要因 | 例 `true`(高スループット WAL、標準より高コスト) | 任意(既定 `true`) |
| `WalMountPath` | FSx for ONTAP Gen2 WAL マウント先の broker パス | 例 `/var/lib/automq/wal` | 任意(既定 `/var/lib/automq/wal`) |
| `VpcEndpointsTemplateUrl` | パッケージ済み共有 VPC エンドポイントテンプレートの HTTPS S3 URL | 例 `https://my-cfn-package-bucket.s3.ap-northeast-1.amazonaws.com/pattern-4/vpc-endpoints.yaml`。既存エンドポイント再利用時は空 | 任意(空でスキップ) |

## プリフライト検証

スタンドアップ前に、既存の共有プリフライトスクリプトをパターン 4 プロファイルで実行する。`deploy.sh` がこれを自動で呼ぶ。

```bash
bash shared/scripts/preflight-check.sh --profile pipeline-pattern-4 --vpc-id vpc-0123456789abcdef0
```

VPC エンドポイント競合を検出した場合はスタンドアップを続行しない。`VpcEndpointsTemplateUrl` を空のままにすると VPC の既存エンドポイントを再利用する。

## VPC エンドポイント競合マトリクス

本プロジェクトで最も多いデプロイ失敗は VPC エンドポイント競合による CREATE_FAILED である。パターン 4 は S3 Gateway Endpoint(AutoMQ 耐久層)と Secrets Manager Interface Endpoint をネストスタックで再利用する。同一 VPC に同一サービスのエンドポイントが既にあると競合する。プリフライト(`--profile pipeline-pattern-4`)がこれを検出する。

| エンドポイント種別 | 競合条件 | 回避策(パラメータ) |
|---|---|---|
| S3 Gateway Endpoint | 同一 VPC・同一ルートテーブルに S3 Gateway Endpoint が既存 | `VpcEndpointsTemplateUrl` を空のままにして既存を再利用する |
| Secrets Manager Interface Endpoint | 同一 VPC に Secrets Manager の Interface Endpoint(PrivateDns 有効)が既存 | `VpcEndpointsTemplateUrl` を空のままにして既存を再利用する |

競合を検出したら、`VpcEndpointsTemplateUrl` を空にして既存エンドポイントを再利用する構成に切り替えてから再実行する。

## デプロイ所要時間の目安

下表は目安であり、測定された保証値ではない(Claim_Tier: unverified)。実際の時間はリージョン・インスタンスタイプ・アカウントの状態で変動する。

| ステップ | 目安 |
|---|---|
| プリフライト | 1 分未満 |
| ネスト VPC エンドポイントスタック(作成する場合) | 3〜5 分 |
| Kafka broker スタック(EC2 + IAM + SG) | 3〜5 分 |
| 合計 | 概ね 6〜12 分(WAL ボリューム設定・検証の手動ステップは含まない) |

## 想定デプロイ経路

下記は**想定している経路**であり、silly-rename のライブ実行(spec タスク 16)は未実施である。したがってライブの挙動確認を伴う段は **Claim_Tier: unverified/hypothesis** として扱い、検証済みとしては提示しない。ライブ実行後に Verification_Record へ結果を記録して初めて `sample-run` になる。

| スタック | エンドポイント設定 | Claim_Tier |
|---|---|---|
| broker スタックのみ(既存 VPC エンドポイント再利用) | `VpcEndpointsTemplateUrl` を空 | unverified(ライブ未実行) |
| broker + ネスト VPC エンドポイント(`PACKAGE_BUCKET` を設定) | S3 Gateway + Secrets Manager Interface を作成 | unverified(ライブ未実行) |
| NFS 上 Kafka の silly-rename 非クラッシュ | 上記に加え WAL/ログの NFS マウント(下記ガードレールの検証後) | hypothesis(ライブ未実行、spec タスク 16 で確認予定) |

## デプロイ手順

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export FILE_SYSTEM_ID="fs-0123456789abcdef0"
export VPC_ID="vpc-0123456789abcdef0"
export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export PACKAGE_BUCKET="my-cfn-package-bucket"
bash integrations/pipeline-verification/pattern-4-kafka-automq/scripts/deploy.sh --profile pipeline-pattern-4 --mode automq-wal
```

`deploy.sh` は次の順で動く: (1) Reuse_Reference 解決 → (2) プリフライト → (3) `aws sts get-caller-identity` でアカウント解決 → (4) 共有 VPC エンドポイントテンプレートのパッケージと `aws cloudformation deploy`。

## 検証手順

```bash
bash integrations/pipeline-verification/pattern-4-kafka-automq/scripts/verify.sh
```

スタックの健全性、broker の SSM 到達性、Gen2 高スループット WAL の意図を確認する。負荷/スケールの測定は含めない。

NFS 上 Kafka の silly-rename サンプル実行は、稼働中の FSx for ONTAP アカウントを要し課金が発生する(spec タスク 16)。実行後、結果を Verification_Record に Claim_Tier `sample-run` で記録する。

## コスト要因

すべてのコスト値に測定日・リージョン・構成条件を併記する。下表は 2026-07(`ap-northeast-1`、最小構成)時点の目安であり、実測ではなく公開単価に基づく。AutoMQ のベンチマーク値は AWS 自身が公開した数値(下記に出典明記)であり、ここでの実測ではない。

| 要因 | 補足 |
|---|---|
| FSx for ONTAP Gen2 高スループット WAL | **標準構成より高コスト**であり、稼働中は継続課金される。本パターン最大の定常コスト要因。この環境はファイルシステムを作成・削除せず、共有基盤として再利用する |
| EC2 Kafka broker | 稼働中は継続課金(既定 `m7g.large`。AWS ベンチは `m7g.4xlarge` を使用) |
| S3(AutoMQ 耐久層) | S3 バックエンドの耐久層に対するストレージとリクエストの課金 |
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金)。本環境は S3 Gateway Endpoint を用いる。Interface Endpoint は構成で作成する場合のみ計上する |

**AWS が公開した AutoMQ ベンチマーク**(AWS 自身の測定であり、本ドキュメントの測定ではない): [AWS Storage ブログ記事(2026年)](https://aws.amazon.com/blogs/storage/achieving-sub-10ms-latency-and-94-cost-savings-with-diskless-kafka-using-automq-and-amazon-fsx-for-netapp-ontap/)は、m7g.4xlarge broker 3 台、マルチ AZ の FSx for ONTAP Generation 2(1,024 GiB、プロビジョニング IOPS 3,072、736 MBps、us-east-1)で、平均エンドツーエンドレイテンシー 7.79 ms(P99 18.04 ms)、および従来型 3 レプリカ・マルチ AZ Kafka の月額約 $317,000 に対し AutoMQ BYOC が同じ P99 書き込みレイテンシー目標で月額約 $18,345 というコスト比較を報告している。これらは AWS 自身の数値として引用し、この環境の測定値と区別する。

## Ephemeral 構成

検証に必要な最小構成で立ち上げ、検証後に速やかに撤去する。`m7g.large` と少ない broker 数は検証用サイジングであり、本番容量計画ではない。検証が済んだら `teardown.sh` で撤去し、他のパターンが必要としないなら基盤スタックの Gen2 高スループット設定を下げる。

## テアダウン手順

```bash
bash integrations/pipeline-verification/pattern-4-kafka-automq/scripts/teardown.sh
```

逆依存順でスタックを削除し、削除完了は削除 API の応答ではなくスタック状態のポーリングで判定する。Gen2 高スループット WAL 構成を課金したまま残す前にコストの注意喚起を提示し、FSx for ONTAP ファイルシステムは既定で削除しない(共有基盤であり、このスタックの所有物ではない)。`--delete-fsxn` を付けると不可逆確認ゲートを表示するが、`-y`/`--yes` では省略できない。

## Day 2 運用

デプロイ後の確認と、本番へ進める場合の継続的な運用の起点。

- **デプロイ直後の確認**: `verify.sh` を実行し、スタックの健全性・broker の SSM 到達性・Gen2 高スループット WAL の意図を確認する。SSM Session Manager で broker に入り、WAL/ログの NFS マウントが自動実行されていないこと(silly-rename ガードレール)を確認する。
- **継続的な確認**: broker の稼働と AutoMQ 耐久層(S3)への書き込み成否を監視する。NFS 上に WAL/ログを置く場合は、下記ガードレールの `-is-preserve-unlink-enabled=true` 設定が維持されていることを継続的に確認する。
- **アラート/監視の接続先**: AWS リソース側(EC2・S3)の CloudWatch メトリクスとアラームは `shared/templates/fsxn-monitoring-dashboard.yaml` を参照点として接続する。ルーティング/変換層の可観測性は `integrations/otel-collector/`(文書化ポインタ)を参照する。本パターンはこれらを自動作成しない(検証環境のため)。
- **定期レビュー**: Ephemeral 構成を長く残さない。とりわけ Gen2 高スループット WAL は本パターン最大の定常コスト要因のため、検証が済んだら `teardown.sh` で撤去し、他のパターンが必要としないなら基盤スタックの Gen2 高スループット設定を下げる。

## ロールバックとクリーンアップ

これは通常のテアダウン(検証完了後の撤去)とは別で、デプロイが途中で失敗したときの復旧手順である。

- **CREATE_FAILED からの復旧**: スタックが CREATE_FAILED で止まったら、`aws cloudformation delete-stack --stack-name fsxn-pattern-4-kafka-automq` で削除する。ROLLBACK_COMPLETE 状態のスタックは更新できないため、削除してから作り直す。
- **プリフライト再実行**: 削除後、原因(多くは VPC エンドポイント競合)を直してから `preflight-check.sh --profile pipeline-pattern-4` を再実行し、緑になってから再デプロイする。
- **よくある VPC EP 競合の修正**: S3 Gateway または Secrets Manager Interface Endpoint が既存なら、`VpcEndpointsTemplateUrl` を空にして既存を再利用する(上の競合マトリクス参照)。
- **完全撤去**: 検証完了後の通常撤去は `teardown.sh`(上記テアダウン手順)を用いる。Gen2 高スループット WAL を課金したまま残さないよう、コスト注意喚起に従う。

## ONTAP バージョン要件

broker のログ/WAL ディレクトリを NFS 上に置く場合、silly-rename 修正 `-is-preserve-unlink-enabled=true`(ONTAP 9.12.1 で導入)を要する。上記「FSx for ONTAP silly-rename ガードレール」で述べたとおり、ここでは 2 つを区別して扱う。

- **ONTAP バージョンの前提は満たされている。** FSx for ONTAP ファイルシステムは ONTAP 9.18.1 以降を実行する。これは AWS サービスチームとの現場での直接確認であり、公開されたバージョン番号ではない — 読者は公開の FSx for ONTAP ドキュメントだけからこれを検証できない。9.18.1 は 9.12.1 よりかなり後なので、バージョンは修正をサポートする。
- **特定の FSx for ONTAP ボリュームが設定され E2E で検証されたことは未確認。** ボリュームで `-is-preserve-unlink-enabled=true` を設定・検証してから WAL/ログをマウントする手順は運用者側のステップであり、ライブの確認は spec タスク 16(課金あり)。

したがって `deploy.sh` は WAL/ログを NFS に自動マウントしない。バージョン前提を満たすことと、特定ボリュームでの設定・検証がまだであることを混同しない。

## スコープ境界

この検証環境は検証環境と検証記録を対象とし、本番グレードデプロイを対象としない。Kafka のマルチリージョン HA はスコープ外。サンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。ここでの AutoMQ のレイテンシ/コスト値は AWS の引用ベンチマークであり、再現ではない。

## 関連ドキュメント

- [検証記録: パターン 4](../../../../docs/ja/observability-storage-patterns/verification/verification-results-pattern-4.md)
- [統合の入口](../../README.md)
- [ストレージ統合パターン](../../../../docs/ja/observability-storage-patterns/README.md)
