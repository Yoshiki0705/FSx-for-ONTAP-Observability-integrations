# Splunk Serverless セットアップガイド

🌐 [English](../en/setup-guide.md)

## 概要

Amazon FSx for NetApp ONTAP の監査ログを Splunk HEC (HTTP Event Collector) 経由でサーバーレスに配信する統合のセットアップ手順です。

> **既存パターンとの違い**: [AWS ブログ](https://aws.amazon.com/jp/blogs/news/auditing-user-and-administrative-actions-on-amazon-fsx-for-netapp-ontap-using-splunk/)の EC2 ベース（syslog-ng + Universal Forwarder）を完全サーバーレスアーキテクチャに置き換えます。

## 前提条件

以下が準備されていることを確認してください:

- **AWS アカウント**: FSx for ONTAP が稼働中であること
- **Splunk アカウント**: Splunk Enterprise または Splunk Cloud（HEC 機能が有効）
- **AWS CLI v2**: 設定済み（`aws configure` 完了）
- **FSx for ONTAP 監査ログ**: S3 バケットに出力されていること
- **前提リソーススタック**: [前提リソース](../../../../docs/ja/prerequisites.md)がデプロイ済み

## Step 1: Splunk HEC トークンの作成

### 1.1 Splunk で HEC トークンを発行

1. Splunk Web にログイン
2. **Settings** → **Data Inputs** → **HTTP Event Collector** に移動
3. **Global Settings** で HEC が有効であることを確認
4. **New Token** をクリック
5. 設定:
   - Name: `fsxn-audit-log-shipper`
   - Source type: `fsxn:ontap:audit`
   - Index: `fsxn_audit`
6. 生成された HEC トークン（UUID 形式）をコピー

> **トークン形式**: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`（8-4-4-4-12 の16進数文字列）

### 1.2 Splunk Index の作成

HEC トークン作成時に指定した Index が存在しない場合は作成します:

```bash
# Splunk CLI（Splunk Enterprise の場合）
splunk add index fsxn_audit -maxDataSize auto_high_volume
```

Splunk Cloud の場合は管理コンソールから Index を作成してください。

## Step 2: AWS Secrets Manager への登録

HEC トークンを AWS Secrets Manager に安全に保存します:

```bash
aws secretsmanager create-secret \
  --name "splunk/fsxn-hec-token" \
  --description "Splunk HEC Token for FSx for ONTAP audit log integration" \
  --secret-string "YOUR_HEC_TOKEN" \
  --region ap-northeast-1
```

### 登録確認

トークンが正しく保存されたことを確認します:

```bash
aws secretsmanager get-secret-value \
  --secret-id "splunk/fsxn-hec-token" \
  --region ap-northeast-1 \
  --query 'SecretString' \
  --output text
```

出力が UUID 形式（`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）であることを確認してください。

## Step 3: CloudFormation デプロイ

### 推奨: デプロイスクリプトを使う

このスクリプトはスタックのデプロイと実 Lambda コードのアップロードを**両方**行います。
CloudFormation テンプレートはハンドラをインラインに持てないため、1 ステップで動作する
統合を得られる唯一の経路です。

```bash
export SPLUNK_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export SPLUNK_HEC_ENDPOINT="..."
export S3_BUCKET_NAME="..."

bash integrations/splunk-serverless/scripts/deploy.sh
```

初回は **3〜5 分**かかり、そのほとんどは CloudFormation が IAM ロール・Lambda・
スケジューラ・アラームを作成する時間です。変更のないスタックへの再実行は数秒で
終わります。対応する変数の一覧は `--help` で確認できます。

`--all` を付けると EMS / FPolicy スタックも対象になります。

> スクリプト実行前に `ALARM_TOPIC_ARN` に SNS トピック ARN を設定すると、CloudWatch
> アラームが通知されるようになります。未設定の場合アラームは通知アクションなしで
> 作成され、コンソールには表示されますが誰にも通知されません。

### 代替: CloudFormation を手動でデプロイする

### 3.1 スタックのデプロイ

```bash
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template.yaml \
  --stack-name fsxn-splunk-integration \
  --parameter-overrides \
    S3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    SplunkHecTokenSecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk/fsxn-hec-token-XXXXXX \
    SplunkHecEndpoint=https://your-splunk-instance:8088 \
    S3BucketName=your-audit-log-bucket \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

### 3.2 デプロイ確認

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-splunk-integration \
  --region ap-northeast-1 \
  --query 'Stacks[0].StackStatus' \
  --output text
```

出力が `CREATE_COMPLETE` または `UPDATE_COMPLETE` であることを確認してください。

### 実 Lambda コードのアップロード（必須）

**スタックだけでは動作しません。** CloudFormation はこの規模のハンドラをインラインに
書けないため、`template.yaml` は `NotImplementedError` を投げるプレースホルダを
持っています。`scripts/deploy.sh` を使った場合はこの手順は済んでいます。手動で
デプロイした場合は、ここで実行してください:

```bash
cd integrations/splunk-serverless/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-splunk-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-splunk-integration-shipper \
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
| `SplunkHecTokenSecretArn` | ARN of the Secrets Manager secret containing the Splunk HEC token |
| `SplunkHecEndpoint` | Splunk HEC endpoint URL (e.g., https://splunk.example.com:8088) |
| `S3BucketName` | S3 bucket name for event notification |
| `EmsApiKeySecretArn` | ARN of the Secrets Manager secret containing the EMS webhook API key |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `S3KeyPrefix` | `''` (empty) | S3 key prefix filter for audit log objects |
| `SplunkIndex` | `fsxn_audit` | Splunk index for audit logs |
| `SplunkSourcetype` | `fsxn:ontap:audit` | Splunk sourcetype assigned to every shipped audit event |
| `VerifySSL` | `true` | Verify SSL certificates (set false for self-signed certs) |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 4: テストイベント送信

### 4.1 Lambda 関数の手動呼び出し

サンプルの S3 イベントを使用して Lambda を呼び出します:

```bash
aws lambda invoke \
  --function-name fsxn-splunk-integration-shipper \
  --payload file://integrations/splunk-serverless/tests/test_data/sample_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json

cat response.json
```

期待される出力:

```json
{"statusCode": 200, "body": {"total_logs": 5, "total_shipped": 5}}
```

### 4.2 CloudWatch Logs の確認

```bash
aws logs tail \
  /aws/lambda/fsxn-splunk-integration-shipper \
  --since 5m \
  --region ap-northeast-1
```

ログに `Successfully shipped` が含まれていることを確認してください。

## Step 5: Splunk Search でのログ到着確認

### 5.1 SPL クエリの実行

Splunk Search で以下のクエリを実行します:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m
```

### 5.2 フィールド確認チェックリスト

到着したイベントに以下のフィールドが含まれていることを確認します:

| フィールド | 説明 | 必須 |
|-----------|------|------|
| `host` | SVM 名 | ✅ |
| `source` | ソース識別子 | ✅ |
| `sourcetype` | `fsxn:ontap:audit` | ✅ |
| `index` | `fsxn_audit` | ✅ |
| `event_type` | イベント種別 | ✅ |
| `user` | 操作ユーザー | ✅ |
| `operation` | 操作種別 | ✅ |
| `path` | ファイルパス | ✅ |
| `result` | 操作結果 | ✅ |
| `svm` | SVM 名 | ✅ |

## Step 6: E2E 検証手順

以下の手順で End-to-End の動作を検証します。各ステップの最大待機時間は **5分** です。

### 6.1 テストイベント送信

```bash
aws lambda invoke \
  --function-name fsxn-splunk-integration-shipper \
  --payload file://integrations/splunk-serverless/tests/test_data/sample_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json
```

**期待結果**: `statusCode` が `200` であること

### 6.2 CloudWatch Logs 確認（最大待機: 1分）

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-splunk-integration-shipper \
  --start-time $(date -d '5 minutes ago' +%s000 2>/dev/null || date -v-5M +%s000) \
  --filter-pattern "Successfully shipped" \
  --region ap-northeast-1
```

**期待結果**: `Successfully shipped` を含むログエントリが表示されること

### 6.3 Splunk Search 確認（最大待機: 5分）

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m
```

**期待結果**: 1件以上のイベントが返されること

### 6.4 レイテンシ測定

S3 オブジェクト作成時刻と Splunk の `_indextime` の差分を記録します。通常 30〜120 秒以内にログが検索可能になります。

### 6.5 スクリーンショット撮影

以下のスクリーンショットを撮影し、`docs/screenshots/splunk/` に保存します:

- Lambda CloudWatch Logs（`Successfully shipped` が表示されている状態）
- Splunk Search 結果（SPL クエリ、結果件数、展開されたイベント）
- Splunk ダッシュボード（FSx for ONTAP 監査ログデータを表示するパネル）

![FSx for ONTAP の監査イベントが表示された Splunk 検索](../../screenshots/splunk-e2e-search-fsxn-audit-xml.png)

> Docker 上の Splunk Enterprise に対して取得したものです。この実行の適用範囲は
> [Splunk 動作確認結果](../../../../docs/ja/verification-results-splunk.md)
> を参照してください。

## トラブルシューティング

### ネットワーク接続の問題

**症状**: Lambda が Splunk HEC エンドポイントに接続できない

**診断**:

```bash
# HEC エンドポイントへの接続テスト
curl -k -s -o /dev/null -w "%{http_code}" \
  https://your-splunk-instance:8088/services/collector/health
```

**期待結果**: HTTP `200` が返ること

**解決策**:
- Splunk が VPC 内にある場合: Lambda を同じ VPC に配置し、NAT Gateway を設定
- Splunk Cloud の場合: HEC エンドポイントがパブリックアクセス可能であることを確認
- セキュリティグループで Lambda からポート 8088 へのアウトバウンドが許可されていることを確認

### 無効なトークン

**症状**: Lambda ログに `Invalid token format` または Splunk から HTTP 403 が返る

**診断**:

```bash
# トークン形式の確認（UUID: 8-4-4-4-12）
aws secretsmanager get-secret-value \
  --secret-id "splunk/fsxn-hec-token" \
  --region ap-northeast-1 \
  --query 'SecretString' \
  --output text | grep -E '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
```

**解決策**:
- トークンが UUID 形式（`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）であることを確認
- Splunk 側で HEC トークンが有効（Enabled）であることを確認
- トークンに割り当てられた Index が正しいことを確認

### SSL 証明書の問題

**症状**: Lambda ログに `SSL: CERTIFICATE_VERIFY_FAILED` エラー

**診断**:

```bash
# SSL 証明書の確認
openssl s_client -connect your-splunk-instance:8088 -showcerts </dev/null 2>/dev/null | openssl x509 -noout -dates
```

**解決策**:
- 自己署名証明書を使用している場合: CloudFormation パラメータで `VerifySSL=false` を設定

```bash
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template.yaml \
  --stack-name fsxn-splunk-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    SplunkHecTokenSecretArn=$SECRET_ARN \
    SplunkHecEndpoint=https://your-splunk-instance:8088 \
    S3BucketName=$BUCKET_NAME \
    VerifySSL=false \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

> **注意**: 本番環境では正規の SSL 証明書を使用することを強く推奨します。`VerifySSL=false` は検証環境でのみ使用してください。

### IAM 権限の問題

**症状**: Lambda ログに `AccessDenied` エラー

**診断**:

```bash
# CloudWatch Logs で AccessDenied を検索
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-splunk-integration-shipper \
  --filter-pattern "AccessDenied" \
  --region ap-northeast-1
```

**解決策**:
- Lambda 実行ロールに以下の権限が付与されていることを確認:
  - `secretsmanager:GetSecretValue`（HEC トークン取得）
  - `s3:GetObject`（S3 Access Point 経由のオブジェクト読み取り）
  - `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`（CloudWatch Logs）
- S3 Access Point のリソースポリシーが Lambda ロールを許可していることを確認
- IAM ポリシーのリソース ARN に `/object/*` サフィックスが含まれていることを確認:

```
arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap/object/*
```

### ログが Splunk に届かない場合の総合チェックリスト

1. Lambda が正常に呼び出されているか（CloudWatch Logs 確認）
2. HEC エンドポイントに接続できるか（curl テスト）
3. HEC トークンが有効か（UUID 形式 + Splunk 側で Enabled）
4. SSL 証明書が有効か（自己署名の場合は `VerifySSL=false`）
5. IAM 権限が正しいか（AccessDenied がないか確認）
6. DLQ にメッセージが溜まっていないか確認:

```bash
aws sqs get-queue-attributes \
  --queue-url $(aws cloudformation describe-stack-resource \
    --stack-name fsxn-splunk-integration \
    --logical-resource-id DeadLetterQueue \
    --query 'StackResourceDetail.PhysicalResourceId' \
    --output text) \
  --attribute-names ApproximateNumberOfMessages \
  --region ap-northeast-1
```

## デプロイの検証

```bash
bash integrations/splunk-serverless/scripts/verify.sh
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
bash integrations/splunk-serverless/scripts/cleanup.sh          # stacks only
bash integrations/splunk-serverless/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/splunk-serverless/scripts/cleanup.sh --all -y  # non-interactive
```

共有リソース（S3 アクセスポイント、監査ログバケット、FPolicy Fargate スタック、
前提スタック）は削除されません。削除順と意図的に残すものについては
[ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md)
を参照してください。

## 関連ドキュメント

- [ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md) — 全ベンダー共通の手順
- [前提条件](../../../../docs/ja/prerequisites.md) — FSx for ONTAP、監査ログ有効化、S3 アクセスポイント
- [デプロイガイド](../../../../docs/ja/deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
