# パターン 2 セットアップガイド: Prometheus + remote_write 検証環境

🌐 **日本語**(このページ) | [English](../en/setup-guide.md)

Prometheus + remote_write パイプラインの検証環境を、既存資産の再利用を土台に立ち上げる手順。これは検証環境と検証記録を対象とし、本番グレードデプロイを対象としない。

## 概要

Prometheus はスクレイプしたメトリクスをローカル TSDB(EBS ローカルブロックストレージ)に書き込み、`remote_write` で長期ストレージバックエンド(Thanos / Mimir / Cortex またはベンダープラットフォーム)へ転送する。その長期バックエンドのオブジェクトストアエクスポートを、FSx for ONTAP の S3 Access Points にアーカイブする。

この検証環境が扱うのは remote_write の下流アーカイブ経路であり、Prometheus 自身のローカル TSDB ではない。

## 既存カバレッジと net-new の区別

| 層 | 区分 | 実体 |
|---|---|---|
| Prometheus サーバ(ローカル TSDB) | net-new | `template.yaml`(本パターン) |
| remote_write アーカイブ先(S3 Access Points) | 再利用 | `shared/templates/s3-access-point.yaml`(ネストスタック) |
| 可視化・アラート | 再利用 | `integrations/grafana/`(文書化ポインタ) |
| FSx for ONTAP ファイルシステム + SVM | 再利用 | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml`(文書化ポインタ) |

Prometheus 層が net-new なのは、リポジトリに汎用アーカイブ層(S3 Access Points)は既にあるが Prometheus 層が存在しないため。アーカイブ先は既存の共有テンプレートをネストスタックで参照し、コピーしない。

## FSx for ONTAP ガードレール

**Prometheus はローカル TSDB について NFS/EFS 非対応を明記している。** `--storage.tsdb.path` を FSx for ONTAP の NFS マウントに向けてはならない。本テンプレートはこの理由でローカル TSDB を専用の EBS ボリューム(`/dev/xvdb`)に置き、NFS をマウントしない。

FSx for ONTAP が触れるのは remote_write の下流エクスポート(長期バックエンドのオブジェクトストア出力)のみであり、Prometheus 自身のホット TSDB ではない。

## 前提条件

- 稼働中の FSx for ONTAP ファイルシステム(SVM を含む)
- 配置先 VPC とプライベートサブネット
- remote_write の長期アーカイブを裏付ける既存 S3 バケット(アーカイブ先を有効化する場合)
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
| `ARCHIVE_BUCKET_NAME`(アーカイブ先 S3 バケット) | `aws s3 ls`(remote_write バックエンドのエクスポート先を用いる) |
| `PACKAGE_BUCKET`(ネストスタックテンプレート配置用 S3 バケット) | `aws s3 ls`(CloudFormation がネストスタックテンプレートを読める同一リージョンのバケット) |

## パラメータ

`shared/templates/restore-verification.yaml` の AllowedPattern 規約を踏襲する。アカウント ID はハードコードせず、`deploy.sh` が実行時に `aws sts get-caller-identity` で解決する。リージョンは `AWS_REGION` 環境変数で上書きする(既定 `ap-northeast-1`)。例値はプレースホルダであり、自環境の値に置き換える。

| パラメータ | 説明 | 取得方法・例値 | 必須 |
|---|---|---|---|
| `VpcId` | Prometheus サーバを配置する VPC | `aws ec2 describe-vpcs`。例 `vpc-0123456789abcdef0` | 必須 |
| `SubnetIds` | プライベートサブネット(先頭にインスタンスを配置) | `aws ec2 describe-subnets`。例 `subnet-0123456789abcdef0,subnet-0123456789abcdef1` | 必須 |
| `FileSystemId` | FSx for ONTAP ファイルシステム ID(`^fs-[0-9a-f]{17}$`) | `aws fsx describe-file-systems`。例 `fs-0123456789abcdef0` | 必須 |
| `OntapMgmtIp` | ONTAP 管理エンドポイント IP | `aws fsx describe-file-systems`。例 `198.51.100.10` | 必須 |
| `SvmId` | SVM 識別子 | `aws fsx describe-storage-virtual-machines`。例 `svm-0123456789abcdef0` | 任意 |
| `ReuseArchiveStackName` | 再利用する S3 Access Point アーカイブスタック名 | 任意の命名。例 `fsxn-pattern-2-remote-write-archive` | 任意(既定あり) |
| `PrometheusInstanceType` | Prometheus サーバの EC2 インスタンスタイプ | 例 `t3.medium`(検証用サイジング) | 任意(既定 `t3.medium`) |
| `LocalTsdbVolumeSizeGiB` | ローカル TSDB 用 EBS ボリュームサイズ(GiB) | 例 `50` | 任意(既定 50) |

## プリフライト検証

スタンドアップ前に、既存の共有プリフライトスクリプトをパターン 2 プロファイルで実行する。`deploy.sh` がこれを自動で呼ぶ。

```bash
bash shared/scripts/preflight-check.sh --profile pipeline-pattern-2 --vpc-id vpc-0123456789abcdef0
```

VPC エンドポイント競合を検出した場合はスタンドアップを続行せず、修正指示(`CreateXxxEndpoint=false` 等)を提示する。

## VPC エンドポイント競合マトリクス

本プロジェクトで最も多いデプロイ失敗は VPC エンドポイント競合による CREATE_FAILED である。パターン 2 のアーカイブ経路は S3 Gateway Endpoint を用いる。同一 VPC・同一ルートテーブルに同一サービスの Gateway Endpoint が既にあると競合する。プリフライト(`--profile pipeline-pattern-2`)がこれを検出する。

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
| Prometheus サーバスタック(EC2 + EBS + IAM) | 3〜5 分 |
| ネストアーカイブスタック(S3 Access Point) | 2〜4 分 |
| 合計 | 概ね 5〜10 分 |

## 想定デプロイ経路

下記は**想定している経路**であり、ライブ実行(spec タスク 10)は未実施である。したがってライブの疎通確認を伴う段は **Claim_Tier: unverified/hypothesis** として扱い、検証済みとしては提示しない。ライブ実行後に Verification_Record へ結果を記録して初めて `sample-run` になる。

| スタック | エンドポイント設定 | Claim_Tier |
|---|---|---|
| Prometheus サーバスタックのみ(アーカイブ無効) | VPC EP 追加なし | unverified(ライブ未実行) |
| Prometheus + ネストアーカイブ(`ARCHIVE_BUCKET_NAME` + `PACKAGE_BUCKET` を設定) | 既存 S3 Gateway Endpoint を再利用 | unverified(ライブ未実行) |
| remote_write → S3 Access Points 疎通 | 上記に加え remote_write バックエンド接続 | hypothesis(ライブ未実行、spec タスク 10 で確認予定) |

## デプロイ手順

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export FILE_SYSTEM_ID="fs-0123456789abcdef0"
export VPC_ID="vpc-0123456789abcdef0"
export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export ARCHIVE_BUCKET_NAME="my-remote-write-archive-bucket"
export PACKAGE_BUCKET="my-cfn-package-bucket"
bash integrations/pipeline-verification/pattern-2-prometheus/scripts/deploy.sh --profile pipeline-pattern-2
```

