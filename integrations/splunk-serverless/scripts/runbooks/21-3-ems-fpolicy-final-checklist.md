# 21.3 EMS/FPolicy 最終チェックリスト

## 概要

EMS Webhook パスおよび FPolicy パスの全検証が完了し、全成果物が揃っていることを確認する最終チェックリスト。全項目が PASS であることを確認してから EMS/FPolicy 検証完了とする。

## 前提条件

- Task 19.1〜19.3 が完了済み（EMS Webhook パス検証）
- Task 20.1〜20.4 が完了済み（FPolicy パス検証）
- Task 21.1 が完了済み（検証結果ドキュメント作成）
- Task 21.2 が完了済み（スクリーンショット検証）

## 最終チェックリスト

### EMS Webhook Lambda

- [ ] **EMS Webhook Lambda が動作している**
  - CloudFormation スタック: `CREATE_COMPLETE`
  - Lambda に `ems-parser` レイヤーがアタッチされている
  - `sourcetype=fsxn:ems:webhook`, `source=fsxn-ems` が設定されている

```bash
# スタック状態確認
aws cloudformation describe-stacks \
  --stack-name fsxn-ems-webhook \
  --region ap-northeast-1 \
  --query 'Stacks[0].StackStatus' \
  --output text

# Lambda 設定確認
aws lambda get-function-configuration \
  --function-name fsxn-splunk-ems-webhook \
  --region ap-northeast-1 \
  --query '{Layers: Layers[*].Arn, Env: Environment.Variables}'
```

### ARP ランサムウェア検知テスト

- [ ] **ARP テストが PASS**
  - `security anti-ransomware volume attack simulate` が正常実行
  - `arw.volume.state` EMS イベントが Splunk に到着（120秒以内）
  - HEC レスポンス: `{"text":"Success","code":0}`

```bash
# Splunk で確認
# SPL: index=fsxn_ems sourcetype=fsxn:ems:webhook arw.volume.state earliest=-1h
```

### Quota 超過テスト

- [ ] **Quota テストが PASS**
  - ソフトクォータ（50MB）設定 → 60MB+ 書き込み
  - `wafl.quota.softlimit.exceeded` EMS イベントが Splunk に到着（180秒以内）

```bash
# Splunk で確認
# SPL: index=fsxn_ems sourcetype=fsxn:ems:webhook wafl.quota.softlimit.exceeded earliest=-1h
```

### FPolicy Lambda

- [ ] **FPolicy Lambda が動作している**
  - EventBridge ルール（source: `fpolicy.fsxn`）がアクティブ
  - Lambda が EventBridge イベントを受信して Splunk HEC に転送
  - `sourcetype=fsxn:fpolicy:event`, `source=fsxn-fpolicy` が設定されている

```bash
# EventBridge ルール確認
aws events list-rules \
  --event-bus-name fpolicy-fsxn-bus \
  --region ap-northeast-1

# Lambda 設定確認
aws lambda get-function-configuration \
  --function-name fsxn-splunk-fpolicy-handler \
  --region ap-northeast-1 \
  --query 'Environment.Variables'
```

### FPolicy ファイル操作テスト

- [ ] **FPolicy ファイル操作テストが PASS**
  - ECS Fargate タスクが Running かつ Healthy
  - ONTAP KeepAlive メッセージが ECS ログに表示（120 秒間隔。検索窓は 300 秒以上取る）
  - CIFS/SMB ファイル作成 → `[SQS] Sent: <filename> (create)` がログに表示
  - Splunk にイベントが 30 秒以内に到着
  - `operation`, `file_path`, `user`, `client_ip` フィールドが含まれる

```bash
# ECS タスク状態確認
aws ecs describe-services \
  --cluster fsxn-fpolicy-cluster \
  --services fsxn-fpolicy-service \
  --region ap-northeast-1 \
  --query 'services[0].{RunningCount: runningCount, Status: status}'

# Splunk で確認
# SPL: index=fsxn_audit sourcetype=fsxn:fpolicy:event earliest=-1h
```

### スクリーンショット

- [ ] **全スクリーンショットが配置されている**
  - `docs/screenshots/splunk/splunk-ems-arp-detection-YYYYMMDD.png` が存在
  - `docs/screenshots/splunk/splunk-fpolicy-file-ops-YYYYMMDD.png` が存在
  - 全ファイルが命名規約に準拠
  - 全ファイルが 500KB 以下

```bash
# スクリーンショット確認
ls -la docs/screenshots/splunk/splunk-ems-arp-detection-*.png
ls -la docs/screenshots/splunk/splunk-fpolicy-file-ops-*.png
```

### 検証結果ドキュメント

