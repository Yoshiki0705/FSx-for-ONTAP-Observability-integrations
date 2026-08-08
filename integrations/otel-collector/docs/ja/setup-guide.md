# OTel Collector 統合セットアップガイド

🌐 **日本語**（このページ） | [English](../en/setup-guide.md)

FSx for ONTAP 監査ログを OpenTelemetry Collector 経由で Grafana Cloud（Loki）と Honeycomb に同時配信するためのセットアップ手順です。

## 前提条件

- Docker および Docker Compose がインストール済み
- AWS CLI v2 が設定済み（`aws configure`）
- FSx for ONTAP S3 Access Point が作成済み
- Grafana Cloud アカウント（Loki エンドポイント、User ID、API Token）
- Honeycomb アカウント（API Key）
- Python 3.12（Lambda 開発用）

## OTel Collector Docker セットアップ

OTel Collector をローカルで起動し、OTLP/HTTP でログを受信します。

### Docker Compose 設定

```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.152.0
    ports:
      - "4318:4318"   # OTLP HTTP
      - "13133:13133" # Health check
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml
    environment:
      - GRAFANA_OTLP_ENDPOINT=${GRAFANA_OTLP_ENDPOINT}
      - GRAFANA_BASIC_AUTH=${GRAFANA_BASIC_AUTH}
      - HONEYCOMB_API_KEY=${HONEYCOMB_API_KEY}
      - HONEYCOMB_DATASET=${HONEYCOMB_DATASET:-fsxn-audit}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:13133/"]
      interval: 5s
      timeout: 3s
      retries: 5
      start_period: 10s
    restart: unless-stopped
```

> **注意**: macOS で Colima を使用している場合、`docker compose` v2 プラグインが利用できません。`docker run` フォールバックを使用してください：
> ```bash
> docker run -d --name otel-collector \
>   -p 4318:4318 -p 13133:13133 \
>   -v $(pwd)/otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml \
>   --env-file .env \
>   otel/opentelemetry-collector-contrib:0.152.0
> ```

### 環境変数の設定

`.env.example` をコピーして `.env` を作成し、認証情報を設定します：

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

### 起動

```bash
cd integrations/otel-collector
docker compose up -d
```

ヘルスチェックの確認：

```bash
curl -f http://localhost:13133/
```

## Collector YAML 設定

OTel Collector の設定ファイルは、OTLP レシーバー、バッチプロセッサー、および Grafana Cloud + Honeycomb エクスポーターを定義します。

> **重要**: Grafana Cloud への OTLP 送信には `loki` エクスポーターではなく `otlp_http/grafana` を使用します。OTLP Gateway エンドポイントがネイティブにログ取り込みを処理します。

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 1000

exporters:
  otlp_http/grafana:
    endpoint: ${env:GRAFANA_OTLP_ENDPOINT}
    headers:
      Authorization: "Basic ${env:GRAFANA_BASIC_AUTH}"

  otlp_http/honeycomb:
    endpoint: https://api.honeycomb.io
    headers:
      x-honeycomb-team: ${env:HONEYCOMB_API_KEY}
      x-honeycomb-dataset: ${env:HONEYCOMB_DATASET}

extensions:
  health_check:
    endpoint: 0.0.0.0:13133

service:
  extensions: [health_check]
  pipelines:
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp_http/grafana, otlp_http/honeycomb]
```

この設定により、Lambda から送信された OTLP ログが自動的に Grafana Cloud と Honeycomb の両方に配信されます。

### 認証パターン（検証済み）

**Grafana Cloud**:
- エンドポイント: `https://otlp-gateway-prod-<region>.grafana.net/otlp`
- 認証: `Basic base64(instanceId:apiToken)`
- Instance ID は数値（例: 1649835）
- リージョン例: `ap-northeast-0`（日本）

**Honeycomb**:
- エンドポイント: `https://api.honeycomb.io`
- 認証: `x-honeycomb-team` ヘッダーに Ingest API Key
- Ingest Key は `hcaik_` で始まる

## CloudFormation デプロイ

3 つのシッパー Lambda（監査ログ、EMS、FPolicy）と関連リソースをデプロイします。

### 推奨: デプロイスクリプトを使う

他のベンダーと違い、このテンプレートはインラインのプレースホルダではなく S3 から Lambda
コードを取得します。そのためスタック作成**より前に**パッケージが S3 に存在している必要が
あります。スクリプトはビルド → アップロード → デプロイをこの順で実行します。

