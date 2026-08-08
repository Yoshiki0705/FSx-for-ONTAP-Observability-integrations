# FSx for ONTAP Observability Integrations セキュリティベストプラクティス

🌐 **日本語**（このページ） | [English](../en/security-best-practices.md)

## 概要

本ドキュメントは、全ベンダー統合に共通するセキュリティ上の考慮事項をまとめたものです。どの Observability バックエンドを選択する場合でも、これらのプラクティスを適用してください。

## シークレット管理

### 推奨事項

- すべての API キー、トークン、認証情報を **AWS Secrets Manager** に保存する
- IAM ポリシーで、どの Lambda 関数がどのシークレットにアクセスできるかを制限する
- 定期的なスケジュールでシークレットをローテーションする（四半期ごとを推奨）
- 統合ごとに個別のシークレットを使用する（複数ベンダーで単一シークレットを共有しない）

### 禁止事項

- Lambda 環境変数にシークレットを保存しない（コンソールで閲覧可能）
- CloudFormation テンプレートやソースコードにシークレットをハードコードしない
- CloudWatch Logs にシークレット値を（部分的にも）ログ出力しない
- クロスアカウント IAM ポリシーなしで AWS アカウント間でシークレットを共有しない

### ベンダー別の注意事項

| ベンダー | シークレット形式 | ローテーション方法 |
|--------|--------------|-----------------|
| Datadog | `{"api_key":"..."}` | Datadog コンソールで再生成 |
| New Relic | `{"license_key":"..."}` | NR API Keys ページで再生成 |
| Grafana Cloud | `{"instance_id":"...","api_key":"..."}` | 新トークン作成後、旧トークン削除 |
| Splunk | `{"hec_token":"..."}` | Splunk で新 HEC トークンを作成 |
| Elastic | `{"api_key":"base64_id:key"}` | 無効化 + 新 API キー作成 |
| Dynatrace | `{"api_token":"dt0c01.XXX.YYY"}` | 取り消し + 新トークン作成 |
| Sumo Logic | `{"url":"https://..."}` | 新 HTTP Source を作成（新 URL） |
| Honeycomb | `{"api_key":"hcaik_..."}` | Ingest キーを再生成 |

## ネットワークセキュリティ

### Lambda 配置の判断

| シナリオ | 推奨 | 理由 |
|----------|---------------|-----|
| S3 AP 読み取りのみ | Lambda を VPC 外に配置 | 最もシンプル、NAT コスト不要 |
| S3 AP + ONTAP REST | Lambda を VPC 内 + NAT | 両方のパスでインターネットが必要 |
| プライベートネットワーク内のベンダー | Lambda を VPC 内 + VPC ピアリング | 直接接続 |

### TLS 要件

- すべてのベンダー API コールは HTTPS（TLS 1.2+）を使用
- 本番環境では `VerifySSL=false` を設定しない
- 自己署名証明書（開発環境のみ）の場合、カスタム CA バンドルを使用

### ファイアウォール / セキュリティグループルール

Lambda が VPC 内にある場合:
- アウトバウンド: ベンダーエンドポイントへの HTTPS（443）を許可
- アウトバウンド: Secrets Manager エンドポイントへの HTTPS（443）を許可
- Lambda にインバウンドルールは不要

## IAM 最小権限

### Lambda 実行ロールパターン

```yaml
# Minimum permissions for audit log shipper
Policies:
  - PolicyName: S3Read
    PolicyDocument:
      Statement:
        - Effect: Allow
          Action: s3:GetObject
          Resource: !Sub '${S3AccessPointArn}/object/*'  # Scoped to AP
  - PolicyName: Secrets
    PolicyDocument:
      Statement:
        - Effect: Allow
          Action: secretsmanager:GetSecretValue
          Resource: !Ref ApiKeySecretArn  # Single secret only
  - PolicyName: DLQ
    PolicyDocument:
      Statement:
        - Effect: Allow
          Action: sqs:SendMessage
          Resource: !GetAtt DeadLetterQueue.Arn  # Specific queue only
```

