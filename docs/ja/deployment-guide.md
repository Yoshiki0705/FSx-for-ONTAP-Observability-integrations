# デプロイメントガイド — 既存 FSx for ONTAP 環境への統合

🌐 **日本語** (このページ) | [English](../en/deployment-guide.md)

## 概要

このガイドは、Amazon FSx for NetApp ONTAP が稼働中の環境に対して、本プロジェクトのオブザーバビリティ/セキュリティスタックをデプロイする手順を説明します。本プロジェクトの全テンプレートは**オーバーレイ設計**であり、FSx ファイルシステム・SVM・ボリュームを新規作成しません。既存インフラの上に監視、インシデント対応、データ保護の機能を追加します。

## 開始前に必要な情報

既存環境から以下を収集してください。

| # | リソース | 確認場所 | 使用先スタック |
|---|----------|----------|---------------|
| 1 | FSx ファイルシステム ID | FSx コンソール → ファイルシステム → `fs-xxxxxxxxxxxxxxxxx` | `fsxn-audit-config`, `restore-verification` |
| 2 | 管理エンドポイント IP | FSx コンソール → ファイルシステム詳細 → 管理 DNS/IP | `automated-response`, `restore-verification`, `fpolicy-apigw`, `lakehouse-monitoring` |
| 3 | SVM 名 | FSx コンソール → ストレージ仮想マシン | `automated-response`, `automated-response-ttl` |
| 4 | SVM ID | FSx コンソール → SVM 詳細 → `svm-xxxxxxxxxxxxxxxxx` | `fsxn-audit-config` |
| 5 | VPC ID | VPC コンソール | 全 VPC モードスタック |
| 6 | プライベートサブネット ID | VPC コンソール → サブネット（FSx ENI と同じ AZ） | 全 VPC モードスタック |
| 7 | セキュリティグループ ID | FSx 管理 IP への HTTPS (443) を許可する SG | 全 VPC モードスタック |
| 8 | ルートテーブル ID | VPC コンソール → ルートテーブル → サブネット関連付け | `restore-verification`, `content-classification-scanner` |
| 9 | ONTAP 管理者クレデンシャル Secret ARN | Secrets Manager → `arn:aws:secretsmanager:...` | `automated-response`, `restore-verification` |
| 10 | S3 Access Point ARN | `aws fsx describe-s3-access-point-attachments` （`describe-data-repository-associations` は別機能なので**不可**）。あわせて `NetworkOrigin` も記録すること。下記 Tier 1 の注記を参照 | `prerequisites`, ベンダースタック |

## スタック一覧

### Tier 1: 監査ログ転送（ベンダー統合）

FSx for ONTAP の監査ログをオブザーバビリティベンダーに転送するスタック群。Lambda を VPC 外で実行する場合、VPC 設定は不要です。

| Stack | Template | Key Parameters | VPC Required |
|-------|----------|---------------|--------------|
| Prerequisites | `shared/templates/prerequisites.yaml` | FsxS3AccessPointArn | No |
| S3 Access Point | `shared/templates/s3-access-point.yaml` | BucketName, VpcId (optional) | No |
| Vendor (×10) | `integrations/<vendor>/template.yaml` | FsxS3AccessPointArn, VendorSecretArn | No (default) |
| Datadog: EMS + FPolicy | `integrations/datadog/template-ems-fpolicy.yaml` | DatadogApiKeySecretArn, FPolicySqsQueueArn | No |
| Datadog: ログアーカイブ | `integrations/datadog/template-log-archive.yaml` | DatadogExternalId, RetentionDays | No |
| Datadog: Snapshot 修復 | `integrations/datadog/template-snapshot-remediation.yaml` | OntapManagementIp, Secret ARN, VPC/Subnet/SG | **Yes** |

> **Tier 1 のデプロイが失敗する 2 大要因:**
>
> 1. **スタック単体では動作しません。** CloudFormation は数百行のハンドラをインライン化
>    できないため、各ベンダーの `template.yaml` は `NotImplementedError` を投げる
>    placeholder を配置します。スタックのデプロイと実コードのアップロードを行う
>    ベンダーの `scripts/deploy.sh` を使うか、後から自分で
>    `aws lambda update-function-code` を実行してください。アップロード忘れは
>    ベンダーの `scripts/verify.sh` が検出します。
> 2. **アクセスポイントのネットワークオリジンと Lambda の配置を一致させること。**
>    Internet-origin のアクセスポイントは VPC 外 Lambda（デフォルト）で動作し、
>    VPC-origin の場合は `VpcEnabled=true` とベンダー API への egress が必要です。
>    不一致の場合 `AccessDenied ... explicit deny in a resource-based policy` で
>    失敗し、IAM の問題のように見えます。オリジンは作成後に変更できません。

### Tier 2: インシデント対応

| Stack | Template | Key Parameters | VPC Required |
|-------|----------|---------------|--------------|
| Automated Response | `shared/templates/automated-response.yaml` | OntapMgmtIp, Secret ARN, VPC/Subnet/SG, DefaultSvmName | Yes |
| TTL Auto-Unblock | `shared/templates/automated-response-ttl.yaml` | Same as above + BlockTtlMinutes, CheckIntervalMinutes | Yes |

### Tier 3: データ保護と分類

| Stack | Template | Key Parameters | VPC Required |
|-------|----------|---------------|--------------|
| Restore Verification | `shared/templates/restore-verification.yaml` | OntapMgmtIp, FileSystemId, Secret ARN, VPC, Subnet, SG, Route Tables | Yes |
| Content Classification | `shared/templates/content-classification-scanner.yaml` | VpcId (optional), LanguageCode | No (default) |

### Tier 4: 高度な監視

| Stack | Template | Key Parameters | VPC Required |
|-------|----------|---------------|--------------|
| **性能＆容量ダッシュボード** | `shared/templates/fsxn-monitoring-dashboard.yaml` | FileSystemId, FileSystemName, CapacityThresholdPercent, NotificationEmail | No |
| **Qtree クォータ監視** | `shared/templates/qtree-quota-monitor.yaml` | OntapMgmtIp, SvmName, VPC/Subnet/SG, QuotaThresholdPercent | Yes |
| Syslog → CloudWatch | `shared/templates/syslog-vpce-cloudwatch.yaml` | VpcId, SubnetIds, VpcCidr | Yes |
| FPolicy Server | `shared/templates/fpolicy-server-fargate.yaml` | VpcId, SubnetIds, FsxnSvmSecurityGroupId, ContainerImage, AlarmNotificationTopicArn | Yes |
| CloudWatch Log Alarm | `shared/templates/cloudwatch-log-alarm.yaml` | LogGroupName, TargetPattern | No |
| Lakehouse Monitoring | `shared/templates/lakehouse-monitoring.yaml` | OntapMgmtEndpoint, S3AccessPointArn, VPC/Subnet/SG | Yes |

