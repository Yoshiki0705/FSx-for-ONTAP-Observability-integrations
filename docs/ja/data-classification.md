# FSx for ONTAP 監査ログのデータ分類ガイド

🌐 **日本語**（このページ） | [English](../en/data-classification.md)

## 概要

FSx for ONTAP の監査ログには、個人識別情報（PII）や機密ビジネスデータに分類される可能性のある情報が含まれます。本ガイドでは、注意が必要なフィールドを特定し、推奨される取り扱いパターンを提供します。

> **重要**
>
> 本ガイドはパイプライン設計判断のための技術的分類を提供するものです。組織のデータ保護責任者やコンプライアンスチームによる正式なデータ分類評価に代わるものではありません。

## フィールド分類マトリクス

### 監査ログフィールド

| フィールド | 値の例 | 分類 | PII リスク | 推奨される取り扱い |
|-----------|--------|------|-----------|-------------------|
| `UserName` | `admin@corp.local` | **PII** | 高 | 非本番環境ではハッシュ化または仮名化 |
| `ObjectName` / `path` | `/vol/hr/employee-records/salary.xlsx` | **機密** | 中 | パスがビジネスコンテキストを露出する可能性 |
| `ClientIP` / `source.ip` | `10.0.x.x` | **内部** | 低 | ほとんどのコンテキストで許容 |
| `EventID` | `4663` | **非機密** | なし | 対応不要 |
| `Operation` | `ReadData` | **非機密** | なし | 対応不要 |
| `Result` | `Success` / `Failure` | **非機密** | なし | 対応不要 |
| `SVMName` | `svm-prod-01` | **内部** | なし | インフラ命名規則を露出する可能性 |
| `Timestamp` | `2026-01-15T12:00:00Z` | **非機密** | なし | 対応不要 |

### EMS イベントフィールド

| フィールド | 値の例 | 分類 | PII リスク |
|-----------|--------|------|-----------|
| `event_name` | `arw.volume.state` | 非機密 | なし |
| `severity` | `alert` | 非機密 | なし |
| `message` | ONTAP フリーテキストメッセージ | **要確認** | 低〜中 |

### FPolicy イベントフィールド

| フィールド | 値の例 | 分類 | PII リスク |
|-----------|--------|------|-----------|
| `user` | `DOMAIN\username` | **PII** | 高 |
| `path` | `/vol/data/file.docx` | **機密** | 中 |
| `operation` | `create` | 非機密 | なし |
| `client_ip` | `10.0.x.x` | 内部 | 低 |

## データ取り扱いパターン

### パターン 1: フルフィデリティ（デフォルト）

全フィールドをそのまま配信します。以下の場合に適切です：
- Observability プラットフォームにアクセス制御（RBAC）がある
- データレジデンシー要件を満たしている（同一リージョン）
- セキュリティチームがデータフローを承認済み
- 保持ポリシーが設定済み

### パターン 2: 仮名化

配信前に PII フィールドをハッシュ化します。以下の場合に使用します：
- 個人を特定せずに運用可視性が必要
- 越境データ転送の懸念がある
- 広範なアクセス権を持つ共有ダッシュボード

```python
import hashlib

def pseudonymize_user(username: str, salt: str) -> str:
    """One-way hash for user identification without revealing identity."""
    return hashlib.sha256(f"{salt}:{username}".encode()).hexdigest()[:16]

# Result: "admin@corp.local" -> "a3f2b1c9d4e5f678"
```

### パターン 3: 秘匿（リダクション）

機密フィールドを完全に削除します。以下の場合に使用します：
- 厳格なデータ最小化要件
- パブリックまたは共有の Observability 環境
- コンプライアンスがフィールド削除を義務付け

OTel Collector（Part 5）での実装：
```yaml
processors:
  attributes:
    actions:
      - key: user.name
        action: delete
      - key: fsxn.path
        action: hash
```

### パターン 4: パス汎化

運用価値を維持しつつパスの詳細度を下げます：

