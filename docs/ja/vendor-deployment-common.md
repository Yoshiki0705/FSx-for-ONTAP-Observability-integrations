# ベンダー統合のデプロイ

🌐 **日本語** | [English](../en/vendor-deployment-common.md)

> 全ベンダーで共通の手順。各ベンダーの `setup-guide.md` はそのベンダー固有の内容だけを
> 扱い、残りはこのドキュメントを参照します。

## テンプレートだけでは動作しない理由

各ベンダーの `template.yaml` は**プレースホルダ**の Lambda を持っています:

```yaml
Code:
  ZipFile: "def lambda_handler(e,c): raise NotImplementedError"
```

CloudFormation は数百行のハンドラをインラインに書けないため、実コードは別途アップロード
する必要があります。`aws cloudformation deploy` だけで止めると、スタックは正常に見え、
スケジューラは時間どおりに発火し、Lambda は毎回 `NotImplementedError` を投げます。
テレメトリは 1 件も配送されず、アラームを設定していなければ誰も気付きません。

**`scripts/deploy.sh` を使ってください。** スタックのデプロイとハンドラのアップロードを
まとめて行います:

```bash
bash integrations/<vendor>/scripts/deploy.sh
```

必要な環境変数は `--help` で確認できます。初回は 3〜5 分かかり、そのほとんどは
CloudFormation が IAM ロール・Lambda・スケジューラ・アラームを作成する時間です。
変更のないスタックへの再実行は数秒で終わります。

`integrations/otel-collector` は例外です。テンプレートがインラインのプレースホルダでは
なく `S3Bucket` / `S3Key` を受け取るため、先にコードをパッケージして S3 へアップロード
する必要があります。手順は同ベンダーのガイドに記載しています。

### CloudFormation を手動でデプロイした場合

後からハンドラをアップロードしないと、スタックは動作しないままです:

```bash
cd integrations/<vendor>/lambda

# Bundle the shared ONTAP audit parser alongside the handler. Without it the
# handler falls back to JSON-only parsing and every XML/EVTX audit log — which
# is every real ONTAP audit log — arrives without parsed fields.
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name <stack-name>-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name <stack-name>-shipper \
  --region ap-northeast-1
```

`scripts/verify.sh` はデプロイ済みコードのサイズを見てアップロード忘れを検知します。
どちらの方法でデプロイした場合も実行してください。

## 開始前に収集する値

| 値 | 取得方法 |
|----|---------|
| FSx for ONTAP S3 Access Point ARN | `aws fsx describe-s3-access-point-attachments --names <ap-name> --query 'S3AccessPointAttachments[0].S3AccessPoint.ResourceARN'` |
| 監査ログ S3 バケット名 | [前提スタック](prerequisites.md)の Output |
| ベンダー資格情報のシークレット ARN | `aws secretsmanager create-secret ...` の出力、または `describe-secret` |
| AWS アカウント ID | `aws sts get-caller-identity --query Account --output text` |
| リージョン | FSx ファイルシステムと同一リージョンであること |

## アクセスポイントのネットワークオリジンと Lambda 配置

オリジンは作成時に固定されます。ここを誤ることが初回デプロイ失敗の最大の原因です。

| Lambda 配置 | Internet オリジン AP | VPC オリジン AP |
|------------|:-------------------:|:--------------:|
| VPC 外（既定） | ✅ 動作する | ❌ 経路がない |
| VPC 内 + S3 Gateway Endpoint のみ | ⚠️ 検証環境ではタイムアウト | ✅ 動作する |
| VPC 内 + NAT Gateway | ✅ 動作する | ✅ 動作する |

既存のアクセスポイントを使う場合は、選択前に確認してください:

```bash
aws s3control get-access-point \
  --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --name <ap-name> --region ap-northeast-1 \
  --query '{Origin:NetworkOrigin,Vpc:VpcConfiguration.VpcId}'
```

VPC オリジンのアクセスポイントは、アクセスポイントポリシーが存在しない場合でも VPC 外
からのリクエストを `AccessDenied ... explicit deny in a resource-based policy` で拒否
します。文面は IAM を指していますが、原因はネットワークオリジンです。

> **AD 参加済み SVM に関する補足**: SVM で CIFS が有効な場合、S3 アクセスポイントの
> **すべての**データ操作で SVM から AD ドメインコントローラへの到達性が必要です。
> `HeadBucket` が成功するのに `ListObjectsV2` が `AccessDenied` になるのは AD DC へ
> 到達できていない兆候で、IAM やポリシーの問題ではありません。

## 収集元 1 か所と拠点ごとの分岐点

複数ファイルシステム・複数拠点の構成を決めるのは、**どちら側が接続を開くか**であり、
拠点の数ではありません。ここに挙げる機構のうち 1 つを除いて、拠点の追加はアカウントの
追加と同じ種類の変更です。

| 機構 | 向き | 1 か所に集約可能か | 何が縛るか |
|------|------|------------------|-----------|
| S3 アクセスポイントの監査ログポーリング | pull | 可 | アクセスポイントのネットワークオリジンのみ |
| ONTAP REST ポーリング（クォータ、自動対応） | pull | VPC 内。拠点ごとではない | プライベートな管理 IP |
| Harvest → ONTAP 管理エンドポイント | pull | 可 | なし。エンドポイントはリストで受ける |
| EMS Webhook | push | 可 | こちら側には無し。ONTAP からパブリックな API Gateway URL への出口経路が必要 |
| **FPolicy** | push | **不可** | ONTAP がエンジンの **IP アドレス**を保持し、ingress は SVM 自身のセキュリティグループから |

