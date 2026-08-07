# Datadog セットアップガイド

🌐 **日本語** (このページ) | [English](../en/setup-guide.md)

## 概要

Amazon FSx for NetApp ONTAP の監査ログを Datadog Logs に配送するサーバーレス統合のセットアップガイドです。

### 動作の仕組み

```
FSx for ONTAP 監査ボリューム
  └─ FSx for ONTAP S3 AP ──┐
                            │  (ListObjectsV2 + GetObject)
   EventBridge Scheduler ───┴─→ Lambda シッパー ──→ Datadog Logs Intake v2
     (5 分ごと)                     │
                                    └─→ SSM Parameter（チェックポイント）
```

FSx for ONTAP S3 Access Point は S3 イベント通知や EventBridge のオブジェクトレベル
イベントをサポートしません。そのためシッパーはスケジュール実行され、監査プレフィックス配下の
オブジェクトを列挙し、最後に処理したキーを SSM Parameter Store のチェックポイントに記録します。
チェックポイントより辞書順で後ろのキーのみを処理するため、日付ベースのプレフィックス
（`YYYY/MM/DD/`）に書き出される監査ログは順序どおり、ローテーションごとに 1 回だけ処理されます。

本ガイドは監査ログ経路を対象としています。他の 2 つのイベントソースはオプションです。

| ソース | レイテンシ | ガイド |
|--------|-----------|--------|
| 監査ログ（本ガイド） | 分単位（ローテーション + スケジュール） | — |
| EMS Webhook | 秒単位 | [ems-fpolicy-setup.md](ems-fpolicy-setup.md) |
| FPolicy ファイルイベント | サブ秒 | [ems-fpolicy-setup.md](ems-fpolicy-setup.md) |

## 前提条件

- FSx for ONTAP ファイルシステムが稼働している AWS アカウント
- Logs 機能が有効な Datadog アカウント
- AWS CLI v2 の設定済み
- SVM で FSx for ONTAP 監査ログが有効化済み
  （`bash shared/scripts/ontap-audit-setup.sh --endpoint <ip> --svm <name> --dry-run`）

### 開始前に収集する値

以降のすべてのステップで少なくとも 1 つを使うため、先に集めておきます。

| 値 | 取得方法 |
|----|---------|
| Datadog API キー | Datadog コンソール → Organization Settings → API Keys |
| Datadog サイト | ログインに使用しているドメイン（例: `ap1.datadoghq.com`） |
| FSx ファイルシステム ID | `aws fsx describe-file-systems --query 'FileSystems[].FileSystemId'` |
| 監査ボリューム ID | `aws fsx describe-volumes --query 'Volumes[].{Id:VolumeId,Name:Name}' --output table` — 1 つのファイルシステムには通常多数のボリュームがある。必要なのは ONTAP が監査ログを書き込むボリューム （`vserver audit show` の `-destination`）で、SVM ルートボリュームではない |
| VPC ID | FSx for ONTAP ファイルシステムが属する VPC |
| AWS アカウント ID | `aws sts get-caller-identity --query Account --output text` |

## Step 1: Datadog API キーの準備

### 1.1 Datadog から API キーを取得

1. Datadog コンソールにログイン
2. **Organization Settings** → **API Keys** に移動
3. **New Key** をクリックして新しい API キーを作成
4. キー名: `fsxn-audit-log-shipper`
5. 生成された API キーをコピー

### 1.2 AWS Secrets Manager に保存

```bash
aws secretsmanager create-secret \
  --name "fsxn-datadog-api-key" \
  --description "Datadog API Key for FSx for ONTAP audit log integration" \
  --secret-string '{"api_key":"YOUR_DATADOG_API_KEY"}' \
  --region ap-northeast-1
```

ハンドラはプレーン文字列と JSON（`{"api_key": ...}` または `{"DD_API_KEY": ...}`）の
両方を受け付けます。返却された ARN を記録してください。AWS が付与する 6 文字のサフィックスが
末尾に付き、CloudFormation に渡すのはこの完全な ARN です。

