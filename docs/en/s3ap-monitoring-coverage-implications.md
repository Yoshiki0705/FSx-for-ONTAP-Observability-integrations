
🌐 [日本語](../ja/s3ap-monitoring-coverage-implications.md) | **English** (this page)

## Purpose of this page

What FPolicy and the ONTAP native audit log can see of access through an FSx for ONTAP S3 Access
Point diverged when measured. This page organises the consequence by design pattern.

The measurement and its conditions are in
[verification-results-fpolicy-s3ap-and-session.md](verification-results-fpolicy-s3ap-and-session.md).
This page is not the record of the measurement; it is the design decisions that follow from it.

## The measurement this rests on

| Path | FPolicy notification | Blocking via FPolicy | ONTAP audit log | Requester in the audit log | Detected by ARP |
|------|---------------------|---------------------|-----------------|---------------------------|-----------------|
| NFS / SMB | fires | possible | recorded | actual user and IP | yes |
| S3 Access Point | **does not fire** | **not possible** | **recorded** | **not recorded** | **yes** |

**The answer differs per mechanism.** Both "every storage-layer control applies" and "nothing
sees the S3 path" are wrong.

Measured on ONTAP 9.18.1P3D1, with both UNIX and WINDOWS identity, with FPolicy both
asynchronous and synchronous + mandatory, and with ARP version 5.0.

## Design patterns affected

### Pattern 1: FPolicy as the change-detection feed

Because S3 Event Notifications are unavailable for an FSx for ONTAP S3 Access Point, FPolicy has
been widely proposed as the event source instead. **When the writes arrive through the S3 Access
Point, nothing flows down that feed.**

If the writes arrive over NFS or SMB it still works as before. What breaks is only the
combination "write over S3, detect with FPolicy".

| Alternative | Suited when | Constraint |
|-------------|-------------|------------|
| Use the ONTAP native audit log as the feed | Auditing is already on, or can be turned on, and a delay of minutes is acceptable | A SACL has to be applied. The log is a file on a volume, so a separate path is needed to collect it |
| Scheduled polling (listing diffs) | The object count is moderate and minutes of detection delay are acceptable | LIST cost and duration grow with the object count |
| Emit the event from the writer | You own the writing application | It is no longer a storage-layer guarantee, and writes that bypass the application are missed |
| Move the write path to NFS / SMB | Nothing in the design requires the S3 API | This reconsiders the reason for using an S3 Access Point at all |

### Pattern 2: FPolicy as a real-time security control

Ransomware detection, DLP, content classification and antivirus integration driven by FPolicy
notifications. **If the same volume has an S3 Access Point attached, writes over that path never
reach the detector.** A write path exists that the detector cannot observe.

This is not a latency problem; it is an invisible-path problem. **What substitutes for it depends
on the purpose.**

| Purpose | Substitute on the S3 Access Point path |
|---------|---------------------------------------|
| Ransomware detection | **ARP works.** It was measured detecting high-entropy files written through the S3 Access Point. It inspects file content, so it does not depend on the path |
| DLP and content classification | ARP is not a classifier and does not substitute. Either post-process from the audit log, or control at the S3 side |
| Blocking an operation | Not expressible in the storage layer. Do it at the S3 side (access point policy, IAM) |
| Change-detection feed | See pattern 1 |

For anything other than ransomware detection, the options are:

1. Do not attach an S3 Access Point to a volume that needs the real-time control. Keep the
   control's premise true at the volume level.
2. Put a separate gate on the S3 side. Restrict who can write using the access point policy and
   IAM.
3. Move to retrospective detection from the audit log. Real-time behaviour is lost, but both
   paths are visible.
4. Accept the exposure and state in the design document that writes through the S3 Access Point
   are undetected.

Whichever is chosen, **"FPolicy is deployed, so every path is observed" is not a statement that
holds.** Nor is the converse: ARP and the audit log do see the S3 path.

### Pattern 3: FPolicy mandatory mode as an enforcement boundary

