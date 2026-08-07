# ベンダー統合のデプロイ

🌐 **日本語** | [English](../en/vendor-deployment-common.md)

> 全ベンダーで共通の手順。各ベンダーの `setup-guide.md` はそのベンダー固有の内容だけを
> 扱い、残りはこのドキュメントを参照します。

## テンプレートだけでは動作しない

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

# 共有 ONTAP 監査ログパーサをハンドラと同梱します。同梱しないとハンドラは JSON 専用の
# 解析にフォールバックし、XML/EVTX の監査ログ（= 実際の ONTAP 監査ログはすべてこれ）が
# フィールド解析されずに配送されます。
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
bash integrations/<vendor>/scripts/cleanup.sh          # スタックのみ
bash integrations/<vendor>/scripts/cleanup.sh --all    # + シークレット・レイヤー・S3 テストデータ
bash integrations/<vendor>/scripts/cleanup.sh --all -y  # 非対話
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
