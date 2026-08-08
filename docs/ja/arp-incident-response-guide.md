# ARP（Autonomous Ransomware Protection）インシデント対応ガイド

🌐 **日本語**（このページ） | [English](../en/arp-incident-response-guide.md)

## 概要

ONTAP の Autonomous Ransomware Protection (ARP) がランサムウェア攻撃の疑いを検知した場合のインシデント対応手順を定義します。本ガイドは、EMS Webhook 経由で Observability プラットフォーム（Datadog 等）にアラートが到着した後のアクションフローを対象とします。

## ARP 検知の仕組み

ARP は以下の異常を AI/ML で検知します:

| 検知項目 | 説明 |
|---------|------|
| エントロピー変化 | ファイルデータのランダム性の異常な増加（暗号化の兆候） |
| ファイル拡張子変化 | 通常使用されない拡張子の出現（48 時間以内に 5 種類以上） |
| IOPS 異常 | 暗号化データを伴う異常なボリュームアクティビティの急増 |

> **ARP/AI（ONTAP 9.16.1+）**
>
> ARP/AI は有効化後**即座にアクティブ**になります。学習期間は不要です。`security anti-ransomware volume attack simulate` コマンドは ONTAP 9.17.1 には存在しません。ARP 検知をテストするには、実際のランサムウェア様のファイル操作（暗号化 + 削除 + 新拡張子付与）を実行してください。詳細は [ONTAP REST API クイックリファレンス](ontap-rest-api-reference.md) を参照。

検知時の動作:
1. **ARP スナップショットの自動作成** — `Anti_ransomware_backup`（または最新バージョンでは `Anti_ransomware_attack_backup`）プレフィックス付き
2. **EMS イベントの発行** — 攻撃検知時は `callhome.arw.activity.seen`（severity: alert）、状態変更時は `arw.volume.state`
3. **本プロジェクトの EMS Webhook 経由で Observability プラットフォームに通知**

---

## インシデント対応フロー

```
[ARP 検知] → [EMS Webhook] → [Observability アラート]
                                      ↓
                              [1. 初動対応]
                                      ↓
                              [2. 影響範囲の特定]
                                      ↓
                              [3. 攻撃の真偽判定]
                                      ↓
                    ┌─────────────────┴─────────────────┐
                    ↓                                   ↓
            [誤検知の場合]                      [攻撃確認の場合]
                    ↓                                   ↓
            [4a. 誤検知処理]                   [4b. 封じ込め]
                                                        ↓
                                               [5. データ復旧]
                                                        ↓
                                               [6. 事後対応]
```

---

## Step 1: 初動対応（検知から5分以内）

### Observability プラットフォームでの確認

Datadog（または他のベンダー）で以下を確認:

```
# Datadog 検索クエリ
source:fsxn-ems @attributes.event_name:arw.volume.state
```

確認すべき情報:
- **severity** — `alert`（高確率）or `warning`（中確率）
- **volume_name** — 影響を受けたボリューム名
- **state** — `attack-detected` or `attack-suspected`
- **timestamp** — 検知時刻

### 即座に実施すべきアクション

1. **インシデントチケットの起票** — 検知時刻、ボリューム名、severity を記録
2. **関係者への通知** — セキュリティチーム、ストレージ管理者、影響を受けるビジネスオーナー
3. **ARP スナップショットの確認** — 自動作成されたスナップショットが存在することを確認

```bash
# ONTAP CLI: Verify ARP snapshots
ssh admin@<management-ip> "volume snapshot show -vserver <svm-name> -volume <volume-name> -snapshot Anti_ransomware*"
```

---

## Step 2: 影響範囲の特定

### ONTAP CLI での調査

```bash
# Check ARP status
ssh admin@<management-ip> "security anti-ransomware volume show -vserver <svm-name> -volume <volume-name>"

# List suspect files (generate attack report)
# Note: `show-suspect-files` does NOT exist in ONTAP 9.17.1+.
# Use `generate-report` instead:
ssh admin@<management-ip> "security anti-ransomware volume attack generate-report -vserver <svm-name> -volume <volume-name> -dest-path <svm-name>:/<volume-name>/"
```

> **ONTAP バージョンに関する補足**
>
> `show-suspect-files` は最新の ONTAP バージョンでは削除されています。REST API または `attack generate-report` を使用してください。

### Observability プラットフォームでの追加調査

```
# 同一ボリュームの最近のファイル操作（FPolicy 経由）
source:fsxn-fpolicy @attributes.vserver:<svm-name>

# 同一クライアント IP からの操作
source:fsxn-fpolicy @attributes.client_ip:<suspect-ip>

# 同一ユーザーの操作
source:fsxn-fpolicy @attributes.user:<suspect-user>
```

