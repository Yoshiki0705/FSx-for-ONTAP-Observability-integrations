# fsxn-monitoring-dashboard (Terraform)

🌐 **日本語** | [English](README.md)

`shared/templates/fsxn-monitoring-dashboard.yaml` に相当する Terraform モジュールで、Amazon FSx for NetApp ONTAP のファイルシステム 1 つに対する CloudWatch ダッシュボードとアラームを作成します。[監視設計](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)（[English](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md)）の Terraform 計画のフェーズ T1 にあたります。使いどころとオプトインのアラームの表は、そのページにあります。

## 検証状況

オフラインの検証では、`make terraform` が `terraform fmt -check`、`terraform init -lockfile=readonly`、`terraform validate`、モックプロバイダーと `command = plan` による `terraform test` を実行します。[`examples/basic/`](examples/basic/) に対しても `init` と `validate` を実行します。AWS の認証情報は不要で、リソースは作成しません。

実環境での 1 回目の検証では、2026-10-05 に `ap-northeast-1` で、第 1 世代 `SINGLE_AZ_1`・HA ペア 1 つのファイルシステムに対してモジュールを plan・apply しました。ファイルサーバーのオプトインアラーム 3 本を有効にし（`file_server_names` は空）、`volume_ids` に 1 件を指定しました。ダッシュボードと 7 本のアラームが作成されました。ダッシュボードの 9 系列すべてがデータを返し、すべてのアラームが INSUFFICIENT_DATA から OK に遷移し、ボリューム単位の容量アラームと inode アラームは ALARM に遷移させて OK に戻しました。記録は [CloudWatch 監視の動作確認結果](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md)（[English](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md)）にあります。

