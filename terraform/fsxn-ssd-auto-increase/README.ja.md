# fsxn-ssd-auto-increase (Terraform)

🌐 **日本語** | [English](README.md)

1 つの Amazon FSx for NetApp ONTAP ファイルシステムの SSD 容量を、必須の絶対上限の範囲内で、かつすべてのガードを通過したときにだけ引き上げる VPC 外の Lambda 関数です。[monitoring-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md) ([日本語](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)) の Terraform 計画のフェーズ T4 にあたります。挙動は [capacity-automation-t4-design.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/capacity-automation-t4-design.md) に規定されています。選択肢の比較と不可逆性の事実は [capacity-automation.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/capacity-automation.md) にあります。

このモジュールは T2 のカスタムメトリクスモジュールとランタイムコードを共有しません。T2 は VPC 内の ONTAP REST ポーラーですが、T4 は AWS API のみを呼び出し VPC 外で動くため、セキュリティグループもインターフェイスエンドポイントも持たず、AZ ごとの ENI 課金もありません。

## 検証状況

オフライン: `make terraform` が `terraform fmt -check`、`terraform init -lockfile=readonly`、`terraform validate`、`terraform test`（モックの `aws` プロバイダと `command = plan`。`archive` プロバイダは実際の Lambda zip をビルドします）を実行し、[`examples/basic/`](examples/basic/) に対して `init` と `validate` も実行します。`shared/lambda/ssd_auto_increase/` の Lambda ソースには、モックした boto3（`fsx`、`cloudwatch`、`sns`、`s3`、`dynamodb`、`logs`）に対する pytest 単体テストがあり、設計のテスト計画のガードとロック状態遷移を扱います。呼び出し直前の 2 回目のスナップショット（両方のガードの再実行）、`accepted` と同じ扱いで保留される呼び出し後のアーカイブ書き込み失敗、冪等な保留分の再送、モードごとのバケット既定保持のマトリクス、ブロック済みレポートの再送、管理アクションのステータスのマトリクス、`GetMetricData` による使用率の取得（ディメンション、レポートとアーカイブのイベントに載る値、値がない場合の分類）、複数回の呼び出しにまたがる連鎖（新しいトークンでの引き継ぎ、上限値到達時の解放、数回の実行にわたる表示の遅れ、`not_accepted` の後に限った新しいトークン）を含みます。これらはモックに対するオフラインテストで、設計のテスト計画の実機の行（実ファイルシステム・バケット・ポリシーシミュレーター）は未実行のままです。

