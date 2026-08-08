# FSx for ONTAP S3 Access Points 仕様書

🌐 **日本語**（このページ） | [English](../en/s3ap-fsxn-specification.md)

## 概要

本プロジェクトで使用する FSx for ONTAP S3 Access Points の仕様、制約、トラブルシューティング知見をまとめたドキュメントです。

---

## 1. ネットワーク制約（最重要）

### 根本原因

**Internet-origin の FSx for ONTAP S3 Access Points は、VPC 内から Gateway Endpoint のみではアクセスできませんでした（本環境での観察結果）。**

AWS [ドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/configuring-network-access-for-s3-access-points.html)によると、VPC-origin の S3 AP は Gateway Endpoint で動作します。Internet-origin AP の場合は NAT Gateway または VPC 外 Lambda が必要です。

### Lambda 配置パターン

| Lambda 配置 | S3 AP アクセス | ONTAP REST API | 推奨用途 |
|------------|-------------|---------------|---------|
| VPC 外 | ✅ 成功 | ❌ 不可 | S3 AP 読み取り専用（本プロジェクトの主要パターン） |
| VPC 内 + S3 Gateway EP (Internet-origin AP) | ⚠️ **タイムアウト** | ✅ 成功 | NAT Gateway または VPC-origin AP が必要 |
| VPC 内 + NAT Gateway | ✅ 成功 | ✅ 成功 | 本番環境推奨 |
| VPC 内 + VPC-origin AP + Gateway EP | ✅ AWS ドキュメント記載 | ✅ 成功 | VPC-origin AP の作成が必要 |

### 本プロジェクトでの設計判断

```
[推奨構成]
Lambda (VPC 外) → S3 AP (インターネット経由) → ログ読み取り → Vendor API 送信

[本番構成]
Lambda (VPC 内 + NAT GW) → S3 AP (NAT 経由) → ログ読み取り → Vendor API 送信
```

**重要**: `shared/templates/prerequisites.yaml` の S3 Access Point は `NetworkOrigin: Internet` で作成されます。VPC 制限をかける場合は NAT Gateway が必須です。

---

## 2. ARN 形式と IAM ポリシー

### 正しい ARN 形式

```
arn:aws:s3:{region}:{account-id}:accesspoint/{access-point-name}
```

### IAM ポリシーのリソース指定

```yaml
# Object operations (GetObject, PutObject)
Resource: !Sub 'arn:aws:s3:${AWS::Region}:${AWS::AccountId}:accesspoint/${AccessPointName}/object/*'

# Bucket-level operations (ListBucket)
Resource: !Sub 'arn:aws:s3:${AWS::Region}:${AWS::AccountId}:accesspoint/${AccessPointName}'
```

### よくある間違い

```yaml
# ❌ Wrong: standard S3 bucket ARN
Resource: arn:aws:s3:::my-bucket/*

# ❌ Wrong: missing /object/* suffix
Resource: arn:aws:s3:ap-northeast-1:123456789012:accesspoint/my-ap/*

# ✅ Correct: with /object/* suffix
Resource: arn:aws:s3:ap-northeast-1:123456789012:accesspoint/my-ap/object/*
```

---

## 3. S3 AP リソースポリシー

IAM ポリシーに加えて、S3 Access Point 自体にもリソースポリシーが必要です。

```bash
aws s3control put-access-point-policy \
  --account-id 123456789012 \
  --name fsxn-audit-ap \
  --policy '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "AllowLambdaRead",
      "Effect": "Allow",
      "Principal": {"AWS": "arn:aws:iam::123456789012:role/fsxn-datadog-lambda-role"},
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
        "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap/object/*"
      ]
    }]
  }'
```

---

## 4. boto3 での使用方法

```python
import boto3

s3_client = boto3.client("s3")

# Use S3 AP ARN as the Bucket parameter
response = s3_client.get_object(
    Bucket="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
    Key="audit/svm-prod-01/2026/01/15/audit_log.json"
)

# ListObjectsV2 works the same way
response = s3_client.list_objects_v2(
    Bucket="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
    Prefix="audit/svm-prod-01/"
)
```

---

## 5. 非対応 S3 API / 機能

