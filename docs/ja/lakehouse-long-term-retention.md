# FSx for ONTAP 監査ログの Lakehouse 長期保管パターン

🌐 **日本語** (このページ) | [English](../en/lakehouse-long-term-retention.md)

## TL;DR

Observability ベンダーは通常、FSx for ONTAP の監査ログを数週間〜数ヶ月保持します。コンプライアンス要件で複数年の保管が必要な場合、あるいはログ検索ではなく複数年分のデータを横断する SQL 結合が必要な場合、このガイドは同じ監査ログストリームを Apache Parquet に変換し、通常の Amazon S3 バケットに保管し、Amazon Athena や Snowflake で SQL クエリ可能にする第二の経路を追加します。これは本プロジェクトのベンダーパイプラインの代替ではなく、長期保管・SQL 分析を補完するものです。

**E2E 検証済み**（2026年7月19日、ap-northeast-1）: 合成監査ログ 500 件 → Kinesis Data Firehose（JSON → Parquet 変換）→ S3（Snappy 圧縮 Parquet、日付パーティション）→ Glue Data Catalog（Partition Projection、クローラー不要）→ Amazon Athena。`SELECT COUNT(*)` で正確に 500 件、`GROUP BY operation, result` 集計クエリの結果が入力データの分布と一致。クエリ実行はスキャン量 556 バイト、実行時間 417ms。

## なぜ第二の経路が必要か

本プロジェクトの9ベンダー統合（Datadog、Splunk、Elastic など）は**検索とアラート**のために構築されています — 今この瞬間に問題のログ行を見つけ、数秒以内にアラートを発火させることです。**複数年にわたる SQL 分析**のためには構築されていません。「過去3年間の四半期ごとにSVM別で失敗した削除操作は何件か」という問いは異なる種類の問いであり、多くの Observability プラットフォームの保持期間（標準ティアで30〜90日）とGBあたりの取り込み課金は、この規模の問いには適していません。

| 要件 | Observability ベンダー（本プロジェクトの9統合） | Lakehouse 長期保管（本ガイド） |
|---|---|---|
| 直近1時間の特定エラーを検索 | ✅ 最適 | ⚠️ 動作するが、リアルタイム検索UX向けではない |
| 異常検知から数秒以内にアラート | ✅ 最適 | ❌ リアルタイムアラート向けではない |
| 複数年のコンプライアンス保管 | ⚠️ 可能だが、規模が大きくなるとコストが高くなりやすい | ✅ 最適（S3ストレージ階層は規模が大きいほどコスト効率が良い） |
| 複数年分データへのアドホックSQL結合 | ❌ 多くのプラットフォームは苦手 | ✅ 最適（AthenaやSnowflakeはSQLネイティブ） |
| BIダッシュボード・レポートツール連携 | ⚠️ ベンダー依存 | ✅ 最適（標準SQL/JDBC/ODBC） |

これはベンダー対ベンダーの優劣判断ではなく、問われている質問の性質に応じて選択するものです。多くの本番環境では、同じソースデータから両方の経路を並行運用します。

## アーキテクチャ

```
FSx for ONTAP（監査ログ有効化済み SVM）
        │
        ▼
S3（標準バケット、監査ログJSON — ベンダーパイプラインと同じソース）
        │
        ▼
Kinesis Data Firehose
  • 入力: OpenX JSON デシリアライザー
  • 出力: Parquet（Snappy圧縮）、DataFormatConversionConfiguration 経由
  • バッファ: 64 MB / 300秒（Parquet変換有効時、64 MBがFirehoseの最小強制値）
        │
        ▼
S3（保管バケット）
  audit-logs/year=YYYY/month=MM/day=DD/*.parquet
        │
        ├──────────────────────────┐
        ▼                          ▼
  AWS Glue Data Catalog      Snowflake External Stage
  （Partition Projection、    （Storage Integration、
   クローラー不要）            2段階 IAM トラスト）
        │                          │
        ▼                          ▼
   Amazon Athena              Snowflake External Table
   （従量課金 SQL）            （SQL + Snowflake ガバナンス機能）
```