> `scripts/` 配下のスクリプトはシークレット**名** `fsxn-datadog-api-key` を
> デフォルトとします。別の名前を使う場合は、実行時に `DD_API_KEY_SECRET_ID` も
> 設定してください。

## Step 2: FSx for ONTAP S3 Access Point の作成

これは `fsx` API で作成しボリュームにアタッチする **FSx for ONTAP S3 Access Point** です。
`aws s3control` で作成する標準の S3 Access Point とは別物です。後者は S3 バケットを
指すものであり、FSx ボリュームを公開することはできません。

```bash
aws fsx create-and-attach-s3-access-point \
  --name fsxn-audit-ap \
  --type ONTAP \
  --ontap-configuration 'VolumeId=fsvol-0123456789abcdef0,FileSystemIdentity={Type=UNIX,UnixUser={Name=root}}' \
  --region ap-northeast-1
```

`AVAILABLE` になったことを確認し、ARN を記録します。

```bash
aws fsx describe-s3-access-point-attachments \
  --names fsxn-audit-ap \
  --region ap-northeast-1 \
  --query 'S3AccessPointAttachments[0].{Lifecycle:Lifecycle,Arn:S3AccessPoint.ResourceARN}'
```

### ネットワークオリジンの選択が重要

`--s3-access-point 'VpcConfiguration={VpcId=...}'` を省略すると
**Internet-origin** のアクセスポイントが作成されます。本統合が想定するのはこちらで、
シッパー Lambda は **VPC 外**で動作し、インターネット経路でアクセスポイントに到達します。
最もシンプルかつ低コストな構成です。

`VpcConfiguration` を指定すると VPC-origin となり、Lambda はその VPC 内で動作させる必要が
あります（`VpcEnabled=true`）。**ネットワークオリジンは作成後に変更できません。**

### 既存のアクセスポイントを再利用する場合

アクセスポイントを新規作成したのでない場合、`VpcEnabled` を決める前にオリジンを確認してください。
初回デプロイ失敗の最頻原因です。

```bash
aws s3control get-access-point \
  --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --name fsxn-audit-ap --region ap-northeast-1 \
  --query '{Origin:NetworkOrigin,Vpc:VpcConfiguration.VpcId}'
```

| 結果 | デプロイ時の指定 |
|------|----------------|
| `"Origin": "Internet"` | `VpcEnabled=false`（デフォルト） |
| `"Origin": "VPC"` | `VpcEnabled=true`、報告された VPC 内のサブネット、**かつ** Datadog API への egress |

VPC-origin のアクセスポイントは、アクセスポイントポリシーが存在しなくても、VPC 外からの
リクエストを `AccessDenied ... explicit deny in a resource-based policy` で拒否します。
この文言は IAM の問題を示唆しますが、原因はネットワークオリジンです。

| Lambda の配置 | Internet-origin AP | VPC-origin AP |
|--------------|-------------------|---------------|
| VPC 外（デフォルト） | ✅ 動作する | ❌ 経路なし |
| VPC 内 + Gateway Endpoint のみ | ⚠️ 当環境ではタイムアウト | ✅ 動作する |
| VPC 内 + NAT Gateway | ✅ 動作する | ✅ 動作する |

> **AD 参加 SVM に関する補足**: SVM で CIFS が有効な場合、**すべての** S3 AP データ操作で
> SVM から AD ドメインコントローラに到達できる必要があります。`HeadBucket` は成功するのに
> `ListObjectsV2` が `AccessDenied` になるのは AD DC 到達不能のサインであり、
> IAM やポリシーの問題ではありません。

## Step 3: デプロイ

### 3.1 推奨: デプロイスクリプトを使う

このスクリプトはスタックをデプロイし、**かつ** CloudFormation ではインライン化できない
実際の Lambda コードをアップロードします。特別な理由がなければこちらを使ってください。

```bash
export DATADOG_API_KEY_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX"
export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
export DATADOG_SITE="ap1.datadoghq.com"

bash integrations/datadog/scripts/deploy.sh
```

初回実行は **3〜5 分**かかります。大半は CloudFormation が IAM ロール、Lambda、スケジューラ、
アラームを作成する時間です。スクリプトは各ステップの完了を出力するので、ハングではありません。
変更のないスタックへの再実行は数秒で完了します。

