# EMS / FPolicy セットアップ（Datadog）

🌐 **日本語** (このページ) | [English](../en/ems-fpolicy-setup.md)

## 概要

[セットアップガイド](setup-guide.md)の監査ログ経路は、ONTAP が監査ステージングファイルを
ローテーションするまで読み取れないため、イベント配送に分単位の時間がかかります。
このレイテンシギャップを埋めるのが以下の 2 つのソースです。

| ソース | レイテンシ | 内容 | トリガー |
|--------|-----------|------|---------|
| 監査ログ | 分単位 | ローテーション後の全監査対象ファイル操作 | EventBridge Scheduler |
| **EMS Webhook** | 秒単位 | ONTAP システムイベント（ARP/ランサムウェア、クォータ、フェイルオーバー） | ONTAP → API Gateway |
| **FPolicy** | サブ秒 | ファイル操作をリアルタイムに | ONTAP → Fargate → SQS |

どちらもオプションです。ランサムウェアやシステムアラートを速く受け取りたい場合は EMS を、
ローテーション遅延なしで操作単位の可視性が必要な場合は FPolicy をデプロイします。

Datadog は両方の Lambda を**1 つの**スタック
（`template-ems-fpolicy.yaml`、スタック名 `fsxn-datadog-ems-fpolicy`）でデプロイします。
本リポジトリの他ベンダーは 2 つの別スタックを使います。この違いはクリーンアップ時に影響しますが、
`scripts/cleanup.sh` が差分を吸収します。

## アーキテクチャ

```
EMS:      ONTAP EMS ──HTTPS──→ API Gateway ──→ Lambda (-ems)     ──→ Datadog
                               (+ Lambda Authorizer)

FPolicy:  ONTAP ──TCP 9898──→ ECS Fargate ──→ SQS ──→ Lambda (-fpolicy) ──→ Datadog
                              (バイナリプロトコル)      (ReportBatchItemFailures)
```

FPolicy は HTTP ではなく TCP 上の独自バイナリプロトコルを使うため、API Gateway ではなく
Fargate サーバーを前段に置いています。ONTAP は Fargate タスク IP に直接接続するため、
**タスク再起動のたびに ONTAP へタスク IP を再登録する必要があります**。

## 前提条件

- 監査ログスタックがデプロイ・検証済み（[セットアップガイド](setup-guide.md)）
- Datadog API キーが Secrets Manager にある（監査経路と同じシークレット）
- FPolicy の場合: 共有 FPolicy インフラがデプロイ済み。どちらのテンプレートでも動作し、
  いずれも ECS Fargate サービス、取り込み用 SQS キューとその DLQ を提供します:
  - `shared/templates/fpolicy-apigw.yaml` — Fargate / EC2 選択可、カスタム EventBridge バス
  - `shared/templates/fpolicy-server-fargate.yaml` — Fargate 専用、より単純
- EMS の場合: EMS パーサ Lambda Layer（任意）。無い場合はハンドラが組み込みのスタブパーサに
  フォールバックし、抽出フィールドが少なくなります

### 収集する値

| 値 | 取得元 |
|----|-------|
| `FPolicySqsQueueArn` | `fsxn-fp-srv` スタック出力 — `IngestionQueueArn`（fpolicy-apigw.yaml）または `FPolicyQueueArn`（fpolicy-server-fargate.yaml） |
| `EventBridgeBusName` | カスタムバス使用時は `fsxn-fpolicy-events`、それ以外は `default` |
| `EmsParserLayerArn` | EMS パーサ Layer ビルドの出力（使用する場合） |

## Step 1: EMS + FPolicy スタックのデプロイ

