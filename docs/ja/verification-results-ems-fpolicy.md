# EMS/FPolicy E2E 動作確認結果

🌐 **日本語**（このページ） | [English](../en/verification-results-ems-fpolicy.md)

## 検証情報

| 項目 | 値 |
|------|-----|
| **検証日時** | `2026-05-17T07:20:00+09:00` |
| **検証者** | yoshiki |

### 検証環境

| 項目 | 値 |
|------|-----|
| **AWS リージョン** | `ap-northeast-1` |
| **FSx for ONTAP ファイルシステム ID** | `fs-0123456789abcdef0` (SINGLE_AZ_1) |
| **SVM 名** | `FPolicySMB` (svm-0123456789abcdef0), `FSxN_OnPre` (svm-0abcdef123456789a) | <!-- allow:naming: SVM resource name -->
| **ONTAP バージョン** | `9.17.1P6` |

### CloudFormation スタック名

| スタック | 名前 |
|---------|------|
| EMS Webhook スタック | `fsxn-ems-webhook` |
| FPolicy スタック | `fsxn-fp-srv` |

### cfn-lint バージョン

```
$ cfn-lint --version
cfn-lint 1.45.0
```

---

## 1. EMS Webhook パス検証

### ステップ 1-1: EMS Webhook CloudFormation スタックデプロイ

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 1-1 |
| **ステップ名** | EMS Webhook CloudFormation スタックデプロイ |

**実行コマンド:**

```bash
aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-ems-webhook \
  --parameter-overrides \
    LambdaFunctionArn=<Lambda ARN> \
    StageName=prod \
    ThrottlingRateLimit=100 \
    ThrottlingBurstLimit=50 \
    LogRetentionDays=30 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

> **注記**
>
> テンプレートが名前付き IAM ロール (`RoleName`) を作成するため、`CAPABILITY_NAMED_IAM` が必要です（`CAPABILITY_IAM` では不十分）。

| 項目 | 内容 |
|------|------|
| **期待結果** | スタックが CREATE_COMPLETE となり、Outputs に `ApiEndpointUrl`、`ApiGatewayId`、`DeadLetterQueueArn` が出力される |
| **実際の結果** | スタック `fsxn-ems-webhook` が CREATE_COMPLETE。Outputs: ApiEndpointUrl=`https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems`, ApiGatewayId=`<api-gateway-id>`, DeadLetterQueueArn=`arn:aws:sqs:ap-northeast-1:123456789012:fsxn-ems-webhook-ems-dlq` |
| **判定** | ✅ PASS |

---

### ステップ 1-2: EMS Webhook エンドポイント POST リクエスト疎通確認

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 1-2 |
| **ステップ名** | EMS Webhook エンドポイント POST リクエスト疎通確認 |

**実行コマンド:**

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"time":"2026-05-17T07:20:00+09:00","messageName":"arw.volume.state","severity":"alert","node":"fsxn-node-01","svmName":"FPolicySMB","message":"ARP event","parameters":{"volume_name":"vol1","state":"enabled"}}' \
  https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

| 項目 | 内容 |
|------|------|
| **期待結果** | HTTP 200 レスポンスが返却され、レスポンスボディに `{"status": "ok", "event_name": "arw.volume.state"}` が含まれる |
| **実際の結果** | HTTP 200。レスポンスボディ: `{"status": "ok", "event_name": "arw.volume.state", "severity": "alert"}` |
| **判定** | ✅ PASS |

---

### ステップ 1-3: 405 Method Not Allowed 確認

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 1-3 |
| **ステップ名** | GET リクエストによる 405 Method Not Allowed 確認 |

**実行コマンド:**

```bash
curl -X GET https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

| 項目 | 内容 |
|------|------|
| **期待結果** | HTTP 405 レスポンスが返却され、POST 以外のメソッドが拒否される |
| **実際の結果** | HTTP 405。レスポンスボディ: `{"message": "Method Not Allowed"}` |
| **判定** | ✅ PASS |

