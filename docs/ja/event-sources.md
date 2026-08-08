# イベントソースガイド

🌐 **日本語**（このページ） | [English](../en/event-sources.md)

## 概要

本プロジェクトは FSx for ONTAP の**4つのイベントソース**に対応しています。

```
┌─────────────────────────────────────────────────────────────────────┐
│ FSx for ONTAP イベントソース                                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. ファイルアクセス監査ログ                                           │
│     → S3 バケット → EventBridge → Lambda → Vendor                   │
│                                                                     │
│  2. 管理監査ログ (NEW — Syslog VPCE)                                 │
│     → Syslog → VPC Endpoint → CloudWatch Logs                      │
│     (EC2/Lambda 不要の AWS ネイティブパス)                             │
│                                                                     │
│  3. EMS (Event Management System)                                   │
│     → Webhook → API Gateway → Lambda → Vendor                      │
│     → CloudWatch Events → EventBridge → Lambda → Vendor            │
│                                                                     │
│  4. FPolicy (ファイルスクリーニング)                                    │
│     → TCP:9898 → ECS Fargate → SQS → EventBridge → Lambda → Vendor │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 1. ファイルアクセス監査ログ

### 対象イベント
- ファイル/ディレクトリのアクセス (EventID 4663)
- オブジェクトのオープン (EventID 4656)
- セキュリティ記述子の変更 (EventID 4670)
- CIFS ログオン/ログオフ

### 配信経路
```
FSx for ONTAP (vserver audit) → S3 バケット → EventBridge → Lambda → Vendor API
```

### 設定方法
[前提条件ガイド](prerequisites.md) の Step 2 を参照。

---

## 2. 管理監査ログ (Management Activity — Syslog VPCE)

### 対象イベント
- ONTAP CLI コマンド実行（SSH セッション）
- REST API 呼び出し（POST/GET/PATCH/DELETE）
- 権限昇格（`set -privilege diagnostic`）
- ログイン/ログアウト
- 構成変更（ボリューム作成、ポリシー変更等）

### 配信パス（AWS ネイティブ — EC2 不要）

```
FSx for ONTAP (cluster log-forwarding)
    │ Syslog (TCP port 1514 or 6514)
    ▼
VPC Endpoint (com.amazonaws.{region}.syslog-logs)
    │ AWS PrivateLink
    ▼
CloudWatch Logs (/syslog/fsxn-admin-audit)
```

Lambda も EC2 も不要のフルマネージドパスです。CloudWatch Logs が syslog フィールド（facility, severity, hostname, appName, message）を自動パースします。

### EC2 Syslog との比較

| 観点 | EC2 syslog-ng（従来） | Syslog VPC Endpoint（新） |
|------|-------------------|--------------------------|
| コンピュート | EC2 インスタンス | なし（マネージド） |
| コスト | ~$66/月 | ~$8/月 |
| パッチ適用 | 月次 | 不要 |
| HA | 手動マルチ AZ | マルチ AZ ENI 組み込み |

### 設定

**CloudFormation**: `shared/templates/syslog-vpce-cloudwatch.yaml`

**ONTAP REST API**:
```bash
curl -sk -u fsxadmin:<password> \
  -X POST "https://<mgmt-ip>/api/security/audit/destinations?force=true" \
  -H "Content-Type: application/json" \
  -d '{"address":"<VPCE_ENI_IP>","port":1514,"protocol":"tcp_unencrypted","facility":"local7"}'
