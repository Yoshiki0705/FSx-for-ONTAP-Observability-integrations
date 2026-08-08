# Workshop Hands-On Guide（半日、3.5 時間）

🌐 **日本語**（このページ） | [English](../en/workshop-hands-on-half-day.md)

## 対象者

- FSx for ONTAP Observability PoC を提供するパートナー
- AWS SA 主導の組織向けワークショップ
- 技術意思決定者向けセルフペースハンズオン

## 前提条件

- 管理者アクセスのある AWS アカウント（サンドボックス推奨）
- 監査ログが有効な FSx for ONTAP ファイルシステム（またはサンプルデータ使用の意思）
- Observability ベンダーアカウント 1 つ（無料枠で十分）
- AWS CLI v2 設定済み
- CloudFormation の基本的な知識

## アジェンダ

| 時間 | 所要時間 | モジュール | 成果 |
|------|---------|----------|------|
| 0:00 | 15 分 | **Module 0**: 環境セットアップ | CLI 確認、リポジトリクローン |
| 0:15 | 30 分 | **Module 1**: アーキテクチャ概要 | 3 つのイベントソースを理解 |
| 0:45 | 45 分 | **Module 2**: 監査ログポーラーのデプロイ | 最初のログがベンダーに到達 |
| 1:30 | 15 分 | 休憩 | — |
| 1:45 | 30 分 | **Module 3**: 確認 & クエリ | ダッシュボード + 最初のクエリ |
| 2:15 | 30 分 | **Module 4**: EMS Webhook の追加 | ランサムウェアアラートパスが動作 |
| 2:45 | 30 分 | **Module 5**: Production Readiness | SLO、セキュリティ、Go/No-Go |
| 3:15 | 15 分 | **Module 6**: まとめ & 次のステップ | PoC 計画、クリーンアップ |

---

## Module 0: 環境セットアップ（15 分）

### AWS CLI の確認

```bash
aws sts get-caller-identity
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE --region ap-northeast-1
```

### リポジトリのクローン

```bash
git clone https://github.com/Yoshiki0705/fsxn-observability-integrations.git
cd fsxn-observability-integrations
```

### ベンダーの選択

ワークショップで使用するベンダーを 1 つ選択してください。初めての方への推奨：
- **Sumo Logic** — 寛大な無料枠、JP リージョン、最もシンプルな認証
- **Datadog** — 最も完成度の高いリファレンス実装
- **Grafana Cloud** — OTLP ネイティブ、OTel に慣れたチーム向け

### ベンダー認証情報の準備

`integrations/<vendor>/README.md` のベンダー固有セットアップに従って：
1. ベンダーアカウントを作成（無料枠）
2. API 認証情報を生成
3. AWS Secrets Manager に保存

---

## Module 1: アーキテクチャ概要（30 分）

### プレゼンテーション（15 分）

以下のキーポイントをカバー：
1. FSx for ONTAP S3 Access Points — 概要とできないこと
2. 3 つのイベントソース：監査ログ、EMS Webhook、FPolicy
3. EventBridge Scheduler ポーリングの理由（S3 Event Notifications ではない理由）
4. Checkpoint パターン（SSM Parameter Store）
5. Production Readiness Levels (0-4)

### ディスカッション（15 分）

参加者に質問：
- どのイベントソースがユースケースに最も関連する？
- 現在の監査ログの可視性は？
- 現在使用している Observability プラットフォームは？

---

## Module 2: 監査ログポーラーのデプロイ（45 分）

### Step 1: 前提条件のデプロイ（未デプロイの場合）

```bash
aws cloudformation deploy \
  --template-file shared/templates/prerequisites.yaml \
  --stack-name fsxn-observability-prerequisites \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-1
```

> **FSx for ONTAP がない場合**
>
> サンプルデータモードを使用 — 通常の S3 バケットにテストファイルをアップロードし、テンプレートをそのバケットに向けます。

### Step 2: サンプルデータのアップロード（サンプルモードの場合）

```bash
# Generate and upload sample audit log
python3 shared/scripts/generate-sample-audit.py --count 10 --output /tmp/sample-audit.json
aws s3 cp /tmp/sample-audit.json s3://<your-bucket>/audit/svm-prod-01/2026/05/24/sample-001.json
```

### Step 3: ベンダー統合のデプロイ

```bash
aws cloudformation deploy \
  --template-file integrations/<vendor>/template.yaml \
  --stack-name fsxn-<vendor>-integration \
  --parameter-overrides \
    S3AccessPointArn=<your-s3-ap-arn> \
    <VendorCredentialParam>=<your-secret-arn> \
    S3BucketName=<your-bucket> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

### Step 4: 初回実行のトリガー

```bash
# Manual invoke to test immediately (don't wait for scheduler)
aws lambda invoke \
  --function-name fsxn-<vendor>-integration-shipper \
  --payload '{"source": "scheduler", "s3_access_point_arn": "<arn>", "prefix": "audit/"}' \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  /tmp/response.json

