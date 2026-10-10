# Syslog VPC Endpoint セットアップガイド — FSx for ONTAP 管理監査ログ → CloudWatch Logs

🌐 **日本語**（このページ） | [English](../en/syslog-vpce-setup-guide.md)

> **所要時間:** 約 15 分（CloudFormation デプロイ + ONTAP 設定）
> **前提:** FSx for ONTAP ファイルシステムが稼働中であること
> **テンプレート**: `shared/templates/syslog-vpce-cloudwatch.yaml`

---

## 概要

FSx for ONTAP の管理アクティビティ監査ログ（ONTAP CLI/API 操作の記録）を、EC2 syslog サーバーなしで CloudWatch Logs に直接配信します。

```
FSx for ONTAP (ONTAP log-forwarding)
    │ Syslog (TCP port 6514 or 1514)
    ▼
VPC Endpoint (com.amazonaws.{region}.syslog-logs)
    │ AWS PrivateLink
    ▼
CloudWatch Logs (/syslog/fsxn-admin-audit)
```

---

## 事前準備

以下の情報を確認してください:

| パラメータ | 確認方法 | 例 |
|-----------|---------|-----|
| VPC ID | FSx コンソール → ファイルシステム → Network | `vpc-0123456789abcdef0` |
| Subnet ID | FSx と同じ AZ のサブネット | `subnet-0123456789abcdef0` |
| VPC CIDR | VPC コンソール → 対象 VPC | `10.0.0.0/16` |
| FSx 管理 IP | FSx コンソール → Management endpoint | `198.51.100.72` |

> **ヒント:** FSx の Subnet ID と同じサブネットを指定するのが最もシンプルです。異なる AZ のサブネットも追加指定可能です（HA 向上）。

---

## Step 1: CloudFormation スタックのデプロイ

```bash
aws cloudformation deploy \
  --template-file shared/templates/syslog-vpce-cloudwatch.yaml \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --parameter-overrides \
    VpcId=<YOUR_VPC_ID> \
    SubnetIds=<YOUR_SUBNET_ID> \
    VpcCidr=<YOUR_VPC_CIDR> \
    LogGroupName=/syslog/fsxn-admin-audit \
    LogRetentionDays=90 \
  --region ap-northeast-1 \
  --no-fail-on-empty-changeset
```

**作成されるリソース**:

| リソース | 用途 |
|---------|------|
| VPC Endpoint | `com.amazonaws.{region}.syslog-logs` への PrivateLink |
| Security Group | VPC CIDR → Port 6514/1514 のイングレス許可 |
| Log Group | `/syslog/fsxn-admin-audit`（90 日保持） |
| Resource Policy | `syslog.logs.amazonaws.com` による書き込み許可 |

