# オブザーバビリティ基盤ストレージのためのオンプレミス/マルチクラウド ONTAP 事例集

🌐 **日本語**(このページ) | [English](../../en/observability-storage-patterns/onprem-and-fsxn-case-studies.md)

⬅️ [概要へ戻る](README.md)

## このページの範囲

このドキュメント群の他の部分は Amazon FSx for NetApp ONTAP に焦点を当てています。NetApp ONTAP 自体は、オンプレミスの AFF/FAS システム、Azure NetApp Files、Google Cloud NetApp Volumes、NetApp Cloud Volumes ONTAP など、複数の提供形態で動作しており、ONTAP をオブザーバビリティプラットフォームのデータストアとして使う公開エビデンスは AWS 以外にも存在します。このページはそのエビデンスを集約し、各実例がどの ONTAP 提供形態を使用したかを明記します。ある提供形態で確認された機能が、別の提供形態でも自動的に確認されたことにはならないためです(この区別が重要な理由は、下記の[Kafka の事例](#ontap-nfs-上の-kafka--cloud-volumes-ontap-でのテストと別途確認した-fsx-for-ontap-のバージョン)を参照)。

**本ページは引用した企業の実装を評価するものではありません。** 以下の実例はいずれも公開文書化された事例やベンダー製品ドキュメントの記述であり、ある機能がどこかに存在するというエビデンスとして引用しており、特定のアーキテクチャの複製を推奨するものではありません。

## Infor Cloverleaf — NetApp Trident 経由の FSx for ONTAP

[NetApp の顧客事例](https://www.netapp.com/customers/infor/) は、Infor のヘルスケアデータ連携基盤 Cloverleaf が、NetApp Trident 経由でプロビジョニングされた FSx for ONTAP を伴う Amazon EKS 上で稼働し、**組み込みの RaimaDB と SQLite データベース**をサポートしていると説明しています。この事例は、最大 7000 万メッセージ/日の処理、重複排除/圧縮による最大 65% のストレージコスト削減、マルチ AZ 可用性を報告しており、実名の引用元(Jesse Evans, Principal Solution Architect, Infor)を伴います。

**確認済みの内容と未確認の内容**: この事例は、SQLite と RaimaDB が本番環境で FSx for ONTAP 上で稼働していることを確認しています。この構成が[NetApp Trident のバックエンドドライバ](https://docs.netapp.com/us-en/trident/trident-use/trident-fsx.html) — `ontap-nas`(NFS)か `ontap-san`(iSCSI)のどちらを使っているかは**述べていません**。本ドキュメントの[iSCSI 推論](README.md#sqliteinfluxdb-と-iscsi-未検証の推論)は、この事例を iSCSI 固有の裏付けとして主張することはできず、SQLite と FSx for ONTAP の組み合わせがどこかの本番環境で稼働しているというエビデンスとしてのみ主張できます。

## FSx for ONTAP 上の OpenSearch — クロスプラットフォーム(AWS、Azure、Google Cloud、オンプレミス)

[NetApp のブログ記事](https://www.netapp.com/blog/opensearch-on-netapp-ontap-in-the-cloud/) は、OpenSearch について本ドキュメントの中心的な問いに明示的に答えています。「OpenSearch はローカルディスク上で効果的に動作するように設計された分散データベースです。しかし、多くの組織が AWS、Azure、Google Cloud に NetApp ONTAP ストレージを展開しています」。この記事は、**Cloud Volumes ONTAP、Azure NetApp Files、Google Cloud NetApp Volumes、オンプレミスの NetApp AFF システム**にわたって、OpenSearch(「検索・分析・オブザーバビリティ」ソフトウェアと明示的に呼ばれています)を NFS または iSCSI ボリューム上に構築する手順を説明しています。FSx for ONTAP はこの記事のベンチマークに含まれており、NetApp LUN(iSCSI)と NFS(FSx for ONTAP)のストレージボリュームについて、比較対象の中で良好な性能結果を観測したと報告しています。

これは、本ドキュメント群がこれまでに見つけた最も強力な単一の公開エビデンスです。**NFS と iSCSI の両方**が、**FSx for ONTAP を含む主要な ONTAP 提供形態のすべて**にわたって、NetApp 自身によってオブザーバビリティワークロード(OpenSearch)向けにベンチマークされているというもので、無関係な文書からの推論ではありません。これは、ネットワーク接続の ONTAP ストレージが、ドキュメントストア型のオブザーバビリティエンジン(本ドキュメントの5パターンが主に対象とする時系列 DB エンジンとは異なる)にとって選択可能であるという(未検証の推論ではなく)推論を直接裏付けます。同記事のサイジングに関する指針: シャードサイズは 10〜50GB に保つこと、ネットワークストレージの無停止ボリューム拡張により、OpenSearch が本来必要とするローカル SSD のリバランシング操作が減ること。

## ONTAP NFS 上の Kafka — Cloud Volumes ONTAP でのテストと、別途確認した FSx for ONTAP のバージョン

[パターン4](pattern-4-kafka-otel-collector.md#このパターンが例外である理由)で詳細を扱っています。ここでは、基盤ソフトウェアが共有されていても「NetApp ONTAP」と「FSx for ONTAP」で別々の確認が必要になることを示す本ドキュメント群で最も明確な例として要約します。[NetApp 自身による Kafka-over-NFS の "silly rename" 修正の機能検証](https://docs.netapp.com/us-en/netapp-solutions/data-analytics/kafka-nfs-functional-validation-silly-rename-fix.html)は、**ONTAP 9.12.1 を実行する NetApp Cloud Volumes ONTAP** に対して実施されたものであり、FSx for ONTAP ではありません。別途、本ドキュメントの著者は AWS サービスチームに確認し、FSx for ONTAP が **ONTAP 9.18.1 以降**を実行していることを確認しました(現場確認、2026-09-26、公開文書化されていません)。これは修正の基盤となるボリューム設定を導入した 9.12.1 よりかなり後のバージョンです。バージョンの問題が解決した上で依然として未確認の内容(具体的には、そのボリューム設定が実際の FSx for ONTAP ボリュームに設定・E2E 検証されているか)、および Kafka を FSx for ONTAP 上で直接使う AWS 独自に確認済みの WAL 用途の事例(AutoMQ)については、[パターン4](pattern-4-kafka-otel-collector.md#fsx-for-ontap-について確認済みの内容と未確認の内容)を参照してください。

## NetApp Harvest — ONTAP 自身の監視ツール、データストアとしての用途ではない点

[NetApp Harvest](https://netapp.github.io/harvest/latest/) は、ONTAP、StorageGRID、E-Series、Cisco Nexus スイッチからパフォーマンス・容量・ハードウェアメトリクスを収集し、Prometheus または InfluxDB へエクスポートする OSS ツールで、付属の Grafana ダッシュボードを持ちます。**これは本ドキュメント群が他で扱う内容の逆方向です**。Harvest は ONTAP を監視*対象*として使うのであって、オブザーバビリティプラットフォームを支える*データストア*として使うのではありません。読者の混乱を避けるためにここに含めています。「NetApp オブザーバビリティ」を検索する読者は Harvest に頻繁に遭遇しますが、それは本ドキュメント群とは別の問いに答えるものです。[FSx for ONTAP を Harvest で監視するための本リポジトリ自身のガイド](https://www.netapp.com/learn/aws-fsx-blg-how-to-monitor-amazon-fsx-for-netapp-ontap-using-netapp-harvest/)が、この「ONTAP を監視する」用途をより詳しく扱っています。

## FSx for ONTAP 上の SQL Server — iSCSI 性能、オブザーバビリティではないが直接関連

オブザーバビリティワークロード自体ではありませんが、データベース型ワークロードに対する FSx for ONTAP のストレージプロトコルを比較した、本ドキュメント群が見つけた中で最も厳密な公開ベンチマークです。[AWS ブログ記事(公開日 2025-05)](https://aws.amazon.com/blogs/modernizing-with-aws/optimizing-protocol-selection-when-using-amazon-fsx-for-netapp-ontap-for-microsoft-sql-server/)は、iSCSI と SMB で構成した FSx for ONTAP 上の SQL Server に対して HammerDB の OLTP ベンチマークを実行しました。r5dn.24xlarge インスタンス、Multi-AZ、80,000 IOPS/2GB/s、4つのファイルシステムサイズ(7/14/21/28TB)にわたる測定です。結果: iSCSI は初回実行で SMB より約20%、steady state で約10%優れた性能を示しました。これは[iSCSI 推論のセクション](README.md#sqliteinfluxdb-と-iscsi-未検証の推論)で、FSx for ONTAP の iSCSI 実装全般に対する方向性のエビデンスとして引用していますが、オブザーバビリティ固有の測定ではありません。

## 本ページが扱わない内容

- **上記の OpenSearch 記事を超える、Azure NetApp Files または Google Cloud NetApp Volumes に特化したオブザーバビリティ事例。** 本ドキュメントの調査では、そのクロスプラットフォームの OpenSearch 記事以外に、Azure NetApp Files または Google Cloud NetApp Volumes を特にオブザーバビリティプラットフォームのデータストアとして名指しした事例は見つかりませんでした。そのような事例が存在し後で見つかった場合は、このページに追加すべきです。
- **ONTAP の提供形態間の性能比較**(例: Cloud Volumes ONTAP の Kafka-NFS 修正は、FSx for ONTAP の同等機能より速いか遅いか)。そのような比較は見つかっておらず、ここでも主張していません。

## 関連資料

- [概要: FSx for ONTAP によるオブザーバビリティ基盤ストレージの統合](README.md)
- [パターン1: MQTT → 時系列 DB → ライブダッシュボード](pattern-1-mqtt-tsdb-live-dashboard.md)
- [パターン4: Kafka + OTel Collector + 時系列 DB](pattern-4-kafka-otel-collector.md)
- [AWS ネイティブ代替マトリクス](../../ja/native-alternative-matrix.md)