```bash
export DATADOG_API_KEY_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX"
export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
# キュー ARN は手書きせず共有 FPolicy スタックの出力から取得する
# 出力キー: IngestionQueueArn（fpolicy-apigw.yaml）または
#           FPolicyQueueArn  （fpolicy-server-fargate.yaml）
export FPOLICY_SQS_QUEUE_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-fp-srv --region ap-northeast-1 \
  --query "Stacks[0].Outputs[?OutputKey=='IngestionQueueArn'].OutputValue" \
  --output text)
export EMS_PARSER_LAYER_ARN="arn:aws:lambda:ap-northeast-1:123456789012:layer:fsxn-ems-parser:3"

bash integrations/datadog/scripts/deploy.sh --all
```

これで両スタックがデプロイされ、3 つのハンドラすべてがアップロードされます。
このスタックだけを手動でデプロイする場合:

```bash
aws cloudformation deploy \
  --template-file integrations/datadog/template-ems-fpolicy.yaml \
  --stack-name fsxn-datadog-ems-fpolicy \
  --parameter-overrides \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX \
    DatadogSite=ap1.datadoghq.com \
    FPolicySqsQueueArn=arn:aws:sqs:ap-northeast-1:123456789012:fsxn-fp-srv-fpolicy-ingestion \
    EmsParserLayerArn=arn:aws:lambda:ap-northeast-1:123456789012:layer:fsxn-ems-parser:3 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

テンプレートが名前付き IAM ロールを作成するため `CAPABILITY_NAMED_IAM` が必須です。

デプロイ後に実際のハンドラコードのアップロードを忘れないでください
（`bash integrations/datadog/scripts/deploy.sh --all --code-only`）。
忘れると両関数は `NotImplementedError` を投げるだけになります。

### パラメータリファレンス

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `DatadogApiKeySecretArn` | — | 必須。監査経路と同じシークレット |
| `DatadogSite` | `ap1.datadoghq.com` | intake エンドポイントを決定 |
| `FPolicySqsQueueArn` | `''` | **主要な** FPolicy トリガー。空の場合 SQS トリガーは作成されず EventBridge 経路のみ動作 |
| `EventBridgeBusName` | `default` | `fpolicy.fsxn` イベントを運ぶバス（副経路） |
| `EmsParserLayerArn` | `''` | EMS パーサ Layer。空の場合は組み込みスタブパーサで抽出フィールドが減る |
| `SqsBatchSize` | `10` | 1 回の実行あたりメッセージ数。失敗はメッセージ単位で報告されるため増やしても安全 |
| `AlarmNotificationTopicArn` | `''` | 4 つのアラームの SNS トピック。**空の場合は誰にも通知されない** |
| `Environment` | `production` | `DD_ENV` の値 |
| `EnableGzip` | `false` | セットアップガイドの gzip 既知の問題を参照 |
| `LogLevel` | `INFO` | |
| `LambdaMemorySize` | `256` | MB |
| `LambdaTimeout` | `60` | 秒 |

### スタックが作成するリソース

| リソース | 目的 |
|----------|------|
| Lambda `-ems` | EMS Webhook → Datadog |
| Lambda `-fpolicy` | FPolicy イベント → Datadog |
| `-fpolicy-dlq` | **非同期 EventBridge 経路**用の SQS DLQ |
| EventBridge ルール + ターゲット DLQ + リトライポリシー | FPolicy 副経路 |
| SQS イベントソースマッピング | FPolicy 主経路、`ReportBatchItemFailures` 付き |
| 4 つの CloudWatch アラーム | EMS エラー、FPolicy エラー、FPolicy スロットル、DLQ 深度 |

### 失敗したイベントは実際どこへ行くのか

ここは誤解されやすいので正確に記します。3 つの配送経路があり、それぞれ失敗の挙動が異なります。

| 経路 | 呼び出し方式 | 失敗時 |
|------|------------|--------|
| FPolicy / SQS（主） | イベントソースマッピング | ハンドラが失敗メッセージを個別に報告し、**取り込みキュー**の redrive ポリシーが `maxReceiveCount` 回の受信後にそのキュー自身の DLQ へ移動。この DLQ は共有 FPolicy スタック側に属し、本スタックのものではない |
| FPolicy / EventBridge | 非同期 | リトライ後 `-fpolicy-dlq` に書き込まれる |
| EMS Webhook | **同期**（API Gateway） | 同期呼び出しでは Lambda DLQ は機能しない。ONTAP は 5xx を受け取り `EmsErrorAlarm` が発火 |

つまり EMS の配送失敗はどこにも DLQ メッセージを残しません。アラームが唯一のシグナルです。
そのため `AlarmNotificationTopicArn` の設定は監査経路よりも重要になります。

## Step 2: EMS — API Gateway のデプロイと ONTAP 設定

### 2.1 Webhook API Gateway のデプロイ

共有テンプレートがエンドポイントと Lambda Authorizer を提供します。

```bash
EMS_LAMBDA_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-datadog-ems-fpolicy --region ap-northeast-1 \
  --query "Stacks[0].Outputs[?OutputKey=='EmsLambdaFunctionArn'].OutputValue" \
  --output text)

aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-datadog-ems-webhook \
  --parameter-overrides LambdaFunctionArn="${EMS_LAMBDA_ARN}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

スタック出力から invoke URL と認証トークンを記録します。

### 2.2 ONTAP に宛先を登録

```bash
# ONTAP CLI
event notification destination create \
  -name datadog-webhook \
  -rest-api-url https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems \
  -certificate-authority <ca-name>

event notification create \
  -filter-name important-events \
  -destinations datadog-webhook
```

### 2.3 転送するイベントを選ぶ

EMS イベントをすべて転送するとノイズになります。セキュリティ用途で価値が高いのは
アンチランサムウェアと可用性関連のイベントです。

| イベント | 意味 |
|---------|------|
| `arw.volume.state` | アンチランサムウェアの状態変化（`attack-detected` を含む） |
| `arw.vserver.state` | SVM での ARP 有効化/無効化 |
| `wafl.vol.full` | ボリューム満杯 — Snapshot ストームの副作用の可能性あり |
| `mgmtgwd.rootvol.space.low` | ルートボリュームの空き容量低下 |

配送パターンを含む完全なカタログは
[ems-detection-capabilities.md](../../../../docs/ja/ems-detection-capabilities.md) にあります。

### 2.4 検証

```bash
# ONTAP からテストイベントを発生させる
event generate -message-name arw.volume.state -values "test"

# Lambda を監視
aws logs tail /aws/lambda/fsxn-datadog-ems-fpolicy-ems --follow
```

その後 Datadog で `source:fsxn-ems` を検索します。

## Step 3: FPolicy — サーバー起動と ONTAP からの接続設定

### 3.1 Fargate サービスの起動

```bash
bash shared/scripts/fpolicy-fargate-control.sh start
bash shared/scripts/fpolicy-fargate-control.sh status
```

### 3.2 タスク IP を ONTAP に登録

Fargate タスク IP は再起動ごとに変わりますが、ONTAP は静的に保持します。
以下のスクリプトが現在のタスク IP を読み取り、ONTAP の外部エンジンを更新します。

```bash
bash shared/scripts/fpolicy-update-engine-ip.sh --auto
```

タスク再起動の**たびに**再実行してください。IP の古さは
「FPolicy イベントが届かなくなった」の最頻原因です。

### 3.3 ONTAP に FPolicy ポリシーを作成

```bash
# ONTAP CLI。`vserver` プレフィックスは ONTAP 9.11+ で非推奨だが引き続き動作する
vserver fpolicy policy event create -vserver <svm> -event-name file-ops \
  -protocol cifs -file-operations create,write,rename,delete

vserver fpolicy policy create -vserver <svm> -policy-name datadog-audit \
  -events file-ops -engine datadog-engine

vserver fpolicy enable -vserver <svm> -policy-name datadog-audit -sequence-number 1
```

### 3.4 検証

```bash
# ボリュームで操作を発生させた後:
aws logs tail /aws/lambda/fsxn-datadog-ems-fpolicy-fpolicy --follow
```

