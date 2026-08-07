# Datadog 統合 — 本番デプロイチェックリスト

FSx for ONTAP Datadog 統合を PoC から本番に昇格する前に使用するチェックリストです。

## デプロイ前

- [ ] FSx 監査設定確認済み（XML フォーマット、ローテーションスケジュール、ボリューム配置）
- [ ] S3 Access Point 作成済み（Lambda ロールへの読み取り専用アクセス）
- [ ] S3 Access Point リソースポリシーが Lambda 実行ロールを許可
- [ ] Datadog API Key を Secrets Manager に保存（`logs_write` スコープのみ）
- [ ] Datadog APP Key を Secrets Manager に保存（管理者スコープ、CI/CD 専用）
- [ ] Datadog サイトリージョン確認済み（AP1/US1/EU1/US3/US5）
- [ ] ネットワークパス検証済み（VPC 外 Lambda または NAT Gateway 経由の S3 AP アクセス）
- [ ] データ分類承認済み：監査ログの外部送信が組織ポリシーで承認済み

## セキュリティとガバナンス

- [ ] IAM Permissions Boundary を Lambda ロールに適用
- [ ] API Key スコープを `logs_write` のみに制限（管理者アクセスなし）
- [ ] APP Key を Terraform/CI パイプラインのみに限定（Lambda ランタイムでは不使用）
- [ ] S3 Archive バケットを SSE-KMS（顧客管理鍵）で暗号化
- [ ] Datadog Log Management CMK 設定済み（規制環境の場合）
- [ ] Sensitive Data Scanner ルール有効（社員ID、電話番号、メール、CC、マイナンバー）
- [ ] OTel エッジ側 PII リダクション設定済み（境界ガバナンスが必要な場合）
- [ ] シークレットローテーション手順テスト済み（API Key + APP Key）

## オブザーバビリティとアラート

- [ ] Log Pipeline 検証済み（6 プロセッサ: Category, Status, Date, Attribute×2, GeoIP）
- [ ] Security Monitors 有効（4 閾値 + 1 アノマリー）
- [ ] Cloud SIEM Detection Rules 有効（MITRE マッピング付き 4 ルール）
- [ ] Saved Views 作成済み（5 調査パターン）
- [ ] Facets 設定済み（8 カスタム Facets）
- [ ] Dashboard 検証済み（10 ウィジェット、データフロー確認）
- [ ] Log-based Metrics 作成済み（適切なカーディナリティで 4 メトリクス）
- [ ] CloudWatch Alarms: DLQ depth > 0, Lambda errors > 1%, HEC 401/403

## レスポンス自動化

- [ ] Workflow 作成済み（`fsxn-security-alert-response`）
- [ ] Monitors が `@workflow-` メンションで Workflow にリンク済み
- [ ] Case Management プロジェクト作成済み（FSXN）
- [ ] SOC トリアージランブック利用可能（Notebook）
- [ ] Snapshot 修復 Lambda デプロイ済み（15 分クールダウン付き）
      — `bash scripts/deploy-snapshot-remediation.sh`
      （スタック: `template-snapshot-remediation.yaml`、手順:
      [snapshot-remediation-setup.md](snapshot-remediation-setup.md)）
- [ ] Snapshot Lambda TLS 設定済み — `CA_CERT_PATH` と `CA_CERT_LAYER_ARN` を設定。
      未設定の場合は `CERT_NONE` で接続するため、PoC 用途に限られる
- [ ] Snapshot Lambda の VPC 設定を検証済み — ONTAP 管理 LIF の TCP 443 に到達できること。
      デプロイ成功ではなくテスト invoke で確認する
- [ ] 修復スタックに `AlarmNotificationTopicArn` を設定済み — 封じ込めアクションの
      サイレント失敗は、通知過多よりも危険

## 運用

- [ ] サービスアカウント特定・モニター除外済み（`svc-*`）
- [ ] 検知ルールを初期 2 週間 Warning モードで運用
- [ ] Critical シグナルのオンコール/エスカレーションパス定義済み
- [ ] DLQ リプレイ手順文書化済み
- [ ] リテンション要件定義済み（Datadog インデックス + S3 アーカイブライフサイクル）
- [ ] 月額コスト見積もり検証済み
- [ ] 復旧手順テスト済み（4 時間障害シミュレーション）

## Infrastructure as Code

- [ ] CloudFormation テンプレート検証済み（cfn-lint + cfn-guard）
- [ ] setup-full-observability.sh をクリーン環境でテスト済み
- [ ] Terraform 移行計画策定済み（マルチ組織/エンタープライズ向け）
- [ ] 全設定を Git でバージョン管理

## コンプライアンス（規制環境）

- [ ] S3 Object Lock（COMPLIANCE モード）を監査アーカイブに適用
- [ ] Datadog で Log Archive 設定済み（source:fsxn → S3 → Glacier）
- [ ] リテンション期間を規制要件に合わせて設定
- [ ] リハイドレーション手順文書化・テスト済み
- [ ] 修復アクションの監査証跡検証済み（CloudTrail + ONTAP + Case）
- [ ] ハッシュソルトを Secrets Manager で管理（FIELD_MAPPING マスキング使用時）

---

## 関連ドキュメント

- [セットアップガイド](setup-guide.md)
- [SPL vs CQL 対比表](spl-cql-comparison.md)
- [フィールドマッピング](field-mapping.md)
- [README（メイン）](../../README.md)
- [パイプライン SLO](../../../../docs/ja/pipeline-slo.md)
- [DLQ リプレイランブック](../../../../docs/ja/runbooks/dlq-replay.md)
- [既存監査ツール共存ガイド](../../../../docs/ja/existing-audit-tool-coexistence.md)
