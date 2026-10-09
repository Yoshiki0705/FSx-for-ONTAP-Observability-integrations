# fsxn-ssd-auto-increase (Terraform)

🌐 **日本語** | [English](README.md)

1 つの Amazon FSx for NetApp ONTAP ファイルシステムの SSD 容量を、必須の絶対上限の範囲内で、かつすべてのガードを通過したときにだけ引き上げる VPC 外の Lambda 関数です。[monitoring-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)) の Terraform 計画のフェーズ T4 にあたります。挙動は [capacity-automation-t4-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/capacity-automation-t4-design.md) に規定されています。選択肢の比較と不可逆性の事実は [capacity-automation.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/capacity-automation.md) にあります。

このモジュールは T2 のカスタムメトリクスモジュールとランタイムコードを共有しません。T2 は VPC 内の ONTAP REST ポーラーですが、T4 は AWS API のみを呼び出し VPC 外で動くため、セキュリティグループもインターフェイスエンドポイントも持たず、AZ ごとの ENI 課金もありません。

## 検証状況

オフライン: `make terraform` が `terraform fmt -check`、`terraform init -lockfile=readonly`、`terraform validate`、`terraform test`（モックの `aws` プロバイダと `command = plan`。`archive` プロバイダは実際の Lambda zip をビルドします）を実行し、[`examples/basic/`](examples/basic/) に対して `init` と `validate` も実行します。`shared/lambda/ssd_auto_increase/` の Lambda ソースには、モックした boto3（`fsx`、`cloudwatch`、`sns`、`s3`、`dynamodb`、`logs`）に対する pytest 単体テストがあり、設計のテスト計画のガードとロック状態遷移を扱います。呼び出し直前の 2 回目のスナップショット（両方のガードの再実行）、`accepted` と同じ扱いで保留される呼び出し後のアーカイブ書き込み失敗、冪等な保留分の再送、モードごとのバケット既定保持のマトリクス、ブロック済みレポートの再送、管理アクションのステータスのマトリクス、`GetMetricData` による使用率の取得（ディメンション、レポートとアーカイブのイベントに載る値、値がない場合の分類）、複数回の呼び出しにまたがる連鎖（新しいトークンでの引き継ぎ、上限値到達時の解放、数回の実行にわたる表示の遅れ、`not_accepted` の後に限った新しいトークン）を含みます。これらはモックに対するオフラインテストで、設計のテスト計画の実機の行（実ファイルシステム・バケット・ポリシーシミュレーター）は未実行のままです。

ライブ: 未実行。モジュールは AWS アカウントに適用されていないため、実ファイルシステムに依存するものはすべて `unverified` です。アラーム遷移、デプロイ用 IAM ポリシー、Object Lock の保持期間の証明、1 回の実増加がそれにあたります。ライブ検証ワークフローは、まず可逆な経路（notify_only、`fsx:UpdateFileSystem` を Deny した auto、ロックと上限のチェック、ポリシーシミュレーション）を実行し、1 回だけの不可逆な +10% 増加は明示的な承認待ちにします。その記録ができるまでは、まず非本番アカウントでモジュールを適用してください。

## 作成するもの

- `aws_lambda_function` `<name_prefix>-evaluator`（Python 3.12、256 MB、300 秒タイムアウト、予約済み同時実行数 1、ハンドラ `ssd_auto_increase_handler.lambda_handler`）。VPC 外で動き、`shared/lambda/ssd_auto_increase/` から `data "archive_file"` でパッケージします（テストは除外）。
- `aws_cloudwatch_event_rule`（`reevaluation_schedule`、既定 `rate(1 hour)`）とそのターゲット、`aws_lambda_permission`。CloudWatch のアラームアクションは状態変化時にのみ発火するため、ALARM のままのアラームでは関数が再起動されません。スケジュールはそのために必要です。
- `aws_sqs_queue` `<name_prefix>-dlq`（保持 14 日、`alias/aws/sqs`）を関数のデッドレターキューとして。
- `aws_dynamodb_table` `<name_prefix>-lock`（`PAY_PER_REQUEST`、ハッシュキー `file_system_id`、TTL なし）を単一実行ロックのストアとして。`expires_at` はリース取り直しの比較値で、関数が現在時刻と比較して判定します。テーブルに TTL はないため、持続状態（`submitted`、`optimizing`、`indeterminate`、`manual_disposition_required`、`blocked`）が設計上の解放より前にサービスに削除されることはありません。
- `aws_cloudwatch_log_group` `/fsx/ssd-auto-increase/<file-system-id>`（`log_retention_days`）を決定ログとして。これは運用履歴であり監査記録ではありません。
- `aws_cloudwatch_log_group` `/aws/lambda/<name_prefix>-evaluator`（`log_retention_days`）を関数自身のロググループとして。Lambda の既定（無期限）に任せず、モジュールが作成して保持期間を管理します。
- `aws_iam_role` `<name_prefix>-role` とインラインポリシー（下の表）。
- `aws_sns_topic` `<name_prefix>-trigger`（Lambda サブスクリプションが 1 つだけで、アラームはこれを通じて関数を起動）と、レポートと approve のメール用の `<name_prefix>-notify`（`notification_email` を設定したときだけメールサブスクリプション）。関数はトリガートピックに発行しないため、レポートが関数を再起動することはありません。
- `aws_cloudwatch_metric_alarm` `<name_prefix>-ssd-utilization`（`AWS/FSx` `StorageCapacityUtilization`、`StorageTier=SSD`、`DataType=All`）と、第 2 世代では `aggregate_names` の各要素ごとに 1 つ。

