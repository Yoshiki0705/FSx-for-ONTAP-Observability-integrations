# 20.2 Splunk 向け FPolicy 受信 Lambda の作成

## 概要

EventBridge カスタムバス（source: `fpolicy.fsxn`）をサブスクライブする Lambda を作成し、FPolicy ファイル操作イベントを Splunk HEC フォーマットに変換して送信する手順書。

## 前提条件

- Task 20.1 が完了済み（FPolicy スタックがデプロイ済み）
- EventBridge カスタムバス（`fpolicy-fsxn-bus`）が存在すること
- Splunk HEC トークンが Secrets Manager に登録済み（`splunk/fsxn-hec-token`）
- Splunk に `fsxn_audit` Index が作成済み

## アーキテクチャ概要

```
EventBridge Custom Bus (source: fpolicy.fsxn)
    ↓ (Rule: match source)
Lambda: fsxn-splunk-fpolicy-handler
    ↓ (HTTP POST)
Splunk HEC (/services/collector/event)
    sourcetype=fsxn:fpolicy:event
    source=fsxn-fpolicy
```

## 手順

### Step 1: Lambda 関数コードの確認

```bash
# FPolicy ハンドラーの存在確認
ls -la integrations/splunk-serverless/lambda/fpolicy_handler.py

# コードの確認（主要な関数）
head -50 integrations/splunk-serverless/lambda/fpolicy_handler.py
```

**確認ポイント:**
- `lambda_handler` 関数が定義されていること
- EventBridge イベント形式を受け取る設計であること
- Splunk HEC フォーマットへの変換ロジックが含まれること

### Step 2: フィールドマッピングの確認

Lambda が FPolicy イベントから Splunk HEC `event` オブジェクトにマッピングするフィールド:

| FPolicy フィールド | Splunk event フィールド | 説明 |
|-------------------|----------------------|------|
| `operation` | `operation` | ファイル操作種別（create, write, rename, delete） |
| `file_path` | `file_path` | 操作対象ファイルのパス |
| `user` | `user` | 操作を実行したユーザー |
| `client_ip` | `client_ip` | クライアントの IP アドレス |

**HEC イベント構造:**
```json
{
  "time": 1705312000,
  "host": "<svm-name>",
  "source": "fsxn-fpolicy",
  "sourcetype": "fsxn:fpolicy:event",
  "index": "fsxn_audit",
  "event": {
    "operation": "create",
    "file_path": "/vol/data/documents/report.docx",
    "user": "DOMAIN\\username",
    "client_ip": "10.0.x.x",
    "timestamp": "2026-01-20T10:00:00Z"
  }
}
```

### Step 3: Lambda のデプロイ

Lambda 関数が CloudFormation テンプレートに含まれている場合:

```bash
# テンプレートに FPolicy Lambda リソースが含まれているか確認
grep -A 5 "FPolicyHandler\|fpolicy_handler" integrations/splunk-serverless/template.yaml

# スタック更新（Lambda リソースを追加）
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template.yaml \
  --stack-name fsxn-splunk-integration \
  --region ap-northeast-1 \
  --parameter-overrides \
    HecSecretArn="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:splunk/fsxn-hec-token-XXXXXX" \
    SplunkHecEndpoint="https://<splunk-hec-host>:8088/services/collector/event" \
    FPolicyEventBusName="fpolicy-fsxn-bus" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

### Step 4: Lambda 設定の確認

```bash
# Lambda 関数の設定を確認
aws lambda get-function-configuration \
  --function-name fsxn-splunk-fpolicy-handler \
  --region ap-northeast-1 \
  --query '{Runtime: Runtime, Timeout: Timeout, MemorySize: MemorySize, Environment: Environment.Variables}'
