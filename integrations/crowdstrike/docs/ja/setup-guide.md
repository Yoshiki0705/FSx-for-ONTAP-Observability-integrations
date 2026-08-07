# CrowdStrike Falcon LogScale セットアップガイド

🌐 **日本語**（このページ） | [English](../en/setup-guide.md)

## 前提条件

- CrowdStrike Falcon LogScale アカウント (Cloud or Self-hosted)
- FSx 監査ログ用の LogScale リポジトリ
- リポジトリに紐付けた Ingest Token
- AWS アカウント + FSx for ONTAP (監査ログ有効化済み)
- S3 Access Point 設定済み

## Step 1: LogScale リポジトリの作成

1. LogScale にログイン
2. **Repositories** → **New Repository**
3. 名前: `fsxn-audit`
4. 保持期間: コンプライアンス要件に応じて設定

## Step 2: Ingest Token の作成

1. リポジトリ → **Settings** → **Ingest tokens**
2. **Add token** をクリック
3. 名前: `fsxn-lambda-shipper`
4. Parser: `json`（推奨）
5. トークン値をコピー

## Step 3: AWS Secrets Manager にトークン保存

```bash
aws secretsmanager create-secret \
  --name crowdstrike/fsxn-logscale-token \
  --secret-string "<your-ingest-token>" \
  --region ap-northeast-1
```

## Step 4: CloudFormation スタックのデプロイ

### 推奨: デプロイスクリプトを使う

このスクリプトはスタックのデプロイと実 Lambda コードのアップロードを**両方**行います。
CloudFormation テンプレートはハンドラをインラインに持てないため、1 ステップで動作する
統合を得られる唯一の経路です。

```bash
export FSX_S3_ACCESS_POINT_ARN="..."
export LOGSCALE_INGEST_TOKEN_SECRET_ARN="..."

bash integrations/crowdstrike/scripts/deploy.sh
```

初回は **3〜5 分**かかり、そのほとんどは CloudFormation が IAM ロール・Lambda・
スケジューラ・アラームを作成する時間です。変更のないスタックへの再実行は数秒で
終わります。対応する変数の一覧は `--help` で確認できます。

このベンダーには EMS / FPolicy ハンドラがまだ無いため、`--all` を付けても監査ログ
経路のみがデプロイされ、他の 2 経路はスキップとして報告されます。対応状況は
[テレメトリ経路のカバレッジ](../../../../docs/ja/README.md#テレメトリ経路のカバレッジ)
を参照してください。

> スクリプト実行前に `ALARM_TOPIC_ARN` に SNS トピック ARN を設定すると、CloudWatch
> アラームが通知されるようになります。未設定の場合アラームは通知アクションなしで
> 作成され、コンソールには表示されますが誰にも通知されません。

### 代替: CloudFormation を手動でデプロイする

```bash
aws cloudformation deploy \
  --template-file integrations/crowdstrike/template.yaml \
  --stack-name fsxn-crowdstrike-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    LogScaleIngestTokenSecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:crowdstrike/fsxn-logscale-token \
    LogScaleUrl=https://cloud.us.humio.com \
  --capabilities CAPABILITY_NAMED_IAM
```

### 実 Lambda コードのアップロード（必須）

**スタックだけでは動作しません。** CloudFormation はこの規模のハンドラをインラインに
書けないため、`template.yaml` は `NotImplementedError` を投げるプレースホルダを
持っています。`scripts/deploy.sh` を使った場合はこの手順は済んでいます。手動で
デプロイした場合は、ここで実行してください:

```bash
cd integrations/crowdstrike/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-crowdstrike-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-crowdstrike-integration-shipper \
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
| `FsxS3AccessPointArn` | ARN of the S3 Access Point for FSx for ONTAP audit logs |
| `LogScaleIngestTokenSecretArn` | ARN of the Secrets Manager secret containing the LogScale Ingest Token |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LogScaleUrl` | `https://cloud.us.humio.com` | LogScale base URL (e.g., https://cloud.us.humio.com) |
| `ScheduleInterval` | `rate(5 minutes)` | EventBridge Scheduler interval for audit log polling |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `HecPath` | `/api/v1/ingest/hec` | HEC endpoint path (LogScale default /api/v1/ingest/hec, Splunk /services/collector/event) |
| `AuditLogPrefix` | `audit/` | Key prefix scanned within the FSx for ONTAP S3 Access Point (e.g. audit/ for the /audit_log directory) |
| `MaxKeysPerRun` | `100` | Maximum audit log files processed per scheduled invocation. Bounds the work per run so a large backlog drains over several runs instead of timing out mid-file; the remainder is picked up on the next schedule. |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when any alarm in this stack fires. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 5: 動作確認

```bash
# Lambda ログ確認
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-crowdstrike-integration-shipper \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))") \
  --region ap-northeast-1

# DLQ が空であることを確認
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessages
```

LogScale で検索:
```
source = "fsxn-ontap"
```

## LogScale パーサー（任意）

より細かいフィールド抽出が必要な場合は、LogScale でカスタムパーサーを作成します:

```
parseJson()
| rename(field=event_type, as=EventID)
| rename(field=client_ip, as=ClientIP)
```

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| HTTP 401 | Ingest Token が無効 | Secrets Manager のトークンが LogScale と一致するか確認 |
| HTTP 403 | トークンに権限なし | トークンが正しいリポジトリに紐付いているか確認 |
| LogScale にログなし | URL またはパーサーの問題 | LogScale URL がアカウントのリージョンと一致するか確認 |
| Lambda タイムアウト | ネットワーク問題 | Lambda にインターネットアクセスがあるか確認（NAT GW or VPC 外） |

## 参考リンク

- [LogScale Ingest API](https://library.humio.com/logscale-api/api-ingest.html)
- [LogScale HEC エンドポイント](https://library.humio.com/logscale-api/log-shippers-hec.html)
- [CrowdStrike Developer Center](https://developer.crowdstrike.com/ngsiem/data-ingestion/)

## デプロイの検証

```bash
bash integrations/crowdstrike/scripts/verify.sh
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
bash integrations/crowdstrike/scripts/cleanup.sh          # スタックのみ
bash integrations/crowdstrike/scripts/cleanup.sh --all    # + シークレット・レイヤー・S3 テストデータ
bash integrations/crowdstrike/scripts/cleanup.sh --all -y  # 非対話
```

共有リソース（S3 アクセスポイント、監査ログバケット、FPolicy Fargate スタック、
前提スタック）は削除されません。削除順と意図的に残すものについては
[ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md)
を参照してください。

## 関連ドキュメント

- [ベンダー統合のデプロイ](../../../../docs/ja/vendor-deployment-common.md) — 全ベンダー共通の手順
- [前提条件](../../../../docs/ja/prerequisites.md) — FSx for ONTAP、監査ログ有効化、S3 アクセスポイント
- [デプロイガイド](../../../../docs/ja/deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
