# 最小テストパス

🌐 **日本語**（このページ） | [English](../en/quick-start-minimum.md)

最もシンプルな構成で監査イベントを Datadog に送信します。

## 必要なもの

- FSx for ONTAP ファイルシステム（監査ログ有効化済み）
- FSx for ONTAP S3 Access Point（audit volume にアタッチ済み）
- Datadog アカウント（無料トライアル可）
- Secrets Manager に保存した Datadog API Key

## 最小構成

| 設定 | 値 | 理由 |
|---------|-------|-----|
| Lambda VPC | VPC 外 | NAT Gateway 不要 |
| Scheduler | rate(5 minutes) | デフォルト |
| Audit rotation | 5分間隔（時間ベース） | ローテーションファイルが素早く出現 |
| Datadog site | 使用サイト（例: ap1.datadoghq.com） | — |

## 手順

```bash
# 1. Deploy (single command — deploys the stack AND uploads the Lambda code)
export DATADOG_API_KEY_SECRET_ARN=<your-secret-arn>
export FSX_S3_ACCESS_POINT_ARN=<your-fsx-s3-ap-arn>
export DATADOG_SITE=<your-site>

bash integrations/datadog/scripts/deploy.sh    # 3-5 minutes on first run

# 2. Confirm the pipeline is wired end to end
export DD_API_KEY_SECRET_ID=fsxn-datadog-api-key
export DD_SITE=<your-site>

bash integrations/datadog/scripts/verify.sh    # expect 4/4 checks passed

# 3. Perform a test file operation on the audited share
#    (create/delete a file via SMB or NFS)

# 4. Wait for ONTAP to rotate the audit log, then for the next 5-minute schedule

# 5. Verify in Datadog
#    Search: source:fsxn
```

> **`template.yaml` を単体でデプロイしないでください。** CloudFormation はハンドラを
> インライン化できないため、テンプレートは `NotImplementedError` を投げる placeholder を
> 配置します。`deploy.sh` は最終ステップで実コードをアップロードします。この手順が無いと
> ログは 1 件も届かず、手順 5 は成功しません。`verify.sh` のチェック 2 がこれを検出します。

素の CloudFormation を使いたい場合は
[セットアップガイド Step 3](../../integrations/datadog/docs/ja/setup-guide.md#step-3-デプロイ)
を参照してください。手動デプロイと必須のコードアップロードの両方を扱っています。

## 成功基準

- [ ] Datadog Log Explorer で `source:fsxn` が1件以上返る
- [ ] `@attributes.operation` が入力されている
- [ ] `@attributes.user` が入力されている

## 最小テストに含まれないもの

- VPC / NAT Gateway 設定
- DLQ リプレイ手順
- カスタムメトリクス
- Datadog Monitor
- マルチ SVM / マルチアカウント

これらは本番強化ステップであり、完全なドキュメントでカバーされています。

## 次のステップ

ログ到着確認後:
1. [フィールドマッピング](../../integrations/datadog/docs/ja/field-mapping.md)を確認
2. [調査クエリ](../../integrations/datadog/docs/ja/field-mapping.md#datadog-検索クエリ)を試す
3. Monitor を設定（ブログシリーズ Part 3）

4. 本番デプロイ（VPC Endpoint、コスト計画、マルチスタック連携）については[デプロイメントガイド](deployment-guide.md)を参照
