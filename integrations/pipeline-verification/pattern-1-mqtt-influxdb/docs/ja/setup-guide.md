# パターン 1 セットアップガイド: MQTT → InfluxDB → Grafana Live 検証環境

🌐 **日本語**(このページ) | [English](../en/setup-guide.md)

MQTT broker + InfluxDB の検証環境を、既存資産の再利用を土台に立ち上げる手順。これは検証環境と検証記録を対象とし、本番グレードデプロイを対象としない。

## 概要

MQTT broker(既定は Mosquitto)が VPC 内の publisher からテレメトリを受け、単一ノードの InfluxDB サーバ(v1 または v2。ともに TSM ストレージエンジン)が直近データをローカルのホット層(EBS ローカルブロックボリューム)に保持する。古いデータは FSx for ONTAP の S3 Access Points 上のコールドアーカイブへエクスポートする。ライブダッシュボード層は Grafana であり、`integrations/grafana/` から文書化ポインタで再利用し、ここでは再構築しない。

この検証環境が扱うのは net-new の broker + InfluxDB サーバ層と下流のアーカイブ経路であり、クラスタ化された本番デプロイではない。

## 既存カバレッジと net-new の区別

| 層 | 区分 | 実体 |
|---|---|---|
| MQTT broker(Mosquitto) | net-new | `template.yaml`(本パターン) |
| InfluxDB v1/v2 単一ノードサーバ(ローカルホット層) | net-new | `template.yaml`(本パターン) |
| Grafana Live 可視化 | 再利用(部分カバー済み) | `integrations/grafana/`(必須の文書化ポインタ、要件 2-9) |
| アーカイブ先(S3 Access Points) | 再利用 | `shared/templates/s3-access-point.yaml`(ネストスタック) |
| ingestion 例(IoT Core → Lambda → S3 Access Point) | 再利用 | `ontap-edge-to-cloud-ai:cloud/iot_ingestion/`(文書化ポインタ) |
| FSx for ONTAP ファイルシステム + SVM | 再利用 | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml`(文書化ポインタ) |

broker と InfluxDB サーバが net-new なのは、リポジトリに汎用アーカイブ層(S3 Access Points)と Grafana 統合はあるが、MQTT broker と時系列データベースサーバが存在しないため。Grafana 可視化層は既に**部分カバー済み**であり — `integrations/grafana/` がダッシュボードとアラートを提供する — 文書化ポインタで必須参照し、再実装しない(要件 2-9)。アーカイブは既存の共有テンプレートをネストスタックで参照し、コピーしない。サーバ自身の EC2・セキュリティグループ・自己参照 MQTT ingress・インスタンスロール/プロファイルは、既存のどのテンプレートともリソース形状を共有しない。

## Grafana 可視化の参照

ライブダッシュボード層は**再利用であり、再構築しない**。`integrations/grafana/` に従い、Grafana をサーバホスト上の InfluxDB HTTP API(自己参照セキュリティグループ ingress により VPC 内から到達可能)に向ける。Grafana のダッシュボードやプロビジョニングを本パターンにコピーせず、単一の真実の源として保つためその統合を参照する。

## ingestion 例の参照

エンドツーエンドのマネージド ingestion 経路(IoT Core → Lambda → FSx for ONTAP S3 Access Point)は、`ontap-edge-to-cloud-ai:cloud/iot_ingestion/`(sibling リポジトリ。ローカルチェックアウト名は `edge-to-cloud-ai`)を文書化ポインタとして参照する。ingestion 側の例であり本リポジトリにはコピーしない。ここでの MQTT broker が net-new の VPC 内経路である。

## InfluxDB v1 と v2 の選び方

InfluxDB v1 と v2 は用途に応じて選ぶものであり、バージョンの優劣を競うものではない。ともに TSM ストレージエンジンを用い、下記の FSx for ONTAP ガードレールを共有する。トレードオフは対称に記載する。

| ライン | 適する用途 | トレードオフ・考慮点 |
|---|---|---|
| v2(ここでの既定) | Flux・組み込み UI・トークン・buckets/orgs を求める新規構成 | Flux は別のクエリ言語。InfluxQL ツールやダッシュボードを持つチームは移行するか v1 互換 API を使う必要がある |
| v1 | 既存の InfluxQL クエリ・Grafana パネル・v1 向け Telegraf 設定を持つチーム | 旧ライン。エコシステムは v2/v3 に集約しつつあるため、いずれの移行経路を計画する |

両者とも本環境ではデータディレクトリをローカルディスクに置き、この選択は下記の FSx for ONTAP ガードレールを変えない。

## FSx for ONTAP ガードレール

**InfluxDB v1/v2(TSM エンジン)は、データディレクトリについて NFS ロック失敗(stale NFS file handle)を明記している。** InfluxDB のデータディレクトリを FSx for ONTAP の NFS マウントに向けてはならない。本テンプレートはこの理由でホット層を専用のローカル EBS ボリューム(`/dev/xvdb`)に置き、NFS をマウントしない。

**iSCSI LUN のホット層は Hot_Tier_Storage_Hypothesis(仮説)である。** iSCSI LUN をホット層のローカルブロックデバイスとして用いる構成は成立しうるが、ここでは断定しない。既定の EBS ホット層と区別し、Verification_Record に名指しの測定対象 — iSCSI LUN 上の InfluxDB v1/v2 の read/write/ロック正当性 — として Claim_Tier `hypothesis` で追跡する。read → write → ロック取得の正当性を確認するサンプル実行 1 回(ライブの spec タスク 19。ECS Fargate は iSCSI initiator を持てないため EC2 前提)で `sample-run` に格上げする。FSx for ONTAP が触れるのは下流のアーカイブエクスポートのみであり、ホットデータディレクトリではない。

## 前提条件

- 稼働中の FSx for ONTAP ファイルシステム(SVM を含む)
- 配置先 VPC とプライベートサブネット
- アーカイブを裏付ける既存 S3 バケット(アーカイブ先を有効化する場合)
- CloudFormation / EC2 / IAM / S3 権限を持つ AWS CLI v2

## 着手前に用意するもの

初回に最もつまずくのは、自環境の既存リソース ID をデプロイ中に探し始めることである。下表の値を**先に**手元に集めておく。いずれもこの検証環境が作成するものではなく、既存の共有基盤から参照する。

| 用意するもの | 取得方法 |
|---|---|
| `VpcId`(配置先 VPC) | `aws ec2 describe-vpcs --query 'Vpcs[].VpcId' --output text` |
| `SubnetIds`(プライベートサブネット) | `aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc-id> --query 'Subnets[].SubnetId' --output text` |
| `FileSystemId`(FSx for ONTAP) | `aws fsx describe-file-systems --query 'FileSystems[?FileSystemType==\`ONTAP\`].FileSystemId' --output text` |
| `OntapMgmtIp`(ONTAP 管理 IP) | `aws fsx describe-file-systems --query 'FileSystems[].OntapConfiguration.Endpoints.Management.IpAddresses' --output text` |
| `SvmId`(SVM 識別子。任意) | `aws fsx describe-storage-virtual-machines --query 'StorageVirtualMachines[].StorageVirtualMachineId' --output text` |
| `ARCHIVE_BUCKET_NAME`(アーカイブ先 S3 バケット) | `aws s3 ls`(並行アーカイブ経路の書き込み先を用いる) |
| `PACKAGE_BUCKET`(ネストスタックテンプレート配置用 S3 バケット) | `aws s3 ls`(CloudFormation がネストスタックテンプレートを読める同一リージョンのバケット) |

## パラメータ

`shared/templates/restore-verification.yaml` とパターン 2/5 の AllowedPattern 規約を踏襲する。アカウント ID はハードコードせず、`deploy.sh` が実行時に `aws sts get-caller-identity` で解決する。リージョンは `AWS_REGION` 環境変数で上書きする(既定 `ap-northeast-1`)。例値はプレースホルダであり、自環境の値に置き換える。

| パラメータ | 説明 | 取得方法・例値 | 必須 |
|---|---|---|---|
| `VpcId` | broker と InfluxDB サーバを配置する VPC | `aws ec2 describe-vpcs`。例 `vpc-0123456789abcdef0` | 必須 |
| `SubnetIds` | プライベートサブネット(先頭にインスタンスを配置) | `aws ec2 describe-subnets`。例 `subnet-0123456789abcdef0,subnet-0123456789abcdef1` | 必須 |
| `FileSystemId` | FSx for ONTAP ファイルシステム ID(`^fs-[0-9a-f]{17}$`) | `aws fsx describe-file-systems`。例 `fs-0123456789abcdef0` | 必須 |
| `OntapMgmtIp` | ONTAP 管理エンドポイント IP | `aws fsx describe-file-systems`。例 `198.51.100.10` | 必須 |
| `SvmId` | SVM 識別子 | `aws fsx describe-storage-virtual-machines`。例 `svm-0123456789abcdef0` | 任意 |
| `InfluxDbLine` | `v2`(既定)または `v1` | 選び方は下記の「InfluxDB v1 と v2 の選び方」参照。例 `v2` | 任意(既定 `v2`) |
| `ReuseArchiveStackName` | 再利用する S3 Access Point アーカイブスタック名 | 任意の命名。例 `fsxn-pattern-1-archive` | 任意(既定あり) |
| `MqttBrokerPort` | Mosquitto broker の MQTT リスナポート | 例 `1883`(VPC 内 publisher からのみ到達) | 任意(既定 1883) |
| `LocalHotTierVolumeSizeGiB` | ローカルホット層用 EBS ボリュームサイズ(GiB) | 例 `50` | 任意(既定 50) |

## プリフライト検証

スタンドアップ前に、既存の共有プリフライトスクリプトをパターン 1 プロファイルで実行する。`deploy.sh` がこれを自動で呼ぶ。

```bash
bash shared/scripts/preflight-check.sh --profile pipeline-pattern-1 --vpc-id vpc-0123456789abcdef0
```

VPC エンドポイント競合を検出した場合はスタンドアップを続行せず、修正指示(`CreateXxxEndpoint=false` 等)を提示する。

## VPC エンドポイント競合マトリクス

本プロジェクトで最も多いデプロイ失敗は VPC エンドポイント競合による CREATE_FAILED である。パターン 1 のアーカイブ経路は S3 Gateway Endpoint を用いる。同一 VPC・同一ルートテーブルに同一サービスの Gateway Endpoint が既にあると競合する。プリフライト(`--profile pipeline-pattern-1`)がこれと、S3 Access Point のネットワークオリジン・AD DC 到達性(AD 参加 SVM の場合)を確認する。

| エンドポイント種別 | 競合条件 | 回避策 |
|---|---|---|
| S3 Gateway Endpoint | 同一 VPC・同一ルートテーブルに S3 Gateway Endpoint が既存 | 既存を再利用する。本パターンは Gateway を追加作成しないため、既存があればそのまま使う |
| Interface Endpoint(SSM 等) | 同一 VPC に同一サービスの Interface Endpoint(PrivateDns 有効)が既存 | 既存を再利用する。SSM Session Manager 到達性は既存の Interface Endpoint で満たす |

競合を検出したら、既存エンドポイントを再利用する構成に切り替えてから再実行する。

## デプロイ所要時間の目安

下表は目安であり、測定された保証値ではない(Claim_Tier: unverified)。実際の時間はリージョン・インスタンスタイプ・アカウントの状態で変動する。

| ステップ | 目安 |
|---|---|
| プリフライト | 1 分未満 |
| broker + InfluxDB サーバスタック(EC2 + EBS + IAM + SG) | 3〜5 分 |
| ネストアーカイブスタック(S3 Access Point) | 2〜4 分 |
| 合計 | 概ね 5〜10 分 |

## 想定デプロイ経路

下記は**想定している経路**であり、iSCSI ホット層のライブ実行(spec タスク 19)は未実施である。したがってライブの疎通・ロック正当性を伴う段は **Claim_Tier: unverified/hypothesis** として扱い、検証済みとしては提示しない。ライブ実行後に Verification_Record へ結果を記録して初めて `sample-run` になる。

| スタック | エンドポイント設定 | Claim_Tier |
|---|---|---|
| broker + InfluxDB サーバスタックのみ(アーカイブ無効) | VPC EP 追加なし | unverified(ライブ未実行) |
| broker + InfluxDB + ネストアーカイブ(`ARCHIVE_BUCKET_NAME` + `PACKAGE_BUCKET` を設定) | 既存 S3 Gateway Endpoint を再利用 | unverified(ライブ未実行) |
| iSCSI LUN ホット層上の InfluxDB v1/v2 read/write/ロック正当性 | 上記に加え EC2 への iSCSI LUN マウント | hypothesis(ライブ未実行、spec タスク 19 で確認予定) |

## デプロイ手順

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export FILE_SYSTEM_ID="fs-0123456789abcdef0"
export VPC_ID="vpc-0123456789abcdef0"
export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export ARCHIVE_BUCKET_NAME="my-archive-bucket"
export PACKAGE_BUCKET="my-cfn-package-bucket"
bash integrations/pipeline-verification/pattern-1-mqtt-influxdb/scripts/deploy.sh --profile pipeline-pattern-1 --line v2
```

