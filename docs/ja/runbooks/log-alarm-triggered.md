# Runbook: CloudWatch Log Alarm 発火時の対応手順

🌐 **日本語**（このページ） | [English](../../en/runbooks/log-alarm-triggered.md)

> **対象**
>
> `fsxn-sensitive-file-access-*`, `fsxn-failed-access-*`, `fsxn-bulk-delete-*`, `fsxn-user-activity-*` アラーム
> **想定初動者**
>
> セキュリティ運用チーム / SRE / ストレージ管理者
> **対応目標**
>
> MTTA < 5 分、原因特定 < 30 分

---

## 1. 初動確認（MTTA < 5 分）

### 1.1 アラーム内容の確認

SNS 通知メールまたは Slack 通知に含まれる以下を確認:

- **アラーム名** — どの検知パターンか（sensitive-file-access / failed-access / bulk-delete / user-activity）
- **ログ行** — 通知に含まれるログ行（最大 50 行）から状況を把握
- **タイムスタンプ** — いつ発生したか

### 1.2 CloudWatch コンソールで確認

```
CloudWatch Console → Alarms → 該当アラーム → History タブ
```

- State transition 時刻
- Query 結果の数値（count）
- マッチしたログ件数

> **注**
>
> 疎なパターン（`count(*) > 0`）のアラームは、マッチを含むウィンドウが評価された瞬間だけ ALARM になり直後に OK へ戻る（フラップする）ことがあります。OK に戻っていても、OK→ALARM 遷移時に SNS 通知は発報済みです。History タブで過去の遷移を確認してください。

### 1.3 Logs Insights で詳細調査

```
CloudWatch Console → Logs → Log Insights → /syslog/fsxn-admin-audit
```

直近のマッチログを確認するクエリ:

```
fields @timestamp, @message
| filter @message like /<アラームのパターン>/
| sort @timestamp desc
| limit 50
```

---

## 2. 検知パターン別の対応

### 2.1 sensitive-file-access（機密ファイルアクセス）

**確認事項**:
- 誰がアクセスしたか（ユーザー名 / クライアント IP）
- どのファイルにアクセスしたか（パス）
- アクセス種別（読み取り / 書き込み / 削除）
- そのユーザーにアクセス権限があるか

**対応フロー**:

```
正規のアクセス → アラーム閾値の調整を検討 / ホワイトリスト化
不正アクセスの疑い → Step 3 (エスカレーション) へ
判断不能 → ユーザーの所属部署に確認
```

### 2.2 failed-access-attempts（認可拒否の急増）