`--all` を付けると EMS と FPolicy のスタックも併せてデプロイします。
環境変数の全一覧は `--help` を参照してください。スタックに触れず
ハンドラだけ再アップロードする場合は `--code-only` を使います。

### 3.2 代替: CloudFormation を手動でデプロイ

```bash
cd integrations/datadog

aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX \
    DatadogSite=ap1.datadoghq.com \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

### 3.3 実際の Lambda コードをアップロード（必須）

**スタックだけでは動作しません。** CloudFormation は数百行のハンドラをインライン化できないため、
`template.yaml` は `NotImplementedError` を投げる placeholder を配置します。
`scripts/deploy.sh` を使った場合は既に完了しています。手動デプロイした場合はここで実施します。

```bash
cd integrations/datadog/lambda
zip function.zip handler.py

aws lambda update-function-code \
  --function-name fsxn-datadog-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-datadog-integration-shipper \
  --region ap-northeast-1
```

`scripts/verify.sh` のチェック 2 は、デプロイ済みコードサイズを見てアップロード忘れを検出します。

### パラメータリファレンス

必須:

| パラメータ | 説明 |
|-----------|------|
| `FsxS3AccessPointArn` | FSx for ONTAP S3 Access Point ARN（監査ボリュームにアタッチ済み） |
| `DatadogApiKeySecretArn` | API キーの Secrets Manager ARN |

任意 — ほとんどのデプロイではデフォルトで動作します:

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `DatadogSite` | `ap1.datadoghq.com` | Datadog サイト。intake エンドポイントを決定（後述） |
| `AuditLogPrefix` | `audit/` | アクセスポイント内でスキャンするキープレフィックス |
| `ScheduleRate` | `rate(5 minutes)` | シッパーが新規ファイルをポーリングする間隔 |
| `MaxKeysPerRun` | `100` | 1 回の実行で処理するファイル数。大量のバックログは複数回に分けて消化 |
| `Environment` | `production` | `env:` タグと `DD_ENV` の値 |
| `EnableGzip` | `false` | ペイロードを gzip 圧縮。有効化前にトラブルシューティングの既知の問題を参照 |
| `LogLevel` | `INFO` | Lambda ログレベル（調査時は `DEBUG`） |
| `LambdaMemorySize` | `256` | MB。大きな EVTX ファイルを処理する場合は増やす |
| `LambdaTimeout` | `300` | 秒。`MaxKeysPerRun` 件を処理する時間を超える必要あり |
| `AlarmNotificationTopicArn` | `''` | エラー/スロットル/DLQ アラームの SNS トピック。**空の場合は誰にも通知されない** |
| `VpcEnabled` | `false` | VPC-origin アクセスポイントの場合のみ `true` |
| `VpcSubnetIds` | `''` | `VpcEnabled=true` の場合は必須 |
| `VpcSecurityGroupIds` | `''` | `VpcEnabled=true` の場合は必須 |

### 後で必要になるスタック出力

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-datadog-integration \
  --region ap-northeast-1 \
  --query 'Stacks[0].Outputs' --output table
```

| 出力 | 用途 |
|------|------|
| `LambdaFunctionName` | `update-function-code` と手動 invoke の対象 |
| `CheckpointParameterName` | `__INIT__` にリセットするとプレフィックス全体を再処理 |
| `DeadLetterQueueUrl` | 未配送バッチの確認 |
| `DashboardName` | パイプライン健全性の CloudWatch ダッシュボード |

### Datadog サイト

| サイト | ドメイン | 用途 | Logs Intake エンドポイント |
|-------|---------|------|--------------------------|
| US1 | `datadoghq.com` | 米国東部（デフォルト） | `http-intake.logs.datadoghq.com` |
| US3 | `us3.datadoghq.com` | 米国（Azure 統合） | `http-intake.logs.us3.datadoghq.com` |
| US5 | `us5.datadoghq.com` | 米国西部 | `http-intake.logs.us5.datadoghq.com` |
| EU1 | `datadoghq.eu` | EU（フランクフルト） | `http-intake.logs.datadoghq.eu` |
| AP1 | `ap1.datadoghq.com` | アジアパシフィック（東京） | `http-intake.logs.ap1.datadoghq.com` |
| AP2 | `ap2.datadoghq.com` | アジアパシフィック（シドニー） | `http-intake.logs.ap2.datadoghq.com` |
| US1-FED | `ddog-gov.com` | 米国政府（FedRAMP） | `http-intake.logs.ddog-gov.com` |

