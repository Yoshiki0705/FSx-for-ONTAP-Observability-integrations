# Splunk Serverless 統合 動作確認結果

🌐 **日本語**（このページ） | [English](../en/verification-results-splunk.md)

> **このドキュメントは記入前のテンプレートです。** 以下の判定欄はすべて
> `<PASS/FAIL>` のままで、環境情報もプレースホルダです。まだ検証結果が書き込まれて
> いません。監査ログ経路自体は実行済みで、FSx for ONTAP の監査イベントが Splunk 検索
> に表示されている様子は
> `integrations/splunk-serverless/screenshots/splunk-e2e-search-fsxn-audit-xml.png`
> にあります。EMS と FPolicy はハンドラとスタックはありますが E2E の実行記録が無いため、
> [テレメトリ経路のカバレッジ](README.md#テレメトリ経路のカバレッジ)の表では ✅ ではなく
> 🔧 としています。

- **検証日時** — <検証日>
- **検証者** — <検証者名> / <役職>

### 検証環境

- **AWS リージョン** — ap-northeast-1
- **CloudFormation スタック名** — <スタック名>
- **Lambda 関数名** — fsxn-splunk-log-shipper
- **Splunk HEC エンドポイント** — <HEC エンドポイント URL>
- **Splunk インデックス** — fsxn_audit
- **FSx for ONTAP ファイルシステム** — <ファイルシステム ID>
- **S3 Access Point** — <S3 Access Point ARN>
- **HEC Token Secret ARN** — <Secrets Manager ARN>

---

## 検証ステップ

| ステップ番号 | ステップ名 | コマンド | 期待結果 | 実測結果 | 判定 |
|:---:|---|---|---|---|:---:|
| 1 | CloudFormation スタックデプロイ | `aws cloudformation deploy --template-file integrations/splunk-serverless/template.yaml --stack-name <スタック名> ...` | CREATE_COMPLETE | <実測結果> | <PASS/FAIL> |
| 2 | HEC Token 検証 | `python3 scripts/verification/splunk_token_validator.py --secret-arn <ARN>` | UUID 形式一致 | <実測結果> | <PASS/FAIL> |
| 3 | Lambda テストイベント送信 | `aws lambda invoke --function-name fsxn-splunk-log-shipper --payload file://integrations/splunk-serverless/tests/test_data/sample_s3_event.json response.json` | statusCode: 200, total_shipped > 0 | <実測結果> | <PASS/FAIL> |
| 4 | CloudWatch Logs 確認 | `aws logs filter-log-events --log-group-name /aws/lambda/fsxn-splunk-log-shipper --filter-pattern "Successfully shipped"` | "Successfully shipped" ログ出力 | <実測結果> | <PASS/FAIL> |
| 5 | Splunk Search ログ到着確認 | SPL クエリ実行（下記参照） | 1件以上のイベント返却 | <実測結果> | <PASS/FAIL> |
| 6 | フィールド検証 | Splunk Search でイベント展開 | 全必須フィールドが非空 | <実測結果> | <PASS/FAIL> |
| 7 | スクリーンショット検証 | `python3 scripts/verification/splunk_screenshot_validator.py docs/screenshots/splunk/` | 3ファイル、命名規約準拠、500KB以下 | <実測結果> | <PASS/FAIL> |
| 8 | セットアップガイド日英対応確認 | `python3 scripts/verification/bilingual_comparator.py --ja integrations/splunk-serverless/docs/ja/setup-guide.md --en integrations/splunk-serverless/docs/en/setup-guide.md` | 見出し構造一致 | <実測結果> | <PASS/FAIL> |

---

## 詳細検証手順

### ステップ 1: CloudFormation スタックデプロイ

- **結果** — <PASS/FAIL>

```bash
aws cloudformation deploy \
  --template-file integrations/splunk-serverless/template.yaml \
  --stack-name <スタック名> \
  --parameter-overrides \
    S3AccessPointArn=<S3 Access Point ARN> \
    HecTokenSecretArn=<Secrets Manager ARN> \
    SplunkHecEndpoint=<HEC エンドポイント URL> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

- **スタックステータス** — <CREATE_COMPLETE / FAILED>
- **作成されたリソース** — Lambda 関数、IAM ロール、DLQ、CloudWatch Alarms、EventBridge Rule

---

### ステップ 2: HEC Token 検証

- **結果** — <PASS/FAIL>

```bash
python3 scripts/verification/splunk_token_validator.py \
  --secret-arn <Secrets Manager ARN>
```

- **トークン形式** — <UUID 形式一致 / 不一致>
- **検証出力** — <出力内容>

---

### ステップ 3: Lambda テストイベント送信

- **結果** — <PASS/FAIL>

```bash
aws lambda invoke \
  --function-name fsxn-splunk-log-shipper \
  --payload file://integrations/splunk-serverless/tests/test_data/sample_s3_event.json \
  --cli-binary-format raw-in-base64-out \
  --region ap-northeast-1 \
  response.json
```

- **レスポンス**:
```json
{"statusCode": <ステータスコード>, "body": {"total_logs": <件数>, "total_shipped": <件数>, "errors": []}}
```

- **確認項目**:
  - [ ] statusCode: 200
  - [ ] total_logs > 0
  - [ ] total_shipped == total_logs
  - [ ] errors: [] (空)

---

### ステップ 4: CloudWatch Logs 確認

- **結果** — <PASS/FAIL>

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-splunk-log-shipper \
  --filter-pattern "Successfully shipped" \
  --start-time $(date -d '15 minutes ago' +%s000) \
  --region ap-northeast-1
```

- **確認項目**:
  - [ ] "Successfully shipped" を含むログ行が存在
  - [ ] タイムスタンプがテストイベント送信後

---

### ステップ 5: Splunk Search ログ到着確認

- **結果** — <PASS/FAIL>

以下の SPL クエリを Splunk Search で実行:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-15m
```

- **返却イベント数** — <件数>
- **到着までの時間** — <秒数>

- **確認項目**:
  - [ ] 1件以上のイベントが返却される
  - [ ] sourcetype が `fsxn:ontap:audit` である
  - [ ] index が `fsxn_audit` である

---

### ステップ 6: フィールド検証

- **結果** — <PASS/FAIL>

Splunk Search でイベントを展開し、以下のフィールドを確認:

| フィールド名 | 期待値 | 実測値 | 非空必須 | 判定 |
|---|---|---|:---:|:---:|
| host | SVM 名 | <実測値> | ✅ | <PASS/FAIL> |
| source | fsxn-observability | <実測値> | ✅ | <PASS/FAIL> |
| sourcetype | fsxn:ontap:audit | <実測値> | ✅ | <PASS/FAIL> |
| index | fsxn_audit | <実測値> | ✅ | <PASS/FAIL> |
| event_type | イベント種別 | <実測値> | ✅ | <PASS/FAIL> |
| user | ユーザー名 | <実測値> | ✅ | <PASS/FAIL> |
| operation | 操作種別 | <実測値> | ✅ | <PASS/FAIL> |
| path | ファイルパス | <実測値> | ✅ | <PASS/FAIL> |
| result | Success/Failure | <実測値> | ✅ | <PASS/FAIL> |
| svm | SVM 名 | <実測値> | ✅ | <PASS/FAIL> |

---

## スクリーンショットエビデンス

以下のスクリーンショットを `docs/screenshots/splunk/` に保存:

| # | ファイル名 | 内容 | 確認項目 |
|---|---|---|---|
| 1 | `splunk-cloudwatch-logs-<YYYYMMDD>.png` | Lambda CloudWatch Logs で "Successfully shipped" ログ行とタイムスタンプが表示されている | [ ] 500KB 以下、PNG 形式 |
| 2 | `splunk-search-results-<YYYYMMDD>.png` | Splunk Search 結果で `index`, `sourcetype`, `host`, `source` フィールドが表示されている | [ ] 500KB 以下、PNG 形式 |
| 3 | `splunk-dashboard-<YYYYMMDD>.png` | Splunk ダッシュボードで FSx for ONTAP 監査ログデータを含むパネルが1つ以上表示されている | [ ] 500KB 以下、PNG 形式 |

![Lambda CloudWatch Logs](../screenshots/splunk/splunk-cloudwatch-logs-<YYYYMMDD>.png)

![Splunk Search 結果](../screenshots/splunk/splunk-search-results-<YYYYMMDD>.png)

![Splunk ダッシュボード](../screenshots/splunk/splunk-dashboard-<YYYYMMDD>.png)

---

## セットアップガイド日英対応確認

- **結果** — <PASS/FAIL>

```bash
python3 scripts/verification/bilingual_comparator.py \
  --ja integrations/splunk-serverless/docs/ja/setup-guide.md \
  --en integrations/splunk-serverless/docs/en/setup-guide.md
```

- **見出し数** — <件数>（一致 / 不一致）
- **コードブロック数** — <件数>（一致 / 不一致）
- **テーブル数** — <件数>（一致 / 不一致）
- **差異件数** — <件数>

| # | セクション | 差異種別 | 内容 |
|---|-----------|---------|------|
| - | - | - | - |

---

## E2E レイテンシ測定

| 測定項目 | 値 |
|---|---|
| S3 オブジェクト作成タイムスタンプ | <タイムスタンプ> |
| Lambda 起動タイムスタンプ | <タイムスタンプ> |
| Splunk `_indextime` | <タイムスタンプ> |
| **E2E レイテンシ（S3 作成 → Splunk 検索可能）** | **<レイテンシ> 秒** |

### レイテンシ内訳

| 区間 | 所要時間 |
|---|---|
| S3 オブジェクト作成 → EventBridge トリガー | <秒数> 秒 |
| EventBridge → Lambda 起動 | <秒数> 秒 |
| Lambda 処理（S3 読み取り + HEC 送信） | <秒数> 秒 |
| HEC 受信 → Splunk インデックス完了 | <秒数> 秒 |
| **合計** | **<レイテンシ> 秒** |

---

## 検出された問題点と対処

| # | 問題内容 | 重要度 | 対処方法 | ステータス |
|---|---------|--------|---------|-----------|
| 1 | <問題内容> | <高/中/低> | <対処方法> | <✅ 対処済み / 📝 記録済み / 🔄 対応中> |

---

## トラブルシューティング実施記録

SPL クエリで15分以内にイベントが返却されない場合の確認項目:

| # | 確認項目 | コマンド/手順 | 結果 |
|---|---------|-------------|------|
| 1 | Lambda 起動確認 | CloudWatch Logs でログストリーム確認 | <結果> |
| 2 | HEC エンドポイント接続性 | `curl -k https://<HEC_ENDPOINT>:8088/services/collector/health` | <結果> |
| 3 | HEC Token 有効性 | `curl -k -H "Authorization: Splunk <TOKEN>" https://<HEC_ENDPOINT>:8088/services/collector/event -d '{"event":"test"}'` | <結果> |
| 4 | Lambda IAM 権限 | CloudWatch Logs でアクセス拒否エラー確認 | <結果> |
| 5 | S3 Access Point 接続性 | `aws s3api list-objects-v2 --bucket <AP_ARN> --max-items 1` | <結果> |

---

## 検証完了サマリ

| ステップ | 名称 | 結果 |
|---------|------|------|
| 1 | CloudFormation スタックデプロイ | <PASS/FAIL> |
| 2 | HEC Token 検証 | <PASS/FAIL> |
| 3 | Lambda テストイベント送信 | <PASS/FAIL> |
| 4 | CloudWatch Logs 確認 | <PASS/FAIL> |
| 5 | Splunk Search ログ到着確認 | <PASS/FAIL> |
| 6 | フィールド検証 | <PASS/FAIL> |
| 7 | スクリーンショット検証 | <PASS/FAIL> |
| 8 | セットアップガイド日英対応確認 | <PASS/FAIL> |

**総合判定**: <✅ 合格 / ❌ 不合格>（E2E 動作確認 <完了 / 未完了>）