本ガイドは本プロジェクトのベンダーパイプラインが使う FSx for ONTAP S3 Access Point パターンには触れません。Firehose ストリームは、既に監査ログJSONを受信している**標準 S3 バケット**（ベンダー Lambda が読み取っているのと同じソース）から読み取ります。これは意図的なスコープ境界です — 本ガイドの制約を、本プロジェクトの他の場所で文書化されている FSx for ONTAP S3 AP 固有の制約（S3イベント通知非対応、AD DC 到達性要件など）から独立させています。これらの制約はここには当てはまりません。Firehose ストリームとその後段の S3 イベント通知（下記 Snowflake セクションの Snowpipe 自動取り込みで使用）は、それらをネイティブにサポートするバケットに対して動作するためです。

## Glue テーブルスキーマ

本プロジェクトのベンダー Lambda ハンドラー（`integrations/otel-collector/lambda/handler.py` の `FIELD_MAPPING`、および `integrations/otel-collector/tests/test_data/sample_audit_logs.json`）と同じフィールド名を、一貫性のためそのまま使用しています。

| カラム | 型 | ソースフィールド | 補足 |
|---|---|---|---|
| `timestamp` | string | `Timestamp` | ISO 8601、文字列として保持（必要に応じてクエリ側でキャスト） |
| `eventid` | string | `EventID` | ONTAP 監査イベントID |
| `svmname` | string | `SVMName` | Storage Virtual Machine 名 |
| `username` | string | `UserName` | 匿名・システム操作では空の場合あり |
| `clientip` | string | `ClientIP` | 空の場合あり |
| `operation` | string | `Operation` | 例: `ReadData`、`WriteData`、`Delete` |
| `objectname` | string | `ObjectName` | ファイルパス |
| `result` | string | `Result` | `Success`、`Failure`、`Access Denied` |
| `year` / `month` / `day` | string（パーティション） | Firehose 配信タイムスタンプから導出 | Partition Projection — クローラー不要 |

> **データ分類に関する補足**: `username`・`clientip`・`objectname` は、組織のファイル命名規則やユーザーディレクトリ構成によっては個人情報を含む可能性があります。この Athena/Snowflake テーブルへの広範なクエリアクセスを許可する前に、[データ分類ガイド](data-classification.md) の取り扱いパターンを参照してください。

## パイプラインのデプロイ

