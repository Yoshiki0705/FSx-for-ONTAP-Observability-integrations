# コスト検証: 見積もり vs 実績

🌐 **日本語**（このページ） | [English](../en/cost-validation.md)

## 目的

本ドキュメントは、ベンダー README やコストモデルで提示した見積もりコストと、本番デプロイ後の実際の AWS 請求データを比較・追跡するためのものです。コスト前提の妥当性を検証し、今後のデプロイにおける見積もり精度の向上に活用してください。

> **ステータス**
>
> テンプレート — 本番運用開始後 1 ヶ月の請求データを記入してください。

## 測定方法

1. 単一ベンダー統合を 30 日間デプロイする
2. Cost Explorer から実際の AWS コストを記録する（スタックタグでフィルタ）
3. ベンダー請求画面から実際のベンダーコストを記録する
4. ベンダー README の見積もりと比較する

## コスト見積もり（ドキュメントより）

### AWS インフラストラクチャ（ベンダー統合あたり）

| コンポーネント | 月額見積もり | 前提条件 |
|-----------|----------------------|-------------|
| Lambda（監査ポーラー） | ~$3 | 5 分間隔、256 MB、平均 ~30 秒 |
| EventBridge Scheduler | ~$1 | 8,640 回/月 |
| Secrets Manager | ~$0.40 | 1 シークレット、~8,640 API コール/月 |
| SSM Parameter Store | ~$0 | 無料枠（Standard パラメータ） |
| SQS（DLQ） | ~$0 | 最小メッセージ数（正常時） |
| CloudWatch Logs | ~$1-3 | ログレベルに依存 |
| **AWS 合計** | **~$5-8** | — |

### ベンダープラットフォーム（監査ログ 10 GB/月）

| ベンダー | 月額見積もり | プラン |
|--------|----------------------|------|
| Sumo Logic | $0 | Free（1.25 credits/日） |
| Honeycomb | $0 | Free（20M イベント/月） |
| New Relic | $0 | Free（100 GB/月） |
| Datadog | ~$15 | Logs indexed（15 日保持） |
| Grafana Cloud | ~$50 | Pro プラン |
| Elastic Cloud | ~$95 | Standard |
| Dynatrace | ~$25 | DDU ベース |
| Splunk Cloud | ~$150+ | ボリュームベース |

## 実績コスト（30 日後に記入）

### AWS インフラストラクチャ — 実績

| コンポーネント | 月額実績 | 見積もりとの差分 | 備考 |
|-----------|--------------------|--------------------|-------|
| Lambda | $ ___ | ___ | Duration: ___s 平均 |
| EventBridge Scheduler | $ ___ | ___ | 呼び出し回数: ___ |
| Secrets Manager | $ ___ | ___ | API コール数: ___ |
| CloudWatch Logs | $ ___ | ___ | 取り込み量: ___ GB |
| SQS | $ ___ | ___ | メッセージ数: ___ |
| **AWS 合計** | **$ ___** | **___** | — |

### ベンダープラットフォーム — 実績

| ベンダー | 月額実績 | 見積もりとの差分 | 備考 |
|--------|--------------------|--------------------|-------|
| ___ | $ ___ | ___ | ボリューム: ___ GB |

## 測定コマンド

```bash
# Get Lambda cost for the last 30 days
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{
    "And": [
      {"Dimensions": {"Key": "SERVICE", "Values": ["AWS Lambda"]}},
      {"Tags": {"Key": "aws:cloudformation:stack-name", "Values": ["fsxn-*"]}}
    ]
  }' \
  --region ap-northeast-1

# Get all costs tagged with fsxn stacks
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --filter '{
    "Tags": {"Key": "aws:cloudformation:stack-name", "Values": ["fsxn-*"]}
  }' \
  --region ap-northeast-1
```

## コスト最適化の知見

検証後、発見事項を記録してください:

| 発見事項 | 影響額 | 推奨対応 |
|---------|--------|---------------|
| ___ | $ ___/月 | ___ |

## 比較サマリー

| カテゴリ | 見積もり | 実績 | 精度 |
|----------|-----------|--------|----------|
| AWS インフラストラクチャ | $5-8 | $ ___ | ___% |
| ベンダープラットフォーム | $ ___ | $ ___ | ___% |
| **合計** | **$ ___** | **$ ___** | **___%** |

## 今後の見積もりへの教訓

- [ ] Lambda Duration の前提は正確だったか？
- [ ] ログボリュームの前提は正確だったか？
- [ ] ベンダー料金プランの前提は正確だったか？
- [ ] 想定外のコスト（データ転送、CloudWatch 等）はあったか？

## 関連ドキュメント

- [コストモデル](cost-model.md)
- [パイプライン SLO](pipeline-slo.md)
- [ベンダー比較](vendor-comparison.md)