- [ ] **検証結果ドキュメントが完成している**
  - `docs/ja/verification-results-splunk.md` に EMS セクションが追加
  - `docs/ja/verification-results-splunk.md` に FPolicy セクションが追加
  - 全ステップの判定が記入されている
  - HEC レスポンスステータスコードが記録されている
  - プレースホルダー（TODO/TBD）が残っていない

```bash
# プレースホルダー残存チェック
grep -c "TODO\|TBD" docs/ja/verification-results-splunk.md
# 期待値: 0
```

## 一括確認スクリプト

```bash
#!/bin/bash
set -euo pipefail

echo "=== EMS/FPolicy Final Checklist ==="
echo ""

PASS=0
FAIL=0

check_stack() {
  local stack_name="$1"
  local status=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region ap-northeast-1 \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || echo "NOT_FOUND")
  if [[ "$status" == "CREATE_COMPLETE" || "$status" == "UPDATE_COMPLETE" ]]; then
    echo "  [PASS] Stack $stack_name: $status"
    ((PASS++))
  else
    echo "  [FAIL] Stack $stack_name: $status"
    ((FAIL++))
  fi
}

check_file() {
  local desc="$1"
  local pattern="$2"
  local count=$(ls $pattern 2>/dev/null | wc -l | tr -d ' ')
  if [ "$count" -ge 1 ]; then
    echo "  [PASS] $desc ($count file(s))"
    ((PASS++))
  else
    echo "  [FAIL] $desc — not found"
    ((FAIL++))
  fi
}

echo "[Stacks]"
check_stack "fsxn-ems-webhook"
check_stack "fsxn-fp-srv"

echo ""
echo "[Screenshots]"
check_file "EMS ARP detection" "docs/screenshots/splunk/splunk-ems-arp-detection-*.png"
check_file "FPolicy file ops" "docs/screenshots/splunk/splunk-fpolicy-file-ops-*.png"

echo ""
echo "[Documentation]"
if grep -q "EMS.*Splunk" docs/ja/verification-results-splunk.md 2>/dev/null; then
  echo "  [PASS] EMS section in verification-results-splunk.md"
  ((PASS++))
else
  echo "  [FAIL] EMS section missing in verification-results-splunk.md"
  ((FAIL++))
fi

if grep -q "FPolicy.*Splunk" docs/ja/verification-results-splunk.md 2>/dev/null; then
  echo "  [PASS] FPolicy section in verification-results-splunk.md"
  ((PASS++))
else
  echo "  [FAIL] FPolicy section missing in verification-results-splunk.md"
  ((FAIL++))
fi

TODO_COUNT=$(grep -c "TODO\|TBD" docs/ja/verification-results-splunk.md 2>/dev/null || echo "0")
if [ "$TODO_COUNT" -eq 0 ]; then
  echo "  [PASS] No TODO/TBD placeholders remaining"
  ((PASS++))
else
  echo "  [FAIL] $TODO_COUNT TODO/TBD placeholders remaining"
  ((FAIL++))
fi

echo ""
echo "=== Summary ==="
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo ""

if [ "$FAIL" -eq 0 ]; then
  echo "  Result: ALL PASS — EMS/FPolicy verification complete"
  exit 0
else
  echo "  Result: $FAIL item(s) FAILED"
  exit 1
fi
```

## PASS/FAIL 判定基準

| 判定 | 条件 |
|------|------|
| **PASS** | 全6項目が PASS |
| **FAIL** | 1項目でも FAIL がある |

**全6項目:**
1. EMS Webhook Lambda が動作（ARP + Quota テスト PASS）
2. FPolicy Lambda が動作（ファイル操作テスト PASS）
3. EMS スクリーンショットが配置済み
4. FPolicy スクリーンショットが配置済み
5. 検証結果ドキュメントが完成
6. プレースホルダーが残っていない

## 不足がある場合の対応

| 不足項目 | 対応タスク |
|---------|-----------|
| EMS Webhook Lambda | Task 19.1 |
| ARP テスト | Task 19.2 |
| Quota テスト | Task 19.3 |
| FPolicy スタック | Task 20.1 |
| FPolicy Lambda | Task 20.2 |
| ONTAP FPolicy 設定 | Task 20.3 |
| FPolicy ファイル操作テスト | Task 20.4 |
| 検証結果ドキュメント | Task 21.1 |
| スクリーンショット検証 | Task 21.2 |

## 関連タスク

- Task 18.4: 最終確認 — 全成果物の確認（監査ログパス）
- Task 19.1〜19.3: EMS Webhook パス検証
- Task 20.1〜20.4: FPolicy パス検証
- Task 21.1: EMS/FPolicy 検証結果ドキュメントの作成
- Task 21.2: EMS/FPolicy スクリーンショット検証
