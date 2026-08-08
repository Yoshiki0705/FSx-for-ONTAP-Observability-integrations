🌐 **日本語** | [English](../en/agent-fpolicy-correlation-pattern.md)

# AI エージェントアクセスログ × ONTAP FPolicy 監査ログ 統合パターン

> **ステータス**: 設計ドキュメント（実装は後続フェーズ）
> **前提**
>
> エージェント基盤（Omnigent / AgentCore）の構築が先行
> **関連**
>
> [fsxn-lakehouse-integrations クロスリポジトリ連携戦略](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/docs/ja/cross-repo-integration-strategy.md)

---

## 概要

AI エージェントが FSx for ONTAP 上のファイルにアクセスする際、「どのエージェントが、どのセッションで、いつ、どのファイル由来の情報を使ったか」を追跡可能にする統合パターンを定義する。

エージェント側の OpenTelemetry スパンと ONTAP FPolicy のファイルアクセスイベントを時間軸で結合し、エンドツーエンドの監査証跡を構築する。

### 解決する課題

| 課題 | 説明 |
|------|------|
| エージェントの透明性 | エージェントがどのファイルを読んだか、LLM コンテキストに何を渡したか |
| Permission-aware 監査 | エージェントが権限外データにアクセスしていないことの証明 |
| インシデント調査 | 不正な回答や情報漏洩時の原因ファイル特定 |
| コンプライアンス | 「誰が・いつ・何を・どの AI に読ませたか」の追跡 |

---

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│                    AI エージェント層                          │
│  Omnigent / AgentCore / Bedrock Agent                       │
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌──────────────┐   │
│  │ Supervisor  │───▶│ Sub-Agent   │───▶│ Tool: FSx    │   │
│  │   Agent     │    │ (Quality)   │    │ File Reader  │   │
│  └─────────────┘    └─────────────┘    └──────┬───────┘   │
│                                                │            │
│         OTel Spans (tool_call, file_access)    │            │
└─────────────────────────────────────────────┬──┼────────────┘
                                              │  │
                    ┌─────────────────────────┘  │
                    ▼                            ▼
┌──────────────────────────┐    ┌──────────────────────────────┐
│   ADOT Collector          │    │   FSx for ONTAP              │
│   (OTel → CloudWatch/     │    │   FPolicy → SQS → Lambda    │
│    X-Ray / S3 / SIEM)     │    │   → SIEM / S3 / OpenSearch  │
└────────────┬─────────────┘    └─────────────┬────────────────┘
             │                                 │
             ▼                                 ▼
┌──────────────────────────────────────────────────────────────┐
│                   分析・突合レイヤー                           │
│   OpenSearch / Athena / CloudWatch Logs Insights             │
│                                                              │
│   JOIN ON: service_account + time_window + file_path         │
│                                                              │
│   ┌─────────────────────────────────────────────────────┐   │
│   │           Correlation Record（突合結果）               │   │
│   └─────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

---

## ログスキーマ設計

### 1. エージェントアクセススパン（OTel Span Attributes）

エージェントが FSx for ONTAP 上のファイルにアクセスするツール呼び出しを記録する OTel スパン。

| フィールド | 型 | 説明 | 例 |
|-----------|------|------|-----|
| `trace_id` | string | W3C Trace Context | `4bf92f3577b34da6a3ce929d0e0e4736` |
| `span_id` | string | スパン ID | `00f067aa0ba902b7` |
| `parent_span_id` | string | 親スパン（オーケストレーター） | `a1b2c3d4e5f6a7b8` |
| `agent_id` | string | エージェント識別子 | `quality-supervisor` |
| `session_id` | string | セッション ID（Omnigent/AgentCore） | `sess_2026061812345` |
| `tool_name` | string | 実行ツール名 | `read_file`, `list_directory` |
| `file_path` | string | リクエストしたファイルパス（正規化 POSIX） | `/vol1/shared/reports/Q2.xlsx` |
| `svm_name` | string | 対象 SVM | `svm-prod-01` |
| `volume_name` | string | 対象ボリューム | `vol_shared_docs` |
| `service_account` | string | ファイルアクセスに使用した ID | `CORP\svc-agent-quality` |
| `operation` | enum | 操作種別 | `read` / `write` / `list` / `delete` |
| `timestamp_start` | ISO 8601 | スパン開始 | `2026-06-18T10:30:00.000Z` |
| `timestamp_end` | ISO 8601 | スパン終了 | `2026-06-18T10:30:01.234Z` |
| `status` | enum | 結果 | `ok` / `error` |
| `bytes_read` | int | 読み取りバイト数 | `524288` |
| `user_principal` | string | エージェントを起動した人間のユーザー | `tanaka@corp.example.com` |
| `purpose` | string | アクセス目的（任意） | `rag_context_retrieval` |