pull 方式の収集はトポロジではなくリストです。管理コンソールは
`OntapManagementEndpoints` をカンマ区切りのパラメータで受け、1 タスク内でエンドポイント
ごとに 1 ポーラーを起動します。監査ログの送信 Lambda は VPC 構成を一切持ちません。AWS も
Harvest を 1 インスタンスのまま 40 台以上のファイルシステムへスケールさせる形でサイジング
しており、インスタンスを増やす形ではありません。

**FPolicy が例外で、到達性を足しても解消しません。** ONTAP は特定のアドレスの TCP 9898 へ
接続するため、収集側にどれだけ経路を足しても 2 つ目の拠点には自前のエンジンが必要です。
タスク再起動でそのアドレスが変わるため `shared/scripts/fpolicy-update-engine-ip.sh` が
存在します。

### 最初に壊れる層

記録されている順序です。チームが何を結論するかがこの順序で決まります。

1. **到達性。エラーではなくタイムアウトとして現れます。** 上の表の Gateway Endpoint の行
   です。拒否は記録されず、呼び出しが時間切れになります。
2. **認可。ただし誤ったラベルを付けて現れます。** 上の 2 つの形です。ポリシーが存在しない
   のに返る VPC オリジンの `explicit deny in a resource-based policy` と、AD 参加 SVM で
   `HeadBucket` が 200 のまま `ListObjectsV2` が `AccessDenied` になる形です。
3. **スループットは現れませんでした。** 本リポジトリにスループット起因の失敗の記録はなく、
   集約構成に起因するものもありません。[S3 AP スループットベンチマーク](s3ap-throughput-benchmark.md)
   の実測値は 1 環境でのサイジング参考値であり、上限ではありません。

したがって「ネットワークの変更かアーキテクチャの変更か」の答えは、**FPolicy 以外の経路は
すべてネットワークの変更**です。これは誤りではなく範囲の狭い正解で、狭さは FPolicy を
足した時点で初めて現れます。チームが最初に踏む失敗はネットワーク側なので、それまで
「ネットワークの変更」は正しいままです。

> **確度に関する補足**: 1 と 2 は自環境での実測で、本リポジトリで最も頻繁な 2 つの
> デプロイ失敗です。トポロジの表はテンプレートから読み取ったもので、2 拠点にまたがる
> 配置からではありません。**この比較のどちら側でも、拠点をまたぐ配置は測定していません。**

## アラーム通知

各スタックは Lambda エラーと DLQ 深度の CloudWatch アラームを作成します。既定では
**通知アクションがありません**。コンソールには表示されますが誰にも通知されません。

SNS トピックを渡すと通知されるようになります:

```bash
export ALARM_TOPIC_ARN="arn:aws:sns:ap-northeast-1:123456789012:fsxn-alerts"
bash integrations/<vendor>/scripts/deploy.sh
```

手動デプロイの場合は
`--parameter-overrides AlarmNotificationTopicArn=arn:aws:sns:...` が対応します。

DLQ アラームの発火は、テレメトリを受け取ったが配送できなかったことを意味します。
メッセージの保持期間は 14 日で、それを過ぎると失われます。
[DLQ replay runbook](runbooks/dlq-replay.md) を参照してください。

## 検証

```bash
bash integrations/<vendor>/scripts/verify.sh
```

スタックの存在、実ハンドラコードがアップロードされているか、スケジュールが有効か、
チェックポイントが前進しているかを確認します。終了コードは `sysexits.h` 準拠で
`0` 成功、`69` 対象リソースが利用不可、`78` 設定エラーです。

## クリーンアップ

```bash
bash integrations/<vendor>/scripts/cleanup.sh          # stacks only
bash integrations/<vendor>/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/<vendor>/scripts/cleanup.sh --all -y  # non-interactive
```

スタックは依存関係を壊さない順序で削除されます。ベンダーが申告した追加スタックが最初、
続いて `-fpolicy`、`-ems-webhook`、`-ems`、`-integration` の順です。API Gateway の
スタックは、それが参照する EMS Lambda のスタックより先に削除する必要があります。

未配送レコードを保持するバケットは意図的に `DeletionPolicy: Retain` としており、
クリーンアップ後も残ります（Splunk Firehose のバックアップバケットと Datadog のログ
アーカイブバケット）。内容を回収または放棄したうえで手動削除してください。

共有リソースはベンダーのクリーンアップでは削除されません（FPolicy Fargate スタック、
S3 アクセスポイント、監査ログバケット、前提スタック）。全ベンダーを撤去したあとに
`shared/scripts/cleanup-shared.sh` を使ってください。

> ベンダーの deploy スクリプトが標準の 4 スタック以外を作る場合、そのベンダーの
> `cleanup.sh` が `EXTRA_STACKS` で申告しないと黙って残り続けます。
> `integrations/splunk-serverless/scripts/cleanup.sh` が実例です。Datadog の
> snapshot remediation スタックはストレージに対して操作できるため、
> `--delete-snapshot-remediation` による opt-in にしています。

## 関連ドキュメント

- [前提条件](prerequisites.md) — FSx for ONTAP、監査ログ有効化、アクセスポイント
- [デプロイガイド](deployment-guide.md) — スタックカタログ、VPC エンドポイント競合、コスト
- [パイプライン SLO](pipeline-slo.md) — 成熟度レベル間の Go/No-Go 基準
- [DLQ replay runbook](runbooks/dlq-replay.md)