#### クイックデプロイ: 性能ダッシュボード（VPC不要）

```bash
aws cloudformation deploy \
  --template-file shared/templates/fsxn-monitoring-dashboard.yaml \
  --stack-name fsxn-monitoring \
  --parameter-overrides \
    FileSystemId=fs-0123456789abcdef0 \
    FileSystemName=fsxn-prod \
    CapacityThresholdPercent=80 \
    NotificationEmail=storage-team@example.com
```

#### クイックデプロイ: Qtree クォータ監視（VPC必要）

```bash
aws cloudformation deploy \
  --template-file shared/templates/qtree-quota-monitor.yaml \
  --stack-name fsxn-qtree-monitor \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=arn:aws:secretsmanager:<region>:<account>:secret:<name> \
    SvmName=svm-prod-01 \
    VpcId=vpc-0123456789abcdef0 \
    SubnetIds=subnet-aaa,subnet-bbb \
    SecurityGroupId=sg-0123456789abcdef0 \
    QuotaThresholdPercent=85 \
    NotificationEmail=storage-team@example.com \
    CreateSecretsManagerEndpoint=false \
  --capabilities CAPABILITY_NAMED_IAM
```

> **VPC Endpoint に関する補足**: Tier 2 スタックを既にデプロイ済みの場合（Secrets Manager エンドポイントが作成済み）は `CreateSecretsManagerEndpoint=false` を設定してください。上記の VPC Endpoint Conflict Matrix を参照。

### Tier 5: 運用アドオン

| Stack | Template | Purpose |
|-------|----------|---------|
| Object Ledger | `shared/templates/object-ledger.yaml` | DynamoDB per-file processing state |
| SQS Buffering | `shared/templates/sqs-buffering.yaml` | SQS buffer + DLQ for high-volume |
| Secrets Rotation | `shared/templates/secrets-rotation-sample.yaml` | Auto-rotate vendor API keys |
| PagerDuty Escalation | `shared/templates/pagerduty-escalation.yaml` | Alert routing to PagerDuty |

---

## VPC Endpoint 競合マトリクス

複数のスタックが VPC Endpoint を作成できます。2種類の競合メカニズムを理解することが重要です。

- **Interface Endpoint**（SecretsManager, SNS, STS, Comprehend）: 同一 VPC 内に同一サービスの `PrivateDnsEnabled=true` の Interface Endpoint を2つ作成すると `"private-dns-enabled cannot be set because there is already a conflicting DNS domain"` エラーでデプロイがロールバックされます。最も頻繁に発生するデプロイ失敗です。
- **Gateway Endpoint**（S3, DynamoDB）: DNS 競合は発生しませんが、同一ルートテーブルに同じサービスの Gateway Endpoint を2つ紐づけることはできません。

既にエンドポイントが存在する場合は、対応する `CreateXxxEndpoint=false` を設定してください。

| Service | Type | automated-response | restore-verification | content-classification | syslog-vpce | vpc-endpoints |
|---------|------|:------------------:|:--------------------:|:----------------------:|:-----------:|:-------------:|
| **SecretsManager** | Interface | `CreateVpcEndpoints` | `CreateSecretsManagerEndpoint` | — | — | Always |
| **SNS** | Interface | `CreateVpcEndpoints` | — | `CreateVpcEndpoints` | — | — |
| **STS** | Interface | — | `CreateStsEndpoint` | — | — | — |
| **Comprehend** | Interface | — | — | `CreateVpcEndpoints` | — | — |
| **CW Logs Syslog** | Interface | — | — | — | Always | — |
| **S3** | Gateway | — | `CreateS3GatewayEndpoint` | `CreateVpcEndpoints` | — | Always |
| **DynamoDB** | Gateway | — | — | `CreateVpcEndpoints` | — | — |

### 既存エンドポイントの確認方法

```bash
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=<your-vpc-id>" \
  --query 'VpcEndpoints[].{Service:ServiceName,Type:VpcEndpointType,State:State}' \
  --output table
```

### 判断ルール

デプロイする各スタックについて、以下を実行してください。
1. 上記のコマンドで既存エンドポイントを確認
2. スタックが作成するエンドポイントが既に存在するか照合
3. 存在する場合 → 該当の `Create*Endpoint=false` に設定
4. 存在しない場合 → `true`（デフォルト）のまま

---

## 検証済みデプロイパス

### Path 1: 監査ログ転送（最もシンプル）

**目的**: FSx for ONTAP の監査ログをオブザーバビリティベンダーに転送する。

```
s3-access-point.yaml → prerequisites.yaml → integrations/<vendor>/template.yaml
```

**手順**:
1. SVM で監査ログを有効化（ONTAP CLI: `vserver audit create`）
2. ログ配信先の S3 バケットを作成
3. `s3-access-point.yaml` をデプロイ（Lambda 用の AP を作成）
4. `prerequisites.yaml` をデプロイ（EventBridge スケジューラ、チェックポイントテーブル）
5. ベンダーテンプレートをデプロイ（例: `integrations/datadog/template.yaml`）

**VPC 設定は不要です。** Lambda はデフォルトで VPC 外にデプロイされます。

### Path 2: インシデント対応

**目的**: ユーザー/IP の自動ブロックと TTL ベースの自動解除。

```
automated-response.yaml (CreateVpcEndpoints=true)
  → automated-response-ttl.yaml
```

**手順**:
1. ONTAP 管理者クレデンシャルを Secrets Manager に保存
2. VPC/Subnet/SG を指定して `automated-response.yaml` をデプロイ
3. Lambda Layer（`shared/lambda-layers/`）をデプロイし、関数に適用
4. 同じ VPC パラメータで `automated-response-ttl.yaml` をデプロイ

**セキュリティに関する補足**: Lambda は ONTAP 管理 IP に直接到達するため VPC 内で実行されます。Secrets Manager と SNS の VPC Endpoint は最初のスタックが作成します。

### Path 3: リカバリポイント検証

**目的**: スナップショットの復元前にランサムウェア指標をスキャンして安全性を検証する。

