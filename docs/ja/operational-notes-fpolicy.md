# FPolicy 運用ノート

🌐 **日本語**（このページ） | [English](../en/operational-notes-fpolicy.md)

## 概要

本ドキュメントは、FPolicy External Engine (ECS Fargate + SQS + EventBridge) の
運用で得られた知見をまとめたものです。

---

## アーキテクチャ概要

```
ONTAP FPolicy → TCP:9898 → ECS Fargate → SQS (FPolicy_Q) → Bridge Lambda → EventBridge → Vendor Lambda
```

### 検証済み構成

| コンポーネント | 値 |
|--------------|-----|
| コンピュートモード | ECS Fargate (ARM64) |
| CPU / Memory | 256 CPU / 512 MB |
| コンテナイメージ | `123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:v2-timeout-fix` |
| リスンポート | TCP 9898 |
| VPC | `vpc-0123456789abcdef0` |
| サブネット | `subnet-xxxxxxxxxxxxxxxx1` (プライベート) |
| FPolicy Server SG | `sg-xxxxxxxxxxxxxxxxx` (TCP 9898 inbound) |
| FSx for ONTAP SVM SG | `sg-0123456789abcdef0` |
| SVM | `FPolicySMB` (svm-0123456789abcdef0) |
| SVM UUID | `<svm-uuid>` |
| SVM 管理 IP | `10.0.x.x` |
| Secrets | `fsx-ontap-fsxadmin-credentials` |

---

## Fargate タスク IP の自動更新

### 問題
Fargate タスクは再起動時に新しい IP アドレスが割り当てられます。
ONTAP FPolicy External Engine は `primary-servers` に IP アドレスを指定するため、
タスク再起動後に接続が切れます。

### 解決策: IP Auto-Updater Lambda

ECS Task State Change イベントを EventBridge で検知し、Lambda が自動的に
ONTAP REST API を呼び出して `primary-servers` を更新します。

```
ECS Task State Change (RUNNING) → EventBridge Rule → IP Updater Lambda → ONTAP REST API (PATCH)
```

**ONTAP REST API エンドポイント:**
```
PATCH https://<mgmt-ip>/api/protocols/fpolicy/<svm-uuid>/engines/<engine-name>
Body: {"primary_servers": ["<new-task-ip>"]}
```

**認証**: Secrets Manager (`fsx-ontap-fsxadmin-credentials`) から取得した Basic Auth

### 手動での IP 更新

IP Auto-Updater が失敗した場合の手動手順:

```bash
# 1. Get the current Fargate task IP
aws ecs list-tasks --cluster fsxn-fpolicy --service-name fsxn-fpolicy-server
aws ecs describe-tasks --cluster fsxn-fpolicy --tasks <task-arn> \
  --query 'tasks[0].attachments[?type==`ElasticNetworkInterface`].details[?name==`privateIPv4Address`].value'

# 2. Update the external engine IP via ONTAP CLI
vserver fpolicy policy external-engine modify -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <new-ip>

# 3. Verify connection status
vserver fpolicy show-engine -vserver FPolicySMB -engine-name fpolicy_lambda_engine
```

---

## KeepAlive メッセージ

ONTAP は FPolicy サーバーに対して約 6 秒間隔で KeepAlive メッセージを送信します。
これは接続が正常であることの指標です。

### 確認方法

```bash
# Check KeepAlive in ECS logs
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server \
  --filter-pattern "KeepAlive" \
  --start-time $(date -d '30 seconds ago' +%s000) \
  --limit 5
```

### 期待される出力
```
KeepAlive from 10.0.x.x (session: xxx)
```

KeepAlive が見えない場合:
1. ONTAP が Fargate タスク IP に接続できていない
2. セキュリティグループが TCP 9898 をブロックしている
3. Fargate タスクが再起動して IP が変わった

---

## SQS AccessDenied エラーの解決

### 症状
ECS ログに以下のエラーが表示される:
```
AccessDenied: User: arn:aws:sts::123456789012:assumed-role/xxx is not authorized
to perform: sqs:SendMessage on resource: arn:aws:sqs:ap-northeast-1:123456789012:FPolicy_Q
```

### 原因
ECS タスクロールに `sqs:SendMessage` 権限がない。

### 解決策
ECS タスクロールに以下のポリシーを追加:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:GetQueueUrl"
      ],
      "Resource": "arn:aws:sqs:ap-northeast-1:123456789012:*-fpolicy-ingestion"
    }
  ]
}
```

> **Note**
>
> CloudFormation テンプレート (`fpolicy-apigw.yaml`) では `EcsTaskRole` に
> この権限が含まれています。手動デプロイ時に忘れやすいポイントです。

---

## コンテナイメージのバージョニング

### 現在のイメージ
```
123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:v2-timeout-fix
```

### タグの意味

| タグ | 説明 |
|------|------|
| `v1` | 初期バージョン |
| `v2-timeout-fix` | タイムアウト処理の修正版（現在使用中） |

### `v2-timeout-fix` で修正された問題
- ONTAP からの接続が長時間アイドル状態になった際のタイムアウト処理
- TCP ソケットの適切なクリーンアップ
- KeepAlive レスポンスの確実な送信

### イメージ更新手順
```bash
# 1. ECR authentication
aws ecr get-login-password --region ap-northeast-1 | \
  docker login --username AWS --password-stdin \
  123456789012.dkr.ecr.ap-northeast-1.amazonaws.com

