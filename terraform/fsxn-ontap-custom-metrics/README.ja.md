# fsxn-ontap-custom-metrics (Terraform)

🌐 **日本語** | [English](README.md)

Amazon FSx for NetApp ONTAP のファイルシステム 1 つの ONTAP REST API を VPC 内の Lambda 関数が定期的にポーリングし、qtree クォータと SnapMirror の健全性・遅延を CloudWatch のカスタムメトリクスとして発行する Terraform モジュールです。アラームは Terraform の state で管理します。どちらの値も FSx for ONTAP のネイティブの CloudWatch メトリクスにはありません。[監視設計](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/monitoring-design.md)（[English](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/monitoring-design.md)）の Terraform 計画のフェーズ T2 にあたり、そのページのメトリクスカタログとアラート設計を実装します。qtree のコレクターは、`shared/templates/qtree-quota-monitor.yaml` のインラインコードと同じソースファイルです。

## 検証状況

オフラインの検証では、`make terraform` が `terraform fmt -check`、`terraform init -lockfile=readonly`、`terraform validate`、`terraform test`（モックの `aws` プロバイダーと `command = plan` による。`archive` プロバイダーは実際の Lambda の zip を作ります）を実行します。[`examples/basic/`](examples/basic/) に対しても `init` と `validate` を実行します。`shared/lambda/ontap_metrics/` の Lambda のソースには、urllib3 と boto3 をモックにした pytest の単体テストがあり、SnapMirror のレコードは ONTAP 9.18.1 REST API リファレンスのレスポンス例の形に合わせています。テストファイルの 1 つは、すべてのアラームが `shared/lambda/ontap_metrics/tests/fixtures/metric_contract.json` にある（名前空間、メトリクス、ディメンション名）の組を読むことを確認し、Python のテストは、コレクターが発行する系列がそのファイルと完全に一致することを確認します。