> **リージョン選択ガイド**:
> - APAC（日本、オーストラリア等）: `ap1.datadoghq.com` または `ap2.datadoghq.com`
> - EMEA（欧州、中東、アフリカ）: `datadoghq.eu`
> - AMERICAS（南北アメリカ）: `datadoghq.com`, `us3.datadoghq.com`, `us5.datadoghq.com`
> - 米国政府: `ddog-gov.com`

## Step 4: Datadog 側の設定

### 4.0 最短経路: セットアップスクリプトを実行

`setup-full-observability.sh` は Datadog API 経由でログパイプライン、Facet、モニター、
ログベースメトリクス、Sensitive Data Scanner ルールを作成します。設定するのは
**Datadog 側のみ**で AWS リソースはデプロイしないため、Step 3 の後に実行してください。

```bash
export DD_API_KEY_SECRET_ID="fsxn-datadog-api-key"
export DD_APP_KEY_SECRET_ID="datadog/fsxn-app-key"   # API キーではなく Application キー
export DD_SITE="ap1.datadoghq.com"

bash integrations/datadog/scripts/setup-full-observability.sh
```

個別に実行することもできます: `setup-facets.sh`、`create-alerts.sh`、`create-dashboard.sh`。

以降の節では同じ設定を手動で行う手順を説明します。

### 4.1 すでに構造化されているもの（Grok パーサは不要）

Lambda は各イベントをネストした `attributes` オブジェクトを持つ JSON として送信し、
Datadog がネイティブに使用するトップレベルの `date`、`ddsource`、`service`、`hostname`、
`ddtags` を設定します。Datadog は JSON を自動的にパースするため:

- **Grok パーサは不要です。** フィールドは `@attributes.user`、`@attributes.operation` などとして
  即座に利用できます。
- **Date Remapper も不要です。** ハンドラがトップレベルの `date` を設定しています。

プロセッサを追加する前に、テストログを 1 件送って属性が現れることを確認してください。

```bash
bash integrations/datadog/scripts/verify.sh
```

### 4.2 ログパイプラインの作成

パイプラインが必要なのは以下のプロセッサ（ステータスマッピングと PII 処理）のためだけです。
プロセッサを省略する場合でも、後から追加する場所として作成しておきます。

1. Datadog コンソール → **Logs** → **Configuration** → **Pipelines**
2. **New Pipeline** をクリック
3. 設定:
   - **Filter**: `source:fsxn`
   - **Name**: `FSx for ONTAP Audit Logs`

#### Category Processor → status

ONTAP は `Success` / `Failure` を出力しますが、これは Datadog のログステータスではありません。
まずマッピングしてから remap します。

| 設定項目 | 値 |
|---------|-----|
| プロセッサ | Category Processor |
| ターゲット属性 | `status_category` |
| カテゴリ `error` | `@attributes.result:Failure` |
| カテゴリ `info` | `@attributes.result:Success` |

続いてステータス属性を `status_category` とした **Status Remapper** を追加します。

`@attributes.result` を Status Remapper に直接指定すると、アクセス失敗が `info` のままになり、
まさにアラートを上げたい対象を見落とします。

#### Sensitive Data Scanner（推奨）

監査ログにはユーザー名とファイルの完全パスが含まれます。フィールドごとの分類と
有効化すべきルールは
[data-classification.md](../../../../docs/ja/data-classification.md) を参照してください。

### 4.3 Facet の作成

`setup-facets.sh` が以下を作成します。手動で追加する場合は、Log Explorer でログを開き、
フィールドをクリックして **Create facet** を選択します。

