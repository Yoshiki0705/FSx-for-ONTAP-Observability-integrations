# 運用ガイド

🌐 **日本語**（このページ） | [English](../en/operational-guide.md)

## 概要

FSx for ONTAP Observability パイプラインの日常運用（監視、トラブルシューティング、メンテナンス）をカバーするガイドです。

## 監視

### 主要 CloudWatch メトリクス

| メトリクス | アラーム閾値 | アクション |
|-----------|-------------|----------|
| Lambda Errors | 10分間で5回超 | CloudWatch Logs でエラー詳細を確認 |
| Lambda Throttles | 1回以上 | 同時実行数の引き上げまたはスケジュール間隔の延長 |
| DLQ Messages Visible | 1件以上 | 失敗イベントを調査し、修正後にリプレイ |
| Lambda Duration | タイムアウトの80%超 | タイムアウト延長またはバッチサイズ最適化 |

### 運用ヘルスダッシュボード

パイプラインの健全性を監視するメトリクス:
- Lambda エラー率と実行時間
- チェックポイントラグ（最後に処理したファイルからの経過時間）
- DLQ 深度
- ベンダー API レスポンスコード（Lambda ログ経由）
- 1回の起動あたりの配信ログ数

## DLQ リプレイ手順

イベント処理が失敗し DLQ に到達した場合:

このスタックは Lambda 非同期呼び出しの DLQ として SQS キューを使用します。DLQ は Lambda にアタッチされているため（SQS ソースキューではない）、`sqs start-message-move-task` による自動リドライブはできません。

```bash
# 1. Check DLQ message count
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessages

# 2. Inspect a sample message
aws sqs receive-message \
  --queue-url <dlq-url> \
  --max-number-of-messages 1 \
  --attribute-names All \
  --message-attribute-names All

# 3. After fixing the root cause, re-invoke Lambda manually
aws lambda invoke \
  --function-name <lambda-function-name> \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  --region ap-northeast-1 \
  replay-output.json

# 4. Delete processed DLQ messages
aws sqs delete-message \
  --queue-url <dlq-url> \
  --receipt-handle <receipt-handle-from-step-2>
```

## チェックポイント管理

パイプラインはチェックポイントを使用して処理済み監査ログファイルを追跡します。

### チェックポイントリセット（全ファイル再処理）

```bash
# Delete checkpoint entries for a specific SVM
aws dynamodb delete-item \
  --table-name fsxn-observability-audit-checkpoint \
  --key '{"svm_name": {"S": "svm-prod-01"}, "file_key": {"S": "LATEST"}}'
```

### 特定ファイルのリプレイ

```bash
# Invoke Lambda with a specific file key
aws lambda invoke \
  --function-name fsxn-datadog-integration-shipper \
  --payload '{"Records":[{"s3":{"bucket":{"name":"<fsx-s3-ap-arn>"},"object":{"key":"audit/svm-prod-01/audit_2026.evtx"}}}]}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response.json
```

## S3 Access Point ヘルス監視

FSx for ONTAP S3 Access Points は以下の場合に `MISCONFIGURED` 状態になり得ます:
- 関連付けられたファイルシステム ID が解決できない場合
- アタッチされたボリュームがオフラインまたはアンマウントされた場合

FSx は根本的な問題が修正されると自動的にアクセスポイントを復元します。アクセスポイントの状態を定期的に監視してください:

```bash
# Check S3 Access Point state
aws fsx describe-data-repository-associations \
  --region ap-northeast-1 \
  --query 'Associations[*].[ResourceARN,Lifecycle]' \
  --output table
```

アクセスポイントが MISCONFIGURED の場合、Lambda 起動は AccessDenied またはタイムアウトエラーで失敗します。確認事項:
1. ボリュームがオンラインでマウントされているか
2. ファイルシステム ID (UNIX/Windows ユーザー) が解決可能か
3. SVM が稼働中か

## Secrets Manager ローテーション

API キーは定期的にローテーションすべきです:

```bash
# Update the secret value
aws secretsmanager put-secret-value \
  --secret-id <secret-arn> \
  --secret-string '{"api_key": "<new-key>"}'
```

Lambda は実行コンテキストごとに API キーをキャッシュします。ローテーション後、新しいキーは次のコールドスタート時（通常数分以内）に取得されます。

## コスト最適化

### 主要コスト変数

| コンポーネント | コストドライバー | 最適化 |
|-------------|---------------|--------|
| Lambda | 起動回数 × 実行時間 | 低ボリューム SVM ではスケジュール間隔を延長 |
| NAT Gateway | $0.045/時 + $0.045/GB | 可能なら Lambda を VPC 外に配置 |
| EventBridge Scheduler | 100万起動あたり$1 | 最小コスト |
| DynamoDB (checkpoint) | リクエスト課金 | 最小コスト |
| ベンダー取り込み | GB/イベント単位 | 監査ポリシーレベルで不要イベントをフィルタ |

### コスト削減のヒント

1. **read auditing を無効化** — 特に必要でない限り（最もボリュームが多い）
2. **Lambda を VPC 外に配置** — NAT Gateway コストを回避（S3 AP がインターネットアクセス可能な場合）
3. **スケジュール間隔を延長** — 低アクティビティ SVM の場合（例: `rate(15 minutes)`）
4. **ソースでフィルタ** — ONTAP 監査ポリシーで必要なイベントのみをキャプチャ

