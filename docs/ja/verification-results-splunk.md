# Splunk Serverless 統合 動作確認結果

🌐 **日本語**（このページ） | [English](../en/verification-results-splunk.md)

> **このファイルは以前、記入前のテンプレートでした** — 判定欄はすべて `<PASS/FAIL>` で
> 環境情報もプレースホルダのままで、検証記録の外形をしていながら何も主張していない状態
> でした。実際に存在する証跡（[統合の README](../../integrations/splunk-serverless/README.md#e2e-verification-evidence)
> に記録されているもの）に置き換えました。
> 以前あった空欄のフォームは
> [`shared/scripts/vendor-verification-checklist.md`](../../shared/scripts/vendor-verification-checklist.md)
> と重複しています。新規に実行する場合はそちらのチェックリストを使ってください。

---

## 監査ログ経路 — 検証済み

**環境**: Splunk Enterprise 10.4.0 をローカルの Docker で実行
（`splunk/splunk:latest`、`--platform linux/amd64`）。HEC トークンは環境変数
`SPLUNK_HEC_TOKEN` で与えています。

**方法**: `python3 shared/scripts/test-xml-e2e.py --vendor splunk`

| 項目 | 結果 |
|------|------|
| XML 監査ログのパース | ✅ 5 イベントをパース（EventID 4663 / 4656 / 4660） |
| HEC 配信 | ✅ HTTP 200、ボディ `{"text":"Success","code":0}` |
| Splunk のインデックス | ✅ `fsxn_audit` インデックスに 5 イベントを確認 |
| フィールド抽出 | ✅ `user` / `path` / `client_ip` / `event_type` / `result` / `svm` / `timestamp` |
| Splunk Search UI | ✅ 全イベントが検索可能でフィールド分解済み |

**スクリーンショット**: [`integrations/splunk-serverless/screenshots/splunk-e2e-search-fsxn-audit-xml.png`](../../integrations/splunk-serverless/screenshots/splunk-e2e-search-fsxn-audit-xml.png)

### この結果の適用範囲

Docker 上の Splunk Enterprise は正当な対象です。HTTP Event Collector の API も
`/services/collector/event` の契約も Splunk Cloud と同一なので、ここで受理される HEC
ペイロードは Splunk Cloud でも受理されます。検証されていないのは Splunk Cloud 固有の
入口部分（DNS、TLS 終端、Cloud スタックが発行するトークン）です。

この差し替えは手抜きではありません。Splunk Cloud の**無償トライアル**アカウントは HEC の
DNS レコード（`http-inputs-<stack>.splunkcloud.com`）が確実に払い出されないため、
トライアルではこのテスト自体が実施できません。ローカル検証には Splunk Enterprise を、
本番相当の実行には有償の Splunk Cloud を使用してください。

---

## EMS / FPolicy 経路 — 未記録

`integrations/splunk-serverless/template-fpolicy.yaml` は提供済みで、EMS ハンドラも
ユニットテストで担保されていますが、**どちらの経路も E2E の実行記録がありません**。
[テレメトリ経路のカバレッジ](README.md#テレメトリ経路のカバレッジ)の表が両方を ✅ ではなく
🔧（実装済み・E2E 未検証）としているのはこのためです。

「壊れている」ではなく「未テスト」と理解してください。EMS / FPolicy の共通インフラ自体は
[EMS/FPolicy E2E 動作確認結果](verification-results-ems-fpolicy.md)で検証済みで、Splunk の
ハンドラも検証済みベンダーと同じ `shared/python/ems_event.py` /
`shared/python/fpolicy_event.py` を再利用しています。未検証なのは、この 2 種のイベントに
対する Splunk 固有の配信部分です。

---

## Firehose 経路 — 未記録

`template-firehose.yaml` は大量ログ向けの代替経路です（Splunk は Firehose の宛先を
組み込みで持つため、レコードごとの Lambda が不要）。こちらも E2E の実行記録はありません。

---

## 記録されていない項目

| 項目 | 状態 |
|------|------|
| 検証日と検証者 | 未記録 |
| AWS アカウント、CloudFormation スタック名 | 未記録 |
| Splunk Cloud エンドポイントでの実行 | 未実施 — 上記「適用範囲」を参照 |
| EMS 経路の E2E | 未記録 |
| FPolicy 経路の E2E | 未記録 |
| Firehose 経路の E2E | 未記録 |

---

## 自動テストによる担保

```bash
python -m pytest integrations/splunk-serverless/tests/ -v
```

119 テスト。HEC ペイロード構築、バッチ分割、Firehose の transform 関数、EMS / FPolicy
イベントのパース、チェックポイント処理をカバーします。検証しているのはペイロードの形であり、
配信そのものではありません。

---

## 関連情報

- [Splunk Serverless 統合 README](../../integrations/splunk-serverless/README.md)
- [EC2 からの移行ガイド](../../integrations/splunk-serverless/docs/ja/migration-from-ec2.md)
- [EMS/FPolicy E2E 動作確認結果](verification-results-ems-fpolicy.md)
- [ベンダー検証チェックリスト](../../shared/scripts/vendor-verification-checklist.md)
- [ベンダー比較](vendor-comparison.md)
