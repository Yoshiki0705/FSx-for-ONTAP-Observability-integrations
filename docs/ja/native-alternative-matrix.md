# AWS ネイティブ代替マトリクス — System Manager / Workload Factory / DII

🌐 **日本語**（このページ） | [English](../en/native-alternative-matrix.md)

## 目的

本ドキュメントは、ONTAP System Manager、NetApp Workload Factory、DII Storage Workload Security の主要機能を、本リポジトリの AWS ネイティブ実装にマッピングしたものです。目的: FSx for ONTAP の本番運用・監視・セキュリティが、専有の管理コンソールなしで達成可能であることを示す。

> **位置づけに関する補足**: これは「競合比較」ではありません。各ツールは異なるコンテキストに適しています。本マトリクスは、AWS ネイティブ運用を選択済みのチームが機能カバレッジを確認するため、および選択肢を評価中のチームが追加ライセンスなしで何が利用可能かを理解するためのものです。

---

## ONTAP System Manager — 機能カバレッジ

| System Manager 機能 | AWS ネイティブ同等 | 本リポジトリ | 状態 |
|-------------------|------------------|-----------|:----:|
| **性能: IOPS** | CloudWatch `DataReadOperations` + `DataWriteOperations` | `fsxn-monitoring-dashboard.yaml` | ✅ |
| **性能: スループット** | CloudWatch `DataReadBytes` + `DataWriteBytes` | `fsxn-monitoring-dashboard.yaml` | ✅ |
| **性能: レイテンシ** | CloudWatch `DataReadOperationTime`/`DataWriteOperationTime`（レイテンシは `OperationTime * 1000 / Operations` で算出） | `fsxn-monitoring-dashboard.yaml` には未実装 | ⚠️ メトリクスは利用可能、ウィジェット未実装 |
| **性能: ネットワーク利用率** | CloudWatch `NetworkThroughputUtilization` | `fsxn-monitoring-dashboard.yaml` | ✅ |
| **容量: ストレージ使用量** | CloudWatch `StorageUsed` + `StorageCapacityUtilization` | `fsxn-monitoring-dashboard.yaml` | ✅ |
| **容量: アラート** | CloudWatch Alarm on `StorageCapacityUtilization` | `fsxn-monitoring-dashboard.yaml`（閾値アラーム） | ✅ |
| **Qtree: クォータ管理** | ONTAP REST API `/storage/quota/rules` | CLI スクリプト / 手動 | ⚠️ API経由の管理、GUIなし |
| **Qtree: クォータ監視** | Lambda → ONTAP REST API → CloudWatch Custom Metric | `qtree-quota-monitor.yaml` | ✅ |
| **Qtree: クォータアラート** | CloudWatch Alarm on `QtreeQuotaUsedPercent` | `qtree-quota-monitor.yaml` | ✅ |
| **ボリューム: 作成/削除/リサイズ** | FSx コンソール + ONTAP REST API | デモテンプレート + FSx コンソール（汎用ボリューム管理テンプレートはなし） | ⚠️ |
| **Snapshot: 作成/スケジュール** | FSx Backup + ONTAP REST API | `ontap_response.py` + FSx ネイティブ | ✅ |
| **Snapshot: リストア** | FSx コンソール + ONTAP REST API | `restore-verification.yaml`（リストア前検証） | ✅ |
| **NFS エクスポート管理** | ONTAP REST API | `ontap_response.py`（export-policy deny rule によるブロック） | ⚠️ ブロックのみ |
| **SMB 共有管理** | ONTAP REST API | `ontap_response.py`（name-mapping deny によるブロック） | ⚠️ ブロックのみ |
| **EMS イベントビューア** | CloudWatch Logs（syslog VPC EP） | `syslog-vpce-cloudwatch.yaml` | ✅ |
| **ARP ステータス** | EMS → Observability パイプライン | 9 ベンダー統合 + EMS webhook | ✅ |
| **SnapMirror 管理** | FSx コンソール + ONTAP REST API | ドキュメント（手動手順） | ⚠️ 自動化なし |
| **QoS ポリシー** | ONTAP REST API | — | ❌ 対象外 |
| **ネットワーク (LIF/DNS)** | FSx コンソール + ONTAP REST API | — | ❌ インフラ管理 |
| **FPolicy 設定** | ONTAP REST API | FPolicy サーバー (Fargate) + スクリプト | ✅ |
| **監査設定** | ONTAP CLI/REST API | セットアップスクリプト + docs | ✅ |

---

## Workload Factory — 機能カバレッジ

