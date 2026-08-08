# 自動応答デモ手順書

🌐 **日本語**（このページ） | [English](../en/demo-automated-response.md)

## 目的

自動インシデント対応機能のエンドツーエンドデモ手順書。カバー範囲: デプロイ → 検知トリガー → 自動ブロック確認 → アクセス拒否確認 → ブロック解除 → アクセス復元確認。

用途:
- 対外デモ（ライブまたは録画）
- ブログ公開前の E2E 検証
- 内部トレーニング

> **エビデンス形式に関する注記**
>
> 本手順書は、各ステップの後に「何を確認すべきか」を平文で記述しており、スクリーンショットのプレースホルダーや架空のサンプル出力は使用していません。本稿執筆時点で、この手順書自体はエンドツーエンドで実行されておらず、実際のスクリーンショットやコマンド出力も一切キャプチャされていません — 本ガイド内のいずれの記述も、これらの手順が実際に実行された証拠として扱わないでください。実際に本手順書を実行する際は、実際のコマンド出力やスクリーンショットを取得し（アカウントID/IP/ARN は `docs/screenshots/mask_screenshots.py` でマスキングしてから）、[自動応答ガイド](automated-response-guide.md)自身の [`e2e-verification-results.md`](../screenshots/automated-response/e2e-verification-results.md) と同じ形式で記録することを推奨します。

---

## 前提条件

| 項目 | 要件 |
|------|------|
| FSx for ONTAP | 稼働中、少なくとも 1 ボリュームで ARP 有効 |
| VPC アクセス | Lambda サブネットが ONTAP 管理 IP に到達可能（TCP 443） |
| ONTAP 認証情報 | `fsxadmin` ユーザー名/パスワードが Secrets Manager に保存済み |
| SMB クライアント | CIFS 経由で SVM にマウント済みの Windows または Linux ホスト |
| NFS クライアント | NFS 経由で SVM にマウント済みの Linux ホスト |
| AWS CLI | 適切な IAM 権限で設定済み |
| jq | JSON フォーマット用にインストール済み |

---

## Phase 1: 自動応答スタックのデプロイ

### ステップ 1.1: CloudFormation デプロイ

```bash
aws cloudformation deploy \
  --template-file shared/templates/automated-response.yaml \
  --stack-name fsxn-automated-response \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=<secret-arn> \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-1>,<subnet-2> \
    SecurityGroupId=<sg-id> \
    DefaultSvmName=<svm-name> \
    NotificationEmail=<your-email> \
  --capabilities CAPABILITY_NAMED_IAM
```

### ステップ 1.2: スタック出力の確認

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-automated-response \
  --query 'Stacks[0].Outputs' \
  --output table
```

**確認ポイント**: 出力テーブルに `TriggerTopicArn` と `NotificationTopicArn` の両方のキーが含まれ、それぞれに空でない SNS トピック ARN が設定されていること。

### ステップ 1.3: CLI 環境変数の設定

```bash
export RESPONSE_TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-automated-response \
  --query 'Stacks[0].Outputs[?OutputKey==`TriggerTopicArn`].OutputValue' \
  --output text)

export DEFAULT_SVM="<svm-name>"
echo "Topic: $RESPONSE_TOPIC_ARN"
```

---

## Phase 2: SMB ユーザーブロックのデモ

### ステップ 2.1: 現在のアクセス確認（ブロック前）

SMB クライアントから正常アクセスを確認:

```bash
# SMB クライアント上（テストユーザーとして）
ls //fsxn-share/test-data/
cat //fsxn-share/test-data/sample-file.txt
echo "write test" > //fsxn-share/test-data/write-test.txt
```

**確認ポイント**: 3 つのコマンド全てが権限エラーなく成功すること — `ls` は想定通りのファイルを一覧表示し、`cat` はファイル内容を表示し、`write-test.txt` が作成されること。

### ステップ 2.2: SMB ユーザーブロックのトリガー

```bash
./shared/scripts/automated-response-cli.sh block-smb \
  --domain <DOMAIN> --user <test-user> \
  --reason "デモ: 内部脅威シミュレーション"
