# アーキテクチャ進化: CloudWatch Logs Syslog VPCE による管理監査ログ配信

🌐 **日本語**（このページ） | [English](../en/architecture-evolution-syslog-vpce.md)

> **ステータス**: 検証中（2026-06-28）
> **関連**
>
> [AWS 発表 — CloudWatch Logs supports managed syslog ingestion](https://aws.amazon.com/about-aws/whats-new/2026/06/amazon-cloudwatch-syslog-ingestion/)
> **参考**
>
> [Classmethod ブログ](https://dev.classmethod.jp/articles/amazon-fsx-for-netapp-ontap-security-audit-log-syslog-to-cw-logs/)

---

## エグゼクティブサマリ

2026 年 6 月、AWS は CloudWatch Logs のマネージド Syslog 取り込み機能を発表しました。これにより、FSx for ONTAP の **管理アクティビティ監査ログ** を EC2 syslog サーバーなしで CloudWatch Logs に直接配信できるようになりました。

**結論**: 本プロジェクトのアーキテクチャは「AWS ネイティブ層」と「ベンダー配信層」の 2 層に再構成されます。

---

## Before / After 比較

### Before（2026 年 6 月以前）

```
管理監査ログ: FSx for ONTAP → EC2 (syslog-ng) → Splunk/SIEM
ファイルアクセス監査: FSx for ONTAP → S3 AP → Lambda → ベンダー
EMS: FSx for ONTAP → Webhook → API Gateway → Lambda → ベンダー
FPolicy: FSx for ONTAP → Fargate (TCP) → SQS → Lambda → ベンダー
```

### After（新アーキテクチャ）

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: AWS ネイティブ（常時稼働、ベンダー非依存）          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  管理監査ログ → Syslog VPCE → CloudWatch Logs               │
│  ファイルアクセス監査 → S3 AP → Lambda → CloudWatch Logs     │
│  EMS → EventBridge (マネージド) → CloudWatch Logs            │
│  FPolicy → Fargate → SQS → Lambda → CloudWatch Logs         │
│                                                              │
│  ※ CloudWatch Logs = 中央ハブ（検索、保持、アラーム）        │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: ベンダー配信（オプション、用途に応じて選択）         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  CloudWatch Logs → Subscription Filter → Lambda → ベンダー   │
│  CloudWatch Logs → Subscription Filter → Firehose → ベンダー │
│  CloudWatch Logs → Subscription Filter → OTel Collector       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 4 つのイベントソース — 更新された配信パス

| # | イベントソース | AWS ネイティブパス | ベンダーパス |
|---|-------------|------------------|------------|
| 1 | **管理監査ログ** (CLI/API操作) | Syslog VPCE → CW Logs | CW Logs → Subscription → Lambda → ベンダー |
| 2 | **ファイルアクセス監査** (EVTX/XML) | S3 AP → Lambda → CW Logs | S3 AP → Lambda → ベンダー（直接） |
| 3 | **EMS** (イベント管理) | EventBridge → CW Logs | API GW → Lambda → ベンダー |
| 4 | **FPolicy** (ファイル操作) | Fargate → SQS → Lambda → CW Logs | SQS → Lambda → ベンダー（直接） |

---

## 管理監査ログ: 新しい Syslog VPCE パス

### コンポーネント

| リソース | 役割 |
|---------|------|
| VPC Endpoint (`com.amazonaws.{region}.syslog-logs`) | Syslog 受信用 PrivateLink ENI |
| Security Group | FSx for ONTAP → VPCE のイングレス許可 |
| CloudWatch Logs Log Group | ログ保存先 |
| Resource Policy | syslog.logs.amazonaws.com に書き込み許可 |
| Syslog Configuration | VPCE → Log Group のマッピング |
| ONTAP `cluster log-forwarding` | ONTAP 側の転送設定 |

### ONTAP CLI 設定

```bash
# SSH to FSx for ONTAP management endpoint
ssh fsxadmin@<management-ip>

# Create log forwarding destination
cluster log-forwarding create \
  -destination syslog-logs.ap-northeast-1.amazonaws.com \
  -port 6514 \
  -protocol tcp-encrypted \
  -facility local7

# Verify
cluster log-forwarding show
```

### 対応プロトコル

| プロトコル | ポート | 推奨用途 |
|-----------|--------|---------|
| TCP + TLS | 6514 | **本番推奨**（暗号化） |
| TCP Plaintext | 1514 | PrivateLink 内で閉じる場合 |
| UDP | 514 | ベストエフォート（非推奨） |

---

## 選択ガイド: AWS ネイティブ vs ベンダー直接配信

### AWS ネイティブのみで十分なケース

- CloudWatch Logs Insights で十分な検索が可能
- 保持要件が CloudWatch Logs の最大保持期間内
- S3 エクスポートで長期アーカイブ
- CloudWatch Alarms で基本的なアラートが十分
- 外部ベンダーへのデータ送信を避けたい

### ベンダー配信が必要なケース

- 高度な SIEM 相関分析（Splunk SPL、Elastic KQL）
- APM × ストレージログの統合ビュー（Datadog、Dynatrace）
- ML ベースの異常検知（Datadog Anomaly、Davis AI）
- 既存の SOC ワークフローとの統合
- 高カーディナリティ分析（Honeycomb BubbleUp）

### ハイブリッドパターン（推奨）

```
FSx for ONTAP
    │
    ├─→ [管理監査] Syslog VPCE → CW Logs ─→ (常時保存)
    │                                      └─→ Subscription → ベンダー (オプション)
    │
    ├─→ [ファイルアクセス] S3 AP → Lambda ─→ CW Logs (常時保存)
    │                                     └─→ ベンダー (直接配信)
    │
    ├─→ [EMS] EventBridge → CW Logs ─→ (常時保存 + Alarm)
    │                               └─→ Lambda → ベンダー (オプション)
    │
    └─→ [FPolicy] Fargate → SQS → Lambda ─→ CW Logs (常時保存)
                                           └─→ ベンダー (直接配信)
```

---

## コスト比較

| パス | 月額目安 (10 GB/月) | 運用負荷 |
|-----|-------------------|---------|
| Syslog VPCE → CW Logs のみ | ~$8 (VPCE + CW Logs 保存) | ゼロ |
| 上記 + Subscription → ベンダー | ~$8 + ベンダー費用 | 低（Lambda 管理のみ） |
| 従来 EC2 syslog-ng | ~$66+ (EC2 + EBS) | 高（OS パッチ、エージェント更新） |

---

## CloudFormation テンプレート

`shared/templates/syslog-vpce-cloudwatch.yaml` で以下をデプロイ:

```bash
aws cloudformation deploy \
  --template-file shared/templates/syslog-vpce-cloudwatch.yaml \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --parameter-overrides \
    VpcId=<FSx-VPC> \
    SubnetIds=<FSx-Subnet> \
    FsxSecurityGroupId=<FSx-SG> \
  --region ap-northeast-1
```

デプロイ後の手動ステップ:
1. AWS Console → CloudWatch → Logs → Syslog configurations → Create
2. VPCE と Log Group を関連付け
3. FSx for ONTAP SSH → `cluster log-forwarding create`

---

## 検証ステータス

| ステップ | 状態 | 備考 |
|---------|------|------|
| VPC Endpoint 作成 | ✅ 完了 | `vpce-010e49474d23c7172`, ENI IP: `198.51.100.28` |
| Security Group 作成 | ✅ 完了 | VPC CIDR (10.0.0.0/16) → Port 6514 許可 |
| Log Group 作成 | ✅ 完了 | `/syslog/fsxn-admin-audit` |
| Resource Policy 設定 | ✅ 完了 | `syslog.logs.amazonaws.com` → Log Group |
| ONTAP log-forwarding 設定 | ✅ 完了 | `198.51.100.28:6514` (tcp_encrypted, local7) |
| Syslog Configuration (Console) | ✅ 完了 | raw SigV4 HTTP API で作成（スクリプト化済み） |
| ログ到着確認 | ✅ 完了 | TCP plaintext (1514) で配信確認 |
| CW Logs Insights クエリ | ✅ 完了 | SSH/REST API 操作が構造化ログとして到着 |

### Syslog Configuration の作成手順（AWS Console）

> **注記**
>
> 2026 年 6 月時点で `PutSyslogConfiguration` API は AWS CLI v2.35.x では未対応です。Console から作成してください。

1. AWS Console → **CloudWatch** → **Logs** → 左メニュー **Syslog configurations**
2. **Create syslog configuration** をクリック
3. 設定:
   - **VPC endpoint**: `vpce-010e49474d23c7172`
   - **Log group**: `/syslog/fsxn-admin-audit`
   - **Allow all sources**: Yes
4. **Create** をクリック

作成後、ONTAP で管理操作（`volume show` 等）を実行すると、数秒以内に CloudWatch Logs にログが到着します。

---

## 関連ドキュメント

- [AWS Docs: Syslog ingestion](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Syslog.html)
- [AWS Docs: Setting up syslog ingestion](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Syslog_Setup.html)
- [NetApp: cluster log-forwarding create](https://docs.netapp.com/us-en/ontap-cli/cluster-log-forwarding-create.html)
- [Classmethod: FSx for ONTAP 管理監査ログ → CW Logs](https://dev.classmethod.jp/articles/amazon-fsx-for-netapp-ontap-security-audit-log-syslog-to-cw-logs/)
- [本プロジェクト: イベントソースガイド](event-sources.md)
