# 16.1 HEC 設定画面スクリーンショット取得手順

## 概要

Splunk HEC（HTTP Event Collector）の設定画面を撮影し、E2E 検証エビデンスとして保存する手順書。

## 前提条件

- Splunk Web にログイン済み（管理者権限）
- HEC トークン `fsxn-audit-log-shipper` が作成済み（Task 14.2 完了）
- スクリーンショットツール（macOS: Cmd+Shift+4、Windows: Snipping Tool）

## 手順

### Step 1: HEC 設定画面に移動

1. Splunk Web にログイン
2. 上部メニューから **Settings** をクリック
3. **Data Inputs** セクションの **HTTP Event Collector** をクリック
4. HEC トークン一覧画面が表示されることを確認

### Step 2: Global Settings の確認

1. **Global Settings** ボタンをクリック
2. 以下を確認:
   - **All Tokens**: Enabled
   - **Default Source Type**: (設定に応じて)
   - **Default Index**: (設定に応じて)
3. 確認後、**Save** または **Cancel** で閉じる

### Step 3: トークン設定の確認

1. トークン一覧から `fsxn-audit-log-shipper` を見つける
2. 以下の情報が表示されていることを確認:
   - **Name**: `fsxn-audit-log-shipper`
   - **Status**: Enabled（緑色のチェックマーク）
   - **Default Index**: `fsxn_audit`
   - **Token Value**: UUID 形式（`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）

### Step 4: スクリーンショット撮影

**キャプチャ対象:**

| 要素 | 確認ポイント |
|------|-------------|
| トークン名 | `fsxn-audit-log-shipper` が表示されている |
| ステータス | Enabled（有効）であること |
| Index 割り当て | `fsxn_audit` が設定されている |
| Source Type | `fsxn:ontap:audit` が設定されている |
| Token Value | UUID 形式で表示されている（マスク可） |

**撮影のポイント:**
- ブラウザのアドレスバーが見える状態で撮影（URL で Splunk インスタンスを特定可能）
- トークンの実際の値はマスクしてよい（セキュリティ上）
- 日付・時刻が確認できる状態が望ましい

### Step 5: ファイル保存

```bash
# 保存先ディレクトリ
docs/screenshots/splunk/

# ファイル名（YYYYMMDD は撮影日）
splunk-hec-config-YYYYMMDD.png

# 例: 2026年1月20日に撮影した場合
docs/screenshots/splunk/splunk-hec-config-20260120.png
```

### Step 6: ファイルサイズ確認

```bash
# 500KB 以下であることを確認
ls -la docs/screenshots/splunk/splunk-hec-config-*.png

# 500KB を超える場合はリサイズ
# macOS:
sips --resampleWidth 1280 docs/screenshots/splunk/splunk-hec-config-YYYYMMDD.png

# ImageMagick:
convert docs/screenshots/splunk/splunk-hec-config-YYYYMMDD.png \
  -resize 1280x -quality 85 \
  docs/screenshots/splunk/splunk-hec-config-YYYYMMDD.png
```

### Step 7: マスキング処理

```bash
# 機密情報をマスクしてからコミット
python3 docs/screenshots/mask_screenshots.py
```

## 検証チェックリスト

- [ ] HEC 設定画面のスクリーンショットが撮影された
- [ ] トークン名 `fsxn-audit-log-shipper` が確認できる
- [ ] ステータスが Enabled である
- [ ] Index `fsxn_audit` が割り当てられている
- [ ] ファイル名が命名規約に準拠（`splunk-hec-config-YYYYMMDD.png`）
- [ ] ファイルサイズが 500KB 以下
- [ ] マスキング処理が完了

## トラブルシューティング

### HEC 設定画面の未表示

- **原因**: 管理者権限がない
- **解決**: `admin` ロールまたは `edit_httpauths` capability を持つユーザーでログイン

### 一覧でのトークンの未表示

- **原因**: トークンが削除された、または別の App コンテキストで作成された
- **解決**: App ドロップダウンで「All Apps」を選択して再確認

## 関連タスク

- Task 14.2: HEC Token 発行
- Task 16.2: 検索結果スクリーンショット
- Task 16.3: ダッシュボードスクリーンショット
