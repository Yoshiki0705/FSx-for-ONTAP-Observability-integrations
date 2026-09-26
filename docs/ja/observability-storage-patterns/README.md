# FSx for ONTAP によるオブザーバビリティ基盤ストレージの統合

🌐 **日本語**（このページ） | [English](../../en/observability-storage-patterns/README.md)

## エグゼクティブサマリ

リアルタイムテレメトリパイプライン — 時系列 DB とライブダッシュボードに流し込む MQTT/IoT ブローカー、Prometheus 型のスクレイプ・アラートスタック、マネージド IoT-to-Timestream パイプライン、Kafka/OTel ベースのストリーミングスタック — は、いずれもホット取り込み経路を持ちます。そして、基盤となる各ツールの公式ドキュメントはいずれも(1つの文書化された例外を除いて — [パターン4](pattern-4-kafka-otel-collector.md)参照)、そのホット取り込み経路がネットワークファイル共有ではなくローカルブロックストレージを要求すると明記しています。FSx for ONTAP はこれらのアーキテクチャの大半においてこのホットパスを置き換えません。FSx for ONTAP が追加できるのは**ホットパスを取り囲む共有ストレージ層**です。複数チームがファイルコピーなしに同時参照できる長期アーカイブ、開発/テスト用の本番テレメトリの瞬時ゼロコピークローン、事後フォレンジック用のイミュータブルなスナップショットです。本ドキュメントは、検証済みの事実と、既存の AWS/NetApp 機能を新しい文脈に当てはめた推論を明確に分離し、未検証の推論(hypothesis)を実測と混同しないよう明示します。

**本ドキュメントは特定企業・特定個人の実装を評価するものではありません。** 以下または各パターンファイルで引用する実例はいずれも公開文書化されたパターンの例示であり、本ドキュメントで示すストレージ統合の選択肢は、同種の構成のパイプラインを運用する読者向けの一般化した提案です。引用した実装の批評や推奨ではありません。

## このドキュメント群の構成

この主題は 1 ファイルでは扱いきれない規模になりました。パイプラインパターンごとの詳細を近くに配置し、公開事例(AWS 以外の ONTAP 事例を含む)を AWS 固有の推論に混ぜずに独立して置くため、以下のように分割しています。

| ファイル | 内容 |
|---|---|
| **README.md**(このページ) | パターン横断の発見、3つの FSx for ONTAP パターン(一度だけ定義)、除外範囲と理由、iSCSI 推論、選択フローチャート、FAQ |
| [パターン1: MQTT → 時系列 DB → ライブダッシュボード](pattern-1-mqtt-tsdb-live-dashboard.md) | MQTT ブローカー/デコーダー/Telegraf/Grafana Live 型のパイプライン |
| [パターン2: Prometheus + remote-write](pattern-2-prometheus-remote-write.md) | Thanos/Mimir/Cortex による長期保存を伴うスクレイプ型メトリクス |
| [パターン3: マネージド IoT → Timestream](pattern-3-managed-iot-timestream.md) | AWS IoT Core / Kinesis / Timestream のフルマネージドパイプライン |
| [パターン4: Kafka + OTel Collector + 時系列 DB](pattern-4-kafka-otel-collector.md) | ストリーミングブローカー + コレクター + 時系列 DB |
| [パターン5: 組み込み/列指向 時系列 DB](pattern-5-embedded-columnar-tsdb.md) | ティアリングされたコールドストレージを持つ QuestDB、ClickHouse、TimescaleDB |
| [オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md) | NetApp ONTAP(オンプレ AFF/FAS、Azure NetApp Files、Google Cloud NetApp Volumes、Cloud Volumes ONTAP)の公開エビデンス。FSx for ONTAP 限定ではない |

## リアルタイムテレメトリパイプラインの5パターン

