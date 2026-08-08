# 前提条件とリソースデプロイガイド

🌐 **日本語**（このページ） | [English](../en/prerequisites.md)

## 概要

本プロジェクトのベンダー統合をデプロイする前に、以下の前提リソースが必要です。

```
┌─────────────────────────────────────────────────────────────────┐
│                    前提リソース（本ガイドで構築）                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  FSx for ONTAP        S3 バケット         S3 Access Point       │
│  (監査ログ有効化)  →  (ログ保存先)    →   (Lambda アクセス用)    │
│                                                                 │
│  EventBridge 通知有効化                                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              ベンダー統合スタック（integrations/）                  │
├─────────────────────────────────────────────────────────────────┤
│  EventBridge Rule → Lambda → Vendor API                         │
└─────────────────────────────────────────────────────────────────┘
```

## 2つのデプロイパターン

### パターン A: 既存 FSx for ONTAP 環境に追加する（推奨）

既に FSx for ONTAP が稼働している環境に、監査ログ配信パイプラインを追加します。

**前提**:
- FSx for ONTAP ファイルシステムが存在する
- SVM が作成済み
- VPC、サブネット、セキュリティグループが設定済み

**手順**:
1. [Step 1: 前提リソーススタックのデプロイ](#step-1-前提リソーススタックのデプロイ)
2. [Step 2: FSx for ONTAP 監査ログの有効化](#step-2-fsx-ontap-監査ログの有効化)
3. [Step 3: ログ配信の確認](#step-3-ログ配信の確認)
4. [Step 4: ベンダー統合のデプロイ](#step-4-ベンダー統合のデプロイ)

### パターン B: ゼロから構築する（検証・デモ用）

FSx for ONTAP を含む全リソースを新規作成します。

**手順**:
1. [Step 0: FSx for ONTAP の作成](#step-0-fsx-for-ontap-の作成新規構築の場合)
2. 以降はパターン A と同じ

---

## Step 0: FSx for ONTAP の作成（新規構築の場合）

> ⚠️ FSx for ONTAP は時間課金のため、検証後は削除を忘れないでください。

### AWS CLI で作成

```bash
# VPC とサブネットが既にある前提
# Preferred subnet: プライマリ
# Standby subnet: セカンダリ（Multi-AZ の場合）

aws fsx create-file-system \
  --file-system-type ONTAP \
  --storage-capacity 1024 \
  --storage-type SSD \
  --subnet-ids subnet-xxxxxxxx subnet-yyyyyyyy \
  --ontap-configuration '{
    "DeploymentType": "MULTI_AZ_1",
    "ThroughputCapacity": 128,
    "PreferredSubnetId": "subnet-xxxxxxxx",
    "FsxAdminPassword": "YourSecurePassword123!",
    "EndpointIpAddressRange": "198.19.0.0/24"
  }' \
  --tags Key=Project,Value=fsxn-observability Key=Environment,Value=dev \
  --region ap-northeast-1
```

### SVM の作成

```bash
# ファイルシステム ID を取得
FS_ID=$(aws fsx describe-file-systems \
  --query "FileSystems[?Tags[?Key=='Project' && Value=='fsxn-observability']].FileSystemId" \
  --output text --region ap-northeast-1)

# SVM 作成
aws fsx create-storage-virtual-machine \
  --file-system-id $FS_ID \
  --name svm-audit-demo \
  --root-volume-security-style NTFS \
  --region ap-northeast-1
```

### CloudFormation で作成する場合

```bash
aws cloudformation deploy \
  --template-file shared/templates/fsxn-filesystem.yaml \
  --stack-name fsxn-demo-filesystem \
  --parameter-overrides \
    VpcId=vpc-xxxxxxxx \
    PrimarySubnetId=subnet-xxxxxxxx \
    StandbySubnetId=subnet-yyyyyyyy \
    FsxAdminPassword=YourSecurePassword123! \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

> 📝 `shared/templates/fsxn-filesystem.yaml` は大規模なテンプレートのため、本プロジェクトには含まれていません。[AWS ドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/creating-file-systems.html)を参照してください。

---

## Step 1: 前提リソーススタックのデプロイ

S3 バケット、S3 Access Point、EventBridge 通知を一括作成します。

```bash
aws cloudformation deploy \
  --template-file shared/templates/prerequisites.yaml \
  --stack-name fsxn-observability-prerequisites \
  --parameter-overrides \
    AuditLogBucketName=my-company-fsxn-audit-logs-ap-northeast-1 \
    AccessPointName=fsxn-audit-ap \
    VpcId=vpc-xxxxxxxx \
    RetentionDays=90 \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

### パラメータ説明

| パラメータ | 必須 | デフォルト | 説明 |
|-----------|------|-----------|------|
| `AuditLogBucketName` | ✅ | - | S3 バケット名（グローバルユニーク） |
| `AccessPointName` | ❌ | `fsxn-audit-ap` | S3 Access Point 名 |
| `VpcId` | ❌ | - | VPC 制限する場合に指定 |
| `RetentionDays` | ❌ | 90 | Glacier 移行までの日数 |
| `EnableGlacierTransition` | ❌ | true | Glacier 自動移行の有効/無効 |
| `KmsKeyArn` | ❌ | - | カスタム KMS キー（省略時は aws/s3） |

### デプロイ後の出力値確認

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-observability-prerequisites \
  --query "Stacks[0].Outputs" \
  --output table \
  --region ap-northeast-1
```

重要な出力値:
- `AccessPointArn` — ベンダー統合スタックの `S3AccessPointArn` パラメータに使用
- `AuditLogBucketName` — FSx for ONTAP 監査ログの出力先として設定

---

## Step 2: FSx for ONTAP 監査ログの有効化

### 方法 A: スクリプトを使用（推奨）

```bash
# ドライラン（コマンド確認のみ）
bash shared/scripts/ontap-audit-setup.sh \
  --endpoint 10.0.1.100 \
  --svm svm-prod-01 \
  --format evtx \
  --dry-run

# 実行
bash shared/scripts/ontap-audit-setup.sh \
  --endpoint 10.0.1.100 \
  --svm svm-prod-01 \
  --format evtx
```

### 方法 B: ONTAP System Manager (GUI)

1. FSx コンソール → ファイルシステム → 管理エンドポイント URL をブラウザで開く
2. **Storage** → **SVMs** → 対象 SVM を選択
3. **Settings** → **Audit** → **Enable**
4. 設定:
   - Destination: `/vol/audit_logs`
   - Format: EVTX または JSON
   - Rotation: Size-based, 100MB

### 方法 C: SSH で手動実行

```bash
# FSx for ONTAP 管理エンドポイントに SSH 接続
ssh admin@<management-endpoint-ip>

# ONTAP CLI で実行
vserver audit create -vserver svm-prod-01 \
  -destination /vol/audit_logs \
  -format evtx \
  -rotate-size 100MB

vserver audit enable -vserver svm-prod-01

# 確認
vserver audit show -vserver svm-prod-01
```

### 監査ログの S3 配信設定

FSx for ONTAP の監査ログを S3 バケットに配信する方法は複数あります:

#### オプション 1: FSx 自動バックアップ + S3 エクスポート

FSx の自動バックアップ機能を使い、バックアップから S3 にエクスポートします。
リアルタイム性は低いですが、設定が簡単です。

#### オプション 2: DataSync による定期同期

```bash
# DataSync タスクを作成して定期的に S3 に同期
aws datasync create-task \
  --source-location-arn arn:aws:datasync:ap-northeast-1:123456789012:location/loc-xxxxx \
  --destination-location-arn arn:aws:datasync:ap-northeast-1:123456789012:location/loc-yyyyy \
  --schedule "ScheduleExpression=rate(5 minutes)" \
  --name fsxn-audit-sync
```

#### オプション 3: FSx for ONTAP S3 Access Point 経由（推奨・最新）

FSx for ONTAP の S3 Access Point 機能（2025年リリース）を使用すると、ボリュームデータに直接 S3 API でアクセスできます。監査ログボリュームに S3 Access Point をアタッチすることで、Lambda から直接読み取り可能です。

```bash
aws fsx create-and-attach-s3-access-point \
  --name fsxn-audit-ap \
  --type ONTAP \
  --ontap-configuration 'VolumeId=fsvol-0123456789abcdef0,FileSystemIdentity={Type=UNIX,UnixUser={Name=root}}' \
  --region ap-northeast-1
```

`AVAILABLE` になったことを確認し ARN を記録します。これが各ベンダースタックの
`S3AccessPointArn` / `FsxS3AccessPointArn` に渡す値です:

```bash
aws fsx describe-s3-access-point-attachments \
  --names fsxn-audit-ap \
  --region ap-northeast-1 \
  --query 'S3AccessPointAttachments[0].{Lifecycle:Lifecycle,Arn:S3AccessPoint.ResourceARN}'
```

検証環境では約 20 秒で `AVAILABLE` になりました。

> 📝 FSx for ONTAP S3 Access Point は通常の S3 Access Point とは異なります。FSx ボリュームに直接アタッチされ、NFS/SMB データに S3 API でアクセスできる機能です。
>
> 作成には上記の `fsx` API を使います。`aws s3control create-access-point`（S3 バケットを
> 指す API）でも `aws fsx create-data-repository-association`（FSx for Lustre の S3 連携）
> でも作成できません。

**ネットワークオリジンは作成時に決まり、後から変更できません。** 上記のように
`--s3-access-point 'VpcConfiguration={VpcId=...}'` を省略すると Internet オリジンの
アクセスポイントになります。本統合群はこれを前提としており、シッパー Lambda は VPC 外で
動作しインターネット経路でアクセスポイントに到達します。オリジンと Lambda 配置の
組み合わせ一覧は[ベンダー統合のデプロイ](vendor-deployment-common.md)を参照してください。

---

## Step 3: ログ配信の確認

### S3 バケットにログが到着しているか確認

```bash
# バケット内のオブジェクト一覧
aws s3 ls s3://my-company-fsxn-audit-logs-ap-northeast-1/audit/ --recursive

# 最新のログファイルを確認
aws s3 ls s3://my-company-fsxn-audit-logs-ap-northeast-1/audit/ \
  --recursive --human-readable | tail -5
```

### EventBridge でイベントが発生しているか確認

```bash
# CloudTrail でS3イベントを確認
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=PutObject \
  --max-results 5 \
  --region ap-northeast-1
```

### テストファイルで動作確認

```bash
# テスト用の監査ログファイルをアップロード
aws s3 cp integrations/datadog/tests/test_data/sample_audit_logs.json \
  s3://my-company-fsxn-audit-logs-ap-northeast-1/audit/svm-prod-01/2026/01/15/test_audit.json
```

---

## Step 4: ベンダー統合のデプロイ

前提リソースが準備できたら、ベンダー統合スタックをデプロイします。

```bash
# 前提スタックの出力値を取得
AP_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-observability-prerequisites \
  --query "Stacks[0].Outputs[?OutputKey=='AccessPointArn'].OutputValue" \
  --output text --region ap-northeast-1)

BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name fsxn-observability-prerequisites \
  --query "Stacks[0].Outputs[?OutputKey=='AuditLogBucketName'].OutputValue" \
  --output text --region ap-northeast-1)

# Datadog 統合をデプロイ
aws cloudformation deploy \
  --template-file integrations/datadog/template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:datadog-api-key \
    DatadogSite=datadoghq.com \
    S3BucketName=$BUCKET_NAME \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

---

## リソース関係図

```
┌─────────────────────────────────────────────────────────────────────────┐
│ AWS Account                                                             │
│                                                                         │
│  ┌──────────────────┐                                                   │
│  │ FSx for ONTAP    │                                                   │
│  │                  │    監査ログ出力                                     │
│  │  SVM: svm-prod   │──────────────┐                                    │
│  │  Audit: enabled  │              │                                    │
│  └──────────────────┘              ▼                                    │
│                           ┌──────────────────┐                          │
│                           │ S3 Bucket        │                          │
│                           │ (audit logs)     │                          │
│                           │                  │◀── EventBridge 通知有効   │
│                           └────────┬─────────┘                          │
│                                    │                                    │
│                           ┌────────┴─────────┐                          │
│                           │ S3 Access Point  │                          │
│                           │ (Lambda用)       │                          │
│                           └────────┬─────────┘                          │
│                                    │                                    │
│  ┌──────────────────┐              │    ┌──────────────────┐            │
│  │ EventBridge Rule │──────────────┼───▶│ Lambda           │            │
│  │ (Object Created) │              │    │ (log shipper)    │────────┐   │
│  └──────────────────┘              │    └──────────────────┘        │   │
│                                    │                                │   │
│                                    │    ┌──────────────────┐        │   │
│                                    └───▶│ Secrets Manager  │        │   │
│                                         │ (API Key)        │        │   │
│                                         └──────────────────┘        │   │
└─────────────────────────────────────────────────────────────────────┼───┘
                                                                      │
                                                                      ▼
                                                          ┌──────────────────┐
                                                          │ Vendor API       │
                                                          │ (Datadog, etc.)  │
                                                          └──────────────────┘
```

---

## トラブルシューティング

### 監査ログが S3 に届かない

1. **FSx for ONTAP 側の確認**:
   ```
   ssh admin@<endpoint>
   vserver audit show -vserver <svm-name> -fields state
   # state が "true" であることを確認
   ```

2. **ボリュームの確認**:
   ```
   volume show -vserver <svm-name> -volume audit_logs
   # ボリュームが存在し、十分な空き容量があることを確認
   ```

3. **S3 配信設定の確認**:
   - DataSync タスクが正常に実行されているか
   - FSx S3 Access Point が正しく設定されているか

### EventBridge イベントが発生しない

1. S3 バケットの EventBridge 通知が有効か確認:
   ```bash
   aws s3api get-bucket-notification-configuration \
     --bucket <bucket-name> --region ap-northeast-1
   ```
   `EventBridgeConfiguration` が含まれていることを確認。

2. 前提スタックを再デプロイ（通知設定が含まれています）。

### S3 Access Point からの読み取りエラー

1. Lambda の IAM ロールに `s3:GetObject` 権限があるか確認
2. リソース ARN が `<access-point-arn>/object/*` 形式か確認
3. VPC 制限がある場合、Lambda が同じ VPC 内にあるか確認

### FPolicy Fargate: ECR イメージ pull タイムアウト

**症状**: ECS タスクが `CannotPullContainerError` または `dial tcp ...ecr...: i/o timeout` で起動失敗。

**原因**: テンプレートのデフォルトは `AssignPublicIp: DISABLED`。NAT Gateway も ECR VPC Endpoint もない場合、Fargate は ECR に到達できない。

**解決策**（いずれか）:
1. `AssignPublicIp=ENABLED` パラメータを設定（最も簡単、検証向き）
2. サブネットに NAT Gateway を追加（本番推奨）
3. Interface VPC Endpoint を作成: `com.amazonaws.<region>.ecr.api`、`com.amazonaws.<region>.ecr.dkr`、S3 Gateway Endpoint

### FPolicy Fargate: ONTAP が FPolicy サーバーに接続できない

**症状**: Fargate コンテナログに `[+] Connection from` エントリが表示されない。

**原因**: Security Group の設定ミス、または FPolicy エンジンの IP が古い。

**解決策**:
1. FPolicy サーバーの Security Group が、FSx SVM の Security Group からのインバウンド TCP 9898 を許可しているか確認
2. Fargate タスクが再起動した場合、IP が変更されている — ONTAP の FPolicy エンジン `primary_servers` が現在のタスク IP と一致しているか確認:
   ```bash
   # 現在のタスク IP を取得
   TASK_ARN=$(aws ecs list-tasks --cluster <cluster> --service <service> --query 'taskArns[0]' --output text)
   aws ecs describe-tasks --cluster <cluster> --tasks $TASK_ARN \
     --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value' --output text
   ```
3. テンプレートには IP Updater Lambda が含まれており、タスク再起動時にエンジン IP を自動更新する（`FsxnMgmtIp`、`FsxnSvmUuid`、`FsxnEngineName`、`FsxnPolicyName`、`FsxnCredentialsSecretArn` パラメータを設定）

### Automated Response Lambda: ModuleNotFoundError

**症状**: Lambda 実行が `ModuleNotFoundError: No module named 'ontap_response'` で失敗。

**原因**: `fsxn-shared-python` Lambda Layer が関数にアタッチされていない。

**解決策**: スタックデプロイ時に `SharedPythonLayerArn` パラメータを指定するか、手動でアタッチ:
```bash
# Layer をビルドして発行
bash shared/python/build-layer.sh
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name fsxn-shared-python \
  --zip-file fileb://shared/python/dist/fsxn-shared-python-layer.zip \
  --compatible-runtimes python3.12 \
  --query 'LayerVersionArn' --output text)

# 関数にアタッチ
aws lambda update-function-configuration \
  --function-name <stack-name>-handler \
  --layers $LAYER_ARN
```

### Automated Response Lambda: タイムアウト (60秒)

**症状**: Lambda が何のアクションも完了せずタイムアウトする。

**原因**: VPC 内の Lambda が Secrets Manager や SNS の API に到達できない。

**解決策**:
1. `CreateVpcEndpoints=true`（デフォルト）でデプロイする、または
2. VPC に `secretsmanager` と `sns` の Interface VPC Endpoint があることを確認
3. VPC Endpoint の Security Group が Lambda の Security Group からの TCP 443 を許可しているか確認

---

## 参考リンク

- [FSx for ONTAP ファイルアクセス監査](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html)
- [S3 Access Points for FSx](https://aws.amazon.com/blogs/storage/bridge-legacy-and-modern-applications-with-amazon-s3-access-points-for-amazon-fsx/)
- [Lambda で FSx ファイルをサーバーレス処理](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/tutorial-process-files-with-lambda.html)
- [EventBridge で S3 イベントを使用](https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventBridge.html)
- [NetApp Workload Factory - Journal Table](https://docs.netapp.com/us-en/workload-fsx-ontap/setup-journal-table.html)


## FSx for ONTAP S3 Access Point 権限チェックリスト

ベンダー統合をデプロイする前に、FSx for ONTAP S3 Access Point の以下を確認してください:

- [ ] アクセスポイントが正しい audit volume にアタッチされている
- [ ] ファイルシステム ID が audit ディレクトリへの読み取り権限を持っている
- [ ] Lambda 実行ロールがアクセスポイント ARN 経由で `s3:GetObject` と `s3:ListBucket` を持っている
- [ ] アクセスポイントポリシーが Lambda 実行ロールプリンシパルを許可している
- [ ] ネットワークパスが検証済み（Lambda が VPC 外、または VPC + NAT Gateway）
- [ ] アクセスポイントが MISCONFIGURED 状態でない（ボリュームオンライン、ID 解決可能）

アクセス確認:

```bash
aws s3api list-objects-v2 \
  --bucket <fsx-s3-access-point-arn-or-alias> \
  --max-keys 5 \
  --region ap-northeast-1
```

監査ログファイルが返されれば、アクセスポイントは Lambda 用に正しく設定されています。

## デプロイトポロジー

このプロジェクトは複数のデプロイパターンをサポートします:

| パターン | 説明 | 使用場面 |
|---------|------|---------|
| 同一アカウントローカル | FSx + Lambda + ベンダー統合を1アカウントに | 単一ワークロード、最もシンプル |
| 集約型ロギング | ワークロードアカウントが中央 Observability アカウントにテレメトリを公開 | 共有セキュリティ/ロギングアカウントを持つエンタープライズ |
| パートナー/MSP 管理 | ユーザーのワークロードアカウント + パートナー運用の統合 | マネージドサービス提供 |

マルチアカウントデプロイでは、クロスアカウント S3 Access Point アクセスと IAM 信頼関係が必要です。詳細は[運用ガイド](operational-guide.md)を参照してください。


---

## 関連ドキュメント

- [**デプロイメントガイド**](deployment-guide.md) — 既存 FSx for ONTAP 環境への全スタック統合ガイド（パラメータマッピング、VPC Endpoint 競合マトリクス、検証済みパス、コスト見積もり）