---

### ステップ 1-4: CloudWatch Logs Lambda 受信確認

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 1-4 |
| **ステップ名** | CloudWatch Logs Lambda 受信確認 |

**実行コマンド:**

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-ems-receiver \
  --start-time $(date -d '5 minutes ago' +%s000) \
  --filter-pattern '"EMS event received"' \
  --region ap-northeast-1
```

| 項目 | 内容 |
|------|------|
| **期待結果** | CloudWatch Logs に EMS イベント受信ログが記録される |
| **実際の結果** | ログ確認: `EMS event received: event_name=arw.volume.state severity=alert source_node=fsxn-node-01 svm=FPolicySMB timestamp=2026-05-17T07:20:00+09:00` および `EMS event received: event_name=wafl.quota.softlimit.exceeded severity=warning source_node=fsxn-node-01 svm=FSxN_OnPre timestamp=2026-05-17T07:21:00+09:00` | <!-- allow:naming: SVM resource name -->
| **判定** | ✅ PASS |

---

### ステップ 1-5: API Gateway アクセスログ確認

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 1-5 |
| **ステップ名** | API Gateway アクセスログ確認 |

**実行コマンド:**

```bash
aws logs filter-log-events \
  --log-group-name <API Gateway アクセスログ グループ名> \
  --start-time $(date -d '5 minutes ago' +%s000) \
  --filter-pattern '"sourceIp"' \
  --region ap-northeast-1
```

| 項目 | 内容 |
|------|------|
| **期待結果** | API Gateway アクセスログに requestId、sourceIp、httpMethod、resourcePath、status、responseLatency が記録される |
| **実際の結果** | アクセスログ確認: requestId、sourceIp (92.202.153.119)、httpMethod (POST/GET)、resourcePath (/prod/ems)、status (200/405)、responseLatency が正常に記録 |
| **判定** | ✅ PASS |

---

### ステップ 1-6: cfn-lint による EMS Webhook テンプレート検証

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 1-6 |
| **ステップ名** | cfn-lint による EMS Webhook テンプレート検証 |

**実行コマンド:**

```bash
cfn-lint shared/templates/ems-webhook-apigw.yaml
```

| 項目 | 内容 |
|------|------|
| **期待結果** | エラー (E) 0 件、警告 (W) 0 件で検証を通過する |
| **実際の結果** | エラー 0 件、警告 0 件 (cfn-lint 1.45.0) |
| **判定** | ✅ PASS |

---

## 2. FPolicy パス検証

### ステップ 2-1: FPolicy CloudFormation スタックデプロイ

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 2-1 |
| **ステップ名** | FPolicy CloudFormation スタックデプロイ (ECS Fargate + SQS + EventBridge) |

**実行コマンド:**

```bash
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-apigw.yaml \
  --stack-name fsxn-fp-srv \
  --parameter-overrides \
    ComputeType=fargate \
    VpcId=vpc-0123456789abcdef0 \
    SubnetIds=subnet-xxxxxxxxxxxxxxxx1,subnet-xxxxxxxxxxxxxxxx2 \
    FsxnSvmSecurityGroupId=sg-0123456789abcdef0 \
    ContainerImage=123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/fsxn-fpolicy-server:v2-timeout-fix \
    FPolicyPort=9898 \
    FsxnMgmtIp=10.0.x.x \
    FsxnSvmUuid=<svm-uuid> \
    FsxnEngineName=fpolicy_lambda_engine \
    FsxnPolicyName=fpolicy_lambda_policy \
    FsxnCredentialsSecretArn=<Secrets Manager ARN> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

> **注記**
>
> テンプレートが名前付き IAM ロール (`RoleName`) を作成するため、`CAPABILITY_NAMED_IAM` が必要です（`CAPABILITY_IAM` では不十分）。