```

**確認ポイント**: CLI ヘルパーまたは `aws sns publish` が成功し、`MessageId` フィールドに空でない値（UUID 形式の文字列）が返ること。

### ステップ 2.3: Lambda 実行の確認

```bash
sleep 15

aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-automated-response-handler \
  --start-time $(date -v-2M +%s000 2>/dev/null || date -d '2 minutes ago' +%s000) \
  --filter-pattern "block_smb_user" \
  --query 'events[*].message' \
  --output text | tail -5
```

**確認ポイント**: 対象ユーザーを対象 SVM 上でブロックした旨を示すログ行があり、指定した理由の文字列が含まれ、同一実行内の他のログ行と対応付けられる `RequestId` があること。

### ステップ 2.4: ONTAP でのブロック確認

```bash
ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
```

**確認ポイント**: `win-unix` 方向の `name-mapping` エントリが存在し、`DOMAIN\<test-user>` に一致し、置換先が空（`" "`）になっていること — これが Lambda が作成する拒否マッピングです。

### ステップ 2.5: アクセス拒否の確認（ブロック後）

```bash
# SMB クライアント上（ブロックされたテストユーザーとして）
ls //fsxn-share/test-data/
# → 期待: Permission denied / Access denied
```

**確認ポイント**: ステップ 2.1 では成功していたコマンドが、権限拒否系のエラーで失敗すること（正確な文言は SMB クライアントの OS により異なります）。

### ステップ 2.6: ユーザーのブロック解除

```bash
./shared/scripts/automated-response-cli.sh unblock-smb \
  --domain <DOMAIN> --user <test-user>
```

### ステップ 2.7: アクセス復元の確認

```bash
# SMB クライアント上（再接続が必要な場合あり）
ls //fsxn-share/test-data/
# → 期待: 成功
```

**確認ポイント**: `ls` が再度成功し、ブロック解除によってアクセスが復元されたことを確認できること。

---

## Phase 3: NFS IP ブロックのデモ

### ステップ 3.1: 現在の NFS アクセス確認

```bash
ls /mnt/fsxn/test-data/
touch /mnt/fsxn/test-data/nfs-write-test.txt
```

**確認ポイント**: 両方のコマンドが権限エラーなく成功すること。

### ステップ 3.2: NFS IP ブロックのトリガー

```bash
CLIENT_IP=$(hostname -I | awk '{print $1}')
echo "Blocking IP: $CLIENT_IP"

./shared/scripts/automated-response-cli.sh block-nfs \
  --ip "$CLIENT_IP" \
  --reason "デモ: 不審 IP からの大量削除シミュレーション"
