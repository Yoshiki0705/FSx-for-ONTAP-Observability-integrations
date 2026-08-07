# FSx for ONTAP 監査ログ → CrowdStrike Falcon LogScale 連携 デプロイガイド

---

## 概要

Amazon FSx for ONTAP の監査ログ（ファイルアクセスログ）を CrowdStrike Falcon LogScale に自動配信するサーバーレスパイプラインです。

**目的**: ファイルアクセスログを LogScale に蓄積し、未使用データの特定・過剰権限のチェックを行う

---

## パフォーマンス特性

### 本ソリューションの特性

| 特性 | 内容 |
|------|------|
| **ログ収集間隔** | 1〜5分 (EventBridge Scheduler) |
| **リアルタイムパス** | **サブ秒** (FPolicy → ECS → SQS → Lambda) |
| **パース速度** | **178,000 events/sec** (実測) |
| **検索応答** | **サブ秒** (LogScale index-free アーキテクチャ) |
| **スケーラビリティ** | Lambda 自動スケール (水平拡張) |
| **運用負荷** | ゼロ運用 (サーバーレス、パッチ不要) |
| **マルチベンダー** | 10 ベンダー対応 (ロックイン回避) |
| **IaC** | CloudFormation テンプレートで即時再構築 |
| **PII リダクション** | OTel Collector / Grafana Alloy で配信前マスキング |
| **コスト** | **~$1/月** (AWS 側) + Observability プラットフォーム |

### 従来のバッチ型ログ収集からの改善点

| 観点 | 従来型の一般的な課題 | 本ソリューション |
|------|-------------------|----------------|
| 収集遅延 | 15〜60分のバッチ間隔 | 1〜5分 (Audit) / サブ秒 (FPolicy) |
| サーバー管理 | 専用サーバーのパッチ、監視 | 完全サーバーレス |
| スケール | 手動増強 | 自動 (Lambda 同時実行) |
| ベンダーロックイン | 独自フォーマット | OTel 標準 (OTLP) |
| 検索速度 | DB クエリ (秒〜十秒) | index-free (サブ秒) |
| セキュリティ統合 | スタンドアロン | XDR/SIEM と統合可 |

### E2E レイテンシ (ファイル操作 → LogScale で検索可能)

| 構成パターン | レイテンシ | ユースケース |
|------------|-----------|------------|
| **Audit ポーリング (5分)** | 1〜5分 | 日次レポート、容量分析、権限チェック |
| **Audit ポーリング (1分)** | 10秒〜1分 | 準リアルタイム監視 |
| **FPolicy (TCP ストリーム)** | **<1秒** | セキュリティアラート、不正検知 |

### 処理能力の実測値

```
テスト環境: Lambda ARM64 (Graviton) 256MB, Python 3.12
パーサーバージョン: v1.1.0

5 events (2KB XML):     0.045ms / 111,000 events/sec
500 events (135KB XML): 2.8ms   / 178,000 events/sec
推定 5,000 events (1.3MB): ~28ms / ~178,000 events/sec

→ 1日 100万イベント (1000ユーザー想定) を 約6秒 で処理完了
```

### サーバーレスパイプラインのメリット

1. **レイテンシ**: ポーリング 1-5分 / FPolicy サブ秒
2. **サーバー不要**: EC2/オンプレサーバーの管理・パッチが不要
3. **検索高速化**: LogScale の index-free アーキテクチャで大量ログも秒単位検索
4. **統合 XDR**: Falcon EDR データと監査ログを同一プラットフォームで相関分析
5. **コスト効率**: Lambda ~$1/月 (従量課金、アイドル時ゼロ)

### ONTAP 推奨設定 (レイテンシ最小化)

```bash
# 監査ログのローテーション設定 (小ファイル・高頻度回転 → レイテンシ改善)
vserver audit modify -vserver <svm-name> \
  -rotate-size 5MB \
  -rotate-schedule-minute 5
# rotate-size: 5MB で回転 (デフォルト 100MB は大きすぎる)
# rotate-schedule-minute: 5分ごとに強制回転
```

