
🌐 [日本語](../ja/verification-results-fpolicy-s3ap-and-session.md) | **English** (this page)

## Verification information

A record of three questions checked against real hardware.

1. Does reading or writing through an FSx for ONTAP S3 Access Point raise an FPolicy notification?
2. Do the same operations appear in ONTAP's native audit log?
3. Does the ONTAP session to an FPolicy server on ECS Fargate get cut after some number of hours or days?

Questions 1 and 2 diverge. **The operations do not appear in FPolicy and do appear in the ONTAP
audit log.** The identity recorded in the audit log, however, is not the same as it is for a
file-protocol access.

| Item | Value |
|------|-------|
| **Verification date** | `2026-08-26` (JST) |
| **Question 1 state** | Measurement complete (both UNIX / NFS and WINDOWS / SMB) |
| **Question 2 state** | Measurement complete |
| **Question 3 state** | Measurement in progress (about 0.8 hours elapsed as this page was written) |

### Verification environment

| Item | Value |
|------|-------|
| **AWS Region** | `ap-northeast-1` |
| **FSx for ONTAP file system ID** | `fs-0123456789abcdef0` |
| **Deployment type / throughput** | `SINGLE_AZ_1` / 128 MBps / SSD 1024 GiB |
| **ONTAP version** | `9.18.1P3D1` |
| **SVM name** | `verify-e2e-svm` (`svm-0123456789abcdef0`) | <!-- allow:naming: SVM resource name -->
| **UNIX test volume** | `fpolicy_s3ap_vol`, UNIX security style, 1 GiB |
| **NTFS test volume** | `fpolicy_s3ap_ntfs`, NTFS security style, 1 GiB |
| **Audit log destination volume** | `fpolicy_s3ap_auditlog`, UNIX security style, 1 GiB |
| **FPolicy stack** | `fpolicy-s3ap-verify` (`shared/templates/fpolicy-server-fargate.yaml`) |
| **FPolicy server** | ECS Fargate, 1 task, 0.25 vCPU / 512 MB, `linux/amd64` |
| **FPolicy protocol version** | `1.2` as negotiated |
| **ONTAP engine settings** | `asynchronous`, `ssl_option=no_auth`, `keep_alive_interval=PT2M`, `status_request_interval=PT10S` |

The FPolicy server code and template are the existing `shared/fpolicy-server/` and
`shared/templates/fpolicy-server-fargate.yaml`, with only the changes measurement required.
Those changes are listed under "Changes made for observation".

### Environment constraints, and the design changes they forced

| Constraint | What was done instead |
|------------|----------------------|
| A 128 MBps file system caps at 6 SVMs, so a dedicated SVM could not be created (`ServiceLimitExceeded`) | An existing SVM was used with the FPolicy policy scope pinned to the test volumes only. Every notification received is therefore attributable to this test |
| The VPC has no NAT gateway and no interface endpoint for ECR, CloudWatch Logs or SQS | The task was placed in a subnet routed to an internet gateway with `AssignPublicIp=ENABLED` for egress. The task security group admits inbound only from the SVM security group, and egress only on 443 |
| The SVM's AD domain had no reachable domain controller | The SMB control and the WINDOWS-identity S3 Access Point used a local SMB user on the SVM. No domain account turned out to be needed (see "Incidental findings") |
| Creating the S3 Access Point with `fsxadmin` as the UNIX identity failed | `root`, which exists as a local UNIX user on the SVM, was used instead |

---

## 1. S3 Access Point I/O and FPolicy notifications

### 1-1. Conclusion

**Data operations through an FSx for ONTAP S3 Access Point raised no FPolicy notification.**
The result is the same for UNIX identity (UNIX volume) and WINDOWS identity (NTFS volume).
File-protocol operations (NFSv3 / SMB) against the same volume, in the same FPolicy session, did.

The conclusion rests on two independent grounds.

| Ground | Content |
|--------|---------|
| Structural | An FPolicy event on ONTAP 9.18.1P3D1 accepts exactly three `protocol` values — `cifs`, `nfsv3`, `nfsv4`. There is no value corresponding to S3 or object access |
| Measured | S3 Access Point data-plane calls produced 0 notifications; file-protocol operations against the same volume immediately after produced notifications |