| Facet | パス | 型 |
|-------|------|-----|
| SVM | `@attributes.svm` | String |
| User | `@attributes.user` | String |
| Operation | `@attributes.operation` | String |
| Client IP | `@attributes.client_ip` | String |
| Result | `@attributes.result` | String |
| File Path | `@attributes.path` | String |
| Event Type | `@attributes.event_type` | String |

属性と ONTAP フィールドの完全な対応は [field-mapping.md](field-mapping.md) にあります。

### 4.4 モニターの作成

`create-alerts.sh` は 3 つのモニターを作成します。

| モニター | 条件 |
|---------|------|
| Failed Access Spike | 5 分間で 10 件を超える失敗 |
| Pipeline Health | Lambda エラーを検知 |
| DLQ Alert | Dead Letter Queue へのメッセージ出現 |

本番投入前に、失敗アクセスモニターからサービスアカウント（`svc-*`）を除外してください。
除外しないと通常の自動処理で通知が発生します。

### 4.5 ダッシュボードの作成（推奨）

`create-dashboard.sh` が作成します。手動で構築する場合は以下を配置します。

- **ログ量トレンド**: `source:fsxn` のログ件数の時系列
- **操作の内訳**: `@attributes.operation` のトップリスト
- **ユーザーアクティビティ**: `@attributes.user` のトップリスト
- **エラー率**: `@attributes.result:Failure` の割合

フォレンジック用ダッシュボードも `dashboards/forensics-dashboard.json` にあります。
**Dashboards** → **New** → **Import dashboard JSON** からインポートしてください。

## Step 5: 検証

### 5.1 検証スクリプトを実行

パイプラインのどこが壊れているかを最短で特定できます。スタック状態、placeholder コードが
置き換わっているか、シッパーの invoke、intake API への合成ログ送信をチェックします。

```bash
export DD_API_KEY_SECRET_ID="fsxn-datadog-api-key"
export DD_SITE="ap1.datadoghq.com"

bash integrations/datadog/scripts/verify.sh
```

終了コード 0 は 4 つのチェックすべての成功を意味します。各チェックは独立に結果を報告するため、
intake チェックが成功して invoke が失敗した場合は「認証情報は正しいが Lambda が
アクセスポイントを読めていない」と判断できます。

### 5.2 実際の監査イベントを生成

スクリプトの `new_files=0` は、チェックポイントが最新の場合の正常な結果です。
新しい監査ログファイルを生成するには、監査対象ボリュームで操作を行います。

```bash
# FSx for ONTAP ボリュームをマウントしたクライアントで実行
# 未マウントの場合は先に:
#   sudo mkdir -p /mnt/fsxn
#   sudo mount -t nfs <svm-nfs-endpoint>:/vol_data /mnt/fsxn
echo "test" > /mnt/fsxn/test-audit.txt
cat /mnt/fsxn/test-audit.txt
rm /mnt/fsxn/test-audit.txt
```

ONTAP は監査レコードをステージングファイルに書き込み、監査ボリュームへのローテーションは
定期的にしか行いません。Datadog で見えるまでには**ローテーション間隔 + スケジュール間隔**が
必要で、秒単位ではありません。強制的にローテーションするには:

```bash
# ONTAP CLI から
vserver audit rotate -vserver <svm-name>
```

### 5.3 任意: ONTAP のローテーションを待たずに検証する

ONTAP は監査レコードをステージングファイルからローテーションするまで読み取り可能にしないため、
アクセスの少ないシステムでは時間がかかります。パイプライン全体を即座に検証するには、
代表的な監査ファイルを 1 件アクセスポイント経由で自分で書き込みます。
シッパーからは実際のローテーションと区別がつきません。

```bash
AP="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
TS=$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))")

cat > /tmp/audit_check.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<Events>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>4663</EventID>
    <TimeCreated SystemTime="${TS}"/>
    <Computer>svm-prod-01</Computer>
  </System>
  <EventData>
    <Data Name="SubjectUserName">CORP\\pipeline-check</Data>
    <Data Name="ObjectName">/vol/data/pipeline-check.txt</Data>
    <Data Name="ObjectType">ReadData</Data>
    <Data Name="IpAddress">198.51.100.1</Data>
    <Data Name="Keywords">Audit Success</Data>
  </EventData>
</Event>
</Events>
EOF

aws s3api put-object --bucket "$AP" \
  --key "audit/$(date -u +%Y/%m/%d)/pipeline_check.xml" \
  --body /tmp/audit_check.xml

bash integrations/datadog/scripts/verify.sh
```