```

**確認ポイント**: ステップ 2.2 と同様に、publish が `MessageId` を返して成功すること。

### ステップ 3.3: ONTAP でのブロック確認

```bash
ssh fsxadmin@<management-ip> "export-policy rule show -clientmatch *fsxn_auto_response*"
```

**確認ポイント**: `clientmatch` にブロック対象の IP を含み、`fsxn_auto_response` マーカーが付いた export-policy ルールが存在し、読み書きアクセスが拒否されていること。

### ステップ 3.4: NFS アクセス拒否の確認

```bash
# NFS クライアント上（再マウントが必要な場合あり）
umount /mnt/fsxn && mount -t nfs <svm-nfs-lif>:/vol_data /mnt/fsxn
ls /mnt/fsxn/test-data/
# → 期待: Permission denied またはマウント失敗
```

> **NFS キャッシュに関する注記**
>
> Linux NFS クライアントはアクセス判定を最大 60 秒間キャッシュします（`actimeo` デフォルト値）。ブロック後、拒否が即座に効くまで最大 60 秒待つか、テスト時は `mount -o actimeo=0` で再マウントしてください。

**確認ポイント**: 再マウントが失敗するか、マウント上での `ls` が権限拒否系のエラーで失敗すること — ブロック直後にアクセスが成功して見える場合は、上記の NFS キャッシュに関する注記を参照してください。

### ステップ 3.5: IP ブロック解除

```bash
./shared/scripts/automated-response-cli.sh unblock-nfs --ip "$CLIENT_IP"
```

---

## Phase 4: 全面封じ込めのデモ（ARP → 自動ブロック）

### ステップ 4.1: ARP アクティブ状態の確認

```bash
ssh fsxadmin@<management-ip> "security anti-ransomware volume show -vserver $DEFAULT_SVM"
```

**確認ポイント**: 対象ボリュームの ARP 状態がコマンド出力で active/enabled と表示されること。

### ステップ 4.2: EMS Webhook をレスポンスパイプラインに接続

EMS Webhook が Datadog/SIEM に配信されていること、および SIEM モニターがレスポンス用 SNS トピックに publish できることを確認します。CloudWatch Log Alarm パスを使用している場合:

```bash
# syslog 配信がアクティブであることを確認
aws logs filter-log-events \
  --log-group-name /syslog/fsxn-admin-audit \
  --start-time $(date -v-5M +%s000 2>/dev/null || date -d '5 minutes ago' +%s000) \
  --limit 3 \
  --query 'events[*].message' --output text
```

### ステップ 4.3: ランサムウェアシミュレーション（テスト環境のみ）

```bash
# 注意: テスト環境の廃棄可能データでのみ実行
ssh fsxadmin@<management-ip> \
  "security anti-ransomware volume attack simulate -vserver $DEFAULT_SVM -volume <test-vol>"
```

**確認ポイント**: コマンドがエラーなく完了し、続けて `security anti-ransomware volume show` を実行すると対象ボリュームでシミュレートされた攻撃状態が反映されていること。

### ステップ 4.4: 検知チェーンの観察

EMS イベント配信まで ~30 秒待機:

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-datadog-ems-fpolicy-handler \
  --start-time $(date -v-2M +%s000 2>/dev/null || date -d '2 minutes ago' +%s000) \
  --filter-pattern "arw.volume.state" \
  --query 'events[*].message' --output text
```

**確認ポイント**: 前のステップのシミュレーションコマンドから約 30 秒後に、`arw.volume.state` EMS イベントと対象ボリューム名を参照するログ行が届くこと。

### ステップ 4.5: 全面封じ込めトリガー

```bash
./shared/scripts/automated-response-cli.sh contain-smb \
  --domain <DOMAIN> --user <suspect-user> \
  --volume <test-vol> \
  --reason "ARP 検知 - arw.volume.state alert"
```

### ステップ 4.6: 封じ込め結果の確認

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-automated-response-handler \
  --start-time $(date -v-2M +%s000 2>/dev/null || date -d '2 minutes ago' +%s000) \
  --filter-pattern "contain_smb_threat" \
  --query 'events[*].message' --output text
```

**確認ポイント**: Snapshot 作成・ユーザーブロック・セッション切断の 3 つの封じ込めステップ全てのログ行があり、同一の `RequestId` を共有していること — これにより、単一の `contain_smb_threat` 呼び出し内で実行されたことが確認できます。

### ステップ 4.7: Snapshot 作成の確認

```bash
ssh fsxadmin@<management-ip> \
  "volume snapshot show -vserver $DEFAULT_SVM -volume <test-vol> -snapshot incident_response_*"
