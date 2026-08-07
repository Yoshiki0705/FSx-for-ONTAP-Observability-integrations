# Snapshot 修復セットアップ（Datadog Workflow → ONTAP Snapshot）

このオプションコンポーネントは、大量削除やランサムウェアのシグナルをアナリストが確認した後に、
Datadog Workflow から証跡保全用の ONTAP Snapshot を作成できるようにするものです。
**封じ込めアクション**であり、ログ配送の一部ではありません。Datadog からストレージに対して
アクションを実行させたい場合にのみデプロイしてください。

> **スコープに関する補足**: これは Snapshot を作成するだけです。ユーザーやクライアント IP の
> ブロックは行いません。ユーザー/IP ブロックについては
> [automated-response-guide.md](../../../../docs/ja/automated-response-guide.md) を参照してください。

## デプロイされるリソース

| リソース | 目的 |
|----------|------|
| Lambda `<stack>-snapshot` | ONTAP REST API で Snapshot を作成 |
| SQS DLQ | リトライ後も失敗した修復リクエストを捕捉 |
| CloudWatch Log Group | 監査証跡、365 日保持 |
| エラーアラーム（しきい値 0） | 封じ込めアクションが 1 回失敗しただけで通知 |
| DLQ アラーム | まったく実行されなかったリクエストを検知 |
| IAM ロール | Secrets Manager 読み取りと VPC ENI 管理のみ |

## この Lambda が VPC 内で動く理由（シッパーは VPC 外）

本リポジトリで最も誤解されやすい点です。

| 関数 | 通信先 | ネットワーク配置 |
|------|--------|------------------|
| 監査ログシッパー | FSx for ONTAP S3 Access Point | **VPC 外** — Internet-origin S3 AP は、当環境では Gateway Endpoint のみの VPC からタイムアウトした |
| Snapshot 修復 | ONTAP 管理 LIF（TCP 443） | **VPC 内** — 管理 LIF にインターネット経路は存在しない |

`template.yaml` のネットワーク設定をこちらに流用しないでください。このスタックでは
`VpcSubnetIds` と `VpcSecurityGroupIds` は任意ではなく必須パラメータです。

## 前提条件

1. **ONTAP 認証情報を Secrets Manager に JSON 形式で登録**:

   ```bash
   aws secretsmanager create-secret \
     --name fsxn-ontap-admin \
     --secret-string '{"username":"fsxadmin","password":"<password>"}' \
     --region ap-northeast-1
   ```

   対象ボリュームで Snapshot を作成できる権限が必要です。

2. **ONTAP 管理 LIF の TCP 443 に到達できるプライベートサブネット**。

3. **そのサブネットから Secrets Manager に到達できること** — NAT Gateway または
   `com.amazonaws.<region>.secretsmanager` のインターフェース VPC エンドポイント。
   これが無いと、自身の認証情報の読み取りでタイムアウトします。

4. **ONTAP 管理 IP の確認**:

   ```bash
   aws fsx describe-file-systems \
     --file-system-ids fs-0123456789abcdef0 \
     --query 'FileSystems[0].OntapConfiguration.Endpoints.Management.IpAddresses' \
     --output text
   ```

## Step 1: デプロイ

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export ONTAP_CREDENTIALS_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-ontap-admin-XXXXXX"
export VPC_SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export VPC_SECURITY_GROUP_IDS="sg-0123456789abcdef0"

# 本番環境では推奨
export CA_CERT_PATH="/opt/certs/ontap-ca.pem"
export CA_CERT_LAYER_ARN="arn:aws:lambda:ap-northeast-1:123456789012:layer:ontap-ca:1"
export ALARM_TOPIC_ARN="arn:aws:sns:ap-northeast-1:123456789012:fsxn-alerts"

bash integrations/datadog/scripts/deploy-snapshot-remediation.sh
```

`--dry-run` を付けると、デプロイせずにパラメータを確認できます。

### パラメータリファレンス

| パラメータ | 環境変数 | デフォルト | 備考 |
|-----------|---------|-----------|------|
| `OntapManagementIp` | `ONTAP_MGMT_IP` | — | 必須 |
| `OntapCredentialsSecretArn` | `ONTAP_CREDENTIALS_SECRET_ARN` | — | 必須、JSON `{username, password}` |
| `VpcSubnetIds` | `VPC_SUBNET_IDS` | — | 必須、カンマ区切り |
| `VpcSecurityGroupIds` | `VPC_SECURITY_GROUP_IDS` | — | 必須、カンマ区切り |
| `DefaultVolume` | `DEFAULT_VOLUME` | `''` | ペイロードに `volume_name` が無い場合のフォールバック |
| `DefaultSvm` | `DEFAULT_SVM` | `''` | ペイロードに `svm_name` が無い場合のフォールバック |
| `CooldownMinutes` | `COOLDOWN_MINUTES` | `15` | 同一ボリュームでの Snapshot 最小間隔 |
| `OntapTimeoutSeconds` | `ONTAP_TIMEOUT_SECONDS` | `10` | ONTAP リクエストごとの read タイムアウト |
| `CaCertPath` | `CA_CERT_PATH` | `''` | 空の場合は `CERT_NONE`（PoC 限定） |
| `CaCertLayerArn` | `CA_CERT_LAYER_ARN` | `''` | CA 証明書を提供する Layer |
| `InvokerRoleArn` | `INVOKER_ROLE_ARN` | `''` | Workflow から呼ぶ場合は Datadog AWS 統合ロール |
| `AlarmNotificationTopicArn` | `ALARM_TOPIC_ARN` | `''` | 両アラーム共通の SNS トピック |
| `LogLevel` | `LOG_LEVEL` | `INFO` | |
| `LambdaTimeout` | — | `60` | `OntapTimeoutSeconds` の 3 倍を超える必要あり |

## Step 2: Invoke テスト

**実際に Snapshot が作成されます** — まず本番以外のボリュームで試してください。

```bash
aws lambda invoke \
  --function-name fsxn-datadog-snapshot-remediation-snapshot \
  --region ap-northeast-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"volume_name":"vol1","svm_name":"svm-prod","reason":"deploy test","user":"operator"}' \
  /dev/stdout
