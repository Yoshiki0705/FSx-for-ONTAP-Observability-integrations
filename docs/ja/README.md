# FSx for ONTAP Observability Integrations

[![CI](https://github.com/Yoshiki0705/fsxn-observability-integrations/actions/workflows/ci.yaml/badge.svg)](https://github.com/Yoshiki0705/fsxn-observability-integrations/actions/workflows/ci.yaml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Yoshiki0705/fsxn-observability-integrations/badge)](https://scorecard.dev/viewer/?uri=github.com/Yoshiki0705/fsxn-observability-integrations)

🌐 **日本語** | [English](../en/README.md)

> Amazon FSx for NetApp ONTAP の監査ログを 9 つの Observability ベンダーへ、さらに EMS イベントと FPolicy ファイル操作をそのうち 9 ベンダーへ（うち 3 経路が E2E 検証済み）、EC2 不要で配信するサーバーレスパターン集。FSx for ONTAP S3 Access Points 経由。AWS + ストレージ運用チーム向けコミュニティリファレンス実装。ベンダーごとの内訳は[テレメトリ経路のカバレッジ](#テレメトリ経路のカバレッジ)を参照。

## はじめる

| やりたいこと | ガイド | 所要時間 |
|---|---|---|
| パイプラインを E2E で検証（初回） | [最小テストパス](quick-start-minimum.md) | 15 分 |
| ベンダー統合を本番デプロイ | [デプロイガイド](deployment-guide.md) | 30 分 |
| ランサムウェアにストレージ層で対応 | [自動インシデント対応](automated-response-guide.md) | 20 分 |
| 複数バックエンドにリダクション付きルーティング | [OTel Collector](../../integrations/otel-collector/) | 45 分 |
| ブラウザ GUI で FSx for ONTAP を管理 | [Management Console](../../management-console/) · [Decision Tree](decision-tree-management-monitoring.md) | 30 分 |
| パートナー PoC を成功基準付きで実施 | [PoC 成功基準](poc-success-criteria.md) · [Solution Brief](partner-solution-brief.md) | — |

> **ワンコマンドセットアップ**: `bash integrations/<vendor>/scripts/setup-full-observability.sh`

## アーキテクチャ

```
               ┌─────────────────────────────────────────────────┐
               │              FSx for ONTAP                      │
               │  audit volume ──► S3 Access Point (S3 API)      │
               └────────┬──────────────┬──────────────┬──────────┘
                        │              │              │
            監査ログ (poll)      EMS (webhook)   FPolicy (TCP)
                        │              │              │
                        ▼              ▼              ▼
              EventBridge       API Gateway      ECS Fargate
              Scheduler              │           → SQS
                   │                 │              │
                   ▼                 ▼              ▼
               Lambda ──────────► ベンダー API / OTel Collector
```

**トリガーモデル**: FSx for ONTAP S3 Access Points は S3 Event Notifications をサポートしていません。本プロジェクトでは EventBridge Scheduler ポーリング + SSM チェックポイントを使用。詳細は [アーキテクチャ](architecture.md) を参照。

<details><summary>📂 対応ベンダー一覧（14 統合）</summary>

| ベンダー | ステータス | 配信方式 |
|--------|--------|------|
| [Datadog](../../integrations/datadog/) | ✅ E2E 検証済み | Logs API v2 via Lambda |
| [New Relic](../../integrations/new-relic/) | ✅ E2E 検証済み | Log API v1 via Lambda |
| [Splunk (Serverless)](../../integrations/splunk-serverless/) | ✅ E2E 検証済み | HEC via Lambda |
| [OTel Collector](../../integrations/otel-collector/) | ✅ E2E 検証済み | ベンダーニュートラル OTLP/HTTP（マルチバックエンド） |
| [Grafana Cloud](../../integrations/grafana/) | ✅ E2E 検証済み | OTLP Gateway（Loki フォールバック） |
| [Elastic](../../integrations/elastic/) | ✅ E2E 検証済み | Bulk API |
| [Dynatrace](../../integrations/dynatrace/) | ✅ E2E 検証済み | Log Ingest API v2 |
| [Sumo Logic](../../integrations/sumo-logic/) | ✅ E2E 検証済み | HTTP Source |
| [Honeycomb](../../integrations/honeycomb/) | ✅ E2E 検証済み | Events Batch API |
| [CrowdStrike Falcon LogScale](../../integrations/crowdstrike/) | ✅ HEC 検証済み | Splunk HEC 互換 |
| [NetApp Console<!-- allow:naming -->](../../integrations/netapp-console/) | ✅ 検証済み | GUI 管理（SaaS） |
| [セルフホスト Management Console](../../management-console/) | ✅ 検証済み | AWS ネイティブ GUI（Cognito/IAM） |
| [自動インシデント対応](automated-response-guide.md) | ✅ E2E 検証済み | ストレージ層 block/snapshot |
| [Mackerel](../../integrations/mackerel/) | ✅ E2E 検証済み（オープンβ） | OTLP/HTTP ログ |

### テレメトリ経路のカバレッジ

FSx for ONTAP は 3 種類のテレメトリを出力し、それぞれ専用のハンドラが必要です。
監査ログは全ベンダーで対応済みかつ検証済みです。EMS と FPolicy のハンドラは 9 ベンダー
分ありますが、実際にベンダーアカウントへ届くところまで確認できているのはそのうち 3 経路
だけです。そのためこの表では「実装済み」と「検証済み」を別の記号で区別しています。

| ベンダー | 監査ログ | EMS イベント | FPolicy ファイル操作 |
|----------|:--------:|:------------:|:--------------------:|
| [Datadog](../../integrations/datadog/) | ✅ | ✅ | ✅ |
| [OTel Collector](../../integrations/otel-collector/) | ✅ | ✅ | ✅ |
| [Grafana Cloud](../../integrations/grafana/) | ✅ | ✅ | ✅ |
| [Splunk (Serverless)](../../integrations/splunk-serverless/) | ✅ | 🔧 | 🔧 |
| [New Relic](../../integrations/new-relic/) | ✅ | 🔧 | 🔧 |
| [Elastic](../../integrations/elastic/) | ✅ | 🔧 | 🔧 |
| [Dynatrace](../../integrations/dynatrace/) | ✅ | 🔧 | 🔧 |
| [Sumo Logic](../../integrations/sumo-logic/) | ✅ | 🔧 | 🔧 |
| [Honeycomb](../../integrations/honeycomb/) | ✅ | 🔧 | 🔧 |
| [CrowdStrike Falcon LogScale](../../integrations/crowdstrike/) | ✅ | — | — |

| 記号 | 意味 |
|:----:|------|
| ✅ | **E2E 検証済み**。テレメトリがベンダー UI に到達することを確認済みで、スクリーンショットまたは記入済みの検証記録（`verification-results-*.md`）があります。 |
| 🔧 | **実装済み・E2E 未検証**。ハンドラと CloudFormation スタックは提供済みでユニットテストも通りますが、その経路について実ベンダーアカウントに対する実行記録がありません。壊れているという意味ではなく、未テストという意味です。 |
| — | **未実装**。その経路のハンドラが存在しません。 |

EMS / FPolicy 列の ✅ の根拠: Datadog（実 ONTAP ファイルシステムに対する検証記録、ステップ
E1〜E4）、Grafana Cloud（スクリーンショット証跡。[検証記録](verification-results-grafana.md)に索引化）、
OTel Collector（検証記録。ただし EMS のステップはローカル Collector に対するサンプル OTLP
ペイロードで実施しており、実 ONTAP の Webhook ではありません）。

🔧 の経路については、各ベンダーの記録が沈黙せず明示しています。例として
[Splunk の記録](verification-results-splunk.md)は、検証済みの監査ログ経路と未記録の
EMS / FPolicy / Firehose 経路を分けて記載しています。

`—` の経路については `scripts/deploy.sh` が該当スタックをスキップして理由を出力します。
プレースホルダ Lambda をデプロイするとイベントを受け取ったうえで全件破棄することになる
ためです。

CrowdStrike は `template-ems.yaml` / `template-fpolicy.yaml` 自体を持たないため、
スキップ対象もありません。現時点で EMS / FPolicy イベントを送る場合は、
[OTel Collector](../../integrations/otel-collector/) 統合を取り込み口として使い、
LogScale を OTLP エクスポータのバックエンドとして設定してください。

EMS / FPolicy ハンドラを提供する 9 ベンダーは共通の実装を共有しています。`shared/python/ems_event.py`
が API Gateway からの抽出とパーサ委譲、`shared/python/fpolicy_event.py` が SQS の
バッチ管理と `batchItemFailures`、`shared/python/vendor_shipper.py` がリトライ方針・
資格情報キャッシュ・バッチ分割を担当し、各ベンダーはペイロード形式とエンドポイント
だけを提供します。

</details>

<details><summary>⚠️ 制約・注意事項</summary>

| 制約 | 影響 | 回避策 |
|---|---|---|
| S3 AP は Event Notifications 非対応 | プッシュトリガー不可 | EventBridge Scheduler ポーリング |
| S3 AP は Presigned URL 非対応 | 直接リンク共有不可 | 標準 S3 バケットへコピー |
| AD 参加 SVM は S3 AP データ操作に AD DC 到達性が必要 | AD 停止時 `AccessDenied` | 事前 AD 接続性チェック |
| VPC Lambda + Gateway Endpoint は Internet-origin AP でタイムアウトの可能性 | デプロイが無言で失敗 | VPC 外 Lambda または NAT 使用 |
| S3 AP の PutObject 上限 5 GB | 大容量書き込み不可 | 5 GB 以内のマルチパート |

詳細: [S3 AP 仕様](s3ap-fsxn-specification.md) · [デプロイガイド — VPC Endpoint マトリクス](deployment-guide.md)

</details>

<details><summary>📚 ドキュメント・関連リソース</summary>

### ドキュメント

| カテゴリ | 主要ドキュメント |
|----------|--------------|
| はじめに | [前提条件](prerequisites.md) · [ベンダー統合のデプロイ](vendor-deployment-common.md) · [デプロイガイド](deployment-guide.md) · [ONTAP 監査設定](ontap-audit-setup.md) |
| アーキテクチャ | [アーキテクチャ](architecture.md) · [イベントソース](event-sources.md) · [S3 AP 仕様](s3ap-fsxn-specification.md) |
| 運用 | [パイプライン SLO](pipeline-slo.md) · [運用ガイド](operational-guide.md) · [Runbook](runbooks/) |
| セキュリティ | [サイバーレジリエンスマップ](cyber-resilience-capability-map.md) · [自動インシデント対応](automated-response-guide.md) · [データ分類](data-classification.md) |
| エンタープライズ | [マルチアカウント](multi-account-deployment.md) · [クロスリージョン DR](cross-region-replication.md) · [PII リダクション](../../integrations/otel-collector/docs/en/pii-redaction-cookbook.md) |
| 監視 | [CloudWatch Log Alarm](cloudwatch-log-alarm.md) · [EMS 検知機能](ems-detection-capabilities.md) · [検知ユースケース](detection-use-cases.md) |

<!-- docs-index:start -->

### ドキュメント一覧

このディレクトリの全ドキュメントをカテゴリ別に掲載しています。`shared/scripts/generate-docs-index.py` が単一のカテゴリ表から生成するため、
日本語版と英語版は常に同じ集合を列挙します。

**はじめに**

- [はじめに](getting-started.md)
- [前提条件とリソースデプロイガイド](prerequisites.md)
- [最小テストパス](quick-start-minimum.md)
- [ベンダー統合のデプロイ](vendor-deployment-common.md)
- [デプロイメントガイド — 既存 FSx for ONTAP 環境への統合](deployment-guide.md)
- [ONTAP 監査設定ガイド](ontap-audit-setup.md)

**アーキテクチャ・リファレンス**

- [アーキテクチャ](architecture.md)
- [アーキテクチャ進化: CloudWatch Logs Syslog VPCE による管理監査ログ配信](architecture-evolution-syslog-vpce.md)
- [イベントソースガイド](event-sources.md)
- [正規化イベントスキーマ](normalized-event-schema.md)
- [FSx for ONTAP S3 Access Points 仕様書](s3ap-fsxn-specification.md)
- [S3 Access Points for FSx for ONTAP — 知見集](s3-access-points-knowledge.md)
- [ONTAP REST API クイックリファレンス (FSx for ONTAP)](ontap-rest-api-reference.md)

**運用**

- [運用ガイド](operational-guide.md)
- [Pipeline SLO 定義](pipeline-slo.md)
- [配信保証パターン](delivery-guarantees.md)
- [保持ポリシーマトリクス](retention-policy-matrix.md)
- [PagerDuty エスカレーション連携ガイド](pagerduty-escalation-guide.md)
- [Syslog VPC Endpoint セットアップガイド — FSx for ONTAP 管理監査ログ → CloudWatch Logs](syslog-vpce-setup-guide.md)
- [CloudWatch Log Alarm — FSx for ONTAP 監査ログからのダイレクトアラーム](cloudwatch-log-alarm.md)

**Runbook**

- [Runbook: DLQ リプレイ](runbooks/dlq-replay.md)
- [Runbook: Lambda エラーアラーム](runbooks/lambda-errors.md)
- [Runbook: Checkpoint 滞留](runbooks/checkpoint-stale.md)
- [Runbook: CloudWatch Log Alarm 発火時の対応手順](runbooks/log-alarm-triggered.md)

**セキュリティ・検知**

- [FSx for ONTAP Observability Integrations セキュリティベストプラクティス](security-best-practices.md)
- [セキュリティレビューチェックリスト](security-review-checklist.md)
- [セキュリティ監視 & インシデント対応 — ドキュメントナビゲーション](security-monitoring-index.md)
- [検知ユースケース](detection-use-cases.md)
- [EMS イベント検知機能 — リファレンスガイド](ems-detection-capabilities.md)
- [サイバーレジリエンス機能マップ — NIST CSF 2.0 機能マッピング](cyber-resilience-capability-map.md)
- [EMS Webhook セキュリティガイド](webhook-security.md)

**自動インシデント対応**

- [自動インシデント対応ガイド — ONTAP REST API によるユーザー/IP ブロック](automated-response-guide.md)
- [自動応答 — セキュリティ & インシデント対応補遺](automated-response-security-addendum.md)
- [ARP（Autonomous Ransomware Protection）インシデント対応ガイド](arp-incident-response-guide.md)
- [検証済みクリーン復旧ポイントガイド — CSF 2.0 RC.RP のギャップを埋める](verified-recovery-point-guide.md)
- [コンテンツレベル PII 分類スキャナー — CSF 2.0 Identify のギャップを埋める](content-classification-scanner.md)

**FPolicy**

- [FPolicy パイプライン — クイックデプロイガイド](fpolicy-quick-deploy.md)
- [FPolicy パイプライン運用ガイド](fpolicy-operational-guide.md)
- [FPolicy 本番アーキテクチャパターン](fpolicy-production-architecture-patterns.md)
- [FPolicy PoC チェックリスト](fpolicy-poc-checklist.md)
- [FPolicy 運用ノート](operational-notes-fpolicy.md)
- [AI エージェントアクセスログ × ONTAP FPolicy 監査ログ 統合パターン](agent-fpolicy-correlation-pattern.md)

**ガバナンス・コンプライアンス**

- [ガバナンスとコンプライアンスに関する考慮事項](governance-and-compliance.md)
- [コンプライアンスエビデンスパックテンプレート](compliance-evidence-pack.md)
- [FSx for ONTAP 監査ログのデータ分類ガイド](data-classification.md)
- [データレジデンシーマトリクス](data-residency.md)

**エンタープライズ・スケール**

- [AWS Organizations を使用したマルチアカウントデプロイ](multi-account-deployment.md)
- [監査ログ DR のためのクロスリージョンレプリケーション](cross-region-replication.md)
- [FSx for ONTAP 監査ログの Lakehouse 長期保管パターン](lakehouse-long-term-retention.md)
- [レイクハウス監視パターン](lakehouse-monitoring-patterns.md)

**アプローチの選択**

- [FSx for ONTAP 管理・監視 Decision Tree](decision-tree-management-monitoring.md)
- [AWS ネイティブ代替マトリクス — System Manager / Workload Factory / DII](native-alternative-matrix.md)
- [ベンダー比較](vendor-comparison.md)
- [EC2 ベースパターンとサーバーレスパターンの比較](ec2-comparison.md)
- [既存監査ツールとの共存ガイド](existing-audit-tool-coexistence.md)
- [ファイルアクセス監査ログ — フォーマット比較 & アーキテクチャ選択肢](file-access-audit-format-comparison.md)
- [ONTAP System Manager GUI 操作ガイド](system-manager-gui-guide.md)
- [Observability 統合補遺 — 高度なパターン & リファレンス](observability-integration-addendum.md)

**コスト**

- [コストモデル — Direct Send vs Collector vs Firehose](cost-model.md)
- [コスト検証: 見積もり vs 実績](cost-validation.md)
- [S3 Access Point 読み取りスループットベンチマーク](s3ap-throughput-benchmark.md)

**パートナー・ワークショップ**

- [パートナーソリューション概要: FSx for ONTAP サーバーレス Observability](partner-solution-brief.md)
- [パートナー FAQ: FSx for ONTAP Observability Integrations](partner-faq.md)
- [PoC 成功基準](poc-success-criteria.md)
- [PoC 提案テンプレート: FSx for ONTAP Observability 統合](poc-proposal-template.md)
- [ワークショップアジェンダ: FSx for ONTAP サーバーレス Observability](workshop-agenda.md)
- [Workshop Hands-On Guide（半日、3.5 時間）](workshop-hands-on-half-day.md)

**デモ・スクリーンショット**

- [デモシナリオ集](demo-scenarios.md)
- [自動応答デモ手順書](demo-automated-response.md)
- [ARP インシデント対応 デモ手順書](demo-arp-incident-response.md)
- [コンテンツ分類スキャナー デモ手順書](demo-content-classification.md)
- [EMS/FPolicy スクリーンショット撮影ガイド](screenshot-capture-guide-ems-fpolicy.md)

**検証結果**

- [Datadog 統合 動作確認結果](verification-results-datadog.md)
- [Splunk Serverless 統合 動作確認結果](verification-results-splunk.md)
- [OTel Collector 統合 E2E 検証結果](verification-results-otel-collector.md)
- [Grafana Cloud 統合 動作確認結果](verification-results-grafana.md)
- [New Relic 統合 動作確認結果](verification-results-new-relic.md)
- [Elastic 統合 動作確認結果](verification-results-elastic.md)
- [Dynatrace 統合 動作確認結果](verification-results-dynatrace.md)
- [Sumo Logic 統合 動作確認結果](verification-results-sumo-logic.md)
- [Honeycomb 統合 動作確認結果](verification-results-honeycomb.md)
- [EMS/FPolicy E2E 動作確認結果](verification-results-ems-fpolicy.md)

**プロジェクト**

- [CI ポリシーと品質ゲート](ci-policy.md)

<!-- docs-index:end -->
### 関連リポジトリ

| リポジトリ | 説明 |
|-----------|------|
| [FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns) | FPolicy パイプライン含む 17 業界ユースケース |
| [fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations) | S3 AP 経由の Data Lake / Lakehouse 統合 |
| [FSx-for-ONTAP-Agentic-Access-Aware-RAG](https://github.com/Yoshiki0705/FSx-for-ONTAP-Agentic-Access-Aware-RAG) | Bedrock によるアクセス制御対応 Agentic RAG |

### 記事

- [AWS Blog: FSx for ONTAP + Splunk 監査](https://aws.amazon.com/jp/blogs/news/auditing-user-and-administrative-actions-on-amazon-fsx-for-netapp-ontap-using-splunk/)（EC2 アプローチ — 本プロジェクトは EC2 不要の代替）

</details>

<details><summary>🔧 開発者向け</summary>

```bash
npm install                  # Install dependencies
npm test                     # TypeScript tests
python -m pytest integrations/*/tests/ shared/lambda-layers/ems-parser/tests/ -v  # All Python tests
cfn-lint integrations/*/template.yaml   # Validate CloudFormation
```

- **技術スタック**: CloudFormation (YAML) · Python 3.12 Lambda · TypeScript · GitHub Actions CI
- **コントリビュート**: [CONTRIBUTING.md](../../CONTRIBUTING.md) 参照
- **変更履歴**: [CHANGELOG.md](../../CHANGELOG.md) 参照
- **ロードマップ**: [ROADMAP.md](../../ROADMAP.md) 参照

</details>

## License

MIT

---

🌐 **日本語** | [English](../en/README.md)
