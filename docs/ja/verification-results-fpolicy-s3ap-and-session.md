
🌐 **日本語**（このページ） | [English](../en/verification-results-fpolicy-s3ap-and-session.md)

## 検証情報

3 つの問いを実機で確認した記録である。

1. FSx for ONTAP S3 Access Point 経由の読み書きは FPolicy 通知を発火するか
2. 同じ操作は ONTAP のネイティブ監査ログに記録されるか
3. ECS Fargate 上の FPolicy サーバの ONTAP セッションは、特定の時間・日数で切断されるか

1 と 2 は答えが分かれた。**FPolicy には現れず、ONTAP 監査ログには現れる。** ただし監査ログに
記録される識別情報は、ファイルプロトコル経由の場合と同じではない。

| 項目 | 値 |
|------|-----|
| **検証日** | `2026-08-26` (JST) |
| **問い 1 の状態** | 測定完了（UNIX / NFS と WINDOWS / SMB の両方） |
| **問い 2 の状態** | 測定完了 |
| **問い 3 の状態** | 測定完了（72 時間の窓を満了。自発的な切断 0 件） |

### 検証環境

| 項目 | 値 |
|------|-----|
| **AWS リージョン** | `ap-northeast-1` |
| **FSx for ONTAP ファイルシステム ID** | `fs-0123456789abcdef0` |
| **デプロイタイプ / スループット** | `SINGLE_AZ_1` / 128 MBps / SSD 1024 GiB |
| **ONTAP バージョン** | `9.18.1P3D1` |
| **SVM 名** | `verify-e2e-svm` (`svm-0123456789abcdef0`) | <!-- allow:naming: SVM resource name -->
| **UNIX 検証ボリューム** | `fpolicy_s3ap_vol`、UNIX セキュリティスタイル、1 GiB |
| **NTFS 検証ボリューム** | `fpolicy_s3ap_ntfs`、NTFS セキュリティスタイル、1 GiB |
| **監査ログ出力ボリューム** | `fpolicy_s3ap_auditlog`、UNIX セキュリティスタイル、1 GiB |
| **FPolicy スタック** | `fpolicy-s3ap-verify`（`shared/templates/fpolicy-server-fargate.yaml`） |
| **FPolicy サーバ** | ECS Fargate 1 タスク、0.25 vCPU / 512 MB、`linux/amd64` |
| **FPolicy プロトコルバージョン** | ネゴシエーション結果 `1.2` |
| **ONTAP 側エンジン設定** | `asynchronous`、`ssl_option=no_auth`、`keep_alive_interval=PT2M`、`status_request_interval=PT10S` |

FPolicy サーバのコードとテンプレートは既存の `shared/fpolicy-server/` と
`shared/templates/fpolicy-server-fargate.yaml` をそのまま使い、測定に必要な差分だけを加えた。
加えた差分は「観測のための変更」の節に記す。

### 環境上の制約と、それによる設計変更

| 制約 | 実際の対応 |
|------|-----------|
| 128 MBps のファイルシステムは SVM 6 個が上限で、専用 SVM を作れなかった（`ServiceLimitExceeded`） | 既存 SVM を使い、FPolicy policy の scope を検証ボリュームだけに限定した。受信した通知はすべてこの検証に帰属する |
| VPC に NAT がなく、ECR / CloudWatch Logs / SQS の interface endpoint もない | インターネットゲートウェイへ経路を持つサブネットに配置し、`AssignPublicIp=ENABLED` で egress を確保した。タスクのセキュリティグループは受信を SVM のセキュリティグループのみに限定し、送信は 443 のみである |
| SVM の AD ドメインに到達可能なドメインコントローラがなかった | SMB の対照実験と WINDOWS identity の S3 Access Point には、SVM のローカル SMB ユーザを使った。ドメインアカウントは不要だった（「付随して判明したこと」参照） |
| S3 Access Point の UNIX identity に `fsxadmin` を指定すると作成が失敗した | SVM のローカル UNIX ユーザに存在する `root` を指定した |

---

## 1. S3 Access Point 経由の I/O と FPolicy 通知

### 1-1. 結論

**FSx for ONTAP S3 Access Point 経由のデータ操作は、FPolicy 通知を発火しなかった。**
UNIX identity（UNIX ボリューム）と WINDOWS identity（NTFS ボリューム）の両方で同じ結果である。
同一ボリューム・同一 FPolicy セッションに対するファイルプロトコル（NFSv3 / SMB）の操作は発火した。

この結論は 2 つの独立した根拠の組み合わせで成り立っている。