### 確認すべき項目

| 項目 | 確認方法 | 判断基準 |
|------|---------|---------|
| 影響ボリューム数 | `security anti-ransomware volume show` | 複数ボリュームなら深刻 |
| 疑わしいファイル数 | `attack generate-report` または REST API | 20ファイル以上なら高確率 |
| 攻撃元クライアント | FPolicy ログの client_ip | 内部/外部の判定 |
| 攻撃元ユーザー | FPolicy ログの user | 正規ユーザーの侵害か |
| 攻撃の時間範囲 | EMS/FPolicy ログのタイムスタンプ | 被害範囲の推定 |

---

## Step 3: 攻撃の真偽判定

### 誤検知の可能性が高いケース

- 大量のファイル変換作業（PDF→画像変換等）を実施した直後
- バックアップソフトウェアによる暗号化バックアップ
- 開発チームによる大量のビルドアーティファクト生成
- データ移行作業中

### 攻撃の可能性が高いケース

- 未知のファイル拡張子（`.encrypted`, `.locked`, `.crypto` 等）の大量出現
- 通常業務時間外のアクティビティ
- 通常アクセスしないユーザーからの大量操作
- ランサムノート（`README.txt`, `DECRYPT_FILES.html` 等）の作成

---

## Step 4a: 誤検知の場合

```bash
# ONTAP CLI: Mark as false positive (ARP snapshot auto-deleted)
ssh admin@<management-ip> "security anti-ransomware volume attack clear-suspect -vserver <svm-name> -volume <volume-name>"
```

- ARP スナップショットは自動的に削除される
- インシデントチケットを「誤検知」としてクローズ
- 必要に応じて ARP の検知パラメータを調整

---

## Step 4b: 攻撃確認 — 封じ込め

### 即座に実施（検知から30分以内）

1. **感染クライアントのネットワーク隔離**

```bash
# Revoke infected client access via AWS Security Group
aws ec2 revoke-security-group-ingress \
  --group-id <sg-id> \
  --protocol tcp \
  --port 445 \
  --cidr <infected-client-ip>/32
```

2. **影響ボリュームへのアクセス制限**

```bash
# ONTAP CLI: Temporarily restrict CIFS share
ssh admin@<management-ip> "vserver cifs share modify -vserver <svm-name> -share-name <share-name> -access-based-enumeration false"

# Or: Restrict export policy
ssh admin@<management-ip> "export-policy rule modify -vserver <svm-name> -policyname <policy> -ruleindex <index> -clientmatch <safe-clients-only>"
```

3. **追加の ARP スナップショット作成**（手動）

```bash
# Create point-in-time snapshot
ssh admin@<management-ip> "volume snapshot create -vserver <svm-name> -volume <volume-name> -snapshot incident_response_$(date +%Y%m%d_%H%M%S)"
```

---

## Step 5: データ復旧

### ARP スナップショットからの復旧

```bash
# 1. List ARP snapshots
ssh admin@<management-ip> "volume snapshot show -vserver <svm-name> -volume <volume-name> -snapshot Anti_ransomware*"

# 2. Restore volume from snapshot
ssh admin@<management-ip> "volume snapshot restore -vserver <svm-name> -volume <volume-name> -snapshot Anti_ransomware_backup.<timestamp>"
```

### 個別ファイルの復旧（.snapshot ディレクトリ経由）

ユーザーが個別ファイルを復旧する場合:

```
# Windows (CIFS/SMB)
\\<server>\<share>\.snapshot\Anti_ransomware_backup.<timestamp>\<file-path>

# Linux (NFS)
/mnt/<volume>/.snapshot/Anti_ransomware_backup.<timestamp>/<file-path>
```

### FlexClone による安全な復旧検証

本番ボリュームを復元する前に、FlexClone で検証:

```bash
# Create FlexClone (snapshot-based)
ssh admin@<management-ip> "volume clone create -vserver <svm-name> -flexclone <clone-name> -parent-volume <volume-name> -parent-snapshot Anti_ransomware_backup.<timestamp>"

# Mount clone for verification
ssh admin@<management-ip> "volume mount -vserver <svm-name> -volume <clone-name> -junction-path /verify_recovery"
```

---

## Step 6: 事後対応

### 実施すべきアクション

1. **インシデントレポートの作成**
   - 検知時刻、対応時刻、復旧完了時刻
   - 影響範囲（ボリューム数、ファイル数、ユーザー数）
   - 根本原因（感染経路の特定）
   - 復旧方法と所要時間

