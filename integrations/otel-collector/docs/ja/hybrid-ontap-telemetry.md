# Hybrid ONTAP Telemetry with OpenTelemetry Collector

🌐 **日本語**（このページ） | [English](../en/hybrid-ontap-telemetry.md)

## Applicability

The OTLP + Collector pattern demonstrated in this repository for FSx for ONTAP can be extended to other ONTAP deployment models:

| ONTAP Deployment | Collector Placement | Network Path |
|-----------------|--------------------|--------------| 
| FSx for ONTAP (AWS) | ECS Fargate / EC2 in same VPC | VPC internal |
| Cloud Volumes ONTAP (AWS/Azure/GCP) | Cloud-native container in same VNet/VPC | Cloud internal |
| On-premises ONTAP | Local VM/container | Direct Connect / VPN to cloud backends |

## Common Elements

Regardless of ONTAP deployment model, the following remain consistent:

- **ONTAP telemetry sources**: Audit logs, EMS, FPolicy
- **Normalized schema**: Same OTLP attribute mapping (event.type, user.name, fsxn.operation, etc.)
- **Collector config**: Same exporter configuration for backends
- **Backend independence**: Same vendor-neutral routing pattern

## Key Differences by Deployment

| Aspect | FSx for ONTAP | CVO | On-Premises |
|--------|--------------|-----|-------------|
| Audit log access | S3 Access Point | S3/Blob | NFS/CIFS mount |
| EMS delivery | API Gateway webhook | API Gateway / Cloud Function | Local webhook receiver |
| FPolicy | ECS Fargate server | Cloud container | Local server |
| Collector hosting | ECS Fargate | Cloud container | VM / bare metal |
| Network to backends | NAT Gateway / VPC Endpoint | Cloud NAT | Direct Connect / Internet |

## Future Work

- Detailed implementation guides for CVO and on-premises ONTAP
- Collector placement patterns for hybrid environments
- Network connectivity patterns (Direct Connect, VPN, PrivateLink)
- Disconnected site behavior and store-and-forward patterns

> This document outlines the architectural direction. Implementation details for non-FSx deployments are planned for future articles.

---

## 概要

ONTAP は複数のデプロイモデルで動作する。本ガイドでは、すべての ONTAP 環境にわたって一貫した OTel ベースのパイプラインでテレメトリを収集・正規化する方法を説明する。

## デプロイパターン

### FSx for ONTAP（AWS マネージド）

```
┌─────────────────────────────────────────────────────────────────┐
│  AWS Account                                                     │
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌────────────────┐  │
│  │ FSx for ONTAP    │────▶│ S3 Bucket    │────▶│ Lambda         │  │
│  │ (Audit logs) │     │ (Raw logs)   │     │ (Parse + OTLP) │  │
│  └──────────────┘     └──────────────┘     └───────┬────────┘  │
│                                                     │            │
│  ┌──────────────┐     ┌──────────────┐             │            │
│  │ FSx for ONTAP    │────▶│ API Gateway  │────▶ Lambda ─┤            │
│  │ (EMS webhook)│     │              │             │            │
│  └──────────────┘     └──────────────┘             │            │
│                                                     │ OTLP/HTTP  │
│  ┌──────────────┐     ┌──────────────┐             │            │
│  │ FSx for ONTAP    │────▶│ ECS Fargate  │────▶ SQS ──▶│ Lambda    │
│  │ (FPolicy TCP)│     │ (FP Server)  │             │            │
│  └──────────────┘     └──────────────┘             │            │
│                                                     ▼            │
│                                            ┌────────────────┐   │
│                                            │ OTel Collector  │   │
│                                            │ (ECS Fargate)   │   │
│                                            └───────┬────────┘   │
│                                                    │             │
└────────────────────────────────────────────────────┼─────────────┘
                                                     │
                                          ┌──────────┼──────────┐
                                          ▼          ▼          ▼
                                      Datadog    Grafana   Honeycomb
```