`deploy.sh` は次の順で動く: (1) Reuse_Reference 解決 → (2) プリフライト → (3) `aws sts get-caller-identity` でアカウント解決 → (4) 共有アーカイブテンプレートのパッケージと `aws cloudformation deploy`。

## 検証手順

```bash
bash integrations/pipeline-verification/pattern-2-prometheus/scripts/verify.sh
```

スタックの健全性、Prometheus サーバの SSM 到達性、再利用アーカイブ先の解決を確認する。負荷/スケールの測定は含めない。

remote_write → S3 Access Points の疎通サンプル実行は、稼働中の FSx for ONTAP アカウントを要し課金が発生する(spec タスク 10)。結果は Verification_Record に Claim_Tier `sample-run` で記録する。

## コスト要因

すべてのコスト値に測定日・リージョン・構成条件を併記する。下表は 2026-07(`ap-northeast-1`、単一 AZ、最小構成)時点の目安であり、実測ではなく公開単価に基づく。

| 要因 | 補足 |
|---|---|
| Interface VPC Endpoint | AZ あたり月額約 7.20 USD/エンドポイント(AZ 単位の ENI 課金)。本環境は S3 Gateway Endpoint を用いるため Interface Endpoint は不要だが、既存構成で作成する場合は計上する |
| FSx for ONTAP ファイルシステム | 稼働中は継続課金。この検証環境が作成・削除する対象ではなく、共有基盤として再利用する |
| EC2 Prometheus サーバ | 稼働中は継続課金(既定 `t3.medium`) |
| EBS ローカル TSDB ボリューム | 稼働中は継続課金(既定 50 GiB gp3) |

