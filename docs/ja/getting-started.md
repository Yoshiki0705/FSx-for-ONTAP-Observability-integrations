# はじめに

🌐 **日本語**（このページ） | [English](../en/getting-started.md)

## 前提条件

- AWS アカウント
- AWS CLI v2 設定済み
- Amazon FSx for NetApp ONTAP ファイルシステム（監査ログ有効化済み）
- Node.js 18+ (開発用)
- Python 3.12+ (Lambda 関数用)

## セットアップ手順

### 1. FSx for ONTAP 監査ログの有効化

FSx for ONTAP コンソールまたは CLI で監査ログを有効化し、S3 バケットへの出力を設定します。

```bash
# Enable audit logging via ONTAP CLI
vserver audit create -vserver <svm-name> \
  -destination /vol/audit_logs \
  -format evtx \
  -rotate-size 100MB
```

### 2. FSx for ONTAP S3 Access Point の作成

これは `fsx` API で作成しボリュームにアタッチする **FSx for ONTAP** の S3 Access Point です。
`aws s3control create-access-point` は使えません。あちらは S3 バケットを前段に置く API で、
FSx ボリュームを公開できません。

```bash
aws fsx create-and-attach-s3-access-point \
  --name fsxn-audit-ap \
  --type ONTAP \
  --ontap-configuration 'VolumeId=fsvol-0123456789abcdef0,FileSystemIdentity={Type=UNIX,UnixUser={Name=root}}' \
  --region ap-northeast-1
```

`VolumeId` は ONTAP が監査ログを書き込むボリューム、つまり `vserver audit show` の
`-destination` です。SVM ルートボリュームではありません。

`--s3-access-point 'VpcConfiguration={VpcId=...}'` を省略すると **Internet-origin** の
アクセスポイントになり、シッパー Lambda を VPC 外で動かせます。これが最も単純な構成で、
以降のコマンドはこれを前提とします。**ネットワークオリジンは作成後に変更できません。**
VPC-origin の場合は
[Datadog セットアップガイド](../../integrations/datadog/docs/ja/setup-guide.md#step-2-fsx-for-ontap-s3-access-point-の作成)
を参照してください。

### 3. ベンダー統合のデプロイ

ベンダーのデプロイスクリプトを使います。CloudFormation は数百行のハンドラをインライン化
できないため、`template.yaml` は `NotImplementedError` を投げる placeholder を配置します。
スクリプトはスタックのデプロイ**と**実コードのアップロードの両方を行います。

```bash
# Example: Datadog integration
export DATADOG_API_KEY_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX"
export FSX_S3_ACCESS_POINT_ARN="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
export DATADOG_SITE="ap1.datadoghq.com"

bash integrations/datadog/scripts/deploy.sh
```

初回実行は 3〜5 分かかり、大半は CloudFormation の処理時間です。手動でデプロイする場合、
本ページの旧版が誤っていた 2 点に注意してください。パラメータ名は `FsxS3AccessPointArn`
（`S3AccessPointArn` ではない）で、テンプレートは名前付き IAM ロールを作成するため
`CAPABILITY_NAMED_IAM`（`CAPABILITY_IAM` ではない）が必要です。

```bash
aws cloudformation deploy \
  --template-file integrations/datadog/template.yaml \
  --stack-name fsxn-datadog-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    DatadogApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-datadog-api-key-XXXXXX \
    DatadogSite=ap1.datadoghq.com \
  --capabilities CAPABILITY_NAMED_IAM

# Required: replace the placeholder with the real handler
cd integrations/datadog/lambda && zip function.zip handler.py
aws lambda update-function-code \
  --function-name fsxn-datadog-integration-shipper \
  --zip-file fileb://function.zip
```

### 4. 動作確認

ベンダーの検証スクリプトを実行します。スタック状態、placeholder が置き換わっているか、
シッパーの invoke、ベンダー API への合成ログ送信をチェックするため、
失敗した場合にどの層が壊れているか特定できます。

```bash
export DD_API_KEY_SECRET_ID="fsxn-datadog-api-key"
export DD_SITE="ap1.datadoghq.com"
bash integrations/datadog/scripts/verify.sh
```

その後 FSx for ONTAP でファイル操作を行い、イベントが届くことを確認します。
ONTAP はステージングファイルをローテーションするまで監査レコードを公開しないため、
秒単位ではなく**ローテーション間隔 + スケジュール間隔**を見込んでください。

## 次のステップ

- [アーキテクチャ詳細](architecture.md)
- [ベンダー比較](vendor-comparison.md)
- [Datadog セットアップガイド](../../integrations/datadog/docs/ja/setup-guide.md)