## マルチアカウントデプロイ

このパターンは2つのデプロイモデルをサポートします:

### アカウント単位（分散型）
- 各ワークロードアカウントが独自のスタックをデプロイ
- 監査ログはアカウント境界内に留まる
- シンプルな IAM、クロスアカウントアクセス不要

### 集約型（ロギングアカウント）
- 全監査ログを専用のロギング/セキュリティアカウントで処理
- クロスアカウント S3 Access Point アクセスが必要
- セキュリティ監視の集約に適している

## アップグレード戦略

デプロイスクリプトは両方のステップを実行し、新しいコードが有効になるまで待機します。
アップグレードではこの挙動が望ましいです。

```bash
# Stack + code, zero-downtime
export DATADOG_API_KEY_SECRET_ARN=<secret-arn>
export FSX_S3_ACCESS_POINT_ARN=<ap-arn>
bash integrations/datadog/scripts/deploy.sh

# Handler change only — leaves the stack untouched
bash integrations/datadog/scripts/deploy.sh --code-only

# Confirm the upgrade landed
bash integrations/datadog/scripts/verify.sh
```

手動で行う場合の等価な手順:

```bash
# Update the CloudFormation stack (zero-downtime)
aws cloudformation deploy \
  --template-file integrations/datadog/template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides <same-params> \
  --capabilities CAPABILITY_NAMED_IAM

# Update Lambda code separately
cd integrations/datadog/lambda
zip function.zip handler.py
aws lambda update-function-code \
  --function-name fsxn-datadog-integration-shipper \
  --zip-file fileb://function.zip
aws lambda wait function-updated \
  --function-name fsxn-datadog-integration-shipper
```

> **チェックポイントはアップグレードで維持されます。** Lambda の外側の SSM Parameter Store に
> 保存されるため、プレフィックス全体を再送するのではなく最後に処理したキーから再開します。
> パース挙動を変更するアップグレードで既配送ファイルを再処理したい場合は、意図的に
> チェックポイントを `__INIT__` にリセットしてください。既に届いている分は重複します。

## セキュリティレビューチェックリスト

- [ ] IAM ロールが最小権限に従っている（S3 AP ARN のみ、特定の Secret ARN のみ）
- [ ] S3 Access Point ポリシーが Lambda 実行ロールにのみアクセスを制限
- [ ] Secrets Manager シークレットが KMS CMK で暗号化されている
- [ ] DLQ が KMS で暗号化されている
- [ ] Lambda がパブリックサブネットに配置されていない
- [ ] CloudWatch Logs の保持期間が設定されている
- [ ] Lambda 環境変数にシークレットがない（ARN 参照のみ）
- [ ] VPC セキュリティグループが最小限のアウトバウンドのみ許可


## ソースヘルスチェック

パイプラインヘルス（Lambda、DLQ、checkpoint）に加えて、監査ソース自体も監視してください:

| チェック項目 | 方法 | 異常指標 |
|-------|-----|-------------------|
| ONTAP audit 有効 | `vserver audit show -vserver <svm> -fields state` | state != enabled |
| ローテーションファイル存在 | S3 AP 経由 `list-objects-v2` | 想定間隔内に新規ファイルなし |
| Audit volume 容量 | `volume show -vserver <svm> -volume <audit-vol> -fields used` | 80%超使用 |
| S3 AP 利用可能 | `aws fsx describe-data-repository-associations` | MISCONFIGURED 状態 |
| 最終処理ファイル経過時間 | checkpoint タイムスタンプ確認 | 古い（Scheduler 間隔の2倍超） |

### パイプライン停滞検知

想定より長く新しい監査ファイルが出現しない場合（例: rotation 間隔の2倍超）、以下を調査:

1. `vserver audit` はまだ有効か？
2. 対象ディレクトリの SACL / NFSv4 ACL audit flags はまだ設定されているか？
3. Audit volume は満杯か？
4. FSx S3 Access Point は MISCONFIGURED 状態か？
5. ファイルシステム ID が解決不能になっていないか？


## EMS パイプラインヘルスチェック

イベント駆動型 EMS Webhook パス（Part 3）については、以下を監視してください:

| チェック項目 | 方法 | 異常指標 |
|-------|-----|-------------------|
| API Gateway 4xx/5xx | CloudWatch API Gateway メトリクス | エラーレスポンスの急増 |
| Lambda エラー | CloudWatch Lambda Errors メトリクス | > 0 |
| EMS イベント配信数 | Lambda ログ（shipped count） | イベントが期待される時に 0 |
| Datadog API 失敗 | Lambda ログ（batch failures） | RuntimeError 発生 |
| DLQ 深度 | SQS ApproximateNumberOfMessagesVisible | > 0 |
| 最終 Webhook 受信 | API Gateway アクセスログ | 想定期間内にリクエストなし |

> 注: EMS イベントが存在しないことは多くの場合正常です（ARP アラートなし = 良好）。パイプラインが稼働していることを積極的に確認する必要がある場合は、合成ハートビートイベントまたは定期的なテスト呼び出しを使用してください。

