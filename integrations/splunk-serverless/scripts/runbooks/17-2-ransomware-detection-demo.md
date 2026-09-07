# 17.2 デモシナリオ2「ランサムウェア検知」EMS Webhook 実行手順

## 概要

ONTAP ARP（Anti-Ransomware Protection）が検知した `arw.volume.state` EMS イベントを API Gateway 経由で Splunk に送信し、ランサムウェア検知のデモシナリオを実行する手順書。

## 前提条件

- CloudFormation スタックがデプロイ済み（EMS Webhook リソース含む、Task 15.1 完了）
- API Gateway エンドポイントが稼働中
- EMS Webhook 用 API キーが Secrets Manager に登録済み
- Splunk に `fsxn_ems` Index が作成済み
- AWS CLI v2 が設定済み
- スクリーンショットツール

## シナリオ概要

**ストーリー**: ONTAP の Anti-Ransomware Protection (ARP) がボリューム上で不審なファイル操作パターン（大量の暗号化・リネーム）を検知し、`arw.volume.state` EMS イベントを発行した。このイベントが API Gateway → Lambda → Splunk HEC の経路で Splunk に到着し、セキュリティチームがリアルタイムで検知する。

**検知対象イベント:**
- `message-name`: `arw.volume.state`
- `message-severity`: `alert`
- `state`: `attack-detected`

## 手順

### Step 1: API Gateway エンドポイントの確認

```bash
# CloudFormation スタック出力から API Gateway URL を取得
aws cloudformation describe-stacks \
  --stack-name fsxn-splunk-integration \
  --region ap-northeast-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`EmsApiEndpoint`].OutputValue' \
  --output text
```

出力例: `https://xxxxxxxxxx.execute-api.ap-northeast-1.amazonaws.com`

### Step 2: API キーの確認

```bash
# Secrets Manager から API キーを取得
aws secretsmanager get-secret-value \
  --secret-id "ems-webhook-api-key" \
  --region ap-northeast-1 \
  --query 'SecretString' \
  --output text
```

### Step 3: EMS イベント送信スクリプトの実行

```bash
# スクリプトの実行
cd integrations/splunk-serverless
bash scripts/send-ems-event.sh

# または手動で curl を使用
EMS_ENDPOINT="<API_GATEWAY_URL>/ems"
API_KEY="<YOUR_API_KEY>"

curl -X POST "${EMS_ENDPOINT}" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{
    "message-name": "arw.volume.state",
    "message-severity": "alert",
    "message-timestamp": "2026-01-15T12:05:30Z",
    "parameters": {
      "volume-name": "vol_data",
      "vserver-name": "svm-prod-01",
      "state": "attack-detected",
      "attack-type": "ransomware",
      "suspect-files-count": "47",
      "snapshot-name": "Anti_ransomware_backup_2026-01-15_1205",
      "description": "Autonomous Ransomware Protection has detected suspicious file activity on volume vol_data. A protective snapshot has been created automatically."
    }
  }'
```

**期待される出力:**
```json
{"statusCode": 200, "body": {"status": "forwarded", "message-name": "arw.volume.state"}}
```

### Step 4: CloudWatch Logs で送信確認

```bash
# EMS Webhook Lambda のログを確認
aws logs tail \
  /aws/lambda/fsxn-splunk-ems-webhook \
  --since 5m \
  --region ap-northeast-1 \
  --format short
```

**確認ポイント:**
- `Forwarded EMS event to Splunk HEC` ログが表示されること
- HTTP 200 レスポンスが記録されていること
- エラーログがないこと

### Step 5: Splunk Search でランサムウェア検知を確認

Splunk Search で以下の SPL クエリを実行:

```spl
index=fsxn_ems sourcetype=fsxn:ontap:ems message-name=arw.volume.state
```

**期待される結果:**
- 1件以上の EMS イベントが返される
- `message-name` が `arw.volume.state` であること
- `message-severity` が `alert` であること
- `parameters.state` が `attack-detected` であること

