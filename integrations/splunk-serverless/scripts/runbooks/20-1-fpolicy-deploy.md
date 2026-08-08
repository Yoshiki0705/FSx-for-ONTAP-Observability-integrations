# 20.1 FPolicy 共有テンプレートのデプロイ

## 概要

FPolicy パス（ECS Fargate + SQS + EventBridge パターン）は 2 つのスタックに分かれる。

1. **`shared/templates/fpolicy-apigw.yaml`**（ベンダー中立の共有スタック）: ECS Fargate 上の FPolicy サーバー本体、SQS キュー、EventBridge カスタムバスを作成する。ONTAP が Fargate タスク IP の TCP:9898 に直接接続し、ファイル操作イベントを SQS/EventBridge に発行する。
2. **`integrations/splunk-serverless/template-fpolicy.yaml`**（Splunk 固有スタック）: 1 の SQS/EventBridge から FPolicy イベントを受け取り、Splunk HEC へ転送する Lambda（`fpolicy_handler.py`）を作成する。

本手順は両方を順にデプロイする。

## 前提条件

- AWS CLI v2 が設定済み（`ap-northeast-1` リージョン）
- Docker（`buildx` 対応）がローカルで起動していること
- VPC、サブネット、セキュリティグループが存在すること
- ECR リポジトリ `fsxn-fpolicy-server` に FPolicy サーバーイメージがプッシュ済み（`shared/fpolicy-server/build-and-push.sh <tag>` で作成、**linux/amd64 必須**）
- Splunk HEC トークンが Secrets Manager に登録済み（`splunk/fsxn-hec-token`）
- FSx for ONTAP SVM のセキュリティグループが TCP:9898 のアウトバウンドを許可していること
- `CAPABILITY_NAMED_IAM` を使用可能であること

## アーキテクチャ概要

```
ONTAP FPolicy → TCP:9898 → ECS Fargate Task → SQS Queue → Lambda (fpolicy_handler.py) → Splunk HEC
                                              ↘ EventBridge Custom Bus (secondary path) ↗
```

**重要な設計ポイント:**
- ONTAP は Fargate タスク IP に直接接続（NLB 経由ではない。NLB はヘルスチェック用途のみ）
- Fargate タスク IP はタスク再起動時に変更される（`fpolicy-apigw.yaml` の IP Updater Lambda が ONTAP 側の external engine 設定を自動更新する）
- FPolicy プロトコルは独自バイナリプロトコル（HTTP/HTTPS ではない）
- EventBridge カスタムバスの source: `fpolicy.fsxn`
- **SQS がプライマリの配信経路、EventBridge はセカンダリ/代替経路**（`template-fpolicy.yaml` の `FPolicySqsEventSourceMapping` と `FPolicyEventBridgeRule` の両方が同じ Lambda をトリガーできる）

> **アーキテクチャの実際値に関する注記**: 本タスク作成当初、ECS タスクは ARM64・ECR リポジトリ名は `fpolicy-server` と想定していたが、実際の `shared/templates/fpolicy-apigw.yaml` の `EcsTaskDefinition.RuntimePlatform.CpuArchitecture` は `X86_64` であり、実際のリポジトリ名は `fsxn-fpolicy-server`。本手順は実際のテンプレート値に合わせて記載している。

## 手順

### Step 1: FPolicy サーバーイメージのビルド・プッシュ

```bash
# ECR にログインし、linux/amd64 でビルドしてプッシュ（タグは任意、例: 日付ベース）
bash shared/fpolicy-server/build-and-push.sh <tag>
```

### Step 2: ECR イメージの確認

```bash
# ECR リポジトリのイメージ一覧を確認
aws ecr describe-images \
  --repository-name fsxn-fpolicy-server \
  --region ap-northeast-1 \
  --query 'imageDetails[].imageTags' \
  --output json
```

**確認ポイント:**
- Step 1 で指定した `<tag>` のイメージが存在すること
- イメージアーキテクチャが `linux/amd64`（`X86_64`）であること — Fargate タスク定義の `RuntimePlatform` と一致させる必要がある

### Step 3: 共有テンプレート（FPolicy サーバー本体）の確認とデプロイ

