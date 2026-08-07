# Grafana Cloud Loki セットアップガイド

🌐 [English](../en/setup-guide.md)

## 概要

Amazon FSx for NetApp ONTAP の監査ログを Grafana Cloud Loki に配信するサーバーレス統合のセットアップ手順です。

本ガイドでは以下のフローを構築します:

1. Grafana Cloud 認証情報の準備
2. CloudFormation による Lambda デプロイ
3. テストイベント送信と動作確認
4. Grafana Explore でのログ到着確認
5. LogQL クエリ例
6. ダッシュボード作成

## 前提条件

- AWS アカウント（FSx for ONTAP 稼働中）
- Grafana Cloud アカウント（Free tier 可: 50GB/月のログ取り込み）
- AWS CLI v2 設定済み
- FSx for ONTAP 監査ログが S3 バケットに出力されていること
- S3 Access Point が作成済みであること（[前提リソース](../../../../docs/ja/prerequisites.md)参照）

## Step 1: Grafana Cloud 認証情報の準備

### 1.1 Instance ID と API Key の取得

Grafana Cloud コンソールから Loki の認証情報を取得します。

1. [Grafana Cloud](https://grafana.com/) にログイン
2. **My Account** ページに移動
3. 左メニューの **Grafana Cloud** セクションから対象のスタックを選択
4. **Loki** カードの **Details** をクリック
5. 以下の情報をメモ:
   - **Instance ID**: 数字の ID（例: `123456`）
   - **URL**: Loki エンドポイント URL
6. **Security** セクションの **API Keys** → **Generate now** をクリック
7. API Key を作成:
   - **Key name**: `fsxn-audit-log-shipper`
   - **Role**: `MetricsPublisher`（`logs:write` スコープを含む）
8. 生成された API Key をコピー（この画面を閉じると再表示できません）

> **重要**: API Key には `logs:write` スコープが必要です。`MetricsPublisher` ロールにはこのスコープが含まれています。

### 1.2 AWS Secrets Manager に保存

取得した Instance ID と API Key を AWS Secrets Manager に保存します。

```bash
aws secretsmanager create-secret \
  --name "grafana/fsxn-loki-credentials" \
  --description "Grafana Cloud Loki credentials for FSx for ONTAP audit log integration" \
  --secret-string '{"instance_id":"YOUR_INSTANCE_ID","api_key":"YOUR_API_KEY"}' \
  --region ap-northeast-1
```

> **シークレット名**: `grafana/fsxn-loki-credentials`
>
> **JSON 形式**: `{"instance_id":"<id>","api_key":"<key>"}`

作成後、シークレットの ARN を控えておきます（Step 2 のデプロイで使用）:

```bash
aws secretsmanager describe-secret \
  --secret-id "grafana/fsxn-loki-credentials" \
  --region ap-northeast-1 \
  --query 'ARN' --output text
```

### 1.3 IAM 権限の確認

Lambda 実行ロールには、Secrets Manager からシークレットを読み取る権限が必要です。CloudFormation テンプレートが自動的に設定しますが、手動で確認する場合は以下の権限が必要です:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-northeast-1:<ACCOUNT_ID>:secret:grafana/fsxn-loki-credentials-*"
    }
  ]
}
```

> **最小権限の原則**: `Resource` はシークレットの ARN に限定してください。ワイルドカード `*` サフィックスは Secrets Manager が自動付与するランダム文字列に対応するためです。

### 1.4 Loki Push エンドポイント

Lambda は以下の形式の URL にログを送信します:

```
https://<instance_id>.grafana.net/loki/api/v1/push
```

例えば Instance ID が `123456` の場合:

```
https://123456.grafana.net/loki/api/v1/push
```

認証は Basic Auth で行われます:
- **Username**: Instance ID
- **Password**: API Key

> **注意**: Grafana Cloud のリージョンによって URL のホスト部分が異なる場合があります（例: `logs-prod-us-central1.grafana.net`）。Step 1.1 で確認した URL を使用してください。CloudFormation デプロイ時に `LokiEndpoint` パラメータとして指定します。

## Step 2: CloudFormation デプロイ

### 推奨: デプロイスクリプトを使う

このスクリプトはスタックのデプロイと実 Lambda コードのアップロードを**両方**行います。
CloudFormation テンプレートはハンドラをインラインに持てないため、1 ステップで動作する
統合を得られる唯一の経路です。

```bash
export GRAFANA_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export LOKI_ENDPOINT="..."
export S3_BUCKET_NAME="..."