### アンチパターン

- 任意のアクションに `Resource: "*"` を使用
- `secretsmanager:GetSecretValue` の代わりに `secretsmanager:*` を使用
- `s3:GetObject` の代わりに `s3:*` を使用
- 複数の Lambda 関数間で実行ロールを共有

## Dead Letter Queue のセキュリティ

- DLQ メッセージには監査ログデータ（ファイルパス、ユーザー名、IP）が含まれる可能性がある
- すべての DLQ キューで KMS 暗号化を有効化（`KmsMasterKeyId: alias/aws/sqs`）
- メッセージ保持期間を 14 日に設定（リプレイに十分、無期限にはしない）
- DLQ へのアクセスを Lambda ロールと運用チームのみに制限

## Webhook セキュリティ（EMS パス）

EMS Webhook パスは API Gateway エンドポイントを公開します。以下で保護してください:

1. **API Key**: すべてのリクエストに `x-api-key` ヘッダーを要求
2. **WAF**: レート制限付きの AWS WAF をアタッチ（100 req/min 推奨）
3. **IP 許可リスト**: FSx for ONTAP 管理 IP 範囲に制限
4. **リクエスト検証**: 処理前に EMS イベントスキーマを検証

詳細な設定は [Webhook セキュリティガイド](webhook-security.md) を参照してください。

## 監査ログデータの分類

FSx for ONTAP 監査ログには以下が含まれる可能性があります:

| データタイプ | 例 | 機密度 |
|-----------|---------|-------------|
| ユーザー名 | `admin@corp.local` | PII（管轄地域に依存） |
| ファイルパス | `/vol/hr/salary-2026.xlsx` | 業務機密 |
| クライアント IP | `10.0.x.x` | 内部ネットワークトポロジ |
| SVM 名 | `svm-prod-finance` | インフラストラクチャメタデータ |

### 推奨事項

- 外部ベンダーに送信する場合、OTel Collector で PII 秘匿化を適用
- `transform` プロセッサでユーザー名やファイルパスをマスク
- ベンダーリージョン選択時にデータレジデンシー要件を考慮
- どのデータフィールドがどのベンダーに送信されるかを文書化

## コンプライアンスに関する考慮事項

> 本セクションは技術的ガイダンスのみを提供します。法的、コンプライアンス、または規制上のアドバイスを構成するものではありません。正式なガイダンスはコンプライアンスチームにご相談ください。

| フレームワーク | 関連する統制 | 本プロジェクトが支援する範囲 |
|-----------|------------------|------------------------|
| SOC 2 | CC6.1（論理アクセス） | ファイルアクセス監査証跡 |
| HIPAA | 164.312(b)（監査統制） | PHI ボリュームのアクセスログ |
| PCI DSS | 10.2（監査証跡） | カード会員データアクセスの監視 |
| GDPR | Art. 30（処理の記録） | データアクセスの文書化 |

### 本プロジェクトが提供しないもの

- コンプライアンス認証またはアテステーション
- 規制要件の法的解釈
- 監査カバレッジの完全性の保証
- 改ざん防止ログストレージ（S3 Object Lock を使用してください）

## デプロイ前セキュリティチェックリスト

- [ ] シークレットは Secrets Manager に保存（環境変数ではない）
- [ ] IAM ロールは最小権限に従っている
- [ ] DLQ は KMS で暗号化されている
- [ ] TLS 検証が有効（`VerifySSL=false` なし）
- [ ] Webhook エンドポイントが保護されている（API Key + WAF）
- [ ] ソースコードやテンプレートに実際の認証情報がない
- [ ] 選択したベンダーに対するデータ分類がレビュー済み
- [ ] シークレットローテーションスケジュールが文書化されている
- [ ] DLQ リプレイ手順がセキュリティチームに承認されている
- [ ] 越境データ移転がレビュー済み（該当する場合）
