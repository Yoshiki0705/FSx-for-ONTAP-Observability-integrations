# Honeycomb セットアップガイド

🌐 [English](../en/setup-guide.md)

## 概要

FSx for ONTAP 監査ログを Honeycomb Events API に配信するセットアップ手順です。

## 前提条件

- Honeycomb アカウント
- [前提リソース](../../../../docs/ja/prerequisites.md)デプロイ済み

## Step 1: Honeycomb API Key の準備

1. Honeycomb → **Team Settings** → **API Keys**
2. **Create API Key** → Permissions: `Send Events`
3. Dataset: `fsxn-audit`（自動作成される）

```bash
aws secretsmanager create-secret \
  --name "honeycomb/fsxn-api-key" \
  --secret-string '{"api_key":"YOUR_HONEYCOMB_API_KEY"}' \
  --region ap-northeast-1
```

## Step 2: CloudFormation デプロイ

### 推奨: デプロイスクリプトを使う

このスクリプトはスタックのデプロイと実 Lambda コードのアップロードを**両方**行います。
CloudFormation テンプレートはハンドラをインラインに持てないため、1 ステップで動作する
統合を得られる唯一の経路です。

```bash
export HONEYCOMB_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export S3_BUCKET_NAME="..."

bash integrations/honeycomb/scripts/deploy.sh
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
  --template-file integrations/honeycomb/template.yaml \
  --stack-name fsxn-honeycomb-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    HoneycombApiKeySecretArn=arn:aws:secretsmanager:... \
    HoneycombDataset=fsxn-audit \
    S3BucketName=$BUCKET_NAME \
  --capabilities CAPABILITY_IAM
```

### 実 Lambda コードのアップロード（必須）

**スタックだけでは動作しません。** CloudFormation はこの規模のハンドラをインラインに
書けないため、`template.yaml` は `NotImplementedError` を投げるプレースホルダを
持っています。`scripts/deploy.sh` を使った場合はこの手順は済んでいます。手動で
デプロイした場合は、ここで実行してください:

```bash
cd integrations/honeycomb/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-honeycomb-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-honeycomb-integration-shipper \
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
| `HoneycombApiKeySecretArn` | Secrets Manager ARN for Honeycomb API key |
| `S3BucketName` | S3 bucket name for EventBridge rule matching |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `HoneycombDataset` | `fsxn-audit` | Honeycomb dataset name |
| `HoneycombApiUrl` | `https://api.honeycomb.io` | Honeycomb API base URL |
| `S3KeyPrefix` | `''` (empty) | S3 key prefix filter for audit log objects |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 3: Honeycomb で確認

1. Dataset: `fsxn-audit`
2. Query: `GROUP BY operation, VISUALIZE COUNT`
3. フィルタ: `result = Failure`

## Honeycomb の強み

- 高カーディナリティデータの探索に最適
- BubbleUp で異常パターンを自動検出
- SLO 設定でファイルアクセス成功率を監視可能

## デプロイの検証

```bash
bash integrations/honeycomb/scripts/verify.sh
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
bash integrations/honeycomb/scripts/cleanup.sh          # スタックのみ
bash integrations/honeycomb/scripts/cleanup.sh --all    # + シークレット・レイヤー・S3 テストデータ
bash integrations/honeycomb/scripts/cleanup.sh --all -y  # 非対話
```

共有リソース（S3 アクセスポイント、監査ログバケット、FPolicy Fargate スタック、
前提スタック）は削除されません。削除順と意図的に残すものについては
[ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md)
を参照してください。

## 関連ドキュメント

- [ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md) — 全ベンダー共通の手順
- [前提条件](../../../../docs/ja/prerequisites.md) — FSx for ONTAP、監査ログ有効化、S3 アクセスポイント
- [デプロイガイド](../../../../docs/ja/deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
