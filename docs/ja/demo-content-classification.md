# コンテンツ分類スキャナー デモ手順書

🌐 **日本語**（このページ） | [English](../en/demo-content-classification.md)

## 目的

コンテンツレベル PII 分類スキャナーのエンドツーエンドデモ手順書。カバー範囲: デプロイ → 合成 PII サンプルのアップロード → スキャナー実行 → DynamoDB レポート確認 → SNS 通知確認 → クリーンアップ。

用途:
- 対外デモ（ライブまたは録画）
- ブログ公開前の E2E 検証
- 内部トレーニング

> **エビデンス形式に関する注記**
>
> 本手順書は、各ステップの後に「何を確認すべきか」を平文で記述しており、スクリーンショットのプレースホルダーや架空のサンプル出力は使用していません。本稿執筆時点で、この手順書自体はエンドツーエンドで実行されておらず、実際のスクリーンショットやコマンド出力も一切キャプチャされていません — 本ガイド内のいずれの記述も、これらの手順が実際に実行された証拠として扱わないでください。実際に本手順書を実行する際は、実際のコマンド出力やスクリーンショットを取得し（アカウントID/IP/ARN は `docs/screenshots/mask_screenshots.py` でマスキングしてから）、[自動応答ガイド](automated-response-guide.md)向けの `docs/screenshots/automated-response/e2e-verification-results.md` と同じ形式で記録することを推奨します。

---

## 前提条件

