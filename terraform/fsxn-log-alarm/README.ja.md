# fsxn-log-alarm (Terraform)

🌐 **日本語** | [English](README.md)

`shared/templates/cloudwatch-log-alarm.yaml` の Terraform 版です。Amazon FSx for NetApp ONTAP のファイルシステム 1 つの EMS と監査のイベントを受け取る CloudWatch Logs のロググループの内容に対してアラームを設定します。[監視設計](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)（[English](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md)）の Terraform 計画のフェーズ T3 にあたり、そのページのメトリクスカタログとアラート設計を実装します。

## 検証状況

オフラインの検証では、`make terraform` が `terraform fmt -check`、`terraform init -lockfile=readonly`、`terraform validate`、`terraform test`（モックの `aws` プロバイダーと `command = plan` による）を実行します。[`examples/basic/`](examples/basic/) に対しても `init` と `validate` を実行します。AWS の認証情報は不要で、何も作りません。テストは、`autosize-fail` のレシピが `wafl.vol.autoSize.fail` を含むパターンのフィルターと、1 回の発生で発火するように配線したアラームを作ること、`failed-access`・`privileged-operations`・`bulk-delete` が実際の監査ログの行で確かめたパターンを同梱していること、そして各検知でアラームが自分のフィルターの発行するメトリクスの名前と名前空間を読むことを確認します。

実環境では、2026-10-09 に `50f1f18` で 1 回のサンプル実行を行いました（[記録](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-09-の-terraform-ログアラームモジュールの実行)）。対象は第 1 世代・HA ペア 1 つのファイルシステム 1 つで、その管理監査ログは syslog VPC エンドポイント経路のポート 1514 で CloudWatch Logs に届いていました。期間 60 秒、評価期間 1 のうち 1 で、`terraform apply` はメトリクスフィルター 6 本とアラーム 6 本を作り、すべてのアラームが INSUFFICIENT_DATA を抜け、`bulk-delete`（`50f1f18` で同梱していたパターン）、`privileged-operations`（いまの既定値になったパターン）、`failed-access-rest403`（追加した `"Error: not authorized"` の検知で、いまの `failed-access` の既定値）は、最初に一致した操作から 13–74 秒後に OK から ALARM に遷移し、一致の無い後の 1 分で OK に戻りました。使ったパターンはすべて AWS に受け付けられました（その範囲で confidence: `verified`）。リソースの作成とアラームの配線には欠陥は見つかりませんでした。同梱の既定のパターンのうち 2 つは欠陥で、実行の後に 3 つの既定値を置き換えました。

- `privileged-operations`: `50f1f18` での既定値 `"admin"` は実際の 5,156 行中 5,150 行に一致しました。ファイルシステム自身の管理の通信（ユーザー `fsx-control-plane`、ロール `admin`）と、すべての `fsxadmin` の行がこの語を含むためです。閾値 0 では、操作する人がいなくてもアラームは ALARM のままでした。既定値はいま `"fsxadmin:fsxadmin" -"Pending"` です。完了した `fsxadmin` の操作 1 回につき 1 行に一致し（5,156 行中 13 行）、実行では実際のアラームを遷移させました。`fsxadmin:fsxadmin` は、監視するユーザーの `<user>:<role>` の文字列に置き換えてください。
- `failed-access`: `50f1f18` での既定値は、実際の REST の認可の失敗 5 件のどれにも一致しませんでした。その行は `Error: not authorized for that command` で終わります。既定値はいま `"Error: not authorized"` です。5 件すべてに、拒否された要求 1 回につき 1 行で一致し、実行では実際のアラームを遷移させました。数えるのは認可による拒否だけです。誤ったパスワードによる REST の要求 1 回（HTTP 401）では、観測した 40 秒の間に監査ログの行は書かれなかったため、どちらのパターンもログインの失敗を検知できるとは確認できていません。
- `bulk-delete`: `50f1f18` での既定値は、閾値 2 で REST の削除 4 回に対して想定どおり発報しましたが、変更の操作は 1 回ごとに 2 回（`Pending` と、続いて結果）記録されるため、操作 1 回につき約 2 行を数えていました。既定値はいま、同じ 3 つの語に結果の行（`:: Success` か `:: Error`）でだけ一致し、完了した削除 1 回につき 1 行（5,156 行中 5 行）なので、閾値は操作の回数を数えます。確かめたのは `aws logs test-metric-filter` だけで、実際のアラームでは動かしておらず、取り込んだ削除も REST のものだけです。
- `autosize-fail`: 実際の `wafl.vol.autoSize.fail` イベントでは発報させていません（`unverified`）。syslog のセットアップガイドが転送するのは監査ログだけで、EMS イベントがロググループに届くのは別の EMS 通知の転送先を通したときだけです。その転送先は設定も試験もしていません。パターンは、ONTAP 9.18.1 の EMS リファレンスから組み立てた、ヘッダーの形を仮定した行に一致しました。
- `unauthorized-access`: 実行していません。パターンはプレースホルダーのパスです。