```

期待されるレスポンス:

```json
{"statusCode": 200, "body": "{\"snapshot_name\": \"remediation_20260807_090000_deploy_test\", \"status\": \"created\", ...}"}
```

ONTAP 側での確認:

```bash
# 管理 LIF に到達できるホストから実行
curl -sku fsxadmin "https://198.51.100.10/api/storage/volumes/<vol-uuid>/snapshots?name=remediation_*"
```

### レスポンスの読み方

| statusCode | 意味 | 対処 |
|-----------|------|------|
| 200 `status: created` | Snapshot 作成成功 | なし |
| 200 `status: skipped` | クールダウン中 | 連続トリガー時は正常動作 |
| 400 | `volume_name` / `svm_name` 未指定 | ペイロードまたはスタックのデフォルトに設定 |
| 403 | ONTAP が認証情報を拒否 | シークレットの内容とアカウント権限を確認 |
| 404 | 指定 SVM にボリュームが存在しない | ボリューム名と SVM 名を確認 |
| 500 | 設定不備または Snapshot API エラー | メッセージに不足している変数名が出力される |
| 504 | 管理 LIF に到達不能 | サブネット、ルートテーブル、セキュリティグループ（TCP 443）を確認 |

## Step 3: Datadog Workflow への組み込み

1. Datadog コンソール → **Workflows** → **New Workflow**
2. **AWS Lambda: Invoke function** アクションを追加
3. スタック出力 `SnapshotRemediationFunctionArn` の ARN を指定
4. ペイロード — トリガー元モニターのフィールドをマッピング:

   ```json
   {
     "volume_name": "{{ Source.volume }}",
     "svm_name": "{{ Source.svm }}",
     "reason": "{{ Source.monitor_name }}",
     "user": "{{ Source.user }}"
   }
   ```

5. Lambda アクションの**前に人間の承認ステップを追加**してください。この関数は本番
   ストレージに対してアクションを実行するため、完全自動化すると誤検知でも発火します。
6. `@workflow-<name>` メンションでモニターと Workflow をリンクします。

Datadog AWS 統合ロール経由で Workflow が関数を呼び出す場合は、そのロール ARN を
`InvokerRoleArn` に設定してリソースベースの権限を付与してください。

## 把握しておくべき挙動

**クールダウンは fail-open です。** クールダウン判定ができない場合（ONTAP に到達不能、
または Snapshot リストがエラーを返した場合）、関数は Snapshot を作成します。
インシデント対応中は、重複した Snapshot のコストは取り逃がすコストよりはるかに小さいためです。
クールダウンは Snapshot ストームの防止が目的で、証跡保全を止めるためのものではありません。

**同時実行数は 1 に固定**しています（`ReservedConcurrentExecutions: 1`）。並列実行すると、
どの Snapshot も作成されていない状態で全実行がクールダウン判定を通過してしまいます。

**Snapshot はボリューム容量を消費します。** 修復 Snapshot は参照するブロックを保持します。
自動トリガーを有効化する前に Snapshot 保持ポリシーを見直してください。モニターが繰り返し
発火するとボリュームが枯渇する可能性があります。

## クリーンアップ

```bash
aws cloudformation delete-stack \
  --stack-name fsxn-datadog-snapshot-remediation \
  --region ap-northeast-1
```

ONTAP 上に作成済みの Snapshot はスタック削除では**削除されません**。
調査完了後に個別に削除してください。

## 関連ドキュメント

- [セットアップガイド](setup-guide.md) — 本コンポーネントが補完する監査ログパイプライン
- [本番チェックリスト](production-checklist.md) — 修復関連の項目
- [EMS / FPolicy セットアップ](ems-fpolicy-setup.md) — リアルタイム検知ソース
- [自動応答ガイド](../../../../docs/ja/automated-response-guide.md) — ユーザー/IP ブロック