```bash
aws cloudformation deploy \
  --template-file integrations/lakehouse-retention/template.yaml \
  --stack-name fsxn-lakehouse-retention \
  --parameter-overrides \
    RetentionBucketName=<globally-unique-bucket-name> \
    AthenaResultsBucketName=<globally-unique-results-bucket-name> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

### 準備が必要なもの

- [ ] S3バケット・Glueデータベース/テーブル・Kinesis Data Firehose 配信ストリーム・IAMロール・Athena ワークグループを作成できる権限を持つAWSアカウント
- [ ] グローバルに一意なS3バケット名2つ（保管用バケット、Athenaクエリ結果用バケット）
- [ ] アカウントで **AWS Lake Formation** が有効な場合（`aws lakeformation get-data-lake-settings` で確認）: 下記の Lake Formation の落とし穴に備えてください — このテンプレートで最も頻出するデプロイ失敗要因です
- [ ] （任意、Snowflakeパスのみ）`ACCOUNTADMIN` 権限を持つ Snowflake アカウント（Standard エディション以上、トライアルアカウントでも可）

### デプロイ時間の見積もり

| 作業 | 所要時間の目安 |
|---|---|
| CloudFormation スタックデプロイ | 約2〜3分 |
| 最初のParquetファイルがS3に出現するまで | 最初のレコード送信後、`BufferIntervalSeconds`（デフォルト300秒）経過時、または64MBバッファが満杯になった時点のいずれか早い方 |
| Snowflake Storage Integration の2段階トラスト設定 | 約5〜10分（Integration作成 → `DESCRIBE INTEGRATION` → SnowflakeのアカウントID/External IDでIAMロール再デプロイ → 再確認） |

### パラメーター完全リファレンス

**`template.yaml`**（`fsxn-lakehouse-retention` スタック）:

| パラメーター | 必須 | デフォルト | 許可される値 | 説明 |
|---|---|---|---|---|
| `RetentionBucketName` | ✅ | — | 有効なS3バケット名（3〜63文字、小文字/数字/ハイフン/ピリオド） | Parquet変換済み監査ログ用の、グローバルに一意な新規バケット。既存であってはならない。 |
| `AthenaResultsBucketName` | ✅ | — | 有効なS3バケット名 | Athenaクエリ結果用の、グローバルに一意な新規バケット。既存であってはならない。 |
| `GlueDatabaseName` | ❌ | `fsxn_audit_lakehouse` | 有効なGlueデータベース名 | 既存データベースとの衝突回避、または環境の名前空間分けに変更。 |
| `GlueTableName` | ❌ | `audit_logs` | 有効なGlueテーブル名 | — |
| `BufferSizeMBs` | ❌ | `64` | `64`〜`128` | Parquet変換有効時、64 MBがFirehoseの最小強制値。大きくするほどParquetファイルが少数・大サイズになり（バイトあたりのクエリ効率が向上）、配信レイテンシとのトレードオフ。 |
| `BufferIntervalSeconds` | ❌ | `300` | `60`〜`900` | Athena/Snowflakeでより新しいデータが必要な場合は小さくする。ファイル数増加・サイズ縮小とのトレードオフ。 |
| `AthenaWorkgroupName` | ❌ | `fsxn-lakehouse-retention` | このアカウント/リージョンで一意なワークグループ名 | — |
| `AthenaScanLimitGB` | ❌ | `10` | `1`/`5`/`10`/`20`/`50`/`100`/`200`/`500` | クエリごとのスキャン上限（AthenaはスキャンTBあたり課金）。CloudFormationにはGBをバイトに変換する算術組み込み関数がないため、固定の値セットに限定（テンプレート内の `AthenaScanLimitBytes` マッピングを参照）。数年分のフルテーブルスキャンなど、正当に多くスキャンする本番クエリでは値を上げる。 |

**`snowflake-role.yaml`**（`fsxn-lakehouse-retention-snowflake` スタック、任意）:

| パラメーター | 必須 | デフォルト | 許可される値 | 説明 |
|---|---|---|---|---|
| `RetentionBucketName` | ✅ | — | `template.yaml` が作成したバケット名と一致する必要あり | Snowflakeに読み取りアクセスを許可する既存の保管用バケット。 |
| `SnowflakeAccountId` | ❌（Phase 1） / ✅（Phase 2） | `''`（空） | 空、または正確に12桁の数字 | Phase 1では空のままにする（自アカウントのプレースホルダートラスト）。Phase 2では `DESCRIBE INTEGRATION` の `STORAGE_AWS_IAM_USER_ARN` から12桁のアカウントID部分を設定。 |
| `SnowflakeExternalId` | ❌（Phase 1） / ✅（Phase 2） | `'snowflake-placeholder'` | 任意の文字列 | Phase 1: デフォルトのままにする（自アカウントトラストではこの値はチェックされない）。Phase 2: `DESCRIBE INTEGRATION` の `STORAGE_AWS_EXTERNAL_ID` の値を、末尾の `=` を含めてそのままコピーして設定。 |

上記の各パラメーターには、テンプレート自体にもインラインの説明と `ParameterLabels` エントリがあり、CloudFormationコンソールのパラメーター入力フォームで確認できます。ここの表はクイックリファレンスであり、唯一の情報源ではありません。

### 検証時に判明した注意点: Lake Formation

アカウントで Lake Formation が有効な場合（他のGlueテーブルで一度でも Lake Formation を使ったことがあるアカウントでは一般的）、Firehose 配信ロールの IAM ポリシーで `glue:GetTable` を許可しているだけでは**不十分**です。Firehose の `DataFormatConversionConfiguration` は同じプリンシパルに対する Lake Formation 権限も別途要求し、なければ配信ストリームの作成が以下のエラーで失敗します。

```
Access was denied when calling Glue. Please ensure that the role specified in the
data format conversion configuration has the necessary permissions. Insufficient
Lake Formation permission(s): Required Describe on audit_logs
```

これは Firehose や Glue のドキュメントからは分かりにくい落とし穴です。IAM 権限と Lake Formation 権限は独立して加算的に評価されるため、片方だけでは、両方に依存するリソースを実際に作成しようとするまで問題が見えません。本テンプレートには既に必要な `AWS::LakeFormation::PrincipalPermissions` リソース（データベースに対する `DESCRIBE`、テーブルに対する `DESCRIBE`/`SELECT`/`ALTER`/`INSERT`、Firehose ロールへの付与）が含まれており、明示的な `DependsOn` 順序付けにより、これらの権限が存在するまで配信ストリームが作成されないようにしています。本テンプレートを自身のユースケース向けにフォークする場合、これらのリソースは残してください — 削除すると Lake Formation 有効アカックでのみサイレントにパイプラインが壊れ、Lake Formation 無効なテスト環境では見逃しやすく、本番環境で予期せず遭遇することになります。

### 検証時に判明したその他2つのCloudFormationの落とし穴

- `AWS::Glue::Table` には明示的な `DependsOn: GlueDatabase` が必要です。これがないと、CloudFormation はデータベースが存在する前にテーブル作成を試みる場合があり（`Database <name> not found`）、`DatabaseName: !Ref GlueDatabaseName`（データベースリソース自体への `!GetAtt`/`!Ref` ではなく単純な文字列参照）からは暗黙の依存関係が推論されないためです。
- `AWS::KinesisFirehose::DeliveryStream` の `ExtendedS3DestinationConfiguration` にある `CloudWatchLoggingOptions.LogStreamName` は `!Ref FirehoseLogStream`（単純な文字列）である必要があり、`!GetAtt FirehoseLogStream.LogStreamName` は使えません。`!GetAtt` を使うと `cfn-lint` の `E1010` 型不一致エラーが発生します。このプロパティは `String` 型を期待しており、`AWS::Logs::LogStream` リソースへの `!Ref` は（多くの他のリソースタイプでは `!Ref` がARNやIDを返すのとは異なり）既にログストリーム名に解決されるためです。

## Athena でのクエリ

```sql
-- Total record count for a specific day
SELECT COUNT(*) AS total_records
FROM fsxn_audit_lakehouse.audit_logs
WHERE year = '2026' AND month = '07' AND day = '19';