```

**プロトコルの選択肢**:

| プロトコル | ポート | ONTAP パラメータ | 使う場面 |
|----------|------|-----------------|---------|
| TCP+TLS | 6514 | `tcp_encrypted` | 本番（暗号化） |
| TCP 平文 | 1514 | `tcp_unencrypted` | 初期検証、PrivateLink のみのネットワーク |

### ベンダーへのファンアウト（オプション）

```
CloudWatch Logs → Subscription Filter → Lambda → Datadog/Splunk/SIEM
CloudWatch Logs → Subscription Filter → Firehose → Splunk/S3
```

### セットアップガイド

詳細は [Syslog VPCE セットアップガイド](syslog-vpce-setup-guide.md) を参照。

---

## 3. EMS (Event Management System)

### 対象イベント

| カテゴリ | EMS イベント名 | 説明 | ユースケース |
|---------|--------------|------|------------|
| **ARP/AI** | `arw.volume.state` | ランサムウェア検知・状態変更 | セキュリティアラート |
| **ARP/AI** | `arw.vserver.state` | ARP SVM レベル状態変更 | セキュリティアラート |
| **クォータ** | `wafl.quota.softlimit.exceeded` | ソフトクォータ閾値超過 | 容量管理 |
| **クォータ** | `wafl.quota.hardlimit.exceeded` | ハードクォータ閾値超過 | 容量管理 |
| **容量** | `sms.vol.full` | ボリューム容量フル | 容量管理 |
| **容量** | `sms.vol.nearlyFull` | ボリューム容量ほぼフル (95%) | 容量管理 |
| **パフォーマンス** | `qos.monitor.memory.maxed` | QoS メモリ上限到達 | パフォーマンス |
| **HA** | `cf.fsm.takeoverStarted` | HA テイクオーバー開始 | 可用性 |
| **ネットワーク** | `net.linkDown` | ネットワークリンクダウン | 可用性 |

### 配信経路

#### パターン A: EMS Webhook → API Gateway → Lambda (推奨)

ONTAP 9.10.1+ では EMS イベントを Webhook で外部に通知できます。

```
ONTAP EMS → Webhook (HTTPS) → API Gateway → Lambda → Vendor API
```

**ONTAP CLI 設定:**
```bash
# 1. Create Webhook notification destination
event notification destination create -name aws-apigw \
  -syslog-transport https \
  -syslog-port 443 \
  -url https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems

# 2. Create event filter
event filter create -filter-name arp-and-quota
event filter rule add -filter-name arp-and-quota -type include \
  -message-name arw.volume.state
event filter rule add -filter-name arp-and-quota -type include \
  -message-name wafl.quota.*

# 3. Create notification
event notification create -filter-name arp-and-quota \
  -destinations aws-apigw
```

#### パターン B: CloudWatch → EventBridge → Lambda

FSx for ONTAP は EMS イベントを CloudWatch Events として発行します。

```
FSx for ONTAP EMS → CloudWatch Events → EventBridge Rule → Lambda → Vendor API
```

**EventBridge ルール例:**
```json
{
  "source": ["aws.fsx"],
  "detail-type": ["FSx for ONTAP EMS Event"],
  "detail": {
    "event-name": ["arw.volume.state", "wafl.quota.softlimit.exceeded"]
  }
}
```

参考: [AWS Docs - Monitoring FSx for ONTAP](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring_overview.html) | [EMS alerts for ARP](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/EMS-ARP.html)

---

## 4. FPolicy (ファイルスクリーニング)

### 対象イベント

FPolicy はファイル操作をイベント駆動で監視し、外部エンジンに通知します。

| プロトコル | 対応操作 |
|-----------|---------|
| **CIFS/SMB** | create, open, close, read, write, rename, delete, setattr, getattr |
| **NFSv3** | create, mkdir, read, write, rename, unlink, rmdir, setattr, link, symlink |
| **NFSv4** | create, open, close, read, write, rename, remove, setattr, getattr |

### アーキテクチャ

FPolicy は独自バイナリプロトコル (TCP) を使用するため、HTTP/API Gateway では直接受信できません。
カスタム FPolicy サーバーコンテナが TCP プロトコルを処理し、SQS → EventBridge 経由でイベントを配信します。

```
┌──────────────┐     TCP:9898      ┌──────────────────┐
│ FSx for ONTAP    │ ─────────────────→ │ ECS Fargate      │
│ FPolicy      │   (直接接続)       │ FPolicy Server   │
└──────────────┘                    └────────┬─────────┘
                                             │ SQS SendMessage
                                             ▼
                                    ┌──────────────────┐
                                    │ SQS Queue        │
                                    │ (FPolicy_Q)      │
                                    └────────┬─────────┘
                                             │ Event Source Mapping
                                             ▼
                                    ┌──────────────────┐
                                    │ Bridge Lambda    │
                                    │ (SQS→EventBridge)│
                                    └────────┬─────────┘
                                             │ PutEvents
                                             ▼
                                    ┌──────────────────┐
                                    │ EventBridge      │
                                    │ Custom Bus       │
                                    │ (fpolicy.fsxn)   │
                                    └────────┬─────────┘
                                             │ Rule
                                             ▼
                                    ┌──────────────────┐
                                    │ Vendor Lambda    │
                                    │ (forwarder)      │
                                    └──────────────────┘
