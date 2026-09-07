# EMS Webhook → Grafana Cloud Loki セットアップ

🌐 **日本語**（このページ） | [English](../en/ems-webhook-setup.md)

## 概要

ONTAP EMS (Event Management System) イベントを API Gateway 経由で Grafana Cloud Loki に転送する設定手順。

## アーキテクチャ

```
ONTAP EMS → HTTPS Webhook → API Gateway (REST) → Lambda → Grafana Cloud OTLP Gateway
```

## 前提条件

- FSx for ONTAP ファイルシステムが稼働中
- Grafana Cloud アカウント（Loki 有効）
- AWS Secrets Manager に認証情報が登録済み

## Step 1: インフラデプロイ

### 1.1 EMS Lambda スタックのデプロイ

```bash
aws cloudformation deploy \
  --template-file integrations/grafana/template-ems.yaml \
  --stack-name fsxn-grafana-ems \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    GrafanaCredentialsSecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:grafana/fsxn-loki-credentials-XXXXXX \
    LokiEndpoint=https://otlp-gateway-prod-ap-northeast-0.grafana.net/otlp \
    EmsParserLayerArn=arn:aws:lambda:ap-northeast-1:123456789012:layer:fsxn-ems-parser:1 \
  --region ap-northeast-1
```

### 1.2 API Gateway スタックのデプロイ

```bash
# Lambda ARN をスタック出力から取得
LAMBDA_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-grafana-ems \
  --query "Stacks[0].Outputs[?OutputKey=='EmsHandlerFunctionArn'].OutputValue" \
  --output text --region ap-northeast-1)

# API Gateway スタックをデプロイ
aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-grafana-ems-webhook \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides LambdaFunctionArn=$LAMBDA_ARN \
  --region ap-northeast-1
```

### 1.3 API Gateway エンドポイント URL の取得

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-grafana-ems-webhook \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpointUrl'].OutputValue" \
  --output text --region ap-northeast-1
```

出力例: `https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems`

## Step 2: ONTAP EMS Webhook 設定

> **注意**: 以下のコマンドは ONTAP CLI (SSH または System Manager CLI) で実行します。

### 2.1 EMS Webhook Destination の作成

```
event notification destination create -name grafana-webhook \
  -rest-api-url https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

### 2.2 EMS Notification の作成

重要なイベントフィルタを使用して通知を作成:

```
event notification create -filter-name important-events \
  -destinations grafana-webhook
```

### 2.3 カスタムフィルタの作成（オプション）

特定のイベントのみを転送する場合:

```
# ARP (Anti-Ransomware Protection) イベントのみ
event filter create -filter-name arp-events
event filter rule add -filter-name arp-events \
  -type include \
  -message-name arw.*

event notification create -filter-name arp-events \
  -destinations grafana-webhook
```

```
# Quota 超過イベントのみ
event filter create -filter-name quota-events
event filter rule add -filter-name quota-events \
  -type include \
  -message-name wafl.quota.*

event notification create -filter-name quota-events \
  -destinations grafana-webhook
```

### 2.4 設定確認

```
event notification show
event notification destination show -name grafana-webhook
```

## Step 3: 動作確認

### 3.1 テストイベント送信（curl）

```bash
curl -X POST https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems \
  -H "Content-Type: application/json" \
  -d '{
    "messageName": "arw.volume.state",
    "severity": "alert",
    "time": "2026-01-15T10:00:00Z",
    "node": "fsxn-node-01",
    "svmName": "svm-prod-01",
    "message": "Anti-ransomware: Volume vol1 state changed to attack-detected",
    "parameters": {
      "volume_name": "vol1",
      "state": "attack-detected",
      "vserver": "svm-prod-01"
    }
  }'
```

期待されるレスポンス:
```json
{"status": "ok", "event_name": "arw.volume.state", "delivered": true}
```

### 3.2 Grafana Explore で確認

Grafana Cloud → Explore → Loki データソース:

```
{service_name="fsxn-ems"}
```

> **注意**: OTLP Gateway 経由の場合、ラベルは `service_name` になります。

### 3.3 severity 別フィルタ

```
{service_name="fsxn-ems"} | json | severity="alert"
```

## トラブルシューティング

### EMS イベントの不着

1. **Lambda CloudWatch Logs を確認**:
   ```bash
   aws logs filter-log-events \
     --log-group-name /aws/lambda/fsxn-grafana-ems-ems-handler \
     --filter-pattern "ERROR" \
     --region ap-northeast-1
   ```

2. **API Gateway アクセスログを確認**:
   ```bash
   aws logs filter-log-events \
     --log-group-name /aws/apigateway/fsxn-grafana-ems-webhook-ems-access \
     --region ap-northeast-1
   ```

3. **ONTAP 側の確認**:
   ```
   event notification destination show -name grafana-webhook
   event log show -messagename arw.*
   ```

### 認証エラー (401/403)

- Secrets Manager の認証情報を確認:
  ```bash
  aws secretsmanager get-secret-value \
    --secret-id grafana/fsxn-loki-credentials \
    --query "SecretString" --output text --region ap-northeast-1
  ```
- Instance ID と API Key が正しいことを確認
- API Key に `logs:write` スコープがあることを確認

## ラベル設計

| ラベル | 値 | 説明 |
|--------|-----|------|
| `service_name` | `fsxn-ems` | OTLP resource attribute (service.name) |
| `source` | `ontap` | ソースシステム |
| `severity` | `alert`, `warning`, etc. | EMS severity レベル（log attribute） |
