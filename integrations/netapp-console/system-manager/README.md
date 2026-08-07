# ONTAP System Manager — FSx for ONTAP GUI 管理

🌐 [日本語](#概要) | [English](#overview)

---

## 概要

ONTAP System Manager は ONTAP の GUI 管理ツールです。**FSx for ONTAP では管理エンドポイントへの直接ブラウザアクセスでは System Manager UI を利用できません。** NetApp Console（旧 BlueXP）経由でアクセスする必要があります。

> ⚠️ **重要**: オンプレミス ONTAP とは異なり、FSx for ONTAP では `https://<management-endpoint-ip>` に直接アクセスしても System Manager UI は表示されません（404 エラー）。REST API (`/api/`) のみ直接利用可能です。

### アクセス方法

FSx for ONTAP で System Manager を利用するには、以下の **NetApp Console 経由** のアクセスが必要です:

```
NetApp Console (https://console.netapp.com)
  → Console Agent (VPC 内 VM) または Link (AWS Lambda)
  → FSx for ONTAP 管理エンドポイント
  → System Manager UI を NetApp Console 内で表示
```

### 前提条件

| 項目 | 要件 |
|------|------|
| NetApp アカウント | **必要**（NSS: NetApp Support Site アカウント） |
| Console Agent or Link | VPC 内に Console Agent (VM) をデプロイ、または Link (Lambda) を作成 |
| AWS 認証情報 | NetApp Console に AWS クレデンシャルを登録（読み取り専用 or 読み書き） |
| ONTAP バージョン | 9.10.1 以上（FSx for ONTAP は対応済み） |
| ネットワーク | Console Agent → FSx for ONTAP 管理エンドポイント (443) の通信許可 |

### 費用

| コンポーネント | 費用 |
|-------------|------|
| NetApp Console (SaaS) | **無料**（基本機能） |
| NetApp アカウント作成 | **無料** |
| Console Agent (EC2) | EC2 インスタンス費用（t3.xlarge 推奨） |
| Link (Lambda) | Lambda 実行費用（微小） |
| System Manager UI | **無料**（NetApp Console 内で利用） |

### 対象ユーザー

- Windows ファイルリソースマネージャーに慣れた運用担当者
- CLI 操作に不慣れだが、ストレージ管理を行う必要がある部門
- GUI での監査ログ・クォータ設定を求めるお客様

## 検証済み操作

> **2026年5月検証結果**: NetApp Console の Systems ページから「System Manager: Open」ボタンで ONTAP System Manager UI にアクセスできます。System Manager 内で FSA（File System Analytics）、監査ログ設定、Qtree、クォータ、EMS 等の全操作が GUI で可能です。

### アクセスパス

```
NetApp Console (console.netapp.com)
  → Management > Systems
  → FSx for ONTAP カードをクリック（右パネル表示）
  → SERVICES セクション > System Manager: Open
  → System Manager UI (iframe 内で表示)
```

URL: `https://console.netapp.com/system-manager/<file-system-id>`

| 操作 | System Manager (GUI) | CLI/REST API | 検証状態 |
|------|---------------------|--------------|---------|
| ボリューム管理 | ✅ 可能 | ✅ 可能 | ✅ E2E 検証済み |
| **FSA (File System Analytics)** | ✅ 可能 | ✅ 可能 | ✅ GUI 検証済み |
| **Activity Tracking** | ✅ 可能（トグルで有効化） | ✅ 可能 | ✅ GUI 検証済み |
| **FSA Explorer** | ✅ 可能 | ✅ 可能 | ✅ GUI 検証済み |
| **FSA Usage** | ✅ 可能 | ✅ 可能 | ✅ GUI 検証済み |
| **Quota Usage** | ✅ 可能 | ✅ 可能 | ✅ GUI 検証済み |
| 監査ログ有効化 | ✅ 可能 | ✅ 可能 | ✅ REST API で検証済み |
| Qtree 作成 | ✅ 可能 | ✅ 可能 | ✅ REST API で検証済み |
| クォータルール設定 | ✅ 可能 | ✅ 可能 | ✅ REST API で検証済み |
| SMB 共有管理 | ✅ 可能 | ✅ 可能 | — |
| EMS Webhook 設定 | ❌ GUI 未対応 | ✅ CLI のみ | ✅ CLI で検証済み |
| FPolicy 設定 | ❌ GUI 未対応 | ✅ CLI のみ | ✅ CLI で検証済み |

> **重要**: System Manager は Workload Factory UI とは**別のインターフェース**です。Workload Factory UI（`/fsxstorage/`）ではボリューム管理のみ可能ですが、System Manager（`/system-manager/`）では FSA、監査ログ、Qtree、クォータ等の全操作が GUI で可能です。

### 接続方式

| 方式 | コンポーネント | 月額コスト | FSx for ONTAP に必要か |
|------|-------------|-----------|---------------------|
| **Link（推奨）** | AWS Lambda + IAM ロール (CloudFormation) | **< ~$1** | ✅ これで十分 |
| Console Agent | EC2 t3.xlarge | ~$120-150 | ❌ 不要（CVO 等に必要） |

## ドキュメント

- [NetApp ドキュメントへのフィードバック](docs/ja/netapp-doc-feedback.md)

アクセス方法・監査ログ設定・クォータ設定の画面手順は未執筆です。同等の内容は
AWS ネイティブ側の手順としてリポジトリ側に揃っています:

- [前提条件（監査ログ有効化を含む）](../../../docs/ja/prerequisites.md)
- [ONTAP 監査設定](../../../docs/ja/ontap-audit-setup.md)
- [System Manager / DII の機能と AWS ネイティブ代替の対応表](../../../docs/ja/native-alternative-matrix.md)

## スクリーンショット

`screenshots/` ディレクトリに操作手順のスクリーンショットを格納しています。

> ⚠️ スクリーンショットはコミット前に `docs/screenshots/mask_screenshots.py` でマスキング処理を行ってください。

## 検証環境

| 項目 | 値 |
|------|-----|
| ONTAP バージョン | 9.17.1P6 |
| デプロイメントタイプ | SINGLE_AZ_1 |
| 検証 SVM | FPolicySMB (NTFS) |
| 検証日 | 2026-05-28 |

---

## Overview

ONTAP System Manager is the built-in GUI management tool for ONTAP. **System Manager IS accessible for FSx for ONTAP via NetApp Console** — access it through NetApp Console > Systems > SERVICES > "Open" (System Manager).

> ⚠️ **Important**: Unlike on-premises ONTAP, you cannot access System Manager by directly browsing to `https://<management-endpoint-ip>` (returns 404). You MUST go through NetApp Console. The REST API (`/api/`) remains directly accessible.

### Target Users

- Operations staff familiar with Windows File Resource Manager
- Teams that need storage management without CLI expertise
- Users/organizations requesting GUI-based audit log and quota configuration

### Verified Operations

> **May 2026 Verification Result**: System Manager is accessible via NetApp Console > Systems > SERVICES > "Open". All ONTAP management operations including FSA, audit logs, Qtrees, quotas are available in the GUI.

| Operation | System Manager (GUI) | CLI/REST API | Status |
|-----------|---------------------|--------------|--------|
| Volume management | ✅ Available | ✅ Available | ✅ E2E verified |
| **FSA (File System Analytics)** | ✅ Available | ✅ Available | ✅ GUI verified |
| **Activity Tracking** | ✅ Available (toggle to enable) | ✅ Available | ✅ GUI verified |
| **FSA Explorer** | ✅ Available | ✅ Available | ✅ GUI verified |
| **FSA Usage** | ✅ Available | ✅ Available | ✅ GUI verified |
| **Quota Usage** | ✅ Available | ✅ Available | ✅ GUI verified |
| Enable audit logging | ✅ Available | ✅ Available | ✅ Verified via REST API |
| Create Qtree | ✅ Available | ✅ Available | ✅ Verified via REST API |
| Configure quota rules | ✅ Available | ✅ Available | ✅ Verified via REST API |
| SMB share management | ✅ Available | ✅ Available | — |
| EMS Webhook setup | ❌ Not in GUI | ✅ CLI only | ✅ Verified via CLI |
| FPolicy setup | ❌ Not in GUI | ✅ CLI only | ✅ Verified via CLI |

> **Important**: System Manager is a **separate interface** from Workload Factory UI. Workload Factory UI (`/fsxstorage/`) provides volume management only, while System Manager (`/system-manager/`) provides full ONTAP management including FSA, audit logs, Qtrees, and quotas.

### Connection Method

| Method | Component | Monthly Cost | Required for FSx for ONTAP? |
|--------|-----------|-------------|---------------------------|
| **Link (recommended)** | AWS Lambda + IAM role (CloudFormation) | **< ~$1** | ✅ Sufficient |
| Console Agent | EC2 t3.xlarge | ~$120-150 | ❌ Not needed (required for CVO etc.) |

## References

- [NetApp Docs — System Manager](https://docs.netapp.com/us-en/ontap/concept_administration_overview.html)
- [AWS Docs — Managing FSx for ONTAP with ONTAP tools](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)
- [NetApp Docs — Quota Management](https://docs.netapp.com/us-en/ontap/task_quotas_to_limit_resources.html)
