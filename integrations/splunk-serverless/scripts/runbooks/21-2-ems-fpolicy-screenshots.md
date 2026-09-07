# 21.2 EMS/FPolicy スクリーンショット検証

## 概要

EMS/FPolicy 検証で撮影したスクリーンショットが `docs/screenshots/splunk/` に正しく配置されていること、命名規約に準拠していること、500KB 以下であることを確認する手順書。

## 前提条件

- Task 19.2 が完了済み（ARP ランサムウェア検知スクリーンショット撮影済み）
- Task 20.4 が完了済み（FPolicy ファイル操作スクリーンショット撮影済み）
- `docs/screenshots/splunk/` ディレクトリが存在すること

## 必須スクリーンショット一覧

| # | ファイル名パターン | 内容 | 撮影タスク |
|---|-------------------|------|-----------|
| 1 | `splunk-ems-arp-detection-YYYYMMDD.png` | ARP ランサムウェア検知イベントの Splunk Search 結果 | Task 19.2 |
| 2 | `splunk-fpolicy-file-ops-YYYYMMDD.png` | FPolicy ファイル操作イベントの Splunk Search 結果 | Task 20.4 |

## 手順

### Step 1: スクリーンショットの存在確認

```bash
# EMS ARP 検知スクリーンショット
ls -la docs/screenshots/splunk/splunk-ems-arp-detection-*.png

# FPolicy ファイル操作スクリーンショット
ls -la docs/screenshots/splunk/splunk-fpolicy-file-ops-*.png
```

**期待される出力:** 各パターンに対して1ファイル以上が存在すること

### Step 2: 命名規約の確認

命名規約: `splunk-<description>-<YYYYMMDD>.png`

**ルール:**
- プレフィックス: `splunk-`
- `<description>`: 3〜40文字、小文字英数字とハイフンのみ
- `<YYYYMMDD>`: 8桁の日付（撮影日）
- 拡張子: `.png`

```bash
# 命名規約チェック（正規表現で確認）
for f in docs/screenshots/splunk/splunk-ems-arp-detection-*.png \
         docs/screenshots/splunk/splunk-fpolicy-file-ops-*.png; do
  basename "$f" | grep -E '^splunk-[a-z0-9-]{3,40}-[0-9]{8}\.png$' > /dev/null
  if [ $? -eq 0 ]; then
    echo "[PASS] $f"
  else
    echo "[FAIL] $f — 命名規約違反"
  fi
done
```

### Step 3: ファイルサイズの確認（500KB 以下）

```bash
# ファイルサイズ確認
for f in docs/screenshots/splunk/splunk-ems-arp-detection-*.png \
         docs/screenshots/splunk/splunk-fpolicy-file-ops-*.png; do
  SIZE=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null)
  MAX_SIZE=$((500 * 1024))  # 500KB = 512000 bytes
  if [ "$SIZE" -le "$MAX_SIZE" ]; then
    echo "[PASS] $f — $(($SIZE / 1024))KB"
  else
    echo "[FAIL] $f — $(($SIZE / 1024))KB (> 500KB)"
  fi
done
```

### Step 4: PNG フォーマットの確認

```bash
# PNG マジックバイトの確認
for f in docs/screenshots/splunk/splunk-ems-arp-detection-*.png \
         docs/screenshots/splunk/splunk-fpolicy-file-ops-*.png; do
  MAGIC=$(xxd -l 8 "$f" | head -1)
  if echo "$MAGIC" | grep -q "8950 4e47 0d0a 1a0a"; then
    echo "[PASS] $f — Valid PNG"
  else
    echo "[FAIL] $f — Not a valid PNG file"
  fi
done
```

### Step 5: スクリーンショット検証ツールの実行（存在する場合）

```bash
# 検証ツールが存在する場合
if [ -f scripts/verification/splunk_screenshot_validator.py ]; then
  python3 scripts/verification/splunk_screenshot_validator.py docs/screenshots/splunk/
fi
```

### Step 6: マスキング処理の確認

```bash
# マスキング処理が実行済みか確認
# （マスキングツールが存在する場合）
if [ -f docs/screenshots/mask_screenshots.py ]; then
  python3 docs/screenshots/mask_screenshots.py --check docs/screenshots/splunk/
fi
```

### Step 7: スクリーンショット内容の目視確認

各スクリーンショットに以下の要素が含まれていることを目視確認:

**splunk-ems-arp-detection-YYYYMMDD.png:**
- [ ] Splunk Search バーに SPL クエリが表示されている
- [ ] 検索結果件数が表示されている
- [ ] イベント詳細に `arw.volume.state` が確認できる
- [ ] `severity: alert` が確認できる
- [ ] 機密情報（IP アドレス、アカウント ID 等）がマスクされている

**splunk-fpolicy-file-ops-YYYYMMDD.png:**
- [ ] Splunk Search バーに SPL クエリが表示されている
- [ ] 検索結果件数が表示されている
- [ ] イベント詳細に `operation`, `file_path` が確認できる
- [ ] `user`, `client_ip` フィールドが確認できる
- [ ] 機密情報がマスクされている

## 検証チェックリスト

- [ ] `splunk-ems-arp-detection-YYYYMMDD.png` が存在する
- [ ] `splunk-fpolicy-file-ops-YYYYMMDD.png` が存在する
- [ ] 全ファイルが命名規約に準拠している
- [ ] 全ファイルが 500KB 以下である
- [ ] 全ファイルが有効な PNG フォーマットである
- [ ] マスキング処理が完了している
- [ ] スクリーンショット内容が適切である（目視確認）

## サイズ超過時の対応

ファイルサイズが 500KB を超える場合:

```bash
# 画像の圧縮（macOS）
sips -s format png -s formatOptions low docs/screenshots/splunk/<filename>.png

# または ImageMagick を使用
convert docs/screenshots/splunk/<filename>.png \
  -quality 85 \
  -resize '1920x1080>' \
  docs/screenshots/splunk/<filename>.png

# 再度サイズ確認
ls -la docs/screenshots/splunk/<filename>.png
```

## トラブルシューティング

### スクリーンショットの不在

- **原因**: 該当タスクが未完了
- **解決**: Task 19.2 または Task 20.4 を先に完了する

### 命名規約への違反

- **原因**: ファイル名に大文字、スペース、特殊文字が含まれている
- **解決**: ファイル名を修正（`mv` コマンドでリネーム）

### ファイルサイズの 500KB 超過

- **原因**: 高解像度スクリーンショット
- **解決**: 上記の圧縮手順を実行

## 関連タスク

- Task 19.2: ARP ランサムウェア検知アラートテスト（スクリーンショット撮影）
- Task 20.4: FPolicy ファイル操作テスト（スクリーンショット撮影）
- Task 21.1: EMS/FPolicy 検証結果ドキュメントの作成
- Task 21.3: EMS/FPolicy 最終チェックリスト