```bash
export OTLP_ENDPOINT="http://your-collector:4318"
export S3_BUCKET_NAME="fsxn-audit-logs-bucket"
export LAMBDA_CODE_S3_BUCKET="my-lambda-artifacts"

bash integrations/otel-collector/scripts/deploy.sh
```

3 つの関数は 1 つのパッケージを共有し、エントリポイントは `Handler` で切り替えます。
スタックに触らずコードだけ再ビルド・再アップロードする場合は `--code-only` を使います。
対応する変数の一覧は `--help` で確認できます。

> スクリプト実行前に `ALARM_TOPIC_ARN` に SNS トピック ARN を設定すると、CloudWatch
> アラームが通知されるようになります。未設定の場合アラームは通知アクションなしで作成され、
> コンソールには表示されますが誰にも通知されません。

### 代替: CloudFormation を手動でデプロイする

先にコードをパッケージしてアップロードします。`LambdaCodeS3Bucket` は必須で、オブジェクト
が既に存在していなければなりません:

```bash
cd integrations/otel-collector/lambda
zip -j /tmp/lambda.zip handler.py ems_handler.py fpolicy_handler.py \
  otlp_auth.py otlp_protobuf.py ../../../shared/python/ontap_audit_parser.py

aws s3 cp /tmp/lambda.zip s3://my-lambda-artifacts/otel-collector/lambda.zip \
  --region ap-northeast-1
```

```bash
aws cloudformation deploy \
  --template-file integrations/otel-collector/template.yaml \
  --stack-name fsxn-otel-integration \
  --parameter-overrides \
    OtlpEndpoint=http://your-collector:4318 \
    S3BucketName=fsxn-audit-logs-bucket \
    LambdaCodeS3Bucket=my-lambda-artifacts \
    ServiceName=fsxn-audit \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

パッケージに `ontap_audit_parser.py` を含めないとハンドラは JSON 専用の解析に
フォールバックし、ONTAP の監査ログ（常に XML か EVTX）はフィールド解析されずに配送されます。

CloudFormation は S3 オブジェクトのキーまたはバージョンが変わらないと新しいコードを
取り込みません。同じキーに再アップロードした場合は `scripts/deploy.sh --code-only`
（または `aws lambda update-function-code`）を実行して関数に読み込ませてください。

### パラメータ

このスタックは監査ログバケット上に S3 アクセスポイントを**作成**し、`S3AccessPointArn`
という Output として公開します。入力パラメータではありません。

<!-- generated from template.yaml; keep in sync when parameters change -->

必須:

| パラメータ | 説明 |
|-----------|-------------|
| `S3BucketName` | Name of the S3 bucket containing FSx for ONTAP audit log files. Used for EventBridge trigger and S3 Access Point creation. |
| `OtlpEndpoint` | OTLP/HTTP base endpoint URL, no trailing /v1/logs — the Lambda appends that path itself and POSTs to `<endpoint>`/v1/logs. See `template.yaml` for the full description. |
| `LambdaCodeS3Bucket` | S3 bucket containing the Lambda deployment package (ZIP) |

任意 — 既定値でほとんどのデプロイに対応できます:

| パラメータ | 既定値 | 説明 |
|-----------|---------|-------------|
| `ApiKeySecretArn` | `''` (empty) | ARN of the Secrets Manager secret containing auth credentials for the OTLP endpoint. Leave empty if no authentication is required. Accepts either a plain string secret, e.g. See `template.yaml` for the full description. |
| `AuthMode` | `none` | Authentication mode for the OTLP endpoint. none: No auth header. bearer: Authorization Bearer `<token>`. basic: Authorization Basic base64(`<secret>`). See `template.yaml` for the full description. |
| `AuthHeaderName` | `Authorization` | Header name used when AuthMode=header (e.g. "Mackerel-Api-Key"). Ignored for other AuthMode values. |
| `ExtraHeadersJson` | `''` (empty) | Optional static (non-secret) extra HTTP headers as a JSON object string, e.g. '{"Accept":"*/*"}'. Required by some vendors' OTLP endpoints (e.g. Mackerel) regardless of auth mode. See `template.yaml` for the full description. |
| `OtlpContentType` | `json` | Wire format for the OTLP/HTTP request body when sending directly to a vendor's endpoint (no OTel Collector in between). json (default) sends OTLP/JSON. See `template.yaml` for the full description. |
| `ServiceName` | `fsxn-audit` | Value for the OTLP resource attribute service.name |
| `LambdaCodeS3Key` | `otel-collector/lambda.zip` | S3 key for the Lambda deployment package (ZIP) |
| `LambdaCodeS3Version` | `''` (empty) | S3 object version ID for the Lambda package (optional) |
| `S3KeyPrefix` | `audit/` | Key prefix for audit log files within the S3 bucket |
| `EventBridgeBusName` | `default` | EventBridge bus name for FPolicy events |
| `FPolicySqsQueueArn` | `''` (empty) | ARN of the SQS queue receiving FPolicy events from Fargate server. If provided, creates an SQS event source mapping (primary trigger). |
| `EmsParserLayerArn` | `''` (empty) | ARN of the shared EMS Parser Lambda Layer (optional) |
| `LogLevel` | `INFO` | Lambda function log level (applies to all functions) |
| `LambdaMemorySize` | `256` | Lambda function memory size in MB |
| `AuditLambdaTimeout` | `300` | Audit log shipper Lambda timeout in seconds |
| `EmsLambdaTimeout` | `30` | EMS handler Lambda timeout in seconds |
| `FPolicyLambdaTimeout` | `30` | FPolicy handler Lambda timeout in seconds |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. See `template.yaml` for the full description. |


## テストイベント実行

Lambda 関数にテストイベントを送信して動作を確認します。

```bash
aws lambda invoke \
  --function-name fsxn-otel-integration-shipper \
  --payload file://integrations/otel-collector/tests/test_data/sample_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  /tmp/otel-response.json

