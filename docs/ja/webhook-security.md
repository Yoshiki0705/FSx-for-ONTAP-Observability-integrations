# EMS Webhook セキュリティガイド

🌐 **日本語**（このページ） | [English](../en/webhook-security.md)

## 概要

ONTAP EMS Webhook はイベント通知を HTTPS エンドポイントに配信します。本ガイドでは、これらのイベントを受信する API Gateway エンドポイントのセキュリティ対策について説明します。

## 認証モード

共有 EMS Webhook テンプレート（`shared/templates/ems-webhook-apigw.yaml`）は 4 つの認証モードをサポートしています:

| モード | `WebhookAuthMode` | ユースケース | ONTAP 互換性 |
|------|-------------------|----------|---------------------|
| None | `NONE` | クイックスタート / PoC 専用 | ✅ 設定不要 |
| API Key | `API_KEY` | 使用量プランによる基本的な保護 | ✅ カスタムヘッダーサポート |
| IAM SigV4 | `IAM` | AWS ネイティブ認証 | ⚠️ SigV4 署名機能が必要 |
| Shared Secret | `SHARED_SECRET` | 本番推奨 | ✅ Authorization ヘッダーの Bearer トークン |

## 推奨: Shared Secret（Lambda Authorizer）

本番 EMS Webhook には `SHARED_SECRET` モードを使用してください。Secrets Manager に保存されたシークレットに対して Bearer トークンを検証する Lambda Authorizer がデプロイされます。

### 動作の仕組み

```
ONTAP EMS → HTTPS POST with Authorization: Bearer <token>
    → API Gateway
    → Lambda Authorizer (validates token against Secrets Manager)
    → If valid: invoke EMS handler Lambda
    → If invalid: return 401/403
```

### セットアップ

1. **Secrets Manager に Webhook シークレットを作成**:

```bash
aws secretsmanager create-secret \
  --name "fsxn/ems-webhook-secret" \
  --secret-string '{"webhook_secret": "<generate-a-strong-random-token>"}' \
  --region ap-northeast-1
```

2. **SHARED_SECRET モードでデプロイ**:

```bash
aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-ems-webhook \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    LambdaFunctionArn=<ems-handler-arn> \
    WebhookAuthMode=SHARED_SECRET \
    WebhookSecretArn=<secret-arn>
```

3. **ONTAP EMS Webhook 送信先を Authorization ヘッダー付きで設定**:

```
vserver ems destination create -name grafana-webhook \
  -rest-api-url https://<api-id>.execute-api.<region>.amazonaws.com/prod/ems \
  -certificate-authority <ca-name>
```

> **注意**
>
> ONTAP EMS Webhook のカスタムヘッダー設定は ONTAP バージョンによって異なります。`Authorization: Bearer <token>` ヘッダーを Webhook リクエストに追加する正しい構文については、ONTAP ドキュメントを参照してください。

4. **Authorizer が正しく許可・拒否することを検証**:

```bash
API_URL="https://<api-id>.execute-api.<region>.amazonaws.com/prod/ems"

# Valid token — expect 200 (or the EMS handler's own status code)
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API_URL" \
  -H "Authorization: Bearer <token>" \
  -H 'Content-Type: application/json' \
  -d '{"records":[]}'

# Wrong token — expect 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API_URL" \
  -H "Authorization: Bearer <an-incorrect-token>" \
  -H 'Content-Type: application/json' \
  -d '{"records":[]}'

# No header — expect 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API_URL" \
  -H 'Content-Type: application/json' \
  -d '{"records":[]}'
```

3 つすべてを実行してください。スタックが正常にデプロイできたことは Authorizer が
機能していることを何も保証しません。しかも 2 つの失敗モードは外から見ると似ています。

| 正しいトークンへの応答 | 意味 |
|---|---|
| 正しいリクエストも含め全て `403` | Authorizer は動いているがトークンが一致していない。シークレットの JSON キーが `webhook_secret` であること、ONTAP が保存した値を送っていることを確認 |
| 全て `500` | Authorizer が動いていない。`-authorizer` ロググループで import エラーを確認し、`Handler` が `index.lambda_handler` であることを確認 |