| 根拠 | 内容 |
|------|------|
| 構造的な根拠 | ONTAP 9.18.1P3D1 の FPolicy event が受け付ける `protocol` 値は `cifs` / `nfsv3` / `nfsv4` の 3 つだけで、S3 やオブジェクトアクセスに相当する値は存在しない |
| 実測の根拠 | S3 Access Point のデータプレーン呼び出しに対して通知 0 件、直後の同一ボリュームへのファイルプロトコル操作に対して通知あり |

### 1-2. 受け付けられる protocol 値の列挙

「通知が来ない」を「設定が届いていないだけ」と区別するために、まず監視対象に指定できる
protocol 値を実機で列挙した。候補を 1 つずつ POST し、作成できたものだけを採用した。

```bash
# Create an event per candidate, deleting the ones that succeed to leave the cluster as found
for proto in cifs nfsv3 nfsv4 nfsv4.1 s3 S3 smb nfs object http fcp iscsi; do
  echo "--- $proto"
done
```

結果は次のとおりである。

| protocol 値 | 結果 |
|------------|------|
| `cifs` | 受け付けられた |
| `nfsv3` | 受け付けられた |
| `nfsv4` | 受け付けられた |
| `nfsv4.1` / `nfsv41` / `nfsv4_1` | 拒否 |
| `s3` / `S3` | 拒否 |
| `smb` / `nfs` / `object` / `http` / `fcp` / `iscsi` | 拒否 |

拒否時のレスポンスは HTTP 400 で、本文は `"s3" is an invalid value for field "protocol"` である。

file operation も同様に 1 つずつ確認した。`cifs` と `nfsv4` は 12 個
（`create` `create_dir` `delete` `delete_dir` `getattr` `open` `close` `read` `write`
`rename` `rename_dir` `setattr`）を受け付ける。`nfsv3` は `getattr` `open` `close` `access`
を受け付けず、代わりに `link` `lookup` `symlink` を受け付ける 12 個である。

### 1-3. 監視設定

受け付けられた 3 プロトコルそれぞれに、そのプロトコルが許す全 file operation を有効にした
event を作成し、1 つの policy にまとめて検証ボリュームに scope を限定した。読み取り側の
取りこぼしを防ぐため `read` と、`cifs` / `nfsv4` では `open` `close` `getattr` も含めている。

| 設定項目 | 値 |
|---------|-----|
| event | `verify_cifs_all_ops` / `verify_nfsv3_all_ops` / `verify_nfsv4_all_ops` |
| policy | `verify_policy`、`mandatory=false`、`priority=2` |
| scope | `include_volumes: [fpolicy_s3ap_vol, fpolicy_s3ap_ntfs]` |
| engine | 単一の外部サーバ、TCP 9898 |

policy の scope は policy 作成時にインラインで指定する必要がある。scope を別途 POST すると
404 になる。また enabled な policy は変更できないため、event や scope を後から変更する場合は
disable → 変更 → enable の順になり、その窓の操作は捕捉されない。

### 1-4. 測定手順

無操作の窓 → S3 Access Point のみの操作 → 静定 → ファイルプロトコルの対照操作、の順で行った。
対照を後に置いたのは、対照が発火すれば「設定が届いていない」という説明が成立しなくなるためである。

```text
PHASE 0  キューを空にして 90 秒間、一切の I/O を行わない
PHASE 1  S3 Access Point のみ: PUT x3, GET x3, HEAD x1, LIST x1, DELETE x1
PHASE 2  60 秒静定して件数を数える
PHASE 3  同一ボリュームにファイルプロトコルで create / read / delete（対照）
```

### 1-5. 結果

UNIX identity（UNIX ボリューム、対照は NFSv3）:

| フェーズ | 操作 | FPolicy 通知 |
|---------|------|-------------|
| PHASE 0 | なし（90 秒） | 0 件 |
| PHASE 1 | S3 Access Point データプレーン 9 回 | **0 件** |
| PHASE 3 | NFSv3 create / read / delete | **3 件** |

WINDOWS identity（NTFS ボリューム、対照は SMB 3.0）:

| フェーズ | 操作 | FPolicy 通知 |
|---------|------|-------------|
| PHASE 0 | なし（90 秒） | 0 件 |
| PHASE 1 | S3 Access Point データプレーン 9 回 | **0 件** |
| PHASE 3 | SMB create / read / delete | **10 件** |

SMB の対照が 10 件になるのは、`cifs` event で `open` `close` `getattr` も監視しているためで、
1 ファイル操作あたり複数の通知が出る。S3 Access Point 側が 0 件であることは、この分だけ
はっきりする。

