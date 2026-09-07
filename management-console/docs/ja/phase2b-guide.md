# FSx for ONTAP Management Console Phase 2B ガイド

🌐 **日本語**（このページ） | [English](../en/phase2b-guide.md)

Phase 2B は既存の Management Console（Phase 2A）を拡張し、カスタムドメイン対応、ロールベースアクセス制御（RBAC）、ダッシュボード自動プロビジョニングを追加します。

## 目次

1. [概要](#概要)
2. [前提条件](#前提条件)
3. [Phase 2A からのアップグレード](#phase-2a-からのアップグレード)
4. [カスタムドメイン設定](#カスタムドメイン設定)
5. [RBAC 設定](#rbac-設定)
6. [ダッシュボード自動プロビジョニング](#ダッシュボード自動プロビジョニング)
7. [トラブルシューティング](#トラブルシューティング)

---

## 概要

Phase 2B で追加される機能:

| 機能 | 説明 |
|------|------|
| カスタムドメイン | Route 53 + ACM 証明書による独自ドメインでのコンソールアクセス |
| RBAC | Cognito グループによる管理者/閲覧者の権限分離 |
| ダッシュボード自動プロビジョニング | CloudFormation デプロイ時に Grafana ダッシュボードを自動インポート |

### 追加・変更されるファイル

```
management-console/
├── lambda/
│   └── dashboard_importer.py       # ダッシュボードインポート Lambda
├── templates/
│   ├── console.yaml                # Route 53 レコード追加（変更）
│   ├── auth.yaml                   # Cognito グループ追加（変更）
│   └── observability.yaml          # ダッシュボードインポート追加（変更）
├── tooljet-workflows/
│   └── rbac-helper.json            # RBAC ロールチェックヘルパー（新規）
├── tests/
│   └── test_dashboard_importer.py  # ユニットテスト（新規）
└── docs/
    ├── ja/phase2b-guide.md         # 本ドキュメント
    └── en/phase2b-guide.md         # 英語版
```

---

## 前提条件

### 既存の Phase 2A デプロイメント

Phase 2B は Phase 2A のインプレースアップグレードです。以下のスタックがデプロイ済みであることを確認してください:

```
fsxn-mgmt-network
fsxn-mgmt-auth
fsxn-mgmt-observability
fsxn-mgmt-console
fsxn-mgmt-monitoring
```

### カスタムドメイン用の前提条件（オプション）

カスタムドメインを使用する場合、以下が必要です:

| リソース | 要件 | 備考 |
|---------|------|------|
| ACM 証明書 | ALB と同じリージョンで発行済み | DNS 検証推奨 |
| Route 53 ホストゾーン | 対象ドメインのパブリックホストゾーン | サブドメインも可 |
| ドメイン名 | Route 53 で管理されているドメイン | 例: `console.example.com` |

#### ACM 証明書の発行

```bash
# 証明書のリクエスト（DNS 検証）
aws acm request-certificate \
  --domain-name console.example.com \
  --validation-method DNS \
  --region ap-northeast-1

# 出力される CertificateArn を控えておく
# DNS 検証レコードを Route 53 に追加して検証を完了させる
```

#### Route 53 ホストゾーン ID の確認

```bash
aws route53 list-hosted-zones-by-name \
  --dns-name example.com \
  --query 'HostedZones[0].Id' --output text
# 出力例: /hostedzone/Z0123456789ABCDEFGHIJ
# "Z0123456789ABCDEFGHIJ" の部分がホストゾーン ID
```

### ダッシュボード自動プロビジョニング用の前提条件（オプション）

| リソース | 要件 | 備考 |
|---------|------|------|
| AMG ワークスペース | 作成済み | API キーの発行が必要 |
| AMG API キー | Admin ロール | Secrets Manager に保存 |

#### AMG API キーの作成と保存

```bash
# AMG ワークスペースで API キーを作成（AMG コンソールから実行）
# 作成した API キーを Secrets Manager に保存
aws secretsmanager create-secret \
  --name fsxn-mgmt-grafana-api-key \
  --description "AMG API key for dashboard auto-provisioning" \
  --secret-string '{"api_key": "<your-amg-api-key>"}'
```

---

## Phase 2A からのアップグレード

Phase 2B は Phase 2A に対する **インプレースアップデート** です。既存の機能はすべて保持されます。

### 新しい環境変数

| 環境変数 | 必須 | 説明 |
|---------|------|------|
| `CUSTOM_DOMAIN_NAME` | いいえ | カスタムドメイン名（例: `console.example.com`） |
| `HOSTED_ZONE_ID` | いいえ* | Route 53 ホストゾーン ID |
| `ADMIN_EMAIL` | いいえ | 初期管理者ユーザーのメールアドレス |

> \* `CUSTOM_DOMAIN_NAME` を設定する場合、`HOSTED_ZONE_ID` と `CERTIFICATE_ARN` も必須です。

### アップグレード手順

#### Step 1: 環境変数の追加

```bash
# 既存の Phase 2A 変数はそのまま維持

# カスタムドメインを使用する場合（オプション）
export CUSTOM_DOMAIN_NAME="console.example.com"
export HOSTED_ZONE_ID="Z0123456789ABCDEFGHIJ"
export CERTIFICATE_ARN="arn:aws:acm:ap-northeast-1:123456789012:certificate/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# 初期管理者ユーザーを作成する場合（オプション）
export ADMIN_EMAIL="admin@example.com"
```

#### Step 2: デプロイスクリプトの実行

```bash
cd management-console/scripts
bash deploy.sh
```

デプロイスクリプトは以下を実行します:
- `CUSTOM_DOMAIN_NAME` が設定されている場合、`CERTIFICATE_ARN` と `HOSTED_ZONE_ID` の存在を検証
- Stack 2（auth）: Cognito グループの作成、`ADMIN_EMAIL` が設定されていれば管理者ユーザーの作成
- Stack 3（observability）: ダッシュボード JSON の S3 アップロードと自動インポート
- Stack 4（console）: Route 53 レコードの作成（カスタムドメイン設定時）

#### Step 3: 動作確認

- カスタムドメインでコンソールにアクセスできることを確認
- 管理者ユーザーでログインし、書き込み操作が可能であることを確認
- 閲覧者ユーザーで書き込み操作がブロックされることを確認
- Grafana ダッシュボードが自動的にインポートされていることを確認

### 後方互換性

- カスタムドメインを設定しない場合、従来通り ALB DNS でアクセス可能
- RBAC グループを設定しない場合、全ユーザーが管理者権限を持つ（Phase 2A と同じ動作）
- `--skip-dashboard-import` フラグでダッシュボードインポートをスキップ可能

---

## カスタムドメイン設定

### 仕組み

CloudFormation テンプレート（`console.yaml`）に条件付きの Route 53 Alias レコードを追加します:

```
ユーザー → console.example.com (Route 53 Alias)
         → ALB (HTTPS/443, ACM 証明書)
         → Cognito 認証
         → ToolJet UI
```

- `CustomDomainName` パラメータが空の場合、Route 53 リソースは作成されない
- ALB DNS による直接アクセスも引き続き可能
- Cognito のコールバック URL にカスタムドメインが自動追加される

### 設定手順

#### 1. ACM 証明書の準備

ALB と同じリージョンで ACM 証明書を発行し、DNS 検証を完了させます:

```bash
# 証明書の状態確認
aws acm describe-certificate \
  --certificate-arn "arn:aws:acm:ap-northeast-1:123456789012:certificate/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
  --query 'Certificate.Status'
# "ISSUED" であることを確認
```

#### 2. 環境変数の設定

```bash
export CUSTOM_DOMAIN_NAME="console.example.com"
export HOSTED_ZONE_ID="Z0123456789ABCDEFGHIJ"
export CERTIFICATE_ARN="arn:aws:acm:ap-northeast-1:123456789012:certificate/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

#### 3. デプロイの実行

```bash
cd management-console/scripts
bash deploy.sh
```

デプロイ完了後、コンソール URL が出力されます:

```
✅ Console URL: https://console.example.com
```

#### 4. DNS 伝播の確認

```bash
# DNS レコードの確認（伝播に数分かかる場合があります）
dig console.example.com +short
# ALB の DNS 名が返されることを確認
```

### カスタムドメインの削除

カスタムドメインを削除する場合は、環境変数を空にして再デプロイします:

```bash
unset CUSTOM_DOMAIN_NAME
unset HOSTED_ZONE_ID
bash deploy.sh
```

クリーンアップスクリプト（`cleanup.sh`）は Route 53 レコードを自動的に削除します。

---

## RBAC 設定

### 仕組み

Cognito User Pool グループと ALB OIDC 認証を組み合わせて、アプリケーションレベルでアクセス制御を実現します:

```
ユーザー → Cognito 認証 → ALB (x-amzn-oidc-data ヘッダー)
         → ToolJet ワークフロー → JWT デコード → cognito:groups クレーム確認
         → fsxn-admins グループ: 全操作可能
         → fsxn-viewers グループ: 読み取り操作のみ
```

### ロールと権限

| ロール | Cognito グループ | 権限 |
|--------|----------------|------|
| 管理者 (Admin) | `fsxn-admins` | 全操作（読み取り + 書き込み） |
| 閲覧者 (Viewer) | `fsxn-viewers` | 読み取り操作のみ |
| 未割当 | なし | 全操作可能（後方互換性） |

> ⚠️ **重要**: Cognito グループが作成されていても、ユーザーがどのグループにも属していない場合は全操作が可能です。これは Phase 2A からの後方互換性を維持するためです。

### 書き込み操作の制限対象

以下の操作は `fsxn-admins` グループのメンバーのみ実行可能です:

| ワークフロー | 制限される操作 |
|-------------|--------------|
| snapshot-restore | スナップショットからのリストア実行 |
| flexclone-management | FlexClone の作成 |
| volume-management | ボリュームのリサイズ・削除 |
| arp-dashboard | 保護スナップショットの作成 |

閲覧者は上記の操作を試みると「Insufficient permissions — Admin role required」エラーが表示されます。

### ユーザー管理

#### 初期管理者ユーザーの作成（デプロイ時）

`ADMIN_EMAIL` 環境変数を設定してデプロイすると、自動的に管理者ユーザーが作成されます:

```bash
export ADMIN_EMAIL="admin@example.com"
bash deploy.sh
```

- 一時パスワードがメールで送信されます
- 初回ログイン時にパスワード変更が求められます
- ユーザーは自動的に `fsxn-admins` グループに追加されます

#### 追加ユーザーの作成

```bash
# ユーザーの作成
aws cognito-idp admin-create-user \
  --user-pool-id <user-pool-id> \
  --username viewer@example.com \
  --user-attributes Name=email,Value=viewer@example.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL

# 閲覧者グループへの追加
aws cognito-idp admin-add-user-to-group \
  --user-pool-id <user-pool-id> \
  --username viewer@example.com \
  --group-name fsxn-viewers
```

#### ユーザーのロール変更

```bash
# 閲覧者から管理者への昇格
aws cognito-idp admin-remove-user-from-group \
  --user-pool-id <user-pool-id> \
  --username user@example.com \
  --group-name fsxn-viewers

aws cognito-idp admin-add-user-to-group \
  --user-pool-id <user-pool-id> \
  --username user@example.com \
  --group-name fsxn-admins
```

### RBAC の技術的な仕組み

1. ユーザーが Cognito Hosted UI で認証
2. ALB が OIDC トークンを検証し、`x-amzn-oidc-data` ヘッダーとして JWT を転送
3. ToolJet ワークフローが JWT をデコードし、`cognito:groups` クレームを抽出
4. 書き込み操作の前に `fsxn-admins` グループの存在を確認
5. グループに属していない場合、操作をブロックしエラーメッセージを表示

---

## ダッシュボード自動プロビジョニング

### 仕組み

CloudFormation Custom Resource を使用して、スタックデプロイ時に Grafana ダッシュボードを自動的にインポートします:

```
deploy.sh
  → ダッシュボード JSON を S3 バケットにアップロード
  → observability.yaml をデプロイ（Custom Resource を含む）
    → Custom Resource が Lambda を起動
      → Lambda が S3 からダッシュボード JSON を読み取り
      → Lambda が AMG API でデータソース（AMP）を設定
      → Lambda が AMG API で各ダッシュボードをインポート
      → Lambda がパネル埋め込み URL を Custom Resource 出力として返却
```

### 特徴

- **冪等性**: ダッシュボード UID による上書きインポート（再デプロイ時に安全に更新）
- **レート制限対応**: AMG API の 429 レスポンスに対するエクスポネンシャルバックオフ（最大 3 回リトライ）
- **スキップ可能**: `--skip-dashboard-import` フラグでインポートをスキップ
- **削除時**: ダッシュボードは AMG に残存（手動クリーンアップ用）

### AMG API キーの準備

ダッシュボードインポートには AMG の Admin ロール API キーが必要です:

1. AMG コンソールでワークスペースを開く
2. **Configuration** → **API keys** → **Add API key**
3. Role: **Admin**、Expiration: 任意（推奨: 30 日）
4. 生成されたキーを Secrets Manager に保存:

```bash
aws secretsmanager create-secret \
  --name fsxn-mgmt-grafana-api-key \
  --description "AMG API key for dashboard auto-provisioning" \
  --secret-string '{"api_key": "<your-amg-api-key>"}'
```

### ダッシュボードインポートのスキップ

AMG API キーが未設定の場合や、手動でダッシュボードを管理したい場合:

```bash
bash deploy.sh --skip-dashboard-import
```

このフラグを指定すると、`SkipDashboardImport=true` が Stack 3 に渡され、Custom Resource Lambda は起動されません。

### インポートされるダッシュボード

`harvest/dashboards/` ディレクトリ内のすべての JSON ファイルが対象です:

| ダッシュボード | ファイル | 内容 |
|-------------|--------|------|
| ARP Status | `arp-status.json` | ARP 状態分布、アラートタイムライン |
| Volume Overview | `volume-overview.json` | ボリュームメトリクス概要 |
| Performance | `performance.json` | IOPS、スループット、レイテンシ |

---

## トラブルシューティング

### カスタムドメイン関連

#### DNS の名前解決の失敗

**症状**: `dig console.example.com` が NXDOMAIN を返す

**原因**: Route 53 レコードが作成されていない、または DNS 伝播が完了していない

**解決策**:
```bash
# CloudFormation スタックでレコードが作成されたか確認
aws cloudformation describe-stack-resources \
  --stack-name fsxn-mgmt-console \
  --query "StackResources[?ResourceType=='AWS::Route53::RecordSet']"

# Route 53 でレコードを直接確認
aws route53 list-resource-record-sets \
  --hosted-zone-id Z0123456789ABCDEFGHIJ \
  --query "ResourceRecordSets[?Name=='console.example.com.']"
```

- DNS 伝播には最大数分かかる場合があります
- `CUSTOM_DOMAIN_NAME` と `HOSTED_ZONE_ID` が正しく設定されていることを確認

#### 証明書エラー（HTTPS）

**症状**: ブラウザで証明書エラーが表示される

**原因**: ACM 証明書のドメイン名が `CUSTOM_DOMAIN_NAME` と一致しない、または証明書が ISSUED 状態でない

**解決策**:
```bash
# 証明書の状態とドメイン名を確認
aws acm describe-certificate \
  --certificate-arn "<certificate-arn>" \
  --query 'Certificate.{Status:Status,DomainName:DomainName,SANs:SubjectAlternativeNames}'
```

- 証明書のドメイン名が `CUSTOM_DOMAIN_NAME` と完全一致（またはワイルドカードでカバー）していることを確認
- 証明書の Status が `ISSUED` であることを確認

#### デプロイ時のバリデーションエラー

**症状**: デプロイスクリプトが以下のエラーで終了:
```
❌ CUSTOM_DOMAIN_NAME is set but CERTIFICATE_ARN or HOSTED_ZONE_ID is missing
```

**原因**: カスタムドメインの 3 つの環境変数が揃っていない

**解決策**: `CUSTOM_DOMAIN_NAME`、`CERTIFICATE_ARN`、`HOSTED_ZONE_ID` の 3 つすべてを設定する

---

### RBAC 関連

#### 書き込み操作の「Insufficient permissions」によるブロック

**症状**: 管理者ユーザーなのに書き込み操作がブロックされる

**原因**: ユーザーが `fsxn-admins` グループに追加されていない

**解決策**:
```bash
# ユーザーのグループ所属を確認
aws cognito-idp admin-list-groups-for-user \
  --user-pool-id <user-pool-id> \
  --username <username>

# fsxn-admins グループに追加
aws cognito-idp admin-add-user-to-group \
  --user-pool-id <user-pool-id> \
  --username <username> \
  --group-name fsxn-admins
```

> グループ変更後、ユーザーは再ログインが必要です（OIDC トークンの更新のため）。

#### 全ユーザーへの管理者権限の付与

**症状**: 閲覧者グループのユーザーも書き込み操作が可能

**原因**: ToolJet ワークフローに RBAC チェックが正しく追加されていない

**解決策**:
- `tooljet-workflows/rbac-helper.json` が正しくインポートされていることを確認
- 各書き込みワークフロー（snapshot-restore, flexclone-management, volume-management, arp-dashboard）に RBAC チェックが含まれていることを確認
- ToolJet のワークフローを再インポートする

#### 初期管理者ユーザーへのメールの不着

**症状**: `ADMIN_EMAIL` を設定してデプロイしたが、一時パスワードのメールが届かない

**原因**: Cognito の SES 設定、またはメールアドレスの入力ミス

**解決策**:
```bash
# ユーザーが作成されているか確認
aws cognito-idp admin-get-user \
  --user-pool-id <user-pool-id> \
  --username <admin-email>

# ユーザーが存在する場合、パスワードをリセット
aws cognito-idp admin-set-user-password \
  --user-pool-id <user-pool-id> \
  --username <admin-email> \
  --password "<temporary-password>" \
  --permanent
```

---

### ダッシュボード自動プロビジョニング関連

#### ダッシュボードの未インポート

**症状**: デプロイ完了後、AMG にダッシュボードが表示されない

**原因**: AMG API キーが未設定、または Lambda がエラーを返している

**解決策**:
```bash
# Custom Resource Lambda のログを確認
aws logs tail /aws/lambda/fsxn-mgmt-dashboard-importer --since 30m

# Secrets Manager に API キーが存在するか確認
aws secretsmanager describe-secret \
  --secret-id fsxn-mgmt-grafana-api-key
```

- API キーが期限切れの場合は再発行して Secrets Manager を更新
- `--skip-dashboard-import` フラグが指定されていないことを確認

#### AMG API レート制限エラー

**症状**: Lambda ログに `429 Too Many Requests` が記録される

**原因**: AMG API のレート制限に達した（短時間に多数のダッシュボードをインポート）

**解決策**:
- Lambda は自動的にエクスポネンシャルバックオフでリトライします（最大 3 回）
- 3 回のリトライ後も失敗する場合は、時間をおいて再デプロイ
- ダッシュボード数が多い場合は、複数回に分けてデプロイを検討

#### S3 バケットへのダッシュボード JSON の未アップロード

**症状**: Lambda ログに「No dashboard files found in S3」と記録される

**原因**: デプロイスクリプトが S3 アップロードに失敗した

**解決策**:
```bash
# S3 バケットの内容を確認
aws s3 ls s3://fsxn-mgmt-dashboards-123456789012/

# 手動でアップロード
aws s3 sync harvest/dashboards/ s3://fsxn-mgmt-dashboards-123456789012/dashboards/
```

---

### 一般的な問題

#### Phase 2A の機能が動作しなくなった

**症状**: Phase 2B アップグレード後に既存のワークフローが動作しない

**原因**: 通常は発生しないが、パラメータの設定ミスの可能性

**解決策**:
- Phase 2B の変更は追加的であり、既存機能を変更しない
- ECS タスクが正常に稼働していることを確認:
  ```bash
  aws ecs describe-services \
    --cluster fsxn-mgmt-cluster \
    --services fsxn-mgmt-tooljet \
    --query 'services[0].{desired:desiredCount,running:runningCount}'
  ```
- 問題が解決しない場合は、Phase 2B の新しい環境変数をすべて unset して再デプロイ

---

## リファレンス

### デプロイスクリプトパラメータ（Phase 2B 追加分）

```bash
# カスタムドメイン（オプション — 3 つセットで指定）
export CUSTOM_DOMAIN_NAME="console.example.com"
export HOSTED_ZONE_ID="Z0123456789ABCDEFGHIJ"
export CERTIFICATE_ARN="arn:aws:acm:ap-northeast-1:123456789012:certificate/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# RBAC 初期管理者（オプション）
export ADMIN_EMAIL="admin@example.com"

# ダッシュボードインポートスキップ（オプション）
bash deploy.sh --skip-dashboard-import
```

### CloudFormation パラメータ（Phase 2B 追加分）

| パラメータ | テンプレート | 型 | デフォルト | 説明 |
|-----------|------------|---|---------|------|
| `CustomDomainName` | console.yaml | String | '' | カスタムドメイン名 |
| `HostedZoneId` | console.yaml | String | '' | Route 53 ホストゾーン ID |
| `SkipDashboardImport` | observability.yaml | String | 'false' | ダッシュボードインポートのスキップ |

### Cognito グループ

| グループ名 | Precedence | 説明 |
|-----------|-----------|------|
| `fsxn-admins` | 1 | 管理者（全操作可能） |
| `fsxn-viewers` | 10 | 閲覧者（読み取りのみ） |
