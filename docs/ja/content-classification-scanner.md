# コンテンツレベル PII 分類スキャナー — CSF 2.0 Identify のギャップを埋める

🌐 **日本語**（このページ） | [English](../en/content-classification-scanner.md)

## エグゼクティブサマリ

[サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md#identify識別) では、Identify 機能におけるギャップを明確に指摘していました。本リポジトリの [データ分類ガイド](data-classification.md) はスキーマレベルの分類（`UserName`、`ObjectName` といった *フィールド* のどれが PII か）を定義していますが、NetApp のデータ分類ツールが CSF 2.0 の Identify 機能に対して行うような、ファイル *内容* のスキャンは行っていませんでした。

本ガイドは、Amazon Comprehend のマネージド PII エンティティ検出を使い、既存の S3 Access Point 経由でファイルを読み取るスタンドアロンの Lambda 関数として、このコンテンツレベルのスキャンを実装します。

1. **オブジェクト一覧の取得**: 指定した S3 Access Point 経由で `ListObjectsV2` を実行し、スキャン対象のテキスト/構造化データ拡張子（`.txt`、`.csv`、`.json`、`.log` など）にフィルタします。バイナリ形式（Office ドキュメントなど）は対象外です（詳細は[残存する限界](#残存する限界)を参照）。
2. **読み取りとチャンク分割**: 各ファイルの内容を、Amazon Comprehend の 1 回あたりのサイズ上限を下回るバイト境界のセグメントに分割します。
3. **`DetectPiiEntities` の呼び出し**: チャンクごとに呼び出し、エンティティタイプ・件数・確信度スコアをファイル単位で集計します。**PII の値そのものは一切保持しません** — エンティティタイプ/オフセット/確信度のみです。
4. **分類レポートの記録**: DynamoDB に書き込み、PII が検出された場合は任意で SNS 通知を送信します。

> **スコープに関する注記**
>
> これは PII の *発見* ツールであり、マスキング、修復、あるいは汎用的なマルウェア/コンテンツスキャナーではありません。答えるのは「このボリュームに PII らしきコンテンツが含まれているか、おおよそどの程度か」という問いです — これは CSF 2.0 の Identify 機能（資産・データの理解）が対象とする問いであり、Protect、Detect、Respond のいずれでもありません。

**主要機能:**
- Amazon Comprehend `DetectPiiEntities` — [12 言語](https://docs.aws.amazon.com/comprehend/latest/dg/supported-languages.html)に対応したマネージド PII 検出、数十種類のエンティティタイプ（SSN、クレジットカード番号、銀行口座番号、各国の国民 ID 番号など）
- データ最小化を前提とした設計: 検出結果にはエンティティタイプと確信度のみを記録し、マッチしたテキスト自体は一切記録しない
- 既存の任意の FSx for ONTAP S3 Access Point に対して動作 — スタンドアロンでも、[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)の FlexClone ベースの Access Point に連結して本番影響ゼロで実行することも可能
- サイズ超過ファイルは全体スキップではなく先頭数バイトのサンプリング対象となるため、大きなログ/CSV ファイルでも部分的な信号は得られる
- DynamoDB のレポート台帳が、監査時の CSF 2.0 Identify 機能エビデンスとしても機能

**実行タイミング:**
- ユーザー生成コンテンツを含むボリューム（共有、ホームディレクトリ、エクスポート）に対して定期的に実行し、PII が実際にどこにあるかの最新像を維持する
- ランサムウェア検証の直後に、[FlexClone 検証用 Access Point](verified-recovery-point-guide.md) に対して実行し、同じ隔離クローンに対して両方のスキャンを組み合わせる
- 新しい Observability/SIEM の配信先にボリュームを接続する前に、フォレンジックダッシュボードやエクスポートされたログサンプルが何を露出する可能性があるかを確認する

> **位置づけに関する補足**
>
> ここで正確な位置づけは「プレーンテキスト/構造化データ形式向けのコンテンツレベル PII 発見」であり、限定なしの「PII 発見」ではありません — 下記の[残存する限界](#残存する限界)節は、Office/PDF のコンテンツが現状スコープ外であることを明示しています。特に組織のデータが Office ドキュメント中心の場合、エンゲージメントの対話ではスキーマレベルの[データ分類ガイド](data-classification.md)を完全対応レイヤーとして先に紹介し、本スキャナーはその対応形式についてのコンテンツレベルの補完として位置づけてください。本スキャナー単独で全ファイル形式に対する包括的なコンテンツレベル PII カバレッジを提供するかのような印象は避けてください。

---

## アーキテクチャ

```
+-------------------------------------------------------------------+
| 入力: 既存の任意の S3 Access Point ARN                             |
| (スタンドアロン、または                                            |
|  verified-recovery-point-guide.md の AttachAccessPoint 出力)        |
+-------------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
| Lambda: ScannerFunction                                            |
|                                                                    |
|  ListObjectsV2 (Access Point 経由)                                 |
|       |                                                            |
|       +-> フィルタ: スキャン対象拡張子か？サイズ > 0か？             |
|              |                                                     |
|              v                                                     |
|         GetObject (通常読み取り、サイズ超過時は Range サンプリング)  |
|              |                                                     |
|              v                                                     |
|         100KB 未満の UTF-8 セグメントにチャンク分割                 |
|              |                                                     |
|              v                                                     |
|         Comprehend DetectPiiEntities (チャンクごと)                 |
|              |                                                     |
|              v                                                     |
|         entity_type -> 件数、最大確信度に集計                       |
+-------------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
| DynamoDB: 分類レポート(Access Point ごと、実行ごと)                  |
| SNS: PII 検出時の任意通知                                           |
+-------------------------------------------------------------------+
```

> **図の説明（テキストによる代替）**
>
> 単一の Lambda（`ScannerFunction`）が S3 Access Point ARN を入力として受け取ります — スタンドアロン、または検証済みクリーン復旧ポイントガイドの `AttachAccessPoint` ステップからの連携どちらでも構いません。`ListObjectsV2` でオブジェクトを一覧化し、スキャン対象拡張子かつサイズが 0 より大きいものにフィルタし、各ファイルを読み取り（サイズ超過時は Range サンプリング）、内容を 100KB 未満の UTF-8 チャンクに分割し、チャンクごとに Comprehend `DetectPiiEntities` を呼び出し、結果をエンティティタイプ別に件数と最大確信度で集計します。集計されたレポートは DynamoDB に書き込まれ、PII が見つかった場合は SNS 通知が発火します。上記の図は ASCII アートです。この段落がスクリーンリーダー利用者向けの完全なテキスト版に相当します。

---

## 分類の仕組み

### ファイル選定

読み取るのはスキャン対象拡張子を持つファイルのみです — 本スキャナーはドキュメント形式のパース（Office/PDF のテキスト抽出）は実装していません:

```
.txt  .csv  .tsv  .json  .xml  .log  .md  .yaml  .yml  .ini  .conf  .sql  .html  .htm
```

0 バイトのファイルはスキップされます。`DEFAULT_MAX_FILE_BYTES`（5 MB）を超えるファイルは全体スキップではなく **サンプリング** されます — S3 の Range `GetObject` により先頭 500 KB のみを読み取り、検出結果には `sampled: true` が記録されるため、結果が部分的であることが分かります。

> **サステナビリティに関する補足**
>
> サイズ超過ファイルに対する 500 KB のサンプリング上限（ファイル全体を読み取ってスキャンするのではなく）自体が、コスト抑制と同じくらいエネルギー節約のための設計選択です — `max_files` の上限とサンプリングの挙動が合わさって、1 回の実行あたりの Comprehend 推論負荷と S3 から読み取る総バイト数の両方を抑えています。下記の設定リファレンスにあるコストに関する補足はこの挙動の金銭コストへの影響を扱っていますが、エネルギーへの影響も直接比例します。Comprehend の推論コストとその基盤となる計算量は、いずれも処理した文字数に応じて増減するためです。複数テラバイトのボリューム全体ではなく代表的なサブセットをスキャンすること（FAQ のコスト回避のガイダンス参照）は、コストを削減する割合とほぼ同じ割合でエネルギー使用量も削減します。

### Comprehend のサイズ上限に対応するチャンク分割

[`DetectPiiEntities`](https://docs.aws.amazon.com/comprehend/latest/dg/how-pii.html) は 1 回の呼び出しあたり UTF-8 で 100 KB のサイズ上限を設けています。本スキャナーはファイル内容をその上限未満のチャンクに分割し、可能な限り行境界で分割します。これにより、エンティティ（例: メールアドレス）が不必要にチャンク境界で分断されることを避けます:

```python
# Simplified — see content_classifier.py's _chunk_text for the full
# line-boundary-aware implementation
for chunk in _chunk_text(file_text, target_bytes=98_000):
    entities = comprehend.detect_pii_entities(Text=chunk, LanguageCode="en")
```

### エンティティの集計 — データ最小化を前提とした設計

各ファイルについて、検出結果には**以下のみ**を記録します:

- エンティティの `Type`（例: `EMAIL`、`SSN`、`CREDIT_DEBIT_NUMBER`、`BANK_ACCOUNT_NUMBER` — [全タイプ一覧](https://docs.aws.amazon.com/comprehend/latest/dg/how-pii.html)）
- タイプごとの検出件数
- タイプごとに観測された最大の確信度 `Score`

マッチしたテキスト自体（実際のメールアドレスや SSN など）は、**レポート・ログ・DynamoDB のいずれにも一切書き込まれません**。これは [データ分類ガイド](data-classification.md) の疑似匿名化の指針と同じ考え方です — 「このファイルには 0.97 以上の確信度で SSN パターンのマッチが 3 件ある」というレポートは、それ自体が新たな PII の露出面になることなく、修復の優先順位付けに役立ちます。

> **プライバシーに関する補足**
>
> レポートはエンティティタイプごとに `highest_confidence_by_type` を記録しますが、本スキャナーは低確信度のマッチを自動でフィルタ・フラグ付けすることは**しません** — 確信度 0.31 の `EMAIL` 検出 1 件は、確信度 0.99 の検出 1 件と全く同じ扱いで `files_with_pii` に計上されます。`files_with_pii`/`pii_density_by_type` を規制上の PII 台帳や DPIA（データ保護影響評価）の入力として使う前に、サマリの件数だけでなく生の検出結果内の `highest_confidence_by_type` を確認し、自社の確信度の下限を適用してください — 低確信度のマッチを全て確定 PII として扱えば露出を過大に見せるリスクがあり、全てをノイズとして扱えば過小に見せるリスクがあります。どちらの判断も本スキャナーは代わりに行いません。

> **データパイプラインに関する補足**
>
> DynamoDB にレポートを書き込む前に、CloudFormation テンプレートのインライン Lambda コードは `json.loads(json.dumps(report), parse_float=str)` というラウンドトリップを行います — これは `boto3` の DynamoDB リソース API が Python 標準の `float` 値を受け付けない（DynamoDB の数値型は `Decimal` を要求する）ことへの回避策です。この副作用として、`highest_confidence_by_type` の確信度スコアは DynamoDB に**文字列**として保存され、数値としては保存されません。つまり、確信度スコアに対して数値的なフィルタやソートを行う DynamoDB クエリ（数値比較を使う `FilterExpression`、あるいは数値カラムを期待する後続の Athena/QuickSight クエリなど）は、明示的なキャスト処理なしにはこのフィールドに対して機能しません — 文字列 `"0.31"` は文字列 `"0.99"` と、期待するような数値的な比較にはなりません。このレポートテーブルの確信度スコアを基にアナリティクスやダッシュボードを構築する予定がある場合は、`put_item` の呼び出し前に Lambda 側で正しく `Decimal` にキャストして `parse_float=str` の回避策を取り除くか、下流の全クエリでこの文字列型フィールドを明示的に考慮してください。

### エラーハンドリング — 1 ファイルの失敗がスキャン全体を止めない

読み取り失敗（`AccessDenied`、`NoSuchKey`）、デコード失敗、Comprehend API エラー（`TextSizeLimitExceededException`、スロットリング）は、例外を発生させずにファイル単位の `error` フィールドに記録されます。問題のある 1 ファイルは記録されてスキップされ、ボリューム内の残りのファイルに対するスキャンは継続されます。

> **API契約に関する補足**
>
> 本スキャナーの Lambda 呼び出しの契約は、リクエスト ID やべき等キー、重複排除の仕組みを持たない、単純な JSON dict の入出力です（`{"access_point_arn": ..., "language_code": ..., "max_files": ...}` → 集計されたレポート）。同一の入力で 2 回呼び出すと、（上記の並行実行に関する補足にある `started_at` ソートキーの仕組みにより）2 つの独立した DynamoDB 行が生成され、エラーやマージされた結果にはなりません。「再実行しても常に安全で、Comprehend のコストに比べれば安価」というステートレスな発見ツールとしては適切な設計ですが、（上記のリストア検証に関する補足にある通り）本スキャナーをより大きな Step Functions ワークフローに連結し、「重複行は無害」以上の強いリトライ安全性の保証が必要な場合は、本 Lambda がべき等性を強制していると想定せず、呼び出し側のワークフローに独自のべき等トークンを追加してください。

> **リトライポリシーに関する補足**
>
> 上記のリストア検証に関する補足が示唆するように、本スキャナーを検証済みクリーン復旧ポイントガイドのワークフローの後段に Step Functions の `Task` ステートとして連結する場合、同ガイド自身のステートマシンは（同ガイド自身のリトライポリシーに関する補足の通り）どのステートにも `Catch` ブロックはあっても `Retry` ブロックはないことに注意してください — 同じステートマシンに本スキャナーを追加ステートとして挿入する場合、挿入したステートで Comprehend の一時的なスロットリングや S3 読み取りの一過性の不調が発生すると、明示的に `Retry` ブロックを追加しない限り、デフォルトでは自動リトライされずそのまま設定した `Catch` の処理に流れます。同じステートマシン内の他の場所に `Catch` ブロックが存在するからといって、周辺ワークフローのエラー処理の慣習に自動リトライが含まれていると想定しないでください。

> **並行実行に関する補足**
>
> 検証済みクリーン復旧ポイントガイドの FlexClone ワークフロー（同じボリュームに対する同時実行が共有リソース名で衝突する可能性がある）とは異なり、本スキャナーにはそのような衝突リスクはありません — DynamoDB のキー（`access_point_arn` + マイクロ秒精度の `started_at`）により、同じ Access Point に対する複数の同時呼び出しでも、互いを上書きしたり競合したりせず、単に複数の独立したレポート行が生成されます。ここでより関連性の高い同時実行上の懸念は、上流側にあります — 異なる Access Point に対して複数のスキャナー呼び出しを並行実行すると、同時に発行される `DetectPiiEntities` の呼び出しが増え、[Comprehend のアカウントレベルの秒間トランザクション数上限](https://docs.aws.amazon.com/comprehend/latest/dg/guidelines-and-limits.html)に達する可能性があります。この場合、実行全体を失敗させるのではなく、上記の `TextSizeLimitExceededException`/スロットリングエラーとしてファイル単位で記録されます。スケジュールに従って本スキャナーを多数の Access Point に対して並行実行する場合は、CloudWatch で Comprehend のスロットリングを監視し、`error` フィールドが本当の読み取り失敗ではなくスロットリングによって蓄積している場合は、呼び出しをずらして実行してください。

---

## 比較: スキーマレベル vs コンテンツレベルの分類

| 観点 | スキーマレベル（データ分類ガイド） | コンテンツレベル（本スキャナー） |
|------|--------------------------------------|-----------------------------------|
| 分類対象 | audit/FPolicy イベントのどの *フィールド* が PII か（`UserName`、`ObjectName`） | ボリューム上のどの *ファイル内容* が PII らしきデータを含むか |
| CSF 2.0 機能 | Identify（メタデータレベルの資産理解） | Identify（データレベルの資産理解） |
| メカニズム | 静的なフィールド分類マトリクス（ドキュメント） | Amazon Comprehend `DetectPiiEntities`（ML 推論） |
| 対応範囲 | 本リポジトリのパイプラインが出力する全イベントフィールド | スキャン対象拡張子に一致するテキスト/構造化データファイルのみ |
| 本リポジトリでの自動化状況 | ✅ 完全対応（リファレンス表） | ✅ 完全対応（本スキャナー） |

> **コンプライアンスに関する補足（HIPAA/FISC/SOC2）**
>
> 両方を組み合わせて使ってください。スキーマレベルのガイドは、ベンダー RBAC でどの *パイプラインフィールド* を制限すべきかを示します（`user`/`path` を表示するダッシュボードは定義上 PII を表示しています）。本スキャナーは、どの *ボリューム/ファイル* がコンテンツとして PII を含んでいる可能性が高いかを示し、DLP 制御、アクセス制限、あるいはストレージ層での正式なデータ分類ラベル付けをどこに適用すべきかの判断材料になります。いずれも、規制目的でのデータ保護責任者による手動レビューの代替にはなりません — データ分類ガイドのスコープに関する注記を参照してください。

> **訴訟保存に関する補足**
>
> これは本ガイド内のプライバシーに関する補足とは異なります — 前者は規制上の PII 台帳における判断の話であり、こちらは*スキャン自体*が訴訟対応に関係してくる場合の話です。2 つの考慮点があります。(1) あるボリュームに対して訴訟保存（litigation hold）が有効な場合、本スキャナーをそのボリュームに対して実行すること自体は保存義務に違反しません（読み取り専用かつ非破壊的です — セキュリティ考慮事項参照）。しかし、その結果生成される DynamoDB レポートとファイルパスの検出結果自体が、開示対象の資料になり得ます — スキャン実行時点でどのような機密コンテンツがどこに存在したかを記録しているためです。基盤データが保存義務の対象である場合は、スキャンレポートも同じ保存義務の対象として扱ってください。(2) 本スキャナーはマッチした PII の値自体を一切記録しません（上記のエンティティの集計を参照）。そのため、案件対応で特定の PII 値（エンティティタイプだけでなく）を開示・マスキングする必要がある場合、本スキャナーはその代替にはなりません — 通常の eDiscovery ツールに回してください。

---

## 残存する限界

本スキャナーが**行わないこと**を明確にしておきます:

1. **ドキュメント形式のパースを行わない。** Office ドキュメント（`.docx`、`.xlsx`、`.pdf`）はテキスト抽出されません — 本スキャナーは生のバイトを読み取り UTF-8 でデコードするだけであり、これはプレーンテキスト/構造化形式には有効ですが、バイナリの Office/PDF 形式では無意味な結果（有効な検出なし）になります。これを拡張するには、現時点では含まれていないドキュメントパースライブラリ（Lambda Layer 経由など）が必要です。
2. **拡張子リストは形式ベースだが、コンテンツ検出は多言語対応。** `SCANNABLE_EXTENSIONS` によるフィルタは言語ではなく形式に基づいています。Comprehend 自体は `language_code` により 12 言語をサポートしますが、本スキャナーは 1 回の実行で 1 言語のみを処理します。複数言語が混在するボリュームには、`language_code` を変えた複数回の実行、または言語検出の前処理ステップ（未実装）が必要です。

> **パッチ管理に関する補足**
>
> `COMPREHEND_SUPPORTED_LANGUAGES` と本スキャナーが依拠する PII エンティティタイプのリストは、執筆時点の Comprehend の機能に対してハードコードされています — AWS は `DetectPiiEntities` の対応言語や PII エンティティタイプを定期的に追加しています。`content_classifier.py` と CloudFormation テンプレートのインライン Lambda コードの両方で `COMPREHEND_SUPPORTED_LANGUAGES` を更新しない限り、本スキャナーは新たに対応した言語を自動的には取り込みません。自社のデータに現在の 12 言語以外の言語が増えてきている場合は、[Comprehend の対応言語ページ](https://docs.aws.amazon.com/comprehend/latest/dg/supported-languages.html)と本スキャナーのハードコードされたリストを定期的に照合してください。
3. **5 MB を超えるファイルは全体スキャンではなくサンプリング。** デフォルトでは大きなログ/CSV ファイルは先頭 500 KB のみスキャンされます — 大きなファイルの後半に現れる PII は検出されません。データの性質上これが問題になる場合は `DEFAULT_MAX_FILE_BYTES`/`sample_bytes` を増やしてください（Comprehend のコストは増加します）。
4. **コストはファイル数とサイズに比例する。** Comprehend `DetectPiiEntities` は処理したテキスト量に応じて課金されます。スキャン対象ファイルが多い/大きい大規模ボリュームをスキャンすると、相応の Comprehend 費用が発生する可能性があります — `max_files` は、1 回の実行あたりのこの費用を制限するために存在します。

> **キャパシティ計画に関する補足**
>
> `max_files` は 1 回の実行あたりのコストと実行時間を制限しますが、これは件数の上限であり、スループットの上限ではありません — 本スキャナーは `DetectPiiEntities` を、チャンクごとに同期的に呼び出しており、スロットリング発生時にファイル単位の `error` として記録される以外に、明示的なレート制限やバックオフの仕組みはありません（上記のエラーハンドリング参照）。多数の Access Point に対してスケジュールに従って本スキャナーを同時実行する予定がある場合（上記の並行実行に関する補足参照）、`max_files` だけが同時実行全体の Comprehend スループットを制限してくれると想定せず、他の共有クォータを持つ AWS サービスと同様に、想定される同時呼び出し数を [Comprehend のアカウントレベルの TPS クォータ](https://docs.aws.amazon.com/comprehend/latest/dg/guidelines-and-limits.html)と比較検討してください。
5. **ファイル変更時の自動再スキャンはない。** これはオンデマンド/スケジュール実行のスキャナーであり、イベント駆動ではありません。定期的な再分類が必要な場合は、[自動応答ガイドのデプロイ節](automated-response-guide.md#デプロイ)にある TTL クリーンアップスケジュールと同じパターンで、独自の EventBridge Scheduler ルールを組み合わせてください。
6. **スキャン途中で Lambda がタイムアウトすると、未スキャン分だけでなく実行全体の検出結果が失われる。** 本スキャナーは `ListObjectsV2`/分類ループが完全に終わった後に一度だけ DynamoDB レポートを書き込みます — 途中経過をチェックポイントする仕組みはありません。大きなボリュームのスキャン途中で `LambdaTimeoutSeconds` を超えると、`put_item` の呼び出し前に Lambda が強制終了され、その実行に対するレポートは（部分的なものすら）一切記録されません。想定するファイル数と平均ファイルサイズに応じて `LambdaTimeoutSeconds` と `max_files` を余裕を持って設定し、「スキャンした結果 PII がなかった」のか「スキャンが完了しなかった」のかを見分けるために、DynamoDB テーブルだけでなく CloudWatch Logs も確認してください。

> **リストア検証に関する補足**
>
> 本スキャナーを[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)のワークフローに連結する場合、実行順序が重要です — 同ガイドのワークフローの `Cleanup` ステップは、成功・失敗どちらの経路でも必ず S3 Access Point をデタッチし FlexClone を削除します（同ガイドのアーキテクチャ参照）。本スキャナーの分類処理は、そのワークフローが `Cleanup` に到達する**前**に呼び出してください — そのステートマシンにステートを追加するか、同一実行の `access_point_arn` 出力を使った同期的な後続呼び出しとして実装します。ワークフローが完了した後に呼び出すと、Access Point が既に存在しないため失敗します。

---

## 前提条件

### AWS 権限

Lambda 実行ロールには以下が必要です:

```
# S3 Access Point オブジェクト読み取り
- s3:ListBucket / s3:GetObject (arn:aws:s3:*:*:accesspoint/* にスコープ)

# Amazon Comprehend(リソースレベル権限はサポートされていません —
# DetectPiiEntities はアドレス可能なリソースを持たない
# ステートレスな同期推論 API です)
- comprehend:DetectPiiEntities

# 分類レポート台帳
- dynamodb:PutItem (レポートテーブルにスコープ)
```

> **IAM設計に関する補足**
>
> `ScannerLambdaRole` は `s3:ListBucket`/`s3:GetObject` を `arn:aws:s3:*:*:accesspoint/*` にスコープして許可しています — これはスキャン対象として意図した 1 つの Access Point だけでなく、アカウント/リージョン内の *全ての* Access Point を横断するワイルドカードです（CloudFormation テンプレート参照。これが必要な理由は、Access Point の ARN がデプロイ時ではなく呼び出し時にのみ判明するため、ロールの静的ポリシーではこれより絞った `Resource` を表現できないことにあります）。実運用上これが意味するのは、意図したものと異なる `access_point_arn` を指定して呼び出せば、このスキャナーの実行ロールはアカウント内の*任意の* S3 Access Point から読み取れるということです — 「この 1 つのスキャナー、この 1 つの Access Point」という想定より広い到達範囲になります。組織の IAM 標準が柔軟性を犠牲にしてもリソースレベルのスコープ設定を求める場合は、本スキャナーを呼び出す前に `access_point_arn` を許可リストと照合するラッパー Lambda や Step Functions タスクの追加を検討してください — IAM ポリシー単独ではこの制約を強制できないためです。

### 既存の S3 Access Point

本スキャナーは S3 Access Point を**作成・管理しません** — 既に存在する Access Point の ARN を渡してください。一般的な取得元は 2 つあります:

- 本番または DR ボリュームに対して直接管理している Access Point
- [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)の `AttachAccessPoint` ステップの出力（`access_point_arn`）— ランサムウェア検証に使用した同じ FlexClone をスキャンする場合

> **ネットワーク起点によって `VpcId` の指定が必要かが決まる**
>
> `AttachAccessPoint` が作成するのは [VPC 限定の Access Point](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/access-points-for-fsxn-vpc.html) であり、これは束縛された VPC の外からは一切到達できません — IAM 権限の有無に関わらず、VPC 外の Lambda はそもそもネットワーク経路を持ちません。インターネット起点の Access Point（`VpcConfiguration` を持たない Access Point）は VPC なしでも到達可能です。本スタックがどちらのモードでデプロイされるかを制御する `VpcId` パラメータについては、下記の[デプロイ](#デプロイ)を参照してください。

---

## デプロイ

### デプロイモード1: スタンドアロン（インターネット起点の Access Point）

`VpcConfiguration` を持たない Access Point をスキャンする場合に使用します — VPC Endpoint を作成しないため、よりシンプルで低コストなモードです:

```bash
aws cloudformation deploy \
  --template-file shared/templates/content-classification-scanner.yaml \
  --stack-name fsxn-content-classification \
  --parameter-overrides \
    DefaultLanguageCode=en \
    DefaultMaxFiles=500 \
    NotificationTopicArn=<optional-sns-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM
```

### デプロイモード2: VPC 内（VPC 限定の Access Point）

VPC 限定の Access Point をスキャンする場合に使用します — [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)の `AttachAccessPoint` ステップに連結する場合は必須です。同ステップが作成する Access Point は常に VPC 限定であるためです:

```bash
aws cloudformation deploy \
  --template-file shared/templates/content-classification-scanner.yaml \
  --stack-name fsxn-content-classification \
  --parameter-overrides \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-1>,<subnet-2> \
    SecurityGroupId=<sg-id> \
    RouteTableIds=<route-table-1>,<route-table-2> \
    DefaultLanguageCode=en \
    DefaultMaxFiles=500 \
    NotificationTopicArn=<optional-sns-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM
```

`VpcId` を設定すると、スキャナー Lambda（`VpcConfig` が追加される）とスタックのリソース（`CreateVpcEndpoints=false` を明示しない限り、S3/DynamoDB の Gateway Endpoint と Comprehend/SNS の Interface Endpoint が追加される）の両方が変化します。`restore-verification.yaml` の VPC を再利用する場合でも、そのスタック自身が用意するのは Secrets Manager/STS/FSx の Endpoint のみで、この 4 つとは異なるため注意してください。

### スタックが作成するリソース

- Lambda 関数 1 個（`{stack-name}-scanner`）— `VpcId` の有無により `VpcConfig` の有無が変わる
- DynamoDB レポートテーブル（`{stack-name}-reports`）
- CloudWatch Logs（365 日保持 — レポートがコンプライアンスエビデンスも兼ねるため）
- `VpcId` が設定され `CreateVpcEndpoints=true`（デフォルト）の場合: S3 Gateway Endpoint、DynamoDB Gateway Endpoint、Comprehend Interface Endpoint、SNS Interface Endpoint

> **変更管理に関する補足**
>
> 検証済みクリーン復旧ポイントガイドのスタックと同様、本デプロイも純粋に追加的です — 新しい Lambda、新しい DynamoDB テーブル、（デプロイモード2の場合）新しい VPC Endpoint を作成するのみで、既存の FSx for ONTAP リソース、S3 Access Point、ネットワーク構成は一切変更しません。本スキャナーは S3 Access Point を自ら作成・管理することはなく（上記の前提条件参照）、指定された既存の Access Point から読み取るだけなので、デプロイ失敗時の影響範囲は本スタック自身のリソースに限定されます。ロールバックは `cloudformation delete-stack` により、スキャン対象の Access Point やボリュームに触れることなくこれらを削除します。復旧検証スタックと同様、デプロイモード2を、既にこれら 4 つの Endpoint のいずれかを他のスタックから持っている可能性がある VPC にデプロイする場合は、VPC Endpoint の重複競合リスクを変更チケットに明記してください。

> **リソースタグ付けに関する補足**
>
> `restore-verification.yaml` と同様、本スタックの CloudFormation テンプレートでも `ClassificationReportTable` のみが `Project`/`Purpose` のタグペアを持ち、`ScannerFunction` Lambda には `Tags` プロパティが設定されていません。アカウントが AWS Config のルールやサービスコントロールポリシーでタグ付けポリシーを強制している場合は、統制が及んでいるアカウントにデプロイする前に `AWS::Lambda::Function` リソースに同等の `Tags` ブロックを追加してください。追加しない場合、この Lambda の計算コストは DynamoDB テーブルのコストとは異なり、コスト配分タグを通じて帰属先を特定できません。

### スキャナーの呼び出し

```bash
aws lambda invoke \
  --function-name fsxn-content-classification-scanner \
  --payload '{
    "access_point_arn": "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/verify-vol-data-20260710",
    "language_code": "en",
    "max_files": 500
  }' \
  --cli-binary-format raw-in-base64-out \
  response.json
```

### 復旧検証との連結

本番データではなく、ランサムウェア検証に使用した同じ隔離 FlexClone をスキャンするには、[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)の `AttachAccessPoint` Step Functions タスクが出力する `access_point_arn` を使ってこの Lambda を呼び出してください。そのステートマシンにステートを追加するか、同一実行の出力を使った別の後続呼び出しとして実装できます。

> **この連結パターンにはデプロイモード2（VPC 内）が必須です**
>
> `AttachAccessPoint` は常に VPC 限定の Access Point を作成し、本スキャナーはそれに到達するために同じ VPC 内で実行される必要があります — 上記[前提条件](#既存の-s3-access-point)のネットワーク起点に関する注記を参照してください。本スタックをスタンドアロンモード（`VpcId` 未指定）で連結先の FlexClone Access Point に対してデプロイすると、`ListObjectsV2` の呼び出しで毎回確実に失敗します。ネットワーク経路自体が存在しないため、間欠的な失敗ではありません。

### 動作確認（Quick Validation）

本スキャナーを実データに向ける前に、合成ファイルを使ってエンドツーエンドで動作するか確認してください — 以下の手順は、本プロジェクト自身の E2E 検証で実際に使用したものと同一です（デプロイモード1: スタンドアロン、`VpcId` 未指定に対して実施）:

```bash
# 1. Create a synthetic PII file — no real personal data, safe to commit
#    to a scratch location or delete immediately after the test
cat > /tmp/pii-test-sample.txt <<'EOF'
Support Ticket #48213
Name: John Sample Doe
Email: john.sample.doe@example.com
Phone: 555-0142-9981
SSN: 078-05-1120
Address: 123 Example Street, Springfield, IL 62704
EOF

# 2. Upload it through an S3 Access Point that has write access to your
#    FSx for ONTAP volume (a read-only access point, if that's what you
#    have, will reject the PutObject — see Prerequisites above for the
#    read-only-vs-read-write access point distinction)
aws s3api put-object \
  --bucket <your-access-point-arn> \
  --key validation/pii-test-sample.txt \
  --body /tmp/pii-test-sample.txt

# 3. Invoke the scanner directly (bypasses any EventBridge/Step Functions
#    trigger you may wire up later, isolating the Lambda + Comprehend path)
aws lambda invoke \
  --function-name fsxn-content-classification-scanner \
  --payload '{"access_point_arn":"<your-access-point-arn>","max_files":50}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/scan-response.json
cat /tmp/scan-response.json

# 4. Confirm the finding landed in the ledger, not just the Lambda response
aws dynamodb get-item \
  --table-name fsxn-content-classification-reports \
  --key '{"access_point_arn":{"S":"<your-access-point-arn>"},"started_at":{"S":"<started_at from step 3 response>"}}'

# 5. Clean up the synthetic file so it doesn't linger in production storage
aws s3api delete-object --bucket <your-access-point-arn> --key validation/pii-test-sample.txt
rm -f /tmp/pii-test-sample.txt /tmp/scan-response.json
```

成功した実行では `"files_with_pii": 1`（Access Point 配下に他のスキャン対象ファイルが既にあれば、それ以上の値）が報告され、`findings` に `pii-test-sample.txt` が含まれ、`NAME`/`EMAIL`/`PHONE`/`SSN`/`ADDRESS` のエンティティタイプが、整形されたフィールド（`EMAIL`、`PHONE`、`SSN`、`ADDRESS`）では通常 0.9 を超える確信度で、`NAME` ではそれより低い確信度で（Comprehend の氏名検出の確信度は、他のタイプよりも周辺の文脈に依存して変動しやすいため）検出されているはずです。手順3がタイムアウトするか、代わりにアクセス拒否エラーが返る場合は、[前提条件](#既存の-s3-access-point)を再確認してください — 最も多い原因は、スタンドアロン（VPC 外）デプロイから VPC 限定の Access Point ARN を呼び出している、またはその逆のケースです。

---

## 設定リファレンス

| パラメータ | デフォルト | 用途 |
|-----------|-----------|------|
| `DefaultLanguageCode` | en | Amazon Comprehend `DetectPiiEntities` の言語コード（12 言語対応） |
| `DefaultMaxFiles` | 500 | 1 回の実行あたりのスキャン対象ファイル数の上限（コスト/実行時間の制御）。呼び出しペイロードの `max_files` キーで実行ごとに上書き可能 |
| `LambdaMemorySize` | 512 MB | スキャナー Lambda のメモリサイズ |
| `LambdaTimeoutSeconds` | 600 | スキャナー Lambda のタイムアウト — 想定ファイル数に応じて調整 |

> **コストに関する補足**
>
> [Amazon Comprehend の同期 PII 検出の課金](https://aws.amazon.com/comprehend/pricing/)は、処理した 100 文字単位ごとに、リクエストあたりの最低課金額とともに計算されます — コストはファイル数ではなく総スキャンコンテンツ量に応じて増減するため、少数の非常に大きなテキストファイルの方が、多数の小さなファイルよりコストが高くなることがあります。Lambda の実行コストと DynamoDB `PutItem` のコストは、まとまった量のスキャンを行う場合の Comprehend 課金に比べれば無視できる程度です。大規模なボリュームに対して実行する前に、代表的なサンプル（`max_files` を低く設定して 1 部門分の共有フォルダをスキャンし、スキャンされた総サイズを記録して外挿する）からコストを見積もってください。ファイル単位で固定コストを想定しないようにしてください — [残存する限界](#残存する限界)項目4と FAQ の関連する記述も参照してください。

---

## セキュリティ考慮事項

- **設計によるデータ最小化** — 検出結果にはマッチした PII テキストが一切含まれません — エンティティタイプ、件数、確信度スコアのみです。上記の[エンティティの集計](#エンティティの集計--データ最小化を前提とした設計)を参照してください。
- **スキャン対象ボリュームへの書き込みアクセスなし** — スキャナーは `s3:GetObject`/`s3:ListBucket` のみを呼び出します — スキャン対象のファイルを変更、マスキング、削除することはできません。
- **レポートテーブルへのアクセスは制限すべき** — レポート自体には PII の値は含まれませんが、PII らしきデータが *どこで*（ファイルパス）*どの程度*見つかったかは含まれます — 機密データの所在に関する一覧と同様のアクセス制限を適用してください。
- **Comprehend はリージョナルな AWS マネージドサービス** — `DetectPiiEntities` に送信されたテキストは、呼び出し先の AWS リージョン内で処理されます。サービス自体のデータ取り扱いに関するコミットメントについては、[Amazon Comprehend のデータプライバシードキュメント](https://docs.aws.amazon.com/comprehend/latest/dg/data-privacy.html)を参照してください。

> **データレジデンシーに関する補足**
>
> 本スキャナーが読み取るファイルの内容は、スタックをデプロイした AWS リージョンの外に一切出ません — Lambda、`DetectPiiEntities` の呼び出し、DynamoDB のレポートテーブルは全てリージョナルなリソースであり、上記の [Comprehend のデータプライバシーの項目](https://docs.aws.amazon.com/comprehend/latest/dg/data-privacy.html)でも、Comprehend 自体が呼び出したリージョン外でデータを処理しないことが確認されています。これはレジデンシーに関するアンケートに対する明快な肯定的回答になります。一点補足すると、マルチリージョンのフリートに対して本スキャナーを複数リージョンにデプロイする場合、各リージョンの DynamoDB レポートテーブルは独立しています — 分類レポートのリージョン横断レプリケーションや集約は組み込まれていないため、各リージョンのスキャン結果をそのリージョン内に留めるというレジデンシー主導の判断は、既にデフォルトの挙動であり、追加で設定する必要はありません。
- **VPC 限定の Access Point は束縛された VPC の外からは経路を持たない** — これは Access Point 自体が持つネットワーク上の性質であり（[AWS のネットワーク起点比較](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/configuring-network-access-for-s3-access-points.html)参照）、IAM だけで回避できるものではありません。VPC 限定の Access Point をスキャンする場合は、`VpcId` を設定して本スタックをデプロイし（デプロイモード2）、本スタックが作成する S3 Gateway Endpoint 経由で、スキャナー Lambda が同じ VPC 内から S3 へのルートを持つようにしてください。

> **サードパーティリスクに関する補足**
>
> 「データは第三者に送信されるか」「データはどこで処理されるか」を問うベンダー評価アンケートに対しては — 本パイプラインがファイル内容を送信する先は Amazon Comprehend という AWS のファーストパーティ・マネージドサービスであり、外部のサードパーティベンダーではありません。処理はデプロイ先の AWS リージョン内に留まります（上記のデータプライバシーの項目参照）。データ保持期間について: 本スキャナーは自身で何かを削除することはありません — DynamoDB のレポートテーブルにはデフォルトで TTL が設定されていないため、スタックを削除するか独自の TTL 属性を追加しない限り、分類レポートは無期限に保持されます。レポートが自社のデータ保持スケジュールの対象である場合はこれを踏まえてください。レポート自体には生の PII 値は含まれませんが、ファイルパス（データ分類ガイドでは Sensitive に分類）は含まれるためです。

> **監査エビデンスに関する補足**
>
> 本スキャナーのレポートがデータ分類/PII 台帳に関する統制のエビデンスとして引用される場合、運用の実効性を検証する監査担当者は、DynamoDB の行そのものに加えて次のようなものを求めます: (1) 統制が主張する頻度（定期実行、明確なトリガーに基づくオンデマンド実行など）でスキャンが実際に実行されたことのエビデンス — 本スキャナーには組み込みのスケジュール機能がないため（残存する限界の項目5参照）、「定期的」を保証するスケジュールはこの Lambda の外側にあり、別途エビデンス化する必要があります（例: EventBridge ルール自体の CloudFormation 定義とその CloudWatch 呼び出し履歴）。(2) `files_with_pii > 0` を示すレポートを誰かがレビューし、文書化された対応を取ったというエビデンス — 本スキャナーの SNS 通知はアラートが発火したエビデンスであり、人間が対応したエビデンスではありません。検証済みクリーン復旧ポイントワークフロー自身の監査エビデンスに関する補足と同様に、本スキャナーが正式なコンプライアンス統制を裏付ける場合は、自動化されたレポートと手動のレビュー/アテステーションのアーティファクトを組み合わせてください。

---

## テスト

`content_classifier.py` モジュールには 23 のユニットテストがあります:

| カテゴリ | 検証内容 |
|---------|---------|
| テキストチャンク分割 | 空入力、単一チャンク、複数チャンク分割、内容を保持した再結合、上限を超える単一行の強制分割、チャンク目標値が Comprehend の上限未満であること |
| ファイル単位の分類 | PII なし、集計された件数/確信度を伴う PII 検出、複数チャンクにわたる集計、読み取り失敗の処理、Comprehend 失敗の処理、空ファイルのスキップ、サイズ超過ファイルの Range サンプリング、確信度の四捨五入 |
| ボリュームレベルのオーケストレーション | 非対応言語の拒否、スキャン対象外拡張子のフィルタリング、0 バイトファイルのスキップ、PII 密度の集計、クリーンなファイルの検出結果からの除外、エラーファイルの検出結果への含有、`max_files` 上限の適用、レポートシリアライズの上限処理、サンプリングされたファイルの個別トラッキング |

```bash
python3 -m pytest shared/python/tests/test_content_classifier.py -v
# 23 passed in 0.08s
```

> **テストカバレッジに関する補足**
>
> 23 件のテストは全て `s3`/`comprehend` の boto3 クライアントを直接モックしており、実際の S3 Access Point や Amazon Comprehend エンドポイントを呼び出すテストはありません。これによりテストスイートは高速かつ AWS 認証情報に依存せず実行できますが、その反面、本モジュール*自身*のロジック（チャンク分割、集計、エラーハンドリング）が正しいことは検証できても、モックされた Comprehend レスポンスの形式が実際の `DetectPiiEntities` API と一致し続けているかは検証できません。`boto3`/`botocore` のバージョンアップ後は、実際の（本番ではない）Access Point と Comprehend エンドポイントに対する手動のスモークテストを別途実施し、`pytest` の成功だけを Lambda が実際の AWS 環境で同じように動作する証拠として扱わないでください。

> **ライセンスに関する補足**
>
> 復旧検証スタックと同様、本スキャナーの唯一の実行時依存関係（`boto3`、`botocore`）は `requirements.txt` にベンダリングされているのではなく Lambda の `python3.12` マネージドランタイムに同梱されています。いずれも Apache License 2.0 であり、コピーレフト義務のない許諾性の高いライセンスであるため、ライセンスリスクは低いです。自動 SBOM/ライセンススキャンについても同じ注意点が当てはまります — 本スタックのインライン CloudFormation `ZipFile` コードをスキャンしても、本リポジトリ内のどこにも依存関係が宣言されていないため、これらは検出されません。サプライチェーンレビューの要件がある場合は、代わりに Lambda マネージドランタイム自身が公開しているマニフェストに対してスキャンしてください。

> **オブザーバビリティに関する補足**
>
> 本スタックが用意するのは CloudWatch Logs のみです — Lambda のエラー・スロットリング・タイムアウトに対する CloudWatch Alarm はありません。[残存する限界](#残存する限界)項目6で述べた「タイムアウトすると実行全体の結果が失われる」という挙動を踏まえ、この Lambda の `Errors`/`Duration` メトリクスに独自のアラームを追加するか、PII 検出通知用に既に配線されている SNS トピックに失敗も流すようにしてください。そうしないと、静かにタイムアウトしたスキャンが「PII なし」と誤って解釈されてしまいます。

> **運用トリアージに関する補足**
>
> 本スキャナーは設計上、呼び出し対応を必要とするサービスではありません — オンデマンド/スケジュール実行のバッチジョブであり（[残存する限界](#残存する限界)項目5参照）、SLO を持つリクエスト処理パイプラインではありません。スケジュール実行が失敗してアラートを受けた場合は、まず CloudWatch Logs でその実行内容を確認してください（DynamoDB テーブルだけを見ると誤解しやすい理由については FAQ の「検出件数ゼロ」の項目を参照）。その後、根本原因（タイムアウト、スロットリング、または `restore-verification.yaml` に連結している場合は Access Point が既に削除・失効している可能性）に対処した上で、同じ `access_point_arn` を使って手動で再実行してください。失敗時にクリーンアップすべき台帳の行や、心配すべき残置リソースはありません — 本スキャナーはインフラを一切作成せず、他者が管理する Access Point から読み取るだけです。

> **障害注入に関する補足**
>
> 本スキャナーに対する有用な障害注入テスト: 複数ファイルのボリュームに対する実行の途中で `comprehend:DetectPiiEntities` をスロットリングまたは拒否させ（例: 一時的な IAM 拒否）、[残存する限界](#残存する限界)項目6が説明する実際の挙動 — 注入した障害の*前に*スキャンされたファイルの結果も、後のファイルの結果と同様に失われる（後のファイルだけが失われるわけではない）こと — を確認してください。これはドキュメントだけを信頼するのではなく実験的に確認する価値があります。「未スキャン分だけを失う」と「実行全体を失う」は、コードを素早く読んだ際に取り違えやすい前提だからです。もう一つの有用な実験は、大きな `max_files` バッチの途中で強制タイムアウトを発生させ、CloudWatch Logs にどのファイルまで進んでいたか、何件完了したかを十分に判別できる詳細が残るかを確認することです — 再スキャンせずにログだけからその状況を「PII なし」のクリーンな実行と見分けられるかどうかです。

> **SNS 配信に関する補足**
>
> 復旧検証ワークフローの `RecordVerdict` Lambda と同様に、本スキャナーの SNS `publish` 呼び出しも、失敗時に警告ログを出すだけの単純な try/except でラップされています（`logger.warning("Notification failed: %s", e)`）— publish の失敗（トピック削除、権限のドリフト、スロットリング）は、Lambda のエラーや失敗した呼び出しとしては一切表面化せず、ログ行のみに残ります。本スキャナーは `files_with_pii > 0` の場合にのみ `sns.publish` を呼び出すため（CloudFormation テンプレートのインライン handler コード参照）、実際に PII を含むボリュームに対する通知が静かに失敗した場合、外部からは「PII なし」のスキャンと完全に同じ見え方になります — どちらも「通知が来なかった」という結果になるためです。この通知の確実な発火に PII 発見プロセスが依存している場合は、通知の欠如をクリーンなスキャンの証拠として扱わず、この Lambda 自身の `"Notification failed"` ログパターンに対してアラームを設定してください。

> **保管時暗号化に関する補足**
>
> `ClassificationReportTable` の `SSESpecification` は暗号化を有効にしていますが（復旧検証スタックの台帳テーブルと同様）、カスタマー管理の KMS キーは設定されておらず、AWS 所有の DynamoDB キーが使用されます。データ分類プログラムが、センシティブなコンテンツの所在を記録する何らかのリソース（本レポートテーブルも該当します。PII の値自体は除外していますが — セキュリティ考慮事項参照）にカスタマー管理 KMS キーを要求する場合は、`SSEType: KMS`/`KMSMasterKeyId` を明示的に追加してください。現在のテンプレートはこれをパラメータとして公開していません。

---

## 関連ドキュメント

- [サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md#identify識別) — 本スキャナーが対応する Identify 機能のギャップ
- [データ分類ガイド](data-classification.md) — 本スキャナーがコンテンツレベルで補完する、スキーマレベル（フィールド名）の分類
- [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) — 本スキャナーが連結でき、本番への影響ゼロでスキャンできる FlexClone + S3 Access Point パターン
- [自動インシデント対応ガイド](automated-response-guide.md) — 保護 Snapshot が復旧検証ワークフロー経由での自然なスキャン対象となる、封じ込めフェーズのモジュール
- [ガバナンス・コンプライアンス](governance-and-compliance.md) — 分類レポートが Identify 機能のエビデンスとしてどう位置づけられるか

## FAQ

**Q: 本スキャナーはファイルから PII をマスキング・削除しますか？**
A: いいえ。これは発見専用のツールであり、CSF 2.0 の Identify 機能のスコープ（棚卸しと理解、修復ではない）に一致します。マスキングには、本モジュールが意図的に実装していない別の書き込み経路が必要になります。

**Q: FlexClone だけでなく、本番ボリュームを直接スキャンできますか？**
A: できます — 本番ボリュームに直接接続した Access Point を含め、任意の S3 Access Point ARN を渡せます。[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)の FlexClone パターンは、本番への読み取り負荷影響をゼロにしたい場合やランサムウェア検証とスキャンを組み合わせたい場合に推奨されますが、必須ではありません。

**Q: なぜ Office ドキュメントや PDF はスキップされるのですか？**
A: 本モジュールは、ドキュメントパースライブラリへの依存を避けるため、意図的にプレーンテキストと構造化データ形式にスコープを絞っています。ボリュームの内容が Office/PDF 中心の場合は、Amazon Textract やドキュメント抽出用の Lambda Layer を前処理ステップとして検討し、本スキャナーの `classify_object` ロジックに生バイトではなく抽出済みテキストを渡すようにしてください。

**Q: 非常に大きなボリュームで Comprehend のコストが過大にならないようにするには？**
A: `max_files` で 1 回あたりのスキャン範囲を制限し、複数テラバイトのボリューム全体を一度にスキャンするのではなく、代表的なサブセット（例: 特定部門の共有フォルダ）に対して実行することを検討してください。Comprehend の課金は処理したテキスト量に応じるため、コストはファイル数だけでなくスキャンした総コンテンツ量に比例します。

**Q: スタンドアロン（`VpcId` 未指定）でデプロイしたところ、毎回 `ListObjectsV2` で失敗します。なぜですか？**
A: 渡している `access_point_arn` はほぼ確実に VPC 限定です（多くの場合、[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)の `AttachAccessPoint` ステップが出力したものです）。VPC 限定の Access Point は、束縛された VPC の外からはネットワーク経路自体を持ちません — これは IAM 権限に関わらず確定的に失敗するもので、間欠的な問題ではありません。`VpcId`/`SubnetIds`/`SecurityGroupId`/`RouteTableIds` を設定して（デプロイモード2）、スキャナーが Access Point と同じ VPC 内で実行されるように再デプロイしてください。

**Q: PII エンティティの `Score`（確信度）が低い場合、無視してよいですか？**
A: 自動では判断できません — 本スキャナーは確信度の下限を自動適用しません（[エンティティの集計](#エンティティの集計--データ最小化を前提とした設計)の プライバシーに関する補足を参照）。ファイル・エンティティタイプごとに観測された最大確信度を記録するだけなので、どの確信度を「検出された」PII とみなすかは、規制上の文脈に応じて*利用者側*が判断してください。

**Q: 大きなボリュームをスキャンしたのに DynamoDB の検出件数がゼロです。本当に実行されたのでしょうか？**
A: 「PII が見つからなかった」と結論する前に、その実行の CloudWatch Logs を確認してください。レポートは handler の最後で一度だけ書き込まれるため（[残存する限界](#残存する限界)項目6参照）、大きなスキャンの途中で Lambda がタイムアウトした場合も、DynamoDB 側では本当に何も見つからなかった場合と同じ症状（レポート行がない）になります。両者は Lambda 自身のログ/実行時間メトリクスでのみ区別できます。

**Q: ユーザーから「PII が全く見つからないと言われたが、明らかに機密データがあるはずだ」と報告されました。エスカレーションする前の最初の切り分け手順は？**
A: `files_with_pii` だけでなく、まず応答内の `files_scanned` と `files_skipped_unscannable` の件数を確認してください。よくある原因は、ボリュームが `SCANNABLE_EXTENSIONS` 以外の拡張子（Office ドキュメント、PDF、あるいはリストにないテキスト形式）中心である場合で、この場合は多くのファイルが「スキャンしてクリーンと判定された」のではなく「スキップされた」だけです（[残存する限界](#残存する限界)項目1と FAQ の Office/PDF に関する項目参照）。2 つ目のよくある原因は、実際に PII を含むファイルに到達する前に `max_files` の上限に達してしまったケースです（上記の キャパシティ計画に関する補足参照）— `files_scanned` が設定/リクエストされた `max_files` と一致しているかを確認してください。一致していれば、ボリュームを使い切ったのではなく上限に達したことを示します。

**Q: 未スキャンだった既存の FSx for ONTAP ボリュームを本スキャナーに新規オンボーディングする場合、初回実行で特に注意すべき点は？**
A: 保守的な `max_files` の値で最初のスキャンを実行し、定期実行をスケジュールする前に、結果の `files_skipped_unscannable`/`files_sampled_too_large` の件数を確認してください — これにより、本スキャナーが実際にそのボリュームのコンテンツをどの程度カバーできているか（[残存する限界](#残存する限界)参照）、ドキュメントパースの拡張やより大きな `sample_bytes` の値が必要かどうかが、事前に分かります。本スキャナーがボリュームに対して事前設定を要求する状態やメタデータはありません — 既存の任意の S3 Access Point で即座に動作します。

**Q: PII が検出されたのに誰も通知を受けていません — 「PII なし」と「通知の失敗」をどう見分ければよいですか？**
A: 通知の到着を代わりの判断材料にするのではなく、DynamoDB のレポート行の `files_with_pii` フィールドを直接確認してください — 本スキャナーは `files_with_pii > 0` の場合にのみ SNS publish を試み、その publish 呼び出しは静かに失敗する可能性があります（セキュリティ考慮事項の SNS 配信に関する補足参照）。レポート内の `files_with_pii` が 0 より大きいにもかかわらず通知が届いていない場合は、スキャン自体は正しく動作しており、通知配信側が失敗しています — CloudWatch Logs で `"Notification failed"` の行を確認してください。