> **テンプレートの欠陥に関する補足:** 2026-10-09 に観測し、その後修正しました。修正前の `main` から取ったテンプレートのコピーでは、このデプロイは `ROLLBACK_COMPLETE` で失敗します。EC2 がセキュリティグループの説明を拒否するためです（"Invalid security group description"）。`GroupDescription` が YAML の折り畳みスカラー `>` で、末尾に改行が残っていました。現在のテンプレートは `>-` を使っています。これは 2026-10-09 の実行でローカルのコピーに加えてデプロイできた変更と同じで、EC2 が拒否する説明が戻ってくると `scripts/tests/test_cfn_security_group_description.py` が失敗します。古いコピーをデプロイして失敗した場合、ロググループは `DeletionPolicy: Retain` なので、失敗したスタックは `/syslog/fsxn-admin-audit` を残します。再試行の前に、そのロググループ（`aws logs delete-log-group`）と `ROLLBACK_COMPLETE` のスタックを削除してください。ロググループが残ったままでの再試行は試していません。[2026-10-09 の記録](verification-results-cloudwatch-monitoring.md#2026-10-09-の-terraform-ログアラームモジュールの実行)を参照してください。

デプロイ後、VPC Endpoint の ENI プライベート IP を取得します:

```bash
VPCE_ID=$(aws cloudformation describe-stacks \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --query "Stacks[0].Outputs[?OutputKey=='VpcEndpointId'].OutputValue" \
  --output text --region ap-northeast-1)

ENI_ID=$(aws ec2 describe-vpc-endpoints --vpc-endpoint-ids $VPCE_ID \
  --query 'VpcEndpoints[0].NetworkInterfaceIds[0]' \
  --output text --region ap-northeast-1)

VPCE_IP=$(aws ec2 describe-network-interfaces --network-interface-ids $ENI_ID \
  --query 'NetworkInterfaces[0].PrivateIpAddress' \
  --output text --region ap-northeast-1)

echo "VPC Endpoint IP: $VPCE_IP"
```

---

## Step 2: Syslog Configuration の作成

VPC Endpoint と Log Group を関連付けます。

```bash
python3 shared/scripts/create-syslog-configuration.py \
  --vpce-id $VPCE_ID \
  --log-group-arn "arn:aws:logs:ap-northeast-1:$(aws sts get-caller-identity --query Account --output text):log-group:/syslog/fsxn-admin-audit" \
  --region ap-northeast-1
```

> **注記:** 本スクリプトは SigV4 で直接署名して API を呼び出します。AWS CLI と boto3 に syslog configuration のコマンドが無かった時期に書いたもので、2026-10-09 にも HTTP 200 を返しました。AWS CLI 2.36.5 には `aws logs put-syslog-configuration`、`list-syslog-configurations`、`delete-syslog-configuration` があります（パラメータは `--log-group-identifier` と `--vpc-endpoint-id`）。2026-10-09 に AWS に対して実行したのは `list` と `delete` です。`put` は実行しておらず、引数なしでローカルで呼ぶと CLI が `--log-group-identifier` を求めたことでコマンドの存在を確認しただけです。
>
> **代替:** AWS Console → CloudWatch → Logs → Syslog configurations → Create からも作成可能です。

---

## Step 3: ONTAP Log-Forwarding の設定

FSx for ONTAP の管理エンドポイントに SSH（または REST API）でアクセスし、syslog 転送先を設定します。

> **ポートの選択に関する補足:** 各 Option の最初のコマンドは、ポート 6514 の TLS の転送先を作ります。2026-10-09 の実行では、その転送先は 201 を返した後に何も届けず、TLS を使わないポート 1514 の転送先は作成から約 3 秒で最初の行を届けました（[プロトコル選択](#プロトコル選択)の TLS での配送に関する補足を参照）。そのため、各 Option に 1514 の形も載せています。ポート 1514 では、変更の操作の要求本文を含む監査ログの行が、VPC 内の ONTAP とエンドポイントの間を暗号化されずに流れます。6514 を使い続ければそれは避けられますが、自分の環境で TLS の配送を動かす必要が出ることがあります。1514 を使うと、通信経路の暗号化と引き換えに、この実行で届いた経路を使うことになります。どちらを選ぶかは、環境ごとのセキュリティの判断です。どちらのポートでも、頼る前に[行の到着の確認](#行の到着の確認)を実行してください。

### Option A: REST API 経由（推奨 — 自動化向き）

```bash
curl -sk -u fsxadmin:<PASSWORD> \
  -X POST "https://<FSx-Management-IP>/api/security/audit/destinations?force=true" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "'$VPCE_IP'",
    "port": 6514,
    "protocol": "tcp_encrypted",
    "facility": "local7"
  }'
```

検証用の代替として、TLS を使わないポート 1514 の形です。2026-10-09 に届いたのはこの呼び出しです。

```bash
curl -sk -u fsxadmin:<PASSWORD> \
  -X POST "https://<FSx-Management-IP>/api/security/audit/destinations?force=true" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "'$VPCE_IP'",
    "port": 1514,
    "protocol": "tcp_unencrypted",
    "facility": "local7"
  }'
```

### Option B: SSH + ONTAP CLI

> **CLI コマンド名の注意:** ONTAP 9.11.1 以降では `cluster log-forwarding` コマンドが `security audit log-forwarding` に変更されています。FSx for ONTAP（9.11.1+）では `security audit log-forwarding` を使用してください。古いドキュメントや記事で `cluster log-forwarding` と記載されている場合がありますが、同じ機能です。

```bash
ssh fsxadmin@<FSx-Management-IP>

FsxId*> security audit log-forwarding create \
  -destination <VPCE_IP> \
  -port 6514 \
  -protocol tcp-encrypted \
  -facility local7

FsxId*> security audit log-forwarding show
```

検証用の代替として、TLS を使わないポート 1514 の形です。2026-10-09 の実行で使ったのは Option A の REST の形で、この CLI の形は実行していません。

```bash
FsxId*> security audit log-forwarding create \
  -destination <VPCE_IP> \
  -port 1514 \
  -protocol tcp-unencrypted \
  -facility local7
```

### 行の到着の確認

転送先の作成自体が変更の操作なので、作成の操作が自分の監査ログの行を書きます。2026-10-09 の実行では、1514 の転送先の `Pending` の行が、呼び出しから約 3 秒でストリーム `<VPCE_ID>_Syslog_<region>` にありました。6514 の転送先では、約 4 分たってもストリームはできませんでした。すでにあるストリームは、新しい転送先が届けていることを示しません。ストリームの名前はエンドポイントから付くため、再実行や、6514 を試した後の 1514 のように、前に一度でも届いていれば残っています。代わりに、転送先を作る直前の時刻から、転送先自身の行を探してください（コードブロック内の英語のコメントは、上から「転送先を作る前に、エポックからのミリ秒で時刻を控える」「作った後に、その転送先自身の監査ログの行を探す（REST と CLI のどちらの形でも）」という意味です）。

```bash
# Before creating the destination: note the time in milliseconds since the epoch
START_MS=$(( $(date +%s) * 1000 ))

# After creating it: look for its own audit line (REST or CLI form)
aws logs filter-log-events \
  --log-group-name /syslog/fsxn-admin-audit \
  --start-time "$START_MS" \
  --filter-pattern '?"audit/destinations" ?"log-forwarding create"' \
  --query 'events[].message' \
  --region ap-northeast-1
```

転送先を作ってから数分たっても一覧が空なら、新しい転送先は届けていません。[ログの不着](#ログの不着)を参照してください。この検索は 2026-10-09 の実行の後に書いたもので、その実行では使っていません。探しているのは、その実行で REST の形について観測した行です。CLI の形の行は取り込んでいません。

### プロトコル選択

| プロトコル | ポート | ONTAP パラメータ | 推奨用途 |
|-----------|--------|-----------------|---------|
| TCP + TLS | 6514 | `tcp-encrypted` | 本番環境。行が届くことを確認してから使う（下の補足を参照） |
| TCP Plaintext | 1514 | `tcp-unencrypted` | 検証用。2026-10-09 の実行で届いた設定 |

> **TLS での配送に関する補足:** 2026-10-09 に観測しました。エンドポイントの IP を指定し、ポート 6514・`tcp_encrypted` で転送先を作ると、`POST` は 201 を返し、ONTAP は `verify_server: true` を設定しましたが、約 4 分間ロググループには何も届かず、ONTAP はそれについて EMS イベントを書きませんでした。代わりに `syslog-logs.<region>.amazonaws.com` で指定すると、クラスターがその名前を解決できないため "Cannot resolve the destination host" で拒否されました。ポート 1514・`tcp_unencrypted` は、作成から約 3 秒で最初の行を届けました。原因は推定で、確認していません。ONTAP の証明書の確認が、IP アドレスとエンドポイントの証明書の名前を照合できないと考えられます。転送先を作ったら、頼る前にその転送先自身の監査ログの行が届くことを確認してください（[行の到着の確認](#行の到着の確認)）。

> **検証での知見:** TCP plaintext (1514) は追加の証明書設定なしで動作確認できます。TLS (6514) の場合、ONTAP がサーバー証明書（AWS 管理の Amazon Trust Services 証明書）を検証するため、ONTAP ノードが CA を信頼していることを確認してください。PrivateLink 経由のため通信経路自体は VPC 内に閉じています。

> **本番環境向けセキュリティ強化:** 本番では次の 3 点を適用します。
>
> | 対策 | 内容 |
> |------|------|
> | Security Group を FSx サブネット CIDR に限定 | テンプレートデフォルトの VPC CIDR (`10.0.0.0/16`) を、FSx for ONTAP が配置されたサブネットの CIDR（例: `10.0.3.0/24`）に狭めてください。 |
> | 認証情報の取り扱い | `curl -u fsxadmin:<PASSWORD>` のようにコマンドラインにパスワードを渡すのは検証用です。本番では Secrets Manager から取得し、環境変数経由で渡してください。 |
> | TLS を使用 | 本番では `tcp-encrypted` (ポート 6514) を優先し、行が届くことを確認してください（上の TLS での配送に関する補足を参照）。PrivateLink 内であっても defense-in-depth として暗号化を推奨します。 |

---

## Step 4: 動作確認

### ログ生成（管理操作の実行）

Step 3 の転送先の `POST` 自体が変更の操作なので、最初に届くのはその行です。GET の要求が監査されるのは、GET の監査を有効にしている場合だけです。2026-10-09 には `GET /api/security/audit` が `http: false` を返し、GET は行を書きませんでした。変更の操作は 1 回ごとに 2 行を書きます。`:: Pending` で終わる行と、結果（`:: Success:` か `:: Error: ...`）で終わる行です。行を任意に発生させるには、テスト用ボリュームに Qtree を作って削除します（コードブロック内の英語のコメントは、上から「GET の監査が有効か。"http": false なら REST の GET は監査ログの行を書かない」「変更の操作は監査される。テスト用ボリュームに Qtree を作る」「もう一度削除する。ボリュームの UUID と Qtree の ID を調べてから DELETE する」という意味です）。

```bash
# Is GET auditing on? ("http": false means REST GETs write no audit line)
curl -sk -u fsxadmin:<PASSWORD> \
  "https://<FSx-Management-IP>/api/security/audit" --max-time 10

# A change operation is audited: create a qtree on a test volume
curl -sk -u fsxadmin:<PASSWORD> -X POST \
  "https://<FSx-Management-IP>/api/storage/qtrees" \
  -H "Content-Type: application/json" \
  -d '{"svm":{"name":"<SVM_NAME>"},"volume":{"name":"<TEST_VOLUME>"},"name":"audit_probe"}' \
  --max-time 10

# Delete it again: look up the volume UUID and qtree ID, then DELETE
curl -sk -u fsxadmin:<PASSWORD> \
  "https://<FSx-Management-IP>/api/storage/qtrees?name=audit_probe&fields=id,volume.uuid" \
  --max-time 10
curl -sk -u fsxadmin:<PASSWORD> -X DELETE \
  "https://<FSx-Management-IP>/api/storage/qtrees/<VOLUME_UUID>/<QTREE_ID>" \
  --max-time 10
```

### CloudWatch Logs での確認

![CloudWatch Logs — ログイベント一覧](../screenshots/syslog-vpce/02-cloudwatch-log-events-ontap-audit.png)

```bash
# Check the log stream (appears within seconds to a minute)
aws logs describe-log-streams \
  --log-group-name /syslog/fsxn-admin-audit \
  --region ap-northeast-1

# Check the latest events
aws logs get-log-events \
  --log-group-name /syslog/fsxn-admin-audit \
  --log-stream-name "<VPCE_ID>_Syslog_<region>" \
  --limit 5 \
  --region ap-northeast-1
```

**期待される出力**（実際の検証結果）:

```
<190>Jun 28 02:06:40 FsxId0123456789abcdef0-01: ... [kern_audit:info:6392]
  ... FsxId0123456789abcdef0:http ... POST /api/storage/volumes ... :: Success
```

---

## トラブルシューティング

### ログの不着

| 原因 | 確認方法 | 対処 |
|------|---------|------|
| SG でブロックされている | VPC Flow Logs で REJECT 確認 | SG に VPC CIDR → 1514/6514 を追加 |
| Syslog Configuration 未作成 | ログストリームが存在しない | Step 2 を実行 |
| ONTAP 転送先が未設定 | `security audit log-forwarding show` | Step 3 を実行 |
| fsxadmin ロック | REST API で "User is not authorized" | パスワードリセット（下記参照） |
| ONTAP → VPCE 疎通不可 | `force=true` なしでエラー | SG 確認 + `force=true` で再作成 |
| 6514 `tcp-encrypted` の転送先を作ったが何も届かない | 転送先の作成から数分たってもログストリームが無い。EMS のエラーも無い | Step 3 の TLS での配送に関する補足を参照。2026-10-09 の実行ではポート 1514 `tcp-unencrypted` で届いた |
| 1 つの操作だけが無く、次の操作はある | 操作は成功したが、ロググループにその行が無い。ONTAP の `GET /api/security/audit/messages` と見比べる | [無通信の接続の後の最初の操作の欠落](#無通信の接続の後の最初の操作の欠落)を参照 |

### 無通信の接続の後の最初の操作の欠落

2026-10-09 に、HA ペア 1 つのファイルシステムの 1 つのノードで 3 回観測しました。ノードが約 4–5 分何も送らなかった後、エンドポイントがその接続を閉じました（`AWS/Logs` の `SyslogConnectionsClosed`）。次の操作（`fsxadmin` による Qtree の作成）は HTTP 201 を返し、ロググループには届きませんでした。1 回目では、ONTAP 自身の `GET /api/security/audit/messages` がその操作を一覧に出しました。残りの 2 回では読んでいません。そのうち 2 回では、約 20 秒後の操作は届きました。その 2 回のうち 1 回では同じ分に `SyslogConnectionsEstablished` が増え、もう 1 回ではそのメトリクスを読んでいません。損失を記録した EMS イベントは無く、アカウントには `SyslogMessagesDropped` の系列がありませんでした。仕組みはメトリクスの時刻からの推定で、確認していません。

静かなノードでの 1 回の操作がロググループに無いことがあるため、1 行に依存するアラームはそれを見逃すことがあります。確認した 1 回では、ONTAP 自身の監査ログ（`GET /api/security/audit/messages`）にエントリが残っていました。対策（たとえば、各ノードの接続を保つ定期的な書き込み）は試していません。

### fsxadmin アカウントがロックされた場合

SSH パスワード認証で複数回失敗するとロックされます。AWS API でリセットできます:

```bash
aws fsx update-file-system \
  --file-system-id <FS_ID> \
  --ontap-configuration '{"FsxAdminPassword":"<NEW_PASSWORD>"}' \
  --region ap-northeast-1
```

> リセット後、30 秒ほど待ってからアクセスしてください。

### Security Group の重要な注意点

> **検証での知見:** FSx for ONTAP のノード ENI は、ユーザーが FSx に割り当てた Security Group とは異なる内部 SG を使用しています。そのため、VPC Endpoint の SG で「FSx の SG からのインバウンド」をソースに指定しても**接続できません**。代わりに **VPC CIDR をソース** に指定してください。

---

## クリーンアップ

作成した転送先はすべて削除してください。フォールバックの 1514 の転送先を残すと、エンドポイントを削除した後もその IP を指し続けます。2026-10-09 には、クラスターに、すでに存在しないエンドポイントの IP を指す転送先が 2 つ（1514 と 6514）あり、アカウントには存在しないエンドポイントに対する syslog configuration が残っていました（コードブロック内の英語のコメントは、上から「ONTAP の転送先を一覧し、作成したものをそれぞれ削除する」「syslog configuration を削除する」「CloudFormation スタックを削除する」「ロググループを手で削除する。スタックはロググループに DeletionPolicy: Retain を設定しているため、スタックを削除しても監査の履歴は意図的に残る」という意味です）。

```bash
# 1. List the ONTAP forwarding destinations, then remove each one you created
curl -sk -u fsxadmin:<PASSWORD> \
  "https://<FSx-Management-IP>/api/security/audit/destinations" \
  --max-time 10
curl -sk -u fsxadmin:<PASSWORD> \
  -X DELETE "https://<FSx-Management-IP>/api/security/audit/destinations/<VPCE_IP>/6514" \
  --max-time 10
curl -sk -u fsxadmin:<PASSWORD> \
  -X DELETE "https://<FSx-Management-IP>/api/security/audit/destinations/<VPCE_IP>/1514" \
  --max-time 10

# 2. Delete the syslog configuration
aws logs delete-syslog-configuration \
  --log-group-identifier "<LOG_GROUP_ARN>" \
  --vpc-endpoint-id $VPCE_ID \
  --region ap-northeast-1

# 3. Delete the CloudFormation stack
aws cloudformation delete-stack \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --region ap-northeast-1

# 4. Delete the log group by hand. The stack sets DeletionPolicy: Retain on it,
#    so deleting the stack deliberately leaves the audit history in place.
aws logs delete-log-group \
  --log-group-name /syslog/fsxn-admin-audit \
  --region ap-northeast-1
```

---

## 次のステップ

### 運用監視（推奨）

Syslog パイプライン自体の健全性を監視するため、以下の CloudWatch メトリクスとアラームを設定してください:

```bash
# Alarm on the SyslogMessagesDropped metric
aws cloudwatch put-metric-alarm \
  --alarm-name "FSx-ONTAP-SyslogDropped" \
  --metric-name SyslogMessagesDropped \
  --namespace AWS/Logs \
  --statistic Sum \
  --period 300 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --dimensions Name=LogGroupName,Value=/syslog/fsxn-admin-audit \
  --alarm-actions <SNS_TOPIC_ARN> \
  --region ap-northeast-1
```

| メトリクス | 意味 | アラーム閾値案 |
|-----------|------|-------------|
| `SyslogMessagesDropped` | 配信失敗で破棄されたメッセージ数 | > 0 (5 分間) |
| `IncomingLogEvents` | 受信ログイベント数 | < 1 (1 時間) で「ログ到着停止」検知 |

> **syslog のメトリクスに関する補足:** 2026-10-09 に観測しました。その実行の間、`AWS/Logs` にはディメンションの無い `SyslogConnectionsEstablished` と `SyslogConnectionsClosed`、ロググループごとの `SyslogMessagesReceived` がありました。`SyslogMessagesDropped` の系列は無く、[無通信の接続の後の最初の操作の欠落](#無通信の接続の後の最初の操作の欠落)の 3 回の損失のときも無かったため、上のアラームではそれを捉えられませんでした。

> **ヒント:** ONTAP の fsx-control-plane は定期的にアクセスチェックを実行するため、クラスター全体としては通常ログが常時到着します。ノードごとには当てはまりません。2026-10-09 には 1 つのノードが約 4–5 分何も送らなかったことが 3 回あり、そのたびに直後の最初の操作が失われました（[無通信の接続の後の最初の操作の欠落](#無通信の接続の後の最初の操作の欠落)）。1 時間以上まったくログがない場合、VPCE 接続や ONTAP 設定に問題がある可能性があります。

### その他の次のステップ

| 機能 | 用途 |
|------|------|
| CloudWatch Alarms | 特定操作（権限昇格、ユーザー作成等）をメトリクスフィルタで検知。たとえば Terraform モジュール [`terraform/fsxn-log-alarm/`](../../terraform/fsxn-log-alarm/README.ja.md) を使う。既定のパターンを使う前に、その検証状況を確認する |
| Subscription Filter | CloudWatch Logs → Lambda → Datadog/Splunk/SIEM へ二次配信 |
| S3 Export | 長期保存用に S3 へエクスポート（Glacier 移行可） |
| CloudWatch Logs Insights | 管理操作の分析クエリ |

### CloudWatch Logs Insights クエリ例

```sql
-- Detect privilege escalation operations
fields @timestamp, @message
| filter @message like /set -privilege/
| sort @timestamp desc
| limit 20

-- Success/failure breakdown of REST API operations
fields @timestamp, @message
| filter @message like /POST|GET|PATCH|DELETE/
| parse @message "* :: *" as operation, result
| stats count() by result
```

---

## 関連ドキュメント

- [アーキテクチャ進化ドキュメント](architecture-evolution-syslog-vpce.md)
- [イベントソースガイド](event-sources.md)
- [AWS Docs: Syslog ingestion](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Syslog.html)
- [AWS Docs: Setting up syslog ingestion](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Syslog_Setup.html)
- [NetApp: ONTAP audit destinations](https://docs.netapp.com/us-en/ontap/system-admin/forward-command-history-log-file-destination-task.html)
- [Classmethod: FSx for ONTAP 管理監査ログ → CW Logs](https://dev.classmethod.jp/articles/amazon-fsx-for-netapp-ontap-security-audit-log-syslog-to-cw-logs/)
