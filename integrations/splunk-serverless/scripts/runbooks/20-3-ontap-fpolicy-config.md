# 20.3 ONTAP FPolicy 外部エンジン設定

## 概要

ONTAP CLI を使用して FPolicy 外部エンジン、イベント、ポリシーを作成・有効化し、ECS Fargate タスクとの接続を確立する手順書。ONTAP が Fargate タスク IP の TCP:9898 に非同期で接続し、KeepAlive メッセージが約6秒間隔で送信されることを確認する。

## 前提条件

- Task 20.1 が完了済み（FPolicy Fargate スタックがデプロイ済み）
- ECS Fargate タスクが Running 状態であること
- Fargate タスクのプライベート IP が取得済みであること
- ONTAP CLI にアクセス可能（SSH）
- CIFS プロトコルが SVM で有効であること

## アーキテクチャ概要

```
ONTAP SVM (FPolicy Engine)
    ↓ TCP:9898 (async, no TLS)
ECS Fargate Task (FPolicy Server)
    ↓ KeepAlive (~6 seconds)
    ↓ File Operation Events
SQS Queue → EventBridge
```

**重要な設計ポイント:**
- 接続は非同期（asynchronous）モード — ファイル操作をブロックしない
- TLS なし（Fargate タスクは VPC 内プライベートサブネット）
- KeepAlive メッセージは約6秒間隔で ONTAP から送信される
- Fargate タスク IP が変更された場合、外部エンジンの更新が必要

## 手順

### Step 1: Fargate タスク IP の確認

```bash
# Fargate タスクのプライベート IP を取得（Task 20.1 Step 6 参照）
TASK_ARN=$(aws ecs list-tasks \
  --cluster fsxn-fpolicy-cluster \
  --service-name fsxn-fpolicy-service \
  --region ap-northeast-1 \
  --query 'taskArns[0]' \
  --output text)

FARGATE_IP=$(aws ecs describe-tasks \
  --cluster fsxn-fpolicy-cluster \
  --tasks "$TASK_ARN" \
  --region ap-northeast-1 \
  --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value' \
  --output text)

echo "Fargate Task IP: $FARGATE_IP"
```

### Step 2: ONTAP CLI に接続

```bash
# ONTAP CLI に SSH 接続
ssh admin@<management-ip>
```

### Step 3: FPolicy 外部エンジンの作成

```bash
# FPolicy 外部エンジンを作成
vserver fpolicy policy external-engine create \
  -vserver <svm-name> \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

**パラメータ説明:**
| パラメータ | 値 | 説明 |
|-----------|-----|------|
| `-vserver` | `<svm-name>` | FPolicy を設定する SVM 名 |
| `-engine-name` | `fpolicy_lambda_engine` | 外部エンジンの識別名 |
| `-primary-servers` | `<fargate-task-ip>` | Fargate タスクのプライベート IP |
| `-port` | `9898` | FPolicy サーバーのリスニングポート |
| `-extern-engine-type` | `asynchronous` | 非同期モード（ファイル操作をブロックしない） |

### Step 4: FPolicy イベントの作成

```bash
# FPolicy イベントを作成（CIFS: create, write, rename, delete）
vserver fpolicy policy event create \
  -vserver <svm-name> \
  -event-name fpolicy_cifs_events \
  -protocol cifs \
  -file-operations create,write,rename,delete
```

**監視対象操作:**
| 操作 | 説明 |
|------|------|
| `create` | ファイル/ディレクトリの作成 |
| `write` | ファイルへの書き込み |
| `rename` | ファイル/ディレクトリのリネーム |
| `delete` | ファイル/ディレクトリの削除 |

### Step 5: FPolicy ポリシーの作成

```bash
# FPolicy ポリシーを作成
vserver fpolicy policy create \
  -vserver <svm-name> \
  -policy-name fpolicy_lambda_policy \
  -events fpolicy_cifs_events \
  -engine fpolicy_lambda_engine