| 項目 | 要件 |
|------|------|
| S3 Access Point | FSx for ONTAP ボリュームに対する既存の S3 Access Point — デプロイモード1にはインターネット起点のもの、デプロイモード2には VPC 限定のものが必要（[コンテンツ分類スキャナー](content-classification-scanner.md#既存の-s3-access-point) を参照） |
| 書き込みアクセス | Access Point（または同じボリューム上の別の Access Point）が `PutObject` を許可していること — 合成テストファイルのアップロードに必要。読み取り専用の Access Point ではアップロードが拒否されます |
| AWS CLI | 適切な IAM 権限で設定済み（`cloudformation:*`、`lambda:InvokeFunction`、`dynamodb:GetItem`、Access Point に対する `s3:PutObject`/`DeleteObject`） |
| jq | JSON フォーマット用にインストール済み（一部ステップの可読性向上に使用、任意） |

---

## Phase 1: スキャナースタックのデプロイ

本手順書は、[コンテンツ分類スキャナー § デプロイ](content-classification-scanner.md#デプロイ) に記載されている2つのモードのうち、より簡易な**デプロイモード1（スタンドアロン、インターネット起点 Access Point）**を使用します。Access Point が VPC 限定の場合（例: [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) の `AttachAccessPoint` ステップが作成したもの）は、デプロイモード2を使用してください — そのガイド自身のデプロイパラメータを参照してください。

### ステップ 1.1: Access Point のネットワーク起点を確認

```bash
aws s3control get-access-point \
  --account-id <account-id> \
  --name <access-point-name> \
  --query NetworkOrigin
```

**確認ポイント**: 出力が `"Internet"` であること（以下のデプロイモード1向け）。`"VPC"` の場合は、デプロイモード2に切り替えてください（ガイドのデプロイ節に従って `VpcId`/`SubnetIds`/`SecurityGroupId`/`RouteTableIds` を設定）— モード1は、IAM 権限に関わらず、VPC 限定の Access Point に対して確実に失敗します。

### ステップ 1.2: CloudFormation デプロイ（モード1）

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

### ステップ 1.3: スタック出力の確認

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-content-classification \
  --query 'Stacks[0].Outputs' \
  --output table
```

**確認ポイント**: 出力テーブルに `ScannerFunctionArn`、`ReportTableName`、`InvokeExample` の各キーが含まれ、それぞれ空でない値が設定されていること。

### ステップ 1.4: CLI 環境変数の設定

```bash
export SCANNER_FUNCTION=$(aws cloudformation describe-stacks \
  --stack-name fsxn-content-classification \
  --query 'Stacks[0].Outputs[?OutputKey==`ScannerFunctionArn`].OutputValue' \
  --output text)

export REPORT_TABLE=$(aws cloudformation describe-stacks \
  --stack-name fsxn-content-classification \
  --query 'Stacks[0].Outputs[?OutputKey==`ReportTableName`].OutputValue' \
  --output text)

export ACCESS_POINT_ARN="<your-access-point-arn>"
echo "Scanner: $SCANNER_FUNCTION"
echo "Report table: $REPORT_TABLE"
```

---

## Phase 2: PII 検出のデモ

本フェーズは、[コンテンツ分類スキャナー § 動作確認](content-classification-scanner.md#動作確認quick-validation) と同じ流れに従います — 本プロジェクト自身の開発時検証で使用した正確なコマンドはそちらを参照してください。

### ステップ 2.1: ベースラインの確認（まだ検出結果は期待しない）

この Access Point への初回スキャンであれば、ステップ 2.2 に直接進んで構いません。以前にこの Access Point をスキャンしたことがある場合は、後で比較するために台帳の現在の `files_with_pii` 値を記録しておいてください:

```bash
aws dynamodb query \
  --table-name "$REPORT_TABLE" \
  --key-condition-expression "access_point_arn = :arn" \
  --expression-attribute-values "{\":arn\": {\"S\": \"$ACCESS_POINT_ARN\"}}" \
  --query 'Items[*].{started_at: started_at.S, files_with_pii: files_with_pii.N}' \
  --output table
```

**確認ポイント**: この Access Point に対する過去のスキャン実行一覧（存在する場合）が表示され、それぞれ `started_at` と `files_with_pii` の件数を持つこと。これが「実施前」のベースラインになります。

### ステップ 2.2: 合成 PII テストファイルの作成

```bash
cat > /tmp/pii-test-sample.txt <<'EOF'
Support Ticket #48213
Name: John Sample Doe
Email: john.sample.doe@example.com
Phone: 555-0142-9981
SSN: 078-05-1120
Address: 123 Example Street, Springfield, IL 62704
EOF
```

**確認ポイント**: `/tmp/pii-test-sample.txt` にファイルが作成されていること。これは合成データであり実際の個人情報は含まれていません — 本デモの一部として安全にアップロード・削除できます。

### ステップ 2.3: Access Point 経由でテストファイルをアップロード

```bash
aws s3api put-object \
  --bucket "$ACCESS_POINT_ARN" \
  --key validation/pii-test-sample.txt \
  --body /tmp/pii-test-sample.txt
```

**確認ポイント**: コマンドがエラーなく完了し、`ETag` が返ること。`AccessDenied` で失敗する場合は、Access Point（または基盤ボリュームの export policy / S3 リソースポリシー）が実際に書き込みアクセスを許可しているか確認してください — 読み取り専用の Access Point ではこれが拒否されます。

### ステップ 2.4: スキャナーの実行

```bash
aws lambda invoke \
  --function-name "$SCANNER_FUNCTION" \
  --payload "{\"access_point_arn\":\"$ACCESS_POINT_ARN\",\"max_files\":50}" \
  --cli-binary-format raw-in-base64-out \
  /tmp/scan-response.json

cat /tmp/scan-response.json
```

**確認ポイント**: 呼び出しが（ペイロードではなく `aws lambda invoke` コマンド自身の出力で）`StatusCode: 200` を返し、`/tmp/scan-response.json` に `files_scanned`、`files_with_pii`、`started_at` タイムスタンプを含む JSON レポートが書き込まれること。`started_at` の値は次のステップで使用するため記録してください。

### ステップ 2.5: 検出結果が台帳に記録されたことを確認

```bash
export STARTED_AT="<started_at value from the previous step's response>"

aws dynamodb get-item \
  --table-name "$REPORT_TABLE" \
  --key "{\"access_point_arn\":{\"S\":\"$ACCESS_POINT_ARN\"},\"started_at\":{\"S\":\"$STARTED_AT\"}}"
```

**確認ポイント**: アイテムが存在し、`files_with_pii` が 1 以上であること（この Access Point 配下でスキャン可能な PII を含むファイルが `pii-test-sample.txt` のみであれば 1、他に既存の PII を含むファイルがあればそれ以上）。`findings` リストには `validation/pii-test-sample.txt` のエントリが含まれ、`NAME`、`EMAIL`、`PHONE`、`SSN`、`ADDRESS` といったエンティティタイプが記録されているはずです — 各エンティティタイプの信頼度スコアの目安については [コンテンツ分類スキャナー § 動作確認](content-classification-scanner.md#動作確認quick-validation) を参照してください。このアイテムのどこにも、マッチした PII の値そのものが**含まれていない**ことを確認してください — 記録されるのはエンティティタイプ・件数・信頼度スコアのみです（これはスキャナーのデータ最小化設計によるものです。[エンティティの集計](content-classification-scanner.md#エンティティの集計--データ最小化を前提とした設計) を参照）。

### ステップ 2.6: SNS 通知の確認（設定している場合）

`NotificationTopicArn` を設定せずにデプロイした場合は、このステップをスキップしてください。

**確認ポイント**: メールまたは他のサブスクライバーエンドポイントが、このスキャンを参照する通知を受信していること。メールのスクリーンショットをエビデンスとして使いたくない場合は、Lambda が同じ通知試行をログに出力しているので、この関数の CloudWatch Logs で publish 成功のログ行、または配信が静かに失敗した場合の `"Notification failed"` 警告を確認してください（publish の失敗が Lambda エラーとして表面化しない理由については、ガイドのテスト節にある [SNS 配信に関する補足](content-classification-scanner.md#テスト) を参照）。

---

## Phase 3: 「PII なし」および トラブルシューティング経路のデモ

### ステップ 3.1: PII を含まないディレクトリのスキャン

PII のような内容を含まないことが分かっているパス（または新規の Access Point）に対してスキャナーを実行します:

```bash
aws lambda invoke \
  --function-name "$SCANNER_FUNCTION" \
  --payload "{\"access_point_arn\":\"$ACCESS_POINT_ARN\",\"max_files\":50}" \
  --cli-binary-format raw-in-base64-out \
  /tmp/scan-response-clean.json

cat /tmp/scan-response-clean.json
```

**確認ポイント**: `files_with_pii` が `0` であり、`files_scanned` が `0` より大きいこと（何もスキャン可能なものがなかったために何も見つからなかったのではなく、実際にファイルに対してスキャンが実行されたことを確認するためです — この2つを区別する方法は次のステップで説明します）。

### ステップ 3.2: 「PII が見つからなかった」と「スキャン可能なものが無かった」を区別する

```bash
cat /tmp/scan-response-clean.json | python3 -c "
import json, sys
r = json.load(sys.stdin)
print('files_scanned:', r.get('files_scanned'))
print('files_skipped_unscannable:', r.get('files_skipped_unscannable'))
print('files_with_pii:', r.get('files_with_pii'))
"
```

**確認ポイント**: [コンテンツ分類スキャナー](content-classification-scanner.md#faq) の FAQ にある通り、`files_skipped_unscannable` がボリューム内のファイルの大半を占める場合、そのスキャンは実際には大半のコンテンツを検査しておらず（スキャン可能な拡張子リスト外の Office/PDF ファイルである可能性が高い — [残存する限界](content-classification-scanner.md#残存する限界) の項目1を参照）、PII が無いことを確認したわけではありません。

---

## クリーンアップ

```bash
# Delete the synthetic test file from the access point
aws s3api delete-object --bucket "$ACCESS_POINT_ARN" --key validation/pii-test-sample.txt

# Remove local temp files
rm -f /tmp/pii-test-sample.txt /tmp/scan-response.json /tmp/scan-response-clean.json

# Delete the CloudFormation stack (optional)
aws cloudformation delete-stack --stack-name fsxn-content-classification
```

> **注記**
>
> スタックを削除すると、スキャナー Lambda と `ClassificationReportTable`（およびデプロイモード2で作成された VPC Endpoint）が削除されます。S3 Access Point や基盤の FSx for ONTAP ボリュームには影響しません — このスキャナーは Access Point を自ら作成・管理することはありません（[前提条件](content-classification-scanner.md#既存の-s3-access-point) を参照）。

---

## 所要時間の目安

| フェーズ | 所要時間 | 備考 |
|---------|---------|------|
| Phase 1（デプロイ） | ~5 分 | CloudFormation デプロイ |
| Phase 2（PII 検出） | ~3 分 | アップロード + 実行 + 台帳確認 + 通知確認 |
| Phase 3（PII なし/トラブルシューティング経路） | ~2 分 | 2 回目の実行 + レスポンス確認 |
| **合計** | **~10 分** | 全フェーズ実行 |

---

## 関連ドキュメント

- [コンテンツ分類スキャナー](content-classification-scanner.md)
- [サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md#identify-id)
- [データ分類ガイド](data-classification.md)
- [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) — デプロイモード1の代わりに、FlexClone ベースの Access Point の後ろにこのスキャナーを連結する場合
