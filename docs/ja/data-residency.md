# データレジデンシーマトリクス

🌐 **日本語**（このページ） | [English](../en/data-residency.md)

> 本ドキュメントはデータフローの送信先に関する技術的ガイダンスを提供するものです。法的、コンプライアンス、または規制上のアドバイスを構成するものではありません。越境データ移転要件（GDPR、APPI、PDPA 等）に関する正式な判断は、法務・コンプライアンスチームにご相談ください。

## 送信されるデータ

FSx for ONTAP 監査ログには以下のデータカテゴリが含まれる可能性があります:

| データカテゴリ | 例 | 機密レベル |
|--------------|---------|-------------------|
| ユーザー ID | `admin@corp.local`, `DOMAIN\username` | PII（管轄地域に依存） |
| ファイルパス | `/vol/hr/salary-2026.xlsx`, `/vol/legal/contract.pdf` | 業務機密（パス名がコンテンツの性質を示す場合あり） |
| クライアント IP アドレス | 内部 IP（RFC 1918） | 内部ネットワークトポロジ |
| タイムスタンプ | ファイルアクセス時刻 | 行動メタデータ |
| 操作タイプ | ReadData, WriteData, Delete | アクティビティメタデータ |
| SVM 名 | `svm-prod-finance` | インフラストラクチャメタデータ |

## ベンダーのデータレジデンシーオプション

| ベンダー | 利用可能リージョン | セルフホスト | データ主権に関する備考 |
|--------|------------------|-------------------|----------------------|
| **Datadog** | US1 (Virginia), US3, US5, EU1 (Frankfurt), AP1 (Tokyo), AP2, US1-FED (GovCloud) | ❌ | AP1 (Tokyo) で日本国内データ保持が可能 |
| **New Relic** | US (Oregon), EU (Frankfurt), JP (Tokyo — 2026年7月予定) | ❌ | JP リージョンは2026年7月開設予定。それまでは US または EU のみ |
| **Grafana Cloud** | US（複数）, EU（複数）, AP (Sydney, Singapore); Tokyo は Dedicated tier のみ | ✅（Grafana OSS + Loki） | セルフホストならデータは VPC 内に保持。Free/Pro tier は US/EU のみ |
| **Splunk** | Splunk Cloud: US, EU, AU, カスタム | ✅（Splunk Enterprise） | セルフマネージドなら自社インフラ内に保持 |
| **Elastic** | Elastic Cloud: US, EU, AP (Tokyo, Sydney, Singapore) | ✅（セルフホスト） | セルフホスト = 完全なデータ主権 |
| **Dynatrace** | SaaS: US, EU, AP (Sydney, Singapore) | ✅（Managed / ActiveGate） | Managed デプロイ = 自社インフラ |
| **Sumo Logic** | US, EU (Dublin, Frankfurt), AU (Sydney), JP (Tokyo) | ❌ | JP デプロイ利用可能 |
| **Honeycomb** | US のみ | ❌ | 現時点で US 以外のオプションなし |
| **OTel Collector** | N/A（セルフホスト） | ✅（常時） | バックエンドにエクスポートするまでデータは VPC 内に保持 |

## 判断フレームワーク

### ステップ 1: データの分類

- 監査ログに PII（ユーザー名、メールアドレス）が含まれるか？
- ファイルパスは業務機密とみなされるか？
- 組織にデータローカライゼーション要件があるか？

### ステップ 2: 規制要件の特定

| 規制 | 主要要件 | ベンダー選定への影響 |
|-----------|----------------|---------------------------|
| GDPR（EU） | データは EU 内に保持、または適切な移転メカニズムが必要 | EU リージョンまたはセルフホストを選択 |
| APPI（日本） | 越境移転には同意または同等の保護が必要 | JP/AP リージョンのベンダーを優先 |
| PDPA（シンガポール） | 移転には同等の保護が必要 | AP リージョンを選択 |
| HIPAA（US） | PHI には BAA が必要 | ベンダーの BAA 対応を確認 |
| SOX / PCI DSS | 監査証跡の完全性 | 改ざん不可ログを持つ任意のベンダー |

### ステップ 3: ベンダー + リージョンの選択

```
データローカライゼーションが必要な場合:
  → セルフホスト（Elastic, Grafana, Splunk, Dynatrace Managed）
  → または現地リージョンを持つベンダー（Datadog AP1, Sumo Logic JP）

厳格なローカライゼーションが不要な場合:
  → 許容可能なセキュリティ体制を持つ任意のベンダー
  → レイテンシのため FSx for ONTAP に最も近いリージョンを優先
```

### ステップ 4: 決定の文書化

PoC 計画に記録:
- [ ] データ分類完了
- [ ] 規制要件特定済み
- [ ] ベンダーリージョン選定（根拠付き）
- [ ] 越境データ移転メカニズム文書化（該当する場合）
- [ ] コンプライアンスチームの承認取得

## PII 秘匿化オプション

ログを外部に送信する必要があるが PII を秘匿化する必要がある場合:

| アプローチ | 方法 | 複雑度 |
|----------|-----|-----------|
| OTel Collector `transform` プロセッサ | ユーザー名/パスフィールドの正規表現置換 | 中 |
| Lambda レベルの秘匿化 | 送信前にハンドラーでフィールドをマスク | 低 |
| Grafana Alloy パイプライン | 組み込みのリラベリングとフィールド削除 | 中 |
| PII フィールドを送信しない | ペイロードからユーザー/パスを完全に除去 | 低（ただし価値が低下） |

OTel Collector での秘匿化例:
```yaml
processors:
  transform:
    log_statements:
      - context: log
        statements:
          - replace_pattern(body, "UserName\":\"[^\"]+\"", "UserName\":\"[REDACTED]\"")
```

## 関連ドキュメント

- [セキュリティベストプラクティス](security-best-practices.md)
- [ガバナンスとコンプライアンス](governance-and-compliance.md)
- [ベンダー比較](vendor-comparison.md)