### 1-2. Enumerating the accepted protocol values

To keep "no notification arrived" distinguishable from "the configuration never covered it",
the protocol values that can be watched were enumerated against the running cluster. Each
candidate was POSTed individually and only the ones that were created counted as accepted.

```bash
# Create an event per candidate, deleting the ones that succeed to leave the cluster as found
for proto in cifs nfsv3 nfsv4 nfsv4.1 s3 S3 smb nfs object http fcp iscsi; do
  echo "--- $proto"
done
```

| protocol value | Result |
|----------------|--------|
| `cifs` | Accepted |
| `nfsv3` | Accepted |
| `nfsv4` | Accepted |
| `nfsv4.1` / `nfsv41` / `nfsv4_1` | Rejected |
| `s3` / `S3` | Rejected |
| `smb` / `nfs` / `object` / `http` / `fcp` / `iscsi` | Rejected |

A rejection is HTTP 400 with a body of the form `"s3" is an invalid value for field "protocol"`.

File operations were probed the same way. `cifs` and `nfsv4` each accept 12
(`create` `create_dir` `delete` `delete_dir` `getattr` `open` `close` `read` `write`
`rename` `rename_dir` `setattr`). `nfsv3` rejects `getattr` `open` `close` `access` and
instead accepts `link` `lookup` `symlink`, also 12.

### 1-3. Monitoring configuration

One event per accepted protocol was created with every file operation that protocol allows,
collected into a single policy scoped to the test volumes. `read` is included — and `open`,
`close` and `getattr` for `cifs` and `nfsv4` — so the read path is not silently uncovered.

| Setting | Value |
|---------|-------|
| events | `verify_cifs_all_ops` / `verify_nfsv3_all_ops` / `verify_nfsv4_all_ops` |
| policy | `verify_policy`, `mandatory=false`, `priority=2` |
| scope | `include_volumes: [fpolicy_s3ap_vol, fpolicy_s3ap_ntfs]` |
| engine | one external server, TCP 9898 |

The policy scope has to be given inline when the policy is created; POSTing the scope
separately returns 404. An enabled policy also cannot be modified, so changing events or scope
later means disable, change, enable — and operations in that window are not captured.

### 1-4. Measurement procedure

A quiet window, then S3-Access-Point-only I/O, then a settle, then a file-protocol control.
The control runs last because if the control fires, "the configuration never covered it" is
no longer available as an explanation.

```text
PHASE 0  purge the queue and perform no I/O of any kind for 90 seconds
PHASE 1  S3 Access Point only: PUT x3, GET x3, HEAD x1, LIST x1, DELETE x1
PHASE 2  settle 60 seconds, then count
PHASE 3  create / read / delete over a file protocol against the same volume (control)
```

### 1-5. Results

UNIX identity (UNIX volume, control over NFSv3):

| Phase | Operation | FPolicy notifications |
|-------|-----------|----------------------|
| PHASE 0 | none (90 s) | 0 |
| PHASE 1 | 9 S3 Access Point data-plane calls | **0** |
| PHASE 3 | NFSv3 create / read / delete | **3** |

WINDOWS identity (NTFS volume, control over SMB 3.0):

| Phase | Operation | FPolicy notifications |
|-------|-----------|----------------------|
| PHASE 0 | none (90 s) | 0 |
| PHASE 1 | 9 S3 Access Point data-plane calls | **0** |
| PHASE 3 | SMB create / read / delete | **10** |

The SMB control reaches 10 because the `cifs` event also watches `open`, `close` and `getattr`,
so one file operation yields several notifications. That makes the zero on the S3 Access Point
side sharper, not weaker.

In neither PHASE 1 window did the server log contain any line other than KeepAlive. No
unrecognised message type arrived either.

That the S3 Access Point writes really reached the volume was confirmed from the file-protocol
side: of the three keys PUT, the one that was DELETEd is gone and the other two exist as files
with matching contents. The I/O completed; the absence of a notification is not the absence of
a write.

### 1-6. Not blocked in mandatory + synchronous mode either

