# Harvest ECS Fargate トラブルシューティングガイド

🌐 **日本語**（このページ） | [English](../en/harvest-ecs-troubleshooting.md)

## 事象: Harvest コンテナの ECS Fargate での ExitCode 1 終了

### 症状

- ECS Deployment Circuit Breaker が発動
- Harvest コンテナが `ExitCode: 1` で終了
- CloudWatch Logs が出力されない（ログストリームが作成されない）
- `StoppedReason: "Essential container in task exited"`

### 根本原因分析

NetApp Harvest (`ghcr.io/netapp/harvest`) を ECS Fargate でカスタムエントリポイントスクリプトと共に実行する際に、複数の要因が特定されました。

#### 要因 1: Busybox sh の非互換性（重大）

Harvest コンテナイメージは `/busybox/sh` をシェルとして使用します。Busybox sh は最小限の POSIX シェルであり、以下をサポート**しません**:

- Bash 配列（`read -ra`、`${ARRAY[@]}`）
- Here-strings（`<<<`）
- プロセス置換
- `xargs`（busybox に含まれない）

**修正**: POSIX 互換の構文のみを使用（`IFS` 分割、`printf`、`tr`）。

参考: [BusyBox sh ドキュメント](https://www.busybox.net/downloads/BusyBox.html)

#### 要因 2: 作業ディレクトリ（重大）

Harvest バイナリ（`bin/poller`）は `/opt/harvest/` から実行されることを前提としています。カスタムエントリポイント（`/busybox/sh -c`）を使用する場合、作業ディレクトリがイメージのデフォルト `WORKDIR` と異なる可能性があります。

**修正**: `bin/poller` 実行前に必ず `cd /opt/harvest` を実行。

#### 要因 3: 正しい CLI フラグ（重要）

Harvest の公式 CLI は `--poller`（単数形）でポーラーを指定します:

```bash
# 単一ポーラー
bin/poller --poller fsxn-cluster-1

# 複数ポーラー（カンマ区切り）
bin/poller --poller fsxn-cluster-1,fsxn-cluster-2
```

`--pollers`（複数形）や `--config` フラグはコンテナエントリポイントでの正しい呼び出し方法では**ありません**。

参考:
- [Harvest Containers — 公式 Docker パターン](https://netapp.github.io/harvest/nightly/install/harvest-containers/)
- [Harvest Containerd — CLI 例](https://netapp.github.io/harvest/nightly/install/containerd/)

#### 要因 4: Exporter 設定（重要）

Prometheus exporter は ADOT サイドカーからのスクレイピングのために `0.0.0.0` にバインドする必要があります。`Pollers` セクションの exporter 名は `Exporters` セクションのキーと完全に一致する必要があります。

```yaml
Exporters:
  prometheus1:          # <-- この名前
    exporter: Prometheus
    addr: 0.0.0.0      # <-- サイドカースクレイピングに必須
    port_range: 12990-12999

Pollers:
  fsxn-cluster-1:
    exporters:
      - prometheus1     # <-- 完全一致が必要
```

参考: [Harvest Prometheus Exporter — port_range](https://netapp.github.io/harvest/nightly/prometheus-exporter/#port_range)

#### 要因 5: ログの未出力

コンテナがロギングドライバーとの接続を確立する前に終了すると、CloudWatch ログストリームが作成されません。以下の場合に発生:

1. エントリポイントスクリプトに構文エラーがある（シェルが即座に終了）
2. `bin/poller` バイナリが `harvest.yml` のパースに失敗（ログ書き込み前に終了）

**回避策**: スクリプトの先頭に `echo` 文を追加し、デプロイ前にロググループが存在することを確認。

### 公式 Harvest コンテナパターン

| パターン | ユースケース | 参考 |
|---------|------------|------|
| Docker Compose（1 ポーラー/コンテナ） | 本番推奨 | [harvest-containers](https://netapp.github.io/harvest/nightly/install/harvest-containers/) |
| 単一コンテナ、複数ポーラー | ECS Fargate（コスト最適化） | カスタム（本プロジェクト） |
| Kubernetes | K8s 環境 | [K8 install](https://netapp.github.io/harvest/nightly/install/k8/) |

### FSx for ONTAP 用 harvest.yml リファレンス

```yaml
Pollers:
  fsxn-cluster-1:
    datacenter: fsxn-1
    addr: <management-endpoint>
    auth_style: basic_auth
    username: fsxadmin
    password: <password>
    use_insecure_tls: true    # FSx for ONTAP に必須
    collectors:
      - Rest
      - RestPerf
    exporters:
      - prometheus1

Exporters:
  prometheus1:
    exporter: Prometheus
    addr: 0.0.0.0
    port_range: 12990-12999

Defaults:
  use_insecure_tls: true      # FSx for ONTAP に必須
```

重要ポイント:
- `use_insecure_tls: true` は FSx for ONTAP に**必須**（自己署名証明書のため）
- exporter の `addr: 0.0.0.0` はサイドカースクレイピングに必須
- `collectors: [Rest, RestPerf]` — ZAPI は FSx for ONTAP では非推奨
- `auth_style: basic_auth` で fsxadmin 認証情報を使用

参考: [Amazon FSx for ONTAP — Harvest 準備](https://netapp.github.io/harvest/nightly/prepare-fsx-clusters/)

### FSx for ONTAP 対応ダッシュボード

すべての Harvest ダッシュボードが FSx for ONTAP で動作するわけではありません。以下が互換性確認済み:

- ONTAP: cDOT
- ONTAP: Cluster
- ONTAP: Data Protection
- ONTAP: Datacenter
- ONTAP: FlexCache
- ONTAP: FlexGroup
- ONTAP: FPolicy
- ONTAP: LUN
- ONTAP: NFS Troubleshooting
- ONTAP: Quota
- ONTAP: Security
- ONTAP: SVM
- ONTAP: Volume
- ONTAP: Volume by SVM
- ONTAP: Volume Deep Dive

参考: [FSx for ONTAP — 対応 Harvest ダッシュボード](https://netapp.github.io/harvest/nightly/prepare-fsx-clusters/#supported-harvest-dashboards)

### ECS 固有の考慮事項

#### CPU アーキテクチャ

`ghcr.io/netapp/harvest` は `linux/amd64` 向けにビルドされています。ECS Fargate のデフォルトは x86_64 で互換性があります。Graviton（ARM64）を使用する場合、コンテナは ExitCode 1 で失敗します。

参考: [ECS Exit code 1 修正 — CPU アーキテクチャ不一致](https://openillumi.com/en/en-ecs-exit-code-1-exec-format-error-arch-fix/)

#### Secrets Manager インジェクション

ECS の `Secrets`（ValueFrom）はタスク起動時に Secrets Manager へのネットワークアクセスが必要です。プライベートサブネットでは Secrets Manager VPC Endpoint が必要です。DNS 伝播が不完全な場合、タスクは `ResourceInitializationError` で失敗します。

参考: [AWS ECS Fargate ResourceInitializationError](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/stopped-task-errors.html)

#### VPC Endpoint DNS 伝播

VPC Endpoints 作成後（Stack 1 経由）、DNS 伝播に 1-5 分かかる場合があります。エンドポイント作成直後に起動された ECS タスクは、エンドポイント DNS 名の解決に失敗する可能性があります。

**回避策**: CloudFormation の `DependsOn` を追加するか、Stack 1 と Stack 3 のデプロイ間に待機時間を設ける。

### デプロイ前チェックリスト

Stack 3（observability）デプロイ前:

- [ ] Stack 1 の VPC Endpoints が `available` 状態であること
- [ ] Secrets Manager シークレットが存在し、有効な JSON を含むこと（`{"username": "fsxadmin", "password": "..."}`）
- [ ] FSx for ONTAP セキュリティグループが Harvest タスク SG からの TCP/443 インバウンドを許可していること
- [ ] Harvest イメージタグが存在すること（初回デプロイには `latest` 推奨）
- [ ] プライベートサブネットに GHCR イメージプル用の NAT Gateway ルートがあること
