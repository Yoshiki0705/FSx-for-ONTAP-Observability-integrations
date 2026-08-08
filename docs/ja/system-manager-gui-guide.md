# ONTAP System Manager GUI 操作ガイド

🌐 **日本語**（このページ） | [English](../en/system-manager-gui-guide.md)

## 概要

本ドキュメントは、FSx for ONTAP の運用部門が **ONTAP System Manager（GUI）** を使用して、以下の操作を行うための手順書です。

- System Manager へのアクセス
- 共有フォルダの監査ログ設定
- Qtree クォータ（容量制限）設定
- 容量監視・通知の設定

> **対象読者**
>
> Windows ファイルリソースマネージャーに慣れた運用担当者

---

## 前提知識: System Manager vs NetApp BlueXP<!-- allow:naming --> vs NetApp Console<!-- allow:naming -->

| ツール | 種類 | 費用 | アカウント | 用途 |
|--------|------|------|-----------|------|
| **ONTAP System Manager** | ONTAP GUI（NetApp Console<!-- allow:naming --> 経由で利用） | **無料** | NSS アカウント必要 | ストレージ管理全般 |
| **NetApp Console**<!-- allow:naming --> (旧 BlueXP<!-- allow:naming -->) | SaaS ポータル | 基本無料 | NSS アカウント必要 | System Manager ホスト + マルチクラウド管理 |
| **[セルフホスト Management Console](../../management-console/)** | AWS ネイティブ（ECS Fargate + AMP/AMG） | 約 $250/月（24時間稼働） | 不要（AWS アカウントのみ） | VPC 内監視・管理 UI |
| **ONTAP REST API** | HTTP API（直接アクセス可能） | **無料** | 不要（fsxadmin で認証） | 自動化・スクリプト |
| **ONTAP CLI** | SSH コマンドライン | **無料** | 不要（fsxadmin で認証） | 高度な設定 |

> ⚠️ **重要な制約**: FSx for ONTAP では、オンプレミス ONTAP と異なり、管理エンドポイント (`https://<management-endpoint-ip>`) に直接ブラウザアクセスしても **System Manager UI は表示されません**（404 エラー）。System Manager を GUI で利用するには **NetApp Console<!-- allow:naming --> 経由** が必須です。REST API (`/api/`) と CLI (SSH) は直接利用可能です。

**結論**: GUI でストレージ管理を行うには **NetApp Console<!-- allow:naming --> のセットアップが必要** です。CLI/REST API であれば NetApp アカウント不要で即座に利用可能です。

---

## 1. System Manager へのアクセス（NetApp Console<!-- allow:naming --> 経由）

### 1.1 NetApp Console<!-- allow:naming --> セットアップ手順

FSx for ONTAP で System Manager を利用するには、以下のセットアップが必要です:

#### Step 1: NetApp アカウント（NSS）の作成