### 2. FPolicy イベント（ONTAP 側）

ONTAP FPolicy が記録するファイルアクセスイベント。

| フィールド | 型 | 説明 | 例 |
|-----------|------|------|-----|
| `event_id` | string | ONTAP 内部イベント ID | `fp-00001234` |
| `timestamp` | ISO 8601 | イベント発生時刻 | `2026-06-18T10:30:00.456Z` |
| `svm_name` | string | SVM 名 | `svm-prod-01` |
| `volume_name` | string | ボリューム名 | `vol_shared_docs` |
| `path` | string | ファイルパス | `/vol1/shared/reports/Q2.xlsx` |
| `user` | string | アクセスユーザー（SMB: DOMAIN\user） | `CORP\svc-agent-quality` |
| `client_ip` | string | クライアント IP | `<agent-host-ip>` |
| `operation` | enum | ファイル操作 | `open` / `read` / `write` / `close` / `delete` / `rename` |
| `protocol` | enum | プロトコル | `nfs` / `smb` |
| `result` | enum | 結果 | `success` / `failure` |
| `handle_id` | string | ファイルハンドル | `0x000001A4` |

### 3. 突合レコード（Correlation Record）

分析レイヤーで生成される結合結果。

| フィールド | 型 | 説明 |
|-----------|------|------|
| `correlation_id` | string | 一意な突合 ID（生成） |
| `trace_id` | string | エージェントスパンの trace_id |
| `span_id` | string | エージェントスパンの span_id |
| `fpolicy_event_id` | string | FPolicy イベント ID |
| `agent_id` | string | エージェント識別子 |
| `session_id` | string | セッション ID |
| `user_principal` | string | 起動した人間のユーザー |
| `file_path` | string | 正規化済みファイルパス |
| `correlation_confidence` | enum | `high` / `medium` / `low` |
| `correlation_method` | string | 突合方法 |
| `time_delta_ms` | int | FPolicy timestamp − span_start（ミリ秒） |
| `created_at` | ISO 8601 | 突合レコード生成時刻 |

---

## 突合ロジック（Time-Axis Join）

### 結合条件

```sql
SELECT
  agent.trace_id,
  agent.span_id,
  agent.agent_id,
  agent.session_id,
  agent.user_principal,
  fp.event_id AS fpolicy_event_id,
  fp.path AS file_path,
  fp.operation AS fpolicy_operation,
  DATEDIFF(ms, agent.timestamp_start, fp.timestamp) AS time_delta_ms,
  CASE
    WHEN agent.service_account = fp.user
     AND fp.timestamp BETWEEN agent.timestamp_start
                          AND DATEADD(s, 5, agent.timestamp_end)
     AND normalize_path(agent.file_path) = normalize_path(fp.path)
    THEN 'high'
    WHEN agent.service_account = fp.user
     AND fp.timestamp BETWEEN agent.timestamp_start
                          AND DATEADD(s, 5, agent.timestamp_end)
    THEN 'medium'
    WHEN fp.timestamp BETWEEN agent.timestamp_start
                          AND DATEADD(s, 5, agent.timestamp_end)
     AND normalize_path(agent.file_path) = normalize_path(fp.path)
    THEN 'low'
  END AS correlation_confidence
FROM agent_access_spans agent
JOIN fpolicy_events fp
  ON fp.timestamp BETWEEN agent.timestamp_start
                      AND DATEADD(s, 5, agent.timestamp_end)
WHERE correlation_confidence IS NOT NULL
```

### 信頼度スコアリング

