# Pipeline SLO 定義

🌐 **日本語**（このページ） | [English](../en/pipeline-slo.md)

## 概要

本ドキュメントでは、FSx for ONTAP Observability Pipeline の Service Level Objectives（SLO）を定義します。これらの SLO はすべてのベンダー統合に適用され、運用健全性の測定可能な目標を提供します。

> **注意**
>
> これらは内部運用目標であり、契約上の SLA ではありません。ワークロード要件やベンダーエンドポイントの特性に応じて閾値を調整してください。

## SLO テーブル

| SLO | 目標 | 測定方法 | アラーム閾値 |
|-----|------|----------|-------------|
| **配信レイテンシ**（監査ログ） | ファイルローテーションからベンダー取り込みまで 10 分未満 | Scheduler 間隔（5分）+ Lambda 実行時間 + ベンダー取り込み遅延 | CloudWatch: Lambda Duration p99 > 60s |
| **配信レイテンシ**（EMS） | ONTAP イベント発生からベンダー取り込みまで 60 秒未満 | API Gateway レイテンシ + Lambda 実行時間 | CloudWatch: API GW Latency p99 > 5s |
| **配信レイテンシ**（FPolicy） | ファイル操作からベンダー取り込みまで 30 秒未満 | SQS メッセージ滞留時間 + Lambda 実行時間 | CloudWatch: SQS ApproximateAgeOfOldestMessage > 30s |
| **データ損失率** | 監査ログファイルの 0.01% 未満 | DLQ メッセージ数 / スケジュール実行総数 | CloudWatch: DLQ ApproximateNumberOfMessagesVisible > 0 |
| **Pipeline 可用性** | 99.5% 以上（月次計測） | Lambda 成功実行数 / 総実行数 | CloudWatch: Lambda Errors > 5 in 10m |
| **Checkpoint 鮮度** | 最新監査ファイルから 15 分以内 | SSM Parameter Store 最終更新時刻と現在時刻の差 | カスタムメトリクス: checkpoint_age_seconds > 900 |
| **DLQ 深度** | 0（定常状態） | SQS ApproximateNumberOfMessagesVisible | CloudWatch: DLQ depth > 0 が 15 分以上継続 |

## イベントソース別 SLO

### 監査ログポーラー（EventBridge Scheduler）

| メトリクス | 目標 | 根拠 |
|-----------|------|------|
| エンドツーエンドレイテンシ | 10 分未満 | 5 分スケジュール + 処理時間 + ベンダー遅延 |
| 実行あたり処理ファイル数 | 0 より大（新規ファイルがある場合） | Checkpoint が前進すること |
| Lambda エラー率 | 1% 未満 | リトライによる一時的障害は許容 |
| Checkpoint 滞留 | スケジュール間隔の 2 倍未満（10 分） | 処理が追いついていることを示す |

### EMS Webhook（API Gateway + Lambda）

| メトリクス | 目標 | 根拠 |
|-----------|------|------|
| API Gateway 5xx 率 | 0.1% 未満 | サーバーエラーはほぼゼロ |
| Lambda コールドスタート | 3 秒未満 | Webhook パスとして許容範囲 |
| エンドツーエンドレイテンシ | 5 秒未満 | リアルタイムアラート要件 |

### FPolicy（ECS Fargate + SQS + Lambda）

| メトリクス | 目標 | 根拠 |
|-----------|------|------|
| SQS メッセージ滞留時間 | 30 秒未満 | ニアリアルタイムのファイル操作可視化 |
| ECS タスク健全性 | Running（定常状態） | Fargate タスクが正常であること |
| Bridge Lambda エラー率 | 1% 未満 | SQS リトライが一時的障害を処理 |

## 測定の実装

### CloudWatch Alarms（テンプレートに含まれる）

各ベンダーテンプレートには以下が含まれています：
- Lambda Errors アラーム（10 分間で 5 回超）
- DLQ depth アラーム（メッセージ数 > 0）

### 追加推奨アラーム

```yaml
# Checkpoint 滞留アラーム（template.yaml に追加）
CheckpointStalenessAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub "${AWS::StackName}-checkpoint-stale"
    MetricName: ParameterStoreAge
    Namespace: Custom/FSxONTAPPipeline
    Statistic: Maximum
    Period: 300
    EvaluationPeriods: 3
    Threshold: 900
    ComparisonOperator: GreaterThanThreshold
    AlarmActions:
      - !Ref AlarmTopic

# Lambda Duration P99 アラーム
LambdaDurationAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub "${AWS::StackName}-duration-p99"
    MetricName: Duration
    Namespace: AWS/Lambda
    ExtendedStatistic: p99
    Period: 300
    EvaluationPeriods: 3
    Threshold: 60000
    ComparisonOperator: GreaterThanThreshold
    Dimensions:
      - Name: FunctionName
        Value: !Ref LogShipperFunction
```

## SLO バーンレートとエラーバジェット

本番デプロイでは、SLO バーンレートアラートの実装を検討してください：

