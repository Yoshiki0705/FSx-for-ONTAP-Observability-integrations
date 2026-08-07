# 自動インシデント対応ガイド — ONTAP REST API によるユーザー/IP ブロック

🌐 **日本語**（このページ） | [English](../en/automated-response-guide.md)

## エグゼクティブサマリ

本ガイドでは、Amazon FSx for NetApp ONTAP に対する自動ストレージ層アクセス遮断（ユーザーブロック、IP ブロック、保護 Snapshot）を、AWS ネイティブの検知サービスと ONTAP REST API の応答アクションを組み合わせて実装する方法を解説します。専用のストレージセキュリティ製品と同等の封じ込めフェーズの能力を、AWS エコシステムとサードパーティ Observability プラットフォーム内で実現します。

> **スコープに関する注記**
>
> 本モジュールが自動化するのは NIST SP 800-61 における「封じ込め（Containment）」フェーズのうち、ストレージ層でのアクセス遮断と証拠保全のみです。侵害端末の隔離、マルウェア除去、認証情報のローテーション、他システムへの横展開の遮断（根絶・復旧フェーズ）は範囲外であり、引き続き人間の判断または他の IR ツールが必要です。

**主要機能:**
- ONTAP name-mapping による SMB ユーザーブロック（全ボリュームでアクセス拒否）
- export-policy ルールによる NFS IP ブロック
- 証拠保全のための保護 Snapshot 作成（ストーム防止クールダウン付き）
- 侵害ユーザーの CIFS セッション即時切断
- 上記を一括実行する複合封じ込めアクション

**応答タイミング（ONTAP 9.17.1P7D1 で実測済み）**:
- Lambda 実行（contain_smb_threat）: **1.8 秒**（Snapshot + ブロック + セッション切断）
- E2E（検知 → ルーティング → 応答）: **2 分以内**（通常）、**3 分以内**（worst-case: Lambda コールドスタート + VPC ENI アタッチ込み）
- SMB ブロック有効化: 次回認証試行時に即座に拒否（既存セッションはトークン期限切れまたはセッション切断まで継続）

**検知ソース（任意の組み合わせ）:**
- CloudWatch Log Alarm（管理監査ログの異常検知）
- EMS Webhook（ARP ランサムウェア検知、クォータイベント）
- FPolicy 分析（大量削除、異常な拡張子変更）
- サードパーティ SIEM モニター（Datadog、Splunk、Elastic など）

---

## アーキテクチャ

```
+-------------------------------------------------------------------+
| Detection Layer (AWS-native + SaaS Observability)                 |
+-------------------------------------------------------------------+
|                                                                   |
|  CloudWatch Log Alarm --+                                         |
|  EMS Webhook -> Monitor-+                                         |
|  FPolicy -> SIEM -------+-- SNS Trigger Topic                     |
|  Manual invocation -----+                                         |
|                                                                   |
+-------------------------------------------------------------------+
| Response Layer (Lambda + ONTAP REST API)                          |
+-------------------------------------------------------------------+
|                                                                   |
|  SNS -> Lambda (VPC) -> ONTAP REST API                            |
|           |                                                       |
|           +-> Block SMB user (name-mapping)                       |
|           +-> Block NFS IP (export-policy rule)                   |
|           +-> Create protective snapshot                          |
|           +-> Disconnect CIFS sessions                            |
|           +-> SNS notification (action result)                    |
|                                                                   |
|  DLQ -> Alarm -> Notification (failed actions)                    |
|                                                                   |
+-------------------------------------------------------------------+
```

```mermaid
graph TD
    subgraph Detection["検知層（任意のソース）"]
        CWA[CloudWatch Log Alarm]
        DD[Datadog Monitor]
        ES[Elastic SIEM]
        CLI[手動 CLI]
    end

    subgraph Trigger["SNS 疎結合"]
        SNS[SNS トリガートピック]
    end

    subgraph Response["応答層（Lambda + ONTAP）"]
        LMB[Lambda VPC内]
        LMB --> BLK_SMB[SMB ユーザーブロック<br/>name-mapping deny]
        LMB --> BLK_NFS[NFS IP ブロック<br/>export-policy + NACL]
        LMB --> SNAP[Snapshot 作成<br/>証拠保全]
        LMB --> DISC[セッション切断<br/>再認証強制]
        LMB --> NOTIFY[SNS 通知<br/>セキュリティチーム]
    end

    subgraph Verification["復旧検証（Step Functions）"]
        SF[Step Functions ワークフロー]
        SF --> CLONE[FlexClone]
        SF --> WAIT[WaitForFsxSync<br/>10 分]
        SF --> DISC2[FSx Discovery<br/>120秒ポーリング]
        SF --> AP[S3 Access Point<br/>Internet-origin]
        SF --> SCAN[指標スキャン<br/>拡張子チェック]
        SF --> VERDICT[判定記録<br/>DynamoDB]
    end

    CWA --> SNS
    DD --> SNS
    ES --> SNS
    CLI --> SNS
    SNS --> LMB
    SNAP -.->|"リストア前に検証"| SF
```

*3層アーキテクチャ: 検知（任意のSIEM/Monitor）→ 応答（Lambda、E2E < 2分）→ 検証（Step Functions、25-50分）。SNS が検知と応答を疎結合 — ベンダーロックインなし。*

---

## ブロックの仕組み

### SMB ユーザーブロック

ONTAP の name-mapping を利用してアクセスを拒否します:

| ステップ | 動作 | ONTAP CLI 相当 |
|---------|------|---------------|
| 1 | Lambda がドメインとユーザー名を含むトリガーを受信 | — |
| 2 | name-mapping を作成: `DOMAIN\user` → `" "`（空文字） | `vserver name-mapping create -direction win-unix -pattern "DOMAIN\\user" -replacement " "` |
| 3 | ユーザーの次のファイル操作が拒否される | SID→UNIX 変換失敗 → アクセス拒否 |
| 4 | SVM 内の全ボリュームに影響 | name-mapping は SVM 全体に適用 |

