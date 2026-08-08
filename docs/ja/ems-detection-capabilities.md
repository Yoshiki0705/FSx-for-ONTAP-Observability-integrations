# EMS イベント検知機能 — リファレンスガイド

🌐 **日本語**（このページ） | [English](../en/ems-detection-capabilities.md)

## エグゼクティブサマリ

ONTAP EMS (Event Management System) は、FSx for ONTAP からのニアリアルタイムのイベント通知を提供します。本ガイドでは、検知可能なイベント、配信レイテンシ、配信メカニズム、および本プロジェクトで利用可能な統合パターンを体系的に整理します。

**要点:**
- EMS 配信は **Push 型**（イベント駆動）であり、ポーリングではない
- 2 つの Push パス: EMS Webhook (~30 秒) と Syslog VPCE (数秒)
- EventBridge Scheduler ポーリング (5 分) は S3 上のファイルアクセス監査ログ専用 — EMS には使用しない
- 100 以上のイベントカテゴリが利用可能、主要なものを以下にカタログ化

---

## 配信メカニズム

### Push パス 1: EMS Webhook (HTTPS POST) — ターゲットアラート推奨

```
ONTAP EMS event → HTTPS POST (即時)
  → API Gateway → Lambda → Observability プラットフォーム
```

| 属性 | 値 |
|------|---|
| 配信モデル | Push（イベント駆動） |
| レイテンシ | **~30 秒**（E2E 検証済み、東京リージョン） |
| プロトコル | HTTPS POST (TLS 1.2+) |
| フォーマット | JSON（フィールド設定可能） |
| フィルタリング | ONTAP 側フィルター（イベント名、重大度） |
| 信頼性 | At-least-once（ONTAP が失敗時にリトライ） |
| 設定 | `event notification destination create -rest-api-url <url>` |

**適用**: 即時対応が必要な重大アラート（ARP、HA フェイルオーバー、容量クリティカル）。

### Push パス 2: Syslog VPCE — 包括的ログ記録推奨

```
ONTAP log-forwarding → VPC Endpoint (TCP+TLS:6514)
  → CloudWatch Logs (マネージド syslog 取り込み)
```

| 属性 | 値 |
|------|---|
| 配信モデル | Push（ストリーム） |
| レイテンシ | **数秒〜数十秒** |
| プロトコル | TCP+TLS (6514)、TCP (1514)、または UDP (514) |
| フォーマット | Syslog (RFC 5424 / RFC 3164) |
| フィルタリング | ファシリティレベル (local0-local7) |
| 信頼性 | TCP = 確実配信; UDP = ベストエフォート |
| 設定 | `cluster log-forwarding create -destination <vpce-ip> -port 6514 -protocol tcp-encrypted` |

**適用**: CloudWatch Logs での分析・アラーム設定を含む包括的な管理監査証跡 + EMS イベント。

### Pull パス: EventBridge Scheduler (S3 AP) — ファイルアクセス監査専用

```
EventBridge Scheduler (5 分) → Lambda
  → S3 AP → EVTX/XML ファイル読み取り → 処理
```

| 属性 | 値 |
|------|---|
| 配信モデル | Pull（ポーリング） |
| レイテンシ | **5 分**（設定可能、1-60 分） |
| スコープ | ファイルアクセス監査ログのみ（S3 上の EVTX/XML） |
| EMS には使用しない | — |

> **重要**
>
> EventBridge Scheduler パスはファイルアクセス監査ログ（S3 上に保存される NFS/SMB ファイル操作の EVTX/XML）専用です。EMS イベントは上記の Push パスを使用します。

---

## EMS イベントカタログ — セキュリティ & オペレーション

### ランサムウェア / データ保護

| EMS イベント | 重大度 | 説明 | 検知ユースケース |
|-------------|--------|------|----------------|
| `arw.volume.state` | alert | ARP がランサムウェア様の活動を検知 | 即時封じ込めトリガー |
| `arw.volume.state` | warning | ARP が異常な行動を疑う | 調査トリガー |
| `arw.vserver.state` | notice | ARP モード変更（学習/アクティブ/無効） | 設定ドリフト検知 |

**ARP 検知の詳細:**
- エントロピー変化（ファイル内容がランダム化 → 暗号化の指標）
- 大量のファイル拡張子変更（20 以上のファイルで異常な新拡張子）
- 暗号化データ特性を伴う異常な IOPS 急増
- 学習期間: 30 日間（ドライランモード、ベースラインを構築）

**`arw.volume.state` alert 時の自動アクション:**
1. ONTAP が `Anti_ransomware_backup` スナップショットを自動作成
2. EMS イベント発火 → Webhook が Observability プラットフォームに配信
3. (本プロジェクト) 自動応答 Lambda がユーザー/IP をブロック可能

### 容量 & クォータ

