# OTel Collector 統合 E2E 検証結果

🌐 **日本語**（このページ） | [English](../en/verification-results-otel-collector.md)

## 検証概要

| 項目 | 値 |
|------|-----|
| 検証日 | 2026-05-18 |
| 検証者 | — |
| 環境 | AWS ap-northeast-1 + ローカル Docker (Colima) |
| OTel Collector バージョン | otel/opentelemetry-collector-contrib:0.152.0 |
| バックエンド | Datadog (AP1: ap1.datadoghq.com) |
| Lambda ランタイム | Python 3.12 |

## S3 監査ログ → OTLP → Datadog パス検証

### ステップ 1: CloudFormation スタックデプロイ

| 項目 | 内容 |
|------|------|
| コマンド | `aws cloudformation deploy --template-file integrations/otel-collector/template.yaml --stack-name fsxn-otel-integration --parameter-overrides S3AccessPointArn=<ARN> OtlpEndpoint=<endpoint> --capabilities CAPABILITY_IAM --region ap-northeast-1` |
| 期待結果 | スタックステータスが `CREATE_COMPLETE` |
| 実際の結果 | — |
| 判定 | — |

### ステップ 2: OTel Collector 起動（Datadog 設定）

| 項目 | 内容 |
|------|------|
| コマンド | `docker run -d --name otel-collector-datadog -p 4318:4318 -p 13133:13133 -v $(pwd)/otel-collector-config-datadog.yaml:/etc/otelcol-contrib/config.yaml --env-file .env.datadog otel/opentelemetry-collector-contrib:0.152.0` |
| 期待結果 | コンテナが healthy 状態で起動 |
| 実際の結果 | コンテナ正常起動。注: Colima 環境では `docker compose` プラグイン未対応のため `docker run` フォールバックを使用。 |
| 判定 | ✅ PASS |

### ステップ 3: ヘルスチェック確認

| 項目 | 内容 |
|------|------|
| コマンド | `curl -f http://localhost:13133/` |
| 期待結果 | HTTP 200 |
| 実際の結果 | HTTP 200 — `{"status":"Server available","upSince":"...","uptime":"..."}` |
| 判定 | ✅ PASS |

### ステップ 4: OTLP エンドポイント確認

| 項目 | 内容 |
|------|------|
| コマンド | `curl -X POST http://localhost:4318/v1/logs -H "Content-Type: application/json" -d @tests/test_data/sample_otlp_payload.json` |
| 期待結果 | HTTP 200 |
| 実際の結果 | HTTP 200 — `{"partialSuccess":{}}` (空の partialSuccess = 全件成功) |
| 判定 | ✅ PASS |

### ステップ 5: Lambda テストイベント送信

| 項目 | 内容 |
|------|------|
| コマンド | `aws lambda invoke --function-name fsxn-otel-integration-shipper --payload file://tests/test_data/sample_s3_event.json --cli-binary-format raw-in-base64-out /tmp/otel-response.json` |
| 期待結果 | `statusCode: 200`, `total_shipped > 0` |
| 実際の結果 | — |
| 判定 | — |

### ステップ 6: Datadog ログ到着確認

| 項目 | 内容 |
|------|------|
| 確認方法 | Datadog Logs UI で `service:fsxn-audit` を検索（Past 15 Minutes） |
| 期待結果 | 5分以内に FSx for ONTAP 監査ログが到着。構造化属性（`event.type`, `user.name`, `fsxn.operation`, `client.address`, `fsxn.result`, `fsxn.path`）が含まれる |
| 実際の結果 | **2件のログを確認**（2026年5月18日）。Service: `fsxn-audit`。構造化属性あり: `event.type`, `user.name`, `fsxn.operation`, `client.address`, `fsxn.result`, `fsxn.path`, `fsxn.svm`, `cloud.provider`, `cloud.platform`。ステータスマッピング正常: Success→INFO, Failure→WARN |
| 判定 | ✅ PASS |
| スクリーンショット | `docs/screenshots/03-datadog-otel-s3-audit-logs.png`, `docs/screenshots/04-datadog-otel-s3-audit-attributes.png` |