**スコープ**: ブロックは SVM 全体に適用されます。SVM 内の全ボリューム、共有、エクスポートでアクセスが拒否されます。

> **運用安全性に関する補足（position 1 挿入）**
>
> 拒否マッピングはデフォルトで position 1 に挿入され、既存の name-mapping よりも *先に* 評価されます。サービスアカウントや自動化ワークフロー用の既存 name-mapping がある SVM では、新しいエントリがそれらを shadowing する可能性があります。常に `health_check` + 非本番ユーザーで事前テストしてください。誤ブロックを即座に解除するには: `./automated-response-cli.sh unblock-smb --domain <DOMAIN> --user <username>`。マルチテナント SVM では、あるテナントの侵害ユーザーをブロックした際に、より高い position の共有サービスアカウントに影響しないことを確認してください。

**解除**: name-mapping エントリを削除するとアクセスが復元されます。

### NFS IP ブロック

export-policy に拒否ルールを追加します:

| ステップ | 動作 | ONTAP CLI 相当 |
|---------|------|---------------|
| 1 | Lambda がクライアント IP を含むトリガーを受信 | — |
| 2 | export-policy ルール作成: `clientmatch=<marker>,<ip>`, `ro_rule=never`, `rw_rule=never` | `export-policy rule create -clientmatch "fsxn_auto_response,<ip>" -rorule never -rwrule never` |
| 3 | ルールがポジション 1 に挿入（最優先） | 許可ルールより先に評価 |
| 4 | クライアント IP が該当ポリシーの全 NFS アクセスからブロック | — |

**スコープ**: 指定した export-policy を使用する全ボリュームに影響します。

**マーカー**: ルールの clientmatch に `fsxn_auto_response` を含めることで、識別と一括クリーンアップが容易です。

**解除**: レスポンスマーカーを含む export-policy ルールを削除します。

### CIFS セッション切断

ブロックされたユーザーが操作を続行できないよう、アクティブなセッションを強制切断します:

- ユーザーまたは IP に一致するアクティブ CIFS セッションを列挙
- REST API で各セッションを削除
- ユーザーブロックと併用して即時効果を実現

### NFS 即時遮断: 多層オプション

SMB（セッション切断で再認証を強制可能）と異なり、NFS にはセッション終了 API がありません。ONTAP export-policy deny rule は次の I/O 時に適用されますが、Linux NFS クライアントはアクセス判定を最大 60 秒間キャッシュします（`actimeo` デフォルト）。3 つのオプションがあり、組み合わせ可能です:

| オプション | レイヤー | 効果タイミング | スコープ | 設定 |
|-----------|---------|--------------|---------|------|
| **NACL deny rule**（推奨） | ネットワーク（VPC） | **即時**（パケットレベル） | IP からサブネットへの全トラフィック | `FsxSubnetId` パラメータ + `block_nfs_ip_network` アクション |
| Export-policy deny rule | ONTAP | 次の I/O 時（最大 60 秒キャッシュ） | そのポリシーを使用する NFS ボリューム | `block_nfs_ip` アクション（既存） |
| Security Group 変更 | ネットワーク（ENI） | 即時 | allow のみ（特定 IP の deny 不可、広い allow を削除する必要） | 手動またはカスタム自動化 |

**推奨アプローチ**: `contain_nfs_threat` を `FsxSubnetId` 設定済みで使用。両レイヤーを自動実行:
1. ONTAP export-policy deny rule（永続的、NACL 削除後も残る）
2. NACL deny rule（即時、ネットワークレベル、クライアントキャッシュ回避不可）

```bash
# ネットワーク層ブロックを有効にしてデプロイ:
aws cloudformation deploy \
  --template-file shared/templates/automated-response.yaml \
  --stack-name fsxn-automated-response \
  --parameter-overrides \
    ... \
    FsxSubnetId=<FSx-ENI-が配置されているサブネット> \
  --capabilities CAPABILITY_NAMED_IAM
```

**スタンドアロンのネットワークブロック**（ONTAP 層なし）:
```json
{"action": "block_nfs_ip_network", "svm_name": "svm-prod", "client_ip": "10.0.5.99", "reason": "大量削除検知"}
```

**解除**:
```json
{"action": "unblock_nfs_ip_network", "svm_name": "svm-prod", "client_ip": "10.0.5.99"}
```

> **スコープに関する補足**
>
> NACL deny rule は指定 IP からサブネット全体への全トラフィックをブロックします（NFS ポート 2049 だけではない）。NFS ポートのみに制限する場合は SNS メッセージで `block_all_ports: false` を指定してください。ONTAP export-policy rule は NFS 固有でより粒度が細かいです。

> **クリーンアップに関する補足**
>
> 本モジュールが作成する NACL ルールはルール番号 50-99 を使用します。この範囲外のルールは一切変更されません。`unblock_nfs_ip_network` または TTL 自動クリーンアップで削除します。

> **VPC Endpoint の要件**
>
> Lambda は EC2 API（`CreateNetworkAclEntry`）を呼び出して NACL ルールを管理します。VPC に NAT Gateway がある場合は自動的に動作します。ない場合は EC2 Interface VPC Endpoint（`com.amazonaws.<region>.ec2`）を追加してください。EC2 API へのアクセスがないと NACL ブロックはタイムアウトします（非致命的 — ONTAP export-policy ブロックはフォールバックとして成功します）。

