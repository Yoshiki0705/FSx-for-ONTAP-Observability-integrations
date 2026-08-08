# コンプライアンスエビデンスパックテンプレート

🌐 **日本語**（このページ） | [English](../en/compliance-evidence-pack.md)

## 概要

本テンプレートは、FSx for ONTAP Observability Pipeline のコンプライアンスエビデンスを整理するための構造を提供します。パイプラインの統制を規制フレームワークにマッピングし、監査準備のためのチェックリストを提供します。

> **ガバナンス上の注意**
>
> 本テンプレートはエビデンス収集の構造を提供するものです。コンプライアンス評価や認証を構成するものではありません。エビデンスが特定の規制要件を満たしているかの検証は、コンプライアンスチームまたは監査人に依頼してください。

## 適用フレームワーク

| フレームワーク | 適用範囲 | 本パイプラインの主要統制 |
|-------------|---------|----------------------|
| **ISMAP** | 日本政府クラウド | 監査ログ、アクセス制御、暗号化、監視 |
| **FISC** | 日本金融 | データ保持（7年）、アクセス証跡、変更管理 |
| **SOC 2** | サービス組織 | 論理アクセス、監視、インシデント対応 |
| **ISO 27001** | 情報セキュリティ | A.12.4 ログ記録、A.12.6 脆弱性管理 |
| **PCI DSS** | 決済カード | 要件 10: アクセス追跡、要件 10.7: 保持 |
| **APPI** | 日本個人情報 | 利用目的制限、越境移転、保持 |

## エビデンスカテゴリ

### 1. データフロードキュメント

**必要なエビデンス**：
- [ ] FSx for ONTAP からベンダーまでのデータフローを示すアーキテクチャ図
- [ ] ネットワークパスのドキュメント（VPC、エンドポイント、NAT、インターネット）
- [ ] 転送中データのフィールド分類（[データ分類ガイド](data-classification.md) を参照）
- [ ] 転送中暗号化の検証（全ベンダー API に TLS 1.2+）
- [ ] 保存時暗号化の検証（DLQ に KMS、DynamoDB に SSE）

**確認場所**：
- アーキテクチャ: `docs/en/architecture.md`
- データ分類: `docs/ja/data-classification.md`
- 暗号化: CloudFormation テンプレート `Properties.KmsMasterKeyId`

### 2. アクセス制御

**必要なエビデンス**：
- [ ] IAM ポリシードキュメント（Lambda 実行ロール）
- [ ] S3 Access Point リソースポリシー
- [ ] Secrets Manager アクセスポリシー
- [ ] 最小権限の原則の検証
- [ ] IAM ポリシーにワイルドカード（`*`）アクションがないこと
- [ ] ベンダープラットフォームの RBAC 設定

**確認場所**：
```bash
# Export IAM policies from deployed stack
aws cloudformation describe-stack-resources \
  --stack-name fsxn-<vendor>-integration \
  --query 'StackResources[?ResourceType==`AWS::IAM::Role`].PhysicalResourceId'

# Get role policy
aws iam get-role-policy \
  --role-name <role-name> \
  --policy-name <policy-name>
```

### 3. 監査証跡

**必要なエビデンス**：
- [ ] Lambda 実行に対して CloudTrail が有効
- [ ] CloudWatch Logs の保持期間が設定済み（30 日以上）
- [ ] DLQ メッセージ保持（14 日）
- [ ] Checkpoint 履歴（SSM Parameter Store バージョン履歴）
- [ ] オブジェクト台帳（DynamoDB、Level 3 の場合）
- [ ] ベンダー側の API アクセス監査ログ

**確認場所**：
```bash
# CloudWatch Log Group retention
aws logs describe-log-groups \
  --log-group-name-prefix /aws/lambda/fsxn- \
  --query 'logGroups[].{Name:logGroupName, Retention:retentionInDays}'

# SSM Parameter history
aws ssm get-parameter-history \
  --name "/fsxn/<vendor>/audit-checkpoint" \
  --query 'Parameters[-5:].[Version, LastModifiedDate, Value]'
```

### 4. 監視とアラート

**必要なエビデンス**：
- [ ] CloudWatch Alarms 設定済み（Lambda エラー、DLQ 深度）
- [ ] アラーム通知先（SNS トピック、メール）
- [ ] Pipeline SLO 定義（[Pipeline SLO](pipeline-slo.md) を参照）
- [ ] インシデント対応 Runbook（[Runbooks](runbooks/) を参照）
- [ ] アラーム履歴（過去 90 日）

**確認場所**：
```bash
# List alarms for the pipeline
aws cloudwatch describe-alarms \
  --alarm-name-prefix fsxn- \
  --query 'MetricAlarms[].{Name:AlarmName, State:StateValue, Actions:AlarmActions}'
```

### 5. データ保持

**必要なエビデンス**：
- [ ] 保持ポリシーの文書化（[保持ポリシーマトリクス](retention-policy-matrix.md) を参照）
- [ ] ベンダー保持設定のスクリーンショット/エクスポート
- [ ] S3 Lifecycle ルール（S3 にアーカイブする場合）
- [ ] DynamoDB TTL 設定（オブジェクト台帳を使用する場合）
- [ ] 保持期間が規制上の最小要件を満たすエビデンス

### 6. 変更管理

**必要なエビデンス**：
- [ ] Infrastructure as Code（Git 内の CloudFormation テンプレート）
- [ ] 全変更の Git コミット履歴
- [ ] CI/CD パイプライン設定（`.github/workflows/ci.yaml`）
- [ ] PR レビュープロセス（ブランチ保護ルール）
- [ ] デプロイ履歴（CloudFormation スタックイベント）