-- Operation/result distribution (partition pruning via year/month/day)
SELECT operation, result, COUNT(*) AS cnt
FROM fsxn_audit_lakehouse.audit_logs
WHERE year = '2026' AND month = '07' AND day = '19'
GROUP BY operation, result
ORDER BY cnt DESC;

-- Failed/denied operations by SVM, across a date range
SELECT svmname, operation, COUNT(*) AS cnt
FROM fsxn_audit_lakehouse.audit_logs
WHERE year = '2026' AND month = '07'
  AND result IN ('Failure', 'Access Denied')
GROUP BY svmname, operation
ORDER BY cnt DESC;
```

**検証済みクエリ性能**（500件のテストデータセット、単一Parquetファイル、Snappy圧縮後2,970バイト）: `DataScannedInBytes=556`、`EngineExecutionTimeInMillis=417`。この規模では、これらの数値は本番規模のベンチマークとしては意味を持ちません — パイプラインが正しく動作することを確認するものであり、大量データ時の速度を示すものではありません。Partition Projection により、上記クエリで Athena は `year=2026/month=07/day=19` プレフィックスのみをスキャンし、バケット全体を列挙しません。パーティション数が数千に増える（複数年保管）ほど、この効果は重要になります。

## Snowflake でのクエリ（External Table）

Snowflake 対応は、[fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations) の Snowflake 統合で既に確立済みの2段階 Storage Integration トラストパターンを再利用しています — FSx for ONTAP S3 Access Point 向けではなく、通常のS3バケット向けに適応しています。

```bash
# Phase 1: deploy the IAM role with a placeholder (own-account) trust policy
aws cloudformation deploy \
  --template-file integrations/lakehouse-retention/snowflake-role.yaml \
  --stack-name fsxn-lakehouse-retention-snowflake \
  --parameter-overrides RetentionBucketName=<your-retention-bucket> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <your-region>
