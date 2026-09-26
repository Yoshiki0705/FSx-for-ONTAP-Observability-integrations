# パターン2: Prometheus + remote-write

🌐 **日本語**(このページ) | [English](../../en/observability-storage-patterns/pattern-2-prometheus-remote-write.md)

⬅️ [概要へ戻る](README.md)

## 典型的な構成

Prometheus がターゲットからメトリクスをスクレイプし、自身のローカル TSDB(ブロック + WAL)に書き込みつつ、`remote_write` が長期保存バックエンド(Thanos、Mimir、Cortex、またはベンダープラットフォーム)へサンプルを転送します。長期保存バックエンド自身は、多くの場合 S3 互換オブジェクトストアに永続化します。Grafana や類似のツールが、ダッシュボードやアラートのためにローカルの Prometheus または長期保存バックエンドのいずれかにクエリします。

**公開されている出典**: [Prometheus 自身の remote-write ドキュメント](https://oneuptime.com/blog/post/2026-02-20-prometheus-remote-write-storage/view); [Thanos remote-write によるスケーリング](https://medium.com/@mohitverma160288/thanos-remote-write-scaling-metrics-with-ease-part1-eb861b9aefa9)。

## ホットパスがローカルに留まる理由

**Prometheus は自身のローカル TSDB について NFS/EFS を非サポートと明記しています。** [prometheus/prometheus#10611 でのメンテナー回答](https://github.com/prometheus/prometheus/issues/10611): 「NFS ファイルシステム(AWS の EFS を含む)はサポートされていません。NFS は POSIX 準拠である可能性がありますが、大半の実装はそうではありません。信頼性のためにローカルファイルシステムのみを使うことを強く推奨します」。これは[SUSE の Rancher Monitoring ドキュメント](https://www.suse.com/support/kb/doc?id=000021332)が別製品の文脈で同じ内容をほぼそのまま繰り返していることで独立に裏付けられています。互いに無関係な2つの出典が、これがプロジェクト全体の制約であり例外的事例ではないと一致しています。

これは、ローカルの Prometheus TSDB 自体が、以下の3つの FSx for ONTAP パターンいずれの対象にも決してならないことを意味します。FSx for ONTAP が関わりうるのは `remote_write` の下流です。

## FSx for ONTAP が当てはまる箇所

| FSx for ONTAP パターン | 当てはまるか | 補足 |
|---|:---:|---|
| [パターン A: アーカイブ](README.md#パターン-a-s3-access-points-経由の長期アーカイブ) | ✅ | `remote_write` 先の生エクスポート(例: Thanos/Mimir のオブジェクトストアバケットのエクスポート、下流の ETL ジョブの出力)に適用する。Prometheus 自身のローカル TSDB ブロックには決して適用しない |
| [パターン B: FlexClone](README.md#パターン-b-flexclone-による開発テストの高速化) | ✅ | パターン A のアーカイブをクローンし、アラートルールやレコーディングルールの変更を現実的な過去データでテストする |
| [パターン C: Snapshot/SnapLock](README.md#パターン-c-snapshot--snaplock-によるフォレンジック保護) | ✅ | SLO のポストモーテムやキャパシティプランニングの検討が依拠するアーカイブ済みの長期メトリクスデータを保護する |

## パターン固有の補足

パターン A のアーカイブ対象は `remote_write` 先のエクスポートであり、Prometheus 自身のストレージディレクトリではありません。Prometheus の `--storage.tsdb.path` を FSx for ONTAP の NFS マウントに直接向けることは、上記で引用した Prometheus 自身のドキュメントに基づき、本パターンが明示的に推奨しない唯一の点です。

Thanos、Mimir、Cortex はいずれも設計上、長期ブロックストレージを S3 互換オブジェクトストアに永続化します。これは[パターン1](pattern-1-mqtt-tsdb-live-dashboard.md)(InfluxDB 3)と[パターン5](pattern-5-embedded-columnar-tsdb.md)(QuestDB、ClickHouse)で独立に観測された、同じホット/ローカル・コールド/オブジェクトストアの分裂です。FSx for ONTAP の S3 Access Points は、そのオブジェクトストア層の候補の1つであり、アーカイブされたメトリクスデータがメトリクススタック自身のツール以外からも NFS/SMB でアクセスされる必要がある場合に選びます。

## 関連資料

- [概要: FSx for ONTAP によるオブザーバビリティ基盤ストレージの統合](README.md)
- [オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md)
- [S3 AP の仕様と制約](../../en/s3ap-fsxn-specification.md)
