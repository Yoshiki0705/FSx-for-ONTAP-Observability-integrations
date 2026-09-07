# 17.1 デモシナリオ1「不正アクセス検知」実行手順

## 概要

FSx for ONTAP 監査ログの `Result=Failure`（アクセス拒否）イベントを Lambda 経由で Splunk に送信し、不正アクセス検知のデモシナリオを実行する手順書。

## 前提条件

- CloudFormation スタックがデプロイ済み（Task 15.1 完了）
- Lambda 関数 `fsxn-splunk-log-shipper` が正常動作（Task 15.2 完了）
- Splunk にログが到着済み（Task 15.3 完了）
- AWS CLI v2 が設定済み
- スクリーンショットツール

## シナリオ概要

**ストーリー**: 不正なユーザーが FSx for ONTAP ボリューム上の機密ファイルにアクセスを試みたが、権限不足でアクセスが拒否された。Splunk でこの不正アクセス試行を検知する。

**検知対象イベント:**
- `Result`: `Failure`
- `Operation`: `Read`, `Write`, `Delete` など
- `EventID`: `4663`（オブジェクトアクセス試行）

## 手順

### Step 1: テストペイロードの確認

Failure イベントを含むテストペイロードを使用します。

テストデータの場所:
```
integrations/splunk-serverless/tests/test_data/sample_audit_logs.json
```

Failure イベントの例:
```json
{
  "EventID": "4663",
  "SVMName": "svm-prod-01",
  "UserName": "unauthorized-user",
  "Operation": "Read",
  "ObjectName": "/vol/confidential/secret-report.xlsx",
  "Result": "Failure",
  "Timestamp": "2026-01-15T12:10:00Z"
}
```

### Step 2: Failure イベント送信スクリプトの実行

```bash
# スクリプトの実行
cd integrations/splunk-serverless
bash scripts/send-failure-event.sh

# または手動で Lambda を呼び出し
aws lambda invoke \
  --function-name fsxn-splunk-log-shipper \
  --payload file://tests/test_data/sample_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  /tmp/response.json

cat /tmp/response.json
```

**期待される出力:**
```json
{"statusCode": 200, "body": {"total_logs": 5, "total_shipped": 5}}
```

### Step 3: CloudWatch Logs で送信確認

```bash
aws logs tail \
  /aws/lambda/fsxn-splunk-log-shipper \
  --since 5m \
  --region ap-northeast-1 \
  --format short
```

**確認ポイント:**
- `Successfully shipped` ログが表示されること
- エラーログがないこと

### Step 4: Splunk Search で不正アクセスを検知

Splunk Search で以下の SPL クエリを実行:

```spl
index=fsxn_audit result=Failure earliest=-15m
```

**期待される結果:**
- 1件以上の Failure イベントが返される
- `result` フィールドが `Failure` であること
- `user` フィールドに不正アクセスを試みたユーザー名が表示される

### Step 5: 詳細分析クエリ

不正アクセスの詳細を分析する追加クエリ:

```spl
# ユーザー別の失敗回数
index=fsxn_audit result=Failure earliest=-15m
| stats count by user
| sort -count

# 操作別の失敗回数
index=fsxn_audit result=Failure earliest=-15m
| stats count by operation, path
| sort -count

# 時系列での失敗イベント
index=fsxn_audit result=Failure earliest=-15m
| timechart span=1m count by user
```

### Step 6: スクリーンショット撮影

**キャプチャ対象:**

以下の要素がすべて画面内に収まるようにスクリーンショットを撮影:

1. **Search バー**: `index=fsxn_audit result=Failure earliest=-15m` が表示されている
2. **結果件数**: Failure イベントの件数が表示されている
3. **イベント詳細**: 展開されたイベントで `result=Failure` が確認できる
4. **ユーザー情報**: 不正アクセスを試みたユーザー名が見える

### Step 7: ファイル保存

```bash
# 保存先ディレクトリ
docs/screenshots/splunk/

# ファイル名（YYYYMMDD は撮影日）
splunk-unauthorized-access-YYYYMMDD.png

# 例: 2026年1月20日に撮影した場合
docs/screenshots/splunk/splunk-unauthorized-access-20260120.png
```

### Step 8: ファイルサイズ確認とマスキング

```bash
# 500KB 以下であることを確認
ls -la docs/screenshots/splunk/splunk-unauthorized-access-*.png

# マスキング処理
python3 docs/screenshots/mask_screenshots.py
```

## 検証チェックリスト

- [ ] `send-failure-event.sh` が正常に実行された
- [ ] Lambda が statusCode 200 を返した
- [ ] CloudWatch Logs に `Successfully shipped` が表示された
- [ ] Splunk Search で `result=Failure` イベントが検索できた
- [ ] イベントに `user`, `operation`, `path`, `result` フィールドが含まれる
- [ ] スクリーンショットが撮影された
- [ ] ファイル名が命名規約に準拠（`splunk-unauthorized-access-YYYYMMDD.png`）
- [ ] ファイルサイズが 500KB 以下
- [ ] マスキング処理が完了

## トラブルシューティング

### Lambda の失敗

1. CloudWatch Logs でエラーメッセージを確認
2. HEC トークンが有効か確認
3. S3 Access Point へのアクセス権限を確認

### Splunk での Failure イベントの不検出

1. 時間範囲を広げて再検索: `earliest=-1h`
2. Index を確認: `index=fsxn_audit` が正しいか
3. フィールド名を確認: `result` vs `Result`（大文字小文字）

### result フィールドの未表示

- **原因**: フィールド抽出が設定されていない
- **解決**: `| spath` を追加して JSON フィールドを展開

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m
| spath
| search result=Failure
```

## 関連タスク

- Task 15.2: Lambda テストイベント送信
- Task 17.2: デモシナリオ2「ランサムウェア検知」
- Task 17.3: ログ到着確認スクリーンショット
