# 19.1 EMS Webhook 用 Splunk テンプレートのデプロイ

## 概要

`integrations/splunk-serverless/template.yaml` を使用して EMS Webhook パス（API Gateway HTTP API + Lambda）をデプロイし、ONTAP EMS イベントを Splunk HEC へ転送する手順書。

> **テンプレート配置に関する注記**: 本タスク作成当初は `shared/templates/ems-webhook-apigw.yaml`（ベンダー中立の共有テンプレート、パラメータ: `LambdaFunctionArn`, `WebhookAuthMode` 等）を単独デプロイし、別途ベンダー固有の EMS Lambda を紐付ける想定だった。実際には `integrations/splunk-serverless/template.yaml` が EMS Webhook 機能（`EmsHttpApi` / `EmsWebhookFunction` / `EmsDeadLetterQueue` / `EmsErrorAlarm` 一式）を単一スタックとして既に内包しているため、本手順では `template.yaml` を直接デプロイする方式に統一する。共有テンプレート版（REST API Gateway + `SHARED_SECRET`/`API_KEY` 認証モード等）を使いたい場合は、別途 `EmsWebhookFunction` の ARN を `LambdaFunctionArn` パラメータに渡して `shared/templates/ems-webhook-apigw.yaml` を追加デプロイすること（両者は排他ではない）。

## 前提条件

- AWS CLI v2 が設定済み（`ap-northeast-1` リージョン）
- Splunk HEC トークンが Secrets Manager に登録済み（`splunk/fsxn-hec-token`）
- Splunk に `fsxn_ems` Index が作成済み
- EMS Webhook 用 API キーが Secrets Manager に登録済み（テンプレートパラメータ `EmsApiKeySecretArn` に渡す）
- S3 Access Point for FSx for ONTAP（audit log 経路用、`S3AccessPointArn` パラメータ）が作成済み

## 手順

### Step 1: Lambda ハンドラの確認

```bash
# EMS ハンドラの存在確認
ls -la integrations/splunk-serverless/lambda/ems_handler.py

# ユニットテストを実行して現在の実装が壊れていないか確認
python3 -m pytest integrations/splunk-serverless/tests/test_ems_handler.py -v
```

### Step 2: テンプレートの確認

```bash
# テンプレートの存在確認
ls -la integrations/splunk-serverless/template.yaml

# cfn-lint でテンプレートを検証
cfn-lint integrations/splunk-serverless/template.yaml
```

**確認ポイント:**
- `AWS::ApiGatewayV2::Api`（HTTP API）が定義されていること（`EmsHttpApi`）
- `EmsWebhookFunction` Lambda 関数リソースが定義されていること
- IAM ロールが最小権限で定義されていること（`EmsLambdaExecutionRole`）

### Step 3: CloudFormation スタックのデプロイ

```bash
# Splunk 統合スタック（audit log + EMS Webhook を含む）のデプロイ
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template.yaml \
  --stack-name fsxn-splunk-integration \
  --region ap-northeast-1 \
  --parameter-overrides \
    S3AccessPointArn="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap" \
    S3BucketName="<audit-log-bucket-name>" \
    SplunkHecTokenSecretArn="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk/fsxn-hec-token-XXXXXX" \
    SplunkHecEndpoint="https://<splunk-hec-host>:8088" \
    EmsApiKeySecretArn="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ems-webhook-api-key-XXXXXX" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

**注意:** このスタックは audit log 経路（`LogShipperFunction`）と EMS Webhook 経路（`EmsWebhookFunction`）を同一スタックにまとめて定義している。EMS Webhook のみを検証したい場合でも、`S3AccessPointArn` / `S3BucketName` は必須パラメータのため省略できない。

### Step 4: スタックステータスの確認

```bash
# スタックステータスを確認
aws cloudformation describe-stacks \
  --stack-name fsxn-splunk-integration \
  --region ap-northeast-1 \
  --query 'Stacks[0].StackStatus' \
  --output text