`deploy.sh` は次の順で動く: (1) Reuse_Reference 解決 → (2) プリフライト → (3) `aws sts get-caller-identity` でアカウント解決 → (4) 共有アーカイブテンプレートのパッケージと `aws cloudformation deploy`。

## 検証手順

```bash
bash integrations/pipeline-verification/pattern-1-mqtt-influxdb/scripts/verify.sh
```

スタックの健全性、InfluxDB サーバの SSM 到達性、再利用アーカイブ先の解決を確認する。負荷/スケールの測定は含めない。

iSCSI LUN ホット層のサンプル実行は、稼働中の FSx for ONTAP アカウントを要し課金が発生する(spec タスク 19)。実行後、結果を Verification_Record に Claim_Tier `sample-run` で記録する。

## コスト要因

すべてのコスト値に測定日・リージョン・構成条件を併記する。下表は 2026-07(`ap-northeast-1`、単一 AZ、最小構成)時点の目安であり、実測ではなく公開単価に基づく。

| 要因 | 補足 |
|---|---|
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金)。本環境は S3 Gateway Endpoint を用いるため Interface Endpoint は不要だが、既存構成で作成する場合は計上する |
| FSx for ONTAP ファイルシステム | 稼働中は継続課金。この検証環境が作成・削除する対象ではなく、共有基盤として再利用する |
| EC2 InfluxDB サーバ | 稼働中は継続課金(既定 `t3.medium`) |
| EBS ローカルホット層ボリューム | 稼働中は継続課金(既定 50 GiB gp3) |