cat /tmp/response.json
```

### 成功基準

- [ ] Lambda が `statusCode: 200` を返す
- [ ] `total_shipped > 0`
- [ ] `errors: []`

---

## Module 3: 確認 & クエリ（30 分）

### Step 1: ベンダープラットフォームで確認

README のベンダー固有クエリを使用：
- **Datadog** — `source:fsxn`
- **Grafana** — `{service_name="fsxn-audit"}`
- **Splunk** — `index=fsxn_audit`
- **Sumo Logic** — `_sourceCategory=aws/fsxn/audit`
- **Elastic** — `fsxn.result: *`（Kibana Discover）
- **Honeycomb** — `WHERE service = "ontap-audit"`
- **Dynatrace** — `fetch logs | filter log.source == "fsxn-ontap"`
- **New Relic** — `SELECT * FROM Log WHERE source = 'fsxn-ontap'`

### Step 2: パイプライン健全性の確認

```bash
# Checkpoint advanced?
aws ssm get-parameter --name "/fsxn/<vendor>/audit-checkpoint" --region ap-northeast-1

# DLQ empty?
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessagesVisible
```

### Step 3: 最初のクエリを作成

「過去 1 時間にファイルにアクセスしたユーザーは誰か？」に答えるクエリを作成してください。

---

## Module 4: EMS Webhook の追加（30 分）

### Step 1: EMS テンプレートのデプロイ

```bash
aws cloudformation deploy \
  --template-file integrations/<vendor>/template-ems.yaml \
  --stack-name fsxn-<vendor>-ems \
  --parameter-overrides \
    <VendorCredentialParam>=<your-secret-arn> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

### Step 2: サンプル EMS イベントでテスト

```bash
# Get API Gateway URL from stack outputs
API_URL=$(aws cloudformation describe-stacks \
  --stack-name fsxn-<vendor>-ems \
  --query 'Stacks[0].Outputs[?OutputKey==`WebhookUrl`].OutputValue' \
  --output text)

# Send test EMS event (ransomware simulation)
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{"messageName":"arw.volume.state","severity":"alert","parameters":[{"name":"volumeName","value":"vol_data"}]}'
```

### Step 3: アラートの到達を確認

ベンダープラットフォームで EMS イベントをクエリしてください。

---

## Module 5: Production Readiness（30 分）

### ディスカッション: 現在の状態は？

Production Readiness Levels をレビュー：
- Level 1: Quickstart（今デプロイしたもの）
- Level 2: Operational PoC（ダッシュボード、アラート、リプレイテスト済み）
- Level 3: Production Baseline（DynamoDB 台帳、セキュリティレビュー）
- Level 4: Enterprise Pipeline（OTel Collector、秘匿、マルチバックエンド）

### レビュー: Pipeline SLO

[Pipeline SLO ドキュメント](https://github.com/Yoshiki0705/fsxn-observability-integrations/blob/main/docs/ja/pipeline-slo.md) をウォークスルー：
- 配信レイテンシ目標
- データ損失率目標
- Level 1 から Level 2 への Go/No-Go 基準

### レビュー: データ分類

[データ分類ガイド](https://github.com/Yoshiki0705/fsxn-observability-integrations/blob/main/docs/ja/data-classification.md) をウォークスルー：
- どのフィールドが PII か？
- どの取り扱いパターンが要件に適合するか？
- ベンダーは必要なデータレジデンシーをサポートしているか？

### 演習: PoC 成功基準の定義

各参加者が記入：
1. この PoC が支援するビジネス成果
2. 成功メトリクス（測定可能）
3. タイムライン（通常 2-4 週間）
4. Go/No-Go 判断者

---

## Module 6: まとめ & 次のステップ（15 分）

### クリーンアップ（サンドボックス使用の場合）

```bash
bash integrations/<vendor>/scripts/cleanup.sh --all
```

### 持ち帰り資料

- [ ] リポジトリリンク: github.com/Yoshiki0705/fsxn-observability-integrations
- [ ] PoC 成功基準テンプレート（Module 5 で記入済み）
- [ ] Pipeline SLO ドキュメント
- [ ] データ分類ガイド
- [ ] DLQ リプレイ Runbook

### 参加者の次のステップ

1. 自身のアカウントで実際の FSx for ONTAP 監査ログを使ってデプロイ
2. 7 日間稼働させて SLO を検証
3. ビジネススポンサーに Go/No-Go を提示
4. Go の場合: Level 2（ダッシュボード + アラート）に進む

---

## ファシリテーターノート

### ワークショップ中のよくある問題

| 問題 | 解決策 |
|------|--------|
| CloudFormation CREATE_FAILED | IAM capabilities、パラメータ値を確認 |
| Lambda タイムアウト | S3 AP ネットワークパスを確認（VPC vs 非 VPC） |
| ベンダーにログが表示されない | Secrets Manager の認証情報を確認、Lambda ログを確認 |
| DLQ にメッセージがある | Lambda エラーログで根本原因を確認 |

### タイミング調整

- 参加者が速い場合: FPolicy モジュールを追加（template-fpolicy.yaml）
- 参加者が遅い場合: Module 4（EMS）をスキップ、監査パスに集中
- FSx for ONTAP がない場合: 全体を通してサンプルデータモードを使用

### 必要な事前準備（ファシリテーター）

1. ワークショップアカウントに前提条件スタックを事前デプロイ
2. サンプル監査データを S3 に事前アップロード
3. ベンダーアカウントを事前作成（参加者ごとまたは共有）
4. ワークショップ前にフルフローをエンドツーエンドでテスト
5. PoC 成功基準テンプレートを印刷/共有