# 2. Build & push (ARM64)
docker buildx build --platform linux/arm64 \
  -t 123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:<new-tag> \
  --push .

# 3. Update ECS service (restart task with new image)
aws ecs update-service --cluster fsxn-fpolicy \
  --service fsxn-fpolicy-server \
  --force-new-deployment
```

---

## NFSv3 Write-Complete 遅延

### 動作
NFSv3 の write 操作は、ONTAP が write-complete を確認するまでデフォルトで
5 秒の遅延があります。これは FPolicy サーバーの `WRITE_COMPLETE_DELAY_SEC`
環境変数で制御されます。

### 影響
- ファイル書き込み後、SQS にイベントが到着するまで最大 5 秒の遅延
- CIFS/SMB の create 操作には影響なし（即座に通知）

### 調整
```yaml
# CloudFormation parameter
WriteCompleteDelaySec:
  Type: Number
  Default: 5
  MinValue: 0
  MaxValue: 60
```

---

## 監視項目

### 必須監視

| 監視対象 | メトリクス/ログ | アラート条件 |
|---------|--------------|------------|
| SQS キュー深度 | `ApproximateNumberOfMessagesVisible` | > 100 (5分間) |
| ECS タスクヘルス | タスク数 < DesiredCount | 1分以上 |
| ECS ログ `[SQS] Sent:` | ログ出力頻度 | 10分間ゼロ |
| Bridge Lambda エラー | `Errors` メトリクス | > 0 |
| DLQ メッセージ数 | `ApproximateNumberOfMessagesVisible` | > 0 |

### CloudWatch Logs クエリ

```bash
# Check FPolicy events (ECS logs)
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server \
  --filter-pattern "[SQS] Sent:" \
  --start-time $(date -d '5 minutes ago' +%s000)

# Check KeepAlive
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server \
  --filter-pattern "KeepAlive" \
  --start-time $(date -d '30 seconds ago' +%s000) \
  --limit 5

# Check Bridge Lambda errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-fpolicy-bridge \
  --filter-pattern "ERROR" \
  --start-time $(date -d '10 minutes ago' +%s000)

# Check IP Updater Lambda execution
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-fpolicy-ip-updater \
  --filter-pattern "updated to" \
  --start-time $(date -d '1 hour ago' +%s000)
```

### SQS キュー確認

```bash
# Check queue status
aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-1.amazonaws.com/123456789012/FPolicy_Q \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

---

## NLB の役割（重要）

**NLB は FPolicy トラフィックのルーティングには使用されません。**

NLB の唯一の目的は ECS Fargate タスクのヘルスチェック（TCP ポート 9898）です。
ONTAP は Fargate タスクの ENI プライベート IP に直接 TCP 接続します。

これは FPolicy が独自バイナリプロトコルを使用するためです。
NLB を経由すると接続の安定性に問題が生じる可能性があります。

---

## トラブルシューティングフローチャート

```
FPolicy イベントが届かない
│
├─ ECS タスクは RUNNING か？
│  └─ No → ECS サービスを確認、タスク定義を確認
│
├─ KeepAlive メッセージはあるか？
│  └─ No → ONTAP が接続できていない
│     ├─ Fargate タスク IP は正しいか？
│     ├─ SG (sg-xxxxxxxxxxxxxxxxx) は TCP 9898 を許可しているか？
│     └─ ONTAP external engine の設定を確認
│
├─ [SQS] Sent: メッセージはあるか？
│  └─ No → ファイル操作がトリガーされていない
│     ├─ FPolicy ポリシーは有効か？
│     ├─ 監視対象のプロトコル/操作は正しいか？
│     └─ FPolicy event の設定を確認
│
├─ SQS にメッセージはあるか？
│  └─ No → SQS SendMessage が失敗している
│     └─ ECS タスクロールの IAM 権限を確認
│
├─ Bridge Lambda は実行されているか？
│  └─ No → Event Source Mapping を確認
│
└─ EventBridge にイベントは届いているか？
   └─ No → Bridge Lambda のエラーログを確認
```

---

## 関連リソース

- テンプレート: `shared/templates/fpolicy-apigw.yaml`
- E2E テスト: `shared/scripts/e2e-test-fpolicy.py`
- イベントソースガイド: `docs/ja/event-sources.md`
- 検証結果: `docs/ja/verification-results-ems-fpolicy.md`