## Ephemeral 構成

検証に必要な最小構成で立ち上げ、検証後に速やかに撤去する。`t3.medium` と 50 GiB のローカルホット層は検証用サイジングであり、本番容量計画ではない。検証が済んだら `teardown.sh` で撤去する。

## テアダウン手順

```bash
bash integrations/pipeline-verification/pattern-1-mqtt-influxdb/scripts/teardown.sh
```

逆依存順でスタックを削除し、削除完了は削除 API の応答ではなくスタック状態のポーリングで判定する。FSx for ONTAP ファイルシステムは既定で削除しない(共有基盤であり、このスタックの所有物ではない)。`--delete-fsxn` を付けると不可逆確認ゲートを表示するが、`-y`/`--yes` では省略できない。

## Day 2 運用

デプロイ後の確認と、本番へ進める場合の継続的な運用の起点。

- **デプロイ直後の確認**: `verify.sh` を実行し、スタックの健全性・InfluxDB サーバの SSM 到達性・再利用アーカイブ先の解決を確認する。SSM Session Manager でサーバに入り、InfluxDB のデータディレクトリがローカル EBS マウント(`/var/lib/influxdb`)を指し、NFS をマウントしていないことを確認する(TSM ロックガードレール)。
- **継続的な確認**: MQTT ingest の受信、ホット層(ローカル EBS または iSCSI LUN 仮説)の書き込み・ロック取得の健全性、アーカイブへの S3 Access Points エクスポートの成否を監視する。ホット層は `HotTierRetentionDays`(既定 15 日)で切れるため、その前にアーカイブが system of record になっていることを確認する。
- **アラート/監視の接続先**: ライブダッシュボードとアラートは `integrations/grafana/`(可視化層の必須の文書化ポインタ、要件 2-9)を参照する。AWS リソース側(EC2・EBS・S3)の CloudWatch メトリクスとアラームは `shared/templates/fsxn-monitoring-dashboard.yaml` を参照点として接続する。本パターンはこれらを自動作成しない(検証環境のため)。
- **定期レビュー**: Ephemeral 構成を長く残さない。検証が済んだら `teardown.sh` で撤去し、継続課金(EC2・EBS)を止める。

