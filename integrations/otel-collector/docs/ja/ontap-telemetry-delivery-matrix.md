# ONTAP テレメトリ配信マトリクス

🌐 **日本語**（このページ） | [English](../en/ontap-telemetry-delivery-matrix.md)

## 配信パスの概要

ONTAP テレメトリは 2 つのパスでバックエンドに到達できる:

| パス | 説明 | メリット | デメリット |
|------|------|---------|-----------|
| **Direct Send** | Lambda → Backend API | シンプル、コンポーネント少 | 単一バックエンド、ファンアウト不可 |
| **OTel Collector** | Lambda → Collector → Backend(s) | マルチバックエンド、設定一元化 | 追加インフラが必要 |

## ソース × 配信パス × バックエンド マトリクス

### 監査ログ

| バックエンド | Direct Send | OTel Collector | 最適用途 |
|-------------|-------------|----------------|----------|
| **Datadog** | ✅ Lambda → Logs API | ✅ otlp_http/datadog | コンプライアンスダッシュボード、ログ分析 |
| **Splunk** | ✅ Lambda → HEC | ✅ splunk_hec exporter | SIEM 相関分析、調査 |
| **Grafana** | ✅ Lambda → Loki API | ✅ otlp_http/grafana | コスト効率の良い長期検索 |
| **Honeycomb** | ✅ Lambda → Events API | ✅ otlp_http/honeycomb | 高カーディナリティ探索 |
| **Elastic** | ✅ Lambda → Bulk API | ✅ elasticsearch exporter | 全文検索、コンプライアンス |

### EMS / ARP（セキュリティイベント）

| バックエンド | Direct Send | OTel Collector | 最適用途 |
|-------------|-------------|----------------|----------|
| **Datadog** | ✅ Lambda → Logs API | ✅ otlp_http/datadog | セキュリティ監視、SIEM |
| **Splunk** | ✅ Lambda → HEC | ✅ splunk_hec exporter | SOC ワークフロー、相関分析 |
| **Grafana** | ✅ Lambda → Loki API | ✅ otlp_http/grafana | アラートルーティング、オンコール |
| **Honeycomb** | ✅ Lambda → Events API | ✅ otlp_http/honeycomb | インシデント調査 |
| **Elastic** | ✅ Lambda → Bulk API | ✅ elasticsearch exporter | セキュリティ分析 |

### FPolicy（ファイルアクティビティ）

| バックエンド | Direct Send | OTel Collector | 最適用途 |
|-------------|-------------|----------------|----------|
| **Datadog** | ✅ Lambda → Logs API | ✅ otlp_http/datadog | リアルタイムファイル監視 |
| **Splunk** | ✅ Lambda → HEC | ✅ splunk_hec exporter | ランサムウェア調査 |
| **Grafana** | ✅ Lambda → Loki API | ✅ otlp_http/grafana | ファイルアクティビティダッシュボード |
| **Honeycomb** | ✅ Lambda → Events API | ✅ otlp_http/honeycomb | パターン分析 |
| **Elastic** | ✅ Lambda → Bulk API | ✅ elasticsearch exporter | ファイルアクセス分析 |

## service.name マッピング

各テレメトリソースはルーティングと識別のために固有の `service.name` を使用する:

| ソース | service.name | event.type 例 | 説明 |
|--------|-------------|-------------:|------|
| 監査ログ | `fsxn-audit` | `file.read`, `file.write`, `file.delete` | CIFS/NFS ファイルアクセス監査 |
| EMS / ARP | `fsxn-ems` | `ems.alert`, `arp.detected`, `arp.resolved` | システムイベント、ランサムウェア対策 |
| FPolicy | `fsxn-fpolicy` | `fpolicy.open`, `fpolicy.create`, `fpolicy.rename` | リアルタイムファイル操作 |

### service.name による Collector ルーティング

```yaml
# OTel Collector config: route by service.name
processors:
  routing:
    from_attribute: service.name
    table:
      - value: fsxn-audit
        exporters: [otlp_http/datadog, otlp_http/grafana, otlp_http/splunk]
      - value: fsxn-ems
        exporters: [otlp_http/datadog, otlp_http/grafana]
      - value: fsxn-fpolicy
        exporters: [otlp_http/datadog, otlp_http/honeycomb]
```

## バックエンド固有の考慮事項

### Datadog

| 観点 | 詳細 |
|------|------|
| フィールドインデックス | 自動: `service`, `status`, `host`。カスタム: `event.type`, `svm.name` にファセット追加 |
| 重要度処理 | OTLP severity → Datadog `status` (info/warn/error/critical) にマッピング |
| タイムスタンプウィンドウ | 過去 18 時間までのイベントを受付 |
| クエリ構文 | `service:fsxn-audit @event.type:file.delete` |
| 最大バッチ | 5 MB / 1000 アイテム/リクエスト |

