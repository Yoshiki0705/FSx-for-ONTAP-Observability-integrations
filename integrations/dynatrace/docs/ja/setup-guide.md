# Dynatrace セットアップガイド

🌐 [English](../en/setup-guide.md)

## 概要

FSx for ONTAP 監査ログを Dynatrace Log Ingest API v2 に配信するセットアップ手順です。

## 前提条件

- Dynatrace 環境（SaaS / Managed）
- [前提リソース](../../../../docs/ja/prerequisites.md)デプロイ済み

## Step 1: Dynatrace API Token の作成

1. Dynatrace → **Settings** → **Integration** → **Dynatrace API**
2. **Generate token** → Scopes: `logs.ingest`
3. トークンをコピー

```bash
aws secretsmanager create-secret \
  --name "dynatrace/fsxn-api-token" \
  --secret-string '{"api_token":"dt0c01.xxx..."}' \
  --region ap-northeast-1
```

## Step 2: CloudFormation デプロイ

### 推奨: デプロイスクリプトを使う

このスクリプトはスタックのデプロイと実 Lambda コードのアップロードを**両方**行います。
CloudFormation テンプレートはハンドラをインラインに持てないため、1 ステップで動作する
統合を得られる唯一の経路です。

```bash
export DT_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export S3_BUCKET_NAME="..."
export DT_ENV_URL="..."

bash integrations/dynatrace/scripts/deploy.sh
```

初回は **3〜5 分**かかり、そのほとんどは CloudFormation が IAM ロール・Lambda・
スケジューラ・アラームを作成する時間です。変更のないスタックへの再実行は数秒で
終わります。対応する変数の一覧は `--help` で確認できます。

`--all` を付けると EMS / FPolicy スタックも対象になります。`FPOLICY_SQS_QUEUE_ARN`
に `shared/templates/fpolicy-apigw.yaml` の ingestion queue ARN を設定すると
FPolicy の主経路（Fargate → SQS → Lambda）が有効になります。未設定の場合、FPolicy
スタックは副経路の EventBridge ルールのみを使います。対応状況は
[テレメトリ経路のカバレッジ](../../../../docs/ja/README.md#テレメトリ経路のカバレッジ)
を参照してください。

> スクリプト実行前に `ALARM_TOPIC_ARN` に SNS トピック ARN を設定すると、CloudWatch
> アラームが通知されるようになります。未設定の場合アラームは通知アクションなしで
> 作成され、コンソールには表示されますが誰にも通知されません。

### 代替: CloudFormation を手動でデプロイする

```bash
aws cloudformation deploy \
  --template-file integrations/dynatrace/template.yaml \
  --stack-name fsxn-dynatrace-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    DynatraceApiTokenSecretArn=arn:aws:secretsmanager:... \
    DynatraceEnvUrl=https://abc12345.live.dynatrace.com \
    S3BucketName=$BUCKET_NAME \
  --capabilities CAPABILITY_IAM
```

### 実 Lambda コードのアップロード（必須）

**スタックだけでは動作しません。** CloudFormation はこの規模のハンドラをインラインに
書けないため、`template.yaml` は `NotImplementedError` を投げるプレースホルダを
持っています。`scripts/deploy.sh` を使った場合はこの手順は済んでいます。手動で
デプロイした場合は、ここで実行してください:

```bash
cd integrations/dynatrace/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-dynatrace-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-dynatrace-integration-shipper \
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
| `S3AccessPointArn` | FSx for ONTAP S3 Access Point ARN |
| `DynatraceApiTokenSecretArn` | Secrets Manager ARN for Dynatrace API token |
| `DynatraceEnvUrl` | Dynatrace environment URL (e.g., https://`<env-id>`.live.dynatrace.com) |
| `S3BucketName` | S3 bucket name for EventBridge rule matching |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `S3KeyPrefix` | `''` (empty) | S3 key prefix filter for audit log objects |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 3: Dynatrace で確認

1. **Observe & Explore** → **Logs**
2. フィルタ: `log.source="fsxn-ontap"`
3. DQL:
```dql
fetch logs
| filter log.source == "fsxn-ontap"
| sort timestamp desc
| limit 20
```

## トラブルシューティング

- **HTTP 401**: API Token の `logs.ingest` スコープを確認
- **1MB 超過**: バッチサイズが自動分割されているか Lambda ログで確認

## デプロイの検証

```bash
bash integrations/dynatrace/scripts/verify.sh
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
bash integrations/dynatrace/scripts/cleanup.sh          # スタックのみ
bash integrations/dynatrace/scripts/cleanup.sh --all    # + シークレット・レイヤー・S3 テストデータ
bash integrations/dynatrace/scripts/cleanup.sh --all -y  # 非対話
```

共有リソース（S3 アクセスポイント、監査ログバケット、FPolicy Fargate スタック、
前提スタック）は削除されません。削除順と意図的に残すものについては
[ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md)
を参照してください。

## 関連ドキュメント

- [ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md) — 全ベンダー共通の手順
- [前提条件](../../../../docs/ja/prerequisites.md) — FSx for ONTAP、監査ログ有効化、S3 アクセスポイント
- [デプロイガイド](../../../../docs/ja/deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