| EMS イベント | 重大度 | 説明 | 検知ユースケース |
|-------------|--------|------|----------------|
| `wafl.vol.autoSize.done` | notice | ボリューム自動リサイズ完了 | 容量トレンド分析 |
| `wafl.vol.autoSize.fail` | error | ボリューム自動リサイズ失敗 | 緊急: 容量枯渇迫る |
| `wafl.quota.softlimit.exceeded` | warning | Qtree/ユーザーのクォータソフトリミット超過 | ユーザー/管理者に警告 |
| `wafl.quota.hardlimit.exceeded` | error | Qtree/ユーザーのクォータハードリミット超過（書込み拒否） | 重大: 本番影響 |
| `monitor.volume.full` | alert | ボリューム容量 100% | 緊急対応 |
| `monitor.volume.nearlyFull` | warning | ボリューム容量が満杯に近づいている（%設定可能） | プロアクティブアラート |

### HA / 可用性

| EMS イベント | 重大度 | 説明 | 検知ユースケース |
|-------------|--------|------|----------------|
| `cf.takeover.general` | alert | HA テイクオーバー発生 | 可用性インシデント |
| `cf.giveback.started` | notice | HA ギブバック開始 | 復旧追跡 |
| `cf.giveback.completed` | notice | HA ギブバック完了 | 復旧確認 |
| `cf.hwassist.takeover` | alert | ハードウェアアシストテイクオーバー | ハードウェア障害 |

### SnapMirror / レプリケーション

| EMS イベント | 重大度 | 説明 | 検知ユースケース |
|-------------|--------|------|----------------|
| `snapmirror.relationship.status` | warning | レプリケーション関係が不健全 | DR 整合性リスク |
| `snapmirror.relationship.transfer.failed` | error | 転送失敗 | データ保護ギャップ |
| `snapmirror.relationship.out.of.sync` | warning | ラグが閾値超過 | RPO リスク |

### ネットワーク / LIF

| EMS イベント | 重大度 | 説明 | 検知ユースケース |
|-------------|--------|------|----------------|
| `net.ifgrp.link.down` | warning | インターフェースグループのリンクダウン | 接続性劣化 |
| `lif.up` | notice | LIF がオンラインに | 復旧追跡 |
| `lif.down` | warning | LIF がオフラインに | アクセス中断 |

### セキュリティ / 認証

| EMS イベント | 重大度 | 説明 | 検知ユースケース |
|-------------|--------|------|----------------|
| `mgwd.login.failed` | warning | 管理ログイン失敗 | ブルートフォース検知 |
| `mgwd.login.succeeded` | notice | 管理ログイン成功 | 監査証跡 |
| `secd.cifsAuth.problem` | warning | CIFS/SMB 認証失敗 | AD 連携問題 |
| `secd.nfsAuth.problem` | warning | NFS 認証失敗 | エクスポートポリシー問題 |

### FPolicy

| EMS イベント | 重大度 | 説明 | 検知ユースケース |
|-------------|--------|------|----------------|
| `fpolicy.server.connect.error` | warning | FPolicy サーバー接続失敗 | 監視ギャップ |
| `fpolicy.server.connected` | notice | FPolicy サーバー接続完了 | 復旧確認 |
| `fpolicy.policy.disabled` | warning | FPolicy ポリシー無効化 | セキュリティギャップ |

### ディスク / ストレージ健全性

| EMS イベント | 重大度 | 説明 | 検知ユースケース |
|-------------|--------|------|----------------|
| `raid.rg.disk.missing` | error | RAID グループからディスク欠損 | ハードウェア障害 |
| `disk.failmsg` | alert | ディスク障害 | データ保護リスク |
| `aggr.check.failed` | error | アグリゲートチェック失敗 | データ整合性 |

---

## 統合パターン

### パターン 1: EMS Webhook → Observability プラットフォーム（直接配信）

```bash
# ONTAP CLI: Configure webhook destination
event notification destination create \
  -name datadog-webhook \
  -rest-api-url https://xxxxx.execute-api.ap-northeast-1.amazonaws.com/prod/ems

# Create filter for critical events
event filter create -filter-name critical-events
event filter rule add -filter-name critical-events \
  -type include -message-name arw.volume.state
event filter rule add -filter-name critical-events \
  -type include -message-name wafl.quota.hardlimit.exceeded
event filter rule add -filter-name critical-events \
  -type include -message-name cf.takeover.general

# Bind filter to destination
event notification create \
  -filter-name critical-events \
  -destinations datadog-webhook
```

**レイテンシ**: ~30 秒（検証済み）
**用途**: 即時対応が必要な重大アラート

### パターン 2: Syslog VPCE → CloudWatch Logs → Log Alarm

```bash
# ONTAP CLI: Configure syslog forwarding
cluster log-forwarding create \
  -destination <syslog-vpce-eni-ip> \
  -port 6514 \
  -protocol tcp-encrypted \
  -facility local7

# AWS: CloudWatch Log Alarm detects patterns
# (configured via CloudFormation, see shared/templates/cloudwatch-log-alarm.yaml)
```