### Splunk

| 観点 | 詳細 |
|------|------|
| フィールドインデックス | `props.conf` / `transforms.conf` で定義。`sourcetype=fsxn:audit` を使用 |
| 重要度処理 | HEC 経由で Splunk `severity` フィールドにマッピング |
| タイムスタンプウィンドウ | インデックスごとに設定可能; デフォルトは過去の任意のタイムスタンプを受付 |
| クエリ構文 | `index=fsxn sourcetype=fsxn:audit EventType=file.delete` |
| 最大バッチ | ハードリミットなし; イベントあたり < 1 MB を推奨 |

### Grafana (Loki via OTLP)

| 観点 | 詳細 |
|------|------|
| フィールドインデックス | ラベル: `service_name`, `event_type`。高カーディナリティフィールドは Structured metadata |
| 重要度処理 | OTLP severity → `detected_level` ラベルにマッピング |
| タイムスタンプウィンドウ | デフォルトで 1 時間以上前のイベントを拒否（`reject_old_samples_max_age`） |
| クエリ構文 | `{service_name="fsxn-audit"} \| json \| event_type="file.delete"` |
| 最大バッチ | プッシュあたり ~4 MB を推奨 |

### Honeycomb

| 観点 | 詳細 |
|------|------|
| フィールドインデックス | 全フィールド自動インデックス（schema-on-read） |
| 重要度処理 | `SeverityText` カラムにマッピング |
| タイムスタンプウィンドウ | 過去 7 日間までのイベントを受付 |
| クエリ構文 | カラムベース: `WHERE service.name = "fsxn-audit" AND event.type = "file.delete"` |
| 最大バッチ | リクエストあたり 5 MB |

### Elastic

| 観点 | 詳細 |
|------|------|
| フィールドインデックス | `event.type`, `svm.name` のマッピングを含むインデックステンプレートを定義 |
| 重要度処理 | ECS `log.level` フィールドにマッピング |
| タイムスタンプウィンドウ | 任意のタイムスタンプを受付; ILM でリテンション管理 |
| クエリ構文 | KQL: `service.name: "fsxn-audit" AND event.type: "file.delete"` |
| 最大バッチ | バルクリクエストあたり ~10 MB を推奨 |

## 検証済み組み合わせ

本プロジェクトでエンドツーエンドテスト済みの組み合わせ:

| ソース | 配信パス | バックエンド | ステータス | 備考 |
|--------|---------|------------|----------|------|
| 監査ログ | Direct Send | Datadog | ✅ 検証済み | リファレンス実装 |
| 監査ログ | Direct Send | Splunk | ✅ 検証済み | HEC エンドポイント |
| 監査ログ | Direct Send | Grafana | ✅ 検証済み | Loki push API |
| 監査ログ | OTel Collector | Datadog | ✅ 検証済み | otlp_http exporter |
| 監査ログ | OTel Collector | Grafana | ✅ 検証済み | otlp_http exporter |
| 監査ログ | OTel Collector | Honeycomb | ✅ 検証済み | otlp_http exporter |
| 監査ログ | OTel Collector | Multi (3) | ✅ 検証済み | Datadog + Grafana + Honeycomb |
| EMS / ARP | Direct Send | Datadog | ✅ 検証済み | Webhook → Lambda → Logs API |
| FPolicy | Direct Send | Datadog | ✅ 検証済み | SQS → Lambda → Logs API |
| FPolicy | OTel Collector | Datadog | ✅ 検証済み | SQS → Lambda → OTLP → Collector |
| EMS / ARP | OTel Collector | Grafana | 🚧 計画中 | — |
| FPolicy | OTel Collector | Splunk | 🚧 計画中 | — |
| 監査ログ | OTel Collector | Elastic | 🚧 計画中 | — |

## 判断ガイド: パスの選択

```
┌─────────────────────────────────────────────┐
│ How many backends receive this telemetry?    │
└──────────────────┬──────────────────────────┘
                   │
          ┌────────┴────────┐
          │                 │
     1 backend         2+ backends
          │                 │
          ▼                 ▼
   ┌─────────────┐  ┌──────────────┐
   │ Direct Send │  │OTel Collector│
   │ (simpler)   │  │ (fan-out)    │
   └─────────────┘  └──────────────┘
```

OTel Collector を選択する追加要因:
- 12 ヶ月以内にバックエンド移行を計画
- フィルタリング/リダクションの一元化が必要
- プラットフォームチームが Collector を共有サービスとして提供
- コンプライアンスで設定変更の監査証跡が必要
