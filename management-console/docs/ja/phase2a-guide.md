# FSx for ONTAP Management Console Phase 2A ガイド

🌐 **日本語**（このページ） | [English](../en/phase2a-guide.md)

Phase 2A は既存の Management Console（Phase 1）を拡張し、ARP ダッシュボード、スナップショットリストア、FlexClone 管理、マルチポーラーサポートを追加します。

## 目次

1. [概要](#概要)
2. [前提条件](#前提条件)
3. [Phase 1 からのアップグレード](#phase-1-からのアップグレード)
4. [新規デプロイ（マルチファイルシステム）](#新規デプロイマルチファイルシステム)
5. [Phase 2A 機能ガイド](#phase-2a-機能ガイド)
   - [ARP ダッシュボード](#arp-ダッシュボード)
   - [スナップショットリストア](#スナップショットリストア)
   - [FlexClone 管理](#flexclone-管理)
   - [マルチポーラー / ファイルシステム切替](#マルチポーラー--ファイルシステム切替)
6. [パラメータリファレンス](#パラメータリファレンス)
7. [トラブルシューティング](#トラブルシューティング)

---

## 概要

Phase 2A で追加される機能:

| 機能 | 説明 |
|------|------|
| ARP ダッシュボード | Autonomous Ransomware Protection の状態可視化、アラート表示、保護スナップショット作成 |
| スナップショットリストア | 確認ダイアログ付きのフルリストアワークフロー（ジョブポーリング対応） |
| FlexClone 管理 | ボリューム/スナップショットからの書き込み可能クローン作成・一覧表示 |
| マルチポーラー | 単一デプロイで複数の FSx for ONTAP ファイルシステムを監視・管理 |

### 追加されるファイル

```
management-console/
├── tooljet-workflows/
│   ├── arp-dashboard.json          # ARP ステータス + フィルタリング + アラート
│   ├── snapshot-restore.json       # スナップショットリストアワークフロー
│   └── flexclone-management.json   # FlexClone 作成 + 一覧
├── harvest/
│   └── dashboards/
│       └── arp-status.json         # ARP Grafana ダッシュボード
└── docs/
    ├── ja/phase2a-guide.md         # 本ドキュメント
    └── en/phase2a-guide.md         # 英語版
```

---

## 前提条件

### ONTAP バージョン要件

| 機能 | 最低バージョン | 備考 |
|------|--------------|------|
| ARP ダッシュボード | ONTAP 9.17+ | ARP/AI 機能には 9.17 以降が必須 |
| スナップショットリストア | ONTAP 9.8+ | Phase 1 と同じ |
| FlexClone 管理 | ONTAP 9.8+ | Phase 1 と同じ |
| マルチポーラー | ONTAP 9.8+ | 各ファイルシステムが 9.8 以上 |

> ⚠️ **重要**: ARP/AI 機能は ONTAP 9.17 以降でのみ利用可能です。9.17 未満のファイルシステムでは、ARP ダッシュボードに「ARP/AI features require ONTAP 9.17 or later」と表示され、ARP 関連のアクションは無効化されます。

### AWS リソース（Phase 1 に追加）

| リソース | 要件 | 備考 |
|---------|------|------|
| Secrets Manager シークレット | 各ファイルシステムに 1 つ | JSON 形式: `{"username": "fsxadmin", "password": "..."}` |
| FSx for ONTAP ファイルシステム | 1〜10 台 | 管理エンドポイント (port 443) にアクセス可能 |

### マルチファイルシステム用の Secrets Manager シークレット作成

各 FSx for ONTAP ファイルシステムに対応するシークレットを作成します:

```bash
# ファイルシステム 1
aws secretsmanager create-secret \
  --name fsxn-mgmt-ontap-credentials-fs1 \
  --description "FSx for ONTAP credentials for file system 1" \
  --secret-string '{"username": "fsxadmin", "password": "<password-1>"}'

# ファイルシステム 2
aws secretsmanager create-secret \
  --name fsxn-mgmt-ontap-credentials-fs2 \
  --description "FSx for ONTAP credentials for file system 2" \
  --secret-string '{"username": "fsxadmin", "password": "<password-2>"}'
```

---

## Phase 1 からのアップグレード

Phase 2A は既存の Phase 1 デプロイメントに対する **インプレースアップデート** です。新しいスタックの作成は不要です。

### アップグレード手順

#### Step 1: パラメータの移行

Phase 1 のシングルエンドポイントパラメータは Phase 2A でも後方互換性があります。既存の環境変数をそのまま使用できます:

```bash
# Phase 1 の変数（引き続き動作）
export ONTAP_MGMT_ENDPOINT="<management-ip>"
export ONTAP_CREDENTIALS_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-mgmt-ontap-credentials-XXXXXX"
```

デプロイスクリプトが自動的に複数形パラメータに変換します。

#### Step 2: デプロイスクリプトの実行

```bash
cd management-console/scripts
bash deploy.sh
```

既存の 5 スタックが更新されます:
- `observability.yaml`: マルチポーラー対応のエントリポイント更新
- `console.yaml`: マルチシークレット IAM 権限の追加

#### Step 3: ToolJet ワークフローのインポート

新しい ToolJet ワークフロー JSON ファイルを Management UI にインポートします:

1. ToolJet 管理画面にログイン
2. **Workflows** → **Import** を選択
3. 以下のファイルを順にインポート:
   - `tooljet-workflows/arp-dashboard.json`
   - `tooljet-workflows/snapshot-restore.json`
   - `tooljet-workflows/flexclone-management.json`

#### Step 4: Grafana ダッシュボードのインポート

ARP ダッシュボードを AMG にインポートします:

1. AMG ワークスペースにアクセス
2. **Dashboards** → **Import** を選択
3. `harvest/dashboards/arp-status.json` をアップロード
4. データソースに AMP ワークスペースを選択

#### Step 5: 動作確認

- ARP ダッシュボードにボリュームの ARP ステータスが表示されることを確認
- スナップショットリストアのワークフローが動作することを確認
- FlexClone 作成フォームが表示されることを確認
- 既存の Phase 1 機能（ボリューム管理、SVM 管理、スナップショット CRUD、レプリケーション管理、S3 ファイルブラウザ）が引き続き動作することを確認

### 後方互換性

Phase 2A は以下の後方互換性を保証します:

- シングルエンドポイントのデプロイは変更なしで動作
- Phase 1 の ToolJet ワークフロー（volume-management, svm-management, snapshot-management, replication-management, s3-file-browser）は変更されない
- 既存の Grafana ダッシュボードは影響を受けない

---

## 新規デプロイ（マルチファイルシステム）

複数の FSx for ONTAP ファイルシステムを一括で監視・管理する場合の手順です。

### Step 1: 環境変数の設定

```bash
# 必須パラメータ
export VPC_ID="vpc-0123456789abcdef0"
export PRIVATE_SUBNET_IDS="subnet-aaaa1111aaaa1111a,subnet-bbbb2222bbbb2222b"
export PUBLIC_SUBNET_IDS="subnet-cccc3333cccc3333c,subnet-dddd4444dddd4444d"

# マルチエンドポイント（カンマ区切り、最大 10 台）
export ONTAP_MGMT_ENDPOINTS="<management-ip-1>,<management-ip-2>,<management-ip-3>"
export ONTAP_CREDENTIALS_SECRET_ARNS="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ontap-fs1-XXXXXX,arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ontap-fs2-XXXXXX,arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ontap-fs3-XXXXXX"

# オプション
export HARVEST_IMAGE_TAG="24.11.0"
export TOOLJET_IMAGE_TAG="v2.50.0-lts"
```

> ⚠️ **重要**: エンドポイントの数とシークレット ARN の数は一致する必要があります。位置で対応付けされます（endpoint[1] ↔ secret[1]）。

### Step 2: デプロイの実行

```bash
cd management-console/scripts
bash deploy.sh
```

デプロイスクリプトは以下のバリデーションを実行します:
- エンドポイント数とシークレット ARN 数の一致確認
- エンドポイント数が 1〜10 の範囲内であることの確認

### Step 3: マルチポーラーの動作確認

```bash
# Harvest タスクのログを確認（各ポーラーの起動を確認）
aws logs tail /ecs/fsxn-mgmt-harvest --since 5m --follow

# 期待される出力:
# Poller fsxn-cluster-1 started (datacenter: fsxn-1)
# Poller fsxn-cluster-2 started (datacenter: fsxn-2)
# Poller fsxn-cluster-3 started (datacenter: fsxn-3)
```

### マルチポーラーの仕組み

Harvest コンテナの起動時に、エントリポイントスクリプトがカンマ区切りのエンドポイントから `harvest.yml` を自動生成します:

```yaml
Pollers:
  fsxn-cluster-1:
    datacenter: fsxn-1
    addr: <management-ip-1>
    auth_style: basic_auth
    username: fsxadmin
    password: <secret-value-1>
    collectors:
      - Rest
      - RestPerf
    exporters:
      - prometheus

  fsxn-cluster-2:
    datacenter: fsxn-2
    addr: <management-ip-2>
    auth_style: basic_auth
    username: fsxadmin
    password: <secret-value-2>
    collectors:
      - Rest
      - RestPerf
    exporters:
      - prometheus

Exporters:
  prometheus:
    exporter: Prometheus
    port_range: 12990-12999

Defaults:
  use_insecure_tls: true
```

各ポーラーは固有の `datacenter` ラベルを持ち、Grafana ダッシュボードでファイルシステムごとにフィルタリングできます。

---

## Phase 2A 機能ガイド

### ARP ダッシュボード

Autonomous Ransomware Protection (ARP) の状態を一覧表示し、アラートの確認と保護スナップショットの作成を行います。

#### 機能概要

- **サマリーカード**: ARP 状態ごとのボリューム数（disabled / dry_run / enabled / paused）
- **ボリュームテーブル**: 名前、SVM、ARP 状態（色分けバッジ）、状態継続時間、最終不審アクティビティ
- **フィルタリング**: ARP 状態によるフィルタ、ボリューム名/SVM 名による検索（大文字小文字区別なし）
- **アラートパネル**: ボリューム名、アラートタイプ、タイムスタンプ、重大度（タイムスタンプ降順）
- **保護スナップショット**: アラート発生ボリュームへのワンクリックスナップショット作成

#### ARP 状態の色分け

| ARP 状態 | 色 | 意味 |
|----------|---|------|
| disabled | 🔴 赤 (#DC3545) | ARP 無効 — 保護されていない |
| dry_run | 🟡 黄 (#FFC107) | 学習モード — 検知のみ、ブロックなし |
| enabled | 🟢 緑 (#28A745) | アクティブ保護 — 検知とブロック |
| paused | ⚪ グレー (#6C757D) | 一時停止中 |

#### 保護スナップショットの作成

アラートが発生したボリュームに対して、ワンクリックで保護スナップショットを作成できます:

- スナップショット名の形式: `arp_protect_<volume_name>_<YYYYMMDD_HHMMSS>`
- API: `POST /api/storage/volumes/{uuid}/snapshots`

#### Grafana 連携

ARP ダッシュボードには Grafana パネルが埋め込まれ、以下を表示します:
- ARP 状態分布（円グラフ）
- ARP アラートタイムライン（時系列グラフ）
- ボリュームごとの ARP イベント数（テーブル）

パネルは 60 秒ごとに自動更新されます。

---

### スナップショットリストア

ボリュームをスナップショットの状態に復元するワークフローです。

#### ワークフロー

1. **ボリューム選択** → スナップショット一覧の表示（名前、作成日時、サイズ、作成日時降順）
2. **スナップショット選択** → 確認ダイアログの表示
3. **確認・実行** → リストア開始、ジョブポーリング（5 秒間隔）
4. **完了** → 成功/失敗の表示

#### 確認ダイアログの内容

- ボリューム名
- スナップショット名 + 作成日時
- ⚠️ 警告: 「{snapshot_time} 以降に書き込まれたすべてのデータは永久に失われます」
- ⚠️ 追加警告（NAS 共有がある場合）: 「接続中の NFS/CIFS クライアントに影響が出る可能性があります」

#### API フロー

```
POST /api/storage/volumes/{volume_uuid}/snapshots/{snapshot_uuid}/restore
  → 202 Accepted (job_uuid)

GET /api/cluster/jobs/{job_uuid}  (5秒ごとにポーリング)
  → state: queued | running | success | failure
```

---

### FlexClone 管理

ボリュームまたはスナップショットから書き込み可能なクローンを作成します。

#### クローン作成フォーム

| フィールド | バリデーション | エラーメッセージ |
|-----------|-------------|--------------|
| クローン名 | `^[a-zA-Z0-9_]{1,203}$` | "Clone name must be 1-203 characters, alphanumeric and underscores only" |
| ジャンクションパス | `^/[a-zA-Z0-9_/\-]+$` | "Junction path must start with / and contain only valid path characters" |
| 親スナップショット | 任意選択 | — |

バリデーションはクライアントサイドで実行され、不正な入力では API コールは発生しません。

#### クローン一覧

ボリューム一覧に以下の情報が追加されます:
- クローンインジケーターバッジ（通常ボリュームとの視覚的区別）
- 親ボリューム名
- 親スナップショット名（スナップショットからクローンした場合）
- クローン作成日時
- スプリット状態
- スペース節約量（共有ブロック vs 固有ブロック）

---

### マルチポーラー / ファイルシステム切替

単一デプロイで複数の FSx for ONTAP ファイルシステムを監視・管理します。

#### ファイルシステムセレクター

Management UI のナビゲーションヘッダーにドロップダウンが表示されます:

- 現在アクティブなファイルシステム名/エンドポイントを表示
- 選択変更時: すべての ONTAP REST API コールが選択されたファイルシステムに切り替わる
- 切替後 3 秒以内に現在のビューが更新される
- すべてのページにアクティブなファイルシステムの視覚的インジケーターを表示

#### Grafana ダッシュボードのフィルタリング

各 Grafana ダッシュボードの上部に `$datacenter` 変数セレクターが追加されます:
- 個別のファイルシステムでフィルタリング可能
- 全ファイルシステムの集約メトリクスも表示可能

#### ポーラー障害時の動作

- 1 つのポーラーが接続に失敗しても、他のポーラーは独立して動作を継続
- 失敗したポーラーは CloudWatch アラームで通知
- UI でエンドポイントが到達不能な場合、そのファイルシステムのエラーを表示し、他のファイルシステムへの切替を許可

---

## パラメータリファレンス

### Phase 2A で変更されたパラメータ

| パラメータ | 型 | 説明 | Phase 1 との違い |
|-----------|---|------|----------------|
| `OntapManagementEndpoints` | `String` | カンマ区切りの管理エンドポイント（1〜10） | 複数形に変更（単数形も後方互換） |
| `OntapCredentialsSecretArns` | `String` | カンマ区切りの Secrets Manager ARN（位置対応） | 複数形に変更（単数形も後方互換） |

### 環境変数

```bash
# Phase 2A（推奨）
export ONTAP_MGMT_ENDPOINTS="<endpoint-1>,<endpoint-2>"
export ONTAP_CREDENTIALS_SECRET_ARNS="<arn-1>,<arn-2>"

# Phase 1 互換（自動変換される）
export ONTAP_MGMT_ENDPOINT="<endpoint>"
export ONTAP_CREDENTIALS_SECRET_ARN="<arn>"
```

### バリデーションルール

- エンドポイント数 = シークレット ARN 数（不一致時はデプロイ失敗）
- エンドポイント数: 最小 1、最大 10
- 各シークレットは JSON 形式: `{"username": "...", "password": "..."}`

---

## トラブルシューティング

### ARP ダッシュボード関連

#### ARP データの未表示

**症状**: ARP ダッシュボードに「Data source unavailable」と表示される

**原因**: ONTAP 管理エンドポイントに接続できない、または ONTAP REST API がエラーを返している

**解決策**:
```bash
# ONTAP エンドポイントへの接続確認
curl -k https://<management-ip>/api/storage/volumes?fields=anti_ransomware \
  -u fsxadmin:<password>

# ECS タスクのログを確認
aws logs tail /ecs/fsxn-mgmt-tooljet --since 10m
```

#### 「ARP/AI features require ONTAP 9.17 or later」の表示

**原因**: 接続先の FSx for ONTAP ファイルシステムが ONTAP 9.17 未満

**解決策**:
- FSx for ONTAP のバージョンを確認: `GET /api/cluster` → `version.full`
- ARP/AI 機能を使用するには ONTAP 9.17 以降にアップグレードが必要
- スナップショットリストアと FlexClone は 9.17 未満でも利用可能

#### 保護スナップショットの作成の失敗

**症状**: ワンクリックスナップショットでエラーが表示される

**原因**: ボリュームのスナップショット上限に達している、またはスペース不足

**解決策**:
```bash
# ボリュームのスナップショット数を確認
curl -k https://<management-ip>/api/storage/volumes/<uuid>/snapshots \
  -u fsxadmin:<password> | jq '.num_records'
```

---

### スナップショットリストア関連

#### リストアジョブの失敗

**症状**: ジョブポーリングで `failure` 状態が表示される

**原因**: ボリュームがビジー状態、または他の操作と競合

**解決策**:
- ONTAP エラーコードとメッセージを確認
- ボリュームに対する他の操作（SnapMirror 転送など）が完了するまで待機
- 再試行する

#### リストア中のクライアント接続の切断

**症状**: NFS/CIFS クライアントがリストア中にアクセスエラーを報告

**原因**: スナップショットリストアはインプレース操作のため、アクティブなクライアント接続に影響する

**解決策**:
- リストア前にクライアントへの通知を推奨
- 確認ダイアログの NFS/CIFS 警告を確認してから実行

---

### FlexClone 関連

#### クローン作成の失敗（スペース不足）

**症状**: ONTAP エラーコード 917927 が表示される

**原因**: アグリゲートに十分な空き容量がない

**解決策**:
- アグリゲートの使用率を確認
- 不要なスナップショットやボリュームを削除してスペースを確保

#### バリデーションエラーの表示

**症状**: フォーム送信時にフィールドレベルのエラーが表示される

**原因**: クローン名またはジャンクションパスが命名規則に違反

**解決策**:
- クローン名: 英数字とアンダースコアのみ、1〜203 文字
- ジャンクションパス: `/` で始まり、有効なパス文字のみ使用

---

### マルチポーラー関連

#### エンドポイント数とシークレット ARN 数の不一致エラー

**症状**: デプロイスクリプトが以下のエラーで終了:
```
❌ Endpoint count (3) does not match secret ARN count (2)
```

**原因**: `ONTAP_MGMT_ENDPOINTS` と `ONTAP_CREDENTIALS_SECRET_ARNS` の要素数が一致しない

**解決策**:
- 各エンドポイントに対応するシークレット ARN が存在することを確認
- カンマの前後にスペースを入れない

#### 特定のポーラーだけに起きる接続の失敗

**症状**: CloudWatch アラームが特定のポーラーの接続失敗を通知

**原因**: 対象ファイルシステムのセキュリティグループ設定、またはエンドポイントの到達性の問題

**解決策**:
```bash
# Harvest タスクのログで失敗しているポーラーを特定
aws logs tail /ecs/fsxn-mgmt-harvest --since 10m --filter-pattern "connection"

# セキュリティグループの確認
aws ec2 describe-security-groups --group-ids <fsxn-sg-id> \
  --query 'SecurityGroups[0].IpPermissionsIngress'
```

- Harvest タスクの SG から対象ファイルシステムの SG へのポート 443 アクセスが許可されていることを確認

#### ファイルシステム切替時のエラー表示

**症状**: UI でファイルシステムを切り替えると接続エラーが表示される

**原因**: 選択したファイルシステムの管理エンドポイントが到達不能

**解決策**:
- 対象ファイルシステムの管理エンドポイントが稼働していることを確認
- ネットワーク接続（VPC ピアリング、Transit Gateway 等）を確認
- 他のファイルシステムに切り替えて作業を継続可能

---

### 一般的な問題

#### Phase 1 の機能が動作しなくなった

**症状**: Phase 2A アップグレード後に既存のワークフローが動作しない

**原因**: 通常は発生しないが、パラメータの設定ミスの可能性

**解決策**:
- `ONTAP_MGMT_ENDPOINTS` が正しいエンドポイントを含んでいることを確認
- Phase 1 のワークフロー JSON ファイルが変更されていないことを確認
- ECS タスクが正常に稼働していることを確認:
  ```bash
  aws ecs describe-services \
    --cluster fsxn-mgmt-cluster \
    --services fsxn-mgmt-tooljet \
    --query 'services[0].{desired:desiredCount,running:runningCount}'
  ```

#### Grafana ダッシュボードでの ARP メトリクスの未表示

**症状**: ARP Grafana ダッシュボードのパネルが「No data」と表示される

**原因**: Harvest が ARP メトリクスを収集していない、または ONTAP バージョンが 9.17 未満

**解決策**:
- ONTAP バージョンが 9.17 以上であることを確認
- Harvest のバージョンが ARP メトリクスをサポートしていることを確認
- AMP でメトリクスが存在するか確認:
  ```
  ontap_volume_anti_ransomware_state
  ```
- メトリクスが存在しない場合、ToolJet 内の ARP ダッシュボード（REST API 直接ポーリング）を使用
