# SnapMirror 移行時の階層化と SSD への戻し方

🌐 **日本語**（本ページ）| [English](../en/snapmirror-migration-tiering.md)

> **ステータス / 対象読者 / 証拠の階層**
>
> ステータスは移行ランブックです。本ページの各主張は、引用した公開ソース（AWS の FSx for ONTAP ドキュメントと docs.netapp.com、公開 KB）に基づきます。対象読者は、SnapMirror で既存の ONTAP から Amazon FSx for NetApp ONTAP へ移行し、移行中とその後の SSD 階層の挙動を理解したいエンジニアです。証拠の階層は次の 3 つを使います。`文書化済み`（引用した AWS または NetApp のページに記載）、`仮説`（推論で、確認していない）、`未確認`（本ブランチに日付付きの実行記録が無い）。この語彙は [monitoring-design.md](monitoring-design.md) の定義に合わせています。出典は主張の箇所にインラインのリンクで置きます。値はプレースホルダー（`fs-0123456789abcdef0`、`svm-0123456789abcdef0`、`svm_name`、`vol_name`）です。本ページは一般化した移行シナリオ（たとえば SnapMirror でオンプレミス ONTAP から移行するケース）を扱い、特定の顧客環境は扱いません。

## エグゼクティブサマリ

SnapMirror はブロックの温度（アクセス状況）を宛先へ引き継ぎません。宛先の階層化ポリシーが Auto のまま初期転送すると、受信したベースラインは宛先で改めて冷却期間を満たすまで SSD に残り、SSD 階層が埋まって「No space left on device」で転送が止まることがあります。AWS のドキュメントは、移行時に宛先を All にして受信データを容量プールへ逃がし、移行中は SSD 使用率が 80% を超えないよう CloudWatch で監視する方法を案内しています（[migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html)、`文書化済み`）。切り替えでは、最終 Update のあとに `snapmirror break` で宛先を RW にします。公開後に必要なデータを SSD へ戻すには、`touch` ではなく実際に読み出す必要があります。`touch` はメタデータだけを書き換え、データを読まないため SSD には戻りません。本ページは移行中の階層化（ブロック A 相当）と、break/resync による切り替え（ブロック B 相当）を 1 本のランブックにまとめます。

## 対象と前提

- SnapMirror で既存の ONTAP（オンプレミスまたはクラウド）から FSx for ONTAP へデータ保護（DP）関係で移行し、切り替え時に宛先を RW 化するシナリオを対象にします。
- FSx for ONTAP の容量プール階層化（FabricPool）が宛先で有効であることを前提にします。
- 管理操作は ONTAP CLI または ONTAP REST API を使います。FSx for ONTAP コンソールと AWS CLI/API では一部の設定（階層化ポリシーの一部挙動、autosize）に手が届かないため、[cloud-retrieval-policy.md](cloud-retrieval-policy.md) と [capacity-automation.md](capacity-automation.md) の該当節を併読してください。
- 事前知識として、SnapMirror 宛先を新規作成するときに階層化がどう扱われるかは、S3AP リポジトリの [pitfalls-flexcache-snapmirror](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/docs/agent/pitfalls-flexcache-snapmirror.md) にまとまっています。

## 移行中の階層化ポリシー

### ブロックの温度が宛先に引き継がれない仕組み

SnapMirror はブロックの温度（アクセス頻度の履歴）を宛先へ転送しません。宛先の階層化ポリシーが Auto の場合、受信したデータは宛先で改めて冷却期間を満たすまで SSD（Performance Tier）に残ります（[volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html)、`文書化済み`）。移行元で冷えていたブロックでも、宛先では「今届いたばかりの新しいブロック」として扱われます。

### 宛先が Auto のときの冷却期間と 24〜48 時間後の階層化