いずれの PHASE 1 の窓でも、サーバログには KeepAlive 以外の行が 1 行も現れなかった。未知の
メッセージ型も届いていない。

S3 Access Point 経由の書き込みが実際にボリュームに到達していることは、ファイルプロトコル側から
確認した。PUT した 3 つのうち DELETE した 1 つが消え、残る 2 つがファイルとして存在し、
内容も一致する。つまり I/O は成立しており、通知が来ないのは書き込みが起きていないからではない。

### 1-6. mandatory + synchronous モードでも遮断されない

通知が来ないことと、遮断できないことは別の主張である。FPolicy を `mandatory=true` にすると、
外部サーバが応答しないときに ONTAP はクライアントアクセスを拒否する。この性質を使えば
「S3 Access Point 経由の経路が FPolicy の関門を通っているか」を、通知の有無ではなく
**遮断されるかどうか**で直接確かめられる。

構成は次のとおりである。既存の非同期ポリシーとは別に、専用ボリューム 1 本だけを対象とする
2 本目のポリシーを作った。

| 設定項目 | 値 |
|---------|-----|
| engine | `type=synchronous`、宛先は**リスナが存在しないアドレス**（事前に TCP 9898 が閉じていることを確認） |
| policy | `mandatory=true`、priority 3、専用ボリューム 1 本に scope 限定 |
| event | `nfsv3` と `cifs`、`create` `write` `delete` `rename` |
| ONTAP 側の状態 | `state=disconnected`、`disconnected_reason="TCP Connection to FPolicy server failed."` |

この状態で同一ボリュームに 2 つの経路からアクセスした結果である。

| 経路 | 結果 |
|------|------|
| NFSv3 の書き込み | **`Permission denied`。ファイルは作成されなかった** |
| S3 Access Point の PUT | **成功** |
| S3 Access Point の GET | **成功**（内容も一致） |
| S3 Access Point の LIST | **成功** |
| S3 Access Point の DELETE | **成功** |

拒否が FPolicy によるものであることは対照で確認した。ポリシーを `enabled=false` にしてから
同一の NFS 書き込みを再実行すると成功する。したがって最初の `Permission denied` は
権限設定の副作用ではなく、FPolicy の強制によるものである。

**つまり S3 Access Point 経由の経路は、通知が出ないだけでなく、FPolicy の関門を通っていない。**
mandatory モードで操作を遮断する設計は、この経路に対しては効かない。遮断を前提にした
セキュリティ設計では、これは通知の欠落より重い。

副産物として、2 本目のポリシーを追加・有効化しても既存ポリシーのセッションは切断されなかった。
`update_time` と `session_uuid` が変わらないまま維持された。ポリシー単位で接続が独立しているため、
別のポリシーを足す作業は既存の観測窓を壊さない。

### 1-6-1. 応答する同期エンジンでも通知されない

「サーバに届かないから遮断もされないだけで、届く構成なら通知されるのではないか」という
可能性が残る。これを潰すため、同期エンジンの宛先を**実際に接続を受け付けているサーバ**に
変更し、`mandatory=false`（fail-open）にして測り直した。

| 経路 | サーバ側の観測 |
|------|--------------|
| NFSv3 の書き込み | **通知が届いた。** `SCREEN_REQ` を受信し、SQS へ送信された（両ノードから 1 件ずつ、計 2 件） |
| S3 Access Point の PUT / GET / DELETE | **1 件も届かない。** 当該時刻の窓には KeepAlive 以外のログが存在しない |

同期エンジンの往復も観測できた。サーバが応答しない場合、ONTAP は
`STATUS_QUERY_REQ`（`ReqId` と `ReqType=NFS_CREAT` を含む）を送って待ち、その後
`SCREEN_CANCEL`（`CancelReason: Cancel Timedout`）を送る。`mandatory=false` なので操作自体は
通り、ファイルは 0 バイトで作成された。

つまり同期エンジンは動作しており、ファイルプロトコルの操作を screening 対象として渡している。
**それでも S3 Access Point 経由の操作は渡されない。** 遮断されないのはサーバに届かないから
ではなく、そもそも FPolicy の対象になっていないからである。

### 1-7. この測定が答えていないこと

| 問い | 状態 |
|------|------|
| 他の ONTAP バージョンでも同じか | 未測定。`9.18.1P3D1` のみ |
| ONTAP ネイティブ S3（FSx の S3 Access Point ではない S3 サーバ）でも同じか | 未測定 |
| FlexCache のキャッシュ側で発火するか | 未測定。本検証に FlexCache は含まれない |

---