ライブ: 2026-10-09 に、第 1 世代 `SINGLE_AZ_1`、HA ペア 1 つ、SSD ストレージ 1,024 GiB のファイルシステム 1 つに、既定の保持期間 1 日のコンプライアンスモードのアーカイブバケットを使ってモジュールを適用しました（[記録](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-09-の-terraform-ssd-自動拡張モジュールの実行)）。そこで検証したのは、実際の OK → ALARM の遷移での `notify_only`、`approve`、アラームが OK のときの分岐、`fsx:UpdateFileSystem` への明示的な IAM の拒否の後ろでの `auto`（拒否される呼び出し 1 回、1 回だけ報告される `blocked` のラッチ、次の実行での沈黙、オペレーターによる解除）、同時の 2 回の呼び出しから最大 1 回の呼び出し、リースの競合と期限切れのリースの引き継ぎ、デプロイ時の `auto` + `GOVERNANCE` の事前条件、バケットの保持期間が短すぎる場合と intent の書き込みが拒否された場合の実行時の `archive_retention_unproven`、読んだアーカイブのバージョンのコンプライアンスモードでの保持、実行ロールの IAM ポリシーのシミュレーションです。関数が呼んだ `UpdateFileSystem` は 4 回ですべて拒否され、容量は変わっていません。`unverified` のまま残るのは、1 回の実際の拡張とその後のクールダウン、第 2 世代と Aggregate のアラーム、メールの配信、デプロイ用 IAM ポリシー（実行では管理者権限を使用）、判断アーカイブのテストの陽性対照です。設計の記述と違う挙動が 3 つあり（記録の F1–F3）、[デプロイのテストと運用](#デプロイのテストと運用)に記載しています。まず非本番アカウントでモジュールを適用してください。

## 作成するもの

- `aws_lambda_function` `<name_prefix>-evaluator`（Python 3.12、256 MB、300 秒タイムアウト、予約済み同時実行数 1、ハンドラ `ssd_auto_increase_handler.lambda_handler`）。VPC 外で動き、`shared/lambda/ssd_auto_increase/` から `data "archive_file"` でパッケージします（テストは除外）。
- `aws_cloudwatch_event_rule`（`reevaluation_schedule`、既定 `rate(1 hour)`）とそのターゲット、`aws_lambda_permission`。CloudWatch のアラームアクションは状態変化時にのみ発火するため、ALARM のままのアラームでは関数が再起動されません。スケジュールはそのために必要です。
- `aws_sqs_queue` `<name_prefix>-dlq`（保持 14 日、`alias/aws/sqs`）を関数のデッドレターキューとして。
- `aws_dynamodb_table` `<name_prefix>-lock`（`PAY_PER_REQUEST`、ハッシュキー `file_system_id`、TTL なし）を単一実行ロックのストアとして。`expires_at` はリース取り直しの比較値で、関数が現在時刻と比較して判定します。テーブルに TTL はないため、持続状態（`submitted`、`optimizing`、`indeterminate`、`manual_disposition_required`、`blocked`）が設計上の解放より前にサービスに削除されることはありません。
- `aws_cloudwatch_log_group` `/fsx/ssd-auto-increase/<file-system-id>`（`log_retention_days`）を判断ログとして。これは運用履歴であり監査記録ではありません。
- `aws_cloudwatch_log_group` `/aws/lambda/<name_prefix>-evaluator`（`log_retention_days`）を関数自身のロググループとして。Lambda の既定（無期限）に任せず、モジュールが作成して保持期間を管理します。
- `aws_iam_role` `<name_prefix>-role` とインラインポリシー（下の表）。
- `aws_sns_topic` `<name_prefix>-trigger`（Lambda サブスクリプションが 1 つだけで、アラームはこれを通じて関数を起動）と、レポートと approve のメール用の `<name_prefix>-notify`（`notification_email` を設定したときだけメールサブスクリプション）。関数はトリガートピックに発行しないため、レポートが関数を再起動することはありません。
- `aws_cloudwatch_metric_alarm` `<name_prefix>-ssd-utilization`（`AWS/FSx` `StorageCapacityUtilization`、`StorageTier=SSD`、`DataType=All`）と、第 2 世代では `aggregate_names` の各要素ごとに 1 つ。

判断アーカイブの S3 バケットは**作成しません**。これはモジュール外で管理される既存の Object Lock バケット（`decision_archive_bucket`）です。関数はプレフィックスへの `s3:PutObject` と読み取り専用の保持期間チェックだけを得て、保持期間を設定・変更しません。コンプライアンスモードの保持期間は短縮できないため、運用者が所有する長期の約束になります。

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

計画中のタグは `terraform-fsxn-ssd-auto-increase-v0.1.0` です。**まだ作成されていません**。[検証状況](#検証状況)の 2026-10-09 のライブ実行では T4 の完了条件のうち判断アーカイブの行が未完了のまま残ったので、その行を記録した後に作る計画です。それまではコミット SHA で git ソースか下のアーカイブ URL をピン留めしてください。モジュールは大きなリポジトリのサブディレクトリなので Terraform Registry にはありません。

Lambda ソースはモジュールディレクトリの外、`shared/lambda/ssd_auto_increase/` にあります。`//subdirectory` ソースでは Terraform がパッケージ全体をダウンロードして展開し、サブディレクトリからモジュールを読む ([module block reference](https://developer.hashicorp.com/terraform/language/block/module)) ため、下のどのソースでも `../../shared` が解決します。

```hcl
# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ssd-auto-increase?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-ssd-auto-increase"
```

次の手順はスパースチェックアウトで、コミットの時点のモジュールのディレクトリを取り出します。その後、このコピーの `terraform/fsxn-ssd-auto-increase` のローカルパスを `source` に指定します。スパースチェックアウトは両方のディレクトリを含める必要があります。さもないと `shared/lambda/ssd_auto_increase` が存在せず `archive_file` がプラン時に失敗します。

```bash
git init fsx-ssd-auto-increase && cd fsx-ssd-auto-increase
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-ssd-auto-increase shared/lambda/ssd_auto_increase
git fetch --depth 1 --filter=blob:none origin <commit-sha>
git checkout FETCH_HEAD
```

> **取得手順に関する補足**
>
> コミット SHA を指定してこの手順を実行し、取り出したコピーの `examples/basic/` で `terraform init -backend=false` と `terraform validate` が成功しました。`?ref=main&depth=1` を指定した git ソースでも、`terraform init` がダウンロードしたコピーにはモジュールの隣に `shared/lambda/ssd_auto_increase/` がありました。そのコピーから `plan` は実行していません。`source` に git ソースを書く場合、[ダッシュボードのモジュールの README](../fsxn-monitoring-dashboard/README.ja.md#モジュールの取得方法) に記録したとおり、SHA と `depth=1` は組み合わせられません。

## 使い方

以下の節は初回デプロイの順序に従います。前提、権限、入力値、デプロイ、テストと運用、削除です。

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

ポリシーは [`examples/basic/iam-policy.json`](examples/basic/iam-policy.json) にあります。スコープ付きステートメントはモジュールが `name_prefix` から作る名前（既定 `fsxn-ssd-auto-increase-*`）を使います。使う前に `123456789012`、`ap-northeast-1`、プレフィックスを置き換えてください。`fsx:DescribeFileSystems` は [サービス認可リファレンス](https://docs.aws.amazon.com/service-authorization/latest/reference/list_fsx.html) がリソースタイプなしで載せているため `Resource: "*"` に置きます。デプロイ側は `fsx:UpdateFileSystem` を呼びません。呼ぶのは関数の実行ロールだけで、モジュールはその付与を呼び出し側が指定する 1 つのファイルシステム ARN（`arn:aws:fsx:...:file-system/fs-...`）にスコープします。`logs` ステートメントは判断ロググループが `name_prefix` ではなくファイルシステム ID から作られるため、アカウントのロググループにスコープします。

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

### デプロイのテストと運用

以下は 2026-10-09 の実行で使った手順です。`examples/basic/` から実行し、プレースホルダーを置き換えてください。

ファイルシステムを変えずにテスト用の決定を起こすには、`mode = "notify_only"` のまま現在の SSD の利用率を読み、`trigger_threshold_percent` をそれより低くします。実行では、閾値の更新の 50 秒後にアラームが実データで OK から ALARM になり、関数を 1 回呼びました。計算できる目標値を最小の拡張量だけにするには、`max_storage_capacity_gib` を `ceil(現在値 × 1.1)`（1,024 GiB なら 1,127 GiB）にします。終わったら閾値を戻します。`aws cloudwatch set-alarm-state` でも動きますが、効くのは次の評価までです。

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/FSx --metric-name StorageCapacityUtilization \
  --dimensions Name=FileSystemId,Value=fs-0123456789abcdef0 Name=StorageTier,Value=SSD Name=DataType,Value=All \
  --start-time 2026-01-01T00:00:00Z --end-time 2026-01-01T01:00:00Z \
  --period 300 --statistics Average
terraform apply -var trigger_threshold_percent=3
# Afterwards
terraform apply -var trigger_threshold_percent=80
```

実際の呼び出しをせずに `auto` を試すには、`mode = "auto"` を適用する前に、実行ロールに `fsx:UpdateFileSystem` への明示的な拒否を付け、`mode` を `notify_only` に戻してから外します。モジュールのロールはこの名前のインラインポリシーを定義しないので、Terraform の plan はこれに触れません。ポリシーシミュレーターは保存されたポリシーを読むので、その `explicitDeny` は変更がすべてのエンドポイントに届いたことを示しません。IAM の変更は結果整合です（[IAM のトラブルシューティング](https://docs.aws.amazon.com/IAM/latest/UserGuide/troubleshoot_general.html#troubleshoot_general_eventual-consistency)）。シミュレーションは、拒否を付けたとき、その 60 秒以上後、`mode = "auto"` を適用する直前の 3 回行ってください。`auto` を適用するのは、すべてのシミュレーションが `explicitDeny` を返したときだけです。リンク先のページは伝播にかかる時間の上限を示していないので、60 秒は実行での運用であって保証ではありません。実行では、2 回目のシミュレーションは 1 回目の 86 秒後で、最初の呼び出しの 4 分前から拒否を付けていて、その呼び出しは拒否されました。第 1 世代では、拒否されなかった呼び出しは実際の、元に戻せない拡張になります。

```bash
aws iam put-role-policy --role-name fsxn-ssd-auto-increase-role \
  --policy-name deny-update-file-system \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Deny","Action":"fsx:UpdateFileSystem","Resource":"*"}]}'
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/fsxn-ssd-auto-increase-role \
  --action-names fsx:UpdateFileSystem \
  --resource-arns arn:aws:fsx:ap-northeast-1:123456789012:file-system/fs-0123456789abcdef0 \
  --query 'EvaluationResults[0].EvalDecision'
# After mode is back to notify_only
aws iam delete-role-policy --role-name fsxn-ssd-auto-increase-role \
  --policy-name deny-update-file-system
```

実行で観測した、各実行が残すもの:

- 呼び出しをしない評価（アラームが OK、管理アクションの実行中、クールダウン、上限に到達）は、そのたびにレポートを 1 通送ります。既定の `rate(1 hour)` では、アラームが OK の間も 1 日最大 24 通です。
- `auto` の呼び出しはレポートを 2 通送ります。呼び出し前のレポートと、その結果です。拒否された呼び出しは `blocked` で終わり、そのレポートはエラーコードを示します。
- `blocked` のラッチで止まった実行は、呼び出しもレポートもせず、判断ログの行もアーカイブのオブジェクトも書きません。記録するのは関数自身のログ `/aws/lambda/<name_prefix>-evaluator` だけで、`blocked latch holds` と書きます。
- `archive_retention_unproven` のレポートは、呼び出しをせずロックも解放しているのに `"lock_state": "calling"` を持ちます。この結果では `decision` と `detail` を読んでください。

`blocked` のラッチは、原因を直してから解除します。設定のフィンガープリント（`max_storage_capacity_gib`、`increase_percent`、`mode`、`decision_archive_required_mode`）を変えて適用するか、ロックの項目にオペレーターの disposition を修正の根拠と一緒に記録します。関数は次の呼び出しで解除を適用し、元の相関 ID の下に `reconciled` のイベントをアーカイブし、同じ呼び出しの中で評価し直します。`auto` でアラームがまだ ALARM なら、その再評価は `UpdateFileSystem` を呼ぶことがあります。

```bash
aws dynamodb update-item --table-name fsxn-ssd-auto-increase-lock \
  --key '{"file_system_id":{"S":"fs-0123456789abcdef0"}}' \
  --update-expression 'SET disposition = :d, evidence = :e' \
  --condition-expression '#s = :blocked' \
  --expression-attribute-names '{"#s":"state"}' \
  --expression-attribute-values '{":d":{"S":"cleared"},":e":{"S":"<what was fixed>"},":blocked":{"S":"blocked"}}'
```

アーカイブの保持は、オブジェクトのバージョンごとに読みます。各バージョンの保持期限は、作成時刻にバケットの既定の期間を足したものです。実行では、読んだバージョンはすべて `COMPLIANCE` で、作成時刻 + 1 日でした。バージョンの中で最も遅い保持期限までは、バケットを空にできず、したがって削除もできません。

```bash
aws s3api list-object-versions --bucket <object-lock-bucket-name> \
  --prefix fsx-ssd-auto-increase/fs-0123456789abcdef0/ \
  --query 'Versions[].[Key,VersionId,LastModified]' --output text
aws s3api get-object-retention --bucket <object-lock-bucket-name> \
  --key fsx-ssd-auto-increase/fs-0123456789abcdef0/<correlation-id>/1-decision.json \
  --version-id <version-id>
```

### 削除

```bash
terraform destroy
```

判断アーカイブのバケットはこのモジュールの外にあり、削除されません。コンプライアンスモードのバケットのオブジェクトは保持期限が過ぎるまで削除できません。

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
| `decision_log_group_name` | 判断ロググループ（運用履歴） |
| `dead_letter_queue_url`、`dead_letter_queue_arn` | 失敗した起動の DLQ |
| `schedule_rule_arn` | EventBridge スケジュールルール |
| `trigger_alarm_arns` | file-system と aggregate/<name> をキーにしたトリガーアラーム ARN |
| `config_fingerprint` | 上限・増加率・モード・アーカイブモードのハッシュ |
