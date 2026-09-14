# 自動応答 — セキュリティ & インシデント対応補遺

🌐 **日本語**（このページ） | [English](../en/automated-response-security-addendum.md)

## 目的

本補遺は、自動インシデント対応モジュールの高度なセキュリティ考慮事項を、トピック領域ごとに整理したものです。メインの[自動応答ガイド](automated-response-guide.md)を補完し、エンタープライズセキュリティレビュー、コンプライアンス評価、インシデント対応計画に必要な深度を提供します。

---

## 1. ONTAP プロトコル & セキュリティスタイルの考慮事項

### ボリュームセキュリティスタイルが SMB ブロックに与える影響

> ### ⚠️ 撤回: 全セキュリティスタイルで動作するという記述
>
> このページの旧版には「name-mapping ブロック機構は全てのボリュームセキュリティスタイルで
> 動作します」と書かれており、`ntfs` に ✅ を付けた理由として「ONTAP は NTFS ボリュームでも
> 内部追跡のために SID→UNIX 変換を実行」と述べていました。
>
> **この記述には測定の裏付けがなく、根拠として挙げた機構も誤りです。** 静かに書き換えずに
> 撤回として残します。過大な封じ込めの主張が最も高くつくのは応答手順書であり、失敗が無音で、
> かつ誤った方向を指すためです。`unix` ボリュームで技術を検証して動作を確認した人が、同じ
> 手順を `ntfs` ボリュームに適用し、遮断できていないのに遮断したと結論します。
>
> 訂正後の表を以下に示します。来歴は[測定したことと測定していないこと](#測定したことと測定していないこと)
> を参照してください。

NTFS セキュリティスタイルのボリュームは、許可判定に Windows ACL をそのまま使用するため、
win→unix マッピングの結果を参照しません。`unix` と `mixed` は参照するので、同じ操作が
そちらでは効果を持ちます。

| ボリュームセキュリティスタイル | SMB ブロック有効？ | 理由 |
|---------------------------|------------------|------|
| `unix` | ✅ はい | 許可判定が name-mapping 経由で UNIX ID を解決するため、deny マッピングは解決を失敗させる |
| `mixed` | ✅ はい | ファイルの実効スタイルが UNIX の場合は `unix` と同じ経路 |
| `ntfs` | ❌ **いいえ** | Windows ACL がそのまま使われる。win→unix マッピングの結果は参照されないので、deny マッピングはアクセスを拒否しない |

NTFS の挙動の出典: NetApp KB [How does name-mapping work when CIFS clients access NTFS
security style
resources](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/How_does_name-mapping_work_when_CIFS_clients_access_NTFS_security_style_resources)
および、同じ結論に至った 2 つの独立したリポジトリの記録。

> **`mixed` セキュリティスタイルについて**
>
> `mixed` は新規デプロイでは推奨されません。NetApp のベストプラクティスは `ntfs`
> （Windows 専用ワークロード）または `unix`（Linux/NFS 主体ワークロード）を明示的に選択する
> ことです。ここに記載しているのは、ブロック機構の挙動がスタイルごとに異なるためで、省くと
> 読者が推測することになります。

### ボリュームが NTFS セキュリティスタイルの場合の代替手段

`block_smb_user` は成功を報告します。name-mapping は作成され、API は `201 Created` を
返します。**このレスポンスはマッピングが存在することを示すもので、アクセスが拒否された
ことを示すものではありません。** NTFS スタイルのボリュームでは拒否されません。

代わりに、または併せて次を使用してください。

| 手段 | 機構 | 補足 |
|------|------|------|
| AD アカウントの無効化 | Active Directory（ONTAP の外） | 当該 SVM に限らず、そのユーザーの新規認証を全体で停止する。出典あり: 無効化またはロックされたアカウントは `Kerberos Error: Clients credentials have been revoked` を発生させ、**name-mapping やセキュリティスタイルに関係なく** NTFS アクセスをブロックする — [NetApp KB](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/Denied_access_to_NTFS_volume_because_AD_Account_is_locked_or_disabled_or_expired)、[デプロイガイド](deployment-guide.md) で既に引用済み |
| CIFS セッションの切断 | `ontap_response.py` の `disconnect_smb_sessions` | 現在のアクセスを終わらせる。認証も止めない限り再接続は可能 |
| NTFS ACL の変更 | 共有またはディレクトリの Windows ACL | NTFS スタイルのボリュームが実際に参照する経路に作用する |
| ネットワーク層での制限 | Security Group / NACL | ID ではなくクライアントアドレスを遮断する |

### 測定したことと測定していないこと

ここでは結論よりも区分が重要なので、まとめずに項目ごとに示します。

| 主張 | 状態 |
|------|------|
| name-mapping API 呼び出しが成功し、エントリが作成される | **測定済み。** ONTAP 9.17.1P7D1、2026-07-08: `block_smb_user (direct API) — PASS — 201 Created — index:99, CORP\testuser99`。[E2E 検証結果](../screenshots/automated-response/e2e-verification-results.md) を参照 |
| AD 参加 SVM で `replacement: "nobody"` の deny マッピングが残存する（`" "` は 30-60 秒で自動削除される） | **測定済み。** ONTAP 9.17.1P7D1、2026 年 7 月。[自動応答ガイド](automated-response-guide.md) を参照 |
| `unix` スタイルのボリュームで SMB アクセスが実際に拒否される | **本リポジトリでは未測定。** 文書化された機構および外部の記録と整合する |
| `ntfs` スタイルのボリュームで SMB アクセスが実際に拒否される | **他所で偽と測定済み。本リポジトリでは未測定。** 撤回した記述はこの逆を主張していた |

**本リポジトリのテストは、ブロック後に SMB アクセスを試行したことが一度もありません。**
E2E の記録は API 呼び出しを検証したもので、`201 Created` が封じ込めとして読まれていました。
当時の環境のボリュームセキュリティスタイルは記録されていないため、`unix` の場合についても
ローカルな証拠はありません。

> **自分で測定するときの罠**: 非管理者アカウントで試験してください。administrators
> グループのメンバーは許可判定を迂回するため、`unix` で「ブロックが効かない」という偽の結果が
> 出ます。逆に `ntfs` で観測した拒否は、マッピングではなく ACL に由来している可能性が
> あります。したがって、成功するはずのコントロールを同一セッションに置かないと、結果は
> 挙動ではなく手順を記録することになります。

### NFS 認証方式が IP ブロックに与える影響

| NFS 認証方式 | IP ベースブロック有効？ | 備考 |
|-------------|---------------------|------|
| AUTH_SYS（最も一般的） | ✅ はい | クライアントは IP で識別; export-policy ルールは IP でブロック |
| Kerberos (krb5/krb5i/krb5p) | ⚠️ 部分的 | クライアントはプリンシパルで認証; 別 IP からの同一プリンシパルは IP ブロックを迂回 |
| AUTH_NONE | ✅ はい | クライアントは IP で識別 |

Kerberos NFS 環境では、IP ブロックに加えて以下を併用:
- ネットワークレベル制御（Security Group、NACL）
- Kerberos プリンシパルの失効（AD アカウント無効化）
- Kerberos 認証クライアントの IP レンジをカバーする export-policy ルール

### マルチプロトコル脅威封じ込め

NFS と SMB の両方でアクセスされるボリュームの場合、両方の封じ込めアクションを実行:

```json
// Message 1: Block SMB
{"action": "contain_smb_threat", "svm_name": "svm-prod", "domain": "CORP", "username": "jdoe", "volume_name": "vol_data"}

// Message 2: Block NFS (same user's workstation IP)
{"action": "contain_nfs_threat", "svm_name": "svm-prod", "client_ip": "203.0.113.99", "volume_name": "vol_data"}
```

> **より簡潔な代替手段**
>
> `contain_multiprotocol_threat` 複合アクションは、上記の 2 メッセージの代わりに、両方のブロック（+ Snapshot + セッション切断）を単一メッセージで実行します。詳細は [自動応答ガイド](automated-response-guide.md)の「複合アクション（マルチステップ）」表を参照。

---

## 2. 証拠保全 & フォレンジック

### Snapshot 改ざん防止

通常の Snapshot はボリューム管理者が削除可能。フォレンジックグレードの証拠保全:

| 保護レベル | メカニズム | ONTAP バージョン | 推奨 |
|-----------|-----------|----------------|------|
| 基本 | 通常の Snapshot（現在の実装） | 全バージョン | 運用対応として許容 |
| 強化 | Snapshot ロック（`snapshot lock create`） | 9.12.1+ | 規制対象環境で推奨 |
| 最高 | SnapLock Compliance ボリューム | 9.10.1+ | 法的ホールドシナリオで必須 |

> **FSx for ONTAP**
>
> Snapshot ロックはサポートされています。保護 Snapshot 作成後、必要に応じてロックしてください:
> ```
> POST /api/storage/volumes/{uuid}/snapshots/{snapshot_uuid}
> Body: {"retention_period": "P30D"}  // ISO 8601 duration — 30 日間ロック
> ```

### Chain of Custody 要件 (DFIR)

フォレンジックとして有効な証拠にするには、応答アクションで以下を記録すべきです:

| 要素 | 現在の状況 | 強化パス |
|------|-----------|---------|
| トリガー元（誰が要求したか） | ✅ CloudWatch Logs に記録（SNS メッセージ本文） | — |
| 正確なタイムスタンプ（UTC） | ✅ CloudWatch Logs + Snapshot メタデータに記録 | — |
| 実行前の状態 | ⚠️ 未取得 | 将来対応: 変更前に name-mapping/export-policy をクエリして記録 |
| 実行後の状態 | ✅ API 応答で作成を確認 | — |
| Lambda 実行 ID | ✅ CloudWatch Logs に記録（requestId） | — |
| トリガーメッセージのハッシュ値 | ❌ 未実装 | 将来対応: SNS メッセージの SHA-256 をログエントリに記録 |

### Snapshot 保持ポリシー

持続的インシデントで複数 Snapshot が生成される場合:
- `incident_response_*` Snapshot を 30 日間保持（設定可能）
- 30 日後: AWS Backup に移行（長期）または削除
- Snapshot スペース消費を CloudWatch メトリクスで監視
- インシデント Snapshot がボリューム容量の 10% を超えたらアラート

---

## 3. FPolicy: 予防的制御 vs 事後的制御

| 側面 | FPolicy Passthrough（本プロジェクト） | FPolicy Mandatory | 自動応答（本モジュール） |
|------|--------------------------------------|-------------------|------------------------|
| タイミング | 操作完了後 | 操作完了前 | 検知 + 分析後 |
| レイテンシ | 0ms（ログのみ） | +1-5ms/ファイル操作 | ~65 秒（検知 + 応答） |
| 被害防止可能？ | ❌ 不可 | ✅ 可（操作を拒否） | ❌ 不可（将来の操作をブロック） |
| 誤検知の影響 | なし（ログのみ） | 高（正当なファイルをブロック） | 中（ユーザー全体をブロック） |

> **多層防御**
>
> FPolicy mandatory モードは既知の悪パターン（`.encrypted` ファイル作成のブロック）に、自動応答は行動異常（大量削除パターン検知後のユーザーブロック）に使用。

---

## 4. 内部脅威 & システム完全性

### 侵害された管理者への対策

| 侵害されたコンポーネント | 影響 | 対策 |
|-------------------------|------|------|
| ONTAP fsxadmin 認証情報 | 自己ブロック解除、Snapshot 削除可能 | ONTAP RBAC: `response_blocker` ロール（書込みのみ、削除不可）を作成 |
| AWS Secrets Manager | 上記と同様 | IAM ポリシー: Lambda 実行ロールのみに Secrets Manager アクセスを制限 |
| SNS トリガートピック | unblock メッセージ送信可能 | SNS アクセスポリシー: `unblock_*` アクションを特定 IAM プリンシパルに制限 |
| Lambda コード / CloudFormation | 応答ロジック改変可能 | CloudTrail アラート: スタック/関数変更を検知 |
| CloudWatch Logs | 監査証跡削除可能 | ログを S3 + Object Lock (WORM) にエクスポート |

### 権限分離パターン

高セキュリティ環境では、ブロックとアンブロックの認証情報を分離:

```
ブロック Lambda: ONTAP ユーザー "response_blocker"
  - 権限: name-mapping 作成、export-policy rule 作成、snapshot 作成
  - 不可: name-mapping 削除、export-policy rule 削除

アンブロック Lambda: ONTAP ユーザー "response_admin"（別シークレット）
  - 権限: name-mapping 削除、export-policy rule 削除
  - 追加要件: IAM ゲート（承認ワークフロー付き別 SNS トピック）
```

### SNS トピックのアクセスポリシー（アンブロックを制限）

```json
{
  "Statement": [{
    "Effect": "Deny",
    "Principal": "*",
    "Action": "SNS:Publish",
    "Resource": "<trigger-topic-arn>",
    "Condition": {
      "StringLike": {"aws:PrincipalArn": "arn:aws:iam::*:role/non-security-*"},
      "ForAnyValue:StringEquals": {"sns:MessageBody": ["unblock_smb_user", "unblock_nfs_ip"]}
    }
  }]
}
```

> 注意: メッセージ本文に対する SNS の条件指定は独自の検証が必要です。厳格に制御する
> 場合は、アンブロック操作用に別の SNS トピックを用意してください。

---

## 5. DR & レプリケーションの考慮事項

### SnapMirror レプリケーション動作

| 項目 | DR に複製される？ | 影響 |
|------|-----------------|------|
| Name-mapping エントリ | ❌ いいえ | ブロックユーザーがフェイルオーバー後にアクセス回復 |
| Export-policy ルール | ✅ はい | IP ブロックはフェイルオーバー後も維持 |
| インシデント対応 Snapshot | ✅ はい（SnapMirror スコープ内の場合） | DR サイトで証拠保全 |
| ARP 状態 | ❌ いいえ（DR でリセット） | DR サイトで学習期間必要 |

**DR 対応ブロック手順**:
1. プライマリ SVM でブロック（通常運用）
2. DR SVM でもブロック（DR エンドポイントに 2 番目のメッセージを publish）
3. フェイルオーバー後、現プライマリ（DR SVM）でブロックがアクティブか確認

---

## 6. コンテナ & 動的環境

### Kubernetes / ECS の考慮事項

IP ベースの NFS ブロックはコンテナ環境では効果が限定的:
- Pod IP は一時的（新しい Pod = 新しい IP = ブロック迂回）
- 複数の Pod が NFS トラフィックで同じノード IP を共有（ノードブロック = 全 Pod ブロック）

| アプローチ | 有効性 | 複雑性 |
|-----------|--------|--------|
| ノード IP レンジ（サブネット）のブロック | ✅ 高 | 低 |
| PersistentVolume アクセスモード制限 | ✅ 高（K8s レベルでアンマウント） | 中 |
| NetworkPolicy (K8s) | ✅ 高（Pod→FSx 通信をブロック） | 中 |
| ServiceAccount → ONTAP ユーザーマッピング | ✅ 精密 | 高 |

---

## 7. ガバナンス & コンプライアンス

### FISC ガイドライン（日本金融業界）

| FISC 要件 | 本システムでの対応 |
|-----------|-----------------|
| 自動応答ポリシーの事前承認 | ポリシーを文書化、有効化前にセキュリティ責任者の承認を取得 |
| 応答ルールの年次レビュー | `PROTECTED_ACCOUNTS`、検知閾値、TTL 設定を年次でレビュー |
| 応答メカニズムの四半期テスト | `health_check` アクション + テスト SVM での安全なブロック/解除サイクル |
| 文書化されたエスカレーションパス | `pagerduty-escalation-guide.md` + 通知トピック → オンコール |

### GDPR 第 22 条の考慮事項

自動ブロックは個人の業務遂行能力に影響を与えます。GDPR 第 22 条:
- 重大な影響を持つ自動決定には人間のレビューが必要
- **推奨** — EU の従業員に対しては、自動ブロック後 1 時間以内に人間のレビューを必須化
- TTL 自動解除（デフォルト: 60 分）がセーフティネットとして機能
- 自動ブロックの合法的根拠（正当利益: データ侵害防止）を文書化

### SOC2 CC7.3 — アクション後のレビュー

全自動応答アクションについて:
1. ✅ 正当な検知に基づくトリガー（CloudWatch Logs の監査証跡）
2. ⚠️ 脅威に比例した対応（設定可能 — severity フィールドを使用）
3. ⚠️ 資格のある担当者による事後レビュー（プロセスが必要）
4. ✅ 不要になった時点で解除（TTL 自動解除または手動解除）

---

## 8. SOAR プラットフォーム連携

### SOAR プレイブックで利用可能な操作

| 操作 | SNS アクション | 返却値 | SOAR での用途 |
|------|-------------|--------|-------------|
| ユーザーブロック | `block_smb_user` | 確認 | 応答ステップ |
| IP ブロック | `block_nfs_ip` | 確認 | 応答ステップ |
| 全面封じ込め | `contain_smb_threat` | マルチステップ結果 | 複合応答 |
| 状態確認 | `list_active_blocks` | 現在のブロック一覧 | エンリッチメントステップ |
| ヘルスチェック | `health_check` | 接続状態 | ヘルス監視 |
| ブロック解除 | `unblock_smb_user` | 確認 | 復旧ステップ |

### べき等性

- `block_smb_user`: べき等でない（2 回呼ぶと重複エントリ作成）。SOAR は `list_active_blocks` で事前確認すべき
- `block_nfs_ip`: べき等でない。事前確認が必要
- `create_snapshot`: クールダウンによりべき等（最近の Snapshot があればスキップ）
- `health_check`: べき等（読取専用）
- `list_active_blocks`: べき等（読取専用）

---

## 9. クラウド非依存での利用

`OntapResponseClient` Python クラスはクラウド非依存です。必要なのは:
- ONTAP 管理 IP へのネットワークアクセス（TCP 443）
- ONTAP 管理者認証情報
- Python 3.9+ と `urllib3`

以下から呼び出し可能:
- AWS Lambda（本プロジェクトのデプロイパターン）
- Azure Functions
- GCP Cloud Functions
- オンプレミスの Python スクリプト
- SOAR プラットフォームカスタム統合（XSOAR、Splunk SOAR）
- CI/CD パイプライン（GitLab CI、GitHub Actions）

SNS/Lambda/CloudFormation ラッピングは AWS ネイティブのデプロイパターンであり、コア封じ込めロジックはポータブルです。

---

## 10. レート制限 & スケーラビリティ

### ONTAP 管理プレーンの制限

| メトリクス | 概算制限 | 影響 |
|-----------|---------|------|
| REST API リクエスト/秒 | ~100 req/s (FSx for ONTAP) | マスブロック（20+ ユーザー）にはキューイングが必要 |
| 同時 CIFS セッション | 数千 | 大規模セッション切断は順次実行 |
| SVM あたりの name-mapping エントリ | 256 | 同時 SMB ブロック数の上限 |
| ポリシーあたりの export-policy ルール | 1024 | ほとんどのシナリオで十分 |

**マスブロックシナリオ**: 検知と応答 Lambda の間に SQS バッファリングを実装し、ONTAP API 呼び出しをレート制限。

---

## 関連ドキュメント

- [自動応答ガイド](automated-response-guide.md) — メインデプロイ・利用ガイド
- [ARP インシデント対応ガイド](arp-incident-response-guide.md) — ARP 固有手順
- [EMS 検知機能リファレンス](ems-detection-capabilities.md) — 検知ソースリファレンス
- [デモ手順書](demo-automated-response.md) — ステップバイステップ検証
- [コンプライアンスエビデンスパック](compliance-evidence-pack.md) — 監査エビデンステンプレート
- [PagerDuty エスカレーションガイド](pagerduty-escalation-guide.md) — 通知チェーン
