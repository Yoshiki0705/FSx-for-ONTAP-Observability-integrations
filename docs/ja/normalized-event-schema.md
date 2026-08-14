# 正規化イベントスキーマ

🌐 **日本語**（このページ） | [English](../en/normalized-event-schema.md)

## 概要

全ベンダー統合は、ONTAP 監査イベントをベンダー固有フォーマットにマッピングする前に、共通の内部スキーマに正規化します。これにより、全 Observability プラットフォームで一貫したフィールド命名が保証されます。

## 内部正規化スキーマ

```json
{
  "event_type": "file_access",
  "source": "fsxn",
  "timestamp": "2026-05-17T01:30:00.000Z",
  "svm": "svm-prod-01",
  "user": "admin@corp.local",
  "client_ip": "198.51.100.50",
  "operation": "ReadData",
  "path": "/vol/data/reports/quarterly.xlsx",
  "result": "Success",
  "raw": {}
}
```

## フィールド定義

| フィールド | 型 | 説明 | ソース |
|-------|------|-------------|--------|
| `event_type` | string | イベントカテゴリ (`file_access`, `ems_alert`, `fpolicy_op`) | 導出 |
| `source` | string | イベントソース識別子 (`fsxn`, `fsxn-ems`, `fsxn-fpolicy`) | 設定値 |
| `timestamp` | ISO 8601 | ONTAP からのイベントタイムスタンプ | EVTX record / XML TimeCreated |
| `svm` | string | Storage Virtual Machine 名 | EVTX SVMName / XML Computer |
| `user` | string | 操作を実行したユーザー | EVTX UserName / XML SubjectUserName |
| `client_ip` | string | クライアント IP アドレス | EVTX ClientIP / XML IpAddress |
| `operation` | string | 操作タイプ | EVTX Operation / XML ObjectType |
| `path` | string | ファイルまたはディレクトリパス | EVTX ObjectName / XML ObjectName |
| `result` | string | Success または Failure | EVTX Result / XML Keywords |
| `raw` | object | 元のパース済みフィールド（ベンダー固有利用） | 完全パースイベント |

## ベンダーマッピングマトリクス

| 内部フィールド | Datadog | Splunk HEC | Elastic (ECS) | Loki | New Relic | OTel (OTLP) |
|---------------|---------|------------|---------------|------|-----------|-------------|
| `source` | `source` tag | `source` | `event.dataset` | `source` label | `logtype` | `event.name` |
| `svm` | `@attributes.svm` | `svm` field | `netapp.ontap.svm` | `svm` label | `svm` attribute | `netapp.ontap.svm` |
| `user` | `@attributes.user` | `user` field | `user.name` | JSON body | `user` attribute | `user.name` |
| `client_ip` | `@attributes.client_ip` | `client_ip` field | `source.ip` | JSON body | `client_ip` attribute | `source.address` |
| `operation` | `@attributes.operation` | `action` field | `event.action` | `operation` label | `operation` attribute | `event.action` |
| `path` | `@attributes.path` | `file_path` field | `file.path` | JSON body | `path` attribute | `file.path` |
| `result` | `@attributes.result` | `result` field | `event.outcome` | `result` label | `result` attribute | `event.outcome` |
| `timestamp` | `timestamp` | `_time` | `@timestamp` | timestamp | `timestamp` | `TimeUnixNano` |

## ベンダー固有の考慮事項

### Datadog
- `source` と `service` タグでパイプラインルーティング
- カスタム属性は `@attributes.*` 名前空間
- Datadog Log Pipeline でインデックスルーティング

### Splunk
- `sourcetype=fsxn:audit:evtx` または `fsxn:audit:xml` にマッピング
- `index=fsxn_audit` で専用リテンション
- CIM `Authentication` および `Change` データモデルを検討

### Elastic
- Elastic Common Schema (ECS) に可能な限り準拠
- ONTAP 固有フィールドは `netapp.ontap.*` 名前空間
- ILM 付きデータストリームでリテンション管理

