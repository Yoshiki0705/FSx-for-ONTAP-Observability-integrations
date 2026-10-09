# 検知ユースケース

🌐 **日本語**（このページ） | [English](../en/detection-use-cases.md)

## イベントソース選択マトリクス

| 検知ユースケース | 最適ソース | 理由 | レイテンシ |
|-------------------|-------------|-----|---------|
| ランサムウェア暗号化挙動 | EMS (ARP) | ONTAP ネイティブ ML ベース検知 | リアルタイム (webhook) |
| 大量ファイル削除 | Audit Logs or FPolicy | Audit はニアリアルタイム、FPolicy はイベント駆動 | 分 / 秒 |
| 異常な読み取りボリューム | Audit Logs | ポリシー依存、ボリューム大 | 分 |
| 不正アクセス試行 | Audit Logs | 失敗アクセスイベント (Result: Failure) | 分 |
| リアルタイムファイルブロック / DLP | FPolicy | プロトコルレベルインターセプト | サブ秒 |
| クォータ閾値超過 | EMS | ONTAP ネイティブクォータ監視 | リアルタイム (webhook) |
| 不審なユーザー行動 | FPolicy + Audit Logs | イベント駆動操作と履歴パターンの相関 | 秒 + 分 |
| 権限変更 | Audit Logs | SACL/ACL 変更イベント | 分 |

## ソース特性

| ソース | レイテンシモデル | ボリューム | 最適用途 |
|--------|--------------|--------|----------|
| ファイルアクセス監査ログ | ニアリアルタイム (Scheduler 頻度 + rotation 間隔) | 高 (特に read auditing 有効時) | コンプライアンス、フォレンジック、パターン分析 |
| EMS Webhook | リアルタイム (HTTPS push) | 低 (重要イベントのみ) | セキュリティアラート、運用監視 |
| FPolicy | イベント駆動 (TCP ストリーム) | 中〜高 (全ファイル操作) | DLP、イベント駆動監視、不審行動検知 |

## ユースケース別 Datadog Monitor 例

### ランサムウェア検知 (ARP + EMS)

```
Query: source:fsxn-ems @attributes.event_name:arw.volume.state @attributes.parameters.state:attack-detected
Threshold: critical > 0
Action: PagerDuty + Slack + 影響ボリュームのスナップショット
```

### 大量アクセス失敗 (Audit Logs)

```
Query: source:fsxn @attributes.result:Failure
Threshold: critical > 10 in 5 minutes
Action: ユーザー + クライアント IP の調査
```

### 異常なファイル削除率 (FPolicy)

```
Query: source:fsxn-fpolicy @attributes.operation_type:delete
Threshold: warning > 50 in 5 minutes, critical > 200 in 5 minutes
Action: ユーザー ID との相関
```

### 機密パスへのアクセス (Audit Logs)

```
Query: source:fsxn @attributes.path:"/vol/data/confidential/*"
Threshold: warning > 0 (機密パスへのアクセス)
Action: ログレビュー + ユーザー確認
```

## 相関パターン

高度な検知のため、ソース間で相関分析:

1. **ARP アラート + FPolicy 大量操作** — ファイルレベルの詳細でランサムウェア活動を確認
2. **アクセス失敗スパイク + 新規 IP からの成功アクセス** — 認証情報漏洩の可能性
3. **クォータ警告 + 単一ユーザーからの大量書き込み** — データ持ち出しまたは不正利用の可能性

---

## CloudWatch Log Alarm によるネイティブ検知（2026-07 GA）

CloudWatch Log Alarm を使用すると、CloudWatch Logs に配信された監査ログに対してメトリクスフィルターなしで直接アラームを作成できます。

### 対象

- **管理監査ログ** (Syslog VPC Endpoint → CloudWatch Logs): ✅ そのまま利用可能
- **ファイルアクセス監査ログ** (FSx for ONTAP S3 AP → EventBridge Scheduler → Lambda): CloudWatch Logs への転送パイプラインが別途必要

### 検知パターン例

| パターン | クエリ | 閾値 | 用途 |
|---------|--------|------|------|
| 機密パスアクセス | `filter @message like /\/vol\/data\/confidential/` | > 0 | コンプライアンス |
| 認可拒否の急増 | `filter @message like /Error: not authorized/` | > 10 | 必要なロールなしのコマンド実行 |
| 大量削除 | `filter @message like /DELETE.*::\sSuccess/ or @message like /DELETE.*::\sError/ or ...`（6 語） | > 50 | ランサムウェア兆候 |
| 特権ユーザー操作 | `filter @message like /fsxadmin:fsxadmin/ and @message not like /Pending/` | > 0 | 内部統制 |

表の最後の 3 行は、テンプレートの `failed-access-attempts`、`bulk-delete-operations`、`specific-user-activity` のクエリのフィルタです。2026-10-09 の実行で確かめたパターンから書き換えたもので、LogAlarm のクエリとしては実行していません（`unverified`）。大量削除のフィルタの全体とそれぞれの状態は、[組み込みの検知クエリ](./cloudwatch-log-alarm.md#組み込みの検知クエリ)にあります。

### デプロイ

```bash
DETECTION_TYPE=sensitive-file-access \
TARGET_PATTERN="/vol/data/confidential" \
CREATE_SNS_TOPIC=true \
  bash shared/scripts/deploy-log-alarm.sh
```

詳細: [CloudWatch Log Alarm セットアップガイド](./cloudwatch-log-alarm.md)