> **NFSv4 リースに関する補足**
>
> NFSv4 には内部的なリース管理がありますが、ONTAP は特定クライアントのリース強制取り消し用の REST API エンドポイントを公開していません。NACL アプローチはプロトコルバージョンに関係なく NFSv3/NFSv4 の両方で機能します。

> **影響範囲に関する補足**
>
> デフォルトでは NACL ルールは指定 IP からの全プロトコルをブロックします（NFS ポート 2049 だけではない）。ブロック対象の IP がモニタリングや管理にも使われている場合は、SNS メッセージで `"block_all_ports": false` を指定して NFS ポートのみに制限してください。

---

## 比較: 本アプローチ vs 専用ストレージセキュリティ製品

専用のストレージセキュリティ製品（DII Storage Workload Security など）は、ML ベースの行動分析と統合された封じ込めを提供します。本プロジェクトでは、AWS ネイティブおよび SaaS の検知と ONTAP REST API の応答を組み合わせて同等の封じ込めを実現します:

| 機能 | 専用製品 | 本アプローチ |
|------|---------|------------|
| SMB ユーザーブロック | ✅ 自動 | ✅ 自動（同じ ONTAP API） |
| NFS IP ブロック | ✅ 自動 | ✅ 自動（同じ ONTAP API） |
| 保護 Snapshot | ✅ 自動 | ✅ 自動（クールダウン付き） |
| セッション切断 | ✅ 自動 | ✅ 自動 |
| ユーザー行動 ML | ✅ 内蔵（ユーザー別ベースライン） | SIEM 経由（Datadog ML Anomaly、Elastic ML Jobs 等） |
| AD ユーザー追跡 | ✅ 専用コレクタ | FPolicy ログ（user フィールド）+ AD ルックアップ |
| 検知スコープ | ストレージのみ | ストレージ + ネットワーク + アプリケーション（より広い文脈） |
| データ残留場所 | SaaS（外部） | AWS リージョン（データは VPC 内に留まる） |
| SIEM 連携 | 限定的なエクスポート | ネイティブ（検知が SIEM から発生） |
| コストモデル | ノード単位ライセンス | 従量課金（Lambda 実行回数） |

> **運用に関する補足**
>
> 基盤となる ONTAP メカニズムは同一です。両方のアプローチが同じ REST API エンドポイントを使用します。違いは検知インテリジェンスの配置場所です。専用製品は ML を SaaS に組み込み、本アプローチは利用者が選択した Observability プラットフォームに検知を委ねます。

> **セキュリティアーキテクチャに関する補足**
>
> AWS ネイティブの検知は、ストレージ専用ソリューションでは相関できない広範な攻撃コンテキスト（VPC Flow Logs、CloudTrail、GuardDuty findings）を提供します。既存の SIEM 投資がある組織にとって、これはセキュリティ運用ワークフローの自然な拡張です。

---

## 前提条件

### ONTAP バージョン

- **FSx for ONTAP** — 現在サポートされている全バージョン（ONTAP 9.11.1+）
- **Name-mapping REST API** — ONTAP 9.6+ から利用可能
- **Export-policy REST API** — ONTAP 9.6+ から利用可能
- **CIFS sessions REST API** — ONTAP 9.8+ から利用可能
- **ARP (Autonomous Ransomware Protection)** — ONTAP 9.10.1+（学習モード）、9.13.1+（ARP/AI）

### ONTAP 権限

Lambda 実行ロールは、Secrets Manager に保存された `fsxadmin` 認証情報で ONTAP に接続します。以下の ONTAP 操作を使用します:

```
# 必要な権限（fsxadmin にはデフォルトで付与済み）
- GET /api/svm/svms
- GET /api/storage/volumes
- POST /api/storage/volumes/{uuid}/snapshots
- GET/POST/DELETE /api/name-services/name-mappings
- GET/POST/DELETE /api/protocols/nfs/export-policies/{id}/rules
- GET/DELETE /api/protocols/cifs/sessions
```

### ネットワークアクセス

Lambda 関数は以下の VPC 内にデプロイする必要があります:
- ONTAP 管理エンドポイントへのルートを持つプライベートサブネット
- ONTAP 管理 IP への HTTPS（TCP 443）アウトバウンドを許可する Security Group
- NAT Gateway は不要（ONTAP エンドポイントはプライベート）

#### ネットワーク前提条件チェックリスト

| 要件 | 確認方法 |
|------|---------|
| Lambda サブネットが ONTAP mgmt IP へのルートを持つ | `aws ec2 describe-route-tables` — ONTAP CIDR へのルートを確認 |
| Security Group が TCP 443 を許可 | SG アウトバウンドルール: `TCP 443 → <ONTAP-mgmt-IP>/32` |
| ONTAP 管理 LIF が UP | `network interface show -role cluster-mgmt`（ONTAP に SSH） |
| クロス VPC（該当する場合） | VPC ピアリングまたは Transit Gateway ルート + SG 参照 |
| 同一 VPC 異なるサブネット | NACL がサブネット間の TCP 443 をブロックしていないか確認 |

> **TLS に関する注記**
>
> デフォルトのモジュール設定は証明書検証を無効にしています（`CERT_NONE`）。本番環境では FSx for ONTAP の CA 証明書を取得し、`CA_CERT_PATH` 環境変数で指定してください。証明書取得方法: `security certificate show -type root-ca -vserver <svm>`（ONTAP CLI）。

### Secrets Manager

ONTAP 認証情報を JSON として保存:
```json
{
  "username": "fsxadmin",
  "password": "<your-password>"
}
```

---

## デプロイ

### デプロイ前チェックリスト

デプロイ前に以下を確認してください（デプロイ失敗の最も一般的な原因）:

| # | 確認事項 | 確認方法 | 不足時の対応 |
|---|---------|---------|------------|
| 1 | Secrets Manager に ONTAP パスワードが保存済み | `aws secretsmanager get-secret-value --secret-id <name>` | 作成: `{"username":"fsxadmin","password":"<pwd>"}` |
| 2 | パスワードが実際の FSx for ONTAP と一致 | SSH で ONTAP に接続できるか | リセット: `aws fsx update-file-system --file-system-id <id> --ontap-configuration FsxAdminPassword=<new>` |
| 3 | Lambda サブネットが ONTAP mgmt IP に到達可能 | 同一 VPC/サブネット | ルートテーブルを確認 |
| 4 | SG が Lambda → ONTAP TCP 443 を許可 | ONTAP ENI の SG に Lambda SG からのイングレスがあるか | ルール追加（下記参照） |
| 5 | VPC の DNS サポートが有効 | `aws ec2 describe-vpc-attribute --attribute enableDnsSupport` | `true` である必要あり |

### パラメータ値の確認方法

> **自動化された事前検証**: `bash shared/scripts/preflight-check.sh --vpc-id <vpc-id> --profile automated-response` を実行すると、既存の VPC Endpoint を自動検出し、セキュリティグループのエグレスを確認し、推奨パラメータ値を生成します。詳細は[デプロイメントガイド](deployment-guide.md)を参照してください。

```bash
# 1. FSx for ONTAP 管理 IP
aws fsx describe-file-systems --file-system-ids <fs-id> \
  --query 'FileSystems[0].OntapConfiguration.Endpoints.Management.IpAddresses[0]' \
  --output text

# 2. VPC とサブネット（FSx と同じ）
aws fsx describe-file-systems --file-system-ids <fs-id> \
  --query 'FileSystems[0].{VpcId:VpcId,SubnetIds:SubnetIds}' --output json

# 3. SVM 名
aws fsx describe-storage-virtual-machines \
  --filters Name=file-system-id,Values=<fs-id> \
  --query 'StorageVirtualMachines[].Name' --output text

# 4. Secrets Manager ARN
aws secretsmanager list-secrets --filters Key=name,Values=fsx \
  --query 'SecretList[].ARN' --output text
```

### Security Group 設定（必須）

ONTAP 管理エンドポイントの ENI に紐づく Security Group に、Lambda SG からの TCP 443 イングレスルールを追加:

```bash
# ONTAP ENI の Security Group を特定
# aws fsx describe-file-systems → NetworkInterfaceIds → describe-network-interfaces

# ルール追加: Lambda SG → ONTAP SG (TCP 443)
aws ec2 authorize-security-group-ingress \
  --group-id <ontap-eni-sg> \
  --ip-permissions 'IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=<lambda-sg>,Description="Automated response Lambda"}]'
```

### CloudFormation スタックのデプロイ

```bash
aws cloudformation deploy \
  --template-file shared/templates/automated-response.yaml \
  --stack-name fsxn-automated-response \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=arn:aws:secretsmanager:<region>:<account>:secret:<name> \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-id> \
    SecurityGroupId=<sg-id> \
    DefaultSvmName=<svm-name> \
    SharedPythonLayerArn=<layer-arn> \
    NotificationEmail=<your-email> \
    CreateVpcEndpoints=true \
  --capabilities CAPABILITY_NAMED_IAM
```

> **`SharedPythonLayerArn` パラメータ（新規）**
>
> `ontap_response.py` を含む `fsxn-shared-python` Lambda Layer の ARN を指定します。未指定の場合、ハンドラーは `ModuleNotFoundError` で失敗します。事前に `bash shared/python/build-layer.sh` でビルド・発行してください。

> **`CreateVpcEndpoints` パラメータ**
>
> Secrets Manager と SNS のインターフェース VPC Endpoint をデフォルトで作成します。VPC 内の Lambda は VPC Endpoint（または NAT Gateway）なしでは AWS API に到達**できません**。既にこれらの VPC Endpoint がある場合は `false` に設定してください。VPC に NAT Gateway がある場合も `false` にすることで追加の ~$14/月 を避けられます。

### デプロイ後: Lambda Layer の接続

ontap_response モジュールは Lambda Layer として提供されます（インラインコードには含まれません）:

```bash
# Layer zip をビルド
bash shared/python/build-layer.sh

# Lambda Layer として発行
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name fsxn-shared-python \
  --zip-file fileb://shared/python/dist/fsxn-shared-python-layer.zip \
  --compatible-runtimes python3.12 \
  --description "FSx for ONTAP shared modules" \
  --query 'LayerVersionArn' --output text)

# Lambda に接続
aws lambda update-function-configuration \
  --function-name fsxn-automated-response-handler \
  --layers $LAYER_ARN
```

> **AD対応 SMB ブロック（Layer v3以降）**: `ontap_response.py` モジュールは、対象SVMがActive Directoryに参加しているかを自動検出します（CIFSサービスの存在を確認）。AD参加SVMでは、`block_smb_user` は `replacement: " "`（スペース）ではなく `replacement: "nobody"` を使用します。ONTAP 9.17.1のバックグラウンドバリデーションプロセスが、解決不能なreplacement値のname-mappingを30-60秒で自動削除するためです。`nobody`ユーザー（UID 65535）はローカルUNIXユーザーテーブルに存在し、永続します。ONTAP 9.17.1P7D1で検証済み（2026年7月）。

### デプロイ検証

```bash
# health_check を送信（5 秒以内に完了するはず）
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-automated-response \
  --query 'Stacks[0].Outputs[?OutputKey==`TriggerTopicArn`].OutputValue' \
  --output text)

aws sns publish --topic-arn $TOPIC_ARN \
  --message '{"action":"health_check","svm_name":"<svm-name>"}'

# Lambda ログを確認（15 秒待機）
sleep 15
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-automated-response-handler \
  --start-time $(( $(date +%s) - 30 ))000 \
  --query 'events[-3:].message' --output text
```