### Step 6: 詳細分析クエリ

ランサムウェア検知の詳細を分析する追加クエリ:

```spl
# ARP イベントの詳細表示
index=fsxn_ems sourcetype=fsxn:ontap:ems message-name=arw.volume.state earliest=-15m
| table _time, message-severity, parameters.volume-name, parameters.state, parameters.suspect-files-count

# 重要度別の EMS イベント集計
index=fsxn_ems sourcetype=fsxn:ontap:ems earliest=-1h
| stats count by message-name, message-severity
| sort -count

# ランサムウェア検知タイムライン
index=fsxn_ems sourcetype=fsxn:ontap:ems message-name=arw.volume.state earliest=-24h
| timechart span=1h count
```

### Step 7: スクリーンショット撮影

**キャプチャ対象:**

以下の要素がすべて画面内に収まるようにスクリーンショットを撮影:

1. **Search バー**: `index=fsxn_ems sourcetype=fsxn:ontap:ems message-name=arw.volume.state` が表示されている
2. **結果件数**: EMS イベントの件数が表示されている
3. **イベント詳細**: 展開されたイベントで以下が確認できる:
   - `message-name`: `arw.volume.state`
   - `message-severity`: `alert`
   - `parameters.state`: `attack-detected`
   - `parameters.suspect-files-count`: 数値

### Step 8: ファイル保存

```bash
# 保存先ディレクトリ
docs/screenshots/splunk/

# ファイル名（YYYYMMDD は撮影日）
splunk-ransomware-detection-YYYYMMDD.png

# 例: 2026年1月20日に撮影した場合
docs/screenshots/splunk/splunk-ransomware-detection-20260120.png
```

### Step 9: ファイルサイズ確認とマスキング

```bash
# 500KB 以下であることを確認
ls -la docs/screenshots/splunk/splunk-ransomware-detection-*.png

# マスキング処理
python3 docs/screenshots/mask_screenshots.py
```

## 検証チェックリスト

- [ ] API Gateway エンドポイントが確認できた
- [ ] `send-ems-event.sh` が正常に実行された（HTTP 200）
- [ ] CloudWatch Logs に転送成功ログが表示された
- [ ] Splunk Search で `arw.volume.state` イベントが検索できた
- [ ] イベントに `message-name`, `message-severity`, `parameters` フィールドが含まれる
- [ ] `parameters.state` が `attack-detected` である
- [ ] スクリーンショットが撮影された
- [ ] ファイル名が命名規約に準拠（`splunk-ransomware-detection-YYYYMMDD.png`）
- [ ] ファイルサイズが 500KB 以下
- [ ] マスキング処理が完了

## トラブルシューティング

### API Gateway が返す 401

- **原因**: API キーが無効または未設定
- **解決**: Secrets Manager の API キーと `x-api-key` ヘッダーの値が一致するか確認

### API Gateway が返す 400

- **原因**: EMS ペイロードに必須フィールドが不足
- **解決**: `message-name`, `message-severity`, `message-timestamp` が含まれているか確認

### API Gateway が返す 502

- **原因**: Splunk HEC エンドポイントに接続できない
- **解決**: HEC エンドポイントの接続性と HEC トークンの有効性を確認

### Splunk での EMS イベントの不検出

1. Index を確認: `fsxn_ems` が存在するか
2. Sourcetype を確認: `fsxn:ontap:ems` が正しいか
3. 時間範囲を広げて再検索: `earliest=-1h`
4. Lambda CloudWatch Logs でエラーを確認

### fsxn_ems Index の不在

```bash
# Splunk CLI で Index を作成（Splunk Enterprise の場合）
splunk add index fsxn_ems -maxDataSize auto_high_volume

# Splunk Cloud の場合は管理コンソールから作成
```

## 関連タスク

- Task 15.1: CloudFormation スタックデプロイ
- Task 17.1: デモシナリオ1「不正アクセス検知」
- Task 19.2: ARP ランサムウェア検知アラートテスト（本番 ONTAP 環境）