```

### 配信経路

```
ファイル操作 → ONTAP FPolicy → TCP:9898 → ECS Fargate → SQS → EventBridge → Lambda → Vendor API
```

### コンピュートモード選択

| モード | 特徴 | 推奨ケース |
|--------|------|-----------|
| **Fargate** | サーバーレス、IP 自動更新 Lambda 付き | 本番環境（推奨） |
| **EC2** | 固定 IP、SSH アクセス可能 | デバッグ・開発環境 |

> **Note**
>
> Fargate モードではタスク再起動時に IP が変わるため、IP Auto-Updater Lambda が
> ECS Task State Change イベントを検知し、ONTAP REST API で FPolicy External Engine の
> `primary-servers` を自動更新します。

### FPolicy 設定

```bash
# 1. Create FPolicy External Engine (port 9898, asynchronous mode)
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous

# 2. Create FPolicy Event
vserver fpolicy policy event create -vserver FPolicySMB \
  -event-name file-ops-event \
  -protocol cifs \
  -file-operations create,write,rename,delete

# 3. Create FPolicy Policy
vserver fpolicy policy create -vserver FPolicySMB \
  -policy-name file-screening \
  -events file-ops-event \
  -engine fpolicy_lambda_engine \
  -is-mandatory false

# 4. Enable FPolicy
vserver fpolicy enable -vserver FPolicySMB \
  -policy-name file-screening \
  -sequence-number 1
```

### 注意事項

- FPolicy は独自バイナリプロトコル (TCP) を使用 — HTTP/HTTPS ではない
- ONTAP は Fargate タスク IP に直接 TCP 接続する（NLB はヘルスチェック専用）
- 非同期モード (`asynchronous`) を推奨（パフォーマンス影響を最小化）
- 同期モード (`synchronous`) はファイル操作をブロックできる（DLP 用途）
- NFSv3 の write-complete には 5 秒のデフォルト遅延あり
- コンテナイメージは ECR に格納（ARM64 アーキテクチャ）

### NLB の役割

NLB は FPolicy トラフィックのルーティングには使用されません。
ECS Fargate タスクのヘルスチェック（TCP ポート 9898）のみに使用されます。
ONTAP は Fargate タスクの ENI IP に直接接続します。

参考: [NetApp FPolicy API](https://library.netapp.com/ecmdocs/ECMLP2886776/html/resources/fpolicy_event.html) | [FPolicy FAQ](https://kb.netapp.com/Advice_and_Troubleshooting/Data_Storage_Software/ONTAP_OS/FAQ:_FPolicy:_Auditing)

---

## ユースケース別推奨構成

| ユースケース | イベントソース | 推奨パターン |
|------------|--------------|------------|
| コンプライアンス監査 | 監査ログ | S3 → EventBridge → Lambda |
| ランサムウェア検知アラート | EMS (ARP/AI) | Webhook → API GW → Lambda |
| 容量管理アラート | EMS (クォータ) | CloudWatch → EventBridge → Lambda |
| イベント駆動ファイル監視 | FPolicy | TCP:9898 → ECS Fargate → SQS → EventBridge → Lambda |
| DLP (データ漏洩防止) | FPolicy (同期) | TCP:9898 → ECS Fargate → 判定 |
| セキュリティ SIEM 連携 | 監査ログ + EMS | 複合パターン |
