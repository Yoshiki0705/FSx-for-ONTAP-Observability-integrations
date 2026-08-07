# New Relic セットアップガイド

🌐 [English](../en/setup-guide.md)

## 概要

FSx for ONTAP 監査ログを New Relic Logs に配信するサーバーレス統合のセットアップ手順です。

## 前提条件

- AWS アカウント（FSx for ONTAP 稼働中）
- New Relic アカウント（Logs 機能有効）
- [前提リソース](../../../../docs/ja/prerequisites.md)デプロイ済み

## Step 1: New Relic License Key の準備

1. New Relic → **API Keys** → **Create a key**
2. Key type: `INGEST - LICENSE`
3. 生成された License Key をコピー

```bash
aws secretsmanager create-secret \
  --name "new-relic/fsxn-license-key" \
  --secret-string '{"license_key":"YOUR_LICENSE_KEY"}' \
  --region ap-northeast-1
```

## Step 2: CloudFormation デプロイ

### 推奨: デプロイスクリプトを使う

このスクリプトはスタックのデプロイと実 Lambda コードのアップロードを**両方**行います。
CloudFormation テンプレートはハンドラをインラインに持てないため、1 ステップで動作する
統合を得られる唯一の経路です。

```bash
export NR_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export S3_BUCKET_NAME="..."

bash integrations/new-relic/scripts/deploy.sh
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
  --template-file integrations/new-relic/template.yaml \
  --stack-name fsxn-new-relic-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    NewRelicLicenseKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:new-relic/fsxn-license-key-XXXXX \
    NewRelicRegion=US \
    S3BucketName=$BUCKET_NAME \
  --capabilities CAPABILITY_IAM
```

### 実 Lambda コードのアップロード（必須）

**スタックだけでは動作しません。** CloudFormation はこの規模のハンドラをインラインに
書けないため、`template.yaml` は `NotImplementedError` を投げるプレースホルダを
持っています。`scripts/deploy.sh` を使った場合はこの手順は済んでいます。手動で
デプロイした場合は、ここで実行してください:

```bash
cd integrations/new-relic/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-new-relic-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-new-relic-integration-shipper \
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
| `S3AccessPointArn` | ARN of the S3 Access Point for FSx for ONTAP audit logs |
| `NewRelicLicenseKeySecretArn` | ARN of the Secrets Manager secret containing the New Relic License Key |
| `S3BucketName` | S3 bucket name for event notification |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `NewRelicRegion` | `US` | New Relic account region (US or EU) |
| `S3KeyPrefix` | `''` (empty) | S3 key prefix filter |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 3: New Relic 側の設定

### Parsing Rule

1. **Logs** → **Parsing** → **Create parsing rule**
2. NRQL: `SELECT * FROM Log WHERE source='fsxn-ontap'`
3. Grok pattern でフィールド抽出

### Alert Condition

```sql
SELECT count(*) FROM Log
WHERE source = 'fsxn-ontap' AND attributes.result = 'Failure'
FACET attributes.user
```

## Step 4: 動作確認

```bash
# テストファイルをアップロード
aws s3 cp integrations/datadog/tests/test_data/sample_audit_logs.json \
  s3://$BUCKET_NAME/audit/svm-prod-01/test.json
```

New Relic Logs UI → `source:fsxn-ontap` で検索。

## トラブルシューティング

- **HTTP 403**: License Key が正しいか確認
- **HTTP 429**: レート制限。Lambda 同時実行数を制限
- **ログ未到着**: CloudWatch Logs で Lambda エラーを確認

## デプロイの検証

```bash
bash integrations/new-relic/scripts/verify.sh
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
bash integrations/new-relic/scripts/cleanup.sh          # スタックのみ
bash integrations/new-relic/scripts/cleanup.sh --all    # + シークレット・レイヤー・S3 テストデータ
bash integrations/new-relic/scripts/cleanup.sh --all -y  # 非対話
```

共有リソース（S3 アクセスポイント、監査ログバケット、FPolicy Fargate スタック、
前提スタック）は削除されません。削除順と意図的に残すものについては
[ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md)
を参照してください。

## 関連ドキュメント

- [ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md) — 全ベンダー共通の手順
- [前提条件](../../../../docs/ja/prerequisites.md) — FSx for ONTAP、監査ログ有効化、S3 アクセスポイント
- [デプロイガイド](../../../../docs/ja/deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