公開文書や事例を横断すると、リアルタイムテレメトリのパイプライン形状は少数のパターンに収束します。本ドキュメントはこの5パターンを「リアルタイムテレメトリパイプライン」の網羅的リストとしてではなく、代表例として使用します。

| # | パターン | 典型的な構成 | 詳細 |
|---|---|---|---|
| 1 | MQTT ブローカー → デコーダー → 時系列 DB → ライブダッシュボード | Mosquitto/EMQX/HiveMQ、Telegraf、InfluxDB、Grafana Live(WebSocket) | [パターン1](pattern-1-mqtt-tsdb-live-dashboard.md) |
| 2 | スクレイプ型メトリクス + 長期保存への remote-write | Prometheus、remote_write、Thanos/Mimir/Cortex、オブジェクトストレージバックエンド | [パターン2](pattern-2-prometheus-remote-write.md) |
| 3 | マネージド IoT 取り込み → マネージド時系列ストア | AWS IoT Core、Kinesis Data Streams/Firehose、Amazon Timestream、S3 | [パターン3](pattern-3-managed-iot-timestream.md) |
| 4 | ストリーミングブローカー + コレクター + 時系列 DB | Kafka、OpenTelemetry Collector、時系列 DB、Grafana | [パターン4](pattern-4-kafka-otel-collector.md) |
| 5 | 高スループットな組み込み/列指向 時系列 DB + ティアリングされたコールドストレージ | QuestDB、ClickHouse、TimescaleDB、S3 互換コールド層 | [パターン5](pattern-5-embedded-columnar-tsdb.md) |

**5パターンのうち4パターンで一貫している発見**: ホット取り込み/WAL 経路は各プロジェクト自身の文書によってローカルディスクのセマンティクスを要求すると記載されており、長期保存/コールドストレージ層は S3 互換オブジェクトストアに収束します。これはパターン1だけから導いた結論ではありません。同じ分裂は、メッセージブローカー(Kafka、1つの文書化された NFS 例外あり)、メトリクスシステム(Prometheus)、専用時系列エンジン(QuestDB、ClickHouse)にわたって、それぞれ独立に現れています。以下の FSx for ONTAP 統合の選択肢は、この横断的な発見を、特定の1実装の詳細にではなく適用したものです。

## 変わるものと変わらないもの

「EBS を FSx for ONTAP に置き換える」という表現は、これらの形状のパイプラインに対しては不正確であり、本ドキュメントでは使いません。EBS は 1 台のインスタンスにアタッチされるブロックボリュームであり、FSx for ONTAP は NFS・SMB・iSCSI・S3 Access Points を通じて複数のコンピュートから同時にアクセス可能な共有ストレージです。正確な表現は**共有ストレージ層の追加**であり、コンピュートが既に使っているホットパスのストレージを置き換えるものではありません。