bash integrations/grafana/scripts/deploy.sh
```

初回は **3〜5 分**かかり、そのほとんどは CloudFormation が IAM ロール・Lambda・
スケジューラ・アラームを作成する時間です。変更のないスタックへの再実行は数秒で
終わります。対応する変数の一覧は `--help` で確認できます。

`--all` を付けると EMS / FPolicy スタックも対象になります。

> スクリプト実行前に `ALARM_TOPIC_ARN` に SNS トピック ARN を設定すると、CloudWatch
> アラームが通知されるようになります。未設定の場合アラームは通知アクションなしで
> 作成され、コンソールには表示されますが誰にも通知されません。

### 代替: CloudFormation を手動でデプロイする

CloudFormation テンプレートを使用して Lambda 関数と関連リソースをデプロイします。

```bash
aws cloudformation deploy \
  --template-file integrations/grafana/template.yaml \
  --stack-name fsxn-grafana-integration \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    S3AccessPointArn="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap" \
    GrafanaCredentialsSecretArn="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:grafana/fsxn-loki-credentials-AbCdEf" \
    LokiEndpoint="https://logs-prod-us-central1.grafana.net" \
    S3BucketName="your-fsxn-audit-log-bucket"
```

> **注意**: 各パラメータの値は自分の環境に合わせて置き換えてください。

### 実 Lambda コードのアップロード（必須）

**スタックだけでは動作しません。** CloudFormation はこの規模のハンドラをインラインに
書けないため、`template.yaml` は `NotImplementedError` を投げるプレースホルダを
持っています。`scripts/deploy.sh` を使った場合はこの手順は済んでいます。手動で
デプロイした場合は、ここで実行してください:

```bash
cd integrations/grafana/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-grafana-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-grafana-integration-shipper \
  --region ap-northeast-1
```

`-j` はパスを平坦化し、実行時に `ontap_audit_parser` が解決できるようにします。この
ファイルを同梱しないとハンドラは JSON 専用の解析に静かにフォールバックし、ONTAP の
監査ログ（常に XML か EVTX）はフィールド解析されずに配送されます。

### パラメータリファレンス

<!-- generated from template.yaml; keep in sync when parameters change -->

Required:

| Parameter | Description |
|-----------|-------------|
| `S3AccessPointArn` | S3 Access Point ARN used by the Lambda for both ListObjectsV2 and GetObject. This should be an access point attached to the S3 bucket or FSx volume containing audit logs. |
| `GrafanaCredentialsSecretArn` | Secrets Manager ARN containing JSON: {"instance_id":"`<id>`","api_key":"`<key>`"} |
| `LokiEndpoint` | Grafana Cloud endpoint URL. Supports two modes: - OTLP Gateway (preferred): https://otlp-gateway-prod-`<region>`.grafana.net/otlp - Loki Push API (fallback): https://logs-prod-`<region>`.grafana.net The Lambda auto-detects the mode from the URL pattern. |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LokiTenantId` | `''` (empty) | X-Scope-OrgID header (optional, for multi-tenant Loki) |
| `S3KeyPrefix` | `''` (empty) | S3 key prefix for listing audit log objects (e.g., "audit/svm-prod-01/"). Used by the scheduler-triggered polling mode to scope ListObjectsV2. |
| `ScheduleExpression` | `rate(5 minutes)` | EventBridge Scheduler rate expression for polling new audit log files. Examples: "rate(5 minutes)", "rate(1 minute)", "rate(15 minutes)" |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 3: テストイベント送信

Lambda 関数が正しく動作するか、テストイベントを送信して確認します。

### 3.1 テストイベントの作成

以下の JSON を `test-event.json` として保存します。S3 オブジェクト作成通知の形式です:

```json
{
  "Records": [
    {
      "eventSource": "aws:s3",
      "eventName": "ObjectCreated:Put",
      "s3": {
        "bucket": {
          "name": "your-fsxn-audit-log-bucket"
        },
        "object": {
          "key": "audit/svm-prod-01/2026/01/15/20260115120000_audit.evtx"
        }
      }
    }
  ]
}
```

> **注意**: `bucket.name` と `object.key` は実際の監査ログパスに合わせて変更してください。`object.key` は FSx for ONTAP が出力する監査ログファイルのパスです。

### 3.2 Lambda 関数の呼び出し

```bash
aws lambda invoke \
  --function-name fsxn-grafana-integration-shipper \
  --payload fileb://test-event.json \
  --cli-binary-format raw-in-base64-out \
  output.json
```