Auto ポリシーでは、ブロックは冷却期間を満たしてから階層化されます。AWS のドキュメントは、冷却期間の経過から 24〜48 時間後に階層化されると記載しています（[volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html)、`文書化済み`）。つまり冷却期間が明けても即座に容量プールへ移るわけではなく、追加の待ち時間があります。

### SSD 使用率 50% が階層化の 1 条件にすぎない点

Auto での階層化は SSD 使用率が 50% を超えてから進みますが、これは条件の 1 つにすぎません。SSD 使用率が 50% を超えていても、冷却期間を満たさないデータは階層化されずに SSD に残ります（[volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html)、`文書化済み`）。したがって「SSD が 50% を超えれば自動で容量プールへ逃げる」とは限りません。

### 初期転送を Auto で行うと SSD が埋まる経路

これらを合わせると、宛先を Auto（長い冷却期間）のまま初期転送した場合、受信したベースライン全体が冷却期間を満たさないため SSD に溜まり続けます。ベースラインが SSD 容量を超えると、「No space left on device」で転送が止まることがあります（[migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html)、`文書化済み`）。

### 初期転送を All にする手順

AWS のドキュメントは、移行時に宛先の階層化ポリシーを All にする方法を案内しています。All では受信データが即座に容量プールへ移るため、SSD にベースライン全体が溜まるのを避けられます（[migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html)、`文書化済み`）。

```text
::> volume modify -vserver svm_name -volume vol_name -tiering-policy all
::> volume show -vserver svm_name -volume vol_name -fields tiering-policy
```

### メタデータの SSD 常駐と 1:10 の目安

All であってもメタデータは常に SSD に残ります。AWS のドキュメントは、SSD と容量プールの比を 1:10 の目安とする記載を置いています（[migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html)、`文書化済み`）。つまり容量プールにデータを逃がしても、メタデータ分の SSD は確保しておく必要があります。

### 移行中の SSD 使用率 80% 監視

移行中は SSD 使用率が 80% を超えないよう CloudWatch で監視することが推奨されています（[migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html)、`文書化済み`）。この監視は本リポジトリの [monitoring-design.md](monitoring-design.md) の `## メトリクスカタログ` にある `StorageCapacityUtilization`（`StorageTier=SSD`）のアラーム設計と、`terraform/fsxn-monitoring-dashboard/` の容量アラームで組めます。

## 切り替えと SSD への戻し方

### break による宛先の RW 化と resync による復元