> **Authorizer コードの配置について**
>
> Authorizer はテンプレートの `Code.ZipFile` にインラインで同梱されているため、
> `aws cloudformation deploy` だけで動作する Authorizer が作成され、追加の
> アップロード手順は不要です。正となるソースは
> `shared/lambda/authorizers/shared_secret_authorizer.py` です。このファイルを
> 編集し、`python3 shared/scripts/sync-inline-lambda.py` でインラインのコピーを
> 再生成してください。両者が乖離すると
> `shared/python/tests/test_inline_lambda_sync.py` が失敗します。
>
> CloudFormation はインラインコードを `index` という名前のファイルに書き出すため、
> リソースは `Handler: index.lambda_handler` を宣言しています。これ以外のモジュール
> 名にすると呼び出し時に import エラーとなり、API Gateway はクリーンな 401 ではなく
> HTTP 500 を返します。

### シークレットローテーション

Lambda Authorizer はシークレットを 5 分間キャッシュします（Authorizer コードの `_SECRET_TTL` で設定可能）。Secrets Manager でシークレットをローテーションした後:

1. キャッシュ TTL 期間中は旧トークンと新トークンの両方が有効
2. 5 分後、新トークンのみが受け入れられる
3. Lambda の再デプロイは不要

ゼロダウンタイムローテーション:
1. 新しいトークンでシークレットを更新
2. Authorizer キャッシュの有効期限切れを待つ（5 分）
3. ONTAP EMS Webhook 設定を新しいトークンで更新

## 追加のハードニング

認証モードに関わらず、以下の追加制御を検討してください:

### API Gateway リソースポリシー

ソース IP または VPC でアクセスを制限:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": "*",
      "Action": "execute-api:Invoke",
      "Resource": "execute-api:/*",
      "Condition": {
        "IpAddress": {
          "aws:SourceIp": ["<ontap-management-ip>/32"]
        }
      }
    }
  ]
}
```

### WAF 統合

インターネット公開エンドポイントの場合、以下を含む AWS WAF をアタッチ:
- レート制限（悪用防止）
- IP レピュテーションリスト
- リクエストサイズ制限
- 地理的制限

### スロットリング

テンプレートには設定可能なスロットリングが含まれています:
- `ThrottlingRateLimit`: 1 秒あたりのリクエスト数（デフォルト: 100）
- `ThrottlingBurstLimit`: バースト容量（デフォルト: 50）

EMS イベントボリュームに基づいて調整してください。

## セキュリティ判断マトリクス

| デプロイ環境 | 推奨認証 | 追加制御 |
|-----------|-----------------|---------------------|
| Dev/PoC | `NONE` | 不要 |
| Staging | `API_KEY` | スロットリング |
| 本番（プライベートネットワーク） | `SHARED_SECRET` | リソースポリシー（ソース IP） |
| 本番（インターネット公開） | `SHARED_SECRET` | リソースポリシー + WAF + スロットリング |

## 推奨する本番ベースライン

ほとんどのデプロイでは、以下の組み合わせが過度な複雑さなしに強固なセキュリティを提供します:

1. **API Gateway Lambda Authorizer** と Shared Secret（Bearer トークン）
2. **AWS Secrets Manager にシークレットを保存**（ローテーションスケジュール付き）
3. **ソース IP 制限**（API Gateway リソースポリシー経由、ONTAP 管理アドレスが安定している場合）
4. **AWS WAF**（インターネット公開エンドポイント向け、レート制限、IP レピュテーション）
5. **API Gateway アクセスログ**を有効化（監査証跡用）
6. **CloudWatch アラーム**（認証失敗 `4XX` カウント）
7. **シークレットローテーション手順書**を文書化・テスト済み

> 初期本番デプロイでは項目 1〜3 から開始してください。エンドポイントがインターネット公開の場合、またはコンプライアンスで要求される場合に WAF（項目 4）を追加してください。

## ファイル一覧

| ファイル | 用途 |
|------|---------|
| `shared/templates/ems-webhook-apigw.yaml` | API Gateway CloudFormation テンプレート。Authorizer を `Code.ZipFile` にインラインで保持し、下記ファイルから生成される |
| `shared/lambda/authorizers/shared_secret_authorizer.py` | Lambda Authorizer コード — 正となるソース。編集はこちら |
| `shared/scripts/sync-inline-lambda.py` | ソースからテンプレートのインラインコピーを再生成。`--check` は乖離時に exit 1 |
| `shared/python/tests/test_inline_lambda_sync.py` | インラインコピーの乖離、`index.*` でないハンドラー、残存プレースホルダーを検出して失敗する |
| `shared/python/auth_cache.py` | 再利用可能な認証情報キャッシュ（ハンドラー側認証用） |
