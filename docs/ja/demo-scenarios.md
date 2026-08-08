# デモシナリオ集

🌐 **日本語**（このページ） | [English](../en/demo-scenarios.md)

## 概要

各ベンダー統合のデモ手順と想定シナリオです。実際のデモ実施時に使用してください。

---

## シナリオ 1: 不正アクセス検知 (Datadog)

### ストーリー
社内の機密ファイルに対して、権限のないユーザーからのアクセス試行を検知し、Datadog でリアルタイムにアラートを発報する。

### 手順

1. **準備**: Datadog 統合をデプロイ済み
2. **操作**: FSx for ONTAP マウントポイントで権限のないファイルにアクセス
   ```bash
   # Attempt to access a confidential file as an unauthorized user
   sudo -u testuser cat /mnt/fsxn/confidential/secret-report.pdf
   # → Permission denied (recorded as Failure in audit log)
   ```
3. **確認**: Datadog Logs で確認
   - 検索: `source:fsxn @attributes.result:Failure`
   - ダッシュボードで失敗アクセスの急増を確認
4. **アラート**: Datadog Monitor で閾値超過時に Slack/PagerDuty 通知

### 期待される結果
- 30秒以内に Datadog Logs にイベント到着
- `@attributes.user`, `@attributes.path`, `@attributes.client_ip` で追跡可能

---

## シナリオ 2: ランサムウェア検知 (Splunk + EMS)

### ストーリー
ARP/AI がランサムウェアの疑いのある活動を検知し、EMS イベント経由で Splunk にアラートを送信する。

### 手順

1. **準備**: Splunk 統合 + EMS Webhook 設定済み
2. **シミュレーション**: 大量のファイルリネーム操作を実行
   ```bash
   # Simulate ransomware-like mass rename (test environment only)
   for f in /mnt/fsxn/test-data/*.txt; do
     mv "$f" "${f}.encrypted"
   done
   ```
3. **ARP 検知**: ONTAP ARP/AI が異常を検知
   - EMS イベント `arw.volume.state` が発行される
   - 自動スナップショット `Anti_ransomware_backup_*` が作成される
4. **Splunk 確認**:
   - 検索: `index=fsxn_audit sourcetype=fsxn:ontap:audit event_type=arw*`
   - アラート: 「ARP Detection」ダッシュボードで確認

### 期待される結果
- ARP 検知から 1 分以内に Splunk にアラート到着
- Splunk ダッシュボードで攻撃タイムラインを可視化

---

## シナリオ 3: クォータ閾値超過アラート (New Relic + EMS)

### ストーリー
ユーザーのストレージ使用量がソフトクォータを超過し、管理者に New Relic 経由で通知する。

### 手順

1. **準備**: New Relic 統合 + EMS CloudWatch 連携設定済み
2. **操作**: 大容量ファイルを書き込んでクォータ超過
   ```bash
   # Create a large test file
   dd if=/dev/zero of=/mnt/fsxn/user-data/large-file.bin bs=1M count=500
   ```
3. **EMS 発行**: `wafl.quota.softlimit.exceeded` イベント
4. **New Relic 確認**:
   - Logs UI で `source:fsxn-ontap` フィルタ
   - NRQL: `SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND event_type LIKE 'wafl.quota%' SINCE 1 hour ago`

### 期待される結果
- クォータ超過から 2 分以内に New Relic にログ到着
- New Relic Alert Condition で自動通知

---

## シナリオ 4: マルチベンダー同時配信 (OTel Collector)

### ストーリー
OTel Collector を使って、同一の監査ログを Grafana Cloud と Honeycomb に同時配信する。

### 手順

