# FPolicy PoC チェックリスト

🌐 **日本語**（このページ） | [English](../en/fpolicy-poc-checklist.md)

## 目的

FPolicy ファイルアクティビティパイプラインの E2E 検証: ONTAP ファイル操作 → ECS Fargate → SQS → Lambda → Datadog

## 前提条件

- [ ] FSx for ONTAP ファイルシステムがデプロイ済み
- [ ] CIFS 対応 SVM に SMB 共有が1つ以上存在
- [ ] FSx for ONTAP と同じ VPC のプライベートサブネット
- [ ] Fargate 用のエグレスパス: NAT Gateway または VPC エンドポイント（ECR, CloudWatch Logs, SQS）
- [ ] ECR リポジトリに FPolicy サーバーイメージ（`linux/amd64` でビルド）
- [ ] Datadog API キーが Secrets Manager に保存済み
- [ ] ONTAP fsxadmin 認証情報にアクセス可能

## PoC スコープ

### スコープ内

- SMB create イベントの Datadog 到着
- Fargate TCP リスナー接続性
- SQS → Lambda → Datadog シッピング
- Fargate タスク再起動と IP 更新復旧
- Datadog Log Explorer クエリ検証

### スコープ外

- 全操作の配信保証（async モードでの rename/delete）
- NFS 本番対応
- Multi-AZ HA 設計
- 高ボリュームパフォーマンスベンチマーク
- 監査ログの代替

## 検証ステップ

### Step 1: インフラストラクチャのデプロイ

```bash
# Deploy Fargate stack
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-server-fargate.yaml \
  --stack-name fsxn-fpolicy-server \
  --parameter-overrides \
    VpcId=<vpc-id> SubnetIds=<subnet-id> \
    FsxnSvmSecurityGroupId=<fsx-sg-id> \
    ContainerImage=<ecr-uri>:latest \
  --capabilities CAPABILITY_NAMED_IAM

# Deploy Datadog Lambda
SQS_ARN=$(aws cloudformation describe-stacks --stack-name fsxn-fpolicy-server \
  --query "Stacks[0].Outputs[?OutputKey=='FPolicyQueueArn'].OutputValue" --output text)

aws cloudformation deploy \
  --template-file integrations/datadog/template-ems-fpolicy.yaml \
  --stack-name fsxn-datadog-ems-fpolicy \
  --parameter-overrides \
    DatadogApiKeySecretArn=<secret-arn> DatadogSite=<site> \
    FPolicySqsQueueArn=${SQS_ARN} \
  --capabilities CAPABILITY_NAMED_IAM
```

### Step 2: ONTAP FPolicy の設定

```bash
# Get Fargate task IP
TASK_IP=$(aws ecs describe-tasks --cluster fsxn-fpolicy-server-cluster \
  --tasks $(aws ecs list-tasks --cluster fsxn-fpolicy-server-cluster \
    --query "taskArns[0]" --output text) \
  --query "tasks[0].containers[0].networkInterfaces[0].privateIpv4Address" --output text)

# Configure ONTAP (via CLI or REST API)
# See: docs/en/fpolicy-production-architecture-patterns.md
```

### Step 3: 接続の検証

- [ ] ECS CloudWatch Logs に `[+] Connection from` が表示される
- [ ] ECS CloudWatch Logs に `[Handshake] Policy=...` が表示される
- [ ] ECS CloudWatch Logs に `[KeepAlive] Received` が表示される

### Step 4: イベント配信の検証

- [ ] SMB 共有上にファイルを作成
- [ ] ECS ログに `[Event] create <filename>` が表示される
- [ ] ECS ログに `[SQS] Sent: <filename> (create)` が表示される
- [ ] Lambda CloudWatch Logs に `shipped: 1` が表示される
- [ ] Datadog Log Explorer: `source:fsxn-fpolicy` でイベントが返される

### Step 5: 再起動復旧の検証

- [ ] Fargate を 0 にスケールし、1 に戻す
- [ ] ONTAP エンジン IP を更新（`fpolicy-update-engine-ip.sh --auto`）
- [ ] 再接続を確認（ログに KeepAlive）
- [ ] 別のファイルを作成し、Datadog への配信を確認

## 成功基準

| 基準 | 目標 |
|------|------|
| Create イベントが Datadog に到着 | 30秒以内 |
| 再起動復旧 | 3分以内 |
| Lambda 処理時間 | イベントあたり 500ms 未満 |
| Lambda エラー | PoC 中 0 件 |

## 既知の制約（Go/No-Go 前に確認）

- [ ] rename/delete イベントは async モードで配信されない場合がある
- [ ] NFS は明示的なバージョン指定と慎重なテストが必要
- [ ] user フィールドが空になる操作がある
- [ ] Fargate タスク再起動中（~2分）のイベントロスの可能性
- [ ] FPolicy はイベント駆動 signal であり、完全な監査ログの代替ではない

## ロールバック手順

```bash
# 1. Disable FPolicy on ONTAP
# vserver fpolicy disable -vserver <svm> -policy-name fpolicy_aws

# 2. Delete AWS stacks
aws cloudformation delete-stack --stack-name fsxn-datadog-ems-fpolicy
aws cloudformation delete-stack --stack-name fsxn-fpolicy-server
```

## Go / No-Go 判断

| 質問 | 回答 |
|------|------|
| Create イベントが Datadog に到着したか？ | Yes/No |
| レイテンシはユースケースに許容可能か？ | Yes/No |
| 既知の制約は許容可能か？ | Yes/No |
| コストモデルを理解したか？ | Yes/No |
| 本番 HA 設計が特定されたか？ | Yes/No |