| SLO | 月間エラーバジェット | 高速バーン（1 時間窓） | 低速バーン（6 時間窓） |
|-----|---------------------|----------------------|----------------------|
| 99.5% 可用性 | 3.6 時間のダウンタイム | エラー率 > 14.4% | エラー率 > 2.4% |
| 0.01% 未満のデータ損失 | 約 4.3 ファイル/月（1000 ファイル/日の場合） | 1 時間で DLQ メッセージ > 1 | 6 時間で DLQ メッセージ > 3 |

## Production Readiness Level 別 Go/No-Go 基準

### Level 1 → Level 2（Quickstart → Operational PoC）

| 基準 | 測定方法 | 必須 |
|------|----------|------|
| 監査ログがベンダーに到達 | クエリで結果が返る | Yes |
| Checkpoint が前進 | SSM パラメータが 5 分ごとに更新 | Yes |
| DLQ が 24 時間空 | SQS メトリクス = 0 | Yes |
| Lambda エラー率 < 5% | CloudWatch メトリクス | Yes |
| コスト見積もり作成済み | ドキュメント化 | Yes |

### Level 2 → Level 3（Operational PoC → Production Baseline）

| 基準 | 測定方法 | 必須 |
|------|----------|------|
| 全 SLO を 7 日間連続で達成 | ダッシュボード/メトリクス | Yes |
| Runbook テスト済み（DLQ replay） | テスト結果のドキュメント | Yes |
| セキュリティレビュー完了 | チェックリスト署名済み | Yes |
| Webhook 認証有効化（EMS） | テンプレートパラメータ != NONE | Yes |
| ダッシュボード + アラート設定済み | ベンダー側で確認 | Yes |
| コストが見積もりの 20% 以内 | 請求比較 | Yes |
| ビジネススポンサー承認 | 承認ドキュメント | Yes |

### Level 3 → Level 4（Production Baseline → Enterprise Pipeline）

| 基準 | 測定方法 | 必須 |
|------|----------|------|
| SLO を 30 日間連続で達成 | ダッシュボード/メトリクス | Yes |
| マルチバックエンドルーティングテスト済み | OTel Collector で検証 | Yes |
| PII 秘匿ルール実装済み | Collector プロセッサ設定 | Yes |
| コンプライアンスエビデンスパック完成 | ガバナンスドキュメント署名済み | Yes |
| Poison-pill 処理テスト済み | 不正ファイルの処理をシミュレーション | Yes |
| DR/フェイルオーバーテスト済み | クロスリージョンまたはバックアップパス | Yes |

## テレメトリコストの SLO

Observability パイプラインは、テレメトリ量を制御しないとコストが無制限に増えます。
コストの上限を運用 SLO として定義してください。

### コスト目標

| コンポーネント | 月次予算 | アラーム閾値 | 超過時のアクション |
|---------------|---------|-------------|-----------------|
| AWS インフラ（Lambda, EventBridge, S3, SQS） | < $50 | > $40（予算の 80%） | スケジュール間隔の見直し、不要イベントのフィルタ |
| ベンダー取り込み（Datadog, Grafana など） | ユーザー定義 | 予算の 80% 超 | ログ詳細度の削減、サンプリング増加、送信元でのフィルタ |
| NAT Gateway（VPC Lambda の場合） | < $50 | > $40 | VPC 外への Lambda 配置を検討 |
| パイプライン総コスト | < $150 | > $120 | FinOps レビューへエスカレーション |

### コスト制御の手段

| 手段 | 実装 | 効果 |
|------|------|------|
| 監査ポリシーのフィルタ | ONTAP `vserver audit` のイベント選択 | 送信元でログ量を削減 |
| スケジュール間隔の調整 | 低活動 SVM は EventBridge Scheduler を `rate(15 minutes)` に | Lambda 呼び出し回数を削減 |
| バッチサイズの最適化 | Lambda のバッチ処理（API 呼び出し回数を削減） | ベンダー API コストを削減 |
| OTel Collector のサンプリング | `probabilistic_sampler` プロセッサ | 大量に出る FPolicy イベントを削減 |
| アダプティブテレメトリ | Grafana Cloud Adaptive Metrics / Logs | ノイズを自動削減 |
| VPC 外 Lambda | Lambda から VPC 設定を外す | NAT Gateway コストを排除 |

### 測定

```bash
# Monthly cost check (AWS infrastructure)
aws ce get-cost-and-usage \
  --time-period Start=$(date -v-30d +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["fsxn-observability"]}}'
```

### コスト異常検知

パイプラインに対して AWS Cost Anomaly Detection を設定します。

- タグで監視: `Project=fsxn-observability`
- アラート閾値: 想定日次支出の 20% 超
- 通知: SNS → Slack/Email

> **アンチパターン**: フィルタせずに全監査イベント（読み取り操作を含む）を収集すること。
> 読み取りイベントは通常、書き込みイベントの 10〜100 倍の量になります。コンプライアンス上
> 読み取り監査が明示的に必要な場合を除き、ONTAP の監査ポリシーレベルでフィルタしてください。

## 関連ドキュメント

- [配信保証パターン](delivery-guarantees.md)
- [運用ガイド](operational-guide.md)
- [PoC 成功基準](poc-success-criteria.md)
- [セキュリティレビューチェックリスト](security-review-checklist.md)
- [コスト検証テンプレート](cost-validation.md)
- [管理・監視 Decision Tree](decision-tree-management-monitoring.md)