### 3.3 期待されるレスポンス

`output.json` の内容を確認します:

```bash
cat output.json
```

**正常時（全件成功）**:

```json
{
  "statusCode": 200,
  "body": {
    "total_logs": 15,
    "total_shipped": 15,
    "errors": []
  }
}
```

**部分成功時**:

```json
{
  "statusCode": 207,
  "body": {
    "total_logs": 15,
    "total_shipped": 12,
    "errors": [
      "Failed to ship batch 2: HTTP 429 Too Many Requests"
    ]
  }
}
```

| フィールド | 説明 |
|-----------|------|
| `statusCode` | `200`: 全件成功、`207`: 部分成功（一部エラーあり） |
| `body.total_logs` | パースされたログエントリの総数 |
| `body.total_shipped` | Loki に正常に送信されたログ数 |
| `body.errors` | エラーメッセージの配列（空配列なら全件成功） |

### 3.4 トラブルシューティング

#### statusCode が 200 以外、または errors にエントリがある場合

**CloudWatch Logs でエラーを確認**:

```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/fsxn-grafana-integration-shipper" \
  --filter-pattern "ERROR" \
  --start-time $(date -d '10 minutes ago' +%s000) \
  --query 'events[].message' --output text
```

> **macOS の場合**: `$(date -d '10 minutes ago' +%s000)` を `$(date -v-10M +%s000)` に置き換えてください。

**DLQ メッセージ数を確認**:

```bash
aws sqs get-queue-attributes \
  --queue-url "https://sqs.ap-northeast-1.amazonaws.com/123456789012/fsxn-grafana-integration-dlq" \
  --attribute-names ApproximateNumberOfMessages \
  --query 'Attributes.ApproximateNumberOfMessages' --output text
```

DLQ にメッセージがある場合、Lambda が処理に失敗したイベントが存在します。CloudWatch Logs と合わせて原因を調査してください。

#### Lambda が FunctionError またはタイムアウトを返す場合

Lambda 実行ロールの IAM 権限を確認します:

**S3 Access Point 読み取り権限の確認**:

```bash
aws iam list-attached-role-policies \
  --role-name fsxn-grafana-integration-lambda-role

aws iam get-role-policy \
  --role-name fsxn-grafana-integration-lambda-role \
  --policy-name S3Read
```

以下の権限が必要です:
- `s3:GetObject` — リソース: `arn:aws:s3:ap-northeast-1:<ACCOUNT_ID>:accesspoint/fsxn-audit-ap/object/*`

**Secrets Manager アクセス権限の確認**:

```bash
aws iam get-role-policy \
  --role-name fsxn-grafana-integration-lambda-role \
  --policy-name Secrets
```

以下の権限が必要です:
- `secretsmanager:GetSecretValue` — リソース: Grafana 認証情報シークレットの ARN

> **タイムアウトの場合**: Lambda のデフォルトタイムアウトは 300 秒です。大量のログファイルを処理する場合は `LambdaTimeout` パラメータを増やすか、ログファイルを分割してテストしてください。


## Step 4: Grafana Explore でログ確認

Lambda のテストイベント送信が成功したら、Grafana Cloud 上でログが到着していることを確認します。

### 4.1 Explore への移動手順