```

```sql
-- In Snowflake (see integrations/lakehouse-retention/sql/01_storage_integration_and_stage.sql
-- for the full script)
CREATE OR REPLACE STORAGE INTEGRATION fsxn_lakehouse_retention_integration
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = '<IAMRoleArn output from snowflake-role.yaml>'
  STORAGE_ALLOWED_LOCATIONS = ('s3://<your-retention-bucket>/audit-logs/');

DESCRIBE INTEGRATION fsxn_lakehouse_retention_integration;
-- Copy STORAGE_AWS_IAM_USER_ARN and STORAGE_AWS_EXTERNAL_ID, then redeploy
-- snowflake-role.yaml with SnowflakeAccountId/SnowflakeExternalId set to
-- those values (Phase 2 trust).

CREATE OR REPLACE STAGE audit_logs_stage
  STORAGE_INTEGRATION = fsxn_lakehouse_retention_integration
  URL = 's3://<your-retention-bucket>/audit-logs/'
  FILE_FORMAT = (TYPE = 'PARQUET');

LIST @audit_logs_stage;

CREATE OR REPLACE EXTERNAL TABLE audit_logs_ext (
    "timestamp"  VARCHAR AS (value:"timestamp"::VARCHAR),
    eventid      VARCHAR AS (value:eventid::VARCHAR),
    svmname      VARCHAR AS (value:svmname::VARCHAR),
    username     VARCHAR AS (value:username::VARCHAR),
    clientip     VARCHAR AS (value:clientip::VARCHAR),
    operation    VARCHAR AS (value:operation::VARCHAR),
    objectname   VARCHAR AS (value:objectname::VARCHAR),
    result       VARCHAR AS (value:result::VARCHAR)
)
  LOCATION = @audit_logs_stage
  FILE_FORMAT = (TYPE = 'PARQUET')
  AUTO_REFRESH = FALSE;

ALTER EXTERNAL TABLE audit_logs_ext REFRESH;

