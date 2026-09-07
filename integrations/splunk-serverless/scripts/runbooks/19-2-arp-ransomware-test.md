# 19.2 ARP ランサムウェア検知アラートテスト

## 概要

ONTAP の Anti-Ransomware Protection (ARP) を使用してランサムウェア攻撃をシミュレートし、`arw.volume.state` EMS イベントが EMS Webhook → Lambda → Splunk HEC 経路で Splunk に到着することを検証する手順書。

## 前提条件

- Task 19.1 が完了済み（EMS Webhook スタックがデプロイ済み）
- ONTAP CLI にアクセス可能（SSH または System Manager）
- 対象ボリュームで ARP が有効化済み（learning-mode 完了後）
- Splunk に `fsxn_ems` Index が作成済み
- スクリーンショットツール

## シナリオ概要

**ストーリー**: ONTAP の ARP 機能がボリューム上でランサムウェアの疑いのあるファイル操作パターンを検知し、`arw.volume.state` EMS イベント（severity: alert）を発行する。このイベントが EMS Webhook 経由で Splunk に到着し、セキュリティチームがリアルタイムで検知する。

**検知対象イベント:**
- EMS イベント名: `arw.volume.state`
- 重要度: `alert`
- 期待される状態: `attack-detected`

## 手順

### Step 1: ARP 状態の確認

```bash
# ONTAP CLI: ARP が有効であることを確認
ssh admin@<management-ip>

# ARP 状態を確認
security anti-ransomware volume show -vserver <svm-name>
```

**期待される出力:**
- State: `enabled` (active モード)
- Learning Period: 完了済み

### Step 2: ランサムウェア攻撃のシミュレーション

```bash
# ONTAP CLI: ランサムウェア攻撃をシミュレート
security anti-ransomware volume attack simulate -vserver <svm-name> -volume <volume-name>
```

**注意事項:**
- このコマンドは実際のデータを変更しない（シミュレーションのみ）
- ARP が `enabled` 状態でないと実行できない
- シミュレーション後、ARP は自動的に `attack-detected` 状態に遷移

### Step 3: EMS イベントの発行確認

```bash
# ONTAP CLI: EMS イベントログを確認
event log show -messagename arw.volume.state -time >5m
```

**期待される出力:**
```
Time        Node    Severity  Event
----------- ------- --------- -----
<timestamp> node-01 ALERT     arw.volume.state: ...
```

### Step 4: Lambda CloudWatch Logs で転送確認

```bash
# EMS Webhook Lambda のログを確認（120秒以内に到着すること）
aws logs tail \
  /aws/lambda/fsxn-splunk-ems-webhook \
  --since 3m \
  --region ap-northeast-1 \
  --format short
```

**確認ポイント:**
- `Forwarded EMS event to Splunk HEC` ログが表示されること
- `event_name: arw.volume.state` が記録されていること
- `severity: alert` が記録されていること

### Step 5: HEC レスポンスの確認

Lambda ログ内で Splunk HEC からのレスポンスを確認:

**期待される HEC レスポンス:**
```json
{"text":"Success","code":0}
```

### Step 6: Splunk Search で到着確認

Splunk Search で以下の SPL クエリを実行:

```spl
index=fsxn_ems sourcetype=fsxn:ems:webhook arw.volume.state earliest=-5m
```

**期待される結果（120秒以内に到着）:**
- 1件以上の EMS イベントが返される
- `event_name` フィールドが `arw.volume.state` であること
- `severity` フィールドが `alert` であること
- `volume_name` フィールドが対象ボリューム名であること
- `state` フィールドが `attack-detected` であること

### Step 7: フィールド詳細の確認

```spl
index=fsxn_ems sourcetype=fsxn:ems:webhook arw.volume.state earliest=-15m
| table _time, event_name, severity, volume_name, state
```

**必須フィールド:**
| フィールド | 期待値 |
|-----------|--------|
| `event_name` | `arw.volume.state` |
| `severity` | `alert` |
| `volume_name` | `<volume-name>` |
| `state` | `attack-detected` |

### Step 8: スクリーンショット撮影

**キャプチャ対象:**

以下の要素がすべて画面内に収まるようにスクリーンショットを撮影:

1. **Search バー**: `index=fsxn_ems sourcetype=fsxn:ems:webhook arw.volume.state` が表示されている
2. **結果件数**: EMS イベントの件数が表示されている
3. **イベント詳細**: 展開されたイベントで以下が確認できる:
   - `event_name`: `arw.volume.state`
   - `severity`: `alert`
   - `state`: `attack-detected`

### Step 9: スクリーンショット保存

```bash
# 保存先（YYYYMMDD は撮影日）
docs/screenshots/splunk/splunk-ems-arp-detection-YYYYMMDD.png

# 例: 2026年1月20日に撮影した場合
docs/screenshots/splunk/splunk-ems-arp-detection-20260120.png

# ファイルサイズ確認（500KB 以下）
ls -la docs/screenshots/splunk/splunk-ems-arp-detection-*.png

# マスキング処理
python3 docs/screenshots/mask_screenshots.py
```

### Step 10: ARP 状態のリセット

```bash
# ONTAP CLI: ARP 状態をクリア（テスト後）
security anti-ransomware volume attack clear-suspect -vserver <svm-name> -volume <volume-name>

# 状態が enabled に戻ったことを確認
security anti-ransomware volume show -vserver <svm-name> -volume <volume-name>
```

## 検証チェックリスト

- [ ] `security anti-ransomware volume attack simulate` が正常に実行された
- [ ] ONTAP EMS ログに `arw.volume.state` イベントが記録された
- [ ] Lambda CloudWatch Logs に転送成功ログが表示された
- [ ] HEC レスポンスが `{"text":"Success","code":0}` であった
- [ ] Splunk Search でイベントが 120 秒以内に到着した
- [ ] イベントに `event_name`, `severity`, `volume_name`, `state` フィールドが含まれる
- [ ] スクリーンショットが `docs/screenshots/splunk/splunk-ems-arp-detection-YYYYMMDD.png` に保存された
- [ ] ファイルサイズが 500KB 以下
- [ ] マスキング処理が完了
- [ ] ARP 状態がリセットされた

## トラブルシューティング

### ARP シミュレーションの失敗

- **原因**: ARP が `enabled` 状態でない（learning-mode 中）
- **解決**: `security anti-ransomware volume show` で状態を確認し、learning 完了を待つ

### EMS イベントの未発行

- **原因**: EMS 宛先設定が不正
- **解決**: `event destination show` で Webhook 宛先が設定されているか確認

### 120 秒以内の Splunk 到着の不成立

1. Lambda CloudWatch Logs でエラーを確認
2. HEC エンドポイントの接続性を確認
3. API Gateway のアクセスログを確認
4. ONTAP EMS 宛先の HTTP 設定を確認

### HEC レスポンスの Success 不一致

| レスポンス | 原因 | 対応 |
|-----------|------|------|
| `{"text":"Invalid token","code":4}` | HEC トークンが無効 | Secrets Manager のトークンを確認 |
| `{"text":"Incorrect index","code":7}` | Index が存在しない | Splunk で `fsxn_ems` Index を作成 |
| `{"text":"Internal server error","code":8}` | Splunk 内部エラー | Splunk サーバーの状態を確認 |

## 関連タスク

- Task 19.1: EMS Webhook 用共有テンプレートのデプロイ
- Task 19.3: Quota 超過アラートテスト
- Task 21.1: EMS/FPolicy 検証結果ドキュメントの作成