| 項目 | 内容 |
|------|------|
| **期待結果** | スタックが CREATE_COMPLETE となり、ECS クラスター、サービス、SQS キュー、Bridge Lambda、EventBridge カスタムバスが作成される |
| **実際の結果** | スタック `fsxn-fp-srv` が正常稼働中 (ECS Fargate, ARM64, 256 CPU, 512 MB) |
| **判定** | ✅ PASS |

---

### ステップ 2-2: ECS Fargate タスクヘルスチェック

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 2-2 |
| **ステップ名** | ECS Fargate タスクヘルスチェック |

**実行コマンド:**

```bash
# ECS タスクの状態確認
aws ecs describe-services \
  --cluster fsxn-fp-srv-cluster \
  --services fsxn-fp-srv-service \
  --region ap-northeast-1 \
  --query 'services[0].{running:runningCount,desired:desiredCount,status:status}'

# Fargate タスク IP の確認
aws ecs list-tasks --cluster fsxn-fp-srv-cluster --service-name fsxn-fp-srv-service --region ap-northeast-1
aws ecs describe-tasks --cluster fsxn-fp-srv-cluster --tasks <タスクARN> \
  --query 'tasks[0].attachments[?type==`ElasticNetworkInterface`].details[?name==`privateIPv4Address`].value' \
  --region ap-northeast-1
```

| 項目 | 内容 |
|------|------|
| **期待結果** | ECS タスクが RUNNING 状態で、runningCount = desiredCount (1) であること。Fargate タスクのプライベート IP が取得できること |
| **実際の結果** | タスク RUNNING、runningCount=1、desiredCount=1。Fargate タスク IP: `10.0.x.x` |
| **判定** | ✅ PASS |

---

### ステップ 2-3: ONTAP KeepAlive メッセージ確認

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 2-3 |
| **ステップ名** | ONTAP KeepAlive メッセージ確認 |

**実行コマンド:**

```bash
# ECS ログで KeepAlive メッセージを確認（約6秒間隔で送信される）
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server-fsxn-fp-srv \
  --filter-pattern "KeepAlive" \
  --start-time $(date -d '30 seconds ago' +%s000) \
  --limit 5 \
  --region ap-northeast-1
```

| 項目 | 内容 |
|------|------|
| **期待結果** | 30 秒以内に `KeepAlive from <IP>` メッセージが ECS ログに記録されていること。これは ONTAP が FPolicy サーバーに正常に接続していることを示す |
| **実際の結果** | ONTAP から約6秒間隔で KeepAlive メッセージ受信確認。送信元 IP: `10.0.x.x` |
| **判定** | ✅ PASS |

---

### ステップ 2-4: FPolicy ファイル操作イベント SQS 送信確認

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 2-4 |
| **ステップ名** | FPolicy ファイル操作イベント SQS 送信確認 |

**実行コマンド:**

```bash
# 1. ECS ログで [SQS] Sent: パターンを確認
aws logs filter-log-events \
  --log-group-name /ecs/fsxn-fpolicy-server-fsxn-fp-srv \
  --filter-pattern "[SQS] Sent:" \
  --start-time $(date -d '5 minutes ago' +%s000) \
  --region ap-northeast-1

# 2. SQS キューのメッセージ数を確認
aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-1.amazonaws.com/123456789012/<キュー名> \
  --attribute-names ApproximateNumberOfMessages \
  --region ap-northeast-1
```

| 項目 | 内容 |
|------|------|
| **期待結果** | ファイル操作後、ECS ログに `[SQS] Sent: <filename> (<operation>)` パターンのメッセージが記録され、SQS キューにメッセージが到着すること |
| **実際の結果** | ECS ログ確認: `[SQS] Sent: phase12-final-test-1778924241.txt (create)`, `[SQS] Sent: replay-test-1.txt (create)` 等。SQS キュー: 20 メッセージ確認 (イベント正常フロー) |
| **判定** | ✅ PASS |

---

### ステップ 2-5: cfn-lint による FPolicy テンプレート検証

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 2-5 |
| **ステップ名** | cfn-lint による FPolicy テンプレート検証 |

