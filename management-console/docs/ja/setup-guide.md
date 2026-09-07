# FSx for ONTAP Management Console セットアップガイド

🌐 **日本語**（このページ） | [English](../en/setup-guide.md)

Amazon FSx for NetApp ONTAP 向けセルフホスト管理コンソールのデプロイ手順書です。

## 目次

1. [前提条件](#前提条件)
2. [デプロイ手順](#デプロイ手順)
3. [パラメータリファレンス](#パラメータリファレンス)
4. [デプロイ後の確認](#デプロイ後の確認)
5. [トラブルシューティング](#トラブルシューティング)
6. [クリーンアップ](#クリーンアップ)
7. [アップデート](#アップデート)

---

## 前提条件

デプロイを開始する前に、以下のリソースが準備されていることを確認してください。

### AWS リソース

| リソース | 要件 | 備考 |
|---------|------|------|
| VPC | 既存の VPC | ECS タスク、ALB、VPC Endpoints を配置 |
| プライベートサブネット | 2 AZ 以上にまたがる最低 2 つ | ECS Fargate タスク、Lambda を配置 |
| パブリックサブネット | 2 AZ 以上にまたがる最低 2 つ | ALB、NAT Gateway を配置 |
| FSx for ONTAP | 管理エンドポイントにアクセス可能 | REST API (port 443) を使用 |
| Secrets Manager シークレット | ONTAP 管理者認証情報 | JSON 形式: `{"username": "fsxadmin", "password": "..."}` |
| ACM 証明書 | ALB 用 HTTPS 証明書 | DNS 検証済み、または既存の証明書 ARN |

### Secrets Manager シークレットの作成

ONTAP 管理者認証情報を Secrets Manager に登録します:

```bash
aws secretsmanager create-secret \
  --name fsxn-mgmt-ontap-credentials \
  --description "FSx for ONTAP admin credentials for Management Console" \
  --secret-string '{"username": "fsxadmin", "password": "<your-password>"}'
```

### S3 Access Point（ファイルブラウザ機能を使用する場合）

FSx for ONTAP S3 Access Point が作成済みであること。ARN 形式:

```
arn:aws:s3:<region>:<account-id>:accesspoint/<access-point-name>
```

### FSx for ONTAP セキュリティグループ

FSx for ONTAP ファイルシステムのセキュリティグループ ID が必要です。Harvest と ToolJet がポート 443 で管理エンドポイントにアクセスするため、デプロイスクリプトがこの SG にルールを自動追加します。

```bash
# FSx for ONTAP のセキュリティグループ ID を確認
aws ec2 describe-network-interfaces \
  --filters "Name=description,Values=*FSx*" \
  --query "NetworkInterfaces[0].Groups[0].GroupId" \
  --output text
```

環境変数に設定:
```bash
export FSXN_SECURITY_GROUP_ID="sg-0123456789abcdef0"
```

### ツール要件

- AWS CLI v2 (設定済み)
- `bash` 4.0 以上
- `jq` (JSON パース用)

---

## デプロイ手順

Management Console は 5 つの CloudFormation スタックで構成されます。`deploy.sh` スクリプトが依存関係を考慮した正しい順序でデプロイを実行します。

### スタック構成と依存関係

```
1. fsxn-mgmt-network       ← VPC Endpoints, NAT Gateway, Security Groups
2. fsxn-mgmt-auth          ← Cognito User Pool, App Client
3. fsxn-mgmt-observability  ← AMP, AMG, Harvest ECS, ADOT
4. fsxn-mgmt-console       ← ToolJet ECS, ALB, RDS, Lambda
5. fsxn-mgmt-monitoring    ← CloudWatch Alarms, Dashboard, SNS
```

### Step 1: 環境変数の設定

```bash
# 必須パラメータ
export VPC_ID="vpc-0123456789abcdef0"
export PRIVATE_SUBNET_IDS="subnet-aaaa1111aaaa1111a,subnet-bbbb2222bbbb2222b"
export PUBLIC_SUBNET_IDS="subnet-cccc3333cccc3333c,subnet-dddd4444dddd4444d"
export ONTAP_MANAGEMENT_ENDPOINT="<management-ip>"
export ONTAP_CREDENTIALS_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-mgmt-ontap-credentials-XXXXXX"
export S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-file-ap"

# 認証パラメータ（auth スタック作成後に自動取得、または既存値を指定）
export COGNITO_USER_POOL_ID=""
export COGNITO_APP_CLIENT_ID=""
export COGNITO_DOMAIN="fsxn-mgmt"

# オプション
export MFA_CONFIGURATION="OPTIONAL"          # OFF | OPTIONAL | REQUIRED
export SESSION_DURATION_HOURS="8"            # 1-12
export HARVEST_IMAGE_TAG="latest"            # 推奨: バージョン固定 (例: 24.05.2)
export TOOLJET_IMAGE_TAG="latest"            # 推奨: LTS バージョン固定
export ALERT_SNS_TOPIC_ARN=""                # 空の場合は新規作成
```

### Step 2: デプロイスクリプトの実行

```bash
cd management-console/scripts
bash deploy.sh
```

`deploy.sh` は以下を実行します:

1. パラメータのバリデーション（VPC ID、サブネット形式の確認）
2. 5 スタックを依存順序でデプロイ
3. 各スタックの `CREATE_COMPLETE` / `UPDATE_COMPLETE` を確認
4. スタック出力を後続スタックのパラメータとして自動引き渡し

### Step 3: 初回デプロイの完了確認

全スタックが正常にデプロイされると、以下の出力が表示されます:

```
✅ All 5 stacks deployed successfully.
   ALB DNS: fsxn-mgmt-xxxxxxxx.ap-northeast-1.elb.amazonaws.com
   Grafana: https://g-xxxxxxxxxx.grafana-workspace.ap-northeast-1.amazonaws.com
   Console: https://fsxn-mgmt-xxxxxxxx.ap-northeast-1.elb.amazonaws.com/app
```

---

## パラメータリファレンス

全 14 パラメータの一覧です。デプロイスクリプトの環境変数、または CloudFormation パラメータとして指定します。

| パラメータ | 型 | 必須 | デフォルト | 説明 |
|-----------|---|------|-----------|------|
| `VpcId` | `AWS::EC2::VPC::Id` | ✅ | — | デプロイ先 VPC |
| `PrivateSubnetIds` | `List<AWS::EC2::Subnet::Id>` | ✅ | — | プライベートサブネット（2 AZ 以上） |
| `PublicSubnetIds` | `List<AWS::EC2::Subnet::Id>` | ✅ | — | パブリックサブネット（ALB + NAT GW 用） |
| `OntapManagementEndpoint` | `String` | ✅ | — | FSx for ONTAP 管理エンドポイント IP/DNS |
| `OntapCredentialsSecretArn` | `String` | ✅ | — | ONTAP 認証情報の Secrets Manager ARN |
| `CognitoUserPoolId` | `String` | ✅ | — | Cognito User Pool ID |
| `CognitoAppClientId` | `String` | ✅ | — | Cognito App Client ID |
| `CognitoDomain` | `String` | ✅ | — | Cognito ドメインプレフィックス |
| `HarvestImageTag` | `String` | ✅ | `latest` | Harvest コンテナイメージタグ |
| `ToolJetImageTag` | `String` | ✅ | `latest` | ToolJet コンテナイメージタグ |
| `S3AccessPointArn` | `String` | ✅ | — | FSx for ONTAP S3 Access Point ARN |
| `MfaConfiguration` | `String` | ✅ | `OPTIONAL` | MFA モード: `OFF` / `OPTIONAL` / `REQUIRED` |
| `SessionDurationHours` | `Number` | ✅ | `8` | セッション有効期間（1〜12 時間） |
| `AlertSnsTopicArn` | `String` | — | (新規作成) | アラーム通知先 SNS Topic ARN |

### パラメータ制約

- `VpcId`: `vpc-` で始まる 8〜17 文字の英数字
- `PrivateSubnetIds` / `PublicSubnetIds`: 最低 2 つ、異なる AZ に配置
- `OntapCredentialsSecretArn`: `arn:aws:secretsmanager:` で始まる有効な ARN
- `MfaConfiguration`: `OFF`, `OPTIONAL`, `REQUIRED` のいずれか
- `SessionDurationHours`: 1 以上 12 以下の整数

---

## コスト見積もり

| リソース | 単価 | 月額（24/7） | 備考 |
|---------|------|------------|------|
| NAT Gateway | $0.062/h + $0.062/GB | ~$45 | データ転送量に依存 |
| ECS Fargate (Harvest + ADOT) | ~$0.05/h | ~$36 | 1024 CPU / 2048 MB |
| ECS Fargate (Appsmith/ToolJet) | ~$0.05/h | ~$36 | 1024 CPU / 2048 MB |
| RDS db.t3.medium | $0.068/h | ~$49 | 状態管理用 |
| VPC Interface Endpoints x5 | $0.014/h each | ~$50 | SM, CW Logs, ECR x2, STS |
| AMP | $0.003/10K samples | ~$5 | メトリクス量に依存 |
| AMG | $9/editor/月 | $9 | Viewer は無料 |
| ALB | $0.0225/h + LCU | ~$20 | リクエスト量に依存 |
| **合計** | | **~$250/月** | フル稼働時の概算 |

> ⚠️ これは **サイジング参考値** です。実際のコストは使用量、リージョン、データ転送量により変動します。VPC-origin S3 AP を使用する場合、NAT Gateway（~$45/月）は不要です。最新の料金は [AWS Pricing Calculator](https://calculator.aws/) で確認してください。

---

## デプロイ後の確認

### ALB アクセス確認

1. ALB DNS 名をブラウザで開きます:

```bash
# ALB DNS 名の取得
aws cloudformation describe-stacks \
  --stack-name fsxn-mgmt-console \
  --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' \
  --output text
```

2. Cognito ログインページにリダイレクトされることを確認
3. ユーザーを作成してログイン:

```bash
# Cognito ユーザーの作成
aws cognito-idp admin-create-user \
  --user-pool-id <user-pool-id> \
  --username admin \
  --temporary-password 'TempPass123!' \
  --user-attributes Name=email,Value=admin@example.com
```

### Grafana ダッシュボード確認

1. AMG ワークスペース URL にアクセス
2. AMP データソースが設定されていることを確認
3. Harvest ダッシュボード（Volume Performance, Aggregate Utilization 等）が表示されることを確認
4. FSx for ONTAP メトリクスがダッシュボードに反映されていることを確認（デプロイ後 2〜3 分）

### ToolJet ログイン確認

1. `https://<alb-dns>/app` にアクセス
2. Cognito 認証後、ToolJet ダッシュボードが表示されることを確認
3. ONTAP REST API データソースの接続テスト:
   - Settings → Data Sources → FSx for ONTAP ONTAP REST → Test Connection

### ECS タスク稼働確認

```bash
# Harvest タスクの確認
aws ecs describe-services \
  --cluster fsxn-mgmt-cluster \
  --services fsxn-mgmt-harvest \
  --query 'services[0].{desired:desiredCount,running:runningCount,status:status}'

# ToolJet タスクの確認
aws ecs describe-services \
  --cluster fsxn-mgmt-cluster \
  --services fsxn-mgmt-tooljet \
  --query 'services[0].{desired:desiredCount,running:runningCount,status:status}'
```

### CloudWatch アラーム確認

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix "fsxn-mgmt" \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}'
```

すべてのアラームが `OK` 状態であることを確認します。

---

## トラブルシューティング

### よくあるデプロイ失敗パターン

#### 1. 2 AZ にまたがっていないサブネット

**エラー**: `At least two subnets in two different Availability Zones must be specified`

**原因**: ALB は最低 2 つの AZ にまたがるサブネットを必要とします。

**解決策**:
```bash
# サブネットの AZ を確認
aws ec2 describe-subnets \
  --subnet-ids subnet-aaaa1111aaaa1111a subnet-bbbb2222bbbb2222b \
  --query 'Subnets[].{SubnetId:SubnetId,AZ:AvailabilityZone}'
```

異なる AZ のサブネットを指定してください。

#### 2. Secrets Manager シークレットの不検出

**エラー**: `Secret not found: arn:aws:secretsmanager:...`

**原因**: シークレット ARN が間違っている、またはシークレットが存在しない。

**解決策**:
```bash
# シークレットの存在確認
aws secretsmanager describe-secret \
  --secret-id fsxn-mgmt-ontap-credentials

# シークレットの JSON 形式確認
aws secretsmanager get-secret-value \
  --secret-id fsxn-mgmt-ontap-credentials \
  --query 'SecretString' --output text | jq .
```

`username` と `password` キーが含まれていることを確認してください。

#### 3. NAT Gateway 作成失敗（Elastic IP 上限）

**エラー**: `The maximum number of addresses has been reached`

**原因**: アカウントの Elastic IP 上限に達している。

**解決策**:
- 未使用の Elastic IP を解放する
- Service Quotas で上限引き上げをリクエストする

#### 4. ECS タスクの起動失敗

**エラー**: `STOPPED (CannotPullContainerError)`

**原因**: ECR からのイメージプルに失敗。VPC Endpoints または NAT Gateway の設定不備。

**解決策**:
```bash
# ECS タスクの停止理由を確認
aws ecs describe-tasks \
  --cluster fsxn-mgmt-cluster \
  --tasks <task-arn> \
  --query 'tasks[0].stoppedReason'
```

- ECR VPC Endpoint (`com.amazonaws.<region>.ecr.dkr`, `com.amazonaws.<region>.ecr.api`) が作成されていることを確認
- Security Group のアウトバウンドルールで VPC Endpoints への HTTPS (443) が許可されていることを確認

#### 5. Harvest から ONTAP への接続の不可

**エラー**: CloudWatch Logs に `connection refused` または `timeout`

**原因**: Security Group が ONTAP 管理エンドポイントへのアクセスを許可していない。

**解決策**:
- `OntapAccessSG` のインバウンドルールに `HarvestTaskSG` からの port 443 が許可されていることを確認
- `OntapManagementEndpoint` パラメータが正しい IP/DNS であることを確認
- FSx for ONTAP のセキュリティグループで ECS タスクからのアクセスが許可されていることを確認

#### 6. CloudFormation スタックの DELETE_FAILED 遷移

**原因**: リソースが他のリソースから参照されている、または手動で変更されている。

**解決策**:
```bash
# 削除に失敗したリソースを確認
aws cloudformation describe-stack-resources \
  --stack-name <stack-name> \
  --query 'StackResources[?ResourceStatus==`DELETE_FAILED`].{LogicalId:LogicalResourceId,Type:ResourceType,Reason:ResourceStatusReason}'
```

手動でリソースを削除してから、スタック削除を再試行してください。

#### 7. Harvest コンテナの起動失敗（/bin/sh not found）

**エラー**: `exec: "/bin/sh": stat /bin/sh: no such file or directory`

**原因**: Harvest Docker イメージには `/bin/sh` が含まれていませんが、`/busybox/sh` が利用可能です。

**解決策**: 
- EntryPoint に `/busybox/sh` を使用（`/bin/sh` ではない）
- 現在の `templates/observability.yaml` は `/busybox/sh -c` で設定ファイルを書き込み、`exec bin/poller` を実行
- init コンテナパターンは不要 — Harvest コンテナ自身が設定生成を処理

#### 8. Harvest コンテナの起動失敗（bin/poller not found）

**エラー**: `exec: "bin/poller": stat bin/poller: no such file or directory`

**原因**: 共有ボリュームを `/opt/harvest` にマウントしたため、Harvest のバイナリファイルが上書きされた。

**解決策**: `/opt/harvest` にボリュームをマウントしないでください。`/busybox/sh -c` エントリポイントパターンを使用する場合、設定ファイルは `/opt/harvest/harvest.yml` に直接書き込みます（共有ボリューム不要）。正しい CLI 構文:

```
bin/poller --config harvest.yml -p fsxn-cluster
```

注意: コマンドは `bin/poller --config` です（`start --config` ではありません）。

#### 9. セキュリティに関する注意事項

**ECS Secrets 注入のリスク認識**:

ECS の `Secrets` フィールドで注入された認証情報は、コンテナの環境変数として平文でメモリ上に存在します。これは ECS Fargate の標準パターンですが、以下のリスクを認識してください：

- メモリダンプ攻撃による認証情報漏洩の可能性
- コンテナログに環境変数が出力されないよう注意（Harvest/ADOT のログレベル設定）

より高いセキュリティ要件がある場合は、アプリケーション内で Secrets Manager API を直接呼び出すパターンを検討してください。

#### 10. VPC Endpoint DNS 伝播タイミング

**エラー**: `ResourceInitializationError: unable to pull secrets or registry auth`

**原因**: VPC Interface Endpoint 作成直後は DNS レコードの伝播に 1-2 分かかります。ECS タスクがこの間に起動すると、Secrets Manager に接続できません。

**解決策**: 
- ECS Deployment Circuit Breaker が自動的にタスクを再試行します
- DNS 伝播完了後（通常 2 分以内）に次のタスク起動が成功します
- Circuit Breaker がトリガーされた場合は、observability スタックを再デプロイしてください

#### 11. CloudFormation スタック削除時の SG 依存関係エラー

**エラー**: `resource sg-xxx has a dependent object`

**原因**: FSx for ONTAP のセキュリティグループに Harvest/ToolJet タスク SG への参照が残っている。

**解決策**: cleanup.sh に `FSXN_SECURITY_GROUP_ID` を設定して実行すると、自動的にルールを削除してからスタックを削除します。

```bash
export FSXN_SECURITY_GROUP_ID="sg-0123456789abcdef0"
bash scripts/cleanup.sh
```

---

## クリーンアップ

Management Console の全リソースを削除するには `cleanup.sh` を使用します。

### クリーンアップの実行

```bash
cd management-console/scripts
bash cleanup.sh
```

`cleanup.sh` は以下を実行します:

1. 5 スタックを依存関係の逆順で削除:
   ```
   fsxn-mgmt-monitoring → fsxn-mgmt-console → fsxn-mgmt-observability → fsxn-mgmt-auth → fsxn-mgmt-network
   ```
2. 各スタックの `DELETE_COMPLETE` を確認
3. 削除失敗時はエラーを報告して終了（非ゼロ終了コード）

### 部分的なクリーンアップ

特定のスタックのみ削除する場合:

```bash
aws cloudformation delete-stack --stack-name fsxn-mgmt-monitoring
aws cloudformation wait stack-delete-complete --stack-name fsxn-mgmt-monitoring
```

> ⚠️ **注意**: スタックの削除順序を守ってください。依存関係のあるスタックを先に削除すると `DELETE_FAILED` になります。

### DELETE_FAILED 時の対応

スタックが `DELETE_FAILED` 状態になった場合:

```bash
# 保持されているリソースを確認
aws cloudformation describe-stack-resources \
  --stack-name <stack-name> \
  --query 'StackResources[?ResourceStatus==`DELETE_FAILED`]'

# リソースを手動削除後、スタック削除を再試行
aws cloudformation delete-stack \
  --stack-name <stack-name> \
  --retain-resources LogicalResourceId1 LogicalResourceId2
```

---

## アップデート

Management Console のアップデートは、コンテナイメージタグの変更で実行します。CloudFormation テンプレートの再デプロイやビルドステップは不要です。

### Harvest のアップデート

```bash
# 新しいイメージタグを指定してスタックを更新
export HARVEST_IMAGE_TAG="24.11.0"

aws cloudformation deploy \
  --template-file templates/observability.yaml \
  --stack-name fsxn-mgmt-observability \
  --parameter-overrides \
    VpcId=$VPC_ID \
    PrivateSubnetIds=$PRIVATE_SUBNET_IDS \
    OntapManagementEndpoint=$ONTAP_MANAGEMENT_ENDPOINT \
    OntapCredentialsSecretArn=$ONTAP_CREDENTIALS_SECRET_ARN \
    CognitoUserPoolId=$COGNITO_USER_POOL_ID \
    HarvestImageTag=$HARVEST_IMAGE_TAG \
  --capabilities CAPABILITY_IAM
```

### ToolJet のアップデート

```bash
# 新しいイメージタグを指定してスタックを更新
export TOOLJET_IMAGE_TAG="v2.50.0-ee-lts"

aws cloudformation deploy \
  --template-file templates/console.yaml \
  --stack-name fsxn-mgmt-console \
  --parameter-overrides \
    VpcId=$VPC_ID \
    PrivateSubnetIds=$PRIVATE_SUBNET_IDS \
    PublicSubnetIds=$PUBLIC_SUBNET_IDS \
    OntapManagementEndpoint=$ONTAP_MANAGEMENT_ENDPOINT \
    OntapCredentialsSecretArn=$ONTAP_CREDENTIALS_SECRET_ARN \
    CognitoUserPoolId=$COGNITO_USER_POOL_ID \
    CognitoAppClientId=$COGNITO_APP_CLIENT_ID \
    CognitoDomain=$COGNITO_DOMAIN \
    ToolJetImageTag=$TOOLJET_IMAGE_TAG \
    S3AccessPointArn=$S3_ACCESS_POINT_ARN \
    SessionDurationHours=$SESSION_DURATION_HOURS \
  --capabilities CAPABILITY_IAM
```

### アップデート時の注意事項

- イメージタグの変更のみで ECS サービスのローリングアップデートが実行されます
- ダウンタイムは通常 30〜60 秒です（ECS ローリングデプロイ）
- アップデート前に現在のイメージタグを記録しておくことを推奨します
- 問題が発生した場合は、以前のイメージタグに戻して再デプロイしてください

### バージョン固定の推奨

本番環境では `latest` タグではなく、特定のバージョンを固定することを推奨します:

| コンポーネント | 推奨タグ形式 | 例 |
|--------------|------------|---|
| Harvest | セマンティックバージョン | `24.05.2`, `24.11.0` |
| ToolJet | LTS バージョン | `v2.50.0-ee-lts` |
| ADOT Collector | セマンティックバージョン | `v0.40.0` |