**成功の指標**: Duration < 5秒、`timeout` なし、`ERROR` なし。

**`timeout` が出る場合**: VPC Endpoint（Secrets Manager + SNS）と Security Group ルールを確認。

### 検知ソースの接続

デプロイ後、トリガー SNS トピックに検知ソースをサブスクライブします:

**CloudWatch Log Alarm → SNS:**
```bash
# Log Alarm の Action が Trigger Topic を指すよう設定
# （Log Alarm 作成時に設定）
```

**SIEM / Observability プラットフォーム → SNS:**

| プラットフォーム | 接続方法 |
|-------------|---------|
| Datadog | Monitor 通知メッセージで `@sns-<topic-name>` を使用 |
| Splunk | Alert action → AWS SNS notification (Splunk Add-on for AWS) |
| Elastic | Kibana Alert → SNS connector action |
| Grafana | Alert rule → Contact point → AWS SNS |
| New Relic | Workflow → Destination → AWS SNS |
| PagerDuty | Event Orchestration → Custom Action → Lambda → SNS |
| 任意のプラットフォーム | HTTP Webhook → Lambda → SNS publish |

**手動テスト実行:**
```bash
aws sns publish \
  --topic-arn <スタック出力の TriggerTopicArn> \
  --message '{
    "action": "contain_smb_threat",
    "svm_name": "svm-prod-01",
    "domain": "CORP",
    "username": "test-user",
    "volume_name": "vol_data",
    "reason": "手動テスト"
  }'
```

---

## サポートされるアクション

### 個別アクション

| アクション | 必須フィールド | 説明 |
|-----------|-------------|------|
| `block_smb_user` | svm_name, domain, username | name-mapping で SMB ユーザーをブロック |
| `unblock_smb_user` | svm_name, domain, username | SMB ユーザーブロックを解除 |
| `block_nfs_ip` | svm_name, client_ip | export-policy ルールで IP をブロック |
| `unblock_nfs_ip` | svm_name, client_ip | IP ブロックを解除 |
| `create_snapshot` | svm_name, volume_name | 保護 Snapshot を作成 |

### 複合アクション（マルチステップ）

| アクション | ステップ | ユースケース |
|-----------|---------|------------|
| `contain_smb_threat` | Snapshot → ユーザーブロック → セッション切断 | 侵害された AD ユーザーを検知 |
| `contain_nfs_threat` | Snapshot → IP ブロック | 不審な NFS クライアント活動 |
| `contain_multiprotocol_threat` | Snapshot → SMB ユーザーブロック → NFS IP ブロック → セッション切断 | SMB と NFS の両方でアクセスされるボリューム — 単一プロトコルのブロックのみでは攻撃者がプロトコルを切り替えて回避できるため不十分 |

---

## SNS メッセージフォーマット

### 基本フォーマット（最低限必須）

```json
{
  "action": "contain_smb_threat",
  "svm_name": "svm-prod-01",
  "domain": "CORP",
  "username": "jdoe",
  "volume_name": "vol_data",
  "policy_name": "default",
  "reason": "ARP ランサムウェア検知 - callhome.arw.activity.seen alert"
}
```

### 拡張フォーマット（コンプライアンス/監査向け推奨）

```json
{
  "action": "contain_smb_threat",
  "svm_name": "svm-prod-01",
  "domain": "CORP",
  "username": "jdoe",
  "volume_name": "vol_data",
  "policy_name": "default",
  "reason": "ARP ランサムウェア検知 - callhome.arw.activity.seen alert",
  "incident_id": "INC-2026-0712-001",
  "detection_source": "datadog-monitor-fsxn-arp",
  "severity": "critical",
  "trigger_id": "msg-abc123-def456"
}
```

| フィールド | 必須 | 説明 |
|-----------|------|------|
| action | はい | サポートされるアクション名のいずれか |
| svm_name | はい（または DEFAULT_SVM_NAME を設定） | 対象 SVM |
| domain | SMB アクション | Windows ドメイン名 |
| username | SMB アクション | Windows ユーザー名 |
| client_ip | NFS アクション | クライアント IP アドレス |
| volume_name | Snapshot / 複合アクション | 保護対象ボリューム |
| policy_name | NFS アクション | エクスポートポリシー（デフォルト: "default"） |
| reason | はい（ブロックアクション） | 人間が読める理由（ログに記録） |
| incident_id | いいえ（推奨） | 外部インシデント追跡 ID（PagerDuty、ServiceNow 等） |
| detection_source | いいえ（推奨） | どのシステムが脅威を検知したか（フォレンジック相関用） |
| severity | いいえ（推奨） | `critical` / `high` / `medium` / `low` |
| trigger_id | いいえ | SNS MessageId または上流の相関 ID |

> **コンプライアンス補足（HIPAA/FISC/SOC2）**
>
> 規制環境では、常に `incident_id`、`detection_source`、`severity` を含めてください。これらのフィールドは CloudWatch Logs と通知メッセージにパススルーされ、監査証跡を形成します。

---

## 連携例

### 重大度ベースのルーティング（重要 — 誤検知によるロックアウト防止）

ARP は 2 つの重大度レベルでイベントを発火します。**`alert`（高信頼）の場合のみ自動ブロック**:

| ARP 重大度 | 信頼度 | 推奨アクション | 根拠 |
|-----------|--------|-------------|------|
| `alert` | 高（ファイル改ざん + 暗号化確認済み） | ✅ 自動封じ込め (`contain_smb_threat`) | 高信頼度、被害進行中 |
| `warning` | 中（疑わしいが未確認） | ⚠️ 通知のみ（自動ブロック禁止） | 学習期間中の誤検知の可能性 |