実環境での検証は、2026-10-08（UTC）に `ap-northeast-1` で行ったサンプル実行 1 回です（[記録](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-08-の-terraform-カスタムメトリクスモジュールの実行)、[English](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/en/verification-results-cloudwatch-monitoring.md#terraform-custom-metrics-module-run-on-2026-10-08)）。対象は第 1 世代 `SINGLE_AZ_1`・HA ペア 1 つのファイルシステム 1 つ（ONTAP 9.18.1P6）で、2 つのコレクターを両方とも有効にし、ポーリング間隔 1 分、遅延の閾値はテスト用に 300 秒にしました。ファイルシステムの SVM の数が文書化された上限の 6 に達していたため、SnapMirror の関係は 1 つの SVM の中の 2 つのテスト用ボリュームの間で組みました。この範囲で `verified` なのは、デプロイ、実際の ONTAP の応答に対する両方のコレクターの系列、最初のポーリングの前のハートビートのアラームの ALARM とその後の OK、`snapmirror-unhealthy` アラームの OK → ALARM → OK（手動の転送を失敗させ、その後の転送で回復）、`snapmirror-lag-high` アラームの OK → ALARM → OK です。所見 F1: 初期化していない関係は `healthy: true` で `lag_time` を持たないと報告され、その関係ではどちらの SnapMirror アラームも発報しませんでした（1 回観測）。このギャップを塞ぐには新しいシグナルが必要で、メトリクスカタログの変更になるため実装していません。`unverified` のまま残るのは、2 つの SVM の間と 2 つのファイルシステムの間の SnapMirror（クラスターピアリングと、別クラスターの宛先ファイルシステムのポーリング）、`qtree-quota-high` の ALARM の経路（使用率は 40.16% で、閾値の 85 に届かなかった）、デプロイ用の IAM ポリシー（実行は管理者権限で行った）、インバウンドルールの追加と destroy の前の取り消しの手順、SNS の通知、CA 証明書で検証する TLS、既定のポーリング間隔 5 分と遅延の閾値 10800 秒、第 2 世代と複数 HA ペアのファイルシステムです。実行したリビジョンから変わったのはモジュールの README だけです。これはサンプル実行で、本番での見積りではありません。形の異なるファイルシステムでは、まず本番以外のアカウントで適用してください。

## 作成されるリソース

- `aws_lambda_function` `<name_prefix>-poller`（Python 3.12、256 MB、タイムアウト 300 秒、予約同時実行数 1、ハンドラー `ontap_metrics_handler.lambda_handler`）。指定したサブネットで動き、`data "archive_file"` が `shared/lambda/ontap_metrics/` から（テストを除いて）パッケージを作ります。
- `rate(N minutes)`（1 のときは `rate(1 minute)`）の `aws_cloudwatch_event_rule`、そのターゲットと `aws_lambda_permission`。
- 関数のデッドレターキューとしての `aws_sqs_queue` `<name_prefix>-dlq`（保持 14 日、`alias/aws/sqs`）。
- `aws_cloudwatch_log_group` `/aws/lambda/<name_prefix>-poller`（`log_retention_days`）。
- `AWSLambdaVPCAccessExecutionRole` とインラインポリシー（下表）を持つ `aws_iam_role` `<name_prefix>-role`。
- `ontap_management_ip/32` と `aws_api_egress_cidr_blocks` の各要素への TCP 443 の送信を許可する `aws_security_group` `<name_prefix>-lambda`。ファイルシステム自身のセキュリティグループは変更しません。
- 任意で、`monitoring` と `secretsmanager` のインターフェイスエンドポイントと、エンドポイント用のセキュリティグループ（[ネットワークの選択肢](#ネットワークの選択肢)を参照）。
- 任意で、`notification_email` を指定したときの `aws_sns_topic` とメールのサブスクリプション。そのときすべてのアラームが ALARM と OK の両方で通知します。
- 最大 7 本の `aws_cloudwatch_metric_alarm`（下表）。

各コレクターは取得・組み立て・発行を自分の `try`/`except` の中で実行し、その後に自分の名前空間へ `CollectorSucceeded`（1 か 0）を発行します。失敗したコレクターはもう一方を止めず、呼び出しも失敗させません。それを知らせるのはハートビートのアラームです。ONTAP が HTTP 401 か 403 を返すと、キャッシュした認証情報を捨て、その回の残りのコレクターは ONTAP へ要求を送らずに飛ばします。ハートビートの発行そのものが失敗したときは呼び出しが失敗するので、Lambda のエラーのアラームと DLQ がそれを捉えます。

`FSxONTAP/Qtree`（qtree のコレクター。CloudFormation テンプレートと同じ系列に、ハートビートを加えたもの）

| メトリクス | ディメンション | 単位 | 発行の条件 | 読むアラーム |
|---|---|---|---|---|
| `QtreeQuotaUsedPercent`、`QtreeQuotaUsedBytes`、`QtreeQuotaLimitBytes` | `SvmName`、`VolumeName`、`QtreeName` | Percent、Bytes、Bytes | 名前があり、ハードリミットが 0 より大きい qtree ごと | なし |
| `QtreeQuotaUsedPercentMax` | `SvmName` | Percent | 1 回の実行に 1 回。使える qtree がなければ発行しない | `qtree_quota` |
| `QtreeQuotaReportTruncated` | `SvmName` | Count | 1 回の実行に 1 回。50 ページの上限で読み取りが止まったとき 1 | なし |
| `CollectorSucceeded` | `FileSystemId`、`Collector=qtree` | Count | 1 回の実行に 1 回、1 か 0 | `heartbeat` |

`FSxONTAP/SnapMirror`（SnapMirror のコレクター。このファイルシステムを宛先として `GET /api/snapmirror/relationships` を呼ぶ）

| メトリクス | ディメンション | 単位 | 発行の条件 | 読むアラーム |
|---|---|---|---|---|
| `SnapMirrorRelationshipHealthy` | `FileSystemId`、`SourcePath`、`DestinationPath` | Count | `snapmirror_max_relationships` までの関係ごと。`healthy` が true なら 1、それ以外は 0 | なし |
| `SnapMirrorLagSeconds` | `FileSystemId`、`SourcePath`、`DestinationPath` | Seconds | 上限までの関係ごと。`lag_time` を解釈できたとき | なし |
| `SnapMirrorUnhealthyCount` | `FileSystemId` | Count | 1 回の実行に 1 回。読んだすべての関係が対象で、関係がなくても 0 を発行 | `snapmirror_unhealthy` |
| `SnapMirrorLagSecondsMax` | `FileSystemId` | Seconds | 1 回の実行に 1 回。`lag_time` を 1 つも解釈できなければ発行しない | `snapmirror_lag` |
| `SnapMirrorRelationshipsTruncated` | `FileSystemId` | Count | 1 回の実行に 1 回。読んだ関係のうち関係ごとの系列を持たないものがあるとき（関係の上限が効いた、またはパスが空）か、50 ページの上限が効いたとき 1 | なし |
| `CollectorSucceeded` | `FileSystemId`、`Collector=snapmirror` | Count | 1 回の実行に 1 回、1 か 0 | `heartbeat` |

SnapMirror の値の規則は次のとおりです。`healthy` がない、または真偽値でない場合は非健全（0）として数え、ログに警告を出します。`lag_time` がない場合（たとえば `uninitialized` の関係）や、年・月を含まない ISO 8601 の期間として解釈できない場合、その関係の遅延のデータポイントは発行しません。送信元か宛先のパスが空の関係には関係ごとの系列を発行しませんが、集計には含め、`SnapMirrorRelationshipsTruncated` を 1 にします。`SnapMirrorUnhealthyCount` に数えられたのに関係ごとの系列に現れない非健全の関係があることが、これでわかります。非健全の関係について、ログには `uuid`、`state`、両方のパス、`unhealthy_reason` のコードとメッセージを出します。`SnapMirrorUnhealthyCount` が 0 でも、「すべて健全」と「この ONTAP ユーザーから見える関係がない」は区別できません。関係の数はログの行 `SnapMirror on <fs>: N relationship(s)` に残ります。フィールド名は [ONTAP 9.18.1 REST API リファレンス](https://docs.netapp.com/us-en/ontap-restapi-9181/get-snapmirror-relationships.html)から取りました。

アラーム（DLQ のアラームを除き、`period` は `max(300, 60 × poll_interval_minutes)`）

| `alarm_arns` のキー | 名前 | メトリクス | 統計 | 評価期間 | 条件 | 欠損データ | 作成の条件 |
|---|---|---|---|---|---|---|---|
| `qtree_quota` | `<prefix>-qtree-quota-high` | `QtreeQuotaUsedPercentMax` | Maximum | 2 | > `qtree_quota_threshold_percent`（85） | `missing` | qtree が有効 |
| `snapmirror_unhealthy` | `<prefix>-snapmirror-unhealthy` | `SnapMirrorUnhealthyCount` | Maximum | 2 | > 0 | `missing` | SnapMirror が有効 |
| `snapmirror_lag` | `<prefix>-snapmirror-lag-high` | `SnapMirrorLagSecondsMax` | Maximum | 1 | > `snapmirror_lag_threshold_seconds`（10800） | `missing` | SnapMirror が有効 |
| `heartbeat/<collector>` | `<prefix>-<collector>-heartbeat` | `CollectorSucceeded` | Minimum | 2 | < 1 | `breaching` | 有効なコレクターごと |
| `dlq_depth` | `<prefix>-dlq-depth` | `AWS/SQS` `ApproximateNumberOfMessagesVisible`（期間 300） | Maximum | 1 | > 0 | `notBreaching` | 常に |
| `lambda_errors` | `<prefix>-lambda-errors` | `AWS/Lambda` `Errors` | Sum | 1 | > 0 | `notBreaching` | 常に |

> **同時実行に関する補足**
>
> 関数の予約同時実行数は 1 で、ポーラーは同時に 1 つしか動きません。1 回の実行は 300 秒のタイムアウトまで続くことがあり、失敗した非同期の実行は Lambda が再試行します。そのため制限がないと、`poll_interval_minutes` が短いときに、前の実行が終わる前に次の実行が始まりえます。実行環境ごとに認証情報を別々にキャッシュするので、それぞれがログインを送り、拒否されたパスワードが並行して試されます。制限があると、重なった呼び出しはスロットリングされ、Lambda がキューに戻して最大 6 時間再試行し、その後 DLQ へ送って破棄します（[非同期呼び出しのエラー処理](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html)）。アラームへの現れ方は次のとおりです。スロットリングは `AWS/Lambda` の `Throttles` に数えられ、`Errors` には数えられません（[Lambda のメトリクス](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html)）。そのため `lambda_errors` は発火せず、モジュールにスロットリングのアラームはありません。実行がアラームの 2 期間を超えて遅れると `CollectorSucceeded` が欠け、`heartbeat/<collector>` が ALARM になります。6 時間後に破棄されたイベントは DLQ に入り、`dlq_depth` が発火します。Lambda で同時実行数を予約できるのは、アカウントに予約されていない同時実行数が 100 以上残る範囲だけです（[予約同時実行](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html)）。同時実行のクォータがそれより低いアカウントでは、この設定で `apply` が失敗します。いずれもこのモジュールでは観測していません（`unverified`）。

モジュールが作る Lambda の実行ロール（デプロイする側にはこのロールへの `iam:PassRole` が必要）

| ステートメント | アクション | リソース |
|---|---|---|
| マネージドポリシー `AWSLambdaVPCAccessExecutionRole` | `logs:CreateLogGroup`、`logs:CreateLogStream`、`logs:PutLogEvents` と VPC 接続のための ENI のアクションを `*` に対して許可（[AWS マネージドポリシーのリファレンス](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AWSLambdaVPCAccessExecutionRole.html)） | `*` |
| `SecretsRead` | `secretsmanager:GetSecretValue` | `ontap_credentials_secret_arn` だけ |
| `SecretKms`（`ontap_credentials_kms_key_arn` を指定したときだけ） | `kms:Decrypt`。`kms:ViaService = secretsmanager.<region>.amazonaws.com` の条件付き | そのキー |
| `CloudWatchPublish` | `cloudwatch:PutMetricData`。`cloudwatch:namespace` を有効なコレクターの名前空間に限定 | `*`（このアクションにはリソースレベルの権限がない） |
| `Logs` | `logs:CreateLogStream`、`logs:PutLogEvents` | モジュールのロググループ |
| `DeadLetterQueue` | `sqs:SendMessage` | モジュールの DLQ |

## モジュールの取得方法

このモジュールは `terraform-fsxn-ontap-custom-metrics-vX.Y.Z` の形式の git タグで版を付けています。現在の版は `terraform-fsxn-ontap-custom-metrics-v0.1.0` で、[GitHub の Release](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/releases/tag/terraform-fsxn-ontap-custom-metrics-v0.1.0) として公開しています。タグは版を固定するもので、[検証状況](#検証状況)に記した検証の範囲は変えません。モジュールは大きなリポジトリのサブディレクトリなので、Terraform Registry には登録されていません。このモジュールについてはダウンロード量を測っていません。同じリポジトリでの実測は [ダッシュボードのモジュールの README](../fsxn-monitoring-dashboard/README.ja.md#モジュールの取得方法) にあります。

Lambda のソースはモジュールのディレクトリの外、`shared/lambda/ontap_metrics/` にあります。`//subdirectory` 形式のソースでは、Terraform はパッケージ全体をダウンロードして展開し、その後でサブディレクトリからモジュールを読みます（[module ブロックのリファレンス](https://developer.hashicorp.com/terraform/language/block/module)）。そのため下のどのソースでも `../../shared` を解決できます。コードブロック内のコメントは英語のままで、上から順に「タグに固定した git ソース（浅い clone）」「コミットに固定した git ソース」「コミットに固定したアーカイブ URL（git 不要）」という意味です。

```hcl
# Git source pinned to a tag (shallow clone)
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ontap-custom-metrics?ref=terraform-fsxn-ontap-custom-metrics-v0.1.0&depth=1"

# Git source pinned to a commit
source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ontap-custom-metrics?ref=<commit-sha>"

# Archive URL pinned to a commit (no git needed)
source = "https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/archive/<commit-sha>.tar.gz//FSx-for-ONTAP-Observability-integrations-<commit-sha>/terraform/fsxn-ontap-custom-metrics"
```

次の手順は sparse checkout で、タグの時点のモジュールのディレクトリを取り出します。その後、このコピーの `terraform/fsxn-ontap-custom-metrics` のローカルパスを `source` に指定します。コミットに固定する場合は、タグ名をコミット SHA に置き換えます。sparse checkout では両方のディレクトリを含めます。含めないと `shared/lambda/ontap_metrics` が存在せず、plan の時点で `archive_file` が失敗します。

```bash
git init fsx-ontap-custom-metrics && cd fsx-ontap-custom-metrics
git remote add origin https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations.git
git sparse-checkout set terraform/fsxn-ontap-custom-metrics shared/lambda/ontap_metrics
git fetch --depth 1 --filter=blob:none origin terraform-fsxn-ontap-custom-metrics-v0.1.0
git checkout FETCH_HEAD
```

> **取得手順に関する補足**
>
> タグを作る前に、タグ名の代わりにコミット SHA を指定してこの手順を実行し、取り出したコピーの `examples/basic/` で `terraform init -backend=false` と `terraform validate` が成功しました。タグそのものの取得は、このモジュールでは実行していません。`?ref=main&depth=1` を指定した git ソースでも、`terraform init` がダウンロードしたコピーにはモジュールの隣に `shared/lambda/ontap_metrics/` がありました。そのコピーから `plan` は実行していません。`source` に git ソースを書く場合、[ダッシュボードのモジュールの README](../fsxn-monitoring-dashboard/README.ja.md#モジュールの取得方法) に記録したとおり、SHA と `depth=1` は組み合わせられません。

## 使い方

以下の節は、初回のデプロイの順に並んでいます。前提条件、ネットワーク、権限、入力値、デプロイ、適用後の確認、削除の順です。

### 前提条件

- Terraform `>= 1.11.0`、`hashicorp/aws` `>= 6.67.0`、`hashicorp/archive` `>= 2.8.1`（6.67.0 と 2.8.1 で確認）。
- ファイルシステムの VPC（またはピアリングした VPC）にあり、ファイルシステムの管理エンドポイントへ TCP 443 で届くサブネット。
- ポーラー用の ONTAP ユーザー。AWS のドキュメントは、組み込みのファイルシステムのロール `fsxadmin-readonly` を、ファイルシステムのレベルですべてを参照でき変更はできないロールで、監視アプリケーションに向くと説明し、ファイルシステムのロールは新しく作れないとしています（[ロールとユーザー](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/roles-and-users.html)）。ユーザーは `fsxadmin` で SSH 接続し、`security login create` で作ります（[ONTAP ユーザーの作成](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/create-new-ontap-users.html)）。そのページは `-application` の値として `http`、`ontapi`、`ssh` を挙げ、例は `ssh` で示しています。下のコマンドは REST API で使うために `http` を指定します。このモジュールのためには実行していません（`unverified`）。ONTAP はパスワードを 2 回尋ねます。

```bash
ssh fsxadmin@<management-endpoint-ip>
security login create -user-or-group-name ontap-monitor -application http -authentication-method password -role fsxadmin-readonly
```

そのユーザーの認証情報は、`{"username": "...", "password": "..."}` の形で Secrets Manager のシークレットに保存します。Terraform と Lambda の環境変数に入るのは ARN だけです。JSON をファイルに書くと、パスワードがシェルの履歴に残りません。作成後にファイルを削除してください。

```bash
aws secretsmanager create-secret --name ontap-monitor \
  --secret-string file://ontap-monitor-secret.json
rm ontap-monitor-secret.json
```

適用後に、ファイルシステムのセキュリティグループへ、モジュールの Lambda のセキュリティグループ（出力 `lambda_security_group_id`）からの TCP 443 を許可するインバウンドのルールを追加します。モジュールはこのグループを変更しないので、このルールは Terraform の状態の外にあり、`terraform destroy` の前に削除する必要があります（[削除手順](#削除手順)を参照）。

```bash
aws ec2 authorize-security-group-ingress --group-id sg-0123456789abcdef0 \
  --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=$(terraform output -raw lambda_security_group_id)}]"
```

> **認証情報に関する補足**
>
> ポーラーは 300 秒ごとにシークレットを読み直すので、ローテーションしたパスワードは再デプロイなしで使われます。基本認証の失敗を繰り返すと ONTAP のアカウントがロックされることがあるため、401 と 403 はその回の中では再試行せず、次の試行は次のスケジュールまで待ちます。SSM Parameter Store の `SecureString` を使う案は検討しましたが、実装していません。ハンドラーが読むのは `ONTAP_CREDENTIALS_SECRET_ARN` だけです。

> **TLS とトピックの暗号化に関する補足**
>
> `ca_cert_path` が空のとき（既定値で、CloudFormation テンプレートと同じ動き）、ポーラーは管理エンドポイントの TLS 証明書を検証せず、ログに警告を出します。本番環境では、管理エンドポイントの証明書に署名した CA 証明書を Lambda レイヤーに入れ、`ca_cert_path` と `ca_cert_layer_arn` の両方を指定してください。`notification_email` のために作る SNS トピックは保存時に暗号化されません。メッセージに入るのはアラームの名前、説明、ファイルシステムの ID で、認証情報は入りません。すべてのトピックの暗号化が求められる環境では、`notification_email` を空にし、アラームの状態の変化を自分の構成で暗号化したトピックへ送ってください。

### ネットワークの選択肢

関数には 3 つの経路が必要です。管理エンドポイントへの TCP 443（常にモジュールの `/32` の送信ルール）と、CloudWatch のモニタリング API と Secrets Manager への HTTPS です。後の 2 つには次のどれかを選びます。

| 選択肢 | 設定 | トレードオフ |
|---|---|---|
| サブネットのルートテーブルに NAT ゲートウェイ | 既定値（`aws_api_egress_cidr_blocks = ["0.0.0.0/0"]`、両方の `create_*_endpoint = false`） | このモジュールによるエンドポイントの料金はない。NAT ゲートウェイの料金がかかり、通信は VPC の外へ出る |
| このモジュールが作るエンドポイント | `create_monitoring_endpoint = true`、`create_secretsmanager_endpoint = true`、`aws_api_egress_cidr_blocks = []` | インターネットへの経路が不要。エンドポイントごと・AZ ごとに時間単位の料金がかかる（[コスト](#コスト)を参照） |
| VPC に既にあるエンドポイント | 両方の `create_*_endpoint = false`、`aws_api_egress_cidr_blocks` は VPC の CIDR | 既存のものを使う。そのセキュリティグループが `lambda_security_group_id` からの 443 を許可している必要がある |

> **VPC エンドポイントの競合に関する補足**
>
> 同じサービスに対してプライベート DNS を有効にしたインターフェイスエンドポイントは、1 つの VPC に 2 つ置けません。既にエンドポイントがあるサービスでは `create_*_endpoint = false` にしてください。`shared/scripts/preflight-check.sh` は既存のエンドポイントを報告しますが、このモジュール用のプロファイルがなく、`monitoring` も確認しません。下のコマンドで両方のサービスを一覧できます。

```bash
aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values=vpc-0123456789abcdef0 \
  --query 'VpcEndpoints[].{Service:ServiceName,PrivateDns:PrivateDnsEnabled,State:State}' --output table
```

CloudWatch Logs については、エンドポイントも NAT も不要だった例が 1 つあります。2026-10-06 に CloudFormation の qtree テンプレートを、NAT ゲートウェイがなく `monitoring` と `secretsmanager` のインターフェイスエンドポイントだけがあるサブネットで実行し、ロググループからログの行を読めました（[記録](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-06-の-qtree-クォータ監視の実行)）。2026-10-08 のこのモジュールの実行も NAT ゲートウェイのないサブネットで、モジュールの `monitoring` エンドポイントと既存の `secretsmanager` エンドポイントを使い、関数のロググループからログの行を読めました（[記録](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-08-の-terraform-カスタムメトリクスモジュールの実行)）。このモジュールのために読んだ AWS のページにはその記述がないため、この 2 つの観測が根拠です。

### 必要な IAM 権限（推定、未検証）

`terraform apply` を実行する ID に必要な権限で、モジュールが作るリソースから導いたものです。この権限だけを持つロールでは実行していません。タグのアクションが抜けやすいことは、ダッシュボードのモジュールで分かっています（[その CloudTrail に関する補足](../fsxn-monitoring-dashboard/README.ja.md#必要な-iam-権限確認済み)）。初回の apply の後でアクションを追加することを見込んでください。

| リソース | アクション |
|---|---|
| `aws_lambda_function`、`aws_lambda_permission` | `lambda:CreateFunction`, `lambda:GetFunction`, `lambda:GetFunctionConfiguration`, `lambda:UpdateFunctionCode`, `lambda:UpdateFunctionConfiguration`, `lambda:PutFunctionConcurrency`, `lambda:DeleteFunctionConcurrency`, `lambda:DeleteFunction`, `lambda:AddPermission`, `lambda:RemovePermission`, `lambda:GetPolicy`, `lambda:ListVersionsByFunction`, `lambda:GetFunctionCodeSigningConfig`, `lambda:TagResource`, `lambda:UntagResource`, `lambda:ListTags` |
| `aws_iam_role` とそのポリシー | `iam:CreateRole`, `iam:GetRole`, `iam:DeleteRole`, `iam:PassRole`, `iam:PutRolePolicy`, `iam:GetRolePolicy`, `iam:DeleteRolePolicy`, `iam:AttachRolePolicy`, `iam:DetachRolePolicy`, `iam:ListRolePolicies`, `iam:ListAttachedRolePolicies`, `iam:ListInstanceProfilesForRole`, `iam:TagRole`, `iam:UntagRole` |
| `aws_sqs_queue` | `sqs:CreateQueue`, `sqs:GetQueueAttributes`, `sqs:SetQueueAttributes`, `sqs:DeleteQueue`, `sqs:TagQueue`, `sqs:UntagQueue`, `sqs:ListQueueTags` |
| `aws_cloudwatch_log_group` | `logs:CreateLogGroup`, `logs:DeleteLogGroup`, `logs:PutRetentionPolicy`, `logs:ListTagsForResource`, `logs:TagResource`, `logs:UntagResource` |
| ロググループの読み戻し（`Resource: "*"`） | `logs:DescribeLogGroups` |
| `aws_cloudwatch_event_rule`、`aws_cloudwatch_event_target` | `events:PutRule`, `events:DescribeRule`, `events:DeleteRule`, `events:PutTargets`, `events:RemoveTargets`, `events:ListTargetsByRule`, `events:ListTagsForResource`, `events:TagResource`, `events:UntagResource` |
| `aws_cloudwatch_metric_alarm` | `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, `cloudwatch:DeleteAlarms`, `cloudwatch:ListTagsForResource`, `cloudwatch:TagResource`, `cloudwatch:UntagResource` |
| `aws_sns_topic`、`aws_sns_topic_subscription`（`notification_email` を指定したときだけ） | `sns:CreateTopic`, `sns:GetTopicAttributes`, `sns:SetTopicAttributes`, `sns:ListTagsForResource`, `sns:TagResource`, `sns:UntagResource`, `sns:DeleteTopic`, `sns:Subscribe`, `sns:GetSubscriptionAttributes`, `sns:Unsubscribe` |
| セキュリティグループ、ルール、エンドポイント（`Resource: "*"`） | `ec2:CreateSecurityGroup`, `ec2:DeleteSecurityGroup`, `ec2:AuthorizeSecurityGroupEgress`, `ec2:RevokeSecurityGroupEgress`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:RevokeSecurityGroupIngress`, `ec2:CreateTags`, `ec2:DeleteTags`, `ec2:CreateVpcEndpoint`, `ec2:DeleteVpcEndpoints`, `ec2:ModifyVpcEndpoint` |
| VPC 接続とルールのための読み取り（`Resource: "*"`） | `ec2:DescribeSecurityGroups`, `ec2:DescribeSecurityGroupRules`, `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeNetworkInterfaces`, `ec2:DescribeVpcEndpoints`, `ec2:DescribePrefixLists`, `ec2:DescribeVpcAttribute` |

ポリシーは [`examples/basic/iam-policy.json`](examples/basic/iam-policy.json) にあります。リソースを絞ったステートメントは、モジュールが `name_prefix` から作る名前（既定では `fsxn-ontap-metrics-*`）を使います。使う前に `123456789012`、`ap-northeast-1` とプレフィックスを置き換えてください。`Resource: "*"` のステートメントは 3 つです。`LogsRead` は `logs:DescribeLogGroups` だけを持ちます。[サービス認可リファレンス](https://docs.aws.amazon.com/service-authorization/latest/reference/list_logs.html)はこのアクションにリソースタイプを挙げていないので、ロググループに絞れません。EC2 の 2 つのステートメントが `Resource: "*"` なのは、作成のアクションが複数の種類のリソース（セキュリティグループ、VPC、サブネット、エンドポイント）に及ぶためで、絞り込みは未着手です。[入力値の調べ方](#入力値の調べ方)と[適用後の確認手順](#適用後の確認手順)の読み取り専用のコマンドは、自分の認証情報で実行するものです。

### 入力値の調べ方

`ontap_management_ip`、`vpc_id`、`subnet_ids` の候補はファイルシステムから、`qtree_svm_name` はその SVM から取得します。

```bash
aws fsx describe-file-systems --file-system-ids fs-0123456789abcdef0 \
  --query 'FileSystems[].OntapConfiguration.Endpoints.Management.IpAddresses'
aws fsx describe-file-systems --file-system-ids fs-0123456789abcdef0 \
  --query 'FileSystems[].{Vpc:VpcId,Subnets:SubnetIds}'
aws fsx describe-storage-virtual-machines --filters Name=file-system-id,Values=fs-0123456789abcdef0 \
  --query 'StorageVirtualMachines[].Name'
```

### examples/basic からのデプロイ手順

次のコマンドは、`terraform/` を含むディレクトリ（リポジトリ全体の clone か、上の sparse checkout）で実行します。`terraform.tfvars` では、必須の値と必要な任意の入力値を編集します（コードブロック内の英語のコメントは「リージョン、file_system_id、ontap_management_ip、シークレットの ARN、ネットワーク、SVM を編集する」という意味です）。

```bash
cd terraform/fsxn-ontap-custom-metrics/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars: region, file_system_id, ontap_management_ip, secret ARN, network, SVM
terraform init
terraform plan
terraform apply
```

自分のルート構成では [`examples/basic/`](examples/basic/) をコピーし、`source = "../.."` を [モジュールの取得方法](#モジュールの取得方法) のいずれかのソースに置き換えます。

```hcl
module "ontap_custom_metrics" {
  source = "github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations//terraform/fsxn-ontap-custom-metrics?ref=terraform-fsxn-ontap-custom-metrics-v0.1.0&depth=1"

  file_system_id               = "fs-0123456789abcdef0"
  ontap_management_ip          = "198.51.100.10"
  ontap_credentials_secret_arn = "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ontap-monitor-XXXXXX"
  vpc_id                       = "vpc-0123456789abcdef0"
  subnet_ids                   = ["subnet-0123456789abcdef0"]
  qtree_svm_name               = "svm-prod-01"
}
```

### 適用後の確認手順

`fsxn-ontap-metrics` は自分の `name_prefix` に置き換えます。最初のコマンドは、スケジュールを待たずに関数を 1 回呼び出します。`response.json` にはコレクターごとの `succeeded` が出ます。

```bash
aws lambda invoke --function-name fsxn-ontap-metrics-poller --payload '{}' \
  --cli-binary-format raw-in-base64-out response.json
aws logs tail /aws/lambda/fsxn-ontap-metrics-poller --since 15m
aws cloudwatch list-metrics --namespace FSxONTAP/SnapMirror
aws cloudwatch list-metrics --namespace FSxONTAP/Qtree
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-ontap-metrics \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table
```

ハートビートのアラームは欠損データを breaching として扱うため、最初のスケジュールのポーリングが `CollectorSucceeded` を発行する前に ALARM に遷移することがあります（観測していません。スケジュールが最初に動く時刻によります）。そのほかのアラームは、必要なデータポイントがそろうまで INSUFFICIENT_DATA のままです。

### 削除手順

Terraform の外で追加したルールを先に削除します。同じ VPC の別のセキュリティグループから参照されている間、セキュリティグループは削除できず、削除は `DependencyViolation` で失敗します（[delete-security-group](https://docs.aws.amazon.com/cli/latest/reference/ec2/delete-security-group.html)）。[前提条件](#前提条件)で追加したインバウンドのルールはモジュールの Lambda のセキュリティグループを参照しているので、ルールが残っている間は `terraform destroy` がそのグループを削除できず、この参照は時間がたっても解消しません。

```bash
terraform output -raw lambda_security_group_id
aws ec2 revoke-security-group-ingress --group-id sg-0123456789abcdef0 \
  --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=$(terraform output -raw lambda_security_group_id)}]"
terraform destroy
aws cloudwatch describe-alarms --alarm-name-prefix fsxn-ontap-metrics \
  --query 'MetricAlarms[].AlarmName' --output text
```

1 つ目のコマンドは Lambda のセキュリティグループの ID を出力します。`destroy` の後は出力が消えるので、下の確認のために控えておいてください。2 つ目は前提条件のコマンドが追加したのと同じルールを削除します（`sg-0123456789abcdef0` はファイルシステムのセキュリティグループです）。[ネットワークの選択肢](#ネットワークの選択肢)で既存のエンドポイントを選んだ場合は、そのセキュリティグループにある、Lambda のセキュリティグループからの 443 を許可するルールも削除してください。最後のコマンドは何も出力しないはずです。

`destroy` が `aws_security_group.lambda` で待つか失敗した場合は、次の 2 つのコマンドで原因を見分けます（`<lambda-security-group-id>` は上で出力した ID に置き換えます）。

```bash
aws ec2 describe-network-interfaces --filters Name=group-id,Values=<lambda-security-group-id> \
  --query 'NetworkInterfaces[].{Id:NetworkInterfaceId,Type:InterfaceType,Status:Status}' --output table
aws ec2 describe-security-groups --filters Name=ip-permission.group-id,Values=<lambda-security-group-id> \
  --query 'SecurityGroups[].{Id:GroupId,Name:GroupName}' --output table
```

1 つ目のコマンドに行が出る場合、それは関数の削除後にまだ解放中の Lambda のネットワークインターフェイスです。時間がたてば解消します。2026-10-08 の実行では、`terraform destroy` が Lambda のセキュリティグループでこの解放を 22 分 3 秒待ちました（1 回観測、[記録](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#2026-10-08-の-terraform-カスタムメトリクスモジュールの実行)）。解消した後で `terraform destroy` を再実行してください。2 つ目のコマンドに行が出る場合、それはインバウンドのルールでまだ Lambda のセキュリティグループを参照しているセキュリティグループです。これは時間がたっても解消しないので、一覧の各グループからそのルールを削除してから `terraform destroy` を再実行してください。

## 入力

| 名前 | 型 | 既定値 | 補足 |
|---|---|---|---|
| `file_system_id` | string | 必須 | `^fs-[0-9a-f]{17}$`。SnapMirror では宛先のファイルシステム |
| `ontap_management_ip` | string | 必須 | IPv4、各オクテット 0–255（テンプレートの `OntapMgmtIp`） |
| `ontap_credentials_secret_arn` | string | 必須 | Secrets Manager の ARN（テンプレートの `OntapCredentialsSecretArn`） |
| `ontap_credentials_kms_key_arn` | string | `null` | シークレットのカスタマー管理キー。`SecretKms` を加える |
| `vpc_id` | string | 必須 | モジュールが作るセキュリティグループの VPC |
| `subnet_ids` | list(string) | 必須 | 1 つ以上、重複不可（テンプレートの `SubnetIds`） |
| `aws_api_egress_cidr_blocks` | list(string) | `["0.0.0.0/0"]` | CloudWatch と Secrets Manager への TCP 443 の送信先 |
| `create_monitoring_endpoint` | bool | `false` | [ネットワークの選択肢](#ネットワークの選択肢)を参照 |
| `create_secretsmanager_endpoint` | bool | `false` | [ネットワークの選択肢](#ネットワークの選択肢)を参照 |
| `enable_qtree_collector` | bool | `true` | 少なくとも 1 つのコレクターを有効にする |
| `enable_snapmirror_collector` | bool | `true` | 少なくとも 1 つのコレクターを有効にする |
| `qtree_svm_name` | string | `null` | qtree のコレクターでは必須、1–66 文字（テンプレートの `SvmName`） |
| `poll_interval_minutes` | number | `5` | 1–60 の整数（テンプレートの `PollIntervalMinutes`）。どの値でも同時に動く実行は 1 つ（[同時実行に関する補足](#作成されるリソース)を参照） |
| `qtree_quota_threshold_percent` | number | `85` | 50–99（テンプレートの `QuotaThresholdPercent`） |
| `snapmirror_lag_threshold_seconds` | number | `10800` | 60–2592000 |
| `snapmirror_max_relationships` | number | `100` | 1–1000 の整数 |
| `ca_cert_path`、`ca_cert_layer_arn` | string | `""` | 両方指定するか、両方空にする（テンプレートの `CaCertPath`、`CaCertLayerArn`）。空なら TLS の検証をしない |
| `log_retention_days` | number | `30` | CloudWatch Logs が受け付ける値 |
| `notification_email` | string | `""` | 空なら SNS を作らない（テンプレートの `NotificationEmail`） |
| `name_prefix` | string | `"fsxn-ontap-metrics"` | 1–48 文字。スタック名の役割を果たす |
| `tags` | map(string) | `{}` | タグを付けられるすべてのリソース |

## 出力

| 名前 | 説明 |
|---|---|
| `lambda_function_name`、`lambda_function_arn`、`lambda_role_arn` | 関数と実行ロール |
| `lambda_security_group_id` | ファイルシステムのセキュリティグループに追加するインバウンドのルールの送信元 |
| `log_group_name` | ポーラーのロググループ |
| `dead_letter_queue_url`、`dead_letter_queue_arn` | DLQ |
| `schedule_rule_arn` | EventBridge のルール |
| `alarm_arns` | アラームの表と同じキー（`heartbeat/<collector>`）のマップ |
| `sns_topic_arn` | トピックの ARN。`notification_email` がなければ `null` |
| `metric_namespaces` | 有効なコレクターの名前空間 |
| `vpc_endpoint_ids` | モジュールが作ったエンドポイントをサービスをキーとしたマップ。既定では空 |

## CloudFormation テンプレートとの意図的な差

- コレクターごとの `CollectorSucceeded` のハートビートと、`breaching` のアラームがある。テンプレートにはなく DLQ のアラームに頼るが、DLQ のアラームは呼び出されないポーラーを捉えない。
- ONTAP への要求は、接続のエラー、429、5xx に対して指数バックオフで 3 回まで再試行する。401 と 403 は再試行せず、残りのコレクターを飛ばす。テンプレートは各要求を 1 回だけ送る。
- 認証情報は 300 秒ごとに読み直す。テンプレートはコンテナが続く間キャッシュする。
- 予約同時実行数を 1 にしているので、ポーリングは重ならない。テンプレートは予約同時実行数を設定していない（テンプレートの後続課題として記録）。
- コレクターの失敗では呼び出しを失敗させず（ハートビートが知らせる）、ハートビートの発行の失敗では失敗させる。テンプレートではどの失敗でも呼び出しが失敗する。
- アラームの期間はポーリングの間隔に従う（`max(300, 60 × poll_interval_minutes)`）。テンプレートは `PollIntervalMinutes` によらず 300。
- 間隔が 1 のときは `rate(1 minute)` を使う。テンプレートは `rate(1 minutes)` を組み立て、これは [EventBridge の rate の構文](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-scheduled-rule-pattern.html)では受け付けられない（テンプレートの後続課題として記録）。
- モジュールが Lambda のセキュリティグループと、任意で `monitoring` のエンドポイントを作る。テンプレートはセキュリティグループの ID を受け取り、Secrets Manager のエンドポイントだけを作る。
- 関数がロググループに依存するので、destroy は関数を先に削除する。テンプレートの実行では、ロググループが先に削除され、実行中の再試行が保持期間なしで作り直した（[QF3](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/verification-results-cloudwatch-monitoring.md#所見qtree-の実行)）。
- `alarm_actions` に加えて `ok_actions` を設定する。
- SnapMirror にはまだ CloudFormation の版がない（[CHANGELOG.md](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/CHANGELOG.md) に後続課題として記録）。

## ファイルシステム間の SnapMirror

宛先のファイルシステムをポーリングします。ONTAP のリファレンスは遅延の例を宛先のエンドポイントを持つクラスターで実行しており、送信元側の `list_destinations_only` の一覧がどの健全性・遅延のフィールドを返すかは `open` です。ファイルシステム A から B への SnapMirror では、B の管理 IP、B の ONTAP ユーザーの認証情報、B へのネットワーク経路で、B 用にモジュールのインスタンスを 1 つデプロイします。宛先のファイルシステムが複数あれば、`name_prefix` を変えてそれぞれに 1 つずつ置きます。モジュールが送信元のファイルシステムを呼ぶことはありません。

qtree の系列は、CloudFormation テンプレートと同じく `SvmName` を持ち、`FileSystemId` は持ちません。そのため、同じアカウントとリージョンで同じ SVM 名を持つ 2 つのファイルシステムは、同じ qtree の系列と同じ `QtreeQuotaUsedPercentMax` に書き込みます。qtree のコレクターはそのどちらか一方だけで有効にするか、SVM 名を分けてください。SnapMirror の系列は `FileSystemId` を持つので、この制約はありません。

## コスト

メトリクスの合計金額は、リージョン・日付・数によって変わるため示しません。既定値での式は次のとおりです。

- カスタムメトリクスの系列。qtree は SVM ごとに `3 × N + 2` とハートビート 1（N はハードリミットを持つ qtree の数）。SnapMirror は `2 × R + 3` とハートビート 1（R は系列を持つ関係の数で、最大 `snapmirror_max_relationships`）。単価は [CloudWatch の料金](https://aws.amazon.com/cloudwatch/pricing/)を参照。
- アラーム。標準解像度のアラームが最大 7 本。
- Lambda。5 分間隔で 30 日あたり 8,640 回の呼び出し。1 回あたり最大 300 秒、256 MB。単価は [Lambda の料金](https://aws.amazon.com/lambda/pricing/)を参照。
- `PutMetricData` の呼び出し。1 回の実行で 20 データポイントごとに 1 回と、ハートビートごとに 1 回。

このモジュールが作るインターフェイスエンドポイントは、エンドポイントごと・AZ ごとの時間単位の料金と、処理した GB 単位の料金がかかります（[AWS PrivateLink の料金](https://aws.amazon.com/privatelink/pricing/)）。リポジトリの[デプロイガイド](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/blob/main/docs/ja/deployment-guide.md)は、エンドポイントごと・AZ ごとに月額約 $7.20 としており、これは米国東部 (バージニア北部) の 1 時間 $0.01 を 720 時間に当てはめた値です。2026-10-07 に AWS Price List API（2026-09-17 公開の価格表）で調べたところ、アジアパシフィック (東京) は 1 時間 $0.014 で 720 時間あたり約 $10.08、処理量はどちらのリージョンも 1 GB あたり $0.01 でした。1 つの AZ に両方のエンドポイントを作れば、その料金が 2 つかかります。自分のリージョンと日付の料金ページを確認してください。

## プロバイダーの版の制約

モジュールは `hashicorp/aws` を `>= 6.67.0`、`hashicorp/archive` を `>= 2.8.1`（確認に使ったリリース）で宣言し、上限は付けていません。2026-10-07 の時点で、Terraform Registry の最新のリリースは 6.67.0 と 2.8.1（2026-09-11 公開）でした。[`examples/basic/`](examples/basic/) は完全一致のピン `= 6.67.0` と `= 2.8.1`、専用の `.terraform.lock.hcl` を持ちます。呼び出し側はこのモジュールの `.terraform.lock.hcl` を使いません。Terraform が読むのはルート構成のロックファイルです（[Dependency Lock File](https://developer.hashicorp.com/terraform/language/files/dependency-lock)）。テストが `override_during = plan` を使うため、Terraform `>= 1.11.0` が必要です。