```

**期待される出力:** `CREATE_COMPLETE` または `UPDATE_COMPLETE`

### Step 5: Lambda 設定の確認

```bash
# Lambda 関数の設定を確認
aws lambda get-function-configuration \
  --function-name fsxn-splunk-integration-ems-webhook \
  --region ap-northeast-1 \
  --query '{Runtime: Runtime, Timeout: Timeout, MemorySize: MemorySize}'
```

**確認ポイント:**
- Runtime: `python3.12`
- Timeout: `30`、MemorySize: `256`（テンプレートのデフォルト値）

### Step 6: Lambda 環境変数の確認

```bash
# 環境変数を確認
aws lambda get-function-configuration \
  --function-name fsxn-splunk-integration-ems-webhook \
  --region ap-northeast-1 \
  --query 'Environment.Variables'
```

**期待される設定:**
```json
{
  "SPLUNK_HEC_ENDPOINT": "https://<splunk-hec-host>:8088",
  "EMS_API_KEY_SECRET_ARN": "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ems-webhook-api-key-XXXXXX",
  "HEC_TOKEN_SECRET_ARN": "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk/fsxn-hec-token-XXXXXX"
}
```

**sourcetype/source/index 設定（`ems_handler.py` 内の定数、環境変数ではない）:**
- `sourcetype`: `fsxn:ontap:ems`
- `source`: `fsxn-ems`
- `index`: `fsxn_ems`

### Step 7: API Gateway エンドポイントの確認

```bash
# API Gateway エンドポイント URL を取得
aws cloudformation describe-stacks \
  --stack-name fsxn-splunk-integration \
  --region ap-northeast-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`EmsApiEndpoint`].OutputValue' \
  --output text
```

### Step 8: HEC 認証ヘッダーの確認

Lambda が Splunk HEC に送信する際のヘッダー形式を確認:

```
Authorization: Splunk <token>
Content-Type: application/json
```

ONTAP から Lambda への Webhook 呼び出し自体は `x-api-key` ヘッダー（`EmsApiKeySecretArn` の値と照合）で認証する。

## 検証チェックリスト

- [ ] `integrations/splunk-serverless/template.yaml` が cfn-lint を通過
- [ ] CloudFormation スタックステータスが `CREATE_COMPLETE`
- [ ] `ems_handler.py` のユニットテストが通過（`test_ems_handler.py`）
- [ ] Lambda 環境変数に `EMS_API_KEY_SECRET_ARN` / `HEC_TOKEN_SECRET_ARN` / `SPLUNK_HEC_ENDPOINT` が設定
- [ ] API Gateway エンドポイント（`EmsApiEndpoint` 出力）が取得できた
- [ ] HEC 認証ヘッダーが `Authorization: Splunk <token>` 形式
- [ ] Webhook 呼び出し自体は `x-api-key` ヘッダーで認証される

## トラブルシューティング

### スタックの CREATE_FAILED 遷移

- **原因**: IAM ロール名の競合、パラメータ不正（`S3AccessPointArn` / `SplunkHecTokenSecretArn` 等の必須パラメータ未指定）
- **解決**: `aws cloudformation describe-stack-events` でエラー詳細を確認

### API Gateway の未作成

- **原因**: テンプレートの `AWS::ApiGatewayV2::Api` 定義に問題
- **解決**: テンプレートの `EmsHttpApi` / `EmsHttpApiIntegration` / `EmsHttpApiRoute` リソースを確認

### Lambda が返す 401

- **原因**: `x-api-key` ヘッダーが未指定、または `EmsApiKeySecretArn` の値と不一致
- **解決**: `ems_handler.py` の `_validate_api_key` ロジックと Secrets Manager の値を確認

## 関連タスク

- Task 19.2: ARP ランサムウェア検知アラートテスト
- Task 19.3: Quota 超過アラートテスト
- Task 17.2: デモシナリオ2「ランサムウェア検知」（シミュレーション版）
- Task 20.1: FPolicy 共有テンプレートのデプロイ（`fpolicy_handler.py` を配線する `template-fpolicy.yaml`）