"No notification arrives" and "the operation cannot be blocked" are different claims. With
`mandatory=true`, ONTAP denies client access when the external server does not answer. That
property turns the question "does the S3 Access Point path pass through the FPolicy gate" into
something checkable **by whether the operation is denied**, rather than by whether a notification
appears.

The configuration is below. A second policy, separate from the existing asynchronous one, was
created against a single dedicated volume.

| Setting | Value |
|---------|-------|
| engine | `type=synchronous`, aimed at an **address with no listener** (TCP 9898 confirmed closed beforehand) |
| policy | `mandatory=true`, priority 3, scope pinned to the one dedicated volume |
| events | `nfsv3` and `cifs`, with `create` `write` `delete` `rename` |
| ONTAP-side state | `state=disconnected`, `disconnected_reason="TCP Connection to FPolicy server failed."` |

With the policy in that state, the same volume was accessed over both paths.

| Path | Result |
|------|--------|
| NFSv3 write | **`Permission denied`. No file was created** |
| S3 Access Point PUT | **succeeded** |
| S3 Access Point GET | **succeeded** (content matched) |
| S3 Access Point LIST | **succeeded** |
| S3 Access Point DELETE | **succeeded** |

That the denial came from FPolicy was confirmed by a control: setting the policy to
`enabled=false` and repeating the identical NFS write succeeds. The earlier `Permission denied`
was therefore FPolicy enforcement, not a side effect of the permission settings.

**So the S3 Access Point path does not merely produce no notification — it does not pass through
the FPolicy gate at all.** A design that blocks an operation in mandatory mode does not apply to
this path. For a security design that assumes blocking, this is heavier than a missing
notification.

As a by-product, adding and enabling a second policy did not disconnect the existing policy's
sessions: `update_time` and `session_uuid` stayed unchanged. Connections are per policy, so
adding another policy does not destroy an in-flight observation window.

### 1-6-1. Not notified by a responding synchronous engine either

One possibility remains: that the operation is simply not blocked because nothing reached the
server, and that a reachable configuration would be notified. To close it, the synchronous
engine was repointed at a **server that does accept connections**, with `mandatory=false`
(fail-open), and the measurement repeated.

| Path | Observed at the server |
|------|-----------------------|
| NFSv3 write | **A notification arrived.** A `SCREEN_REQ` was received and forwarded to SQS (one from each node, two in total) |
| S3 Access Point PUT / GET / DELETE | **Nothing arrived.** The log for that window contains no line other than KeepAlive |

The synchronous exchange was also observable. When the server does not answer, ONTAP sends a
`STATUS_QUERY_REQ` (carrying `ReqId` and `ReqType=NFS_CREAT`) and waits, then sends a
`SCREEN_CANCEL` with `CancelReason: Cancel Timedout`. With `mandatory=false` the operation still
went through, and the file was created at 0 bytes.

So the synchronous engine is working and does hand file-protocol operations over for screening.
**S3 Access Point operations are still not handed over.** They are not un-blocked because nothing
reached the server; they are not subject to FPolicy in the first place.

### 1-7. What this measurement does not answer

| Question | State |
|----------|-------|
| Does the same hold on other ONTAP versions? | Not measured. `9.18.1P3D1` only |
| Does the same hold for ONTAP native S3, as opposed to an FSx S3 Access Point? | Not measured |
| Does it fire on the FlexCache cache side? | Not measured. FlexCache is not part of this verification |

---

## 2. S3 Access Point I/O and the ONTAP native audit log

### 2-1. Conclusion

**Data operations through an S3 Access Point were recorded in ONTAP's native audit log.** The
result is the opposite of FPolicy's. They appear as audit events with `Source` set to `HTTP`,
carrying the file name, the operation, and for reads and writes the offset and byte count.

**The identity of the requester, however, is not recorded.** That is the second finding, alongside
the FPolicy gap.

### 2-2. Configuration

Auditing is a separate mechanism from FPolicy and has to be enabled separately. ONTAP file
auditing records only operations on objects carrying an audit ACE (SACL), so enabling auditing
alone records nothing.