1. **準備**: OTel Collector 統合デプロイ + Collector 設定
   ```yaml
   # otel-collector-config.yaml (verified working)
   exporters:
     otlp_http/grafana:
       endpoint: https://otlp-gateway-prod-ap-southeast-0.grafana.net/otlp
       headers:
         Authorization: Basic ${GRAFANA_BASIC_AUTH}
     otlp_http/honeycomb:
       endpoint: https://api.honeycomb.io
       headers:
         x-honeycomb-team: ${HONEYCOMB_KEY}
         x-honeycomb-dataset: fsxn-audit
   service:
     pipelines:
       logs:
         exporters: [otlp_http/grafana, otlp_http/honeycomb]
   ```
2. **操作**: FSx for ONTAP でファイル操作
3. **確認**:
   - Grafana: Explore → Loki → `{job="fsxn-audit"}`
   - Honeycomb: Dataset `fsxn-audit` → Query

### 期待される結果
- 両ベンダーに同一イベントが到着
- ベンダー切り替え時にコード変更不要

---

## シナリオ 5: コンプライアンス監査レポート (Elastic)

### ストーリー
四半期ごとのコンプライアンス監査のため、特定期間のファイルアクセスログを Elasticsearch で集計・レポート生成する。

### 手順

1. **準備**: Elastic 統合デプロイ済み
2. **データ蓄積**: 通常運用で監査ログが日次インデックスに蓄積
3. **レポート生成**: Kibana で可視化
   ```
   GET fsxn-audit-2026.01.*/_search
   {
     "query": {"bool": {"must": [
       {"term": {"fsxn.svm": "svm-prod-01"}},
       {"range": {"@timestamp": {"gte": "2026-01-01", "lte": "2026-03-31"}}}
     ]}},
     "aggs": {
       "by_user": {"terms": {"field": "user.name.keyword"}},
       "by_operation": {"terms": {"field": "fsxn.operation.keyword"}}
     }
   }
   ```
4. **ダッシュボード**: Kibana Dashboard でアクセスパターン可視化

---

## シナリオ 6: FPolicy リアルタイムファイル監視 (Dynatrace)

### ストーリー
特定の拡張子（.exe, .bat, .ps1）のファイルが作成された場合、即座に Dynatrace にアラートを送信する。

### 手順

1. **準備**: FPolicy + Dynatrace 統合設定済み
2. **FPolicy 設定**:
   ```bash
   fpolicy policy event create -vserver svm-prod-01 \
     -event-name suspicious-files \
     -protocol cifs \
     -file-operations create \
     -filters-on-extension exe,bat,ps1,vbs
   ```
3. **操作**: 疑わしいファイルを作成
   ```bash
   echo "test" > /mnt/fsxn/shared/malware.exe
   ```
4. **Dynatrace 確認**: Problems → Custom events

---

## シナリオ 7: CloudWatch Log Alarm によるネイティブ検知 (AWS ネイティブ)

### ストーリー
ベンダー製品を使わず、AWS だけで完結する検知。機密パスへのアクセスを 1 件でも検知したら即通知（`sensitive-file-access`）、および 5 分間に 50 件を超える削除でランサムウェアの兆候を検知（`bulk-delete-operations`）する。管理監査ログは前回構築した Syslog VPC エンドポイント経由で CloudWatch Logs に届いている前提。

### 手順

1. **準備**: 管理監査ログが CloudWatch Logs（`/syslog/fsxn-admin-audit`）に配信済み
2. **デプロイ（機密ファイルアクセス検知）**:
   ```bash
   DETECTION_TYPE=sensitive-file-access \
   TARGET_PATTERN="/vol/data/confidential" \
   CREATE_SNS_TOPIC=true \
   SNS_TOPIC_NAME=fsxn-security-alerts \
     bash shared/scripts/deploy-log-alarm.sh
   ```
3. **デプロイ（大量削除検知）**:
   ```bash
   DETECTION_TYPE=bulk-delete-operations \
   ALARM_THRESHOLD=50 \
   QUERY_RESULTS_TO_ALARM=2 \
   SNS_TOPIC_ARN=<YOUR_SNS_ARN> \
     bash shared/scripts/deploy-log-alarm.sh
   ```