**想定実行時間**: 1 回の検証あたり 25〜50 分（FSx-ONTAP 同期遅延が大部分を占める）。

```
automated-response.yaml (first, creates SecretsManager EP)
  → restore-verification.yaml (CreateSecretsManagerEndpoint=false)
```

**タイミング設計**（なぜこの時間がかかるのか）:

| フェーズ | 所要時間 | 理由 |
|---------|---------|------|
| CreateFlexClone | 約 5 秒 | ONTAP REST API、即時の CoW クローン作成 |
| WaitForFsxSync | 10 分（設定可能） | 静的待機 — FSx API が ONTAP 作成ボリュームを発見するのに 12〜36 分必要（[AWS ドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)）。無駄な Lambda 呼び出しを回避 |
| WaitForFsxDiscovery | 0〜40 分（ポーリング） | 120 秒間隔でクローンの発見を待機。10 分の静的 Wait 後は数回の試行で成功する傾向 |
| AttachAccessPoint | 1〜3 分 | S3 AP 作成 + AVAILABLE 遷移 |
| ScanForIndicators | 数秒〜数分 | オブジェクト数に依存（ListObjectsV2 ページネーション） |
| RecordVerdict + Cleanup | 約 1 分 | DynamoDB 書き込み + AP デタッチ + ボリューム削除 |

**このパス固有の前提条件**:
- 対象ボリュームが **UNIX セキュリティスタイル**であること（確認: `volume show -fields security-style`）
- 対象 SVM に **ONTAP ネイティブ S3 サーバーが無効**であること（確認: `vserver object-store-server show`）
- サブネットのルートテーブル ID が必要

**手順**:
1. 前提条件を検証（`preflight-check.sh --profile restore-verification` を実行）
2. `automated-response.yaml` が未デプロイなら先にデプロイ
3. `CreateSecretsManagerEndpoint=false` を指定して `restore-verification.yaml` をデプロイ
4. スナップショット詳細を指定して Step Functions ワークフローを実行

### Path 4: コンテンツ分類（PII スキャナー）

**目的**: Amazon Comprehend を使い、FSx for ONTAP ボリューム上のファイルから PII を検出する。

```
content-classification-scanner.yaml (VPC外 mode)
```

**最もシンプルなデプロイ** — VpcId を空のままにします。対象ボリュームに Internet-origin S3 Access Point がアタッチされている必要があります。

VPC スコープのアクセスポイント（restore-verification が作成するもの）を使う場合は、VpcId を設定し、既存のエンドポイントがあれば作成を無効にしてください。

### Path 5: フルスイート（全機能）

VPC Endpoint の競合を回避するため、以下の順序でデプロイしてください。

| Order | Stack | Endpoint Settings |
|-------|-------|-------------------|
| 1 | `automated-response.yaml` | `CreateVpcEndpoints=true` (creates SecretsManager + SNS) |
| 2 | `automated-response-ttl.yaml` | No endpoints created |
| 3 | `restore-verification.yaml` | `CreateSecretsManagerEndpoint=false`, `CreateStsEndpoint=true`, `CreateS3GatewayEndpoint=false` (if S3 GW EP exists) |
| 4 | `content-classification-scanner.yaml` | Deploy in **VPC-外 mode** (VpcId='') for simplicity |
| 5 | `syslog-vpce-cloudwatch.yaml` | Always creates CW Logs Syslog EP (unique, no conflict) |

---

## デプロイ前検証 (Pre-flight)

VPC モードのスタックをデプロイする前に、事前検証スクリプトを実行してください。

```bash
bash shared/scripts/preflight-check.sh \
  --vpc-id vpc-0123456789abcdef0 \
  --profile automated-response

# Available profiles:
#   audit-shipping        — Path 1
#   automated-response    — Path 2
#   restore-verification  — Path 3
#   content-classification — Path 4
#   full-suite            — Path 5
```

スクリプトの確認項目:
- 既存 VPC Endpoint（重複作成によるデプロイ失敗の防止）
- セキュリティグループのエグレスルール（ONTAP 管理 IP への HTTPS 443）
- サブネットのルートテーブル関連付け
- 対象 SVM の ONTAP S3 サーバー有無（restore-verification のみ）
- S3 Access Point のネットワークオリジン（Internet vs VPC）

---

## パラメータファイルテンプレート

`cfn-params/` に CloudFormation 標準 JSON 形式のサンプルパラメータファイルがあります。

```
cfn-params/
├── README.md                              ← Usage instructions
├── automated-response.example.json
├── automated-response-ttl.example.json
├── restore-verification.example.json
├── content-classification.example.json
└── vendor-datadog.example.json
```

コピーしてリネーム（`.example` を削除）し、値を入力してからデプロイします。

```bash
cp cfn-params/automated-response.example.json cfn-params/automated-response.json
# Edit with your values

# Option A: create-stack (supports file:// parameter files)
aws cloudformation create-stack \
  --stack-name fsxn-automated-response \
  --template-body file://shared/templates/automated-response.yaml \
  --parameters file://cfn-params/automated-response.json \
  --capabilities CAPABILITY_NAMED_IAM

# Option B: deploy (inline Key=Value only — no file:// support)
aws cloudformation deploy \
  --template-file shared/templates/automated-response.yaml \
  --stack-name fsxn-automated-response \
  --parameter-overrides \
    OntapMgmtIp=198.51.100.10 \
    OntapCredentialsSecretArn=arn:aws:secretsmanager:... \
    VpcId=vpc-xxx SubnetIds=subnet-aaa,subnet-bbb \
    SecurityGroupId=sg-xxx DefaultSvmName=svm-prod \
    CreateVpcEndpoints=true \
  --capabilities CAPABILITY_NAMED_IAM
```

**運用に関する補足**: `aws cloudformation deploy --parameter-overrides` は `file://` を**サポートしません**。`deploy` コマンドではインラインの `Key=Value` 形式を使用するか、`create-stack --parameters file://` で JSON ファイルベースのデプロイを行ってください。

---

## デプロイ所要時間の目安

| Path | Stacks | Estimated Duration | Notes |
|------|--------|--------------------|-------|
| 1: Audit Log Shipping | 3 | 5–10 minutes | No VPC Endpoints to create |
| 2: Incident Response | 2 | 8–15 minutes | Interface EP creation: ~2 min each |
| 3: Recovery Point Verification | 2 | 10–20 minutes | STS EP creation adds time |
| 4: Content Classification | 1 | 3–5 minutes (VPC外) | VPC mode adds 5–10 min for EPs |
| 5: Full Suite | 5 | 25–40 minutes | Deploy sequentially |

