# サイバーレジリエンス機能マップ — NIST CSF 2.0 機能マッピング

🌐 **日本語**（このページ） | [English](../en/cyber-resilience-capability-map.md)

## このドキュメントの目的

本ドキュメントが答えるべき本質的な問いは、製品比較の問いではなくサイバーレジリエンスの問いです — **NIST CSF 2.0 の 6 機能それぞれについて、本リポジトリは FSx for ONTAP ワークロードに対して何を提供しているのか、既存の要素を組み合わせれば実現できる部分はどこか、そして本当に未対応のまま残っている部分はどこか**。

以前のバージョンでは、本ドキュメントは特定の 1 ベンダーツール — NetApp DII（Data Infrastructure Insights）Storage Workload Security — を軸に構成され、本リポジトリの機能をフェーズごとに DII と比較する形になっていました。この構成は比較対象を中心に据え、フレームワーク側を背景に押し込めてしまうもので、実際の優先順位を反転させていました。本来、[NIST Cybersecurity Framework（CSF）2.0](https://www.nist.gov/cyberframework) こそが本リポジトリが満たすべき構成の骨格であり、DII SWS や AWS 自身が公開しているランサムウェア対応ガイダンス、その他のツールは、それぞれの CSF 機能をどう実現するかについての「参照例の一つ」に過ぎず、他の全てを測る基準ではありません。

本ドキュメントは、CSF 2.0 の 6 機能 — **Govern（統制）、Identify（識別）、Protect（保護）、Detect（検知）、Respond（対応）、Recover（復旧）** — を骨格として構成します。各機能セクションでは以下を扱います:

1. **その機能が要求すること** — CSF 2.0 に基づき、ランサムウェアに特化した文脈では [NIST IR 8374r1 — Ransomware Risk Management: A Cybersecurity Framework 2.0 Community Profile](https://csrc.nist.gov/pubs/ir/8374/r1/final) も参照
2. **本リポジトリが FSx for ONTAP に対してどう実現しているか** — 本ドキュメントの主要な内容であり、これが本リポジトリの実際のスコープです
3. **代替の実装パス** — 別の AWS ネイティブサービスや SaaS/サードパーティ製品が同じ CSF 機能要件を満たせる場合、それを選択肢として提示します（データレジデンシー制約、既存ツールへの投資、チームのスキルセットなど文脈に応じた選択肢として）。本リポジトリのアプローチに対する競合として位置づけるものではありません
4. **ステータス**: ✅ 完全対応、⚠️ 組み合わせが必要、❌ ギャップ — 以前と同じ規律を維持し、参照するいずれのツールに対しても対応度を過大に見せません

> **エビデンスの区分**
>
> サードパーティツール（DII SWS、Splunk、Datadog など）に関する記述は各ベンダーの公開ドキュメントに基づき、各記述に出典リンクを付記しています。AWS サービス（Macie、GuardDuty、AWS Backup、Config）に関する記述は AWS の公開ドキュメントに基づき、各記述に出典リンクを付記し、執筆時点の実際のサービス挙動と照合しています。本リポジトリ自身の機能に関する記述は、例外を明記しない限り、このコードベースで実装済み・E2E 検証済みの機能を指します。

> **オンボーディングに関する補足**
>
> 本リポジトリに初めて触れる場合は、個別の実装ガイドに進む前に、まず 6 つの機能セクションを上から下まで通読してください — ✅/⚠️/❌ のステータスマークを見れば、その機能がすぐデプロイ可能か、組み合わせ作業が必要か、そもそも未提供かが事前に分かります。興味のあるセクションを見つけたら、最も早くデプロイまで到達する道筋は通常、リポジトリをクローンし、そのセクションがリンクする実装ガイド自身の「前提条件」と「デプロイ」の節を読むことです。より広い CSF 2.0 の文脈が必要になった場合にのみ、本ドキュメントに戻ってきてください。

## NIST CSF 2.0 の概要

CSF 2.0 は、組織の *全体* のサイバーセキュリティリスク管理プログラムを 6 つの機能に整理しています。クラウド・ストレージベンダー各社も、自社ツールがどの機能をカバーし、どこが依然として組織側の責任かを利用者に示すために、自社の CSF マッピングを公開する慣行があります — 本ドキュメントもこの慣行に従います。AWS は [Ransomware Risk Management on AWS Using the NIST CSF](https://docs.aws.amazon.com/whitepapers/latest/ransomware-risk-management-on-aws-using-nist-csf/technical-capabilities.html) を公開し、AWS サービスを技術的能力（Backup、Event detection、Forensics and analytics、Mitigation and containment など）として CSF 機能別に整理しています。NetApp も別途、BlueXP<!-- allow:naming -->、ONTAP、DII SWS を CSF の各機能へマッピングした解説を公開しています（出典: [Fortify your cybersecurity defenses with NIST framework](https://www.netapp.com/it/blog/fortify-cybersecurity-nist-framework/)）。

| CSF 2.0 機能 | 要求される内容 |
|-------------|----------------|
| **Govern（統制）** | リスク管理戦略、役割、ポリシー、統治体制の確立と伝達 |
| **Identify（識別）** | 重要な資産・データ・依存関係の棚卸しと理解 |
| **Protect（保護）** | インシデントの発生可能性と影響を抑える保護策 |
| **Detect（検知）** | 継続的な監視による異常や有害イベントの発見 |
| **Respond（対応）** | 検知されたインシデントへの対応: 分析、低減、報告（CSF 2.0 では RS.AN などのサブ機能に分かれる） |
| **Recover（復旧）** | システム・データの復旧、および関係者との復旧連携（CSF 2.0 では RC.RP — Incident Recovery Plan Execution と RC.CO — Incident Recovery Communication に分割） |

**CSF 2.0 と NIST SP 800-61 は競合するフレームワークではなく、異なる高度で動作しています。** CSF 2.0 は Govern を頂点に置く組織全体のリスク管理の「輪」であり、SP 800-61 は実際のインシデント発生時に CSF の Detect/Respond/Recover 機能が委譲する戦術的なインシデント対応ライフサイクル（Protect → Detect → Contain/Respond → Recover）です。以下のいずれかの機能セクションで SP 800-61 レベルの運用詳細（例: Respond における具体的な封じ込めメカニズム）が必要な場合は、その詳細を別の最上位構造としてではなく、該当する CSF 機能の中に入れ子にして記載します。

---

## Govern（統制）

**要求事項**: リスク管理戦略、役割、ポリシー、統治体制の確立と伝達。

**本リポジトリのアプローチ**: 提供していません。本リポジトリは観測性/対応の「パイプライン」であり、統制プログラムではありません。組織のリスク統治は、どのストレージ層ツールも代替できません。CloudFormation によるコード化と CloudWatch Logs の監査証跡は、統制プログラムが入力として利用できるエビデンス（誰が何をデプロイしたか、いつ・なぜブロックが発火したか）を提供します — [ガバナンス・コンプライアンス](governance-and-compliance.md) と [コンプライアンスエビデンスパック](compliance-evidence-pack.md) を参照してください。

**ステータス**: ⚠️ 設計上スコープ外。

> **参照例**
>
> DII SWS、AWS 自身のホワイトペーパー、本リポジトリ — どのベンダーの CSF マッピングも、同じ構造的な理由で Govern をスコープ外としています。戦略・役割・取締役会レベルの報告は、どのツールも代わりに行えない組織的な判断だからです。Govern を「解決する」と主張するツールがあれば、疑ってかかってください。

---

## Identify（識別）

**要求事項**: 重要な資産・データ・依存関係の棚卸しと理解。

### FSx for ONTAP での実装(本リポジトリ)

[データ分類ガイド](data-classification.md) は、本リポジトリの audit-log/FPolicy パイプラインが出力する `user`/`path`/`client_ip` フィールドに対する、フィールドレベルの分類マトリクス（PII/Sensitive/Internal）を定義しています。これはスキーマレベル（フィールド名）の分類であり、コンテンツ分類ではありません。

[コンテンツレベル PII 分類スキャナー](content-classification-scanner.md) はコンテンツレベルの発見を追加します — FSx for ONTAP の S3 Access Point 経由でファイルを読み取り、Amazon Comprehend の `DetectPiiEntities` を呼び出して PII らしきコンテンツを検出します。プレーンテキスト/構造化データ形式（`.txt`、`.csv`、`.json`、`.log` など）に限定され、ドキュメント形式のパースは未対応です（詳細は同ガイドの「残存する限界」参照）。

> **プライバシーに関する補足**
>
> コンテンツスキャンにおける「完全対応」がカバーするのは *発見* の部分です — PII らしきコンテンツを見つけ、マッチしたテキスト自体ではなくエンティティタイプ/確信度を記録する（設計によるデータ最小化）。DPO/プライバシープログラムが依然として担うべき判断——ある規制において確信度がいくつ以上なら「検出された」とみなすか、誤検知にコンプライアンスログ上の訂正が必要か、発見された PII が処理の法的根拠にどう対応するか——はカバーしていません。スキャナーのレポートは、そのプログラムへの入力として扱ってください。プログラム自体の代替にはなりません。

> **データパイプラインに関する補足**
>
> PII スキャナーの DynamoDB 出力を基にレポートやダッシュボード（例: 複数のスキャン結果を集約した「PII がどこにあるか」のフリート横断ビュー）を構築する予定がある場合は、先に [コンテンツレベル PII 分類スキャナー](content-classification-scanner.md) 自身のデータパイプラインに関する補足を確認してください — Lambda コード内の `parse_float=str` という回避策により、確信度スコアは数値ではなく DynamoDB の文字列として保存されており、クエリ時に明示的にキャストしない限り数値フィルタ/ソートが機能しません。

### 代替の実装パス

| アプローチ | 仕組み | 適合する場面 |
|-----------|--------|-------------|
| **Amazon Macie**（AWS ネイティブ） | Macie の自動/ジョブベースの機密データ発見は、バケット名またはバケットレベルの条件で選択した S3 汎用バケットに対して動作します（出典: [Scope options for sensitive data discovery jobs](https://docs.aws.amazon.com/macie/latest/user/discovery-jobs-scope.html)）。本リポジトリの Comprehend ベースのスキャナーとは異なり、Macie は S3 Access Point の ARN をスキャン対象として直接受け付けません — FSx for ONTAP のデータに Macie を使うには、まず標準の S3 バケットにデータをコピーまたは同期する必要があり、これは本リポジトリの S3 Access Point ベースのスキャナーがまさに避けようとした、二重ストレージ/データの陳腐化という問題を再び持ち込むことになります。 | データが既に S3 に存在する（あるいは定期的に S3 へ同期されている）場合で、Macie の幅広いエンティティタイプカタログや Security Hub/EventBridge とのネイティブ統合が、その同期ステップのコストに見合う場合に適しています。FSx for ONTAP ボリュームを直接スキャンする代替にはなりません。 |
| **NetApp BlueXP<!-- allow:naming --> データ分類 / DII SWS**（ベンダー SaaS） | S3 を経由する中間ステップなしに、ストレージ（オンプレミスやマルチクラウドの NetApp システムを含む）を直接スキャンし、検出結果をワークロードの重要度にマッピングします。 | オンプレミス/クラウドが混在した NetApp フリート全体に既に BlueXP<!-- allow:naming --> を使っている組織、あるいは分類対象を本リポジトリの AWS ネイティブパイプラインがカバーする範囲を超えて広げたい組織に適しています（同じフリート範囲のトレードオフについては、下記 Recover のマルチクラウド適用範囲に関する補足も参照）。 |

**ステータス**: ✅ FSx for ONTAP ネイティブのパスによるスキーマレベル分類とテキスト/構造化データのコンテンツスキャンは完全対応。ドキュメント形式（Office/PDF）のコンテンツ抽出は、本リポジトリのスキャナーでは未実装のまま（Textract による拡張ポイントは同ガイドの「残存する限界」を参照）。

---

## Protect（保護）

**要求事項**: インシデントの発生可能性と影響を抑える保護策。

### FSx for ONTAP での実装(本リポジトリ)

プロアクティブな ONTAP ネイティブの制御（export-policy、name-mapping）に加え、ONTAP 自身が提供する Snapshot/SnapLock 不変性を利用します — これは共有の ONTAP プラットフォーム機能であり、本リポジトリ固有でも、その上に重ねる特定の監視ツール固有でもありません。本リポジトリ自身のパイプラインの攻撃対象領域（ONTAP REST API を呼び出す Lambda、IAM ロール、Secrets Manager の認証情報）は別途、IAM 最小権限と Secrets Manager のローテーションでカバーされます — [セキュリティ考慮事項](automated-response-guide.md#security-considerations) を参照してください。

### 代替の実装パス

| アプローチ | 仕組み | 適合する場面 |
|-----------|--------|-------------|
| **AWS Backup Vault Lock**（AWS ネイティブ） | 基盤ストレージサービス自身の不変性機能とは独立して、バックアップボールトに WORM（write-once-read-many）不変性を適用します。コンプライアンスモードでは、一度ロックすると root ユーザーでも上書きできません。 | ONTAP 自身の SnapLock に*加えて*（代替としてではなく）、AWS 管理の第 2 の不変性レイヤーを持ちたい場合、特にコンプライアンスプログラムがストレージベンダー側のメカニズムだけでなく AWS 側でも不変性のアテステーションを要求する場合に有用です。 |
| **DII SWS のユーザー別アクセスベースライン**（ベンダー SaaS） | ML によって確立された、パッシブなユーザー別アクセス監視を Protect フェーズの入力として利用します — これは正常な振る舞いを記述するものであり、単独ではブロック制御にはなりません。 | 本リポジトリはこれに相当するパッシブ ML ベースラインを提供していません（下記 Detect でこの役割を代替するものを参照）— 能動的なブロックとは独立してパッシブな行動ベースライン化そのものが必須要件である場合は、DII SWS または同等の SIEM 機能が依然として必要です。 |

**ステータス**: ✅ ストレージ層の保護策としては完全対応（共有 ONTAP メカニズム）。

---

## Detect（検知）

**要求事項**: 継続的な監視による異常や有害イベントの発見。

### FSx for ONTAP での実装(本リポジトリ)

ONTAP ARP（Autonomous Ransomware Protection）が、ネイティブのファイル内容エントロピー/拡張子変更検知を提供します — [ARP インシデント対応ガイド](arp-incident-response-guide.md) を参照。ONTAP EMS は、クォータ/容量異常を含む、より広範なイベントカタログを提供します — [EMS 検知機能リファレンス](ems-detection-capabilities.md) を参照。いずれもサードパーティ SIEM を必要とせず、ONTAP ネイティブであり、本リポジトリの Lambda ベースの EMS Webhook は ARP アラートを ~30 秒で配信します。

ユーザー別の *行動* 異常検知（既知のランサムウェアシグネチャとの一致ではなく、このユーザーの現在のアクセスパターンを本人の過去のベースラインと比較して区別すること）は、既に audit/FPolicy イベントを配信している SIEM に委ねられます — Datadog Watchdog、Elastic ML Jobs、Splunk MLTK など、[検知ユースケース](detection-use-cases.md) ガイドを参照してください。これは本リポジトリに組み込まれておらず、SIEM 側の設定・学習データが必要です。

> **脅威インテリジェンスに関する補足**
>
> エントロピー/拡張子変更分析（ARP）と行動 ML は、検知上のトレードオフにおいて対極に位置しており、明示的に言及しておく価値があります — エントロピーベースの検知は、これまで見たことのない *新規* のランサムウェアファミリーに対しても良く一般化します（暗号化は、どのマルウェアが原因であるかに関わらず、本質的にファイルのエントロピーを高めるためです）。一方、行動 ML は、暗号化を全く行わない攻撃者（例: 二重恐喝キャンペーンで増加傾向にある、暗号化なしの大量データ持ち出し）による *異常なアクセスパターン* の検知に良く一般化します。どちらか一方のアプローチだけでは、両方のケースをうまくカバーできません — 本リポジトリが ARP（シグネチャ/エントロピー）と SIEM 委譲型の ML（行動）を組み合わせているのは、まさにこの理由によるものであり、どちらか一方だけで十分だとは考えていません。[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) 自身の拡張子リストベースのスキャンは、ARP の上に重ねられた 3 つ目の、より狭いシグネチャリストベースのチェックを追加しますが、その 3 つ目の層が 3 つの中で最も回避されやすい盲点を持つ理由については、同ガイド自身の脅威インテリジェンスに関する補足を参照してください。

### 代替の実装パス

| アプローチ | 仕組み | 適合する場面 |
|-----------|--------|-------------|
| **Amazon GuardDuty Malware Protection for S3**（AWS ネイティブ） | AWS 独自および第三者のスキャンエンジンを使い、新しくアップロードされた S3 オブジェクトを自動的にマルウェアスキャンします。AWS 自身のドキュメントによると、この機能は **S3 汎用バケットのみをサポート**しています（出典: [Supportability of Amazon S3 features](https://docs.aws.amazon.com/guardduty/latest/ug/supported-s3-features-malware-protection-s3.html)）— FSx for ONTAP の S3 Access Point 経由で公開されたオブジェクトはスキャンできず、標準の S3 バケットに対する場合のように FSx for ONTAP のファイルデータを直接対象にすることはできません。 | 別のパイプライン（バックアップのエクスポートやデータレイクへの取り込みなど）の一部として、FSx for ONTAP のコンテンツを既に標準の S3 バケットへステージングまたはエクスポートしている場合に関連します — GuardDuty はその S3 コピーをスキャンできますが、これは稼働中の FSx ボリュームをスキャンする経路とは異なります。 |
| **DII SWS の行動 ML**（ベンダー SaaS） | NetApp の SaaS バックエンドで動作する、学習済みのユーザー別異常検知モデルです — シグネチャは不要で、ユーザーの通常/季節的アクセスパターンからの逸脱を検知します。 | 上記の SIEM への ML 委譲パスに最も近い代替であり、自社で設定・学習する SIEM 機能ではなく、すぐ使えるモデルとしてパッケージ化されています。独自の SIEM の ML チューニング作業なしに行動検知を得たい場合に適していますが、別の SaaS 依存関係が発生するというトレードオフがあります。 |

**ステータス**: ✅ シグネチャ/エントロピーベース検知とクォータ異常検知は完全対応（ネイティブ ONTAP メカニズム、サードパーティ依存なし）。⚠️ 行動 ML については組み合わせが必要（SIEM 側の設定・学習データ、またはベンダー SaaS による代替）。

---

## Respond（対応）

**要求事項**: 検知されたインシデントへの対応: 分析、低減、報告。

### FSx for ONTAP での実装(本リポジトリ)

[自動インシデント対応ガイド](automated-response-guide.md) が、ONTAP ネイティブの封じ込め — `name-mapping` による拒否（SMB ユーザーブロック）、export-policy 拒否ルール（NFS IP ブロック）、`create_snapshot`（ストーム防止クールダウン付き保護 Snapshot）— を実装しており、SNS 経由で*任意*の検知ソースからトリガー可能です。特定の SIEM や ML モデルに紐づいていません。管理者へのアラートは SNS トピック（後段は任意: メール、Slack、PagerDuty）を使用し、時間制限付きアクセス制限は EventBridge Scheduler による自動解除の付随スタックを使用します。

フォレンジック調査（RS.AN 分析サブ機能）は、audit-log / FPolicy パイプラインに既に存在する、正規化済みの `user`/`client_ip`/`path`/`operation` フィールドから構築します。現在、4 つの SIEM に対してベンダー別のダッシュボード実装が存在します:

| SIEM | 実装内容 | 参照先 |
|------|---------|--------|
| Splunk | 4 本の `.spl` 検索（ユーザータイムライン、全活動、IP 中心のドリルダウン、ファイルエンティティ履歴）を、入力トークン付きの Dashboard Studio ダッシュボードに構成 | [`integrations/splunk-serverless/searches/`](../../integrations/splunk-serverless/searches/) |
| Datadog | ダッシュボード JSON（8 ウィジェット: ARP タイムライン、応答アクション、影響ボリューム、重要度、ユーザーアクティビティ、監査証跡、クライアント IP、復旧検証） | [`integrations/datadog/dashboards/`](../../integrations/datadog/dashboards/) |
| Grafana | `\| json` パース付き LogQL を使った提供済みダッシュボード JSON（Loki のラベルカーディナリティ制約により `user`/`client_ip`/`path` はラベルではなくログ本文に保持） | [`integrations/grafana/dashboards/forensics-investigation.json`](../../integrations/grafana/dashboards/forensics-investigation.json) |
| Elastic | Kibana Discover + Lens。ECS フィールドマッピング（`user.name`、`source.ip`、`file.path`、`event.action`）は[正規化イベントスキーマ](normalized-event-schema.md#vendor-mapping-matrix)で既に定義済み | [Elastic セットアップガイド](../../integrations/elastic/docs/ja/setup-guide.md#フォレンジック調査-kibana-discoverlens) |

> **PII/コンプライアンスの相互参照**
>
> フォレンジックダッシュボードを構築する前に、[データ分類ガイド](data-classification.md) を確認してください — `user`/`UserName` は PII（高リスク）、`path`/`ObjectName` は Sensitive に分類されています。フォレンジックダッシュボードは定義上、これらのフィールドを生の形で調査担当者に表示するものです。ベンダーの RBAC でダッシュボードアクセスを適切に制限してください。

> **データソースの選択**
>
> フォレンジック調査には 2 つの独立したパイプラインが存在します — **FPolicy**（`operation_type`/`file_path`/`client_ip`/`user`/`protocol`、アクションレベル、サブ秒・イベント駆動 — 「今、何のアクションが起きたか」）と、**Audit Log**（`operation`/`path`/`user`/`client_ip`/`result`、アクセスチェックレベル、分単位のレイテンシ — 「過去 N 日間に何が起きたか」、かつアクセス拒否のフォレンジックに必須の `result: Failure` を持つ唯一のソース）。両者は独立しているため、同一のファイル操作について FPolicy 側のギャップを audit-log パイプラインの `EventID` ベースの記録と相互チェックできます。

### 代替の実装パス

| アプローチ | 仕組み | 適合する場面 |
|-----------|--------|-------------|
| **DII SWS の自動応答**（ベンダー SaaS） | DII 自身の ML 検知を起点とした、ユーザー/IP の自動ブロック + 保護 Snapshot + 管理者アラート（任意の第三者検知ソースからはトリガーできません）。 | 対応を DII 自身の検知モデルに密結合させ、単一のベンダー管理ワークフローの中で行いたい場合に適しています。本リポジトリのアプローチは、その密結合を、ソースに依存しないトリガー（任意の SIEM、任意の検知ルール、SNS 経由）と引き換えています。 |
| **AWS Security Hub + Systems Manager Automation**（AWS ネイティブ） | GuardDuty/Security Hub の検出結果を SSM Automation の Runbook にルーティングし、封じ込めアクションを実行します。 | ONTAP REST API を直接呼び出さない AWS 中心の検知ソースに対して有効なパターンです。本リポジトリの Lambda が既に実装している同じ `name-mapping`/export-policy の ONTAP アクションを呼び出すカスタム Runbook ステップが必要になります。 |

**ステータス**: ✅ 低減ツールとしては完全対応（ONTAP ネイティブ、ソースに依存しない）。✅ Forensics ダッシュボードは Datadog（JSON）、Grafana（JSON）、Elastic（KQL Saved Searches）、Splunk（SPL クエリ）向けに提供済み — ベンダー別アーティファクトとデプロイ方法は [AWS ネイティブ代替マトリクス](native-alternative-matrix.md#forensics-ダッシュボード--ベンダー別リファレンス)を参照。

---

## Recover（復旧）

**要求事項**: システム・データの復旧、および関係者との復旧連携。CSF 2.0 ではこれを **RC.RP**（Incident Recovery Plan Execution）と **RC.CO**（Incident Recovery Communication）に分割しています。

> **レジリエンス成熟度に関する補足**
>
> CSF 2.0 の RECOVER 機能に関する業界分析（例: [Elastio による ransomware recovery の CSF 2.0 マッピング](https://elastio.com/blog/mapping-ransomware-recovery-to-nist-csf-20)）は、本節の残りを読む前に踏まえておくべき重要な指摘をしています — Snapshot やバックアップが存在すること自体は **Protect** のエビデンスであり、RC.RP が運用上信頼できることの証明にはなりません。RC.RP が信頼できると言えるのは、実際にテストされ、侵害されていないことが確認された復旧ポイントを指し示せる場合のみであり、単に Snapshot ジョブが完了したことではありません。「Snapshot が作成された」ことと「検証済みでクリーンな復旧ポイントがあり、テスト済みである」ことは異なる成熟度レベルとして扱ってください。

### FSx for ONTAP での実装(本リポジトリ)

保護 Snapshot は Respond の際に作成されます（上記参照）— それだけでは第一の成熟度レベルしか達成できません。[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) が RC.RP の検証ギャップを解消します — 検証対象 Snapshot を FlexClone として複製し、隔離された S3 Access Point 経由でランサムウェア関連のファイル拡張子をスキャンし、人間がリストアを決断する前に clean/suspicious/error の判定結果を記録します。これは高速な事前フィルタであり、完全なフォレンジックグレードのスキャンやエンドツーエンドのリストアリハーサルではありません（正確な境界は同ガイドの比較セクションを参照）。

> **リストア検証に関する補足**
>
> 「RC.RP 検証: 完全対応」は、拡張子ベースの自動事前フィルタが完全に実装されていることを意味しますが、復旧可能性そのものが完全に証明されたことを意味するわけではありません。実際のリストア（復旧したボリュームのマウント、アプリケーションレベルのデータ整合性検証、RTO に対する所要時間の計測）は依然として別途・定期的に実施すべき演習であり、本リポジトリはこれを自動化していません。検証ワークフローの「clean」判定は、そのSnapshotがリストアリハーサルの実施サイクルに値するかどうかを決めるゲートとして扱ってください。リハーサル自体の代替にはなりません。

> **所要時間の期待値に関する補足**
>
> ここでの「完全対応」は*高速である*ことを意味しません。本ワークフローの `AttachAccessPoint` ステップは、ONTAP REST API 経由で作成されたボリュームを Amazon FSx 自身が非同期に発見するのを待ちます — AWS のドキュメントはこの同期を「数分かかる場合がある」と説明していますが、本プロジェクト自身が同一のアイドル状態ファイルシステムに対して観測ギャップなしで連続的に計測した 3 回の実行では、約 12 分、約 24 分、約 36 分という結果になり、増加するパターンでありながら上限を確証するにはデータ点が少なすぎます。RC.RP 検証の SLA（例: インシデント Runbook 向けに「Snapshot 作成から 15 分以内に検証完了」）を設定する場合は、AWS の「数分」というガイダンスや本プロジェクトの実測範囲がそのまま自社の環境に当てはまると仮定せず、自社の環境で実測した遅延に対してその目標値を確認してください — 計測方法については [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md#ステップ2-s3-access-point-の接続) 自身の計測に関する補足を参照してください。

> **DR Runbook連携に関する補足**
>
> 本節で説明する RC.RP の機能は技術的な構成要素であり、DR Runbook のステップではありません — [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)をデプロイしただけでは、実際のインシデント時に自社がフェイルオーバーやリストアをどう判断するかは変わりません。本ワークフローの判定結果をゲートとして使うよう DR Runbook を明示的に更新しない限りは（具体的な順序付けの問いについては同ガイド自身の DR Runbook連携に関する補足を参照してください）。

RC.CO（関係者レベルの復旧連携）は最小限のカバーに留まります — 上記 Respond で説明した SNS 通知は、何かが起きたという信号であり、調整された連携計画ではありません。

### 代替の実装パス

| アプローチ | 仕組み | 適合する場面 |
|-----------|--------|-------------|
| **AWS Backup restore testing**（AWS ネイティブ） | AWS Backup のリストアテスト機能は、実際の復旧ポイントに対して*自動化・定期的にスケジュールされた*リストアジョブを実行します。対応リソースタイプには **Amazon FSx（Lustre、ONTAP、OpenZFS、Windows）** が Aurora、DynamoDB、EBS、EC2、EFS、Neptune、RDS、S3 と並んで明示的に含まれています（出典: [Restore testing — AWS Backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/restore-testing.html)）。本リポジトリの FlexClone ベースのスキャンとは異なり、AWS Backup のリストアテストは（単なるコンテンツスキャンではなく）*実際のリストア*を実行し、検証ウィンドウ終了後にリストア済みリソースを削除します — これは FlexClone スキャンの重複ではなく、真に異なる補完的な機能です。AWS Backup Audit Manager はさらに、リストアテストが指定したリストア目標を満たしたことを確認するコントロールを有効化でき、RC.RP のコンプライアンス上の主張を直接裏付けます。 | FSx for ONTAP ファイルシステムの保護に AWS Backup を既に使っている（ONTAP ネイティブの Snapshot のみに依存しているのではない）場合、リストアテストは本リポジトリの FlexClone ベースの事前フィルタに対する強力な補完になります — 事前フィルタを高速かつ頻繁な第一段のゲートとして実行し、AWS Backup のリストアテストを、上記のリストア検証に関する補足で本リポジトリが自動化していないと述べた「実際にエンドツーエンドでリストアできるか」という、より深い定期演習として実行してください。 |
| **DII SWS の自動 Snapshot リストア**（ベンダー SaaS） | 検知時点で取得された自動 Snapshot によりリストアが簡略化されますが、リストア自体は依然手動の ONTAP 操作です — DII SWS も、アラート以上の RC.CO ツールは公開していません。 | 本リポジトリの Respond フェーズの Snapshot（RC.RP 検証レイヤーを追加する前の状態）に相当する成熟度レベルです — 執筆時点で、DII SWS が本リポジトリと同等の FlexClone ベースのリストア前検証ステップを公開している様子は見られません。 |

**ステータス**: ✅ FlexClone 事前フィルタによる RC.RP 検証は完全対応（高速な事前フィルタであり、完全なフォレンジックスキャンではない）。⚠️ 実際のエンドツーエンドのリストアリハーサルは組み合わせが必要 — FSx for ONTAP 向けには AWS Backup のリストアテストが推奨される AWS ネイティブの補完です。⚠️ RC.CO の関係者連携は依然、本リポジトリでは最小限の SNS 信号のまま。

---

## 残存するギャップ（未対応部分）

本ドキュメントが対応度を過大に見せないよう、本リポジトリが**行わないこと**を明確にしておきます:

1. **組み込みのユーザー別行動 ML モデルがない。** 上記 Detect で扱った通り、本リポジトリは SIEM の ML 機能またはベンダー SaaS の代替に委ねます。いずれも自社学習モデルとは誤検知の特性が異なります。
2. **全データを横断する既製の単一ダッシュボードがない — 構築は可能だが未提供。** 本リポジトリは本質的にマルチベンダー構成であり、複数の SIEM に配信する場合、フォレンジック調査（上記 Respond 参照）は現状ベンダーごとに個別に行うことになります。これは技術的な限界ではなく、パッケージングのギャップです。解消する経路は2つあります: (a) 全ベンダーを [OTel Collector 統合](../../integrations/otel-collector/) 経由で単一の OTLP ネイティブバックエンドにルーティングする。(b) 各ベンダーパイプラインは同じ [正規化イベントスキーマ](normalized-event-schema.md) のフィールド（`source`、`svm`、`user`、`path`、`operation`）を出力するため、ベンダー固有のストア横断でクエリできる層（例: エクスポート済みログに対する Athena）を用意すれば、特定ベンダーを単一の正とすることなく統合ビューを再構築できます。
3. **ストレージシステムを横断する既製のビューがない — 構築は可能だが未提供。** 本リポジトリはデフォルトでは FSx for ONTAP に限定されており、他の NetApp システム（オンプレミスの ONTAP、他の FSx for ONTAP ファイルシステム、他リージョン/他アカウント）は標準では相関されません。これは提供範囲のギャップです: 同じ audit-log/FPolicy パイプラインは、FPolicy/audit log の経路を持つ任意の ONTAP ベースシステムに対してデプロイ可能であり、[マルチアカウントデプロイ](multi-account-deployment.md) の StackSets パターンは既にこのパイプラインを複数の AWS アカウント・リージョンにファンアウトしています。

> **マルチクラウド適用範囲に関する補足**
>
> 上記の「任意の ONTAP ベースシステム」という記述の範囲は、AWS 上の ONTAP（FSx for ONTAP）、または本リポジトリの Lambda/ECS タスクからネットワーク到達可能なオンプレミスの ONTAP に限定されます。本リポジトリのパイプラインはクラウド中立のコレクターではなく AWS ネイティブサービスを基盤としているため、他のハイパースケーラー上の ONTAP オファリング（例: Azure NetApp Files、Google Cloud NetApp Volumes）には、ネットワーク/IAM 層をそのプラットフォームの相当品に適応させない限り及びません。

> **データレジデンシーに関する補足**
>
> [マルチアカウントデプロイ](multi-account-deployment.md) の StackSets パターンは、本リポジトリのパイプラインを複数の AWS アカウント*およびリージョン*にファンアウトします — これは、特定の司法管轄区域のデータを特定のリージョン境界内に留めるというレジデンシー要件に直接役立ちます。各 StackSet デプロイインスタンスは、それぞれのターゲットリージョン内で独立して動作するためです。本リポジトリは、あるデプロイのデータが実際に意図したリージョン境界内に留まったことを検証しません — これは依然としてデプロイ時の設定上の規律として利用者側が担うものです。

> **リソースタグ付けに関する補足**
>
> StackSets 経由でこれらのパイプラインを複数アカウント/リージョンにファンアウトすると、既存のタグ付けギャップもスタックインスタンスごとに増幅されます — [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) と [コンテンツレベル PII 分類スキャナー](content-classification-scanner.md) のいずれのテンプレートも、DynamoDB テーブルのみをタグ付けしており、Lambda 関数（復旧ガイドについては Step Functions ステートマシンも）はタグなしのままです。これらのテンプレートを大規模にファンアウトする前に不足している `Tags` ブロックを追加してください。

> **パッチ管理に関する補足**
>
> 本リポジトリの各 Lambda 関数は `python3.12` をランタイムとして固定していますが、インライン CloudFormation `ZipFile` コード内で `boto3`/`botocore` パッケージの正確なバージョンは固定していません。組織がデプロイする全アーティファクトについて固定済みかつ監査可能な依存関係バージョンを要求する場合は、`requirements.txt` で固定したデプロイパッケージ（Lambda Layer またはコンテナイメージ）で Lambda をパッケージ化してください。

> **セキュリティに関する補足**
>
> [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)の ONTAP と通信する Lambda 群は、インライン CloudFormation コード内で TLS 証明書検証（`cert_reqs="CERT_NONE"`）を無条件に無効化しています — 具体的な修正内容は同ガイドの「前提条件」内にあるセキュリティに関する補足を参照してください。

> **リトライポリシーに関する補足**
>
> [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)の Step Functions ステートマシンと、[コンテンツレベル PII 分類スキャナー](content-classification-scanner.md)の通知経路の両方に共通するパターンがあります — どちらも一時的なエラーに対する自動リトライを実装しておらず、両スタックの SNS `publish` 呼び出しは、アラーム対象のエラーとして表面化するのではなく、静かにログ行に落ちて失敗します。

> **保管時暗号化に関する補足**
>
> [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)と[コンテンツレベル PII 分類スキャナー](content-classification-scanner.md)の両方の DynamoDB テーブルは暗号化を有効化していますが、両テンプレートとも AWS 所有の DynamoDB キーを使用し、カスタマー管理の KMS キーには対応していません。

> **ONTAP ライフサイクルに関する補足**
>
> 同じ親ボリュームに対する FlexClone の作成/削除サイクルを繰り返す（[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)が検証実行ごとに行うパターン）と、ONTAP 内部の保持機構である「volume recovery queue」に引っかかることがあります — `aws fsx describe-volumes` がもう子クローンを一切表示していない状態でも、*親*ボリュームの削除が「クローンが 1 つ以上ある」というエラーでブロックされます。これは本リポジトリ自身のワークフローのギャップではありません（自分自身が作成したクローンのみを削除し、親ボリュームを削除することは一度もないため）が、このワークフローを繰り返し実行する対象となる親ボリュームを管理する担当者が知っておくべきギャップです — 診断方法と（FSx 側ではなく ONTAP 側での）解決方法については、そのガイド自身のステップ5にある運用上の知見に関する補足を参照してください。

> **コスト蓄積に関する補足**
>
> 本ドキュメント全体で参照している FSx 向け Lambda をフォークまたは拡張する場合（[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md)自身の `Cleanup` Lambda はすでにこれに対応済み）、`fsx.delete_volume()` は、`OntapConfiguration.SkipFinalBackup=True` を明示的に渡さない限り、デフォルトで削除対象ボリュームの最終バックアップを取得することに注意してください。使い捨てまたは中間的なボリューム（例えば、スキャンされて破棄されるだけの FlexClone）に対してこのフラグを省略すると、削除ごとに 1 件の、自動的な期限切れなしに保持され続けるバックアップが静かに積み重なります — これは、本ドキュメントが複数の機能にわたって説明しているような、スケジュール実行・頻繁実行される自動化においてまさに複利的に効いてくるコストの罠です。

> **CI カバレッジに関する補足**
>
> 本ドキュメントの E2E 検証済みという主張（冒頭のエビデンス階層に関する補足を参照）は、コードベース内で検証された機能を反映していますが、「E2E で検証済み」と「自動 CI マージゲートでカバーされている」は同じ主張ではありません。本稿執筆時点で、`.github/workflows/ci.yaml` は push・pull request のたびに各ベンダーの `tests/` スイートと `shared/lambda-layers/ems-parser/tests/` を実行しますが、`shared/python/tests/` は実行しません — [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) の `restore_verification.py` に対するユニットテスト（そのガイド自身のテスト節にある CI カバレッジに関する補足を参照）や、本ドキュメントの他の箇所で参照されている `shared/python/` の他のモジュールは、コントリビューターがローカルで `pytest` を実行した場合にのみ実行されます。これらのモジュールでの回帰は、現状では CI を失敗させません。

4. **コンテンツレベルの PII 発見は、テキスト/構造化データ形式のみをカバーする — これも技術的な上限ではない。** 上記 Identify で扱った通り、Office ドキュメントや PDF はテキスト抽出されません。Amazon Textract で抽出したテキストを本スキャナー既存の `classify_object` ロジックに渡せば、これらの形式もカバーできます。
5. **Govern 機能のツールがない。** 上記 Govern で扱った通り、これは本ドキュメントで参照している全てのストレージ層ツールに共通する構造的なギャップです。

## FAQ

**Q: なぜ本ドキュメントは DII SWS 比較から CSF 2.0 構成に変更されたのですか？**
A: 比較を優先する構成は、本ドキュメントの本来の目的である「本リポジトリ自身のサイバーレジリエンス対応範囲の記述」を、単一ベンダーの製品を中心に据える形にしてしまいました。CSF 2.0 は本リポジトリが満たすべきフレームワークであり、DII SWS、AWS Backup、Macie、GuardDuty などのツールは、それぞれ関連する機能セクション内で複数ある実装選択肢の一つとして参照されるものであり、構成の骨格そのものではありません。

**Q: Respond セクションの Splunk、Datadog、Grafana、Elastic の 4 つすべてを実装する必要がありますか？**
A: 不要です — 既に audit/FPolicy イベントを配信している SIEM についてのみ Forensics ダッシュボードを構築してください。本ドキュメントは 4 つすべてを扱っていますが、それは本リポジトリが 4 つすべてを配信先としてサポートしているためであり、同時に 4 つ必要という意味ではありません。

**Q: 本リポジトリはユーザー別行動 ML モデル（DII SWS のものを含む）を置き換えますか？**
A: いいえ。上記 Detect の通り、ML 行動ベースラインは残存するギャップであり、本リポジトリは SIEM の ML 機能またはベンダー SaaS の代替に委ねています。いずれも自社学習モデルとは誤検知の特性が異なります。

**Q: CSF 2.0 の Govern 機能はどこに位置づけられますか？本ドキュメントでは実装していないようですが。**
A: 意図的に実装していません。Govern（リスク管理戦略、役割、ポリシー、取締役会による監督）は組織側の責任であり、どのストレージ層ツールも代行できません。Govern プログラムが *入力として利用できる* エビデンス（監査証跡、コードとしてのデプロイ、ブロック/対応ログ）については [ガバナンス・コンプライアンス](governance-and-compliance.md) と [コンプライアンスエビデンスパック](compliance-evidence-pack.md) を参照してください。これらはプログラム自体の代替ではありません。

**Q: 本ドキュメントでは RC.RP 検証やコンテンツレベルの PII 発見が「✅ 完全対応」となっていますが、これはそれぞれの問題がエンドツーエンドで解決済みという意味ですか？**
A: 「完全対応」は、記載された特定の機能が完全に実装され E2E 検証済みであることを意味します — その機能が属するより広い課題全体が解決済みという意味ではありません。RC.RP 検証は高速で自動化された事前フィルタであり、実際のリストアリハーサルは別の演習です（上記 Recover の AWS Backup リストアテストが AWS ネイティブの補完として参照されています）。コンテンツレベルの PII 発見も同様に、エンティティタイプ/確信度を報告する時点で止まります — 自社の規制上の文脈においてどの確信度を「検出された」PII とみなすかは、依然としてデータ保護プログラム側の責任です。

**Q: 本ドキュメントを読者への説明やエンゲージメントの場でどう位置づけるべきですか？**
A: 特定のベンダーとのマーケティング的な比較としてではなく、認知されたフレームワークに対する透明性のある機能・ギャップ分析として位置づけてください — ✅/⚠️/❌ のマーカーは本リポジトリが自身の対応範囲を過大に見せないために存在し、代替実装パスの表は、読者が本リポジトリのアプローチだけを唯一の選択肢と決めつけるのではなく、自分の文脈に合った適切なツールを選べるようにするために存在します。読者向けの要約では ⚠️/❌ の行を誠実に先に示してください。

**Q: 本ドキュメントの ✅ マークは、内部監査や SOX のコントロールマトリクスにおけるエビデンスとしてそのまま引用できますか？**
A: そのままでは引用できません — 本ドキュメントの ✅ は「このコードベースにおいてその機能が実装され E2E 検証済みである」ことを示すもので、「自社の環境でその統制が監査対象期間中に実効的に運用されていた」こととは別の主張です。各リンク先の機能が正式な統制の主張を裏付けるために、機能の存在自体を超えてどのような追加エビデンス（実行履歴、人間によるレビュー/アテステーション）を必要とするかについては、[検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) と [コンテンツレベル PII 分類スキャナー](content-classification-scanner.md) の監査エビデンスに関する補足を参照してください。

**Q: 復旧検証ワークフローまたは PII スキャナーのいずれかから「suspicious」または「PII found」の通知が一度も届かない場合、それは結果がクリーンだったことを意味しますか？**
A: 必ずしもそうではありません — 両方の機能が、各ガイドの SNS 配信に関する補足の通り、SNS の publish 失敗をアラームではなくログ行に落として握り潰しています（残存するギャップの リトライポリシーに関する補足で横断的なパターンを参照）。「通知が来なかった」ことを「何も見つからなかった」ことと同義に扱うのではなく、基盤となる DynamoDB の記録（`verdict`/`files_with_pii`）を直接確認してください。

**Q: 本ドキュメントの ✅/⚠️/❌ マーカーは、特定のコンプライアンスプログラム（FedRAMP、ISMAP、HIPAA、PCI DSS）に対応していますか？**
A: いいえ — 本ドキュメントは NIST CSF 2.0 の機能カバレッジのみをマッピングしたものであり、これはリスク管理フレームワークであって、認定プログラムではありません。ここでの ✅ は、CSF の機能が技術的に実装されており、本コードベースでエンドツーエンドで検証済みであることを意味するに過ぎず、自社の特定のデプロイが FedRAMP、ISMAP、HIPAA、PCI DSS、あるいは独自のコントロールカタログと監査プロセスを持つ他の制度に対する正式な評価を完了しているかどうかについては何も述べていません。本ドキュメントは、コンプライアンスプログラムが自社のコントロール要件を利用可能な技術的機能にマッピングする際に使える一つの入力として扱ってください — そのプログラム自身の認定作業の代替にはなりません。正式な評価が実際に依拠するエビデンス収集レイヤーについては、[ガバナンスとコンプライアンス](governance-and-compliance.md)と[コンプライアンスエビデンスパック](compliance-evidence-pack.md)を参照してください。

## 関連ドキュメント

- [自動インシデント対応ガイド](automated-response-guide.md) — Respond フェーズの実装
- [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) — RC.RP 検証（FlexClone + 隔離スキャン + 判定結果の記録）、Recover における「残存するギャップ」項目4の対になる部分を解決
- [コンテンツレベル PII 分類スキャナー](content-classification-scanner.md) — Amazon Comprehend によるコンテンツレベルの PII 発見、テキスト/構造化データ形式について「残存するギャップ」項目4を解決
- [ARP インシデント対応ガイド](arp-incident-response-guide.md) — ONTAP ネイティブのランサムウェア検知による Protect/Detect
- [EMS 検知機能リファレンス](ems-detection-capabilities.md) — Detect フェーズのイベントカタログ
- [検知ユースケース](detection-use-cases.md) — Detect フェーズ設定のためのソース選定
- [正規化イベントスキーマ](normalized-event-schema.md) — 上記すべての Forensics 実装の基盤となる共有フィールド定義
- [データ分類ガイド](data-classification.md) — user/IP/path フィールドの PII 取り扱い、およびコンテンツレベル PII 分類スキャナーが補完するスキーマレベル分類
- [ガバナンス・コンプライアンス](governance-and-compliance.md) — 本ドキュメントが参照する Govern 機能のエビデンス層
- [コンプライアンスエビデンスパック](compliance-evidence-pack.md) — Govern/RC.CO 報告用の監査証跡エビデンス
- [セキュリティ監視ナビゲーション](security-monitoring-index.md) — 全セキュリティドキュメントへのロール別ナビゲーション

## 外部参照

- [NIST Cybersecurity Framework（CSF）2.0](https://www.nist.gov/cyberframework)
- [NIST IR 8374r1 — Ransomware Risk Management: A Cybersecurity Framework 2.0 Community Profile](https://csrc.nist.gov/pubs/ir/8374/r1/final)
- [AWS — Ransomware Risk Management on AWS Using the NIST CSF](https://docs.aws.amazon.com/whitepapers/latest/ransomware-risk-management-on-aws-using-nist-csf/technical-capabilities.html)
- [AWS Backup — Restore testing](https://docs.aws.amazon.com/aws-backup/latest/devguide/restore-testing.html)
- [Amazon Macie — Scope options for sensitive data discovery jobs](https://docs.aws.amazon.com/macie/latest/user/discovery-jobs-scope.html)
- [Amazon GuardDuty — Supportability of Amazon S3 features for Malware Protection](https://docs.aws.amazon.com/guardduty/latest/ug/supported-s3-features-malware-protection-s3.html)
- [NetApp — Fortify your cybersecurity defenses with NIST framework](https://www.netapp.com/it/blog/fortify-cybersecurity-nist-framework/)
- [NetApp — Data Infrastructure Insights Storage Workload Security](https://docs.netapp.com/us-en/ontap-technical-reports/ransomware-solutions/ransomware-DII-workload-security.html)
- [NetApp — Forensics Activity FAQ](https://kb.netapp.com/Cloud/BlueXP/Cloud_Insights/FAQ:_Storage_Workload_Security_Forensics_Activity) <!-- allow:naming -->
- [Elastio — Mapping Ransomware Recovery to NIST CSF 2.0](https://elastio.com/blog/mapping-ransomware-recovery-to-nist-csf-20)