**主な特徴:**
- 完全サーバーレスな取り込み（Lambda + ECS Fargate）
- S3 を監査ログバッファとして使用（EventBridge トリガーまたはスケジュールポーリング）
- Collector は同じ VPC 内の ECS Fargate サービスとして実行
- EC2 インスタンス不要

### Cloud Volumes ONTAP（クラウド上のセルフマネージド）

```
┌─────────────────────────────────────────────────────────────────┐
│  AWS / Azure / GCP Account                                       │
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌────────────────┐  │
│  │ CVO ONTAP    │────▶│ Cloud Storage│────▶│ Serverless Fn  │  │
│  │ (Audit logs) │     │ (S3/Blob/GCS)│     │ (Parse + OTLP) │  │
│  └──────────────┘     └──────────────┘     └───────┬────────┘  │
│                                                     │            │
│  ┌──────────────┐                                   │ OTLP/HTTP  │
│  │ CVO ONTAP    │────▶ EMS Webhook ────────────────▶│            │
│  │ (EMS)        │                                   │            │
│  └──────────────┘                                   ▼            │
│                                            ┌────────────────┐   │
│                                            │ OTel Collector  │   │
│                                            │ (Container/VM)  │   │
│                                            │ Same VPC/VNet   │   │
│                                            └───────┬────────┘   │
└────────────────────────────────────────────────────┼─────────────┘
                                                     │
                                          ┌──────────┼──────────┐
                                          ▼          ▼          ▼
                                      Datadog    Splunk    Elastic
```

**主な特徴:**
- Collector を CVO と同じ VPC/VNet にデプロイ
- クラウドネイティブコンピュートで取り込み（Lambda/Functions/Cloud Run）
- クラウドプロバイダーによりストレージが異なる（S3/Blob/GCS）
- クラウドプロバイダーに関係なく同じ OTLP スキーマ

### オンプレミス ONTAP

```
┌─────────────────────────────────────────────────────────────────┐
│  On-Premises Data Center                                         │
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌────────────────┐  │
│  │ ONTAP Cluster│────▶│ NFS/CIFS     │────▶│ Log Collector  │  │
│  │ (Audit logs) │     │ Share        │     │ (VM/Container) │  │
│  └──────────────┘     └──────────────┘     └───────┬────────┘  │
│                                                     │            │
│  ┌──────────────┐                                   │ OTLP/HTTP  │
│  │ ONTAP Cluster│────▶ EMS/Syslog ─────────────────▶│            │
│  │ (EMS)        │                                   │            │
│  └──────────────┘                                   ▼            │
│                                            ┌────────────────┐   │
│                                            │ OTel Collector  │   │
│                                            │ (VM/Container)  │   │
│                                            │ On-prem         │   │
│                                            └───────┬────────┘   │
└────────────────────────────────────────────────────┼─────────────┘
                                                     │
                                          Direct Connect / VPN / Proxy
                                                     │
                                          ┌──────────┼──────────┐
                                          ▼          ▼          ▼
                                      Datadog    Splunk    Grafana
                                      (Cloud)    (Cloud)   (Cloud)
```

**主な特徴:**
- Collector は VM またはコンテナとしてオンプレミスで実行
- 監査ログは NFS/CIFS マウント経由でアクセス（S3 ではない）
- Direct Connect、VPN、またはプロキシ経由でクラウドバックエンドに接続
- ネットワーク中断に備えたローカルバッファリングが必要な場合あり

## 共通正規化スキーマ

ONTAP のデプロイモデルに関係なく、すべてのテレメトリは同じ OTLP スキーマに正規化される:

```yaml
# Common attributes across all ONTAP types
resource:
  service.name: "fsxn-audit"          # or fsxn-ems, fsxn-fpolicy
  service.version: "1.0.0"
  deployment.environment: "production"
  ontap.cluster.name: "<cluster-name>"
  ontap.svm.name: "<svm-name>"
  ontap.deployment.type: "fsx"        # fsx | cvo | onprem
  cloud.provider: "aws"              # aws | azure | gcp | onprem
  cloud.region: "ap-northeast-1"     # or azure region, etc.

log_record:
  timestamp: "<ontap-event-timestamp>"
  severity_number: 9                  # INFO
  body: "<original event content>"
  attributes:
    event.type: "file.read"
    event.id: "<unique-event-id>"
    file.path: "/vol1/data/report.xlsx"
    user.name: "DOMAIN\\username"
    source.address: "<client-ip>"
```

### デプロイタイプ別スキーマの違い

| 属性 | FSx for ONTAP | Cloud Volumes ONTAP | オンプレミス ONTAP |
|------|---------------|--------------------:|-------------------|
| `ontap.deployment.type` | `fsx` | `cvo` | `onprem` |
| `cloud.provider` | `aws` | `aws` / `azure` / `gcp` | `onprem` |
| `cloud.region` | AWS リージョン | クラウドリージョン | `<datacenter-id>` |
| `ontap.filesystem.id` | `fs-0123456789abcdef0` | インスタンス ID | クラスターシリアル |
| 監査ログソース | S3 バケット | クラウドストレージ | NFS/CIFS 共有 |

## ネットワーク接続パターン

### FSx for ONTAP → クラウドバックエンド

| パターン | ユースケース | レイテンシ | コスト |
|---------|------------|----------|--------|
| VPC → Internet (NAT GW) | クラウドバックエンドのデフォルト | 低 | NAT GW 時間課金 + データ |
| VPC → PrivateLink | 対応バックエンド（Datadog） | 最低 | PrivateLink 時間課金 |
| VPC → VPC Peering | 別 VPC の Collector | 低 | データ転送のみ |

### オンプレミス → クラウドバックエンド

| パターン | ユースケース | レイテンシ | コスト |
|---------|------------|----------|--------|
| Direct Connect | 大量、低レイテンシ | 最低 | DC ポート + データ |
| Site-to-Site VPN | 中程度の量 | 中 | VPN 時間課金 + データ |
| HTTPS プロキシ | 制限されたネットワーク | 高め | プロキシインフラ |
| ローカルバッファ + バッチ | 断続的な接続 | 可変 | ローカルストレージ |

### オンプレミスの Collector 配置

```
Option A: Collector on-prem (recommended for high volume)
  ONTAP → [local network] → Collector → [WAN] → Cloud backends
  Pros: Low latency collection, local buffering
  Cons: On-prem infrastructure to manage

Option B: Collector in cloud (recommended for simplicity)
  ONTAP → [WAN] → Collector (cloud) → Cloud backends
  Pros: No on-prem Collector management
  Cons: WAN dependency for collection, higher latency

Option C: Dual Collector (recommended for hybrid)
  ONTAP → [local] → Collector (on-prem) → [WAN] → Collector (cloud) → Backends
  Pros: Local buffering + cloud routing flexibility
  Cons: Two Collectors to manage
```

## Collector 配置デシジョンツリー

```
┌─────────────────────────────────────────────┐
│ Where does ONTAP run?                        │
└──────────────────┬──────────────────────────┘
                   │
     ┌─────────────┼─────────────┐
     │             │             │
   FSx for ONTAP    CVO ONTAP    On-Prem
     │             │             │
     ▼             ▼             ▼
┌──────────┐ ┌──────────┐ ┌──────────────────────┐
│ Collector│ │ Collector│ │ Is WAN reliable?      │
│ in same  │ │ in same  │ └──────────┬───────────┘
│ VPC      │ │ VPC/VNet │      ┌─────┴─────┐
│ (Fargate)│ │(Container│      │           │
└──────────┘ │ or VM)   │     YES          NO
             └──────────┘      │           │
                               ▼           ▼
                        ┌──────────┐ ┌──────────┐
                        │ Collector│ │ Collector│
                        │ in cloud │ │ on-prem  │
                        │ (simple) │ │ (buffer) │
                        └──────────┘ └──────────┘
```