4. **確認（コンソール）**: CloudWatch → Alarms に「Log alarm」タイプで表示される

   ![CloudWatch Alarms 一覧 — Log alarm タイプ表示](../screenshots/01-cloudwatch-alarms-list.png)

5. **確認（実データ）**: Logs Insights でクエリを実行し、監査ログがヒットすることを確認（下は `/volume/` フィルタで 12 件マッチ、3,482 レコード scan）

   ![Logs Insights — 監査ログのクエリ結果（/volume/ で 12 件マッチ）](../screenshots/03-logs-insights-query-result.png)

### 期待される結果
- 該当アクセスが無い間、アラームは「OK」を維持（INSUFFICIENT_DATA → OK 遷移を確認）
- 機密パスへのアクセスや閾値超過の削除が発生すると ALARM に遷移し、SNS 経由で通知（`ActionLogLineCount` 設定時はマッチしたログ行も通知に含まれる）

   ![Log alarm — 状態 OK（INSUFFICIENT_DATA → OK 遷移を確認）](../screenshots/04-log-alarm-state-ok.png)

> 詳細な手順・検知プリセット（5 種）・IAM 要件は [CloudWatch Log Alarm ガイド](cloudwatch-log-alarm.md) を参照。

---

## デモ環境セットアップチェックリスト

デモ前に一通り確認してください。スタックのデプロイ成功は、パイプラインが動作していることを
意味しません。

- [ ] FSx for ONTAP ファイルシステム稼働中
- [ ] 監査ログ有効化済み、かつ `-destination` のボリュームを把握している
- [ ] そのボリュームに FSx for ONTAP S3 Access Point がアタッチされている
- [ ] アクセスポイントの `NetworkOrigin` と Lambda の配置が一致している
      （`Internet` なら Lambda は VPC 外、`VPC` なら `VpcEnabled=true` + インターネット egress）
- [ ] 対象ベンダー統合スタックデプロイ済み
- [ ] **実 Lambda コードをアップロード済み** — テンプレートは `NotImplementedError` を投げる
      placeholder を配置する。`scripts/deploy.sh` が実施し、`scripts/verify.sh` の
      チェック 2 が確認する
- [ ] `bash integrations/<vendor>/scripts/verify.sh` が全チェック PASS
- [ ] 実際の監査イベントが 1 件以上ベンダーに到達済み
      （ONTAP のローテーションが先に必要。デモ中に気付く事態を避ける）
- [ ] テスト用ファイル/ディレクトリ準備
- [ ] ベンダー側ダッシュボード/アラート設定済み
- [ ] スクリーンショット撮影ツール準備