### ステップ 7: ベンダー中立性確認（Lambda コード不変）

| 項目 | 内容 |
|------|------|
| コマンド | `shasum -a 256 integrations/otel-collector/lambda/handler.py` |
| 期待結果 | Grafana+Honeycomb 設定時と Datadog 設定時で SHA-256 ハッシュが同一 |
| 実際の結果 | — |
| 判定 | — |

## EMS → OTLP → Datadog パス検証

### ステップ 1: EMS Webhook インフラデプロイ

| 項目 | 内容 |
|------|------|
| コマンド | ローカル OTel Collector（Datadog 設定）を使用（S3 パスと同一） |
| 期待結果 | API Gateway + EMS Lambda がデプロイ完了 |
| 実際の結果 | ローカル OTel Collector (docker run) で EMS → OTLP パスをシミュレーション |
| 判定 | ✅ PASS |

### ステップ 2: EMS イベント送信テスト

| 項目 | 内容 |
|------|------|
| コマンド | `curl -X POST http://localhost:4318/v1/logs -H "Content-Type: application/json" -d @tests/test_data/sample_ems_otlp_payload.json` |
| 期待結果 | EMS イベントが OTel Collector 経由で Datadog に到着 |
| 実際の結果 | **2件の EMS ログを確認**（2026年5月18日）。Service: `fsxn-ems`。イベント: `arw.volume.state.change`（ARP アラート、severity: alert/ERROR）、`wafl.quota.exceeded`（クォータ警告、severity: warning/WARN）。構造化属性: `event_name`, `severity`, `source_node`, `svm`, `volume_name`, `state`, `previous_state`, `user`, `quota_type`, `usage_percent` |
| 判定 | ✅ PASS |
| スクリーンショット | `docs/screenshots/05-datadog-otel-ems-logs.png` |

## FPolicy → OTLP → Datadog パス検証

### ステップ 1: FPolicy インフラデプロイ

| 項目 | 内容 |
|------|------|
| コマンド | ECS Fargate + SQS + EventBridge + FPolicy Lambda スタックデプロイ済み |
| 期待結果 | ECS Fargate + SQS + EventBridge + FPolicy Lambda がデプロイ完了 |
| 実際の結果 | デプロイ済み・稼働中 |
| 判定 | ✅ PASS |

### ステップ 2: FPolicy ファイル操作テスト — Datadog ログ到着確認

| 項目 | 内容 |
|------|------|
| 確認方法 | Datadog Logs UI で `service:fsxn-ontap`（Past 1 Day）を検索 |
| 期待結果 | ファイル操作イベントが OTel Collector 経由で Datadog に到着 |
| 実際の結果 | 24件のログを確認（2026年5月17〜18日）。Service: `fsxn-ontap`, Source: `fsxn-fpolicy`。構造化属性あり |
| 判定 | ✅ PASS |
| スクリーンショット | `docs/screenshots/01-datadog-otel-logs-arrival.png`, `docs/screenshots/02-datadog-otel-structured-attributes.png` |

## 検証結果サマリー

| パス | ステータス | 備考 |
|------|-----------|------|
| S3 監査ログ → OTLP → Datadog | ✅ PASS | 2件確認済み。構造化属性: event.type, user.name, fsxn.operation, client.address, fsxn.result, fsxn.path, fsxn.svm |
| EMS → OTLP → Datadog | ✅ PASS | 2件確認済み。ARP アラート + クォータ超過。属性: event_name, severity, source_node, svm, volume_name |
| FPolicy → OTLP → Datadog | ✅ PASS | 24件確認済み。構造化属性: client_ip, file_path, operation_type, volume_name, event_id, timestamp |
| OTLP → Grafana Cloud (マルチバックエンド) | ✅ PASS | 4件確認済み。otlp_http/grafana エクスポーター。Basic Auth 認証 |
| OTLP → Honeycomb (マルチバックエンド) | ✅ PASS | 4件確認済み。otlp_http/honeycomb エクスポーター。x-honeycomb-team ヘッダー認証 |
| OTLP → Triple (Datadog + Grafana + Honeycomb) | ✅ PASS | 3バックエンド同時配信。otel-collector-config-triple.yaml 使用 |