## 2. S3 Access Point 経由の I/O と ONTAP ネイティブ監査ログ

### 2-1. 結論

**S3 Access Point 経由のデータ操作は ONTAP のネイティブ監査ログに記録された。** FPolicy とは
結果が逆である。監査ログ上は `Source` が `HTTP` の監査イベントとして現れ、ファイル名・操作種別・
読み書きのオフセットとバイト数まで記録される。

ただし**要求者の識別情報は記録されない。** これが FPolicy の非発火と並ぶもう 1 つの論点である。

### 2-2. 設定

FPolicy とは別の機構なので、別に有効化する必要がある。ONTAP のファイル監査は監査 ACE
（SACL）が付いたオブジェクトの操作だけを記録するため、監査を有効化しただけでは何も記録されない。

| 設定項目 | 値 |
|---------|-----|
| 監査対象イベント | `file_operations`、`cifs_logon_logoff`、`audit_policy_change` |
| ログ形式 / ローテーション | `xml` / 10 MiB |
| ログ出力先 | 別ボリューム（監査対象ボリュームとは分離） |
| SACL | NTFS ボリューム直下に `Everyone` / `audit_success` / `full_control` を `this_folder` `files` `sub_folders` に適用 |

SACL は REST の `POST /protocols/file-security/permissions/{svm}/{path}/acl` で適用できる。
ジョブとして非同期に走り、完了メッセージに適用ファイル数が入る。

### 2-3. 結果

監査ログを XML として解析し、`Source` と `EventName` で集計した。対照の SMB 操作は
S3 Access Point の操作より後に実行しているため、S3 側の記録が SACL 未設定によるものでないことは
対照が記録されている事実で否定できる。

| Source | 操作 | 監査イベント |
|--------|------|-------------|
| `HTTP`（S3 Access Point） | PUT x4 | `Create Object` (4656) x4 |
| `HTTP`（S3 Access Point） | GET x3 | `Read Object` (4663) x3、`ReadOffset` と `ReadCount` あり |
| `HTTP`（S3 Access Point） | DELETE x1 | `Unlink Object` (9998) x1 |
| `S3`（S3 Access Point） | LIST x3 | `S3A List Object` (4663) x3。オブジェクトではなくボリュームルートに対して記録される |
| `HTTP`（S3 Access Point） | HEAD x6 | **記録なし** |
| `CIFS`（対照） | create / read / delete | 8 件。`Open Object` x2 / `Open Object with Delete Intent` / `Set Object Attributes` x2 / `Get Object Attributes` x2 / `Write Object` |

`Source` の値は 3 種類ある。オブジェクトに対する操作は `HTTP`、LIST は `S3` である。
どの経路も 1 つの値だけで絞り込めない。

HEAD は 6 回発行して 1 件も記録されなかった。ただし「HEAD は監査されない」と断定するには
別バージョンでの確認が必要である。

### 2-3-1. 測定値を 2 度取り違えた経緯

この節の数値は一度誤っていた。原因は 2 つあり、いずれも**取得できなかった分を「無かった」と
報告する**形の誤りである。監査ログを読む手順を作る際の注意点として残す。

| 原因 | 症状 | 対処 |
|------|------|------|
| NFS のクライアント側キャッシュ | 監査ログを NFS 経由で読むと、ONTAP が追記した分が見えない。イベント数が少なく出る | `noac` でマウントし直す。あるいは読む直前に再マウントする |
| 取得経路の出力上限 | ログ全体を base64 で持ち出すと上限で切り捨てられ、**新しいレコードから消える** | 転送前に gzip する。転送後にバイト数を元ファイルと突き合わせる |

いずれの場合も、返ってきた結果自体はエラーにならず、少ない件数として正常に見える。
**イベント数を数える前に、読めたバイト数が元と一致しているかを確認する必要がある。**

### 2-4. 記録される識別情報の違い

同じボリューム・同じ監査設定で、経路によって識別情報が変わる。

| 監査レコードの項目 | S3 Access Point 経由（`Source=HTTP`） | ファイルプロトコル経由（`Source=CIFS`） |
|------------------|--------------------------------------|--------------------------------------|
| `SubjectUserName` | `Not Present` | 実際のユーザ名 |
| `SubjectDomainName` | `Not Present` | 実際のドメイン（SVM のローカルドメイン名） |
| `SubjectUserIsLocal` | `false` | `true` |
| `SubjectIP` | AWS が保有するパブリック IP。呼び出し 1 回ごとに変わり、要求元のアドレスではない | 実際のクライアントの IP |
| `SubjectUnix` | `Uid=65535 Gid=65535` | 実際の UID / GID |
| `SubjectUserSid` | S3 Access Point の identity にマップされた SID | 実際のユーザの SID |

