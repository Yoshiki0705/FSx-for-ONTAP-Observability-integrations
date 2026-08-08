# ARP インシデント対応 デモ手順書

🌐 **日本語**（このページ） | [English](../en/demo-arp-incident-response.md)

## 目的

[ARP インシデント対応ガイド](arp-incident-response-guide.md) に記載されている、ARP（Autonomous Ransomware Protection）の検知から対応までのフローを実演するための手順書。本手順書は、ARP 検知チェーンとその Datadog 側での確認に範囲を限定しています — 実際/シミュレートされた ARP アラートがトリガーする自動封じ込めアクション（SMB/NFS ブロック、Snapshot 作成）については、[自動応答デモ手順書](demo-automated-response.md) の Phase 4 を参照してください。本手順書はそれを重複させるのではなく補完するものです。

用途:
- 対外デモ（ライブまたは録画）
- ブログ公開前の E2E 検証
- 内部トレーニング

> **エビデンス形式に関する注記 — 検証状況の更新（2026-07-12）**
>
> Phase 1（EMS 配信パイプライン）と Phase 2（実際の ARP/AI 検知）の両方が ONTAP 9.17.1P7D1 でエンドツーエンドで検証済みです。検証からの主要な発見: (1) `attack simulate` CLI コマンドは ONTAP 9.17.1 に存在しない — 実際のファイル操作（暗号化 + 削除 + 新拡張子付与）が必要; (2) ARP/AI は学習期間不要で即座にアクティブ; (3) ARP はランサムウェア様のパターンを正しく検知（Attack Probability: moderate, Detected By: file_analysis）し、`Anti_ransomware_attack_backup` Snapshot を自動作成; (4) EMS イベント `callhome.arw.activity.seen` が severity=alert で発行された。Phase 3（インシデント対応の判断ステップ）は部分検証済み（`clear-suspect -false-positive true` は動作するが、`show-suspect-files` は CLI コマンドとして存在しない）。完全な検証結果: [`docs/screenshots/group-b-verification-results.md`](../screenshots/group-b-verification-results.md)。

---

## 前提条件

| 項目 | 要件 |
|------|------|
| FSx for ONTAP | 稼働中、少なくとも 1 ボリュームで ARP 有効（`security anti-ransomware volume show`） |
| ONTAP 認証情報 | 管理エンドポイントへの管理者 SSH アクセス |
| EMS Webhook スタック | デプロイ済み（`fsxn-ems-webhook` または利用ベンダーの相当スタック — [前提条件](prerequisites.md) を参照） |
| Observability プラットフォーム | Datadog（または設定済みのベンダー）が EMS/FPolicy イベントを受信していること |
| AWS CLI | 適切な IAM 権限で設定済み |

---

## Phase 1: EMS 検知パイプラインの確認（検証済み）

このフェーズは、`docs/ja/verification-results-datadog.md` に記録されているパイプラインテストを再現します。Lambda から Datadog への配信経路が機能することを確認しますが、ONTAP 自身の ARP 検知ロジック自体は実行しません（それについては Phase 2 を参照）。

### ステップ 1.1: EMS/FPolicy スタックがデプロイ済みであることを確認

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-datadog-ems-fpolicy \
  --query 'Stacks[0].StackStatus' \
  --output text
```

**確認ポイント**: `CREATE_COMPLETE` または `UPDATE_COMPLETE` であること。このスタックがまだ存在しない場合は、先にデプロイしてください — テンプレートとパラメータについては [EMS 検知機能リファレンス](ems-detection-capabilities.md) を参照してください。

### ステップ 1.2: EMS Lambda に合成 ARP イベントを送信

これは、本プロジェクト自身の過去の検証で使用したのと同じ invoke です — 実際の攻撃や実際の `attack simulate` 実行なしに、EMS が ARP アラートに対して配信するであろう JSON ペイロードをシミュレートします:

```bash
aws lambda invoke \
  --function-name fsxn-datadog-ems-fpolicy-ems \
  --payload '{"body":"{\"messageName\":\"arw.volume.state\",\"severity\":\"alert\",\"parameters\":{\"volume_name\":\"<test-vol>\",\"state\":\"attack-detected\"}}","requestContext":{}}' \
  --cli-binary-format raw-in-base64-out \
  response.json