SELECT COUNT(*) AS total_records FROM audit_logs_ext;
```

> **本セクションの検証状況: E2E検証済み**（2026年7月20日）。上記Firehoseパイプラインが生成した同じ500件のParquetデータセットを使い、Storage Integrationの2段階トラスト設定が成功し、`LIST @audit_logs_stage` が実際のParquetファイルをS3から返し、`SELECT COUNT(*) FROM audit_logs_ext` は正確に500件を返しました — Athenaの結果と完全に一致します。同じExternal Tableに対する `GROUP BY operation, result` クエリも、上記Athena検証と同じOperation×Result分布に一致する15行を返しました。

### FSx for ONTAP S3 AP 版 Snowflake パスとの構成上の違い

本パイプラインのデータは FSx for ONTAP S3 Access Point ではなく**通常のS3バケット**に着地するため、S3イベント通知によってトリガーされる実際の Snowpipe 自動取り込みが直接動作すると予想されます。`fsxn-lakehouse-integrations` プロジェクトの Snowflake 統合は、まさにこの理由（FSx for ONTAP S3 AP は S3イベント通知非対応）で FSx for ONTAP S3 AP に対して自動取り込みを使えず、FPolicy + Lambda + SNS + Snowpipe REST API、またはスケジュール実行の `COPY INTO` にフォールバックする必要がありました。本ガイドのアーキテクチャは、保管先の標準S3バケットがS3イベント通知をネイティブにサポートするため、この制約を取り除いています（本検証では Snowpipe 自動取り込み自体は実施しておらず、上記External Tableパスを検証しました。ただし自動取り込みが依存するS3イベント通知機能自体は、FSx for ONTAP S3 AP とは異なり通常のS3バケットの標準機能です）。

## 検証済みデプロイパス

検証時（2026年7月19日/20日、ap-northeast-1）に、2つのデプロイパスをE2Eで実行しました。いずれもクリーンなAWSアカウント状態（既存スタックなし）から開始し、`snowflake-role.yaml` は `template.yaml` のバケットにのみ依存するため（逆方向の依存はなし）、どちらを先にデプロイしても問題ありません。

**パスA — Athenaのみ**（Snowflakeなし）:
1. `bash integrations/lakehouse-retention/scripts/preflight-check.sh --region <対象リージョン>` を実行 — AWS認証情報を確認し、アカウントでLake Formationが有効かどうかを報告します。
2. `aws cloudformation deploy --template-file integrations/lakehouse-retention/template.yaml ...`（[パイプラインのデプロイ](#パイプラインのデプロイ) 参照）。
3. ベンダーパイプラインが既に書き込んでいる同じS3バケットに監査ログJSONレコードを送信する（あるいはスモークテストとして、`DeliveryStreamName` 出力にあるFirehose配信ストリームに直接 `PutRecord`/`PutRecordBatch` する）。
4. 最初のParquetファイルが出現するまで `BufferIntervalSeconds` 分待ち、`AthenaWorkgroupName` 出力を使ってAthenaでクエリする。

**パスB — Athena + Snowflake**:
1. 上記パスAの手順1〜4（先にAthenaの結果が正しいことを確認する — これによりFirehose/Glue側の問題とSnowflake固有の問題を分離できる）。
2. `snowflake-role.yaml` のPhase 1（自アカウントのプレースホルダートラスト）をデプロイ。
3. Snowflakeで `sql/01_storage_integration_and_stage.sql` のPhase 1ステートメントを実行し、`DESCRIBE INTEGRATION` を実行。
4. `DESCRIBE INTEGRATION` の出力から `SnowflakeAccountId`/`SnowflakeExternalId` を設定して `snowflake-role.yaml` のPhase 2を再デプロイ。
5. `sql/01_storage_integration_and_stage.sql` の残りのステートメント（`CREATE STAGE`、`LIST`、`CREATE EXTERNAL TABLE`、`REFRESH`）を実行。
6. 同じ元のParquetデータに対して、AthenaとSnowflake External Tableの `SELECT COUNT(*)` を比較する — 両方が同じS3オブジェクトを読み取るため、完全に一致するはずです。

## Day 2 運用

- **新しいパーティションがクエリ可能か確認**: Partition Projectionでは、新しい `year=/month=/day=` パーティションに対してGlueクローラーの実行や `MSCK REPAIR TABLE` は不要です — 対応するS3プレフィックスに1件でもParquetオブジェクトがあれば即座にAthenaでクエリ可能になります。これを暗黙に信頼せず、定期的に確認してください（例: `SELECT COUNT(*) WHERE year='<現在の年>' AND month='<現在の月>'`）。
- **Firehose配信の健全性を監視**: `FirehoseLogGroup`（`/aws/kinesisfirehose/<スタック名>`）で `DataFormatConversionConfiguration` の失敗を確認してください — Glueテーブルのカラム定義と一致しない不正な形式のソースJSONレコードは、サイレントに失敗するのではなく `errors/` S3プレフィックス（`ErrorOutputPrefix`）に配置されます。このプレフィックスを定期的に確認してください。エラー件数の増加は通常、上流の監査ログ形式のスキーマドリフトを示します。
- **Athenaスキャン上限を定期的に見直す**: データセットが数年分に増えるにつれ、正当なクエリが `AthenaScanLimitGB` の `BytesScannedCutoffPerQuery` 制限に達し始めた場合（Athenaのスキャン上限エラーでクエリが失敗）、`AthenaScanLimitGB` を次に許可された値に上げて再デプロイしてください — これは想定される運用上の調整であり、計画不足を示すものではありません。
- **ライフサイクル移行が実際に発生しているか確認**: 保管バケットのライフサイクルポリシーは、90日でS3標準低頻度アクセスへ、365日でS3 Glacier Instant Retrievalへオブジェクトを移行します。`aws s3api list-objects-v2 --bucket <RetentionBucketName> --query "Contents[].StorageClass"` を定期的に（例: 四半期ごとに）確認し、古いパーティションが実際に移行しているかを確認してください。ライフサイクルポリシーの設定ミスは、そうしないと予想外に高いストレージ料金としてしか表面化しません。
- **Snowflake External Tableの鮮度**（利用する場合）: 本ガイドの例では `AUTO_REFRESH = FALSE` のため、新しく到着したParquetファイルを取り込むには External Table のメタデータを手動で更新（`ALTER EXTERNAL TABLE audit_logs_ext REFRESH;`）するか、スケジュール実行する（Snowflake Task、または同じステートメントを呼ぶ外部スケジューラー）必要があります。これは、本ガイドの最初の段階でSnowpipe自動取り込みのインフラに依存しないための意図的な選択です。自動取り込みの選択肢については [FSx for ONTAP S3 AP 版 Snowflake パスとの構成上の違い](#fsx-for-ontap-s3-ap-版-snowflake-パスとの構成上の違い) を参照してください。
- **定期的なアクセスレビュー**: このパイプラインは機密性を持ちうるフィールド（`username`・`clientip`・`objectname`）をこのテーブルへのAthena/Snowflakeクエリアクセス権を持つ全員に公開するため、そのアクセス権を持つ人を組織のデータ分類ポリシーに照らして定期的にレビューしてください（[データ分類ガイド](data-classification.md) 参照）。

## ロールバックとクリーンアップ

クリーンアップの順序は重要です — 順序を誤ると、回避可能なCloudFormationの `DELETE_FAILED` 状態が発生します。

```bash
# 1. Delete the Athena workgroup FIRST if any queries were ever run against it.
#    AWS::Athena::WorkGroup deletion through CloudFormation fails with query
#    history still attached; --recursive-delete-option is a CLI-only flag,
#    not available as a CloudFormation stack-level setting, so this step
#    must be run manually before stack deletion.
aws athena delete-work-group \
  --work-group <AthenaWorkgroupName> \
  --recursive-delete-option \
  --region <your-region>