2. **セキュリティ強化**
   - 感染クライアントのフォレンジック調査
   - パスワードリセット（侵害されたアカウント）
   - エンドポイントセキュリティの更新
   - ネットワークセグメンテーションの見直し

3. **ARP 設定の最適化**
   - 検知パラメータの調整（誤検知が多い場合）
   - 監視対象ボリュームの拡大
   - アラート通知先の見直し

4. **バックアップ戦略の確認**
   - SnapLock（WORM）の導入検討
   - AWS Backup との連携確認
   - RPO/RTO の再評価

---

## Observability プラットフォームでのアラート設定推奨

### Datadog Monitor 設定例

```json
{
  "name": "FSx-ONTAP ARP Ransomware Detection Alert",
  "type": "log alert",
  "query": "source:fsxn-ems @attributes.event_name:arw.volume.state @attributes.severity:alert",
  "message": "🚨 ONTAP ARP detected suspicious activity\n\nVolume: {{@attributes.parameters.volume_name}}\nState: {{@attributes.parameters.state}}\nSeverity: {{@attributes.severity}}\n\nVerify the alert and follow the incident response guide.\nResponse guide: docs/en/arp-incident-response-guide.md",
  "options": {
    "thresholds": {"critical": 0},
    "notify_no_data": false
  }
}
```

### 推奨アラートルール

| アラート名 | クエリ | 重要度 | 通知先 |
|-----------|--------|--------|--------|
| ARP 攻撃検知 | `source:fsxn-ems arw.volume.state severity:alert` | Critical | セキュリティチーム + Slack |
| ARP 疑い検知 | `source:fsxn-ems arw.volume.state severity:warning` | Warning | ストレージ管理者 |
| 大量ファイル削除 | `source:fsxn-fpolicy operation:delete` count > 100/5min | Warning | ストレージ管理者 |
| 異常な拡張子変更 | `source:fsxn-fpolicy operation:rename` + 未知拡張子 | Warning | セキュリティチーム |

---

## 必要なスクリーンショット一覧

本ガイドのデモ実行時に撮影すべきスクリーンショット:

| # | 画面 | ファイル名 | 内容 |
|---|------|-----------|------|
| 1 | Datadog Logs | `datadog-arp-detection.png` | `source:fsxn-ems arw.volume.state` の検索結果 |
| 2 | Datadog Log Detail | `datadog-arp-log-detail.png` | ARP イベントの構造化属性展開表示 |
| 3 | CloudWatch Logs | `aws-ems-lambda-logs.png` | EMS Lambda の実行ログ（成功） |
| 4 | Datadog FPolicy | `datadog-fpolicy-suspect-activity.png` | 疑わしいファイル操作の FPolicy ログ |
| 5 | ONTAP CLI | `ontap-arp-status.png` | `security anti-ransomware volume show` の出力 |
| 6 | ONTAP CLI | `ontap-arp-snapshot.png` | ARP スナップショット一覧 |

---

## 参考リンク

- [AWS Docs: Protecting your data with ARP](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/ARP.html)
- [AWS Docs: Responding to ARP alerts](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/respond-ARP.html)
- [AWS Docs: Understanding EMS alerts for ARP](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/EMS-ARP.html)
- [AWS Blog: Protecting data using ARP on FSx for ONTAP](https://aws.amazon.com/blogs/storage/protecting-data-using-autonomous-ransomware-protection-on-amazon-fsx-for-netapp-ontap/)
- [NetApp Docs: Restore data from ARP snapshots](https://docs.netapp.com/us-en/ontap/anti-ransomware/recover-data-task.html)
- [NetApp Docs: Respond to abnormal activity](https://docs.netapp.com/us-en/ontap/anti-ransomware/respond-abnormal-task.html)
- [NetApp KB: Ransomware prevention and recovery](https://kb.netapp.com/Advice_and_Troubleshooting/Data_Storage_Software/ONTAP_OS/Ransomware_prevention_and_recovery_in_ONTAP)

---

## 関連ドキュメント

- [自動応答ガイド](automated-response-guide.md) — 封じ込めアクションの詳細
- [自動応答デモ手順書](demo-automated-response.md) — Phase 4: ARP → 自動封じ込め
- [EMS 検知機能リファレンス](ems-detection-capabilities.md) — ARP 関連 EMS イベント一覧
- [ONTAP REST API クイックリファレンス](ontap-rest-api-reference.md) — API 操作時の注意点
- [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) — ARP Snapshot からの安全な復旧検証