つまり「誰が」と「どこから」が監査ログから復元できない。操作されたファイルと操作種別は分かるが、
要求した IAM プリンシパルにも、要求元のネットワークアドレスにも辿れない。監査ログを
アクセス主体の追跡に使う設計は、S3 Access Point 経由の経路では成立しない。

なお `SubjectIP` に現れるのは S3 のサービス側アドレスであり、ボリュームへの到達経路を示すものでは
あっても、要求者を示すものではない。同一バーストの 8 回の呼び出しで 4 つの異なるアドレスが
記録された。

---

---

## 3. S3 Access Point 経由の I/O と自動ランサムウェア防御（ARP）

### 3-1. 結論

**ARP は S3 Access Point 経由で書き込まれたファイルを検知した。** FPolicy とは異なり、この経路が
見えている。検知理由はファイル内容のエントロピーであり、アクセス経路とは独立している。

このクラスタの ARP バージョンは両ノードで `5.0` である。この世代は学習期間を必要としないため、
有効化した直後から同一セッション内で比較できた。

### 3-2. 測定手順

ARP を有効化した 2 本のボリュームに、それぞれ 1 つの経路だけで同じパターンを書き込んだ。
パターンは「初めて見る拡張子を持つ、高エントロピーのファイルを多数」である。

| ボリューム | 書き込み経路 | 内容 |
|-----------|------------|------|
| A | S3 Access Point のみ | `/dev/urandom` 由来 64 KiB × 150 オブジェクト、拡張子は新規。約 7 分 |
| B | NFSv3 のみ | 同じパターン 150 ファイル。約 2 秒の一括と、約 5 分に分散した 2 回 |

### 3-3. 結果

| ボリューム | 経路 | ARP suspects | 検知理由 | `attack_probability` |
|-----------|------|-------------|---------|---------------------|
| A | S3 Access Point | **150 件** | すべて `High Entropy` | `moderate` |
| B | NFSv3 | **204 件** | すべて `High Entropy` | `moderate` |

A の suspect は `suspect_time` が `00:31:40` から `00:38:32` で、S3 Access Point の書き込み窓
（`00:31:33`–`00:38:28`）と一致する。suspect レコードにはファイルパスと拡張子が入る。

**両経路とも検知された。** ARP はファイル内容を見ているので、経路による差は観測されなかった。

### 3-4. 検知は即時ではなく、短い観測窓は偽陰性を作る

測定中に一度誤った読み取りをした。B の一括書き込み（2 秒で 150 ファイル）の 4 分後に
`attack_probability` を見ると `none` で、「ARP は NFS の短時間バーストを見ていない」と読んだ。
実際には**その 150 ファイルはすべて suspect として記録されていた。** `attack_probability` が
`moderate` に変わったのは書き込みから約 14 分後である。

| 観測対象 | 遅延 |
|---------|------|
| suspect レコード | 最初の書き込みから数秒 |
| `attack_probability` | 書き込み開始から 10 分以上 |

`attack_probability` だけを見て「検知されていない」と判断してはならない。suspect の一覧を
見る必要がある。

### 3-5. この測定が答えていないこと

| 問い | 状態 |
|------|------|
| ARP は S3 Access Point 経由の書き込みを遮断できるか | 未測定。本測定は検知のみで、遮断は試していない |
| 高エントロピー以外の検知理由でも経路差がないか | 未測定。観測されたのは `High Entropy` のみ |
| 他の ONTAP / ARP バージョンでも同じか | 未測定。ARP `5.0` のみ |

## 4. 3 つの機構の対比

| | FPolicy | ONTAP ネイティブ監査ログ | ARP |
|---|---|---|---|
| S3 Access Point 経由の操作を検知するか | **しない** | **する** | **する** |
| S3 Access Point 経由の操作を遮断できるか | **できない**（mandatory + synchronous でも通過する） | 該当なし（監査は遮断しない） | 未測定 |
| ファイルプロトコル経由の操作を検知するか | する | する | する |
| ファイルプロトコル経由の操作を遮断できるか | できる（mandatory モードで実測） | 該当なし | 未測定 |
| 検知の判断材料 | プロトコルと file operation の指定 | SACL の有無 | ファイル内容のエントロピーと拡張子 |
| 監視対象プロトコルの指定 | `cifs` / `nfsv3` / `nfsv4` のみ。S3 に相当する値がない | プロトコル別の指定はなく、SACL の有無で決まる | プロトコル指定はない |
| S3 Access Point 経由の要求者の識別 | 該当なし（通知自体がない） | 記録されない（`Not Present`） | 該当なし（ファイル単位で記録） |
| 検知までの遅延 | 即時（0.3 秒で観測） | ログ書き出しまで数分 | suspect は数秒、`attack_probability` は 10 分以上 |
| 用途 | リアルタイム通知と、mandatory モードでの遮断 | 事後の監査記録 | ランサムウェア様の書き込みパターンの検知 |

