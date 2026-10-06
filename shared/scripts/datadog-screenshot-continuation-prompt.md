# Datadog E2E スクリーンショット撮影 — 継続プロンプト

このプロンプトを新しい Kiro セッションに貼り付けて、Datadog のスクリーンショット撮影を完了してください。

---

## コンテキスト

`datadog-e2e-verification` Spec の動作確認は完了しています。以下が確認済み:
- S3 監査ログ → Datadog（source:fsxn）✅
- EMS Webhook → Datadog（source:fsxn-ems）✅ — ARP ランサムウェア検知イベント到着確認
- FPolicy → Datadog（source:fsxn-fpolicy）✅ — ファイル操作イベント到着確認

残作業はスクリーンショットの撮影のみです。

## 撮影すべきスクリーンショット

Playwright MCP（`--allow-unrestricted-file-access` 付き）を使用して以下を撮影してください。

### 手順

1. Datadog にログイン（AP1 サイト: https://ap1.datadoghq.com）
   - Email: (internal reviewer)
   - Password: (認証情報はここに記録しないこと。使い捨てのデモアカウントかシークレットマネージャーを使用する / do not record credentials here; use a disposable demo account or a secrets manager)
   - reCAPTCHA が出たら手動で突破してください

2. 以下のスクリーンショットを撮影（`${PROJECT_DIR}` はこのリポジトリのクローンのルート）:

| # | URL / 操作 | 保存先 |
|---|-----------|--------|
| 1 | `https://ap1.datadoghq.com/logs?query=source%3Afsxn-ems&from_ts=1778932000000&to_ts=1778980000000` | `${PROJECT_DIR}/docs/screenshots/datadog-arp-detection.png` |
| 2 | 上記の検索結果から ARP イベントをクリックして詳細展開 | `${PROJECT_DIR}/docs/screenshots/datadog-arp-log-detail.png` |
| 3 | `https://ap1.datadoghq.com/logs?query=source%3Afsxn-fpolicy&from_ts=1778932000000&to_ts=1778980000000` | `${PROJECT_DIR}/docs/screenshots/datadog-fpolicy-suspect-activity.png` |
| 4 | AWS CloudWatch コンソール → Log groups → `/aws/lambda/fsxn-datadog-ems-fpolicy-ems` | `${PROJECT_DIR}/docs/screenshots/aws-ems-lambda-logs.png` |

### Playwright MCP コマンド例

```
browser_navigate → URL
browser_wait_for → ログが表示されるまで待機
browser_take_screenshot → ファイルパスを指定して保存
```

### 注意事項

- Playwright MCP は `--allow-unrestricted-file-access` オプション付きで設定済み
- reCAPTCHA は自動突破できないため、表示されたら手動で対応
- 時間範囲が合わない場合は `from_ts` / `to_ts` を調整（Unix ミリ秒）
- Datadog のログは18時間以上前のものは検索できない（仕様）ため、必要に応じて Lambda を再 invoke してログを再送信:
  ```bash
  aws lambda invoke \
    --function-name fsxn-datadog-ems-fpolicy-ems \
    --payload '{"body":"{\"messageName\":\"arw.volume.state\",\"severity\":\"alert\",\"node\":\"fsxn-node-01\",\"svmName\":\"svm-prod-01\",\"time\":\"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'\",\"message\":\"Anti-ransomware: Volume vol_data state changed to attack-detected\",\"parameters\":{\"volume_name\":\"vol_data\",\"state\":\"attack-detected\"}}","requestContext":{"requestId":"screenshot-test"}}' \
    --cli-binary-format raw-in-base64-out \
    --region ap-northeast-1 \
    /tmp/ems-response.json
  ```

## 完了後

撮影完了後、以下を実行して全スクリーンショットが揃っていることを確認:
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.verification.screenshot_validator import validate_screenshots
results = validate_screenshots('docs/screenshots')
for r in results:
    print(f'{r.result}: {r.step_name}')
"
```