# 2. Empty both S3 buckets. RetentionBucket has versioning enabled, so a
#    plain `aws s3 rm --recursive` deletes current objects but leaves old
#    versions and delete markers behind — CloudFormation stack deletion of a
#    non-empty (even if "logically empty") versioned bucket fails.
aws s3api list-object-versions --bucket <RetentionBucketName> --output json | \
  python3 -c "
import json, sys
d = json.load(sys.stdin)
objs = [{'Key': v['Key'], 'VersionId': v['VersionId']}
        for v in d.get('Versions', []) + d.get('DeleteMarkers', [])]
print(json.dumps({'Objects': objs}))
" > /tmp/lakehouse-versions.json
aws s3api delete-objects --bucket <RetentionBucketName> --delete file:///tmp/lakehouse-versions.json
rm -f /tmp/lakehouse-versions.json

aws s3 rm s3://<AthenaResultsBucketName> --recursive --region <your-region>

# 3. Delete the Snowflake role stack (if deployed) and the main stack. Order
#    between these two does not matter -- snowflake-role.yaml has no
#    CloudFormation-level dependency on template.yaml (it only references
#    the bucket NAME as a parameter string, not a cross-stack !ImportValue),
#    so either can be deleted first or independently of the other.
aws cloudformation delete-stack --stack-name fsxn-lakehouse-retention-snowflake --region <your-region>
aws cloudformation delete-stack --stack-name fsxn-lakehouse-retention --region <your-region>

