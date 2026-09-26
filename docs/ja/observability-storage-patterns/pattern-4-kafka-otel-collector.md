# パターン4: Kafka + OTel Collector + 時系列 DB

🌐 **日本語**(このページ) | [English](../../en/observability-storage-patterns/pattern-4-kafka-otel-collector.md)

⬅️ [概要へ戻る](README.md)

## 典型的な構成

Kafka クラスタが高ボリュームのテレメトリを取り込み、OpenTelemetry Collector(または Kafka ネイティブのコンシューマー)が変換・ルーティングし、時系列 DB や検索エンジンが Grafana などのツールでクエリするために保存します。

**公開されている出典**: [Apache Kafka 自身のファイルシステム設計ドキュメント](https://kafka.apache.org/090/design/design/)は、ブローカーがログセグメントファイルにローカルディスクを前提としていることを説明しています。

## このパターンが例外である理由

Kafka は、本ドキュメントの5パターンの中で唯一「ホットパスはローカルディスクを要求する」が絶対的な制約ではないパターンです。その理由を正確に述べる価値があります。他の4パターン(Prometheus、InfluxDB、SQLite、Timestream)の理由はここには当てはまりません。

**過去の問題**: Kafka ブローカーのログディレクトリを NFS 上で動かすと、パーティションの再割り当てやクラスタのリサイズ中にクラッシュが発生していました。これは "silly rename" 問題と呼ばれるものが原因です。NFS は、まだ開いている参照が残るファイルの unlink を許可しません。NFS クライアントはこれを回避するため、ファイルを特別な一時名にリネームし、最後にクローズされた時点で削除します。Kafka のリバランス処理は、まだ開いている参照が残るファイルを削除するため、この回避策が発動し、以下で説明する修正が入る前はブローカーをクラッシュさせることがありました。[複数の独立した報告がこれが理論上の問題ではなく実際に再現する問題だったことを裏付けています](https://stackoverflow.com/questions/60900481/kafka-doesnt-work-with-external-nfs-volume)。

**NetApp の修正**: NetApp 自身のブログ記事[「ONTAP is ready for streaming applications」(2023年)](https://www.netapp.com/blog/ontap-ready-for-streaming-applications/)は、NFSv4.x の "delete on last close" 機能を ONTAP の NFS サーバー側と Linux の NFS クライアント側の両方に実装し、クライアント側の変更を upstream に貢献したと説明しています。同ブログによれば、クライアント側の修正は **RHEL 8.7 と RHEL 9.1** で一般提供(GA)されました。ONTAP サーバー側については、[NetApp の Trident プロジェクトの対応する GitHub issue](https://github.com/NetApp/trident/issues/808) が、この動作を有効にするために `true` に設定する必要がある新しい ONTAP ボリューム設定 `-is-preserve-unlink-enabled` が **ONTAP 9.12.1** で導入されたことを記録しています。

**NetApp 自身による機能検証**(本ドキュメント自身のテストではありません): [2025-09-15付の NetApp ソリューションドキュメント](https://docs.netapp.com/us-en/netapp-solutions/data-analytics/kafka-nfs-functional-validation-silly-rename-fix.html)は、AWS 上で2つの並行した Confluent Platform 7.2.1 の Kafka クラスタを構築した検証を説明しています。一方は汎用の NFSv3 サーバー上、もう一方は **ONTAP 9.12.1** を実行する **NetApp Cloud Volumes ONTAP** インスタンス上に NFSv4.1 でマウントし修正を有効化した構成です。両方でパーティション再割り当てをトリガーした結果(NetApp の報告): NFSv3 クラスタは silly-rename の障害でクラッシュし、修正を有効化した ONTAP NFSv4.1 クラスタは中断なく再割り当てを完了しました。[NetApp の Confluent 向け併載記事](https://www.netapp.com/pt/blog/simplify-apache-kafka-confluent/)は、クラッシュ修正以外の運用上の効果も挙げています。ブローカーの復旧が速くなる(データが共有ストレージ上にあるため、再起動したブローカーがレプリカから再構築する必要がない)ことと、ブローカーのコンピュート要件が減る(データ処理がストレージシステムにオフロードされる)ことです。

## FSx for ONTAP について確認済みの内容と未確認の内容

上記のすべての検証 — 機能テスト、クラッシュ有無の比較、具体的な ONTAP バージョン(9.12.1)とボリューム設定(`-is-preserve-unlink-enabled`) — は、NetApp によって **NetApp Cloud Volumes ONTAP** に対して実施されたものであり、Amazon FSx for NetApp ONTAP に対して直接実施されたものではありません。このテスト結果自体は、FSx for ONTAP についてのエビデンスにはなりません。

**FSx for ONTAP のソフトウェアバージョン**: 本ドキュメントの著者は AWS サービスチームに直接確認し、FSx for ONTAP のファイルシステムが **ONTAP 9.18.1 以降**を実行していることを確認しました(2026-09-26 時点。ONTAP 自体はこの時点で 9.19.1 まで公開されています)。これは**現場での直接確認であり、公開されたバージョン番号ではありません**。AWS は本ファイルの調査で確認した FSx for ONTAP のドキュメントの範囲では、稼働中の ONTAP ソフトウェアバージョンを明記していないため、読者は公開ドキュメントだけからこれを独立に検証することはできません。9.18.1 は `-is-preserve-unlink-enabled` を導入した 9.12.1 よりかなり後のバージョンであり、ONTAP は以前のリリースで導入されたボリュームレベルの設定を、非推奨と明記されない限り削除しないため、本ドキュメントは**Kafka-over-NFS の silly-rename 修正が FSx for ONTAP の基盤となる ONTAP バージョンで利用可能である**として扱います。これは、バージョンを確認できず FSx for ONTAP について未確認としていた本ドキュメントの以前のドラフトからの変更です。

**依然として未確認の内容**: [FSx for ONTAP ボリュームの更新に関する AWS 公式ドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/updating-volumes.html)は、FSx for ONTAP ボリュームが FSx のコンソール/CLI/API に加えて「NetApp ONTAP コマンドラインインターフェース(CLI)と REST API」経由でも到達可能であることを確認しています。そのため `-is-preserve-unlink-enabled` のような ONTAP ネイティブのボリューム属性を設定する仕組み自体は原理上存在します。本ドキュメントは、その具体的な属性を設定する FSx for ONTAP 固有の手順を見つけられませんでした。したがって「ONTAP バージョンが修正をサポートしている」(上記の現場確認による)ことと、「実際に特定の FSx for ONTAP ボリュームにその修正が設定され、E2E で検証された」こと(本ドキュメントでは未確認 — NetApp の NFSv3 対 NFSv4.1 のパーティション再割り当て比較のような機能テストが FSx for ONTAP に対して直接実行された例は見つかりませんでした)は区別して扱ってください。

## もう1つの独立した公開事例: AutoMQ の Diskless Kafka on FSx for ONTAP

[AWS Storage ブログ記事(2026年公開)](https://aws.amazon.com/blogs/storage/achieving-sub-10ms-latency-and-94-cost-savings-with-diskless-kafka-using-automq-and-amazon-fsx-for-netapp-ontap/)は、FSx for ONTAP と Kafka の実質的に異なる — かつ直接関連する — 組み合わせを説明しています。S3 バックエンドの「Diskless Kafka」実装である AutoMQ は、silly-rename 修正が対象とするログセグメントの主ストレージとしてではなく、S3 の手前に置く **マルチ AZ 共有 WAL(write-ahead log)層**として FSx for ONTAP を使用します。これは上記の NFS/silly-rename の問題と同じシナリオではなく、FSx for ONTAP と Kafka が組み合わさる、独立に確認された別の方法です。

**AWS が測定した内容**(AWS 自身のベンチマークであり、本ドキュメント自身の測定ではありません): m7g.4xlarge ブローカー3台、マルチ AZ モードの FSx for ONTAP Generation 2(容量 1,024 GiB、プロビジョニング IOPS 3,072、スループット 736 MBps)、us-east-1、読み書き比 4:1、メッセージサイズ 64 KB、書き込み 300 MBps・読み取り 1.2 GiBps を維持。結果: 平均書き込みレイテンシー **5.98 ms**(P99 12.87 ms)、平均エンドツーエンドレイテンシー **7.79 ms**(P99 18.04 ms)。ローカルディスクの Kafka の性能に近づきつつ、S3 を耐久性のある長期保存層として維持しています。AWS はコスト比較も報告しています(従来の3レプリカ・マルチ AZ Kafka が同じ P99 書き込みレイテンシー目標に対して月額約 $317,000 に対し、FSx for ONTAP を使う AutoMQ BYOC は月額約 $18,345)。これは AWS 自身が公開した数値として引用しており、独立に再現した測定ではありません。

これは、「Kafka と FSx for ONTAP が本番で組み合わさって動作する」ことについて、silly-rename 修正よりも有意に強いエビデンスです。Cloud Volumes ONTAP でのテストからの推論ではなく、AWS 自身が FSx for ONTAP に対して直接公開したベンチマークだからです。ただし、これは silly-rename 修正そのものを確認するものではありません。AutoMQ の WAL は「固定サイズのリングバッファ」と説明されており、この使用パターンは silly-rename 問題が依拠する、パーティション再バランス時の open 状態でのファイル削除というコードパスを実行しない可能性があります。

## FSx for ONTAP が当てはまる箇所

| FSx for ONTAP パターン | 当てはまるか | 補足 |
|---|:---:|---|
| [パターン A: アーカイブ](README.md#パターン-a-s3-access-points-経由の長期アーカイブ) | ✅ | ブローカーのログディレクトリ自体ではなく下流のエクスポート(例: Kafka Connect の S3 シンク出力、時系列 DB 自身のコールド層エクスポート)に適用する |
| [パターン B: FlexClone](README.md#パターン-b-flexclone-による開発テストの高速化) | ✅ | パターン A のアーカイブをクローンし、コレクター/コンシューマーのロジックを現実的なデータでテストする |
| [パターン C: Snapshot/SnapLock](README.md#パターン-c-snapshot--snaplock-によるフォレンジック保護) | ✅ | インシデント後の調査が依拠するアーカイブ済みストリームエクスポートを保護する |

## パターン固有の補足

読者が Kafka ブローカー自身のログディレクトリを FSx for ONTAP の NFS 上に置きたい場合(パターン A/B/C を下流エクスポートのみに使うのではなく)、silly-rename 修正に必要な ONTAP バージョン要件は満たされている(上記の現場確認による)として扱えますが、本番環境で依拠する前に、具体的なボリューム設定(`-is-preserve-unlink-enabled`)を E2E で検証すべきです。その設定手順自体は、本ドキュメントでは FSx for ONTAP に対して独立に確認できていません。主ログストレージではなく WAL 的な用途であれば、上記の AutoMQ の事例が AWS によって直接確認されています。

## 関連資料

- [概要: FSx for ONTAP によるオブザーバビリティ基盤ストレージの統合](README.md)
- [オンプレミス/マルチクラウド ONTAP 事例集](onprem-and-fsxn-case-studies.md)
- [S3 AP の仕様と制約](../../en/s3ap-fsxn-specification.md)