正常な実行では `FPolicy handler invoked: SQS batch of N record(s)` に続いて
`Shipped N/N FPolicy event(s)` が出力されます。その後 Datadog で
`source:fsxn-fpolicy` を検索します。

## トラブルシューティング

### EMS イベントが届かない

1. ONTAP は Webhook 宛先に**信頼された CA** を要求します。自己署名証明書は
   ONTAP 側でサイレントに拒否されます。
2. Lambda ログより先に API Gateway の実行ログを確認してください。
   Authorizer で拒否された場合は関数に到達しません。
3. `EmsErrorAlarm` が発火しているのに DLQ にメッセージが無いのは正常です。
   EMS は同期経路で DLQ を持ちません（上記の表を参照）。

### FPolicy イベントが止まった

ほぼ常に Fargate タスク IP の古さが原因です。

```bash
bash shared/scripts/fpolicy-fargate-control.sh status
bash shared/scripts/fpolicy-update-engine-ip.sh --auto
```

タスクが動作中で IP も最新の場合、取り込みキューにメッセージが溜まっているか
（Lambda 側の問題）まったく届いていないか（ONTAP またはネットワーク側の問題）を確認します。

```bash
aws sqs get-queue-attributes \
  --queue-url <ingestion-queue-url> \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

### 同じ FPolicy イベントが繰り返し配送される

パースできないメッセージは、破棄せずキューの redrive ポリシーで DLQ に移動させるため
failure として報告されます。`maxReceiveCount` に達するまではリトライされ、
これが重複のように見えます。取り込み DLQ（共有 FPolicy スタック出力の
`FPolicyDeadLetterQueueUrl`）を確認してください。

```bash
aws sqs receive-message --queue-url <ingestion-dlq-url> --max-number-of-messages 1
```

### partial batch failure が機能しない

イベントソースマッピングに `FunctionResponseTypes: [ReportBatchItemFailures]` が
設定されている必要があります。無い場合、ハンドラの `batchItemFailures` レスポンスは
無視され、1 件の不正メッセージでバッチ全体が再配送されます。

```bash
aws lambda list-event-source-mappings \
  --function-name fsxn-datadog-ems-fpolicy-fpolicy \
  --query 'EventSourceMappings[].FunctionResponseTypes'
```

### イベントは届くがフィールドが空

EMS の場合、通常は `EmsParserLayerArn` が空のまま組み込みスタブパーサが使われています。
Layer をビルドしてアタッチし、再デプロイしてください。

各ソースが生成する属性名は [field-mapping.md](field-mapping.md) を参照してください。

## クリーンアップ

`scripts/cleanup.sh` が依存関係を考慮した順序でこれらのスタックを削除します
（API Gateway スタックは EMS Lambda を持つスタックより先に削除する必要があります）。

```bash
bash integrations/datadog/scripts/cleanup.sh
```

先に ONTAP 側で FPolicy ポリシーを無効化してください。無効化しないと、
存在しないサーバーへの接続を ONTAP がリトライし続けます。

```bash
vserver fpolicy disable -vserver <svm> -policy-name datadog-audit
```

共有 FPolicy Fargate スタック（`fsxn-fp-srv`）は全ベンダーで使用するため
**削除されません**。他に必要がなければサービスを停止してください。

```bash
bash shared/scripts/fpolicy-fargate-control.sh stop
```

## 関連ドキュメント

- [セットアップガイド](setup-guide.md) — 監査ログ経路
- [フィールドマッピング](field-mapping.md) — ソースごとの属性
- [ログアーカイブ設定](log-archive-setup.md) — 長期保管
- [Snapshot 修復](snapshot-remediation-setup.md) — 自動封じ込め
- [EMS 検知能力](../../../../docs/ja/ems-detection-capabilities.md) — イベントカタログ
- [FPolicy クイックデプロイ](../../../../docs/ja/fpolicy-quick-deploy.md) — 共有インフラ