`mandatory=true` denies access when the external server does not answer. That works for the file
protocols (measured: `Permission denied`). **Operations through the S3 Access Point all succeeded
under the same policy — PUT, GET, LIST and DELETE.**

Mandatory mode is chosen precisely to guarantee that everything passes the gate, so a single path
that does not pass invalidates the reason for choosing it. To extend an enforcement boundary over
the S3 Access Point path, it has to be expressed at the S3 layer (access point policy, IAM, and
equivalent controls) rather than in the storage layer.

### Pattern 4: Answering "who accessed what" from the audit log

Operations through the S3 Access Point are in the audit log, but `SubjectUserName` and
`SubjectDomainName` are `Not Present` and `SubjectIP` is an AWS service-side address. **The file
and the operation are known; the requester is not.**

| What is wanted | Obtainable | How |
|----------------|-----------|-----|
| Which file was operated on, when, and how | Yes | ONTAP audit log |
| Which IAM principal made the request | Not from the audit log | CloudTrail data events on the S3 Access Point |
| The requester's network address | Not from the audit log | Same |
| Granularity tied to an identity | Only as fine as the access points are split | Split access points per purpose or per principal |

The design becomes a correlation of two logs by timestamp. Correlating by file name is wrong:
reading the same file over a file protocol later produces a record for that same file name at the
read time.

### Pattern 5: The ONTAP native audit log as the visibility path for S3 access

Within what was measured there are two ways to see S3-Access-Point operations at the storage
layer: the audit log and ARP. The audit log records every operation; ARP detects only
ransomware-like patterns. Which to use depends on what is to be known. Points to note when
adopting the audit log:

| Note | Content |
|------|---------|
| A SACL is required | Enabling auditing alone records nothing. An audit ACE has to be applied to the target |
| `Source` takes three values | Object operations are `HTTP`, a LIST is `S3`, file protocols are `CIFS` / `NFS`. No single value selects the path |
| HEAD was not recorded | Six calls, zero records. An existence check may not be traceable |
| The requester is not recorded | As in pattern 4 |
| The log is a file on a volume | A separate collection path is needed. If NFS is used to read it, beware client-side caching |

## How to choose

```text
I want to detect writes that arrive through the S3 Access Point
  |- I need to block them in real time
  |    -> not expressible in the storage layer. Do it at the S3 layer (access point policy / IAM)
  |- I need real-time detection but not blocking
  |    -> no mechanism today. Fall back to retrospective audit-log detection, or revisit the write path
  |- Knowing after the fact is enough
       -> ONTAP native audit log. Apply a SACL and use Source to tell the paths apart

I want to identify the requester behind an S3 Access Point access
  |- I want it entirely from storage-layer logs
  |    -> not possible. The audit log does not carry the requester
  |- I can correlate two logs
       -> audit log (what and when) + CloudTrail data events (who), correlated by timestamp
```

## Where this judgement applies

| Item | Range |
|------|-------|
| ONTAP version | Measured on `9.18.1P3D1` only. Other releases unconfirmed |
| Identity type | Same result for UNIX and WINDOWS |
| FPolicy mode | Asynchronous, and synchronous + mandatory |
| FPolicy mode | Asynchronous, synchronous + mandatory, and a synchronous engine that accepts connections |
| ARP | Detection confirmed on version 5.0, the generation that needs no learning period. Blocking not measured |
| Unconfirmed | Behaviour on the FlexCache cache side, ONTAP native S3 (as distinct from an FSx S3 Access Point), the coverage of Vscan, and blocking by ARP |

Vscan is a mechanism separate from both FPolicy and ARP, and **this measurement says nothing about
Vscan.** That FPolicy does not see the S3 path must not be used as grounds for claiming that Vscan
does not either.

ARP needs the opposite caution. Detection was measured; **blocking was not.** That it can detect
must not be used as grounds for claiming it can block. And `attack_probability` lags the writes by
more than ten minutes, so reading only that value over a short window produces a false negative.
The suspect list has to be read.