決定アーカイブの S3 バケットは**作成しません**。これはモジュール外で管理される既存の Object Lock バケット（`decision_archive_bucket`）です。関数はプレフィックスへの `s3:PutObject` と読み取り専用の保持期間チェックだけを得て、保持期間を設定・変更しません。コンプライアンスモードの保持期間は短縮できないため、運用者が所有する長期の約束になります。

## ガード

| ガード | 挙動 |
|---|---|
| 上限 | `max_storage_capacity_gib` は必須の絶対上限で、デプロイ時（変数検証とシェイプ最大値に対する precondition）と実行時（`DescribeFileSystems` に対する再チェック）に検証されます |
| モード | `notify_only`（既定）は計算して通知、`approve` はコマンドをメール、`auto` は API を呼び出し。`auto` は `decision_archive_required_mode = COMPLIANCE` を要求します |
| アラーム状態 | 各起動で `DescribeAlarms` によりトリガーアラームを読み、少なくとも 1 つが ALARM のときだけ動作します |
| ターゲット | `target = min(ceiling, max(ceil(current × 1.10), ceil(current × (1 + increase_percent / 100))))`。上限が 10% の最小増加の余地を残さないときは呼び出しません |
| 管理アクション | `FILE_SYSTEM_UPDATE` が稼働中、または `STORAGE_OPTIMIZATION` が未完了の間は呼び出さず、呼び出しの直前にも再読します |
| クールダウン | 直近の SSD・IOPS・スループット変更が 6 時間未満のときは延期します |
| IOPS モード | `AUTOMATIC`: IOPS 引数なし。`USER_PROVISIONED`: `Iops = max(current, 3 × target)`。リージョン最大値を超えると `blocked` にラッチします |
| 単一実行 | ファイルシステム ID をキーにした DynamoDB の条件付き書き込みと、関数の予約済み同時実行数 1 |
| 監査記録 | イベントごとに S3 Object Lock オブジェクト 1 つ。保持期間を実行時に証明し、`auto` ではフェイルクローズ、`notify_only` と `approve` ではギャップを報告してフェイルオープンします |

> **プラン時に関する補足**
>
> プロバイダがすべてのデプロイタイプについてプラン時に `ha_pairs` を読むかは `open` です（設計の「上限に関する補足」）。precondition が評価できなかった場合は Lambda の実行時の上限再チェックが補うため、再チェックは常に残します。

## モジュールの入手