cat /tmp/otel-response.json
```

期待されるレスポンス：

```json
{"statusCode": 200, "body": {"total_logs": 6, "total_shipped": 6, "errors": []}}
```

## 検証手順

### 1. Lambda 実行ログの確認

CloudWatch Logs で OTLP 配信成功を確認します：

```bash
aws logs tail /aws/lambda/fsxn-otel-integration-shipper --since 5m
```

期待される出力：`OTLP payload sent successfully` のログエントリが表示されること。


### 2. Grafana Cloud でのログ到着確認

Grafana Cloud Explore で以下のクエリを実行します：

- データソース: Loki
- クエリ: `{job="fsxn-audit"}`

5分以内に FSx for ONTAP 監査ログが表示されることを確認します。`event.type`、`user.name`、`fsxn.operation` 属性が含まれていることを確認してください。

![Grafana Cloud ログ到着](../../../../docs/screenshots/06-grafana-cloud-otel-logs.png)

### 3. Honeycomb でのログ到着確認

Honeycomb の `fsxn-audit` データセットでクエリを実行します：

- データセット: `fsxn-audit`
- 時間範囲: 過去5分

5分以内に FSx for ONTAP 監査ログが表示されることを確認します。

### 4. マルチバックエンド一貫性確認

Grafana Cloud と Honeycomb の両方で同一のイベント（同じタイムスタンプ、同じファイルパス）が確認できることを検証します。

## Honeycomb のみの設定

Grafana Cloud を使用せず、**Honeycomb のみ**をバックエンドとして使用する場合の設定です。Lambda コードの変更は不要で、OTel Collector の設定ファイルを切り替えるだけです。

### Honeycomb 専用 Collector 設定

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 1000

exporters:
  otlp_http/honeycomb:
    endpoint: https://api.honeycomb.io
    headers:
      x-honeycomb-team: ${env:HONEYCOMB_API_KEY}
      x-honeycomb-dataset: ${env:HONEYCOMB_DATASET}

extensions:
  health_check:
    endpoint: 0.0.0.0:13133

service:
  extensions: [health_check]
  pipelines:
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp_http/honeycomb]
```

### 環境変数

```bash
# .env.honeycomb
HONEYCOMB_API_KEY=hcaik_your_ingest_key_here
HONEYCOMB_DATASET=fsxn-audit
```

### 起動コマンド

```bash
# Honeycomb 専用設定ファイルを作成後:
docker run -d --name otel-collector-honeycomb \
  -p 4318:4318 -p 13133:13133 \
  -v $(pwd)/otel-collector-config-honeycomb.yaml:/etc/otelcol-contrib/config.yaml \
  --env-file .env.honeycomb \
  otel/opentelemetry-collector-contrib:0.152.0
```

> **注意**: Honeycomb の Ingest API Key は `hcaik_` で始まります。Environment Key（`hcxik_`）ではデータ取り込みができません。

## Datadog バックエンド設定

Grafana Cloud + Honeycomb の代わりに **Datadog** をバックエンドとして使用する場合の設定です。Lambda コードの変更は不要で、OTel Collector の設定ファイルを切り替えるだけで配信先を変更できます。