```

### Step 6: FPolicy ポリシーの有効化

```bash
# FPolicy ポリシーを有効化（sequence-number 1）
vserver fpolicy enable \
  -vserver <svm-name> \
  -policy-name fpolicy_lambda_policy \
  -sequence-number 1
```

### Step 7: FPolicy 設定の確認

```bash
# 外部エンジンの確認
vserver fpolicy policy external-engine show -vserver <svm-name>

# イベントの確認
vserver fpolicy policy event show -vserver <svm-name>

# ポリシーの確認
vserver fpolicy policy show -vserver <svm-name>

# 有効化状態の確認
vserver fpolicy show -vserver <svm-name>
```

**期待される出力（fpolicy show）:**
```
Vserver    Policy Name         Sequence  Status   Engine
---------- ------------------- --------- -------- ------
<svm-name> fpolicy_lambda_policy  1       on       fpolicy_lambda_engine
```

### Step 8: ECS CloudWatch Logs で KeepAlive 確認

```bash
# ECS Fargate タスクのログを確認
aws logs tail \
  /ecs/fsxn-fpolicy-server \
  --since 1m \
  --region ap-northeast-1 \
  --format short
```

**期待される出力（約6秒間隔）:**
```
[KeepAlive] Received from ONTAP (session: <session-id>)
[KeepAlive] Received from ONTAP (session: <session-id>)
[KeepAlive] Received from ONTAP (session: <session-id>)
```

**確認ポイント:**
- KeepAlive メッセージが約6秒間隔で表示されること
- セッション ID が一定であること（接続が安定していること）
- エラーメッセージがないこと

### Step 9: 接続状態の確認

```bash
# ONTAP CLI: FPolicy 接続状態を確認
vserver fpolicy policy external-engine show-connected \
  -vserver <svm-name> \
  -engine-name fpolicy_lambda_engine
```

**期待される出力:**
- Connected: `yes`
- Server: `<fargate-task-ip>:9898`

## 検証チェックリスト

- [ ] FPolicy 外部エンジンが作成された（port 9898, async, no TLS）
- [ ] FPolicy イベントが作成された（CIFS: create, write, rename, delete）
- [ ] FPolicy ポリシーが作成された
- [ ] FPolicy ポリシーが有効化された（sequence-number 1）
- [ ] ECS CloudWatch Logs で KeepAlive メッセージが確認できた（約6秒間隔）
- [ ] ONTAP から Fargate タスクへの接続が確立されている

## トラブルシューティング

### 外部エンジン作成時にエラー

- **原因**: SVM 名が不正、ポート番号が範囲外
- **解決**: `vserver show` で SVM 名を確認

### KeepAlive メッセージの未表示

1. **ネットワーク接続を確認**: ONTAP SVM から Fargate タスク IP:9898 への TCP 接続が可能か
2. **セキュリティグループを確認**: Fargate タスクの SG で TCP:9898 インバウンドが許可されているか
3. **Fargate タスク状態を確認**: タスクが Running かつ Healthy か
4. **外部エンジン接続状態を確認**: `vserver fpolicy policy external-engine show-connected`

### 接続の切断

- **原因**: Fargate タスクが再起動した（IP 変更）
- **解決**: 新しい Fargate タスク IP を取得し、外部エンジンを更新:
  ```bash
  # 自動更新スクリプトを使用
  bash shared/scripts/fpolicy-update-engine-ip.sh --auto
  ```

### FPolicy ポリシーの有効化の不可

- **原因**: イベントまたはエンジンの参照が不正
- **解決**: `vserver fpolicy policy show` でポリシー設定を確認

## 関連タスク

- Task 20.1: FPolicy 共有テンプレートのデプロイ
- Task 20.2: Splunk 向け FPolicy 受信 Lambda の作成
- Task 20.4: FPolicy ファイル操作テスト