| Workload Factory 機能 | AWS ネイティブ同等 | 本リポジトリ | 状態 |
|---------------------|------------------|-----------|:----:|
| **ファイルシステム作成ウィザード** | FSx コンソール / CloudFormation | `demo-ad-environment.yaml` + テンプレート群 | ✅ |
| **コスト最適化推奨** | AWS Cost Explorer + CloudWatch メトリクス | — | ❌ 将来対応 |
| **FabricPool 階層化推奨** | CloudWatch 容量メトリクス + ONTAP tiering API | ドキュメント（手動ガイダンス） | ⚠️ |
| **バックアップ管理** | FSx Backup（AWS マネージド） | AWS ネイティブ（テンプレート不要） | ✅ |
| **レプリケーション設定** | FSx コンソール + SnapMirror API | ドキュメント（手動手順） | ⚠️ |
| **セキュリティ姿勢スキャン** | AWS Security Hub + cfn-guard | `guard/rules/` + CI | ✅ |
| **GenAI データ準備** | Bedrock Knowledge Bases + S3 AP | S3AP Patterns リポジトリ | ✅ |
| **データ移行** | AWS DataSync | — | ❌ 対象外 |
| **コンプライアンステンプレート** | CloudFormation + cfn-guard | `compliance-evidence-pack.md` | ✅ |

---

## DII Storage Workload Security — 機能カバレッジ

> **DII** = Data Infrastructure Insights（旧 Cloud Insights）。Storage Workload Security はそのランサムウェア検知・対応モジュールです。

> **応答レイテンシに関する補足**: DII はインバンドでユーザーをブロックします（サブ秒、ONTAP データパスに統合）。本リポジトリの Lambda ベースの応答は、単一の封じ込めアクション（ブロック + スナップショット）で2〜5秒、完全な復旧ポイント検証で25〜50分（FSx for ONTAP 同期遅延が支配的）です。サブ秒の応答が必須要件であれば DII を選択し、検知が既に SIEM にありカスタマイズ可能な応答ロジックが必要な場合は本アプローチを選択してください。

| DII 機能 | AWS ネイティブ同等 | 本リポジトリ | 状態 |
|---------|------------------|-----------|:----:|
| **ML ベースライン異常検知** | ONTAP ARP/AI（内蔵）+ SIEM ML | EMS → Datadog/9 ベンダー（パイプライン提供のみ。ML検知設定はSIEM側の責任） | ✅ パイプライン |
| **ユーザー自動ブロック** | ONTAP REST API（name-mapping deny） | `ontap_response.py` + `automated-response.yaml` | ✅ E2E検証済み |
| **IP 自動ブロック** | ONTAP REST API（export-policy）+ VPC NACL | `ontap_response.py` + `automated-response.yaml` | ✅ E2E検証済み |
| **保護 Snapshot** | ONTAP REST API | `ontap_response.py` `create_snapshot` | ✅ E2E検証済み |
| **セッション切断** | ONTAP REST API（CIFS sessions） | `ontap_response.py` `disconnect_smb_sessions` | ✅ E2E検証済み |
| **Forensics ダッシュボード** | ベンダーダッシュボード（Datadog / Grafana / Splunk / Elastic / Sumo Logic） | ベンダー別ダッシュボード定義 — 下表参照 | ✅ 再現可能 |
| **ユーザーアクティビティタイムライン** | Timeseries / Log パネル | Forensics ダッシュボード（全対応ベンダー） | ✅ |
| **ファイルアクセス監査証跡** | Log stream / Discover / Log Explorer | Forensics ダッシュボード（全対応ベンダー） | ✅ |
| **影響ボリューム可視化** | TopList / Bar chart | Forensics ダッシュボード（全対応ベンダー） | ✅ |
| **アラート重要度分布** | 集約ウィジェット | Forensics ダッシュボード（全対応ベンダー） | ✅ |
| **復旧ポイント検証** | Step Functions（FlexClone + S3 AP + Scan） | `restore-verification.yaml` | ✅ E2E検証済み |
| **自動解除（TTL）** | EventBridge Scheduler | `automated-response-ttl.yaml` | ✅ |
| **マルチ SVM 封じ込め** | Step Functions fan-out / multi-SVM CLI | `automated-response-multi-svm-cli.sh` | ✅ |

---

### Forensics ダッシュボード — ベンダー別リファレンス

監査/EMS/FPolicy ログを受信する全てのベンダーで同等のフォレンジクスビューを構築可能です。以下は本リポジトリで提供済みのアーティファクトです:

| ベンダー | 形式 | アーティファクト | デプロイ方法 |
|---------|------|-------------|------------|
| **Datadog** | Dashboard JSON | [`integrations/datadog/dashboards/forensics-dashboard.json`](../../integrations/datadog/dashboards/) | Datadog REST API (`POST /api/v1/dashboard`) |
| **Grafana** | Dashboard JSON（Loki + LogQL） | [`integrations/grafana/dashboards/forensics-investigation.json`](../../integrations/grafana/dashboards/forensics-investigation.json) | [`deploy-forensics-dashboard.sh`](../../integrations/grafana/scripts/deploy-forensics-dashboard.sh) または UI インポート |
| **Elastic** | Kibana NDJSON（Saved Searches + Dashboard） | [`integrations/elastic/dashboards/forensics-investigation.ndjson`](../../integrations/elastic/dashboards/) | Kibana Saved Objects API または UI インポート |
| **Splunk** | SPL Saved Searches + Dashboard Studio | [`integrations/splunk-serverless/searches/`](../../integrations/splunk-serverless/searches/) | Splunk UI または REST API |
| **Sumo Logic** | Dashboard JSON（DashboardV2） | [`integrations/sumo-logic/dashboards/forensics-investigation.json`](../../integrations/sumo-logic/dashboards/) | Sumo Logic Content API (`POST /v2/dashboards`) |
| **New Relic** | Dashboard JSON（NRQL） | [`integrations/new-relic/dashboards/forensics-investigation.json`](../../integrations/new-relic/dashboards/) | NerdGraph API (`dashboardCreate` mutation) |
| **Dynatrace** | Dashboard JSON（DQL） | [`integrations/dynatrace/dashboards/forensics-investigation.json`](../../integrations/dynatrace/dashboards/) | Dynatrace Dashboards API (`POST /api/config/v1/dashboards`) |

> **ベンダー中立の原則**: フォレンジクス機能は特定のベンダーに依存しません。監査ログが既に配信されているベンダーを選択してください。調査ワークフロー（ユーザータイムライン → 全アクティビティ → IP ドリルダウン → ファイルエンティティ履歴）は全ベンダーで同一 — 異なるのはクエリ言語のみです。

> **Honeycomb に関する補足**: Honeycomb は分散トレーシングと高カーディナリティイベント探索に最適化されています。パイプライン経由で FSx for ONTAP 監査ログを受信しますが、フォレンジック調査ワークフロー向けの従来型ダッシュボード/保存検索アーティファクトは提供していません。トレーシング相関には Honeycomb を、フォレンジクスダッシュボードには上記7ベンダーのいずれかを使用してください。

> **CrowdStrike Falcon LogScale に関する補足**: CrowdStrike Falcon LogScale も HEC 経由で FSx for ONTAP 監査ログを受信しますが（[ベンダー比較](vendor-comparison.md) 参照）、本リポジトリはまだこのベンダー向けのフォレンジクスダッシュボードアーティファクトを提供していません。上表の7ベンダーは現時点で提供済みのものであり、本リポジトリが対応する全ベンダーの一覧ではありません。

---

## サマリ: カバレッジ状況

| プロダクト | マッピング機能数 | ✅ 対応済み | ⚠️ 部分対応 | ❌ 対象外 |
|----------|:-------------:|:---------:|:---------:|:--------:|
| System Manager | 21 | 13 | 6 | 2 |
| Workload Factory | 9 | 5 | 2 | 2 |
| DII SWS | 13 | 13 | 0 | 0 |

**重要な洞察**: セキュリティ/インシデント対応機能（DII 相当）は **100% カバー**。運用監視（System Manager 相当）は **62% 完全対応 + 29% 部分対応**（マッピングした機能のうちそれぞれ21件中13件・6件）— 部分対応はセキュリティブロック専用のエクスポート/共有管理実装とデモ用ボリュームテンプレートです。残る **10%**（QoS、LIF/DNS）は FSx コンソールに適したインフラ管理タスクです。

