# Application-layer signal correlation (self-managed portal)

🌐 [日本語](docs/ja/setup-guide.md) | [English](docs/en/setup-guide.md)

Correlates FSx for ONTAP audit records with signals emitted by an application you
built yourself. The reference application is the Amplify Gen2 file portal in
[FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns)
(`solutions/amplify-portal`), but nothing here is specific to it.

> **Status: not yet verified end to end.** The module and the stack pass their
> unit tests and the policy gates. No throughput, latency or cost figure is
> published on this page yet, because none has been measured. See
> [What is not verified](#what-is-not-verified).

## The gap this fills

The other nine integrations in this repository ship signals that **FSx for ONTAP
emits**: the ONTAP audit log, EMS events, FPolicy notifications. This one adds a
fourth source that FSx for ONTAP cannot produce — the application's own view of
what it asked the storage to do.

That matters because of a measured property of the audit log, recorded in
[s3ap-monitoring-coverage-implications.md](../../docs/en/s3ap-monitoring-coverage-implications.md):
for an operation arriving through an FSx for ONTAP S3 access point,
`SubjectUserName` and `SubjectDomainName` are the literal string `Not Present`
and `SubjectIP` is an AWS service-side address. **The file and the operation are
known; the requester is not.**

That page's answer is CloudTrail data events on the access point, which name the
IAM principal. For a web application reading the access point on behalf of many
people, every request uses the same Lambda execution role — so CloudTrail's
answer is identical for every user, and "which person opened this file" stays
unanswered by both logs.

| Question | Audit log | CloudTrail data events | Application signal |
|---|---|---|---|
| Which object, which operation, when | yes | yes | yes |
| Which IAM principal | no | yes | n/a |
| **Which end user of the application** | no | **no** (one role for everyone) | **yes** |
| Which volume and SVM | yes | no | no |
| Whether it arrived over S3 rather than SMB/NFS | yes (`Source`) | implied | n/a |

## How the correlation works

There is **no free-form field in an ONTAP audit record**, so a trace ID cannot be
propagated into one. The normalized schema is exactly `timestamp`, `event_type`,
`source`, `svm`, `user`, `domain`, `client_ip`, `operation`, `access_protocol`,
`path`, `result`, `raw` (see
[`shared/python/ontap_audit_parser.py`](../../shared/python/ontap_audit_parser.py)).

So the key is **derived independently on both sides** from what both already
know, and time is matched as a window because the two timestamps come from
different clocks:

```
join key = <normalized operation> | <normalized object key>

  application side   emit_s3ap_app_signal(object_key="data/object.txt", operation="PUT")
                       -> "PUT|data/object.txt"

  audit side         ObjectName "(vol1);/data/object.txt", EventName "Create Object"
                       -> "PUT|data/object.txt"
```

The volume prefix `(vol1);` and the leading slash are stripped; `Create Object`
and `Write Object` both map to `PUT`.

### Why the protocol filter is not optional

The coverage page warns that correlating on file name alone is wrong: **a later
read of the same file over SMB or NFS produces a record with that same file name
at that later time.** The correlator keeps only records whose `Source` is `HTTP`
(object operations) or `S3` (bucket-level operations such as LIST). Remove that
filter and the join starts attributing SMB writes to whoever last opened the file
in the portal.

`FileProtocolRecordsSkipped` counts what the filter excluded, so the exclusion is
visible rather than implicit.

## Two adoption tiers

**Tier 1 — no application change.** Enable X-Ray Active tracing on the
application's existing Lambdas (`aws lambda update-function-configuration
--tracing-config Mode=Active`, a configuration change requiring no code change),
turn on CloudTrail data events for the access point, and run this correlator. You
get object, operation, timing and IAM principal correlated across the two logs.
**You do not get end-user attribution.**

**Tier 2 — the application adopts the module.** The application calls
`emit_s3ap_app_signal` from
[`shared/python/observability.py`](../../shared/python/observability.py), which is
dependency-free standard library only. The end-user identifier joins the picture.

## Contents

| Path | What it is |
|---|---|
| `template.yaml` | Correlator Lambda, EventBridge Scheduler, SSM checkpoint, DLQ, three alarms, dashboard |
| `lambda/handler.py` | Reads newly rotated audit files, filters to the S3 access path, emits the audit side of the join |
| `scripts/deploy.sh` | Deploys the stack and uploads the handler, then verifies the placeholder is gone |
| `scripts/cleanup.sh` | Deletes the stack and reports residuals plus the FSx-side deletion order |
| `tests/` | 20 unit tests |

The instrumentation module itself lives in `shared/python/observability.py`
because it is not specific to this integration.

## Deploying

Prerequisites, in the order they have to exist:

1. A data volume with `mixed` security style, an allow ACE and an audit ACE, and
   an S3 access point attached with a **WINDOWS** identity. See
   [the identity requirement](#the-data-volume-needs-a-windows-identity-not-a-unix-one).
2. A separate volume for the rotated audit files, with an S3 access point the
   correlator can read (a UNIX identity is fine for this one).
3. ONTAP auditing enabled on the SVM, `format=xml`, `log_path` pointing at that
   audit volume.
4. The shared Python layer published.

The [setup guide](docs/en/setup-guide.md) has the ONTAP calls for steps 1 to 3.

```bash
# 1. Build and publish the shared layer (the handler imports from it)
bash shared/python/build-layer.sh

# 2. Deploy
export FSX_S3_ACCESS_POINT_ARN='arn:aws:s3:ap-northeast-1:123456789012:accesspoint/example-ap'
export SHARED_LAYER_ARN='arn:aws:lambda:ap-northeast-1:123456789012:layer:fsxn-shared-python:1'
export OWNER_TAG='your-team'          # no default, so no name is committed
export AUDIT_LOG_PREFIX='audit/'
bash integrations/amplify-portal/scripts/deploy.sh
```

`OWNER_TAG` has no default on purpose. Every resource is tagged
`cost=fsxn-obs-appsig`; `cost` specifically because it is an already-activated
cost allocation tag, and activating a new tag key only affects data recorded
after activation.

### The data volume needs a Windows identity, not a UNIX one

This is the prerequisite that is easy to get wrong, and getting it wrong produces
either no audit records or no data access at all. Measured on ONTAP 9.18.1P3D1;
the full sequence is in the [setup guide](docs/en/setup-guide.md).

Auditing file operations and reaching the volume with a UNIX identity **cannot
both be true**:

1. Turning auditing on records nothing by itself. The only entry that appears is
   the audit subsystem's own `Audit Enabled` event. Auditing file operations
   needs an **audit ACE**.
2. An audit ACE cannot exist on a `unix`-security-style volume. That volume has
   mode bits and no ACL container at all.
3. Applying an audit ACE flips the path's **effective style to `ntfs`**. At that
   point an access point presenting a **UNIX** file-system identity gets
   `AccessDenied` on every data operation — PUT, GET, LIST and DELETE were all
   measured failing.

The combination that works:

| Setting | Value |
|---|---|
| Volume security style | `mixed` |
| Access point `FileSystemIdentity` | **`WINDOWS`**, naming a Windows user |
| ACLs on the data path | an allow ACE for that user **and** an audit ACE |

Attaching a WINDOWS-identity access point makes FSx create the `s3_win` name
mapping itself, so that is not a manual step.

A **local** ONTAP Windows user is enough — a domain user is not required, which
matters because an AD-joined SVM whose domain controllers are unreachable cannot
authorize domain identities. That was the state of the SVM used for this
measurement, and a local user worked.

An audit-log volume read by the correlator has no such constraint: it is read, not
audited, so `unix` style with a UNIX identity is fine there.

## Running the join

The correlator writes the audit side to its own log group. The join is a
CloudWatch Logs Insights query across that log group and the application's:

```
fields s3ap_join_key, marker, app_principal, audit_source_key, audit_operation
| filter ispresent(s3ap_join_key)
| stats count(marker) as audit_rows, count(app_principal) as app_rows,
        earliest(app_principal) as principal,
        earliest(audit_source_key) as audit_file
    by s3ap_join_key
| filter audit_rows > 0 and app_rows > 0
| sort s3ap_join_key
```

Counting `count(<field>)` rather than `count(*)` is what distinguishes "seen from
both sides" from "seen twice on one side" — two writes of the same object would
otherwise look like a correlated pair. Logs Insights has `ispresent()` and no
`ismissing()`.

The query is also an output of the stack (`JoinQuery`), so what is deployed is
what gets run.

### Measured result

Driving PUT, GET and DELETE for two objects on behalf of two different users, on
2026-08-30 against ONTAP 9.18.1P3D1:

```
join key                         audit  app  principal
DELETE|data/alice-report.txt         1    1  b5d2a8c37510ca3d
DELETE|data/bob-notes.txt            1    1  a59268d39eca9880
GET|data/alice-report.txt            1    1  b5d2a8c37510ca3d
GET|data/bob-notes.txt               1    1  a59268d39eca9880
PUT|data/alice-report.txt            1    1  b5d2a8c37510ca3d
PUT|data/bob-notes.txt               1    1  a59268d39eca9880
```

All six correlated, and the two users stayed separate. The principal column is
the part no storage-layer log can supply.

Operations driven without an application-side signal appear in the same query
with `app_rows = 0` — correlated to an object and an operation, attributable to
nobody. That is what Tier 1 looks like.

## Metrics

Namespace defaults to `FSxONTAPAppSignals`. **Both halves must use the same
namespace** or they land apart.

| Metric | Meaning |
|---|---|
| `S3PathRecords` | Audit records that arrived over the S3 access path (joinable) |
| `FileProtocolRecordsSkipped` | SMB/NFS records excluded from the join |
| `JoinKeysEmitted` | Audit-side records written |
| `S3apOperations` | Application-side operations, by `Operation` dimension |
| `UnparseableFiles` | **A file that could not be parsed.** The invocation still succeeded and the checkpoint still advanced, so this does not show up as a Lambda error |
| `UnmappedOperations` | An ONTAP `EventName` with no verified verb mapping |
| `AuditFilesRead`, `AuditRecordsParsed` | Volume counters |

Object keys are emitted as EMF **properties**, not dimensions: every distinct
dimension value combination is a separately billed custom metric, so putting keys
in a dimension would make the bill scale with the number of files.

## What is not verified

Stating this explicitly because the rest of the page reads as though it works.

| Item | Status |
|---|---|
| Unit tests, cfn-lint, cfn-guard | Passing |
| End-to-end correlation against a live portal | **Not yet run** |
| Latency between an operation and its correlated pair | **Not measured** |
| Cost of a real verification run | **Not measured** — estimated only |
| ONTAP `EventName` for an S3 GET | **Unknown.** The captured set covers create, write, list and unlink. `ONTAP_EVENT_TO_S3_VERB` has no GET row, and an unrecognised name is normalized rather than guessed, so a GET correlates only if both sides spell it identically. `UnmappedOperations` exists to make this visible |
| HEAD operations | **Not auditable.** The coverage page reports six HEAD calls producing zero audit records, so an existence check has no audit side to join to |
| ONTAP versions other than 9.18.1P3D1 | Unconfirmed |

## Related

- [S3 access point monitoring coverage](../../docs/en/s3ap-monitoring-coverage-implications.md) — the measured basis for everything on this page
- [`shared/python/observability.py`](../../shared/python/observability.py) — the drop-in module
- [`shared/python/ontap_audit_parser.py`](../../shared/python/ontap_audit_parser.py) — the audit schema
- [OTel Collector integration](../otel-collector/README.md) — where these signals fan out to the nine vendor backends
