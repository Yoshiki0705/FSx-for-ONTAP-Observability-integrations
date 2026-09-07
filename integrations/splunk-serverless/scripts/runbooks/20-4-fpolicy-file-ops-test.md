# 20.4 FPolicy ファイル操作テスト

## 概要

FPolicy パス全体（ONTAP → ECS Fargate → SQS → EventBridge → Lambda → Splunk HEC）の E2E 動作を検証する手順書。CIFS/SMB 経由でファイルを作成し、ECS ログでの SQS 送信確認から Splunk でのイベント到着確認までを実施する。

## 前提条件

- Task 20.1〜20.3 が完了済み
- ECS Fargate タスクが Running かつ Healthy
- ONTAP FPolicy ポリシーが有効化済み
- ECS CloudWatch Logs で KeepAlive メッセージが確認済み
- CIFS/SMB 共有にアクセス可能なクライアントがあること
- Splunk に `fsxn_audit` Index が作成済み
- スクリーンショットツール

## 手順

### Step 1: ECS Fargate タスクの状態確認

```bash
# ECS サービスの状態確認
aws ecs describe-services \
  --cluster fsxn-fpolicy-cluster \
  --services fsxn-fpolicy-service \
  --region ap-northeast-1 \
  --query 'services[0].{Status: status, RunningCount: runningCount, DesiredCount: desiredCount, HealthStatus: healthCheckGracePeriodSeconds}'
```

**期待される出力:**
- Status: `ACTIVE`
- RunningCount: `1`
- DesiredCount: `1`

### Step 2: タスクヘルスチェックの確認

```bash
# タスクの詳細確認
TASK_ARN=$(aws ecs list-tasks \
  --cluster fsxn-fpolicy-cluster \
  --service-name fsxn-fpolicy-service \
  --region ap-northeast-1 \
  --query 'taskArns[0]' \
  --output text)

aws ecs describe-tasks \
  --cluster fsxn-fpolicy-cluster \
  --tasks "$TASK_ARN" \
  --region ap-northeast-1 \
  --query 'tasks[0].{LastStatus: lastStatus, HealthStatus: healthStatus, StartedAt: startedAt}'
```

**期待される出力:**
- LastStatus: `RUNNING`
- HealthStatus: `HEALTHY`

### Step 3: ONTAP KeepAlive の確認

```bash
# ECS CloudWatch Logs で KeepAlive メッセージを確認
aws logs tail \
  /ecs/fsxn-fpolicy-server \
  --since 30s \
  --region ap-northeast-1 \
  --format short
```

**期待される出力（約6秒間隔）:**
```
[KeepAlive] Received from ONTAP (session: <session-id>)
```

### Step 4: CIFS/SMB 経由でファイルを作成

テスト用ファイルを CIFS/SMB 共有に作成:

```bash
# macOS/Linux から SMB マウント経由
echo "FPolicy test file - $(date -u +%Y-%m-%dT%H:%M:%SZ)" > /Volumes/<share-name>/fpolicy-test-$(date +%Y%m%d%H%M%S).txt

# または Windows から
# echo "FPolicy test file" > \\<svm-cifs-dns>\<share-name>\fpolicy-test.txt
```

**テストファイル名を記録:** `fpolicy-test-<timestamp>.txt`

### Step 5: ECS ログで SQS 送信確認

```bash
# ECS CloudWatch Logs で SQS 送信メッセージを確認
aws logs tail \
  /ecs/fsxn-fpolicy-server \
  --since 30s \
  --region ap-northeast-1 \
  --format short \
  --filter-pattern "[SQS]"
```

**期待される出力:**
```
[SQS] Sent: fpolicy-test-<timestamp>.txt (create)
```

**確認ポイント:**
- ファイル名が正しいこと
- 操作が `create` であること
- エラーメッセージがないこと

### Step 6: EventBridge イベントの確認（オプション）

```bash
# EventBridge のメトリクスで確認
aws cloudwatch get-metric-statistics \
  --namespace AWS/Events \
  --metric-name Invocations \
  --dimensions Name=RuleName,Value=<fpolicy-rule-name> \
  --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 60 \
  --statistics Sum \
  --region ap-northeast-1
```

### Step 7: Lambda CloudWatch Logs で転送確認

```bash
# FPolicy Lambda のログを確認
aws logs tail \
  /aws/lambda/fsxn-splunk-fpolicy-handler \
  --since 1m \
  --region ap-northeast-1 \
  --format short
```