### Datadog 用 Collector 設定

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 1000

exporters:
  datadog:
    api:
      key: ${env:DD_API_KEY}
      site: ${env:DD_SITE}

extensions:
  health_check:
    endpoint: 0.0.0.0:13133

service:
  extensions: [health_check]
  pipelines:
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [datadog]
```

### Docker Compose（Datadog 版）の起動

```bash
# 1. Configure credentials
cp .env.datadog.example .env.datadog
# Edit .env.datadog with your DD_API_KEY and DD_SITE
# DD_SITE examples:
#   datadoghq.com (US1), datadoghq.eu (EU),
#   ap1.datadoghq.com (AP1/Japan), us3.datadoghq.com (US3)

# 2. Start OTel Collector with Datadog config
# Option A: docker compose (if available)
docker compose -f docker-compose-datadog.yaml --env-file .env.datadog up -d

# Option B: docker run (fallback for Colima or environments without compose plugin)
docker run -d --name otel-collector-datadog \
  -p 4318:4318 -p 13133:13133 \
  -v $(pwd)/otel-collector-config-datadog.yaml:/etc/otelcol-contrib/config.yaml \
  --env-file .env.datadog \
  otel/opentelemetry-collector-contrib:0.152.0

# 3. Verify health check
curl -f http://localhost:13133/
```

> **注意**: macOS で Colima を使用している場合、`docker compose` (v2 プラグイン) が利用できないことがあります。その場合は方法 B の `docker run` を使用してください。

### Datadog での検証手順

1. Datadog Logs UI にログインします
2. 検索フィルタに `source:fsxn-audit` または `service:fsxn-ontap`（FPolicy の場合）を入力します
3. FSx for ONTAP ログが到着していることを確認します（5分以内）
4. 構造化属性が含まれることを確認します：
   - **S3 監査ログ**: `event.type`、`user.name`、`fsxn.operation`、`client.address`、`fsxn.result`、`fsxn.path`
   - **FPolicy**: `client_ip`、`file_path`、`operation_type`、`volume_name`、`event_id`、`timestamp`、`file_size`、`svm`/`vserver`

> **確認済み**: FPolicy → OTel Collector → Datadog パスは 2026-05-18 に検証完了。
> Service: `fsxn-ontap`、Source: `fsxn-fpolicy` として Datadog に表示されます。

### ローカルテストスクリプト

自動化されたローカルテストを実行するには：

```bash
bash scripts/test-local-datadog.sh
```

このスクリプトは以下を自動実行します：
- OTel Collector の起動（Datadog 設定）
- ヘルスチェック確認
- サンプル OTLP ペイロードの送信
- Collector ログの確認
- クリーンアップ


## 3バックエンド同時配信（Datadog + Grafana Cloud + Honeycomb）

単一の OTLP ストリームから **3つのバックエンド**（Datadog、Grafana Cloud、Honeycomb）に同時配信するには、トリプルバックエンド設定を使用します。Lambda コードの変更は不要です。

### トリプルバックエンド Collector の起動

```bash
# Start with triple-backend config
docker run -d --name otel-collector-triple \
  -p 4318:4318 -p 13133:13133 \
  -v $(pwd)/otel-collector-config-triple.yaml:/etc/otelcol-contrib/config.yaml \
  --env-file .env.triple \
  otel/opentelemetry-collector-contrib:0.152.0
```

### 環境変数

```bash
cp .env.triple.example .env.triple
# Edit .env.triple with your credentials for all 3 backends
```

### service.name マッピング

S3 監査ログは `service.name=fsxn-audit`、EMS は `service.name=fsxn-ems`、FPolicy は `service.name=fsxn-fpolicy` を使用します。

> Honeycomb の環境やデータセットモデルによっては、`x-honeycomb-dataset` がオプションまたは異なる扱いになる場合があります。Honeycomb の OTLP セットアップページを参照してください。

## Firehose バッファリングパス（高ボリューム向け）

1,000 イベント/秒を超える高ボリュームシナリオでは、Lambda から直接 OTel Collector に送信する代わりに、Kinesis Data Firehose を中間バッファとして使用することを検討してください。

### アーキテクチャ

```
S3 Access Point → Lambda → Kinesis Data Firehose → OTel Collector → Backends
                                    │
                                    ├── 自動バッファリング (60秒 or 1MB)
                                    ├── 自動リトライ
                                    └── バックプレッシャー処理