> **ポイント**: ログファイルが回転するまで Lambda は読み取れません。`rotate-size` を小さく、`rotate-schedule-minute` を短くすることで E2E レイテンシが改善します。

---

## アーキテクチャ

```
FSx for ONTAP (SVM 監査ログ有効化)
    │
    │ S3 Access Point 経由で XML ログを読み取り
    ▼
EventBridge Scheduler (1〜5分毎) → Lambda (Python 3.12, ARM64)
    │
    │ XML パース (178K events/sec) → 正規化 → HEC 形式に変換
    ▼
CrowdStrike Falcon LogScale (/api/v1/ingest/hec, gzip圧縮)
    │
    │ 検索・分析・アラート (サブ秒応答)
    ▼
ダッシュボード (未使用データ・権限チェック)
```

### リアルタイムパス (FPolicy) — サブ秒レイテンシ

```
FSx for ONTAP → FPolicy (TCP:9898) → ECS Fargate → SQS → Lambda → LogScale
                                                            │
                                                    レイテンシ: <1秒
```

> FPolicy は ONTAP がファイル操作時にリアルタイムで TCP 通知を送信する仕組みです。ポーリング不要でサブ秒の配信が可能です。セキュリティアラート（不正削除検知等）に推奨。

---

## 前提条件

| 項目 | 必要なもの |
|------|-----------|
| FSx for ONTAP | 監査ログ有効化済み (`vserver audit create -format xml`) |
| S3 Access Point | FSx for ONTAP S3 AP 作成済み |
| CrowdStrike | Falcon Insight XDR (10GB/日 無料インジェスト枠) + Ingest Token |
| AWS | CloudFormation デプロイ権限、Secrets Manager 書き込み権限 |

---

## デプロイ手順 (詳細)

### Step 1: LogScale Ingest Token の取得

Falcon コンソールにログインし、Ingest Token を発行します。

1. `falcon.us-2.crowdstrike.com` にログイン
2. Menu → **Next-Gen SIEM** → **Log management** → **Data settings**
3. 対象リポジトリを選択（または新規作成 `fsxn_audit`）
4. **Ingest tokens** → **+ Add Token**
5. 名前: `fsxn-audit-log-shipper`
6. トークン値をコピー（例: `a1b2c3d4-e5f6-...`）

---

### Step 2: AWS Secrets Manager にトークンを保存

```bash
aws secretsmanager create-secret \
  --name "crowdstrike/fsxn-ingest-token" \
  --secret-string '{"ingest_token":"<Step 1でコピーしたトークン>"}' \
  --region ap-northeast-1
```

出力される ARN をメモします:
```
arn:aws:secretsmanager:ap-northeast-1:<account-id>:secret:crowdstrike/fsxn-ingest-token-XXXXXX
```

---

### Step 3: CloudFormation スタックのデプロイ

```bash
# リポジトリをクローン
git clone https://github.com/Yoshiki0705/fsxn-observability-integrations.git
cd fsxn-observability-integrations

# CloudFormation テンプレートをデプロイ
aws cloudformation deploy \
  --template-file integrations/crowdstrike/template.yaml \
  --stack-name fsxn-crowdstrike-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:<region>:<account-id>:accesspoint/<ap-name> \
    LogScaleIngestTokenSecretArn=arn:aws:secretsmanager:<region>:<account-id>:secret:crowdstrike/fsxn-ingest-token-XXXXXX \
    LogScaleUrl=https://cloud.us.humio.com \
    ScheduleInterval="rate(5 minutes)" \
    LogLevel=INFO \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

**パラメータ説明**:

| パラメータ | 値 | 説明 |
|-----------|---|------|
| `FsxS3AccessPointArn` | `arn:aws:s3:ap-northeast-1:<account>:accesspoint/<name>` | FSx for ONTAP S3 AP の ARN |
| `LogScaleIngestTokenSecretArn` | `arn:aws:secretsmanager:...` | Step 2 で作成した Secret ARN |
| `LogScaleUrl` | `https://cloud.us.humio.com` | LogScale のベース URL |
| `ScheduleInterval` | `rate(5 minutes)` | ポーリング間隔 |
| `LogLevel` | `INFO` | Lambda ログレベル |

