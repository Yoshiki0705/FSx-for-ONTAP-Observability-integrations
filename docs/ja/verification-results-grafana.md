# Grafana Cloud 統合 動作確認結果

🌐 **日本語**（このページ） | [English](../en/verification-results-grafana.md)

> **これは証跡の索引であり、日付付きの実行記録ではありません。** 同じディレクトリにある
> 他ベンダーの記録（Datadog、Honeycomb、Elastic ほか）は検証の実行中に書かれたため、
> 日付・アカウント・スタック名・正確なログ件数を含んでいます。Grafana については
> それが記録されていません。存在するのは以下のスクリーンショット証跡であり、
> [テレメトリ経路のカバレッジ](README.md#テレメトリ経路のカバレッジ)の表が Grafana の
> 3 経路すべてを ✅ としている根拠はこれです。
>
> 欠けているのは検証そのものではなく記録です。このファイルは提示できる事実だけを記載し、
> 記録されていない項目を明示します。環境情報を後から再構成することはしません。

---

## 証跡の内容

スクリーンショットはすべて `integrations/grafana/docs/screenshots/` にあり、実データが
パイプラインを流れている状態の Grafana Cloud に対して取得されています。各行のクエリを
実行すれば再現できます。

| 経路 | スクリーンショット | 実行したクエリ | 示している内容 |
|------|------------------|--------------|--------------|
| 監査ログ | `explore-log-arrival.png` | `{service_name="fsxn-ontap"}` | FSx for ONTAP の監査イベントが Grafana Cloud Loki に到達し、Explore で timestamp と content フィールド付きで照会できること |
| 監査ログ | `dashboard-overview.png` | （ダッシュボード） | ダッシュボードの 4 パネル（ログ量・操作種別・ユーザー活動・失敗イベント）すべてにデータが描画されること |
| 監査ログ | `grafana-unauthorized-access.png` | 失敗アクセスで絞り込み | 失敗アクセスイベントが成功と区別できること |
| EMS イベント | `grafana-ems-events.png` | `{service_name="fsxn-ems"}` | EMS イベントが `event_name` / `severity` / `svm` フィールド付きで到達すること |
| FPolicy ファイル操作 | `grafana-fpolicy-events.png` | `{service_name="fsxn-fpolicy"}` | FPolicy のファイル操作が `operation` / `file_path` / `user` フィールド付きで到達すること |

各スクリーンショットの取得手順（画面遷移と確認すべきフィールド）は
[`integrations/grafana/docs/screenshots/README.md`](../../integrations/grafana/docs/screenshots/README.md)
にあります。

> **`grafana-logs-arrival.png` に関する補足**
>
> このファイルは `explore-log-arrival.png` とバイト単位で同一（MD5 一致）です。別名の
> 重複であり、2 回目の実行を示す独立した証跡ではありません。

---

## 検証された配信経路

```
FSx for ONTAP audit volume
  → S3 Access Point
  → EventBridge Scheduler (poll + SSM checkpoint)
  → Lambda
  → Grafana Cloud OTLP Gateway
```

検証済みの経路は OTLP Gateway です（`otlp_http` exporter →
`https://otlp-gateway-prod-<region>.grafana.net/otlp`、`base64(instanceID:token)`
による Basic 認証）。`loki` exporter はレガシーなフォールバックとして統合に残っていますが、
**検証済みの経路ではありません**。理由は[統合の README](../../integrations/grafana/README.md)
を参照してください。

---

## 記録されていない項目

「記録が無いこと」を「合格」と誤読されないよう明示します。

| 項目 | 状態 |
|------|------|
| 検証日と検証者 | 未記録 |
| AWS アカウント、スタック名、Grafana インスタンス ID | 未記録 |
| 経路ごとの正確なログ件数 | 未記録（スクリーンショットは到達を示すが件数は示さない） |
| EMS のスクリーンショットが実 ONTAP の Webhook 由来か投入ペイロード由来か | 未記録 |
| Firehose 配信経路 | 対象外 — Grafana Cloud に Firehose の宛先が無いため、この統合は Lambda のみ |

この統合について日付付きの完全な記録を作成する場合は、
[`shared/scripts/vendor-verification-checklist.md`](../../shared/scripts/vendor-verification-checklist.md)
を順に実施し、[Honeycomb の記録](verification-results-honeycomb.md)と同じ形式で
書き起こしてください。

---

## 自動テストによる担保

ユニットテストとプロパティテストは Grafana への配信自体を検証しませんが、配信が依存する
ペイロード構築を網羅しています。

```bash
python -m pytest integrations/grafana/tests/ -v
```

102 テスト。OTLP ペイロードの整形、Loki フォールバックのフォーマッタ、認証ヘッダの構築、
バッチ分割、チェックポイント処理をカバーします。

---

## 関連情報

- [Grafana 統合 README](../../integrations/grafana/README.md)
- [Grafana セットアップガイド](../../integrations/grafana/docs/ja/setup-guide.md)
- [OTel Collector 動作確認結果](verification-results-otel-collector.md) — Grafana Cloud は OTLP バックエンドとしてもそちらに登場します
- [EMS/FPolicy E2E 動作確認結果](verification-results-ems-fpolicy.md) — EMS と FPolicy の共通インフラ
- [ベンダー検証チェックリスト](../../shared/scripts/vendor-verification-checklist.md)
