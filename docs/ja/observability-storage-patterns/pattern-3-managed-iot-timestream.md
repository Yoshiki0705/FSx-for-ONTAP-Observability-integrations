# パターン3: マネージド IoT → Timestream

🌐 **日本語**(このページ) | [English](../../en/observability-storage-patterns/pattern-3-managed-iot-timestream.md)

⬅️ [概要へ戻る](README.md)

## 典型的な構成

AWS IoT Core がデバイステレメトリを受信し、IoT ルールが(直接、または Kinesis Data Streams/Firehose 経由で)Amazon Timestream に転送して準リアルタイムクエリを可能にし、任意で長期保存のために S3 にも転送します。Grafana または Amazon Managed Grafana が Timestream にクエリしてダッシュボードを表示し、Lambda が同じストリームに対してリアルタイム異常検知を行うこともあります。

**公開されている出典**: [AWS リファレンスアーキテクチャ: Timestream への IoT 時系列データ取り込みパターン](https://aws.amazon.com/blogs/database/patterns-for-aws-iot-time-series-data-ingestion-with-amazon-timestream/); [AWS スマートホーム IoT ガイダンス](https://docs.aws.amazon.com/solutions/building-smart-home-solutions-on-aws-iot/)は、準リアルタイム監視のための Timestream への転送と、Firehose バッファリングを経由した長期保存のための S3(Iceberg テーブルとして)への転送を明示的に説明しています。

> **サービス提供状況に関する補足**: **Amazon Timestream for LiveAnalytics は 2025-06-20 付で新規顧客のアクセスが終了しています**([出典](https://docs.aws.amazon.com/timestream/latest/developerguide/AmazonTimestreamForLiveAnalytics-availability-change.html))。既存顧客のワークロードは継続しますが、新規に構築する場合は選べません。AWS は新規顧客に対して Amazon Timestream for InfluxDB を代替として案内しています。本パターンのタイトルは慣例的に「Timestream」としていますが、新規構築を検討する読者は Timestream for InfluxDB、または[パターン1](pattern-1-mqtt-tsdb-live-dashboard.md)・[パターン5](pattern-5-embedded-columnar-tsdb.md)で扱う自己管理の時系列 DB を検討してください。

## このパターンが他の4パターンと異なる理由

Amazon IoT Core と Amazon Timestream はフルマネージドの AWS サービスです。ブローカーやデータベースプロセスを動かすコンピュートホストは存在せず、ホットパスのどこにも FSx for ONTAP が接続できるアタッチ済みのブロック/ファイルボリュームがありません。これは他の4パターンのようなプロトコル上の制約ではなく、そもそも統合すべきボリューム自体が存在しないという話です。

## FSx for ONTAP が当てはまる箇所

| FSx for ONTAP パターン | 当てはまるか | 補足 |
|---|:---:|---|
| [パターン A: アーカイブ](README.md#パターン-a-s3-access-points-経由の長期アーカイブ) | ⚠️ 条件付き | パイプラインが*さらに*自己管理のストアにも生データのコピーを書き出している場合(例: IoT ルールの Lambda コンシューマー、Kinesis Firehose の変換 Lambda)のみ当てはまる。[AWS 自身のスマートホームガイダンス](https://docs.aws.amazon.com/solutions/building-smart-home-solutions-on-aws-iot/)のように、パイプラインが既にネイティブに S3 へ転送している場合、その既存の S3 経路に対する FSx for ONTAP の追加価値は小さい |
| パターン B: FlexClone | 該当なし | クローンすべきボリュームが存在しない。Timestream と IoT Core は自身のストレージを内部で管理する |
| パターン C: Snapshot/SnapLock | 該当なし | 同じ理由。このパイプラインのホットパスに保護すべき FSx for ONTAP ボリュームがない |

## パターン固有の補足

このパターンは[概要](README.md)で、本ドキュメントの一般的な推奨事項(「FSx for ONTAP はアーカイブ/クローン/保護の層を追加する」)が、自己管理エクスポートの任意アーカイブを除いて意味のある形で当てはまらない唯一のケースとして明記されています。フルマネージドの IoT→Timestream パイプラインを運用している読者は、FSx for ONTAP にこれ以上の役割を期待すべきではありません。本ドキュメントはパターンの網羅性を実態より均一に見せるために適用範囲を過大に主張しません。

パイプラインが自己管理コンポーネント(例: IoT Core と並行して追加された Kafka や MQTT の層)を含むように発展した場合は、追加されたコンポーネントに応じて[パターン1](pattern-1-mqtt-tsdb-live-dashboard.md)または[パターン4](pattern-4-kafka-otel-collector.md)に基づいて再評価してください。

## 関連プロジェクト: ONTAP Edge-to-Cloud AI

[ontap-edge-to-cloud-ai](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai) は、エッジ/IoT デバイスから AWS の分析・AI サービス(Athena、Glue、SageMaker、Bedrock)へのデータ集約を、FSx for ONTAP をストレージ層として扱うリファレンス実装です。このパターンと直接関係する2点があります。

- **同型の実装がデプロイ可能なコードとして存在します。** 同プロジェクトの [`cloud/iot_ingestion/`](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/cloud/iot_ingestion/template.yaml) は、AWS IoT Core → Lambda という取り込み経路を実装していますが、Timestream ではなく **FSx for ONTAP の S3 Access Point** へ直接書き込みます。これは本パターンの「パターン A(アーカイブ)が条件付きで当てはまる」という記述の具体例であり、IoT Core の Lambda コンシューマーが自己管理ストア(この場合は FSx for ONTAP)にも書き込む構成そのものです。
- **Timestream for LiveAnalytics の新規顧客への提供終了を、同プロジェクトの [Pattern 07: デジタルツイン](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/docs/ja/aws-patterns/07-digital-twin.md) が独立に確認し、新規構築時の時系列 DB 選択肢(Timestream for InfluxDB、ストリーミング+オブジェクトストレージ+Iceberg、列指向 DB)を比較しています。** 本パターンで「時系列ストア」と一般化して呼んでいる箇所は、新規構築であれば同ドキュメントの選択肢を検討してください。

このプロジェクト固有の設計判断(デバイス識別子の検証、S3 Access Point の互換性制約、ローカルデモでの検証手順)は、[ontap-edge-to-cloud-ai の README](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/README.md) と [S3 AP 互換性と制約](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/docs/ja/s3ap-compatibility-matrix.md) を参照してください。本ドキュメントはそれらを重複させません。

## 関連資料

- [概要: FSx for ONTAP によるオブザーバビリティ基盤ストレージの統合](README.md)
- [オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md)
- [ontap-edge-to-cloud-ai](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai) — エッジ/IoT データを FSx for ONTAP に集約する実装。[`cloud/iot_ingestion/`](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/cloud/iot_ingestion/) が本パターンの IoT Core 取り込み経路の実装例
- [Pattern 07: デジタルツイン(ontap-edge-to-cloud-ai)](https://github.com/Yoshiki0705/ontap-edge-to-cloud-ai/blob/main/docs/ja/aws-patterns/07-digital-twin.md) — Timestream for LiveAnalytics の提供終了と、新規構築時の時系列 DB 選択肢の比較