| パイプラインの層(5パターン共通) | 推奨 | 理由 |
|---|---|---|
| メッセージブローカー/取り込み(MQTT ブローカー、Kafka、IoT Core) | 変更なし(Kafka には文書化された NFS 例外あり — [パターン4](pattern-4-kafka-otel-collector.md)参照) | ローカルまたはマネージドなブローカー状態であり、大半のケースで共有ストレージの課題ではない |
| ライブ/ストリーミング配信(WebSocket push、ダッシュボードのスクレイプ) | 変更なし | [除外: リアルタイム配信経路](#除外-リアルタイム配信経路)を参照 |
| ホット時系列ストレージ(InfluxDB、Prometheus TSDB、QuestDB、ClickHouse、Timestream) | 変更なし | 各エンジンの公式文書がローカルディスクを要求、またはフルマネージドである。[除外: リアルタイム配信経路](#除外-リアルタイム配信経路)を参照 |
| ホットストレージの保持期間満了後の生テレメトリ | **候補**: S3 Access Points 経由のアーカイブ | [パターン A](#パターン-a-s3-access-points-経由の長期アーカイブ) |
| パイプラインロジックの開発・テスト(デコーダー、変換処理、アラートルール) | **候補**: FlexClone による本番データ複製 | [パターン B](#パターン-b-flexclone-による開発テストの高速化) |
| インシデント後の根本原因調査データ | **候補**: Snapshot / SnapLock | [パターン C](#パターン-c-snapshot--snaplock-によるフォレンジック保護) |
| SQLite ベースのダッシュボード/メタデータストア(例: Grafana 自身のストア) | **推論、未検証** | [SQLite/InfluxDB と iSCSI](#sqliteinfluxdb-と-iscsi-未検証の推論) |

## パイプライン種別を横断した FSx for ONTAP パターン

パイプラインパターンごとに同じアーカイブ/クローン/保護の理由を繰り返す代わりに、下表は5つのパイプラインパターンそれぞれに、3つの FSx for ONTAP パターンのどれが当てはまるかを示します。パターンごとの詳細と留意事項は各パターンのファイルにあります。

| パイプラインパターン | パターン A: アーカイブ | パターン B: FlexClone | パターン C: Snapshot/SnapLock | 詳細 |
|---|:---:|:---:|:---:|---|
| 1. MQTT → 時系列 DB → ライブダッシュボード | ✅ | ✅ | ✅ | [パターン1](pattern-1-mqtt-tsdb-live-dashboard.md) |
| 2. Prometheus + remote-write | ✅ | ✅ | ✅ | [パターン2](pattern-2-prometheus-remote-write.md) |
| 3. マネージド IoT → Timestream | ⚠️ | 該当なし | 該当なし | [パターン3](pattern-3-managed-iot-timestream.md) |
| 4. Kafka + OTel Collector + 時系列 DB | ✅ | ✅ | ✅ | [パターン4](pattern-4-kafka-otel-collector.md) |
| 5. QuestDB/ClickHouse/TimescaleDB | ✅ | ✅ | ✅ | [パターン5](pattern-5-embedded-columnar-tsdb.md) |

**表の読み方**: パターン A・B・C は以下で一度だけ定義し、どのパイプラインパターンが生テレメトリを生成したかにかかわらず同じ理由で適用されます。パターン3(フルマネージド)は、FSx for ONTAP がマネージドサービス自体が既に提供するもの以上をほとんど追加できない唯一のケースであり、本ドキュメントが適用範囲を過大に主張しないよう明記しています。

## パターン A: S3 Access Points 経由の長期アーカイブ

**課題**: 上記5パターンのすべてのホットストレージエンジンは、何らかの保持期間の制約を持つか、すべてを永久に保持することがコスト的に見合いません。根本原因分析、コンプライアンス、またはホットストア へのクエリ権限を持たないチームがそのウィンドウより古いデータを必要とする場合、あるいはチームごとにコピーを持たずに生データを共有する必要がある場合、ホットストアの保持期間が切れるとデータは失われます(あるいはチームごとに断片化したエクスポートとしてのみ存在します)。

**パターン**: パイプラインが既にホットストアへ書き込む取り込み用 Lambda・コンシューマー・エクスポートジョブを持っている箇所であれば、同じペイロードを [S3 Access Point](../../en/s3ap-fsxn-specification.md) 経由で FSx for ONTAP ボリュームにも書き込むことができます。これは[AWS が公式に文書化している Lambda ファイル処理パターン](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/tutorial-process-files-with-lambda.html)と同じ仕組みで、NFS/SMB でバックアップされたボリュームに対して S3 API 経由でファイルを読み書きします。アーカイブされたデータは、どのチームからも NFS/SMB で同時に参照可能になり、チームごとのコピーも同期対象の二重データストアも不要です。

**ここで関係する制約**: FSx for ONTAP S3 Access Points は条件付き書き込み(`If-None-Match`)に対応していません。これは [s3ap-fsxn-specification.md](../../en/s3ap-fsxn-specification.md#8-fsx-for-ontap-s3-access-points--constraints--validated-patterns) に既に記録されています。これにより、条件付き PUT のセマンティクスに基づくトランザクショナルなテーブル形式(Delta Lake、Iceberg、Hudi)をこのアーカイブ上に直接構築することはできません。つまりこのアーカイブは書き込み専用(write-once-per-object)の追記・アーカイブ対象であり、トランザクショナルなデータレイクではありません。生 JSON/バイナリテレメトリペイロードに対してはこの制約は問題になりませんが、アーカイブされたファイルの上に直接トランザクショナルなテーブル形式を構築する場合は、AWS が検証済み回避策として文書化している中間ステップ(例: DataSync による native S3 への転送、またはバッチ ETL ジョブ)が必要です。

## パターン B: FlexClone による開発/テストの高速化

**課題**: デコーダー・変換処理・アラートルールのロジック変更を現実的なデータ量でテストするには、通常本番データセットのコピーが必要です。規模が大きくなるほど時間がかかり、セキュリティ管理と最終的な削除が必要な二重コピーが発生します。

**パターン**: [FlexClone](https://aws.amazon.com/blogs/storage/accelerate-development-refresh-cycles-and-optimize-cost-with-amazon-fsx-for-netapp-ontap-cloning/) は、ボリュームの瞬時かつ容量効率の高い書き込み可能クローンを作成します。パターン A の生テレメトリアーカイブが FSx for ONTAP ボリューム上にあれば、そのボリュームのクローンをフルコピーにかかる時間ではなく数秒でテスト環境にアタッチでき、クローン後にソースから分岐したブロックのみのストレージを消費します。これが本ドキュメントの範囲で言う「ゼロコピー」の仕組みです。クローンを作成するためにファイル単位のコピー操作(S3 PUT/GET のラウンドトリップや `scp`)は不要です。

## パターン C: Snapshot / SnapLock によるフォレンジック保護

**課題**: 異常発生後の根本原因分析は、上記5パターンの大半で挙げられている用途です。その分析の根拠となるアーカイブ済みテレメトリが(誤操作や侵害された認証情報によって)改変・削除可能であれば、フォレンジックの記録自体の信頼性が損なわれます。

**パターン**: ONTAP の [Snapshot](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/using-backups.html) はボリュームの読み取り専用のポイントインタイムコピーを提供します。ポイントインタイムリカバリだけでなく不変性(immutability)がコンプライアンス・ガバナンス要件として求められる場合、[SnapLock](../../en/governance-and-compliance.md) がその上に write-once-read-many(WORM)保持を強制します。本リポジトリの既存の[自動対応ガイド](../../en/automated-response-guide.md)と[ARP インシデント対応ガイド](../../en/arp-incident-response-guide.md)は、FSx for ONTAP ボリュームに対する自動対応とランサムウェア対策の仕組みを既にカバーしているため、本ドキュメントではその内容を重複させません。本ドキュメントが追加するのは、テレメトリアーカイブ特有の位置づけのみです。パターン A のアーカイブボリュームに同じスナップショット/SnapLock 保護を適用し、根本原因分析が依拠するデータが黙って改変されないようにします。

> **不可逆性に関する補足**: SnapLock の保持ロックは、選択したロックモードによっては保持期間満了前に取り消せません(Compliance モードは設計上取り消し不可)。ボリュームに SnapLock を適用する前に、保持期間、影響範囲(どのボリューム、どのデータ)、選択した期間中そのデータが削除不能であることのコストを確認してください。Compliance モードと Enterprise モードの区別については[ガバナンスとコンプライアンス](../../en/governance-and-compliance.md)を参照し、その区別を読まずに進める判断ではありません。

## 除外: リアルタイム配信経路

本ドキュメントは、上記マトリクスに挙げたライブ/ストリーミング配信層と、すべてのホットストレージエンジンを、FSx for ONTAP 統合の対象から意図的に除外します。1つの文書化された例外(NFS 上の Kafka、[パターン4](pattern-4-kafka-otel-collector.md)参照)があります。理由は各プロジェクトが独立に文書化しているプロトコルとアーキテクチャ上の制約であり、単なる好みではありません。

- **Amazon ECS Fargate には FSx for ONTAP のネイティブなマウント経路がありません。** [AWS は ECS on EC2 起動タイプのみを対象に FSx for ONTAP のマウント手順を文書化しています](https://docs.aws.amazon.com/us_en/fsx/latest/ONTAPGuide/mount-ontap-ecs-containers.html)。NFS/SMB 共有を EC2 ホスト側でマウントし、そのホストパスをコンテナへ bind mount する手順です。Fargate のタスク定義は[bind mount host volume のみをサポート](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html)し、共有を事前マウントするホスト自体を持ちません。デコーダー/コレクター/時系列 DB の層を Fargate 上で動かすパイプラインは、そこで FSx for ONTAP を直接マウントできません。ECS on EC2 であれば可能です。これは読者が自身のデプロイに対して確認すべき実際の分岐点です。
- **Prometheus は自身のローカル TSDB について NFS/EFS を非サポートと明記しています。** 具体的な出典は[パターン2](pattern-2-prometheus-remote-write.md)を参照してください。
- **Apache Kafka のブローカー設計はログセグメントにローカルディスクを前提としていましたが、2023年の NetApp の修正によって NFSv4.x ではこの前提が変わりました。** 詳細は[パターン4](pattern-4-kafka-otel-collector.md)を参照してください。これは「ホットパスはローカルに留まる」が絶対的な規則ではない唯一のパターンです。
- **InfluxDB v1/v2(TSM ストレージエンジン)には NFS でのロック障害が文書化されています。** [パターン1](pattern-1-mqtt-tsdb-live-dashboard.md)を参照してください。
- **InfluxDB 3、QuestDB、ClickHouse は、コールドデータに対して NFS/SMB ではなく同じオブジェクトストアという答えに収束しています。** [パターン1](pattern-1-mqtt-tsdb-live-dashboard.md)と[パターン5](pattern-5-embedded-columnar-tsdb.md)を参照してください。
- **マネージドパイプライン(パターン3)にはそもそもマウントすべきボリュームがありません。** Amazon Timestream と AWS IoT Core はフルマネージドであり、ホットパスに FSx for ONTAP が接続できるコンピュートホストやアタッチされたストレージが存在しません。
- **SQLite ベースのダッシュボードメタデータストアは、NFS 上での運用が安全でないと文書化されています。** NFS の代わりに提案する iSCSI 固有の推論は次節で扱います。

## SQLite/InfluxDB と iSCSI: 未検証の推論

**本節は文書化された性質からの推論であり、実測結果ではありません。本ドキュメントのためにベンチマークや読み書き・ロックのテストは実施していません。** 本番環境でこのパターンに依拠する前に、読者自身で検証を実施することを推奨します。

FSx for ONTAP は [iSCSI プロトコル](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/mount-iscsi-luns-linux.html)をサポートしており、LUN を生のブロックデバイスとして提示します。クライアント側でローカルファイルシステム(ext4、xfs、NTFS)でフォーマットし、ローカルディスクと同様にマウントします。これは、ネットワーク**ファイル共有**プロトコルである NFS/SMB とは異なる性質です。SQLite 公式の[ファイルロックと並行性に関するドキュメント](https://sqlite.org/lockingv3.html)は、そのロックモデルが OS レベルのファイルロックに依存すると説明しており、複数の独立した報告([Sonarr/Sonarr#1886](https://github.com/Sonarr/Sonarr/issues/1886)、[Grafana Community フォーラム](https://community.grafana.com/t/two-grafana-servers-with-single-sqlite3-databse-on-nfs/2734))が、NFS・CIFS ネットワーク共有上で特有の SQLite 破損やロック失敗を報告しています(ローカルブロックデバイス上ではありません)。本ドキュメントが提示する推論(hypothesis)は次のとおりです。**iSCSI LUN は、フォーマット・マウント後は OS と SQLite に対してネットワークファイル共有ではなくローカルディスクとして見えるため、NFS/CIFS で文書化されているロック障害のクラスは同程度には適用されない。** これは本ドキュメントのために実測されたものではありません。

同じ推論は、ローカルファイルシステムを前提とする TSM エンジンを持つ InfluxDB v1/v2 にも拡張できます。NFS が安全でないと文書化されている状況において、iSCSI でバックエンドされたローカルファイルシステムは妥当な代替候補です。InfluxDB 3、QuestDB のオブジェクトストアコールド層、ClickHouse の S3 バックエンド外部ディスクは、この推論の対象外です。iSCSI のブロックインターフェースは、ロックの問題とは無関係に、これらいずれのオブジェクトストア依存も満たせません。

**この主張について、検証済みの内容と未検証の内容:**

| 主張 | 状態 | 出典 |
|---|---|---|
| SQLite のロックは NFS/CIFS 上で信頼できない | 検証済み(文書化) | [SQLite ドキュメント](https://sqlite.org/lockingv3.html)、[Grafana Community](https://community.grafana.com/t/two-grafana-servers-with-single-sqlite3-databse-on-nfs/2734) |
| FSx for ONTAP の iSCSI は生のブロックデバイスとして提示される | 検証済み(文書化) | [AWS iSCSI プロビジョニングドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/mount-iscsi-luns-linux.html) |
| SQLite/InfluxDB v1v2 を FSx for ONTAP の iSCSI 上に置くと NFS のロック障害クラスを回避できる | **推論 — 本ドキュメントのために未実測** | 推論のみ |
| FSx for ONTAP 上で iSCSI がデータベースワークロードにおいて SMB より性能が高い | 検証済み(実測、別ワークロード) | [AWS ブログ、SQL Server/HammerDB、r5dn.24xlarge、Multi-AZ、80,000 IOPS/2GB/s、公開日 2025-05](https://aws.amazon.com/blogs/modernizing-with-aws/optimizing-protocol-selection-when-using-amazon-fsx-for-netapp-ontap-for-microsoft-sql-server/)。FSx for ONTAP の iSCSI 実装が OLTP 型 I/O に対して十分な性能を発揮するという方向性のエビデンスとして引用しており、SQLite や InfluxDB の実測ではありません |
| SQLite と FSx for ONTAP を組み合わせた公開事例が存在する | 検証済み — ただしプロトコルは未確認 | [オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md#infor-cloverleaf--netapp-trident-経由の-fsx-for-ontap)を参照 |

**リアルタイム配信経路の除外と共通する制約**: ECS Fargate は iSCSI LUN もマウントできません。iSCSI イニシエータの設定はホスト OS 側で行う操作であり、Fargate はそれを公開しません。このパターンも FSx for ONTAP の NFS/SMB マウントと同様、ECS on EC2 起動タイプ(またはダッシュボード/データベースを直接動かす通常の EC2 インスタンス)が必要です。

## Kubernetes 経路(EKS/Trident): 参考情報

上記5パターンのいずれも、ECS や EC2 の代わりに Amazon EKS 上で動作可能です。本節は情報提供のみを目的とし、上記の推奨事項を変更するものではありません。

[NetApp Trident](https://docs.netapp.com/us-en/trident/trident-use/trident-fsx.html) は、Amazon EKS 向けに FSx for ONTAP バックエンドの永続ボリュームをプロビジョニングし、設定に応じて `ontap-nas`(NFS)または `ontap-san`(iSCSI)のバックエンドドライバを使用します。Trident 経由で SQLite と RaimaDB が本番稼働している公開事例は、[オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md#infor-cloverleaf--netapp-trident-経由の-fsx-for-ontap)を参照してください。ここではこの組み合わせが実際にどこかの本番環境で稼働している**公開エビデンス**として提示しており、ECS/EC2 ベースのパイプラインを EKS に移行すべきという推奨ではありません。

読者のパイプラインが既に ECS/EC2 ではなく EKS 上で動作している場合、評価すべき統合ポイントは上記の ECS bind mount 経路ではなく Trident です。本ドキュメントはこれ以上 Trident の設定には踏み込みません。本ドキュメントが対象とする ECS/EC2 ベースのパターンとは異なるコンピュートプラットフォームであるためです。

## 選択フローチャート

```
┌───────────────────────────────────────────┐
│ FSx for ONTAP に何を保存したいか            │
└──────────────────┬────────────────────────┘
                    │
   ┌────────────────┼─────────────────┬──────────────────────┐
   ▼                ▼                 ▼                      ▼
ホットストア      本番データの       過去のインシデントの    SQLite ベースの
保持期間満了後の   開発/テスト用複製   フォレンジック/         ダッシュボードストア、
生テレメトリ                          コンプライアンス記録     またはホットな時系列DB/ブローカー
   │                │                 │                      │
   ▼                ▼                 ▼                      ▼
パターン A:       パターン B:        パターン C:          ECS Fargate を
S3 Access         FlexClone          Snapshot /           使っているか、または
Points アーカイブ                     SnapLock             フルマネージドの
   │                                                        パイプライン(パターン3)か?
   ▼                                                        ┌────┴────┐
アーカイブの上に                                    │ Yes     │ No
Delta Lake / Iceberg /                              ▼         ▼
Hudi を使うか?                              未対応/該当なし  上記の推論
┌────┴────┐                                — ECS on EC2   セクションが
│ Yes      │ No                            または通常の    適用される
▼          ▼                              EC2 を使用      (iSCSI、未検証)。
先に DataSync  Lambda 経由の                              ただし NFS 上の
→ native S3    直接 S3 API                                Kafka は例外
のステップを   読み書き                                    (パターン4)
追加する       (パターン A)
```

## FAQ

**Q: これは FSx for ONTAP がこの種のパイプラインの EBS を置き換えるという意味ですか。**
A: いいえ。EBS(またはマネージドサービス自身のストレージ)は、ホット取り込み・ホットストレージ・ライブ配信の各層を動かすコンピュートにアタッチされたままです。FSx for ONTAP はそのホットパスを取り囲む共有アーカイブ/クローン/保護層を追加します。[変わるものと変わらないもの](#変わるものと変わらないもの)を参照してください。

**Q: なぜ本ドキュメントは1パターンではなく5パターンを扱うのですか。**
A: 単一の実装の詳細から構築した推奨事項は、リアルタイムテレメトリパイプライン全般の性質ではなく、その1つのアーキテクチャの産物にすぎないというリスクを負います。Prometheus、Kafka、マネージド IoT パイプライン、専用時系列 DB と照合した結果、同じホット/ローカル・コールド/オブジェクトストアという分裂が独立に確認されました(1つの文書化された例外あり)。[リアルタイムテレメトリパイプラインの5パターン](#リアルタイムテレメトリパイプラインの5パターン)を参照してください。

**Q: Lambda はライブ/ストリーミング配信経路(例: WebSocket push)に直接書き込めますか。**
A: 本ドキュメントでは Lambda/S3 Access Points と任意のライブ配信経路の間の接続は提案していません。本ドキュメントの S3 Access Points パターン(パターン A)は、既に取り込み・エクスポートジョブがホットストレージへ書き込んでいる箇所のアーカイブ経路に適用するものであり、ブラウザやクライアントへのリアルタイム配信には適用しません。

**Q: 「ゼロコピー」という主張は FlexClone のことですか、S3 Access Points のことですか。**
A: FlexClone(パターン B)がゼロコピーの仕組みそのものです。瞬時かつ容量効率の高いボリュームクローンで、ファイル単位のコピー操作を伴いません。S3 Access Points(パターン A)は別種のコピーを排除します。共有ボリューム上にデータが一度置かれれば、複数チームが NFS/SMB/S3 経由でそれぞれのコピーを維持せずに参照できます。両方とも本ドキュメントでは「ゼロコピー」と表現していますが、これはファイル単位の複製ステップ(PUT/GET、`scp`)が発生しないという意味であり、実際には2つの異なる課題に対する異なる ONTAP の仕組みです。

**Q: Kafka は本当に「ホットパスはローカルに留まる」の例外なのですか。**
A: はい。本ドキュメント自身のテストではなく、NetApp 自身が文書化しています。修正内容、それが要求する ONTAP バージョン、FSx for ONTAP 自身の ONTAP バージョンについて現場確認した内容、そして AWS 自身が独立に確認した Kafka on FSx for ONTAP のもう1つの事例(AutoMQ の WAL 用途)は[パターン4](pattern-4-kafka-otel-collector.md)を参照してください。

**Q: SQLite や InfluxDB を FSx for ONTAP の iSCSI 上で実際に動かして動作を確認した人はいますか。**
A: 本ドキュメントの基準では検証済みとは言えません。何が確認済みで何が未確認かは[オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md)を参照してください。本ドキュメントのロック安全性に関する推論は SQLite 公式ドキュメントからの推論であり、再現テストではありません。本番環境で依拠する前に、読者自身で読み書き・ロックの検証を実施してください。

**Q: v1/v2 は対象なのに、InfluxDB 3(および QuestDB/ClickHouse のコールド層)が iSCSI の推論から除外される理由は何ですか。**
A: これらのシステムのコールド/カタログ経路は条件付き PUT または強い整合性セマンティクスを持つ S3 互換オブジェクトストアを要求します。ローカルファイルシステムとは完全に異なるストレージモデルです。iSCSI はブロックデバイスを提示するものであり、InfluxDB v1/v2 のようなローカルディスク前提のエンジンに適用されるロックの論点とは無関係に、この要件を満たせません。[除外: リアルタイム配信経路](#除外-リアルタイム配信経路)を参照してください。

## 関連資料

- [パターン1: MQTT → 時系列 DB → ライブダッシュボード](pattern-1-mqtt-tsdb-live-dashboard.md)
- [パターン2: Prometheus + remote-write](pattern-2-prometheus-remote-write.md)
- [パターン3: マネージド IoT → Timestream](pattern-3-managed-iot-timestream.md)
- [パターン4: Kafka + OTel Collector + 時系列 DB](pattern-4-kafka-otel-collector.md)
- [パターン5: 組み込み/列指向 時系列 DB](pattern-5-embedded-columnar-tsdb.md)
- [オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md)
- [S3 AP の仕様と制約](../../en/s3ap-fsxn-specification.md) — パターン A とオブジェクトストア除外の両方が依拠する条件付き書き込みなどの制約
- [ガバナンスとコンプライアンス](../../en/governance-and-compliance.md) — パターン C で参照する SnapLock の Compliance モードと Enterprise モードの区別
- [自動対応ガイド](../../en/automated-response-guide.md) / [ARP インシデント対応ガイド](../../en/arp-incident-response-guide.md) — パターン C が重複させずに前提とする既存のランサムウェア対策の仕組み
- [レイクハウス監視パターン](../lakehouse-monitoring-patterns.md) — レイクハウス統合において FSx for ONTAP 自体を監視する、関連するが別個のパターン群
- [AWS ネイティブ代替マトリクス](../../en/native-alternative-matrix.md) — Kubernetes/Trident 経路の運用ツールを評価する際に関係する、プロプライエタリな管理ツールに対する AWS ネイティブな代替