`new_files=1, shipped=1` が期待値です。`xmlns` に注目してください。ONTAP は Windows Event Log
XML スキーマで書き出すため、これを含まないフィクスチャは実際の入力を再現していません。

直後に再実行すると `new_files=0` になります。チェックポイントが前進した証拠であり、
ログが重複しないことの確認にもなります。

> **クリーンアップに関する補足**: この方法で書き込んだファイルはアクセスポイントではなく
> FSx ボリューム上に存在します。マウントしたクライアントから削除するか、アクセスポイントを
> 削除する**前に**アクセスポイント経由で削除してください。アクセスポイントを削除すると
> ファイルは残ります。

### 5.4 Datadog で確認

1. Datadog コンソール → **Logs** → **Search**
2. 検索クエリ: `source:fsxn`
3. ログが届いただけでなく、`@attributes.user`、`@attributes.operation`、`@attributes.path` に
   値が入っていることを確認

### 5.5 CloudWatch で Lambda を確認

```bash
aws logs tail /aws/lambda/fsxn-datadog-integration-shipper --follow
```

正常なスケジュール実行では `Scheduler mode: prefix=..., checkpoint=...` に続いて
`No new audit log files to process` または `Found N new audit log file(s) to process` が
出力されます。

## トラブルシューティング

### Lambda が NotImplementedError を投げる