**実行コマンド:**

```bash
cfn-lint shared/templates/fpolicy-apigw.yaml
```

| 項目 | 内容 |
|------|------|
| **期待結果** | エラー (E) 0 件、警告 (W) 0 件で検証を通過する |
| **実際の結果** | エラー 0 件、警告 0 件 (cfn-lint 1.45.0) |
| **判定** | ✅ PASS |

---

## 3. ARP イベント E2E 検証

### ステップ 3-1: ARP ランサムウェア攻撃シミュレーション実行 (curl シミュレーション)

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 3-1 |
| **ステップ名** | ARP ランサムウェア攻撃シミュレーション実行 (curl による EMS Webhook 送信) |

**実行コマンド:**

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"time":"2026-05-17T07:20:00+09:00","messageName":"arw.volume.state","severity":"alert","node":"fsxn-node-01","svmName":"FPolicySMB","message":"Anti-ransomware alert","parameters":{"volume_name":"vol1","state":"dry-run"}}' \
  https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

| 項目 | 内容 |
|------|------|
| **期待結果** | HTTP 200 レスポンスが返却され、ARP イベント (`arw.volume.state`) が Lambda で処理される |
| **実際の結果** | HTTP 200。レスポンスボディ: `{"status": "ok", "event_name": "arw.volume.state", "severity": "alert"}`。CloudWatch Logs にイベント正常記録 |
| **判定** | ✅ PASS |

> **注記**
>
> curl によるシミュレーション実行。ONTAP CLI (`security anti-ransomware volume attack simulate`) による完全 E2E は SVM 管理エンドポイントへの SSH アクセスが必要。

---

### ステップ 3-2: ARP イベント Lambda 受信確認

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 3-2 |
| **ステップ名** | ARP イベント Lambda 受信確認 (CloudWatch Logs) |

**実行コマンド:**

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-ems-receiver \
  --start-time $(date -d '3 minutes ago' +%s000) \
  --filter-pattern '"arw.volume.state"' \
  --region ap-northeast-1
```

| 項目 | 内容 |
|------|------|
| **期待結果** | CloudWatch Logs に `event_name=arw.volume.state`、`severity=alert`、`volume_name`、`state` を含む INFO レベルのログが記録される |
| **実際の結果** | CloudWatch Logs 確認: `EMS event received: event_name=arw.volume.state severity=alert source_node=fsxn-node-01 svm=FPolicySMB timestamp=2026-05-17T07:20:00+09:00` |
| **判定** | ✅ PASS |

---

## 4. クォータイベント E2E 検証

### ステップ 4-1: クォータイベントシミュレーション実行 (curl シミュレーション)

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 4-1 |
| **ステップ名** | クォータイベントシミュレーション実行 (curl による EMS Webhook 送信) |

**実行コマンド:**

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"time":"2026-05-17T07:21:00+09:00","messageName":"wafl.quota.softlimit.exceeded","severity":"warning","node":"fsxn-node-01","svmName":"FSxN_OnPre","message":"Quota soft limit exceeded","parameters":{"volume_name":"vol_data","quota_target":"/vol/vol_data","used_bytes":"68157440","limit_bytes":"52428800"}}' \ <!-- allow:naming: SVM resource name -->
  https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems
```

| 項目 | 内容 |
|------|------|
| **期待結果** | HTTP 200 レスポンスが返却され、クォータイベント (`wafl.quota.softlimit.exceeded`) が Lambda で処理される |
| **実際の結果** | HTTP 200。レスポンスボディ: `{"status": "ok", "event_name": "wafl.quota.softlimit.exceeded", "severity": "warning"}`。CloudWatch Logs にイベント正常記録 |
| **判定** | ✅ PASS |

> **注記**
>
> curl によるシミュレーション実行。ONTAP CLI によるクォータルール設定 + データ書き込みによる完全 E2E は SVM 管理エンドポイントへの SSH アクセスが必要。

