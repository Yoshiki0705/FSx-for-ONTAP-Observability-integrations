# S3 Access Points for FSx for ONTAP — 知見集

🌐 **日本語**（このページ） | [English](../en/s3-access-points-knowledge.md)

**作成日**: 2026-05-16
**目的**: FSx for ONTAP の S3 Access Points 設定に関する知見を集約

---

## 概要

Amazon S3 Access Points for FSx for ONTAP は、FSx for ONTAP ボリュームに S3 互換のアクセスポイントをアタッチする機能。NFS/SMB でアクセスしているデータに対して、S3 API 経由でもアクセスできるようになる（データのコピーは不要）。

## ユーザーマッピングの仕組み

### デュアルレイヤー認証モデル

S3 Access Points は2層の認証を使用:

1. **AWS IAM 層**: S3 Access Point ポリシーで IAM プリンシパルのアクセスを制御
2. **ファイルシステム層**: Access Point に紐づけた「ファイルシステムユーザー ID」の権限でファイルアクセスを認可

### ファイルシステムユーザー ID

Access Point 作成時に指定する「ファイルシステムユーザー ID」が、全ての S3 API リクエストの認可に使用される。

- **UNIX ID** — UNIX セキュリティスタイルのボリューム用（UID/GID ベース）
- **Windows ID** — NTFS セキュリティスタイルのボリューム用（ドメイン\ユーザー名）

**重要**: `root` ユーザー（UID 0）を指定すると全ファイルにアクセス可能。制限付きユーザーを指定するとそのユーザーの権限に制限される。

### セキュリティスタイルとの対応

| ボリュームのセキュリティスタイル | 推奨 ID タイプ | 権限モデル |
|------|------|------|
| UNIX | UNIX ID | mode-bits / NFSv4 ACL |
| NTFS | Windows ID | Windows ACL |
| Mixed | ケースバイケース | 両方のモデルが適用 |

## S3 Access Point の作成手順

### 前提条件

- FSx for ONTAP ボリュームが存在し、マウント済み（junction path が設定されている）
- ボリュームが AVAILABLE 状態

### CLI での作成

```bash
aws fsx create-and-attach-s3-access-point \
  --name <access-point-name> \
  --type ONTAP \
  --ontap-configuration 'VolumeId=<volume-id>,FileSystemIdentity={Type=UNIX,UnixUser={Name=<username>}}' \
  --s3-access-point 'VpcConfiguration={VpcId=<vpc-id>}' \
  --region ap-northeast-1
```

### レスポンス例

```json
{
  "S3AccessPointAttachment": {
    "Lifecycle": "CREATING",
    "Name": "fsxn-audit-observability",
    "S3AccessPoint": {
      "ResourceARN": "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-observability",
      "Alias": "fsxn-audit-obser-cbsi8mwwgahuh7sans3bbtxijig4sapn1b-ext-s3alias",
      "VpcConfiguration": {
        "VpcId": "vpc-0123456789abcdef0"
      }
    }
  }
}
```

## Lambda からの S3 Access Point 利用

### 重要なポイント

S3 Access Point ARN を `Bucket` パラメータとして使用する（通常の S3 バケット名の代わりに）:

```python
s3_client.get_object(
    Bucket="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-observability",
    Key="path/to/audit-log.json"
)
```

### IAM ポリシー

Lambda の IAM ロールには以下の権限が必要:

```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject"],
  "Resource": ["arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-observability/object/*"]
}
```

## VPC 制限

- Access Point を VPC に制限すると、その VPC 内からのみアクセス可能
- Lambda を同じ VPC 内で実行するか、VPC エンドポイント経由でアクセスする必要がある
- Internet origin にすると VPC 外からもアクセス可能（IAM ポリシーで制御）

## MISCONFIGURED 状態

以下の場合に Access Point が MISCONFIGURED 状態になる:
- ファイルシステムユーザー ID がファイルシステム上で解決できなくなった
- アタッチされたボリュームがオフラインまたはアンマウントされた

→ 原因が解消されると自動的に AVAILABLE に戻る

## このプロジェクトでの設定

| 項目 | 値 |
|------|------|
| ファイルシステム ID | `fs-0123456789abcdef0` |
| ボリューム ID | `fsvol-0123456789abcdef0` |
| ボリューム名 | `audit_logs_observability` |
| Junction Path | `/audit_logs_observability` |
| SVM | `svm-0abcdef123456789a` (FSxN_OnPre) | <!-- allow:naming: SVM resource name -->
| Access Point 名 | `fsxn-audit-observability` |
| Access Point ARN | `arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-observability` |
| Access Point Alias | `fsxn-audit-obser-cbsi8mwwgahuh7sans3bbtxijig4sapn1b-ext-s3alias` |
| VPC | `vpc-0123456789abcdef0` |
| ファイルシステムユーザー | `root` (UNIX) |

## 参考リンク

- [AWS Docs: Creating access points](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/create-access-points.html)
- [AWS Docs: Managing access point access](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/s3-ap-manage-access-fsxn.html)
- [NetApp Blog: User access mapping with S3 Access Points](https://community.netapp.com/t5/Tech-ONTAP-Blogs/User-access-mapping-with-Amazon-S3-Access-Points-for-Amazon-FSx-for-NetApp-ONTAP/ba-p/467120)
- [AWS Blog: Enabling AI-powered analytics on enterprise file data](https://aws.amazon.com/blogs/storage/enabling-ai-powered-analytics-on-enterprise-file-data-configuring-s3-access-points-for-amazon-fsx-for-netapp-ontap-with-active-directory/)
