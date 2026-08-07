# ログアーカイブ設定（Datadog → S3 リハイドレーション）

🌐 **日本語** (このページ) | [English](../en/log-archive-setup.md)

## 概要

Datadog のログ保持期間は通常 15 日または 30 日ですが、監査ログの保持要件は年単位で
求められることが多いです。このスタックは、Datadog が FSx for ONTAP の監査ログを
自社アカウントにアーカイブし、後のコンプライアンス調査でリハイドレーション（再取り込み）
できるようにする S3 バケットと IAM ロールを作成します。

```
Lambda シッパー ──→ Datadog Logs（ホット、15-30 日、検索可能）
                        │
                        └─ Log Archive ──→ 自社 S3 バケット ──→ Glacier
                                              （必要時にリハイドレーション）
```

重要なのはこのリハイドレーションです。アーカイブされたログは、Datadog に該当期間を
インデックスへ復元するよう依頼するまで検索できません。調査計画では分単位ではなく
時間単位で見積もってください。

## S3 を直接クエリしないのはなぜか

直接クエリすることもできます（オブジェクトは圧縮 JSON です）が、本統合で構築した Facet、
保存済みビュー、検知ルールが使えなくなります。リハイドレーションは、調査でも
ライブ監視と同じクエリを使えるようにするための仕組みです。S3 直接アクセスは、
インシデントトリアージではなく大規模分析（Athena）が必要な場合に適した手段です。

## 前提条件

- 監査ログスタックがデプロイ済みでログを配送している
  （[セットアップガイド](setup-guide.md)）
- API キーに加えて Datadog **Application キー**（アーカイブ設定は管理者操作）
- Datadog の **AWS External ID** — Datadog コンソール →
  **Integrations** → **Amazon Web Services** → 該当 AWS アカウントのタイル
- 必要な保持期間（日数）の把握

## Step 1: アーカイブスタックのデプロイ

```bash
aws cloudformation deploy \
  --template-file integrations/datadog/template-log-archive.yaml \
  --stack-name fsxn-datadog-log-archive \
  --parameter-overrides \
    DatadogExternalId=<external-id-from-datadog-console> \
    RetentionDays=30 \
    GlacierRetentionDays=2555 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1
```

Datadog が assume する名前付き IAM ロールを作成するため `CAPABILITY_NAMED_IAM` が必須です。

### パラメータリファレンス

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `DatadogExternalId` | — | 必須、`NoEcho`。Datadog AWS 統合ページから取得。他の Datadog 顧客が自社ロールを assume することを防ぐ唯一の要素 |
| `ArchiveBucketName` | `''` | 空の場合 `fsxn-datadog-archive-<account>-<region>` |
| `DatadogAwsAccountId` | `464622532012` | US1/AP1 は `464622532012`、EU1 は `417141415827` |
| `RetentionDays` | `30` | Glacier へ移行するまでの S3 Standard 保持日数 |
| `GlacierRetentionDays` | `2555` | 合計保持日数。2555 ≈ 7 年 |
| `KmsKeyArn` | `''` | 空の場合 SSE-S3（AES256）。SSE-KMS を使う場合は CMK ARN を設定 |

### 保持期間の選び方

| 期間 | 日数 | 一般的な根拠 |
|------|------|------------|
| 1 年 | 365 | 社内ポリシーのベースライン |
| 3 年 | 1095 | 契約上の最小要件として多い |
| 7 年 | 2555 | 金融業界の記録保持（デフォルト） |
| 10 年 | 3650 | 一部の公共部門要件 |

`GlacierRetentionDays` はオブジェクトが失効する**合計**経過日数で、
`RetentionDays` の後に追加される期間ではありません。

> **コストに関する補足**: Glacier への移行はストレージコストを下げますが、
> 取り出しコストと遅延が加わります。128 KB 未満のオブジェクトは移行する価値がありません。
> S3 は Glacier 階層でオブジェクトごとに最小課金サイズを適用するため、小さな監査ファイルが
> 大量にあるとアーカイブした方が Standard より高くつく場合があります。
> Datadog は十分に大きなバッチオブジェクトを書き込むため通常は問題ありませんが、
> 削減を前提にする前に自社のデータ量で検証してください。

### スタックが作成するリソース

| リソース | 備考 |
|----------|------|
| S3 バケット | `DeletionPolicy: Retain` — 意図的にスタック削除後も残る |
| バケットポリシー | アーカイブロールのみ許可、非 TLS リクエストを拒否 |
| ライフサイクルルール | `RetentionDays` で Standard → Glacier、`GlacierRetentionDays` で失効 |
| IAM ロール `<stack>-archive-role` | Datadog が assume、External ID でゲート |

> **セキュリティに関する補足**: 信頼関係は**ロールの信頼ポリシー**の `sts:ExternalId` で
> 強制され、バケットポリシーでは強制されません。`sts:ExternalId` は `sts:AssumeRole` の
> リクエストコンテキストにのみ存在し S3 リクエストには現れないため、
> バケットポリシーでこれを条件にしても一致しません。
> ここではバケットポリシーでアーカイブロールを明示的に指定しています。

