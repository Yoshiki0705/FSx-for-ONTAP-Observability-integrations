# パターン5: 組み込み/列指向 時系列 DB(QuestDB、ClickHouse、TimescaleDB)

🌐 **日本語**(このページ) | [English](../../en/observability-storage-patterns/pattern-5-embedded-columnar-tsdb.md)

⬅️ [概要へ戻る](README.md)

## 典型的な構成

専用の時系列/列指向データベース(QuestDB、ClickHouse、TimescaleDB)が、別のブローカーを介さずに高スループットのメトリクスやイベントを直接取り込み、自身のストレージエンジンから(多くは Grafana による)ダッシュボードのクエリに応答します。これらのエンジンの多くは、ホットなローカル層とコールドなオブジェクトストア層を持つ、ティアリングされたストレージモデルを標準で備えています。

**公開されている出典**: [QuestDB のコールドストレージ運用ドキュメント](https://questdb.com/docs/operations/cold-storage/); [ClickHouse の外部ディスクストレージドキュメント](https://clickhouse.com/docs/concepts/features/configuration/server-config/storing-data)。

## ホットパスがローカルに留まる理由

これらのエンジンは、自身のホット層ストレージモデルをローカルディスク指向と文書化しており、独立に、コールドデータに対してネットワークファイル共有ではなくネイティブな S3 互換層を提供しています。

- **QuestDB**: [コールドストレージは Enterprise 限定・既定で無効な別機能](https://questdb.com/docs/operations/cold-storage/)であり、ローカルディスクのホットストレージエンジンの上に重ねるものであって、それを置き換えるものではありません。
- **ClickHouse**: [公式ドキュメントは「ClickHouse サーバーが動作しているマシンのローカルファイルシステムに通常保存される」と説明](https://clickhouse.com/docs/concepts/features/configuration/server-config/storing-data)しており、外部ディスク(S3 互換を含む)は、レイテンシー要求の低いデータのための明示的な別設定として扱われます。
- **TimescaleDB** は PostgreSQL の拡張機能であり、ホットな hypertable について PostgreSQL 自身のローカルディスク指向のストレージモデルを継承しています。

これは、[パターン1](pattern-1-mqtt-tsdb-live-dashboard.md)(InfluxDB 3)と[パターン2](pattern-2-prometheus-remote-write.md)(Thanos/Mimir/Cortex)で観測された、同じホット/ローカル・コールド/オブジェクトストアの分裂の3番目の独立した裏付けです。互いに無関係な3つのプロジェクトが独立に同じ答えに収束しています。

## FSx for ONTAP が当てはまる箇所

| FSx for ONTAP パターン | 当てはまるか | 補足 |
|---|:---:|---|
| [パターン A: アーカイブ](README.md#パターン-a-s3-access-points-経由の長期アーカイブ) | ✅ | エンジン自身のコールド層の代替先の1つ。アーカイブされたデータがデータベース自身のツール以外からも NFS/SMB でアクセスされる必要がある場合に選ぶ。既存のコールド層で十分ならば必須の追加ではない |
| [パターン B: FlexClone](README.md#パターン-b-flexclone-による開発テストの高速化) | ✅ | パターン A のアーカイブをクローンし、クエリやスキーマの変更を現実的な過去データでテストする |
| [パターン C: Snapshot/SnapLock](README.md#パターン-c-snapshot--snaplock-によるフォレンジック保護) | ✅ | コンプライアンスやフォレンジック調査が依拠するアーカイブ済みコールド層データを保護する |

## パターン固有の補足

パターン1、2、4とは異なり、このパターンのホット/コールドの分裂は、FSx for ONTAP が回避策を提供する制約ではありません。これらのエンジンが既に自身で行った設計選択であり、本ドキュメントがパターン A で推奨する同じ S3 互換オブジェクトストアという答えに独立に到達しています。FSx for ONTAP の S3 Access Points は、その同じコールド層の実装候補の1つであり、コールド層を持たないエンジンに新規機能を追加しているわけではありません。エンジン組み込みのコールド層機能の代わりに FSx for ONTAP を選ぶかどうかは、そのアーカイブされたデータに対して(データベース自身のツール以外から)NFS/SMB アクセスが実際に必要かどうかで判断します。必要がなければ、組み込み機能のほうが簡潔です。

## 関連資料

- [概要: FSx for ONTAP によるオブザーバビリティ基盤ストレージの統合](README.md)
- [オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md)
- [S3 AP の仕様と制約](../../en/s3ap-fsxn-specification.md)
