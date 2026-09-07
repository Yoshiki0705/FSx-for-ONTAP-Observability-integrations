# 17.3 ログ到着確認スクリーンショット取得手順

## 概要

正常な FSx for ONTAP 監査ログが Splunk に到着していることを確認し、スクリーンショットをエビデンスとして保存する手順書。

## 前提条件

- Splunk Web にログイン済み
- Lambda テストイベント送信済み（Task 15.2 完了）
- FSx for ONTAP 監査ログが Splunk に到着済み（Task 15.3 完了）
- スクリーンショットツール

## 手順

### Step 1: Splunk Search に移動

1. Splunk Web にログイン
2. 左メニューまたは上部メニューから **Search & Reporting** をクリック
3. Search バーが表示されることを確認

### Step 2: SPL クエリの実行

Search バーに以下の SPL クエリを入力して実行:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m
```

**実行方法:**
1. Search バーにクエリを入力（またはコピー＆ペースト）
2. 時間範囲ピッカーが「Last 15 minutes」になっていることを確認
3. 緑色の **Search** ボタン（🔍）をクリック、または Enter キーを押下

### Step 3: 結果の確認

以下を確認:

| 確認項目 | 期待値 |
|---------|--------|
| 結果件数 | 1件以上（`X events` と表示） |
| sourcetype | `fsxn:ontap:audit` |
| index | `fsxn_audit` |
| イベント表示 | 構造化されたフィールドが表示される |

### Step 4: イベントの展開

1. 検索結果の最初のイベントをクリックして展開
2. 以下のフィールドが表示されることを確認:

| フィールド | 説明 | 必須 |
|-----------|------|------|
| `host` | SVM 名 | ✅ |
| `source` | ソース識別子 | ✅ |
| `sourcetype` | `fsxn:ontap:audit` | ✅ |
| `index` | `fsxn_audit` | ✅ |
| `event_type` | イベント種別 | ✅ |
| `user` | 操作ユーザー | ✅ |
| `operation` | 操作種別（Read, Write 等） | ✅ |
| `path` | ファイルパス | ✅ |
| `result` | 操作結果（Success, Failure） | ✅ |
| `svm` | SVM 名 | ✅ |

### Step 5: スクリーンショット撮影

**キャプチャ対象:**

以下の要素がすべて画面内に収まるようにスクリーンショットを撮影:

1. **Search バー**: SPL クエリ `index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m` が表示されている
2. **結果件数**: `X events (Y.YYs)` の表示
3. **展開されたイベント**: 少なくとも1件のイベントが展開され、フィールド値が見える状態
4. **タイムライン**: イベントの時間分布が表示されている

**撮影のポイント:**
- クエリバーが完全に見える状態で撮影
- 結果件数が明確に読み取れること
- 展開されたイベントのフィールド名と値が読み取れること
- ブラウザのスクロール位置を調整して全要素を1画面に収める

### Step 6: ファイル保存

```bash
# 保存先ディレクトリ
docs/screenshots/splunk/

# ファイル名（YYYYMMDD は撮影日）
splunk-logs-arrival-YYYYMMDD.png

# 例: 2026年1月20日に撮影した場合
docs/screenshots/splunk/splunk-logs-arrival-20260120.png
```

### Step 7: ファイルサイズ確認

```bash
# 500KB 以下であることを確認
ls -la docs/screenshots/splunk/splunk-logs-arrival-*.png

# 500KB を超える場合はリサイズ
sips --resampleWidth 1280 docs/screenshots/splunk/splunk-logs-arrival-YYYYMMDD.png
```

### Step 8: マスキング処理

```bash
# 機密情報をマスクしてからコミット
python3 docs/screenshots/mask_screenshots.py
```

## 代替クエリ（結果が0件の場合）

結果が0件の場合、時間範囲を広げて再試行:

```spl
# 過去1時間
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-1h

# 過去24時間
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h

# 全期間
index=fsxn_audit sourcetype=fsxn:ontap:audit
```

## 検証チェックリスト

- [ ] SPL クエリが Search バーに表示されている
- [ ] 結果件数が1件以上
- [ ] 少なくとも1件のイベントが展開されている
- [ ] `host`, `source`, `sourcetype`, `index` フィールドが確認できる
- [ ] `event_type`, `user`, `operation`, `path`, `result`, `svm` フィールドが確認できる
- [ ] スクリーンショットが撮影された
- [ ] ファイル名が命名規約に準拠（`splunk-logs-arrival-YYYYMMDD.png`）
- [ ] ファイルサイズが 500KB 以下
- [ ] マスキング処理が完了

## PASS/FAIL 判定基準

| 判定 | 条件 |
|------|------|
| **PASS** | SPL クエリで1件以上のイベントが返され、全必須フィールドが確認でき、スクリーンショットが命名規約に準拠して保存された |
| **FAIL** | イベントが0件、必須フィールドが欠落、またはスクリーンショットが保存できない |

## トラブルシューティング

### 結果が0件

1. Lambda が正常に実行されたか確認（CloudWatch Logs）
2. HEC エンドポイントへの接続を確認
3. Index 名が正しいか確認（`fsxn_audit`）
4. 時間範囲を広げて再検索

### フィールドの未表示

- **原因**: イベントが展開されていない
- **解決**: イベント行の左側の `>` をクリックして展開

### sourcetype の不一致

- **原因**: HEC トークン設定で sourcetype が正しく設定されていない
- **解決**: Task 14.2 の HEC トークン設定を再確認

## 関連タスク

- Task 15.3: Splunk Search でログ到着確認
- Task 16.2: 検索結果スクリーンショット
- Task 18.2: スクリーンショット検証