**作成されるリソース**:
- Lambda 関数 (Python 3.12, 256MB, 5分タイムアウト)
- EventBridge Scheduler (5分毎に Lambda を起動)
- Dead Letter Queue (KMS 暗号化、14日保持)
- CloudWatch Alarm (Lambda エラー率 + DLQ 深度)
- IAM Role (最小権限: S3 AP 読み取り、Secrets Manager 読み取り、ログ書き込み)

---

### Step 4: Lambda 関数コードのアップロード

CloudFormation テンプレートはプレースホルダー Lambda を作成します。実際のハンドラーコードをアップロードします:

```bash
# Lambda 関数のパッケージ作成
cd integrations/crowdstrike/lambda
zip function.zip handler.py

# Lambda 関数コードを更新
aws lambda update-function-code \
  --function-name fsxn-crowdstrike-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

# プロジェクトルートに戻る
cd ../../..
```

---

### Step 5: 動作確認

```bash
# Lambda を手動実行してテスト
aws lambda invoke \
  --function-name fsxn-crowdstrike-integration-shipper \
  --payload '{"source": "manual-test"}' \
  --region ap-northeast-1 \
  output.json

# レスポンス確認
cat output.json

# CloudWatch ログ確認 (直近5分)
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-crowdstrike-integration-shipper \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))") \
  --region ap-northeast-1 \
  --query 'events[*].message' \
  --output text | head -20

# DLQ が空であることを確認 (0 = 正常)
aws sqs get-queue-attributes \
  --queue-url $(aws cloudformation describe-stacks \
    --stack-name fsxn-crowdstrike-integration \
    --query 'Stacks[0].Outputs[?OutputKey==`DeadLetterQueueUrl`].OutputValue' \
    --output text --region ap-northeast-1) \
  --attribute-names ApproximateNumberOfMessages \
  --region ap-northeast-1
```

---

### Step 6: LogScale で検索確認

Falcon コンソール → Next-Gen SIEM → Log management → Advanced event search:

```
#repo=fsxn_audit
| head(10)
```

フィールドが正しく展開されていることを確認:

```
#repo=fsxn_audit
| table([@timestamp, event_type, user, path, result, client_ip, svm])
```

---

## パーサーの挙動

Lambda 内のパーサーは ONTAP XML 監査ログを以下のように処理します:

**入力** (ONTAP が出力する XML):
```xml
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>4663</EventID>
    <TimeCreated SystemTime="2026-06-06T01:55:00.000000Z"/>
    <Computer>ProductionSVM</Computer>
  </System>
  <EventData>
    <Data Name="SubjectUserName">CORP\user-finance-01</Data>
    <Data Name="ObjectName">/share/finance/quarterly-reports/Q2-2026.xlsx</Data>
    <Data Name="ObjectType">File</Data>
    <Data Name="IpAddress">10.0.1.50</Data>
    <Data Name="HandleID">0x000001A4</Data>
    <Data Name="Keywords">Audit Success</Data>
  </EventData>
</Event>
```

**処理フロー**:
1. `<Data Name="key">value</Data>` → `{"key": "value"}` に展開
2. `<TimeCreated SystemTime="...">` → `timestamp` に正規化
3. 共通スキーマに変換 (user, path, event_type, result, client_ip, svm, operation)
4. 元の全フィールドを `raw` に保持 → LogScale で詳細検索可能