placeholder コードがまだデプロイされています。[Step 3.3](#33-実際の-lambda-コードをアップロード必須)
を参照するか、以下を実行してください。

```bash
bash integrations/datadog/scripts/deploy.sh --code-only
```

### AccessDenied "explicit deny in a resource-based policy" が出る

アクセスポイントが VPC-origin で Lambda が VPC 外にある（またはその逆）状態です。
文言に反してアクセスポイントポリシーは関係ありません。
[Step 2](#既存のアクセスポイントを再利用する場合) のコマンドでオリジンを確認し、
`VpcEnabled` を合わせてください。オリジンは変更できないため、別のオリジンが必要な場合は
アクセスポイントを新規作成します。

### Datadog にログが表示されない

1. **Lambda エラーを確認**:
   ```bash
   aws logs filter-log-events \
     --log-group-name /aws/lambda/fsxn-datadog-integration-shipper \
     --filter-pattern "ERROR"
   ```

2. **DLQ メッセージを確認**:
   ```bash
   aws sqs get-queue-attributes \
     --queue-url https://sqs.ap-northeast-1.amazonaws.com/123456789012/fsxn-datadog-integration-dlq \
     --attribute-names ApproximateNumberOfMessages
   ```

3. **API キーを確認**: Secrets Manager の値が正しいことを確認

4. **タイムスタンプを確認**: Datadog は `date` が **18 時間**より過去のログを
   サイレントに破棄します。古い監査ボリュームをバックフィルすると成功したように見えて
   （HTTP 202）何もインデックスされません。テストには現在のデータを使ってください。

5. **Datadog サイトを確認**: Lambda 環境変数 `DATADOG_SITE` が正しいサイトを指していることを
   確認します。日本リージョンでは `ap1.datadoghq.com` を使用します。

### チェックポイントが進まない

シッパーは配送できなかった最初のファイルで意図的に停止します。そこを飛ばして進めると
その監査レコードが恒久的に失われるためです。Lambda ログから失敗したキーを特定し、
根本原因を解消してください。次回実行は同じ位置から再試行します。

```bash
# 現在のチェックポイントを読む
aws ssm get-parameter \
  --name /fsxn-datadog/fsxn-datadog-integration/last-processed-key \
  --region ap-northeast-1 --query 'Parameter.Value' --output text
```

プレフィックス全体を再処理する場合（Datadog 上の既存ログと**重複します**）:

```bash
aws ssm put-parameter \
  --name /fsxn-datadog/fsxn-datadog-integration/last-processed-key \
  --value '__INIT__' --type String --overwrite --region ap-northeast-1
```

[checkpoint-stale.md](../../../../docs/ja/runbooks/checkpoint-stale.md) も参照してください。

### 同じログが Datadog に 2 回現れる

チェックポイントが永続化されていません。Lambda ログで `Failed to update checkpoint` を
確認してください（通常は `ssm:PutParameter` 権限の不足）。あわせて環境変数
`CHECKPOINT_PARAM_NAME` が設定されていることを確認します。

### 大量のバックログの消化が遅い

`MaxKeysPerRun`（デフォルト 100）は、バックログがファイル処理中に Lambda タイムアウトを
使い切らないよう 1 回の実行を制限しています。より速く消化するには `LambdaTimeout` と
併せて引き上げるか、一時的に `ScheduleRate` を短くしてください。

### VPC 制限付き S3 Access Point を使う場合

S3 Access Point が VPC-origin の場合、Lambda を同じ VPC 内で動作させる必要があります。
CloudFormation デプロイ時に以下のパラメータを追加します。

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX \
    DatadogSite=ap1.datadoghq.com \
    VpcEnabled=true \
    VpcSubnetIds=subnet-0123456789abcdef0,subnet-0123456789abcdef1 \
    VpcSecurityGroupIds=sg-0123456789abcdef0 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

> **注意**: VPC 内の Lambda が Datadog API に到達するには NAT Gateway または VPC エンドポイントが
> 必要です。Secrets Manager 用の VPC エンドポイント
> （`com.amazonaws.ap-northeast-1.secretsmanager`）も必要で、これが無いと自身の API キー
> 読み取りでタイムアウトします。

先に `bash shared/scripts/preflight-check.sh --vpc-id vpc-xxx` を実行してください。
VPC エンドポイントの競合は本プロジェクトで最も多いデプロイ失敗要因です。

### gzip 圧縮の既知の問題

現時点で、Datadog AP1 サイト（`ap1.datadoghq.com`）では gzip 圧縮したペイロードが
正しくインデックスされない場合があります。ペイロードは受理される（HTTP 202）のに検索に
現れないため、まったく別の問題のように見えます。そのため `EnableGzip` はデフォルト `false` です。
大容量環境でペイロードサイズが問題になる場合は、gzip サポート状況について Datadog サポートに
問い合わせてください。

### レート制限エラー

Datadog API のレート制限に達した場合、Lambda は指数バックオフで自動リトライします。
頻発する場合は Lambda の同時実行数を制限してください。

```bash
aws lambda put-function-concurrency \
  --function-name fsxn-datadog-integration-shipper \
  --reserved-concurrent-executions 5
```

## クリーンアップ

```bash
bash integrations/datadog/scripts/cleanup.sh          # スタックのみ
bash integrations/datadog/scripts/cleanup.sh --all    # + シークレット、Layer、S3 テストデータ
```

FSx for ONTAP S3 Access Point と監査バケットは共有リソースのため**削除されません**。
不要になった場合は個別にデタッチしてください。

```bash
aws fsx detach-and-delete-s3-access-point \
  --name fsxn-audit-ap --region ap-northeast-1
```

## 関連ドキュメント

- [フィールドマッピング](field-mapping.md) — Datadog 属性 ↔ ONTAP フィールド対応
- [EMS / FPolicy セットアップ](ems-fpolicy-setup.md) — リアルタイムイベントソース
- [ログアーカイブ設定](log-archive-setup.md) — S3 への長期保管
- [Snapshot 修復](snapshot-remediation-setup.md) — 自動封じ込め
- [本番チェックリスト](production-checklist.md) — 本番投入前の確認
- [SPL / CQL 比較](spl-cql-comparison.md) — クエリ変換リファレンス
- [パイプライン SLO](../../../../docs/ja/pipeline-slo.md) — SLO 定義と Go/No-Go 基準
- [DLQ リプレイ Runbook](../../../../docs/ja/runbooks/dlq-replay.md)