### 7. 脆弱性管理

**必要なエビデンス**：
- [ ] Lambda ランタイムバージョン（Python 3.12 — サポート対象）
- [ ] 依存関係スキャン結果（CI での Trivy）
- [ ] cfn-guard セキュリティルール結果
- [ ] Lambda Layer に既知の CVE がないこと
- [ ] シークレットローテーションスケジュール

### 8. データレジデンシー

**必要なエビデンス**：
- [ ] ベンダーデプロイリージョンの文書化
- [ ] 越境データ転送評価（該当する場合）
- [ ] データレジデンシーマトリクス（[データレジデンシー](data-residency.md) を参照）
- [ ] 承認済みリージョン外にデータが保存されていないこと

## フレームワーク別チェックリスト

### ISMAP チェックリスト

| 統制 | エビデンス | 状態 |
|------|----------|------|
| 8.1.1 監査ログ | CloudWatch Logs + ベンダープラットフォーム | [ ] |
| 8.1.2 ログ保護 | DLQ、CloudWatch に KMS 暗号化 | [ ] |
| 8.1.3 ログ保持 | 最低 1 年（有料ベンダープラン） | [ ] |
| 9.1.1 アクセス制御 | IAM 最小権限 + S3 AP ポリシー | [ ] |
| 9.4.1 転送中暗号化 | ベンダー API に TLS 1.2+ | [ ] |
| 9.4.2 保存時暗号化 | KMS (DLQ)、SSE (DynamoDB) | [ ] |
| 12.1.1 監視 | CloudWatch Alarms + SLO | [ ] |
| 12.1.2 インシデント対応 | Runbook + エスカレーションパス | [ ] |

### FISC チェックリスト

| 統制 | エビデンス | 状態 |
|------|----------|------|
| アクセス証跡 | 監査ログをベンダーに配信 | [ ] |
| 7 年保持 | S3 Glacier アーカイブ + ベンダー保持 | [ ] |
| 変更管理 | Git + CloudFormation + CI/CD | [ ] |
| 暗号化 | 転送中 TLS、保存時 KMS | [ ] |
| 監視 | アラーム + SLO + Runbook | [ ] |
| データレジデンシー | JP リージョンベンダーまたはセルフホスト | [ ] |

### SOC 2（Trust Services Criteria）

| 基準 | 統制 | エビデンス | 状態 |
|------|------|----------|------|
| CC6.1 | 論理アクセス | IAM ポリシー、S3 AP ポリシー | [ ] |
| CC6.2 | アクセスプロビジョニング | Secrets Manager、ハードコード認証情報なし | [ ] |
| CC7.2 | 監視 | CloudWatch Alarms、SLO | [ ] |
| CC7.3 | インシデント対応 | Runbook、DLQ リプレイ | [ ] |
| CC8.1 | 変更管理 | Git、CI/CD、CloudFormation | [ ] |
| A1.2 | 復旧 | DLQ、Checkpoint、リトライ | [ ] |

## エビデンス収集スクリプト

```bash
#!/bin/bash
# Collect compliance evidence for a specific vendor integration
VENDOR="${1:-datadog}"
STACK_NAME="fsxn-${VENDOR}-integration"
OUTPUT_DIR="evidence/${VENDOR}/$(date +%Y-%m-%d)"
mkdir -p "$OUTPUT_DIR"

echo "Collecting evidence for: $STACK_NAME"

# 1. Stack resources and policies
aws cloudformation describe-stack-resources \
  --stack-name "$STACK_NAME" > "$OUTPUT_DIR/stack-resources.json"

# 2. IAM policies
for role in $(aws cloudformation describe-stack-resources \
  --stack-name "$STACK_NAME" \
  --query 'StackResources[?ResourceType==`AWS::IAM::Role`].PhysicalResourceId' \
  --output text); do
  aws iam get-role --role-name "$role" > "$OUTPUT_DIR/iam-role-${role}.json"
done

# 3. CloudWatch Alarms
aws cloudwatch describe-alarms \
  --alarm-name-prefix "fsxn-${VENDOR}" > "$OUTPUT_DIR/alarms.json"

# 4. Log group retention
aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/fsxn-${VENDOR}" > "$OUTPUT_DIR/log-groups.json"

# 5. Lambda configuration
aws lambda get-function-configuration \
  --function-name "fsxn-${VENDOR}-integration-shipper" > "$OUTPUT_DIR/lambda-config.json" 2>/dev/null

echo "Evidence collected in: $OUTPUT_DIR"
```

## 監査準備タイムライン

| 監査までの期間 | アクション |
|-------------|----------|
| 8 週間前 | 適用フレームワークと統制を特定 |
| 6 週間前 | エビデンス収集スクリプトを実行 |
| 4 週間前 | チェックリストを記入、ギャップを特定 |
| 3 週間前 | ギャップを修正（不足アラーム、保持期間等） |
| 2 週間前 | エビデンス収集を再実行、完全性を確認 |
| 1 週間前 | エビデンスをパッケージ化、ウォークスルーを準備 |
| 監査当日 | アーキテクチャコンテキストとともにエビデンスを提示 |

## 関連ドキュメント

- [データ分類ガイド](data-classification.md)
- [保持ポリシーマトリクス](retention-policy-matrix.md)
- [Pipeline SLO](pipeline-slo.md)
- [セキュリティレビューチェックリスト](security-review-checklist.md)
- [ガバナンス & コンプライアンス](governance-and-compliance.md)
- [Runbooks](runbooks/)
