# アーキテクチャ

🌐 **日本語**（このページ） | [English](../en/architecture.md)

## 全体構成

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        FSx for ONTAP                                    │
├─────────────────┬──────────────────┬────────────────────────────────────┤
│  監査ログ        │  EMS イベント     │  FPolicy ファイル操作通知            │
│  (EVTX/XML)     │  (ARP, Quota等)  │  (create/write/rename/delete)      │
└────────┬────────┴────────┬─────────┴──────────────┬─────────────────────┘
         │                  │                         │
         ▼                  ▼                         ▼
┌─────────────────┐  ┌──────────────┐  ┌──────────────────────────────────┐
│ S3 Access Point │  │ EMS Webhook  │  │ FPolicy TCP:9898                 │
│ + EventBridge   │  │ (HTTPS POST) │  │ (proprietary binary protocol)    │
│   Scheduler     │  │              │  │                                  │
└────────┬────────┘  └──────┬───────┘  └──────────────┬───────────────────┘
         │                  │                          │
         ▼                  ▼                          ▼
┌─────────────────┐  ┌──────────────┐  ┌──────────────────────────────────┐
│ Lambda          │  │ API Gateway  │  │ ECS Fargate                      │
│ (ログパース・配信) │  │ → Lambda     │  │ (FPolicy Server → SQS → Lambda)  │
└────────┬────────┘  └──────┬───────┘  └──────────────┬───────────────────┘
         │                  │                          │
         └──────────────────┼──────────────────────────┘
                            ▼
                 ┌──────────────────────┐
                 │  Observability       │
                 │  Vendor API          │
                 │  (Datadog, Splunk等)  │
                 └──────────────────────┘
```

## 4つのテレメトリパス

本プロジェクトは ONTAP の4つのテレメトリソースに対応します:

### パス 1: ファイルアクセス監査ログ（S3 AP + EventBridge Scheduler）
```
ONTAP 監査ログ → S3 Access Point → EventBridge Scheduler → Lambda → Vendor API
```
- **用途** — コンプライアンス向けファイルアクセス履歴
- **レイテンシ** — ニアリアルタイム（Scheduler 間隔依存、通常5分）
- **形式** — EVTX / XML

### パス 2: 管理監査ログ（Syslog VPC Endpoint → CloudWatch Logs）
```
ONTAP log-forwarding → Syslog (TCP 1514/6514) → VPC Endpoint → CloudWatch Logs
```
- **用途** — 管理アクティビティの記録（CLI/API 操作）
- **レイテンシ** — リアルタイム（秒単位）
- **形式** — RFC 3164 syslog（CloudWatch が自動パース）
- **コンピュート** — なし（フルマネージド、Lambda/EC2 不要）
- **オプション** — CloudWatch Logs → Subscription Filter → Lambda/Firehose → Vendor

### パス 3: EMS イベント（Webhook）
```
ONTAP EMS → Webhook (HTTPS) → API Gateway → Lambda → Vendor API
```
- **用途** — ARP ランサムウェア検知、クォータ超過、HA フェイルオーバー等
- **レイテンシ** — リアルタイム（~30秒）
- **形式** — JSON

### パス 4: FPolicy ファイル操作（ECS Fargate）
```
ONTAP FPolicy → ECS Fargate (TCP:9898) → SQS → Lambda → Vendor API
```
- **用途** — リアルタイムファイル操作監視（create, write, rename, delete）
- **レイテンシ** — リアルタイム（~6-8秒）
- **形式** — FPolicy バイナリプロトコル → JSON 正規化
- **注意** — FPolicy は独自バイナリプロトコルのため Lambda 不可、ECS Fargate が必要

> **本シリーズにおける「サーバーレス」の意味**
>
> サーバー管理や差別化されないコレクター運用を最小化すること — 全てを Lambda に押し込むことではありません。FPolicy は永続的な TCP リスナーが必要なため ECS Fargate（サーバーレスコンテナ）を使用し、イベントのデカップリングには SQS、短時間処理には Lambda を使用します。各 AWS サービスはその運用特性に応じて選択しています。

## ONTAP テレメトリソース選択ガイド

| 要件 | 最適ソース | レイテンシ | コンピュート |
|------|-----------|----------|-----------|
| コンプライアンス向けファイルアクセス履歴 | ファイルアクセス監査ログ | ニアリアルタイム | Lambda |
| 管理アクティビティ監査 | 管理監査ログ (syslog) | リアルタイム（秒） | なし（マネージド） |
| ランサムウェア検知 | ARP via EMS | リアルタイム（webhook） | Lambda |
| イベント駆動ファイル操作詳細 | FPolicy | イベント駆動（TCP） | Fargate + Lambda |
| 運用アラート（クォータ、HA、ボリューム満杯） | EMS | リアルタイム（webhook） | Lambda |
| ベンダーニュートラルパイプライン | OpenTelemetry | 構成依存 | Collector + Lambda |

## コンポーネント詳細

### 1. FSx for ONTAP 監査ログ

FSx for ONTAP の監査ログ機能を有効化し、SVM 内の audit volume に出力します。

- **ログ形式** — EVTX (Windows Event Log) または XML — `-format {evtx|xml}` で設定
- **出力先** — SVM 内の audit volume（`vserver audit create -destination /audit_log`）
- **ログ内容** — ファイルアクセス（SMB/NFS）、認証イベント
- **アクセス方式** — FSx for ONTAP S3 Access Point 経由で S3 API としてアクセス

> **重要**
>
> 監査ログは FSx ボリューム上に保存されます。S3 バケットには書き込まれません。Lambda は FSx for ONTAP S3 Access Point を通じて S3 API でログファイルを読み取ります。

#### 監査ログフォーマット: EVTX vs XML

両フォーマットは**同一のイベントフィールド**を含みます。ONTAP は内部のステージングファイル（中間バイナリ）にイベントを記録した後、設定されたフォーマットに変換して出力するため、情報量に差はありません。

| 観点 | EVTX | XML |
|------|------|-----|
| 含まれる情報 | 同一（EventID, TimeCreated, UserName, ObjectName 等） | 同一 |
| エンコーディング | バイナリ（Windows Event Log 形式） | テキスト（人間可読） |
| パース複雑度 | 高（バイナリ構造解析 or `python-evtx` ライブラリ必要） | 低（標準 XML パーサーで処理可能） |
| 外部ビューア | Microsoft Event Viewer | 任意のテキストエディタ / XML ツール |
| Lambda 互換性 | ⚠️ 簡易パーサー（ヘッダーのみ）or 大きなライブラリ依存 | ✅ stdlib `xml.etree.ElementTree` で完全パース |
| Lambda Layer サイズ影響 | 大（`python-evtx` 使用時） | なし（stdlib のみ） |
| デフォルト | ✅（ONTAP デフォルト） | `-format xml` を明示指定 |

**サーバーレスパイプライン向け推奨**: **XML フォーマット**（`-format xml`）を使用してください。サードパーティ依存なしで Lambda で全フィールドを抽出できます。

```bash
# Create audit config with XML format (recommended for pipeline delivery)
vserver audit create -vserver <svm-name> -destination /audit_log -format xml -rotate-size 200MB