「S3 Access Point は S3 API で受けた内容をファイル操作に翻訳するので、監査を含めてバイパスされ
ない」という説明は、**ONTAP ネイティブ監査ログと ARP については成り立つ。FPolicy については
成り立たない。** また監査ログについては、記録されるのは操作であって要求者ではない。

つまり機構ごとに答えが違う。**「ストレージ層の制御は全部効く」も「S3 経路は何も見えない」も
どちらも誤りである。** 何を担保したいかによって、使う機構が変わる。

---

## 5. FPolicy セッションの継続性

### 5-1. 結果 — 72 時間の窓を満たした

窓は満了した。**エンジン IP を固定した構成で、FPolicy の制御チャネルは 72 時間、自発的な切断なしに
維持された。**

| 項目 | 値 |
|------|-----|
| 観測窓 | `2026-08-25T17:16:53Z` → `2026-08-28T17:16:53Z`（起点は scope 変更に伴う再接続の完了時刻。それ以前の窓は無効） |
| 満了確認時刻 | `2026-08-28T17:17:12Z`（経過 72.01 時間） |
| 新規セッションの確立 | **1 件のみ**（起点の 1 件。`socket_timeout=3600`） |
| 自発的な切断（`closed by peer`） | **0 件** |
| ONTAP EMS の `fpolicy.server.disconnect` | **0 件** |
| サーバ側アイドルタイムアウトの発火 | **0 件** |
| サーバプロセスの再起動 / タスク入れ替え | **0 件**（同一タスクが窓の全体で `RUNNING`） |
| ERROR / 例外 | **0 件** |
| KeepAlive | 4,694 行、最大間隔 **120.4 秒**、300 秒超のギャップ **0 件**（エンジン設定 `keep_alive_interval=PT2M` と一致） |
| 窓内のサーバログ総数 | 5,975 イベント |

**「切断されない」と一般化はしない。** 測ったのは 1 回・72 時間・単一構成である。72 時間を超えた
先、エンジン IP を再登録した場合、ロードバランサを挟んだ場合の挙動は、いずれも別の問いとして
残っている。

窓の満了を確認した後、検証環境は削除した。**ログ保持は 30 日だが、ロググループは Fargate スタックの
構成要素なので、スタックの削除と同時に消える。** 後から問い直せるのは、上表の集計と、削除前に取得
した窓全体のサーバログのエクスポート（追跡対象外の作業領域に保管。内部 IP を含むため公開しない）
である。**「環境を消してもログから再検証できる」とは書けない。**

#### 窓の起点を無効にしかけた 2 件

EMS には窓内に 2 件のイベントがある。**どちらも測定対象ではない。**

| 時刻 | イベント | 対象エンジン |
|------|---------|------------|
| `2026-08-25T23:25:56Z` | `fpolicy.server.connectError` | `<unreachable-engine-ip>`（測定対象とは別のアドレス） |
| `2026-08-25T23:55:07Z` | `fpolicy.server.connect` | `<blackhole-engine-ip>`（policy `sync_mandatory_policy`） |

測定対象のタスクの IP（`<verify-engine-ip>`）は、同一 SVM の別エンジン `verify_engine` に登録されて
いる。上の 2 件は mandatory 遮断テスト用の別エンジン `sync_blackhole_engine` に対するもので、IP と
エンジン名の両方で一致しない。サーバ側に追加の接続行が 0 件であることと合わせて、**23:55 の
connect は測定セッションの再接続ではない。**

判定に使ったのはアドレスの値そのものではなく、**EMS が報告したエンジンと、`verify_engine` に登録
されているエンジンが別である**という事実である。そのため上記はプレースホルダで足りる。

これは 5-2 で挙げた交絡の実例である。**片側だけを見れば、この connect を再接続と数えて窓の起点を
2026-08-25T23:55:07Z に動かしていた。**

### 5-2. 交絡要因の潰し方

セッション切断の測定は、同じ見え方をする別の事象と混同しやすい。以下を分離した。

