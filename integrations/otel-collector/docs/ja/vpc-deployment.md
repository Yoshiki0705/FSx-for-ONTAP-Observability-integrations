# VPC デプロイメントガイド — OTel Collector on ECS Fargate

🌐 **日本語**（このページ） | [English](../en/vpc-deployment.md)

本ドキュメントでは、OTel Collector を ECS Fargate 上にデプロイし、VPC 内の Lambda から外部バックエンドへログを転送するアーキテクチャを説明します。

## アーキテクチャ概要

```
┌─────────────────────────────────────────────────────────────────┐
│ VPC                                                             │
│                                                                 │
│  ┌──────────────┐     ┌─────────────────────────┐              │
│  │ Lambda       │────▶│ OTel Collector (Fargate) │              │
│  │ (OTLP送信)   │     │ Port 4318 (OTLP HTTP)   │              │
│  └──────────────┘     │ Port 13133 (Health)      │              │
│                       └───────────┬─────────────┘              │
│                                   │                             │
│                       ┌───────────▼─────────────┐              │
│                       │ NAT Gateway              │              │
│                       └───────────┬─────────────┘              │
└───────────────────────────────────┼─────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
            Grafana Cloud      Honeycomb        Datadog
```

## VPC デプロイが必要な条件

| シナリオ | VPC 必要 | 理由 |
|---------|---------|------|
| Lambda → 外部 OTLP エンドポイント直接 | ❌ | Lambda は VPC 外で動作可能 |
| Lambda → VPC 内 OTel Collector | ✅ | Collector が VPC 内にある |
| Lambda → S3 AP + ONTAP REST API | ✅ | ONTAP API は VPC 内のみ |
| 高セキュリティ要件（全通信 VPC 内） | ✅ | コンプライアンス要件 |

## ECS Fargate タスク定義

```json
{
  "family": "otel-collector",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "containerDefinitions": [
    {
      "name": "otel-collector",
      "image": "otel/opentelemetry-collector-contrib:0.152.0",
      "portMappings": [
        {"containerPort": 4318, "protocol": "tcp"},
        {"containerPort": 13133, "protocol": "tcp"}
      ],
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:13133/ || exit 1"],
        "interval": 10,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 15
      },
      "environment": [
        {"name": "GRAFANA_OTLP_ENDPOINT", "value": "https://otlp-gateway-prod-ap-northeast-0.grafana.net/otlp"},
        {"name": "HONEYCOMB_DATASET", "value": "fsxn-audit"}
      ],
      "secrets": [
        {"name": "GRAFANA_BASIC_AUTH", "valueFrom": "arn:aws:secretsmanager:<region>:<account-id>:secret:grafana-auth"},
        {"name": "HONEYCOMB_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account-id>:secret:honeycomb-key"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/otel-collector",
          "awslogs-region": "ap-northeast-1",
          "awslogs-stream-prefix": "otel"
        }
      }
    }
  ]
}
```

## セキュリティグループ設定

### OTel Collector セキュリティグループ

```yaml
# インバウンドルール
- Protocol: TCP
  Port: 4318
  Source: Lambda セキュリティグループ
  Description: OTLP HTTP from Lambda

- Protocol: TCP
  Port: 13133
  Source: ALB セキュリティグループ (ヘルスチェック用)
  Description: Health check from ALB

# アウトバウンドルール
- Protocol: TCP
  Port: 443
  Destination: 0.0.0.0/0
  Description: HTTPS to external backends (via NAT Gateway)
```

### Lambda セキュリティグループ

```yaml
# アウトバウンドルール
- Protocol: TCP
  Port: 4318
  Destination: OTel Collector セキュリティグループ
  Description: OTLP HTTP to Collector
```

## NAT Gateway 設定

OTel Collector が外部バックエンド（Grafana Cloud、Honeycomb、Datadog）にデータを送信するには、NAT Gateway が必要です。

### サブネット構成

```
VPC CIDR: 10.0.0.0/16

パブリックサブネット (NAT Gateway 配置):
  - 10.0.1.0/24 (AZ-a)
  - 10.0.2.0/24 (AZ-c)

プライベートサブネット (ECS Fargate + Lambda 配置):
  - 10.0.11.0/24 (AZ-a)
  - 10.0.12.0/24 (AZ-c)
```

### ルートテーブル（プライベートサブネット）

```yaml
Routes:
  - Destination: 0.0.0.0/0
    Target: nat-<gateway-id>
  - Destination: 10.0.0.0/16
    Target: local
```

## Lambda → OTel Collector 接続

Lambda を VPC 内に配置し、OTel Collector の内部エンドポイントに接続します。

### Lambda 環境変数

```yaml
OTLP_ENDPOINT: http://<collector-private-ip>:4318
# または Service Discovery を使用:
OTLP_ENDPOINT: http://otel-collector.local:4318
```

### Service Discovery（推奨）

AWS Cloud Map を使用して、ECS タスクの IP を自動解決します：

```yaml
ServiceDiscovery:
  Namespace: fsxn-observability.local
  Service: otel-collector
  DNS: otel-collector.fsxn-observability.local
```

## スケーリング考慮事項

| メトリクス | 閾値 | アクション |
|-----------|------|---------|
| CPU 使用率 | > 70% | タスク数を増加 |
| メモリ使用率 | > 80% | タスクサイズを増加 |
| キュー深度 | > 1000 | バッチサイズを調整 |

### Auto Scaling 設定

```yaml
ScalingPolicy:
  MinCapacity: 1
  MaxCapacity: 4
  TargetTrackingScaling:
    TargetValue: 70
    PredefinedMetricSpecification:
      PredefinedMetricType: ECSServiceAverageCPUUtilization
```

## コスト見積もり

| コンポーネント | 月額概算 (ap-northeast-1) |
|--------------|--------------------------|
| ECS Fargate (0.5 vCPU, 1GB) | ~$15/月 |
| NAT Gateway (固定) | ~$45/月 |
| NAT Gateway (データ転送) | ~$0.062/GB |
| CloudWatch Logs | ~$0.76/GB |

> **注意**: NAT Gateway のコストが最も大きい固定費です。低トラフィック環境では VPC 外 Lambda + 外部 OTLP エンドポイント直接送信の方がコスト効率が良い場合があります。

## トラブルシューティング

### Lambda から OTel Collector への接続の不可

1. Lambda と Collector が同じ VPC 内にあることを確認
2. セキュリティグループのインバウンドルール（Port 4318）を確認
3. Lambda のサブネットからのルーティングを確認

### OTel Collector から外部バックエンドへの送信の不可

1. NAT Gateway が正しく設定されていることを確認
2. プライベートサブネットのルートテーブルに NAT Gateway へのルートがあることを確認
3. Collector のログで接続エラーを確認

### ヘルスチェック失敗

1. Collector コンテナが正常に起動しているか確認
2. Port 13133 がセキュリティグループで許可されているか確認
3. `curl http://localhost:13133/` でローカルテスト
