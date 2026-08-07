# Elastic セットアップガイド

🌐 [English](../en/setup-guide.md)

## 概要

FSx for ONTAP 監査ログを Elasticsearch Bulk API で配信し、Kibana で可視化するセットアップ手順です。

## 前提条件

- Elastic Cloud または自己ホスト Elasticsearch クラスタ
- [前提リソース](../../../../docs/ja/prerequisites.md)デプロイ済み

## Step 1: Elasticsearch API Key の作成

```bash
# Elastic Cloud: Kibana -> Stack Management -> API Keys -> Create
aws secretsmanager create-secret \
  --name "elastic/fsxn-api-key" \
  --secret-string '{"api_key":"YOUR_ENCODED_API_KEY"}' \
  --region ap-northeast-1
```

## Step 2: CloudFormation デプロイ

### 推奨: デプロイスクリプトを使う

このスクリプトはスタックのデプロイと実 Lambda コードのアップロードを**両方**行います。
CloudFormation テンプレートはハンドラをインラインに持てないため、1 ステップで動作する
統合を得られる唯一の経路です。

```bash
export ELASTIC_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export S3_BUCKET_NAME="..."
export ELASTIC_ENDPOINT="..."

bash integrations/elastic/scripts/deploy.sh
```

初回は **3〜5 分**かかり、そのほとんどは CloudFormation が IAM ロール・Lambda・
スケジューラ・アラームを作成する時間です。変更のないスタックへの再実行は数秒で
終わります。対応する変数の一覧は `--help` で確認できます。

