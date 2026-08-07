# Observability 統合補遺 — 高度なパターン & リファレンス

🌐 **日本語**（このページ） | [English](../en/observability-integration-addendum.md)

## 目的

メインドキュメントを補完する高度な Observability 統合パターン、判断ガイド、リファレンステーブル。検知戦略の選択、MITRE ATT&CK マッピング、OTel セマンティック規約、コストモデル、クロスアカウントパターン、ベンダーポータビリティをカバー。

---

## 1. 検知戦略の選択ガイド

### CloudWatch Log Alarm vs カスタムメトリクス + Anomaly Detection

| 基準 | Log Alarm | カスタムメトリクス + Anomaly Detection |
|------|-----------|--------------------------------------|
| パターンタイプ | 既知パターン（特定文字列/閾値） | 未知パターン（行動ドリフト） |
| 例 | "5 分で 10 回以上のログイン失敗" | "ログインレートが 7 日平均から 3σ 上昇" |
| セットアップ複雑性 | 低（1 クエリ、1 アラーム） | 中（メトリクスフィルター + 異常バンド） |
| 誤検知率 | 低（明示的閾値） | 高め（ML モデルのオーバーフィットの可能性） |
| 検知カバレッジ | 明示的に定義したもののみ | 新規の異常を捕捉 |
| コスト | ~$0.30/アラーム/月 | ~$0.30/メトリクス/月 + $3/異常評価 |
| 最適用途 | セキュリティ（決定論的）、コンプライアンス | パフォーマンス監視、容量計画 |

**推奨**: セキュリティ検知（ARP、認証失敗、大量削除）には Log Alarm を使用（正確なパターンが既知）。FSx for ONTAP CloudWatch メトリクス（DataWriteIOPS）の早期警告には Anomaly Detection を使用（ARP を補完）。

### Pre-ARP 早期警告: CloudWatch Anomaly Detection (IOPS)

FSx for ONTAP は `DataWriteIOPS` を CloudWatch に発行。ランサムウェアはファイル 1 から IOPS スパイクを引き起こす（ARP はファイル 20+ で検知）。このメトリクスの Anomaly Detection アラームで 10-30 秒早い警告が可能。

---

## 2. MITRE ATT&CK マッピング

### FSx for ONTAP イベント → ATT&CK テクニック

| 検知ソース | イベント | MITRE テクニック | タクティック | 自動応答？ |
|-----------|---------|----------------|-----------|-----------|
| ARP alert | `arw.volume.state` (alert) | T1486 Data Encrypted for Impact | Impact | ✅ 自動遮断（ストレージ層） |
| FPolicy | 大量ファイル削除 (>50/5分) | T1485 Data Destruction | Impact | ✅ 自動遮断（ストレージ層） |
| FPolicy | 大量ファイルリネーム (.encrypted) | T1486 Data Encrypted for Impact | Impact | ✅ 自動遮断（ストレージ層） |
| 管理監査 | 管理ログイン失敗 (>10) | T1110 Brute Force | Credential Access | ⚠️ 通知 + 調査 |
| 管理監査 | 不正な export-policy 変更 | T1562.001 Disable or Modify Tools | Defense Evasion | ⚠️ 通知 |
| FPolicy | 異常 IP からのアクセス | T1021.002 SMB/Windows Admin Shares | Lateral Movement | ⚠️ 調査 |
| 管理監査 | Snapshot 削除 | T1490 Inhibit System Recovery | Impact | ⚠️ 重大通知 |
| EMS | ARP 無効化 | T1562.001 Disable or Modify Tools | Defense Evasion | ⚠️ 重大通知 |
| 管理監査 | Name-mapping 変更 | T1098 Account Manipulation | Persistence | ⚠️ 通知 |
| EMS | SnapMirror 切断 | T1490 Inhibit System Recovery | Impact | ⚠️ 通知 |

> **スコープに関する注記**
>
> 「自動遮断」の行は、[自動応答モジュール](automated-response-guide.md)の封じ込めフェーズのアクション（ユーザー/IP ブロック、Snapshot、セッション切断）をストレージ層でのみトリガーします。根絶・復旧（侵害端末の隔離、マルウェア除去、認証情報のローテーション）は自動化されておらず、引き続き人間による対応または別の IR ツールが必要です。

> **SOC 統合**
>
> MITRE ATT&CK 連携のある SIEM（Splunk ES、Elastic SIEM、Datadog Cloud SIEM）では、各検知ルールに対応するテクニック ID をタグ付け。ATT&CK Navigator でのカバレッジ可視化が可能。

---

## 3. OpenTelemetry セマンティック規約マッピング

### ONTAP EMS フィールド → OTel セマンティック規約

