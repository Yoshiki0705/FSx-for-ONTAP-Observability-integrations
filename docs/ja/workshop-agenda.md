# ワークショップアジェンダ: FSx for ONTAP サーバーレス Observability

🌐 **日本語**（このページ） | [English](../en/workshop-agenda.md)

## ワークショップ概要

| 項目 | 詳細 |
|------|--------|
| 所要時間 | 2 時間（30 分講義 + 90 分ハンズオン） |
| 対象者 | ストレージ管理者、プラットフォームエンジニア、セキュリティチーム |
| 前提条件 | FSx for ONTAP を持つ AWS アカウント（またはサンドボックスアカウント） |
| 成果 | 選択したベンダーに配信する監査ログパイプラインが稼働 |

## アジェンダ

### Part 1: 講義（30 分）

| 時間 | トピック | 資料 |
|------|-------|-----------|
| 0:00-0:05 | イントロダクションと目標 | 本アジェンダ |
| 0:05-0:15 | アーキテクチャ概要: 3 つのイベントソース（Audit/EMS/FPolicy） | アーキテクチャ図 |
| 0:15-0:20 | S3 Access Point の制約と設計判断 | S3AP 仕様ドキュメント |
| 0:20-0:25 | ベンダー選定ガイド: コスト、機能、データレジデンシー | ベンダー比較 |
| 0:25-0:30 | Q&A | — |

### Part 2: ハンズオン（90 分）

| 時間 | アクティビティ | 成功基準 |
|------|----------|-----------------|
| 0:30-0:40 | **Lab 1**: 前提条件スタックのデプロイ | S3 AP アクセス可能、監査ログ確認 |
| 0:40-0:55 | **Lab 2**: ベンダー統合のデプロイ（1 つ選択） | CloudFormation デプロイ成功 |
| 0:55-1:10 | **Lab 3**: 監査イベントのトリガー + 配信確認 | ベンダー UI にログ表示 |
| 1:10-1:25 | **Lab 4**: ダッシュボードとアラートの設定 | ダッシュボードにデータ表示、テストでアラート発火 |
| 1:25-1:40 | **Lab 5**: 障害パスのテスト（DLQ + リプレイ） | DLQ にメッセージ到着、リプレイ成功 |
| 1:40-1:55 | **Lab 6**: クリーンアップ + コストレビュー | スタック削除完了、コスト見積もり作成 |
| 1:55-2:00 | まとめ: Go/No-Go ディスカッション | 次のステップ文書化 |

## ワークショップ前チェックリスト（ファシリテーター）

- [ ] AWS サンドボックスアカウントをプロビジョニング（または組織が提供）
- [ ] FSx for ONTAP ファイルシステムが稼働中、監査ログ有効
- [ ] S3 バケット + Access Point デプロイ済み（前提条件スタック）
- [ ] ベンダーアカウント作成済み（無料枠推奨）
- [ ] API キー / トークンを Secrets Manager に保存済み
- [ ] ワークショップガイドを印刷または共有（本ドキュメント）
- [ ] 即時テスト用のサンプル監査ログファイルが S3 に配置済み

## ワークショップ前チェックリスト（参加者）

- [ ] AWS コンソールアクセス（IAM ユーザーまたは SSO）
- [ ] AWS CLI 設定済み（`aws sts get-caller-identity` が動作）
- [ ] ベンダーアカウントアクセス（Datadog/Grafana/Splunk 等）
- [ ] `git`、`python3`、`aws` CLI が使えるターミナル
- [ ] リポジトリクローン済み: `git clone https://github.com/Yoshiki0705/fsxn-observability-integrations.git`

## Lab 手順サマリー

### Lab 1: 前提条件のデプロイ

```bash
aws cloudformation deploy \
  --template-file shared/templates/prerequisites.yaml \
  --stack-name fsxn-observability-prerequisites \
  --parameter-overrides \
    FsxS3AccessPointArn=<your-s3-ap-arn> \
  --capabilities CAPABILITY_IAM
```

### Lab 2: ベンダー統合のデプロイ

ベンダーを選択し、ベンダー README の Quick Deploy コマンドを実行してください。

### Lab 3: トリガーと確認

```bash
# Check Lambda logs for successful processing
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-<vendor>-integration-shipper \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))")

# Verify in vendor UI (vendor-specific query)
```

### Lab 4: ダッシュボードとアラート

ベンダーの `create-dashboard.sh` スクリプト（利用可能な場合）を実行するか、ベンダー UI で手動作成してください。

### Lab 5: 障害パス

```bash
# Temporarily break the secret to simulate failure
# (restore immediately after testing)
# Check DLQ for messages
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessages
```

### Lab 6: クリーンアップ

```bash
aws cloudformation delete-stack --stack-name fsxn-<vendor>-integration
aws cloudformation delete-stack --stack-name fsxn-observability-prerequisites
```

## ワークショップ後の成果物

| 成果物 | オーナー | 期限 |
|-------------|-------|-----|
| ワークショップフィードバックフォーム | ファシリテーター | 当日 |
| PoC レポート（組織エンゲージメントの場合） | パートナー/SA | +3 日 |
| Go/No-Go 推奨 | 組織 + パートナー | +1 週間 |
| 本番デプロイ計画（Go の場合） | プラットフォームチーム | +2 週間 |

## カスタマイズノート

- **セキュリティ重視**のワークショップ: EMS Webhook Lab を追加（ARP 検知シナリオ）
- **マルチベンダー**ワークショップ: OTel Collector で 2 バックエンドを使用
- **移行**ワークショップ: Splunk EC2 比較から開始、その後サーバーレスをデプロイ
- **エグゼクティブ**向け: ハンズオンを 45 分に短縮、ビジネス価値の議論を拡大