`--all` を付けると EMS / FPolicy スタックも対象になります。`FPOLICY_SQS_QUEUE_ARN`
に `shared/templates/fpolicy-apigw.yaml` の ingestion queue ARN を設定すると
FPolicy の主経路（Fargate → SQS → Lambda）が有効になります。未設定の場合、FPolicy
スタックは副経路の EventBridge ルールのみを使います。対応状況は
[テレメトリ経路のカバレッジ](../../../../docs/ja/README.md#テレメトリ経路のカバレッジ)
を参照してください。

> スクリプト実行前に `ALARM_TOPIC_ARN` に SNS トピック ARN を設定すると、CloudWatch
> アラームが通知されるようになります。未設定の場合アラームは通知アクションなしで
> 作成され、コンソールには表示されますが誰にも通知されません。

### 代替: CloudFormation を手動でデプロイする

```bash
aws cloudformation deploy \
  --template-file integrations/elastic/template.yaml \
  --stack-name fsxn-elastic-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    ElasticApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:elastic/fsxn-api-key-XXXXX \
    ElasticEndpoint=https://my-cluster.es.ap-northeast-1.aws.found.io:9243 \
    S3BucketName=$BUCKET_NAME \
    IndexPrefix=fsxn-audit \
  --capabilities CAPABILITY_IAM
```

### 実 Lambda コードのアップロード（必須）

**スタックだけでは動作しません。** CloudFormation はこの規模のハンドラをインラインに
書けないため、`template.yaml` は `NotImplementedError` を投げるプレースホルダを
持っています。`scripts/deploy.sh` を使った場合はこの手順は済んでいます。手動で
デプロイした場合は、ここで実行してください:

```bash
cd integrations/elastic/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-elastic-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-elastic-integration-shipper \
  --region ap-northeast-1
```

`-j` はパスを平坦化し、実行時に `ontap_audit_parser` が解決できるようにします。この
ファイルを同梱しないとハンドラは JSON 専用の解析に静かにフォールバックし、ONTAP の
監査ログ（常に XML か EVTX）はフィールド解析されずに配送されます。

### パラメータリファレンス

<!-- generated from template.yaml; keep in sync when parameters change -->

Required:

| Parameter | Description |
|-----------|-------------|
| `S3AccessPointArn` | FSx for ONTAP S3 Access Point ARN (attached to the audit volume) |
| `ElasticApiKeySecretArn` | Secrets Manager ARN for the Elasticsearch API key |
| `ElasticEndpoint` | Elasticsearch cluster endpoint URL (https://...) |
| `S3BucketName` | S3 bucket name for EventBridge rule matching |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `S3KeyPrefix` | `''` (empty) | S3 key prefix filter for audit log objects |
| `IndexPrefix` | `fsxn-audit` | Elasticsearch index name prefix. The handler appends a UTC date suffix, producing e.g. fsxn-audit-2026.08.07 |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 3: Kibana 設定

### Index Pattern 作成
1. Kibana → **Stack Management** → **Index Patterns**
2. Pattern: `fsxn-audit-*`
3. Time field: `@timestamp`

### Discover で確認
- フィルタ: `fsxn.operation: ReadData`
- 時間範囲: Last 1 hour

### ダッシュボード例
- 操作別円グラフ: `fsxn.operation.keyword`
- ユーザー別棒グラフ: `user.name.keyword`
- 失敗アクセスタイムライン: `fsxn.result: Failure`

## インデックス管理

日次インデックス `fsxn-audit-YYYY.MM.DD` が自動作成されます。ILM (Index Lifecycle Management) で自動削除を設定:

```json
PUT _ilm/policy/fsxn-audit-policy
{
  "policy": {
    "phases": {
      "hot": {"actions": {"rollover": {"max_age": "30d"}}},
      "delete": {"min_age": "90d", "actions": {"delete": {}}}
    }
  }
}
```

## フォレンジック調査 (Kibana Discover/Lens)

> 🔍 ユーザー/IP/パス中心の調査ワークフロー（誰が、どこから、何にアクセスし、何をしたか — DII Storage Workload Security の Forensics ダッシュボードに類似）が必要な場合、[正規化イベントスキーマ](../../../../docs/en/normalized-event-schema.md) で ONTAP audit / FPolicy フィールドは既に ECS（`user.name`、`source.ip`、`file.path`、`event.action`）へマッピングされているため、カスタムマッピングは不要です。Kibana で以下を構築してください:

### 保存検索 (KQL)

| 調査ビュー | KQL クエリ | DII SWS の対応ビュー |
|-----------|-----------|----------------------|
| User Overview | `user.name: "<value>"` | Forensic User Overview |
| All Activity | `event.dataset: "fsxn"`（フィルタなし、`@timestamp` 降順） | Forensics - All Activity |
| IP 中心ドリルダウン | `source.ip: "<value>"` | Forensic User Activity Data |
| エンティティ/ファイル履歴 | `file.path: "<value>"` | Forensic Entities Page |

それぞれを分かりやすい名前（例: `fsxn-forensics-user-overview`）で Kibana の **Saved Search** として保存すれば、調査担当者はクエリを作り直さずに Discover から適切なビューを選択できます。

### Lens ビジュアライゼーション

現在フィルタ中の保存検索に対して `event.action`（操作種別）を集計する **Lens** バーチャートを追加してください — DII SWS の Forensics ダッシュボードがユーザー/エンティティごとのアクション分布を表示するのと同じ方法で、異常なアクションの偏り（例: 削除操作の急増）を可視化できます。

### エクスポート

Discover の **Share → CSV Reports**（新しい Kibana では **Generate CSV**）で、選択した時間範囲に絞った現在のフィルタビューをエクスポートできます — DII SWS の 31 日フィルタ付き CSV エクスポート相当ですが、31 日固定の上限はありません（保持期間は上記 ILM ポリシーで管理されます）。

この実装が対応する CSF 2.0 機能全体のカバレッジ、および既知のデータソース上の注意点（FPolicy と audit log のカバレッジ差異、[データ分類ガイド](../../../../docs/en/data-classification.md) 経由の PII 取り扱い）については [サイバーレジリエンス機能マップ](../../../../docs/ja/cyber-resilience-capability-map.md#respond対応) を参照してください。

## デプロイの検証

```bash
bash integrations/elastic/scripts/verify.sh
```

2 層が順に実行されます。まず共有の AWS 側チェック（スタックが正常か、デプロイ済み
Lambda がプレースホルダではなく実ハンドラか、スタックが作成する場合はスケジュールと
チェックポイントが揃っているか）。次にベンダーのエンドポイントへ合成ログを送り、
資格情報とネットワーク到達性を確認します。

どちらも必要です。ベンダーのエンドポイントがテストログを受理しても、パイプラインが
動いている保証にはなりません。そのため AWS 側が失敗した場合は、エンドポイントが
正常応答していてもスクリプトは非ゼロで終了します。

終了コードは `sysexits.h` 準拠で `0` 成功、`69` チェック失敗、`78` 必要な設定が
不足。デプロイ前にベンダー疎通だけ試す場合は `SKIP_AWS_CHECKS=1` を設定します。

## クリーンアップ

```bash
bash integrations/elastic/scripts/cleanup.sh          # stacks only
bash integrations/elastic/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/elastic/scripts/cleanup.sh --all -y  # non-interactive
```

共有リソース（S3 アクセスポイント、監査ログバケット、FPolicy Fargate スタック、
前提スタック）は削除されません。削除順と意図的に残すものについては
[ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md)
を参照してください。

## 関連ドキュメント

- [ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md) — 全ベンダー共通の手順
- [前提条件](../../../../docs/ja/prerequisites.md) — FSx for ONTAP、監査ログ有効化、S3 アクセスポイント
- [デプロイガイド](../../../../docs/ja/deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