| 機能 | 対応状況 | 影響 | 回避策 |
|------|---------|------|--------|
| GetBucketNotificationConfiguration | ❌ | イベント駆動不可 | 通常 S3 バケットの EventBridge 通知を使用 |
| S3 Event Notifications | ❌ | Lambda 直接トリガー不可 | EventBridge Rule 経由 |
| Object Lifecycle | ❌ | 自動削除/移行不可 | カスタム Lambda で定期削除 |
| Object Versioning | ❌ | バージョン管理不可 | DynamoDB でバージョン追跡 |
| Presigned URL | ❌ | 時限共有不可 | 通常 S3 にコピー + Presign |
| SSE-KMS | ❌ | カスタム KMS 不可 | FSx ボリュームレベル KMS で暗号化 |
| PutObject > 5GB | ❌ | 大ファイル書き込み不可 | Multipart Upload (5GB 以内) |

---

## 6. トラブルシューティング

### 症状: Lambda が S3 AP からの読み取りでタイムアウト

**原因**: Lambda が VPC 内に配置されており、S3 Gateway VPC Endpoint のみ設定されている

**解決策**:
1. Lambda を VPC 外に配置する（推奨）
2. または NAT Gateway を追加する

**確認コマンド**:
```bash
# Check Lambda VPC configuration
aws lambda get-function-configuration \
  --function-name fsxn-datadog-integration-shipper \
  --query 'VpcConfig'

# Check VPC Endpoints
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=vpc-xxx" \
  --query 'VpcEndpoints[*].{Service:ServiceName,Type:VpcEndpointType}'
```

### 症状: AccessDenied エラー

**確認ポイント** (3 層の認可):
1. **IAM ポリシー**: Lambda ロールに `s3:GetObject` + 正しい ARN 形式
2. **S3 AP リソースポリシー**: Lambda ロールに対する許可
3. **FSx ファイルシステム権限**: S3 AP に関連付けられたユーザーの UNIX/NTFS 権限

```bash
# Check IAM policy
aws iam get-role-policy --role-name <lambda-role> --policy-name S3AccessPointRead

# Check S3 AP policy
aws s3control get-access-point-policy --account-id <account> --name <ap-name>
```

### 症状: ListObjectsV2 が空の結果を返す

**原因候補**:
- Prefix が間違っている（FSx for ONTAP のパス構造は `/` 始まりではない）
- S3 AP の network origin が `VPC` で、Lambda が別 VPC にいる

---

## 7. テスト設計の注意事項

### ユニットテスト
- S3 AP ARN を `Bucket` パラメータとして渡すことをテストで検証
- `conftest.py` で `S3_ACCESS_POINT_ARN` 環境変数を設定

### 統合テスト
- VPC 外 Lambda でのテストを優先（ネットワーク問題を回避）
- VPC 内テストは NAT Gateway 確認後に実施

### CloudFormation テスト
- `cfn-lint` で ARN パターンの検証
- IAM リソースの `/object/*` サフィックスを確認

---

## 参考リンク

