# 検証済みクリーン復旧ポイントガイド — CSF 2.0 RC.RP のギャップを埋める

🌐 **日本語**（このページ） | [English](../en/verified-recovery-point-guide.md)

## エグゼクティブサマリ

[サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md#recover復旧) では、本リポジトリがこれまで対応していなかったギャップを明確に指摘していました。保護 Snapshot が存在すること自体は **Protect** フェーズのエビデンスであり、その Snapshot が実際にクリーンで復旧に使えることの証明にはなりません。NIST CSF 2.0 の **RC.RP**（Incident Recovery Plan Execution）サブカテゴリが信頼できると言えるのは、復旧ポイントが実際にテストされ、侵害されていないことが確認された場合のみであり、単にその存在が確認されたことではありません。

本ガイドは、この未対応だった検証ステップを AWS ネイティブサービスのみで実装します。

1. **FlexClone**: 検証対象の Snapshot を読み書き可能なクローンとして複製します（ONTAP REST API）。本番ボリュームや元の Snapshot には一切触れません。
2. **VPC 限定の S3 Access Point** をクローンに接続します（AWS FSx API）。NFS/SMB でマウントせずに S3 API 経由でクローンのファイルを公開するため、検証処理は本番データプレーンへのネットワーク経路を一切持ちません。
3. **クローンのファイル一覧をスキャン**します（S3 Access Point 経由の `ListObjectsV2`）。ランサムウェアに関連するファイル拡張子を検出する高速な事前フィルタであり、ONTAP ARP のエントロピー分析の代替ではありません。
4. **clean/suspicious/error の判定結果を DynamoDB に記録**し（任意で SNS 通知も送信）、結果に関わらず S3 Access Point と FlexClone を**常に削除**します。

> **スコープに関する注記**
>
> 本ガイドが埋めるのは RC.RP の検証ギャップに限定されます。ONTAP ARP（攻撃発生中に本番ボリュームに対してランサムウェアを検知する仕組み）や、[自動インシデント対応ガイド](automated-response-guide.md)の Respond フェーズのブロック機能を置き換えるものではありません。本ガイドが答えるのは別の、より後段の問いです — 「復旧ポイントとして採用しようとしている、この特定の Snapshot は、実際にクリーンなのか？」

> **デプロイ検証に関する注記**
>
> 本ワークフローは ONTAP 9.17.1P7D1（FSx for ONTAP、ap-northeast-1）に対して Step Functions ワークフロー全体のエンドツーエンドで検証済みです（2026年7月）。`CreateFlexClone` → `WaitForFsxSync` → `WaitForFsxDiscovery` → `AttachAccessPoint` → `ScanForIndicators` → `RecordVerdict` → `Cleanup` の全ステートが正常完了し、`SUCCEEDED` ステータスを確認しています。検証実行の結果: `verdict: suspicious`（テストデータにランサムウェア関連拡張子のファイルが含まれていたため）、`cleaned_up: true`。以下の知見はこの E2E 検証から得られたものです: FSx for ONTAP 同期遅延の実測（12-36分）、AD DC 到達不能時の AccessDenied パターン、Internet-origin AP への設計変更の経緯、`put_access_point_policy` 廃止の理由。

**主要機能:**
- FlexClone ベースの検証により本番ボリュームへの影響ゼロ（copy-on-write、クローンに対する読み取り専用処理のみ）
- VPC 限定の S3 Access Point — クローンの内容がインターネットから到達可能になることはない
- ランサムウェア痕跡の高速な拡張子ベース事前フィルタ
- Step Functions の `Catch` により、失敗時も含めてクリーンアップを保証 — クローンや Access Point が残置されることはない
- 全検証実行を DynamoDB 台帳に記録し、監査時の CSF 2.0 RC.RP エビデンスとしても機能

**実行タイミング:**
- [自動インシデント対応ガイド](automated-response-guide.md)の `create_snapshot` アクションが発火した後、その Snapshot を復旧ポイントとして信頼する前に
- 定期的な保護 Snapshot / スケジュール Snapshot に対して定期実行し、復旧レディネスの継続チェックとして
- 計画的な DR テストやコンプライアンス監査の前に、手動で「テスト済み」の復旧ポイントであることのエビデンスを取得するために

**自社導入がうまくいっているかの見分け方**: 導入が成功している状態とは、インシデント起因・スケジュール起因を問わず全ての Snapshot がリトライ予算のウィンドウ内で台帳エントリを獲得し、（判定結果が clean かどうかだけでなく）全ての実行で `cleaned_up: true` になっている（この特定のフィールドを見る理由は上記ステップ5の修正済みバグに関する補足を参照）、そして実行の間に孤立した FlexClone ボリュームが `aws fsx describe-volumes` に一切蓄積していない状態です。この 3 つの指標は、実際にインシデントが発生した後だけでなく、本番運用開始後の最初の数週間から追跡してください。

> **位置づけに関する補足**
>
> ランサムウェアレジリエンスのために FSx for ONTAP を評価している読者に本ガイドを説明する際、正確な一文での位置づけは「人間がリストアサイクルを無駄にする前に、明らかに侵害された Snapshot を除外する自動化された事前フィルタであり、Snapshot がマルウェアを含まないことの証明書ではない」です。エンゲージメントの対話や RFP 回答において、「clean」判定を完全なフォレンジック証明と同等のものとして提示しないでください — 本ガイド内の他の Resilience-maturity や 脅威インテリジェンスに関する補足が、その位置づけがなぜ機能を過大に見せることになるかを正確に説明しています。正確でありながら十分に説得力のある主張は、自動化とエビデンストレイル自体です — これは、以前は存在しなかったギャップ（検証済みでクリーンな復旧ポイントのワークフローがなかった）を AWS ネイティブサービスと DynamoDB の監査証跡で解消するものであり、スキャンの深さを過剰に売り込む必要のない、意味のある能力です。

---

## アーキテクチャ

```
+-------------------------------------------------------------------+
| Step Functions: 検証済みクリーン復旧ポイントワークフロー            |
+-------------------------------------------------------------------+
|                                                                   |
|  CreateFlexClone (ONTAP REST API)                                 |
|       |                                                           |
|       v                                                           |
|  WaitForFsxDiscovery (FSx describe-volumes ポーリング)            |
|       |                                                           |
|       v                                                           |
|  AttachAccessPoint (AWS FSx API, VPC 限定)                        |
|       |                                                           |
|       v                                                           |
|  ScanForIndicators (Access Point 経由の S3 ListObjectsV2)          |
|       |                                                           |
|       v                                                           |
|  RecordVerdict (DynamoDB + 任意で SNS)                             |
|       |                                                           |
|       v                                                           |
|  Cleanup (S3 AP デタッチ + FlexClone 削除)  <-- 必ず実行            |
|                                                                   |
|  (いずれかのステップで失敗) --> CleanupAfterError                  |
|                              --> RecordErrorVerdict --> Fail       |
+-------------------------------------------------------------------+
```

![Step Functions グラフビュー — 実行成功](../screenshots/automated-response/stepfunctions-graph-view-succeeded.png)

*Step Functions コンソール: 全ステート成功（緑）。検証済みクリーン復旧ポイントワークフローがエンドツーエンドで完了 — FlexClone 作成、FSx discovery、S3 Access Point アタッチ、ランサムウェア指標スキャン、判定記録、クリーンアップ全て正常終了。ONTAP 9.17.1P7D1（2026年7月）で E2E 検証済み。*

設計上の重要な選択: **クリーンアップは成功・失敗どちらの経路でも必ず実行されます**。Step Functions の `Catch` ブロックは、どのエラーも正常系と同じ Cleanup Lambda に直接ルーティングします。そのため、ワークフローの途中で失敗しても、FlexClone ボリュームや S3 Access Point がストレージを消費したり不要なアクセス面を残したりすることはありません。

> **WaitForFsxDiscovery が存在する理由**: ONTAP REST API は FlexClone を即座に作成しますが、FSx API がそれを発見するまで「数分かかる場合がある」（[AWSドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)参照）。この専用のポーリングステップがその同期遅延を吸収し、AttachAccessPoint は S3 Access Point のライフサイクルのみに集中できます。

> **図の説明（テキストによる代替）**
>
> Step Functions ステートマシンは 6 つのステートを順に実行します — `CreateFlexClone`（ONTAP REST API）→ `WaitForFsxDiscovery`（FSx `describe-volumes` をポーリングしクローンが見えるまで待機）→ `AttachAccessPoint`（AWS FSx API、VPC 限定）→ `ScanForIndicators`（Access Point 経由の S3 `ListObjectsV2`）→ `RecordVerdict`（DynamoDB、任意で SNS）→ `Cleanup`（S3 Access Point のデタッチと FlexClone の削除。このステートは常に実行されます）。最初の 4 ステートのいずれかが失敗すると、制御は `CleanupAfterError` に移り、同じ `Cleanup` Lambda を呼び出した後、`RecordErrorVerdict` が失敗結果を台帳に記録し、実行は `Fail` ステートで終了します。

---

## 検証の仕組み

### ステップ1: FlexClone の作成

[FlexClone](https://aws.amazon.com/fsx/netapp-ontap/features/) は、親ボリュームと copy-on-write でデータブロックを共有する、その時点の書き込み可能なコピーです。作成はほぼ即時で、クローンへの書き込みが発生するまで追加のストレージを消費しません（本ワークフローはクローンへの読み取りのみを行うため、追加ストレージは発生しません）。親ボリュームのストレージ効率化機能（重複排除、圧縮、シンプロビジョニング）はクローンにも自動的に継承されます — クローン側で個別に設定する必要はありません。親の既に重複排除・圧縮済みのブロックを共有するだけで、独自のブロックを新たに割り当てるわけではないためです。

```
POST /api/storage/volumes
{
  "name": "verify_vol_data_20260710_143022",
  "svm": {"name": "svm-prod-01"},
  "clone": {
    "parent_volume": {"name": "vol_data"},
    "parent_snapshot": {"name": "incident_response_20260708_143022"},
    "is_flexclone": true
  }
}
```

これは非同期ジョブを返します。Lambda は `GET /api/cluster/jobs/{uuid}` を `state: success` になるまでポーリングし、その後クリーンアップ用にクローンの ONTAP ボリューム UUID を解決します。

> **並行実行に関する補足**
>
> `clone_name` は `verify_{volume_name}_{timestamp}` から生成され、`timestamp` は 1 秒単位の精度です（`%Y%m%d_%H%M%S`）。同じ `volume_name` に対して同じ 1 秒以内に開始された 2 つの Step Functions 実行 — 例えばスケジュール実行と手動トリガーが同時に発火した場合 — は、同一名の ONTAP ボリュームを作成しようとし、2 回目の `POST /storage/volumes` 呼び出しは処理を継続できずに名前の衝突エラーで失敗します。本ワークフローには、これを防ぐための実行レベルのロック（DynamoDB の条件付き書き込みや Step Functions の名前重複排除など）はありません。実際にはこの衝突ウィンドウは狭く、トリガーパターンとしても頻度は高くありませんが、同じボリュームに対して複数の独立したトリガー（例: スケジュールと SOAR プレイブックの両方）から本ワークフローを呼び出す場合は、同一秒での衝突を避けるため、識別用のサフィックス（短いランダムトークンや呼び出し元の実行 ID など）を追加することを検討してください。

### ステップ2: FSx API によるクローン発見待ち（WaitForFsxDiscovery）

ONTAP REST API でクローンが作成された後、FSx API がそのボリュームを発見するまでには「数分かかる場合がある」ことが [AWS ドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)に明記されています。本プロジェクトの実測では 12〜36 分の範囲で変動しています。

この専用ステップが `fsx describe-volumes` をポーリングし、クローンが `CREATED` または `AVAILABLE` ステータスで見えるまで待機します。

![Step Functions AttachAccessPoint — FsxDiscoveryPending リトライ中](../screenshots/automated-response/stepfunctions-execution-running.png)

*Step Functions コンソール: AttachAccessPoint のステート詳細。State input にはクローン名・UUID・親ボリューム・親 Snapshot が表示される。`FsxDiscoveryPending` エラーが発生し、Step Functions の Retry ブロックが再呼び出しを行っている。*

```python
# WaitForFsxDiscovery Lambda (概略)
paginator = fsx.get_paginator("describe_volumes")
for page in paginator.paginate(
    Filters=[{"Name": "file-system-id", "Values": [file_system_id]}]
):
    for volume in page.get("Volumes", []):
        if volume.get("Name") == clone_name and volume.get("Lifecycle") in (
            "AVAILABLE", "CREATED",
        ):
            return {"fsvol_id": volume["VolumeId"], ...}

raise FsxDiscoveryPending(f"Clone volume '{clone_name}' not yet visible via FSx API")
```

Step Functions のリトライ設定（120 秒間隔、最大 25 回試行、10 分の静的 Wait の後）が、FSx 同期の遅延を吸収します。各リトライは数百ミリ秒の短い Lambda 呼び出しで、Lambda 自身のタイムアウト（15 分制限）にブロックされません。総バジェット: 10 分（Wait）+ 50 分（ポーリング）= 60 分。

### ステップ3: S3 Access Point の接続

クローンは **Internet-origin の S3 Access Point** 経由で公開されます。VPC-origin AP は Lambda からの `ListObjectsV2` で AccessDenied を引き起こすことが E2E 検証で確認されたため、Internet-origin を採用しています（参照: [FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns) で検証済みのパターン）。

```python
fsx.create_and_attach_s3_access_point(
    Name="verify-vol-data-20260710-143022",
    Type="ONTAP",
    OntapConfiguration={
        "VolumeId": fsvol_id,  # DescribeVolumes で解決(ONTAP UUID ではない)
        "FileSystemIdentity": {"Type": "UNIX", "UnixUser": {"Name": "root"}},
    },
    # VpcConfiguration は指定しない = Internet-origin
    # AP リソースポリシーも設定しない — 同一アカウントでは IAM identity policy で十分
)
```

> **Internet-origin を選択した理由と設計判断**
>
> VPC-origin AP（`S3AccessPoint={"VpcConfiguration": {"VpcId": vpc_id}}`）を使えばネットワーク層で追加の保護が得られるのは確かです。しかし本プロジェクトの E2E 検証で以下が確認されました:
> 1. VPC 内 Lambda + S3 Gateway EP 経由で VPC-origin AP に `ListObjectsV2` すると AccessDenied が返る（ポリシー全開放でも同じ）
> 2. 同じ環境で Internet-origin AP + VPC 外 Lambda では正常動作
> 3. [FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns) リポジトリでも「VPC-external Lambda for Internet-origin S3AP」が検証済みパターン
>
> Internet-origin AP のセキュリティは IAM + S3 Block Public Access（FSx for ONTAP AP では強制有効・変更不可）で担保されます。匿名アクセスは不可能です。
>
> **AP リソースポリシーを設定しない理由**: 同一アカウント内のアクセスでは、IAM identity policy のみで S3 AP データ操作が通ることを確認しました。`s3control.put_access_point_policy()` は不要であり、FSx for ONTAP AP に対して呼び出すと AccessDenied を引き起こす場合があることも E2E で観測されています。

> **ONTAP UUID から fsvol-id への解決について — 実測した遅延とリトライ設計**
>
> AWS FSx は ONTAP REST API 経由で作成されたボリュームを非同期に検出します。ONTAP ボリューム UUID を対応する `fsvol-xxxx` ID に直接マッピングする API は存在しません。AWS 公式ドキュメントには、この同期に「数分かかる場合がある」と明記されています（[Managing FSx for ONTAP resources using NetApp applications](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)）。これは、ONTAP 自身の[非同期 REST API ジョブモデル](https://docs.netapp.com/us-en/ontap-automation/rest/asynchronous_processing.html)（上記ステップ1のような `POST` は job UUID 付きの HTTP 202 を返し、呼び出し側は `GET /cluster/jobs/{uuid}` をポーリングしてジョブ自身が `success`/`failure` に解決するまで待つ）とは別の関心事です — `CreateFlexClone` は、このステップが実行される前に、既に ONTAP ジョブが完了するまでポーリングしています。つまり `AttachAccessPoint` が実行される時点では、ONTAP 側の操作は既に成功しています。以下で説明する遅延は、その*後*に起きることです: FSx 自身の ONTAP ボリューム一覧が、ONTAP が既に行った変更にまだ追いついていないのです。本プロジェクト自身のエンドツーエンド検証では、観測ギャップを挟まない連続ポーリングにより、同じ（他にアイドル状態の）ファイルシステムに対する 3 回の別実行で、この遅延を直接計測しました — 1 回目は**約 12 分**、2 回目は**約 24 分**、3 回目は**約 36 分**。単発のばらつきではなく実行を重ねるごとに増加するパターンですが、データ点が 3 つしかないため、これが厳密に周期的なものか単なる偶然かは確証できていません。単一の数値そのものより、この変動とその傾向自体が運用上重要な事実です。この理由から、`AttachAccessPoint` は呼び出しごとに 1 回だけ確認（`DescribeVolumes` の後 `DescribeS3AccessPointAttachments` を照会）し、まだ準備できていない場合は `FsxDiscoveryPending`/`S3AttachPending` を発生させます — **Step Functions ステートマシン自身の `Retry` ブロック**（Lambda 内のループではなく）が、スケジュールに従ってこれを再呼び出しします（初回間隔 30 秒、バックオフ率 1.25、最大 150 秒でキャップ、最大 28 回試行 — 合計で約 60 分の予算があり、実測した 3 回のうち最も遅かったものに余裕を持たせたサイジングですが、自社の環境の遅延がさらに長くなる場合、この予算でも十分とは保証できません）。この設計上の選択は運用上重要です: 各リトライはそれぞれ数百ミリ秒の短い Lambda 呼び出しであり、Lambda 自身の最大タイムアウト（15 分。実測した遅延はこれに近づく、あるいは超えることがあります）にブロックされる単一の Lambda ではありません。また、リトライスケジュール全体が Step Functions の実行履歴上で可視化されており、sleep ループの中に隠れることもありません。

Access Point は `CREATING` → `AVAILABLE`（エラー時は `FAILED`/`MISCONFIGURED`）と遷移します。上記の Retry による再呼び出しは、終了状態になるまで `DescribeS3AccessPointAttachments` をポーリングします。`AttachAccessPoint` 自身の「既にリクエスト済みか」の再確認（`Filters` ではなく `Names` パラメータを使った `DescribeS3AccessPointAttachments` — この特定の API では `Filters` を使うと `BadRequest: Request failed validation` が返ることを実際の API で確認済み）により、各リトライ試行はべき等になっています: Step Functions は失敗したタスクを毎回*元の*入力で再呼び出しし、前回の部分的な結果をマージすることはないため、Lambda は「既に作成済みである」という情報を引き渡された入力から得ることができません — 代わりに、呼び出しごとに AWS API からその事実を再取得します。

### ステップ3: ランサムウェア痕跡スキャン

スキャンは Access Point 経由でクローンのオブジェクトを一覧化し（`ListObjectsV2`）、ランサムウェアファミリーが付与することの多いファイル拡張子（`.encrypted`、`.locked`、`.crypt`、`.wcry`、`.locky` など）を検出します。Snapshot が **suspicious（疑わしい）** と判定されるのは、以下の両方を満たす場合のみです。

- 疑わしいオブジェクト数が `SuspiciousMinCount`（デフォルト 20）以上 — 少数のファイルが偶然この拡張子を持つ小規模ボリュームでの誤検知を回避
- 疑わしい比率が `SuspiciousRatioThreshold`（デフォルト 5%）以上

> **レジリエンス成熟度に関する補足**
>
> これは意図的に粗く高速な事前フィルタであり、攻撃発生中に本番ボリュームに対して動作する [ONTAP ARP](arp-incident-response-guide.md) のファイル内容エントロピー分析の代替ではありません。本スキャンが答えるのは、より狭く、より後段の問いです — 「この特定の Snapshot は、ランサムウェアによってリネームされたファイルが多数を占めるボリュームを捉えているように見えるか」。ここでの「clean」判定は RC.RP のエビデンスにはなりますが、汎用的なマルウェアスキャンではなく、ファイルの *内容* は検査しません（データ分類を目的とした補完的な内容スキャン機能については、[コンテンツレベル PII 分類スキャナー](content-classification-scanner.md)を参照してください — こちらはランサムウェア検知ではなく別の課題に対応するものです）。

> **スケールに関する補足**
>
> このスキャンは `list_objects_v2` のページネーター（1 ページあたり 1000 キー）を使用し、判定結果を計算する前に全ページを走査します — 早期終了やサンプリングはありません。オブジェクト数が非常に多いボリュームでは、`ScanForIndicators` の実行時間はオブジェクトの総数に比例してスケールし、「suspicious」パターンがどれだけ早く明らかになるかとは無関係です。`StepTimeoutSeconds`（デフォルト 180 秒）は、他の 4 つの Lambda と同様に、この Lambda 自身の `Timeout` プロパティとして設定されます（Step Functions レベルの `TimeoutSeconds` ではありません）— 一覧取得とキーごとの拡張子チェックの合計がこのウィンドウを超えるほどオブジェクト数が多い場合、部分的な判定結果を返すのではなく Lambda 自体がタイムアウトし、その結果生じるタスク失敗がそのステートの `Catch: States.ALL` ブロックで捕捉されます。このステートには `Retry` ブロックがないため（テスト節のリトライポリシーに関する補足を参照）、ここでのタイムアウトは初回発生時点で直接 `CleanupAfterError` に進みます。数百万オブジェクト規模のボリュームから Snapshot を検証する場合は、代表的なボリュームに対して `ScanForIndicators` の実際の実行時間を計測してからデフォルトのタイムアウトに依拠し、必要であれば `StepTimeoutSeconds` を引き上げてください。

> **脅威インテリジェンスに関する補足**
>
> `SUSPICIOUS_EXTENSIONS` は、既知のランサムウェアファミリーに歴史的に関連付けられている拡張子（`.locky`、`.wcry`、`.cerber` など）の固定リストです。これはシグネチャベースのアプローチであり、シグネチャベースならではの盲点を引き継いでいます — ランダムまたは被害者固有の拡張子を付与するランサムウェア（拡張子ベースの検知を回避するために、まさにこの目的で最近の攻撃キャンペーンで増加傾向にあるパターン）は、このリストのどの項目にも一致しないため、そのようなバリアントによって暗号化された Snapshot でも、本スキャン単独では「clean」判定を受ける可能性があります。このリストは新しいランサムウェアファミリーに合わせて自動的に更新される仕組みではありません — 出荷時のリストを網羅的なものとして扱わず、最新の脅威インテリジェンス（SIEM ベンダーの脅威フィードやメンテナンスされている公開リストなど）と照らし合わせて `SUSPICIOUS_EXTENSIONS` を定期的に見直し・拡張してください。これは、ここでの「clean」判定が事前フィルタであり保証ではない、（上記で既に述べた理由に加えた）2 つ目の独立した理由です — [ONTAP ARP](arp-incident-response-guide.md) のエントロピーベース検知は特定の拡張子を認識することに依存しないため、まさにこの盲点に対する意味のある補完となります。

### ステップ4: 判定結果の記録

clean、suspicious、error のいずれであっても、全ての実行結果は DynamoDB 台帳テーブル（パーティションキー `snapshot_key` = `{svm}/{volume}/{snapshot}`、ソートキー `started_at`）に記録されます。これにより、どの復旧ポイントがいつ検証され、どのような結果だったかをクエリ可能な履歴として保持できます。このテーブルが、監査時に RC.RP のエビデンスとして提示するアーティファクトになります。

> **データ最小化に関する補足**
>
> 台帳アイテム自体には、フラグが立てられたファイルパスの一覧は保存されません — 保存されるのは `suspicious_object_count` と `suspicious_ratio`（集計された数値）のみです。実際の `suspicious_objects` 配列（`to_dict()` で先頭 50 件にキャップされます — テスト節のフルオーケストレーションの行を参照）は、出荷時点の実装では `RecordVerdict` の `PutItem` 呼び出しに一切渡されません。これは、Security Considerations にある監査証跡の完全性に関する補足の逆方向の事実です: CloudWatch Logs（ステートマシンの `IncludeExecutionData: true` 経由）はファイルパスを含む実行ごとの完全なペイロードを保持しますが、本ガイドが恒久的な RC.RP エビデンスとして提示する DynamoDB 台帳にはそれが含まれません。CloudWatch Logs に頼らずにコンプライアンスやフォレンジックのクエリでフラグ付きファイルパスを利用可能にする必要がある場合は、`RecordVerdict` の `item` dict にそのフィールドを明示的に追加する必要があります — その際は、監査証跡の完全性に関する補足がログについて指摘している「機微なパスがより長期保持されるストアに残る」という懸念と比較検討してください。

### ステップ5: クリーンアップの保証

Cleanup Lambda は設計上べき等的に振る舞います。`access_point_name` や `fsvol_id` が欠落している場合（例: 早期の失敗によりクリーンアップ実行時にまだそれらのリソースが存在しない場合）は no-op として扱われ、エラーにはなりません。S3 Access Point のデタッチと FlexClone の削除は、いずれも「既に削除済み」（404 / `NotFound`）の応答を許容します。

> **FlexClone の削除は ONTAP REST API ではなく FSx API 経由で行います — これは見落としではなく、実測に基づく意図的な設計判断です**
>
> 本ワークフローの以前のバージョンでは、`CreateFlexClone` が使うのと同じ ONTAP REST API に対して `DELETE /storage/volumes/{uuid}` を直接呼び出してクローンを削除していました。これは、Amazon FSx のバックアップ管理下にある親ボリュームから派生した FlexClone ボリュームに対しては失敗します。AWS 公式ドキュメントには次の記載があります: 「[Amazon FSx バックアップは SnapMirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/cannot-delete-svm.html) を使用して、ファイルシステムのボリュームの時点増分バックアップを作成します。バックアップ用のこの SnapMirror リレーションシップは、ONTAP CLI から削除することはできません。しかし、このリレーションシップは、AWS CLI、API、またはコンソールを通じてボリュームを削除すると自動的に削除されます。」本プロジェクト自身のエンドツーエンド検証では、ONTAP の DELETE 呼び出しが HTTP 202（ジョブ受付）を即座のエラーなしに返しましたが、その裏側の ONTAP ジョブは後に `"state": "failure"` に確定し、具体的なエラー `Volume "..." is the destination or source endpoint of one or more SnapMirror relationships`（ONTAP エラーコード 917858）を伴いました。この失敗は、最初のリクエストの HTTP ステータスだけを確認し、ジョブの完了を追跡しないコードには一切見えません。現在の実装は `fsx.delete_volume(VolumeId=fsvol_id)`（ONTAP の API ではなく FSx の API）を呼び出しており、これは SnapMirror の解体を内部で処理し、本プロジェクトの実測では約 6 分で完了しました。これに伴う結果の一つ: `Cleanup` は今、`CreateFlexClone` からの ONTAP `volume_uuid` だけでなく、`fsvol_id`（`AttachAccessPoint` が FSx でクローンを発見した時点で設定される）を必要とします。ワークフローの実行が `AttachAccessPoint` の完了*前*に失敗した場合、`fsvol_id` は一度も設定されず、`Cleanup` は FSx API 経由でクローンを削除できません — この特定のギャップへの対処方法は、下記の孤立クローンに関する補足を参照してください。

> **孤立クローンに関する補足（fsvol_id が欠落している場合）**
>
> `CreateFlexClone` が成功したものの、`AttachAccessPoint` が `fsvol_id` を解決する前にワークフローが失敗した場合（例: 28 回のリトライ試行すべてを使い切った、または無関係なエラーが発生した場合）、`Cleanup` は警告をログに記録し、ONTAP API 経由の削除を試みることなく `cleaned_up: false` を報告します — そのパスは FSx バックアップ管理下のボリュームに対して失敗することが分かっているためです（上記参照）。クローンボリュームは ONTAP 内に残りますが、意味のある追加ストレージは消費しません（copy-on-write）が、孤立したリソースとして存在します。本プロジェクト自身の検証でも、まさにこのケースに遭遇しました — 当時有効だったリトライ予算を上回る FSx 検出遅延が発生した実行で、クローン作成から約 35〜37 分後に `describe-volumes` で初めて確認できる孤立クローンが残り、ワークフロー自体はそれよりずっと前に error 判定を記録して処理を終えていました。FSx がそれを発見した後に手動で解決するには: `aws fsx describe-volumes --filters Name=file-system-id,Values=<対象のファイルシステムID> --query "Volumes[?starts_with(Name,'verify_')]"` で名前から `fsvol-xxxx` ID を見つけ、`aws fsx delete-volume --volume-id <fsvol-id>` を実行してください。非常に長い時間（約 60 分のリトライ予算を大幅に超える時間）が経過してもクローンが `describe-volumes` に現れない場合、それ自体が本プロジェクトで観測した FSx for ONTAP 同期遅延を超えた何かを示唆しており、エスカレーションする価値があります。

> **修正済みバグ — 成功した `AttachAccessPoint` の出力から `fsvol_id` が静かに欠落していた**
>
> 本テンプレートの以前のバージョンの `AttachAccessPoint` は、Access Point が `AVAILABLE` になるのを確認するために内部で `fsvol_id` を解決していましたが、それを次のステートに返す dict に一度も含めていませんでした — 関数自身の docstring（`fsvol_id` を出力の一部として記載していた）と、実際の `result.update(...)` 呼び出しとの間の不一致です。実際に生じた影響: `Cleanup` は、失敗した実行だけでなく**成功した検証実行全て**で `cleaned_up: false` を報告していました。`Cleanup` が実行される時点で `event` から `fsvol_id` が常に欠落していたためです — Access Point のデタッチ自体は成功していたにもかかわらずです。これは複数回の手動エンドツーエンド実行を通じて検出されずに残っていました。理由は、`cleaned_up: false` が Step Functions の実行自体を失敗させるわけではない（ワークフロー全体としては成功を報告する）こと、そしてクリーンアップのうち Access Point 側は正常に機能していたため、ボリュームクリーンアップの不完全さが隠れてしまっていたことです。修正では、`AttachAccessPoint` が `AVAILABLE` 確認のために既に使っている `describe_s3_access_point_attachments` の同じレスポンスから `OntapConfiguration.VolumeId` として `fsvol_id` を読み取り、返す dict に含めるようにしました。**本ワークフローへの今後の変更をレビューする際の教訓**: `SUCCEEDED` した実行の出力内の `cleaned_up: false` は、Step Functions コンソールのトップレベルのステータスには自動的に表示されません — `AttachAccessPoint` や `Cleanup` に変更を加えた後は、実行ステータスだけに依拠せず、実際の出力ペイロード（または、それに相当するフィールドを追加した場合の DynamoDB 台帳）を必ず確認してください。

> **修正済みバグ — `Cleanup` が Access Point のデタッチ完了前に `DeleteVolume` を呼んでいた**
>
> `fsx.detach_and_delete_s3_access_point()` は非同期です — Access Point を `DELETING` に遷移させて即座に返り、デタッチの完了を待ちません。`Cleanup` の以前のバージョンは、待機なしに直後に `fsx.delete_volume()` を呼んでいたため、本プロジェクト自身のエンドツーエンド検証で `Cannot delete volume while it has one or multiple S3 access points: [<name>]` というエラーで確実に失敗していました — `delete_volume` を呼んだ時点で、Access Point が（削除処理の途中で）実際にまだ存在していたためです。修正では、`delete_volume` に進む前に、`describe_s3_access_point_attachments` が `NotFound` を返すまで呼び続ける短いポーリングループ（`_wait_for_ap_gone`、3 秒間隔、60 秒の予算）を追加しました。これは `StepTimeoutSeconds` のデフォルト 180 秒に十分収まります。自社の環境で Access Point のデタッチが 60 秒より長くかかる場合（本プロジェクト自身の実測ではいずれも大幅に短かった）は、`_wait_for_ap_gone` の `max_wait_seconds` 引数を増やし、`StepTimeoutSeconds` にそれを上回る十分な余裕があることを確認してください。

> **運用上の知見 — ONTAP のクローン「recovery queue」が、FSx がクローン自体を「消えた」と報告した後でも、*親*ボリュームの削除をブロックすることがある**
>
> これは本ガイド内の他のタイミングに関する補足とは種類が異なります — FSx が*新しい* ONTAP リソースを発見するまでの話ではなく、ONTAP 自身の内部管理が、リソースの*削除*に追いつくまでの遅延に関する話です。`Cleanup` が `fsx.delete_volume()` で FlexClone を削除すると、FSx 側の `describe-volumes` はほぼ即座にそのクローンを一覧から外しますが、ONTAP 自身はそのクローンを即座には忘れません — 完全にパージされるまでの間、内部的な「volume recovery queue」（一部の ONTAP リリースでは NetApp のドキュメントによれば約 12 時間程度とされる保持期間のウィンドウ）にそれを配置します。クローンがこのキューに存在する間、**親**ボリューム自身の `clone.has_flexclone` フィールドは ONTAP 側で `true` のままとなり、その*親*ボリュームを削除しようとする試み（FSx API、ONTAP REST API、ONTAP CLI のいずれでも）は、`aws fsx describe-volumes` がもうどこにもクローンを表示していないにもかかわらず、`Failed to delete volume "..." because it has one or more clones. Only the cluster administrator can delete the clones associated with this volume.` というエラーで失敗します。本プロジェクト自身の検証でも、これに直接遭遇しました — 同じ親ボリュームに対して複数回のクローン作成/削除サイクルを繰り返した手動テスト実行の後、その親ボリュームの削除が全く同じエラーメッセージで何度も失敗し、ONTAP 自身の `clone.parent_volume` フィルタで相互確認したところ、実際に残っている子ボリュームは 0 件であることが確認できました — ブロックの原因は、実際のクローン関係ではなく、古い recovery-queue エントリでした。
>
> **これは、本プロジェクトの中で唯一、FSx 側で待つのではなく ONTAP を直接確認することでブロックを解消できるケースです** — `aws fsx describe-volumes` は、FSx の視点からは既に削除済みのこれらのクローンを表示することが決してないため、FSx 側でポーリングする対象自体が存在しません。この状況に遭遇した場合は、保持期間が自然に終了するのを待つのではなく、ONTAP の recovery queue を直接クエリし（`GET /private/cli/volume/recovery-queue?vserver=<svm>`）、詰まっている該当エントリを purge してください（`DELETE /private/cli/volume/recovery-queue/purge?vserver=<svm>&volume=<queued-volume-name>`）。これには ONTAP 管理者認証情報が必要で、ONTAP CLI 相当のプライベート API です — 本ワークフロー自身の `Cleanup` Lambda がこれを自動的に行うことは**なく**、行うべきでもありません（クローンを予定より早く queue からパージすると、その特定のクローンに対する ONTAP 自身の安全ウィンドウを失うことになり、この操作は日常的な自動化ではなく手動のインシデント対応に限定されるべきものです）。これは、基盤となる親ボリュームを管理する担当者向けの純粋な運用上の補足です（例: 本ワークフローの検証に繰り返し使ったテストボリュームを廃止する場合など）— `Cleanup` は常に自分自身が作成したクローンのみを削除し、親ボリュームを削除することは一度もないため、ワークフロー自体の実行ごとの正しさには影響しません。
>
> **セキュリティに関する補足**
>
> recovery-queue エントリを purge するために必要な認証情報は、`VerificationLambdaRole`（本ワークフローの Lambda が日常的な `fsx:*` 呼び出しに使う共有ロール）よりも高い権限層にあります — この分離は意図的なものです。本番運用でこの purge を実行する必要がある場合は、ONTAP 管理者への直接操作に対して自社が既に要求している特権アクセス承認・監査プロセスを経由させてください。将来の親ボリュームのクリーンアップに備えて「念のため」recovery-queue purge 権限を本スタックのいずれかの Lambda ロールに事前にプロビジョニングすることはしないでください。

> **API契約に関する補足**
>
> Step Functions のステート間の契約は、バージョン管理されたスキーマではなく、暗黙的で型のない dict のマージです — 各 Lambda はステートマシン定義内の `"ResultPath": "$"` を通じて、前段のステートまでに積み上がった JSON ペイロード全体を受け取り、自身の新しいキーを追加した `dict(event)` を返します。そのため下流のステートは、上流のキー名が変わらないことに暗黙的に依存しています。5 つのステートで構成される単一目的のワークフローで、全ステートを一体で保守する場合はこれで十分機能しますが、デプロイ時にキー名の誤字やフィールド名の変更を検知するスキーマ検証がないことを意味します — 不一致は `RecordVerdict` や `Cleanup` 内で `event.get(...)` が明確なエラーではなく暗黙のデフォルト値を返す形で、実行時にキーの欠落として初めて表面化する可能性が高いです。本ワークフローを拡張する場合（例: コンテンツレベル PII 分類スキャナーの リストア検証に関する補足注記にある通り、追加のステートとして挿入する場合）は、異なる形のペイロードを導入するのではなく、同じ暗黙のパススルーの慣習を維持してください。そうしないと、挿入したステートの出力が、後続のステートが依存するキーを静かに落としてしまう可能性があります。

---

## 比較: Snapshot の存在 vs 検証済みクリーン復旧ポイント

| 観点 | Snapshot が存在する（Respond フェーズ） | 検証済みクリーン復旧ポイント（本ワークフロー） |
|------|------------------------------------------|--------------------------------------------------|
| CSF 2.0 機能 | Protect（Snapshot 自体） | Recover — 具体的には RC.RP |
| 何を証明するか | 時点コピーが取得されたこと | そのコピーが検査され、ランサムウェアの痕跡が見られないこと |
| 本番への影響 | なし（Snapshot 作成はほぼ即時） | なし（FlexClone は copy-on-write、スキャンはクローンへの読み取り専用） |
| リストア判断への確度 | 低い — 攻撃 *中* に取得した Snapshot 自体に暗号化済みファイルが含まれる可能性がある | より高い — 人間がリストアを決断する前の自動 go/no-go 信号 |
| 本リポジトリでの自動化状況 | ✅ 完全対応（[自動インシデント対応ガイド](automated-response-guide.md)） | ✅ 完全対応（本ガイド） |

> **復旧十分性に関する補足**
>
> 本ワークフローの「clean」判定は、リストア前の *必要条件* であって *十分条件* ではないものとして扱ってください。粗く分かりやすいケース（ランサムウェアによってリネームされたファイルが多数を占めるボリューム）を、高速かつ低コストに除外するものです。アプリケーションレベルのデータ整合性の検証や、Snapshot がエンドツーエンドでクリーンにリストアできることの検証、あるいは完全な DR テストの代替にはなりません。定期的な完全リストアテストは別途スケジュールし、本ワークフローは全ての検証対象 Snapshot に対して実行する自動化された第一段のゲートとして位置づけ、復旧可能性についての最終判断としては使わないでください。

> **DR Runbook連携に関する補足**
>
> これは上記の復旧十分性に関する補足とは異なります — あちらは「clean」判定の技術的な十分性を扱いますが、こちらは本ワークフローがより広範な DR 計画の運用上の連携にどう位置づけられるかを扱います。このステップは、他の DR Runbook のステップ（フェイルオーバー判断、関係者への通知、アプリケーション再起動の順序）と比べてどこに位置しますか？妥当な配置は次のようになります: インシデント検知 → 保護 Snapshot の取得（Respond） → **本検証の実行** → 「clean」の場合のみ、その Snapshot が、既存の DR 判断基準（RTO/RPO の目標、事業影響評価）と併せて（代替としてではなく）、リストアまたはフェイルオーバーの判断者に提示される。現在の DR Runbook が「Snapshot が存在すること」をリストア開始の十分条件として扱っている場合は、本ワークフローの判定結果をゲートとするよう Runbook を更新してください。ただしこれは、DR 計画への文書化された変更として行い、実際のインシデント時にその Runbook を実行する担当者に伝達すべきものであり、本ガイドの中に埋め込まれた暗黙の前提として扱うべきではありません。

---

## 前提条件

### ONTAP バージョン

- **FlexClone REST API**（`clone.is_flexclone`）: ONTAP 9.8+ で利用可能
- **ボリューム作成/削除 REST API** — ONTAP 9.6+ で利用可能

> **パッチ管理に関する補足**
>
> 本ガイドが示すのは各 API が必要とする*最低*バージョンであり、実行を*推奨*するバージョンではありません。本ワークフローは、ONTAP 管理エンドポイントに触れる他のどのコンポーネントとも同様に扱ってください — 稼働中の ONTAP バージョンについて [NetApp のセキュリティアドバイザリ](https://security.netapp.com/)を追跡し、組織の通常のサイクルでパッチを適用してください。`secretsmanager:GetSecretValue` 権限を持つ認証情報で ONTAP REST API を自ら呼び出す検証ワークフローだからといって、他の ONTAP API 利用者と同じパッチ管理の規律から免除されるわけではありません。別の観点として、Lambda ランタイム（`python3.12`）とその `boto3`/`botocore`/`urllib3` 依存関係（CloudFormation テンプレートのインライン `ZipFile` コード参照）は、本スタック内で特定バージョンに固定されていません — Lambda はデプロイ時点で利用可能な最新の `python3.12` マネージドランタイムとそこに同梱された SDK バージョンを解決するため、AWS の Lambda ランタイム廃止スケジュールを追跡し、一度デプロイしたら永続的に最新の状態が保たれると想定せず、定期的に再デプロイしてください。

> **セキュリティに関する補足 — 本番投入前の必須修正事項**
>
> `CreateFlexClone`（ONTAP REST API を直接呼び出す唯一残った Lambda — `Cleanup` がもう呼び出さない理由は上記ステップ5の補足を参照）は、`urllib3.PoolManager` を `cert_reqs="CERT_NONE"` で構築しています — 本スタックのインライン Lambda コードでは TLS 証明書検証が無条件に無効化されており、Secrets Manager から取得した ONTAP 管理者認証情報を送信するこの通信経路では、（中間者攻撃者が提示する自己署名証明書を含む）任意の TLS 証明書が検証なしに受け入れられます。本スタックのロジックの元になっているスタンドアロンの `restore_verification.py` ライブラリには、適切な検証を有効にするための `ca_cert_path` パラメータが*存在します*が、CloudFormation テンプレートのインライン `ZipFile` コードはこのオプションを公開・利用しておらず、`CERT_NONE` を固定でハードコードしています。これは隔離されたラボ VPC での PoC としては許容範囲ですが、実際の ONTAP 管理者認証情報を通信路に流す本番デプロイでは許容できません。本番利用前に、(a) FSx for ONTAP 管理エンドポイントの CA 証明書を Lambda に提供し（Lambda Layer、バンドルした証明書を指す環境変数、または Secrets Manager 経由）、`cert_reqs` を `"CERT_REQUIRED"` に変更して対応する `ca_certs` を設定する、または (b) 補完的なネットワーク制御（例: Lambda が ONTAP 管理 IP に到達する経路が、トランジットされないプライベートな VPC サブネットのみである）により、自社の環境では経路上での TLS 傍受が現実的に不可能であることを確認し、その判断を暗黙のデフォルトとして残さず明示的に文書化してください。

### AD 参加済み SVM の前提条件 — AD DC 到達可能であること

**AD 参加済み（CIFS 有効）の SVM に対して本ワークフローを実行する場合、Active Directory ドメインコントローラーが SVM から到達可能でなければなりません。** AD DC が到達不能な場合、S3 Access Point は正常に作成されますが、`ScanForIndicators` の `ListObjectsV2` が `AccessDenied` で失敗します。

**メカニズム**: CIFS 有効の SVM では、S3 AP 経由のデータ操作時に ONTAP が unix→win の reverse name-mapping lookup を実行します。この lookup には AD DC への LDAP/Kerberos 接続が必要です。DC が停止・削除・ネットワーク不通の場合、ファイルシステム層の認可チェックが失敗し、IAM/ポリシー側は完全に正しいにもかかわらず `AccessDenied` が返ります。

**紛らわしい点**: `HeadBucket`（AP の存在確認）は成功しますが、`ListObjectsV2`/`GetObject`/`PutObject` は全て失敗します。ファイルシステム層を通らない操作（メタデータのみ）と、通る操作（データアクセス）で挙動が異なるためです。

**本ワークフローの対応**: `CreateFlexClone` Lambda の先頭で `protocols/cifs/services` と `protocols/cifs/domains` API を使い、AD DC の発見可能性を事前チェックします。DC が発見できない場合、30分以上のクローン作成+FSx discovery を経た後に AccessDenied で失敗するのではなく、数秒で `AD CONNECTIVITY FAILURE` という明確なエラーで即座に失敗します。

```bash
# 手動での確認方法
curl -sk -u "<user>:<pass>" "https://<mgmt-ip>/api/protocols/cifs/services?svm.name=<svm-name>&fields=ad_domain.fqdn"
# records が返ればCIFS有効（AD参加済み）
# その場合、以下でDC到達性を確認:
curl -sk -u "<user>:<pass>" "https://<mgmt-ip>/api/protocols/cifs/domains?svm.name=<svm-name>&fields=discovered_servers"
# discovered_servers が空 = AD DC到達不能 → ワークフロー実行前に修復が必要
```

> **純粋 NFS/UNIX の SVM ではこの確認は不要です。** CIFS が無効な SVM では name-mapping lookup が発生しないため、AD の有無に関わらず S3 AP データ操作は正常に動作します。

### SVM の前提条件 — 既存の ONTAP ネイティブ S3 サーバーがないこと

**失敗した後ではなく、最初の実行前に確認してください。** 対象 SVM に、ONTAP ネイティブの S3 オブジェクトストレージサーバー（ONTAP CLI の `vserver object-store-server`、REST API の `protocols/s3/services`）が既に設定されていないことが必要です。設定されている場合、`AttachAccessPoint` は次のエラーで**間欠的ではなく毎回確実に**失敗します:

```
Amazon FSx is unable to create an S3 access point because of an existing
ONTAP object storage server on SVM <svm-name>. Please delete the existing
s3 server and retry.
```

これは**構造的な競合であり、タイミングの問題ではありません** — 下記ステップ2で説明する FSx for ONTAP 同期遅延とは異なり、リトライしても、待ち時間を延ばしても、`AttachAccessPoint` のリトライ予算を広げても、決して解決しません。本プロジェクト自身のエンドツーエンド検証でも、まさにこのケースに遭遇しました — 共有テスト用ファイルシステムの SVM に、本ワークフローとは無関係の ONTAP S3 サーバー（他チームのテストデータが入ったバケットを含む）が既に設定されており、その SVM 上のボリュームに対する検証実行は、リトライ設定に関わらず全てここで失敗しました。

自分が完全に管理していない SVM に対してデプロイする前に、必ず確認してください:

```bash
# ONTAP 管理エンドポイントに対して実行します(ONTAP 管理者認証情報が必要)
# — このワークフローに事前チェックのステップとして追加できる、同等の
# プログラム的なチェックについては AttachAccessPoint Lambda 自身の
# docstring を参照してください
curl -sk -u "<user>:<pass>" "https://<mgmt-ip>/api/protocols/s3/services?svm.name=<svm-name>"
# {"records": [], "num_records": 0}  <- 進めて問題ない
# {"records": [...], "num_records": 1}  <- 競合あり; 別の SVM を選ぶ
```

チェックでレコードが返ってきた場合、**その S3 サーバーの所有者とそこに格納されているデータを確認せずに削除しないでください** — 共有ファイルシステムでは、それが無関係な別のユースケースに使われている可能性が非常に高いです。代わりに、別の SVM（あるいは新規作成した SVM）に対して本ワークフローをデプロイしてください。これは、他者の ONTAP S3 サーバー設定の削除について合意を取るよりも、ほぼ常に簡単で安全です。

### ボリュームの前提条件 — UNIX security style のみ対応

**本ワークフローは出荷時点では UNIX security style のボリュームでのみ動作します。** `AttachAccessPoint` は `UnixUser` パラメータ（デフォルト `root`）経由で常に `FileSystemIdentity.Type=UNIX` を設定します。AWS 公式ドキュメントには、この組み合わせが必須であることが明記されています: 「UNIX security style のボリュームには UNIX ファイルシステムアイデンティティタイプを、NTFS security style のボリュームには Windows アイデンティティタイプを使用してください」（[Managing access point access](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/s3-ap-manage-access-fsxn.html)）。FSx for ONTAP の S3 Access Point は「二層認可モデル」を採用しています — S3 の IAM/リソースポリシーと、基盤ファイルシステム自身の権限チェックの両方が通過する必要があり、NTFS security style のボリュームに対して UNIX アイデンティティを使うと、2つ目の層で失敗します。

本プロジェクト自身のエンドツーエンド検証でも、まさにこのケースに遭遇しました — `AttachAccessPoint` と S3 Access Point のリソースポリシーはいずれも成功したものの、`ScanForIndicators` の `ListObjectsV2` 呼び出しが `AccessDenied` で失敗しました。IAM 側は完全に正しく見えるため、混乱しやすい失敗モードです。対象ボリュームの security style を確認すると初めて理解できます:

```bash
curl -sk -u "<user>:<pass>" \
  "https://<mgmt-ip>/api/storage/volumes?name=<volume-name>&svm.name=<svm-name>&fields=nas.security_style"
# "security_style": "unix"  <- 本ワークフローは動作する
# "security_style": "ntfs"  <- ScanForIndicators が AccessDenied になる
```

対象ボリュームが NTFS security style の場合（Windows クライアントに SMB 共有しているボリュームでよくあるケース）、本ワークフローは出荷時点では対応していません — Windows アイデンティティ（`FileSystemIdentity.Type=WINDOWS`、Active Directory 連携が必要）を受け付けるように `AttachAccessPoint` を拡張することが先に必要です。代わりに、検証用に UNIX security style のボリュームを選ぶか新規作成してください。

### AWS 権限

Lambda 実行ロールには以下が必要です:

```
# ONTAP REST API(Secrets Manager の認証情報経由)
- secretsmanager:GetSecretValue

# FSx S3 Access Point ライフサイクル(現時点でこれらのアクションは
# リソースレベル権限をサポートしていません)
- fsx:CreateAndAttachS3AccessPoint
- fsx:DetachAndDeleteS3AccessPoint
- fsx:DescribeS3AccessPointAttachments
- fsx:DescribeVolumes

# FlexClone の削除 — ONTAP REST API ではなく FSx API 経由(理由は
# 「検証の仕組み」のステップ5にあるFlexClone削除に関する補足を参照)
- fsx:DeleteVolume (arn:aws:fsx:*:*:volume/<ファイルシステムID>/* にスコープ)

# S3 Access Point ライフサイクル — 呼び出し元自身のロールには、上記の
# fsx:* アクションに加えてこれらの S3 アクションも必要です:
# fsx:CreateAndAttachS3AccessPoint / fsx:DetachAndDeleteS3AccessPoint は
# 呼び出し元の代わりに S3Control の CreateAccessPoint / GetAccessPoint /
# DeleteAccessPoint を呼び出すため、FSx 側の権限だけでは不十分です
# (作成時・デタッチ削除時の両方で AccessDeniedException が発生することを
# エンドツーエンド検証で確認済み)
- s3:CreateAccessPoint / s3:GetAccessPoint / s3:DeleteAccessPoint (arn:aws:s3:*:*:accesspoint/* にスコープ)

# S3 Access Point オブジェクト読み取り(スキャン用)+ ポリシー管理
- s3:ListBucket / s3:GetObject (arn:aws:s3:*:*:accesspoint/* にスコープ)
- s3:PutAccessPointPolicy / s3:GetAccessPointPolicy

# 判定結果台帳
- dynamodb:PutItem / dynamodb:UpdateItem (台帳テーブルにスコープ)

# SNS 通知（任意）
- sns:Publish (設定時は NotificationTopicArn にスコープ)
```

> **最小権限に関する補足**
>
> `sns:Publish` ステートメントの `Resource` は、デプロイ時に `NotificationTopicArn` パラメータが設定されている場合のみそのトピックにスコープされます。`NotificationTopicArn` を指定せずにデプロイした場合（通知無効）、本テンプレートの IAM ポリシーはアクションを完全に拒否するのではなく、`Resource: arn:aws:sns:<region>:<account-id>:*`（そのアカウント・リージョン内の全 SNS トピック）にフォールバックします。実際には、この構成でこの権限が行使されることはありません（`NOTIFICATION_TOPIC_ARN` が空の場合、`RecordVerdict`/`RecordErrorVerdict` は `sns.publish()` の呼び出し自体を完全にスキップします）が、IAM ポリシー自体はその実行時の挙動を反映していません — この構成で Lambda 実行環境が侵害された場合、そのアカウント内の任意のトピックに publish できる状態のままです。自社の最小権限標準が、アプリケーションロジックだけでなく IAM ポリシー自体が「通知無効」を反映することを要求する場合は、このステートメントを固定のワイルドカードなしのプレースホルダー ARN に絞るか、通知なしでデプロイする際は `SNSPublish` ステートメント自体を省略してください — Lambda 側のコードパスが広い権限付与を無害化していることに依拠しないでください。

### ネットワークアクセス

どの Lambda が VPC 内で実行されるかは、「検証ワークフローだから一律 VPC 内」という単純なルールではなく、各 Lambda が何を呼び出すかによって決まります:

| Lambda | 呼び出し先 | VPC 内で実行？ | 理由 |
|--------|-----------|----------------|------|
| `CreateFlexClone` | ONTAP REST API を直接呼び出し（ボリューム作成） | ✅ 実行する | `SubnetIds`/`SecurityGroupId` が提供する管理 IP へのルートが必要 |
| `AttachAccessPoint` | FSx コントロールプレーン API のみ（`CreateAndAttachS3AccessPoint`、`DescribeVolumes`）— ONTAP には直接触れない | ❌ 実行しない | FSx API はパブリックな AWS API であり VPC なしでも到達可能。この 1 ステップのためだけに FSx 用 Interface Endpoint を用意する必要をなくすため、VPC 外で実行 |
| `ScanForIndicators` | 前ステップで作成した **Internet-origin** の S3 Access Point に対する `ListObjectsV2` | ❌ 実行しない | Internet-origin AP はパブリック S3 エンドポイント経由でアクセス可能。VPC 外 Lambda が最もシンプルかつ検証済みのパターン（[FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns) 準拠） |
| `RecordVerdict` | DynamoDB + SNS のみ | ❌ 実行しない | いずれの API も VPC アクセスを必要としない |
| `Cleanup` | FSx コントロールプレーン API のみ（`DetachAndDeleteS3AccessPoint`、`DeleteVolume`）— ONTAP には直接触れない | ❌ 実行しない | いずれもパブリックな AWS API で VPC なしでも到達可能。本スタックの以前のバージョンでは、VPC 内から ONTAP REST API 経由で FlexClone を削除していました — このパスが FSx API に置き換えられた理由は上記ステップ5の補足を参照してください。その結果、`Cleanup` はもう FSx 用 Interface Endpoint も VPC アクセスも一切必要としません |

> **重要: VPC Endpoint について**。`CreateFlexClone` は（ONTAP 認証情報のために）VPC から Secrets Manager と STS に到達できる必要があります。`ScanForIndicators` は、ルートテーブル（`RouteTableIds` パラメータ）に紐づいた **S3 Gateway Endpoint** が必要です — これがないと、スキャンステップは VPC 限定の Access Point に一切到達できず、ワークフローは間欠的にではなく毎回そのステートで失敗します。`AttachAccessPoint` と `Cleanup` はいずれも FSx コントロールプレーン API のみを呼び出すパブリック API であるため、VPC Endpoint を一切必要としません。残り 3 つの Endpoint にはそれぞれ独立したパラメータ（`CreateSecretsManagerEndpoint`/`CreateStsEndpoint`/`CreateS3GatewayEndpoint`、いずれもデフォルト `true`）が用意されています — VPC に既に存在するものだけを `false` に設定してください。全部か無しかの二択として扱う必要はありません。これら 3 つの値を選ぶ前に何を確認すべきかは、直後の「デプロイ前チェック」を参照してください。

---

## デプロイ前チェック: 既存の VPC Endpoint を確認する

これは本スタックで最も頻発するデプロイ失敗であり、しかもエラーメッセージを一読しただけでは診断を誤りやすい形で失敗します。同じ VPC 内に、同じサービス向けの Interface VPC Endpoint が `PrivateDnsEnabled: true` で既に存在する場合、CloudFormation は**2 つ目**の Interface Endpoint 作成を拒否します。両方の Endpoint が同じプライベート DNS ドメイン（例: `secretsmanager.<region>.amazonaws.com`）を VPC 内に登録しようとするためです。エラーメッセージは競合している DNS ドメイン名を示しますが、それを既に登録しているリソースの名前は示さないため、見当違いの場所を調査してしまいがちです。

**`CreateXxxEndpoint` パラメータの値を決める前に、必ず以下を実行してください:**

```bash
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=<対象VPCのID>" \
  --query "VpcEndpoints[].{Service:ServiceName,Type:VpcEndpointType,State:State}" \
  --output table
```

出力結果を以下の表と照合してください:

| 出力に表示されるもの | このパラメータを設定 |
|---------------------|----------------------|
| `com.amazonaws.<region>.secretsmanager`（Interface） | `CreateSecretsManagerEndpoint=false` |
| `com.amazonaws.<region>.sts`（Interface） | `CreateStsEndpoint=false` |
| `com.amazonaws.<region>.s3`（Gateway）、かつ本デプロイで指定する `RouteTableIds` と同じルートテーブルに既に関連付けられている | `CreateS3GatewayEndpoint=false` |
| 上記のいずれも存在しない | 3 つ全てデフォルトの `true` のままでよい |

`CreateFsxEndpoint` というパラメータは存在しません — `AttachAccessPoint` と `Cleanup` はいずれも FSx コントロールプレーン API（パブリックな AWS API）のみを呼び出し、ONTAP REST API は一切呼び出さないため、本スタック内のどの Lambda も FSx 用 Interface Endpoint を必要としません。これは本スタックの以前のバージョンからの意図的な単純化です — 理由は上記「検証の仕組み」内のステップ5の FlexClone 削除に関する補足を参照してください。

[`automated-response.yaml`](automated-response-guide.md) と同じ VPC に本スタックをデプロイする場合、そのスタック自身は VPC Endpoint を一切作成しません — 元々その VPC に存在していた Endpoint に依存する構成です。したがって上記のチェックはそのまま全て適用されます。「`automated-response.yaml` が先にデプロイされているから、この 4 つの Endpoint のいずれかが既に存在するはずだ」という前提は置かないでください。

> **Route53 プライベートホストゾーンに関する補足**
>
> 組織によっては、FSx for ONTAP や他の AWS サービスを実行する VPC 自体が、Transit Gateway・VPC ピアリング・Virtual Private Gateway経由で共有サービス VPC に接続された「スポーク」であり、Interface VPC Endpoint はその共有サービス VPC 側に集中的に作成され、そのプライベートホストゾーンが（`AssociateVPCWithHostedZone` により）解決を必要とする各スポーク VPC に関連付けられている、という構成を取っている場合があります。自社の VPC がこの種のトポロジーにおけるスポークである場合、スポーク VPC に対して `describe-vpc-endpoints` を実行しても Endpoint は**何も表示されません** — たとえ（例えば）`secretsmanager.<region>.amazonaws.com` がその VPC 内で既に解決可能であっても、Endpoint リソース自体は別の VPC に存在し、そのVPCへ届いているのは Route 53 のプライベートホストゾーン関連付けだけだからです。このトポロジーでは、本スタック自身の Interface Endpoint を `PrivateDnsEnabled: true` で作成しようとすると、同じ DNS ドメイン競合エラーで失敗しますが、`describe-vpc-endpoints` だけでは事前に警告されません。`describe-vpc-endpoints` で何も表示されないにもかかわらず共有サービス型トポロジーが疑われる場合は、`CreateXxxEndpoint=true` が安全だと判断する前に、`aws route53 list-hosted-zones-by-vpc --vpc-id <対象VPCのID> --vpc-region <リージョン>` を追加で実行し、対象 AWS サービスのドメイン（例: `secretsmanager.<region>.amazonaws.com.`）に一致するプライベートホストゾーンがないか確認してください。一致するゾーンが存在する場合、そのサービスは既に中央管理された Endpoint 経由でその VPC 内でプライベートに解決可能であり、本スタック自身がそのサービス向けに Endpoint を作成しようとしても、文字通りの重複と同じ形で失敗します。これは仮定の話ではありません — 本プロジェクト自身の検証デプロイが、FSx・Secrets Manager・SSM・SNS 関連のプライベートホストゾーンが VPC 外から既に関連付けられた共有サービス型 VPC に対して、まさにこの失敗モードに遭遇しています。

**それでもデプロイして競合に遭遇した場合**: スタックは自動的にロールバックし（`ROLLBACK_COMPLETE`）、CloudFormation はそのステートのスタックに対して `deploy`/`create-stack` を再試行させません — 先に削除する必要があります:

```bash
# 失敗を確認し、どのリソースが競合したかを確認する
aws cloudformation describe-stack-events \
  --stack-name fsxn-restore-verification \
  --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].{Resource:LogicalResourceId,Reason:ResourceStatusReason}" \
  --output table

# ロールバックしたスタックを削除する(安全です — 動作可能な状態に一度も
# 到達していないため、台帳テーブルに失う検証履歴はありません)
aws cloudformation delete-stack --stack-name fsxn-restore-verification
aws cloudformation wait stack-delete-complete --stack-name fsxn-restore-verification

# 上記の表に従って正しい CreateXxxEndpoint の値を指定して再デプロイする
```

---

## デプロイ

### ワンスタックデプロイ

> **このテンプレートには `--s3-bucket` が必須です。** サイズが 56,946 バイトあり、
> CloudFormation がインラインのテンプレート本文として受け付けるのは 51,200 バイトまで
> です。これを超えるテンプレートは S3 に置く必要があります（上限 1 MB）。`--s3-bucket`
> を付けない場合、サイズ上限ではなくテンプレート冒頭を引用した `ValidationError` が
> 返るため、構文エラーのように見えます。同一リージョンで書き込めるバケットであれば
> どれでも構いません。CLI がテンプレートをアップロードし URL で渡します。

```bash
aws cloudformation deploy \
  --template-file shared/templates/restore-verification.yaml \
  --s3-bucket <your-cfn-staging-bucket> \
  --stack-name fsxn-restore-verification \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=<secret-arn> \
    FileSystemId=<fs-id> \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-1>,<subnet-2> \
    SecurityGroupId=<sg-id> \
    RouteTableIds=<route-table-1>,<route-table-2> \
    NotificationTopicArn=<optional-sns-topic-arn> \
    CreateSecretsManagerEndpoint=<true-or-false> \
    CreateStsEndpoint=<true-or-false> \
    CreateS3GatewayEndpoint=<true-or-false> \
  --capabilities CAPABILITY_NAMED_IAM
```

3 つの `CreateXxxEndpoint` の値は、上記の[デプロイ前チェック](#デプロイ前チェック-既存の-vpc-endpoint-を確認する)の表に従って設定してください — `describe-vpc-endpoints` による確認を行わずにデフォルト値のままデプロイしないでください。

スタックが作成するリソース:
- Step Functions ステートマシン（`{stack-name}-workflow`）。`AttachAccessPoint` には実測した FSx for ONTAP 同期遅延に合わせてサイジングした `Retry` ブロックが設定されています（上記「検証の仕組み」のステップ2参照）
- Lambda 関数 5 個（create-clone、attach-ap、scan、record-verdict、cleanup）— VPC 内で実行されるのは `create-clone` と `scan` のみで、`attach-ap`、`record-verdict`、`cleanup` は VPC 外で実行されます（上記[ネットワークアクセス](#ネットワークアクセス)参照）
- DynamoDB 台帳テーブル（`{stack-name}-ledger`）
- ステートマシン用 CloudWatch Logs（365 日保持）、各 Lambda 用 CloudWatch Logs（90 日保持。record-verdict はコンプライアンスエビデンスも兼ねるため 365 日保持）
- `true` のままにした Secrets Manager・STS（Interface）・S3 Gateway の各 VPC Endpoint — どの Lambda がどの Endpoint を必要とするかは上記[ネットワークアクセス](#ネットワークアクセス)、それぞれを独立して選ぶ方法は[デプロイ前チェック](#デプロイ前チェック-既存の-vpc-endpoint-を確認する)を参照

> **変更管理に関する補足**
>
> 変更承認プロセス向けに、本デプロイが触れるものと触れないものを明確にしておきます — 本デプロイは新規かつ追加的なリソース（新しい Step Functions ステートマシン、新しい Lambda 群、新しい DynamoDB テーブル、選択した VPC Endpoint）のみを作成し、FSx for ONTAP ファイルシステム本体、既存の ONTAP ボリューム、あるいは既存の VPC ネットワーク構成は一切変更しません。デプロイ失敗時の影響範囲はこれらの新規リソースに限定され、`cloudformation delete-stack` によるロールバックは本番ストレージに一切触れずにこれらを削除します。変更チケットで明記すべき唯一の共有状態リスクは次の点です: いずれかの `CreateXxxEndpoint` パラメータを `true` のままにした対象サービスについて、対象 VPC に既に別のスタック（例: `automated-response.yaml` の VPC、あるいは中央管理された共有サービス VPC — 上記の Route53 プライベートホストゾーンに関する補足を参照）による Interface Endpoint が存在している場合、本デプロイは既存の Endpoint を暗黙的に再利用するのではなく、重複 Endpoint の DNS 競合でスタック全体がロールバックします。この確認は最初の失敗後に後追いで行うのではなく、変更チケットのデプロイ前検証の一部として[デプロイ前チェック](#デプロイ前チェック-既存の-vpc-endpoint-を確認する)を実行してください。

> **リソースタグ付けに関する補足**
>
> 本スタックの CloudFormation テンプレートでは、`VerificationLedgerTable` のみが `Project`/`Purpose` のタグペアを持っており、5 つの Lambda 関数と Step Functions ステートマシンには `Tags` プロパティが一切設定されていません。組織がコスト配分タグやリソースグループタグに依存してコストの帰属やタグ付けポリシーを強制している場合（例: AWS Config の `required-tags` ルールや、タグなしリソースの作成を拒否するサービスコントロールポリシー）、DynamoDB テーブルは正しくタグ付けされているにもかかわらず、本スタックはそのまま出荷された状態ではその強制を通過できないか、Lambda/Step Functions の支出がコスト帰属から漏れてしまいます。タグ付けガバナンスが強制されているアカウントにデプロイする前に、`AWS::Lambda::Function` リソースと `AWS::StepFunctions::StateMachine` リソースに同等の `Tags` ブロックを追加してください。

> **スタック更新に関する補足**
>
> 本テンプレート内のどのリソースにも `DeletionPolicy` や `UpdateReplacePolicy` は設定されていません（上記で既に触れた DynamoDB テーブル自体の削除保護のギャップは別として）— `VerificationLedgerTable` を置き換えることになる CloudFormation スタックの**更新**（例: `KeySchema` や `AttributeDefinitions` への変更は、インプレース更新ではなく置き換えを強制します）は、デフォルトでは古いテーブルとその検証履歴全体を削除してしまいます。これは、スタック**削除**の場合として既に文書化されているリスクよりも狭い範囲ですが、見落としやすいリスクです — 通常のメンテナンス作業中の、善意によるテンプレート変更が同じデータ損失を引き起こす可能性があります。本スタックの DynamoDB テーブル定義に何らかの変更を加える前には、必ず `aws cloudformation create-change-set` を先に実行し、その出力で対象テーブルが `Replacement: True` になっていないかを確認してください。なっている場合は、事前にテーブルの内容をエクスポートするか、`VerificationLedgerTable` に `DeletionPolicy: Retain` を追加して、更新による置き換えが発生しても古いテーブルが削除されず孤立した状態で残るようにしてください。

### 検証実行の開始

```bash
aws stepfunctions start-execution \
  --state-machine-arn <StateMachineArn の出力値> \
  --input '{
    "svm_name": "svm-prod-01",
    "volume_name": "vol_data",
    "snapshot_name": "incident_response_20260708_143022",
    "vpc_id": "vpc-0123456789abcdef0"
  }'
```

[自動インシデント対応ガイド](automated-response-guide.md)の `create_snapshot` アクションの後段に本ワークフローを連結するには、SOAR プレイブックまたは Step Functions ファンアウト（同ガイドの FAQ 参照）から、封じ込め完了後に新規作成された Snapshot 名を指定してこのステートマシンを呼び出してください。

> **キャパシティ計画に関する補足**
>
> 本ワークフローは `ReservedConcurrentExecutions` や `MaxConcurrency` をどこにも設定していません — 各 Step Functions 実行は、アカウント/リージョンで共有される未予約の同時実行プールに対して、それぞれ 5 つの Lambda 呼び出しを行います。多数の Snapshot がほぼ同時に検証される大規模フリート（例: 数百のボリュームに対するスケジュールされた一斉スイープ、または多数の `create_snapshot` アクションを一度に発火させる大規模インシデントシナリオ）では、検証実行の同時実行バーストが、`automated-response.yaml` のレスポンスハンドラーを含むアカウント内の他の全 Lambda 関数と同じプールを争うことになります。大規模フリートに対して本ワークフローをスケジュールする前に、想定される同時検証実行数をアカウントの Lambda 同時実行数の上限と比較検討し、実行をファンアウトする仕組み（EventBridge Scheduler、Step Functions の `Map` ステート、または SOAR ツール）側に `MaxConcurrency` の上限を設定することを検討してください。アカウント全体の上限を暗黙のスロットルとして依存するのではなく。

### 台帳のクエリ

```bash
aws dynamodb query \
  --table-name fsxn-restore-verification-ledger \
  --key-condition-expression "snapshot_key = :sk" \
  --expression-attribute-values '{":sk": {"S": "svm-prod-01/vol_data/incident_response_20260708_143022"}}'
```

> **クエリパターンに関する補足**
>
> `VerificationLedgerTable` はベーステーブルのプライマリキー（パーティションキー `snapshot_key`、ソートキー `started_at`）のみを定義しており、グローバルセカンダリインデックスはありません。上記のクエリは、その `snapshot_key`（svm/volume/snapshot）が既に分かっている場合にうまく機能します — 「この特定の Snapshot が検証を通過したか」という、よくあるケースです。しかし、「今週フリート全体で `suspicious` だった判定を全部見せて」や「`svm-prod-01` に対する全ての検証実行を見せて」といったパーティションを横断するクエリはサポートしません — いずれも `FilterExpression` 付きの全テーブル `Scan`（動作はしますが、全アイテムをスキャンするため台帳が大きくなるほどスケールしません）が必要になるか、そのアクセスパターンが臨時ではなく日常的になるのであれば GSI の追加が必要になります。追加する場合、`verdict` 単独を GSI のパーティションキーにすることは避けてください — 値は 3 つ（`clean`/`suspicious`/`error`）しかなく、大規模なフリートで大半の実行が `clean` になる場合、この偏りが GSI の書き込み・クエリトラフィックの大半を単一パーティションに集中させます。`verdict` に加えて、より基数の高い項目（例: `svm_name` や日付単位に切り詰めた `started_at`）を組み合わせた複合キーにすることで、このホットパーティションのリスクを避けられます。フリート全体のダッシュボードやレポートにベーステーブルだけで十分だと決める前に、実際に必要なアクセスパターンを見極めてください。

---

## 設定リファレンス

| パラメータ | デフォルト | 用途 | この値の根拠 |
|-----------|-----------|------|-------------|
| `FsxSyncWaitSeconds` | 600 | FSx API のクローン発見を待つ前の静的待機時間（秒） | FSx 同期遅延は実測で 12〜36 分。10 分の静的 Wait により、確実に失敗する Lambda 呼び出しを約 5 回分回避し、コストと CloudWatch Logs ノイズを削減。0 に設定すると静的 Wait をスキップ（全遅延をポーリングで吸収） |
| `StepTimeoutSeconds` | 180 | 各 Lambda 呼び出しのタイムアウト（CreateFlexClone, Scan, RecordVerdict, Cleanup） | ONTAP ジョブ完了 + UUID 解決は 180 秒以内に十分収まる。数百万オブジェクトのボリュームをスキャンする場合のみ増加を検討 |
| `AttachAccessPointTimeoutSeconds` | 60 | AttachAccessPoint Lambda 1 回の呼び出しのタイムアウト | 1 回の試行 = 2〜3 回の AWS API 呼び出し（DescribeVolumes + CreateAndAttachS3AccessPoint or DescribeS3AccessPointAttachments）。API 呼び出し自体には 60 秒で十分 — 同期遅延は Step Functions Retry が吸収するため、このタイムアウトには含まれない |
| `SuspiciousRatioThreshold` | 0.05 | 「suspicious」判定に必要な、ランサムウェア関連拡張子を持つオブジェクトの比率 | 5% = ボリューム全体で有意なリネーム活動があったことを意味する（数件のテストファイルでは発火しない） |
| `SuspiciousMinCount` | 20 | 疑わしいオブジェクト数の絶対的な下限 | 小規模ボリュームでの誤検知を回避（例: 50 ファイル中に正当な `.encrypted` が 3 件） |
| `UnixUser` | root | 検証用 S3 Access Point のファイルシステムアクセスチェックに使用する UNIX ID | `root`（UID 0）は全 UNIX スタイルファイルを読み取り可能。ボリュームが root を拒否する場合や特定のサービスアカウントを使う場合は変更 |
| `LambdaMemorySize` | 512 MB | 検証用 Lambda 全体のメモリサイズ | 256 MB でも大半の実行には十分。512 MB は大規模な `ListObjectsV2` ページネーションへの余裕 |
| `CreateSecretsManagerEndpoint` | true | Secrets Manager 用 Interface VPC Endpoint を作成 | VPC に既に存在する場合は `false`（重複 = デプロイ失敗）。事前に `preflight-check.sh` を実行 |
| `CreateStsEndpoint` | true | STS 用 Interface VPC Endpoint を作成 | 同上 — 既に存在する場合は `false` |
| `CreateS3GatewayEndpoint` | true | S3 Gateway VPC Endpoint を作成 | ScanForIndicators が VPC外で実行されるようになったため（Internet-origin AP）、本ワークフローのスキャンステップには不要。VPC内の他の Lambda が S3 アクセスに必要な場合に有用。不要または既にある場合は `false` |

### タイミングバジェットの内訳

FSx for ONTAP 同期に対するワークフローの総待機バジェット:

```
総バジェット = FsxSyncWaitSeconds + (IntervalSeconds × MaxAttempts)
             = 600s（10 分）     + (120s × 25 = 3000s / 50 分)
             = 3600s（60 分）
```

| フェーズ | 所要時間 | コスト | 動作内容 |
|---------|---------|--------|---------|
| `WaitForFsxSync` | 10 分（設定可能） | $0（ネイティブ Wait ステート） | 静的待機 — Lambda 呼び出しなし、ログ出力なし |
| `WaitForFsxDiscovery` ポーリング | 120 秒間隔 × 最大 25 回 | 試行あたり約 $0.001（Lambda サブ秒） | `fsx:DescribeVolumes` を呼び出し、未発見なら `FsxDiscoveryPending` を raise |
| `AttachAccessPoint` S3 AP ライフサイクル | 15 秒間隔 × 最大 12 回 | 試行あたり約 $0.001 | AP を作成し AVAILABLE 状態をポーリング |
| `ScanForIndicators` AP ポリシー伝播 | 15 秒間隔 × 最大 5 回 | 試行あたり約 $0.001 | `AccessDenied` でリトライ（S3 AP リソースポリシーの結果整合性） |

**設計根拠 — なぜポーリング前に静的 Wait を入れるのか？**

静的 Wait がないと、ワークフローは最初の 10 分以上にわたり、確実に失敗すると分かっている Lambda を 120 秒ごとに呼び出し続けます。各失敗呼び出しは:
- コストが発生（Lambda 課金、微小だが非ゼロ）
- CloudWatch Logs にエントリを生成（ワークフロー監視者へのノイズ）
- Step Functions ステート遷移を消費（STANDARD タイプでは遷移ごとに課金）

`Wait` ステートは Step Functions のネイティブステートです — Lambda 呼び出しゼロ、CloudWatch Logs ゼロ、コストゼロ。設定された時間だけ実行を一時停止し、その後ポーリングステップに引き渡します。Wait 後は（既に同期時間が経過しているため）1〜2 回目の試行で成功する確率が大幅に高くなります。

**自社環境に合わせたチューニング:**

| 観測された FSx 同期遅延 | 推奨 `FsxSyncWaitSeconds` | 残りのポーリングバジェット |
|------------------------|--------------------------|------------------------|
| < 10 分 | 300（5 分） | 50 分（十分） |
| 10〜20 分 | 600（10 分、デフォルト） | 50 分 |
| 20〜40 分 | 900〜1200（15〜20 分） | 50 分 |
| 不明（初回デプロイ） | 600（デフォルト） | 50 分 — 初回実行を監視してから調整 |

> **自社の FSx 同期遅延の計測方法**: 検証を実行し、`WaitForFsxDiscovery` が初めて成功したタイムスタンプ（Step Functions の実行履歴で、当該ステートの最初の `LambdaFunctionSucceeded` イベントとして確認可能）を記録します。`CreateFlexClone` の完了時刻と `FsxSyncWaitSeconds` の値を差し引きます。残りが実際にポーリングにかかった時間です。2〜3 回繰り返して範囲を把握し、観測された最小遅延のやや下に `FsxSyncWaitSeconds` を設定してください。

> **コストに関する補足**
>
> 1 回の検証実行あたりの主なコスト要因は、Lambda の実行時間（5 つの短命な関数、それぞれ数秒程度）、DynamoDB のオンデマンド書き込み（`PAY_PER_REQUEST` テーブルへの `PutItem`/`UpdateItem` 呼び出しが実行あたり 2〜3 回）、Step Functions のステート遷移（本スタックは `StateMachineType` を設定していないため、デフォルトの `STANDARD` タイプでデプロイされます — `EXPRESS` のようなリクエスト単位・実行時間単位の課金ではなく、ステート遷移単位の課金です。`AttachAccessPoint` の `Retry` ブロックが最大約 60 分に及ぶことがあるため、`EXPRESS` の実行時間の上限（5 分）を大きく超えるこのワークフローには `STANDARD` が正しい選択です）、そして `CreateVpcEndpoints=true` の場合は Interface VPC Endpoint（Secrets Manager、STS、FSx）の時間課金とデータ処理量あたりの課金です。S3 Gateway Endpoint 自体には時間課金はありません。これらはいずれも、コンテンツレベル PII 分類スキャナーの [Amazon Comprehend の課金](https://aws.amazon.com/comprehend/pricing/)のようにスキャン対象オブジェクト単位で課金されるものではなく、コストは*実行頻度*に応じて増減します（ボリュームサイズには依存しません）。そのため、大規模なフリート内の全 Snapshot に対して 1 時間おきに本ワークフローを実行するのと、インシデントごとに一度だけ実行するのとでは、コストの規模が大きく異なります。同じ VPC 内で本スタックと `automated-response.yaml` の Interface VPC Endpoint を共用し（`CreateVpcEndpoints=false`）、重複課金を避けてください。

> **修正済みバグ — `DeleteVolume` が使い捨てクローンごとに静かにバックアップを作成していた**
>
> `fsx.delete_volume()` は、`OntapConfiguration.SkipFinalBackup=True` を明示的に渡さない限り、デフォルトでボリュームの最終バックアップを取得します — 実際のデータボリュームに対しては標準的で妥当な挙動ですが、このフラグを渡さない `Cleanup` は、スキャンされて破棄されるだけのクローンに対して、**検証実行ごとに** 1 件の `USER_INITIATED` バックアップ（課金対象のバックアップストレージで、自動的な期限切れなしに無期限保持される）を作成していたことになります。さらに悪いことに、本プロジェクト自身のエンドツーエンド検証では、最終バックアップの取得処理自体が途中で失敗し、ボリュームが `FAILED` ライフサイクル状態のまま固まって以降の全ての削除試行をブロックするケース（`Cannot take backup while <volume> is in FAILED`）にも遭遇しました。`SkipFinalBackup=True` を指定して再試行するまで解決しませんでした。現在の実装は、`delete_volume` の全ての呼び出しで `OntapConfiguration={"SkipFinalBackup": True}` を渡します。本ワークフローをフォークまたは拡張する場合は、このフラグを維持してください — 省略すると、対応する復旧価値のないバックアップストレージのコストが静かに積み重なります（このクローンはそもそも独立して価値のあるデータではなく、スキャン対象にすぎません）。これは、自社のファイルシステム自体の `DailyAutomaticBackupStartTime`/`AutomaticBackupRetentionDays` 設定（実際のボリュームの自動バックアップを管理するもの）とは無関係です — この問題を回避するためにファイルシステムのバックアップポリシーを無効化するべきではありません。修正は `delete_volume` 呼び出し側に属するものです。

> **サステナビリティに関する補足**
>
> これは計算炭素の観点から見て根本的に軽量なワークロードです — 1 回の実行あたり 5 つの短命な Lambda 呼び出しがあり、それぞれ持続的な CPU バウンドの計算ではなく I/O バウンドの作業（ONTAP REST 呼び出し、S3 一覧取得、DynamoDB 書き込み）を行い、実行間にアイドリングする永続的なコンピュート（常時起動の EC2 やコンテナ）もありません。上記のコストに関する補足と同じ観察が、単位を変えてここにも当てはまります — 消費エネルギーにとって重要なレバーは*実行頻度*であり、ボリュームサイズではありません。これは `ScanForIndicators` がファイルの内容を読むのではなくオブジェクトキーを一覧化するだけ（`ListObjectsV2`）であるためです。大規模フリートの全 Snapshot に対して 1 時間おきに本ワークフローをスケジュールすると、呼び出し回数（したがってエネルギー消費）はスケジュール頻度に線形に比例して増加します。目的がインシデント後の検証ではなく定期的な復旧レディネスチェックであれば、フリート全体を毎時スキャンするよりも、代表的なボリュームに対して日次または週次のケイデンスで実行する方が、有意に少ない総エネルギー消費で同じ目的を達成できる可能性が高いです。

---

## セキュリティ考慮事項

- **本番データへの経路なし** — FlexClone は copy-on-write で親ボリュームとブロックを共有しますが、S3 Access Point が公開するのは *クローン* のみで、親ボリュームは公開されません。クリーンアップ時にクローンを削除しても、親ボリュームや元の Snapshot には影響しません。

> **証拠保全に関する補足**
>
> この特性は、本番影響の回避以上の意味を持ちます — 検証対象の Snapshot 自体が、例えば [自動応答ガイド](automated-response-guide.md) の `create_snapshot` アクションがインシデント発生中に作成したような、保護的な証拠として作成されたものである場合です。本ワークフローは常に*クローン*のみを読み取り・削除し — `CreateFlexClone`、`AttachAccessPoint`、`ScanForIndicators`、`Cleanup` のいずれも元の Snapshot に書き戻すことはありません — そのため、保護的な Snapshot に対して検証を実行しても、その Snapshot 自体の chain of custody を変更・損なうことはありません。これは、本ワークフローが記録する*判定結果自体*が調査における証拠として耐えうるかどうかとは別の問いです。そのギャップ（実行前の状態とトリガーメッセージが現時点でハッシュ化されていないこと）については、[自動応答セキュリティ補遺](automated-response-security-addendum.md#chain-of-custody-要件-dfir)の Chain of Custody 要件の表を参照してください。
- **VPC 限定の Access Point** — Access Point は作成時に VPC に束縛され、VPC 外からは到達できません。これは、ポリシーのみで制御するインターネット起点の Access Point よりも強い保証です — 詳細は [AWS のネットワーク起点比較](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/configuring-network-access-for-s3-access-points.html)を参照してください。この特性のため、Access Point のオブジェクトを一覧化する `ScanForIndicators` は、束縛された VPC 内で S3 へのルート（本スタックが作成する S3 Gateway Endpoint）を持って実行される*必要があります* — インターネット起点の Access Point とは異なり、VPC 限定の Access Point にはポリシーだけで VPC 外から到達する方法がありません。

> **データレジデンシーに関する補足**
>
> 本ワークフローが作成する全てのリソース — FlexClone（定義上、親の FSx for ONTAP ファイルシステムと同じリージョン）、S3 Access Point、DynamoDB 台帳テーブル、Lambda 関数自体 — は、本スタックをデプロイした単一の AWS リージョン内に留まります。本ワークフロー内でリージョンを跨いだデータ移動は一切ありません。これはデータレジデンシーのアンケートに対して、推測に頼らず積極的に確認・肯定できる点です。組織が複数リージョンで本ワークフローを運用する場合（サイバーレジリエンス機能マップで参照されている[マルチアカウントデプロイ](multi-account-deployment.md)パターンに従い、リージョンごとに 1 スタック）、各リージョンの台帳は独立しています — 検証履歴のリージョン横断レプリケーションや集約は組み込まれていないため、レジデンシー要件に基づくマルチリージョン展開では、独自の集約レイヤーを構築しない限り、リージョンごとに独立したエビデンストレイルになります。
- **`fsx:*S3AccessPoint*` アクションの最小権限** — これらのアクションは現時点で、他の FSx アクションほど細かいリソースレベル権限や条件キーをサポートしていません。本スタックの IAM ポリシーではこれらを `Resource: '*'` にスコープし、その理由をドキュメント化しています。AWS がリソースレベルのサポートを追加した場合は見直してください。

> **IAM設計に関する補足**
>
> `VerificationLambdaRole` は 5 つの Lambda 全てに割り当てられる単一の共有ロールであり（CloudFormation テンプレート参照）、各関数が実際に必要とする権限に絞った 5 つの個別ロールにはなっていません。`ScanForIndicators`（`s3:ListBucket`/`s3:GetObject` のみが必要）は、技術的には `AttachAccessPoint`/`Cleanup` が実際に使用している同じ広範な `Resource: '*'` の `fsx:*S3AccessPoint*` 権限も引き継いでいますが、これらの API を一切呼び出しません。これは単一目的のスタックにおいてよくある合理的なトレードオフです（監査対象のロール数が減り、ロール間の `iam:PassRole` の複雑さがない）が、`ScanForIndicators` の実行環境が侵害された場合、そのコード自体が必要とする範囲よりも広い IAM の到達範囲を持つことを意味します。組織の IAM 標準が単一ワークフロー内でも関数単位の最小権限を求める場合は、`VerificationLambdaRole` を各関数のコードパスが実際に呼び出すポリシーステートメントのみに絞った、関数別のロールに分割してください。
- **判定結果台帳はエビデンスであり、ガバナンスの代替ではない** — DynamoDB 台帳は、誰が何をいつ検証し、どのような結果だったかという監査可能なエビデンスを提供し、CSF 2.0 の Govern プログラムが入力として利用できます — 本リポジトリがガバナンスプログラム自体を自動化しようとしない理由については、[サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md#govern統制) の Govern 機能に関する議論を参照してください。
- **台帳テーブルにはデフォルトで削除保護がない** — `VerificationLedgerTable` は Point-in-Time Recovery を有効にして作成されますが、`DeletionProtectionEnabled` や明示的な `DeletionPolicy: Retain` は設定されていません。このテーブルを定期的なリストア検証の主要なエビデンスとして利用する場合（下記のサイバー保険エビデンスに関する補足を参照）、監査目的で依拠する前に `DeletionProtectionEnabled: true` を追加し、`DeletionPolicy: Retain` の上書きも検討してください — うっかり `aws cloudformation delete-stack` を実行しても、エビデンスの履歴が消えてしまうことがないようにするためです。

> **保管時暗号化に関する補足**
>
> `VerificationLedgerTable` の `SSESpecification` は `SSEEnabled: true` で暗号化を有効にしていますが、`SSEType`/`KMSMasterKeyId` は設定していません。つまり、カスタマー管理の KMS キーではなく AWS 所有の DynamoDB キーで暗号化されます。これはほとんどのワークロードにとって妥当なデフォルトであり「保管時に暗号化されている」という要件は満たしますが、組織の鍵管理ポリシーがカスタマー管理 KMS キー（独立したローテーション管理、クロスアカウントのキーポリシー、特定のコンプライアンスマッピングのため）を要求する場合は、`SSEType: KMS` と `KMSMasterKeyId: <自社のキー ARN>` を明示的に追加してください — 現在のテンプレートはこれをパラメータとして公開していません。同じことが、ステートマシンと各 Lambda の CloudWatch Log Group にも当てはまります（こちらもデフォルトで AWS 所有の CloudWatch Logs キーで暗号化され、いずれの `AWS::Logs::LogGroup` リソースにも `KmsKeyId` は設定されていません）。

> **監査証跡の完全性に関する補足**
>
> `VerificationStateMachine` の `LoggingConfiguration` は `Level: ALL` と `IncludeExecutionData: true` を設定しており、これは完全な可観測性のためには望ましい設定です — しかし、ここでの「完全な可観測性」は、DynamoDB 台帳の厳選されたフィールドだけでなく、全ての実行における全ステートの入出力ペイロード全体（`svm_name`、`volume_name`、`snapshot_name`、および `reason`/`error` のテキストを含む）が CloudWatch Logs に書き込まれることも意味します。自社の環境で `volume_name` や `snapshot_name` の値にセンシティブな文脈（インシデントチケット番号、Snapshot ラベルに含まれた組織名や個人名など）が埋め込まれている場合、その文脈は台帳テーブルだけでなく、本スタックのログ保持期間（365 日）を持つ CloudWatch Logs にも存在することになります — 単一の正となる情報源が台帳テーブルだけだと想定していると、この点を見落としがちです。Snapshot・ボリュームの命名規則をこの点を踏まえて見直すか、ログ内の完全なペイロードが許容できる露出範囲を超える場合は `IncludeExecutionData: false` を設定してください（その場合、対応するデバッグ情報の詳細さは失われます）。

> **サイバー保険エビデンスに関する補足**
>
> サイバー保険の引受チェックリストでは、バックアップの存在だけでなく*テスト済み*であることのエビデンスが求められる傾向が強まっています — 2026 年時点の複数の引受ガイドで「定期的なリストア検証（periodic restore verification）」が明示的に挙げられています。本ワークフローが生成する DynamoDB 台帳（実行ごとにタイムスタンプ付きの `snapshot_key`、判定結果、`reason` を記録）は、この問いに対する合理的なエビデンスアーティファクトになりますが、保険会社側のエビデンス提出フォーマットを念頭に設計されたものではありません。更新時のアンケート提出用に PDF/CSV へエクスポートする機能は組み込まれておらず（上記の通り）、エビデンス自体を守る削除保護もありません。引受や更新の際に本台帳を提示する予定がある場合は、まず上記の削除保護を有効化した上で、該当する `dynamodb query` の出力結果のスナップショットを他の管理エビデンスと一緒にエクスポートしてください。

> **監査エビデンスに関する補足**
>
> 本ワークフローが内部監査や SOX のコントロールマトリクスにおける統制として引用される場合（例: 「復旧ポイントはリストア前に検証される」というコントロールの主張）、運用の実効性を検証する監査担当者は通常、次のようなエビデンスを求めます: (1) その統制が*存在する*だけでなく、サンプル期間中に実際に*実行された*というエビデンス — DynamoDB 台帳の `started_at`/`completed_at` タイムスタンプはこれを裏付けます。(2) 「suspicious」または「error」判定を、適切な権限を持つ誰かが*レビューし*、対応を取ったというエビデンス — これは本ワークフロー自体が生成するものではありません。SNS 通知が発火したことは、アラートが*送信された*エビデンスであり、誰かが*対応した*エビデンスではありません。(3) 本スタックの IAM 権限/コードを変更できる担当者（事実上「clean」の意味を制御する立場）と、その判定結果に依拠してリストアを承認する担当者との間の職務分離。SOX のコントロールが本ワークフローに依拠する場合は、自動化された判定結果と、人間によるレビューと対応を記録する手動のアテステーションステップ（チケット、承認ワークフローなど）を組み合わせ、そのアテステーションを DynamoDB の記録と併せて実際の監査エビデンスとして保持してください — 台帳単体では、スキャンが実行されたことは記録されますが、誰かがその結果に対して判断を下したことは記録されません。

---

## テスト

`restore_verification.py` モジュールには 23 のユニットテストがあります:

| カテゴリ | 検証内容 |
|---------|---------|
| FlexClone 作成/削除 | 成功、ジョブ失敗、クローン解決失敗、既に削除済み（404） |
| S3 Access Point 接続/切断 | 成功、fsvol 解決タイムアウト、MISCONFIGURED 状態、ClientError 処理、切断時の not-found |
| ランサムウェア痕跡スキャン | クリーンなボリューム、疑わしいボリューム、空のボリューム、大文字小文字を区別しない拡張子マッチング |
| フルオーケストレーション | clean 判定、suspicious 判定、最小件数未満での誤検知回避、クリーンアップを伴うエラー経路、クローン作成後のエラーでのクリーンアップ、結果シリアライズの上限処理（`to_dict()` で `suspicious_objects` は先頭 50 件に切り詰められるが、`suspicious_object_count` は常に実際の合計件数を反映する） |

```bash
python3 -m pytest shared/python/tests/test_restore_verification.py -v
# 23 passed in 0.11s
```

> **テストカバレッジに関する補足**
>
> 23 件のテストは全て `boto3` クライアントと ONTAP への HTTP レスポンスをモックした状態で実行され、実際の FSx for ONTAP ファイルシステムや実際の S3 Access Point には一切アクセスしません。これによりテストスイートは高速かつ CI 上で安全に実行できます（AWS 認証情報や稼働中のインフラは不要）が、その反面、モックされた API の契約と実際の ONTAP REST API/AWS FSx API の挙動との間にずれが生じても検出できません。`pytest` の成功は、ワークフローがエンドツーエンドで動作するための必要条件ではあっても十分条件ではないものとして扱ってください — 本番投入前に実際の（本番でない）FSx for ONTAP ファイルシステムで検証し、FlexClone や S3 Access Point の API に関わる ONTAP や AWS SDK のバージョンアップ後は再検証してください。

> **CI カバレッジに関する補足**
>
> 上記の「CI 上で安全」というのは、テスト自身の設計（稼働中のインフラを必要としない）についての記述であり、本リポジトリの CI ワークフローが実際にこれらを実行していることを意味するものではありません。本稿執筆時点で、`.github/workflows/ci.yaml` の Python テストステップは各ベンダー統合の `tests/` ディレクトリと `shared/lambda-layers/ems-parser/tests/` をカバーしていますが、`shared/python/tests/` はカバーしていません。そのため、この 23 件のテストは、コントリビューターがローカルで `pytest` を実行した場合にのみ実行され、push や pull request のたびに自動実行されるわけではありません。`restore_verification.py` への変更がこれらのテストの一つを破壊しても、現状では CI は失敗しません。このテストスイートをマージゲートとして依拠する場合は、既存の各ベンダー・共有レイヤーのステップと同様に、`python -m pytest shared/python/tests/ -v` を実行するステップを CI ワークフローに追加してください（`ems-parser` が既にカバーされているのと同じ形で）。

> **ライセンスに関する補足**
>
> 本ワークフローの実行時依存関係は `boto3`、`botocore`、`urllib3` であり、いずれも本リポジトリにベンダリングされているのではなく Lambda の `python3.12` マネージドランタイムに同梱されているため、直接ライセンススキャンできる `requirements.txt` やロックファイルは本スタック内に存在しません。3 つとも [Apache License 2.0](https://github.com/boto/boto3/blob/develop/LICENSE) であり、これはコピーレフト義務を課さない許諾性の高いライセンスです。そのためライセンスコンプライアンスの観点からは低リスクな依存関係セットと言えます。組織がサプライチェーンレビューの一環として自動ライセンススキャン（SBOM 生成ツールや `pip-licenses` のようなツールなど）を実行している場合、本スタックのインライン CloudFormation `ZipFile` コードを直接スキャンしても、`requirements.txt` ベースのデプロイの場合とは異なりこれらの依存関係は検出されない点に注意してください — 代わりに Lambda マネージドランタイム自身が公開している依存関係マニフェストに対してスキャンする必要があります。あるいは、上記の パッチ管理に関する補足注記で触れた固定依存関係方式のデプロイに移行すれば、このスキャンも容易になります。

> **運用トリアージに関する補足**
>
> 検証実行の失敗でアラートを受けた場合、まず次の手順で確認してください: (1) 該当する `StateMachineExecutionArn` の Step Functions 実行履歴を確認し、失敗したステート名から 5 つの Lambda のうちどれが失敗したかを特定する。(2) その Lambda の CloudWatch Logs（`/aws/lambda/{stack-name}-{create-clone|attach-ap|scan|record-verdict|cleanup}`）で実際の例外内容を確認する。(3) 該当する `snapshot_key` で DynamoDB 台帳を検索する — `error` 判定と `reason` フィールドだけで、ログを見ずにトリアージできることも多いです。失敗した実行そのものは深夜 2 時の即時対応を必要としません — ワークフロー自身の `Cleanup`/`CleanupAfterError` ステートが、FlexClone や S3 Access Point の残置を既に保証しているためです（上記アーキテクチャ参照）。そのため基本的には「都合のよいときに調査すればよい」アラートであり、「今すぐ止血が必要」なアラートではありません。ただし同じ `snapshot_key` が繰り返し失敗する場合は、ONTAP 側または IAM 側の問題である可能性があり、早めのエスカレーションを検討してください。

> **障害注入に関する補足**
>
> 上記のテスト節は正常系といくつかのモック済み失敗モードを検証していますが、「クリーンアップはどの経路でも実行される」（アーキテクチャ参照）という主張は、Step Functions の `Catch` 構成を信頼するだけでなく、実際の（本番でない）デプロイに対して実際に障害を注入して検証するのが最も確実です。試す価値のある実験: (1) `ScanForIndicators` Lambda の実行中に強制的に失敗させ（例: `s3:ListBucket` への一時的な IAM 拒否）、ステートマシンが成功を報告しているだけでなく、FlexClone が実際に削除されたことを確認する。(2) 一時的に Lambda ロールから `fsx:DeleteVolume` を剥奪し、`Cleanup`（下流に独自の catch を持たない唯一の Lambda）が失敗を静かに握り潰すのではなく `cleaned_up: false` を報告し、失敗を明確にログに残すことを確認する — そして、クリーンアップ自体のセーフティネットがない状況で、残置されたクローンを検出・手動修復できるか確認する。(3) `RecordVerdict` は成功したが、続く `Cleanup` ステップの Lambda 呼び出し自体が開始に失敗する場合（アプリケーションエラーではなく Step Functions レベルの障害）に何が起こるかを検証し、CloudWatch アラームが実際にそのギャップを検知するかを確認する。(4) 本プロジェクト自身の検証では、同一のアイドル状態ファイルシステムに対する 3 回の別実行で、FSx for ONTAP 同期遅延が*増加*していくパターン（約 12 分 → 約 24 分 → 約 36 分）が観測されているため、1 回だけでなく検証実行を連続して複数回行い、いずれも `AttachAccessPoint` の 28 回のリトライ予算を使い切らないことを確認してください。1 回の実行が成功しただけでは、この観測された増加傾向を踏まえた予算の十分性を確認したことにはなりません。自社の環境の実際の遅延が本プロジェクトの実測よりも長くかかる場合（例: より稼働率の高い、あるいは大規模なファイルシステム、あるいは同じ増加パターンがさらに続く場合）は、ステートマシン定義内の `MaxAttempts` を増やす必要があります — 現時点でこの遅延に上限があるという確証は得られていません。これらはいずれも本番データを必要とせず、使い捨ての SVM/ボリュームと合成的な Snapshot で十分です。この 4 つの実験は、一度だけ実施する事前チェックリストではなく、本ワークフロー専用の小規模な game day として扱ってください — `AttachAccessPoint` のリトライパラメータや `Cleanup` のロジックへの変更、あるいは ONTAP/AWS SDK のバージョンアップの後には再度実施してください。対象のシステムへの変更後にカオスエンジニアリングの実験を再実施するのと同じ考え方です。

> **リトライポリシーに関する補足**
>
> `AttachAccessPoint` のみが `Retry` ブロックを定義しています（実測した FSx for ONTAP 同期遅延に合わせてサイジング — 上記ステップ2参照）。`CreateFlexClone`、`ScanForIndicators`、`RecordVerdict`、`Cleanup` は `Catch` ブロックを定義していますが `Retry` ブロックはありません。この 4 つのいずれかで一時的な障害（ONTAP 管理エンドポイントが瞬間的に到達不能、Secrets Manager や DynamoDB の短時間のスロットリング、VPC 内での一過性のネットワーク不調など）が発生すると、最初の試行でリトライすることなく即座に `CleanupAfterError`/`RecordErrorVerdict` に流れます。つまり、2 回目の試行なら成功していたはずのワークフローが、代わりに永続的な「error」判定を記録し、実際には回復可能な一時的な不調に対して完全なクローン作成とクリーンアップのサイクルを丸ごと発生させてしまいます。自社の環境でこの 4 つのステートに一時的な ONTAP API や AWS API のエラーが時々発生する場合は、`Catch` に流れる前に `Retry` ブロック（例: `ErrorEquals: ["States.Timeout", "States.TaskFailed"]` と短い `IntervalSeconds`/`MaxAttempts`/`BackoffRate`）を追加することを検討してください — これは Step Functions の標準的なパターンで、本ワークフローでは現在 `AttachAccessPoint` のみが使用していますが、その理由は一般的な一時エラーへの対応ではなく、既知で予測された遅延への対応です。

> **SNS 配信に関する補足**
>
> `RecordVerdict` と `RecordErrorVerdict` の両方の SNS `publish` 呼び出しは、失敗時に警告ログを出すだけの単純な `try`/`except Exception` でラップされています（`logger.warning("Verdict notification failed: %s", e)`）— それ以前の DynamoDB `put_item` は必ず成功するかエラーを発生させますが、SNS publish の失敗（トピックの削除、権限変更、スロットリング）は呼び出し元から見ると静かに握り潰されます。Step Functions の実行自体は成功を報告し、DynamoDB 台帳にも正しい判定結果が記録されますが、「suspicious」な結果を知るために依拠していた通知自体は単純に届かなかった可能性があります。しかも本ワークフロー自身の CloudWatch メトリクスには、「PII なし・通知不要」と「通知を試みたが失敗した」を区別する仕組みが何もありません。SNS の配信がインシデント対応のトリガーチェーンの一部である場合は、アラートの欠如を「疑わしい判定がなかった」ことと同義に扱うのではなく、この Lambda 自身のログパターン（`"notification failed"`）に対する CloudWatch アラームを追加してください。

---

## 関連ドキュメント

- [サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md#recover復旧) — 本ガイドが対応する Recover 機能に関する議論
- [自動インシデント対応ガイド](automated-response-guide.md) — 本ワークフローの実行前に通常先行する、Respond フェーズのブロックと保護 Snapshot 作成
- [ARP インシデント対応ガイド](arp-incident-response-guide.md) — 本ワークフローのスキャンが補完する（代替するのではない）、本番ボリュームに対するリアルタイムのエントロピー検知
- [コンテンツレベル PII 分類スキャナー](content-classification-scanner.md) — 同じ FlexClone + S3 Access Point パターンを基盤とした、CSF 2.0 の Identify 機能（データ分類）向けの関連コンテンツスキャン機能
- [ガバナンス・コンプライアンス](governance-and-compliance.md) — 判定結果台帳が Govern 機能のエビデンスとしてどう位置づけられるか
- [コンプライアンスエビデンスパック](compliance-evidence-pack.md) — 監査証跡エビデンスのテンプレート

## FAQ

**Q: 「clean」判定は、その Snapshot が安全にリストアできることを保証しますか？**
A: いいえ — 上記の 復旧十分性に関する補足を参照してください。これはファイル拡張子パターンに基づく高速で自動化された事前フィルタであり、完全なマルウェアスキャンやアプリケーションレベルの整合性チェックではありません。必要な第一段のゲートとして扱い、最終判断とはしないでください。

**Q: 本番ボリュームを直接スキャンせず、なぜ FlexClone を使うのですか？**
A: 稼働中のボリュームをスキャンすると本番の I/O と競合し、進行中の攻撃と干渉するリスクがあります。FlexClone は copy-on-write でブロックを共有する、隔離された時点の読み書き可能なコピーです。検証はこのクローンに対して本番への影響ゼロで実行され、完了後に削除されます。

**Q: NFS/SMB でクローンをマウントせず、なぜ S3 Access Point を使うのですか？**
A: マウントには SVM のデータ LIF へのネットワークレベルのアクセスと、検証環境で稼働する NFS/SMB クライアントが必要です。S3 Access Point を使えば、ステートレスな Lambda がマウント手順なしに S3 API 経由でファイルを一覧・読み取りできます。VPC 限定にした場合は、読み取り専用の Access Point 自体を除けば本番データプレーンへの経路も一切生まれません。

**Q: ワークフローが途中で失敗した場合、クローンは残置されますか？**
A: されません。Step Functions の `Catch` ブロックは、どの失敗モードも正常系と同じ `Cleanup` Lambda にルーティングします。Cleanup Lambda は部分的な状態（例: クローンは作成されたが Access Point の接続が失敗した場合）を許容し、存在するものだけをクリーンアップします。

**Q: 自動応答モジュールが作成した Snapshot 以外にも実行できますか？**
A: できます。`verify_snapshot()` および Step Functions の入力に必要なのは `svm_name`、`volume_name`、`snapshot_name` のみです。既存の ONTAP Snapshot であれば、インシデントとは無関係なスケジュール Snapshot ポリシーによる Snapshot も含めて動作します。

**Q: ユーザーから「検証が終わらない」と報告されました。エスカレーションする前に最初に確認すべきことは？**
A: まず、コンソールまたは `describe-execution` で、その Step Functions 実行の現在のステートを確認してください — 「終わらない」はほぼ常に特定のステートで止まっている（あるいはリトライを繰り返している）状態を意味し、どこかで静かにハングしているわけではありません。実行履歴に `FsxDiscoveryPending`/`S3AttachPending` の失敗が複数回表示された状態で `AttachAccessPoint` 内で `RUNNING` になっている場合、それは非常に高い確率で**想定された挙動**であり、ハングではありません — この状態の `Retry` ブロックがサイジングされている、実測した約 12 分/約 24 分/約 36 分（実行を重ねるごとに増加）の FSx for ONTAP 同期遅延については上記「検証の仕組み」のステップ2を参照し、インシデントとして扱う前に、経過時間が約 60 分のリトライ予算内かどうかを確認してください。また、本プロジェクトのデータからは、この遅延に確証された上限があるとは言えないことも念頭に置いてください。`ScanForIndicators` で止まっている場合、最も一般的な原因は VPC ネットワーキングのギャップです（上記のネットワークアクセス参照 — 例えば S3 Gateway Endpoint がないケースは間欠的ではなく毎回確実に失敗します）。`CreateFlexClone` で止まっている場合は、リクエスト内の ONTAP ボリューム/SVM 名が実際に存在するか、また ONTAP ジョブが管理エンドポイントに対する `GET /api/cluster/jobs/{uuid}` の直接呼び出しで進捗を示しているかを確認してください。`CreateFlexClone`、`ScanForIndicators`、`RecordVerdict`、`Cleanup` にはそれぞれ `StepTimeoutSeconds`（デフォルト 180 秒）の上限とリトライなしの設定があるため、この 4 つのいずれかでの本当のハングは無期限に実行されるのではなく、その時間内に Step Functions の `States.Timeout` エラーとして表面化するはずです。

**Q: `AttachAccessPoint` が 28 回のリトライをすべて使い切り、ワークフローが error 判定を記録しました。60 分では不十分ということですか？**
A: その可能性はあります — 本プロジェクト自身の実測（同一のアイドル状態ファイルシステムに対する 3 回の別実行でそれぞれ約 12 分、約 24 分、約 36 分）は安定した範囲ではなく*増加*するパターンを示しており、リトライ予算のサイジングに全面的な確信を持てる、確証された上限は現時点でありません。自社の環境でより稼働率の高い、あるいは大規模なファイルシステムであれば、60 分を超える可能性は十分に考えられます。まず DynamoDB 台帳の `reason` フィールドと `Cleanup` Lambda のログを確認し、クローンが実際に削除されたことを確認してください（上記ステップ5の孤立クローンに関する補足参照 — 本プロジェクト自身の検証でも、当時のより小さいリトライ予算でワークフローが既に諦めた後、クローン作成から約 35〜37 分後になって初めて可視化される孤立クローンが残りました）。これが繰り返し発生する場合は、ステートマシンの `DefinitionString` 内の `AttachAccessPoint` の Retry ブロックで `MaxAttempts`（および/または `MaxDelaySeconds`）を増やして再デプロイし、自社の特定のファイルシステムに対して自社独自の基準値を得るために、本プロジェクト自身の計測手法（`aws fsx describe-volumes` による観測ギャップのない連続ポーリングを、1 回ではなく複数回の別実行に対して実施）を再実施することも検討してください。本プロジェクトの実測範囲がそのまま自社の環境に当てはまると仮定せず、実測に基づいて判断してください。

**Q: 既存の FSx for ONTAP フリートを本検証ワークフローに移行する場合、どこから始めればよいですか？**
A: まず 1 つのボリュームに対して本スタックをデプロイし、既存のスケジュール Snapshot に対して `verify_snapshot()` を手動で実行してみてください — これにより、自動トリガーに組み込む前に、自社の特定の環境（ONTAP バージョン、VPC 構成）に対して ONTAP 権限、VPC ネットワーキング、FSx S3 Access Point のサポートが実際に機能することを確認できます。本ワークフローが存在する前に作成された既存の Snapshot も、特別な処理なしに動作します — 本ワークフローが Snapshot に遡って要求するメタデータやタグ付けはありません。1 つのボリュームで検証できたら、ボリュームやアカウント単位で手動デプロイするのではなく、サイバーレジリエンス機能マップで参照されている同じ[マルチアカウントデプロイ](multi-account-deployment.md)の StackSets パターンを使ってフリート全体に展開してください。

**Q: DynamoDB 台帳に「suspicious」判定が記録されているのに、誰も SNS 通知を受け取っていません。これは想定される挙動ですか？**
A: 通知パイプライン全体が壊れていると結論する前に、`RecordVerdict` Lambda の CloudWatch Logs で `"Verdict notification failed"` という警告がないか確認してください。SNS の `publish` 呼び出しは try/except でラップされており、失敗時はログを出して処理を継続します（上記セキュリティ考慮事項の SNS 配信に関する補足参照）— これは意図的な設計で、通知配信の問題が判定結果の記録をブロックしないようにするためですが、その特定のログ行を監視していない限り、通知の失敗は静かに起こります。このアラートをインシデント対応のトリガーとして利用している場合は、そのログパターンに対する CloudWatch アラームを追加してください。「アラートを受け取らなかった」ことを「何も疑わしいことは起きなかった」ことと同義に扱わないでください。

**Q: DynamoDB テーブルのキースキーマを変更したところ、過去の検証履歴が消えてしまいました。何が起きたのですか？**
A: `VerificationLedgerTable` の `KeySchema` や `AttributeDefinitions` を変更する CloudFormation スタックの更新は、インプレース更新ではなく DynamoDB テーブルの置き換えを強制します。本テンプレートはそのテーブルに `DeletionPolicy: Retain` を設定していないため（上記デプロイ節のスタック更新に関する補足参照）、置き換えを適用する過程で CloudFormation が古いテーブルを削除しました。これが復旧可能なのは、Point-in-Time Recovery が有効になっていて、その保持期間内にリストアする場合のみです。今後は、スキーマに影響する更新の前に必ず `aws cloudformation create-change-set` を実行し、出力に `Replacement: True` がないか確認してください。


---

## 関連ドキュメント

- [デプロイメントガイド](deployment-guide.md) — デプロイ前検証、VPC Endpoint 競合回避、パラメータファイルの設定方法
- [Automated Response ガイド](automated-response-guide.md) — リカバリ検証に先立つインシデント対応アクション
