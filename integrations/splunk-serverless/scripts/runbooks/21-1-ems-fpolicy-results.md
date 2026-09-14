# 21.1 EMS/FPolicy 検証結果ドキュメントの作成

## 概要

`docs/ja/verification-results-splunk.md` に EMS Webhook パスおよび FPolicy パスの検証結果セクションを追加する手順書。各ステップの実行結果を記録し、PASS/FAIL 判定を行う。

## 前提条件

- Task 19.1〜19.3 が完了済み（EMS Webhook パス検証完了）
- Task 20.1〜20.4 が完了済み（FPolicy パス検証完了）
- `docs/ja/verification-results-splunk.md` が存在すること
- 各テストの実行結果（HEC レスポンス、Splunk 検索結果）が記録されていること

## ドキュメント構造

`docs/ja/verification-results-splunk.md` に以下のセクションを追加:

```markdown
## EMS → Splunk パス検証

### ステップ一覧

| # | ステップ名 | コマンド | 期待結果 | 実測結果 | 判定 |
|---|-----------|---------|---------|---------|------|
| 19.1-1 | EMS Webhook スタックデプロイ | `aws cloudformation ...` | CREATE_COMPLETE | ... | PASS/FAIL |
| 19.1-2 | Lambda レイヤーアタッチ確認 | `aws lambda get-function-configuration ...` | ems-parser レイヤー含む | ... | PASS/FAIL |
| 19.2-1 | ARP シミュレーション実行 | `security anti-ransomware volume attack simulate ...` | コマンド成功 | ... | PASS/FAIL |
| 19.2-2 | EMS イベント発行確認 | `event log show -messagename arw.volume.state` | arw.volume.state (ALERT) | ... | PASS/FAIL |
| 19.2-3 | HEC レスポンス確認 | Lambda CloudWatch Logs | {"text":"Success","code":0} | ... | PASS/FAIL |
| 19.2-4 | Splunk 到着確認 (120s以内) | SPL: `index=fsxn_ems ...` | 1件以上 | ... | PASS/FAIL |
| 19.3-1 | クォータ設定 | `volume quota policy rule create ...` | ソフトリミット 50MB | ... | PASS/FAIL |
| 19.3-2 | クォータ超過書き込み | `dd if=/dev/urandom ...` | 60MB+ 書き込み成功 | ... | PASS/FAIL |
| 19.3-3 | Splunk 到着確認 (180s以内) | SPL: `index=fsxn_ems ... wafl.quota.*` | 1件以上 | ... | PASS/FAIL |

## FPolicy → Splunk パス検証

### ステップ一覧

| # | ステップ名 | コマンド | 期待結果 | 実測結果 | 判定 |
|---|-----------|---------|---------|---------|------|
| 20.1-1 | FPolicy スタックデプロイ | `aws cloudformation ...` | CREATE_COMPLETE | ... | PASS/FAIL |
| 20.1-2 | ECS Fargate タスク確認 | `aws ecs describe-tasks ...` | RUNNING, HEALTHY | ... | PASS/FAIL |
| 20.2-1 | FPolicy Lambda デプロイ | `aws lambda get-function-configuration ...` | python3.12, 設定正常 | ... | PASS/FAIL |
| 20.3-1 | FPolicy 外部エンジン作成 | `vserver fpolicy policy external-engine create ...` | 作成成功 | ... | PASS/FAIL |
| 20.3-2 | FPolicy ポリシー有効化 | `vserver fpolicy enable ...` | sequence-number 1, on | ... | PASS/FAIL |
| 20.3-3 | KeepAlive 確認 | ECS CloudWatch Logs | 120 秒間隔で受信（検索窓は 300 秒以上）| ... | PASS/FAIL |
| 20.4-1 | ファイル作成 (CIFS) | SMB 経由ファイル作成 | ファイル作成成功 | ... | PASS/FAIL |
| 20.4-2 | SQS 送信確認 | ECS Logs: `[SQS] Sent: ...` | ファイル名 (create) | ... | PASS/FAIL |
| 20.4-3 | Splunk 到着確認 (30s以内) | SPL: `index=fsxn_audit ...` | 1件以上 | ... | PASS/FAIL |
| 20.4-4 | HEC レスポンス確認 | Lambda CloudWatch Logs | {"text":"Success","code":0} | ... | PASS/FAIL |
```