```

### いつ Firehose パスを使用するか

| 条件 | 直接送信 | Firehose パス |
|------|---------|--------------|
| イベント量 | < 1,000/秒 | > 1,000/秒 |
| レイテンシ要件 | リアルタイム (< 5秒) | ニアリアルタイム (< 60秒) |
| バースト耐性 | Lambda 同時実行数に依存 | Firehose が自動バッファ |
| コスト | Lambda 実行時間のみ | + Firehose 料金 |
| 信頼性 | Lambda リトライのみ | Firehose 自動リトライ + S3 バックアップ |

### Firehose 設定例

```yaml
# CloudFormation snippet
FirehoseDeliveryStream:
  Type: AWS::KinesisFirehose::DeliveryStream
  Properties:
    DeliveryStreamName: fsxn-otel-firehose
    HttpEndpointDestinationConfiguration:
      EndpointConfiguration:
        Url: http://<collector-endpoint>:4318/v1/logs
        Name: OTelCollector
      BufferingHints:
        IntervalInSeconds: 60
        SizeInMBs: 1
      RetryOptions:
        DurationInSeconds: 300
      S3BackupMode: FailedDataOnly
      S3Configuration:
        BucketARN: arn:aws:s3:::fsxn-firehose-backup
        RoleARN: !GetAtt FirehoseRole.Arn
```

### 注意事項

- Firehose は HTTP エンドポイントに対して JSON 形式でバッチ送信します
- OTel Collector 側で Firehose 形式のパースが必要な場合があります
- Datadog と Splunk は Firehose のネイティブ宛先として利用可能（OTel Collector 不要）
- Firehose の最小バッファ間隔は 60 秒のため、リアルタイム性が必要な場合は直接送信を推奨


---

## 追加バックエンド: Snowflake（実験的）

Snowflake は [OpenFlow ListenOTLP プロセッサ](https://docs.snowflake.com/en/user-guide/data-integration/openflow/processors/listenotlp) やコミュニティレシーバー（[snowflake-opentelemetry-receiver](https://github.com/KellerKev/snowflake-opentelemetry-receiver)）を通じて OTLP データを受信できます。既存の Snowflake データレイクハウス内で FSx for ONTAP 監査ログの SQL ベースセキュリティ分析が可能になります。

```yaml
# Example: OTel Collector exporter for Snowflake (community path)
exporters:
  otlphttp/snowflake:
    endpoint: https://<account>.snowflakecomputing.com/v1/otlp
    headers:
      Authorization: "Bearer ${env:SNOWFLAKE_TOKEN}"
```

> **ステータス**: 本プロジェクトでは未検証です。Snowflake の OTLP 取り込みパスは進化中です。本番利用前にエンドポイント構成、認証、Event Table スキーマをお使いの Snowflake 環境で検証してください。マネージドな代替手段として [Bindplane Snowflake destination](https://docs.bindplane.com/integrations/destinations/snowflake) も参照ください。

## デプロイの検証

```bash
bash integrations/otel-collector/scripts/verify.sh
```

3 つのシッパー関数すべてに対して共有の AWS 側チェックを実行します（スタックが正常か、
各関数が古い・欠落したパッケージではなく実ハンドラを保持しているか）。続いて
`OTLP_ENDPOINT` が設定されていれば `$OTLP_ENDPOINT/v1/logs` へ合成 OTLP ログを 1 件
POST します。

どちらも必要です。Collector がテストログを受理しても、パイプラインが動いている保証には
なりません。そのため AWS 側が失敗した場合は、エンドポイントが正常応答していてもスクリプトは
非ゼロで終了します。デプロイ前にエンドポイントだけ試す場合は `SKIP_AWS_CHECKS=1` を
設定します。

終了コードは `sysexits.h` 準拠で `0` 成功、`69` チェック失敗、`78` 必要な設定が不足。

## クリーンアップ

```bash
bash integrations/otel-collector/scripts/cleanup.sh          # stacks only
bash integrations/otel-collector/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/otel-collector/scripts/cleanup.sh --all -y  # non-interactive
```

`s3://$LAMBDA_CODE_S3_BUCKET/$LAMBDA_CODE_S3_KEY` にある Lambda パッケージは削除されません。
このバケットは利用者側のもので、本スタックが作成したものではないためです。不要になったら
オブジェクトを手動で削除してください。

## 関連ドキュメント

- [ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md) — 全ベンダー共通の手順
- [前提条件](../../../../docs/ja/prerequisites.md) — FSx for ONTAP、監査ログ有効化、S3 アクセスポイント
- [デプロイガイド](../../../../docs/ja/deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