cat response.json
```

**確認ポイント**: レスポンスボディに `"shipped": 1`（または利用ベンダーの Lambda に応じた類似の値）が報告されること。過去に検証済みのレスポンス（`verification-results-datadog.md` に記録: `{"statusCode": 200, "body": {"total_events": 1, "shipped": 1}}`）と比較してください。

### ステップ 1.3: Datadog（または利用ベンダー）への到着を確認

Datadog で検索:

```
source:fsxn-ems @attributes.event_name:arw.volume.state
```

**確認ポイント**: ステップ 1.2 で送信したボリューム名・重要度に一致するログエントリが 1 件、invoke からおよそ 30 秒以内に届くこと。これは、過去の検証パスにおいて [`datadog-arp-detection.png`](../screenshots/datadog-arp-detection.png) と [`datadog-arp-log-detail.png`](../screenshots/datadog-arp-log-detail.png) が示している内容そのものです — これらのスクリーンショットは「結果がどう見えるべきか」の参考として使い、「今回の実行が成功した証拠」としては扱わないでください。

---

## Phase 2: ONTAP の実際の ARP 機能を実行する（未検証）

このフェーズでは、結果として生じる EMS ペイロードをシミュレートするのではなく、実際の（テスト用）ボリュームに対して ONTAP 自身の ARP 検知を実行します。本稿執筆時点で、本プロジェクトはこのフェーズをエンドツーエンドで実行しておらず、対応するスクリーンショットも存在しません。

### ステップ 2.1: 対象ボリュームで ARP がアクティブであることを確認

```bash
ssh admin@<management-ip> "security anti-ransomware volume show -vserver <svm-name> -volume <volume-name>"
```

**確認ポイント**: 対象ボリュームの ARP 状態が有効/アクティブと表示されること。表示されない場合は、続行する前にそのボリュームで ARP を有効化してください — メインガイドが参照している [AWS の ARP ドキュメント](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/ARP.html) を参照してください。

### ステップ 2.2: 攻撃をシミュレート（テスト環境限定、廃棄可能なデータ）

> **重要（ONTAP 9.16.1+ / ARP/AI）**
>
> `security anti-ransomware volume attack simulate` コマンドは ONTAP 9.17.1 には**存在しません**。ARP/AI 検知をトリガーするには、実際のランサムウェア様のファイル操作を実行する必要があります。ARP/AI は即座にアクティブ（学習期間不要）で、高エントロピーデータ書き込み + ファイル削除 + 未知の拡張子のパターンを検知します。

マウント済みの NFS/SMB クライアントから実際のファイル操作で検知をトリガー:

```bash
# 対象ボリュームを NFS または SMB でマウントしたクライアントで:
# 1. 通常のファイルを作成
for i in $(seq 1 15); do
  dd if=/dev/urandom of=/mnt/target-vol/doc_${i}.dat bs=256K count=1 status=none
done

# 2. 暗号化する（ランサムウェア挙動のシミュレーション — パスワード付き zip + 新拡張子）
cd /mnt/target-vol
for f in doc_*.dat; do
  zip -q -e -P TestPass123 "${f}.ktkt" "$f" && rm -f "$f"
done
```

**確認ポイント**: 1〜5 分後に ARP が検知したか確認:
```bash
ssh admin@<management-ip> "security anti-ransomware volume show -vserver <svm-name> -volume <volume-name>"
```
期待値: `Attack Probability: moderate`（以上）、`Attack Detected By: file_analysis`。

> **検知閾値に関する補足**
>
> ARP/AI は `never_seen_before_file_extension_count_notify_threshold: 5` を使用 — 拡張子は新しくかつ異なるものが必要です。同じ拡張子（多数のファイルでも）だけでは検知されない場合があります。暗号化 + 削除 + 新拡張子の組み合わせが最も確実なトリガーパターンです。

### ステップ 2.3: ARP Snapshot が作成されたことを確認

```bash
ssh admin@<management-ip> "volume snapshot show -vserver <svm-name> -volume <volume-name> -snapshot Anti_ransomware*"
```

**確認ポイント**: `Anti_ransomware_backup` プレフィックスを持つ Snapshot が表示され、そのタイムスタンプがファイル操作の実行時刻と近いこと。ARP/AI は不審な活動を検知すると自動的にこの Snapshot を作成します。検証済みデプロイでの出力例:
```
Anti_ransomware_attack_backup.2026-07-12_0042    30.73MB
```

### ステップ 2.4: 実際の EMS イベントが Datadog に届くことを確認

ステップ 1.3 と同じ Datadog 検索を再度実行:

```
source:fsxn-ems @attributes.event_name:arw.volume.state
```

**確認ポイント**: 今回は直接 invoke した Lambda ペイロードではなく、ARP が実際のファイル操作を検知して発行した新しいログエントリが表示されること。EMS イベント名は `callhome.arw.activity.seen`（severity=alert）。数分以内に表示されない場合、ARP 自体がシミュレートされた攻撃を検知しなかったと決めつける前に、EMS Webhook スタック自身の Lambda ログ（[EMS 検知機能リファレンス](ems-detection-capabilities.md) を参照）を確認してください — ギャップは ONTAP → EMS Webhook → Lambda → ベンダーのチェーンのどこにでも存在する可能性があります。

### ステップ 2.5: ONTAP CLI で ARP ステータスを確認

```bash
ssh admin@<management-ip> "security anti-ransomware volume show -vserver <svm-name> -volume <volume-name>"
```

**確認ポイント**: ボリュームの ARP 状態が検知された攻撃を反映していること（例: `Attack Probability: moderate`、`Attack Detected By: file_analysis`）。ONTAP 9.17.1P7D1 で検証した出力:
```
Vserver Name: <svm-name>
 Volume Name: <volume-name>
       State: enabled