| 信頼度 | 条件 | 用途 |
|--------|------|------|
| **HIGH** | サービスアカウント一致 + 時間窓内 + パス一致 | 監査レポート、コンプライアンス証跡 |
| **MEDIUM** | サービスアカウント一致 + 時間窓内（パス不一致/部分一致） | ディレクトリ一覧やメタデータ取得のケース |
| **LOW** | 時間窓 + パス一致のみ（アカウント不一致） | 要手動レビュー（共有アカウントの可能性） |

### 時間窓バッファ

```
span_start ─────────── span_end ──── +5s buffer
                │                          │
                ▼                          ▼
FPolicy events in this window are candidates for correlation
```

+5s バッファの理由:
- NFS/SMB の close 操作はスパン終了後に非同期で発生する場合がある
- ネットワークレイテンシと ONTAP 内部処理遅延を吸収

---

## サービスアカウント戦略

エージェントが FSx for ONTAP にアクセスする際のサービスアカウント設計。突合精度に直結する。

| 戦略 | 粒度 | 突合精度 | 運用負荷 | 推奨シナリオ |
|------|------|---------|---------|------------|
| エージェント種別ごと | `svc-agent-quality`, `svc-agent-cataloger` | 高 | 中 | 標準推奨 |
| セッションごと | `svc-agent-sess-{session_id}` | 最高 | 高 | 高セキュリティ環境 |
| 共通アカウント | `svc-agent-common` | 低 | 低 | PoC のみ |

**推奨**: エージェント種別ごとのサービスアカウント。Active Directory グループ `AG-FSx-ONTAP-Agents` に所属させ、FPolicy フィルタで明示的に監視対象にする。

---

## 統合クエリ例

### Q1: 特定セッションでアクセスされた全ファイルを列挙

```sql
-- "All files read during this agent session"
SELECT DISTINCT
  cr.file_path,
  cr.correlation_confidence,
  fp.operation AS fpolicy_operation,
  fp.timestamp AS access_time,
  agent.tool_name,
  agent.bytes_read
FROM correlation_records cr
JOIN agent_access_spans agent ON cr.trace_id = agent.trace_id
                             AND cr.span_id = agent.span_id
JOIN fpolicy_events fp ON cr.fpolicy_event_id = fp.event_id
WHERE agent.session_id = 'sess_2026061812345'
  AND cr.correlation_confidence IN ('high', 'medium')
ORDER BY fp.timestamp ASC
```

### Q2: 特定ファイルにアクセスした全エージェントセッションを列挙

```sql
-- "All agents and sessions that read this file"
SELECT
  agent.agent_id,
  agent.session_id,
  agent.user_principal AS triggered_by,
  agent.tool_name,
  agent.timestamp_start,
  cr.correlation_confidence
FROM correlation_records cr
JOIN agent_access_spans agent ON cr.trace_id = agent.trace_id
                             AND cr.span_id = agent.span_id
WHERE cr.file_path = '/vol1/shared/reports/Q2-2026-financial.xlsx'
  AND cr.correlation_confidence IN ('high', 'medium')
ORDER BY agent.timestamp_start DESC
```

### Q3: 権限外アクセスの検出

```sql
-- "Agent attempted access that resulted in FPolicy failure = unauthorized"
SELECT
  agent.agent_id,
  agent.session_id,
  agent.user_principal,
  fp.path,
  fp.timestamp,
  fp.result
FROM fpolicy_events fp
JOIN agent_access_spans agent
  ON fp.user = agent.service_account
  AND fp.timestamp BETWEEN agent.timestamp_start
                       AND DATEADD(s, 5, agent.timestamp_end)
WHERE fp.result = 'failure'
  AND fp.user LIKE '%svc-agent-%'
ORDER BY fp.timestamp DESC
```

### Q4: エージェント別アクセス頻度の時系列（異常検知向け）

```sql
-- "File access count trend per agent type"
SELECT
  agent.agent_id,
  DATE_TRUNC('hour', fp.timestamp) AS hour_bucket,
  COUNT(DISTINCT fp.path) AS unique_files_accessed,
  COUNT(*) AS total_operations,
  SUM(agent.bytes_read) AS total_bytes_read
FROM correlation_records cr
JOIN agent_access_spans agent ON cr.trace_id = agent.trace_id
JOIN fpolicy_events fp ON cr.fpolicy_event_id = fp.event_id
WHERE cr.correlation_confidence = 'high'
  AND fp.timestamp >= DATEADD(day, -7, CURRENT_TIMESTAMP)
GROUP BY agent.agent_id, DATE_TRUNC('hour', fp.timestamp)
ORDER BY hour_bucket DESC, total_operations DESC
```

