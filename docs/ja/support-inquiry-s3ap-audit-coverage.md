
🌐 **日本語**（このページ） | [English](../en/support-inquiry-s3ap-audit-coverage.md)

## このページの目的

FSx for ONTAP S3 Access Point の監視・監査カバレッジについて AWS サポートへ問い合わせる文面である。
仕様確認と機能改善要望を分けてある。前者は「文書化された挙動は何か」を問い、後者は「変更してほしい」
という別の要求なので、1 つのケースに混ぜると片方しか答えられない。

根拠となる測定は
[verification-results-fpolicy-s3ap-and-session.md](verification-results-fpolicy-s3ap-and-session.md)
にある。送信前に `<...>` のプレースホルダを実際の値に置き換える。アカウント ID、ファイルシステム ID、
プライベートアドレスをこのページの公開コピーに書き戻さないこと。

---

## 送信前の確認

| 確認事項 | 理由 |
|---------|------|
| 対照実験を含めていること | 対照がないと、読む側は「非対応」と「設定ミス」を区別できない。下の文面が対照とその結果を明記しているのはこのためである |
| 測定した範囲しか主張していないこと | 所見のうち 2 つは 1 回の観測に基づくもので、そう明記してある。過大な主張は、答えてもらう代わりに訂正されて終わる |
| バージョンと構成を明記していること | 回答がバージョン依存である可能性がある。ONTAP リリース、デプロイタイプ、identity 型をすべて記載している |
| 要望と質問を分けていること | 仕様確認の中に要望を混ぜると、どちらか一方として処理されやすい |

---

## ケース 1 — 仕様確認

**件名**: FSx for ONTAP S3 Access Point: FPolicy and audit-log coverage of data-plane operations

**サービス**: Amazon FSx for NetApp ONTAP
**カテゴリ**: Technical / Specification clarification

**本文**:

```text
Environment
  Region                : <region>
  File system           : <file-system-id>  (SINGLE_AZ_1, 128 MBps, SSD 1024 GiB)
  ONTAP release         : 9.18.1P3D1
  SVM                   : one SVM with NFS, SMB and an FSx S3 Access Point attached
  Volumes               : one UNIX-security-style volume, one NTFS-security-style volume
  S3 Access Points      : one with UNIX FileSystemIdentity, one with WINDOWS FileSystemIdentity
  FPolicy external engine: asynchronous, ssl-option no-auth, protocol version 1.2 negotiated

What I measured

1. FPolicy notifications are not raised for S3 Access Point data-plane operations.

   Configuration: one FPolicy event per protocol that ONTAP 9.18.1P3D1 accepts (cifs, nfsv3,
   nfsv4), each with every file operation that protocol allows enabled - including read, open,
   close and getattr where the protocol supports them. A single policy scoped to the two test
   volumes, mandatory=false, pointed at one external engine.

   Procedure: a 90-second window with no I/O, then S3 Access Point data-plane calls only
   (3 PUT, 3 GET, 1 HEAD, 1 LIST, 1 DELETE), then a 60-second settle, then a file-protocol
   control (create, read, delete) against the same volume in the same FPolicy session.

   Result, identical for the UNIX-identity and the WINDOWS-identity access point:
     quiet window                : 0 notifications
     S3 Access Point calls       : 0 notifications
     file-protocol control       : notifications received (3 over NFSv3, 10 over SMB)

   During the S3 Access Point window the FPolicy server log contained nothing but KeepAlive
   messages - no notification of any kind and no unrecognised message type. The writes did
   reach the volume: the objects are visible as files over NFS and SMB with matching content,
   and the object deleted through the S3 API is absent.

   I also enumerated the accepted values of the FPolicy event "protocol" field by POSTing each
   candidate to the REST API. cifs, nfsv3 and nfsv4 are accepted. s3, S3, object, smb, nfs,
   http, nfsv4.1 are all rejected with HTTP 400 and
   '"<value>" is an invalid value for field "protocol"'.

2. ONTAP native file auditing does record the same operations, with Source=HTTP.

   Configuration: audit enabled on the SVM with file_operations, xml log format, log destination
   on a separate volume, and an audit_success SACL for Everyone with full_control applied to the
   NTFS volume root for this_folder, files and sub_folders.

   Result: 3 PUT produced 3 "Create Object" (EventID 4656), 3 GET produced 3 "Read Object"
   (4663) with ReadOffset and ReadCount, and 1 DELETE produced 1 "Unlink Object" (9998), all
   with Source=HTTP. The SMB control that followed produced Source=CIFS events on the same file.

3. The audit records for the S3 Access Point path do not identify the requester.

   Comparing audit records for the same volume and the same audit configuration:

     field                 S3 Access Point (Source=HTTP)      file protocol (Source=CIFS)
     SubjectUserName       Not Present                        the actual user name
     SubjectDomainName     Not Present                        the actual domain
     SubjectIP             an AWS-owned public address that    the actual client IP
                           varies between calls
     SubjectUnix           Uid=65535 Gid=65535                the actual UID/GID
     SubjectUserSid        the SID the access point identity   the actual user's SID
                           maps to

   Four distinct AWS-owned addresses appeared in SubjectIP across eight calls in one burst.

4. FPolicy in mandatory + synchronous mode does not block the S3 Access Point path either.

   Configuration: a second FPolicy policy on the same SVM, scoped to one dedicated volume that
   has its own S3 Access Point. Engine type synchronous, aimed at an address with no listener on
   TCP 9898 (confirmed closed beforehand). Policy mandatory=true, priority 3. Events nfsv3 and
   cifs with create, write, delete, rename. ONTAP reported the engine as
   state=disconnected, disconnected_reason="TCP Connection to FPolicy server failed."

   Result, same volume, both paths:
     NFSv3 write                 : Permission denied, no file created
     S3 Access Point PUT         : succeeded
     S3 Access Point GET         : succeeded, content matched
     S3 Access Point LIST        : succeeded
     S3 Access Point DELETE      : succeeded

   Control: setting the same policy to enabled=false and repeating the identical NFS write
   succeeds. The denial was therefore FPolicy enforcement, not a permissions artefact.

   So the S3 Access Point path is not merely un-notified; it does not pass through the FPolicy
   gate at all. A mandatory-mode policy intended to deny an operation does not apply to it.

5. ARP does see this path, which sharpens the FPolicy question rather than softening it.

   With ARP (version 5.0) enabled, 150 high-entropy objects written through the S3 Access Point
   were recorded as ARP suspects with reason "High Entropy", and attack_probability moved to
   moderate. The suspect timestamps match the S3 Access Point write window. So the platform is
   able to observe this path; it is FPolicy specifically that does not.

6. The same holds with a synchronous engine that is accepting connections.

   Repointing the synchronous engine at a server that does accept connections, with
   mandatory=false, an NFSv3 write produced a SCREEN_REQ at the server (one per node), and the
   S3 Access Point PUT, GET and DELETE produced nothing at all in the same window. ONTAP's
   synchronous exchange was visible for the NFS operation: STATUS_QUERY_REQ carrying ReqId and
   ReqType=NFS_CREAT, then SCREEN_CANCEL with CancelReason "Cancel Timedout" once the server
   did not answer. So the engine is functioning and does hand file-protocol operations over for
   screening; S3 Access Point operations are simply not handed over.

Questions

Q1. Is it the intended and documented behaviour that data-plane operations arriving through an
    FSx for ONTAP S3 Access Point do not generate FPolicy notifications, and are not subject to
    a mandatory-mode FPolicy policy? If it is documented, please point me at the page. If it is
    not intended, please advise how to configure FPolicy so that these operations are covered.

Q2. Is it the intended behaviour that the ONTAP audit record for an operation arriving through
    an S3 Access Point carries SubjectUserName and SubjectDomainName as "Not Present", and a
    service-side address in SubjectIP rather than the requester's address? Is there any
    supported way to obtain the requesting IAM principal and the requester's source IP for
    these operations?

Q3. I found no audit event corresponding to the HEAD request. This is a single observation and I
    am not asserting it as a rule. Is HEAD (and are LIST / metadata-only operations generally)
    expected to be audited?

Q4. Is the behaviour in Q1 and Q2 specific to this ONTAP release, or does it apply to all
    releases that support FSx for ONTAP S3 Access Points?

Why this matters to my design

I am documenting the monitoring and audit story for an architecture in which data is collected
over an S3 Access Point and consumed over NFS and SMB. Real-time controls built on FPolicy -
ransomware detection, DLP, and any FPolicy policy in mandatory mode intended to block an
operation - would not see the writes that arrive over the S3 path. I need to know whether to
document that as a supported limitation or as a configuration error on my side.
```