## Step 2: Datadog 側でアーカイブを設定

入力する値をスタック出力から取得します。

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-datadog-log-archive \
  --region ap-northeast-1 \
  --query "Stacks[0].Outputs[?OutputKey=='DatadogArchiveConfig'].OutputValue" \
  --output text
```

続いて:

1. Datadog コンソール → **Logs** → **Configuration** → **Archives**
2. **Add a new archive** をクリック
3. 入力:
   - **Name**: `fsxn-audit-logs`
   - **Filter**: `source:fsxn source:fsxn-ems source:fsxn-fpolicy`
     （アーカイブしたい対象すべてに一致するクエリ）
   - **Archive type**: AWS S3
   - **AWS Account**: バケットを含むアカウント
   - **Bucket**: スタック出力の値
   - **Path**: `fsxn-audit-logs`
   - **Role**: スタック出力のロール名
4. 保存

Datadog は設定を即座に検証し、ロールを assume できない場合はアーカイブにエラーを表示します。

### 順序が重要

アーカイブは**上から下**へ評価され、ログは**最初に**一致したアーカイブにのみ格納されます。
既にキャッチオールのアーカイブがある場合はこれをその上に配置してください。
そうしないと FSx for ONTAP のログはキャッチオール側とその保持ルールに入ってしまいます。

## Step 3: 検証

アーカイブのアップロードはバッチ処理のため即座には現れません。15 分待ってから確認します。

```bash
aws s3 ls s3://fsxn-datadog-archive-123456789012-ap-northeast-1/fsxn-audit-logs/ \
  --recursive --human-readable | head
```

`dt=<date>/hour=<hour>/` 形式のプレフィックス配下にオブジェクトが現れます。
Datadog コンソールのアーカイブタイルには最後の成功アップロード時刻が表示されます。
赤い状態でアップロードが無い場合、ほぼ確実に External ID かロール名の不一致です。

## Step 4: 調査のためのリハイドレーション

1. Datadog コンソール → **Logs** → **Historical Views**
2. **New Historical View** をクリック
3. アーカイブ、期間、クエリを選択
4. リハイドレーションを開始して待機 — 期間と Glacier 階層かどうかにより
   数分から数時間かかります

リハイドレーションされたログはビューの有効期間中、ライブログと同様にインデックスされ
課金されます。調査に本当に必要な期間に絞ってください。

> **コストに関する補足**: リハイドレーションはスキャンおよびインデックスしたデータ量で
> 課金されます。7 年分のアーカイブに対する広範囲な指定は高額になります。
> まず時間で絞り、次にクエリで絞ってください。

## トラブルシューティング

### Datadog でアーカイブがエラー表示になる

1. **External ID の不一致** — 最も多い原因です。Datadog AWS 統合ページから
   再度コピーしてスタックを更新してください。
2. **ロール名の不一致** — ロールはスタックスコープ（`<stack-name>-archive-role`）に
   なりました。旧版テンプレートが作成していた固定名 `DatadogLogArchiveRole` では
   ありません。スタック出力の値を使ってください。
3. **Datadog アカウント ID の誤り** — EU1 はデフォルトではなく `417141415827` です。

信頼関係を直接確認する:

```bash
aws iam get-role --role-name fsxn-datadog-log-archive-archive-role \
  --query 'Role.AssumeRolePolicyDocument'
```

### オブジェクトは書き込まれているがリハイドレーションで何も見つからない

アーカイブの**フィルタ**が対象ログに一致していたか確認してください。
ログは最初に一致したアーカイブに格納されるため、リスト上位にあるより広範なアーカイブが
先に取り込んでいる可能性があります。

### バケットの削除が失敗する

バケットはコンプライアンスデータを保持するため意図的に `DeletionPolicy: Retain` です。
スタックを削除してもバケットは残ります。意図的に削除する場合:

```bash
# 不可逆です。保持義務を先に確認してください。
aws s3 rm s3://<bucket-name> --recursive
aws s3api delete-bucket --bucket <bucket-name> --region ap-northeast-1
```

## クリーンアップ

```bash
bash integrations/datadog/scripts/cleanup.sh --delete-log-archive
```

先に Datadog コンソールでアーカイブ設定を削除してください。削除しないと Datadog が
存在しないロールへのアップロードを試み続け、アーカイブがエラー状態になります。

## 関連ドキュメント

- [セットアップガイド](setup-guide.md) — 監査ログパイプライン
- [EMS / FPolicy セットアップ](ems-fpolicy-setup.md) — アーカイブ対象の追加ソース
- [本番チェックリスト](production-checklist.md) — 保持期間の確認項目
- [データ分類](../../../../docs/ja/data-classification.md) — これらのログに含まれる内容
- [コンプライアンス証跡パック](../../../../docs/ja/compliance-evidence-pack.md) — ISMAP/FISC/SOC2 テンプレート