## Cloud Insights / BlueXP との関係

### 補完関係の成立と置き換えの不成立

| 機能 | Cloud Insights / BlueXP | 本プロジェクト (OTel) |
|------|------------------------|---------------------|
| **目的** | インフラ監視、キャパシティプランニング | 監査コンプライアンス、セキュリティテレメトリ |
| **データスコープ** | パフォーマンスメトリクス、トポロジ | 監査ログ、EMS イベント、FPolicy |
| **バックエンド** | NetApp Cloud Insights | 任意の OTLP 互換バックエンド |
| **デプロイ** | SaaS（NetApp マネージド） | セルフマネージド（自社インフラ） |
| **カスタマイズ** | CI の機能に限定 | ルーティング/フィルタリングの完全制御 |
| **コストモデル** | ノード単位ライセンス | インフラコストのみ |

### 使い分け

**Cloud Insights / BlueXP を使用する場合:**
- ストレージパフォーマンス監視（IOPS、レイテンシ、スループット）
- キャパシティプランニングと予測
- インフラトポロジの可視化
- NetApp 固有のヘルスチェックと推奨事項

**OTel Collector パイプラインを使用する場合:**
- コンプライアンスバックエンドへの監査ログ配信
- 複数 SIEM/Observability ツールへのセキュリティイベントファンアウト
- カスタムフィルタリング、リダクション、エンリッチメント
- マルチベンダーバックエンド戦略
- FPolicy ベースのランサムウェア検出パイプライン

**両方を併用する場合:**
- Cloud Insights でインフラヘルスを監視
- OTel パイプラインで監査/セキュリティテレメトリを配信
- 相関調査: CI が「ストレージに何が起きたか」+ OTel が「誰が何をしたか」を表示

## 将来の考慮事項

### 計画中の拡張

| 拡張 | ステータス | 影響 |
|------|----------|------|
| ONTAP ネイティブ OTLP エクスポート | 議論中 | Lambda パースレイヤーが不要に |
| FSx for ONTAP S3 Event Notifications | 未対応 | ポーリングからプッシュに置き換え |
| OTel Collector Kubernetes operator | 利用可能 | オンプレミス/EKS デプロイを簡素化 |
| Collector 設定ホットリロード | 利用可能 (0.100+) | ゼロダウンタイム設定変更 |

### 移行パス: Direct Send → OTel Collector

現在 Direct Send を使用している環境向け:

1. 既存 Lambda と並行して Collector をデプロイ
2. Lambda を両方に送信するよう設定（デュアルライト期間）
3. Collector 経由のバックエンドでデータを検証
4. カットオーバー: Lambda は Collector のみに送信
5. Direct-send コードパスを廃止

### マルチクラウド統合ビュー

複数クラウドで ONTAP を運用する組織向け:

```
┌──────────┐  ┌──────────┐  ┌──────────┐
│ FSx for ONTAP│  │ CVO (AWS)│  │CVO(Azure)│
│ (AWS)    │  │          │  │          │
└────┬─────┘  └────┬─────┘  └────┬─────┘
     │              │              │
     ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Collector│  │ Collector│  │ Collector│
│ (AWS)    │  │ (AWS)    │  │ (Azure)  │
└────┬─────┘  └────┬─────┘  └────┬─────┘
     │              │              │
     └──────────────┼──────────────┘
                    │
                    ▼
         ┌────────────────────┐
         │ Central Backend(s) │
         │ Unified dashboards │
         │ Cross-cloud queries│
         └────────────────────┘
```

すべての Collector が同じ正規化スキーマを使用するため、`ontap.deployment.type` と `cloud.provider` 属性を使ったクロスクラウドクエリが可能。