> **ARP 学習期間**
>
> ARP は行動ベースラインの構築に 30 日を要します。この期間中、`warning` イベントは頻発し多くは良性です（大量ファイル変換、バックアップソフトウェア）。学習期間中に `warning` で自動ブロックすると正当なユーザーを妨害します。

**検知ルール設定例（任意の SIEM）**:
```
# 自動封じ込め: severity=alert のみ
フィルター: source=fsxn-ems AND event_name=arw.volume.state AND severity=alert
→ アクション: SNS publish (contain_smb_threat)

# 通知のみ: severity=warning
フィルター: source=fsxn-ems AND event_name=arw.volume.state AND severity=warning
→ アクション: 通知のみ（Slack/PagerDuty/メール — 調査のみ、自動ブロック禁止）
```

### 例 1: CloudWatch Log Alarm → 自動ブロック

管理監査ログで 5 分以内にログイン失敗が 10 回以上検知された場合:

```
CloudWatch Log Alarm (クエリ: ログイン失敗 > 10)
  → SNS (Trigger Topic)
  → Lambda
  → ONTAP: block_smb_user + create_snapshot
  → SNS 通知 → セキュリティチーム
```

### 例 2: SIEM ARP Monitor → 全面封じ込め

Observability プラットフォームが ARP EMS イベントを受信しモニターが発火:

```
ONTAP ARP → EMS Webhook → Observability プラットフォーム（任意のベンダー）
  → 検知ルール発火（ARP イベント一致）
  → Workflow/Action → SNS publish (contain_smb_threat)
  → Lambda → ONTAP: snapshot + block + disconnect
  → 通知 → PagerDuty / Slack / メール
```

### 例 3: FPolicy 大量削除 → IP ブロック

FPolicy 分析で単一 IP から 5 分以内に 50 回以上の削除を検知:

```
FPolicy → SQS → Lambda → SIEM（任意のベンダー）
  → SIEM ルール発火（大量削除閾値）
  → SNS publish (contain_nfs_threat)
  → Lambda → ONTAP: snapshot + IP ブロック
```

---

## 運用手順

### ブロック済みユーザー/IP の確認

```bash
# FSx for ONTAP 管理エンドポイントに SSH
ssh fsxadmin@<management-ip>

# ブロック済み SMB ユーザー（空の replacement を持つ name-mapping）
vserver name-mapping show -direction win-unix -replacement " "

# ブロック済み NFS IP（マーカー付きルール）
export-policy rule show -clientmatch *fsxn_auto_response*
```

### 手動解除

SNS メッセージ経由:
```bash
aws sns publish \
  --topic-arn <TriggerTopicArn> \
  --message '{"action":"unblock_smb_user","svm_name":"svm-prod-01","domain":"CORP","username":"jdoe"}'
```

ONTAP CLI 経由（緊急時）:
```bash
# SMB ユーザーのブロック解除
vserver name-mapping delete -direction win-unix -position <position>

# NFS IP のブロック解除
export-policy rule delete -vserver <svm> -policyname <policy> -ruleindex <index>
```

### 監視

| メトリクス | ソース | アラート閾値 |
|-----------|--------|------------|
| DLQ 深度 | CloudWatch（自動作成アラーム） | > 0 メッセージ |
| Lambda エラー | CloudWatch Lambda メトリクス | 5 分で > 0 |
| 応答レイテンシ | Lambda Duration メトリクス | p95 > 10s |
| アクティブブロック数 | カスタムメトリクス（オプション） | トラッキングのみ |
| 保護対象アカウントへのブロック試行 | CloudWatch Logs フィルタ `Cannot block protected account`（`_validate_username` からの HTTP 403） | > 0 — 即時調査 |
| ブロック発生率（プロトコル問わず） | Lambda 呼び出し回数からのカスタムメトリクス | 想定インシデント発生率を超えたらアラーム — [自動応答セキュリティ補遺](automated-response-security-addendum.md)の「10. レート制限 & スケーラビリティ」セクションを参照 |

> **インシデント対応に関する補足**
>
> 保護対象アカウント（`fsxadmin`、`administrator`、または `PROTECTED_ACCOUNTS_EXTRA` に登録されたエントリ）へのブロック試行が拒否された場合、それ自体が調査に値する信号です。誤った ID を指す検知ルールの設定ミス、または攻撃者が応答パイプライン自体を悪用して正当な管理者をロックアウトしようとしている可能性を示唆します。ログに記録するだけでなく、この状態にアラートを設定してください。
>
> **信頼性に関する補足**
>
> 過剰に反応する自動応答システムは、それ自体が自己誘発型のサービス拒否ベクターになり得ます（例: ノイズの多い検知ルールが短時間で多数のユーザーに対して `contain_smb_threat` を発火させる）。失敗だけでなく呼び出し/ブロック発生率にもアラームを設定し、暴走した検知ソースが大量のユーザーをロックアウトする前に検知できるようにしてください。

---

## セキュリティ考慮事項

- **最小権限** — Lambda ロールは Secrets Manager（読取）、SNS（発行）、ONTAP への VPC ネットワークアクセスのみ。広範な IAM 権限なし。
- **認証情報ローテーション** — Secrets Manager の自動ローテーションを ONTAP 認証情報に使用。
- **監査証跡** — 全アクションは CloudWatch Logs に相関 ID 付きで記録。
- **クールダウン保護** — Snapshot 作成に設定可能なクールダウン（デフォルト 15 分）を適用し、持続的攻撃時のストレージ枯渇を防止。
- **マーカーベースのクリーンアップ** — 全応答ルールに `fsxn_auto_response` マーカーを含め、安全な識別と一括削除を実現。
- **時間制限付きブロック** — 付随する [`automated-response-ttl.yaml`](../../shared/templates/automated-response-ttl.yaml) スタックをデプロイすると、EventBridge Scheduler により設定可能な期間後にブロックを自動解除できます。未デプロイの場合、ブロックは手動で解除するまで残り続けます — この挙動が環境として許容できるか、デプロイ計画時に判断してください。