> **名前空間に関する補足**: ONTAP は Windows Event Log XML スキーマで出力するため、
> 上記のとおり全要素に `xmlns="http://schemas.microsoft.com/win/2004/08/events/event"` が
> 付きます。XML パーサーはタグ照合の前に名前空間を除去するため、名前空間付き・無しの
> どちらの文書でも 1 つの `<Event>` が 1 レコードになります。
> 動作確認用のフィクスチャを作る場合は、名前空間を含めてください。含めないと実際の
> 入力を再現できません。

**出力** (LogScale に送信される JSON):
```json
{
  "event": {
    "timestamp": "2026-06-06T01:55:00.000000Z",
    "event_type": "4663",
    "user": "CORP\\user-finance-01",
    "path": "/share/finance/quarterly-reports/Q2-2026.xlsx",
    "result": "Audit Success",
    "client_ip": "10.0.1.50",
    "svm": "ProductionSVM",
    "operation": "File"
  },
  "source": "fsxn-ontap",
  "sourcetype": "fsxn:audit:xml",
  "index": "fsxn_audit"
}
```

---

## 未使用データ・権限チェック用クエリ

### 未使用ファイルの特定 (30日以上アクセスなし)

```
#repo=fsxn_audit
| groupBy(path, function=[max(@timestamp, as=last_access)])
| last_access < now() - 30d
| sort(last_access, order=asc)
```

### 過剰権限の検出 (失敗アクセスが多いユーザー)

```
#repo=fsxn_audit result="Audit Failure"
| groupBy([user, path], function=count())
| _count > 5
| sort(_count, order=desc)
```

### コントラクター・外部ユーザーのアクセス監視

```
#repo=fsxn_audit user=*contractor* OR user=*ext-*
| groupBy([user, path, operation], function=count())
| sort(_count, order=desc)
```

### ファイル削除操作の時系列

```
#repo=fsxn_audit event_type="4660"
| timechart(span=1h, function=count())
```

### 部門別アクセス集計

```
#repo=fsxn_audit
| path=/share/finance/* OR path=/share/hr/* OR path=/share/engineering/*
| replace(path, regex="^/share/([^/]+)/.*", with="$1", as=department)
| groupBy([department, user], function=count())
```

---

## EventID リファレンス

| EventID | 意味 | 活用例 |
|---------|------|-------|
| 4663 | オブジェクトへのアクセス試行 | 最終アクセス日の追跡 |
| 4656 | オブジェクトへのハンドル要求 | ファイルオープン操作の検知 |
| 4660 | オブジェクトの削除 | 不正削除の検知・アラート |
| 4658 | ハンドルのクローズ | セッション完了の追跡 |

---

## コスト

| コンポーネント | 月額概算 |
|--------------|---------|
| Lambda (5分毎、平均1秒実行) | ~$0.50 |
| Secrets Manager | ~$0.40 |
| EventBridge Scheduler | ~$0.00 |
| S3 AP 読み取り | ~$0.05 |
| **AWS 側合計** | **~$1/月** |

LogScale 側: Falcon Insight XDR の 10GB/day 無料インジェスト枠内であれば追加費用なし

---

## トラブルシューティング

| 症状 | 確認ポイント |
|------|------------|
| Lambda がタイムアウト | S3 AP への接続確認 (VPC 外配置を推奨) |
| HEC 401 エラー | Secrets Manager の Ingest Token が正しいか確認 |
| HEC 403 エラー | LogScale リポジトリの権限・トークン割り当て確認 |
| DLQ にメッセージ | CloudWatch ログでエラー詳細を確認 |
| イベントが 0 件 | ONTAP 監査ログが出力されているか確認 (`vserver audit show`) |

---

## リソース

