# 19.3 Quota 超過アラートテスト

## 概要

ONTAP でソフトクォータを設定し、意図的にクォータを超過させることで `wafl.quota.softlimit.exceeded` EMS イベントを発生させ、EMS Webhook → Lambda → Splunk HEC 経路で Splunk に到着することを検証する手順書。

## 前提条件

- Task 19.1 が完了済み（EMS Webhook スタックがデプロイ済み）
- ONTAP CLI にアクセス可能（SSH または System Manager）
- テスト用ボリュームが存在すること
- CIFS/NFS 経由でデータ書き込みが可能であること
- Splunk に `fsxn_ems` Index が作成済み

## シナリオ概要

**ストーリー**: ストレージ管理者がボリュームにソフトクォータ（50MB）を設定している。ユーザーが 60MB 以上のデータを書き込むと、ONTAP が `wafl.quota.softlimit.exceeded` EMS イベント（severity: warning）を発行する。このイベントが EMS Webhook 経由で Splunk に到着し、容量管理アラートとして検知される。

**検知対象イベント:**
- EMS イベント名: `wafl.quota.softlimit.exceeded`
- 重要度: `warning`

## 手順

### Step 1: テスト用クォータの設定

```bash
# ONTAP CLI: SSH 接続
ssh admin@<management-ip>

# クォータポリシールールの作成（ソフトリミット 50MB）
volume quota policy rule create \
  -vserver <svm-name> \
  -policy-name default \
  -volume <volume-name> \
  -type tree \
  -target "" \
  -soft-disk-limit 50MB

# クォータの有効化
volume quota on -vserver <svm-name> -volume <volume-name>

# クォータ状態の確認
volume quota show -vserver <svm-name> -volume <volume-name>
```

**期待される出力:**
- Status: `on`
- Soft Disk Limit: `50MB`

### Step 2: クォータ状態の確認

```bash
# クォータレポートの確認
volume quota report -vserver <svm-name> -volume <volume-name>
```

### Step 3: ソフトクォータ超過データの書き込み

CIFS/SMB または NFS 経由で 60MB 以上のデータを書き込む:

```bash
# NFS マウント経由の場合
dd if=/dev/urandom of=/mnt/<volume-name>/test-quota-exceed.dat bs=1M count=65

# または SMB 経由（Windows/macOS）
# 65MB のテストファイルを SMB 共有にコピー
```

**注意事項:**
- ソフトクォータは書き込みをブロックしない（警告のみ）
- ハードクォータとは異なり、データは正常に書き込まれる
- EMS イベントは超過検知時に発行される

### Step 4: EMS イベントの発行確認

```bash
# ONTAP CLI: EMS イベントログを確認
event log show -messagename wafl.quota.* -time >5m
```

**期待される出力:**
```
Time        Node    Severity  Event
----------- ------- --------- -----
<timestamp> node-01 WARNING   wafl.quota.softlimit.exceeded: ...
```

### Step 5: Lambda CloudWatch Logs で転送確認

```bash
# EMS Webhook Lambda のログを確認（180秒以内に到着すること）
aws logs tail \
  /aws/lambda/fsxn-splunk-ems-webhook \
  --since 5m \
  --region ap-northeast-1 \
  --format short
```

**確認ポイント:**
- `Forwarded EMS event to Splunk HEC` ログが表示されること
- `event_name: wafl.quota.softlimit.exceeded` が記録されていること
- `severity: warning` が記録されていること

### Step 6: Splunk Search で到着確認

Splunk Search で以下の SPL クエリを実行:

```spl
index=fsxn_ems sourcetype=fsxn:ems:webhook wafl.quota.softlimit.exceeded earliest=-10m
```

**期待される結果（180秒以内に到着）:**
- 1件以上の EMS イベントが返される
- `event_name` フィールドが `wafl.quota.softlimit.exceeded` であること
- `severity` フィールドが `warning` であること

### Step 7: フィールド詳細の確認

```spl
index=fsxn_ems sourcetype=fsxn:ems:webhook wafl.quota.softlimit.exceeded earliest=-15m
| table _time, event_name, severity, volume_name, quota_target, used_bytes, limit_bytes
```

**必須フィールド:**
| フィールド | 期待値 |
|-----------|--------|
| `event_name` | `wafl.quota.softlimit.exceeded` |
| `severity` | `warning` |
| `volume_name` | `<volume-name>` |
| `quota_target` | クォータターゲット |
| `used_bytes` | 60MB 以上の値 |
| `limit_bytes` | 50MB (52428800) |

### Step 8: テストデータのクリーンアップ

```bash
# テストファイルの削除
rm /mnt/<volume-name>/test-quota-exceed.dat

# クォータの無効化（テスト後）
# ONTAP CLI:
volume quota off -vserver <svm-name> -volume <volume-name>

# クォータルールの削除
volume quota policy rule delete \
  -vserver <svm-name> \
  -policy-name default \
  -volume <volume-name> \
  -type tree \
  -target ""
```

## 検証チェックリスト

- [ ] ソフトクォータ（50MB）が正常に設定された
- [ ] 60MB 以上のデータが正常に書き込まれた
- [ ] ONTAP EMS ログに `wafl.quota.softlimit.exceeded` イベントが記録された
- [ ] Lambda CloudWatch Logs に転送成功ログが表示された
- [ ] Splunk Search でイベントが 180 秒以内に到着した
- [ ] イベントに `volume_name`, `quota_target`, `used_bytes`, `limit_bytes` フィールドが含まれる
- [ ] テストデータがクリーンアップされた

## トラブルシューティング

### クォータ設定の未反映

- **原因**: クォータが `on` になっていない
- **解決**: `volume quota on` を実行し、`volume quota show` で状態を確認

### EMS イベントの未発行

- **原因**: ソフトクォータ超過が検知されていない
- **解決**: `volume quota report` で使用量がソフトリミットを超えているか確認

### 180 秒以内の Splunk 到着の不成立

1. ONTAP EMS 宛先設定を確認: `event destination show`
2. Lambda CloudWatch Logs でエラーを確認
3. API Gateway のアクセスログを確認
4. HEC エンドポイントの接続性を確認

### クォータレポートへの使用量の未反映

- **原因**: クォータスキャンが完了していない
- **解決**: `volume quota report` を再実行し、スキャン完了を待つ

## 関連タスク

- Task 19.1: EMS Webhook 用共有テンプレートのデプロイ
- Task 19.2: ARP ランサムウェア検知アラートテスト
- Task 21.1: EMS/FPolicy 検証結果ドキュメントの作成