**確認ポイント:**
- `Forwarded FPolicy event to Splunk HEC` ログが表示されること
- `operation: create` が記録されていること
- `file_path` にテストファイル名が含まれること

### Step 8: Splunk Search で到着確認

Splunk Search で以下の SPL クエリを実行:

```spl
index=fsxn_audit sourcetype=fsxn:fpolicy:event earliest=-5m
```

**期待される結果（30秒以内に到着）:**
- 1件以上の FPolicy イベントが返される
- テストファイルの `create` 操作が含まれること

### Step 9: フィールド詳細の確認

```spl
index=fsxn_audit sourcetype=fsxn:fpolicy:event earliest=-15m
| table _time, operation, file_path, user, client_ip
```

**必須フィールド:**
| フィールド | 期待値 |
|-----------|--------|
| `operation` | `create` |
| `file_path` | テストファイルのパス |
| `user` | CIFS 接続ユーザー |
| `client_ip` | クライアントの IP アドレス |

### Step 10: スクリーンショット撮影

**キャプチャ対象:**

以下の要素がすべて画面内に収まるようにスクリーンショットを撮影:

1. **Search バー**: `index=fsxn_audit sourcetype=fsxn:fpolicy:event` が表示されている
2. **結果件数**: FPolicy イベントの件数が表示されている
3. **イベント詳細**: 展開されたイベントで以下が確認できる:
   - `operation`: `create`
   - `file_path`: テストファイルのパス
   - `user`: ユーザー名
   - `client_ip`: IP アドレス

### Step 11: スクリーンショット保存

```bash
# 保存先（YYYYMMDD は撮影日）
docs/screenshots/splunk/splunk-fpolicy-file-ops-YYYYMMDD.png

# 例: 2026年1月20日に撮影した場合
docs/screenshots/splunk/splunk-fpolicy-file-ops-20260120.png

# ファイルサイズ確認（500KB 以下）
ls -la docs/screenshots/splunk/splunk-fpolicy-file-ops-*.png

# マスキング処理
python3 docs/screenshots/mask_screenshots.py
```

### Step 12: テストファイルのクリーンアップ

```bash
# テストファイルの削除
rm /Volumes/<share-name>/fpolicy-test-*.txt
```

## 検証チェックリスト

- [ ] ECS Fargate タスクが Running かつ Healthy
- [ ] ECS CloudWatch Logs で ONTAP KeepAlive メッセージが確認できた
- [ ] CIFS/SMB 経由でテストファイルが作成された
- [ ] ECS CloudWatch Logs で `[SQS] Sent: <filename> (create)` が確認できた
- [ ] Lambda CloudWatch Logs で転送成功ログが表示された
- [ ] Splunk Search でイベントが 30 秒以内に到着した
- [ ] イベントに `operation`, `file_path`, `user`, `client_ip` フィールドが含まれる
- [ ] スクリーンショットが `docs/screenshots/splunk/splunk-fpolicy-file-ops-YYYYMMDD.png` に保存された
- [ ] ファイルサイズが 500KB 以下
- [ ] マスキング処理が完了

## トラブルシューティング

### ECS ログでの SQS 送信メッセージの未表示

1. KeepAlive メッセージが表示されているか確認（接続が生きているか）
2. FPolicy ポリシーが有効か確認: `vserver fpolicy show`
3. ファイル操作が CIFS プロトコル経由か確認（NFS は対象外）
4. FPolicy イベントの `file-operations` に `create` が含まれているか確認

### Lambda でのイベントの未受信

- **原因**: EventBridge ルールのターゲット設定が不正
- **解決**: `aws events list-targets-by-rule` でターゲットを確認

### 30 秒以内の Splunk 到着の不成立

1. SQS キューにメッセージが滞留していないか確認
2. Lambda の実行エラーを CloudWatch Logs で確認
3. HEC エンドポイントの接続性を確認
4. Lambda のコンカレンシー制限を確認

### ファイル操作の未検知

- **原因**: FPolicy フィルターが適用されている
- **解決**: FPolicy スコープ設定を確認し、テスト対象ボリュームが含まれているか確認

## 関連タスク

- Task 20.1: FPolicy 共有テンプレートのデプロイ
- Task 20.2: Splunk 向け FPolicy 受信 Lambda の作成
- Task 20.3: ONTAP FPolicy 外部エンジン設定
- Task 21.1: EMS/FPolicy 検証結果ドキュメントの作成