- [GitHub リポジトリ](https://github.com/Yoshiki0705/fsxn-observability-integrations/tree/main/integrations/crowdstrike)
- [LogScale HEC API ドキュメント](https://library.humio.com/logscale-api/log-shippers-hec.html)
- [CrowdStrike Developer Center - Data Ingestion](https://developer.crowdstrike.com/ngsiem/data-ingestion/)
- [ONTAP 監査ログ設定ガイド](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html)


---

## PoC デモシナリオ: Before/After 計測

### デモ手順

**Step 1: ファイル操作の実行**
```bash
# テスト用ファイルを SMB 経由で作成 (タイムスタンプ記録)
echo "test data $(date)" > /mnt/fsxn-share/demo/test-$(date +%s).txt
echo "操作時刻: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

**Step 2: LogScale で検索 (本ソリューション)**
```
#repo=fsxn_audit path=*demo*
| sort(@timestamp, order=desc)
| head(1)
| formatTime(field=@timestamp, format="%Y-%m-%d %H:%M:%S", as=ingested_at)
```

**Step 3: レイテンシ計測**
```
E2E レイテンシ = LogScale の @timestamp - ファイル操作時刻
```

### 期待される結果

| 計測項目 | 従来バッチ型 | 本ソリューション |
|---------|---------------|----------------|
| ファイル操作→検索可能 | 15〜60分 | **1〜5分** (Audit) / **<1秒** (FPolicy) |
| 1日分ログの検索速度 | 5〜30秒 | **<1秒** |
| 100万イベントの取り込み | 数十分 | **約6秒** |

### PoC 成功基準

- [ ] ファイル操作から 5 分以内に LogScale で検索可能
- [ ] 1日分 (推定 50MB) のログを 1 秒以内にパース完了
- [ ] LogScale での検索応答が 1 秒以内
- [ ] DLQ にメッセージなし (処理エラー 0)
- [ ] Lambda エラー率 0%

---

## レイテンシ要件別の推奨構成

| レイテンシ要件 | 構成 | AWS 月額コスト | 備考 |
|-------------|------|-------------|------|
| **5分以内で十分** | Audit + Scheduler (5分) | ~$1 | 最もシンプル。容量分析・権限チェック向き |
| **1分以内** | Audit + Scheduler (1分) | ~$3 | Lambda 実行回数増。準リアルタイム監視 |
| **サブ秒** | FPolicy + ECS Fargate + SQS | ~$50 | Fargate 常時稼働。セキュリティアラート向き |
| **サブ秒 + 監査** | FPolicy + Audit 併用 | ~$52 | リアルタイム検知 + 完全性保証の両立 |

---

## 想定ログ量と Lambda 処理能力

| ユーザー数 | 推定日次ログ量 | 推定イベント数/日 | Lambda 処理時間/日 |
|-----------|-------------|----------------|------------------|
| 100 | ~50MB | ~100K | ~0.6秒 |
| 500 | ~250MB | ~500K | ~3秒 |
| 1,000 | ~500MB | ~1M | ~6秒 |
| 5,000 | ~2.5GB | ~5M | ~28秒 |
| 10,000 | ~5GB | ~10M | ~56秒 |

> Lambda 5分タイムアウト (300秒) で処理可能な最大量: 約50Mイベント/回 (~25GB XML)。10,000ユーザーまでの環境は余裕を持って対応可能。

---

## Windows Security Event との互換性

ONTAP 監査ログは Windows Security Event Log と同じ EventID 体系を使用しています。既存のログ分析ツールで使用していた EventID がそのまま使えます:

| EventID | 意味 | 説明 | LogScale クエリ |
|---------|------|------|----------------|
| 4663 | ファイルアクセス | オブジェクトへのアクセス試行 | `event_type="4663"` |
| 4656 | ハンドル要求 | ファイルオープン操作 | `event_type="4656"` |
| 4660 | ファイル削除 | オブジェクト削除 | `event_type="4660"` |
| 4658 | ハンドルクローズ | セッション完了 | `event_type="4658"` |

> 既存ツールで使用していた EventID ベースのフィルタリングルールは、そのまま LogScale のクエリに移植できます。
