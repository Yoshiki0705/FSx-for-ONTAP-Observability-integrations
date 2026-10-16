# cloud-retrieval-policy による SSD への読み戻しの制御

🌐 **日本語**（本ページ）| [English](../en/cloud-retrieval-policy.md)

> **ステータス / 対象読者 / 証拠の階層**
>
> ステータスはリファレンスです。本ページの各主張は、引用した公開ソース（AWS の FSx for ONTAP ドキュメントと docs.netapp.com）に基づきます。対象読者は、Amazon FSx for NetApp ONTAP の容量プール階層化（FabricPool）を使い、容量プールにあるデータを SSD へ読み戻す挙動を制御したいエンジニアです。証拠の階層は次の 3 つを使います。`文書化済み`（引用した AWS または NetApp のページに記載）、`仮説`（推論で、確認していない）、`未確認`（本ブランチに日付付きの実行記録が無い）。この語彙は [monitoring-design.md](monitoring-design.md) の定義に合わせています。読み方に関する助言（Web サーバー、CDN）は、断定を避けて運用上の考慮として記します。出典は主張の箇所にインラインのリンクで置きます。

## エグゼクティブサマリ

`cloud-retrieval-policy` は、容量プールにある冷えたデータを読み出したときに SSD へ戻すかどうかを決めます。Auto ボリュームの既定（default）は、ランダム読み出しのときだけ SSD へ戻し、シーケンシャル読み出しは容量プールに残します。ウイルススキャンや分析のための全読み出しで冷えたデータが戻らないため、多くの用途でこの既定が望ましいとされています（[modify-cloud-retrieval-policy-task](https://docs.netapp.com/us-en/ontap/fabricpool/modify-cloud-retrieval-policy-task.html)、`文書化済み`）。on-read は読み方に関係なく戻し、ボリューム全体に効くため常用は勧められません。応答時間が気になる場合は、on-read に切り替える前に、冷却期間の延長（Auto の既定は 31 日）を先に検討します。

## 4 つのポリシーの動作

### default（ランダム読み出しのみ SSD へ戻す）

default は、ランダム読み出しで触れたブロックだけを SSD へ戻し、シーケンシャル読み出しで触れたブロックは容量プールに残します。ウイルススキャンや分析のようにボリュームを順に全読みする処理で冷えたデータが SSD に戻ってしまうのを避けられるため、多くの用途で望ましいとされています（[modify-cloud-retrieval-policy-task](https://docs.netapp.com/us-en/ontap/fabricpool/modify-cloud-retrieval-policy-task.html)、`文書化済み`）。

### on-read（読み方に関係なく戻す、ボリューム全体への影響）

on-read は、読み方（ランダムかシーケンシャルか）に関係なく、読み出したブロックを SSD へ戻します。ボリューム全体に効くため、常用は勧められません。一時的に同じデータを繰り返し読む必要があるときだけ切り替える使い方が案内されています（[oracle-tiering-retrieval-policies](https://docs.netapp.com/us-en/ontap-apps-dbs/oracle/oracle-tiering-retrieval-policies.html)、`文書化済み`）。

### never と promote の動作

never は、読み出しても容量プールのブロックを SSD へ戻しません。promote は、ポリシーに応じて容量プールのデータを SSD へ引き上げます。各値の設定と動作は FSx for ONTAP のドキュメントにまとまっています（[set-cloud-retrieval](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/set-cloud-retrieval.html)、`文書化済み`）。

## 読み出しパターンの判定

### Web サーバーのシーケンシャル読み出しがランダムと判定されない点

default はランダム読み出しだけを SSD へ戻すため、「どう読まれるか」で挙動が変わります。Web サーバーはファイルを先頭から順に読むことが多く、その場合シーケンシャルと判定されて SSD に戻らないことがあります。「Web サーバーの読み出しは常にランダム扱いになる」とは限らない点に注意してください（判定の仕様は [modify-cloud-retrieval-policy-task](https://docs.netapp.com/us-en/ontap/fabricpool/modify-cloud-retrieval-policy-task.html) を参照、運用上の考慮）。

### CDN 経由でも FSx for ONTAP まで読み出しが届く経路（キャッシュミス・パージ・対象外・バイパス）

CDN を前段に置いていても、オリジンへの読み出しが完全になくなるわけではありません。キャッシュの期限切れ、手動パージ、キャッシュ対象外のコンテンツ、CDN を通らない処理などでは、読み出しが FSx for ONTAP まで届きます。そのときに default のボリュームでシーケンシャルと判定されれば、冷えたデータは SSD に戻らず容量プールから読まれます（一般的な CDN の挙動に基づく運用上の考慮であり、特定の構成での測定ではありません）。

## on-read の前に冷却期間の延長を検討する理由

容量プールからの読み出しの応答時間が気になる場合、on-read に切り替えるとボリューム全体で読み戻しが起き、SSD の使用量が増えます。先に検討できるのは冷却期間の延長で、よく読まれるデータが SSD に長くとどまるようにする方法です。Auto の冷却期間の既定は 31 日です（[modify-cloud-retrieval-policy-task](https://docs.netapp.com/us-en/ontap/fabricpool/modify-cloud-retrieval-policy-task.html)、`文書化済み`）。この調整は、本リポジトリの検証環境（[monitoring-design.md](monitoring-design.md) に記載の第 1 世代・HA ペア 1 つのファイルシステム）での測定ではなく、ドキュメントに基づく既定値です。

## 未確認の範囲

- FSx for ONTAP で `cloud-retrieval-policy` を on-read にした場合の実際の動作。読み方に関係なく SSD へ戻すというドキュメントの記述を、FSx for ONTAP 上で確認した記録は本ブランチにありません（`未確認`）。検証する場合は、on-read 設定下でシーケンシャル全読みを行い、`volume show-footprint` の Performance Tier フットプリントが増えるかを default のボリュームと対照して観測します。
- S3 Access Points 経由の読み出しが、Auto の default で SSD に戻る扱いになるか。S3 の GET がランダムとシーケンシャルのどちらと判定されるかは、本ブランチに実行記録がありません（`未確認`）。NFS/SMB のランダム読みを対照に置いて観測する計画を作ります。

## 関連ドキュメント

- [snapmirror-migration-tiering.md](snapmirror-migration-tiering.md): 移行後に冷えたデータを SSD へ戻す運用（break 後の読み出し）。
- [capacity-automation.md](capacity-automation.md): SSD 容量と autosize の関係。
- [tiering-optimizer（S3AP リポジトリ）](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/operations/tiering-optimizer/README.md): 階層化ポリシーと冷却期間を推奨するツール。本ページは読み戻しポリシーの説明を担います。
- [FabricPool の概要（NetApp）](https://docs.netapp.com/us-en/ontap/fabricpool/index.html): 容量プール階層化の一般的な説明。
