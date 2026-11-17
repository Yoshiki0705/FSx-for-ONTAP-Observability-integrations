# 監査ログ・クォータ設定 GUI ラッパー提案

## 背景

NetApp Console の Workload Factory UI では、以下の操作が GUI で提供されていない（2026年5月検証時点）:

- 監査ログの有効化・設定
- Qtree の作成・管理
- クォータルールの設定・初期化
- EMS Webhook の設定

これらは ONTAP REST API で実行可能なため、本プロジェクトの管理コンソール（Appsmith/ToolJet）に GUI ラッパーを追加することで、運用部門が GUI で操作できるようになる。

## 提案する機能

### 1. 監査ログ管理ページ

```
ONTAP REST API: POST /api/protocols/audit
```

| GUI 要素 | 対応 API フィールド |
|---------|-------------------|
| SVM 選択ドロップダウン | `svm.name` |
| 有効/無効トグル | `enabled` |
| ログ保存先パス | `log_path` |
| フォーマット選択 (EVTX/JSON) | `log.format` |
| ローテーションサイズ | `log.rotation.size` |
| 保存ボタン | POST/PATCH |

### 2. Qtree 管理ページ

```
ONTAP REST API: POST /api/storage/qtrees
```

| GUI 要素 | 対応 API フィールド |
|---------|-------------------|
| ボリューム選択 | `volume.name` |
| Qtree 名入力 | `name` |
| セキュリティスタイル選択 (NTFS/UNIX/Mixed) | `security_style` |
| 作成ボタン | POST |
| 一覧テーブル | GET /api/storage/qtrees |

### 3. クォータ管理ページ

```
ONTAP REST API: POST /api/storage/quota/rules
```

| GUI 要素 | 対応 API フィールド |
|---------|-------------------|
| ボリューム選択 | `volume.name` |
| Qtree 選択 | `qtree.name` |
| タイプ選択 (tree/user/group) | `type` |
| ハードリミット入力 | `space.hard_limit` |
| ソフトリミット入力 | `space.soft_limit` |
| 初期化ボタン | PATCH /api/storage/volumes/{uuid} `quota.enabled=true` |
| 使用状況テーブル | GET /api/storage/quota/reports |

### 4. EMS Webhook 管理ページ

```
ONTAP REST API: POST /api/support/ems/destinations
```

| GUI 要素 | 対応 API フィールド |
|---------|-------------------|
| 通知先名入力 | `name` |
| Webhook URL 入力 | `destination` |
| フィルタ選択 | `filters` |
| テスト送信ボタン | — (curl でテスト) |
| 通知先一覧 | GET /api/support/ems/destinations |

## 実装優先度

| 機能 | 優先度 | 理由 |
|------|--------|------|
| クォータ管理 | ★★★ | 利用者からの問い合わせ最多 |
| 監査ログ管理 | ★★★ | セキュリティ要件で必須 |
| Qtree 管理 | ★★☆ | クォータの前提条件 |
| EMS Webhook | ★☆☆ | 初期設定のみ（日常操作不要） |

## 技術的考慮事項

- ONTAP REST API は Basic Auth (fsxadmin) で認証
- Secrets Manager から fsxadmin パスワードを取得
- VPC 内からのアクセスが必要（管理エンドポイントはプライベート IP）
- Appsmith/ToolJet の REST API データソースとして設定可能
- TLS 証明書検証を無効化する必要あり（自己署名証明書）

## 既存の management-console との統合

本プロジェクトの `management-console/` は既に以下を提供:
- Layer 1: Harvest + ADOT → AMP → AMG（メトリクス）
- Layer 2: Appsmith/ToolJet on ECS Fargate（管理 UI）

上記の監査ログ・クォータ GUI は **Layer 2 の Appsmith/ToolJet ワークフロー** として追加する形が最も自然。

## 次のステップ

1. Appsmith のローカル開発環境で REST API データソースを設定
2. 監査ログ管理ページのプロトタイプを作成
3. クォータ管理ページのプロトタイプを作成
4. E2E テスト（実際の FSx for ONTAP 環境で動作確認）
5. CloudFormation テンプレートに統合
