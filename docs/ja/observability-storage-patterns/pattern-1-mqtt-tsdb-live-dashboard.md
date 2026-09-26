# パターン1: MQTT → 時系列 DB → ライブダッシュボード

🌐 **日本語**(このページ) | [English](../../en/observability-storage-patterns/pattern-1-mqtt-tsdb-live-dashboard.md)

⬅️ [概要へ戻る](README.md)

## 典型的な構成

MQTT ブローカー(Mosquitto、EMQX、HiveMQ)がエッジデバイスやパブリッシャーからテレメトリを受信し、デコーダー/コレクター(多くは Telegraf)が正規化し、時系列 DB(多くは InfluxDB)が保存し、ライブダッシュボード(Grafana、多くは Grafana Live の WebSocket 経路)が運用担当者にリアルタイムで表示します。並行してアーカイブ経路が存在することも多くあります。

**公開されている実例**: [橋本, 2026](https://speakerdeck.com/hashimoto_kei/jinkou-eisei-kaihatsu-o-sasaeru-grafana) は衛星テレメトリ可視化についてこの形状を説明しています(MQTT ブローカー → デコーダー → Telegraf → Grafana Live、並行して IoT Core → Lambda → InfluxDB のアーカイブ経路)。[HiveMQ の MQTT+Grafana ガイド](https://www.hivemq.com/blog/mqtt-data-visualization-with-grafana/)と[EMQX の IoT 可視化ガイド](https://www.emqx.com/en/blog/building-an-iot-visualization-platform-with-emqx-tables-and-grafana)も、無関係な用途で独立に同じ一般的な形状を説明しています。本ドキュメントはこれらの実装のいずれも評価するものではありません。それぞれ公開文書化されたパイプライン形状の例として引用しており、批評や推奨の対象ではありません。

## ホットパスがローカルに留まる理由

- **InfluxDB v1/v2(TSM ストレージエンジン)には文書化された NFS ロック障害があります。** [influxdata/influxdb#9047](https://github.com/influxdata/influxdb/issues/9047) は、TSM データディレクトリを NFS 上で運用した際の "stale NFS file handle" エラーを報告しています。
- **InfluxDB 3(Core/Enterprise)は、カタログに対して条件付き PUT セマンティクスを持つ S3 互換オブジェクトストアを要求します。** [InfluxData 公式ドキュメント](https://docs.influxdata.com/influxdb3/core/object-storage/s3/)がこれを明示しています。FSx for ONTAP S3 Access Points は条件付き書き込み(`If-None-Match`)に対応していません([S3 AP の仕様と制約](../../en/s3ap-fsxn-specification.md#8-fsx-for-ontap-s3-access-points--constraints--validated-patterns)参照)。そのため InfluxDB 3 のカタログはそれに対して正しく動作しません。
- **Grafana 自身のダッシュボード/メタデータストア(SQLite)は、NFS 上での運用が安全でないと文書化されています。** [Grafana Community フォーラム](https://community.grafana.com/t/two-grafana-servers-with-single-sqlite3-databse-on-nfs/2734): 「NOT SAFE — SQLite DB を NFS 共有から使うべきではない。破損/データ整合性の問題が生じる」。
- **Amazon ECS Fargate には FSx for ONTAP のネイティブなマウント経路がありません。** デコーダー/Telegraf/Grafana の層が ECS 上で動く場合に関係します。AWS ドキュメントの出典は[概要の除外セクション](README.md#除外-リアルタイム配信経路)を参照してください。ECS on EC2 は FSx for ONTAP をマウントできますが、Fargate はできません。

## FSx for ONTAP が当てはまる箇所

| FSx for ONTAP パターン | 当てはまるか | 補足 |
|---|:---:|---|
| [パターン A: アーカイブ](README.md#パターン-a-s3-access-points-経由の長期アーカイブ) | ✅ | 既にホット時系列 DB へ書き込んでいるアーカイブ経路の Lambda/コンシューマー(例: IoT Core→Lambda→InfluxDB の経路)が、同じ生ペイロードを S3 Access Points 経由で FSx for ONTAP ボリュームにも書き込める |
| [パターン B: FlexClone](README.md#パターン-b-flexclone-による開発テストの高速化) | ✅ | パターン A のアーカイブボリュームをクローンし、デコーダー/Telegraf のロジック変更を現実的なデータでテストする |
| [パターン C: Snapshot/SnapLock](README.md#パターン-c-snapshot--snaplock-によるフォレンジック保護) | ✅ | 異常発生後の根本原因分析が依拠するアーカイブを保護する |

## パターン固有の補足

概要ドキュメントに記載した一般的な注意点以外に、このパイプライン形状に特有の留意事項はありません。本ドキュメントのパターン横断の発見は、もともとこのパイプライン形状に対して確認され、その後パターン2〜5でも同様に成り立つことが確認されました。

## この文脈での iSCSI 推論の当てはめ

このパイプラインの SQLite ベースのダッシュボードストア(Grafana 自身のメタデータ)や InfluxDB v1/v2 インスタンスを、運用上の理由でローカル EBS/インスタンスストレージから移す必要がある場合、[概要の iSCSI 推論](README.md#sqliteinfluxdb-と-iscsi-未検証の推論)が(未検証ながら)検討すべき選択肢であり、NFS ではありません。これは本ドキュメントのためにテストされていません。

## 関連資料

- [概要: FSx for ONTAP によるオブザーバビリティ基盤ストレージの統合](README.md)
- [オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md)
- [S3 AP の仕様と制約](../../en/s3ap-fsxn-specification.md)