---

## コスト見積もり

### 比較: 本アプローチ vs DII Storage Workload Security

| 観点 | 本アプローチ（AWS ネイティブ） | DII Storage Workload Security |
|------|-------------------------------|-------------------------------|
| **月額ベースライン** | ~$0.51（Lambda + SNS + Secrets Manager） | 容量ベースライセンス（ノード単位 or TB 単位） |
| **VPC Endpoint（必要時）** | +~$14/月（Interface EP 2本） | N/A（SaaS データプレーン） |
| **スケール基準** | 呼び出し回数（インシデント頻度） | 監視対象ストレージ容量 |
| **月 10 インシデント時** | ~$0.45 | ライセンス費用変化なし |
| **月 10,000 インシデント時** | ~$36 | ライセンス費用変化なし |
| **検知コスト** | 既存 SIEM（既に支払い済み） | ライセンスに内包 |
| **セットアップ時間** | 30 分（CloudFormation） | 数日（エージェント + コレクタ + AD 設定） |
| **データ残留場所** | お客様の AWS VPC のみ | ベンダー SaaS クラウド |

> **コスト比較に関する補足**: 比較対象は*封じ込めフェーズ*のコストのみです。DII のライセンスには ML ベースの検知（本アプローチでは既存 SIEM に委任）も含まれています。異常検知機能付きの SIEM をまだ持っていない場合は、そのコストも本アプローチ側の比較に加えてください。

| コンポーネント | 月額（一般的） | 備考 |
|-------------|-------------|------|
| Lambda | ~$0.10 | 月 100 回未満の実行（インシデント対応のみ） |
| SNS | ~$0.01 | 低メッセージ量 |
| Secrets Manager | ~$0.40 | 1 シークレット |
| VPC ENI | $0（Lambda VPC） | 既存 VPC と共有 |
| **合計** | **~$0.51/月** | VPC/NAT コスト除外（共有） |

### スケール時のコスト

| インシデント量 | Lambda | CW Logs | SNS | 合計 |
|-------------|--------|---------|-----|------|
| 10/月 | $0.01 | $0.03 | $0.01 | ~$0.45 + Secrets Manager |
| 100/月 | $0.05 | $0.30 | $0.01 | ~$0.76 |
| 1,000/月 | $0.50 | $3.00 | $0.05 | ~$3.95 |
| 10,000/月 | $5.00 | $30.00 | $0.50 | ~$35.90 |

> 10,000 件/月（極端な量 — 検知閾値の設定ミスを示唆）でも月額 $40 未満。Lambda VPC のコールドスタートコストは、ENI が呼び出し間で再利用されるため無視可能です。

### 本番デプロイに関する補足

CloudFormation テンプレートは、クイックスタート用に Lambda コードを `ZipFile` として埋め込んでいます。本番の GitOps ワークフロー向けには:
1. `ontap_response.py` を Lambda Layer としてパッケージ（`shared/python/` ディレクトリ構造）
2. `ZipFile` の代わりに S3 バケット経由の `CodeUri` を使用
3. Layer をスタックとは独立にバージョン管理
4. CI/CD パイプライン: `pytest` → Layer パッケージ → デプロイ → 統合テスト

---

---

## クリーンアップ / 環境削除

```bash
# TTL スタックを先に削除（依存関係なし）
aws cloudformation delete-stack --stack-name fsxn-automated-response-ttl

# メインスタックを削除（VPC Endpoints + Lambda + SNS）
aws cloudformation delete-stack --stack-name fsxn-automated-response

# Lambda Layer を削除（オプション）
aws lambda delete-layer-version --layer-name fsxn-shared-python --version-number 2

# テスト Snapshot の削除
# ssh fsxadmin@<management-ip> "volume snapshot delete -vserver <svm> -volume <vol> -snapshot incident_response_*"
```

---

## 検証済み環境

本ソリューションは以下の環境で E2E 検証済み:

| 項目 | 値 |
|------|---|
| FSx for ONTAP | Single-AZ, ONTAP 9.17.1 |
| リージョン | ap-northeast-1 (Tokyo) |
| Lambda ランタイム | Python 3.12 |
| テスト済みアクション | health_check, create_snapshot, block_smb_user, block_nfs_ip, unblock, TTL cleanup, cooldown |
| 全アクション | 1 秒未満（VPC Endpoints プロビジョニング後） |

---

## 関連ドキュメント

- [デプロイメントガイド](deployment-guide.md) — VPC Endpoint 設定、パラメータファイル、デプロイ前検証を含む完全なデプロイ手順
- [Verified-Clean Recovery Point ガイド](verified-recovery-point-guide.md) — インシデント後のスナップショット検証ワークフロー
- [ARP インシデント対応ガイド](arp-incident-response-guide.md)
- [EMS Webhook セットアップ](../../integrations/datadog/docs/ja/ems-fpolicy-setup.md)
- [FPolicy 運用ノート](operational-notes-fpolicy.md)
- [パイプライン SLO](pipeline-slo.md)
- [CloudWatch Log Alarm ガイド](syslog-vpce-setup-guide.md)

---

## トラブルシューティング

### Lambda 実行の失敗

