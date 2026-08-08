# セキュリティ監視 & インシデント対応 — ドキュメントナビゲーション

🌐 **日本語**（このページ） | [English](../en/security-monitoring-index.md)

## 概要

本プロジェクトのセキュリティ関連ドキュメント全体のナビゲーションインデックスです。ロールとタスクに応じて適切なドキュメントを見つけるために使用してください。

---

## ロール別

### ストレージ管理者
| 必要なこと | ドキュメント | キーセクション |
|-----------|-----------|-------------|
| 利用可能な EMS イベントの理解 | [EMS 検知機能リファレンス](ems-detection-capabilities.md) | イベントカタログ |
| EMS webhook 宛先の設定 | [EMS 検知機能リファレンス](ems-detection-capabilities.md) | ONTAP フィルター設定ガイド |
| ARP 検知アラートの対応 | [ARP インシデント対応ガイド](arp-incident-response-guide.md) | ステップバイステップ対応 |
| アクティブなユーザー/IP ブロックの確認 | [自動応答ガイド](automated-response-guide.md) | 運用手順 |
| syslog → CloudWatch のセットアップ | [Syslog VPCE セットアップガイド](syslog-vpce-setup-guide.md) | フルセットアップ |

### セキュリティアナリスト / SOC
| 必要なこと | ドキュメント | キーセクション |
|-----------|-----------|-------------|
| 自動ストレージ層遮断の理解 | [自動応答ガイド](automated-response-guide.md) | ブロックの仕組み |
| 評価用の完全デモ実行 | [デモ手順書](demo-automated-response.md) | 全フェーズ |
| NIST CSF 2.0 の 6 機能全体における本リポジトリの対応範囲を、封じ込め以外も含めて理解したい | [サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md) | NIST CSF 2.0 の概要 |
| ユーザー/IP/ファイルパス単位のフォレンジック調査ダッシュボード（誰が、どこから、何にアクセスしたか）を構築したい | [サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md) | Respond（対応） |
| リストア前に Snapshot がクリーンであることを検証したい（RC.RP） | [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) | 検証の仕組み |
| フィールドレベルの分類だけでなく、ファイル内容の PII を発見したい | [コンテンツレベル PII 分類スキャナー](content-classification-scanner.md) | 分類の仕組み |
| 専用セキュリティ製品との比較（DII Storage Workload Security など） | [自動応答ガイド](automated-response-guide.md) | 比較テーブル、FAQ |
| 検知レイテンシの確認 | [EMS 検知機能リファレンス](ems-detection-capabilities.md) | 配信レイテンシ比較 |

### クラウドアーキテクト / DevOps
| 必要なこと | ドキュメント | キーセクション |
|-----------|-----------|-------------|
| 自動応答のデプロイ | [自動応答ガイド](automated-response-guide.md) | デプロイ |
| CloudWatch Log Alarm のデプロイ | [CloudWatch Log Alarm ガイド](cloudwatch-log-alarm.md) | デプロイ |
| アーキテクチャ進化の理解 | [アーキテクチャ進化: Syslog VPCE](architecture-evolution-syslog-vpce.md) | Before/After |
| マルチアカウントデプロイ | [マルチアカウントデプロイ](multi-account-deployment.md) | StackSets |

### コンプライアンス / 監査
| 必要なこと | ドキュメント | キーセクション |
|-----------|-----------|-------------|
| エビデンスパックテンプレート | [コンプライアンスエビデンスパック](compliance-evidence-pack.md) | 全体 |
| データ分類（フィールドレベル） | [データ分類](data-classification.md) | PII フィールド |
| データ分類（ファイル内容レベル） | [コンテンツレベル PII 分類スキャナー](content-classification-scanner.md) | 分類の仕組み |
| 復旧ポイントがテストされクリーンだったことのエビデンス（RC.RP） | [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) | テスト、デプロイ |
| ログ保持ポリシー | [パイプライン SLO](pipeline-slo.md) | 保持期間 |
| ブロックの監査証跡 | [自動応答ガイド](automated-response-guide.md) | セキュリティ考慮事項 |