```python
def generalize_path(path: str, depth: int = 3) -> str:
    """Keep only top N path segments."""
    parts = path.split("/")
    if len(parts) > depth + 1:
        return "/".join(parts[:depth + 1]) + "/..."
    return path

# "/vol/hr/employee-records/salary.xlsx" -> "/vol/hr/employee-records/..."
```

## 規制上の考慮事項

| 規制 | 主要要件 | パイプラインへの影響 |
|------|---------|-------------------|
| **APPI**（日本） | 個人情報の取り扱い、越境移転 | UserName を個人情報として評価；JP リージョンベンダーを推奨 |
| **GDPR**（EU） | データ最小化、消去権 | 仮名化を検討；保持期間を文書化 |
| **FISC**（日本金融） | データレジデンシー、アクセス制御 | ベンダーデータが承認済みリージョンに留まることを確認 |
| **ISMAP**（日本政府クラウド） | セキュリティ管理策、監査証跡 | アクセス制御付きフルフィデリティ |
| **HIPAA**（米国医療） | PHI 保護 | ファイルパスに患者識別子が含まれる場合は秘匿 |

> **ガバナンス上の注意**
>
> この表は技術的な認識を提供するものであり、法的ガイダンスではありません。拘束力のある規制解釈についてはコンプライアンスチームに相談してください。

## 実装推奨事項

### PoC / 検証向け（Level 1-2）

- サンプル/合成データで**パターン 1（フルフィデリティ）**を使用
- テストデータを非本番として明確にラベル付け
- Observability プラットフォームへのアクセスを PoC チームに制限

### 本番向け（Level 3）

- 共有ダッシュボードの UserName に**パターン 2（仮名化）**を実装
- ベンダー側の RBAC を設定（生ログを閲覧できる人を制限）
- コンプライアンス要件に合致する保持ポリシーを設定
- データ処理台帳にデータフローを文書化

### エンタープライズ / 規制対象向け（Level 4）

- OTel Collector プロセッサで**パターン 3（秘匿）**を実装
- クロスチームダッシュボードに**パターン 4（パス汎化）**を使用
- セキュリティ調査用に制限付きインデックス/データセットでフルフィデリティログを維持
- Observability データへのアクセス監査証跡を実装

## OTel Collector 秘匿設定

OTel Collector パス（Part 5）では、秘匿プロセッサを追加します：

```yaml
processors:
  # Pseudonymize user fields
  transform:
    log_statements:
      - context: log
        statements:
          - set(attributes["user.name.hash"], SHA256(attributes["user.name"]))

  # Remove raw PII after hashing
  attributes/redact:
    actions:
      - key: user.name
        action: delete
      - key: source.ip
        action: delete

  # Generalize file paths
  transform/paths:
    log_statements:
      - context: log
        statements:
          - replace_pattern(attributes["fsxn.path"], "^(/[^/]+/[^/]+/[^/]+)/.*", "$$1/...")
```

## ベンダー別データ制御機能

| ベンダー | RBAC | フィールドレベルマスキング | 保持期間制御 | データレジデンシー |
|---------|------|------------------------|-------------|-----------------|
| Datadog | あり | あり（Sensitive Data Scanner） | あり | US, EU |
| Grafana Cloud | あり | なし（Collector で対応） | あり | US, EU, AU |
| Splunk | あり | あり（field masking） | あり | セルフホスト可 |
| Elastic | あり | あり（field-level security） | あり（ILM） | セルフホスト可 |
| New Relic | あり | あり（obfuscation rules） | あり | US, EU |
| Honeycomb | あり | なし（Collector で対応） | あり | US のみ |
| Dynatrace | あり | あり（data masking） | あり | リージョン固有 |
| Sumo Logic | あり | あり（field extraction rules） | あり | JP, US, EU, AU |

## 関連ドキュメント

- [ガバナンス & コンプライアンス](governance-and-compliance.md)
- [セキュリティレビューチェックリスト](security-review-checklist.md)
- [データレジデンシーマトリクス](data-residency.md)
- [Pipeline SLO](pipeline-slo.md)
- [OTel Collector 統合](../../integrations/otel-collector/README.md) — プロセッサによる秘匿