### Q5: セッション単位のデータアクセス範囲サマリ

```sql
-- "Volume and path scope summary for each session"
SELECT
  agent.session_id,
  agent.agent_id,
  agent.user_principal,
  MIN(fp.timestamp) AS first_access,
  MAX(fp.timestamp) AS last_access,
  COUNT(DISTINCT fp.path) AS files_accessed,
  COUNT(DISTINCT agent.volume_name) AS volumes_touched,
  ARRAY_AGG(DISTINCT SPLIT_PART(fp.path, '/', 3)) AS directories
FROM correlation_records cr
JOIN agent_access_spans agent ON cr.trace_id = agent.trace_id
JOIN fpolicy_events fp ON cr.fpolicy_event_id = fp.event_id
WHERE cr.correlation_confidence IN ('high', 'medium')
GROUP BY agent.session_id, agent.agent_id, agent.user_principal
ORDER BY files_accessed DESC
```

---

## パス正規化

FPolicy パスとエージェントリクエストパスの表記揺れを吸収する。

| ケース | FPolicy 側 | エージェント側 | 正規化後 |
|--------|-----------|--------------|---------|
| SMB → POSIX | `\vol1\shared\reports\Q2.xlsx` | `/vol1/shared/reports/Q2.xlsx` | `/vol1/shared/reports/Q2.xlsx` |
| 大文字小文字 | `/Vol1/Shared/Reports/Q2.xlsx` | `/vol1/shared/reports/Q2.xlsx` | `/vol1/shared/reports/q2.xlsx`（SMB: case-insensitive） |
| 末尾スラッシュ | `/vol1/shared/reports/` | `/vol1/shared/reports` | `/vol1/shared/reports` |
| 共有名付き | `\\server\share\reports\Q2.xlsx` | `/vol1/shared/reports/Q2.xlsx` | SVM 共有マッピングテーブルで変換 |

```python
def normalize_path(path: str, protocol: str = "smb") -> str:
    """Normalize file path for correlation matching."""
    # Backslash → forward slash
    normalized = path.replace("\\", "/")
    # Remove trailing slash
    normalized = normalized.rstrip("/")
    # Case-insensitive for SMB
    if protocol == "smb":
        normalized = normalized.lower()
    return normalized
```

---

## 実装ノート

### 前提条件

| 項目 | 要件 |
|------|------|
| エージェント基盤 | Omnigent / AgentCore が OTel スパンを出力すること |
| FPolicy | FSx for ONTAP で FPolicy が有効化され、エージェント用サービスアカウントの操作を監視対象に含めること |
| 時刻同期 | エージェントホストと FSx for ONTAP が NTP で同期していること（突合精度に直結） |
| OTel Collector | ADOT または OTel Collector がスパンを収集し、分析レイヤーへ配信すること |
| 分析レイヤー | OpenSearch / Athena / CloudWatch Logs Insights のいずれかが利用可能であること |

### 実装フェーズ

| フェーズ | 内容 | 前提 |
|---------|------|------|
| Phase 1 | サービスアカウント設計 + FPolicy フィルタ設定 | FSx for ONTAP 稼働中 |
| Phase 2 | エージェント OTel スパン定義 + ADOT 配信 | エージェント基盤構築後 |
| Phase 3 | 突合ロジック実装（Lambda / Step Functions） | Phase 1 + 2 完了後 |
| Phase 4 | ダッシュボード + アラート + 異常検知 | Phase 3 完了後 |

### データフロー選択肢

| パターン | 突合タイミング | レイテンシ | 適用シナリオ |
|---------|-------------|-----------|------------|
| バッチ突合（Athena） | 定期（5分〜1時間） | 分〜時間 | 日次レポート、コンプライアンス |
| ストリーム突合（Kinesis + Lambda） | リアルタイム | 秒 | セキュリティアラート、異常検知 |
| ハイブリッド | リアルタイム検知 + バッチ集計 | 秒（検知）/ 分（集計） | 本番推奨 |

---

## セキュリティ考慮事項

