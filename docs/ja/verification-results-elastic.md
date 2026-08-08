# Elastic 統合 動作確認結果

🌐 **日本語**（このページ） | [English](../en/verification-results-elastic.md)

## 実施概要

- **検証日時** — 2026-05-24T10:51:00+09:00
- **検証環境** — 検証環境（ap-northeast-1）

---

## 環境情報

| 項目 | 値 |
|------|-----|
| AWS リージョン | ap-northeast-1 |
| AWS アカウント ID | ****6981 |
| CloudFormation スタック名 | fsxn-elastic-integration |
| Lambda 関数名 | fsxn-elastic-integration-shipper |
| Elastic Cloud プロジェクト | My Elasticsearch project |
| Elastic Cloud タイプ | Serverless |
| Elastic Cloud リージョン | ap-northeast-1 (Tokyo, AWS) |
| Elasticsearch エンドポイント | https://my-elasticsearch-project-****45.es.ap-northeast-1.aws.elastic.cloud:443 |
| Kibana URL | https://my-elasticsearch-project-****45.kb.ap-northeast-1.aws.elastic.cloud |
| S3 Access Point ARN | arn:aws:s3:ap-northeast-1:****6981:accesspoint/fsxn-audit-logs-ap |

---

## テスト結果サマリー

| ステップ | 名称 | 結果 |
|---------|------|------|
| 1 | Elastic Cloud アカウント作成 | ✅ PASS |
| 2 | CloudFormation スタックデプロイ | ✅ PASS |
| 3 | Lambda テストイベント送信 | ✅ PASS |
| 4 | Kibana Discover でログ到着確認 | ✅ PASS |
| 5 | セットアップガイド日英対応確認 | ✅ PASS |
| 6 | スクリーンショット検証 | ✅ PASS |

---

## 各ステップの詳細結果

### ステップ 1: Elastic Cloud アカウント作成

- **結果** — ✅ PASS

- **作成方法** — Google OAuth（Playwright 自動操作）
- **プロジェクトタイプ** — Elasticsearch Serverless
- **Cloud Provider** — AWS
- **リージョン** — ap-northeast-1 (Tokyo)
- **API Key 作成** — Kibana → Stack Management → Security → API Keys → Create

```bash
# Register API Key in Secrets Manager
aws secretsmanager create-secret \
  --name "elastic/fsxn-api-key" \
  --secret-string '{"api_key":"<base64_encoded_key>"}' \
  --region ap-northeast-1
```

---

### ステップ 2: CloudFormation スタックデプロイ

- **結果** — ✅ PASS

```bash
aws cloudformation deploy \
  --template-file integrations/elastic/template.yaml \
  --stack-name fsxn-elastic-integration \
  --parameter-overrides \
    S3AccessPointArn=arn:aws:s3:ap-northeast-1:****6981:accesspoint/fsxn-audit-logs-ap \
    ElasticApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:****6981:secret:elastic/fsxn-api-key-XXXXXX \
    ElasticEndpoint=https://my-elasticsearch-project-****45.es.ap-northeast-1.aws.elastic.cloud:443 \
    S3BucketName=fsxn-audit-logs-observability-test \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

- **スタックステータス** — CREATE_COMPLETE
- **作成されたリソース**:
  - [x] Lambda 関数
  - [x] IAM ロール
  - [x] EventBridge Rule
  - [x] Dead Letter Queue（KMS 暗号化）
  - [x] CloudWatch LogGroup（30日保持）
  - [x] CloudWatch Alarm

---

### ステップ 3: Lambda テストイベント送信

- **結果** — ✅ PASS

```bash
aws lambda invoke \
  --function-name fsxn-elastic-integration-shipper \
  --payload file:///tmp/test-event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json
```

- **レスポンス**:
```json
{
  "statusCode": 200,
  "body": {
    "total_logs": 2,
    "total_shipped": 2,
    "errors": []
  }
}
```

- **確認項目**:
  - [x] statusCode: 200
  - [x] total_logs: 2
  - [x] total_shipped: 2
  - [x] errors: [] (空)
- **Elasticsearch Bulk API レスポンス** — HTTP 200

---

### ステップ 4: Kibana Discover でログ到着確認

- **結果** — ✅ PASS

- **確認方法** — Kibana → Discover → データが表示されていることを確認
- **到着ドキュメント数** — 2件
- **到着までの時間** — 即時（数秒以内）
- **インデックスパターン** — `fsxn-audit-YYYY.MM.DD`（日次インデックス）

- **ECS フィールドマッピング確認**:
  - [x] `@timestamp` — ISO 8601 形式
  - [x] `event.type` — イベント ID
  - [x] `user.name` — ユーザー名
  - [x] `fsxn.operation` — 操作タイプ
  - [x] `fsxn.path` — ファイルパス
  - [x] `fsxn.result` — 結果（Success/Failure）
  - [x] `fsxn.svm` — SVM 名
  - [x] `cloud.provider` — aws
  - [x] `cloud.service.name` — fsx-ontap

![Kibana Discover — ログ到着確認](../screenshots/elastic/kibana-discover.png)

---

### ステップ 5: セットアップガイド日英対応確認

- **結果** — ✅ PASS

- **日本語** — `integrations/elastic/docs/ja/setup-guide.md` — 存在確認済み
- **英語** — `integrations/elastic/docs/en/setup-guide.md` — 存在確認済み

---

### ステップ 6: スクリーンショット検証

- **結果** — ✅ PASS

| # | ファイル名 | 内容 | 判定 |
|---|-----------|------|------|
| 1 | `kibana-discover.png` | Kibana Discover — fsxn-audit データ表示 | ✅ |

---

## 既知の問題と対応策

| # | 問題内容 | 重要度 | 対処方法 | ステータス |
|---|---------|--------|---------|-----------|
| 1 | Elastic Cloud Serverless は 14 日間トライアル（その後有料） | 低 | PoC 期間内に検証完了 | 📝 記録済み |
| 2 | API Key は Encoded 形式（Base64）で Secrets Manager に格納 | 低 | README に手順記載済み | ✅ 対処済み |

---

## 総合判定

- **判定** — ✅ 監査ログパス本番環境利用可能
- **合格基準数** — 6 / 6
- **不合格基準** — なし

---

## 検証完了確認

- [x] 全ステップの結果が記録されている
- [x] スクリーンショットが配置されている（`docs/screenshots/elastic/`）
- [x] ECS フィールドマッピングが確認されている
- [x] 既知の問題と対応策が記録されている
- [x] セットアップガイド日英対応が確認されている