## Ephemeral 構成

検証に必要な最小構成で立ち上げ、検証後に速やかに撤去する。`t3.medium` と 50 GiB のローカル TSDB は検証用サイジングであり、本番容量計画ではない。検証が済んだら `teardown.sh` で撤去する。

## テアダウン手順

```bash
bash integrations/pipeline-verification/pattern-2-prometheus/scripts/teardown.sh
```

逆依存順でスタックを削除し、削除完了は削除 API の応答ではなくスタック状態のポーリングで判定する。FSx for ONTAP ファイルシステムは既定で削除しない(共有基盤であり、このスタックの所有物ではない)。`--delete-fsxn` を付けると不可逆確認ゲートを表示するが、`-y`/`--yes` では省略できない。

## Day 2 運用

デプロイ後の確認と、本番へ進める場合の継続的な運用の起点。

- **デプロイ直後の確認**: `verify.sh` を実行し、スタックの健全性・Prometheus サーバの SSM 到達性・再利用アーカイブ先の解決を確認する。SSM Session Manager でサーバに入り、`--storage.tsdb.path` がローカル EBS マウント(`/var/lib/prometheus`)を指し、NFS をマウントしていないことを確認する(ガードレール)。
- **継続的な確認**: remote_write の送信成功率と、長期バックエンドから FSx for ONTAP の S3 Access Points へのアーカイブエクスポートの成否を監視する。ローカル TSDB は `RemoteWriteRetentionDays`(既定 15 日)で切れるため、その前に remote_write が system of record になっていることを確認する。
- **アラート/監視の接続先**: Prometheus 自身の可視化・アラートは `integrations/grafana/`(可視化層の再利用)を参照する。AWS リソース側(EC2・EBS・S3)の CloudWatch メトリクスとアラームは `shared/templates/fsxn-monitoring-dashboard.yaml` を参照点として接続する。本パターンはこれらを自動作成しない(検証環境のため)。
- **定期レビュー**: Ephemeral 構成を長く残さない。検証が済んだら `teardown.sh` で撤去し、継続課金(EC2・EBS)を止める。

## ロールバックとクリーンアップ

これは通常のテアダウン(検証完了後の撤去)とは別で、デプロイが途中で失敗したときの復旧手順である。

- **CREATE_FAILED からの復旧**: スタックが CREATE_FAILED で止まったら、`aws cloudformation delete-stack --stack-name fsxn-pattern-2-prometheus` で削除する。ROLLBACK_COMPLETE 状態のスタックは更新できないため、削除してから作り直す。
- **プリフライト再実行**: 削除後、原因(多くは VPC エンドポイント競合)を直してから `preflight-check.sh --profile pipeline-pattern-2` を再実行し、緑になってから再デプロイする。
- **よくある VPC EP 競合の修正**: 同一サービスの Gateway/Interface Endpoint が既存なら、既存を再利用する構成に切り替える(上の競合マトリクス参照)。
- **完全撤去**: 検証完了後の通常撤去は `teardown.sh`(上記テアダウン手順)を用いる。

## ONTAP バージョン要件

パターン 2 が FSx for ONTAP に触れるのは remote_write の下流アーカイブエクスポート(S3 Access Points への書き込み)のみであり、ホット TSDB はローカル EBS に置く。S3 Access Points を用いる本経路は、標準的な FSx for ONTAP の S3 対応バージョンで動作し、silly-rename のような特定バージョン依存の前提を持たない。特別なバージョン前提を必要としない点で、パターン 4 のような直接確認事項はない。自環境の FSx for ONTAP が S3 Access Points をサポートするバージョンであることを確認すれば足りる。

## スコープ境界

この検証環境は検証環境と検証記録を対象とし、本番グレードデプロイを対象としない。各 TSDB のマルチリージョン HA はスコープ外。サンプル実行を超える負荷/スケールベンチマークは、明示的に追求する場合を除きスコープ外。

## 関連ドキュメント

- [パターン 2 概説(ストレージ統合パターン)](../../../../docs/ja/observability-storage-patterns/pattern-2-prometheus-remote-write.md)
- [検証記録: パターン 2](../../../../docs/ja/observability-storage-patterns/verification/verification-results-pattern-2.md)
- [統合の入口](../../README.md)