計画中のタグは `terraform-fsxn-ssd-auto-increase-v0.1.0` です。**まだ作成されていません**。[検証状況](#検証状況)のライブ実行の後に作る計画です。それまではコミット SHA で git ソースか下のアーカイブ URL をピン留めしてください。モジュールは大きなリポジトリのサブディレクトリなので Terraform Registry にはありません。

Lambda ソースはモジュールディレクトリの外、`shared/lambda/ssd_auto_increase/` にあります。`//subdirectory` ソースでは Terraform がパッケージ全体をダウンロードして展開し、サブディレクトリからモジュールを読む ([module block reference](https://developer.hashicorp.com/terraform/language/block/module)) ため、下のどちらのソースでも `../../shared` が解決します。

```hcl
# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ssd-auto-increase?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-ssd-auto-increase"
```

スパースチェックアウトは両方のディレクトリを含める必要があります。さもないと `shared/lambda/ssd_auto_increase` が存在せず `archive_file` がプラン時に失敗します。

```bash
git init fsx-ssd-auto-increase && cd fsx-ssd-auto-increase
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-ssd-auto-increase shared/lambda/ssd_auto_increase
git fetch --depth 1 --filter=blob:none origin <commit-sha>
git checkout FETCH_HEAD
```

## 使い方

以下の節は初回デプロイの順序に従います。前提、権限、入力値、デプロイ、削除です。

### 前提

- Terraform `>= 1.11.0`、`hashicorp/aws` `>= 6.67.0`、`hashicorp/archive` `>= 2.8.1`（6.67.0 と 2.8.1 で検証）。
- モジュール外で管理される、Object Lock の既定保持期間を持つ既存の S3 バケット。`auto` ではバケットはコンプライアンスモードで少なくとも `decision_archive_min_retention_days` が必要です。`notify_only` と `approve` はガバナンスバケットを受け入れ、保持期間チェックはブロックせずギャップを報告します。
- ファイルシステムのデプロイタイプと HA ペア数は、この設計が列挙するもの（`SINGLE_AZ_1`、`MULTI_AZ_1`、`MULTI_AZ_2`、`SINGLE_AZ_2`）でなければなりません。未知のタイプへのマップ参照は意図的にプランを失敗させます。

> **モードに関する補足**
>
> `mode` は既定で `notify_only` なので、モジュールをデプロイしてもファイルシステムは何も変わりません。決定を観察してから `approve`、`auto` へ進めてください。SNS のメールサブスクリプションは受信者が確認するまで保留のままなので、`approve` を頼る前に確認してください。

### 必要な IAM 権限（推定、未検証）

`terraform apply` を実行する ID の権限で、モジュールが作成するリソースから導いたものです。これらに限定したロールでは実行していないため、初回の apply のあとにアクションを追加することを見込んでください。

| リソース | アクション |
|---|---|
| `aws_lambda_function`、`aws_lambda_permission` | `lambda:CreateFunction`, `lambda:GetFunction`, `lambda:GetFunctionConfiguration`, `lambda:UpdateFunctionCode`, `lambda:UpdateFunctionConfiguration`, `lambda:PutFunctionConcurrency`, `lambda:DeleteFunctionConcurrency`, `lambda:DeleteFunction`, `lambda:AddPermission`, `lambda:RemovePermission`, `lambda:GetPolicy`, `lambda:ListVersionsByFunction`, `lambda:GetFunctionCodeSigningConfig`, `lambda:TagResource`, `lambda:UntagResource`, `lambda:ListTags` |
| `aws_iam_role` とそのポリシー | `iam:CreateRole`, `iam:GetRole`, `iam:DeleteRole`, `iam:PassRole`, `iam:PutRolePolicy`, `iam:GetRolePolicy`, `iam:DeleteRolePolicy`, `iam:ListRolePolicies`, `iam:ListAttachedRolePolicies`, `iam:ListInstanceProfilesForRole`, `iam:TagRole`, `iam:UntagRole` |
| `aws_sqs_queue` | `sqs:CreateQueue`, `sqs:GetQueueAttributes`, `sqs:SetQueueAttributes`, `sqs:DeleteQueue`, `sqs:TagQueue`, `sqs:UntagQueue`, `sqs:ListQueueTags` |
| `aws_dynamodb_table` | `dynamodb:CreateTable`, `dynamodb:DescribeTable`, `dynamodb:DeleteTable`, `dynamodb:UpdateTimeToLive`, `dynamodb:DescribeTimeToLive`, `dynamodb:TagResource`, `dynamodb:UntagResource`, `dynamodb:ListTagsOfResource` |
| `aws_cloudwatch_log_group`（アカウントのロググループにスコープ） | `logs:CreateLogGroup`, `logs:DeleteLogGroup`, `logs:PutRetentionPolicy`, `logs:ListTagsForResource`, `logs:TagResource`, `logs:UntagResource` |
| `aws_cloudwatch_event_rule`、`aws_cloudwatch_event_target` | `events:PutRule`, `events:DescribeRule`, `events:DeleteRule`, `events:PutTargets`, `events:RemoveTargets`, `events:ListTargetsByRule`, `events:ListTagsForResource`, `events:TagResource`, `events:UntagResource` |
| `aws_cloudwatch_metric_alarm` | `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, `cloudwatch:DeleteAlarms`, `cloudwatch:ListTagsForResource`, `cloudwatch:TagResource`, `cloudwatch:UntagResource` |
| `aws_sns_topic`、`aws_sns_topic_subscription` | `sns:CreateTopic`, `sns:GetTopicAttributes`, `sns:SetTopicAttributes`, `sns:ListTagsForResource`, `sns:TagResource`, `sns:UntagResource`, `sns:DeleteTopic`, `sns:Subscribe`, `sns:GetSubscriptionAttributes`, `sns:Unsubscribe` |
| プラン時のファイルシステム読み取り（`Resource: "*"`） | `fsx:DescribeFileSystems` |

ポリシーは [`examples/basic/iam-policy.json`](examples/basic/iam-policy.json) にあります。スコープ付きステートメントはモジュールが `name_prefix` から作る名前（既定 `fsxn-ssd-auto-increase-*`）を使います。使う前に `123456789012`、`ap-northeast-1`、プレフィックスを置き換えてください。`fsx:DescribeFileSystems` は [サービス認可リファレンス](https://docs.aws.amazon.com/service-authorization/latest/reference/list_fsx.html) がリソースタイプなしで載せているため `Resource: "*"` に置きます。デプロイ側は `fsx:UpdateFileSystem` を呼びません。呼ぶのは関数の実行ロールだけで、モジュールはその付与を呼び出し側が指定する 1 つのファイルシステム ARN（`arn:aws:fsx:...:file-system/fs-...`）にスコープします。`logs` ステートメントは決定ロググループが `name_prefix` ではなくファイルシステム ID から作られるため、アカウントのロググループにスコープします。

### examples/basic からのデプロイ

`terraform/` を含むディレクトリ（フルクローンか上のスパースチェックアウト）から実行します。`terraform.tfvars` に必須値と任意の入力を設定します。

```bash
cd terraform/fsxn-ssd-auto-increase/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: region, file_system_id, max_storage_capacity_gib, bucket
terraform init
terraform plan
terraform apply
```

自分のルート構成では [`examples/basic/`](examples/basic/) をコピーし、`source = "../.."` を [モジュールの入手](#モジュールの入手) のソースのいずれかに置き換えます。

```hcl
module "ssd_auto_increase" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ssd-auto-increase?ref=<commit-sha>"

  file_system_id           = "fs-0123456789abcdef0"
  max_storage_capacity_gib = 2048
  decision_archive_bucket  = "<object-lock-bucket-name>"
}
```

### 削除

```bash
terraform destroy
```

決定アーカイブのバケットはこのモジュールの外にあり、削除されません。コンプライアンスモードのバケットのオブジェクトは保持期限が過ぎるまで削除できません。

## 入力

| 名前 | 型 | 既定 | 備考 |
|---|---|---|---|
| `file_system_id` | string | 必須 | `^fs-[0-9a-f]{17}$` |
| `max_storage_capacity_gib` | number | 必須 | 1024〜1048576 の整数。シェイプ最大値に対して検証 |
| `mode` | string | `notify_only` | `notify_only` / `approve` / `auto`。`auto` は COMPLIANCE を要求 |
| `trigger_threshold_percent` | number | `80` | 1〜100 |
| `increase_percent` | number | `10` | 10〜100 の整数。10% の最小値を下回りません |
| `reevaluation_schedule` | string | `rate(1 hour)` | `rate(...)` または `cron(...)` |
| `log_retention_days` | number | `365` | CloudWatch Logs が受け付ける値 |
| `indeterminate_reconcile_hours` | number | `6` | 1〜24 の整数 |
| `decision_archive_bucket` | string | 必須 | 既存の Object Lock バケット名 |
| `decision_archive_prefix` | string | `fsx-ssd-auto-increase/` | アーカイブオブジェクトのキープレフィックス |
| `decision_archive_required_mode` | string | `COMPLIANCE` | `COMPLIANCE` / `GOVERNANCE` |
| `decision_archive_min_retention_days` | number | `365` | 必要な最小保持日数 |
| `aggregate_names` | list(string) | `[]` | 第 2 世代の Aggregate 名。それぞれにトリガーアラーム 1 つ |
| `notification_email` | string | `""` | 空ならメールサブスクリプションを省略 |
| `name_prefix` | string | `"fsxn-ssd-auto-increase"` | 1〜48 文字。スタック名の役割 |
| `tags` | map(string) | `{}` | すべてのタグ付け可能なリソース |

## 出力

| 名前 | 説明 |
|---|---|
| `lambda_function_name`、`lambda_function_arn`、`lambda_role_arn` | 関数と実行ロール |
| `trigger_topic_arn`、`notification_topic_arn` | 2 つの SNS トピック |
| `lock_table_name`、`lock_table_arn` | DynamoDB 単一実行ロックテーブル |
| `decision_log_group_name` | 決定ロググループ（運用履歴） |
| `dead_letter_queue_url`、`dead_letter_queue_arn` | 失敗した起動の DLQ |
| `schedule_rule_arn` | EventBridge スケジュールルール |
| `trigger_alarm_arns` | file-system と aggregate/<name> をキーにしたトリガーアラーム ARN |
| `config_fingerprint` | 上限・増加率・モード・アーカイブモードのハッシュ |