新しい既定値は、2026-10-09 に `aws logs test-metric-filter` で、記録にあるマスク済みのサンプル行と、取り込んだ 5,156 行すべてに対して確かめました。件数は[記録](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#実際のログ行に対するフィルターパターンの一致t3-の実行)にあります。既定の期間 300 秒・閾値・N/M の値、SNS 通知、デプロイ用の IAM ポリシー、第 2 世代や HA ペアが 2 つ以上のファイルシステムは、まだ `unverified` です。配送経路では、ノードの接続が約 4–5 分無通信だった後の最初の操作が、実行中に 3 回失われました（[セットアップガイド](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/syslog-vpce-setup-guide.md#無通信の接続の後の最初の操作の欠落)）。1 行に依存する検知は、その操作を見逃すことがあります。

## 作成されるリソース

- 検知ごとに 1 本の `aws_cloudwatch_log_metric_filter`。`log_group_name` に対して設定し、各検知のログのパターンを `metric_namespace` の件数のメトリクスに変換します。変換は `default_value = "0"` を設定するので、一致のないウィンドウでメトリクスは 0 を報告します。
- 検知ごとに 1 本の `aws_cloudwatch_metric_alarm`。そのメトリクスに対して設定し、`statistic = "Sum"`、`treat_missing_data = "notBreaching"`（テンプレートの `TreatMissingData: notBreaching`）と、その検知の閾値・比較演算子・期間・評価期間・M-out-of-N のデータポイント数を使います。
- `notification_email` が設定され、かつ `alarm_sns_topic_arn` が指定されていないときの `aws_sns_topic` とメールの `aws_sns_topic_subscription`。そのときすべてのアラームが ALARM と OK の両方で通知し、メールが届く前に受信者がサブスクリプションを承認する必要があります。モジュールが作るトピックは既定では暗号化されません（新しいトピックのコンソール既定と同じ）。暗号化するには `sns_kms_master_key_id` を設定します。トピックを作らず呼び出し側が所有する `AlarmSnsTopicArn` を取る CloudFormation テンプレートに合わせるには、代わりに `alarm_sns_topic_arn` を設定します。そのときモジュールはトピックを作らず、所有・暗号化・サブスクリプション・配信ポリシーを呼び出し側に委ねます。

仕組みはメトリクスフィルターとメトリクスアラームで、ネイティブのログアラームのリソースではありません。CloudFormation テンプレートは `AWS::CloudWatch::LogAlarm` を使い、スケジュール実行する CloudWatch Logs Insights のクエリに対して直接アラームを設定します。固定している `hashicorp/aws` 6.67.0 には同等のリソースがありません（`aws_cloudwatch_log_alarm` もスケジュールクエリのリソースもない）。[プロバイダーの v6.67.0 のリソース索引](https://github.com/hashicorp/terraform-provider-aws/tree/v6.67.0/website/docs/r)で確認しました（confidence: `verified-in-repo`）。そのためこのモジュールは [監視設計](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)が挙げる代替手段を使います。トレードオフは [CloudFormation テンプレートとの意図的な差](#cloudformation-テンプレートとの意図的な差)にあります。

## 検知のレシピ

`detections` はマップで、各キーが 1 つのメトリクスフィルターと 1 つのアラームに対応します。既定では 5 つのレシピを同梱します。呼び出し側はレシピを上書きしたり、減らしたり、テンプレートの `custom` タイプのためにキーを追加したりできます。パターンは [CloudWatch Logs のフィルターパターン](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html)の文字列（イベントのどこかに一致する引用符付きの部分文字列。`?"a" ?"b"` は OR）で、Logs Insights のクエリではありません。

| キー | テンプレートの `DetectionType` との対応 | フィルターパターン | 閾値 / N / M |
|---|---|---|---|
| `autosize-fail` | T3 で新規: EMS `wafl.vol.autoSize.fail` | `"wafl.vol.autoSize.fail"` | 0 / 1 / 1 |
| `failed-access` | `failed-access-attempts` | `"Error: not authorized"` | 10 / 3 / 3（変更なし、未実行。下を参照） |
| `bulk-delete` | `bulk-delete-operations` | `%DELETE.*::\sSuccess\|DELETE.*::\sError\|delete.*::\sSuccess\|delete.*::\sError\|remove.*::\sSuccess\|remove.*::\sError%` | 50 / 3 / 2（変更なし、未実行。下を参照） |
| `privileged-operations` | `specific-user-activity` | `"fsxadmin:fsxadmin" -"Pending"`（監視する `<user>:<role>` に置き換える） | 0 / 3 / 1 |
| `unauthorized-access` | `sensitive-file-access` | `"/vol/data/confidential"`（パスに置き換える） | 0 / 3 / 1 |

監査される ONTAP の変更の操作は、1 回ごとに 2 行を書きます。`:: Pending` で終わる行と、結果（`:: Success:` か `:: Error: ...`）で終わる行です。`failed-access`・`privileged-operations`・`bulk-delete` の既定値は結果の行にだけ一致するので、閾値は操作の回数を数えます。これらは 2026-10-09 の実行の後に、`?"Failure" ?"denied" ?"DENIED"`、`"admin"`、`?"DELETE" ?"delete" ?"remove"` を置き換えたものです（[検証状況](#検証状況)）。閾値と N/M の値はパターンと一緒には変えておらず、実行もしていません。2026-10-09 の実行では、期間 60 秒、1 のうち 1 を使いました。以前の `bulk-delete` のパターンは削除 1 回ごとに両方の行を数えていたため、閾値 50 には REST の削除約 25 回で達していました。新しいパターンでは、同じ 50 が完了した削除 50 回を意味します。`failed-access` が数えるのは、そのコマンドの権限がないとして ONTAP が拒否した要求で、ログインの失敗ではありません。フィルターパターンの OR の語は除外と組み合わせられないため、`bulk-delete` は正規表現のパターンです。CloudWatch Logs では、正規表現のパターンを持つメトリクスフィルターとサブスクリプションフィルターは、ロググループごとに 5 本までです（[フィルターパターンの構文](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html)）。`"delete"` と `"remove"` は部分文字列なので、その語を含む完了したコマンドはどれも数えられます。

`autosize-fail` はこのフェーズが明示的に挙げる EMS イベントです。severity は `error` で、空き容量の枯渇が差し迫っています（[ems-detection-capabilities.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/ems-detection-capabilities.md)）。[ONTAP 9.18.1 の EMS リファレンス](https://docs.netapp.com/us-en/ontap-ems-9181/wafl-vol-events.html)は severity を NOTICE としており、2 つの出典の食い違いはまだ解消していません。1 回の発生で発火します。テンプレートの `custom` タイプに対応するキーを同梱していないのは、`detections` を呼び出し側で拡張できるためです。新しいキーの下に `{ pattern = "..." }` を追加してください。

## モジュールの取得方法

予定しているタグは `terraform-fsxn-log-alarm-v0.1.0` ですが、**まだ作成していません**。作成の時期は決めていません。既定のパターンは 2026-10-09 の実行の後に置き換えており、新しい `bulk-delete` のパターンは実際のアラームでは動かしていません。それまでは、下の git ソースかアーカイブ URL でコミット SHA を固定してください。モジュールは大きなリポジトリのサブディレクトリなので、Terraform Registry には登録されていません。このモジュールについてはダウンロード量を測っていません。同じリポジトリでの実測は [ダッシュボードのモジュールの README](../fsxn-monitoring-dashboard/README.ja.md#モジュールの取得方法) にあります。コードブロック内のコメントは英語のままで、上から順に「コミットに固定した git ソース」「コミットに固定したアーカイブ URL（git 不要）」という意味です。

```hcl
# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-log-alarm?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-log-alarm"
```

下の手順は、sparse checkout でモジュールのディレクトリだけをコミットで取り出します。その後 `source` をこのコピー内の `terraform/fsxn-log-alarm` のローカルパスにします。タグを作った後は、`<commit-sha>` の代わりにタグを使ってください。

```bash
git init fsx-ontap-log-alarm && cd fsx-ontap-log-alarm
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-log-alarm
git fetch --depth 1 --filter=blob:none origin <commit-sha>
git checkout FETCH_HEAD
```

## 使い方

以下の節は、初回のデプロイの順に並んでいます。前提条件、権限、入力値、デプロイ、適用後の確認、削除の順です。

### 前提条件

- Terraform `>= 1.11.0`。
- `hashicorp/aws` `>= 6.67.0`。6.67.0 で確認済み。6.0 から 6.66 のリリースは未確認です。
- IAM Identity Center か IAM ロールの AWS 認証情報と、`provider "aws"` ブロックに設定したロググループのリージョン。モジュールに `provider` ブロックはありません。
- ファイルシステムの EMS と監査のイベントを既に受け取っている CloudWatch Logs のロググループ。このモジュールはそのグループを読むだけで、グループや配送経路は作りません。そのグループの名前を `log_group_name` に渡します。[syslog-vpce-setup-guide.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/syslog-vpce-setup-guide.md) が設定するのは syslog の VPC エンドポイントと ONTAP の監査ログの転送先だけで、グループが受け取るのはコマンド履歴（`kern_audit`）だけです。EMS イベントは届きません。`autosize-fail` には、同じエンドポイントとロググループへ送る ONTAP の EMS 通知の転送先を別に設定する必要があります。その手順はここでは文書化しておらず、`unverified` です。

### 必要な IAM 権限（推定、未検証）

`terraform apply` を実行する ID に必要な権限で、モジュールが作るリソースから導いたものです。この権限だけを持つロールでは実行していません。タグのアクションが抜けやすいことは、ダッシュボードのモジュールで分かっています（[その CloudTrail に関する補足](../fsxn-monitoring-dashboard/README.ja.md#必要な-iam-権限確認済み)）。初回の apply の後でアクションを追加することを見込んでください。

| リソース | アクション |
|---|---|
| `aws_cloudwatch_log_metric_filter`（アカウントのロググループに絞る） | `logs:PutMetricFilter`, `logs:DeleteMetricFilter`, `logs:DescribeMetricFilters` |
| `aws_cloudwatch_metric_alarm` | `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, `cloudwatch:DeleteAlarms`, `cloudwatch:ListTagsForResource`, `cloudwatch:TagResource`, `cloudwatch:UntagResource` |
| `aws_sns_topic`、`aws_sns_topic_subscription`（モジュールがトピックを作るときだけ） | `sns:CreateTopic`, `sns:GetTopicAttributes`, `sns:SetTopicAttributes`, `sns:ListTagsForResource`, `sns:TagResource`, `sns:UntagResource`, `sns:DeleteTopic`, `sns:Subscribe`, `sns:GetSubscriptionAttributes`, `sns:Unsubscribe` |

ポリシーは [`examples/basic/iam-policy.json`](examples/basic/iam-policy.json) にあります。`Alarms` と `Topic` のステートメントは、モジュールが `name_prefix` から作る名前（既定では `fsxn-log-alarm-*`）に絞っています。使う前に `123456789012`、`ap-northeast-1` とプレフィックスを置き換えてください。`Filters` のステートメントはアカウントのロググループ（`log-group:*`）に絞っています。ロググループは呼び出し側が渡す入力で、プレフィックスから作る名前ではないためです。必要なら自分のロググループの ARN に狭めてください。メトリクスフィルターの 3 つのアクションは、`logs:DescribeMetricFilters` も含めて、[サービス認可リファレンス](https://docs.aws.amazon.com/service-authorization/latest/reference/list_logs.html)でロググループのリソースタイプを取ります（confidence: `documented`、2026-10-09 に同ページで確認）。既存の `alarm_sns_topic_arn` を使うときは `Topic` のステートメントは不要です。[適用後の確認手順](#適用後の確認手順)の読み取り専用のコマンドは、自分の認証情報で実行するものです。

### examples/basic からのデプロイ手順

次のコマンドは、`terraform/` を含むディレクトリ（リポジトリ全体の clone か、上の sparse checkout）で実行します。`terraform.tfvars` では、必須の値と必要な任意の入力値を編集します（コードブロック内の英語のコメントは「リージョン、log_group_name、必要な任意の入力値を編集する」という意味です）。

```bash
cd terraform/fsxn-log-alarm/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: region, log_group_name, and any optional inputs
terraform init
terraform plan
terraform apply
```

自分のルート構成では [`examples/basic/`](examples/basic/) をコピーし、`source = "../.."` を [モジュールの取得方法](#モジュールの取得方法) のいずれかのソースに置き換えます。

```hcl
module "fsx_ontap_log_alarm" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-log-alarm?ref=<commit-sha>"

  log_group_name     = "/syslog/fsxn-admin-audit"
  notification_email = "ops@example.com"

  # Omit detections to use the five shipped recipes, or override and extend:
  detections = {
    autosize-fail = { pattern = "\"wafl.vol.autoSize.fail\"" }
    my-custom     = { pattern = "\"ERROR\"", threshold = 0 }
  }
}
```

### 適用後の確認手順

`fsxn-log-alarm` は自分の `name_prefix` に、`/syslog/fsxn-admin-audit` は自分のロググループに置き換えます。

```bash
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-log-alarm \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table
aws logs describe-metric-filters --log-group-name /syslog/fsxn-admin-audit \
  --query 'metricFilters[].{Name:filterName,Pattern:filterPattern}' --output table
```

アラームは、評価期間に必要なデータポイントがそろうまで INSUFFICIENT_DATA のままです。`treat_missing_data = "notBreaching"` により、一致のないウィンドウは not breaching として数えます。2026-10-09 の実行では、期間 60 秒で、すべてのアラームが `apply` から約 2 分以内に INSUFFICIENT_DATA を抜けました。ログの行がまったく届かないウィンドウにはデータポイントがありません。`default_value` が出るのは、届いて一致しなかった行に対してだけだからです。`notification_email` を指定した場合、メールのサブスクリプションは受信者が承認するまで保留のままです。

### 削除手順

```bash
terraform destroy
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-log-alarm \
  --query 'MetricAlarms[].AlarmName' --output text
```

2 つ目のコマンドは、すべてのアラームが消えれば何も出力しないはずです。

## 入力

| 名前 | 型 | 既定値 | 補足 |
|---|---|---|---|
| `log_group_name` | string | 必須 | `^[A-Za-z0-9_./#-]{1,512}$`（テンプレートの `LogGroupName`） |
| `name_prefix` | string | `"fsxn-log-alarm"` | リソース名でスタック名の役割を果たす |
| `metric_namespace` | string | `"FSxONTAP/LogAlarm"` | 件数のメトリクスの名前空間 |
| `notification_email` | string | `""` | 設定し `alarm_sns_topic_arn` が空のとき、モジュールがトピックを作り登録する |
| `alarm_sns_topic_arn` | string | `""` | 既存の呼び出し側所有のトピック（テンプレートの `AlarmSnsTopicArn`）。設定するとトピックを作らない |
| `sns_kms_master_key_id` | string | `""` | モジュールが作るトピックの KMS キー。空なら暗号化しない |
| `detections` | map(object) | 5 つのレシピ | キーごとに 1 本のフィルターと 1 本のアラーム。[検知のレシピ](#検知のレシピ)を参照 |
| `tags` | map(string) | `{}` | アラームと SNS トピック。メトリクスフィルターはタグを取らない |

各 `detections` の値はオブジェクトです。`pattern`（必須）、`threshold`（0）、`comparison_operator`（`GreaterThanThreshold`）、`evaluation_periods`（1）、`datapoints_to_alarm`（1）、`period_seconds`（300）、`metric_value`（`"1"`）、`alarm_description`（`""`。空なら生成）。`period_seconds` は 60、300、600、900、1800、3600 のいずれか（テンプレートの `EvaluationFrequencyMinutes` の集合を秒にしたもの）です。`evaluation_periods`（N）と `datapoints_to_alarm`（M）はそれぞれ 1 から 100 の整数で（API の最小値は 1、テンプレートは両方を 100 で上限）、M は N 以下です。`name_prefix-detectionkey` を連結した名前は、CloudWatch のアラーム名とメトリクス名の上限である 255 文字以内に収める必要があります。

## 出力

| 名前 | 説明 |
|---|---|
| `metric_filter_names`、`alarm_names`、`alarm_arns` | 検知をキーとしたマップ |
| `metric_namespace` | フィルターが発行する名前空間 |
| `sns_topic_arn` | 実際のトピックの ARN（既存または作成）。どちらも設定しなければ `null` |
| `sns_topic_created` | モジュールがトピックを所有すれば `true`、既存トピックまたはなしなら `false` |

## CloudFormation テンプレートとの意図的な差

テンプレートは `AWS::CloudWatch::LogAlarm` を使い、このモジュールはメトリクスフィルターとメトリクスアラームを使います。固定したプロバイダーにネイティブのログアラームのリソースがないためです。トレードオフは対称です。

- メトリクスフィルターとメトリクスアラーム（このモジュール）はスケジュールクエリの実行ロールもログ行用のロールも要りません。件数のメトリクスはダッシュボードや異常検知で使え、`period` に従って継続的に評価します。通知に一致したログ行を含められず（テンプレートの `ActionLogLineCount` に対応するものがない）、一致はフィルターパターンの語であって Logs Insights のクエリ構文（集計や時間の計算）ではなく、フィルターの作成後に取り込まれたイベントだけを数え、過去にはさかのぼりません。
- LogAlarm（CloudFormation テンプレート）は 1 つのリソースで、完全な Logs Insights のクエリを使い、通知に一致したログ行を最大 50 行含められ、既存のログにさかのぼります。スケジュールクエリの実行ロール（と任意のログ行用のロール）が必要で、固定したプロバイダーにはネイティブの Terraform リソースがありません。

コストに関する補足です。どちらの経路も CloudWatch の個別のメーターで課金されるため、一方を無料とみなさずアカウント単位で比較してください。このモジュールは検知ごとにカスタムメトリクス 1 本とメトリクスアラーム 1 本を作ります。カスタムメトリクスはメトリクス単位の月額、メトリクスアラームはアラーム単位の月額で課金され、メトリクスフィルター自体は発行するメトリクスを超える課金を足しません。LogAlarm はカスタムメトリクスを持ちませんが、スケジュール実行する Logs Insights のクエリが実行ごとにスキャンしたギガバイト数で課金されるため、コストはメトリクスの本数ではなくログ量とクエリ頻度に比例します。どちらの経路を見積もる前も、リージョンごとの現行の料率と無料枠を [Amazon CloudWatch の料金ページ](https://aws.amazon.com/cloudwatch/pricing/)で確認してください（本ドキュメントのために料率を測り直してはいません。最終参照 2026-10-09）。

選び方は次のとおりです。ダッシュボードや異常検知のための件数のメトリクスで、新しい IAM ロールを増やしたくなければ、このモジュール（T3）を使います。アラートに一致したログ行を入れたい、または過去にさかのぼる Logs Insights のクエリが要るなら、CloudFormation の [`cloudwatch-log-alarm.yaml`](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/shared/templates/cloudwatch-log-alarm.yaml) テンプレートを使います。

テンプレートの既定値とのそのほかの差は次のとおりです。`alarm_actions` に加えて `ok_actions` を設定します。`detections` は `DetectionType` の列挙ではなくデータ駆動のマップなので、複数の検知を同時に動かせ、呼び出し側が独自の検知を追加できます。通知については、`alarm_sns_topic_arn` を設定するとテンプレートと完全に同じになります（呼び出し側所有のトピックで、ここでは何も作らない）。`notification_email` を設定すればモジュールがトピックを所有でき、そのときは `sns_kms_master_key_id` が暗号化を制御します。

## プロバイダーの版の制約

モジュールは `hashicorp/aws` を `>= 6.67.0`（確認に使った最低のリリース）で宣言し、上限は付けていません。[`examples/basic/`](examples/basic/) は完全一致のピン `= 6.67.0` と専用の `.terraform.lock.hcl` を持ちます。呼び出し側はこのモジュールの `.terraform.lock.hcl` を使いません。Terraform が読むのはルート構成のロックファイルです（[Dependency Lock File](https://developer.hashicorp.com/terraform/language/files/dependency-lock)）。このモジュールのロックファイルは、このリポジトリの `make terraform` と CI のためだけにあります。テストが `override_during = plan` を使うため、Terraform `>= 1.11.0` が必要です。