Attack Probability: moderate
   Attack Timeline: 7/12/2026 00:26:12
 Number of Attacks: 1
Attack Detected By: file_analysis
```

---

## Phase 3: インシデント対応の判断ステップを実演する

このフェーズは、[ARP インシデント対応ガイド § Step 1〜Step 4a/4b](arp-incident-response-guide.md#step-1-初動対応検知から5分以内) に従います。これは技術的な検証ステップというより、判断・記録の演習です — 「インシデント対応プロセスが正しく実施された」ことを確認する単一のコマンド出力は存在しません。

### ステップ 3.1: 影響範囲の調査

> **補足**
>
> `show-suspect-files` サブコマンドは ONTAP 9.17.1 には存在しません。REST API で疑わしいファイルを照会するか、ARP 攻撃レポートを確認してください:

```bash
# ARP 攻撃レポートの生成と閲覧
ssh admin@<management-ip> \
  "security anti-ransomware volume attack generate-report -vserver <svm-name> -volume <volume-name> -dest-path <svm-name>:/<volume-name>/"

# または REST API で確認:
# GET /api/storage/volumes/<vol-uuid>?fields=anti_ransomware
```

**確認ポイント**: このコマンドが出力する疑わしいファイルの一覧 / 攻撃レポートを、[ARP インシデント対応ガイド § Step 2](arp-incident-response-guide.md#step-2-影響範囲の特定) の確認すべき項目と照らし合わせ、誤検知か実際の攻撃かを判断してください。

### ステップ 3.2: 両方の結果パスを実践する

デモでは、どちらか一方だけを選ぶのではなく、両方の分岐を実践してください:

**誤検知の分岐** — [Step 4a](arp-incident-response-guide.md#step-4a-誤検知の場合) を参照:
```bash
ssh admin@<management-ip> "security anti-ransomware volume attack clear-suspect -vserver <svm-name> -volume <volume-name> -false-positive true"
```
**確認ポイント**: コマンドがエラーなく完了し、続けて `security anti-ransomware volume show` を実行すると攻撃状態が表示されなくなっていること。ステップ 2.3 で作成した ARP Snapshot は、このアクションの一部として自動的に削除されます。

> **補足**
>
> `-false-positive` パラメータは**必須**です（省略不可）。誤検知クリアの場合は `true`、攻撃確認後のクリアの場合は `false` を使用してください。

**攻撃確認の分岐** — [Step 4b](arp-incident-response-guide.md#step-4b-攻撃確認--封じ込め) を参照。ここで封じ込めコマンドを繰り返すのではなく、[自動応答デモ手順書](demo-automated-response.md) の Phase 4 に進んでください。これは、確認済みの ARP 攻撃がトリガーするはずの自動封じ込め経路（SMB ユーザーブロック、Snapshot、セッション切断）を実演します。

---

## クリーンアップ

```bash
# Phase 3 の誤検知分岐で既にクリアしていない場合、Phase 2 で作成した
# ARP Snapshot を削除
ssh admin@<management-ip> "volume snapshot delete -vserver <svm-name> -volume <volume-name> -snapshot Anti_ransomware_backup.<timestamp>"

# 本デモのためだけに EMS/FPolicy スタックをデプロイした場合、これは他の
# 検知フローと共有される基盤である可能性があります — 他の統合がこれに
# 依存していないことを確認せずに削除しないでください。
```

---

## 所要時間の目安

| フェーズ | 所要時間 | 備考 |
|---------|---------|------|
| Phase 1（EMS パイプライン確認） | ~3 分 | 実行 + 検索 |
| Phase 2（ONTAP ARP の実行） | ~5 分 | シミュレート + Snapshot 確認 + EMS 配信確認 |
| Phase 3（インシデント対応の実演） | ~5 分 | 影響範囲調査 + 両方の分岐 |
| **合計** | **~13 分** | 全フェーズ実行。攻撃確認後の封じ込め経路も実演する場合は [自動応答デモ手順書](demo-automated-response.md) の Phase 4（~5 分）を追加 |

---

## 関連ドキュメント

- [ARP インシデント対応ガイド](arp-incident-response-guide.md)
- [自動応答ガイド](automated-response-guide.md) — 確認済みの ARP 攻撃がトリガーするはずの封じ込めアクション
- [自動応答デモ手順書](demo-automated-response.md) — Phase 4 で ARP → 自動封じ込めフローを実演
- [EMS 検知機能リファレンス](ems-detection-capabilities.md) — `arw.volume.state` を含む EMS イベントの全カタログ
- [セキュリティ監視インデックス](security-monitoring-index.md)
