# FPolicy パイプライン — クイックデプロイガイド

🌐 **日本語**（このページ） | [English](../en/fpolicy-quick-deploy.md)

FPolicy ファイルアクティビティパイプラインを4ステップでデプロイします。

## 前提条件

- AWS CLI が適切な権限で設定済み
- Docker（buildx 対応、linux/amd64 イメージビルド用）
- CIFS 対応 SVM を持つ FSx for ONTAP ファイルシステム
- Datadog アカウントと API キー

## Step 1: 前提条件デプロイ（ECR + シークレット）

```bash
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-prerequisites.yaml \
  --stack-name fsxn-fpolicy-prerequisites \
  --parameter-overrides \
    DatadogApiKey=<your-datadog-api-key> \
  --region <your-region>
```

出力を確認:
```bash
aws cloudformation describe-stacks --stack-name fsxn-fpolicy-prerequisites \
  --query "Stacks[0].Outputs" --output table
```

## Step 2: FPolicy サーバーイメージのビルド＆プッシュ

```bash
# Get ECR URI from Step 1 output
ECR_URI=$(aws cloudformation describe-stacks --stack-name fsxn-fpolicy-prerequisites \
  --query "Stacks[0].Outputs[?OutputKey=='ECRRepositoryUri'].OutputValue" --output text)

# Authenticate to ECR
aws ecr get-login-password | docker login --username AWS --password-stdin \
  $(echo $ECR_URI | cut -d/ -f1)

# Build and push (MUST be linux/amd64 for Fargate)
docker buildx build --platform linux/amd64 \
  -t ${ECR_URI}:latest --push shared/fpolicy-server/
```

## Step 3: Fargate スタックデプロイ

```bash
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-server-fargate.yaml \
  --stack-name fsxn-fpolicy-server \
  --parameter-overrides \
    VpcId=<your-vpc-id> \
    SubnetIds=<your-private-subnet> \
    FsxnSvmSecurityGroupId=<fsx-svm-security-group-id> \
    ContainerImage=${ECR_URI}:latest \
    AlarmNotificationTopicArn=<sns-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

このスタックは取り込みキューと**その DLQ** を作成します。ベンダー Lambda が処理できない
メッセージは `MaxReceiveCount`（既定 5）回の受信後に DLQ へ退避され、無限リトライには
なりません。DLQ にメッセージが入ると `FPolicyDLQAlarm` が発火します。

`AlarmNotificationTopicArn` は任意ですが設定を推奨します。未設定の場合、DLQ アラームと
バックログアラームは CloudWatch コンソールでしか確認できず、誰にも通知されません。

| 出力 | 用途 |
|------|------|
| `FPolicyQueueArn` | ベンダースタックの `FPolicySqsQueueArn` に渡す（Step 4） |
| `FPolicyDeadLetterQueueUrl` | バックエンドに届かなかったイベントの確認 |

> **キューのタイミングに関する補足**: キューの可視性タイムアウトは 360 秒です。これは
> ベンダー FPolicy Lambda の既定タイムアウト 60 秒の 6 倍で、AWS の推奨に沿っています。
> ベンダー Lambda のタイムアウトを 360 秒より大きくする場合はこちらも引き上げてください。
> 関数タイムアウトがキューの可視性タイムアウトを超えると Lambda がイベントソース
> マッピングを拒否します。

## Step 4: Datadog シッピング Lambda デプロイ

```bash
# Get SQS ARN and Secret ARN from previous stacks
SQS_ARN=$(aws cloudformation describe-stacks --stack-name fsxn-fpolicy-server \
  --query "Stacks[0].Outputs[?OutputKey=='FPolicyQueueArn'].OutputValue" --output text)

SECRET_ARN=$(aws cloudformation describe-stacks --stack-name fsxn-fpolicy-prerequisites \
  --query "Stacks[0].Outputs[?OutputKey=='ApiKeySecretArn'].OutputValue" --output text)

aws cloudformation deploy \
  --template-file integrations/datadog/template-ems-fpolicy.yaml \
  --stack-name fsxn-datadog-ems-fpolicy \
  --parameter-overrides \
    DatadogApiKeySecretArn=${SECRET_ARN} \
    DatadogSite=<your-datadog-site> \
    FPolicySqsQueueArn=${SQS_ARN} \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

## Step 5: ONTAP FPolicy 設定

Fargate タスク IP を取得:
```bash
TASK_ARN=$(aws ecs list-tasks --cluster fsxn-fpolicy-server-cluster \
  --service-name fsxn-fpolicy-server-service --query "taskArns[0]" --output text)
TASK_IP=$(aws ecs describe-tasks --cluster fsxn-fpolicy-server-cluster \
  --tasks $TASK_ARN \
  --query "tasks[0].containers[0].networkInterfaces[0].privateIpv4Address" --output text)
echo "Fargate Task IP: $TASK_IP"
```

ONTAP CLI で設定:
```
vserver fpolicy policy external-engine create -vserver <svm-name> \
  -engine-name fpolicy_aws_engine \
  -primary-servers <task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous \
  -ssl-option no-auth

vserver fpolicy policy event create -vserver <svm-name> \
  -event-name cifs_file_events \
  -protocol cifs \
  -file-operations create,write,rename,delete

vserver fpolicy policy create -vserver <svm-name> \
  -policy-name fpolicy_aws \
  -events cifs_file_events \
  -engine fpolicy_aws_engine \
  -is-mandatory false

vserver fpolicy enable -vserver <svm-name> \
  -policy-name fpolicy_aws \
  -sequence-number 1
```

## 検証

1. ECS ログで KeepAlive を確認:
```bash
aws logs tail /ecs/fsxn-fpolicy-server --follow
```

2. SMB 共有にテストファイルを作成

3. Datadog で確認: `source:fsxn-fpolicy`

## クリーンアップ

```bash
# Disable FPolicy on ONTAP first, then:
aws cloudformation delete-stack --stack-name fsxn-datadog-ems-fpolicy
aws cloudformation delete-stack --stack-name fsxn-fpolicy-server
aws cloudformation delete-stack --stack-name fsxn-fpolicy-prerequisites
```

## スタック依存関係

```
fsxn-fpolicy-prerequisites (ECR + Secret)
  ↓
fsxn-fpolicy-server (Fargate + SQS)
  ↓
fsxn-datadog-ems-fpolicy (Lambda + SQS mapping)
```

削除は逆順で実行してください。