**運用に関する補足**: restore-verification（Path 3）の Step Functions 実行は、FlexClone への S3 Access Point アタッチ時の FSx for ONTAP 内部同期のため、追加で 15〜40 分かかります。

---

## コスト情報

### VPC Endpoint（固定月額）

| Endpoint Type | Cost (per endpoint) | Notes |
|---------------|--------------------:|-------|
| Interface (SecretsManager, SNS, STS, Comprehend) | ~$7.20/month + $0.01/GB | Per-AZ ENI charge |
| Gateway (S3, DynamoDB) | Free | No hourly or data charges |

**フルスイートのベースライン**（Interface EP 4本 × 2 AZ）: データ処理前で約 $57.60/月。

### コンピュートと API コスト（従量課金）

| Service | Pricing | Typical Monthly (light usage) |
|---------|---------|------------------------------:|
| Lambda (audit polling, 5-min schedule) | $0.20/1M requests + compute | $1–5 |
| Lambda (incident response, event-driven) | Same | < $1 |
| Step Functions (restore-verification) | $0.025/1K transitions | < $1 |
| Comprehend DetectPiiEntities | $0.0001/unit (100 chars) | Varies by scan volume |
| DynamoDB (checkpoint/ledger) | Pay-per-request | < $1 |
| EventBridge Scheduler | $1/1M invocations | < $1 |
| SNS notifications | $0.50/1M publishes | < $1 |

**コストに関する補足**: 最大の変動コストは Comprehend PII スキャン（content-classification）です。平均 10 KB のファイル 10,000 件をスキャンすると、1 回のスキャンで約 $10 かかります。

### コスト最適化

- 必要なパスのみデプロイ（監査ログ転送だけならフルスイートは不要）
- 可能な限り Gateway EP（S3, DynamoDB）を使用 — 無料
- content-classification の `DefaultMaxFiles` でスキャンあたりの Comprehend コストを制限
- content-classification を VPC 外モードでデプロイすれば追加の 4 つの VPC Endpoint が不要

### データ処理に関する考慮事項（コンテンツ分類）

content-classification-scanner を使用する場合、FSx for ONTAP ボリューム上のファイル内容が Amazon Comprehend の `DetectPiiEntities` API に送信されて分析されます。Comprehend は同一 AWS リージョン内でデータを処理し、処理後にデータを保存しません。ただし、ファイル内容を AWS AI サービスに送信することを制限するデータレジデンシーや分類ポリシーがある場合は、機密ボリュームで PII スキャンを有効にする前にコンプライアンスチームと確認してください。

---

## ONTAP バージョン要件

| Feature | Minimum ONTAP Version | FSx for ONTAP Support |
|---------|----------------------|----------------------|
| REST API (all stacks) | 9.6+ | All FSx for ONTAP versions |
| S3 Access Point (restore-verification, classification) | 9.11.1+ | Supported since launch (9.11.1+) |
| Audit logging (NAS) | 9.0+ | All FSx for ONTAP versions |
| Name-mapping (automated-response) | 9.0+ | All FSx for ONTAP versions |
| FPolicy (external server) | 9.0+ | All FSx for ONTAP versions |

FSx for ONTAP は ONTAP 9.11.1 以降で動作するため、本プロジェクトの全機能がサポートされます。

---

## 既存インフラとの制約

### ONTAP S3 サーバーの排他制約（restore-verification のみ）

FSx for ONTAP S3 Access Point と ONTAP ネイティブ S3 サーバーは、同一 SVM 上で排他的です。SVM に ONTAP S3 サーバーが有効になっている場合、`restore-verification` スタックの FlexClone + S3 AP アタッチステップは以下のエラーで失敗します。

> Amazon FSx is unable to create an S3 access point because of an existing ONTAP object storage server on SVM {svm}

**対処法**: ONTAP S3 サーバーのない別の SVM を使用するか、既存サーバーを削除する（データ損失リスクあり — チームと確認のこと）。

### ボリュームセキュリティスタイル（restore-verification のみ）

S3 Access Point は対象ボリュームに UNIX セキュリティスタイルを要求します。NTFS や mixed ボリュームは直接の S3 AP アタッチに対応していません。

**確認方法**: `curl -sk -u admin:pass "https://<mgmt-ip>/api/storage/volumes?name=<vol>&fields=nas.security_style"`

### Active Directory 連携

`automated-response` スタックの SMB ユーザーブロック（name-mapping）は ONTAP SVM レベルで動作します。ドメイン ID（例: `CORP\jdoe`）で SMB ユーザーをブロックするには、SVM が Active Directory に参加している必要があります。

#### AD 参加済み SVM での S3 Access Point 利用

SVM が AD 参加済み（CIFS 有効）の場合、**S3 AP データ操作にも AD ドメインコントローラーへの接続が必須**です。SMB だけでなく、`restore-verification` や `content-classification` が使用する S3 AP アクセスにも影響します。

| 条件 | S3 AP データ操作 |
|------|:---:|
| 非 AD SVM（CIFS 無効） | ✅ 常に動作 |
| AD 参加 SVM + AD DC 到達可能 | ✅ 動作 |
| AD 参加 SVM + AD DC 到達不能 | ❌ AccessDenied |

**事前確認**: AD 参加 SVM で `restore-verification` をデプロイする前に AD 健全性を確認してください:
```bash
# 同じ VPC/サブネットの EC2 から:
nslookup <ADドメインFQDN> <AD-DNS-IP>
# タイムアウト → AD DC が停止 → S3 AP データ操作は失敗する
```

**設計上の推奨**: `restore-verification` には可能な限り非 AD SVM を使用してください。AD 参加 SVM を使う場合は、AD インフラの監視と高可用性を確保してください。

#### AD 参加: 検証済みデプロイ手順

1. **AD を作成または特定**（AWS Managed AD またはセルフマネージド）
2. **`demo-ad-environment.yaml` をデプロイ**（または既存 AD インフラを使用）
3. **`demo-ad-join-svm.sh` を実行**して SVM を AD に参加
4. **CIFS 共有を作成**（SMB アクセスに必要）

#### AD 参加: 重要な設定（ONTAP 9.17.1 で検証済み）