| ONTAP EMS フィールド | OTel セマンティック規約 | 属性キー | 例 |
|--------------------|----------------------|---------|---|
| SVM 名 | `host.name` | `host.name` | `svm-prod-01` |
| イベント名 | `event.name` | `event.name` | `arw.volume.state` |
| 重大度 | `severity_text` | `severity_text` | `alert` |
| ユーザー名 | `user.name` | `enduser.id` | `CORP\jdoe` |
| クライアント IP | `client.address` | `client.address` | `10.0.5.99` |
| ボリューム | `service.name` | `service.name` | `vol_data` |
| ファイルパス | `file.path` | `file.path` | `/data/confidential/report.pdf` |
| 操作 | `event.action` | カスタム: `fsxn.operation` | `read` / `write` / `delete` |

---

## 4. 総所有コスト (TCO)

### フルスタック比較（100 ユーザー、10 GB ログ/月）

| コンポーネント | AWS ネイティブのみ | AWS + SIEM (Datadog) | 専用ライセンス | EC2 syslog (レガシー) |
|-------------|------------------|---------------------|--------------|-------------------|
| Syslog VPCE | $8 | $8 | — | — |
| CloudWatch Logs（ストレージ） | $5 | $5 | — | — |
| Log Alarm（5 アラーム） | $1.50 | — | — | — |
| Response Lambda | $0.51 | $0.51 | — | — |
| TTL Lambda | $0.10 | $0.10 | — | — |
| Datadog ログ取り込み | — | ~$15 | — | — |
| 専用 SWS ライセンス | — | — | $5,000-15,000/年 | — |
| EC2 インスタンス（2×t3.medium） | — | — | — | $66 |
| **月額合計** | **~$15** | **~$29** | **~$400-1,250** | **~$86** |
| **年額合計** | **~$180** | **~$350** | **$5,000-15,000** | **~$1,030** |

> **トレードオフ**
>
> 低コスト（AWS ネイティブ） vs より豊富な ML 検知（専用製品） vs より広い SIEM コンテキスト（Datadog/Splunk）。既存投資とセキュリティ成熟度に応じて選択。

---

## 5. クロスアカウント Observability パターン

```
ワークロードアカウント（FSx for ONTAP）
  ├── Syslog VPCE → CloudWatch Logs（ソース）
  ├── Response Lambda（FSx と同一 VPC）
  └── CloudWatch Logs Subscription Filter
            │
            ▼（クロスアカウント宛先）
中央セキュリティアカウント
  ├── CloudWatch Logs Destination
  ├── Log Alarms + Dashboards
  ├── SIEM 統合（Datadog/Splunk/Elastic）
  └── SNS → Response trigger（ワークロードアカウントへクロスアカウント publish）
```

---

## 6. ベンダーポータビリティマトリクス

| 切り替え対象 | 変更が必要 | 変更不要 |
|------------|-----------|---------|
| ログ送信先（Datadog → Grafana） | Lambda/OTel のエクスポーター設定 | 検知ロジック、応答パイプライン、ONTAP 設定 |
| 検知プラットフォーム（Datadog → Elastic） | SIEM ルール構文、SNS トリガー方法 | Response Lambda、ONTAP 設定、通知チェーン |
| 応答先（本モジュール → 専用製品） | 応答スタック全体 | 検知パイプライン、ログ送信先 |
| 単一ベンダー → OTel マルチバックエンド | OTel Collector 追加、エクスポーター再設定 | ONTAP 設定、検知閾値 |

**重要な洞察**: SNS トリガートピックが検知と応答の間の「ユニバーサルインターフェース」。JSON を SNS に publish できる任意のシステムがストレージ層の遮断をトリガー可能。これがポータビリティレイヤー。

---

## 7. ログ量の見積もりガイド

| ソース | 一般的なボリューム（100 ユーザー） | 影響因子 |
|--------|-------------------------------|---------|
| 管理監査（syslog） | 50-200 MB/月 | 管理操作の頻度 |
| EMS イベント | 1-10 MB/月 | インフライベントの頻度 |
| ファイルアクセス監査（EVTX） | 1-10 GB/月 | ファイル操作の集約度 |
| FPolicy（リアルタイム） | 5-50 GB/月 | ファイル作成/削除レート |
| Response Lambda ログ | <1 MB/月 | インシデント頻度 |

---

## 8. Observability ヘルスモニタリング（カナリアパターン）

監視パイプライン自体を監視:

```
EventBridge Schedule (毎時)
  → SNS publish: {"action": "health_check", "svm_name": "svm-prod"}
  → Response Lambda
  → 結果を通知トピックに publish
  → CloudWatch メトリクス: custom/ResponsePipeline/HealthStatus (1=healthy, 0=unhealthy)
  → CloudWatch Alarm: HealthStatus < 1 が 2 回連続 → アラート
```

実際のインシデント発生前にパイプライン障害（ONTAP 到達不可、Lambda 設定ミス、認証情報期限切れ）を発見可能。

---

## 関連ドキュメント

- [自動応答ガイド](automated-response-guide.md)
- [セキュリティ補遺](automated-response-security-addendum.md)
- [EMS 検知機能リファレンス](ems-detection-capabilities.md)
- [パイプライン SLO](pipeline-slo.md)
- [OTel Collector PII Redaction Cookbook](../../integrations/otel-collector/docs/ja/pii-redaction-cookbook.md)