**レイテンシ**: 数秒 (syslog) + 1 分 (Log Alarm 評価)
**用途**: Logs Insights クエリの柔軟性を活かした広範な監視

### パターン 3: EMS Webhook → 自動応答（本プロジェクト）

```bash
# Detection: EMS → Webhook → Datadog Monitor → SNS
# Response: SNS → Lambda → ONTAP REST API (block user / snapshot)

# Or: EMS → Webhook → CloudWatch Log Alarm → SNS → Lambda
```

**レイテンシ**: ~30 秒（検知）+ ~5 秒（応答）= ~35 秒 合計
**用途**: 自動封じ込め（ランサムウェア、内部脅威）

---

## 配信レイテンシ比較

| パス | 検知レイテンシ | アラートまでの E2E | 適用 |
|------|-------------|-----------------|------|
| EMS Webhook → Datadog Monitor | ~30 秒 | ~60 秒（モニター評価含む） | ターゲット型重大アラート |
| Syslog VPCE → CW Log Alarm | 数秒 | ~90 秒（アラーム評価期間含む） | 広範な管理監査監視 |
| EMS Webhook → 自動応答 | ~30 秒 | ~35 秒（即時アクション） | 自動封じ込め |
| EventBridge Scheduler → S3 AP | 5 分 | 5-10 分 | ファイルアクセス監査（コンプライアンス） |

---

## ONTAP EMS フィルター設定ガイド

### 利用可能なイベントの確認

```bash
# SSH to FSx for ONTAP
ssh fsxadmin@<management-ip>

# List all EMS event categories
event catalog show

# Search for specific events
event catalog show -message-name *arw*
event catalog show -message-name *quota*
event catalog show -message-name *snapmirror*

# View event details
event catalog show -message-name arw.volume.state -instance
```

### カスタムフィルターの作成

```bash
# Create filter for security events
event filter create -filter-name security-events
event filter rule add -filter-name security-events \
  -type include -message-name arw.*
event filter rule add -filter-name security-events \
  -type include -message-name mgwd.login.failed
event filter rule add -filter-name security-events \
  -type include -message-name fpolicy.*

# Create filter for capacity alerts
event filter create -filter-name capacity-events
event filter rule add -filter-name capacity-events \
  -type include -message-name wafl.quota.*
event filter rule add -filter-name capacity-events \
  -type include -message-name monitor.volume.*
event filter rule add -filter-name capacity-events \
  -type include -message-name wafl.vol.autoSize.*
```

### 設定の確認

```bash
# Show notification destinations
event notification destination show

# Show active notifications
event notification show

# Show recent EMS events
event log show -time >1h
```

---

## FAQ

**Q: EMS 配信はリアルタイムですか？バッチですか？**
A: リアルタイム（Push）です。ONTAP は EMS イベント発生時に即座に Webhook (HTTPS POST) または syslog ストリームで配信します。EMS イベントにバッチ処理やスケジュール配信はありません。

**Q: Webhook 宛先が利用不可の場合はどうなりますか？**
A: ONTAP がリトライします。正確なリトライ動作は ONTAP バージョンに依存しますが、イベントは一時的にバッファされます。確実な配信のためには、syslog パスで CloudWatch Logs（永続ストレージ）に保存しつつ、低レイテンシアラートには Webhook を併用することを推奨します。

**Q: ONTAP レベルでイベントをフィルタリングできますか？**
A: はい。ONTAP のイベントフィルターにより、イベント名パターンと重大度による include/exclude ルールを設定できます。これによりノイズと Lambda 呼び出しを削減します。フィルターに一致するイベントのみが宛先に送信されます。

**Q: Webhook 宛先はいくつ設定できますか？**
A: ONTAP は複数の通知宛先をサポートします。同じイベントを複数の宛先（例: Datadog + CloudWatch）に同時に送信できます。

**Q: Syslog VPCE パスには EMS イベントも含まれますか？CLI 監査だけですか？**
A: 両方含まれます。`cluster log-forwarding` コマンドは管理監査ログ（CLI/API 操作）と EMS イベントの両方を syslog メッセージとして送信します。ファシリティコードでの区別が可能です。

---

## 関連ドキュメント

- [アーキテクチャ進化: Syslog VPCE](architecture-evolution-syslog-vpce.md)
- [ARP インシデント対応ガイド](arp-incident-response-guide.md)
- [自動応答ガイド](automated-response-guide.md)
- [EMS Webhook セットアップ (Datadog)](../../integrations/datadog/docs/ja/ems-fpolicy-setup.md)
- [AWS Docs: EMS イベントの監視](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/ems-events.html)
- [NetApp: EMS 設定](https://docs.netapp.com/us-en/ontap/error-messages/index.html)
