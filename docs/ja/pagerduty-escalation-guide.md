# PagerDuty エスカレーション連携ガイド

🌐 [English](../en/pagerduty-escalation-guide.md) | **日本語**

## 概要

FSx for ONTAP Observability パイプラインの CloudWatch アラームを PagerDuty にエスカレーションし、オンコールチームに即時通知する設定ガイドです。

## アーキテクチャ

```
CloudWatch Alarm (ARP 検知 / DLQ / poison-pill / 管理操作異常)
    │
    ▼ AlarmAction
SNS Topic (fsxn-pagerduty-critical)
    │
    ▼ HTTPS Subscription
PagerDuty Events API v2
    │
    ▼ Escalation Policy
オンコール担当者 (SMS / 電話 / Push / Slack)
```

## 前提条件

- PagerDuty アカウント（Free tier で検証可能）
- PagerDuty サービスの作成済み
- Events API v2 Integration Key の取得済み

## デプロイ手順

### 1. PagerDuty 側の設定

1. PagerDuty にログイン → **Services** → **+ New Service**
2. サービス名: `FSx for ONTAP Observability`
3. Escalation Policy: 既存のポリシーを選択（またはデフォルト）
4. Integration: **Events API V2** を選択
5. Integration URL をコピー（`https://events.pagerduty.com/integration/<key>/enqueue`）

### 2. CloudFormation デプロイ

```bash
aws cloudformation deploy \
  --template-file shared/templates/pagerduty-escalation.yaml \
  --stack-name fsxn-pagerduty-escalation \
  --parameter-overrides \
    PagerDutyIntegrationUrl="https://events.pagerduty.com/integration/<your-key>/enqueue" \
    EscalationLevel=critical \
  --region ap-northeast-1
```

### 3. 既存アラームに SNS Topic を接続

デプロイ後の Output `PagerDutyTopicArn` を既存のアラームに追加します。

```bash
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-pagerduty-escalation \
  --query 'Stacks[0].Outputs[?OutputKey==`PagerDutyTopicArn`].OutputValue' \
  --output text)

aws cloudwatch put-metric-alarm \
  --alarm-name fsxn-arp-ransomware-detected \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN" \
  # ... (keep existing parameters)
```

## 接続推奨アラーム

| アラーム | トリガー条件 | 重大度 | PagerDuty 接続 |
|---------|------------|--------|---------------|
| ARP ランサムウェア検知 | `arw.volume.state` severity:alert | Critical | ✅ 必須 |
| DLQ 深度超過 | DLQ messages > 0 | Critical | ✅ 必須 |
| Poison-pill 検出 | 3回連続処理失敗 | High | ✅ 推奨 |
| バッファ逆圧 | Queue depth > 100 (15分継続) | High | ○ 任意 |
| チェックポイント停滞 | 30分以上進行なし | High | ○ 任意 |
| Snapshot 大量削除 | 5回/5分 超過 | Critical | ✅ 推奨 |
| Lambda エラー率 | エラー > 5% (5分間) | High | ○ 任意 |

## エスカレーションポリシー例

```
Level 1 (即時):     ストレージ管理者      — Push通知 + Slack
Level 2 (15分後):   セキュリティチーム    — 電話 + SMS
Level 3 (30分後):   インフラ責任者       — 電話
```

## アラートのライフサイクル

```
[Alarm → ALARM]  →  PagerDuty Incident 作成 (trigger)
[Alarm → OK]     →  PagerDuty Incident 自動解決 (resolve)
```

- CloudWatch Alarm が OK に戻ると PagerDuty のインシデントも自動で resolve される
- `OKActions` にも同じ SNS Topic を設定することで自動解決が有効になる

## Severity（重大度）のマッピング

> **重要**
>
> 本テンプレートの `EscalationLevel` パラメータは **SNS トピック名を決めるだけ**（`fsxn-pagerduty-critical` 等）で、PagerDuty 側のインシデント severity を自動設定するものではありません。

CloudWatch Alarm → SNS → PagerDuty Events API v2 の標準連携では、以下の挙動になります。