| Setting | Value |
|---------|-------|
| Audited events | `file_operations`, `cifs_logon_logoff`, `audit_policy_change` |
| Log format / rotation | `xml` / 10 MiB |
| Log destination | a separate volume, not one of the audited volumes |
| SACL | `Everyone` / `audit_success` / `full_control` applied to `this_folder`, `files` and `sub_folders` at the NTFS volume root |

The SACL can be applied over REST with
`POST /protocols/file-security/permissions/{svm}/{path}/acl`. It runs as an asynchronous job
whose completion message carries the number of files modified.

### 2-3. Results

The audit log was parsed as XML and aggregated by `Source` and `EventName`. The SMB control ran
after the S3 Access Point operations, so the possibility that the S3-side records came from a
mis-set SACL is excluded by the control having been recorded at all.

| Source | Operation | Audit events |
|--------|-----------|--------------|
| `HTTP` (S3 Access Point) | PUT x4 | `Create Object` (4656) x4 |
| `HTTP` (S3 Access Point) | GET x3 | `Read Object` (4663) x3, with `ReadOffset` and `ReadCount` |
| `HTTP` (S3 Access Point) | DELETE x1 | `Unlink Object` (9998) x1 |
| `S3` (S3 Access Point) | LIST x3 | `S3A List Object` (4663) x3, recorded against the volume root rather than an object |
| `HTTP` (S3 Access Point) | HEAD x6 | **no record** |
| `CIFS` (control) | create / read / delete | 8 events. `Open Object` x2 / `Open Object with Delete Intent` / `Set Object Attributes` x2 / `Get Object Attributes` x2 / `Write Object` |

`Source` takes three values. Operations on an object are `HTTP`; a LIST is `S3`. No single value
selects the whole access path.

HEAD was issued six times and produced no record. Stating that HEAD is never audited would still
need confirmation on another release.

### 2-3-1. How these numbers were wrong twice

The figures in this section were wrong once. Two causes, both of the same shape: **reporting what
could not be collected as though it were absent.** Kept here as a caution for anyone building a
procedure that reads these logs.

| Cause | Symptom | Remedy |
|-------|---------|--------|
| NFS client-side caching | Reading the audit log over NFS does not show what ONTAP has appended since. The event count comes out low | Remount with `noac`, or remount immediately before reading |
| The collection path's output cap | Carrying the whole log out as base64 is truncated at the cap, and **the newest records are the ones lost** | gzip before transfer, and reconcile the received byte count against the source file |

In both cases the result came back without an error and looked like a legitimately smaller count.
**The byte count has to be reconciled against the source before the events are counted.**

### 2-4. The identity that is recorded differs by path

On the same volume with the same audit configuration, the recorded identity changes with the
access path.