| 設定 | AWS Managed AD | セルフマネージド AD |
|------|---------------|-----------------|
| OU パス | `OU=Computers,OU=<ShortName>,DC=...` | `OU=Computers,DC=...` またはカスタム |
| FileSystemAdministratorsGroup | `Domain Admins` | `Domain Admins` または委任されたグループ |
| NetBIOS 名 | ドメイン ShortName と異なる値 | ドメイン内で一意 |
| ユーザー名 | `Admin` | 委任された権限を持つサービスアカウント |

**よくある失敗**: `FileSystemAdministratorsGroup` に `AWS Delegated FSx Administrators` を使用すると SVM が `MISCONFIGURED` 状態になります。`Domain Admins` を使用してください。

**AWS Managed AD の OU パス**: AWS Managed AD はドメインの短縮名で中間 OU を作成します。ドメイン `demo.fsx.local`（ShortName: `demo`）の場合、正しいパスは `OU=Computers,OU=demo,DC=demo,DC=fsx,DC=local` です。`OU=Computers,DC=demo,DC=fsx,DC=local` ではありません。

**MISCONFIGURED からの復旧**: `aws fsx update-storage-virtual-machine` を正しいパラメータで再実行できます。SVM の削除は不要です。

#### Windows EC2 ドメイン参加（CloudFormation）

`AWS::SSM::Association` で AWS 管理ドキュメント `AWS-JoinDirectoryServiceDomain` を使用してください。EC2 インスタンスの `SsmAssociations` プロパティや `aws:domainJoin` のカスタム SSM Document は使用しないでください。

検証済みパターンは `shared/templates/demo-ad-environment.yaml` を参照してください。

#### デモ環境の前提条件

`demo-ad-environment.yaml` をデプロイする前に、以下の VPC Endpoint が存在することを確認してください（プライベートサブネットの EC2 に SSM でアクセスするために必要）:

```bash
# 既存の SSM エンドポイントを確認
aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=<vpc-id>" \
  --query 'VpcEndpoints[?contains(ServiceName,`ssm`)].ServiceName'

# 不足している場合は作成（サービスごとに1コマンド）:
for svc in ssm ssmmessages ec2messages; do
  aws ec2 create-vpc-endpoint --vpc-id <vpc-id> --vpc-endpoint-type Interface \
    --service-name com.amazonaws.<region>.$svc \
    --subnet-ids <subnet1>,<subnet2> --security-group-ids <sg-id> --private-dns-enabled
done
```

#### NFS ブロック: Export-Policy vs NACL

| メカニズム | 同一サブネット | 異なるサブネット | root クライアント | 効果タイミング |
|-----------|:-----------:|:------------:|:-----------:|:-------------:|
| Export-policy deny rule | ✅ 有効 | ✅ 有効 | ✅ root もブロック | 即時 |
| NACL deny rule | ❌ 無効 | ✅ 有効 | ✅ 全てブロック | 即時 |

多くの FSx for ONTAP デプロイメント（クライアントと FSx ENI が同一サブネット）では、**export-policy deny rule が確実なメカニズム**です。NACL はクライアントと FSx が異なるサブネットにある場合の多層防御としてのみ有用です。

#### 「同一サブネットだがパケットレベルのブロックが必要」な場合の選択肢

セキュリティポリシーがネットワーク層のブロックを要求する場合でも、クライアントと FSx が同一サブネットにある場合は以下の選択肢があります。

| 選択肢 | 方法 | トレードオフ |
|--------|------|------------|
| **1. クライアントを別サブネットに移動** | アプリケーション用サブネットとストレージ用サブネットを分離。FSx ENI は `subnet-storage`、クライアントは `subnet-app` | 再設計が必要。本番環境のベストプラクティスとしては推奨 |
| **2. Security Group 間接拒否** | クライアントの SG を FSx ENI のインバウンド許可ソースから除外。ただし既存 TCP 接続はタイムアウトまで維持される | SG 変更はそのグループの全クライアントに影響 |
| **3. Export-policy deny（推奨）** | 本モジュールで既に実装済み。即時有効、root でもブロック。ネットワーク層の変更不要 | 同一 IP からの NFS 以外のトラフィック（DNS、SSH 等）はブロックできない |
| **4. ホストレベルファイアウォール** | SSM 経由でクライアントに iptables ルールを追加: `iptables -A OUTPUT -d <FSx-IP> -j DROP` | 攻撃者のホストへの SSM アクセスが必要（侵害されている可能性あり） |
| **5. サブネットルート変更（上級）** | クライアント IP 向けのブラックホールルートをルートテーブルに追加 | そのIP の全トラフィックに影響（NFS以外含む） |

**同一サブネット構成での推奨**: export-policy deny rule（選択肢 3）を主要メカニズムとして使用してください。即時有効、IP 単位、ネットワークインフラ変更不要です。NFS 以外のトラフィックもブロックする必要がある場合は、長期的なアーキテクチャ改善として選択肢 1 を検討してください。

#### SMB ブロック: Name-Mapping の制限事項（検証済み）

name-mapping deny メカニズム（`replacement: " "`）には重要なスコープ制限があります。

| 条件 | SMB をブロック? | 備考 |
|------|:-----------:|------|
| UNIX/MIXED ボリューム、非管理者ユーザー | ✅ | SMB アクセス時に UNIX ID 解決が必要 |
| NTFS ボリューム、全ユーザー | ❌ | NTFS ACL が直接評価され、マッピングはスキップ |
| 全ボリューム、Domain Admins メンバー | ❌ | FileSystemAdministratorsGroup がマッピングをバイパス |

#### 管理者/特権アカウントのブロック: 選択肢と考慮事項

Domain Admins（または`FileSystemAdministratorsGroup`のメンバー）は name-mapping deny をバイパスします。侵害された管理者アカウントをブロックする必要がある場合は、以下の代替メカニズムを使用してください。