| 症状 | 原因 | 解決策 |
|------|------|--------|
| Lambda タイムアウト (60s) | ONTAP 管理エンドポイント到達不可 | Security Group ルール（TCP 443 アウトバウンド）確認、ONTAP 管理 LIF のステータス確認 |
| ONTAP から HTTP 401 | 認証情報が期限切れまたは不正 | Secrets Manager のシークレット値を確認、fsxadmin パスワードを検証 |
| ONTAP から HTTP 403 | 権限不足 | fsxadmin ロールが必要な API アクセスを持つか確認（デフォルト: フル） |
| `SVM not found` エラー | SVM 名の不一致 | SVM 名が正確に一致するか確認（大文字小文字区別）。`vserver show` で一覧 |
| `Volume not found` エラー | ボリュームが存在しないか SVM が違う | ボリューム名と SVM の関連付けを確認 |
| DLQ メッセージの蓄積 | 繰り返しの失敗 | Lambda CloudWatch Logs で根本原因を確認、DLQ からリプレイ |

### ブロックが効かない

| 症状 | 原因 | 解決策 |
|------|------|--------|
| ブロック後も SMB ユーザーがアクセス可能 | 既存セッションがアクティブ | `contain_smb_threat`（セッション切断含む）を使用するか、セッションタイムアウトを待つ |
| ブロック後も NFS クライアントがアクセス可能 | NFS 属性キャッシュ | 60 秒待つ（デフォルト `actimeo`）か、テスト時は `mount -o actimeo=0` で再マウント |
| ブロック存在するが ONTAP にエントリなし | SVM が違う | SNS メッセージの SVM 名がターゲット SVM と一致するか確認 |

### TTL 自動解除の問題

| 症状 | 原因 | 解決策 |
|------|------|--------|
| ブロックが削除されない | EventBridge Scheduler が実行されていない | EventBridge コンソールでスケジューラー状態を確認 |
| 全ブロックが早期に削除される | TTL Lambda が全レスポンスマーカー付きブロックを削除 | 下記「TTL の制限事項」参照 |
| クリーンアップ Lambda エラー | TTL Lambda から ONTAP に到達不可 | メイン Lambda と同じ VPC/SG 要件 |

> **TTL の制限事項**
>
> 現在の TTL 実装は、各実行時に `fsxn_auto_response` マーカーを持つ ALL ブロックを作成時刻に関係なく削除します。ONTAP の name-mapping エントリは作成タイムスタンプを持ちません。正確な時間ベースの TTL 実施には、ブロック作成時刻を記録する DynamoDB テーブルを実装し、TTL Lambda がそのテーブルを確認してから削除する必要があります。将来の拡張として追跡中です。

---

## FAQ

**Q: これは DII Storage Workload Security を完全に置き換えますか？**
A: ストレージ層での同じ *封じ込めフェーズのアクション*（ブロック/Snapshot/切断）を提供します。検知インテリジェンスは異なります: DII は内蔵のユーザー別 ML ベースラインを使用し、本アプローチは利用者が選択した SIEM の分析機能を使用します。本モジュールも DII も根絶・復旧（侵害端末の隔離、マルウェア除去、認証情報のローテーション）は行いません — どちらも封じ込めフェーズのツールであり、インシデント対応ライフサイクルの残りの部分には引き続き人間または別の IR ツールが必要です。既存の SIEM 投資がある組織では、組み合わせアプローチにより、ストレージ単体の検知よりも広い検知文脈（ネットワーク + アプリケーション + ストレージ）を提供できます。

**Q: Lambda が ONTAP に到達できない場合はどうなりますか？**
A: 実行が失敗し、メッセージが DLQ に入り、DLQ アラームが発火します。ネットワーク接続性（Security Group、ルートテーブル、ONTAP 管理 LIF の状態）を調査してください。

**Q: 複数の SVM で同時にユーザーをブロックできますか？**
A: SVM ごとに 1 つの SNS メッセージを送信してください。複合アクションは 1 回の実行で単一 SVM を対象とします。マルチ SVM ブロックには、以下の Step Functions ファンアウトパターンまたは CLI ループを使用:

```
EventBridge / SIEM Alert
  -> Step Functions (fan-out)
       -> Parallel state:
            Branch 1: SNS publish (svm-prod-01, contain_smb_threat)
            Branch 2: SNS publish (svm-prod-02, contain_smb_threat)
            Branch 3: SNS publish (svm-dr-01, contain_smb_threat)
       -> Wait for all branches
       -> Notify: "3 SVM でユーザーをブロック"
```

`automated-response-multi-svm-cli.sh` ラッパーを使用すると、複数 SVM へのファンアウト、SVM ごとの成功/失敗の報告、いずれかの SVM が失敗した場合の非ゼロ終了コードが得られます:

```bash
export RESPONSE_TOPIC_ARN="arn:aws:sns:ap-northeast-1:123456789012:fsxn-automated-response-trigger"

./shared/scripts/automated-response-multi-svm-cli.sh contain-smb \
  --svms "svm-prod-01,svm-prod-02,svm-dr-01" \
  --domain CORP --user jdoe --volume vol_data \
  --reason "ARP detection - multi-SVM block"
```

**Q: ブロックはどのくらいの速さで有効になりますか？**
A: SMB name-mapping ブロックは新しい接続に対して即時有効です。既存セッションは切断されるまでアクティブのままです（`contain_smb_threat` アクションがこれを処理します）。NFS export-policy ルールは新しいマウントに対して即時有効ですが、既存マウントはキャッシュの有効期限切れが必要な場合があります。

**Q: 正当なユーザーをブロックしてしまうリスクはありますか？**
A: はい — これは全ての自動応答システムに共通です。対策: (1) 検知閾値を保守的に設定、(2) 通知トピックでオペレーターに即時通知、(3) 自動解除付きの時間制限ブロックの実装、(4) 迅速な手動解除の手順書を整備。


---