| 混同しうる事象 | 分離の方法 |
|--------------|-----------|
| サーバ自身のアイドルタイムアウトによる close | `SOCKET_TIMEOUT_SEC` を 3600 秒に上げ、タイムアウト時のログを peer からの close と別文言にした |
| Fargate タスクの入れ替え | サーバプロセス起動行を数え、起動があればそれ以降を別の観測窓として扱う |
| エンジン IP 再登録による切断 | IP 更新を自動化しないスタック（NLB なし）を選び、engine IP は手動登録した |
| 自分の設定変更による切断 | ONTAP EMS の `fpolicy.server.disconnect` の reason 文字列で判定する |

実際にこの分離が効いた例がある。測定開始前の切断が両ノードで記録されているが、EMS の reason は
`FPolicy server is removed from external engine.` であり、これは event を追加するために policy を
disable した操作そのものである。サーバ側ログだけを見ていれば、これを自発的な切断として
数えていた。

### 5-3. 両側の記録を突き合わせる手順

`shared/scripts/fpolicy-session-report.py` が、サーバ側の CloudWatch Logs と ONTAP 側の EMS を
同じ窓で読み、セッション継続時間・切断の主体・KeepAlive の最大間隔・切断理由を出力する。

```bash
# Tunnel to the ONTAP management endpoint through the bastion
aws ssm start-session --region ap-northeast-1 --target <bastion-instance-id> \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["<ontap-mgmt-ip>"],"portNumber":["443"],"localPortNumber":["8443"]}'

# Correlate both sides over the 72-hour window
python3 shared/scripts/fpolicy-session-report.py \
  --log-group /ecs/fpolicy-s3ap-verify \
  --since 2026-08-25T17:16:53Z \
  --ontap-url https://127.0.0.1:8443 \
  --keepalive-gap-threshold 300
```

長時間の観測に常駐プロセスは不要である。切断はサーバログの 1 行として CloudWatch に残り、
理由は ONTAP EMS に残る。どちらも後から同じ窓を問い直せる。ログ保持は 30 日である。

不在時の検出には、`Connection closed by peer` にマッチするメトリクスフィルタと、それに対する
アラーム `fpolicy-s3ap-verify-session-dropped` を置いた。アラームが鳴っても、EMS の reason を
読むまでは自発的な切断と断定できない。

### 5-4. 周期性の判定基準

「特定の時間・日数で切れる」を主張するには切断間隔が 2 つ以上必要である。1 回目で止めると
間隔が 1 つも得られない。したがって切断を検出しても測定は止めず、2 回目までの間隔を測る。
72 時間で 0 件だった場合に書けるのは「72 時間では発生しなかった」であり、「切断されない」ではない。

---

## 6. 観測のための変更

既存コードに加えた差分は次のとおりで、いずれも既定値は従来の挙動を保つ。

| 変更 | 理由 |
|------|------|
| `SOCKET_TIMEOUT_SEC` 環境変数（既定 300 秒） | サーバ側タイムアウトが ONTAP 側の切断と区別できなくなるのを防ぐ。セッション継続時間を測るときだけ大きくする |
| close 時に `uptime` と KeepAlive / event / その他の件数を出力 | 静かだったセッションと終わったセッションを読み分けるため |
| peer からの close とサーバ側タイムアウトを別文言に | 同上。同じ文言だと後から集計できない |
| KeepAlive に連番と前回からの経過秒を付与 | ログが途切れた区間の長さを後から確定するため |
| 通知から `protocol` と client IP を抽出してログと SQS ペイロードに載せる | 通知がどのプロトコルに帰属したかを記録するため |
| 未知のメッセージ型はヘッダ全文と本文先頭を出力 | 想定外の通知が来た場合、ヘッダ自体が結果になるため切り詰めない |
| テンプレートの `AssignPublicIp` をパラメータ化（既定 `DISABLED`） | NAT も interface endpoint もない VPC で egress を確保するため |

`protocol` は本検証の NFSv3 / SMB 通知ではいずれも `unreported` だった。ONTAP がこの
プロトコルバージョンの XML 本文にプロトコル名を載せていないためで、抽出側の不具合ではない。

---

## 7. 付随して判明したこと