---

## ケース 2 — 機能改善要望

ケース 1 を参照しつつ別ケースとして出す。質問として処理されず、要望として追跡されるようにするため
である。

**件名**: Feature request: FPolicy coverage and requester identity for FSx for ONTAP S3 Access Point access

**本文**:

```text
Reference: Case <case-id> (specification question, same environment and measurements)

Request 1 - FPolicy coverage of the S3 access path

  Today an FPolicy event can be configured for cifs, nfsv3 or nfsv4. There is no value for the
  S3 access path, and measurement confirms that operations arriving through an FSx for ONTAP
  S3 Access Point raise no FPolicy notification.

  This makes the following designs incomplete rather than merely partial, which is the reason
  for the request:

    - Ransomware and anomaly detection driven by FPolicy notifications. A write that arrives
      over the S3 Access Point is invisible to the detector, so an attack path exists that the
      control cannot observe. Note that ARP does observe this path, so the gap is specific to
      FPolicy-driven detection rather than to the storage layer as a whole - which is also why
      the gap looks closable.
    - Data loss prevention and content classification triggered on file creation.
    - Any FPolicy policy in mandatory mode intended to block an operation. Measured: with a
      mandatory policy whose engine is unreachable, an NFS write is denied while PUT, GET, LIST
      and DELETE through the S3 Access Point all succeed. The guarantee that mandatory mode is
      chosen for does not hold for this path.
    - Event-driven pipelines that use FPolicy as the change feed, given that S3 Event
      Notifications are not available for an FSx for ONTAP S3 Access Point.

  What I am asking for: an FPolicy event protocol value covering the S3 access path, with the
  same file_operations granularity as the existing protocols, so that one FPolicy policy can
  cover every path into a volume.

Request 2 - Requester identity in audit records

  Operations arriving through an S3 Access Point are recorded in the ONTAP audit log, but
  without the requester: SubjectUserName and SubjectDomainName are "Not Present" and SubjectIP
  holds a service-side address that varies between calls.

  This is a gap for any environment that has to answer "who accessed this file, and from
  where" from the audit trail. The operation is recorded; the acting party is not.

  What I am asking for: record the requesting IAM principal (or the requester ARN, or the S3
  access point request identity) and the requester's source IP in the audit record for
  operations that arrive through an S3 Access Point.

Priority from my side

  Request 1 is the higher priority. A missing audit attribute weakens an investigation after
  the fact; a monitoring path that produces no event at all means a real-time control silently
  does not apply.
```

---

## 回答が来たあと

| 回答が | 対応 |
|-------|------|
| 文書化された仕様で、該当ページがある | 検証記録にページを引用し、主張を「1 リリースでの実測」から「文書化済み」に上げる |
| 意図された挙動だが未文書 | 主張は「実測」に留め、未公開であることを明記する。「ドキュメント修正を提出した」という回答は、公開されるまで未反映として扱う |
| 意図されていない | サポートから提示された構成で再測定し、発火するなら検証記録を訂正する |
| 機能要望が受理された | 要望が追跡されている事実だけを記録する。内部チケット識別子は公開しない |
