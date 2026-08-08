# ファイルアクセス監査ログ — フォーマット比較 & アーキテクチャ選択肢

🌐 **日本語**（このページ） | [English](../en/file-access-audit-format-comparison.md)

## 概要

FSx for ONTAP と FSx for Windows File Server のファイルアクセス監査ログのフォーマット、CloudWatch Logs 連携、大量ログ処理のアーキテクチャ選択肢を整理します。

> **よくある誤解**
>
> 「FSx for ONTAP はファイルアクセス監査ログを JSON で直接 CloudWatch Logs に送れる」— これは誤りです。この機能は FSx for Windows File Server にのみ存在し、そのフォーマットも JSON ではなく XML です。

---

## フォーマット比較

| 属性 | FSx for ONTAP | FSx for Windows File Server |
|------|--------------|---------------------------|
| ファイルアクセス監査 → CW Logs 直接配信 | **非対応** | 対応（AWS マネージド） |
| ファイルアクセス監査 → Firehose 直接配信 | **非対応** | 対応 |
| 監査ログフォーマット | **EVTX**（バイナリ）または **XML**（ONTAP 固有） | **XML**（Windows Event Log XML） |
| フォーマット設定 | `vserver audit create -format {evtx\|xml}` | 設定不可（常に XML で CW Logs に配信） |
| JSON 出力 | **非対応** | **非対応**（CW Logs 上は XML） |
| ログ保存場所 | ONTAP ボリューム（ファイル、ローテーション管理） | AWS マネージド（CW Logs/Firehose に直接配信） |
| アクセス方法 | FSx for ONTAP S3 AP（S3 API） | AWS マネージド配信（ユーザー操作不要） |
| 管理監査 → CW Logs | **対応**（Syslog VPCE、2026年6月新機能） | N/A |

### 要点

FSx for ONTAP も FSx for Windows も、ファイルアクセス監査ログを JSON で CloudWatch Logs に送ることはできません。どちらも XML 形式のイベントを生成します。違いは、FSx for Windows には CW Logs への マネージド配信パスがあるのに対し、FSx for ONTAP では S3 AP 経由での読み取り + 変換処理（Lambda/ECS 等）が必要な点です。

---

## FSx for Windows File Server の CloudWatch Logs 配信の仕組み

FSx for Windows は監査イベントを XML テキストとして CloudWatch Logs へ送信します。
クエリは構造化 JSON ではなく文字列マッチになります。

```
# CloudWatch Logs Insights query (text matching on XML)
fields @message
| filter @message like /4660/           # Delete events
| filter @message like /event.txt/      # Specific filename
```

イベントは `@message` に XML として届きます。

```xml
<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>
  <System>
    <EventID>4663</EventID>
    <TimeCreated SystemTime='2021-06-03T19:10:13.887Z'/>
    <Computer>amznfsxgyzohmw8.example.com</Computer>
  </System>
  <EventData>
    <Data Name='SubjectUserName'>Admin</Data>
    <Data Name='ObjectName'>\Device\HarddiskVolume8\share\event.txt</Data>
    <Data Name='AccessMask'>0x1</Data>
  </EventData>
</Event>
```

