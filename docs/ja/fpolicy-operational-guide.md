# FPolicy パイプライン運用ガイド

🌐 **日本語**（このページ） | [English](../en/fpolicy-operational-guide.md)

## ヘルスモデル

FPolicy パイプラインには 4 つのヘルスレイヤーがあります。本番運用に向けて 4 つすべてを監視してください。

### 1. 接続ヘルス

| メトリクス | ソース | アラート閾値 |
|--------|--------|-----------------|
| Fargate タスク稼働中 | ECS RunningTaskCount | 1 分以上 < 1 |
| ONTAP エンジン接続済み | ECS ログ（KeepAlive） | 5 分以上 KeepAlive なし |
| KeepAlive 鮮度 | ECS ログからのカスタムメトリクス | 経過時間 > 300 秒 |

### 2. キューヘルス

| メトリクス | ソース | アラート閾値 |
|--------|--------|-----------------|
| SQS 可視メッセージ数 | ApproximateNumberOfMessagesVisible | 5 分以上 > 100 |
| SQS 最古メッセージ経過時間 | ApproximateAgeOfOldestMessage | > 300 秒 |

### 3. 配信ヘルス

| メトリクス | ソース | アラート閾値 |
|--------|--------|-----------------|
| Lambda エラー | Lambda Errors メトリクス | 5 分以上 > 0 |
| Lambda 実行時間 | Lambda Duration メトリクス | p99 > 10 秒 |
| DLQ 深度 | SQS DLQ ApproximateNumberOfMessagesVisible | > 0 |

### 4. データ鮮度

| メトリクス | ソース | アラート閾値 |
|--------|--------|-----------------|
| 最終 FPolicy イベント | カスタムメトリクスまたは Datadog クエリ | 10 分以上イベントなし（営業時間中） |
| Datadog ログ到着 | `source:fsxn-fpolicy` カウント | 10 分以上 0 |

## ランブック

### Fargate タスクが再起動した場合

1. ECS サービスイベントを確認: `aws ecs describe-services --cluster <cluster> --services <service>`
2. 新しいタスク IP を取得: `aws ecs describe-tasks --cluster <cluster> --tasks <task-arn>`
3. ONTAP エンジンを更新: `bash shared/scripts/fpolicy-update-engine-ip.sh --auto`
4. ECS ログで KeepAlive を確認する。最大 5 分まで待つ。ハンドシェイク後の最初の
   KeepAlive は `keep_alive_interval`（既定 120 秒）ぶん待つことがあるため、60 秒で
   打ち切ると正常な再接続を失敗として報告することになる

### ONTAP エンジンが切断された場合

1. Fargate タスクが稼働中か確認
2. セキュリティグループが FSx SG からの TCP:9898 インバウンドを許可しているか確認
3. ONTAP エンジンステータスを確認: `vserver fpolicy show-engine -vserver <svm>`
4. タスク IP が変更されている場合、エンジンを更新
5. タスクが稼働中で IP が正しい場合、ネットワーク接続性を確認

### SQS バックログが増加している場合

1. CloudWatch で Lambda エラーを確認
2. Lambda 同時実行数を確認（スロットリング）
3. Datadog API ステータスを確認
4. Lambda が失敗している場合、DLQ でエラー詳細を確認
5. Datadog がダウンしている場合、イベントは SQS に安全にバッファされる

### Datadog ログが欠落している場合

1. Datadog Log Explorer で `source:fsxn-fpolicy` を確認
2. Lambda CloudWatch Logs で配信エラーを確認
3. SQS キュー深度を確認（イベントがバッファされている可能性）
4. Fargate ECS ログで FPolicy イベントを確認
5. ONTAP エンジン接続ステータスを確認

### NFS クライアントハングが観測された場合

1. FPolicy がハングの原因か確認: `vserver fpolicy show -vserver <svm>`
2. FPolicy を一時的に無効化: `vserver fpolicy disable -vserver <svm> -policy-name <policy>`
3. NFS 操作が再開されることを確認
4. FPolicy スコープを調査（監視対象の操作またはボリュームを削減）
5. 調査後、より狭いスコープで再有効化

## FPolicy エンジン IP 整合性確保

### 望ましい状態

```
ONTAP external engine primary-servers = 現在の正常な Fargate タスクのプライベート IP
```

### 整合性確保フロー

```
ECS Task State Change (RUNNING)
  → EventBridge Rule (detail-type: "ECS Task State Change", lastStatus: "RUNNING")
    → Reconciliation Lambda
      → ECS API から現在のタスク IP を取得
      → ONTAP REST API から現在のエンジン IP を取得
      → 異なる場合: ポリシー無効化 → エンジン更新 → ポリシー有効化
      → CloudWatch メトリクスを発行（success/failure/no-change）
```

### 自動整合性確保の前提条件

- NAT Gateway 付き VPC 内の Lambda（ONTAP 管理エンドポイントへのアクセス用）
- Secrets Manager 内の ONTAP 認証情報
- ECS DescribeTasks、Secrets Manager GetSecretValue の IAM 権限
- ONTAP 管理 LIF へのネットワーク到達性

## 合成ヘルスチェック

プロアクティブな監視のために、合成ファイル作成テストをスケジュール:

1. EventBridge Scheduler が 15 分ごとに Lambda をトリガー
2. Lambda が ONTAP REST API 経由で SMB 共有にテストファイルを作成
3. Lambda が 30 秒待機後、Datadog で期待されるログをクエリ
4. 見つからない場合、CloudWatch アラームを発行

これにより、手動介入なしでパイプライン全体をエンドツーエンドで検証できます。
