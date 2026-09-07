# 18.2 スクリーンショット検証手順

## 概要

`docs/screenshots/splunk/` ディレクトリ内のスクリーンショットが命名規約、ファイル形式、サイズ制限に準拠していることを検証する手順書。

## 前提条件

- Python 3.12 がインストール済み
- スクリーンショットが `docs/screenshots/splunk/` に配置済み（Task 16.1〜17.3 完了）
- `scripts/verification/splunk_screenshot_validator.py` が存在

## 手順

### Step 1: スクリーンショットディレクトリの確認

```bash
# ディレクトリ内のファイル一覧
ls -la docs/screenshots/splunk/

# PNG ファイルのみ表示
ls -la docs/screenshots/splunk/*.png
```

### Step 2: スクリーンショット検証ツールの実行

```bash
python3 scripts/verification/splunk_screenshot_validator.py docs/screenshots/splunk/
```

### Step 3: 出力結果の確認

**期待される出力（PASS の場合）:**

```
=== Splunk Screenshot Validation ===
Directory: docs/screenshots/splunk/

[Required Files]
  Lambda CloudWatch Logs screenshot found
  Splunk Search results screenshot found
  Splunk dashboard screenshot found
  Required: 3, Found: 3

[Naming Convention]
  splunk-hec-config-20260120.png — valid
  splunk-search-results-20260120.png — valid
  splunk-dashboard-20260120.png — valid

[File Size]
  splunk-hec-config-20260120.png — 245KB (max 500KB)
  splunk-search-results-20260120.png — 312KB (max 500KB)
  splunk-dashboard-20260120.png — 198KB (max 500KB)

[File Format]
  All files are valid PNG format

[Overall Result]
  Status: PASS
```

### Step 4: 検証項目の詳細

#### 4.1 必須ファイル（3ファイル）

| # | 必須スクリーンショット | 説明 |
|---|---------------------|------|
| 1 | Lambda CloudWatch Logs | "Successfully shipped" を含むログ行とタイムスタンプ |
| 2 | Splunk Search results | `index`, `sourcetype`, `host`, `source` フィールドが見えるログエントリ |
| 3 | Splunk dashboard | FSx for ONTAP 監査ログデータを表示する1つ以上のパネル |

#### 4.2 命名規約

```
splunk-<description>-<YYYYMMDD>.png
```

| ルール | 詳細 |
|--------|------|
| プレフィックス | `splunk-` で始まる |
| description | 3〜40文字、小文字英数字とハイフンのみ (`[a-z0-9-]{3,40}`) |
| 日付 | `YYYYMMDD` 形式（8桁数字） |
| 拡張子 | `.png` |

**有効な例:**
- `splunk-hec-config-20260120.png`
- `splunk-search-results-20260120.png`
- `splunk-logs-arrival-20260120.png`

**無効な例:**
- `screenshot-20260120.png`（`splunk-` プレフィックスなし）
- `splunk-AB-20260120.png`（description が2文字で短すぎる）
- `splunk-search-results-2026.png`（日付形式が不正）
- `splunk-search-results-20260120.jpg`（拡張子が PNG でない）

#### 4.3 ファイルサイズ制限

| 制限 | 値 |
|------|-----|
| 最大サイズ | 500KB (512,000 bytes) |

#### 4.4 ファイル形式

| 検証項目 | 方法 |
|---------|------|
| PNG マジックバイト | ファイル先頭8バイトが `\x89PNG\r\n\x1a\n` |

### Step 5: 手動確認（ツールが利用できない場合）

```bash
# ファイル数の確認（.gitkeep を除く）
find docs/screenshots/splunk/ -name "*.png" | wc -l
# 期待値: 3以上

# 命名規約の確認
find docs/screenshots/splunk/ -name "*.png" | grep -E "^docs/screenshots/splunk/splunk-[a-z0-9-]{3,40}-[0-9]{8}\.png$"

# ファイルサイズの確認（500KB = 512000 bytes 以下）
find docs/screenshots/splunk/ -name "*.png" -size +500k
# 期待値: 出力なし（500KB 超のファイルがない）

# PNG 形式の確認
file docs/screenshots/splunk/*.png
# 期待値: すべて "PNG image data" と表示
```

## PASS/FAIL 判定基準

| 判定 | 条件 |
|------|------|
| **PASS** | 3つの必須ファイルが存在し、全ファイルが命名規約に準拠、500KB 以下、PNG 形式 |
| **FAIL** | 必須ファイルが不足、命名規約違反、500KB 超過、または PNG 形式でないファイルがある |

### 個別判定

| チェック項目 | PASS 条件 |
|-------------|-----------|
| 必須ファイル | 3ファイル以上が存在 |
| 命名規約 | 全 PNG ファイルが `splunk-<description>-<YYYYMMDD>.png` に準拠 |
| サイズ制限 | 全ファイルが 500KB 以下 |
| ファイル形式 | 全ファイルが PNG マジックバイトを持つ |

## 検証チェックリスト

- [ ] `docs/screenshots/splunk/` ディレクトリが存在する
- [ ] 必須スクリーンショットが3ファイル以上存在する
- [ ] 全ファイルが命名規約 `splunk-<description>-<YYYYMMDD>.png` に準拠
- [ ] 全ファイルが 500KB 以下
- [ ] 全ファイルが PNG 形式
- [ ] `splunk_screenshot_validator.py` の全体結果が PASS

## トラブルシューティング

### 500KB を超えるファイルの存在

```bash
# macOS でリサイズ
sips --resampleWidth 1280 docs/screenshots/splunk/<filename>.png

# ImageMagick でリサイズ
convert docs/screenshots/splunk/<filename>.png -resize 1280x docs/screenshots/splunk/<filename>.png

# pngquant で圧縮
pngquant --quality=65-80 docs/screenshots/splunk/<filename>.png
```

### 命名規約に違反するファイルの存在

```bash
# ファイル名を修正（例）
mv docs/screenshots/splunk/old-name.png docs/screenshots/splunk/splunk-search-results-20260120.png
```

### PNG 形式でないファイルの存在

```bash
# JPEG から PNG に変換
sips -s format png docs/screenshots/splunk/<filename>.jpg --out docs/screenshots/splunk/<filename>.png
```

### 必須ファイルが不足

- Task 16.1〜17.3 の手順に戻り、不足しているスクリーンショットを撮影

## 関連タスク

- Task 16.1: HEC 設定画面スクリーンショット
- Task 16.2: 検索結果スクリーンショット
- Task 16.3: ダッシュボードスクリーンショット
- Task 17.3: ログ到着確認スクリーンショット
- Task 18.4: 最終確認 — 全成果物の確認