タグ `terraform-fsxn-monitoring-dashboard-v0.1.0` には表示の欠陥があります。4 つのウィジェット（Network Throughput、IOPS、Network Sent/Received、Storage Used）が、式の入力である生のメトリクスを換算後の MB/s、IOPS、GB の系列と同じ軸に描画するため、換算後の線は 0 付近に表示されます。2026-10-07 に、デプロイしたダッシュボードのスクリーンショットを撮ったときに見つかりました。データとすべてのアラームは影響を受けていません。タグ `terraform-fsxn-monitoring-dashboard-v0.1.1` で、これらの生の行に `visible = false` を設定して修正しました。v0.1.1 以降を使ってください。記録は [ダッシュボード表示に関する補足](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#所見)（[English](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#findings)）にあります。

実環境での 2 回目の検証は、ファイルシステムの `storage_capacity` アラームを対象にしました。このアラームは閾値の範囲 50–95 がテスト用ファイルシステムの利用率 3.5% より上にあるため、2026-10-05 には ALARM に遷移させられませんでした。2026-10-06〜07 (UTC) に、アジアパシフィック (東京) リージョン（`ap-northeast-1`）の第 1 世代 `SINGLE_AZ_1`・HA ペア 1 つのファイルシステムへ、既定のアラームだけと `capacity_threshold_percent = 50` でモジュールを再度適用しました。テスト用ボリュームに実データを書き込んで SSD の利用率を 58.6% まで上げ、CloudFormation テンプレートとこのモジュールの両方の容量アラームが OK から ALARM に遷移し、OK に戻りました。どちらも名前空間 `AWS/FSx` の `StorageCapacityUtilization` を `FileSystemId` + `StorageTier=SSD` + `DataType=All` で読みます。アラームが OK に戻ったのは、削除したテスト用ボリュームを ONTAP のリカバリキューから消去した後で、ボリュームの削除だけでは利用率は下がりませんでした。記録は [容量アラームの実データによる実行](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-06-の容量アラームの実データによる実行)（[English](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#capacity-alarm-real-data-run-on-2026-10-06)）にあります。

IAM 権限の実環境での検証では、2026-10-07 (UTC) に `ap-northeast-1` で、`terraform-fsxn-monitoring-dashboard-v0.1.0` タグから取得したモジュールを、すべての機能を有効にして（10 リソース）、[必要な IAM 権限（確認済み）](#必要な-iam-権限確認済み)のポリシーだけを持つロールで適用しました。作成・タグの変更・削除は、`Resource: "*"` でも、`name_prefix` で絞った ARN でも成功し、受信者がメールのサブスクリプションを確認しました。

SNS によるアラーム通知の配信（IAM の検証ではメールのサブスクリプションを確認したが、通知の配信は記録していない）、`file_server_names` を指定した第 2 世代のファイルシステム、複数 HA ペアのファイルシステム、負荷時の挙動は、まだ `unverified` です。構成の異なるファイルシステムでは、先に本番以外のアカウントで `terraform plan` を実行してください。

## 作成されるリソース

- テンプレートと同じ 7 つのウィジェット（タイトル、スループット、IOPS、ネットワークスループット利用率、ストレージ容量利用率、ネットワーク送受信、ストレージ使用量）を持つ `aws_cloudwatch_dashboard`。
- テンプレートと同じ 2 本の `aws_cloudwatch_metric_alarm`（ストレージ容量利用率とネットワークスループット利用率）。容量アラームとウィジェットは、テンプレートと同じ `FileSystemId` + `StorageTier=SSD` + `DataType=All` を指定します。
- `aws_sns_topic` とメールの `aws_sns_topic_subscription`。`notification_email` が空でないときだけ作成し、そのとき 2 本のアラームは ALARM と OK の両方で通知します。メールが届くのは、受信者がサブスクリプションを確認した後です。
- 既定ではすべて無効のオプトインのアラーム。`CPUUtilization`、`FileServerDiskIopsUtilization`、`FileServerDiskThroughputUtilization`（各 1 本、または `file_server_names` の要素ごとに 1 本）と、`volume_ids` の要素ごとに 2 本（ボリュームの `StorageCapacityUtilization` と、メトリクス演算 `100 * FilesUsed / FilesCapacity` による inode 利用率）があります。

メトリクスはすべて名前空間 `AWS/FSx` にあり、名前・ディメンション・統計は AWS のメトリクスのページ（[ファイルシステム（第 1 世代）](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-system-metrics.html)、[ファイルシステム（第 2 世代）](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/so-file-system-metrics.html)、[ボリューム](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-metrics.html)）から取りました。

下の画像は、このモジュールが 2026-10-07 に `ap-northeast-1` の第 1 世代のファイルシステムへ、表示の修正後にデプロイしたダッシュボードです（12 時間の範囲、UTC、コンソールの言語は日本語、ファイルシステム ID はマスク済み）。撮影条件、グラフが示している内容、アラーム一覧は [検証記録](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-07-のダッシュボードとアラームの画面)（[English](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#dashboard-and-alarm-screenshots-on-2026-10-07)）にあります。

![このモジュールが作成した CloudWatch ダッシュボード: テキストウィジェットと 6 つのグラフ（Network Throughput、IOPS、Network Throughput Utilization、Storage Capacity Utilization、Network Sent/Received、Storage Used）、12 時間の範囲。ファイルシステム ID はマスク済み](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/screenshots/cloudwatch-monitoring/01-dashboard-12h.png?raw=true)

## モジュールの取得方法

このモジュールは `terraform-fsxn-monitoring-dashboard-vX.Y.Z` の形式の git タグで版を付けています。現在の版は `terraform-fsxn-monitoring-dashboard-v0.1.1` で、[GitHub の Release](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/releases/tag/terraform-fsxn-monitoring-dashboard-v0.1.1) として公開しています。最初のタグ `terraform-fsxn-monitoring-dashboard-v0.1.0`（[GitHub の Release](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/releases/tag/terraform-fsxn-monitoring-dashboard-v0.1.0)）には、[検証状況](#検証状況)に記したダッシュボードの表示の欠陥があるため、v0.1.1 以降を使ってください。モジュールは大きなリポジトリのサブディレクトリなので、Terraform Registry には登録されていません。下表のサイズは、最初のタグを作る前の 2026-10-07 にコミット `4b27a84` で測ったもので、リポジトリが大きくなれば増えます。

| 方法 | ダウンロードされるもの（実測） | 版の固定 | git の要否 | 現時点の状態 |
|---|---|---|---|---|
| タグを指定した git ソースと `?ref=<tag>&depth=1` | 作業ツリー全体の浅い clone、約 50 MB（タグではなく `ref=main&depth=1` で測定） | タグ | 要 | 使える（現在のタグは `terraform-fsxn-monitoring-dashboard-v0.1.1`） |
| コミット SHA を指定した git ソースと `?ref=<commit-sha>` | リポジトリ全体の完全な clone、約 62 MB。SHA に `&depth=1` を付けると `fatal: Remote branch <sha> not found` で失敗する。`depth` はブランチ名かタグ名でしか使えないため | コミット SHA | 要 | 使える |
| コミット SHA を指定したアーカイブ URL | ダウンロード約 20 MB。サブディレクトリのパスは `FSx-for-ONTAP-Observability-integrations-<commit-sha>/` で始める必要がある | コミット SHA | 不要 | 使える |
| モジュールのディレクトリだけを `git sparse-checkout` し、ローカルパスを `source` にする | 約 912 KB（モジュールと、`LICENSE` を含むリポジトリ直下のファイル） | 手元のコピーのタグかコミット SHA | 要 | 使える |

タグを指定した git ソース、コミット SHA を指定した git ソース、アーカイブ URL の `source` の書き方は次のとおりです。コードブロック内のコメントは英語のままで、上から順に「タグに固定した git ソース（浅い clone）」「コミットに固定した git ソース」「コミットに固定したアーカイブ URL（git 不要）」という意味です。

```hcl
# Git source pinned to a tag (shallow clone)
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-monitoring-dashboard?ref=terraform-fsxn-monitoring-dashboard-v0.1.1&depth=1"

# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-monitoring-dashboard?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-monitoring-dashboard"
```

次の手順は sparse checkout で、タグの時点のモジュールのディレクトリだけを取り出します。その後、このコピーの `terraform/fsxn-monitoring-dashboard` のローカルパスを `source` に指定します。コミットに固定する場合は、タグ名をコミット SHA に置き換えます。

```bash
git init fsx-ontap-monitoring && cd fsx-ontap-monitoring
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-monitoring-dashboard
git fetch --depth 1 --filter=blob:none origin terraform-fsxn-monitoring-dashboard-v0.1.1
git checkout FETCH_HEAD
```

> **ダウンロード量に関する補足**
>
> `//subdirectory` 形式のソースでは、Terraform はパッケージ全体をダウンロードして展開し、その後でサブディレクトリからモジュールを読みます（[module ブロックのリファレンス](https://developer.hashicorp.com/terraform/language/block/module)）。git とアーカイブのソースがモジュール本体よりはるかに多くを取得するのはこのためです。

> **Subversion に関する補足**
>
> GitHub は 2024 年に Subversion のサポートを終了しました（[GitHub changelog](https://github.blog/changelog/2024-01-07-subversion-has-been-sunset/)）。1 つのディレクトリだけを `svn export` する方法は使えません。

どの方法を選ぶかは、ダウンロード量・git の要否・更新の手順・版の固定のどれを優先するかで決まります。アーカイブ URL は git が不要でコミットを固定できます。更新するときは長い URL の中の 2 か所の SHA を書き換えます。SHA を指定した git ソースはコミットを固定でき、更新では `ref` の値を 1 つ変えます。git が必要で、新しい作業ディレクトリで `init` するたびにリポジトリ全体を clone します。タグの形式は読みやすい版を固定でき、浅い clone が使えます。更新では `ref` のタグ名を変えます。git が必要で、作業ツリー全体はダウンロードします。sparse checkout はダウンロード量が最も少なく、使う前にコードを確認できます。コピーは自分で管理し、更新も自分で取り込みます。

## 使い方

以下の節は、初回のデプロイの順に並んでいます。前提条件、権限、入力値、デプロイ、適用後の確認、削除の順です。

### 前提条件

- Terraform `>= 1.11.0`。
- `hashicorp/aws` `>= 6.67.0`。6.67.0 で確認済みで、6.0〜6.66 は未確認です。
- IAM Identity Center または IAM ロールによる AWS の認証情報と、`provider "aws"` ブロックに設定したファイルシステムのリージョン。モジュール自体は `provider` ブロックを持ちません。

### 必要な IAM 権限（確認済み）

2026-10-07 (UTC) に `ap-northeast-1` で、Terraform 1.15.8 と `hashicorp/aws` 6.67.0 を使い、`terraform-fsxn-monitoring-dashboard-v0.1.0` タグから取得したモジュールを、第 1 世代 `SINGLE_AZ_1`・HA ペア 1 つのファイルシステムに適用しました。ダッシュボード、既定の 2 本のアラーム、SNS トピックとメールのサブスクリプション、ファイルサーバーの 3 本のアラーム、ボリューム 1 つ（容量と inode のアラーム）、`tags` をすべて有効にし、リソースは合計 10 個です。呼び出し元は、プロバイダーが `assume_role` で引き受ける専用の IAM ロールで、下表のアクションを持つインラインポリシーだけを付けました。作成、変更のない plan、タグの値の変更、すべてのタグの削除、削除（destroy）がすべて成功しました。表のアクションはどれもこのライフサイクルに必要でした。タグの 4 アクションを除いたポリシーでは、作成（`sns:TagResource`）とタグの変更（`cloudwatch:TagResource`）が失敗しました。これは 1 つのアカウントと 1 つのファイルシステムの形で行ったサンプル実行です。v0.1.0 と比べて v0.1.1 で変わったのはダッシュボードの本文だけで、書き込みには引き続き `cloudwatch:PutDashboard` を使います。このロールでの実行は v0.1.1 では繰り返していません。

| リソース | アクション |
|---|---|
| `aws_cloudwatch_dashboard` | `cloudwatch:PutDashboard`, `cloudwatch:GetDashboard`, `cloudwatch:DeleteDashboards` |
| `aws_cloudwatch_metric_alarm` | `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, `cloudwatch:DeleteAlarms`, `cloudwatch:ListTagsForResource`, `cloudwatch:TagResource`, `cloudwatch:UntagResource` |
| `aws_sns_topic`（`notification_email` を指定したときだけ） | `sns:CreateTopic`, `sns:GetTopicAttributes`, `sns:SetTopicAttributes`, `sns:ListTagsForResource`, `sns:TagResource`, `sns:UntagResource`, `sns:DeleteTopic` |
| `aws_sns_topic_subscription`（`notification_email` を指定したときだけ） | `sns:Subscribe`, `sns:GetSubscriptionAttributes`, `sns:Unsubscribe` |

ポリシーは [`examples/basic/iam-policy.json`](examples/basic/iam-policy.json) にあります。各ステートメントは、モジュールが `name_prefix` から作る名前に絞っています。ダッシュボードは `<name_prefix>-<file_system_name>`、アラームは `<name_prefix>-...`、トピックは `<name_prefix>-alarms` です。ポリシーを作る前に、`123456789012` を自分のアカウント ID に、`ap-northeast-1` をファイルシステムのリージョンに、`name_prefix` を変えた場合は `fsxn-monitoring` をその値に置き換えます。ダッシュボードの ARN にはリージョンの部分がありません。ワイルドカードは、同じプレフィックスで始まる名前のほかのダッシュボード・アラーム・トピックにも一致します。ファイルを編集した後、`examples/basic/` で次のコマンドを実行し、そのロールを `provider "aws"` ブロックで使います。検証では同じ内容をロールのインラインポリシーとして付けました。次のコマンドは、代わりにカスタマー管理ポリシーを作ります。

```bash
aws iam create-policy --policy-name fsxn-monitoring-terraform \
  --policy-document file://iam-policy.json
aws iam attach-role-policy --role-name <terraform-role-name> \
  --policy-arn arn:aws:iam::123456789012:policy/fsxn-monitoring-terraform
```

同じアクションで `Resource: "*"` にしたポリシーも検証で成功しましたが、ファイルでは絞った形を公開しています。プロバイダーが呼ぶ `sts:GetCallerIdentity` と、`data "aws_region"` には権限が不要でした。[ディメンション値の調べ方](#ディメンション値の調べ方)と[適用後の確認手順](#適用後の確認手順)の読み取り専用のコマンドには、`cloudwatch:DescribeAlarms` と `cloudwatch:GetDashboard` のほかに `cloudwatch:ListMetrics` と `fsx:DescribeFileSystems` が必要です。これらは apply 用のロールではなく自分の認証情報で実行するもので、ロールでは実行していません。

> **CloudTrail に関する補足**
>
> 1 回の apply で CloudTrail に記録されたアクションだけからポリシーを作ると、タグのアクションが抜けます。`tags` を指定してトピックを作るとき、`sns:TagResource` がないと `TagResource` のイベントではなく `CreateTopic` の AccessDenied として記録されます。`cloudwatch:TagResource` はタグ付きのアラームの作成には不要でしたが、後でタグの値を変えるときに必要でした。`TagResource` と `UntagResource` の呼び出しは、タグを変えたときにだけ現れます。

> **未確認のサブスクリプションに関する補足**
>
> メールのサブスクリプションを確認しないままにすると、`terraform destroy` は `Unsubscribe` を呼び、保留中のサブスクリプションに対して `InvalidParameterException` で失敗し、プロバイダーはそれを state から外します。保留中のエントリは、SNS が未確認のサブスクリプションを 48 時間後に削除するまで `aws sns list-subscriptions` に残ります（[Amazon SNS のメール通知](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html)）。

### ディメンション値の調べ方

ファイルシステム ID は Amazon FSx の API（`aws fsx describe-file-systems`）から取得します。`volume_ids` に指定する `VolumeId` と、`file_server_names` に指定する `FileServer` の値は、そのファイルシステムについて CloudWatch にすでにあるメトリクスから読めます。最後のコマンドは、重複を除いた値の一覧を出力します。

```bash
aws fsx describe-file-systems --query 'FileSystems[].{Id:FileSystemId,Type:FileSystemType}' --output table
aws cloudwatch list-metrics --namespace AWS/FSx --dimensions Name=FileSystemId,Value=fs-0123456789abcdef0
aws cloudwatch list-metrics --namespace AWS/FSx --dimensions Name=FileSystemId,Value=fs-0123456789abcdef0 \
  --query 'Metrics[].Dimensions[?Name==`VolumeId` || Name==`FileServer`].Value | []' --output text | tr '\t' '\n' | sort -u
```

### examples/basic からのデプロイ手順

次のコマンドは、`terraform/` を含むディレクトリで実行します。[モジュールの取得方法](#モジュールの取得方法)の sparse checkout で作った `fsx-ontap-monitoring/` か、リポジトリ全体の clone です。そこでは `examples/basic/` が `source = "../.."` でモジュールを呼ぶので、取り出したコピーが使われます。`terraform.tfvars` では `region`・`file_system_id` と、必要な任意の入力値を編集します（コードブロック内の英語のコメントと同じ内容です）。

```bash
cd terraform/fsxn-monitoring-dashboard/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: region, file_system_id, and any optional inputs
terraform init
terraform plan
terraform apply
```

自分のルート構成では [`examples/basic/`](examples/basic/) をコピーし、`source = "../.."` を [モジュールの取得方法](#モジュールの取得方法) のいずれかのソースに置き換えます。この場合は clone も sparse checkout も不要で、`terraform init` が `source` からモジュールをダウンロードします。タグを使うと、module ブロックは次のようになります。コメントの `Opt-in alarms (all off by default)` は「オプトインのアラーム（既定ではすべて無効）」という意味です。

```hcl
module "fsx_ontap_monitoring" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-monitoring-dashboard?ref=terraform-fsxn-monitoring-dashboard-v0.1.1&depth=1"

  file_system_id             = "fs-0123456789abcdef0"
  file_system_name           = "fsx-for-ontap-prod"
  capacity_threshold_percent = 80
  notification_email         = "ops@example.com"

  # Opt-in alarms (all off by default)
  enable_cpu_utilization_alarm = true
  volume_ids                   = ["fsvol-0123456789abcdef0"]
}
```

### 適用後の確認手順

`fsxn-monitoring` は自分の `name_prefix` に、ダッシュボード名は出力 `dashboard_name` の値に置き換えます。

```bash
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-monitoring \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table
aws cloudwatch get-dashboard --dashboard-name fsxn-monitoring-fsx-for-ontap
```

アラームは、評価期間に必要なデータポイントがそろうまで INSUFFICIENT_DATA のままです。`notification_email` を指定した場合、メールのサブスクリプションは受信者が確認するまで保留のままです。

### 削除手順

```bash
terraform destroy
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-monitoring \
  --query 'MetricAlarms[].AlarmName' --output text
```

すべてのアラームが削除されていれば、2 つ目のコマンドは何も出力しません。

## 入力

| 名前 | 型 | 既定値 | 補足 |
|---|---|---|---|
| `file_system_id` | string | 必須 | `^fs-[0-9a-f]{17}$`（テンプレートの `FileSystemId`） |
| `file_system_name` | string | `"fsx-for-ontap"` | ダッシュボードの名前とタイトル（テンプレートの `FileSystemName`） |
| `name_prefix` | string | `"fsxn-monitoring"` | リソース名の中でスタック名の役割を果たす |
| `capacity_threshold_percent` | number | `80` | 50–95（テンプレートの `CapacityThresholdPercent`） |
| `throughput_threshold_percent` | number | `80` | 1–100。テンプレートは 80 に固定 |
| `notification_email` | string | `""` | 空なら SNS を作らない（テンプレートの `NotificationEmail`） |
| `enable_cpu_utilization_alarm` | bool | `false` | `cpu_utilization_threshold_percent`（80）と組み合わせる |
| `enable_disk_iops_utilization_alarm` | bool | `false` | `disk_iops_utilization_threshold_percent`（80）と組み合わせる |
| `enable_disk_throughput_utilization_alarm` | bool | `false` | `disk_throughput_utilization_threshold_percent`（80）と組み合わせる |
| `file_server_names` | list(string) | `[]` | 第 2 世代のファイルシステムの `FileServer` の値。例: `FsxId0123456789abcdef0-01` |
| `volume_ids` | list(string) | `[]` | `^fsvol-[0-9a-f]{17}$`、重複不可 |
| `volume_capacity_threshold_percent` | number | `80` | 1–100 |
| `volume_inode_threshold_percent` | number | `80` | 1–100 |
| `tags` | map(string) | `{}` | アラームと SNS トピックに付ける。ダッシュボードはタグを持たない |

## 出力

| 名前 | 説明 |
|---|---|
| `dashboard_name`, `dashboard_arn`, `dashboard_url` | ダッシュボードの識別子とコンソールの URL（テンプレートの `DashboardUrl` と同じ形） |
| `capacity_alarm_arn`, `throughput_alarm_arn` | テンプレートと同じ 2 本のアラーム |
| `sns_topic_arn` | トピックの ARN。`notification_email` がなければ `null` |
| `file_server_alarm_arns` | `<metric_key>` または `<metric_key>/<file_server>` をキーとするマップ |
| `volume_alarm_arns` | `{ capacity = { <volume_id> = arn }, inode = { <volume_id> = arn } }` |

## CloudFormation テンプレートとの意図的な差

- `throughput_threshold_percent` で、テンプレートが 80 に固定している閾値を変更できる。
- `alarm_actions` に加えて `ok_actions` を設定する。
- `file_system_name` の既定値は `fsx-for-ontap`。
- `aws_fsx_ontap_file_system` データソースを使わない。使うと plan 時の権限に `fsx:DescribeFileSystems` が加わり、ファイルシステムができる前に plan できなくなる。代わりに ID の形式を正規表現で検証する。
- SNS トピックはテンプレートと同じく暗号化しない。T1 には KMS の入力がない。

## 第 2 世代ファイルシステムでの注意点

AWS は第 2 世代のファイルシステムのファイルサーバーのメトリクスを `FileSystemId` + `FileServer` で、容量を省略可能な `Aggregate` 付きで文書化しています。`FileSystemId` だけのネットワークスループットの系列、`Aggregate` なしの容量の系列、`FileServer` なしのファイルサーバーの系列が第 2 世代にあるかは `unverified` です。第 2 世代のファイルシステムでは `file_server_names` を指定し、オプトインのアラームが文書化されたディメンションの組を使うようにしてください。

## プロバイダーの版の制約

モジュールは `hashicorp/aws` を `>= 6.67.0`（確認した最も古いリリース）で宣言し、上限は付けていません。[`examples/basic/`](examples/basic/) は完全一致のピン `= 6.67.0` と専用の `.terraform.lock.hcl` を持ちます。呼び出し側はこのモジュールの `.terraform.lock.hcl` を使いません。Terraform が読むのはルート構成のロックファイルです（[Dependency Lock File](https://developer.hashicorp.com/terraform/language/files/dependency-lock)）。モジュールのロックファイルは、このリポジトリの `make terraform` と CI のためだけにあります。テストが `override_during = plan` を使うため、Terraform `>= 1.11.0` が必要です。
