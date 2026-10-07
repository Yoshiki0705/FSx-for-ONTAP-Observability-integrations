# CloudWatch 監視の動作確認結果（ダッシュボードテンプレート、Terraform モジュール、Qtree クォータ監視）

🌐 **日本語**（このページ） | [English](../en/verification-results-cloudwatch-monitoring.md)

## 実施概要

このページは 4 回の実行を記録しています。先にダッシュボードテンプレートと Terraform モジュールの 2026-10-05 の実行を記載します。Qtree クォータ監視の 2026-10-06 の最初の実行は、1 回のポーリングに成功した後に停止しており、経緯として [2026-10-06 の Qtree クォータ監視の実行](#2026-10-06-の-qtree-クォータ監視の実行)に残しています。同じ日の後刻の再実行は 4 回のポーリングを完了し、`QtreeQuotaAlarm` を OK から ALARM へ遷移させて OK に戻しました: [2026-10-06 の Qtree クォータ監視の再実行](#2026-10-06-の-qtree-クォータ監視の再実行)。2026-10-06 の夜に始めた実行では、テスト用ボリュームに実データを書き込み、テンプレートとモジュールの両方のファイルシステム容量アラームを OK から ALARM へ遷移させて OK に戻しました。これで F1 が残した ALARM 経路の空白が埋まります: [2026-10-06 の容量アラームの実データによる実行](#2026-10-06-の容量アラームの実データによる実行)。2026-10-07 に、ダッシュボードの表示を修正した後のモジュールをデプロイして撮影したダッシュボードとアラーム一覧の画面は、[2026-10-07 のダッシュボードとアラームの画面](#2026-10-07-のダッシュボードとアラームの画面)にあります。

2026-10-05（UTC）に、CloudFormation のダッシュボードテンプレート `shared/templates/fsxn-monitoring-dashboard.yaml` と Terraform モジュール `terraform/fsxn-monitoring-dashboard/` を、実在する Amazon FSx for NetApp ONTAP ファイルシステム 1 つに対してデプロイしました。対象は第 1 世代、`SINGLE_AZ_1`、HA ペア 1 つです。ダッシュボードのすべての系列がデータを返し、すべてのアラームが INSUFFICIENT_DATA を抜けて OK に達しました。Terraform のボリューム単位のアラーム 2 つは、ALARM に遷移させてから OK に戻すところまで確認しました。ファイルシステムの容量アラーム（CloudFormation と Terraform）は ALARM に遷移させられませんでした。閾値の下限（50%）が、観測した利用率（約 3.5%）を上回るためです（[F1](#所見) を参照）。この実行では、テンプレートとモジュールに欠陥は見つかっていません。2026-10-07 に見つかったダッシュボードの表示の欠陥は、[所見](#所見) の補足に記載しています。

| 項目 | 値 |
|------|-----|
| 検証日時 | 2026-10-05T16:03Z から 2026-10-06T00:06Z（UTC）。約 7.5 時間の中断を含む（環境情報を参照） |
| 検証環境 | テスト環境（`ap-northeast-1`）。アイドル状態のファイルシステムでのサンプル実行 |
| 範囲 | AWS 側の CloudWatch メトリクス・アラーム・ダッシュボードのみ。この実行では ONTAP REST API を呼んでいない |
| 結果 | デプロイ・系列・OK 評価: 合格。ボリューム単位の ALARM 経路: 合格。ファイルシステム容量の ALARM 経路: 到達不可（F1） |

以下の値が示すのは、このファイルシステムで系列が存在し、アラームがそれを評価することです。負荷時の挙動、第 2 世代のファイルシステム、HA ペアが 2 つ以上のファイルシステムについては何も示しません。

---

## 環境情報

| 項目 | 値 |
|------|-----|
| AWS リージョン | `ap-northeast-1` |
| ファイルシステム | `fs-0123456789abcdef0`（プレースホルダー）、AVAILABLE |
| デプロイタイプ / HA ペア数 | `SINGLE_AZ_1`（第 1 世代）/ 1 |
| スループット容量 / SSD 容量 | 128 MBps / 1024 GiB |
| 対象ボリューム | `fsvol-0123456789abcdef0`（プレースホルダー）。ルート以外、Lifecycle `CREATED`、`svm-0123456789abcdef0`（プレースホルダー）上 |
| ソースのリビジョン | `1a2414b`（main、#105 の後）。追跡ファイルは変更していない |
| Terraform / プロバイダー | Terraform v1.15.8（darwin_arm64）、ロックファイルの `hashicorp/aws` v6.67.0（`terraform init -lockfile=readonly`） |
| その他のツール | AWS CLI、boto3 1.43.93（読み取り専用の CloudWatch 呼び出し） |
| 認証 | AWS IAM Identity Center（SSO）のセッション |
| ONTAP のバージョン | 記録なし。この実行では ONTAP REST API を呼んでいない |
| 事前に存在した名前 | デプロイ前: スタック `fsxn-verify-monitoring-dashboard` は無し、接頭辞 `fsxn-verify` のアラームとダッシュボードはいずれも 0 件 |

SSO セッションは、最初のアラーム履歴の読み取り中の 2026-10-05T16:11Z ごろに期限切れになりました。回避策は取らずに実行を中断し、再サインイン後の 2026-10-05T23:47:34Z に再開しています。中断中に存在したのは CloudFormation スタックだけです。

`list-metrics` はこのファイルシステムについて 760 系列を返しました。2 つの成果物が使う組はすべて存在します。`CPUUtilization`、`FileServerDiskIopsUtilization`、`FileServerDiskThroughputUtilization`、`NetworkThroughputUtilization`、`DataReadBytes`/`DataWriteBytes`、`DataReadOperations`/`DataWriteOperations`、`NetworkSentBytes`/`NetworkReceivedBytes`、`StorageUsed` は `FileSystemId` のみ、`StorageCapacityUtilization` は `FileSystemId` + `StorageTier=SSD` + `DataType=All` です。`FileSystemId` だけの `StorageCapacityUtilization` 系列はありません。ボリューム単位では、`StorageCapacityUtilization` が `FileSystemId` + `VolumeId`（および `StorageTier=SSD` + `DataType=All` を加えた組）で存在し、`FilesUsed` と `FilesCapacity` が `FileSystemId` + `VolumeId` で存在します。

> **ディメンションに関する補足**: このファイルシステムに `FileSystemId` だけの `StorageCapacityUtilization` 系列が無いことは、#105 のディメンション修正と整合します。その変更の前は、容量アラームとウィジェットが `FileSystemId` だけを指定しており、ここではどの系列にも一致しなかったはずです。

---

## デプロイした構成

2 つの成果物は同じファイルシステムを対象に、順にデプロイしました。どちらも通知先のメールアドレスを指定していないため、SNS トピックは作成していません。

### CloudFormation ダッシュボードスタック

`shared/templates/fsxn-monitoring-dashboard.yaml` からスタック `fsxn-verify-monitoring-dashboard` を作成しました。パラメータは `CapacityThresholdPercent=80`、`FileSystemName=verify-fs` で、`NotificationEmail` は指定していません。作成されたのはダッシュボード 1 つ（`fsxn-verify-monitoring-dashboard-verify-fs`）と、テンプレートが常に作る 2 つのアラーム `StorageCapacityAlarm` と `ThroughputUtilizationAlarm` です。SNS トピックは作成されていません。

```bash
aws cloudformation deploy \
  --template-file shared/templates/fsxn-monitoring-dashboard.yaml \
  --stack-name fsxn-verify-monitoring-dashboard \
  --parameter-overrides \
    FileSystemId=fs-0123456789abcdef0 \
    FileSystemName=verify-fs \
    CapacityThresholdPercent=80 \
  --region ap-northeast-1
```

### Terraform モジュール

ローカルのルート構成から `name_prefix = "fsxn-verify-tf"` でモジュールを適用しました。オプトインのファイルサーバーアラーム 3 つをすべて有効にし、`file_server_names` は空にしたため、いずれも第 1 世代の形である `FileSystemId` のみを指定します。`volume_ids` にはボリュームを 1 つ指定しました。`notification_email` は指定していないため、SNS トピックは作成されていません。

```hcl
file_system_id                           = "fs-0123456789abcdef0"
file_system_name                         = "verify-fs"
name_prefix                              = "fsxn-verify-tf"
enable_cpu_utilization_alarm             = true
enable_disk_iops_utilization_alarm       = true
enable_disk_throughput_utilization_alarm = true
file_server_names                        = []
volume_ids                               = ["fsvol-0123456789abcdef0"]
```

`terraform plan` は 8 件の追加を報告し、`terraform apply` はダッシュボード 1 つ（`fsxn-verify-tf-verify-fs`）とアラーム 7 つを作成しました。

| アラーム（リソース） | 由来 | 確認した範囲 |
|----------------------|------|--------------|
| `storage_capacity` | テンプレートと同等 | OK の評価 |
| `network_throughput` | テンプレートと同等 | OK の評価 |
| `file_server["cpu_utilization"]` | オプトイン | OK の評価 |
| `file_server["disk_iops_utilization"]` | オプトイン | OK の評価 |
| `file_server["disk_throughput_utilization"]` | オプトイン | OK の評価 |
| `volume_capacity["fsvol-0123456789abcdef0"]` | オプトイン、`volume_ids` | OK、ALARM、OK への復帰 |
| `volume_inode["fsvol-0123456789abcdef0"]` | オプトイン、`volume_ids`（メトリクス算術式 `100 * FilesUsed / FilesCapacity`） | OK、ALARM、OK への復帰 |

---

## テスト結果サマリー

| # | 確認項目 | 結果 | 時刻（UTC） |
|---|----------|------|-------------|
| P4-1 | CloudFormation スタックのデプロイ | ✅ 合格。ダッシュボード 1、アラーム 2、SNS 無し | 2026-10-05T16:03:06Z → 16:03:43Z |
| P4-2 | ダッシュボードの全ウィジェット系列がデータポイントを返す（3 時間の窓） | ✅ 合格。9 系列中 9 系列が非ゼロ | 16:10:56Z、再確認 23:55:05Z |
| P4-3 | `StorageCapacityAlarm` INSUFFICIENT_DATA → OK | ✅ 合格 | 16:04:09Z |
| P4-4 | `ThroughputUtilizationAlarm` INSUFFICIENT_DATA → OK | ✅ 合格 | 16:04:26Z |
| P4-5 | `CapacityThresholdPercent=1` への更新 | ⛔ テンプレートが拒否（`MinValue` 50）。スタックは 80 のまま | 23:47:43Z |
| P4-6 | 下限の 50 へ更新し、`StorageCapacityAlarm` → ALARM | ⚠️ 到達不可。閾値 50.0 は反映されたが、利用率 3.45–3.51% でアラームは OK のまま（F1） | 23:47:54Z → 23:48:30Z、90 秒後に再読み取り |
| P4-7 | 80 に戻し、両アラームが OK | ✅ 合格 | 23:54:25Z → 23:55:02Z |
| P5-1 | `terraform init -lockfile=readonly` | ✅ 合格（`hashicorp/aws` v6.67.0） | 23:55:37Z |
| P5-2 | `terraform plan` | ✅ 合格。追加 8、変更 0、削除 0 | 23:55:37Z |
| P5-3 | `terraform apply` | ✅ 合格。8 件追加（ダッシュボード 1、アラーム 7） | 23:55:54Z → 23:55:57Z |
| P5-4 | 7 つのアラームすべてが INSUFFICIENT_DATA → OK | ✅ 合格（23:57:16Z までに全件 OK） | [アラームの状態遷移](#アラームの状態遷移)を参照 |
| P5-5 | ダッシュボードのウィジェットがデータを返す（3 時間の窓） | ✅ 合格。9 系列中 9 系列が非ゼロ | 2026-10-06T00:00:26Z |
| P5-6 | `capacity_threshold_percent=1` | ⛔ モジュールの検証が拒否（範囲 50–95）、`plan` は exit 1 | 00:00:34Z |
| P5-7 | 下限の閾値（容量 50、ボリューム容量 1、ボリューム inode 1）→ ALARM | ⚠️ 一部。ボリューム単位の 2 アラームは ALARM に到達。ファイルシステム容量アラームは閾値 50 を受け付け、3.45% で OK のまま（F1） | apply 00:00:42Z → 00:00:46Z |
| P5-8 | 既定値に戻し、全アラームが OK | ✅ 合格。00:03:56Z に 7 つすべて OK。続く `plan -detailed-exitcode` は exit 0、変更なし | apply 00:02:09Z → 00:02:17Z |
| P6 | 後片付けと約 65 秒後の再読み取り | ✅ 全リソースで合格 | 00:04:08Z → 00:06:10Z |

---

## アラームの状態遷移

`describe-alarm-history`（`StateUpdate`）より、UTC。アラーム名に含まれるボリューム ID はプレースホルダーに置き換えています。

| アラーム | 遷移 | 時刻 | 状態理由に含まれる値 |
|----------|------|------|----------------------|
| `fsxn-verify-monitoring-dashboard-capacity-high` | INSUFFICIENT_DATA → OK | 2026-10-05T16:04:09Z | 3.49, 3.49, 3.49, not > 80 |
| `fsxn-verify-monitoring-dashboard-throughput-high` | INSUFFICIENT_DATA → OK | 2026-10-05T16:04:26Z | 0.337, 0.273, 0.271, not > 80 |
| `fsxn-verify-tf-capacity-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:56:34Z | 3.45 ×3, not > 80 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-capacity-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:56:42Z | 20.23 ×3, not > 80 |
| `fsxn-verify-tf-cpu-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:56:51Z | 13.64, 12.81, 13.25, not > 80 |
| `fsxn-verify-tf-throughput-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:56:55Z | 0.280, 0.282, 0.315, not > 80 |
| `fsxn-verify-tf-disk-iops-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:57:03Z | 0.367, 0.318, 0.414, not > 80 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-inode-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:57:11Z | 34.908 ×3, not > 80 |
| `fsxn-verify-tf-disk-throughput-high` | INSUFFICIENT_DATA → OK | 2026-10-05T23:57:16Z | 0.367, 0.337, 0.393, not > 80 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-inode-high` | OK → ALARM | 2026-10-06T00:01:45Z | 34.908 ×3, > 1 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-capacity-high` | OK → ALARM | 2026-10-06T00:01:46Z | 20.23 ×3, > 1 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-inode-high` | ALARM → OK | 2026-10-06T00:03:20Z | 34.908 ×3, not > 80 |
| `fsxn-verify-tf-fsvol-0123456789abcdef0-capacity-high` | ALARM → OK | 2026-10-06T00:03:47Z | 20.23 ×3, not > 80 |

すべてのアラームが作成から約 1.5 分以内に OK に達しました。CloudWatch が既存のメトリクス履歴から 300 秒の期間 3 つを評価したためです。INSUFFICIENT_DATA のまま残ったアラームはありません。CloudFormation の容量アラームと Terraform のファイルシステム容量アラームには ALARM の記録がありません（F1）。

---

## ダッシュボードウィジェットのデータポイント

両方のダッシュボードが描画する 9 つのメトリクス系列について、直近 3 時間の `get-metric-data` の結果です。式の行（MB/s と IOPS への換算）はこれらの系列から計算されるため、別には数えていません。すべてのクエリで Status は `Complete` でした。

| ウィジェット | メトリクス（統計 / 期間） | ディメンション | CFN 13:10–16:10Z | CFN 20:55–23:55Z | TF 21:00–00:00Z | 観測した範囲 |
|--------------|--------------------------|----------------|:---:|:---:|:---:|--------------|
| Network Throughput (MB/s) | `DataReadBytes` Sum / 60 | `FileSystemId` | 180 | 180 | 180 | 0–3098 bytes/min |
| Network Throughput (MB/s) | `DataWriteBytes` Sum / 60 | `FileSystemId` | 180 | 180 | 180 | 0–1128 bytes/min |
| IOPS (Operations/s) | `DataReadOperations` Sum / 60 | `FileSystemId` | 180 | 180 | 180 | 0–6 /min |
| IOPS (Operations/s) | `DataWriteOperations` Sum / 60 | `FileSystemId` | 180 | 180 | 180 | 0–1 /min |
| Network Throughput Utilization (%) | `NetworkThroughputUtilization` Average / 60 | `FileSystemId` | 180 | 179 | 179 | 0.217–0.540% |
| Storage Capacity Utilization (%) | `StorageCapacityUtilization` Average / 300 | `FileSystemId`, `StorageTier=SSD`, `DataType=All` | 36 | 36 | 36 | 3.45–3.51% |
| Network Sent/Received (MB/s) | `NetworkSentBytes` Sum / 60 | `FileSystemId` | 180 | 179 | 179 | 1.95e7–4.86e7 bytes/min |
| Network Sent/Received (MB/s) | `NetworkReceivedBytes` Sum / 60 | `FileSystemId` | 180 | 179 | 179 | 1.53e7–3.81e7 bytes/min |
| Storage Used (GB) | `StorageUsed` Average / 300 | `FileSystemId` | 36 | 36 | 36 | 3.58e9–3.79e9 bytes |

180 ではなく 179 になっているのは、クエリ時点でまだ公開されていなかった直近 1 分の区間です。データポイントが 0 件の系列はありません。

> **負荷に関する補足**: ファイルシステムはアイドル状態で、負荷は生成していません。これらの件数は系列が存在して描画されることを示すもので、性能値ではありません。

---

## 所見

| # | 所見 | 種別 | この記録への影響 |
|---|------|------|------------------|
| F1 | 容量閾値の範囲が、利用率の低いファイルシステムでの ALARM 経路の確認を妨げる。`CapacityThresholdPercent`（CloudFormation、`MinValue` 50 / `MaxValue` 95）と `capacity_threshold_percent`（Terraform、同じ 50–95 の検証）はどちらも 1 を拒否した。下限の 50 では、利用率 3.45–3.51% は閾値を超えない | 検証上の制約で、コードの欠陥ではない | `StorageCapacityAlarm` と Terraform の `storage_capacity` について、OK → ALARM → OK は**この実行では未検証**。後に第 1 世代のファイルシステムで実データを書き込んで検証した: [2026-10-06 の容量アラームの実データによる実行](#2026-10-06-の容量アラームの実データによる実行)。検証済みなのは、ディメンションの組がデータを返し、アラームがそれを OK と評価すること。ALARM 経路は、閾値が 1–100 を受け付けるボリューム単位の 2 アラームで検証した。`set-alarm-state` は使っていない。これが確かめるのは通知の配線で、メトリクスの評価ではないため |
| F2 | 状態をまたがない閾値だけの変更（CloudFormation で 50）の後、CloudWatch は履歴を追加せず、アラームの `StateReason` は直前の遷移の文言（"threshold (80.0)"）のまま残る。`Threshold` フィールドは 50.0 を示す | CloudWatch の挙動で、コードの欠陥ではない | 50 での評価は ALARM にならなかったことからの推定で、直接は観測していない |

ダッシュボードテンプレートと Terraform モジュールの欠陥: この実行では見つかっていません。ダッシュボードの 9 系列と、アラームのメトリクスの組 9 つ（CloudFormation 2、Terraform 7）はすべて既存の系列に一致し、このファイルシステムでデータを返しました。

> **ダッシュボード表示に関する補足**
>
> 2026-10-07 に、デプロイしたダッシュボードのスクリーンショットを撮り、`aws cloudwatch get-dashboard` で本文を読み戻したところ、4 つのウィジェット（Network Throughput、IOPS、Network Sent/Received、Storage Used）が、式の入力である生のメトリクスを換算後の系列と同じ軸に描画していました。軸は 1 分あたりの生のバイト数や操作数、または生のバイト数を示し（Network Throughput の軸は MB/s のラベルのまま約 1.9G に達した）、換算後の MB/s、IOPS、GB の線は 0 付近にありました。2 つの利用率のウィジェット（Network Throughput Utilization、Storage Capacity Utilization）とすべてのアラームは影響を受けていません。上の `get-metric-data` の件数は、描画されたグラフではなく系列を読んだものなので、引き続き有効です。修正では、テンプレートとモジュールの両方で、生の入力 7 行に `visible: false` を設定しました。タグ `terraform-fsxn-monitoring-dashboard-v0.1.0` はこの修正より前のもので、修正はタグ `terraform-fsxn-monitoring-dashboard-v0.1.1` に含まれています。修正後のダッシュボードの画面は [2026-10-07 のダッシュボードとアラームの画面](#2026-10-07-のダッシュボードとアラームの画面) にあります。

---

## 未検証の範囲

| 項目 | 状態 | 理由 |
|------|------|------|
| `shared/templates/qtree-quota-monitor.yaml` | この実行では未実施 | 2026-10-05 の実行の範囲外で、Qtree のリソースはデプロイしていない。2026-10-06 に別途実行した。最初の実行は 1 回のポーリングに成功した後に停止し（[2026-10-06 の Qtree クォータ監視の実行](#2026-10-06-の-qtree-クォータ監視の実行)）、再実行は完了した（[2026-10-06 の Qtree クォータ監視の再実行](#2026-10-06-の-qtree-クォータ監視の再実行)） |
| ファイルシステム容量アラームの ALARM 経路（CloudFormation と Terraform） | この実行では未検証 | F1。後に第 1 世代のファイルシステムで検証済み: [2026-10-06 の容量アラームの実データによる実行](#2026-10-06-の容量アラームの実データによる実行) |
| 第 2 世代のファイルシステム（`file_server_names`、`FileServer` と `Aggregate` のディメンション） | 未実施 | 検証対象は第 1 世代 |
| HA ペアが 2 つ以上のファイルシステム | 未実施 | 検証対象の HA ペアは 1 つ |
| SNS 通知の配信 | 未実施 | `NotificationEmail` / `notification_email` を指定しなかったため、トピック・購読・アラーム/OK アクションが存在しなかった |
| レイテンシウィジェット | 対象外 | ダッシュボードにレイテンシウィジェットが実装されていない |
| 負荷時の挙動 | 未実施 | アイドル状態のファイルシステムで、負荷を生成していない |

---

## 後片付け

| 手順 | 結果 | 時刻（UTC） |
|------|------|-------------|
| 削除前の `terraform plan -detailed-exitcode` | exit 0、変更なし | 2026-10-06T00:04Z |
| `terraform destroy -auto-approve` | exit 0、8 件削除。`terraform state list` は空 | 00:04:08Z → 00:04:13Z |
| `aws cloudformation delete-stack` + `wait stack-delete-complete` | どちらも exit 0。`list-stacks` は DELETE_COMPLETE | 00:04:20Z → 00:04:51Z |
| 全リソースの再読み取り | スタックは存在しない。ダッシュボード 2 つはどちらも `ResourceNotFound`。どちらの接頭辞でもダッシュボード 0 件、アラーム 0 件（メトリクスと複合の両方）。9 つのアラーム名はそれぞれ 0 件。SNS トピック 0 件 | 00:06:10Z（スタック削除完了の約 65 秒後） |
| ローカルの Terraform 作業ディレクトリ（state、plan ファイル、tfvars） | 削除済み | 再読み取りの後 |

カスタムメトリクスは出力していません。2 つの成果物のメトリクス参照はすべて名前空間 `AWS/FSx` で、どちらも Lambda 関数も `PutMetricData` も含まず、この実行が作成したのは CloudWatch のアラームとダッシュボードだけです。VPC エンドポイント・セキュリティグループ・IAM・ボリューム・Lambda のリソースは作成も変更もしていません。CloudFormation スタックは SSO の中断を含めて約 8 時間、Terraform のリソースは約 8 分存在しました。

---

## 総合判定

| 項目 | 値 |
|------|-----|
| 判定 | ✅ 第 1 世代・HA ペア 1 つのファイルシステムで、両成果物のデプロイ・系列の指定・OK の評価を検証済み。ボリューム単位の ALARM 経路を検証済み（Terraform）。ファイルシステム容量の ALARM 経路はこの実行では未検証（F1）で、後の[実データによる実行](#2026-10-06-の容量アラームの実データによる実行)で検証済み |
| 合格 | 16 件中 12 件（P4-1–P4-4、P4-7、P5-1–P5-5、P5-8、P6） |
| 一部合格・到達不可 | 16 件中 2 件（P4-6、P5-7）。どちらも F1 による |
| 設計どおりの拒否 | 16 件中 2 件（P4-5、P5-6）。範囲外の閾値の要求を、テンプレートとモジュールが拒否した |
| 見つかった欠陥 | この実行では無し。2026-10-07 にダッシュボードの表示の欠陥が見つかった。[所見](#所見) を参照 |

---

## 2026-10-06 の Qtree クォータ監視の実行

2026-10-06（UTC）に、`shared/templates/qtree-quota-monitor.yaml` を、HA ペア 1 つの第 1 世代 `SINGLE_AZ_1` の FSx for ONTAP ファイルシステムに対してデプロイしました。実行は 02:16:26Z に停止しました。ONTAP がポーラーに HTTP 401 を返し始めたためで、ONTAP からの 401 または 403 はこの実行の停止条件の 1 つでした。停止の前に、スケジュールによるポーリングが 1 回成功しています。このポーリングは `FSxONTAP/Qtree` にデータポイントを 5 つ公開し、どの値も ONTAP のクォータレポートと一致しました。`QtreeQuotaAlarm` はそのデータポイントで INSUFFICIENT_DATA を抜けて OK に達しています。`QtreeQuotaAlarm` を ALARM に遷移させるための閾値の変更は、予定していましたが実行していません。その後に続いた失敗したポーリングにより、DLQ の経路は端から端まで確認できました。テンプレート単位の問題が 2 つ見つかっています（401/403 に対するバックオフが無いこと、スタック削除の順序によりロググループが取り残されること）。後述の所見（QF2、QF3）を参照してください。

| 項目 | 値 |
|------|-----|
| 検証日時 | 2026-10-06T01:56Z から 03:03Z（UTC） |
| 検証環境 | テスト環境（`ap-northeast-1`）。SVM 1 つ、テスト用ボリューム 1 つ、tree クォータを設定した Qtree 1 つでのサンプル実行 |
| 範囲 | スタックのデプロイ、Qtree 単位と SVM 単位のメトリクスの公開、アラームの初期評価、後片付け。Qtree の準備とクォータレポートの読み取りのため、踏み台ホストから ONTAP REST API を呼んだ |
| 結果 | 停止。予定した 2 回の成功ポーリングのうち 1 回を完了した後、ONTAP が HTTP 401 を返した。`QtreeQuotaAlarm` の ALARM 経路は未実施 |

以下の値は、1 つの Qtree に対する 1 回のポーリングから得たものです。示すのは、テンプレートが記載するディメンションの組で系列が公開されること、アラームが SVM 単位の系列を評価することです。複数サイクルにわたる挙動、規模を大きくしたときの挙動、第 2 世代や HA ペアが 2 つ以上のファイルシステムでの挙動は示しません。

### 環境とデプロイした構成（Qtree の実行）

| 項目 | 値 |
|------|-----|
| AWS リージョン | `ap-northeast-1` |
| ファイルシステム | `fs-0123456789abcdef0`（プレースホルダー）、`SINGLE_AZ_1`（第 1 世代）、HA ペア 1 つ、128 MBps |
| ONTAP のバージョン | NetApp Release 9.18.1P6（`GET /api/cluster`） |
| SVM | `<svm-name>`（プレースホルダー）、NFS 有効 |
| テンプレートのリビジョン | main の `54e4c5d`（#104）の `qtree-quota-monitor.yaml`。作業ツリーと同一 |
| テスト用ボリューム | `zz_mon_verify_qtree`、1024 MiB、UNIX セキュリティスタイル、スナップショットポリシー none、階層化ポリシー NONE。この実行のために Amazon FSx for NetApp ONTAP の管理 API（`aws fsx create-volume`）で作成し、終了後に削除 |
| Qtree とクォータ | Qtree `qt_mon_verify` に tree クォータルールを設定。ハードリミット 104857600 バイト（100 MiB）。ボリュームのクォータを有効化 |
| 書き込んだデータ | 踏み台ホストから一時的に NFSv3 でマウントして 60 MiB を書き込み、アンマウント。書き込み後の ONTAP のクォータレポート: 使用量 63168512 バイト、ハードリミット 104857600、`hard_limit_percent` 60。ポリシーを変更せずにマウントできたのは、ボリュームに SVM の `default` エクスポートポリシーが割り当てられ、そのルールがクライアント `0.0.0.0/0` に読み書きとスーパーユーザーのアクセスを許可していたため（テスト環境の設定。下のセキュリティに関する補足を参照） |
| Lambda の配置 | ファイルシステムのサブネット。そのルートテーブルは `0.0.0.0/0` をインターネットゲートウェイに送り、NAT ゲートウェイは無い |
| CloudWatch への経路 | この実行のために Lambda のサブネットに作成した `com.amazonaws.ap-northeast-1.monitoring` interface エンドポイント（プライベート DNS 有効） |
| Secrets Manager への経路 | VPC に既存の interface エンドポイントがあるため `CreateSecretsManagerEndpoint=false` |
| セキュリティグループ | Lambda と新しいエンドポイントの両方に、ファイルシステムの既存のセキュリティグループを使用。インバウンドはすべてのプロトコルを `0.0.0.0/0` から許可し、エグレスはすべての通信を許可している（テスト環境の設定。下のセキュリティに関する補足を参照）。そのため 443 は既に許可されており、セキュリティグループと IAM は変更していない |
| ONTAP の認証情報 | Secrets Manager に保存した `fsxadmin` の認証情報 |
| 認証 | AWS IAM Identity Center（SSO）のセッション |

> **セキュリティに関する補足**: 上のセキュリティグループとエクスポートポリシーは、テスト環境に既存の設定であり、推奨する設定ではありません。どちらも許可範囲が広かったため、この実行が示すのは、ネットワークと NFS の経路が開いている状態でモニターが動くことです。最小権限の構成を検証したものではありません。最小権限の構成では、Lambda のセキュリティグループから管理インターフェイスと 2 つの interface エンドポイントへの 443 だけを許可し、エクスポートポリシーはマウントが必要なクライアントに限ります。その構成はこの実行では試していません。

`shared/scripts/preflight-check.sh --profile automated-response` は exit 0 で、既存の Secrets Manager エンドポイントについて警告を 1 件出しました。このスクリプトにはこのテンプレート用のプロファイルが無く、`monitoring` エンドポイントの有無も確認しません（QF4）。

```bash
aws cloudformation create-stack \
  --stack-name fsxn-verify-qtree-quota \
  --template-body file://shared/templates/qtree-quota-monitor.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=OntapMgmtIp,ParameterValue=<management-ip> \
    ParameterKey=OntapCredentialsSecretArn,ParameterValue=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:<secret-name>-XXXXXX \
    ParameterKey=SvmName,ParameterValue=<svm-name> \
    ParameterKey=VpcId,ParameterValue=vpc-0123456789abcdef0 \
    ParameterKey=SubnetIds,ParameterValue=subnet-0123456789abcdef0 \
    ParameterKey=SecurityGroupId,ParameterValue=sg-0123456789abcdef0 \
    ParameterKey=PollIntervalMinutes,ParameterValue=5 \
    ParameterKey=QuotaThresholdPercent,ParameterValue=85 \
    ParameterKey=NotificationEmail,ParameterValue='' \
    ParameterKey=CreateSecretsManagerEndpoint,ParameterValue=false \
    ParameterKey=CaCertPath,ParameterValue='' \
    ParameterKey=CaCertLayerArn,ParameterValue='' \
  --region ap-northeast-1
```

スタックが作成したのは、IAM ロール、Lambda 関数、そのロググループ（保持期間 30 日）、DLQ、EventBridge のスケジュールルール、2 つのアラーム `fsxn-verify-qtree-quota-quota-high`（`QtreeQuotaAlarm`）と `fsxn-verify-qtree-quota-dlq-depth` です。`NotificationEmail` は空のため、SNS トピックは作成されていません。

### 確認項目の結果（Qtree の実行）

| # | 確認項目 | 結果 | 時刻（UTC） |
|---|----------|------|-------------|
| Q0 | 事前確認: エンドポイント、ルート、セキュリティグループ、ONTAP の読み取り専用の確認（クラスター、SVM、クォータレポートとルール、ボリューム） | ✅ 合格。SVM に tree クォータが無かったため Q1 が必要だった | 01:56:09Z → 01:59:22Z |
| Q1 | Qtree の準備: ボリューム、Qtree、100 MiB の tree クォータルール、クォータの有効化、60 MiB の書き込み | ✅ 合格 | 02:00:04Z → 02:02:28Z |
| Q2-1 | `monitoring` interface エンドポイント | ✅ 合格。available | 02:03:11Z → 02:04:26Z |
| Q2-2 | スタックの作成 | ✅ 合格。CREATE_COMPLETE | 02:04:00Z → 02:06:48Z |
| Q2-3 | ポーリング 1 回目: 系列が公開され、値が ONTAP のレポートと一致する | ✅ 合格。データポイント 5 つがすべて一致 | 02:11:22Z |
| Q2-4 | ポーリング 2 回目（必要な 2 回目の成功サイクル） | ❌ 不合格。ONTAP が HTTP 401（停止条件） | 02:16:22Z |
| Q3 | `QuotaThresholdPercent` を 85 → 50 にして `QtreeQuotaAlarm` を 60.24% で ALARM に遷移させ、元に戻す | ⏹️ 未実施（その前に停止） | — |
| Q4 | 後片付けと再読み取り | ✅ AWS のリソースはすべて合格。ONTAP 側の再読み取りは不可（401） | 02:24:36Z → 03:03:54Z |

### 観測したメトリクスとディメンションの組

ポーリング 1 回目（02:11:22Z）はコールドスタートでした。関数は `Found 2 qtree quota reports for SVM <svm-name> in 1 page(s)` と `Published 5 metric data points` をログに出し、実行時間は 629.89 ms、最大メモリは 95 MB でした。初期化時には、TLS 証明書の検証が無効（`cert_reqs=CERT_NONE`）で PoC 用途に限るという警告をログに出しています。`CaCertPath` が空だったためです。

その後、`FSxONTAP/Qtree` に対する `list-metrics` は次のディメンションの組を返しました。

| メトリクス | ディメンション |
|------------|----------------|
| `QtreeQuotaUsedPercent`、`QtreeQuotaUsedBytes`、`QtreeQuotaLimitBytes` | `SvmName`、`VolumeName=zz_mon_verify_qtree`、`QtreeName=qt_mon_verify` |
| `QtreeQuotaUsedPercentMax`、`QtreeQuotaReportTruncated` | `SvmName` のみ |

ボリュームのデフォルト Qtree（名前が空で、ハードリミットが無い）については系列を公開していません。ONTAP はこれも tree クォータレポートのレコードとして返します。

| メトリクス | 公開した値（各 1 データポイント） | ONTAP のクォータレポート（02:02:28Z） | 一致 |
|------------|----------------------------------|---------------------------------------|------|
| `QtreeQuotaUsedBytes` | 63168512 Bytes | 使用量 63168512 | 一致 |
| `QtreeQuotaLimitBytes` | 104857600 Bytes | ハードリミット 104857600 | 一致 |
| `QtreeQuotaUsedPercent` | 60.2421875 Percent | 63168512 / 104857600 × 100 = 60.2421875（ONTAP の `hard_limit_percent` は 60） | 一致 |
| `QtreeQuotaUsedPercentMax` | 60.2421875 Percent | Qtree 1 つでの最大値 | 一致 |
| `QtreeQuotaReportTruncated` | 0 Count | 1 ページ、次のリンク無し | 一致 |

> **ページングに関する補足**: これは 1 ページの場合です。レコード 2 件が 1 ページに収まり、次のリンクが無かったため、`QtreeQuotaReportTruncated` は 0 でした。次のリンクをたどる動作、50 ページの上限、打ち切りの値 1 は確認していません。

### アラームの状態遷移（Qtree スタック）

`describe-alarm-history`（`StateUpdate`）と `describe-alarms` より、UTC。

| アラーム | 遷移 | 時刻 | 状態理由に含まれる値 |
|----------|------|------|----------------------|
| `fsxn-verify-qtree-quota-dlq-depth` | INSUFFICIENT_DATA → OK | 02:05:51Z | 初回の評価 |
| `fsxn-verify-qtree-quota-quota-high` | INSUFFICIENT_DATA → OK | 02:12:50Z | データポイント 1 つ、60.2421875、not > 85 |
| `fsxn-verify-qtree-quota-dlq-depth` | OK → ALARM | 02:22:51Z | データポイント 1 つ、1.0、> 0 |

`QtreeQuotaAlarm` は、`EvaluationPeriods` が 2 であるにもかかわらず、データポイント 1 つで OK に達しました（QF8）。Q3 を実行していないため、ALARM の記録はありません。

DLQ の経路は、停止の副次的な結果として端から端まで確認できました。02:16:22Z に失敗した呼び出しは非同期で 2 回リトライされ（02:17:19Z、02:19:12Z）、02:19:16Z にエラーの文言を属性に持つメッセージが DLQ に届き、02:22:51Z に DLQ 深度アラームが ALARM に遷移しました。`NotificationEmail` が空だったため、SNS のアクションはありません。

### ONTAP の HTTP 401 によるポーラーの停止

| 時刻（UTC） | 呼び出し | 結果 |
|-------------|----------|------|
| 02:11:22Z | サイクル 1、コールドスタート | 成功（ONTAP の呼び出しは 0.63 秒） |
| 02:16:22Z | サイクル 2。サイクル 1 と同じウォームコンテナ、同じキャッシュ済みの認証情報 | HTTP 401 |
| 02:17:19Z、02:19:12Z | サイクル 2 の非同期リトライ | HTTP 401。02:19:16Z に DLQ メッセージ |
| 02:21:22Z | サイクル 3、ウォームコンテナ | HTTP 401 |
| 02:22:24Z | シークレットを読み直した新しいコンテナでのリトライ | HTTP 401 |
| 02:24:42Z | リトライ | HTTP 401 |

401 の応答はそれぞれ約 4.1–4.5 秒かかりました。ログに出るのはリクエストのパスとステータスだけで、パスワードやシークレットの値はログに出ていません。

停止後は ONTAP へのログインを試みず、読み取りだけで調べた結果は次のとおりです。

- シークレットの値が最後に変更されたのは実行の前（01:48:18Z）で、実行中には変更されていない。
- FSx for ONTAP の管理 API（`UpdateFileSystem`）による最後の `fsxadmin` のパスワードリセットは 01:41:33Z に要求され、完了している。CloudTrail には 01:00Z から 03:00Z の間に他の `UpdateFileSystem` の呼び出しが無い。
- つまり、踏み台ホスト（01:58Z から 02:02Z）とポーラー（02:11:23Z）で通った認証情報が 02:16:26Z から拒否された。その間に FSx for ONTAP の管理 API によるリセットもシークレットの変更も無い。
- この実行のプリンシパルのほかに、もう 1 つのプリンシパルが 02:22:30Z に同じシークレットを読んでいる。それが何に接続するのかは特定していない。

401 の原因は特定できていません。仮説が 2 つ残っており、どちらも ONTAP 側からは確認していません。

- 別のクライアントによるアカウントのロック。同じ VPC にある別の関数が、02:02Z ごろ、02:06Z ごろに 2 回、02:12Z ごろに呼び出されていた。いずれも 01:41Z のパスワードリセットの後、02:16:22Z の最初の 401 の前である。02:06Z の呼び出しはそれぞれ約 4 秒かかっており、ポーラーの 401 応答と同じ所要時間だった。この関数がリセット前のパスワードで認証を試み、ログイン失敗の繰り返しで `fsxadmin` がロックされたという見方と整合する。
- FSx for ONTAP の管理 API を経由しない、ONTAP 内でのパスワード変更。

どちらかを確かめるには、ONTAP 側の読み取り（ログインとロックの状態、または EMS イベント）か、FSx for ONTAP の管理 API による新たなパスワードリセットが必要です。この実行ではどちらも行っていません。同じ日の後刻の再実行では、その別の関数をスロットリングしてからパスワードをリセットし、401 なしで 4 回のポーリングを完了しました。[再実行前の認証情報の扱い](#再実行前の認証情報の扱い)を参照してください。

> **認証情報の共有に関する補足**: `fsxadmin` のパスワードを保存しているクライアントは、パスワードのリセットより前に、またはリセットと同時に更新する必要があります。古いパスワードを持つクライアントがログインに失敗し続け、ONTAP がアカウントをロックすると、このポーラーを含め、そのアカウントを共有するすべてのクライアントが失敗します。ポーラーが送るのは `/api/storage/quota/reports` への `GET` リクエストだけなので（確信度: `コード確認済み`）、読み取り専用ロールを持つ専用の ONTAP アカウントを使えば、`fsxadmin` を他のクライアントと共有せずに済みます。読み取り専用アカウントはこの実行では試していません。

### 所見（Qtree の実行）

| # | 所見 | 種別 | この記録への影響 |
|---|------|------|------------------|
| QF1 | `fsxadmin` の認証情報が、通ってから約 5 分後の 02:16:26Z から拒否された（HTTP 401）。その間に FSx for ONTAP の管理 API によるリセットもシークレットの変更も無い | 環境。原因は未特定 | 2 回目のポーリングサイクルと Q3 を妨げた。再実行の前に、ONTAP へのアクセスを回復し、原因を特定する必要がある |
| QF2 | ポーラーには 401/403 に対するバックオフが無い。5 分間隔のスケジュールと、呼び出しごとの 2 回の非同期リトライにより、約 8 分間（02:16:26Z から 02:24:46Z）に失敗する Basic 認証のリクエストを 6 回送った。ONTAP のロックアウトポリシーの下では、アカウントのロックが続く | テンプレートの設計上のトレードオフ | 401 で例外を送出しなければリトライは避けられるが、失敗が DLQ にも入らなくなる。この記録では修正を提案しない |
| QF3 | スタックの削除で、ロググループ（02:24:40Z）が関数（02:25:18Z）より先に削除され、ロールは 02:25:33Z まで `logs:CreateLogGroup` を許可していた。実行中のリトライが 02:24:51Z に保持期間なしでロググループを作り直した | テンプレートの欠陥（削除の順序） | スタックを削除した利用者の手元に、期限切れにならないロググループが取り残されうる。考えられる修正（未検証）: 関数に `DependsOn` を付けて先に削除させる、またはロールから `logs:CreateLogGroup` を外す |
| QF4 | `preflight-check.sh` にはこのテンプレート用のプロファイルが無く、`com.amazonaws.<region>.monitoring` の有無を確認しない。テンプレートもこのエンドポイントを作成しない | ツールとドキュメントの不足 | 今回のように NAT の無いサブネットでは、Lambda が `PutMetricData` を呼べるよう、エンドポイントを手作業で作成する必要があった |
| QF5 | `OntapMgmtIp` は IPv4 のリテラルだけを受け付け、管理用の DNS 名は `AllowedPattern` で拒否される | パラメータの制約 | 管理 IP が 1 つの今回の環境では問題なかった。IP が変わりうる場合に DNS 名のほうが安全な入力かどうかは評価していない |
| QF6 | ログの `Found 2 qtree quota reports` と戻り値のフィールド `qtrees_monitored`（2）は、後で読み飛ばすデフォルト Qtree のレコードを数えている。公開したのは Qtree 1 つ | 件数の表現 | この件数はレポートのレコード数で、監視している Qtree の数ではない |
| QF7 | 公開する割合（60.2421875）はバイト数から計算している。ONTAP の `hard_limit_percent` は 60 に丸める | 挙動に関する補足 | アラームの閾値は丸める前の値と比較される |
| QF8 | `QtreeQuotaAlarm` は `EvaluationPeriods` 2 で、データポイント 1 つで INSUFFICIENT_DATA から OK に遷移した | CloudWatch の挙動。1 回観測 | CloudWatch が部分的なデータを評価したことと整合する。再現は未確認 |

### 後片付け（Qtree の実行）

| 手順 | 結果 | 時刻（UTC） |
|------|------|-------------|
| `aws cloudformation delete-stack` | DELETE_COMPLETE | 02:24:37Z → 02:26:35Z |
| `monitoring` interface エンドポイントの削除 | 受理（失敗した項目は無し） | 02:25:02Z |
| ONTAP REST API: クォータルールの削除、クォータの無効化、Qtree の削除 | 試行していない。ONTAP は 401 を返しており、ログインの失敗を重ねるとロックが延びるおそれがあった | — |
| `SkipFinalBackup=true` での `aws fsx delete-volume` | ボリュームは見つからない状態として読み戻された | 02:56:00Z → 02:57:18Z |
| リトライが作り直したロググループの削除（QF3） | 削除済み。唯一のストリーム（02:24:42Z のリトライ）は先に保存した | 02:59:35Z |
| AWS の全リソースの再読み取り | スタックは存在しない（スタック ID では DELETE_COMPLETE）。エンドポイント、ボリューム、アラーム、関数、IAM ロール、DLQ、スケジュールルール、Lambda のネットワークインターフェイス、ロググループはいずれも何も返さない。踏み台ホストにマウントは残っていない | 02:58:59Z → 03:03:54Z |

未解決の項目が 2 つあります。

- ボリュームの削除時に、ONTAP が SVM のクォータポリシーから tree クォータルールを外したかどうかは未確認です。ONTAP へのアクセスが回復したら、`GET /api/storage/quota/rules?svm.name=<svm-name>` で確認し直してください。この項目は再実行で解消しました。05:29:25Z にこの呼び出しが返したのは再実行で作成した直後のルール 2 件だけで、この実行のルールは SVM に残っていませんでした。
- `FSxONTAP/Qtree` のカスタムメトリクス 5 系列は削除できず、02:59:04Z の時点でも一覧に出ていました。CloudWatch は、新しいデータが約 2 週間無いメトリクスを一覧に出さなくなり、そのデータを 15 か月保持します（[CloudWatch の概念](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html)、[re:Post](https://repost.aws/knowledge-center/cloudwatch-delete-metric)）。

### 未検証の範囲（Qtree の実行）

| 項目 | 状態 | 理由 |
|------|------|------|
| 2 回目以降の成功したポーリングサイクルと、サイクル間の一貫性 | 未検証 | 成功したのはサイクル 1 だけ（QF1） |
| `QtreeQuotaAlarm` の ALARM への遷移と OK への復帰 | 未実施 | Q3 を実行していない |
| 使用率 0% のときの挙動 | 未実施 | 最初のポーリングの前にデータを書き込んだ |
| 401 の原因と、クォータルールと Qtree の ONTAP 側での再読み取り | 未検証 | 実行中に ONTAP へのアクセスが回復しなかった |
| 第 2 世代のファイルシステムと、HA ペアが 2 つ以上の構成 | 未実施 | 検証対象は第 1 世代で HA ペアは 1 つ |
| CA 証明書を使った TLS（`CaCertPath`、`CaCertLayerArn`） | 未実施 | 実行したのは `CERT_NONE` だけ |
| SNS 通知の配信 | 未実施 | `NotificationEmail` が空で、トピックを作成していない |
| 1 ページを超えるページング、打ち切りの経路（`QtreeQuotaReportTruncated=1`）、Qtree が 200 を超える SVM | 未実施 | SVM が返したのは 1 ページに収まる 2 レコード |
| ポーラー専用の読み取り専用 ONTAP アカウント | 未試験 | この実行は `fsxadmin` を使った |
| 最小権限のセキュリティグループとエクスポートポリシー | 未試験 | この実行は、`0.0.0.0/0` に開いたセキュリティグループと、`0.0.0.0/0` に読み書きとスーパーユーザーのアクセスを許可する `default` エクスポートポリシーを流用した |
| QF2 と QF3 の修正 | 未試験 | 提案のみ |

### 判定（Qtree の実行）

| 項目 | 値 |
|------|-----|
| 判定 | ⚠️ 一部。第 1 世代・HA ペア 1 つのファイルシステムで、Qtree 単位と SVM 単位のメトリクスの公開を 1 回のポーリングサイクルについて検証し、値は ONTAP のクォータレポートと一致した。`QtreeQuotaAlarm` が実データで OK と評価することを観測した。その ALARM 経路は未検証。DLQ の経路は端から端まで検証済み。実行は ONTAP の HTTP 401 で停止し、原因は未特定 |
| 合格 | 8 件中 6 件（Q0、Q1、Q2-1、Q2-2、Q2-3、Q4） |
| 不合格 | 8 件中 1 件（Q2-4、2 回目のポーリングサイクル） |
| 未実施 | 8 件中 1 件（Q3） |
| テンプレート単位の問題 | 2 件（QF2 は設計上のトレードオフ、QF3 は削除の順序） |

---

## 2026-10-06 の Qtree クォータ監視の再実行

2026-10-06（UTC）の後刻に、同じテンプレートを同じファイルシステムへもう一度デプロイしました。再実行の前に、最初の実行のロック仮説で挙げた別の関数をスロットリングし、その後で `fsxadmin` のパスワードをリセットしました。再実行は予定したすべての段階を完了しました。ONTAP はどの呼び出しにも 401 や 403 を返していません。スケジュールによるポーリングが 4 回続けて成功し、毎回データポイントを 5 つ公開し、値は ONTAP のクォータレポートと一致しました。`QtreeQuotaAlarm` は INSUFFICIENT_DATA を抜けて OK に達し、閾値を 50 に下げると ALARM に遷移し、85 に戻すと OK に復帰しました。後片付けでは、AWS 側と ONTAP 側のすべてのリソースを削除しました。

| 項目 | 値 |
|------|-----|
| 検証日時 | 2026-10-06T05:25Z から 06:02Z（UTC） |
| 検証環境 | テスト環境（`ap-northeast-1`）。SVM 1 つ、テスト用ボリューム 1 つ、tree クォータを設定した Qtree 1 つでのサンプル実行 |
| 範囲 | スタックのデプロイ、4 回のポーリングサイクル、Qtree 単位と SVM 単位のメトリクスの公開、`QtreeQuotaAlarm` の OK → ALARM → OK の経路、AWS と ONTAP での再読み取りを伴う後片付け |
| 結果 | 合格。確認項目 12 件中 12 件が合格 |

以下の値は、第 1 世代・HA ペア 1 つのファイルシステム上の 1 つの Qtree に対する 4 回のポーリングから得たものです。規模を大きくしたときの挙動、クォータレコードが 1 ページを超えるときの挙動、第 2 世代や HA ペアが 2 つ以上のファイルシステムでの挙動は示しません。

### 環境とデプロイした構成（再実行）

ファイルシステム、ONTAP のバージョン（9.18.1P6）、SVM、サブネット、セキュリティグループ、Secrets Manager のエンドポイント、スタック名、パラメータは[最初の実行](#2026-10-06-の-qtree-クォータ監視の実行)と同じです。`QuotaThresholdPercent=85`、`PollIntervalMinutes=5` で、`NotificationEmail`、`CaCertPath`、`CaCertLayerArn` は空です。最初の実行のセキュリティに関する補足に書いた、許可範囲の広いセキュリティグループと `default` エクスポートポリシーは、変更せずにそのまま使いました。異なる点は次のとおりです。

| 項目 | 値 |
|------|-----|
| テンプレートのリビジョン | main の `76f643f`（#110）の `qtree-quota-monitor.yaml`。このファイルは最初の実行のリビジョン `54e4c5d` から変わっていない |
| ONTAP の認証情報 | `fsxadmin`。FSx for ONTAP の管理 API によるパスワードリセット（05:18:26Z の `UpdateFileSystem`）の後のもの。シークレットは 05:20:41Z に更新 |
| テスト用ボリューム | `zz_mon_verify_qtree` を最初の実行と同じ設定で作り直した（05:27:31Z に `aws fsx create-volume`、05:28:31Z に `CREATED`） |
| 書き込んだデータ | 踏み台ホストから一時的に NFSv4.1 でマウントして 60 MiB を書き込み、アンマウントしてマウントポイントを削除。書き込み後の ONTAP のクォータレポート: 使用量 63168512 バイト、ハードリミット 104857600 |
| CloudWatch への経路 | 再実行のために新たに作成した `com.amazonaws.ap-northeast-1.monitoring` interface エンドポイント（プライベート DNS 有効）。作成前の VPC にはこのエンドポイントが無かった |

### 再実行前の認証情報の扱い

最初の実行で最有力だった仮説は、同じ VPC にある別の関数が以前の `fsxadmin` のパスワードを使い続け、それによってロックが起きたというものです。再実行の前に次のことを行いました。

- その関数の予約済み同時実行数を 0 に設定した。最後の呼び出しは 04:59Z ごろで、それ以降はトリガーがスロットリングされている（10 分ごとにスロットリング 3 回、再実行の終わりまで呼び出し 0 回、エラー 0 件）。設定は 0 のまま残し、06:01Z ごろに読み直して 0 だった。
- その後、05:18:26Z に FSx for ONTAP の管理 API でパスワードをリセットし、05:20:41Z にシークレットを更新した。
- 最初の ONTAP の呼び出し（05:25:31Z）から後片付けの最後の読み取り（06:01:49Z）まで、踏み台ホストとポーラーのどの呼び出しも 401 や 403 を受けていない。

したがって、最初の実行の 401 の原因は、その別のクライアントが以前のパスワードでログインし続けていたことである可能性が高いと考えます。リセットの前にそのクライアントをスロットリングしたところ、その後のポーリング 4 回で 401 は起きませんでした（観測）。これはロック仮説と整合しますが、ONTAP 側でのロックを証明するものではありません。どちらの実行でも ONTAP のログインとロックの状態や EMS イベントを読んでおらず、ONTAP 内でのパスワード変更の可能性も ONTAP 側からは排除していません。

> **パスワードリセットに関する補足**: 共有している `fsxadmin` のパスワードをリセットする前に、それを保存しているクライアントをすべて洗い出し、先にそれぞれを止めるか更新してください。この環境では、既知のクライアント 1 つをリセットの前にスロットリングするだけで再実行には足りました。`fsxadmin` の共有を避けられる、ポーラー専用の読み取り専用 ONTAP アカウントは、今回も試していません。

### 確認項目の結果（再実行）

| # | 確認項目 | 結果 | 時刻（UTC） |
|---|----------|------|-------------|
| R0-1 | ONTAP の読み取り専用の事前確認: 踏み台ホストからの `GET /api/cluster` | ✅ 合格。HTTP 200、9.18.1P6 | 05:25:31Z |
| R0-2 | `preflight-check.sh --profile automated-response` と、既存の `monitoring` エンドポイントの確認 | ✅ 合格。exit 0。既存の Secrets Manager エンドポイントがあるため `CreateSecretsManagerEndpoint=false`。`monitoring` エンドポイントは無かった | 05:26Z ごろ |
| R0-3 | `fsxadmin` のパスワードを保存している別のクライアントがスロットリングされたままであること | ✅ 合格。実行中の呼び出し 0 回、終了時の予約済み同時実行数 0 | 05:20Z → 06:01Z |
| R1 | Qtree の準備: ボリューム、Qtree、100 MiB の tree クォータルール、クォータの有効化、60 MiB の書き込み | ✅ 合格。ONTAP はデフォルトの tree ルールも作成した（RF1） | 05:27:31Z → 05:31Z |
| R2-1 | `monitoring` interface エンドポイント | ✅ 合格。available | 05:31:02Z → 05:32:03Z |
| R2-2 | スタックの作成 | ✅ 合格。CREATE_COMPLETE | 05:32:11Z → 05:35:19Z |
| R2-3 | ポーリングサイクル 1–4: エラー無し、毎回データポイント 5 つ、値が ONTAP のレポートと一致 | ✅ 合格。4 回中 4 回 | 05:39:27Z、05:44:27Z、05:49:27Z、05:54:27Z |
| R2-4 | Lambda の `Errors` と `Throttles`、DLQ の深さ | ✅ 合格。0、0、メッセージ 0 件 | 05:37Z → 05:54Z |
| R3-1 | `QtreeQuotaAlarm` が INSUFFICIENT_DATA → OK | ✅ 合格 | 05:40:27Z |
| R3-2 | `QuotaThresholdPercent` を 85 → 50（`update-stack`）。`QtreeQuotaAlarm` が 60.24% で OK → ALARM | ✅ 合格 | 更新 05:51:52Z → 05:52:24Z、ALARM 05:52:31Z |
| R3-3 | `QuotaThresholdPercent` を 50 → 85。`QtreeQuotaAlarm` が ALARM → OK | ✅ 合格 | 更新 05:52:52Z → 05:53:24Z、OK 05:54:45Z |
| R4 | 後片付けと、AWS と ONTAP での再読み取り | ✅ すべてのリソースで合格 | 05:55:13Z → 06:01:49Z |

### 観測したメトリクス（再実行）

各ポーリングは `Found 2 qtree quota reports for SVM <svm-name> in 1 page(s)` と `Published 5 metric data points` をログに出しました。ポーリング 1 回目はコールドスタート（618 ms）で、2–4 回目は 322–334 ms でした。コールドスタート時に、`cert_reqs=CERT_NONE` が PoC 用途に限るという警告を 1 回、urllib3 の `InsecureRequestWarning` とともにログに出しています。

| メトリクス | ディメンション | データポイント数 | 各ポーリングの値 | ONTAP のクォータレポート | 一致 |
|------------|----------------|:---:|------------------|--------------------------|------|
| `QtreeQuotaUsedPercent` | `SvmName`、`VolumeName`、`QtreeName` | 4 | 60.2421875 | 63168512 / 104857600 × 100 | 一致 |
| `QtreeQuotaUsedBytes` | `SvmName`、`VolumeName`、`QtreeName` | 4 | 63168512 | 使用量 63168512 | 一致 |
| `QtreeQuotaLimitBytes` | `SvmName`、`VolumeName`、`QtreeName` | 4 | 104857600 | ハードリミット 104857600 | 一致 |
| `QtreeQuotaUsedPercentMax` | `SvmName` | 4 | 60.2421875 | Qtree 1 つでの最大値 | 一致 |
| `QtreeQuotaReportTruncated` | `SvmName` | 4 | 0 | 1 ページ、次のリンク無し | 一致 |

05:51Z ごろに ONTAP のクォータレポートを読み直すと、使用量とリミットは同じ値でした。4 回のポーリングの間に値は変わっていません。

### アラームの状態遷移（再実行）

`describe-alarm-history`（`StateUpdate`）より、UTC。`EvaluationPeriods` は 2、統計は 300 秒間の `Maximum` です。

| アラーム | 遷移 | 時刻 | 状態理由に含まれる値 |
|----------|------|------|----------------------|
| `fsxn-verify-qtree-quota-dlq-depth` | INSUFFICIENT_DATA → OK | 05:33:02Z | データポイント無し、`notBreaching`。実行中ずっと OK |
| `fsxn-verify-qtree-quota-quota-high` | INSUFFICIENT_DATA → OK | 05:40:27Z | データポイント 1 つ、not > 85 |
| `fsxn-verify-qtree-quota-quota-high` | OK → ALARM | 05:52:31Z | データポイント 2 つ、60.2421875（05:47:00Z）と 60.2421875（05:42:00Z）、> 50 |
| `fsxn-verify-qtree-quota-quota-high` | ALARM → OK | 05:54:45Z | データポイント 2 つ、60.2421875（05:49:00Z）と 60.2421875（05:44:00Z）、not > 85 |

OK への復帰は、閾値を 85 に戻すスタック更新の完了から約 81 秒後でした。同じアラーム名の `describe-alarm-history` には最初の実行の 02:12:50Z の記録も含まれます。履歴はアラーム名で引かれ、両方の実行が同じスタック名を使ったためです。`NotificationEmail` が空だったため、SNS のアクションは実行されていません。

### 所見（再実行）

| # | 所見 | 種別 | この記録への影響 |
|---|------|------|------------------|
| RF1 | ボリュームに最初の明示的な tree クォータルールを作成すると、ONTAP がデフォルトの tree ルール（Qtree 名が空、リミット無し）を追加した。ポーラーはこれを報告し（`Found 2 qtree quota reports`）、読み飛ばすため、公開するデータポイントは 8 つではなく 5 つになる。後片付けでは両方のルールを削除する必要がある。デフォルトのルールの削除は HTTP 409 を返し、削除は成功したがクォータを無効化して再度有効化するまでルールは適用されたままという文言だった。その後のルール一覧は 0 件 | ONTAP の挙動。1 回観測 | 後片付けの手順にデフォルトのルールを削除する手順が必要。409 は削除の失敗を意味しないので、ルール一覧を読み直して確かめる |
| RF2 | スタックを削除する前にスケジュールルールを無効化したところ、ロググループは取り残されなかった。削除後、このスタックの `/aws/lambda/` ロググループは残っていない | QF3 の回避策。1 回観測 | QF3 はテンプレートでは修正されていない。この順序で避けられたのはこの実行についてだけ |
| RF3 | `QtreeQuotaAlarm` は今回もデータポイント 1 つで INSUFFICIENT_DATA から OK に達した。ALARM への遷移と OK への復帰は、それぞれデータポイント 2 つを根拠にしている | CloudWatch の挙動。QF8 の 2 回目の観測 | 最初の OK の評価は、`EvaluationPeriods` から想定するより 1 期間早く来ることがある |
| RF4 | セキュリティグループは `0.0.0.0/0` からのすべてのインバウンドを許可し、エクスポートポリシーは `0.0.0.0/0` に読み書きとスーパーユーザーのアクセスを許可している（どちらもこのテスト VPC に既存の設定） | テスト環境の設定 | この実行で通信できたことは、最小権限の構成で動くことを示さない。最小権限の構成では、Lambda のセキュリティグループから管理インターフェイスと、`monitoring` および Secrets Manager のエンドポイントへの 443 が必要 |
| RF5 | `CaCertPath` が空だったため、ポーラーは `CERT_NONE` で動いた | 範囲の制約 | CA 証明書による検証は確認していない |

### 後片付け（再実行）

| 手順 | 結果 | 時刻（UTC） |
|------|------|-------------|
| EventBridge のスケジュールルールの無効化 | DISABLED | 05:55:13Z |
| `aws cloudformation delete-stack` | 完了 | 05:55:14Z → 05:57:16Z |
| `monitoring` interface エンドポイントの削除 | 受理（失敗した項目は無し） | 05:57:21Z |
| ONTAP REST API: 明示的な tree クォータルールの削除 | HTTP 200（ジョブ） | 05:57:32Z |
| ONTAP REST API: デフォルトの tree ルールの削除（RF1） | HTTP 409。その後のルール一覧は 0 件 | 05:57:58Z |
| ONTAP REST API: ボリュームのクォータの無効化 | クォータの状態は off | 05:58:18Z |
| ONTAP REST API: Qtree の削除 | HTTP 200（ジョブ）。残ったのはボリュームの暗黙の Qtree だけ | 05:58:44Z |
| `SkipFinalBackup=true` での `aws fsx delete-volume` | ボリュームは見つからない状態として読み戻された | 05:59:14Z → 06:00:07Z |
| AWS での再読み取り | スタック、エンドポイント、ボリューム、2 つのアラーム、関数、ロググループ、スケジュールルール、DLQ、IAM ロール、スタックまたはエンドポイントのネットワークインターフェイスは、いずれも何も返さない | 06:01:22Z → 06:01:41Z |
| ONTAP での再読み取り | SVM のクォータルールは 0 件。Qtree `qt_mon_verify` は無い。ボリューム `zz_mon_verify_qtree` は見つからない | 06:01:41Z → 06:01:49Z |

`FSxONTAP/Qtree` のカスタムメトリクス 5 系列は削除できないため、最初の実行と同じく CloudWatch の保持期間による失効に任せています。再実行後の一覧は確認し直していません。

### 未検証の範囲（再実行）

| 項目 | 状態 | 理由 |
|------|------|------|
| 第 2 世代のファイルシステムと、HA ペアが 2 つ以上の構成 | 未実施 | 検証対象は第 1 世代で HA ペアは 1 つ |
| CA 証明書を使った TLS（`CaCertPath`、`CaCertLayerArn`） | 未実施 | 実行したのは `CERT_NONE` だけ（RF5） |
| SNS 通知の配信 | 未実施 | `NotificationEmail` が空で、アラームにアクションが無かった |
| 1 ページを超えるページング、打ち切りの経路（`QtreeQuotaReportTruncated=1`）、Qtree が 200 を超える SVM | 未実施 | SVM が返したのは 1 ページに収まる 2 レコード |
| 最小権限のセキュリティグループとエクスポートポリシー | 未試験 | この実行は、許可範囲の広いテスト環境の設定を流用した（RF4） |
| ポーラー専用の読み取り専用 ONTAP アカウント | 未試験 | この実行は `fsxadmin` を使った |
| 最初の実行のロックを ONTAP 側で証明すること | 未検証 | ONTAP のログインとロックの状態や EMS イベントを読んでいない |
| 使用率 0% のときの挙動と、使用量が変化するときの挙動 | 未実施 | 最初のポーリングの前にデータを書き込み、実行中は変化させていない |
| テンプレートでの QF2 と QF3 の修正 | 未試験 | 提案のみ。RF2 は運用の順序による回避策 |

### 判定（再実行）

| 項目 | 値 |
|------|-----|
| 判定 | ✅ 第 1 世代・HA ペア 1 つのファイルシステムで、Qtree 単位と SVM 単位のメトリクスの公開を連続 4 回のポーリングサイクルについて検証し、値は ONTAP のクォータレポートと一致した。`QtreeQuotaAlarm` の OK → ALARM → OK の経路を実データで検証した。401 と 403 は起きていない |
| 合格 | 12 件中 12 件 |
| 不合格または未実施 | 0 件 |
| テンプレート単位の問題 | 新たな問題は無し。最初の実行の QF2 と QF3 はテンプレートに残っている |

---

## 2026-10-06 の容量アラームの実データによる実行

2026-10-06T18:17Z から 2026-10-07T00:56Z（UTC）にかけて、ファイルシステム容量アラームを実データで OK から ALARM へ遷移させ、OK に戻しました。対象は CloudFormation テンプレート（`StorageCapacityAlarm`）と Terraform モジュール（`storage_capacity`）の両方です。これで、2026-10-05 の実行で [F1](#所見) が残した ALARM 経路の空白が埋まります。シンプロビジョニングのテスト用ボリュームに約 472.5 GiB のランダムなデータを書き込み、SSD の利用率を 3.48% から最大 58.6% まで上げて、閾値の 50% を超えさせました。両アラームは 22:27Z に ALARM へ遷移し、00:41Z に OK に戻りました。OK への復帰は、テスト用ボリュームを削除し、そのエントリを ONTAP の volume recovery queue から purge した後に利用率が下がったことで観測しています。閾値は引き上げていません。ボリュームを削除しただけでは利用率は下がりませんでした。[OK への復帰と volume recovery queue](#ok-への復帰と-volume-recovery-queue) を参照してください。

| 項目 | 値 |
|------|-----|
| 検証日時 | 2026-10-06T18:17Z から 2026-10-07T00:56Z（UTC）。SSO の再サインインのための約 9 分の中断を含む |
| 検証環境 | テスト環境（`ap-northeast-1`）。SVM 1 つ、テスト用ボリューム 1 つ、書き込みストリーム 1 本でのサンプル実行 |
| 範囲 | 両成果物のファイルシステム容量アラームについて、実データでの ALARM への遷移と OK への復帰。アグリゲートと volume recovery queue の読み取り、および recovery queue のエントリの purge のために、踏み台ホストから ONTAP REST API を呼んだ |
| 結果 | 合格。両アラームが INSUFFICIENT_DATA → OK → ALARM → OK と遷移した。8 件中 8 件が合格 |

以下の値は、第 1 世代・HA ペア 1 つのファイルシステムでの 1 回の実行から得たものです。示すのは、アラームが指定した系列で発報し、解除されることです。ファイルシステムのスループット値ではなく、第 2 世代や複数 HA ペアのファイルシステムについては何も示しません。

### 方法と環境（容量の実行）

実データを書き込む方法を取ったのは、2026-10-06 の先行する最初の試行で、領域の予約によって利用率を上げられなかったためです。その試行ではシックプロビジョニングのボリューム（スペースギャランティ `volume`）を作成しようとし、ONTAP はエラーコード 787011 で拒否しました: "Aggregates with attached object stores cannot contain volumes with a guarantee other than none"。検証対象のファイルシステムのアグリゲートにはオブジェクトストアが接続されている（`cloud_storage` の使用量を報告する）ため、このファイルシステムでは SSD の使用量はデータを書き込んだときにだけ増えます。そこでこの実行ではデータを書き込み、書き込み量をできるだけ小さくするために閾値を下限の 50 にしました。同じくデータを書き込んだ 2 回目の試行は、AWS とは無関係な理由で書き込みの途中に停止しており、この記録には含めていません。

| 項目 | 値 |
|------|-----|
| ファイルシステム | `fs-0123456789abcdef0`（プレースホルダー）、`SINGLE_AZ_1`（第 1 世代）、HA ペア 1 つ、128 MBps、SSD IOPS 3072（自動） |
| ONTAP のバージョン | 9.18.1P6（`GET /api/cluster`） |
| アグリゲート | `aggr1`、861.76 GiB。`StorageTier=SSD`、`DataType=All` の CloudWatch `StorageCapacity` も同じサイズを報告した |
| ソースのリビジョン | `shared/templates/fsxn-monitoring-dashboard.yaml` と `terraform/fsxn-monitoring-dashboard/` のどちらも `fc9e80f`（main） |
| Terraform プロバイダー | コミット済みのロックファイルの `hashicorp/aws` v6.67.0、ローカルの state |
| アラームの設定（両成果物） | 閾値 50（`CapacityThresholdPercent=50`、`capacity_threshold_percent=50`）。`StorageCapacityUtilization` を `FileSystemId` + `StorageTier=SSD` + `DataType=All` で参照、Average、300 秒 × 3、`GreaterThanThreshold`。通知先のメールアドレスは指定しておらず、SNS トピックは無し |
| Terraform のリソース | `name_prefix = "fsxn-verify-tf"`、オプトインのアラームは無し。3 件追加（ダッシュボードとテンプレート同等のアラーム 2 つ） |
| テスト用ボリューム | シンプロビジョニング（スペースギャランティ none）、520,000 MB、UNIX セキュリティ形式、Snapshot ポリシー none、階層化ポリシー `NONE`、ストレージ効率は無効。`aws fsx create-volume` で作成し、`SkipFinalBackup=true` で削除 |
| 書き込み | 踏み台ホストからの NFS 4.2 マウント上で、1 GiB のファイルを書き込む `dd if=/dev/urandom bs=1M oflag=direct` のループ 1 本 |
| 書き込み量 | 実際のアグリゲート使用量から、861.76 GiB の 58%（停止条件の 65% 未満）に達するよう算出。書き込み完了時にボリューム上にあったのは 472.5 GiB（483,818 MiB） |
| 認証 | AWS IAM Identity Center（SSO）のセッション。ONTAP は `fsxadmin` |

実行開始時のアグリゲートの使用量は 230.09 GiB（26.7%）で、アイドル時の基準値ではありませんでした。以前の試行で削除したテスト用ボリューム 3 つが、ONTAP の volume recovery queue に残っていたためです。書き込みを始める前にこの 3 エントリを purge すると、約 4 分以内に約 200 GiB が解放され、30.00 GiB（CloudWatch で 3.48%）になりました。

書き込みは 18:31:11Z に 10.41 GiB を書いた時点で 1 回停止しました。以前の試行の後片付けのコマンドが、遅れて踏み台ホストに届いたためです。そのコマンドは取り消し、残りの量を実際のアグリゲート使用量から計算し直して、18:44:17Z に書き込みを再開しました。

> **書き込み速度に関する補足**: 再開後、書き込みストリーム 1 本は最初の 5 分間は約 96 MiB/s、その後は約 30 MiB/s で安定しました。再開後に書いた 460 GiB の平均は 31.3 MiB/s で、4 時間 11 分かかっています。速度が落ちた原因は特定していません。1 回の実行で、バーストクレジットや IOPS のメトリクスも確認していないため、これらはファイルシステムのスループットの測定値ではありません。ストリーム 1 本でこの規模を書き込む場合は数時間を見込んでください。

### 確認項目の結果（容量の実行）

| # | 確認項目 | 結果 | 時刻（UTC） |
|---|----------|------|-------------|
| C0 | 事前確認: ONTAP の `GET /api/cluster`、アグリゲート、recovery queue、CloudWatch、ファイルシステムの状態 | ✅ 合格。HTTP 200。アグリゲートの使用量は 230.09 GiB（26.7%）で、以前の試行の recovery queue のエントリ 3 つが保持していた | 18:17Z |
| C1 | 古い recovery queue のエントリ 3 つの purge | ✅ 合格。それぞれ HTTP 202。18:18:52Z までに queue は 0 件、18:21:43Z までにアグリゲートは 30.00 GiB | 18:17:32Z → 18:21:43Z |
| C2 | テスト用ボリュームの作成、マウント、データの書き込み | ✅ 合格。ボリューム上に 472.5 GiB。1 回中断して再開（上記を参照） | 18:27:37Z → 22:55:28Z |
| C3 | CloudFormation スタックのデプロイと Terraform モジュールの適用（どちらも閾値 50） | ✅ 合格。CREATE_COMPLETE、3 件追加 | 18:30:30Z → 18:31:30Z |
| C4 | 両容量アラームが INSUFFICIENT_DATA → OK | ✅ 合格 | 18:32:26Z、18:32:28Z |
| C5 | 両容量アラームが OK → ALARM | ✅ 合格 | 22:27:26Z、22:27:28Z |
| C6 | アンマウント、ボリュームの削除、その recovery queue のエントリの purge。両アラームが ALARM → OK | ✅ 合格 | 22:58:52Z → 00:41:28Z |
| C7 | 後片付けと再読み取り | ✅ 全リソースで合格 | 00:54:58Z → 00:56:41Z |

### 利用率とアラームの状態遷移（容量の実行）

`describe-alarm-history`（`StateUpdate`）より、UTC。同じアラーム名では、同じ名前を使った 2026-10-05 の実行の記録も返ります。この実行に属するのは下表の記録だけです。

| アラーム | 遷移 | 時刻 | 状態理由に含まれる値 |
|----------|------|------|----------------------|
| `fsxn-verify-tf-capacity-high`（Terraform） | INSUFFICIENT_DATA → OK | 2026-10-06T18:32:26Z | 初回の評価。利用率は約 3.5–5% |
| `fsxn-verify-monitoring-dashboard-capacity-high`（CloudFormation） | INSUFFICIENT_DATA → OK | 2026-10-06T18:32:28Z | 初回の評価。利用率は約 3.5–5% |
| `fsxn-verify-tf-capacity-high`（Terraform） | OK → ALARM | 2026-10-06T22:27:26Z | 3 データポイント、50.176（22:12）、51.21（22:17）、52.242（22:22）、> 50 |
| `fsxn-verify-monitoring-dashboard-capacity-high`（CloudFormation） | OK → ALARM | 2026-10-06T22:27:28Z | 同じ 3 データポイント、> 50 |
| `fsxn-verify-tf-capacity-high`（Terraform） | ALARM → OK | 2026-10-07T00:41:26Z | 1 データポイント、48.21（00:36）、not > 50 |
| `fsxn-verify-monitoring-dashboard-capacity-high`（CloudFormation） | ALARM → OK | 2026-10-07T00:41:28Z | 同じデータポイント、not > 50 |

50% を超えた最初の 300 秒の期間は 22:12Z に始まり、両アラームはその 15 分後、連続 3 期間が閾値を超えた時点で ALARM に達しました。アグリゲートの使用量は、書き込みの終了（22:55Z）から purge（00:37:59Z）まで 504.96〜505.10 GiB（58.6%、CloudWatch の最大値は 58.61%）にとどまりました。CloudWatch の利用率が 50% を上回っていたのは約 2 時間 25 分（22:12Z の期間から 00:38Z まで）で、アラームが ALARM だったのは約 2 時間 14 分です。停止条件の 65% には近づいていません。

### OK への復帰と volume recovery queue

| 時刻（UTC） | 手順 | アグリゲートの使用量（ONTAP） | CloudWatch の利用率（60 秒の Average） |
|-------------|------|-------------------------------|----------------------------------------|
| 22:58:29Z | 書き込み後のアグリゲートの読み取り（書き込みの終了は 22:55:28Z） | 504.96 GiB（58.6%） | |
| 22:58:52Z | 通常の `umount` が成功（遅延アンマウントは不要）。マウントポイントを削除 | | |
| 00:36:36Z | `aws fsx delete-volume`（`SkipFinalBackup=true`）。00:37:38Z に `describe-volumes` から消えた | | |
| 00:37:44Z | 削除後: 削除したボリュームの recovery queue のエントリが 1 つ | 505.10 GiB（58.6%）、変化なし | 58.61%（00:37Z） |
| 00:37:59Z | そのエントリの purge: HTTP 202（ジョブを登録） | | |
| 00:39:02Z | 読み取り | 414.39 GiB（48.1%） | 40.86%（00:39Z） |
| 00:40:06Z | 読み取り | 337.68 GiB（39.2%） | |
| 00:41:10Z | 読み取り | 244.58 GiB（28.4%） | 21.8%（00:41Z） |
| 00:41:26Z、00:41:28Z | 両アラームが ALARM → OK | | |
| 00:42:14Z | 読み取り。queue は 0 件 | 155.13 GiB（18.0%） | 3.92%（00:43Z） |
| 00:54:35Z | 読み取り | 29.36 GiB（3.4%）、基準値 | 3.41%（00:45Z 以降） |

空欄は、その手順の時点で読み取っていない値です。

OK への復帰は、閾値の引き上げではなく、purge の後に利用率が下がったことで観測しました。アグリゲートは purge 後の最初の読み取り（63 秒後）で 50% を下回り、CloudWatch の 1 分のデータポイントは 00:39Z に 50% を下回り、両アラームは purge の約 3.5 分後に OK になりました。閾値を超えないデータポイント 1 つで足りています。00:36Z に始まる 300 秒の期間の平均が 48.21% でした。CloudWatch の利用率は、ONTAP から読んだアグリゲートの使用量より速く下がりました。両者は取得元が異なり、差は調べていません。

FSx for ONTAP の管理 API（`aws fsx delete-volume`） でボリュームを削除しても、その領域は解放されませんでした。AWS は、削除した FSx for ONTAP ボリュームが ONTAP の recovery queue に置かれることを文書化しており（[Recovering deleted FSx for ONTAP volumes](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/recovering-deleted-volumes.html)）、AWS re:Post は、削除したボリュームが既定で少なくとも 12 時間その queue に保持されてから完全に削除されると説明しています（[How can I recover a deleted FSx for ONTAP volume?](https://repost.aws/knowledge-center/fsx-ontap-recover-deleted-volume)）（確信度: `文書化済み`）。この実行では、削除したボリュームは purge するまで SSD の使用量に数えられ続けました。アグリゲートは削除の前後とも 505.10 GiB で、実行開始時には以前の試行で削除したボリューム 3 つがまだ約 200 GiB を保持しており、CloudWatch はそれを利用率 26.7% として報告していました（観測）。purge せずに保持期間が過ぎた後の解放は観測していません。

> **容量アラームの運用に関する補足**: ボリュームを削除しても、容量アラームはすぐには解除されません。recovery queue のエントリが期限切れになる（上記の re:Post の記事によれば既定で少なくとも 12 時間）か purge されるまで、削除したボリュームのデータは SSD の使用量と `StorageCapacityUtilization` に数えられたままです。したがって、ボリュームの削除は容量の逼迫をすぐに和らげる手段にはなりません。purge すればこの実行のように数分で領域が戻りますが、元に戻せません。purge したボリュームは queue から復旧できなくなります。このテスト環境では、`fsxadmin` が ONTAP REST API の private CLI パススルー `POST /api/private/cli/volume/recovery-queue/purge` に、本文で SVM と queue 上のボリューム名を渡してエントリを purge し、HTTP 202 とジョブが返りました。

### 所見（容量の実行）

| # | 所見 | 種別 | この記録への影響 |
|---|------|------|------------------|
| CF1 | ONTAP はシックプロビジョニングのボリューム（ギャランティ `volume`）をエラーコード 787011 で拒否した。アグリゲートにオブジェクトストアが接続されているため | ONTAP の制約で、このファイルシステムで観測 | ここでは領域の予約で利用率を上げられない。容量アラームの確認には実データが要る |
| CF2 | FSx for ONTAP の管理 API（`aws fsx delete-volume`） で削除したボリュームは ONTAP の recovery queue に残り、purge されるか保持期間（既定で少なくとも 12 時間、文書化済み。期限切れは未観測）が過ぎるまで SSD の使用量に数えられ続ける | ONTAP の挙動。保持期間は文書化済み、使用量は観測 | ボリュームを削除しても容量アラームはすぐには解除されない。この実行では purge の後に OK に戻った |
| CF3 | purge は数分で領域を解放した。3 エントリでは約 200 GiB を約 4 分以内に、1 エントリでは 505.10 GiB から 63 秒以内に 50% 未満へ、00:54:35Z までに基準値の 29.36 GiB へ | ONTAP の挙動で、この実行で 2 回観測 | purge は元に戻せない。テスト環境での手順であり、本番で推奨する手順ではない |
| CF4 | ALARM は閾値を超えた最初の期間の開始から 15 分後（3 期間中 3 期間）。OK は purge の約 3.5 分後で、閾値を超えないデータポイント 1 つによる | CloudWatch の評価で、1 回観測 | このアラーム設定では、ALARM の遅れは約 3 期間、OK の遅れは約 1 期間と見込む |
| CF5 | 書き込みストリーム 1 本の速度が、約 5 分後に約 96 MiB/s から約 30 MiB/s に落ちた | 観測。原因は未特定 | スループットの測定値ではない。この規模の書き込みに約 4 時間かかった |

この実行では、ダッシュボードテンプレートと Terraform モジュールに欠陥は見つかっていません。ダッシュボードの表示の欠陥が、その後 2026-10-07 に見つかりました。[所見](#所見) を参照してください。

### 後片付け（容量の実行）

| 手順 | 結果 | 時刻（UTC） |
|------|------|-------------|
| 書き込みプロセスが無いことの確認、アンマウント、マウントポイントの削除 | マウント配下にプロセスは無し。通常の `umount` が成功 | 22:58:52Z → 22:58:55Z |
| `aws fsx delete-volume` と、その recovery queue のエントリの purge | ボリュームは `describe-volumes` から消え、queue は 0 件 | 00:36:36Z → 00:42Z |
| `terraform destroy` | exit 0、3 件削除 | 00:54:58Z |
| `aws cloudformation delete-stack` + wait | 削除済み | 00:55Z → 00:55:33Z |
| ローカルと踏み台ホストの一時ファイル | 削除済み | 再読み取りの前 |
| 再読み取り | テスト用ボリュームは無し。recovery queue は 0 件。スタックは存在しない。接頭辞 `fsxn-verify-` のアラームは 0 件、接頭辞 `fsxn-verify` のダッシュボードは 0 件。踏み台ホストにテスト用のマウント・ディレクトリ・書き込みプロセスは無し。アグリゲートの使用量は 29.36 GiB（基準値）で、`cloud_storage` の使用量は 2.68 GiB のまま。CloudWatch の利用率は 3.41% | 00:56:41Z |

セキュリティグループ、IAM、エクスポートポリシーは変更していません。他のボリュームや recovery queue のエントリには触れていません。

### 未検証の範囲（容量の実行）

| 項目 | 状態 | 理由 |
|------|------|------|
| 第 2 世代のファイルシステム（`Aggregate` ディメンション付きの容量）と、HA ペアが 2 つ以上のファイルシステム | 未実施 | 検証対象は第 1 世代で、HA ペアは 1 つ |
| SNS 通知の配信 | 未実施 | 通知先のメールアドレスを指定しておらず、アラームにアクションが無かった |
| 50 以外の閾値（既定の 80 を含む）での ALARM | 未実施 | 書き込み量を抑えるため 50 だけを使った |
| purge せずに recovery queue のエントリが期限切れになったときの領域の解放 | 未観測 | エントリは purge した |
| 書き込み速度が落ちた原因 | 未特定 | バーストクレジットや IOPS のメトリクスを確認していない |

### 判定（容量の実行）

| 項目 | 値 |
|------|-----|
| 判定 | ✅ 第 1 世代・HA ペア 1 つのファイルシステムで、CloudFormation テンプレートと Terraform モジュールの両方について、ファイルシステム容量アラームの OK → ALARM → OK の経路を閾値 50 で実データにより検証した。OK への復帰は、purge の後に利用率が下がったことで観測した |
| 合格 | 8 件中 8 件 |
| 不合格または未実施 | 0 件 |
| 見つかった欠陥 | この実行では無し。2026-10-07 にダッシュボードの表示の欠陥が見つかった。[所見](#所見) を参照 |

---

## 2026-10-07 のダッシュボードとアラームの画面

2026-10-07（UTC）に、Terraform モジュールを `ap-northeast-1` の第 1 世代 `SINGLE_AZ_1`、HA ペア 1 つのファイルシステムに適用し、CloudWatch コンソールを撮影しました。確認項目を伴う検証の実行ではなく、1 つの環境でのサンプルです。画像が記録しているのは、撮影時点でコンソールに表示されていた内容です。デプロイは撮影後に削除しました。どちらの画像も、コンソールのナビゲーションバーとフッターを切り取り、ファイルシステム ID とボリューム ID をマスクしています。

| 項目 | 値 |
|------|-----|
| リージョン | アジアパシフィック（東京）、`ap-northeast-1` |
| ファイルシステム | `fs-0123456789abcdef0`（プレースホルダー）、`SINGLE_AZ_1`（第 1 世代）、HA ペア 1 つ |
| モジュール | `terraform/fsxn-monitoring-dashboard/` をローカルパスから適用。1 回目の apply は生の系列の表示の修正前、2 回目の apply は修正後。`hashicorp/aws` v6.67.0 |
| 入力 | `file_system_name = "fsx-for-ontap-demo"`、`name_prefix` は既定値（`fsxn-monitoring`）、ファイルサーバーの任意アラーム 3 つを `file_server_names` を空にして有効化、`volume_ids` に 1 件、`notification_email` は指定なしで SNS トピックは無し |
| リソース | 1 回目の apply は約 06:45Z に完了: 8 件追加（ダッシュボード 1、アラーム 7）。2 回目の apply は約 08:27Z: 1 件変更（ダッシュボードのみ）。destroy は約 08:30Z: 8 件削除 |
| コンソール | 日本語の UI、タイムゾーンは UTC |
| ダッシュボードの撮影 | 約 08:28Z、12 時間の範囲、修正後 |
| アラーム一覧の撮影 | 約 07:03Z、`fsxn-monitoring` で絞り込み、2 回目の apply の前。修正はダッシュボードの本文だけを変えたので、アラームは両方の apply で同じ |

![CloudWatch ダッシュボード fsxn-monitoring-fsx-for-ontap-demo の 12 時間の範囲（UTC）: ファイルシステム名、リージョン、コンソールへのリンクを表示するテキストウィジェットと、Network Throughput (MB/s)、IOPS (Operations/s)、Network Throughput Utilization (%)、Storage Capacity Utilization (%)、Network Sent/Received (MB/s)、Storage Used (GB) の 6 つのグラフ。ファイルシステム ID はマスク済み](../screenshots/cloudwatch-monitoring/01-dashboard-12h.png)

12 時間の範囲（2026-10-06 の約 20:28Z から 2026-10-07 の約 08:28Z）は、同じファイルシステムでの[容量アラームの実データによる実行](#2026-10-06-の容量アラームの実データによる実行)の終盤と重なります。CloudWatch はメトリクスの履歴を保持しているので、2026-10-07 に作成したダッシュボードにもそれ以前の時間帯が描画されます。23:00Z の少し前まで、スループット、IOPS、ネットワークのグラフには、その実行の書き込みストリーム 1 本が現れています。書き込みの終了は 22:55:28Z です。Storage Capacity Utilization は約 58% まで上がり（その実行の CloudWatch の最大値は 58.61%）、その水準にとどまった後、テスト用ボリュームの recovery queue のエントリを 00:37:59Z に purge してから基準値に戻ります（[OK への復帰と volume recovery queue](#ok-への復帰と-volume-recovery-queue) を参照）。80 の位置の破線は、このダッシュボードの閾値の注記です。その実行のアラームは閾値 50 を使っており、このダッシュボードには載らない別のデプロイに属していました。約 01:00Z 以降のグラフには小さなスパイクしかありません。この撮影のための負荷はかけていません。

![CloudWatch のアラーム一覧を fsxn-monitoring で絞り込んだ画面: アラーム 7 件がすべて OK、アクションなし。条件は StorageCapacityUtilization>80（3 データポイント、15 分以内）など。2 つのアラーム名に含まれるボリューム ID はマスク済み](../screenshots/cloudwatch-monitoring/02-alarms-list.png)

7 件のアラームは、テンプレートと同等の 2 つ（`capacity-high`、`throughput-high`）、ファイルサーバーの任意アラーム 3 つ（`cpu-high`、`disk-iops-high`、`disk-throughput-high`）、ボリューム単位の 2 つ（`fsvol-…-capacity-high`、`fsvol-…-inode-high`）です。アクションの列が「アクションなし」なのは、`notification_email` を指定していないためです。条件の列は、15 分以内の 3 データポイントで 80 を超えることを示しています。最終状態の更新の列では、すべてのアラームが 1 回目の apply の直後、06:45:24Z から 06:46:11Z の間に OK に達しています。一覧の右上にあるコンソールの表示「メトリクスデータが検証されていません」は調べていません。

> **表示の修正に関する補足**
>
> 修正前に撮影したこのダッシュボードの最初の画面では、4 つのウィジェットが、式の入力である生のメトリクスを換算後の系列と同じ軸に描画していました。これは [所見](#所見) のダッシュボード表示に関する補足に記載した欠陥です。上のダッシュボードの画像は、同じデプロイに修正を適用した後に撮影したものです。

これらの画像は、SNS 通知（メールアドレスを指定していない）、このデプロイでの ALARM 状態、第 2 世代や複数 HA ペアのファイルシステムを示していません。

---

## 関連ドキュメント

- [監視設計](monitoring-design.md): ダッシュボードテンプレート、Terraform T1 モジュール、Qtree クォータ監視。確信度の階層がこの記録を引用している
- [AWS ネイティブ代替マトリクス](native-alternative-matrix.md): System Manager のビュー → CloudWatch メトリクス → テンプレートの対応
- [Terraform モジュール: fsxn-monitoring-dashboard](../../terraform/fsxn-monitoring-dashboard/README.md): 入力・出力・検証状況
- [CloudWatch ログアラーム](cloudwatch-log-alarm.md): 別のログアラームテンプレートと、その 2026-07-02 の E2E 記録