---

## 機能別

### 検知 → 応答パイプライン

```
[1] 検知の設定
    └─→ EMS 検知機能リファレンス (ems-detection-capabilities.md)
    └─→ CloudWatch Log Alarm (cloudwatch-log-alarm.md)
    └─→ FPolicy セットアップ (ベンダー別ドキュメント)

[2] 応答のデプロイ
    └─→ 自動応答ガイド (automated-response-guide.md)
    └─→ CLI ヘルパー (shared/scripts/automated-response-cli.sh)

[3] インシデント対応
    └─→ ARP インシデント対応ガイド (arp-incident-response-guide.md)
    └─→ デモ手順書 (demo-automated-response.md)
    └─→ ランブック (runbooks/)

[3.5] フォレンジック調査（横断的、全フェーズ）
    └─→ サイバーレジリエンス機能マップ (cyber-resilience-capability-map.md)
    └─→ Splunk / Datadog / Grafana / Elastic ダッシュボード（Respond セクション内のベンダー別ガイダンス）

[3.6] 復旧検証 & データ発見（Recover / Identify 機能）
    └─→ 検証済みクリーン復旧ポイントガイド (verified-recovery-point-guide.md)
    └─→ コンテンツレベル PII 分類スキャナー (content-classification-scanner.md)

[4] 運用
    └─→ PagerDuty エスカレーション (pagerduty-escalation-guide.md)
    └─→ パイプライン SLO (pipeline-slo.md)
    └─→ TTL 自動解除 (automated-response-guide.md #time-limited)
```

### ドキュメント依存関係グラフ

```
architecture-evolution-syslog-vpce.md
  ├─→ ems-detection-capabilities.md (syslog 経由の EMS イベント)
  ├─→ cloudwatch-log-alarm.md (それらのログへのアラート)
  │     └─→ automated-response-guide.md (アラートへの対応)
  │           ├─→ demo-automated-response.md (ステップバイステップ証明)
  │           └─→ arp-incident-response-guide.md (ARP 固有フロー)
  └─→ demo-scenarios.md (シナリオ 7-10)
```

---

## クイックリファレンス: 主要コマンド

### EMS 監視
```bash
# Check EMS webhook delivery
aws logs filter-log-events --log-group-name /aws/lambda/fsxn-*-ems-* --filter-pattern "arw"

# Check syslog delivery
aws logs filter-log-events --log-group-name /syslog/fsxn-admin-audit --limit 5
```

### 自動応答
```bash
# Block user
./shared/scripts/automated-response-cli.sh contain-smb --domain CORP --user jdoe --volume vol1 --reason "reason"

# Check active blocks
ssh fsxadmin@<mgmt-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
ssh fsxadmin@<mgmt-ip> "export-policy rule show -clientmatch *fsxn_auto_response*"

# Unblock
./shared/scripts/automated-response-cli.sh unblock-smb --domain CORP --user jdoe
```

### CloudWatch Log Alarm
```bash
# Deploy detection alarm
DETECTION_TYPE=sensitive-file-access bash shared/scripts/deploy-log-alarm.sh

# Check alarm state
aws cloudwatch describe-alarms --alarm-name-prefix "fsxn-" --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}'
```

---

## 関連ブログ記事

| Part | トピック | ドキュメント |
|------|---------|-----------|
| 2 | ARP + FPolicy 検知 | `arp-incident-response-guide.md` |
| 14 | Syslog VPCE セットアップ | `architecture-evolution-syslog-vpce.md`, `syslog-vpce-setup-guide.md` |
| 17 | CloudWatch Log Alarm | `cloudwatch-log-alarm.md` |
| 18 | 自動インシデント対応 | `automated-response-guide.md`, `demo-automated-response.md` |