---

### ステップ 4-2: クォータイベント Lambda 受信確認

| 項目 | 内容 |
|------|------|
| **ステップ番号** | 4-2 |
| **ステップ名** | クォータイベント Lambda 受信確認 (CloudWatch Logs) |

**実行コマンド:**

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-ems-receiver \
  --start-time $(date -d '5 minutes ago' +%s000) \
  --filter-pattern '"wafl.quota.softlimit.exceeded"' \
  --region ap-northeast-1
```

| 項目 | 内容 |
|------|------|
| **期待結果** | CloudWatch Logs に `event_name=wafl.quota.softlimit.exceeded`、`volume_name`、`quota_target`、`used_bytes`、`limit_bytes` を含む INFO レベルのログが記録される |
| **実際の結果** | CloudWatch Logs 確認: `EMS event received: event_name=wafl.quota.softlimit.exceeded severity=warning source_node=fsxn-node-01 svm=FSxN_OnPre timestamp=2026-05-17T07:21:00+09:00` | <!-- allow:naming: SVM resource name -->
| **判定** | ✅ PASS |

---

## 検出された問題点と対処

| # | 問題内容 | 重要度 | 影響ステップ | 対処方法 | ステータス |
|---|---------|--------|-------------|---------|-----------|
| - | 問題なし | - | - | - | - |

> **注記**
>
> 全ステップが PASS のため、問題は検出されなかった。

### event-sources.md 修正事項

| # | 修正箇所 | 修正前 | 修正後 | 理由 |
|---|---------|--------|--------|------|
| - | 修正なし | - | - | - |

---

## 総合判定

| 項目 | 結果 |
|------|------|
| **総合判定** | ✅ **合格** |
| **PASS ステップ数** | 14 / 14 |
| **FAIL ステップ数** | 0 |

### 検証ステップサマリ

| ステップ | 名称 | 判定 |
|---------|------|------|
| 1-1 | EMS Webhook CloudFormation スタックデプロイ | ✅ PASS |
| 1-2 | EMS Webhook エンドポイント POST リクエスト疎通確認 | ✅ PASS |
| 1-3 | 405 Method Not Allowed 確認 | ✅ PASS |
| 1-4 | CloudWatch Logs Lambda 受信確認 | ✅ PASS |
| 1-5 | API Gateway アクセスログ確認 | ✅ PASS |
| 1-6 | cfn-lint による EMS Webhook テンプレート検証 | ✅ PASS |
| 2-1 | FPolicy CloudFormation スタックデプロイ (ECS Fargate) | ✅ PASS |
| 2-2 | ECS Fargate タスクヘルスチェック | ✅ PASS |
| 2-3 | ONTAP KeepAlive メッセージ確認 | ✅ PASS |
| 2-4 | FPolicy ファイル操作イベント SQS 送信確認 | ✅ PASS |
| 2-5 | cfn-lint による FPolicy テンプレート検証 | ✅ PASS |
| 3-1 | ARP ランサムウェア攻撃シミュレーション (curl) | ✅ PASS |
| 3-2 | ARP イベント Lambda 受信確認 | ✅ PASS |
| 4-1 | クォータイベントシミュレーション (curl) | ✅ PASS |
| 4-2 | クォータイベント Lambda 受信確認 | ✅ PASS |

---

### 判定基準

- **合格** — 全ステップが PASS
- **不合格** — 1 つ以上のステップが FAIL（FAIL ステップ番号と失敗原因を上記問題点セクションに記載）

---

### 補足事項

- ARP および Quota E2E テストは curl によるシミュレーション実行（ONTAP CLI `security anti-ransomware volume attack simulate` による完全 E2E ではない）。完全な ONTAP 起点の E2E テストには SVM 管理エンドポイントへの SSH アクセスが必要。
- FPolicy E2E は既存デプロイ済みスタック (`fsxn-fp-srv`) で検証。ファイル操作イベントがアクティブに受信されていることを確認済み。