# 4. (Snowflake side, if used) Drop the Snowflake-side objects independently
#    -- they are not managed by CloudFormation and will not be cleaned up by
#    deleting the AWS stacks above.
#    In Snowflake: DROP EXTERNAL TABLE audit_logs_ext; DROP STAGE audit_logs_stage;
#    DROP STORAGE INTEGRATION fsxn_lakehouse_retention_integration;
```

上記の手順を実行してもスタック削除が失敗する場合（`DELETE_FAILED` 状態が続く場合）、再試行の前にCloudFormationコンソールの「ステータスの理由」列に記載されている具体的なリソースを確認してください。根本原因（オブジェクトが残っているS3バケット、アウトオブバンドで個別に取り消されたLake Formation権限など）に対処せずに `delete-stack` を再試行しても、同じエラーで再度失敗します。

## コスト比較

| コンポーネント | 月額コストの主な要因 | 補足 |
|---|---|---|
| S3ストレージ（保管バケット） | 標準S3料金、90日でS3標準低頻度アクセスへ、365日でS3 Glacier Instant Retrieval へ移行（本テンプレートのライフサイクルポリシー） | Parquet + Snappy圧縮により、生のJSONに比べて保存バイト数が大幅に削減される（参考: 500件のテストで生成されたParquetファイルは2,970バイト、同等の生JSONはおおよそ25倍のサイズ） |
| Kinesis Data Firehose | 取り込みGBあたり + フォーマット変換GBあたりの課金 | クエリ量に関わらず、配信された監査ログデータ量に対して課金 |
| Athena | スキャンTBあたりの課金（本稿執筆時点で$5/TB） | Partition Projection + Parquet列形式によりクエリごとのスキャンバイト数を最小化。本テンプレートのAthenaワークグループに設定した10GBのクエリ上限が、暴走クエリのコストを制限する |
| Snowflake（利用する場合） | Snowflakeコンピュートクレジット（ウェアハウス） + `COPY INTO` を使う場合はSnowflake側の追加ストレージ | External Table はデータをS3に残すため重複ストレージコストを回避。`COPY INTO` はストレージが重複するが、Snowflakeネイティブの性能機能（クラスタリング、Time Travel）が使える |

**同じデータをObservabilityベンダーで複数年保持する場合との比較**: 多くのベンダープラットフォームは「取り込みGB×保持日数」で課金され、保持期間が長くなるほど複雑に積み上がります。S3ストレージコストは同様の形で保持期間と複雑に積み上がるわけではなく（ライフサイクル移行によりさらに時間経過で削減される）、月単位ではなく年単位のコンプライアンス要件では、このコスト構造の違いが決定要因になることが多いです。一方、秒単位のレイテンシでアラートが必要な本当のユースケースでは、ベンダープラットフォームが依然として適切な選択です。本Lakehouse経路はそのレイテンシ向けには設計されていません。

## 関連ドキュメント

- [integrations/lakehouse-retention/README.md](../../integrations/lakehouse-retention/README.md) — クイックスタートの参照先、デプロイ時間の見積もり、事前チェックスクリプト
- [ベンダー比較](vendor-comparison.md)
- [データ分類ガイド](data-classification.md)
- [パイプライン SLO 定義](pipeline-slo.md)
- [Lakehouse モニタリングパターン](lakehouse-monitoring-patterns.md) — FSx for ONTAP + Lakehouse 統合の運用メトリクス（別の関心事: 監査データそのもののクエリではなく、パイプラインの健全性の監視）
- [fsxn-lakehouse-integrations](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations) — 本ガイドの Snowflake パターンと Athena/Glue IAM 規約の元になった姉妹プロジェクト