```

**期待される設定:**
```json
{
  "Runtime": "python3.12",
  "Timeout": 60,
  "MemorySize": 256,
  "Environment": {
    "SPLUNK_HEC_SECRET_ARN": "arn:aws:secretsmanager:...",
    "SPLUNK_HEC_ENDPOINT": "https://<splunk-hec-host>:8088/services/collector/event",
    "SPLUNK_SOURCETYPE": "fsxn:fpolicy:event",
    "SPLUNK_SOURCE": "fsxn-fpolicy",
    "SPLUNK_INDEX": "fsxn_audit"
  }
}
```

### Step 5: EventBridge ルールの確認

```bash
# EventBridge ルールの確認
aws events list-rules \
  --event-bus-name fpolicy-fsxn-bus \
  --region ap-northeast-1

# ルールのターゲット確認
aws events list-targets-by-rule \
  --rule <rule-name> \
  --event-bus-name fpolicy-fsxn-bus \
  --region ap-northeast-1
```

**確認ポイント:**
- ルールの EventPattern に `"source": ["fpolicy.fsxn"]` が含まれること
- ターゲットに Lambda 関数 ARN が設定されていること

### Step 6: Lambda の手動テスト

```bash
# テストイベントで Lambda を呼び出し
aws lambda invoke \
  --function-name fsxn-splunk-fpolicy-handler \
  --region ap-northeast-1 \
  --payload '{
    "version": "0",
    "source": "fpolicy.fsxn",
    "detail-type": "FPolicy File Operation",
    "detail": {
      "operation": "create",
      "file_path": "/vol/data/test-file.txt",
      "user": "TESTDOMAIN\\testuser",
      "client_ip": "10.0.x.x",
      "timestamp": "2026-01-20T10:00:00Z",
      "svm_name": "svm-prod-01"
    }
  }' \
  --cli-binary-format raw-in-base64-out \
  /tmp/fpolicy-lambda-response.json

# レスポンスの確認
cat /tmp/fpolicy-lambda-response.json
```

**期待される出力:**
```json
{"statusCode": 200, "body": {"status": "forwarded", "operation": "create"}}
```

### Step 7: CloudWatch Logs の確認

```bash
# Lambda ログを確認
aws logs tail \
  /aws/lambda/fsxn-splunk-fpolicy-handler \
  --since 5m \
  --region ap-northeast-1 \
  --format short
```

**確認ポイント:**
- `Forwarded FPolicy event to Splunk HEC` ログが表示されること
- HEC レスポンスが `{"text":"Success","code":0}` であること

## 検証チェックリスト

- [ ] `integrations/splunk-serverless/lambda/fpolicy_handler.py` が存在する
- [ ] Lambda 関数がデプロイされている
- [ ] 環境変数に `SPLUNK_SOURCETYPE=fsxn:fpolicy:event` が設定されている
- [ ] 環境変数に `SPLUNK_SOURCE=fsxn-fpolicy` が設定されている
- [ ] EventBridge ルールが `fpolicy.fsxn` source にマッチする
- [ ] EventBridge ルールのターゲットに Lambda が設定されている
- [ ] テストイベントで Lambda が正常に動作する（HTTP 200）
- [ ] `event` オブジェクトに `operation`, `file_path`, `user`, `client_ip` がマッピングされている

## トラブルシューティング

### Lambda でのイベントの未受信

- **原因**: EventBridge ルールのターゲット設定が不正
- **解決**: `aws events list-targets-by-rule` でターゲット Lambda ARN を確認

### HEC 送信の失敗

- **原因**: HEC トークンが無効、エンドポイント接続不可
- **解決**: Secrets Manager のトークンと HEC エンドポイントの接続性を確認

### フィールドマッピングが不正

- **原因**: EventBridge イベント構造と Lambda のパース処理が不一致
- **解決**: CloudWatch Logs で受信イベントの構造を確認し、パース処理を修正

### Lambda タイムアウト

- **原因**: HEC エンドポイントへの接続が遅い
- **解決**: Lambda のタイムアウト値を増やす、VPC 設定を確認

## 関連タスク

- Task 20.1: FPolicy 共有テンプレートのデプロイ
- Task 20.3: ONTAP FPolicy 外部エンジン設定
- Task 20.4: FPolicy ファイル操作テスト