| CloudWatch | PagerDuty |
|-----------|-----------|
| ALARM 遷移 | インシデント trigger（severity は PagerDuty サービス既定） |
| OK 遷移 | インシデント resolve |

**アラームごとに severity を出し分けたい場合**の選択肢:

1. **PagerDuty Event Rules**: PagerDuty サービス側で、受信ペイロード（AlarmName 等）に応じて severity/urgency を振り分ける（推奨・追加インフラ不要）
2. **EventBridge Input Transformer**: CloudWatch Alarm → EventBridge → 入力変換で `severity` フィールドを付与して PagerDuty へ。細かい制御が可能だが構成が増える
3. **tier 別 SNS トピック**: `EscalationLevel` を変えて critical/high 用の SNS トピックを分け、PagerDuty 側で別サービス（別エスカレーションポリシー）に紐づける

本テンプレートは 3 の「tier 別トピック」を前提にした最小構成です。severity をペイロードで細かく制御したい場合は 1 または 2 を併用してください。

## セキュリティ注記

- **Integration URL は秘密情報** — URL に PagerDuty integration key が埋め込まれています。CloudFormation パラメータは `NoEcho: true` でコンソール/API から隠蔽していますが、SNS サブスクリプションのエンドポイントには保存されます。デプロイ権限を持つプリンシパルと SNS トピックポリシーを機密として扱ってください
- **キーのローテーション** — PagerDuty 側で integration key をローテーションした場合、スタックを更新して新しい URL を反映してください

## コスト

| コンポーネント | 月額概算 |
|-------------|---------|
| SNS Topic | ~$0（アラーム通知回数に依存、微小） |
| SNS HTTPS 配信 | $0.60 / 100万リクエスト（通常は $0 に近い） |
| PagerDuty | Free tier: 5ユーザーまで無料 |
| **合計** | **実質 $0**（Free tier 利用時） |

## テスト方法

```bash
# Publish a test message to SNS and confirm an incident is raised in PagerDuty
aws sns publish \
  --topic-arn "$TOPIC_ARN" \
  --subject "ALARM: fsxn-test-pagerduty" \
  --message '{"AlarmName":"fsxn-test","NewStateValue":"ALARM","NewStateReason":"Test escalation"}'
```

## トラブルシューティング

| 問題 | 原因 | 対処 |
|------|------|------|
| PagerDuty にインシデントが作成されない | SNS Subscription が Pending | PagerDuty 側で Subscription Confirmation が必要（Events API v2 は自動確認） |
| Subscription が ConfirmationPending のまま | Integration URL が間違っている | URL が `/enqueue` で終わることを確認 |
| インシデントが自動解決されない | OKActions が未設定 | `--ok-actions "$TOPIC_ARN"` を追加 |
| 重複インシデント | 同一アラームが複数回 ALARM 状態になる | PagerDuty のdeduplication key（AlarmName）で自動重複排除される |

## Observability ベンダー側のアラートとの使い分け

| 通知経路 | 用途 | レイテンシ |
|---------|------|----------|
| **Datadog Monitor → PagerDuty** | ベンダー側で高度な条件検知（ML Anomaly等）→ エスカレーション | ログ到着 + 評価ウィンドウ |
| **CloudWatch Alarm → SNS → PagerDuty** (本テンプレート) | AWS インフラ層の異常（DLQ、Lambda エラー、パイプライン停止）→ エスカレーション | 即時（5分評価周期） |

**推奨**: 両方を併用する。
- ベンダー側: ログ内容ベースの検知（ARP イベント内容の分析、異常パターン）
- AWS 側: パイプライン自体の健全性（配信失敗、処理遅延、リソース障害）

## 関連リンク

- [ARP インシデント対応ガイド](./arp-incident-response-guide.md)
- [DLQ リプレイ Runbook](./runbooks/dlq-replay.md)
- [パイプライン SLO 定義](./pipeline-slo.md)
- [PagerDuty Events API v2 ドキュメント](https://developer.pagerduty.com/docs/events-api-v2/overview/)