```

**確認ポイント**: `incident_response_*` という名前の Snapshot が表示され、そのタイムスタンプが封じ込めがトリガーされた時刻と一致すること。

### ステップ 4.8: SNS 通知の受信確認

`NotificationEmail` の受信箱で、封じ込め結果の JSON を確認してください。

**確認ポイント**: 封じ込め結果（Snapshot 名、ブロックしたユーザー、切断したセッション）を含む JSON がメールで届いていること。メールをエビデンスとして使いたくない場合は、Lambda 自身が同じ JSON ペイロードをログに出力しているので、ステップ 4.6 と同じ `filter-log-events` パターンで確認できます。加えて `aws sns get-topic-attributes --topic-arn <NotificationTopicArn> --query 'Attributes.NumberOfNotificationsFailed'` で配信失敗がないことを別途確認してください。

---

## Phase 5: TTL 自動解除のデモ

### ステップ 5.1: TTL スタックのデプロイ

```bash
aws cloudformation deploy \
  --template-file shared/templates/automated-response-ttl.yaml \
  --stack-name fsxn-automated-response-ttl \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=<secret-arn> \
    SubnetIds=<subnet-1>,<subnet-2> \
    SecurityGroupId=<sg-id> \
    DefaultSvmName=$DEFAULT_SVM \
    BlockTtlMinutes=5 \
    CheckIntervalMinutes=1 \
    NotificationTopicArn=<notification-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM
```

### ステップ 5.2: ブロック作成

```bash
./shared/scripts/automated-response-cli.sh block-smb \
  --domain <DOMAIN> --user <test-user> \
  --reason "TTL デモ - 5 分後に自動解除"
```

### ステップ 5.3: TTL 失効待ち

```bash
echo "ブロック作成完了。TTL クリーンアップ Lambda は 1 分ごとに実行。"
echo "ブロックは約 5 分以内に自動解除されます。"
echo "CloudWatch Logs を監視（7 分でタイムアウト）..."

# クリーンアップ Lambda を監視（無限に待たないようタイムアウト付き）
timeout 420 aws logs tail /aws/lambda/fsxn-automated-response-ttl-cleanup --follow
# タイムアウトしても削除メッセージが見えない場合、Lambda エラーを確認:
# aws logs filter-log-events --log-group-name /aws/lambda/fsxn-automated-response-ttl-cleanup --filter-pattern "ERROR"
```

> **注記**
>
> TTL クリーンアップは現在、各実行時に `fsxn_auto_response` マーカーを持つ全ブロックを削除します。個別のブロック作成時刻は追跡しません。本番環境では DynamoDB による追跡テーブルの実装を検討してください。

**確認ポイント**: TTL クリーンアップ Lambda が失効を検知し、SMB ブロックを削除した旨の CloudWatch Logs エントリがあること。

### ステップ 5.4: 自動解除の確認

```bash
ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
# → 期待: エントリなし（ブロックが TTL により自動削除）
```

**確認ポイント**: ステップ 2.4 で作成した name-mapping エントリが消えており、TTL クリーンアップが自動的に削除したことを確認できること。

---

## Phase 6: アクティブブロック一覧（運用可視化）

```bash
ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
ssh fsxadmin@<management-ip> "export-policy rule show -clientmatch *fsxn_auto_response*"
```

**確認ポイント**: 両方のコマンドで現在アクティブなブロックの状態が確認できること（それまでのフェーズでブロック解除済みであれば空になっているはず）。

---

## 所要時間の目安

| フェーズ | 所要時間 | 備考 |
|---------|---------|------|
| Phase 1（デプロイ） | ~5 分 | CloudFormation デプロイ |
| Phase 2（SMB ブロック） | ~3 分 | ブロック + 確認 + 解除 |
| Phase 3（NFS ブロック） | ~3 分 | ブロック + 確認 + 解除 |
| Phase 4（全面封じ込め） | ~5 分 | ARP シミュレート + 封じ込め |
| Phase 5（TTL） | ~7 分 | デプロイ + TTL 失効待ち |
| Phase 6（運用確認） | ~1 分 | 状態チェック |
| **合計** | **~24 分** | 全フェーズ実行 |

短縮版デモ（対外ミーティング用）: Phase 1 + Phase 2 + Phase 4 = ~13 分。

---

## クリーンアップ

```bash
# テストブロックの全削除
ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
# 残っているエントリを手動削除