## 手順

### Step 1: 既存ドキュメントの確認

```bash
# 既存の検証結果ドキュメントを確認
ls -la docs/ja/verification-results-splunk.md

# 現在の内容を確認
cat docs/ja/verification-results-splunk.md
```

### Step 2: EMS パス検証結果の記録

各テスト結果を以下の形式で記録:

```markdown
### 19.2 ARP ランサムウェア検知アラートテスト

#### ステップ 19.2-1: ARP シミュレーション実行

**コマンド:**
```
security anti-ransomware volume attack simulate -vserver <svm-name> -volume <volume-name>
```

**期待結果:** コマンドが正常に完了し、ARP が attack-detected 状態に遷移

**実測結果:** <実際の出力を記録>

**判定:** PASS / FAIL

#### ステップ 19.2-3: HEC レスポンス確認

**コマンド:**
```
aws logs tail /aws/lambda/fsxn-splunk-ems-webhook --since 3m --region ap-northeast-1
```

**期待結果:** `{"text":"Success","code":0}`

**実測結果:** <実際の HEC レスポンスを記録>

**HEC ステータスコード:** <code 値を記録>

**判定:** PASS / FAIL
```

### Step 3: FPolicy パス検証結果の記録

各テスト結果を同様の形式で記録。特に以下を重点的に記録:

- ECS Fargate タスクの状態（Running/Healthy）
- KeepAlive メッセージの間隔
- SQS 送信ログの内容
- Splunk 到着までのレイテンシ
- HEC レスポンスステータスコード

### Step 4: HEC レスポンスステータスコードの記録

各 HEC 送信の結果を一覧で記録:

```markdown
### HEC レスポンスステータスコード一覧

| テスト | HEC レスポンス | code | 判定 |
|--------|--------------|------|------|
| ARP 検知 (19.2) | `{"text":"Success","code":0}` | 0 | PASS |
| Quota 超過 (19.3) | `{"text":"Success","code":0}` | 0 | PASS |
| FPolicy ファイル操作 (20.4) | `{"text":"Success","code":0}` | 0 | PASS |
```

### Step 5: ドキュメントの更新

```bash
# ドキュメントを編集（実測値で更新）
# エディタで docs/ja/verification-results-splunk.md を開き、
# 上記セクションを追加

# プレースホルダーが残っていないことを確認
grep -c "TODO\|TBD\|\.\.\." docs/ja/verification-results-splunk.md
```

### Step 6: 最終確認

```bash
# ドキュメントの構造確認（見出しレベル）
grep "^#" docs/ja/verification-results-splunk.md

# PASS/FAIL の集計
grep -c "PASS" docs/ja/verification-results-splunk.md
grep -c "FAIL" docs/ja/verification-results-splunk.md
```

## 検証チェックリスト

- [ ] `docs/ja/verification-results-splunk.md` に EMS セクションが追加された
- [ ] `docs/ja/verification-results-splunk.md` に FPolicy セクションが追加された
- [ ] 各ステップに: ステップ番号、ステップ名、コマンド、期待結果、実測結果、判定 が含まれる
- [ ] HEC レスポンスステータスコードが記録されている
- [ ] プレースホルダー（TODO/TBD）が残っていない
- [ ] 全ステップの判定が PASS/FAIL で記入されている

## 記録テンプレート

以下のテンプレートを使用して各ステップの結果を記録:

```markdown
#### ステップ X.Y-Z: <ステップ名>

**コマンド:**
```
<実行したコマンド>
```

**期待結果:** <期待される出力/状態>

**実測結果:** <実際の出力/状態>

**判定:** PASS / FAIL

**備考:** <必要に応じて補足情報>
```

## 関連タスク

- Task 18.3: 検証結果ドキュメントの生成（監査ログパス）
- Task 19.1〜19.3: EMS Webhook パス検証
- Task 20.1〜20.4: FPolicy パス検証
- Task 21.2: EMS/FPolicy スクリーンショット検証
- Task 21.3: EMS/FPolicy 最終チェックリスト