| Audit record field | Through the S3 Access Point (`Source=HTTP`) | Through a file protocol (`Source=CIFS`) |
|--------------------|--------------------------------------------|----------------------------------------|
| `SubjectUserName` | `Not Present` | the actual user name |
| `SubjectDomainName` | `Not Present` | the actual domain (the SVM's local domain name) |
| `SubjectUserIsLocal` | `false` | `true` |
| `SubjectIP` | an AWS-owned public address. It varies between calls and is not the requester's address | the actual client's IP |
| `SubjectUnix` | `Uid=65535 Gid=65535` | the actual UID / GID |
| `SubjectUserSid` | the SID the S3 Access Point identity maps to | the actual user's SID |

So "who" and "from where" cannot be reconstructed from the audit log. The file touched and the
operation performed are recorded, but neither the requesting IAM principal nor the requester's
network address can be recovered. A design that uses the audit log to trace the acting party
does not hold for the S3 Access Point path.

The address in `SubjectIP` is the S3 service side. It describes how the request reached the
volume, not who issued it. Four distinct addresses were recorded across the eight calls of a
single burst.

---

---

## 3. S3 Access Point I/O and autonomous ransomware protection (ARP)

### 3-1. Conclusion

**ARP detected files written through the S3 Access Point.** Unlike FPolicy, it sees this path.
The detection reason is the entropy of the file content, which is independent of the access path.

This cluster reports ARP version `5.0` on both nodes. That generation requires no learning
period, which made a same-session comparison possible immediately after enabling it.

### 3-2. Measurement procedure

Two volumes with ARP enabled, each written through exactly one path with the same pattern: many
high-entropy files carrying a never-before-seen extension.

| Volume | Write path | Content |
|--------|-----------|---------|
| A | S3 Access Point only | 64 KiB of `/dev/urandom` x 150 objects, novel extension, over about 7 minutes |
| B | NFSv3 only | The same 150 files. Once as a ~2 second burst, once spread over ~5 minutes |

### 3-3. Results

| Volume | Path | ARP suspects | Reason | `attack_probability` |
|--------|------|-------------|--------|---------------------|
| A | S3 Access Point | **150** | all `High Entropy` | `moderate` |
| B | NFSv3 | **204** | all `High Entropy` | `moderate` |

The suspects on A carry `suspect_time` from `00:31:40` to `00:38:32`, matching the S3 Access
Point write window (`00:31:33`–`00:38:28`). Each suspect record carries the file path and the
extension.

**Both paths were detected.** ARP looks at file content, and no difference by path was observed.

### 3-4. Detection is not immediate, and a short window manufactures a false negative

One reading during this measurement was wrong. Four minutes after the burst on B (150 files in
two seconds), `attack_probability` read `none`, which was read as "ARP does not see a short NFS
burst". In fact **all 150 of those files were already recorded as suspects.**
`attack_probability` only moved to `moderate` about 14 minutes after the writes.

| What is observed | Lag |
|------------------|-----|
| Suspect records | seconds after the first write |
| `attack_probability` | more than 10 minutes after the writes begin |

`attack_probability` alone must not be read as "not detected". The suspect list has to be read.

### 3-5. What this measurement does not answer

| Question | State |
|----------|-------|
| Can ARP block a write arriving through the S3 Access Point? | Not measured. This covers detection only; blocking was not attempted |
| Is the path equivalence the same for detection reasons other than high entropy? | Not measured. Only `High Entropy` was observed |
| Does the same hold on other ONTAP or ARP versions? | Not measured. ARP `5.0` only |

## 4. The three mechanisms side by side

| | FPolicy | ONTAP native audit log | ARP |
|---|---|---|---|
| Detects operations through an S3 Access Point | **No** | **Yes** | **Yes** |
| Can block an operation through an S3 Access Point | **No** — it passes even in mandatory + synchronous mode | Not applicable (auditing does not block) | Not measured |
| Detects operations through a file protocol | Yes | Yes | Yes |
| Can block an operation through a file protocol | Yes (measured in mandatory mode) | Not applicable | Not measured |
| What the detection is based on | The configured protocol and file operations | The presence of a SACL | The entropy of the file content and its extension |
| How the watched protocol is specified | `cifs` / `nfsv3` / `nfsv4` only; no value corresponds to S3 | No per-protocol setting; determined by the presence of a SACL | No protocol setting exists |
| Identifies the requester for S3 Access Point access | Not applicable (there is no notification) | Not recorded (`Not Present`) | Not applicable (recorded per file) |
| Detection lag | Immediate (observed at 0.3 s) | Minutes, until the log is written out | Suspects in seconds; `attack_probability` more than 10 minutes |
| Purpose | Real-time notification, and blocking in mandatory mode | Retrospective audit record | Detecting ransomware-like write patterns |

The statement that "an S3 Access Point translates what it receives over the S3 API into file
operations, so auditing is not bypassed" **holds for the ONTAP native audit log and for ARP, and
does not hold for FPolicy.** For the audit log, what is recorded is the operation, not the
requester.

So the answer differs per mechanism. **Both "every storage-layer control applies" and "nothing
sees the S3 path" are wrong.** Which mechanism to use depends on what is to be guaranteed.

---

## 5. FPolicy session continuity

### 5-1. Current state

The measurement is still running. Only the following is settled as this page is written.

| Item | Value |
|------|-------|
| Observation window start | `2026-08-25T17:16:53Z` (when the reconnection after the scope change completed; any earlier window is void) |
| Elapsed as written | about 0.5 hours |
| 72-hour mark | `2026-08-28T17:16:53Z` |
| Spontaneous disconnects | 0 as written |
| Server-side idle timeouts | 0 |
| Measured KeepAlive interval | 120.4–120.5 s, matching the engine's `keep_alive_interval=PT2M` |

At this elapsed time "the session is not cut" cannot be written. That is answerable only once
the 72-hour window is filled.

### 5-2. Removing the confounds

A session-cut measurement is easy to confuse with events that look identical. These were separated.

| Event it could be confused with | How it was separated |
|--------------------------------|---------------------|
| A close caused by the server's own idle timeout | `SOCKET_TIMEOUT_SEC` raised to 3600 s, and the timeout log line worded differently from a peer close |
| A Fargate task replacement | Server process start lines are counted; a start begins a new observation window |
| A disconnect caused by re-registering the engine IP | A stack that does not automate the IP update (no NLB) was chosen, and the engine IP registered by hand |
| A disconnect caused by our own configuration change | Judged from the reason string on ONTAP EMS `fpolicy.server.disconnect` |

This separation already earned its place. A disconnect is recorded on both nodes before the
measurement window, and the EMS reason is
`FPolicy server is removed from external engine.` — which is the policy disable performed to
add an event. Reading the server log alone would have counted it as a spontaneous cut.

### 5-3. Correlating both records

`shared/scripts/fpolicy-session-report.py` reads the server-side CloudWatch Logs and the
ONTAP-side EMS over the same window and reports session durations, which side closed, the
widest KeepAlive interval, and the disconnect reason.

```bash
# Tunnel to the ONTAP management endpoint through the bastion
aws ssm start-session --region ap-northeast-1 --target <bastion-instance-id> \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["<ontap-mgmt-ip>"],"portNumber":["443"],"localPortNumber":["8443"]}'

# Correlate both sides over the 72-hour window
python3 shared/scripts/fpolicy-session-report.py \
  --log-group /ecs/fpolicy-s3ap-verify \
  --since 2026-08-25T17:16:53Z \
  --ontap-url https://127.0.0.1:8443 \
  --keepalive-gap-threshold 300
```

A long observation needs no resident process. A cut lands as one line in CloudWatch and its
reason lands in ONTAP EMS; both can be re-queried over the same window afterwards. Log
retention is 30 days.

For detection while unattended, a metric filter matches `Connection closed by peer` and the
alarm `fpolicy-s3ap-verify-session-dropped` fires on it. An alarm firing is not by itself a
spontaneous cut — the EMS reason has to be read first.

### 5-4. What would justify calling it periodic

Claiming a cut "at a specific hour or day" needs at least two intervals between cuts. Stopping
at the first cut yields no interval at all. So the measurement does not stop on detection; it
continues to the second cut and measures the gap. If 72 hours pass with zero cuts, what can be
written is "did not occur within 72 hours", not "is not cut".

---

## 6. Changes made for observation

The diff against the existing code is below. Every default preserves the previous behaviour.

| Change | Reason |
|--------|--------|
| `SOCKET_TIMEOUT_SEC` environment variable (default 300 s) | Keeps a server-side timeout from being indistinguishable from an ONTAP-side cut. Raised only when measuring session duration |
| On close, log `uptime` and the KeepAlive / event / other counts | To tell a quiet session apart from a session that ended |
| Word a peer close and a server-side timeout differently | Same reason. Identical wording cannot be aggregated afterwards |
| Add a sequence number and the interval since the previous KeepAlive | So the length of a gap in the log can be established afterwards |
| Extract `protocol` and client IP from a notification into the log and the SQS payload | To record which protocol a notification was attributed to |
| Log an unrecognised message type with its full header and the head of its body | When an unexpected notification arrives the header is the finding, so it is not truncated |
| Parameterise `AssignPublicIp` in the template (default `DISABLED`) | To obtain egress in a VPC with neither NAT nor interface endpoints |

`protocol` came through as `unreported` for both the NFSv3 and the SMB notifications here. ONTAP
does not carry a protocol name in the XML body at this protocol version; this is not an
extraction defect.

---

## 7. Incidental findings

| Finding | Content |
|---------|---------|
| A WINDOWS-identity S3 Access Point did not need a domain account | It was created with the name of a local SMB user on the SVM. Creation succeeded, and the data plane worked, with no reachable domain controller. The AD join remains configured on the SVM |
| `fsxadmin` cannot be used as the UNIX identity of an S3 Access Point | Creation ends `FAILED` with `Failed to lookup the provided user in ONTAP`. `fsxadmin` is a cluster administration account, not a UNIX user in the SVM's name services |
| Attaching an S3 Access Point stands up an ONTAP S3 server on the SVM | An attached SVM reports `s3.enabled=true` with a server named `amazon-fsx-<svm>.<region>.amazonaws.com` |
| The measured KeepAlive interval is 120 seconds | The "about 6 second intervals" in an earlier record did not reproduce. What arrives every 10 seconds is the STATUS_REQ from `status_request_interval=PT10S`, not a KeepAlive |
| An S3 Access Point whose creation failed still needs detaching | A `FAILED` attachment still exists as an attachment, so `detach-and-delete-s3-access-point` is required before recreating it |
| Notifications cannot be correlated by file name | Reading a file over a file protocol that was written through the S3 Access Point produces a notification for that file name at read time. Correlating by name makes the S3-side write look as though it fired. Correlation has to be by timestamp |
| Adding a local SMB user to a group takes the SID, not the group name | Putting `BUILTIN\Administrators` in the path returns 404. Use `S-1-5-32-544` |
| An audit-record count depends on how it is counted | A line-based `grep -c` and an extraction of `<Event>` elements do not always agree. Aggregate by element extraction, and reconcile the bytes read against the source file |
| This repository's audit-log parser had three defects | Found by running real audit records through it. All three predate this verification. (1) The client IP was read from `IpAddress`, but ONTAP emits `SubjectIP`, so it was **empty on every event**. (2) The operation was read from `ObjectType`, so every file event reported `File` and the real operation name was lost. (3) `Source` was not carried through, so an S3-access-path event could not be told from a file-protocol one. In addition, `Not Present` in `SubjectUserName` passed through as if it were a user name |
| The existing tests assumed field names that do not exist | The parser tests used `IpAddress`, and an `ObjectType` carrying an operation. ONTAP emits neither. **The tests passed because they verified an assumed schema rather than a measured one.** Cases holding captured records have been added |

---

## 8. What to confirm with AWS Support

The behaviours established here are split into what should be confirmed as documented
specification and what should be raised as an improvement. The wording is in
`docs/en/support-inquiry-s3ap-audit-coverage.md`.

| Class | Content |
|-------|---------|
| Specification | Is it intended that operations arriving through an S3 Access Point raise no FPolicy notification? Is it documented? |
| Specification | Is it intended that the audit log's `SubjectUserName` and `SubjectDomainName` are `Not Present`, and that `SubjectIP` is an AWS service address rather than the requester's? |
| Specification | Is it intended that a HEAD request produces no audit event? |
| Improvement | Let FPolicy also monitor operations arriving over the S3 access path, since a design that performs ransomware detection or DLP in real time will otherwise miss writes made through an S3 Access Point |
| Improvement | Record the requesting IAM principal and the real source IP in the audit log |

---

## 9. Teardown order

```bash
# 1. Disable FPolicy and auditing before deleting anything
#    fpolicy policy disable -> delete engine -> delete events; audit disable
# 2. Delete the Fargate stack
aws cloudformation delete-stack --stack-name fpolicy-s3ap-verify --region ap-northeast-1
# 3. Detach both S3 Access Points (mandatory before deleting the volumes)
aws fsx detach-and-delete-s3-access-point --name fpolicy-verify-ap --region ap-northeast-1
aws fsx detach-and-delete-s3-access-point --name fpolicy-verify-ap-win --region ap-northeast-1
# 4. Delete the three test volumes
# 5. Unmount NFS and SMB, delete the local SMB user, its secret and the scoped IAM policy
# 6. Delete the metric filters and the alarm
```

Deleting a volume that still has an S3 Access Point attached fails. And deleting the external
server while the FPolicy policy is still enabled leaves ONTAP emitting
`fpolicy.server.disconnect` against an address that no longer answers. The audit log
destination volume is deleted after auditing has been disabled.
