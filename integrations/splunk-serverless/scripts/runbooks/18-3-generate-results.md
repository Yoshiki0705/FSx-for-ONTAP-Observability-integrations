# 18.3 検証結果ドキュメントの生成手順

## 概要

E2E 検証で得られた実測値を `docs/ja/verification-results-splunk.md` に記録し、レイテンシ測定結果とスクリーンショット参照を含む完成版ドキュメントを生成する手順書。

## 前提条件

- E2E 検証が完了済み（Task 15.1〜17.3）
- `docs/ja/verification-results-splunk.md` テンプレートが存在（Task 11.3 で作成）
- スクリーンショットが `docs/screenshots/splunk/` に配置済み
- Lambda テストイベントの実行結果が手元にある

## 手順

### Step 1: テンプレートの確認

```bash
# テンプレートの存在確認
ls -la docs/ja/verification-results-splunk.md

# 現在の内容を確認
cat docs/ja/verification-results-splunk.md
```

### Step 2: 検証日と環境情報の記入

以下の情報を実測値で更新:

| 項目 | 記入内容 | 取得方法 |
|------|---------|---------|
| 検証日 | YYYY-MM-DD 形式 | 当日の日付 |
| AWS リージョン | `ap-northeast-1` | 固定値 |
| CloudFormation スタック名 | 実際のスタック名 | `aws cloudformation list-stacks` |
| Lambda 関数名 | 実際の関数名 | スタック出力から取得 |
| Splunk 環境 | Cloud / Enterprise | 使用環境に応じて |

### Step 3: E2E レイテンシ測定

#### 3.1 S3 オブジェクト作成タイムスタンプの取得

```bash
# テストイベントで使用した S3 オブジェクトの作成時刻を取得
aws s3api head-object \
  --bucket <audit-log-bucket> \
  --key <audit-log-object-key> \
  --region ap-northeast-1 \
  --query 'LastModified' \
  --output text
```

#### 3.2 Splunk _indextime の取得

Splunk Search で以下の SPL クエリを実行:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-1h
| head 1
| eval indextime_readable=strftime(_indextime, "%Y-%m-%d %H:%M:%S")
| table _time, indextime_readable, _indextime
```

#### 3.3 レイテンシの計算

```
E2E レイテンシ = Splunk _indextime - S3 オブジェクト作成タイムスタンプ
```

**記録フォーマット:**

| 測定項目 | 値 |
|---------|-----|
| S3 オブジェクト作成時刻 | `YYYY-MM-DD HH:MM:SS UTC` |
| Splunk _indextime | `YYYY-MM-DD HH:MM:SS UTC` |
| E2E レイテンシ | `XX 秒` |

### Step 4: 各検証ステップの結果記入

以下の各ステップについて実測結果を記入:

#### 4.1 HEC トークン検証

| 項目 | 記入内容 |
|------|---------|
| 実行コマンド | `python3 scripts/verification/splunk_token_validator.py` |
| 結果 | PASS / FAIL |
| トークン形式 | UUID 形式確認済み |

#### 4.2 Lambda テストイベント送信

| 項目 | 記入内容 |
|------|---------|
| 実行コマンド | `aws lambda invoke ...` |
| statusCode | 200 |
| total_logs | 実測値 |
| total_shipped | 実測値 |

#### 4.3 Splunk ログ到着確認

| 項目 | 記入内容 |
|------|---------|
| SPL クエリ | `index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m` |
| 結果件数 | 実測値 |
| 確認フィールド | host, source, sourcetype, index, event_type, user, operation, path, result, svm |

### Step 5: スクリーンショット参照の追加

ドキュメントに以下のスクリーンショット参照を追加:

```markdown
## スクリーンショットエビデンス

### Lambda CloudWatch Logs
![Lambda Logs](../../docs/screenshots/splunk/splunk-hec-config-YYYYMMDD.png)
*Lambda が "Successfully shipped" ログを出力している状態*

### Splunk Search 結果
![Search Results](../../docs/screenshots/splunk/splunk-search-results-YYYYMMDD.png)
*FSx for ONTAP 監査ログが Splunk に到着し、構造化フィールドが表示されている状態*

### Splunk ダッシュボード
![Dashboard](../../docs/screenshots/splunk/splunk-dashboard-YYYYMMDD.png)
*FSx for ONTAP 監査ログデータを可視化するダッシュボード*
```

**注意:** `YYYYMMDD` は実際の撮影日に置き換える。

### Step 6: フィールド検証チェックリストの記入

```markdown
## フィールド検証結果

| フィールド | 期待値 | 実測値 | 判定 |
|-----------|--------|--------|------|
| host | 非空 | <実測値> | PASS |
| source | 非空 | <実測値> | PASS |
| sourcetype | fsxn:ontap:audit | <実測値> | PASS |
| index | fsxn_audit | <実測値> | PASS |
| event_type | 非空 | <実測値> | PASS |
| user | 非空 | <実測値> | PASS |
| operation | 非空 | <実測値> | PASS |
| path | 非空 | <実測値> | PASS |
| result | 非空 | <実測値> | PASS |
| svm | 非空 | <実測値> | PASS |
```

### Step 7: 最終確認と保存

```bash
# ドキュメントの文字数確認（空でないことを確認）
wc -l docs/ja/verification-results-splunk.md

# プレースホルダーが残っていないか確認
grep -n "TODO\|TBD" docs/ja/verification-results-splunk.md
```

## PASS/FAIL 判定基準

| 判定 | 条件 |
|------|------|
| **PASS** | 全検証ステップの結果が記入済み、E2E レイテンシが測定済み、スクリーンショット参照が含まれ、プレースホルダーが残っていない |
| **FAIL** | 未記入の項目がある、レイテンシが未測定、またはスクリーンショット参照が欠落 |

## 検証チェックリスト

- [ ] 検証日と環境情報が記入されている
- [ ] E2E レイテンシが測定・記録されている
- [ ] HEC トークン検証結果が記入されている
- [ ] Lambda テストイベント結果が記入されている
- [ ] Splunk ログ到着確認結果が記入されている
- [ ] フィールド検証チェックリストが完成している
- [ ] スクリーンショット参照が3件以上含まれている
- [ ] プレースホルダー（TODO, TBD）が残っていない

## トラブルシューティング

### テンプレートの不在

```bash
# Task 11.3 の成果物を確認
find docs/ -name "verification-results-splunk*"
```

### E2E レイテンシの計算の不可

- **原因**: S3 オブジェクトのタイムスタンプと Splunk _indextime のタイムゾーンが異なる
- **解決**: 両方を UTC に統一して計算

### スクリーンショットのパスが不正

- **原因**: 相対パスの起点が異なる
- **解決**: `docs/ja/` からの相対パスで `../../docs/screenshots/splunk/` を使用

## 関連タスク

- Task 11.3: 検証結果ドキュメントテンプレート作成
- Task 15.2: Lambda テストイベント送信
- Task 15.3: Splunk Search でログ到着確認
- Task 18.4: 最終確認 — 全成果物の確認