参照: [AWS Docs — FSx for Windows File Access Auditing](https://docs.aws.amazon.com/fsx/latest/WindowsGuide/file-access-auditing.html)

---

## FSx for ONTAP で CloudWatch Logs に対応しているもの

| ログ種別 | CloudWatch Logs パス | CW Logs 上のフォーマット |
|---------|---------------------|----------------------|
| **管理監査**（CLI/API 操作） | Syslog VPCE → CW Logs（マネージド、Lambda 不要） | Syslog テキスト (RFC 5424) |
| **ファイルアクセス監査** | **直接配信非対応** — Lambda/ECS が必要 | N/A |
| **EMS イベント** | Syslog VPCE → CW Logs（管理監査と同じパス） | Syslog テキスト |

Syslog VPCE パス（2026年6月）は**管理監査と EMS イベントのみ**に対応 — NFS/SMB のファイル操作は対象外です。

---

## 大量ファイルアクセスログの処理アーキテクチャ

高ボリューム環境（数十 GB/日以上）向け:

### 案 1: Step Functions Distributed Map + Lambda（EC2 排除の推奨案）

```
ONTAP volume (EVTX)
  -> FSx for ONTAP S3 AP (読み取り)
  -> Step Functions Distributed Map
  -> Lambda (ファイル単位: EVTX -> JSON)
  -> S3 標準バケット (JSON 出力)
  -> Athena (クエリ)
```

- 並列度: 最大 10,000 同時 Lambda 実行
- 処理時間: 並列度に応じてスケール（時間単位 → 分単位）
- 本プロジェクトの EVTX パーサー: `shared/lambda-layers/log-parser/`

### 案 2: XML フォーマット変更 + Glue（フォーマット変更可能な場合に最もシンプル）

```
ONTAP volume (XML) <- vserver audit modify -format xml
  -> FSx for ONTAP S3 AP (読み取り)
  -> Glue Crawler (XML ネイティブ対応)
  -> Parquet (S3)
  -> Athena (高速カラムナクエリ)
```

- 要件: `vserver audit modify -format xml`（ONTAP 設定変更）
- トレードオフ: XML ファイルは EVTX の 2-3 倍サイズ; Event Viewer 互換性を失う
- 注意: ONTAP は 1 SVM に 1 フォーマットのみ（EVTX と XML の同時出力不可）

### 案 3: ECS Fargate バッチ（EC2 の直接置換）

```
ONTAP volume (EVTX)
  -> FSx for ONTAP S3 AP (読み取り)
  -> EventBridge Schedule -> ECS Fargate Task
  -> 現在の EC2 と同じ変換ロジック
  -> S3 標準バケット (JSON)
  -> Athena
```

- 最もシンプルな移行: 同じコード、異なるコンピュート
- インスタンス管理・パッチ適用・SSH 不要

### 案 4: ハイブリッド — 全量は S3/Athena + セキュリティイベントのみ CW Logs

```
ONTAP volume (EVTX)
  -> FSx for ONTAP S3 AP
  -> Lambda (EVTX -> JSON)
      |
      +-> 全量: S3 標準バケット -> Athena (コンプライアンス、全量クエリ)
      |
      +-> フィルタ済み（失敗/削除/高権限のみ）: CloudWatch Logs
           -> Logs Insights (インタラクティブ)
           -> Log Alarm (リアルタイム検知)
           -> 自動応答 (ユーザー/IP ブロック)
```

- 低コスト全量保存 + リアルタイムセキュリティ検知の両立
- CloudWatch Logs には全体のごく一部のみ投入（コスト効率的）

---

## 大量ログ投入のコスト考慮事項

| アプローチ | 相対コスト | 備考 |
|-----------|-----------|------|
| EC2 バッチ処理 | ベースライン | 固定 EC2 コスト + S3 + Athena |
| 全量を CloudWatch Logs に投入 | **非常に高い** | CW Logs 取り込み $0.76/GB — 数十 GB/日では非現実的 |
| 案 1: Step Functions + Lambda -> S3 + Athena | EC2 ベースラインと同程度 | Lambda + S3 + Athena (EC2 管理不要) |
| 案 4: ハイブリッド（全量->S3、フィルタ->CW） | ベースラインをやや上回る | フィルタ済みサブセットのみ CW Logs コスト追加 |

> **重要**
>
> 大量のファイルアクセスログ（数十 GB/日）を全て CloudWatch Logs に送るのはコスト的に非現実的です。実用的なアプローチは、全量データは S3（Athena 用）に保持し、セキュリティ関連イベントのみ CloudWatch Logs に流す（リアルタイム検知・アラーム用）ことです。

---

## ONTAP の監査フォーマット設定

```bash
# Check current format
ssh fsxadmin@<management-ip>
vserver audit show -vserver <svm-name> -fields format

# Change to XML (if choosing Option 2)
vserver audit modify -vserver <svm-name> -format xml

# Note: Only affects NEWLY generated log files.
# Existing EVTX files are not converted.
# Cannot run EVTX + XML simultaneously on the same SVM.
```

---

## FAQ

**Q: FSx for ONTAP はファイルアクセス監査ログを直接 CloudWatch Logs に送れますか？**
A: いいえ。管理監査ログ（Syslog VPCE 経由）のみ直接送れます。ファイルアクセス監査ログは ONTAP ボリューム上にファイルとして保存され、FSx for ONTAP S3 AP 経由で読み取り、Lambda/ECS/Glue で処理する必要があります。

**Q: Glue は EVTX フォーマットを読めますか？**
A: いいえ。Glue は EVTX（Windows Event Log バイナリ）をネイティブサポートしていません。Glue を使う場合は ONTAP の監査フォーマットを XML に変更してください（`vserver audit modify -format xml`）。

**Q: 「FSx が JSON を CloudWatch に送れる」というブログは何ですか？**
A: FSx for Windows File Server の機能です。CloudWatch Logs への直接配信がありますが、フォーマットは JSON ではなく XML（Windows Event Log XML）です。AWS が配信を管理するため中間処理は不要ですが、CW Logs 上では XML テキストとして保存されます。

**Q: 大量 EVTX 処理で EC2 を排除する最もコスト効率の良い方法は？**
A: Step Functions Distributed Map + Lambda（案 1）。ファイルを並列処理し、EC2 を排除、JSON を S3 に出力して Athena でクエリ。処理時間は並列度に応じてスケールします。

---

## 関連ドキュメント

- [イベントソースガイド](event-sources.md)
- [EMS 検知機能リファレンス](ems-detection-capabilities.md) — 管理監査 + EMS の Push 配信
- [アーキテクチャ進化: Syslog VPCE](architecture-evolution-syslog-vpce.md)
- [AWS Docs: FSx for ONTAP ファイルアクセス監査](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html)
- [AWS Docs: FSx for Windows ファイルアクセス監査](https://docs.aws.amazon.com/fsx/latest/WindowsGuide/file-access-auditing.html)