> **ライブデモのタイミングに関する補足**: 監査イベントは ONTAP がステージングファイルを
> ローテーションするまでシッパーから見えず、その後 5 分スケジュールの実行を待ちます。
> **ローテーション間隔 + スケジュール間隔**を見込んでください。強制ローテーションは
> `vserver audit rotate -vserver <svm-name>`。待たずにパイプラインをリハーサルする方法は
> Datadog セットアップガイドの
> [ローテーションを待たない検証](../../integrations/datadog/docs/ja/setup-guide.md#53-任意-ontap-のローテーションを待たずに検証する)
> を参照してください。

## スクリーンショット撮影ポイント

各デモで以下のスクリーンショットを撮影:

1. **Lambda CloudWatch Logs**: 正常処理のログ
2. **ベンダー UI**: ログ到着確認画面
3. **ダッシュボード**: 可視化されたデータ
4. **アラート**: 通知が発報された画面
5. **アーキテクチャ図**: 実際のリソース構成

---

## シナリオ 8: 自動インシデント対応 — SMB ユーザーブロック（AWS ネイティブ）

### ストーリー
CloudWatch Log Alarm または SIEM モニターで侵害ユーザーを検知。自動応答パイプラインが数秒以内に FSx for ONTAP 上でユーザーをブロックし、保護 Snapshot を作成し、アクティブセッションを切断 — 人間の介入なし。

### 手順

1. **セットアップ**: 自動応答スタックデプロイ済み (`shared/templates/automated-response.yaml`)
2. **トリガー**:
   ```bash
   ./shared/scripts/automated-response-cli.sh contain-smb \
     --domain CORP --user jdoe --volume vol_data \
     --reason "Simulated insider threat"
   ```
3. **確認（Lambda）**:
   ```bash
   aws logs filter-log-events \
     --log-group-name /aws/lambda/fsxn-automated-response-handler \
     --filter-pattern "contain_smb_threat" \
     --query 'events[-1].message' --output text
   ```
4. **確認（ONTAP）**:
   ```bash
   ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
   ```
5. **確認（アクセス拒否）**: ブロックされたユーザーとしてファイルアクセス → 拒否
6. **解除**:
   ```bash
   ./shared/scripts/automated-response-cli.sh unblock-smb \
     --domain CORP --user jdoe
   ```

### 期待結果
- Lambda が 3 つの封じ込めステップ（snapshot + block + disconnect）を ~5 秒で実行
- ユーザーは SVM 上の全共有にアクセス不可
- 解除後、アクセス復元
- 封じ込め詳細のメール通知を受信

> 完全な手順: [自動応答デモ手順書](demo-automated-response.md)

---

## シナリオ 9: 時間制限付きブロックと自動解除（TTL）

### ストーリー
ブロックは無期限に持続すべきではない。TTL スタックが設定可能な期間後に期限切れのブロックを自動削除し、意図しないロックアウトを防止する。

### 手順

1. **セットアップ**: TTL スタックデプロイ済み (`shared/templates/automated-response-ttl.yaml`, TTL=5分)
2. **ブロック**:
   ```bash
   ./shared/scripts/automated-response-cli.sh block-smb \
     --domain CORP --user jdoe \
     --reason "TTL demo - auto-expires in 5 minutes"
   ```
3. **待機**: TTL クリーンアップ Lambda のログを ~5 分間観察
   ```bash
   aws logs tail /aws/lambda/fsxn-automated-response-ttl-cleanup --follow
   ```
4. **確認**: TTL 失効後にブロック自動解除
   ```bash
   ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
   # → Empty (block removed)
   ```

### 期待結果
- ブロックが作成され、設定された TTL 期間アクティブ
- TTL 失効後、EventBridge Scheduler がクリーンアップ Lambda を実行
- 人間の介入なしでブロックが自動削除
- 自動削除確認の通知を送信

---

## シナリオ 10: ARP 検知 → E2E 自動遮断

### ストーリー
完全なチェーン: ONTAP ARP がランサムウェア様の行動を検知 → EMS Webhook → Observability プラットフォーム → SIEM モニター発火 → SNS → Response Lambda → ユーザーブロック + Snapshot + セッション切断。検知からストレージ層の遮断まで合計 ~65 秒。これは封じ込めフェーズのみの自動化であり、根絶（攻撃者の足がかりの除去）と復旧は対象外で、引き続き人間による対応が必要です。

### 手順

1. **セットアップ**: フルパイプラインデプロイ済み（EMS Webhook + SIEM 連携 + 自動応答）
2. **ARP シミュレーション**:
   ```bash
   ssh fsxadmin@<management-ip> \
     "security anti-ransomware volume attack simulate -vserver <svm> -volume <vol>"
   ```
3. **観察**（60 秒以内）:
   - EMS イベントが Observability プラットフォームに到着 (~30 秒)
   - SIEM モニター発火、SNS に publish
   - Response Lambda が封じ込め実行 (~5 秒)
4. **確認**:
   - ONTAP snapshot 作成済み (`incident_response_*`)
   - ユーザーブロック済み（name-mapping エントリ）
   - セッション切断済み
   - メール通知受信

### 期待結果
- ~65 秒でストレージ層の遮断と証拠保全が完了
- 遮断ステップに人間の介入は不要
- 証拠保全済み（snapshot + 監査ログ）

> 完全な手順: [自動応答デモ手順書](demo-automated-response.md) Phase 4