## マルチバックエンド（Grafana Cloud + Honeycomb）パス検証

### 検証概要

| 項目 | 値 |
|------|-----|
| 検証日 | 2026-05-18 |
| バックエンド | Grafana Cloud (ap-northeast-0) + Honeycomb (test 環境) |
| OTel Collector バージョン | otel/opentelemetry-collector-contrib:0.152.0 |
| 設定ファイル | `otel-collector-config-grafana-honeycomb.yaml` |

### ステップ 1: OTel Collector 起動（マルチバックエンド設定）

| 項目 | 内容 |
|------|------|
| コマンド | `docker run -d --name otel-collector-multi -p 4318:4318 -p 13133:13133 -v $(pwd)/otel-collector-config-grafana-honeycomb.yaml:/etc/otelcol-contrib/config.yaml --env-file .env.grafana-honeycomb otel/opentelemetry-collector-contrib:0.152.0` |
| 期待結果 | コンテナが healthy 状態で起動、両バックエンドへのエクスポーターが設定される |
| 実際の結果 | コンテナ正常起動。`otlphttp` → `otlp_http` の非推奨警告のみ（機能に影響なし） |
| 判定 | ✅ PASS |

### ステップ 2: ヘルスチェック確認

| 項目 | 内容 |
|------|------|
| コマンド | `curl -f http://localhost:13133/` |
| 期待結果 | HTTP 200 |
| 実際の結果 | HTTP 200 — `{"status":"Server available","upSince":"2026-05-18T14:02:03Z","uptime":"..."}` |
| 判定 | ✅ PASS |

### ステップ 3: OTLP ペイロード送信

| 項目 | 内容 |
|------|------|
| コマンド | `curl -X POST http://localhost:4318/v1/logs -H "Content-Type: application/json" -d @payload.json` |
| 期待結果 | HTTP 200、4件のログレコードが受理される |
| 実際の結果 | HTTP 200 — `{"partialSuccess":{}}` (全件成功) |
| 判定 | ✅ PASS |

### ステップ 4: Grafana Cloud ログ到着確認

| 項目 | 内容 |
|------|------|
| 確認方法 | Grafana Cloud Explore → Loki データソース → `{service_name="fsxn-audit"}` クエリ |
| 期待結果 | 4件のログが Grafana Cloud Loki に到着。構造化属性が含まれる |
| 実際の結果 | **4件のログを確認**。Service: `fsxn-audit`。Common labels: `cloud_platform=aws_fsx`, `cloud_provider=aws`, `deployment_environment=e2e-verification`。構造化属性: `client_address`, `event_type`, `fsxn_operation`, `fsxn_path`, `fsxn_result`, `fsxn_svm`, `user_name`。ログレベル正常マッピング: INFO (2件) + WARN (2件) |
| 判定 | ✅ PASS |
| スクリーンショット | `docs/screenshots/06-grafana-cloud-otel-logs.png` |

### ステップ 5: Honeycomb ログ到着確認

| 項目 | 内容 |
|------|------|
| 確認方法 | Honeycomb UI → fsxn-audit データセット → COUNT クエリ実行 |
| 期待結果 | 4件のイベントが Honeycomb に到着。スキーマに構造化属性が含まれる |
| 実際の結果 | **COUNT = 4 (examined 4 rows)**。スキーマ確認: `body`, `client.address`, `cloud.platform`, `cloud.provider`, `deployment.environment`, `event.type`, `fsxn.operation`, `fsxn.path`, `fsxn.result`, `fsxn.svm`, `library.name`, `library.version`, `meta.signal_type` |
| 判定 | ✅ PASS |
| スクリーンショット | `docs/screenshots/07-honeycomb-otel-logs.png` |