| 選択肢 | 方法 | 効果 | 考慮事項 |
|--------|------|------|---------|
| **1. ADアカウント無効化** | PowerShell: `Disable-ADAccount -Identity <user>` | **再認証時に即時有効** — Kerberos更新が「credentials revoked」で失敗 | ストレージ以外の全サービスに影響。既存セッションはチケット有効期限（デフォルト約10時間）まで継続。セッション切断と併用推奨 |
| **2. セッション強制切断** | ONTAP REST API: `DELETE /protocols/cifs/sessions/{svm}/{id}/{conn}` | アクティブセッションを即時終了、再認証を強制 | 選択肢1と併用が前提 — 切断のみでは一時的（アカウントが有効ならクライアントが再接続） |
| **3. SMB用export-policy有効化** | `vserver cifs options modify -is-exportpolicy-enabled true` + denyルール追加 | IPベースのSMBブロック、ユーザー権限に関係なく動作 | デフォルト無効。SVMレベルの設定変更が必要。参照: [AWS Blog: export-policyによるアクセス制限](https://aws.amazon.com/blogs/storage/restrict-access-to-your-amazon-fsx-for-netapp-ontap-volumes-using-export-policies/) |
| **4. NACL deny（クロスサブネット）** | AWS VPC NACLに管理者クライアントIPのdenyルール | ネットワークレベルのブロック、ONTAPメカニズムでは回避不可 | クロスサブネットのみ有効。同一サブネットでは効果なし |
| **5. Security Group除外** | クライアントのSGをFSx ENI許可ソースから除外 | クライアントからの新規TCP接続を阻止 | そのSGの全ユーザーに影響。既存TCP接続は維持される |

**管理者アカウント侵害時の推奨手順**:
1. `Disable-ADAccount`（新規Kerberosチケットの発行を阻止）
2. `DELETE /protocols/cifs/sessions/...`（既存セッションを強制切断）
3. Kerberosチケットキャッシュの期限切れを待機（またはホストアクセス可能なら `klist purge` を強制実行）

出典: [NetApp KB: ADアカウントのロック/無効/期限切れによるNTFSボリュームへのアクセス拒否](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/Denied_access_to_NTFS_volume_because_AD_Account_is_locked_or_disabled_or_expired) — 無効化/ロックされたADアカウントが「Kerberos Error: Clients credentials have been revoked」を発生させ、name-mappingやセキュリティスタイルに関係なくNTFSアクセスをブロックすることを確認。

> **運用に関する補足**: AD管理者アカウントの無効化は影響の大きいアクションです。実際のインシデントではADチームと連携し、復旧手順を文書化してください。日常的に使用しない専用の「break-glass」管理者アカウントを用意しておくことで、プライマリの管理者を無効化しても全ての管理アクセスを失わずに済みます。

**読者への重要な注意**: FSx for ONTAP ボリュームが NTFS セキュリティスタイル（Windows 環境で一般的）の場合、name-mapping deny は SMB アクセスをブロックしません。NTFS ボリュームの代替メカニズム:
- AD ユーザーアカウントを直接無効化（Kerberos 認証を阻止）
- NTFS ACL を変更してユーザーのアクセスを除外
- Security Group 変更でネットワークレベルのアクセスをブロック（上記のクロスサブネット制限あり）

**なぜこうなるか**（NetApp ドキュメントより）:

NTFSセキュリティスタイルのボリュームでは、ONTAPは内部資格情報構築（UID/GID取得）のためにwin→unix name-mappingを実行しますが、最終的なアクセス判定は**Windows資格情報（NTFS ACL）**で行います。deny mapping（`" "`）はUNIX ID解決を阻止しますが、ONTAPは`default-unix-user`（`pcuser`, UID 65534）にフォールバックし、元のWindowsトークンでNTFS ACL評価を続行します。

UNIX/MIXEDスタイルのボリュームでは、アクセスは**マッピングされたUNIX UID/GID**で制御されます。deny mappingがUNIX ID解決を完全にブロックし、アクセスが拒否されます。

出典:
- [NetApp KB: CIFS clients access NTFS security style resources](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/How_does_name-mapping_work_when_CIFS_clients_access_NTFS_security_style_resources) — 「Access is granted or denied based on the Windows credentials because the volume is set to NTFS security style.」
- [NetApp KB: Understanding name-mapping in a multiprotocol environment](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/Understanding_name-mapping_in_a_multiprotocol_environment) — CIFS→UNIXスタイル: 「Access is granted or denied based on the UID and GID(s) of the UNIX credentials」
- [NetApp ONTAP Docs: Security styles and their effects](https://docs.netapp.com/us-en/ontap/smb-admin/security-styles-their-effects-concept.html) — 「Security styles only determine the type of permissions ONTAP uses to control data access」
- [NetApp ONTAP Docs: Create name mappings](https://docs.netapp.com/us-en/ontap/nfs-admin/create-name-mapping-task.html) — 「-replacement statement to explicitly deny a mapping to the user by using the null replacement string \" \"」
- [NetApp KB: Workload Security のブロック解除手順](https://kb.netapp.com/Cloud/BlueXP/DII/How_to_manually_unblock_SMB_CIFS_access_that_blocked_by_Workload_security) — DII が `replacement: " "` を使用することを確認（本プロジェクトと同一メカニズム）

**DII Storage Workload Security への示唆**: DII の SMB ユーザーブロックも同じ name-mapping deny メカニズムを使用しています。これは DII のブロックも UNIX/MIXED セキュリティスタイルのボリュームでのみ SMB アクセス制御に有効であることを意味します。（DII のドキュメントにはこの制限の明示的な記載はありませんが、上記の ONTAP の name-mapping 動作から論理的に導かれます。）

**調査中の事象（制限事項 3）**: AD参加SVMでのテスト（ONTAP 9.17.1P7D1）において、`replacement: " "` のname-mappingエントリが作成直後（HTTP 201返却、即時読み戻し確認済み）に数秒で消失する事象を確認しています。非AD参加SVMでは発生しません。根本原因は調査中 — `default-unix-user`の設定やCIFSサーバーの資格情報構築プロセスとの関連が疑われます。DII Workload Securityはこのメカニズムを正常に使用しているため、基本的な非互換性ではなく設定差異が原因と推察されます。

**根本原因特定（制限事項 3）**: AD参加SVMのONTAP 9.17.1P7D1で、バックグラウンドバリデーションプロセスがname-mappingの`replacement`値をローカルUNIXユーザーテーブルに対して定期検証し、有効なUNIXユーザーに解決できない値（スペース`" "`を含む）を作成後30〜60秒で自動削除することを確認しました。DII Workload Securityが正常に動作する理由は、関連するUNIXユーザー検索インフラが適切に構成されているためと推察されます。

**検証済みの動作**:
| replacement 値 | 60秒後に残存? | 理由 |
|----------------|:---:|------|
| `" "` (スペース) | ❌ 自動削除 | UNIXユーザーとして解決不能 |
| `"pcuser"` (UID 65534) | ✅ 残存 | ローカルUNIXユーザーテーブルに存在 |
| `"nobody"` (UID 65535) | ✅ 残存 | ローカルUNIXユーザーテーブルに存在 |

**AD参加SVMでの回避策**: `replacement: " "` の代わりに `replacement: "nobody"` を使用します。`nobody`ユーザー（UID 65535）は標準的な無権限UNIXアイデンティティです。ボリュームのパーミッションを制限（モード750または700、owner=root、group=root）と組み合わせることで、UNIX/MIXEDスタイルのボリュームで効果的にSMBアクセスを拒否できます（`nobody`にはother読み取り権限がないため）。

**ボリューム側の必要なアクション**（`nobody`回避策使用時）:
```bash
# ボリュームルートのパーミッションを'other'アクセス拒否に設定:
curl -sk -u fsxadmin:<pass> -X PATCH \
  "https://<mgmt-ip>/api/storage/volumes/<uuid>" \
  -H "Content-Type: application/json" \
  -d '{"nas":{"unix_permissions":"750"}}'
```

**補足**: この回避策はメカニズムの意味が変わります — 「マッピング失敗→認証レベルでアクセス拒否」から「マッピング成功（無権限UID）→UNIXパーミッションでアクセス拒否」へ。最終結果（ユーザーがファイルアクセス不可）は同じですが、ボリュームのUNIXパーミッションが制限的である必要があります。

> **ステータス: 解決済み** — `ontap_response.py`（Lambda Layer v3以降）はAD参加SVMを自動検出し、`replacement: "nobody"`を自動的に使用します。手動設定は不要です。E2E検証済み: AD参加SVMでmappingが90秒以上永続することを確認（以前のスペースreplacementでは30-60秒で削除されていた）。

### DNS / Route 53

Route 53 レコードを作成するスタックはありません。VPC Endpoint のプライベート DNS は AWS が自動処理します（PrivateDnsEnabled=true）。カスタム DNS 設定は不要です。

---

## トラブルシューティング

### スタックロールバック: "private-dns-enabled cannot be set"

**原因**: VPC に既存の同一サービスの VPC Endpoint がある。

**対処法**: 該当の `Create*Endpoint=false` パラメータを設定する。上記の VPC Endpoint 確認コマンドを使用。

### Lambda タイムアウト（ONTAP 管理 IP への接続）

**原因**: セキュリティグループが管理 IP への HTTPS (443) アウトバウンドを許可していない、または Lambda が管理 IP へのルートを持たないサブネットにいる。

**対処法**: SecurityGroupId が OntapMgmtIp:443 へのエグレスを許可していることを確認。SubnetIds が FSx と同じ VPC にあることを確認。

### Secrets Manager の AccessDeniedException

**原因**: (a) Secret ARN が変わっている（Secrets Manager は再作成時に新しいランダムサフィックスを割り当てる）、または (b) Secrets Manager の VPC Endpoint が利用できない。

**対処法**: 正確な ARN を確認: `aws secretsmanager describe-secret --secret-id <name> --query ARN`。正しい ARN でスタックを更新する。

### restore-verification: "existing ONTAP object storage server"

**原因**: 対象 SVM に ONTAP S3 サーバーがある。これは構造的な競合であり、リトライでは解決しない。

**対処法**: 別の SVM を使用するか、ONTAP S3 サーバーを削除する（データ損失がないか事前確認のこと）。

### S3 AP AccessDenied（AD参加済みSVM: HeadBucket成功、データ操作失敗）

**原因**: SVM が Active Directory に参加済み（CIFS有効）だが、AD ドメインコントローラーに到達できない。AD参加SVMでは、ONTAP がすべてのファイルシステム操作で `unix→win` 逆引きを実行する。AD DC が停止していると、このルックアップが失敗し全データ操作が AccessDenied を返す。UNIX セキュリティスタイルのボリュームでも同様。

**診断方法**: HeadBucket は成功する（S3層のAP存在確認のみ）が、ListObjectsV2 / GetObject / PutObject はすべて AccessDenied。IAM ポリシーと AP リソースポリシーは正しい。**これは IAM の問題ではない。**

**対処法**:
1. SVM サブネットからの AD DC 接続性を確認: `nslookup <domain> <ad-dns-ip>`
2. AD が削除/再作成された場合: SVM の DNS IPs を更新するか、CIFS を強制削除して再作成（以下参照）
3. AD が恒久的に不要で SMB が不要な場合: CIFS サービスを強制削除して AD 依存を除去

**CIFS 強制削除と新 AD への再参加**（ONTAP REST API 経由）:
```bash
# 1. secure DNS 動的更新を無効化
curl -sk -X PATCH -u fsxadmin:$PW -H 'Content-Type: application/json' \
  -d '{"dynamic_dns":{"use_secure":false}}' \
  "https://<mgmt-ip>/api/name-services/dns/<svm-uuid>"

# 2. CIFS サービスを強制削除
curl -sk -X DELETE -u fsxadmin:$PW -H 'Content-Type: application/json' \
  -d '{"force":true,"ad_domain":{"fqdn":"<domain>","user":"Admin","password":"<pw>"}}' \
  "https://<mgmt-ip>/api/protocols/cifs/services/<svm-uuid>"

# 3. stale records をクリーンアップ
curl -sk -X POST -u fsxadmin:$PW -H 'Content-Type: application/json' \
  -d '{"vserver":"<svm-name>"}' \
  "https://<mgmt-ip>/api/private/cli/vserver/cifs/users-and-groups/remove-stale-records"

# 4. 新しい AD で CIFS を再作成（新しい NetBIOS 名を使用 — 旧名は AD に残る）
curl -sk -X POST -u fsxadmin:$PW -H 'Content-Type: application/json' \
  -d '{"svm":{"uuid":"<svm-uuid>"},"name":"<NEW-NETBIOS>","ad_domain":{"fqdn":"<domain>","organizational_unit":"<ou>","user":"Admin","password":"<pw>"}}' \
  "https://<mgmt-ip>/api/protocols/cifs/services"
```

**重要な注意点**:
- 失敗した再参加試行ごとに AD にコンピュータアカウントが作成される — 常に新しい NetBIOS 名を使用
- FSx API は ONTAP レベルで CIFS が正常でも SVM を `MISCONFIGURED` と報告する場合がある
- FSx は S3 AP の `s3_unix` name-mapping を自動管理 — 手動設定不要
- 同一アカウントの S3 AP アクセスには AP リソースポリシーは不要（IAM identity policy のみで十分）

---

## Day 2: デプロイ後の検証と運用

デプロイ後、スタックが正常に動作していることを確認してください。

### 即時検証（1 時間以内）

| Path | Verification Step | Expected Result |
|------|-------------------|-----------------|
| 1 (Audit) | Perform a file operation on the audited share, wait 5–10 min | Logs appear in vendor platform (`source:fsxn` in Datadog) |
| 2 (Response) | Send `health_check` via SNS trigger | Lambda returns `"status": "healthy"` |
| 3 (Recovery) | Execute Step Functions with a test snapshot | Workflow completes with `verdict: clean` or `suspicious` |
| 4 (Classification) | Invoke Lambda with a test S3 AP ARN | DynamoDB table contains scan results |

### 継続的な監視

以下の CloudWatch メトリクスにアラームを設定してください。

| Metric | Alarm Condition | Action |
|--------|----------------|--------|
| Lambda Errors (all stacks) | > 0 for 5 minutes | Investigate via CloudWatch Logs |
| DLQ ApproximateNumberOfMessagesVisible | > 0 | Replay failed messages (see [DLQ Replay Runbook](runbooks/dlq-replay.md)) |
| Lambda Duration (audit poller) | > 80% of timeout | Increase LambdaTimeout or LambdaMemorySize |
| Step Functions ExecutionsFailed | > 0 | Check execution history in console |

### 定期レビュー（月次）

- **ProtectedAccountsExtra**: 自動ブロック除外アカウントの一覧をレビューする。サービスアカウントは時間とともに変化するため、古いエントリが蓄積するとインシデント対応の有効性が低下する
- **Secret ARN の有効性**: Secrets Manager のシークレットがローテーションまたは再作成された場合、ARN サフィックスが変わる。`aws secretsmanager describe-secret --secret-id <name> --query ARN` で確認
- **VPC Endpoint のコスト**: Interface EP のコストを確認し、不要になったものを削除する

---

## ロールバックとクリーンアップ

### 自動ロールバック

CloudFormation はデプロイ失敗時に自動的にロールバックします。スタック自体の手動介入は不要です。ただし:

- 失敗ポイント前に作成された VPC Endpoint はロールバックで**クリーンアップされます**
- `DeletionPolicy: Retain` の DynamoDB テーブルは残る可能性があります（`aws dynamodb list-tables` で確認）
- スタックが `ROLLBACK_COMPLETE` 状態になった場合、同名で再作成する前に削除が必要: `aws cloudformation delete-stack --stack-name <name>`

### 手動クリーンアップ（完全削除）

ベンダー固有のクリーンアップスクリプトまたは共有ユーティリティを使用します。

```bash
# Single vendor stack
bash integrations/<vendor>/scripts/cleanup.sh --all -y

# Shared stacks (delete in reverse deployment order)
aws cloudformation delete-stack --stack-name fsxn-content-classification
aws cloudformation delete-stack --stack-name fsxn-restore-verification
aws cloudformation delete-stack --stack-name fsxn-automated-response-ttl
aws cloudformation delete-stack --stack-name fsxn-automated-response
```

**運用に関する補足**: デプロイの逆順でスタックを削除してください。`automated-response` スタックは他のスタックが使用する VPC Endpoint を作成しており、先に削除すると他スタックの削除中に Secrets Manager アクセスが失われます（通常は無害ですがエラーログが生成されます）。

### E2E 検証環境のクリーンアップ

検証環境全体（FSx + AD + デモスタック）を解体する場合、以下の順序で削除してください（依存関係エラー回避）。

| 順序 | リソース | コマンド | 備考 |
|------|---------|---------|------|
| 1 | `fsxn-rv-e2e` スタック | `aws cloudformation delete-stack --stack-name fsxn-rv-e2e` | Step Functions + Lambda + DynamoDB + VPC Endpoints |
| 2 | ヘルパー EC2 インスタンス | `aws ec2 terminate-instances --instance-ids <id>` | SSM ヘルパー、Windows デモ EC2 |
| 3 | FlexClone ボリューム | `aws fsx delete-volume --volume-id <fsvol-id> --ontap-configuration '{"SkipFinalBackup":true}'` | 使い捨てクローンには必ず `SkipFinalBackup` を指定 |
| 4 | デモボリューム | 同上 | `demo_*` や `verify_*` ボリューム |
| 5 | AD 環境スタック | `aws cloudformation delete-stack --stack-name fsxn-demo-ad-env` | Managed AD の削除に 15〜30 分 |
| 6 | SVM（専用の場合） | `aws fsx delete-storage-virtual-machine --storage-virtual-machine-id <id>` | ボリュームや AD 関係が残っていると失敗 |
| 7 | IAM ロール/プロファイル | ポリシーデタッチ → プロファイル削除 → ロール削除 | 下記参照 |

**IAM クリーンアップ手順**（順序重要）:
```bash
# 1. インスタンスプロファイルからロールを削除
aws iam remove-role-from-instance-profile \
  --instance-profile-name <name> --role-name <name>
# 2. インスタンスプロファイル削除
aws iam delete-instance-profile --instance-profile-name <name>
# 3. マネージドポリシーをデタッチ
aws iam detach-role-policy --role-name <name> --policy-arn <arn>
# 4. インラインポリシーを削除
aws iam delete-role-policy --role-name <name> --policy-name <name>
# 5. ロール削除
aws iam delete-role --role-name <name>
```

**クリーンアップ時の既知の問題:**

| 問題 | 原因 | 解決策 |
|------|------|--------|
| AD 削除後も SVM が `MISCONFIGURED` のまま | FSx API が旧 AD 設定の状態を保持 | SVM を直接削除、またはFS自体を廃止するなら無視 |
| ボリューム削除失敗: "has one or more clones" | ONTAP recovery queue に古いクローン参照が残留 | 12 時間待つか、`DELETE /private/cli/volume/recovery-queue/purge` で purge（ONTAP 管理者のみ） |
| ボリューム削除失敗: "SnapMirror relationships" | FSx バックアップ管理 | 常に FSx API（`delete-volume`）で削除。ONTAP REST API は使わない |
| スタック DELETE_FAILED | リソース依存関係 | `describe-stack-events` で対象リソースを確認し、解決してからリトライ |

---

## 関連ドキュメント

- [前提条件とリソースデプロイ](prerequisites.md) — S3 バケット、監査ログ、S3 AP 作成
- [クイックスタート（最小テスト）](quick-start-minimum.md) — ログ配信の最速確認パス
- [Verified Recovery Point ガイド](verified-recovery-point-guide.md) — restore-verification の詳細
- [Automated Response ガイド](automated-response-guide.md) — インシデント対応の運用
- [マルチアカウントデプロイ](multi-account-deployment.md) — StackSets による組織展開
