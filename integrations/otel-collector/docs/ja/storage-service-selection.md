# ストレージサービス選択ノート

🌐 **日本語**（このページ） | [English](../en/storage-service-selection.md)

## コンテキスト

本プロジェクトは FSx for ONTAP からテレメトリを生成します。すべてのログやテレメトリデータのストレージレイヤーとして FSx for ONTAP を規定するものではありません。

## 用途に適したストレージの選択

| ストレージサービス | 最適な用途 | 不向きな用途 |
|----------------|----------|---------|
| **FSx for ONTAP** | エンタープライズファイルワークロード、ONTAP データ管理、監査ログソース | 汎用ログアーカイブ |
| **Amazon S3** | 耐久性のあるオブジェクトストレージ、生の監査アーカイブ、データレイク、長期保持 | 低レイテンシファイルアクセス |
| **Amazon EFS** | Linux ワークロード向け共有 POSIX ファイルストレージ | Windows ワークロード、ブロックストレージ |
| **Amazon EBS** | EC2 インスタンスにアタッチされたブロックストレージ | 共有ファイルアクセス、オブジェクトストレージ |

## FSx for ONTAP S3 Access Points

FSx for ONTAP ボリュームにアタッチされた S3 Access Points は、データを別の S3 バケットにコピーすることなく、ファイルデータへの S3 API アクセスを可能にします。主な特性:

- データは FSx for ONTAP ファイルシステム上に残る
- NFS、SMB、S3 API で同時にアクセス可能
- S3 API レイテンシ: 数十ミリ秒（ネイティブ S3 のサブミリ秒ではない）
- スループットは FSx ファイルシステムのプロビジョニング済みスループットキャパシティに依存
- 標準 S3 バケットのスケーリング特性とは同等ではない

> ソース: [AWS FSx for ONTAP — Accessing data via S3 access points](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/accessing-data-via-s3-access-points.html)