> **この表の正しい読み方**: 「100% カバー」は、本リポジトリが実装している封じ込め/検知対応アクションに限定した機能レベルの対応範囲を示すものであり、本アプローチが DII の完全な代替であるという主張ではなく、両者を単純に比較して一方を推奨する趣旨のものでもありません。DII の ML 検知、エージェントベースの収集、ベンダー管理による運用は、本リポジトリがゼロから構築していない機能です。このカバー率は、本リポジトリのより狭い範囲の AWS ネイティブな仕組みが、別の経路で同じ*封じ込めアクション*に到達していることを表しています。どちらの状況にどちらが適するかは、下記の[選び方ガイド](#選び方ガイド)を参照してください。

---

## 選び方ガイド

| 状況 | 推奨 |
|------|------|
| AWS Observability（Datadog、Grafana、Splunk等）に投資済み + ストレージ層 IR が欲しい | **本アプローチ** — 既存スタックをストレージ層の封じ込めに拡張 |
| SIEM 設定なしでターンキーの ML 異常検知が欲しい | **DII Storage Workload Security** — 内蔵のユーザー別ベースライン、外部 SIEM 不要 |
| GUI でのストレージ日常運用（ボリューム作成、共有管理）が必要 | **FSx コンソール + ONTAP System Manager** — インタラクティブ管理に特化 |
| 大規模な自動インフラプロビジョニングが必要 | **CloudFormation/Terraform** — 監視ツールの選択に関わらず IaC が正しいパターン |
| 上記全てが大規模フリートで必要 | ハイブリッド検討: 本アプローチ（IR）+ DII（検知）+ FSx コンソール（アドホック操作） |

> **全てを1つのツールでカバーするものは存在しません。** 上記マトリクスは本リポジトリがカバーする範囲です。インタラクティブ操作には FSx コンソール、ベンダー管理の ML 検知には DII、検知が既に SIEM にあり追加ライセンスなしで自動封じ込め + フォレンジックエビデンスが欲しい場合は本リポジトリを使ってください。

> **コストモデルの違いに関する補足**: DII は TB 単位のライセンス（価格は NetApp にお問い合わせ）。本リポジトリの運用コストは Lambda 呼び出し + CloudWatch メトリクス + VPC Endpoints が中心（典型的な単一 SVM 構成で月額 $15〜30 のベースライン、あくまで概算であり本リポジトリのドキュメントではまだコンポーネント単位の内訳を示していません）。これは同一条件での TCO 比較ではありません — AWS 側の数字はインフラコストのみであり、検知ルールのチューニング、誤検知の調査、四半期ごとの訓練といった継続的な作業は含まれていません。これらは本アプローチにも DII にも同様に発生します。DII のライセンス費用についても、同様にセットアップおよび運用オーバーヘッドと合わせて検討する必要があります。本リポジトリがドキュメント化しているコスト内訳としては [コストモデル](cost-model.md)（ログ配信パイプラインのコスト。ここで扱っているインシデント対応スタックとは別のコスト領域）を参照してください。

---

## デプロイクイックリファレンス

| 機能 | テンプレート | デプロイ順序 |
|------|-----------|:---------:|
| 性能＆容量 | `fsxn-monitoring-dashboard.yaml` | いつでも |
| Qtree クォータ監視 | `qtree-quota-monitor.yaml` | VPC EP 存在後 |
| インシデント対応 | `automated-response.yaml` | Tier 2 |
| 復旧検証 | `restore-verification.yaml` | Tier 2 の後 |
| フォレンジクス | ベンダー別ダッシュボード JSON/クエリ（上表参照） | ログパイプライン後 |

### Qtree クォータアラーム — 問題の Qtree を特定する方法

Qtree クォータアラームが発火した場合、SVM 上の**少なくとも1つの** qtree が閾値を超えたことを示します。アラームは全 qtree の `Maximum` を使用するため、アラーム自体はどの qtree かを教えてくれません。以下のコマンドで特定してください:

```bash
aws cloudwatch get-metric-data \
  --metric-data-queries '[{
    "Id": "q1",
    "MetricStat": {
      "Metric": {
        "Namespace": "FSxONTAP/Qtree",
        "MetricName": "QtreeQuotaUsedPercent",
        "Dimensions": [{"Name": "SvmName", "Value": "<your-svm-name>"}]
      },
      "Period": 300,
      "Stat": "Maximum"
    }
  }]' \
  --start-time "$(date -u -v-1H +%Y-%m-%dT%H:%M:%S)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%S)"
```

または、個別 qtree メトリクスを直接確認:

```bash
aws cloudwatch list-metrics \
  --namespace "FSxONTAP/Qtree" \
  --metric-name "QtreeQuotaUsedPercent" \
  --dimensions Name=SvmName,Value=<your-svm-name> \
  --query 'Metrics[].Dimensions[?Name==`QtreeName`].Value' \
  --output text
```

---

## 関連ドキュメント

- [Adoption Playbook — 可観測性](https://github.com/Yoshiki0705/FSx-for-ONTAP-Adoption-Playbook/blob/main/docs/en/domains/observability/README.md) — **経路の選択と、この対応表が扱う範囲の限界。** 本表が答えるのは「その機能を AWS ネイティブで到達できるか」で、ハブが答えるのは「収集経路をこれにすべきか」です。Harvest 経路では `ONTAP: Qtree` が既に対応対象であることもハブ側に記録されています
- [デプロイメントガイド](deployment-guide.md) — 全スタックのデプロイパスと VPC Endpoint 管理
- [サイバーレジリエンス機能マップ](cyber-resilience-capability-map.md) — NIST CSF 2.0 マッピング
- [自動応答ガイド](automated-response-guide.md) — DII 相当の封じ込めアクション
- [検証済みクリーン復旧ポイントガイド](verified-recovery-point-guide.md) — Step Functions 検証ワークフロー
- [コンプライアンスエビデンスパック](compliance-evidence-pack.md) — ISMAP/FISC/SOC2 監査証跡テンプレート