## ロールバックとクリーンアップ

これは通常のテアダウン(検証完了後の撤去)とは別で、デプロイが途中で失敗したときの復旧手順である。

- **CREATE_FAILED からの復旧**: スタックが CREATE_FAILED で止まったら、`aws cloudformation delete-stack --stack-name fsxn-pattern-1-mqtt-influxdb` で削除する。ROLLBACK_COMPLETE 状態のスタックは更新できないため、削除してから作り直す。
- **プリフライト再実行**: 削除後、原因(多くは VPC エンドポイント競合)を直してから `preflight-check.sh --profile pipeline-pattern-1` を再実行し、緑になってから再デプロイする。
- **よくある VPC EP 競合の修正**: 同一サービスの Gateway/Interface Endpoint が既存なら、既存を再利用する構成に切り替える(上の競合マトリクス参照)。
- **完全撤去**: 検証完了後の通常撤去は `teardown.sh`(上記テアダウン手順)を用いる。

## ONTAP バージョン要件

パターン 1 が FSx for ONTAP に触れるのは下流のアーカイブエクスポート(S3 Access Points への書き込み)のみであり、ホットデータディレクトリはローカル EBS に置く。S3 Access Points を用いる本経路は、標準的な FSx for ONTAP の S3 対応バージョンで動作し、silly-rename のような特定バージョン依存の前提を持たない。iSCSI LUN をホット層とする仮説経路(spec タスク 19)についても、iSCSI LUN 提供は FSx for ONTAP の標準機能であり特別なバージョン前提を要しない。自環境の FSx for ONTAP が S3 Access Points と iSCSI をサポートするバージョンであることを確認すれば足りる。

## スコープ境界

この検証環境は検証環境と検証記録を対象とし、本番グレードデプロイを対象としない。InfluxDB のマルチリージョン HA はスコープ外。サンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。

## 関連ドキュメント

- [検証記録: パターン 1](../../../../docs/ja/observability-storage-patterns/verification/verification-results-pattern-1.md)
- [統合の入口](../../README.md)
- [ストレージ統合パターン](../../../../docs/ja/observability-storage-patterns/README.md)