1. [NetApp User Registration](https://mysupport.netapp.com/site/user/registration) にアクセス
2. **NetApp Customer/End User** アクセスレベルを選択（これはUI上の選択肢のラベルをそのまま表記したものです — 実際のアクセス階層はエンドユーザー/標準レベルであり、ベンダーサポート契約を意味するものではありません）
3. **SERIAL NUMBER** フィールドに FSx for ONTAP の **File System ID** を入力
4. 登録完了後、1営業日以内にエンドユーザーレベルのアクセスに昇格

> **Note**
>
> アカウント作成自体は無料です。サポートケース起票には有償サポート契約が必要ですが、System Manager の利用には不要です。

#### Step 2: NetApp Console<!-- allow:naming --> にログイン

1. [NetApp Console<!-- allow:naming -->](https://console.netapp.com) にアクセス
2. NSS クレデンシャルでログイン
3. 初回ログイン時にアカウント名を設定

#### Step 3: AWS 認証情報の登録

NetApp Console<!-- allow:naming --> に AWS クレデンシャルを追加します:

- **読み取り専用** — FSx for ONTAP の検出・監視のみ
- **読み書き** — ボリューム作成・変更等の管理操作も可能

参考: [Set up permissions](https://docs.netapp.com/us-en/storage-management-fsx-ontap/requirements/task-setting-up-permissions-fsx.html)

#### Step 4: Console Agent または Link の作成

System Manager を含む管理機能を利用するには、以下のいずれかが必要です:

| 方式 | 説明 | 推奨シナリオ |
|------|------|------------|
| **Console Agent** | VPC 内にデプロイする EC2 インスタンス (t3.xlarge) | 本番環境、複数ファイルシステム管理 |
| **Link** | AWS Lambda で信頼関係を構築 | 軽量な管理、コスト最小化 |

- Console Agent: [AWS での作成手順](https://docs.netapp.com/us-en/console-setup-admin/concept-install-options-aws.html)
- Link: [Link の作成手順](https://docs.netapp.com/us-en/workload-fsx-ontap/create-link.html)

#### Step 5: FSx for ONTAP の検出

1. NetApp Console<!-- allow:naming --> → **Systems** ページ
2. **Discover** → **Amazon FSx for NetApp ONTAP** を選択
3. AWS リージョンとクレデンシャルを指定
4. 既存の FSx for ONTAP ファイルシステムが検出される

#### Step 6: System Manager を開く

1. NetApp Console<!-- allow:naming --> → **Systems** ページ → 対象ファイルシステムを選択
2. **System Manager** をクリック
3. `fsxadmin` のクレデンシャルを入力
4. System Manager UI が NetApp Console<!-- allow:naming --> 内に表示される

### 1.2 代替手段: CLI / REST API（NetApp Console<!-- allow:naming --> 不要）

NetApp Console<!-- allow:naming --> のセットアップが不要な管理方法:

| 方法 | アクセス先 | 認証 | 用途 |
|------|-----------|------|------|
| **ONTAP CLI** | `ssh fsxadmin@<management-endpoint-ip>` | fsxadmin パスワード | 全ての ONTAP 操作 |
| **ONTAP REST API** | `https://<management-endpoint-ip>/api/` | Basic Auth (fsxadmin) | 自動化・スクリプト |
| **AWS CLI** | `aws fsx ...` | IAM 認証 | ファイルシステムレベルの管理 |

> **推奨**
>
> 監査ログやクォータの初期設定は CLI/REST API で実施し、日常的な監視・確認に NetApp Console<!-- allow:naming --> (System Manager) を使用するハイブリッドアプローチが現実的です。

> **セキュリティベストプラクティス**
> - `fsxadmin` パスワードは AWS Secrets Manager に保存すること
> - System Manager の全操作は ONTAP 監査ログに記録される

---

## 2. 監査ログ設定（GUI 手順）

### 2.1 前提条件

- 監査ログ保存用のボリュームが作成済みであること
- SVM（Storage Virtual Machine）が存在すること

### 2.2 監査ログ保存用ボリュームの作成

> 既にボリュームがある場合はスキップ

1. System Manager → **Storage** → **Volumes**
2. **+ Add** をクリック
3. 設定:
   - Volume name: `audit_logs`
   - SVM: 対象の SVM を選択
   - Size: 50GB 以上推奨（ログ量に応じて調整）
   - Export Policy: なし（内部利用のみ）
4. **Save** をクリック

### 2.3 監査ログの有効化

1. System Manager → **Storage** → **Storage VMs**
2. 対象の SVM をクリック
3. **Settings** タブを選択
4. **Security** セクション → **Audit** の横にある鉛筆アイコン（編集）をクリック
5. **Enable Auditing** をオンにする
6. 設定項目:

| 項目 | 推奨値 | 説明 |
|------|--------|------|
| Log Destination | `/vol/audit_logs` | 監査ログ保存先パス |
| Log Format | **EVTX** | Windows イベントログ形式（Windows 運用者に馴染みやすい） |
| Rotation Schedule | Size-based | サイズベースのローテーション |
| Rotation Size | 100 MB | 1ファイルの最大サイズ |
| Rotation Limit | 0 (unlimited) | 保持するファイル数（0=無制限） |

7. **Save** をクリック

### 2.4 監査対象の設定（SACL）

監査ログで「どのフォルダ/ファイルへのアクセスを記録するか」は、Windows の SACL（System Access Control List）で制御します。

**Windows エクスプローラーから設定:**
1. 対象フォルダを右クリック → **プロパティ**
2. **セキュリティ** タブ → **詳細設定**
3. **監査** タブ → **追加**
4. 監査エントリを設定:
   - プリンシパル: `Everyone`（全ユーザー対象）
   - 種類: **成功** と **失敗** の両方
   - アクセス許可: **フル コントロール**（全操作を記録する場合）

> **Note**
>
> SACL は Windows のファイルリソースマネージャーで設定するため、運用部門の既存スキルで対応可能です。

### 2.5 監査ログの確認

設定後、以下で動作確認:

1. CIFS 共有フォルダにファイルを作成/アクセス
2. System Manager → **Events** で監査イベントを確認
3. または、監査ログボリュームに EVTX ファイルが生成されていることを確認

---

## 3. Qtree クォータ（容量制限）設定

### 3.1 概要

Qtree クォータを使用すると、フォルダ単位で容量制限を設定できます。

| クォータ種類 | 動作 |
|------------|------|
| **ソフトリミット** | 閾値超過時に警告（EMS イベント発行）。書き込みは継続可能 |
| **ハードリミット** | 閾値超過時に書き込みを拒否 |

### 3.2 Qtree の作成

1. System Manager → **Storage** → **Volumes** → 対象ボリュームをクリック
2. **Qtrees** タブを選択
3. **+ Add Qtree** をクリック
4. 設定:
   - Name: `dept-sales`（部門名など）
   - Security Style: **NTFS**（Windows 環境の場合）
   - Export Policy: 必要に応じて設定
5. **Save** をクリック

### 3.3 クォータルールの作成

1. System Manager → **Storage** → **Volumes** → 対象ボリュームをクリック
2. **Quota Rules** タブを選択（ONTAP バージョンにより **Quotas** タブ）
3. **+ Add Quota Rule** をクリック
4. 設定:

| 項目 | 設定例 | 説明 |
|------|--------|------|
| Quota Type | **Tree** | Qtree 単位の制限 |
| Qtree | `dept-sales` | 対象 Qtree |
| Disk Space Hard Limit | 100 GB | 書き込み拒否の上限 |
| Disk Space Soft Limit | 80 GB | 警告閾値（80%） |
| File Count Hard Limit | 1,000,000 | ファイル数上限（任意） |

5. **Save** をクリック

### 3.4 クォータの有効化

クォータルール作成後、クォータを有効化（初期化）する必要があります:

1. System Manager → **Storage** → **Volumes** → 対象ボリューム
2. **Quota Rules** タブ → **Initialize Quotas** ボタンをクリック

> **Note**
>
> クォータの初期化には数分かかる場合があります。大量のファイルがある場合は、初回のスキャンに時間がかかります。

### 3.5 クォータ使用状況の確認

1. System Manager → **Storage** → **Volumes** → 対象ボリューム
2. **Quota Rules** タブ → 各 Qtree の使用量が表示される

---

## 4. 容量監視・通知の設定

### 4.1 監視方法の選択

| 方法 | 監視対象 | リアルタイム性 | 通知方法 | 推奨シナリオ |
|------|---------|-------------|---------|------------|
| **A: EMS Webhook** | Qtree クォータ閾値 | ◎ リアルタイム | Lambda → SNS → メール | クォータ超過の即時通知 |
| **B: CloudWatch Alarms** | ボリューム全体の容量 | ○ 5分間隔 | SNS → メール | ボリューム容量の監視 |
| **C: ONTAP EMS → CloudWatch Events** | EMS イベント全般 | ○ 数分 | EventBridge → SNS | AWS ネイティブ統合 |
| **D: Harvest + Grafana** | 全メトリクス | ○ 60秒間隔 | Grafana Alerting | 詳細ダッシュボード |

### 4.2 方法 A: EMS Webhook（Qtree クォータ通知 — 推奨）

ONTAP は Qtree クォータの閾値超過時に以下の EMS イベントを自動発行します:

| EMS イベント | トリガー条件 | 重要度 |
|------------|------------|--------|
| `wafl.quota.softlimit.exceeded` | ソフトリミット超過 | warning |
| `wafl.quota.hardlimit.exceeded` | ハードリミット超過 | error |

#### アーキテクチャ

```
Qtree 容量超過
  → ONTAP EMS イベント発行
  → Webhook (HTTPS POST)
  → API Gateway
  → Lambda（パース + 配信）
  → SNS トピック
  → メール通知
```

#### 設定手順

**Step 1: AWS 側のデプロイ**

本リポジトリの EMS Webhook テンプレートを使用:

```bash
# Deploy EMS Webhook stack
aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-ems-webhook \
  --parameter-overrides \
    LambdaFunctionArn=<EMS handler Lambda ARN> \
  --capabilities CAPABILITY_NAMED_IAM
```

**Step 2: SNS トピック作成 + メールサブスクリプション**

```bash
# Create SNS topic
aws sns create-topic --name fsxn-quota-alerts

# Subscribe email address
aws sns subscribe \
  --topic-arn arn:aws:sns:ap-northeast-1:123456789012:fsxn-quota-alerts \
  --protocol email \
  --notification-endpoint ops-team@example.com
```

**Step 3: ONTAP EMS Webhook 設定（CLI）**

> ⚠️ EMS Webhook の設定は **CLI でのみ可能** です（System Manager GUI では未対応）。

```bash
# SSH to ONTAP management endpoint
ssh fsxadmin@<management-endpoint-ip>

# 1. Create webhook notification destination
event notification destination create -name quota-webhook \
  -rest-api-url https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems

# 2. Create quota event filter
event filter create -filter-name quota-alerts
event filter rule add -filter-name quota-alerts -type include \
  -message-name wafl.quota.*

# 3. Create notification
event notification create -filter-name quota-alerts \
  -destinations quota-webhook

# 4. Verify
event notification show
event notification destination show
```

**Step 4: 動作確認**

```bash
# Test: Write data to Qtree exceeding soft limit
# Copy a large file via Windows Explorer
# → EMS event issued → Webhook → Lambda → SNS → Verify email received
```

### 4.3 方法 B: CloudWatch Alarms（ボリューム容量監視）

CloudWatch で監視できるのは**ボリュームレベル**の容量のみです（Qtree 単位は不可）。

```bash
# Create CloudWatch alarm
aws cloudwatch put-metric-alarm \
  --alarm-name "FSx-ONTAP-Volume-Capacity-Warning" \
  --metric-name "StorageCapacityUtilization" \
  --namespace "AWS/FSx" \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:ap-northeast-1:123456789012:fsxn-quota-alerts \
  --dimensions Name=FileSystemId,Value=fs-0123456789abcdef0
```

| CloudWatch メトリクス | 説明 | 閾値例 |
|---------------------|------|--------|
| `StorageCapacityUtilization` | ボリューム使用率 (%) | 80% で警告、90% で緊急 |
| `StorageUsed` | 使用量 (bytes) | 絶対値での監視 |

### 4.4 方法 C: ONTAP EMS → CloudWatch Events → EventBridge

FSx for ONTAP は一部の EMS イベントを CloudWatch Events として自動発行します。

```json
{
  "source": ["aws.fsx"],
  "detail-type": ["FSx for ONTAP EMS Event"],
  "detail": {
    "event-name": ["wafl.quota.softlimit.exceeded"]
  }
}
```

```bash
# Create EventBridge rule
aws events put-rule \
  --name "FSx-ONTAP-Quota-Alert" \
  --event-pattern '{"source":["aws.fsx"],"detail-type":["FSx for ONTAP EMS Event"],"detail":{"event-name":["wafl.quota.softlimit.exceeded","wafl.quota.hardlimit.exceeded"]}}'

# Add SNS target
aws events put-targets \
  --rule "FSx-ONTAP-Quota-Alert" \
  --targets "Id"="1","Arn"="arn:aws:sns:ap-northeast-1:123456789012:fsxn-quota-alerts"
```

> **Note**
>
> CloudWatch Events 経由の EMS イベント配信は、全ての EMS イベントが対象ではありません。対応イベントは [AWS ドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-cloudwatch-events.html) を参照してください。

### 4.5 推奨構成（組み合わせ）

| 監視対象 | 方法 | 通知先 |
|---------|------|--------|
| Qtree クォータ超過（即時） | EMS Webhook (方法 A) | メール + Slack |
| ボリューム容量 80% 超過 | CloudWatch Alarm (方法 B) | メール |
| ボリューム容量 90% 超過 | CloudWatch Alarm (方法 B) | メール + PagerDuty |
| ランサムウェア検知 | EMS Webhook (方法 A) | メール + Slack + PagerDuty |

---

## 5. System Manager で「できること」と「できないこと」

### ✅ System Manager で可能な操作

| カテゴリ | 操作 |
|---------|------|
| **ボリューム管理** | 作成、サイズ変更、削除、スナップショット |
| **Qtree 管理** | 作成、削除、セキュリティスタイル変更 |
| **クォータ管理** | ルール作成、有効化、使用状況確認 |
| **監査ログ** | 有効化、設定変更、ステータス確認 |
| **CIFS 共有** | 作成、ACL 設定、プロパティ変更 |
| **NFS エクスポート** | ポリシー作成、ルール追加 |
| **SnapMirror** | レプリケーション設定、ステータス確認 |
| **ネットワーク** | LIF 確認、DNS/NIS 設定 |
| **パフォーマンス** | IOPS、スループット、レイテンシのリアルタイム表示 |

### ⚠️ CLI が必要な操作

| カテゴリ | 操作 | 理由 |
|---------|------|------|
| **EMS Webhook** | 通知先・フィルタ・通知ルール設定 | GUI 未対応 |
| **FPolicy** | 外部エンジン・ポリシー設定 | GUI 未対応（FSx for ONTAP） |
| **高度な監査設定** | イベントの詳細フィルタリング | GUI では基本設定のみ |
| **S3 Access Point** | バケットポリシー設定 | AWS CLI / コンソールで実施 |

### ❌ FSx for ONTAP で利用不可な System Manager 機能

| 機能 | 理由 |
|------|------|
| ノード管理 | マネージドサービスのため AWS が管理 |
| ディスク管理 | マネージドサービスのため AWS が管理 |
| クラスタ設定 | マネージドサービスのため AWS が管理 |
| ONTAP アップグレード | AWS コンソールから実施 |
| ライセンス管理 | FSx サービスに含まれる |

---

## 5.1 FSA Explorer: フォルダドリルダウンとファイルパス分析

FSA Explorer では、任意のディレクトリ階層まで**フォルダ単位でドリルダウン**してファイルアクセスパターンを分析できます。

### FSA Explorer へのアクセス

1. System Manager → **Storage** → **Volumes** → 対象ボリュームをクリック
2. **File system** タブを選択
3. **Explorer** サブタブをクリック
4. **Analytics enabled** トグルが ON であることを確認

### Explorer の機能

| 機能 | 説明 |
|------|------|
| **ディレクトリツリーナビゲーション** | 左パネルにフォルダ階層を表示。フォルダをクリックでドリルダウン |
| **ファイル一覧とメタデータ** | 右パネルに選択ディレクトリ内のファイル名とサイズを表示 |
| **サブディレクトリ/ファイル数** | 各階層でサブディレクトリ数とファイル数を正確に表示 |
| **Access history カラム** | 各ファイル/ディレクトリの最終アクセス日時を表示 |
| **Modify history カラム** | 最終更新日時を表示 |
| **パンくずナビゲーション** | パスバーに現在位置を表示（例: `/folder1/folder2/`） |

### ドリルダウン動作（検証済み）

Explorer のディレクトリツリーでフォルダをクリックした際の動作:

```
Root (/)
  └── folder1 (クリック)
        ├── text2.txt, text22.txt          ← folder1 内のファイル
        ├── folder2 (クリック)
        │     ├── text3.txt, text33.txt    ← folder2 内のファイル
        │     ├── folder3
        │     ├── folder4
        │     └── folder5
        ├── folder3
        ├── folder4
        └── folder5
```

**検証結果**:
- `folder1` クリック時: サブディレクトリ 4 個（folder2-5）、ファイル 8 個（text2-5.txt + text22-55.txt）が正しく表示
- `folder2` クリック時: サブディレクトリ 3 個（folder3-5）、ファイル 6 個（text3-5.txt + text33-55.txt）が正しく表示
- 各階層でディレクトリ数・ファイル数が正確に更新される
- パンくずバーに現在のディレクトリパスが表示される

### Explorer からの CSV エクスポート

Explorer ビューは CSV ダウンロードをサポート:
- Explorer ツールバーの**ダウンロードアイコン**（↓）をクリック
- CSV 内容: ファイル/ディレクトリ名、サイズ、アクセス履歴、更新履歴
- **重要** — これは現在表示されているビューの **point-in-time スナップショット** であり、時系列エクスポートではない

> ⚠️ **制限事項**: Explorer CSV は現在表示中の内容のみをキャプチャします。長期的なアクセス履歴分析には、監査ログ（S3 → Athena）を使用してください。推奨アーキテクチャは [管理・監視 Decision Tree](decision-tree-management-monitoring.md) を参照。

### ユースケース

| ユースケース | Explorer の対応 | 制限事項 |
|------------|----------------|---------|
| 非アクティブファイルの特定 | ✅ Access history カラムで最終アクセス日を確認 | `-atime-update` 有効が前提 |
| フォルダ構造の確認 | ✅ 完全なディレクトリツリーナビゲーション | — |
| 部門フォルダごとのファイル数カウント | ✅ 各階層でファイル/ディレクトリ数を表示 | — |
| ファイル一覧のエクスポート | ✅ CSV ダウンロード | point-in-time のみ |
| 長期アクセス傾向分析 | ❌ 非対応 | 監査ログ + Athena を使用 |

---

## 5.2 代替実現方法の比較と導線

System Manager（NetApp Console 経由）で行える各操作について、AWS ネイティブサービスやサードパーティツールで代替する方法を整理します。NetApp Console のセットアップが不要な方法、または GUI に依存しない自動化を検討する際の選択肢です。

### 監査ログの設定と配信

| 実現方法 | ツール/サービス | メリット | 導線（リンク） |
|---------|--------------|---------|-------------|
| System Manager GUI | NetApp Console 経由 | 直感的、運用者に馴染みやすい | 本ガイド Section 2 |
| ONTAP CLI (SSH) | `vserver audit create` | NetApp Console 不要、再現可能 | [前提条件ガイド — 監査ログ有効化](prerequisites.md#step-2-fsx-for-ontap-監査ログの有効化) |
| ONTAP REST API | `POST /api/protocols/audit` | IaC 統合可能、完全自動化 | [ONTAP REST API リファレンス](ontap-rest-api-reference.md) |
| セットアップスクリプト | `ontap-audit-setup.sh` | ドライラン対応、パラメータ化 | [スクリプト](../../shared/scripts/ontap-audit-setup.sh) |

**配信先の選択肢**

| 配信方法 | アーキテクチャ | コスト目安 | 導線 |
|---------|-------------|-----------|------|
| S3 AP + EventBridge + Lambda | AWS ネイティブ（本リポジトリ） | $2-8/月 | [前提条件ガイド](prerequisites.md) |
| AWS DataSync | 定期同期（5分間隔） | $5-15/月 | [AWS DataSync ドキュメント](https://docs.aws.amazon.com/datasync/) |
| Kinesis Data Firehose | 高スループット配信 | ログ量依存 | [ベンダー比較](vendor-comparison.md#architecture-pattern-comparison) |

### クォータ（容量制限）の設定

| 実現方法 | ツール/サービス | メリット | 導線 |
|---------|--------------|---------|------|
| System Manager GUI | NetApp Console 経由 | 直感的な Qtree/クォータ管理 | 本ガイド Section 3 |
| ONTAP CLI | `quota policy rule create` | スクリプト化可能 | [NetApp Docs — Qtree クォータ](https://docs.netapp.com/us-en/ontap/volumes/manage-volumes-task.html) |
| ONTAP REST API | `POST /storage/quota/rules` | IaC 統合、CI/CD パイプライン対応 | [ONTAP REST API リファレンス](ontap-rest-api-reference.md), [quota セットアップスクリプト](../../shared/scripts/ontap-quota-setup.sh) |

### 容量監視と通知

| 実現方法 | 監視粒度 | リアルタイム性 | 導線 |
|---------|---------|-------------|------|
| **CloudWatch Alarms** | ボリュームレベル | 5分間隔 | 本ガイド Section 4.3 |
| **EMS Webhook → Lambda → SNS** | Qtree レベル（クォータ超過） | リアルタイム | 本ガイド Section 4.2, [EMS 検知機能](ems-detection-capabilities.md) |
| **CloudWatch Events (EventBridge)** | 一部 EMS イベント | 数分 | 本ガイド Section 4.4 |
| **NetApp Harvest + Grafana** | 全メトリクス（300+） | 60秒 | [メトリクス収集比較](#10-メトリクス収集ツールの比較) |
| **OTel Collector** | カスタム設定 | 設定次第 | [OTel Collector 統合](../../integrations/otel-collector/) |
| **Datadog / Splunk / Elastic** | 監査ログ＋メトリクス | ベンダー依存 | [ベンダー比較 — ダッシュボード統合](vendor-comparison.md#ダッシュボード統合--各ベンダーでのイベント表示) |

### ファイルアクセス分析（FSA Explorer の代替）

| 実現方法 | 分析対象 | 長期分析 | 導線 |
|---------|---------|---------|------|
| **FSA Explorer (System Manager)** | point-in-time のファイル一覧・サイズ・アクセス日 | ❌ | 本ガイド Section 5.1 |
| **監査ログ + Amazon Athena** | 全アクセス履歴（who/what/when） | ✅ | [管理・監視 Decision Tree](decision-tree-management-monitoring.md) |
| **S3 Inventory (S3 AP 経由)** | オブジェクト一覧・サイズ | ✅ (日次) | [AWS S3 Inventory](https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-inventory.html) |
| **FPolicy + SIEM** | リアルタイムファイル操作追跡 | ✅ | [FPolicy アーキテクチャパターン](fpolicy-production-architecture-patterns.md) |
| **Amazon Comprehend (PII スキャン)** | ファイル内容の分類 | ✅ | [コンテンツ分類スキャナー](content-classification-scanner.md) |

### パフォーマンス監視

| 実現方法 | メトリクス範囲 | カスタムダッシュボード | 導線 |
|---------|-------------|---------|------|
| **System Manager** | ボリューム IOPS/スループット/レイテンシ | ❌（画面のみ） | NetApp Console 経由 |
| **CloudWatch メトリクス** | FSx レベル（ボリューム、FS 全体） | ✅ | [AWS Docs — CloudWatch メトリクス](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-cloudwatch.html) |
| **NetApp Harvest → Prometheus → Grafana** | ONTAP 全メトリクス（300+） | ✅ | [Harvest GitHub](https://github.com/NetApp/harvest) |
| **OTel Collector → 任意バックエンド** | カスタム設定 | ✅ | [OTel Collector 統合](../../integrations/otel-collector/) |

### ランサムウェア対策（ARP + 自動応答）

| 実現方法 | 検知 | 自動封じ込め | 導線 |
|---------|------|-----------|------|
| **ARP/AI + 本リポジトリの自動応答** | ONTAP ARP/AI（即時、学習期間不要） | ✅ Lambda → ONTAP REST API | [自動応答ガイド](automated-response-guide.md) |
| **DII Storage Workload Security** | 内蔵 ML（ユーザー別ベースライン） | ✅ 内蔵 | [自動応答ガイド — 比較表](automated-response-guide.md#比較-本アプローチ-vs-専用ストレージセキュリティ製品) |
| **CloudWatch Log Alarm → SNS → Lambda** | ログパターン検知 | ✅ 本リポジトリ | [CloudWatch Log Alarm ガイド](cloudwatch-log-alarm.md) |
| **Datadog/Splunk ML + SNS** | SIEM 側 ML 検知 | ✅ 本リポジトリ | [ベンダー比較 — 自動封じ込め連携](vendor-comparison.md#自動封じ込め連携パターン全ベンダー共通) |

### 選定フローチャート

```mermaid
flowchart TD
    A[何を実現したいか？] --> B{GUI で操作したい？}
    B -- はい --> C[NetApp Console セットアップ<br/>本ガイド Section 1]
    B -- いいえ --> D{自動化したい？}
    D -- はい --> E{AWS 完結？}
    E -- はい --> F[CloudWatch + Lambda<br/>本リポジトリのテンプレート]
    E -- いいえ --> G[OTel Collector or<br/>Harvest + Grafana]
    D -- いいえ --> H[ONTAP CLI/REST API<br/>直接操作]
```

---

## 6. 検証チェックリスト

### Phase 1: System Manager アクセス確認

- [ ] 管理エンドポイント IP を確認
- [ ] セキュリティグループで 443 ポートが許可されていることを確認
- [ ] ブラウザで `https://<管理IP>` にアクセスできることを確認
- [ ] `fsxadmin` でログインできることを確認
- [ ] ダッシュボードが正常に表示されることを確認

### Phase 2: 監査ログ設定

- [ ] 監査ログ用ボリュームが存在することを確認（なければ作成）
- [ ] Storage VMs → Settings → Audit で監査を有効化
- [ ] EVTX 形式でログが出力されることを確認
- [ ] CIFS 共有へのアクセスがログに記録されることを確認

### Phase 3: Qtree クォータ設定

- [ ] テスト用 Qtree を作成
- [ ] クォータルール（ソフト: 80MB、ハード: 100MB）を設定
- [ ] クォータを初期化
- [ ] ソフトリミット超過時に EMS イベントが発行されることを確認

### Phase 4: 容量監視・通知

- [ ] CloudWatch アラーム（ボリューム容量 80%）を設定
- [ ] EMS Webhook（クォータ超過）を設定
- [ ] SNS トピック + メールサブスクリプションを設定
- [ ] テストデータ書き込みでメール通知が届くことを確認

---

## 7. トラブルシューティング

### System Manager にアクセスできない

| 症状 | 原因 | 対処 |
|------|------|------|
| 接続タイムアウト | セキュリティグループ未設定 | 443 ポートを許可 |
| 証明書エラー | 自己署名証明書 | ブラウザで例外を追加 |
| ログイン失敗 | パスワード不一致 | AWS コンソールからリセット |
| ページが表示されない | ブラウザ互換性 | Chrome/Firefox 最新版を使用 |

### 監査ログが出力されない

| 症状 | 原因 | 対処 |
|------|------|------|
| ログファイルが生成されない | 監査が無効 | `vserver audit show` で確認 |
| アクセスが記録されない | SACL 未設定 | Windows で SACL を設定 |
| ログが古い | ローテーション設定 | ローテーションサイズを確認 |

### クォータが効かない

| 症状 | 原因 | 対処 |
|------|------|------|
| 制限を超えて書き込める | クォータ未初期化 | Initialize Quotas を実行 |
| 使用量が 0 のまま | スキャン未完了 | 数分待つ |
| EMS イベントが出ない | ソフトリミット未設定 | ソフトリミットを設定 |

---

## 8. セキュリティと可用性の考慮事項

### 8.1 NetApp Console<!-- allow:naming --> への AWS 認証情報提供

NetApp Console<!-- allow:naming --> に AWS 認証情報を登録する際のセキュリティモデル:

| 項目 | 内容 |
|------|------|
| **認証方式** | IAM Role の AssumeRole（Datadog AWS Integration と同じ trust model） |
| **NetApp Console<!-- allow:naming --> が取得する情報** | FSx for ONTAP のメタデータ（ファイルシステム ID、容量、SVM 一覧等） |
| **NetApp Console<!-- allow:naming --> が取得しない情報** | ファイルデータ、監査ログの中身、ユーザーデータ |
| **最小権限の推奨** | `fsx:Describe*` + `ec2:Describe*` のみ（読み取り専用） |

> **比較**
>
> Datadog AWS Integration も同様に IAM Role を assume してメトリクスを収集します。NetApp Console<!-- allow:naming --> の trust model はこれと同等です。読み書き権限を付与する場合は影響範囲を理解した上で判断してください。

### 8.2 NetApp Console<!-- allow:naming --> の可用性と依存関係

| コンポーネント | NetApp Console<!-- allow:naming --> 障害時 | 影響 |
|--------------|---------------------|------|
| System Manager GUI | ❌ 利用不可 | GUI での管理操作が不可 |
| ONTAP CLI (SSH) | ✅ 影響なし | 全ての ONTAP 操作が可能 |
| ONTAP REST API | ✅ 影響なし | 自動化・スクリプトは継続 |
| 監査ログ配信パイプライン | ✅ 影響なし | Lambda は S3 AP を直接使用 |
| EMS Webhook | ✅ 影響なし | ONTAP が直接 API Gateway に送信 |

> **結論**
>
> NetApp Console<!-- allow:naming --> は GUI アクセスの便利なレイヤーですが、運用の critical path には含まれません。本プロジェクトの監査ログ配信パイプラインは NetApp Console<!-- allow:naming --> に一切依存しません。

### 8.3 Link Lambda の権限スコープと通信先

Link Lambda は NetApp Console<!-- allow:naming --> と FSx for ONTAP 管理エンドポイント間のブリッジです:

| 項目 | 内容 |
|------|------|
| **通信先** | NetApp Console<!-- allow:naming --> バックエンド + FSx for ONTAP 管理 IP |
| **送信するデータ** | ONTAP REST API のレスポンス（メタデータ、設定情報） |
| **送信しないデータ** | ファイルデータ、監査ログの中身 |
| **暗号化** | 全通信は HTTPS (TLS 1.2+) |

### 8.4 NSS アカウントで共有されるデータ

| 情報 | 用途 | 必須 |
|------|------|------|
| メールアドレス | アカウント認証 | ✅ |
| 氏名・会社名 | アカウント識別 | ✅ |
| FSx File System ID | サービス紐付け | ✅ |

> NSS アカウント作成は無料です。サポートケース起票には有償契約が必要ですが、System Manager 利用には不要です。

---

## 9. GUI 設定とパイプライン配信の接続

System Manager GUI で設定した監査ログが、本プロジェクトの Lambda パイプラインに流れるフロー:

```
設定フェーズ（1回）: System Manager → Audit Enable → /vol/audit_logs
    ↓
運用フェーズ（自動）:
  ONTAP → 監査ログ書き込み → S3 AP → Lambda → ベンダー配信
```

> GUI で監査ログを有効化した後は、CloudFormation テンプレートをデプロイするだけで配信が開始されます。

---

## 10. メトリクス収集ツールの比較

| ツール | 種類 | ONTAP メトリクス範囲 | 配信先 |
|--------|------|-------------------|--------|
| **NetApp Harvest** | NetApp 専用 | 全メトリクス (300+) | Prometheus/Grafana |
| **OTel Collector** | ベンダー中立 | カスタム設定次第 | 任意の OTLP バックエンド |
| **Grafana Alloy** | Grafana ネイティブ | カスタム設定次第 | Grafana Cloud |
| **CloudWatch** | AWS ネイティブ | FSx レベルのみ | CloudWatch |

**選択ガイド**: Grafana 統一 → Harvest、ベンダー中立 → OTel Collector、AWS 完結 → CloudWatch

---

## 11. CLI/REST API: 推奨される自動化パス

| 観点 | GUI (System Manager) | CLI/REST API |
|------|---------------------|-------------|
| 初期設定 | 直感的 | スクリプト化可能、再現性高 |
| 障害時 | NetApp Console<!-- allow:naming --> 依存 | 直接アクセス（依存なし） |
| IaC 統合 | 不可 | CloudFormation 連携可能 |

**推奨**: Day 1 は CLI で初期設定（再現可能）、Day 2+ は GUI で状況確認。

---

## 参考リンク

- [AWS Docs — FSx for ONTAP ファイルアクセス監査](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html)
- [AWS Docs — FSx for ONTAP モニタリング](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring_overview.html)
- [AWS Docs — CloudWatch メトリクス](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-cloudwatch.html)
- [NetApp Docs — ONTAP System Manager](https://docs.netapp.com/us-en/ontap/task_admin_manage_storage_system.html)
- [NetApp Docs — Qtree クォータ管理](https://docs.netapp.com/us-en/ontap/volumes/manage-volumes-task.html)
- [本リポジトリ — EMS イベントソースガイド](event-sources.md)
- [本リポジトリ — 前提条件ガイド](prerequisites.md)
