# 監査ログ DR のためのクロスリージョンレプリケーション

🌐 **日本語**（このページ） | [English](../en/cross-region-replication.md)

## 概要

本ドキュメントでは、災害復旧（DR）と事業継続のために、FSx for ONTAP 監査ログパイプラインの状態とデータを AWS リージョン間でレプリケーションするパターンを説明します。

> **スコープ**
>
> 本ドキュメントは Observability パイプラインの DR を対象としています。FSx for ONTAP ファイルシステムのデータ DR については、NetApp SnapMirror または FSx for ONTAP クロスリージョンバックアップを使用してください。

## パイプラインのクロスリージョン DR が必要な理由

| シナリオ | DR なしの影響 | DR ありの場合 |
|---------|-------------|-------------|
| プライマリリージョン障害 | 監査ログのベンダーへの配信が停止 | セカンダリリージョンが引き継ぎ |
| ベンダーリージョナルエンドポイント障害 | 配信失敗、DLQ が蓄積 | 代替ベンダーリージョンにルーティング |
| コンプライアンス要件 | 単一障害点 | DR 能力を文書化 |

## アーキテクチャオプション

### オプション A: Active-Passive（大半のケースで推奨）

プライマリリージョンが通常通りログを処理。セカンダリリージョンにはインフラが事前デプロイされているが非アクティブ（Scheduler 無効）。フェイルオーバーは手動または自動。

```
プライマリリージョン (ap-northeast-1)          セカンダリリージョン (ap-southeast-1)
+----------------------------------+         +----------------------------------+
| EventBridge Scheduler (ENABLED)  |         | EventBridge Scheduler (DISABLED) |
| Lambda (処理中)                   |         | Lambda (待機)                     |
| SSM Checkpoint (アクティブ)        |         | SSM Checkpoint (レプリケート済み)   |
| DLQ                              |         | DLQ                              |
+----------------------------------+         +----------------------------------+
```

**フェイルオーバー**: セカンダリの Scheduler を有効化、Lambda がレプリケートされた Checkpoint から再開。

### オプション B: Active-Active（高複雑度）

両リージョンが DynamoDB Global Table を使用して共有状態で同時処理。ベンダー側での重複排除が必要。

### オプション C: 監査ログレプリケーションのみ（最もシンプル）

S3 Cross-Region Replication で監査ログをセカンダリリージョンにコピー。セカンダリにレプリカバケットを指すパイプラインをデプロイ。状態レプリケーション不要。

## 推奨: オプション A の実装

### Checkpoint レプリケーション

```bash
#!/bin/bash
# Replicate checkpoint from primary to secondary (run every 5 min)
PRIMARY_REGION="ap-northeast-1"
SECONDARY_REGION="ap-southeast-1"
PARAM_NAME="/fsxn/<vendor>/audit-checkpoint"

CHECKPOINT=$(aws ssm get-parameter \
  --name "$PARAM_NAME" --region "$PRIMARY_REGION" \
  --query 'Parameter.Value' --output text)

aws ssm put-parameter \
  --name "$PARAM_NAME" --value "$CHECKPOINT" \
  --type String --overwrite --region "$SECONDARY_REGION"
```

### フェイルオーバー手順

1. プライマリリージョン障害を検知（CloudWatch アラームまたは手動）
2. セカンダリリージョンの Scheduler を有効化
3. セカンダリの Lambda がレプリケートされた Checkpoint から再開
4. S3 バケットがリージョン固有の場合は ONTAP 監査ログ出力先を更新

### セカンダリリージョンテンプレート

セカンダリリージョンに `SchedulerState: DISABLED` で同じベンダーテンプレートをデプロイ：

```bash
aws cloudformation deploy \
  --template-file integrations/<vendor>/template.yaml \
  --stack-name fsxn-<vendor>-integration-dr \
  --parameter-overrides \
    S3AccessPointArn=<secondary-region-ap-arn> \
    ScheduleExpression="rate(5 minutes)" \
    SchedulerState=DISABLED \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-southeast-1
```

## RPO/RTO 目標

| オプション | RPO | RTO | 月額コスト | 複雑度 |
|----------|-----|-----|----------|--------|
| A (Active-Passive) | 5-10 分 | 5-15 分 | < $5 | 中 |
| B (Active-Active) | 0 | 0 | $5-20 | 高 |
| C (S3 Replication) | 15 分 | 30-60 分 | ~$0.02/GB | 低 |

## DR テスト（四半期ごと）

| ステップ | アクション | 確認事項 |
|---------|----------|---------|
| 1 | プライマリの Scheduler を無効化 | プライマリが処理を停止 |
| 2 | Checkpoint がレプリケートされていることを確認 | セカンダリの SSM が一致 |
| 3 | セカンダリの Scheduler を有効化 | セカンダリが処理を開始 |
| 4 | ベンダーにログが到達することを確認 | ベンダープラットフォームでクエリ |
| 5 | プライマリを再有効化、セカンダリを無効化 | 通常運用に復帰 |
| 6 | 結果を文書化 | DR テストエビデンス |

## S3 Cross-Region Replication（オプション C）

```yaml
AuditLogBucket:
  Type: AWS::S3::Bucket
  Properties:
    VersioningConfiguration:
      Status: Enabled
    ReplicationConfiguration:
      Role: !GetAtt ReplicationRole.Arn
      Rules:
        - Id: AuditLogCRR
          Status: Enabled
          Prefix: audit/
          Destination:
            Bucket: !Sub arn:aws:s3:::fsxn-audit-logs-${AWS::AccountId}-dr
            StorageClass: STANDARD_IA
```

## 判断マトリクス

| 要件 | 推奨オプション |
|------|-------------|
| コンプライアンスチェックボックス（DR が存在） | オプション A |
| RPO < 15 分 | オプション A |
| RPO = 0 | オプション B |
| 最もシンプルな実装 | オプション C |

## 関連ドキュメント

- [マルチアカウントデプロイ](multi-account-deployment.md)
- [Pipeline SLO](pipeline-slo.md)
- [配信保証パターン](delivery-guarantees.md)
- [コンプライアンスエビデンスパック](compliance-evidence-pack.md)