| 観点 | 設計方針 |
|------|---------|
| 突合レコードのアクセス制御 | 監査担当者のみ閲覧可。エージェント自身は突合結果を参照できない |
| ログの改ざん防止 | S3 Object Lock (WORM) + CloudTrail Integrity |
| PII リダクション | file_path にユーザー名が含まれる場合、ダッシュボード表示時にマスキング |
| サービスアカウントの最小権限 | エージェントごとに必要最小限のボリューム/パスのみ許可 |
| 突合結果の保持期間 | コンプライアンス要件に応じて設定（例: 7年） |

---

### Databricks Platform Security 新機能への対応検討（2026-06 時点）

> **Evidence tier**
>
> Public（[DAIS 2026 発表ブログ](https://www.databricks.com/blog/whats-new-databricks-platform-security-and-compliance-data-ai-summit-2026)）
> **ステータス**: 未検証 — 設計への影響を確認中

DAIS 2026（2026-06-17）で発表された Databricks Platform Security の新機能のうち、本パターンのエージェント監査設計に影響する可能性がある項目を以下に整理する。

| 機能 | 概要 | 本パターンへの影響 |
|------|------|------------------|
| Private Network Gateway / Lakebase Private Link | サーバーレス・AI ワークロードから Lakebase へのプライベート接続 | エージェントが Lakebase にアクセスする場合、Private Link 経由のトラフィックが CloudTrail / VPC Flow Logs で可視化されるか確認が必要。突合ロジックのデータソースに VPC Flow Logs を追加する可能性がある |
| Automatic Identity Management (AIM) for Entra ID — GA (AWS/GCP) | Entra ID ↔ Databricks 間のユーザー・グループ同期の自動化 | エージェント用サービスアカウントが属するグループメンバーシップが Databricks ワークスペースへ自動同期される可能性がある。本パターンの「サービスアカウント戦略」セクションで定義した AD グループ設計・FPolicy フィルタとの整合性を確認する必要がある |
| Context-Based Ingress Policies | コンテキスト（デバイス、ネットワーク、ID 属性）に基づくアクセス制御 | エージェント実行環境のコンテキスト情報が Ingress Policy 評価対象になる場合、監査ログに拒否理由が記録される可能性がある。突合結果に Databricks 側のアクセス拒否イベントを含めることを検討 |

**リージョン制約**:

> ⚠️ Lakebase は 2026-06-18 時点で **ap-northeast-1（東京）では利用不可**。本プロジェクトの FSx for ONTAP 環境は東京リージョンに配置されているため、Lakebase Private Link の検証は対応リージョンでの展開後に実施する。東京リージョンでの GA 時期は未定。

**今後の確認事項**:

- Lakebase が ap-northeast-1 で利用可能になった時点で、Private Link 経由のエージェントアクセスが Databricks 側の Unity Catalog 監査ログにどのように記録されるか（`service_account` フィールドとの対応）
- AIM によりグループメンバーシップが自動同期される場合、エージェント用サービスプリンシパルに対する ONTAP SVM の AD グループ解決結果が Databricks 側と一致するかの検証方法
- AIM で自動生成・同期されるサービスプリンシパルの命名パターンと、本パターンの FPolicy フィルタ設定（`%svc-agent-%`）との互換性
- Context-Based Ingress Policies の拒否イベントを OTel スパンまたは Correlation Record に取り込む方法

> **注記**
>
> 上記は DAIS 2026 時点の公開情報に基づく検討事項であり、本パターンの既存設計（OTel × FPolicy 突合）を変更するものではない。Lakebase が東京リージョンで利用可能になり、連携が具体化した段階で、突合ロジックの拡張を検討する。

---

## 関連ドキュメント

- [FPolicy サーバー設計](../../shared/templates/fpolicy-server-fargate.yaml)
- [パイプライン SLO](pipeline-slo.md)
- [データ分類ガイド](data-classification.md)
- [セキュリティベストプラクティス](security-best-practices.md)
- [fsxn-lakehouse-integrations: クロスリポジトリ連携戦略](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/docs/ja/cross-repo-integration-strategy.md)
- [fsxn-lakehouse-integrations: Omnigent 評価（可観測性設計セクション）](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/docs/ja/omnigent-multi-agent-evaluation.md)