| 事項 | 内容 |
|------|------|
| WINDOWS identity の S3 Access Point にドメインアカウントは不要だった | SVM のローカル SMB ユーザ名を指定して作成できた。ドメインコントローラに到達できない状態でも作成でき、データプレーンも動作した。AD 参加は SVM 側の設定として残っている |
| S3 Access Point の UNIX identity に `fsxadmin` は使えない | 作成が `FAILED` になり、理由は `Failed to lookup the provided user in ONTAP` である。`fsxadmin` はクラスタ管理アカウントで、SVM の名前サービス上の UNIX ユーザではない |
| S3 Access Point のアタッチは SVM 上に ONTAP の S3 サーバを立てる | アタッチ済みの SVM は `s3.enabled=true` で、サーバ名は `amazon-fsx-<svm>.<region>.amazonaws.com` の形になる |
| KeepAlive の実測間隔は 120 秒である | 過去の記録にある「約 6 秒間隔」は再現しなかった。10 秒間隔で届くのは `status_request_interval=PT10S` の STATUS_REQ であり、KeepAlive ではない |
| 作成 API が失敗した S3 Access Point もデタッチが必要 | `FAILED` 状態でもアタッチメントとして存在するため、作り直す前に `detach-and-delete-s3-access-point` が必要である |
| 通知はファイル名では対応づけられない | S3 Access Point で書いたファイルを後からファイルプロトコルで読むと、そのファイル名の通知が読み取り時刻に発生する。ファイル名で対応づけると S3 側の書き込みが発火したように見える。時刻で対応づける必要がある |
| ローカル SMB ユーザのグループ追加はグループ名ではなく SID で指定する | `BUILTIN\Administrators` をパスに入れると 404 になる。`S-1-5-32-544` を使う |
| 監査ログの件数は数え方で変わる | 行単位の `grep -c` と `<Event>` 要素の抽出は一致しないことがある。集計は要素抽出で行い、読めたバイト数を元ファイルと突き合わせる |
| 本リポジトリの監査ログパーサに 3 つの欠陥があった | 実際の監査レコードを通したことで判明した。いずれも本検証の前から存在していた。(1) クライアント IP を `IpAddress` から取ろうとしていたが、ONTAP が出すのは `SubjectIP` なので**全イベントで空だった**。(2) 操作名を `ObjectType` から取っていたため、ファイル操作はすべて `File` になり実際の操作名が失われていた。(3) `Source` を保持していなかったため、S3 アクセス経路のイベントとファイルプロトコルのイベントを区別できなかった。加えて `SubjectUserName` の `Not Present` がそのまま利用者名として通っていた |
| 既存テストが実在しないフィールド名を前提にしていた | パーサのテストは `IpAddress` や、操作名が入った `ObjectType` を使っていた。ONTAP はどちらも出さない。**テストが通っていたのは、実測値ではなく想定したスキーマを検証していたためである。** 実測したレコードを固定値として持つテストを追加した |

---

## 8. AWS サポートへ確認する事項

本検証で確定した挙動のうち、仕様として明文化されているかを確認すべき点と、改善を要望すべき点を
分けて整理した。文面は `docs/en/support-inquiry-s3ap-audit-coverage.md` にある。

| 分類 | 内容 |
|------|------|
| 仕様確認 | S3 Access Point 経由の操作が FPolicy 通知を発火しないのは意図された動作か。ドキュメントに記載があるか |
| 仕様確認 | ONTAP 監査ログの `SubjectUserName` / `SubjectDomainName` が `Not Present` になり、`SubjectIP` が要求者ではなく AWS のサービス側アドレスになるのは意図された動作か |
| 仕様確認 | HEAD 要求に対応する監査イベントが出ないのは意図された動作か |
| 改善要望 | FPolicy が S3 アクセス経路の操作も監視できるようにすること（ランサムウェア検知や DLP をリアルタイムに行う構成が S3 Access Point 経由の書き込みを見落とすため） |
| 改善要望 | 監査ログに要求元の IAM プリンシパルと実際のソース IP を記録すること |

---

## 9. テアダウン順序

```bash
# 1. Disable FPolicy and auditing before deleting anything
#    fpolicy policy disable -> delete engine -> delete events; audit disable
# 2. Delete the Fargate stack
aws cloudformation delete-stack --stack-name fpolicy-s3ap-verify --region ap-northeast-1
# 3. Detach both S3 Access Points (mandatory before deleting the volumes)
aws fsx detach-and-delete-s3-access-point --name fpolicy-verify-ap --region ap-northeast-1
aws fsx detach-and-delete-s3-access-point --name fpolicy-verify-ap-win --region ap-northeast-1
# 4. Delete the three test volumes
# 5. Unmount NFS and SMB, delete the local SMB user, its secret and the scoped IAM policy
# 6. Delete the metric filters and the alarm
```

S3 Access Point がアタッチされたままボリュームを削除しようとすると失敗する。
また FPolicy policy を有効なまま外部サーバを消すと、ONTAP 側は接続先を失ったまま
`fpolicy.server.disconnect` を出し続ける。監査ログ出力先のボリュームは、監査を無効化してから
削除する。
