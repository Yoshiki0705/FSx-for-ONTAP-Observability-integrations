# Mackerel ログ機能 セットアップガイド（オープンβ）

🌐 [English](../en/setup-guide.md)

> **本ガイドの範囲**: 本ガイドは Mackerel の認証情報準備と、OpenTelemetry Collector でログを受信・転送する設定手順（Step 1〜4）、および Collector を経由せず直接送信する代替手段（Step 5）を扱います。両方の経路の Lambda / CloudFormation コードは、本リポジトリの [OTel Collector 統合](../../../otel-collector/) に既に存在します — 実際に何が検証済みかは [../../README.md#実装ステータス](../../README.md#実装ステータス) を参照してください。**Collector 経由の経路、直接送信の経路（Step 5）とも、実際の Mackerel オーガニゼーションに対する E2E 実行を確認済みです**（2026年7月18日）。直接送信の経路では `OTLP_CONTENT_TYPE=protobuf` の指定が必須です — Mackerel の OTLP エンドポイントは JSON を拒否します。詳細は下記 Step 5.1 を参照してください。

## 概要

Mackerel のログ機能（2026年7月16日にオープンβとして公開）は OpenTelemetry Protocol (OTLP) 経由の送信**のみ**を受け付けます。ログ直接投稿用の独自 REST API はありません。本ガイドでは以下を行います:

1. Write 権限のある Mackerel API キーの取得
2. `logs` パイプライン用の OpenTelemetry Collector 設定の準備
3. サンプル OTLP ログペイロードの送信と、Mackerel ログ画面での到着確認
4. 本番利用前に理解すべきβ版の制約

## 前提条件

- Mackerel アカウント（フリープラン、スタンダードプラン、いずれかのトライアル）
- Docker（ローカルでの Collector 検証用。既に別の場所で Collector を運用している場合は不要）
- `curl` または OTLP 対応のテストクライアント

## Step 1: Mackerel 認証情報の準備

### 1.1 Write 権限のある API キーの取得

1. [Mackerel](https://mackerel.io/signin) にログイン
2. オーガニゼーション設定から **API キー** を開く
3. **Write** 権限のあるキーを新規作成、または既存のものを利用
4. キーの値をコピー — `Mackerel-Api-Key` ヘッダーの値として使用します

> **重要**: Read 権限のみの API キーはログ送信に使用できません。ログ送信には、Mackerel のトレーシング（APM）機能と同じ Write 権限が必要です。

### 1.2 AWS Secrets Manager への保存

ドキュメント整備のみの現段階でも、本リポジトリの他ベンダーと同じパターンでキーを保存しておくことで、将来の Lambda 実装時に認証設計をやり直す必要がなくなります:

```bash
aws secretsmanager create-secret \
  --name "mackerel/fsxn-log-credentials" \
  --description "Mackerel API key (Write scope) for FSx for ONTAP log integration (beta)" \
  --secret-string '{"api_key":"YOUR_MACKEREL_API_KEY"}' \
  --region ap-northeast-1
```

> **シークレット名**: `mackerel/fsxn-log-credentials`
>
> **JSON 形式**: `{"api_key":"<key>"}`

### 1.3 OTLP エンドポイントの確認

Mackerel のログ機能は、**トレーシング（APM）機能と同一の OTLP エンドポイント**を使用します:

```
https://otlp-vaxila.mackerelio.com
```

認証はトレーシングと共通の単一ヘッダーです:

```
Mackerel-Api-Key: <your-write-scoped-api-key>
```

> **注意**: 一部のベンダーとは異なり、Mackerel は OTLP 送信に Basic Auth やベアラートークンを使用しません。単一のカスタムヘッダーです。また、`Accept: */*` ヘッダーが必須である点にも注意してください（Mackerel公式ドキュメントで必須と明記されていますが、その理由自体は公開されていません）。

## Step 2: OpenTelemetry Collector 設定の準備

Mackerel は2種類の Collector をドキュメント化しています:

- **OpenTelemetry Collector Contrib** — 汎用コレクター。複数バックエンドへ同時にファンアウト可能（本リポジトリの既存 [OTel Collector 統合](../../../otel-collector/) と一貫性がある）
- **Mackerel Distro of OpenTelemetry (MDOT) コレクター** — Mackerel 提供の専用ディストリビューション。Mackerel 向け exporter が組み込み済み

本ガイドでは本リポジトリの既存マルチバックエンド構成との一貫性を保つため、**OpenTelemetry Collector Contrib** を使用します。

### 2.1 config.yaml

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"
      http:
        endpoint: "0.0.0.0:4318"

processors:
  batch:
    # This is a general-purpose batch processor setting (not something
    # Mackerel's own docs specifically recommend — their own config examples
    # skip this processor and rely solely on the exporter's sending_queue
    # below). This repo's actual verified config
    # (otel-collector-config-mackerel.yaml) uses a batch processor with
    # timeout: 5s / send_batch_size: 1000; this sample mirrors that.
    timeout: 5s
    send_batch_size: 1000

exporters:
  otlphttp/mackerel:
    endpoint: "https://otlp-vaxila.mackerelio.com"
    headers:
      Accept: "*/*"
      Mackerel-Api-Key: "${env:MACKEREL_APIKEY}"
    sending_queue:
      batch:
        max_size: 3500000
        sizer: bytes

extensions:
  health_check:

service:
  extensions: [health_check]
  pipelines:
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/mackerel]
```

> **マルチバックエンドに関する補足**: FSx for ONTAP のログを Mackerel と別のバックエンド（例: 本リポジトリの既存 OTel Collector 統合経由の Datadog）に同時にファンアウトしたい場合は、同じ `logs` パイプラインの `exporters` リストに2つ目の exporter（例: `otlphttp/datadog`）を追加するだけです。Lambda 側の変更は不要です — これは本リポジトリで既にドキュメント化されている [Datadog + Grafana + Honeycomb のマルチバックエンド構成](../../../otel-collector/) と同じパターンです。

### 2.2 Collector のローカル起動（Docker）

```bash
# Preferred: keep the key out of shell history / process listings
echo "MACKEREL_APIKEY=YOUR_MACKEREL_API_KEY" > .env.mackerel   # add .env.mackerel to .gitignore
docker run --rm \
  -p 4317:4317 -p 4318:4318 \
  --env-file .env.mackerel \
  -v "$(pwd)/config.yaml:/etc/otelcol-contrib/config.yaml" \
  otel/opentelemetry-collector-contrib:latest
```

> **重要**: 実際の API キーをインラインで記述した `config.yaml` を絶対にコミットしないでください。また、`-e KEY=value` のようにコマンドライン上に直接シークレットを渡すことは避けてください — シェル履歴に残り、共有ホストでは他ユーザーが `ps` で確認できてしまいます。上記のように git 管理外の `--env-file` を使用するか、ECS Fargate で運用する場合はシークレット管理機能（Secrets Manager 連携）を使用してください。

> **コストに関する補足**: Mackerel のログ取り込み自体はオープンβ期間中は無料ですが、Collector の実行（ローカル Docker、または常設運用の ECS Fargate）には Mackerel とは独立した AWS インフラコストが発生します。Lambda / EventBridge / Secrets Manager の典型的なコストは [AWS インフラコスト推定](../../../../docs/ja/vendor-comparison.md#aws-インフラコスト推定) の表を参照してください。ECS Fargate 上の Collector コストは本統合ではまだ見積もっていません。Mackerel のログ機能の正式版料金（インジェスト量課金、70円/GB税抜、2026年秋GA予定）は既に公表されていますが（README「参考資料」の公式ブログを参照）、GA自体がまだ到達していないため、本番運用のコスト見積もりは暫定値として扱ってください。

## Step 3: サンプル OTLP ログペイロードの送信

FSx for ONTAP 監査ログ用の Lambda 配信部分が実装される前でも、サンプル OTLP ログペイロードを使って Collector → Mackerel のパスを単体で検証できます。

### 3.1 サンプルペイロード（`sample-otlp-logs.json`）

```json
{
  "resourceLogs": [
    {
      "resource": {
        "attributes": [
          { "key": "service.name", "value": { "stringValue": "fsxn-audit-poc" } },
          { "key": "service.namespace", "value": { "stringValue": "fsxn" } }
        ]
      },
      "scopeLogs": [
        {
          "logRecords": [
            {
              "timeUnixNano": "1737000000000000000",
              "severityNumber": 9,
              "severityText": "INFO",
              "body": { "stringValue": "sample audit log record for Mackerel beta verification" },
              "attributes": [
                { "key": "operation", "value": { "stringValue": "create" } },
                { "key": "svm", "value": { "stringValue": "svm-prod-01" } }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

> **`severityNumber` が必要な理由**: OTLP Logs Data Model 上は `severityNumber` と `severityText` はいずれもオプションですが、Mackerel の UI 側は数値の `severityNumber`（1〜24、TRACE〜FATAL にマッピング。9〜12 が INFO）でログの重要度を判定します。Mackerel 公式ドキュメントによると、`severityNumber` を省略すると、`severityText` があっても検索結果上は `UNSPECIFIED` として表示されます。このサンプルでは両方を設定し、この問題を回避しています。

### 3.2 ペイロードの送信

```bash
curl -s -X POST "http://localhost:4318/v1/logs" \
  -H "Content-Type: application/json" \
  -d @sample-otlp-logs.json
```

送信に成功すると、ローカル Collector のレシーバーから HTTP 200 と空の `{}` ボディが返ります（これは Collector が受理したことのみを確認するものです。Mackerel への配信確認は Step 4 で行います）。

### 3.3 トラブルシューティング

> **まず Collector 側か Mackerel 側かを切り分ける**: Mackerel がペイロードを拒否していると決めつける前に、Collector 自体がペイロードを受信してキューに入れたかを確認してください。`health_check` extension（公開している場合は `curl http://localhost:13133/`）と Collector 自体の標準出力ログで receiver 側のエラーがないか確認します。Collector がペイロードを受理したことを確認した後で、下記の exporter 側（Mackerel）エラーコードを確認してください — これにより、ローカル Collector の設定ミスを Mackerel 側の拒否と誤診断することを防げます。

| 症状 | 想定される原因 | 対処 |
|------|---------------|------|
| `curl` が `:4318` で connection refused | Collector が起動していない、またはポートマッピングが誤っている | `docker ps` でコンテナが起動しているか、`-p 4318:4318` が指定されているか確認 |
| Collector のログに exporter から `401` または `403` | `Mackerel-Api-Key` が間違っている、未設定、または Write 権限がない | Mackerel の API キー設定でキーを再確認、必要であれば再生成 |
| Collector のログに exporter から `400` | OTLP ペイロードの形式が不正、または `service.namespace` / `service.name` が欠落（Mackerel はこの2つの組でログをグルーピングする） | OTLP Logs Data Model に沿ってペイロードを検証し、`service.namespace` と `service.name` の両方の resource attribute が含まれていることを確認 |
| Collector のログにリクエストサイズエラー | バッチが Mackerel のリクエストサイズ上限を超えている | 上記の `sending_queue.batch.max_size: 3500000`（バイト単位）が設定されているか確認 |
| Collector のログに DNS エラー（例: `dial tcp: lookup otlp-vaxila.mackerelio.com on 127.0.0.11:53: server misbehaving`）。ホスト側では同じホスト名を問題なく解決できている | Docker Desktop の内部 DNS リゾルバ（`127.0.0.11`）がコンテナ内から外部ホスト名の解決に間欠的に失敗する。Mackerel 固有の問題ではなく、設定側の問題でもない | `docker-compose-mackerel.yaml` の `otel-collector` サービスに明示的な `dns:` ブロック（`8.8.8.8`、`1.1.1.1`）を追加（デフォルトで有効化済み）— 詳細は [OTel Collector README → トラブルシューティング → 「Docker Desktop DNS Resolution」](../../../otel-collector/README.md#docker-desktop-dns-resolution-server-misbehaving) を参照 |

## Step 4: Mackerel でのログ確認

1. Mackerel にログインし、サイドバーの **ログ** 項目を開く
2. **ログの検索を開始** をクリック
3. 送信した `service.namespace` / `service.name` の組（例: `fsxn` / `fsxn-audit-poc`）で識別されるサービスを選択
4. サンプルログレコードが、`operation` と `svm` の属性が構造化フィールドとして見える状態で表示されることを確認

以下は実際の E2E 検証時のスクリーンショットです（メールアドレス等の個人情報はマスキング済み）。

![Mackerel ログ検索結果](../../../docs/screenshots/mackerel/mackerel-logs-search-results.png)
*ログ検索結果: fsxn-audit サービスに送信された FSx for ONTAP 監査ログが表示されている。スクリーンショット中の IP アドレス（203.0.113.x）は RFC 5737 TEST-NET-3 のドキュメント用アドレスであり、実在する環境のアドレスではない*

![Mackerel ログ詳細](../../../docs/screenshots/mackerel/mackerel-logs-detail.png)
*ログ詳細: 操作種別、結果、SVM名、ファイルパス、ユーザー情報、クライアントIP、タイムスタンプなどの属性が検索可能な状態で保持されている*

> **ヒント**: 数分待っても表示されない場合は、まず Step 3.3 のトラブルシューティング表を再確認してください。多くの失敗は Mackerel 自体ではなく、Collector → Mackerel 間のホップで発生します。

> **検証時の注意点 — サンプルペイロードのタイムスタンプが古い場合**: Step 3.1 のサンプルペイロードは `timeUnixNano` が固定値です。このペイロードを保存して後日（例: 翌日）再送信すると、タイムスタンプが Mackerel のデフォルトのログ検索期間の範囲外になり、実際には送信が成功していても「届いていない」ように見えることがあります。検索 UI に依存せず配信を確認するには、Collector 自身のエクスポートメトリクスを確認してください（`curl http://localhost:8888/metrics | grep otelcol_exporter_sent_log_records` — このエンドポイントの有効化方法は `otel-collector-config-mackerel.yaml` の `telemetry` ブロックを参照）。または Mackerel の UI 側で検索期間をペイロードの実際のタイムスタンプを含む範囲まで広げてください。固定サンプルの代わりに `scripts/generate-otlp-payload.sh` で生成したペイロードを使う場合、常に現在時刻のタイムスタンプが生成されるため、この問題は発生しません。

## Step 5: 直接送信の代替手段（Collector を経由しない）

上記 Step 1〜4 はログをローカルの OpenTelemetry Collector 経由でルーティングします。Collector を経由せず、本リポジトリの FSx for ONTAP 監査ログ / EMS / FPolicy Lambda から Mackerel の OTLP エンドポイントへ直接送信したい場合は、[OTel Collector 統合](../../../otel-collector/) の Lambda ハンドラーが汎用のカスタムヘッダー認証モードでこれをサポートしています。

Mackerel の `Mackerel-Api-Key` ヘッダーは、既存の `bearer`/`basic` 認証モード（`Authorization: Bearer <token>` または `Authorization: Basic <base64>` しか生成できない）では表現できなかったため、汎用の `AUTH_MODE=header` オプションを追加しました — これは Mackerel 専用ではなく、独自ヘッダー名が必要な任意のベンダーで同様に使用できます。

> **重要 — Protobuf が必須**: Mackerel の OTLP エンドポイントは Protobuf 形式のリクエストボディのみを受け付け、OTLP/JSON を `{"code":400,"message":"json is not supported yet"}` という HTTP 400 で拒否します。Lambda 直接送信経路はデフォルトで OTLP/JSON を送信するため、Mackerel には `OTLP_CONTENT_TYPE=protobuf`（CloudFormation では `OtlpContentType=protobuf`）が**必須**です — これを指定しないと、認証が正しくても送信は失敗します。Collector 経由の経路（Step 1〜4）には影響しません。OTel Collector はデフォルトで既に Protobuf を送信しているためです。

### 5.1 必要な環境変数 / CloudFormation パラメータ

| Lambda 環境変数 | `template.yaml` パラメータ | Mackerel 向けの値 |
|-----------------|---------------------------|-------------------|
| `OTLP_ENDPOINT` | `OtlpEndpoint` | `https://otlp-vaxila.mackerelio.com` |
| `AUTH_MODE` | `AuthMode` | `header` |
| `AUTH_HEADER_NAME` | `AuthHeaderName` | `Mackerel-Api-Key` |
| `EXTRA_HEADERS_JSON` | `ExtraHeadersJson` | `{"Accept":"*/*"}` |
| `OTLP_CONTENT_TYPE` | `OtlpContentType` | `protobuf`（**必須** — 上記の重要な注意を参照） |
| `API_KEY_SECRET_ARN` | `ApiKeySecretArn` | 上記 [Step 1.2](#12-aws-secrets-manager-への保存) で作成したシークレットの ARN |

### 5.2 CloudFormation デプロイ例

```bash
aws cloudformation deploy \
  --template-file integrations/otel-collector/template.yaml \
  --stack-name fsxn-otel-integration \
  --parameter-overrides \
    S3BucketName=your-fsxn-audit-log-bucket \
    OtlpEndpoint=https://otlp-vaxila.mackerelio.com \
    AuthMode=header \
    AuthHeaderName=Mackerel-Api-Key \
    ExtraHeadersJson='{"Accept":"*/*"}' \
    OtlpContentType=protobuf \
    ApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:mackerel/fsxn-log-credentials-AbCdEf \
    LambdaCodeS3Bucket=my-lambda-code-bucket \
    LambdaCodeS3Key=otel-collector/lambda.zip \
  --capabilities CAPABILITY_NAMED_IAM
```

> **注意**: このデプロイは共有の `otel-collector/template.yaml` から3つの Lambda 関数（監査ログ、EMS Webhook、FPolicy）すべてを作成します — 独立した `integrations/mackerel/template.yaml` は存在しません。パラメータの詳細は [OTel Collector README](../../../otel-collector/README.md#alternative-mackerel-backend-open-beta) を参照してください。

### 5.3 Collector 経由 vs. 直接送信: どちらを使うか

| | Collector 経由（Step 1〜4） | 直接送信（本 Step） |
|---|---|---|
| Lambda コード変更 | 不要 | 不要（既存の汎用 `header` 認証モードを使用） |
| 追加インフラ | Collector（Docker/ECS Fargate） | なし |
| マルチバックエンドへのファンアウト | 可能（1つの Collector 設定に exporter を追加） | 不可（Lambda デプロイごとに1エンドポイント） |
| ベンダーに送る前のバッファリング/リトライ | Collector 側（Lambda 自身のリトライに加えて） | Lambda 自身のリトライのみ |
| 推奨されるケース | 本番運用、マルチベンダー配信 | 迅速な検証、単一バックエンド、コストを抑えたい構成 |

本リポジトリの [OTel Collector README](../../../otel-collector/README.md) は、一般的に本番運用では Collector 経由の経路を推奨しています。Mackerel のログ機能のようなβ版機能の場合、データ保持の保証がまだない機能をテストするためだけに Collector インフラを立てるコストを避けられるという点で、初回検証には直接送信の経路が向いている場合があります。

## 本番利用前に周知すべきβ版の制約

Mackerel公式のβ版公開告知（2026年7月16日）による内容:

- β期間中のデータ保持は保証されない
- 臨時メンテナンスが予告なく発生する可能性がある
- 保存期間（β版・正式版とも）は30日間を予定
- 正式リリースは2026年秋頃を予定。料金体系（インジェスト量課金、70円/GB税抜）は既に公表済みだが、正式な GA 日付自体は未確定

この Collector 設定を将来 FSx for ONTAP 監査ログパイプラインに接続し、本番のアラート用途（ランサムウェア検知等）に使う場合は、このβ版という位置づけをアラートの利用者に明示してください。また、本統合は本リポジトリの他9ベンダー（正式版で検証済み）の**代替ではなく、多層防御の一環として併用する**ものとして扱ってください。

## 次のステップ

- 実装進捗は [../../README.md#実装ステータス](../../README.md#実装ステータス) で追跡
- 両方の配信経路（Collector 経由は `bash integrations/otel-collector/scripts/test-local-mackerel.sh`、直接送信は上記 Step 5.2 の CloudFormation デプロイに `OtlpContentType=protobuf` を指定）について、実際の Mackerel オーガニゼーションへの E2E 送信を確認済み（2026年7月18日）
- Mackerel のログ機能が正式版に到達し、データ保持の SLA が明示された段階で、「検証中・β版ベンダー」から `docs/en/vendor-comparison.md` / `docs/ja/vendor-comparison.md` の「対応ベンダー一覧」メイン表へ移行する
