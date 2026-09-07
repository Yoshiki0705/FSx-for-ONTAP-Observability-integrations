# OTel セマンティックマッピングガイド

🌐 **日本語**（このページ） | [English](../en/otel-semantic-mapping.md)

## 属性の分類

### 標準 / Well-Known 属性

| Attribute | Source | OTel Semantic Convention | Notes |
|-----------|--------|------------------------|-------|
| `service.name` | Config | [Service resource](https://opentelemetry.io/docs/specs/semconv/resource/#service) | Standard |
| `cloud.provider` | Config | [Cloud resource](https://opentelemetry.io/docs/specs/semconv/resource/cloud/) | Standard (`aws`) |
| `user.name` | FSx for ONTAP `UserName` | General convention | Widely understood |
| `client.address` | FSx for ONTAP `ClientIP` | [Client attributes](https://opentelemetry.io/docs/specs/semconv/attributes-registry/client/) | Standard |

### プロジェクト固有属性 (fsxn.* namespace)

| Attribute | Source | Why Not Standard | Notes |
|-----------|--------|-----------------|-------|
| `fsxn.operation` | FSx for ONTAP `Operation` | ONTAP-specific operation names | Could map to `event.action` in future |
| `fsxn.path` | FSx for ONTAP `ObjectName` | ONTAP volume path semantics differ from `file.path` | Volume-relative path |
| `fsxn.result` | FSx for ONTAP `Result` | ONTAP-specific result values | Drives severity mapping |
| `fsxn.svm` | FSx for ONTAP `SVMName` | No standard for storage VM | NetApp-specific concept |
| `event.type` | FSx for ONTAP `EventID` | Currently holds numeric ID (4663) | Consider `event.id` or `fsxn.event_id` in future |
| `cloud.platform` | Config | `aws_fsx` is not in standard enum | Project-specific marker |

## スキーマ進化の考慮事項

現在のマッピングは以下を優先しています：
1. **可読性** — 属性名が自己説明的であること
2. **バックエンド互換性** — Datadog、Grafana、Honeycomb で動作すること
3. **安定性** — 既存のクエリが壊れないこと

将来的に検討する変更：
- `event.type` → `event.id`（Windows Event ID の数値）
- `fsxn.operation` → `event.action` とのデュアルエミット
- `fsxn.path` → `file.path` とのデュアルエミット（セマンティクスが一致する場合）
- `cloud.platform` → 標準 enum 外のカスタムリソース属性

> **重要**: スキーマ変更には、すべての Lambda ハンドラー、テストデータ、バックエンドクエリ、ドキュメントの同時更新が必要です。バージョン管理されたマイグレーションアプローチを使用してください。

## OTLP が解決しないこと

OTLP はプロデューサーと Collector 間のワイヤーフォーマットを標準化します。以下は保証しません：
- バックエンド間で同一のフィールドインデックス
- バックエンド間で同一のクエリ構文
- 同等のリテンションポリシー
- 同一の重大度/ステータスの可視化
- 自動的なファセット/フィールド作成

各バックエンドは独自のデータモデルに従って OTLP 属性を解釈します。PoC 中にバックエンドごとの動作を検証してください（[PoC チェックリスト](poc-checklist.md)参照）。

## バックエンドではない OpenTelemetry

OpenTelemetry が定義するもの：
- テレメトリー生成のための API と SDK
- テレメトリー転送のための OTLP プロトコル
- テレメトリー処理とエクスポートのための Collector
- 属性命名のためのセマンティック規約

OpenTelemetry が提供しないもの：
- ストレージやインデックス
- 可視化やダッシュボード
- アラートやインシデント管理
- 長期保存

これらはバックエンド（Datadog、Grafana Cloud、Honeycomb、Splunk 等）の責務です。
