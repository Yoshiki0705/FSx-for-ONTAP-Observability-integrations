# FPolicy → Grafana Cloud Loki 統合セットアップ

🌐 **日本語**（このページ） | [English](../en/fpolicy-setup.md)

## 概要

ONTAP FPolicy External Engine からのリアルタイムファイル操作イベントを Grafana Cloud Loki に転送するセットアップ手順。

**アーキテクチャ:**
```
ONTAP SVM (FPolicy)
    | TCP:9898 (async, no TLS)
ECS Fargate Task (FPolicy Server)
    | SQS Queue
Bridge Lambda (SQS -> EventBridge)
    | EventBridge (source: fpolicy.fsxn)
Grafana Vendor Lambda
    | OTLP Gateway / Loki Push API
Grafana Cloud Loki
```

**ラベル設定:** `{job="fsxn-fpolicy", source="ontap", operation="<op>"}`

## 前提条件

- Grafana Cloud アカウント（`logs:write` スコープの API Key）
- AWS Secrets Manager に認証情報が登録済み
- FSx for ONTAP SVM で CIFS プロトコルが有効
- VPC 内にプライベートサブネットが存在

## Step 1: FPolicy 共有インフラのデプロイ

FPolicy 共有インフラ（ECS Fargate + SQS + EventBridge）をデプロイします。

```bash
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-apigw.yaml \
  --stack-name fsxn-fp-srv \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ComputeType=fargate \
    VpcId=<vpc-id> \
    SubnetIds=<private-subnet-1>,<private-subnet-2> \
    FsxnSvmSecurityGroupId=<fsxn-svm-sg-id> \
    ContainerImage=<account-id>.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:v2-timeout-fix \
  --region ap-northeast-1
```

**確認:**
```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-fp-srv \
  --query "Stacks[0].StackStatus" \
  --region ap-northeast-1 \
  --output text
# 期待値: CREATE_COMPLETE
```

## Step 2: Grafana Vendor Lambda のデプロイ

EventBridge から Grafana Cloud への転送 Lambda をデプロイします。

```bash
aws cloudformation deploy \
  --template-file integrations/grafana/template-fpolicy.yaml \
  --stack-name fsxn-grafana-fpolicy \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    GrafanaCredentialsSecretArn=<secret-arn> \
    LokiEndpoint=https://otlp-gateway-prod-ap-northeast-0.grafana.net/otlp \
    EventBusName=fsxn-fpolicy-events \
  --region ap-northeast-1
```

**Lambda コードのデプロイ:**
```bash
cd integrations/grafana/lambda
zip fpolicy.zip fpolicy_handler.py
aws lambda update-function-code \
  --function-name fsxn-grafana-fpolicy-handler \
  --zip-file fileb://fpolicy.zip \
  --region ap-northeast-1
rm fpolicy.zip
```

## Step 3: ONTAP FPolicy External Engine 設定

### 3.1 Fargate タスク IP の取得

```bash
TASK_ARN=$(aws ecs list-tasks \
  --cluster fsxn-fp-srv-fpolicy \
  --service-name fsxn-fp-srv-fpolicy-server \
  --region ap-northeast-1 \
  --query 'taskArns[0]' \
  --output text)

FARGATE_IP=$(aws ecs describe-tasks \
  --cluster fsxn-fp-srv-fpolicy \
  --tasks "$TASK_ARN" \
  --region ap-northeast-1 \
  --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value' \
  --output text)

echo "Fargate Task IP: $FARGATE_IP"
```

### 3.2 ONTAP CLI で FPolicy 設定

ONTAP CLI に SSH 接続し、以下のコマンドを実行します。

```bash
# ONTAP CLI に接続
ssh admin@<management-ip>
```

#### 外部エンジンの作成

```
vserver fpolicy policy external-engine create \
  -vserver <svm-name> \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

#### FPolicy イベントの作成

```
vserver fpolicy policy event create \
  -vserver <svm-name> \
  -event-name fpolicy_cifs_events \
  -protocol cifs \
  -file-operations create,write,rename,delete
```

#### FPolicy ポリシーの作成

```
vserver fpolicy policy create \
  -vserver <svm-name> \
  -policy-name fpolicy_lambda_policy \
  -events fpolicy_cifs_events \
  -engine fpolicy_lambda_engine
```

#### FPolicy ポリシーの有効化

```
vserver fpolicy enable \
  -vserver <svm-name> \
  -policy-name fpolicy_lambda_policy \
  -sequence-number 1
```

### 3.3 設定確認

```
vserver fpolicy show -vserver <svm-name>
```

**期待される出力:**
```
Vserver    Policy Name              Sequence  Status   Engine
---------- ------------------------ --------- -------- ------
<svm-name> fpolicy_lambda_policy    1         on       fpolicy_lambda_engine
```

## Step 4: 接続ヘルスチェック

### 4.1 ECS CloudWatch Logs で KeepAlive 確認

```bash
aws logs tail \
  /ecs/fsxn-fp-srv-fpolicy-server \
  --since 1m \
  --region ap-northeast-1 \
  --format short
```

**期待される出力（約6秒間隔）:**
```
[KeepAlive] Received from ONTAP (session: <session-id>)
```

### 4.2 ONTAP 接続状態の確認

```
vserver fpolicy policy external-engine show-connected \
  -vserver <svm-name> \
  -engine-name fpolicy_lambda_engine
```

## Step 5: Grafana Explore でログ確認

ファイル操作を実行後、Grafana Explore で確認します。

**LogQL クエリ:**
```
{job="fsxn-fpolicy"} | json
```

**操作別フィルタ:**
```
{job="fsxn-fpolicy"} | json | operation="create"
```

**期待されるフィールド:** `operation`, `file_path`, `user`, `client_ip`

## トラブルシューティング

### KeepAlive メッセージの未表示

1. Fargate タスクが Running 状態か確認
2. セキュリティグループで TCP:9898 インバウンドが許可されているか確認
3. ONTAP SVM から Fargate タスク IP への接続が可能か確認

### Fargate タスク IP 変更時

```bash
# 自動更新スクリプトを使用
bash shared/scripts/fpolicy-update-engine-ip.sh --auto
```

### Lambda エラー

```bash
aws logs tail \
  /aws/lambda/fsxn-grafana-fpolicy-handler \
  --since 5m \
  --filter-pattern "ERROR" \
  --region ap-northeast-1
```