宛先を書き込み可能（RW）にするのは `snapmirror break` です。関係を削除する必要はありません。関係を元の DP 状態へ戻すには `snapmirror resync` を使います（[snapmirror-resync](https://docs.netapp.com/us-en/ontap-cli/snapmirror-resync.html)、`文書化済み`）。

### 関係の削除と create の後は resync で、initialize ではない点

関係を削除したあとに `create` で作り直すことはできますが、続けて実行するのは `initialize` ではなく `resync` です。`resync` は共通 Snapshot を使って差分だけを転送します。共通 Snapshot がなくなっていると初期転送からやり直しになります（[snapmirror-resync](https://docs.netapp.com/us-en/ontap-cli/snapmirror-resync.html)、`文書化済み`）。

### resync が共通 Snapshot まで戻し RW 中の変更を破棄する動作

`resync` は宛先を共通 Snapshot の時点まで戻します。そのため break して RW にしていた間に宛先へ書いた変更は破棄されます（[snapmirror-resync](https://docs.netapp.com/us-en/ontap-cli/snapmirror-resync.html)、`文書化済み`）。切り替え後に宛先で更新を続けたあと resync すると、その更新が失われる点に注意してください。

### All の状態で break すると Auto に変わる動作

宛先の階層化ポリシーが All の状態で `snapmirror break` を実行すると、ポリシーは Auto に変わります（[NetApp KB: SnapMirror break converts the tiering policy of volume from all to auto](https://kb.netapp.com/on-prem/ontap/DP/SnapMirror/SnapMirror-KBs/SnapMirror_break_converts_the_tiering_policy_of_volume_from_all_to_auto)、`文書化済み`）。移行中に All で容量プールへ移したデータは、break 後に Auto へ変わってもそのまま容量プールに残り、Auto に変えただけでは SSD へ戻りません。

### touch がメタデータだけを書き換え SSD に戻さない点

`touch` はファイルのメタデータ（タイムスタンプ）を書き換えるだけで、データブロックを読みません。そのため冷えて容量プールにあるデータは `touch` では SSD に戻りません。SSD へ戻すには実際に読み出す必要があります（読み戻しの判定については [cloud-retrieval-policy.md](cloud-retrieval-policy.md)、冷却と階層化の仕組みは [volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html)、`文書化済み`）。

### 推奨手順（最終 Update → quiesce → break → 読み出しで SSD へ → 公開）

切り替え時に、読み戻しのための追加の break/resync を避けられる手順は次のとおりです。

1. 最終 Update を実行して宛先を最新にします。
2. `snapmirror quiesce` で転送を止めます。
3. `snapmirror break` で宛先を RW にします。All の宛先であればこの時点でポリシーは Auto に変わります（上記 KB）。
4. 公開前に、SSD に載せておきたいデータを実際に読み出して SSD へ戻します（`touch` ではなく読み出し）。
5. 宛先を公開します。

```text
::> snapmirror update -destination-path svm_name:vol_name
::> snapmirror quiesce -destination-path svm_name:vol_name
::> snapmirror break -destination-path svm_name:vol_name
::> snapmirror show -destination-path svm_name:vol_name
```

> **切り替え時期に関する補足**
>
> 切り替えの一定期間前に宛先を Auto へ変更して Update を続けると、切り替え直前の更新分は Auto として SSD に残ります。ただし All の間に容量プールへ移ったデータは、Auto に変えても容量プールに残り、自動では SSD へ戻りません（[migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html)、[volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html)、`文書化済み`）。

## 未確認の範囲

- resync のあと、読み出して SSD に戻したデータが SSD に残るか。resync は宛先を共通 Snapshot まで戻すため、その前に読み戻した SSD フットプリントが resync 後も保持されるかは、本ブランチに実行記録がありません（`未確認`）。検証する場合は `volume show-footprint` の Performance Tier フットプリントを resync 前後で比較する計画を作ります。
- SnapMirror 宛先から break したボリュームの autosize の既定値。break 後の autosize は各ボリュームの設定に従いますが、break 後ボリュームで autosize が有効かと既定の最大値は別問であり、確認していません（`未確認`）。詳細と検証計画は [capacity-automation.md](capacity-automation.md#autosize-の継承とボリュームサイズの扱い) にあります。
- T2（`terraform/fsxn-ontap-custom-metrics/`）の SnapMirror メトリクスの、オンプレミス ONTAP → FSx for ONTAP の関係での動作。本リポジトリで検証済みなのは同一リージョンの FSx 内の関係であり、オンプレミス起点のソースでの到達性・遅延・クラスタ間 LIF の挙動は確認していません（`未確認`）。

## 関連ドキュメント

- [cloud-retrieval-policy.md](cloud-retrieval-policy.md): 移行後に SSD へ読み戻す挙動を制御する `cloud-retrieval-policy`。
- [capacity-automation.md](capacity-automation.md): break 後の autosize、ボリュームサイズ、SSD 容量アクション。
- [monitoring-design.md](monitoring-design.md): 移行中の SSD 使用率監視（`StorageCapacityUtilization`、`StorageTier=SSD`）のアラーム設計。
- `terraform/fsxn-monitoring-dashboard/`: 容量アラームの Terraform モジュール。
- [cross-region-replication.md](cross-region-replication.md): SnapMirror を使った DR パターン。
- [pitfalls-flexcache-snapmirror](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/docs/agent/pitfalls-flexcache-snapmirror.md): SnapMirror 宛先の新規作成時の階層化の既定（S3AP リポジトリ）。