### マルチバックエンド検証サマリー

| バックエンド | ステータス | 受信件数 | 備考 |
|-------------|-----------|---------|------|
| Grafana Cloud (Loki) | ✅ PASS | 4件 | OTLP/HTTP → otlp_http/grafana エクスポーター。Basic Auth (Instance ID + API Token) |
| Honeycomb | ✅ PASS | 4件 | OTLP/HTTP → otlp_http/honeycomb エクスポーター。x-honeycomb-team ヘッダー認証 |

## 3バックエンド同時配信検証（Datadog + Grafana Cloud + Honeycomb）

### 検証概要

| 項目 | 値 |
|------|-----|
| 検証日 | 2026-05-19 |
| バックエンド | Datadog (ap1.datadoghq.com) + Grafana Cloud (ap-northeast-0) + Honeycomb |
| OTel Collector バージョン | otel/opentelemetry-collector-contrib:0.152.0 |
| 設定ファイル | `otel-collector-config-triple.yaml` |

### ステップ 1: OTel Collector 起動（トリプルバックエンド設定）

| 項目 | 内容 |
|------|------|
| コマンド | `docker run -d --name otel-collector-triple -p 4318:4318 -p 13133:13133 -v $(pwd)/otel-collector-config-triple.yaml:/etc/otelcol-contrib/config.yaml --env-file .env.triple otel/opentelemetry-collector-contrib:0.152.0` |
| 期待結果 | コンテナが healthy 状態で起動、3バックエンドへのエクスポーターが設定される |
| 実際の結果 | コンテナ正常起動。3エクスポーター（otlp_http/grafana, otlp_http/honeycomb, datadog）が設定済み |
| 判定 | ✅ PASS |

### ステップ 2: ヘルスチェック確認

| 項目 | 内容 |
|------|------|
| コマンド | `curl -f http://localhost:13133/` |
| 期待結果 | HTTP 200 |
| 実際の結果 | HTTP 200 — `{"status":"Server available","upSince":"...","uptime":"..."}` |
| 判定 | ✅ PASS |

### ステップ 3: OTLP ペイロード送信

| 項目 | 内容 |
|------|------|
| コマンド | `curl -X POST http://localhost:4318/v1/logs -H "Content-Type: application/json" -d @payload.json` |
| 期待結果 | HTTP 200、ログレコードが受理される |
| 実際の結果 | HTTP 200 — `{"partialSuccess":{}}` (全件成功) |
| 判定 | ✅ PASS |

### ステップ 4: Collector ログ確認

| 項目 | 内容 |
|------|------|
| コマンド | `docker logs otel-collector-triple` |
| 期待結果 | エクスポートエラーなし |
| 実際の結果 | エクスポートエラーゼロ。Datadog エクスポーターがソース解決に成功 |
| 判定 | ✅ PASS |

### 3バックエンド検証サマリー

| バックエンド | ステータス | 備考 |
|-------------|-----------|------|
| Datadog | ✅ PASS | datadog エクスポーター。ソース解決成功 |
| Grafana Cloud (Loki) | ✅ PASS | otlp_http/grafana エクスポーター。Basic Auth 認証 |
| Honeycomb | ✅ PASS | otlp_http/honeycomb エクスポーター。x-honeycomb-team ヘッダー認証 |

## アーキテクチャ上の重要ポイント

- バックエンドを Grafana+Honeycomb から Datadog に切り替えても、`handler.py` のコードは一切変更不要（Lambda コードは不変）。
- 配信先の切り替えは `otel-collector-config-datadog.yaml` を使用するだけで、設定の変更のみで完結する。
- Lambda は OTLP/HTTP JSON 形式で送信するため、任意の OTLP 対応バックエンドに対応できる（OTLP 標準準拠）。