### Grafana / Loki
- ラベルは低カーディナリティに保つ: `source`, `svm`, `operation`, `result`
- 高カーディナリティフィールド (`path`, `user`, `client_ip`) は JSON ログボディに格納
- ファイルパスやユーザー名を Loki ラベルに入れないこと

### New Relic
- Log API 属性にマッピング
- NRQL でクエリ: `FROM Log WHERE source = 'fsxn'`
- `aws.account.id` + `fsx.filesystem.id` でエンティティ関連付け

### Honeycomb
- 全フィールドをイベント属性として保持（高カーディナリティ OK）
- パイプライン可観測性フィールド追加: `processing_latency_ms`, `batch_size`
- パスプレフィックス分析に derived columns を使用

### OpenTelemetry (OTLP)
- OpenTelemetry Semantic Conventions for Logs に準拠
- `event.*`, `file.*`, `user.*`, `source.*` 名前空間を使用
- リソース属性: `cloud.provider`, `cloud.region`, `cloud.account.id`

## 設計原則

1. **一度正規化し、ベンダーごとにマッピング** — パースと正規化は共有レイヤーで実行。ベンダー固有のフォーマッティングのみがベンダーごとのコード。

2. **ベンダー固有 Lambda は迅速な導入とネイティブ API 動作に最適化**。一方、OpenTelemetry 統合は OTLP を標準化する組織向けのベンダーニュートラルなパスを提供。

3. **監査パイプライン自体を可観測なシステムとして扱う** — ベンダーがサポートする場合、監査イベントと共に処理レイテンシ、バッチサイズ、リトライ回数、ベンダーレスポンスメタデータを出力。


## EMS / ARP イベントマッピング

| 内部フィールド | Datadog | OpenTelemetry | Splunk | Elastic ECS |
|---------------|---------|---------------|--------|-------------|
| `event_name` | `@attributes.event_name` | `event.name` | `event_name` | `event.action` |
| `severity` | `@attributes.severity` | `severity_text` | `severity` | `event.severity` |
| `svm` | `@attributes.svm` | `netapp.ontap.svm` | `svm` | `netapp.ontap.svm` |
| `source_node` | `host` | `host.name` | `host` | `host.name` |
| `parameters.volume_name` | `@attributes.parameters.volume_name` | `netapp.ontap.volume` | `volume_name` | `netapp.ontap.volume` |
| `parameters.state` | `@attributes.parameters.state` | `netapp.ontap.arp.state` | `arp_state` | `netapp.ontap.arp.state` |
| `timestamp` | `date` | `time_unix_nano` | `_time` | `@timestamp` |
| `message` | `message` | `body` | `_raw` | `message` |


## FPolicy ファイル操作イベントマッピング

| 内部フィールド | Datadog | OpenTelemetry | Splunk | Elastic ECS |
|---------------|---------|---------------|--------|-------------|
| `operation_type` | `@attributes.operation_type` | `event.action` | `operation_type` | `event.action` |
| `file_path` | `@attributes.file_path` | `file.path` | `file_path` | `file.path` |
| `client_ip` | `@attributes.client_ip` | `source.ip` | `client_ip` | `source.ip` |
| `volume_name` | `@attributes.volume_name` | `netapp.ontap.volume` | `volume_name` | `netapp.ontap.volume` |
| `svm` | `@attributes.svm` | `netapp.ontap.svm` | `svm` | `netapp.ontap.svm` |
| `user` | `@attributes.user` | `user.name` | `user` | `user.name` |
| `protocol` | `@attributes.protocol` | `network.protocol` | `protocol` | `network.protocol` |
| `timestamp` | `date` | `time_unix_nano` | `_time` | `@timestamp` |
| `event_id` | `@attributes.event_id` | `event.id` | `event_id` | `event.id` |

### 注記

- FPolicy は `operation_type`（create, write, rename, delete）を使用し、監査ログは `operation`（ReadData, WriteData 等）を使用します。これは意図的な設計です — 異なる抽象レベルを表しています。
- `svm` フィールドは、FPolicy サーバーがハンドシェイクコンテキストから SVM 名を解決できない場合 "unknown" になることがあります。
- `user` フィールドは、ONTAP の FPolicy 通知内容によっては空になることがあります。
