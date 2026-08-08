# FPolicy 本番アーキテクチャパターン

🌐 **日本語**（このページ） | [English](../en/fpolicy-production-architecture-patterns.md)

## 概要

本ドキュメントは FPolicy ファイルアクティビティパイプラインの本番アーキテクチャパターンを説明します。[Part 4 ブログ記事](https://dev.to/aws-builders/fpolicy-file-activity-pipeline-ontap-to-datadog-via-ecs-fargate)では単一 Fargate タスクで E2E パスを検証しました。本番デプロイでは HA、IP 安定性、障害復旧の追加設計が必要です。

## パターン 1: 単一 Fargate タスク（検証用）

```
ONTAP FPolicy → Fargate Task (単一) → SQS → Lambda → Vendor
```

- **用途** — PoC、開発、低ボリューム監視
- **利点** — 最もシンプル、最低コスト（AWS 側 ~$14/月）
- **欠点** — 単一障害点、再起動時に IP 変更、~2分の復旧ギャップ

## パターン 2: Primary/Secondary FPolicy サーバー

ONTAP external engine は `primary-servers` と `secondary-servers` パラメータをサポートします。Primary が到達不能な場合、ONTAP は自動的に Secondary にフェイルオーバーします。

```
ONTAP FPolicy
  ├─ primary-servers: [Fargate Task A IP]
  └─ secondary-servers: [Fargate Task B IP]
```

- **用途** — HA 要件のある本番環境
- **利点** — ONTAP による自動フェイルオーバー、単一タスク再起動時のイベントロスなし
- **欠点** — 2つの Fargate タスク稼働（~$28/月）、両方の IP 更新が必要

### ONTAP の設定

```
vserver fpolicy policy external-engine create -vserver <svm-name> \
  -engine-name fpolicy_aws_engine \
  -primary-servers <task-a-ip> \
  -secondary-servers <task-b-ip> \
  -port 9898 \
  -extern-engine-type asynchronous \
  -ssl-option no-auth
```

## パターン 3: 状態照合による自動更新

手動 IP 更新の代わりに、EventBridge トリガーの Lambda で期待状態（ONTAP engine IP = 現在の健全なタスク IP）を照合します。

```
ECS Task State Change (RUNNING)
  → EventBridge Rule
    → IP 照合 Lambda
      → 現在のタスク IP と ONTAP engine primary-servers を比較
      → 差分がある場合のみ更新
      → 成功/失敗の CloudWatch メトリクスを発行
```

## パターン 4: Multi-AZ 配置

| コンポーネント | AZ 耐性 | 備考 |
|-------------|---------|------|
| Fargate Task | タスクごとに単一 AZ | spread 配置または複数サービス |
| SQS Queue | Multi-AZ（マネージド） | 対応不要 |
| Lambda | Multi-AZ（マネージド） | 対応不要 |
| ONTAP SVM | FSx デプロイタイプ依存 | Single-AZ または Multi-AZ |

### 障害モードマトリクス

| 障害 | 影響 | 復旧 |
|------|------|------|
| Fargate タスククラッシュ | 再起動中のイベントロス（~2分） | ECS 自動再起動 + IP 更新 |
| AZ 障害（Single-AZ FSx） | パイプライン全停止 | FSx フェイルオーバー + 新タスク |
| AZ 障害（Multi-AZ FSx） | 障害 AZ のタスクのみ停止 | ONTAP が Secondary にフェイルオーバー |
| Lambda スロットリング | SQS にバッファ | 自動スケール、データロスなし |
| Datadog API 障害 | SQS にバッファ | Lambda がバックオフでリトライ |

## 計画メンテナンスの Runbook

1. **メンテナンス前**: SQS キューが空であることを確認し、KeepAlive が健全であることを確認
2. **メンテナンス中**: Fargate が影響を受ける場合、イベントは SQS にバッファされる
3. **メンテナンス後**: ONTAP が再接続することを確認（ECS ログの KeepAlive を確認）
4. **IP が変わった場合**: `fpolicy-update-engine-ip.sh --auto` を実行、または自動照合を待つ

## 参考資料

- [NetApp FPolicy external engine ドキュメント](https://docs.netapp.com/us-en/ontap/nas-audit/create-fpolicy-external-engine-task.html)
- [AWS Fargate ドキュメント](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/AWS_Fargate.html)
- [FPolicy persistent store (ONTAP 9.14.1+)](https://docs.netapp.com/us-en/ontap/nas-audit/persistent-stores.html)