このクエリが数えるのは、ONTAP が `Error: not authorized` で拒否した要求（そのコマンドに必要なロールがユーザーにない）です。ログインの失敗は数えません。2026-10-09 の実行では、誤ったパスワードによる REST の要求（HTTP 401）で監査ログの行は書かれませんでした（[記録](../verification-results-cloudwatch-monitoring.md#2026-10-09-の-terraform-ログアラームモジュールの実行)）。

**確認事項**:
- 拒否されたユーザー名とそのロール（行の `<user>:<role>` の語）
- クライアント IP（単一 IP からの集中 or 分散）
- 拒否されたコマンド（ロールが含まない 1 つの作業か、さまざまなコマンドか）
- 直近でロール変更やアカウント変更があったか

**対応フロー**:

```
ロールが含まない定常作業をユーザーが実行 → ロールまたは自動化を修正
単一 IP から、さまざまなコマンドが拒否 → 権限の探索の疑い → Step 3 へ
複数アカウントまたは複数 IP → 認証情報の窃取の疑い → Step 3 へ
```

### 2.3 bulk-delete-operations（大量削除）

**確認事項**:
- 削除対象のボリューム / パス
- 削除を実行したユーザー
- 削除件数と速度（件/分）
- ONTAP ARP (Autonomous Ransomware Protection) のステータス

**対応フロー**:

```
計画的な整理作業（変更管理チケットあり） → 正常、アラーム解除
単一ユーザーによる異常速度の削除 → ランサムウェア疑い → Step 3 へ (緊急)
```

**ランサムウェア疑い時の緊急対応**:
1. ONTAP CLI でボリューム Snapshot を即時取得: `volume snapshot create -vserver <svm> -volume <vol> -snapshot emergency-$(date +%Y%m%d%H%M)`
2. 影響ユーザーの CIFS セッションを強制切断検討
3. ARP ステータス確認: `security anti-ransomware volume show`
4. セキュリティチームにエスカレーション

### 2.4 specific-user-activity（特定ユーザー監視）

**確認事項**:
- 監視対象ユーザーの操作内容
- 操作が承認済みか（変更管理チケットの有無）
- 通常の業務時間内か

**対応フロー**:

```
承認済み操作 → 記録して終了
未承認操作 → 上長確認 → Step 3 へ（必要に応じて）
```

---

## 3. エスカレーション

### エスカレーション基準

| 重大度 | 条件 | エスカレーション先 |
|--------|------|------------------|
| P1 (緊急) | ランサムウェア疑い、大量データ削除進行中 | セキュリティチーム + ストレージ管理者 + インシデント対応チーム |
| P2 (高) | 不正アクセス成功の疑い、機密データ漏洩の可能性 | セキュリティチーム + データオーナー |
| P3 (中) | ブルートフォース試行、不審なパターン | セキュリティチーム（翌営業日可） |
| P4 (低) | 正規ユーザーの操作ミス、閾値調整要 | 運用チーム内で対応 |

### エスカレーション時の情報

以下を含めて報告:
- アラーム名と発火時刻
- マッチしたログ行（SNS 通知から取得）
- 影響範囲（ボリューム名、ファイル数、ユーザー数）
- 暫定対応の有無（Snapshot 取得、セッション切断等）
- Logs Insights クエリ URL（コンソールのクエリリンク）

---

## 4. 事後対応

### 4.1 正常終了の場合

- [ ] アラーム状態が OK に復帰したことを確認
- [ ] 必要に応じて閾値 / パターンを調整
- [ ] 対応記録を残す

### 4.2 インシデントの場合

- [ ] インシデントレポート作成
- [ ] 根本原因分析 (RCA)
- [ ] 再発防止策の実装
- [ ] アラームルールの改善（検知漏れ or 誤検知の修正）
- [ ] Postmortem 実施

---

## 5. アラームチューニング

### 誤検知が多い場合

- `AlarmThreshold` を上げる（0 → 5 等）
- `QueryResultsToAlarm` を増やす（1 → 2 等、連続発生時のみ）
- クエリに除外条件を追加（`| filter @message not like /known-safe-pattern/`）

### 検知漏れがある場合

- `EvaluationFrequencyMinutes` を短縮（5 → 1 分）
- クエリのフィルタパターンを拡張
- `AlarmThreshold` を下げる
- **疎なパターンに注意** — `stats count(*)` は 0 件のとき結果行を返さない（欠損扱い）。イベントが評価ウィンドウに入らないと発火しないため、頻度を短くしてウィンドウの取りこぼしを減らす（詳細は[セットアップガイドの落とし穴節](../cloudwatch-log-alarm.md#よくある落とし穴-疎なパターンは-alarm-になりにくいe2e-検証で確認)）

### コストが高い場合

- `EvaluationFrequencyMinutes` を延長（5 → 15 分）
- クエリに `limit` 句を追加
- 不要なアラームを削除

---

## 関連ドキュメント

- [CloudWatch Log Alarm セットアップガイド](../cloudwatch-log-alarm.md)
- [検知ユースケース](../detection-use-cases.md)
- [DLQ リプレイ Runbook](./dlq-replay.md)
- [Lambda エラー Runbook](./lambda-errors.md)
- [パイプライン SLO](../pipeline-slo.md)
