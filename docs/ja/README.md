# FSx for ONTAP Observability Integrations

[![CI](https://github.com/Yoshiki0705/fsxn-observability-integrations/actions/workflows/ci.yaml/badge.svg)](https://github.com/Yoshiki0705/fsxn-observability-integrations/actions/workflows/ci.yaml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Yoshiki0705/fsxn-observability-integrations/badge)](https://scorecard.dev/viewer/?uri=github.com/Yoshiki0705/fsxn-observability-integrations)

🌐 **日本語** | [English](../en/README.md)

> Amazon FSx for NetApp ONTAP の監査ログを 9 つの Observability ベンダーへ、さらに EMS イベントと FPolicy ファイル操作をそのうち 9 ベンダーへ、EC2 不要で配信するサーバーレスパターン集。FSx for ONTAP S3 Access Points 経由。AWS + ストレージ運用チーム向けコミュニティリファレンス実装。ベンダーごとの内訳は[テレメトリ経路のカバレッジ](#テレメトリ経路のカバレッジ)を参照。

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
監査ログは全ベンダーで対応済み、EMS と FPolicy は一部ベンダーで実装済みです。

| ベンダー | 監査ログ | EMS イベント | FPolicy ファイル操作 |
|----------|:--------:|:------------:|:--------------------:|
| [Datadog](../../integrations/datadog/) | ✅ | ✅ | ✅ |
| [Splunk (Serverless)](../../integrations/splunk-serverless/) | ✅ | ✅ | ✅ |
| [OTel Collector](../../integrations/otel-collector/) | ✅ | ✅ | ✅ |
| [Grafana Cloud](../../integrations/grafana/) | ✅ | ✅ | ✅ |
| [New Relic](../../integrations/new-relic/) | ✅ | ✅ | ✅ |
| [Elastic](../../integrations/elastic/) | ✅ | ✅ | ✅ |
| [Dynatrace](../../integrations/dynatrace/) | ✅ | ✅ | ✅ |
| [Sumo Logic](../../integrations/sumo-logic/) | ✅ | ✅ | ✅ |
| [Honeycomb](../../integrations/honeycomb/) | ✅ | ✅ | ✅ |
| [CrowdStrike Falcon LogScale](../../integrations/crowdstrike/) | ✅ | — | — |

`—` はその経路のハンドラが未実装であることを示します。`scripts/deploy.sh` は該当
スタックをスキップして理由を出力します。プレースホルダ Lambda をデプロイすると
イベントを受け取ったうえで全件破棄することになるためです。

CrowdStrike は `template-ems.yaml` / `template-fpolicy.yaml` 自体を持たないため、
スキップ対象もありません。現時点で EMS / FPolicy イベントを送る場合は、
[OTel Collector](../../integrations/otel-collector/) 統合を取り込み口として使い、
LogScale を OTLP エクスポータのバックエンドとして設定してください。

全経路対応の 9 ベンダーは共通の実装を共有しています。`shared/python/ems_event.py`
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
| 運用 | [パイプライン SLO](../en/pipeline-slo.md) · [運用ガイド](operational-guide.md) · [Runbooks](../en/runbooks/) |
| セキュリティ | [サイバーレジリエンスマップ](cyber-resilience-capability-map.md) · [自動インシデント対応](automated-response-guide.md) · [データ分類](../en/data-classification.md) |
| エンタープライズ | [マルチアカウント](../en/multi-account-deployment.md) · [クロスリージョン DR](../en/cross-region-replication.md) · [PII リダクション](../../integrations/otel-collector/docs/en/pii-redaction-cookbook.md) |
| 監視 | [CloudWatch Log Alarm](cloudwatch-log-alarm.md) · [EMS 検知機能](ems-detection-capabilities.md) · [検知ユースケース](detection-use-cases.md) |

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
npm install                  # 依存関係インストール
npm test                     # TypeScript テスト
python -m pytest integrations/*/tests/ shared/lambda-layers/ems-parser/tests/ -v  # 全 Python テスト
cfn-lint integrations/*/template.yaml   # CloudFormation バリデーション
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