# テスト Snapshot の削除
ssh fsxadmin@<management-ip> "volume snapshot delete -vserver $DEFAULT_SVM -volume <test-vol> -snapshot incident_response_*"

# CloudFormation スタック削除（オプション）
aws cloudformation delete-stack --stack-name fsxn-automated-response-ttl
aws cloudformation delete-stack --stack-name fsxn-automated-response
```

---

## E2E 検証済み出力（2026年7月、ONTAP 9.17.1P7D1）

以下は実機 E2E 検証で取得した出力です。自身のデプロイ検証の参考にしてください。

### NFS ブロック: Before/After

**Before（アクセス許可）**:
```
$ ls -la /mnt/fsxn/
total 12
-rw-r--r--. 1 root root   46 Jul 12 16:00 hr-salary.txt
-rw-r--r--. 1 root root   43 Jul 12 16:00 project-spec.txt
drwxr-xr-x. 2 root root 4096 Jul 12 16:00 reports

$ cat /mnt/fsxn/hr-salary.txt
Confidential HR Record - Employee Salary Data
```

**After（export-policy deny rule 適用後）**:
```
$ ls /mnt/fsxn/
ls: cannot access '/mnt/fsxn/': Permission denied

$ cat /mnt/fsxn/hr-salary.txt
cat: /mnt/fsxn/hr-salary.txt: Permission denied
```

### SMB ブロック: Before/After（AD参加SVM, testuser）

**Before（アクセス許可）**:
```
PS> net use X: \\SVM\data /user:DEMO\testuser TestP@ss2026!
The command completed successfully.

PS> Get-ChildItem X:\
Mode   Length Name
----   ------ ----
d-----        reports
-a---- 46     hr-salary.txt
-a---- 43     project-spec.txt

PS> Get-Content X:\hr-salary.txt
Confidential HR Record - Employee Salary Data
```

**After（block_smb_user 実行 → nobody mapping + 750 パーミッション）**:
```
PS> net use X: \\SVM\data /user:DEMO\testuser TestP@ss2026!
The command completed successfully.

PS> Test-Path X:\
False

[Result] ACCESS DENIED - Drive not accessible
```

### スクリーンショット撮影ポイント

プレゼンテーションやブログ用にデモを実行する際、以下のタイミングでスクリーンショットを撮影してください:

| # | タイミング | 撮影対象 | ファイル名 |
|---|----------|---------|----------|
| 1 | Phase 2 Before | Windows ファイルエクスプローラーで共有ファイルが見える状態 | `smb-access-granted.png` |
| 2 | Phase 2 After | Windows のアクセス拒否ダイアログまたは空のドライブ | `smb-access-denied.png` |
| 3 | Phase 3 Before | ターミナルで `ls /mnt/fsxn/` がファイル一覧を表示 | `nfs-access-granted.png` |
| 4 | Phase 3 After | ターミナルで `Permission denied` エラー | `nfs-access-denied.png` |
| 5 | Phase 4 | CloudWatch Logs の Lambda 実行ログ | `lambda-execution-log.png` |
| 6 | オプション | Step Functions グラフビュー（restore-verification 実行時） | `stepfunctions-graph.png` |
| 7 | オプション | Datadog/CloudWatch での ARP 検知表示 | `detection-alert.png` |

**マスキング**: スクリーンショットをコミットする前に実行:
```bash
python3 docs/screenshots/mask_screenshots.py
```

---

## 関連ドキュメント

- [自動応答ガイド](automated-response-guide.md)
- [デプロイメントガイド](deployment-guide.md) — VPC Endpoint 競合、AD 連携、パラメータファイル
- [ARP インシデント対応ガイド](arp-incident-response-guide.md)
- [EMS 検知機能リファレンス](ems-detection-capabilities.md)
- [デモシナリオ（全ベンダー）](demo-scenarios.md)
- [CLI ヘルパー](../../shared/scripts/automated-response-cli.sh)
