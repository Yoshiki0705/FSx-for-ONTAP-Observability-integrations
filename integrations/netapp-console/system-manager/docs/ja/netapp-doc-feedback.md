# NetApp ドキュメントへのフィードバック

## 概要

FSx for ONTAP で System Manager / NetApp Console を利用する際に、ドキュメントの不明確さが原因でセットアップに困難が生じた点をまとめます。

**検証日**: 2026年5月28日
**ONTAP バージョン**: 9.17.1P6
**対象ドキュメント**:
- [Managing FSx for ONTAP resources using NetApp applications](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)
- [Integrate ONTAP System Manager with NetApp Console](https://docs.netapp.com/us-en/ontap/concepts/sysmgr-integration-console-concept.html)
- [Add AWS credentials to Workload Factory](https://docs.netapp.com/us-en/workload-setup-admin/add-credentials.html)
- [Quick start for FSx for ONTAP](https://docs.netapp.com/us-en/storage-management-fsx-ontap/start/task-getting-started-fsx.html)

---

## フィードバック項目

### 1. 「System Manager」の名称と実態のギャップ

**問題**: AWS ドキュメント（2023年12月発表）では「FSx for ONTAP now supports using NetApp System Manager」と記載されているが、実際には従来の System Manager UI は独立して提供されておらず、Workload Factory UI に統合されている。

**影響**: 利用者が「System Manager = 無料で即座に使える GUI」と誤解し、管理エンドポイントに直接アクセスして 404 エラーに遭遇する。

**提案**: 
- AWS ドキュメントに「System Manager は NetApp Console の Workload Factory UI 内で提供される」ことを明記
- 「直接ブラウザアクセスでは利用不可」の注意書きを追加

### 2. NetApp Console と Workload Factory の URL 混在

**問題**: 2つのコンソールが別 URL で存在し、機能が分散している。
- `console.netapp.com` — NetApp Console（Systems ページ、Discover）
- `console.workloads.netapp.com` — Workload Factory（Credentials 管理、Administration）

**影響**: クレデンシャルの管理ページにたどり着けない。NetApp Console の UI からは Workload Factory の Administration > Credentials への導線がない。

**提案**:
- NetApp Console の Storage ダッシュボードに「Manage credentials」への直接リンクを追加
- ドキュメントに両 URL の関係と使い分けを明記

### 3. Link 作成に必要な IAM 権限が不明確

**問題**: Link 作成ウィザードで「FSx for ONTAP network identifier: n/a」と表示され、先に進めない。原因は IAM ロールに `ec2:DescribeNetworkInterfaces` 等の権限が不足していたため。

**影響**: 権限不足のエラーメッセージが「Insufficient privileges to assume role」のみで、具体的にどの権限が不足しているか分からない。

**提案**:
- Link 作成に必要な IAM 権限の完全なリストをドキュメントに記載
- エラーメッセージに不足している具体的な権限名を含める
- CloudFormation テンプレートで Link 用 IAM ロールを自動作成するオプションを提供

### 4. クレデンシャルの「Credentials are no longer valid」エラー

**問題**: 自動生成されたクレデンシャルが参照する IAM ロール（`credentials-role-*`）が AWS 側に存在しない場合、「Credentials are no longer valid」エラーが表示されるが、修正方法が不明。

**影響**: 古いクレデンシャルが残り続け、Discover ページで選択すると毎回エラーになる。削除や再作成の手順が分かりにくい。

**提案**:
- 無効なクレデンシャルの削除手順を明記
- エラー時に「このクレデンシャルを削除して再作成してください」のガイダンスを表示

### 5. Workload Factory UI での機能カバレッジが不明確

**問題**: Workload Factory UI でどの ONTAP 操作が可能で、どれが CLI/REST API 必須かがドキュメントに明記されていない。

**影響**: 利用者が「GUI で全て管理できる」と期待してセットアップしたが、監査ログやクォータの設定ができないことが後から判明する。

**提案**:
- Workload Factory UI で可能な操作の一覧を明記
- 「CLI/REST API が必要な操作」セクションを追加
- 各操作ページに「この操作は CLI でのみ実行可能です」の注記を追加

---

## 再現手順

1. FSx for ONTAP ファイルシステムを作成
2. 管理エンドポイント IP にブラウザでアクセス → 404 エラー
3. NetApp Console (`console.netapp.com`) にログイン
4. Storage ダッシュボードで FSx for ONTAP を Discover
5. クレデンシャルの権限不足で「Insufficient privileges to assume role」エラー
6. Workload Factory (`console.workloads.netapp.com`) > Administration > Credentials でクレデンシャル修正
7. Link 作成 → IAM 権限不足で「network identifier: n/a」
8. IAM ロールに Lambda/CloudFormation/EC2 Describe 権限を追加
9. Link 作成成功 → Workload Factory UI でボリューム管理可能
10. 監査ログ・クォータ設定を GUI で探すが見つからない → CLI/REST API で実施

---

## 参考: 検証で使用した IAM 権限（Link 作成に必要）

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:DeleteFunction",
        "lambda:InvokeFunction",
        "lambda:GetFunction",
        "lambda:UpdateFunctionCode",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PassRole",
        "iam:GetRole",
        "cloudformation:CreateStack",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeStacks",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "fsx:DescribeFileSystems",
        "fsx:DescribeStorageVirtualMachines",
        "fsx:DescribeVolumes"
      ],
      "Resource": "*"
    }
  ]
}
```


---

## 追加フィードバック: データ主権とプライバシー

### 6. Link 経由で NetApp SaaS に送信されるデータの範囲が不明確

**問題**: Link（Lambda）を作成して NetApp Console から System Manager にアクセスする際、どのデータが NetApp の SaaS 基盤（`console.netapp.com`）に送信されるかがドキュメントに明記されていない。

**影響**: データレジデンシー要件がある組織（金融、公共、医療）が、NetApp Console の利用可否を判断できない。

**確認すべき項目**:
- ONTAP REST API のレスポンスデータは NetApp SaaS を経由するのか、ブラウザに直接返されるのか
- ボリューム名、SVM 名、ファイルパス等のメタデータは NetApp 側に保存されるのか
- 監査ログの内容（ユーザー名、アクセス先パス）は NetApp SaaS に送信されるのか
- FSA Activity Tracking のデータ（Top files/users）は NetApp 側に保存されるのか
- Link Lambda の実行ログに含まれる情報は何か

**提案**:
- NetApp Console のデータフロー図（どのデータがどこを通るか）をドキュメントに追加
- 「NetApp SaaS に保存されるデータ」と「ユーザーの VPC 内に留まるデータ」の明確な区別
- データレジデンシー要件がある組織向けの代替構成（セルフホスト管理コンソール等）の案内