```bash
# テンプレートの存在確認
ls -la shared/templates/fpolicy-apigw.yaml

# cfn-lint でテンプレートを検証
cfn-lint shared/templates/fpolicy-apigw.yaml

# FPolicy サーバースタックのデプロイ
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-apigw.yaml \
  --stack-name fsxn-fp-srv \
  --region ap-northeast-1 \
  --parameter-overrides \
    ComputeType=fargate \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-1>,<subnet-2> \
    FsxnSvmSecurityGroupId=<sg-id> \
    ContainerImage=<account-id>.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:<tag> \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

### Step 4: スタックステータスの確認

```bash
# スタックステータスを確認
aws cloudformation describe-stacks \
  --stack-name fsxn-fp-srv \
  --region ap-northeast-1 \
  --query 'Stacks[0].StackStatus' \
  --output text
```

**期待される出力:** `CREATE_COMPLETE` または `UPDATE_COMPLETE`

### Step 5: ECS Fargate タスク設定の確認

```bash
# ECS サービスの確認（クラスタ名・サービス名は fpolicy-apigw.yaml の命名: ${StackName}-fpolicy / ${StackName}-fpolicy-server）
aws ecs describe-services \
  --cluster fsxn-fp-srv-fpolicy \
  --services fsxn-fp-srv-fpolicy-server \
  --region ap-northeast-1 \
  --query 'services[0].{Status: status, RunningCount: runningCount, DesiredCount: desiredCount}'

# タスク定義の確認
aws ecs describe-task-definition \
  --task-definition fsxn-fp-srv-fpolicy-server \
  --region ap-northeast-1 \
  --query 'taskDefinition.{Cpu: cpu, Memory: memory, RuntimePlatform: runtimePlatform}'
```

**期待される設定（テンプレートのデフォルト値）:**
| 項目 | 期待値 |
|------|--------|
| CPU | 256 (.25 vCPU) |
| Memory | 512 MB |
| Architecture | X86_64 |
| OS Family | LINUX |

### Step 6: Fargate タスク IP の取得

```bash
# 実行中のタスク ARN を取得
TASK_ARN=$(aws ecs list-tasks \
  --cluster fsxn-fp-srv-fpolicy \
  --service-name fsxn-fp-srv-fpolicy-server \
  --region ap-northeast-1 \
  --query 'taskArns[0]' \
  --output text)

# タスクの ENI 情報からプライベート IP を取得
aws ecs describe-tasks \
  --cluster fsxn-fp-srv-fpolicy \
  --tasks "$TASK_ARN" \
  --region ap-northeast-1 \
  --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value' \
  --output text
```

**注意:** この IP アドレスは ONTAP FPolicy 外部エンジンの `primary-servers` に設定する値。タスク再起動時に変更される。`FsxnMgmtIp` / `FsxnSvmUuid` / `FsxnEngineName` / `FsxnPolicyName` / `FsxnCredentialsSecretArn` パラメータを指定していれば、`fpolicy-apigw.yaml` の IP Updater Lambda（`IpUpdaterLambdaFunction`）が ECS Task State Change イベントを検知して自動更新する。手動更新が必要な場合は `shared/scripts/fpolicy-update-engine-ip.sh --auto` を使う。

### Step 7: EventBridge カスタムバスの確認

```bash
# カスタムイベントバスの確認（バス名は EventBusName パラメータ、デフォルト fsxn-fpolicy-events）
aws events describe-event-bus \
  --name fsxn-fpolicy-events \
  --region ap-northeast-1

# イベントルールの確認
aws events list-rules \
  --event-bus-name fsxn-fpolicy-events \
  --region ap-northeast-1
```

**確認ポイント:**
- カスタムバスが存在すること
- source: `fpolicy.fsxn` のルールが設定されていること（`fpolicy-apigw.yaml` の `EventBridgeLogRule` によるデバッグ用ログ転送ルール）

### Step 8: SQS キューの確認

```bash
# SQS キューの確認（キュー名は fpolicy-apigw.yaml の命名: ${StackName}-fpolicy-ingestion）
aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-1.amazonaws.com/<account-id>/fsxn-fp-srv-fpolicy-ingestion \
  --attribute-names All \
  --region ap-northeast-1
```

### Step 9: セキュリティグループの確認

```bash
# Fargate タスクのセキュリティグループで TCP:9898 インバウンドが許可されていることを確認
aws ec2 describe-security-groups \
  --group-ids <fargate-sg-id> \
  --region ap-northeast-1 \
  --query 'SecurityGroups[0].IpPermissions[?FromPort==`9898`]'