- [AWS Docs — FSx for ONTAP S3 AP API Support](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/access-points-for-fsxn-object-api-support.html)
- [AWS Docs — Managing S3 AP Access](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/s3-ap-manage-access-fsxn.html)
- [AWS Blog — S3 Access Points for FSx](https://aws.amazon.com/blogs/storage/bridge-legacy-and-modern-applications-with-amazon-s3-access-points-for-amazon-fsx/)
- [AWS Docs — Process files with Lambda](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/tutorial-process-files-with-lambda.html)
- [AWS Blog — AI-powered analytics with S3 AP + AD](https://aws.amazon.com/blogs/storage/enabling-ai-powered-analytics-on-enterprise-file-data-configuring-s3-access-points-for-amazon-fsx-for-netapp-ontap-with-active-directory/)

---

## 8. FSx for ONTAP S3 Access Points — 制約と検証済みパターン

包括的な互換性マトリクス、検証済みパターン、既知の制約（2026年5月 AWS サポート確認済み）については以下を参照:

📋 **[FSx for ONTAP S3 AP 互換性マトリクス](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/docs/en/compatibility-matrix.md)**

本プロジェクトに関連する主要な制約:

| 制約 | 影響 | 回避策 |
|------|------|--------|
| 条件付き書き込み非対応 (If-None-Match) | Delta Lake/Iceberg/Hudi のトランザクション書き込み不可 | 読み取り専用分析、または DataSync → S3 で書き込みワークロード |
| S3 Event Notifications 非対応 | Snowpipe 自動取り込み、Auto Loader ファイル通知モード不可 | FPolicy → Lambda、スケジュールポーリング、Snowpipe REST API |
| SnapMirror S3 非対応 | ONTAP S3 バケットを AWS S3 にレプリケート不可 | DataSync (NFS → S3) を検証済み同期メカニズムとして使用 |
| ListObjectsV2 レイテンシ | 10〜5,000 オブジェクトでネイティブ S3 比 1.3〜1.4 倍（2026-08-05 実測）。5,000 件超は未測定 | この規模では対策不要。大規模ディレクトリではファイル統合とキー空間のパーティション化 — 下記の補足を参照 |
| SSE-FSX 暗号化のみ | SSE-S3, SSE-KMS, SSE-C 非対応 | デフォルト SSE-FSX を使用（透過的、AWS KMS 管理） |
| Object Versioning 非対応 | S3 バージョニング不可 | ONTAP Snapshot でポイントインタイムリカバリ |
| Presigned URL: 公式非対応 | 実際には動作するが保証なし | 非クリティカルパスのみ使用、IAM ベースアクセスを推奨 |
| ONTAP 9.17.1+ 必須 | S3 Access Points の最小バージョン | デプロイ前に FSx ファイルシステムの ONTAP バージョンを確認 |

プラットフォーム固有の互換性（Athena, Glue, EMR, Databricks, Snowflake, Bedrock）を含む完全なマトリクスは[完全版ドキュメント](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/docs/en/compatibility-matrix.md)を参照。

### ListObjectsV2 レイテンシ — 「30-80 倍」の記述は撤回

2026-08-05 に再測定した結果、これまで記載していた「小規模ディレクトリでネイティブ S3 比 30-80 倍遅い」というペナルティは再現しませんでした。実測値（median）は以下です。

| オブジェクト数 | FSx for ONTAP S3 AP | ネイティブ S3 | 比率 |
|--------:|--------------------:|----------:|------:|
| 10 | 38 ms | 27 ms | 1.4x |
| 100 | 52 ms | 39 ms | 1.3x |
| 1,000 | 162 ms | 128 ms | 1.3x |
| 5,000 | 665 ms | 704 ms | 0.9x |

**測定条件**: ap-northeast-1、SINGLE_AZ_1 ファイルシステム、SSD 1024GB、スループット 128MBps、UNIX security style ボリューム、Internet-origin アクセスポイント。データ点ごとに warm-up 1 回を破棄した上で記録試行 5 回の median。計測範囲はページネーションされた `ListObjectsV2` ループのみで、クライアント生成・認証解決・オブジェクト投入は除外しています。リトライは無効化（`max_attempts=1`）し、遅い呼び出しが再試行で隠れないようにしています。フラット構造とネスト構造（2 階層・leaf あたり 10 オブジェクト）で同じ比率になりました。

- エビデンス: [`verification-pack/s3ap-list-latency/evidence/2026-08-05/benchmark-result.yaml`](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/verification-pack/s3ap-list-latency/evidence/2026-08-05/benchmark-result.yaml)
- 再現スクリプト: [`shared/scripts/benchmark_list_objects.py`](https://github.com/Yoshiki0705/fsxn-lakehouse-integrations/blob/main/shared/scripts/benchmark_list_objects.py)

**測定範囲の限界 — 大規模ディレクトリは未測定です。** 測定は 5,000 オブジェクトで打ち切っており、単一 prefix 配下に数十万〜数百万件が存在する場合の挙動は測定していません。ONTAP は `ListObjectsV2` が要求する辞書順を得るためにディレクトリエントリをインメモリでソートするため、リスト処理のコストはディレクトリ規模に応じて増加します。したがって大規模データセットではファイル統合とキー空間のパーティション化が引き続き推奨される設計です。ただしその判断根拠はディレクトリ規模とインメモリソートであり、再現しなかった小規模でのレイテンシペナルティではありません。

**元の数値の出自: 未解明です。** この数値は、本セクション冒頭で参照している 2026 年 5 月の AWS サポートとのやり取りに由来し、そこではプロダクトレベルの特性として説明されていました。ただし測定記録や測定条件は残されておらず、今回の測定と比較する対象が存在しないため、差異の原因は特定できません。可能性として考えられる要因を列挙するに留めます: CLI ラッパー経由の測定でプロセス起動時間が短時間の呼び出しを支配していた、当時のファイルシステムが劣化した状態だった、あるいは当時から今回の測定までの間にプラットフォーム側の変更があった。

**副次的な実測結果 — 1 ページあたりに返るキー数が異なります。** `MaxKeys=1000` を指定した場合、ネイティブ S3 は 1 API call で 1,000 キーを返しましたが、アクセスポイントは 2 call を必要としました。5,000 件では 6 call 対 5 call でした。総時間は同等なのでスループットのボトルネックではありませんが、API リクエストコストを試算する場合やページネーション深度に制限のあるクライアントを使う場合に、API call 数が同一であることを前提にしないでください。