# Create audit config with EVTX format (default, for Windows Event Viewer)
vserver audit create -vserver <svm-name> -destination /audit_log -rotate-size 200MB
```

**抽出されるフィールド（両フォーマット共通）**:

| フィールド | 説明 | 例 |
|-----------|------|-----|
| EventID | Windows セキュリティイベント ID | 4663 (Read/Write Object) |
| TimeCreated | イベント発生日時 (UTC) | 2026-05-31T10:00:00Z |
| Computer | SVM 名 | FPolicySMB |
| SubjectUserName | 操作を実行したユーザー | DOMAIN\username |
| IpAddress | クライアント IP アドレス | 10.0.1.50 |
| ObjectName | ファイル/ディレクトリパス | /share/folder/report.xlsx |
| ObjectType | File または Directory | File |
| HandleID | オブジェクトハンドル | — |
| Keywords | 成功 or 失敗 | Audit Success |

**本プロジェクトのパーサー実装状況**:

| フォーマット | パーサー | 抽出フィールド |
|------------|---------|-------------|
| XML | ✅ 完全実装（`_parse_xml_logs`） | 上記全フィールド |
| JSON | ✅ 完全実装（`_parse_json_logs`） | 全フィールド |
| EVTX | ⚠️ 簡易実装（`_parse_evtx`） | タイムスタンプのみ（EventID = "unknown"） |

> EVTX の本格パースには [`python-evtx`](https://github.com/williballenthin/python-evtx) を Lambda Layer としてパッケージングする方法があります（展開時 ~15 MB）。ただし、ONTAP 監査設定レベルで XML に切り替えることを推奨します — サーバーレスパイプラインには追加依存なしで動作するこちらが最適です。

参考: [AWS Docs — ファイルアクセス監査](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html) | [NetApp Docs — 監査設定の作成](https://docs.netapp.com/us-en/ontap/nas-audit/create-auditing-config-task.html)

> **NFS 監査に関する注意**
>
> NFS ファイルアクセス監査（ONTAP 9.13.1+）では、SMB 監査イベントにはない追加フィールドが含まれる場合があります。上記のフィールドマッピングは SMB アクセスイベント（EventID 4656/4660/4663）に基づいています。NFS 監査を設定する際は、ご利用の ONTAP バージョンで実際のフィールド可用性を確認してください。

### 2. FSx for ONTAP S3 Access Point

FSx for ONTAP ボリュームにアタッチされる S3 Access Point です。

- **目的** — Lambda が NFS/SMB マウントなしで監査ログを読み取るためのアクセス境界
- **特性** — データは FSx ファイルシステム上に残り、S3 API でアクセス可能
- **制約** — S3 Event Notifications / EventBridge 通知は非対応
- **VPC 制約** — Internet-origin S3 AP の場合、VPC 内 Lambda + Gateway Endpoint のみではタイムアウト（NAT Gateway または VPC-origin AP が必要）

### 3. トリガー方式

FSx for ONTAP S3 Access Points は S3 イベント通知をサポートしないため、EventBridge Scheduler による定期起動を使用します。

**EventBridge Scheduler + チェックポイント**
- EventBridge Scheduler が Lambda を定期的に起動（例: 5分間隔）
- Lambda は前回処理済みファイルをチェックポイント（DynamoDB）で管理
- 新しくローテーションされたログファイルのみを処理

### 4. Lambda 関数

監査ログを取得・パースし、各ベンダーの API へ配信します。

- **ランタイム** — Python 3.12
- **処理フロー** — 1. FSx for ONTAP S3 Access Point 経由でログファイル一覧取得
  2. チェックポイントと比較し、未処理ファイルを特定
  3. EVTX/XML パース
  4. ベンダー固有フォーマットへ変換
  5. API エンドポイントへバッチ送信
  6. 失敗時リトライ（exponential backoff）
  7. チェックポイント更新

### 5. 代替パターン: Kinesis Data Firehose

大量ログの場合、Firehose 経由で直接ベンダーへ配信します。

```
FSx for ONTAP S3 AP → Lambda (変換) → Kinesis Data Firehose → ベンダー API
```

- **利点** — 自動バッファリング、リトライ、スケーリング
- **対応ベンダー** — Splunk (HEC), Datadog, New Relic, HTTP エンドポイント全般

### 6. AWS ネイティブ代替案との比較

| 方式 | 最適用途 | トレードオフ |
|------|---------|------------|
| Lambda → ベンダー API 直接 | ベンダー固有マッピング、EVTX/XML パース | カスタムリトライ/バックオフが必要 |
| Kinesis Data Firehose | マネージドバッファリング | 変換の柔軟性に制限 |
| CloudWatch Logs 経由 | AWS ネイティブ運用優先 | 外部ツールへの追加ルーティングが必要 |
| SQS バッファ（パーサーとシッパー間） | 疎結合、バックプレッシャー対応 | コンポーネント数が増加 |
| OpenTelemetry Collector | ベンダーニュートラル標準 | スキーマ/マッピング設計が必要 |
| Security Lake / OCSF | セキュリティ分析、長期保存 | OCSF スキーマへの変換が必要 |

本プロジェクトでは Lambda → ベンダー API 直接方式を採用。理由:
- EVTX/XML パースにフル制御が必要
- ベンダー固有の API セマンティクス（バッチサイズ、認証、リトライ）に対応
- AWS ネイティブロギングを優先する場合、CloudWatch Logs や S3 アーカイブを並列出力として追加可能

## セキュリティ設計

### IAM 最小権限

```yaml
# Lambda execution role
- s3:GetObject (FSx for ONTAP S3 Access Point ARN only)
- s3:ListBucket (FSx for ONTAP S3 Access Point ARN only)
- secretsmanager:GetSecretValue (API Key Secret only)
- dynamodb:GetItem, dynamodb:PutItem (checkpoint table only)
- logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
```

### シークレット管理

- API キーは AWS Secrets Manager に保存
- Lambda 環境変数には ARN のみ設定
- KMS カスタマーマネージドキーで暗号化推奨

### ネットワーク

- FSx for ONTAP S3 Access Point へのアクセスは NAT Gateway 経由（VPC 内配置時）
- **注意** — Internet-origin S3 AP は VPC 内から Gateway Endpoint のみではアクセス不可（NAT Gateway 必要）
- Lambda を VPC 外に配置する場合は問題なくアクセス可能（推奨: 読み取り専用の場合）
- セキュリティグループで最小限のアウトバウンドのみ許可

## 監視・アラート

- **CloudWatch Metrics** — Lambda エラー率、実行時間、スロットリング
- **CloudWatch Alarms** — 配信失敗率閾値超過時に SNS 通知
- **Dead Letter Queue** — 処理失敗イベントを SQS DLQ に退避
- **X-Ray** — 分散トレーシングによるボトルネック特定