```

**確認ポイント:**
- TCP:9898 のインバウンドが FSx for ONTAP SVM のセキュリティグループから許可されていること

### Step 10: Splunk 固有スタック（FPolicy シッパー Lambda）のデプロイ

```bash
# cfn-lint で検証
cfn-lint integrations/splunk-serverless/template-fpolicy.yaml

# Step 3 の SQS キュー ARN を取得
FP_SQS_ARN=$(aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-1.amazonaws.com/<account-id>/fsxn-fp-srv-fpolicy-ingestion \
  --attribute-names QueueArn --region ap-northeast-1 \
  --query 'Attributes.QueueArn' --output text)

# Splunk FPolicy シッパースタックのデプロイ
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template-fpolicy.yaml \
  --stack-name fsxn-splunk-fpolicy \
  --region ap-northeast-1 \
  --parameter-overrides \
    SplunkHecTokenSecretArn="arn:aws:secretsmanager:ap-northeast-1:<account-id>:secret:splunk/fsxn-hec-token-XXXXXX" \
    SplunkHecEndpoint="https://<splunk-hec-host>:8088" \
    EventBridgeBusName="fsxn-fpolicy-events" \
    FPolicySqsQueueArn="$FP_SQS_ARN" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

**確認ポイント:**
- `fpolicy_handler.py` のユニットテスト（`python3 -m pytest integrations/splunk-serverless/tests/test_fpolicy_handler.py -v`）が通過していること
- HEC payload の `sourcetype` が `fsxn:ontap:fpolicy`、`index` が `fsxn_fpolicy` であること

## 検証チェックリスト

- [ ] ECR に指定タグのイメージが `linux/amd64` (`X86_64`) でプッシュされている
- [ ] `shared/templates/fpolicy-apigw.yaml` / `integrations/splunk-serverless/template-fpolicy.yaml` が両方 cfn-lint を通過
- [ ] 両 CloudFormation スタックのステータスが `CREATE_COMPLETE`
- [ ] ECS Fargate タスクが X86_64, 256 CPU, 512 MB で設定されている
- [ ] Fargate タスクが Running 状態
- [ ] Fargate タスクのプライベート IP が取得できた
- [ ] EventBridge カスタムバス（source: `fpolicy.fsxn`）が存在する
- [ ] SQS キューが作成されている
- [ ] セキュリティグループで TCP:9898 が許可されている
- [ ] `fpolicy_handler.py` のユニットテストが通過している
- [ ] Splunk FPolicy シッパー Lambda が SQS イベントソースマッピングでトリガーされる

## トラブルシューティング

### ECS タスクが起動しない

- **原因**: ECR イメージの取得失敗、リソース不足、アーキテクチャ不一致（イメージが `linux/arm64` でビルドされている等）
- **解決**: ECS イベントログを確認: `aws ecs describe-services --cluster ... --query 'services[0].events[:5]'`。イメージを `shared/fpolicy-server/build-and-push.sh` で `linux/amd64` として再ビルド

### Fargate タスクが STOPPED になる

- **原因**: コンテナのヘルスチェック失敗、OOM
- **解決**: CloudWatch Logs でコンテナログを確認

### セキュリティグループで接続が拒否される

- **原因**: TCP:9898 のインバウンドルールが不足
- **解決**: Fargate タスクの SG に FSx for ONTAP SVM SG からの TCP:9898 を許可

### EventBridge カスタムバスが作成されない

- **原因**: テンプレートの EventBridge リソース定義に問題
- **解決**: CloudFormation イベントでエラー詳細を確認

### Splunk シッパー Lambda が SQS イベントを受け取らない

- **原因**: `FPolicySqsQueueArn` パラメータが未指定または誤り（`HasFPolicySqsQueue` Condition が false のままだと `FPolicySqsEventSourceMapping` が作成されない）
- **解決**: `aws cloudformation describe-stacks --stack-name fsxn-splunk-fpolicy` で実際に渡されたパラメータを確認し、Step 10 の `FP_SQS_ARN` が正しく解決されているか確認

## 関連タスク

- Task 19.1: EMS Webhook 用 Splunk テンプレートのデプロイ
- Task 20.2: Splunk 向け FPolicy 受信 Lambda の作成（`fpolicy_handler.py` として実装済み）
- Task 20.3: ONTAP FPolicy 外部エンジン設定
- Task 20.4: FPolicy ファイル操作テスト