1. [Grafana Cloud](https://grafana.com/) にログインし、対象のスタックを開く
2. 左サイドバーのコンパスアイコン **Explore** をクリック（またはキーボードショートカット `Cmd+Shift+E` / `Ctrl+Shift+E`）
3. 画面上部のデータソースドロップダウンから **grafanacloud-\<stack\>-logs**（Loki データソース）を選択
4. 時間範囲ピッカーで **Last 15 minutes** を選択

### 4.2 基本クエリでの確認

以下の LogQL クエリを入力し、**Run query** をクリックします:

```logql
{job="fsxn-audit"}
```

ログが正常に配信されていれば、1 件以上のログエントリがタイムラインとログ一覧に表示されます。

![Grafana Explore ログ到着確認](../screenshots/explore-log-arrival.png)

> **ヒント**: ログが表示されない場合は、時間範囲を **Last 1 hour** に広げて再度クエリを実行してください。Lambda テストイベントのタイムスタンプが現在時刻と離れている場合があります。

### 4.3 期待されるログフィールド

クエリ結果のログエントリを展開すると、以下のフィールドが確認できます:

| フィールド名 | 説明 | 例 |
|-------------|------|-----|
| `timestamp` | イベント発生時刻（ISO 8601 形式） | `2026-01-15T10:30:00.000Z` |
| `UserName` | 操作を実行したユーザー名 | `admin`, `vsadmin` |
| `Operation` | 実行された操作の種類 | `create`, `read`, `write`, `delete`, `rename` |
| `ObjectName` | 操作対象のファイル/ディレクトリパス | `/vol1/data/report.xlsx` |

これらのフィールドが表示されていれば、ログパイプラインが正常に動作しています。

### 4.4 トラブルシューティング: ログが 5 分以内に表示されない場合

5 分経過してもログが Grafana Explore に表示されない場合、以下のチェックリストを順に確認してください。

#### Lambda 呼び出しエラーの確認（CloudWatch Logs）

Lambda 関数の実行ログを確認し、エラーが発生していないか確認します:

```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/fsxn-grafana-log-shipper" \
  --filter-pattern "ERROR" \
  --start-time $(date -d '10 minutes ago' +%s000) \
  --region ap-northeast-1
```

- `ERROR` や `Exception` が出力されている場合、Lambda 関数内でログ配信に失敗しています
- `Loki push failed` や `HTTP 4xx/5xx` のメッセージがあれば、認証またはエンドポイントの問題です

#### ネットワーク接続性の確認（VPC エンドポイント / セキュリティグループ）

Lambda が VPC 内に配置されている場合、Loki エンドポイントへの HTTPS 通信が許可されているか確認します:

- **NAT Gateway**: VPC 内 Lambda から外部 API（Grafana Cloud）へ通信するには NAT Gateway が必要です
- **セキュリティグループ**: Lambda のセキュリティグループで HTTPS（ポート 443）のアウトバウンドが許可されていることを確認
- **VPC エンドポイント**: Internet-origin S3 AP は Gateway Endpoint のみではアクセス不可（[制約事項](../../../../docs/ja/prerequisites.md)参照）

```bash
# セキュリティグループのアウトバウンドルール確認
aws ec2 describe-security-groups \
  --group-ids <lambda-sg-id> \
  --query 'SecurityGroups[].IpPermissionsEgress' \
  --region ap-northeast-1
```

#### 認証情報の確認（Instance ID / API Key の有効性）

Secrets Manager に保存した認証情報が正しいか確認します:

```bash
# シークレットの値を確認（Instance ID と API Key）
aws secretsmanager get-secret-value \
  --secret-id "grafana/fsxn-loki-credentials" \
  --region ap-northeast-1 \
  --query 'SecretString' --output text | python3 -c "
import sys, json
secret = json.loads(sys.stdin.read())
print(f'Instance ID: {secret[\"instance_id\"]}')
print(f'API Key: {secret[\"api_key\"][:8]}...(masked)')
"
```

確認ポイント:
- **Instance ID** が Grafana Cloud コンソールの Loki Details に表示される数字と一致するか
- **API Key** が有効期限切れになっていないか（Grafana Cloud → Security → API Keys で確認）
- **API Key のスコープ** に `logs:write` が含まれているか（`MetricsPublisher` ロール）

> **解決しない場合**: 上記すべてを確認しても解決しない場合は、Grafana Cloud のステータスページ（https://status.grafana.com/）でサービス障害が発生していないか確認してください。


## Step 5: LogQL クエリ例

Grafana Explore でログが確認できたら、LogQL を使って監査ログを効率的に検索・分析できます。以下に代表的なクエリパターンを示します。

### 5.1 操作別フィルタ

特定の操作タイプ（create, read, write, delete, rename）でログを絞り込みます。ファイル作成イベントのみを確認したい場合に使用します。

```logql
{job="fsxn-audit"} | json | Operation="create"
```

> **使用例**: 新規ファイル作成の監査、不正なファイル作成の検知。`Operation` の値を `delete` や `rename` に変更することで、他の操作タイプもフィルタできます。

### 5.2 ユーザー別フィルタ

特定のユーザーが実行した操作のみを表示します。ユーザーアクティビティの調査やアクセス監査に使用します。

```logql
{job="fsxn-audit"} | json | UserName="admin"
```

> **使用例**: 管理者アカウントの操作履歴確認、特定ユーザーの不審なアクティビティ調査。`UserName` の値を対象ユーザー名に変更してください。

### 5.3 失敗操作フィルタ

操作結果が失敗（Failure）のログのみを抽出します。アクセス拒否やパーミッションエラーの検知に有効です。

```logql
{job="fsxn-audit"} | json | Result="Failure"
```

> **使用例**: 権限不足によるアクセス拒否の検知、ブルートフォース攻撃の兆候確認。失敗イベントが短時間に集中している場合はセキュリティインシデントの可能性があります。

### 5.4 SVM 別フィルタ

特定の SVM（Storage Virtual Machine）のログのみを表示します。マルチテナント環境で SVM ごとにログを分離して確認する場合に使用します。

```logql
{job="fsxn-audit", svm="svm-prod-01"}
```

> **使用例**: 本番 SVM のみの監査ログ確認、SVM 間のアクティビティ比較。`svm` ラベルは Lambda がログ配信時に付与するストリームラベルです。

### 5.5 line_format 出力整形

`line_format` を使用してログ出力を見やすく整形します。必要なフィールドのみを抽出して表示することで、大量のログを効率的に確認できます。

```logql
{job="fsxn-audit"} | json | line_format "{{.UserName}} {{.Operation}} {{.ObjectName}}"
```

> **使用例**: ログ一覧を「誰が・何を・どのファイルに」の形式で簡潔に表示。Grafana Explore のログビューで視認性が向上します。

### 5.6 count_over_time 集計

指定した時間ウィンドウ内のログ件数を集計します。時系列でのイベント発生頻度を把握するのに使用します。

```logql
count_over_time({job="fsxn-audit"} | json [5m])
```

> **使用例**: 5 分間隔でのイベント発生数の推移を確認。異常なスパイクの検知や、通常時のベースライン把握に活用できます。ダッシュボードの時系列パネルで使用すると効果的です。

### 5.7 rate 流量計算

ログの流入レート（1 秒あたりのログ件数）を計算します。システム全体のログスループットを監視する場合に使用します。

```logql
rate({job="fsxn-audit"}[5m])
```

> **使用例**: ログ配信パイプラインのスループット監視、容量計画のためのログ流量把握。急激なレート上昇はファイルシステムへの大量アクセスを示唆します。

## Step 6: ダッシュボード作成

LogQL クエリを活用して、FSx for ONTAP 監査ログの可視化ダッシュボードを作成します。以下の 4 つのパネルで構成されるダッシュボードにより、ログ量の推移、操作の内訳、ユーザーアクティビティ、失敗イベントを一目で把握できます。

### ダッシュボード作成手順

1. Grafana Cloud にログインし、左サイドバーの **Dashboards** → **New** → **New Dashboard** をクリック
2. **Add visualization** をクリックしてパネルを追加
3. データソースに **grafanacloud-\<stack\>-logs**（Loki）を選択
4. 以下の各パネル設定に従ってクエリとビジュアライゼーションを構成

### 6.1 ログ量推移パネル

監査ログの発生量を時系列で表示します。異常なスパイクや通常時のベースラインを視覚的に把握できます。

| 設定項目 | 値 |
|---------|-----|
| **パネルタイトル** | ログ量推移（Log Volume Over Time） |
| **ビジュアライゼーション** | Time series |
| **LogQL クエリ** | 下記参照 |

```logql
count_over_time({job="fsxn-audit"}[5m])
```

**パネル設定のポイント**:
- **Query type**: Range を選択
- **Legend**: `{{job}}` を設定
- **グラフスタイル**: Lines（デフォルト）
- **単位**: short（イベント数）
- 時間ウィンドウ `[5m]` は環境に応じて `[1m]` や `[15m]` に調整可能

### 6.2 操作別内訳パネル

操作タイプ（create, read, write, delete, rename）ごとのイベント数を円グラフまたは棒グラフで表示します。どの操作が最も多いかを直感的に把握できます。

| 設定項目 | 値 |
|---------|-----|
| **パネルタイトル** | 操作別内訳（Operations Breakdown） |
| **ビジュアライゼーション** | Pie chart または Bar gauge |
| **LogQL クエリ** | 下記参照 |

```logql
sum by (Operation) (count_over_time({job="fsxn-audit"} | json [1h]))
```

**パネル設定のポイント**:
- **Query type**: Instant を選択（円グラフの場合）
- **Legend**: `{{Operation}}` を設定
- **Pie chart** を選択すると操作タイプの割合が視覚的に分かりやすい
- **Bar gauge** を選択すると各操作の絶対数を比較しやすい
- 時間ウィンドウ `[1h]` はダッシュボードの時間範囲に応じて調整

### 6.3 ユーザーアクティビティパネル

イベント数の多い上位 10 ユーザーを表示します。アクティブなユーザーの特定や、異常に多いアクセスを行っているアカウントの検知に使用します。

| 設定項目 | 値 |
|---------|-----|
| **パネルタイトル** | ユーザーアクティビティ Top 10（User Activity Top 10） |
| **ビジュアライゼーション** | Bar gauge または Table |
| **LogQL クエリ** | 下記参照 |

```logql
topk(10, sum by (UserName) (count_over_time({job="fsxn-audit"} | json [1h])))
```

**パネル設定のポイント**:
- **Query type**: Instant を選択
- **Legend**: `{{UserName}}` を設定
- JSON パイプライン `| json` により `UserName` フィールドをラベルとして抽出
- `topk(10, ...)` で上位 10 ユーザーに限定
- **Bar gauge** で横棒グラフとして表示すると、ユーザー間の比較が容易
- **Table** ビジュアライゼーションを使用すると、正確な数値を確認可能

### 6.4 失敗イベントパネル

操作結果が失敗（Failure）のイベント数を時系列で表示します。アクセス拒否やパーミッションエラーの発生傾向を監視し、セキュリティインシデントの早期検知に活用します。

| 設定項目 | 値 |
|---------|-----|
| **パネルタイトル** | 失敗イベント推移（Failed Events Over Time） |
| **ビジュアライゼーション** | Time series |
| **LogQL クエリ** | 下記参照 |

```logql
count_over_time({job="fsxn-audit"} | json | Result="Failure" [5m])
```

**パネル設定のポイント**:
- **Query type**: Range を選択
- **Legend**: `Failed Events` を設定
- **グラフスタイル**: Lines + Points（失敗イベントを目立たせる）
- **しきい値**: 必要に応じてアラートしきい値を設定（例: 5 分間に 10 件以上で警告）
- **カラー**: 赤系の色を設定して視覚的に警告を強調
- `Result="Failure"` フィルタにより失敗イベントのみをカウント

### ダッシュボード完成イメージ

4 つのパネルを配置したダッシュボードの全体像です:

![ダッシュボード概要](../screenshots/dashboard-overview.png)

> **ヒント**: ダッシュボードの時間範囲を **Last 1 hour** または **Last 6 hours** に設定すると、各パネルに十分なデータが表示されます。テスト直後はデータ量が少ないため、時間範囲を広げて確認してください。

### ダッシュボードの保存と共有

1. 画面右上の **Save dashboard**（💾アイコン）をクリック
2. ダッシュボード名を入力: `FSx for ONTAP Audit Log Overview`
3. フォルダを選択（例: `FSx for ONTAP Monitoring`）
4. **Save** をクリック

> **エクスポート**: ダッシュボードの JSON モデルをエクスポートして、他の Grafana インスタンスにインポートすることも可能です。**Dashboard settings** → **JSON Model** からコピーできます。


## トラブルシューティング

本セクションでは、Grafana Cloud Loki 統合で発生しうる問題とその解決手順をまとめます。Step 3〜4 で記載したトラブルシューティングの内容を含む、包括的なリファレンスです。

### ログが Grafana に届かない

Grafana Explore で `{job="fsxn-audit"}` を実行してもログが表示されない場合、以下の 3 つのカテゴリを順に確認してください。

#### Lambda 呼び出しエラー

Lambda 関数が正常に実行されていない可能性があります。CloudWatch Logs でエラーを確認します。

```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/fsxn-grafana-integration-shipper" \
  --filter-pattern "ERROR" \
  --start-time $(date -d '10 minutes ago' +%s000) \
  --region ap-northeast-1
```

> **macOS の場合**: `$(date -d '10 minutes ago' +%s000)` を `$(date -v-10M +%s000)` に置き換えてください。

**確認ポイント**:

| 症状 | 原因 | 対処 |
|------|------|------|
| `ERROR` や `Exception` が出力されている | Lambda 関数内でログ配信に失敗 | エラーメッセージの詳細を確認し、該当箇所を修正 |
| `Loki push failed` や `HTTP 4xx/5xx` | 認証またはエンドポイントの問題 | 下記「認証エラー」セクションを参照 |
| `S3 Access Denied` | IAM 権限不足 | 下記「Lambda タイムアウト」セクションの IAM 確認手順を参照 |
| ログイベント自体が存在しない | Lambda が呼び出されていない | EventBridge Scheduler の設定と S3 バケットのイベント通知を確認 |

**DLQ メッセージ数の確認**:

```bash
aws sqs get-queue-attributes \
  --queue-url "https://sqs.ap-northeast-1.amazonaws.com/<ACCOUNT_ID>/fsxn-grafana-integration-dlq" \
  --attribute-names ApproximateNumberOfMessages \
  --query 'Attributes.ApproximateNumberOfMessages' --output text
```

DLQ にメッセージがある場合、Lambda が処理に失敗したイベントが存在します。メッセージ内容を確認して原因を特定してください。

#### ネットワーク接続性

Lambda が VPC 内に配置されている場合、Grafana Cloud Loki エンドポイントへの HTTPS 通信が許可されているか確認します。

**チェックリスト**:

- **NAT Gateway**: VPC 内 Lambda から外部 API（Grafana Cloud）および Internet-origin S3 AP へ通信するには NAT Gateway が必要です。
- **セキュリティグループ**: Lambda のセキュリティグループで HTTPS（ポート 443）のアウトバウンドが許可されていること
- **サブネットルートテーブル**: NAT Gateway へのルート（`0.0.0.0/0 → nat-xxx`）が存在すること

```bash
# セキュリティグループのアウトバウンドルール確認
aws ec2 describe-security-groups \
  --group-ids <lambda-sg-id> \
  --query 'SecurityGroups[].IpPermissionsEgress' \
  --region ap-northeast-1
```

```bash
# サブネットのルートテーブル確認
aws ec2 describe-route-tables \
  --filters "Name=association.subnet-id,Values=<lambda-subnet-id>" \
  --query 'RouteTables[].Routes' \
  --region ap-northeast-1
```

> **推奨構成**: ログ読み取り専用の Lambda は VPC 外に配置することで、NAT Gateway のコストとネットワーク設定の複雑さを回避できます。

#### 認証情報の不一致

Secrets Manager に保存した認証情報が Grafana Cloud の設定と一致しているか確認します。詳細は下記「認証エラー」セクションを参照してください。

### 認証エラー

Loki Push API が HTTP 401（Unauthorized）または 403（Forbidden）を返す場合、認証情報に問題があります。

#### Instance ID の確認

```bash
aws secretsmanager get-secret-value \
  --secret-id "grafana/fsxn-loki-credentials" \
  --region ap-northeast-1 \
  --query 'SecretString' --output text | python3 -c "
import sys, json
secret = json.loads(sys.stdin.read())
print(f'Instance ID: {secret[\"instance_id\"]}')
print(f'API Key: {secret[\"api_key\"][:8]}...(masked)')
"
```

#### 確認ポイント

| チェック項目 | 確認方法 |
|-------------|---------|
| Instance ID が正しいか | Grafana Cloud コンソール → Loki Details に表示される数字と一致するか確認 |
| API Key が有効期限切れでないか | Grafana Cloud → Security → API Keys で状態を確認 |
| API Key のスコープが正しいか | `logs:write` スコープが含まれていること（`MetricsPublisher` ロール） |
| Loki エンドポイント URL が正しいか | `https://<instance_id>.grafana.net` または Grafana Cloud コンソールに表示される URL |
| シークレットの JSON 形式が正しいか | `{"instance_id":"<id>","api_key":"<key>"}` の形式であること |

#### API Key の再発行手順

API Key が無効な場合は、以下の手順で再発行します:

1. [Grafana Cloud](https://grafana.com/) にログイン
2. **My Account** → 対象スタック → **Security** → **API Keys**
3. 古いキーを削除（必要に応じて）
4. **Generate now** をクリック
5. **Key name**: `fsxn-audit-log-shipper`、**Role**: `MetricsPublisher`
6. 生成された API Key をコピー

Secrets Manager のシークレットを更新します:

```bash
aws secretsmanager put-secret-value \
  --secret-id "grafana/fsxn-loki-credentials" \
  --secret-string '{"instance_id":"YOUR_INSTANCE_ID","api_key":"YOUR_NEW_API_KEY"}' \
  --region ap-northeast-1
```

> **注意**: シークレット更新後、Lambda の次回呼び出し時（コールドスタート時）に新しい認証情報が読み込まれます。即座に反映させたい場合は、Lambda 関数を手動で再デプロイするか、実行環境をリセットしてください。

#### Grafana Cloud サービス状態の確認

認証情報が正しいにもかかわらずエラーが続く場合、Grafana Cloud 側のサービス障害の可能性があります:

- **ステータスページ**: https://status.grafana.com/
- **Loki 取り込み制限**: Free tier は 50GB/月。制限に達している場合は HTTP 429 が返されます

### Lambda タイムアウト

Lambda がタイムアウト（デフォルト: 300 秒）する場合、IAM 権限不足またはリソースアクセスの問題が考えられます。

#### IAM ロール権限の確認

Lambda 実行ロールに必要な権限が付与されているか確認します。

**S3 Access Point 読み取り権限**:

```bash
aws iam list-attached-role-policies \
  --role-name fsxn-grafana-integration-lambda-role

aws iam get-role-policy \
  --role-name fsxn-grafana-integration-lambda-role \
  --policy-name S3Read
```

必要な権限:

| アクション | リソース |
|-----------|---------|
| `s3:GetObject` | `arn:aws:s3:ap-northeast-1:<ACCOUNT_ID>:accesspoint/fsxn-audit-ap/object/*` |
| `s3:ListBucket` | `arn:aws:s3:ap-northeast-1:<ACCOUNT_ID>:accesspoint/fsxn-audit-ap` |

> **重要**: S3 Access Point の IAM ポリシーでは、リソース ARN に `/object/*` サフィックスが必須です。

**Secrets Manager アクセス権限**:

```bash
aws iam get-role-policy \
  --role-name fsxn-grafana-integration-lambda-role \
  --policy-name Secrets
```

必要な権限:

| アクション | リソース |
|-----------|---------|
| `secretsmanager:GetSecretValue` | `arn:aws:secretsmanager:ap-northeast-1:<ACCOUNT_ID>:secret:grafana/fsxn-loki-credentials-*` |

#### タイムアウトの原因と対処

| 原因 | 症状 | 対処 |
|------|------|------|
| 大量のログファイル処理 | 処理時間が 300 秒を超過 | `LambdaTimeout` パラメータを増やす（最大 900 秒）、またはログファイルを分割 |
| S3 Access Point への接続タイムアウト | `ConnectTimeoutError` | VPC 内 Lambda の場合は NAT Gateway の設定を確認 |
| Secrets Manager への接続タイムアウト | `EndpointConnectionError` | VPC エンドポイント（`com.amazonaws.ap-northeast-1.secretsmanager`）を追加 |
| Loki エンドポイントへの接続タイムアウト | `MaxRetryError` | NAT Gateway の設定確認、Grafana Cloud のステータス確認 |

#### Lambda メモリとタイムアウトの調整

大量のログを処理する場合は、CloudFormation パラメータを調整します:

```bash
aws cloudformation deploy \
  --template-file integrations/grafana/template.yaml \
  --stack-name fsxn-grafana-integration \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    S3AccessPointArn="<existing-value>" \
    GrafanaCredentialsSecretArn="<existing-value>" \
    LokiEndpoint="<existing-value>" \
    S3BucketName="<existing-value>" \
    LambdaMemorySize=512 \
    LambdaTimeout=600
```

> **ヒント**: メモリを増やすと CPU 割り当ても比例して増加するため、処理速度が向上します。256MB → 512MB への変更で処理時間が大幅に短縮されることがあります。

## デプロイの検証

```bash
bash integrations/grafana/scripts/verify.sh
```

2 層が順に実行されます。まず共有の AWS 側チェック（スタックが正常か、デプロイ済み
Lambda がプレースホルダではなく実ハンドラか、スタックが作成する場合はスケジュールと
チェックポイントが揃っているか）。次にベンダーのエンドポイントへ合成ログを送り、
資格情報とネットワーク到達性を確認します。

どちらも必要です。ベンダーのエンドポイントがテストログを受理しても、パイプラインが
動いている保証にはなりません。そのため AWS 側が失敗した場合は、エンドポイントが
正常応答していてもスクリプトは非ゼロで終了します。

終了コードは `sysexits.h` 準拠で `0` 成功、`69` チェック失敗、`78` 必要な設定が
不足。デプロイ前にベンダー疎通だけ試す場合は `SKIP_AWS_CHECKS=1` を設定します。

## クリーンアップ

```bash
bash integrations/grafana/scripts/cleanup.sh          # スタックのみ
bash integrations/grafana/scripts/cleanup.sh --all    # + シークレット・レイヤー・S3 テストデータ
bash integrations/grafana/scripts/cleanup.sh --all -y  # 非対話
```

共有リソース（S3 アクセスポイント、監査ログバケット、FPolicy Fargate スタック、
前提スタック）は削除されません。削除順と意図的に残すものについては
[ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md)
を参照してください。

## 関連ドキュメント

- [ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md) — 全ベンダー共通の手順
- [前提条件](../../../../docs/ja/prerequisites.md) — FSx for ONTAP、監査ログ有効化、S3 アクセスポイント
- [デプロイガイド](../../../../docs/ja/deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
