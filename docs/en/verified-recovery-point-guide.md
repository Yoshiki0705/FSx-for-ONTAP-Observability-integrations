# Verified-Clean Recovery Point Guide — Closing the CSF 2.0 RC.RP Gap

🌐 [日本語](../ja/verified-recovery-point-guide.md) | **English** (this page)

## Executive Summary

The [Cyber Resilience Capability Map](cyber-resilience-capability-map.md#recover-rc) is explicit about a gap this repository did not previously address: a protective snapshot existing is a **Protect**-phase artifact, not evidence that the snapshot is actually clean and restorable. NIST CSF 2.0's **RC.RP** (Incident Recovery Plan Execution) subcategory is only credible once a recovery point has been tested and confirmed free of compromise — not merely confirmed to exist.

This guide implements that missing verification step using AWS-native services only:

1. **FlexClone** the candidate snapshot into a read/write clone (ONTAP REST API) — never touches the production volume or the original snapshot.
2. **Attach a VPC-scoped S3 Access Point** to the clone (AWS FSx API) — exposes the clone's files via the S3 API without mounting NFS/SMB, so verification has no network path to the production data plane.
3. **Scan the clone's file listing** (S3 `ListObjectsV2` through the access point) for ransomware-associated file extensions — a fast pre-filter, not a substitute for ONTAP ARP's entropy analysis.
4. **Record a clean/suspicious/error verdict** in DynamoDB (and optionally notify via SNS), then **always tear down** the S3 Access Point and FlexClone regardless of outcome.

> **Scope note**: This closes the RC.RP verification gap specifically — it does not replace ONTAP ARP (which detects ransomware *during* the attack, against the live volume) or the [Automated Response Guide](automated-response-guide.md)'s Respond-phase blocking. It answers a different, later question: *"is this specific snapshot, that we are about to promote to a restore point, actually clean?"*

> **Deployment-verification note**: This workflow has been verified end-to-end against ONTAP 9.17.1P7D1 (FSx for ONTAP, ap-northeast-1) in July 2026. The full Step Functions workflow (`CreateFlexClone` → `WaitForFsxSync` → `WaitForFsxDiscovery` → `AttachAccessPoint` → `ScanForIndicators` → `RecordVerdict` → `Cleanup`) completed with `SUCCEEDED` status. Verification result: `verdict: suspicious` (test data contained ransomware-extension files), `cleaned_up: true`. Key findings from this E2E verification: FSx for ONTAP sync delay measured at 12-36 minutes, AD DC unreachability causes AccessDenied at file-system layer, Internet-origin AP required (VPC-origin causes AccessDenied from Lambda even with Gateway EP), `put_access_point_policy` unnecessary for same-account access and potentially counterproductive on FSx for ONTAP APs.

**Key capabilities:**
- FlexClone-based verification with zero impact on production volumes (copy-on-write, read-only workload against the clone)
- Internet-origin S3 Access Point with IAM + Block Public Access enforcement — no anonymous access possible
- Fast extension-based pre-filter for ransomware indicators
- Guaranteed cleanup via Step Functions `Catch` — no orphaned clones or access points, even on failure
- DynamoDB ledger of every verification run, doubling as CSF 2.0 RC.RP evidence for audits

**When to run this:**
- After the [Automated Response Guide](automated-response-guide.md)'s `create_snapshot` action fires, before relying on that snapshot as your recovery point
- On a schedule against your regular protective/scheduled snapshots, as a periodic recovery-readiness check
- Manually, before a planned DR test or compliance audit that requires evidence of a *tested* recovery point

**How to know it's working for you**: a successful rollout looks like every incident-driven or scheduled snapshot getting a ledger entry within your retry budget's window, `cleaned_up: true` on every run (not just successful verdicts — see the Fixed-bug note under Step 5 for why this specific field is worth watching), and zero orphaned FlexClone volumes accumulating in `aws fsx describe-volumes` between runs. Track those three signals from your first few weeks of production use rather than only checking after an incident actually happens.

> **Positioning note**: When positioning this guide to a reader evaluating FSx for ONTAP for ransomware resilience, the accurate one-sentence framing is: "an automated pre-filter that flags obviously-compromised snapshots before a human wastes a restore cycle on one, not a certification that a snapshot is malware-free." Do not represent the "clean" verdict as equivalent to a full forensic clearance in an engagement conversation or RFP response — the Resilience-maturity and Threat-intelligence notes elsewhere in this guide spell out exactly why that framing would overstate the capability. The accurate, still-compelling claim is the automation and evidence trail itself: this closes a documented gap (no verified-clean recovery point workflow existed before) using AWS-native services, with a DynamoDB audit trail — that's a meaningful capability without needing to oversell the scan's depth.

---

## Architecture

```
+-------------------------------------------------------------------+
| Step Functions: Verified-Clean Recovery Point Workflow            |
+-------------------------------------------------------------------+
|                                                                   |
|  CreateFlexClone (ONTAP REST API)                                 |
|       |                                                           |
|       v                                                           |
|  WaitForFsxDiscovery (FSx describe-volumes polling)               |
|       |                                                           |
|       v                                                           |
|  AttachAccessPoint (AWS FSx API, Internet-origin)                     |
|       |                                                           |
|       v                                                           |
|  ScanForIndicators (S3 ListObjectsV2 via access point)             |
|       |                                                           |
|       v                                                           |
|  RecordVerdict (DynamoDB + optional SNS)                          |
|       |                                                           |
|       v                                                           |
|  Cleanup (detach S3 AP + delete FlexClone)  <-- ALWAYS runs        |
|                                                                   |
|  (any step failure) --> CleanupAfterError --> RecordErrorVerdict   |
|                          --> Fail                                  |
+-------------------------------------------------------------------+
```

![Step Functions Graph View — execution succeeded](../screenshots/automated-response/stepfunctions-graph-view-succeeded.png)

*Step Functions console: All states succeeded (green). The verified-clean recovery point workflow completed end-to-end — FlexClone creation, FSx discovery, S3 Access Point attachment, ransomware indicator scan, verdict recording, and cleanup all passed. E2E verified on ONTAP 9.17.1P7D1 (July 2026).*

The key design choice: **cleanup runs on every path**, success or failure. The Step Functions `Catch` block routes any error straight to the same cleanup Lambda used on the happy path, so a mid-workflow failure never leaves a FlexClone volume or S3 Access Point behind consuming storage or presenting an unnecessary access surface.

> **Why WaitForFsxDiscovery exists**: ONTAP REST API creates the FlexClone instantly, but FSx API "may take up to several minutes" to discover it (see [AWS docs](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)). This dedicated polling step absorbs that sync delay, keeping AttachAccessPoint focused solely on S3 Access Point lifecycle.

> **Diagram description (text alternative)**: The Step Functions state machine runs seven states in sequence — `CreateFlexClone` (ONTAP REST API, with AD DC pre-flight check) → `WaitForFsxSync` (static Wait, 10 min) → `WaitForFsxDiscovery` (polls FSx `describe-volumes` until the clone appears) → `AttachAccessPoint` (AWS FSx API, Internet-origin) → `ScanForIndicators` (S3 `ListObjectsV2` via the access point, runs outside VPC) → `RecordVerdict` (DynamoDB, optional SNS) → `Cleanup` (detaches the S3 Access Point and deletes the FlexClone; this state always runs). If any of the first five states fails, control transfers to `CleanupAfterError`, which invokes the same `Cleanup` Lambda, then `RecordErrorVerdict` writes the failure to the ledger before the execution ends in a `Fail` state.

---

## How Verification Works

### Step 1: FlexClone Creation

A [FlexClone](https://aws.amazon.com/fsx/netapp-ontap/features/) is a point-in-time, writable copy that shares data blocks with its parent volume via copy-on-write — creation is near-instant and consumes no additional storage until something writes to the clone (which this workflow never does; it only reads). Storage efficiency features on the parent volume (deduplication, compression, thin provisioning) are inherited by the clone automatically — there is nothing to configure on the clone itself, since it shares the parent's already-deduplicated/compressed blocks rather than allocating its own.

```
POST /api/storage/volumes
{
  "name": "verify_vol_data_20260710_143022",
  "svm": {"name": "svm-prod-01"},
  "clone": {
    "parent_volume": {"name": "vol_data"},
    "parent_snapshot": {"name": "incident_response_20260708_143022"},
    "is_flexclone": true
  }
}
```

This returns an async job; the Lambda polls `GET /api/cluster/jobs/{uuid}` until `state: success`, then resolves the clone's ONTAP volume UUID for later cleanup.

> **Concurrency note**: `clone_name` is derived from `verify_{volume_name}_{timestamp}`, where `timestamp` has 1-second resolution (`%Y%m%d_%H%M%S`). Two Step Functions executions started against the *same* `volume_name` within the same second — e.g., a scheduled run and a manually-triggered one firing together — will attempt to create two ONTAP volumes with an identical name, and the second `POST /storage/volumes` call will fail with a naming conflict rather than proceeding. The workflow has no execution-level locking (no DynamoDB conditional write, no Step Functions `name` deduplication) to prevent this. In practice this is a narrow window and an infrequent trigger pattern, but if you invoke this workflow from multiple independent triggers (e.g., both a schedule and a SOAR playbook) against the same volume, consider adding a distinguishing suffix (a short random token or the invoking execution's own ID) to avoid a same-second collision.

### Step 1b: Wait for FSx API Discovery (WaitForFsxDiscovery)

After ONTAP creates the clone, FSx API needs time to discover it — AWS documents this as "may take up to several minutes" ([Managing resources using NetApp applications](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)). Measured delays in this project: 12–36 minutes across multiple runs.

This dedicated step polls `fsx describe-volumes` until the clone appears with `CREATED` or `AVAILABLE` lifecycle status.

![Step Functions AttachAccessPoint — FsxDiscoveryPending retry](../screenshots/automated-response/stepfunctions-execution-running.png)

*Step Functions console: The state detail panel shows the state input (clone name, UUID, parent volume, parent snapshot) and the `FsxDiscoveryPending` error being retried by the Step Functions Retry block.*

```python
# WaitForFsxDiscovery Lambda (simplified)
paginator = fsx.get_paginator("describe_volumes")
for page in paginator.paginate(
    Filters=[{"Name": "file-system-id", "Values": [file_system_id]}]
):
    for volume in page.get("Volumes", []):
        if volume.get("Name") == clone_name and volume.get("Lifecycle") in (
            "AVAILABLE", "CREATED",
        ):
            return {"fsvol_id": volume["VolumeId"], ...}

raise FsxDiscoveryPending(f"Clone volume '{clone_name}' not yet visible via FSx API")
```

Step Functions retry config (120s interval, 25 max attempts, after a 10-minute static Wait) absorbs the FSx sync delay. Each retry is a sub-second Lambda invocation — not blocked by Lambda's own 15-minute timeout limit. Total budget: 10 min (Wait) + 50 min (polling) = 60 min.

### Step 2: S3 Access Point Attachment

The clone is exposed via an **Internet-origin S3 Access Point**. VPC-origin was initially planned for stronger network isolation, but E2E verification confirmed that VPC-origin APs + Lambda (even with S3 Gateway Endpoint) consistently return `AccessDenied`. Internet-origin with IAM + S3 Block Public Access (enforced by FSx, non-modifiable) is the verified working pattern.

```python
fsx.create_and_attach_s3_access_point(
    Name="verify-vol-data-20260710-143022",
    Type="ONTAP",
    OntapConfiguration={
        "VolumeId": fsvol_id,  # resolved via DescribeVolumes, NOT the ONTAP UUID
        "FileSystemIdentity": {"Type": "UNIX", "UnixUser": {"Name": "root"}},
    },
    # No VpcConfiguration = Internet-origin AP
    # No put_access_point_policy needed — same-account IAM identity policy is sufficient
)
```

> **Why Internet-origin, not VPC-origin?**
>
> VPC-origin APs (`S3AccessPoint={"VpcConfiguration": {"VpcId": vpc_id}}`) provide stronger network-layer isolation in theory. However, this project's E2E verification found that Lambda running inside the bound VPC with an S3 Gateway Endpoint consistently received `AccessDenied` from `ListObjectsV2` — even with all IAM policies, AP resource policies (including `Principal: *` full-open), and VPC Endpoint policies confirmed correct. The same workflow succeeded immediately when switched to Internet-origin AP + VPC-external Lambda. This matches the pattern verified in [FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns): "VPC-external Lambda for Internet-origin S3AP access (no VpcConfig)".
>
> Security of Internet-origin APs: FSx for ONTAP enforces S3 Block Public Access on all attached APs (non-modifiable), so anonymous/public access is impossible regardless of network origin. Access requires valid IAM credentials with `s3:ListBucket`/`s3:GetObject` on the AP ARN.
>
> **Why no `put_access_point_policy`?** Same-account access to FSx for ONTAP S3 APs works with IAM identity policy alone — the AP resource policy defaults to delegating to IAM. Calling `s3control.put_access_point_policy()` on FSx for ONTAP APs was observed to cause persistent `AccessDenied` in testing, possibly conflicting with FSx's internal authorization layer.

> **Resolving fsvol-id from the ONTAP UUID — measured delay and retry design**: AWS FSx discovers volumes created via ONTAP REST API asynchronously — there is no direct API to map an ONTAP volume UUID to the corresponding `fsvol-xxxx` ID. AWS's own documentation states this sync "may take up to several minutes" ([Managing FSx for ONTAP resources using NetApp applications](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)). This is a distinct concern from ONTAP's own [asynchronous REST API job model](https://docs.netapp.com/us-en/ontap-automation/rest/asynchronous_processing.html) (a `POST` like the one in Step 1 above returns HTTP 202 with a job UUID, and the caller polls `GET /cluster/jobs/{uuid}` until the job itself resolves to `success`/`failure`) — `CreateFlexClone` already polls that ONTAP job to completion before this step ever runs, so by the time `AttachAccessPoint` executes, the ONTAP-side operation has already succeeded. The delay discussed below is what happens *after* that: FSx's own inventory of ONTAP volumes hasn't caught up to a change ONTAP has already made. This project's own end-to-end verification measured this delay directly, with continuous polling and no observation gaps, across three separate runs against the same otherwise-idle file system: **~12 minutes**, then **~24 minutes**, then **~36 minutes** — an increasing pattern across runs, not fixed noise, though three data points are not enough to confirm this is strictly periodic rather than coincidental. The variance and its trend, not any single number, are the operationally important fact. Because of this, `AttachAccessPoint` makes exactly one check per invocation (querying `DescribeVolumes`, then `DescribeS3AccessPointAttachments`) and raises `FsxDiscoveryPending`/`S3AttachPending` when not ready — the **Step Functions state machine's own `Retry` block** (not a loop inside the Lambda) re-invokes it on a schedule (30s initial interval, 1.25x backoff, capped at 150s, 28 attempts — roughly 60 minutes of cumulative budget, sized with margin above the slowest of the three measured runs, but not guaranteed to be sufficient if your environment's delay runs longer still) until FSx reports the clone `AVAILABLE`. This design choice matters operationally: each retry is a separate few-hundred-millisecond Lambda invocation rather than one Lambda blocking for up to 15 minutes (Lambda's own maximum timeout, which the measured delays can approach or exceed), and the full retry schedule is visible in the Step Functions execution history rather than buried inside a sleep loop.

The access point transitions `CREATING` → `AVAILABLE` (or `FAILED`/`MISCONFIGURED` on error) — the Retry-driven re-invocations described above poll `DescribeS3AccessPointAttachments` until a terminal state. `AttachAccessPoint`'s own re-check for an already-requested access point (via `DescribeS3AccessPointAttachments` with the `Names` parameter — not `Filters`, which returns `BadRequest: Request failed validation` for this specific API) makes each retry attempt idempotent: Step Functions re-invokes a failed task with the *original* input every time, not a merged partial result, so the Lambda cannot rely on "I already created this" being passed in — it re-derives that fact from the AWS API on every attempt instead.

### Step 3: Ransomware Indicator Scan

The scan lists the clone's objects through the access point (`ListObjectsV2`) and flags file extensions commonly appended by ransomware families (`.encrypted`, `.locked`, `.crypt`, `.wcry`, `.locky`, and others). A snapshot is flagged **suspicious** only when both:

- The suspicious object count is ≥ `SuspiciousMinCount` (default 20 — avoids false positives on small volumes with a couple of legitimately-named files)
- The suspicious ratio is ≥ `SuspiciousRatioThreshold` (default 5%)

> **Resilience-maturity note**: This is deliberately a coarse, fast pre-filter — not a replacement for [ONTAP ARP](arp-incident-response-guide.md)'s file-content entropy analysis, which runs against the live volume during an active attack. This scan answers a narrower, later question: does *this specific snapshot* look like it captured a volume dominated by ransomware-renamed files? A "clean" verdict here is evidence for RC.RP; it is not a general-purpose malware scan, and it does not inspect file *contents* (see the [Content-Level PII Classification Scanner](content-classification-scanner.md) for a complementary content-scanning capability aimed at a different problem — data classification, not ransomware detection).

> **Scale note**: The scan uses `list_objects_v2`'s paginator (1000 keys per page) and walks every page before computing a verdict — there is no early exit and no sampling. For a volume with a very large number of objects, this means `ScanForIndicators`' wall-clock time scales with total object count, not with how quickly a "suspicious" pattern would become apparent. `StepTimeoutSeconds` (default 180 seconds) is set as this Lambda's own `Timeout` property (not a Step Functions-level `TimeoutSeconds`), the same as the other four Lambdas — if your volume's object count is large enough that a full listing plus per-key extension check exceeds that window, the Lambda itself times out and the resulting task failure is caught by the state's `Catch: States.ALL` block rather than returning a partial verdict. There is no `Retry` block on this state (see the Retry-policy note under Testing), so a timeout here routes straight to `CleanupAfterError` on the first occurrence. If you verify snapshots from volumes with millions of objects, measure `ScanForIndicators`' actual duration against a representative volume before relying on the default timeout, and raise `StepTimeoutSeconds` if needed.

> **Threat-intelligence note**: `SUSPICIOUS_EXTENSIONS` is a fixed list of extensions historically associated with named ransomware families (`.locky`, `.wcry`, `.cerber`, and similar). This is a known-signature approach and inherits a known-signature blind spot: ransomware operators that append a random or victim-specific extension (a pattern observed increasingly in current campaigns, precisely to evade extension-based detection like this) will not match any entry in the list, and a snapshot encrypted by such a variant can still receive a "clean" verdict from this scan alone. This list is not automatically kept current with emerging ransomware families — review and extend `SUSPICIOUS_EXTENSIONS` periodically against current threat intelligence (e.g., your SIEM vendor's threat feed or a maintained public list) rather than treating the shipped list as exhaustive. This is a second, independent reason (beyond the ones already noted above) that a "clean" verdict here is a pre-filter, not a guarantee — [ONTAP ARP](arp-incident-response-guide.md)'s entropy-based detection does not depend on recognizing a specific extension and is a meaningful complement for exactly this blind spot.

### Step 4: Verdict Recording

Every run — clean, suspicious, or error — is written to a DynamoDB ledger table (`snapshot_key` = `{svm}/{volume}/{snapshot}`, sort key `started_at`), giving you a queryable, timestamped history of which recovery points have been verified and what the outcome was. This table is the artifact you point to as RC.RP evidence in an audit.

> **Data-minimization note**: The ledger item itself does not store the list of flagged file paths — only `suspicious_object_count` and `suspicious_ratio` (aggregate numbers). The actual `suspicious_objects` array (capped at the first 50 entries in `to_dict()` — see the Full orchestration row under Testing) never reaches `RecordVerdict`'s `PutItem` call as shipped. This is the inverse of the Audit-trail-completeness note under Security Considerations: CloudWatch Logs (via the state machine's `IncludeExecutionData: true`) *does* carry the full per-run payload including file paths, while the DynamoDB ledger — the artifact this guide points to as your durable RC.RP evidence — does not. If you need flagged file paths available for a compliance or forensic query without falling back to CloudWatch Logs, you would need to add that field to `RecordVerdict`'s `item` dict explicitly; weigh that against the same sensitive-path-in-a-longer-retained-store concern the Audit-trail-completeness note raises for logs.

### Step 5: Guaranteed Cleanup

The cleanup Lambda is idempotent-ish by design: a missing `access_point_name` or `fsvol_id` (e.g., cleanup running after an early failure, before those resources existed) is treated as a no-op, not an error. Both the S3 Access Point detach and FlexClone deletion tolerate "already gone" (404 / `NotFound`) responses.

> **FlexClone deletion goes through the FSx API, not the ONTAP REST API — this is a deliberate, tested design decision, not an oversight**: An earlier version of this workflow deleted the FlexClone by calling `DELETE /storage/volumes/{uuid}` directly against the ONTAP REST API (the same API `CreateFlexClone` uses to create it). This fails for FlexClone volumes derived from a parent volume under Amazon FSx's backup management: AWS's own documentation states that "[Amazon FSx backups use SnapMirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/cannot-delete-svm.html) to create point-in-time, incremental backups of your file system's volumes. You can't delete this SnapMirror relationship for your backups in the ONTAP CLI. However, this relationship is automatically deleted when you delete a volume through the AWS CLI, API, or console." During this project's own end-to-end verification, the ONTAP DELETE call returned HTTP 202 (job accepted) with no immediate error — but the underlying ONTAP job later resolved to `"state": "failure"` with the specific error `Volume "..." is the destination or source endpoint of one or more SnapMirror relationships` (ONTAP error code 917858), a failure invisible to any code that checks only the HTTP status of the initial request rather than polling the job to completion. The current implementation calls `fsx.delete_volume(VolumeId=fsvol_id)` — the FSx API, not ONTAP's — which handles the SnapMirror teardown internally and completed in roughly 6 minutes in this project's own measurement. One consequence: `Cleanup` now needs `fsvol_id` (set by `AttachAccessPoint` once FSx discovers the clone), not just the ONTAP `volume_uuid` from `CreateFlexClone`. If a workflow execution fails *before* `AttachAccessPoint` completes, `fsvol_id` is never set, and `Cleanup` cannot delete the clone via the FSx API — see the Orphaned-clone note below for how to handle that specific gap.

> **Orphaned-clone note (fsvol_id missing)**: If `CreateFlexClone` succeeds but the workflow fails before `AttachAccessPoint` resolves `fsvol_id` (e.g., all 28 retry attempts exhausted, or an unrelated error), `Cleanup` logs a warning and reports `cleaned_up: false` rather than attempting an ONTAP-API delete — that path is known to fail for FSx-backup-managed volumes (see above). The clone volume is left in ONTAP, consuming no meaningful extra storage (copy-on-write) but existing as an orphaned resource. This project's own verification hit this exact case: a run whose FSx-discovery delay exceeded the retry budget in effect at the time left an orphaned clone that appeared via `describe-volumes` roughly 35-37 minutes after creation, well after the workflow itself had already recorded an error verdict and moved on. To resolve manually once FSx has discovered it: `aws fsx describe-volumes --filters Name=file-system-id,Values=<your-fs-id> --query "Volumes[?starts_with(Name,'verify_')]"` to find the `fsvol-xxxx` ID by name, then `aws fsx delete-volume --volume-id <fsvol-id>`. If it's been a very long time (well beyond the ~60-minute retry budget) and the clone still doesn't appear via `describe-volumes`, that itself is worth escalating — it suggests something beyond the FSx for ONTAP sync lag this project observed.

> **Fixed bug — `fsvol_id` was silently omitted from a successful `AttachAccessPoint` output**: An earlier version of this template's `AttachAccessPoint` resolved `fsvol_id` internally (needed to poll for the access point becoming `AVAILABLE`) but never included it in the dict returned to the next state — a mismatch between the function's own docstring (which documented `fsvol_id` as part of the output) and its actual `result.update(...)` call. The practical effect: `Cleanup` reported `cleaned_up: false` on every single successful verification run, not just failed ones, because `fsvol_id` was always missing from `event` by the time `Cleanup` executed — even though the access point detach itself succeeded. This went undetected through several manual end-to-end runs because `cleaned_up: false` doesn't fail the Step Functions execution (the workflow still reports overall success) and the access point half of cleanup was working, masking the incomplete volume cleanup. The fix reads `fsvol_id` from `OntapConfiguration.VolumeId` on the same `describe_s3_access_point_attachments` response `AttachAccessPoint` already uses to confirm `AVAILABLE`, and includes it in the returned dict. **Lesson for reviewing any future changes to this workflow**: `cleaned_up: false` in a `SUCCEEDED` execution's output is not automatically visible in the Step Functions console's top-level status — check the actual output payload (or the DynamoDB ledger's equivalent field, if you add one) after any change to `AttachAccessPoint` or `Cleanup`, don't rely on execution status alone.

> **Fixed bug — `Cleanup` called `DeleteVolume` before the access point detach had actually finished**: `fsx.detach_and_delete_s3_access_point()` is asynchronous — it transitions the access point to `DELETING` and returns immediately, it does not wait for the detach to complete. An earlier version of `Cleanup` called `fsx.delete_volume()` immediately afterward with no wait, and this failed deterministically in this project's own end-to-end verification with `Cannot delete volume while it has one or multiple S3 access points: [<name>]` — because the access point genuinely still existed (mid-deletion) at the moment `delete_volume` was called. The fix adds a short poll loop (`_wait_for_ap_gone`, 3-second interval, 60-second budget) that calls `describe_s3_access_point_attachments` until it raises `NotFound`, before proceeding to `delete_volume`. This fits comfortably inside `StepTimeoutSeconds`'s default of 180 seconds. If your environment's access point detach takes longer than 60 seconds (this project's own measurements were well under that), increase the `max_wait_seconds` argument in `_wait_for_ap_gone` and confirm `StepTimeoutSeconds` still has enough headroom above it.

> **Operational finding — ONTAP's clone "recovery queue" can block deleting the *parent* volume, well after FSx reports the clone itself gone**: This is distinct from every other timing note in this guide — it's not about FSx discovering a *new* ONTAP resource, but about ONTAP's own internal bookkeeping lagging behind a resource's *deletion*. When `Cleanup` deletes a FlexClone via `fsx.delete_volume()`, FSx-side `describe-volumes` stops listing it almost immediately — but ONTAP itself does not immediately forget the clone: it places it in an internal "volume recovery queue" (a retention window, documented by NetApp as roughly 12 hours in some ONTAP releases) before fully purging it. While a clone sits in that queue, the **parent** volume's own `clone.has_flexclone` field stays `true` on the ONTAP side, and any attempt to delete the *parent* volume — via FSx API, ONTAP REST API, or ONTAP CLI — fails with `Failed to delete volume "..." because it has one or more clones. Only the cluster administrator can delete the clones associated with this volume.`, even though `aws fsx describe-volumes` no longer shows the clone anywhere. This project's own verification hit this directly: after several clone create/delete cycles against the same parent volume during repeated manual test runs, deleting that parent failed repeatedly with this exact message, and cross-checking ONTAP's own `clone.parent_volume` filter confirmed zero actual child volumes remained — the block was coming from stale recovery-queue entries, not a real clone relationship.
>
> **This is the one case in this project where checking ONTAP directly resolves a block that waiting on the FSx side does not** — `aws fsx describe-volumes` will simply never show these already-deleted-from-FSx's-perspective clones, so there is nothing to poll for on the FSx side. If you hit this, query ONTAP's recovery queue directly (`GET /private/cli/volume/recovery-queue?vserver=<svm>`) and purge the specific stuck entries (`DELETE /private/cli/volume/recovery-queue/purge?vserver=<svm>&volume=<queued-volume-name>`) rather than waiting for the retention window to lapse on its own. This requires ONTAP admin credentials and is an ONTAP-CLI-equivalent private API — it is **not** something this workflow's own `Cleanup` Lambda does or should do automatically (deleting a queued clone early forfeits ONTAP's own safety window for that specific clone, and this operation is scoped to manual incident response, not routine automation). This is purely an operational note for whoever manages the underlying parent volumes (e.g., when decommissioning a test volume used repeatedly for this workflow) — it does not affect the workflow's own per-run correctness, since `Cleanup` only ever deletes the clone it itself created, never the parent.
>
> **Security note**: the credentials needed to purge a recovery-queue entry sit at a higher privilege tier than `VerificationLambdaRole` (the shared role this workflow's Lambdas use for their routine `fsx:*` calls) — that separation is intentional. If your organization needs to perform this purge in production, route it through whatever privileged-access approval and audit process you already require for direct ONTAP administrator actions, rather than pre-provisioning any Lambda role in this stack with recovery-queue-purge permissions "just in case" a parent-volume cleanup is needed later.

> **API contract note**: The contract between Step Functions states is an implicit, untyped dict merge rather than a versioned schema — each Lambda receives the full accumulated JSON payload from prior states (via `"ResultPath": "$"` in the state machine definition) and returns `dict(event)` updated with its own new keys, so downstream states silently depend on upstream key names never changing. This works well for a five-state, single-purpose workflow where all states are maintained together, but it means there is no schema validation catching a typo'd key name or a renamed field at deploy time — a mismatch would only surface at runtime as a missing key, likely inside `RecordVerdict` or `Cleanup` reading `event.get(...)` with a silent default rather than a clear error. If you extend this workflow (e.g., inserting the Content-Level PII Classification Scanner as an additional state per that guide's Restore-testing note), keep the same implicit-passthrough convention rather than introducing a differently-shaped payload, or the inserted state's output may silently drop keys later states depend on.

---

## Comparison: Snapshot Exists vs Verified-Clean Recovery Point

| Aspect | Snapshot Exists (Respond phase) | Verified-Clean Recovery Point (this workflow) |
|--------|----------------------------------|------------------------------------------------|
| CSF 2.0 function | Protect (the snapshot itself) | Recover — RC.RP specifically |
| What it proves | A point-in-time copy was captured | The copy was inspected and does not show ransomware indicators |
| Production impact | None (snapshot creation is near-instant) | None (FlexClone is copy-on-write; scan is read-only against the clone) |
| Confidence for a restore decision | Low — a snapshot taken *during* an attack can itself contain encrypted files | Higher — an automated go/no-go signal before a human commits to a restore |
| Automation in this repo | ✅ Full ([Automated Response Guide](automated-response-guide.md)) | ✅ Full (this guide) |

> **Recovery-sufficiency note**: Treat a "clean" verdict from this workflow as a *necessary*, not *sufficient*, condition before restoring. It rules out the coarse, obvious case (a volume dominated by ransomware-renamed files) fast and cheaply. It does not verify application-level data integrity, verify the snapshot restores cleanly end-to-end, or replace a full DR test. Schedule periodic full restore tests separately; use this workflow as the automated first gate that runs on every candidate snapshot, not as the last word on recoverability.

> **DR-runbook sequencing note**: This is distinct from the Recovery-sufficiency note above — that one addresses the technical sufficiency of a "clean" verdict; this one addresses where this workflow fits into a broader DR plan's operational sequencing. Where does this step sit relative to your other DR runbook steps (failover decision, stakeholder notification, application restart order)? A sensible placement is: incident detected → protective snapshot taken (Respond) → **this verification runs** → only if "clean," the snapshot is presented to whoever makes the restore-or-failover decision, alongside (not instead of) your existing DR decision criteria (RTO/RPO targets, business-impact assessment). If your DR runbook currently treats "a snapshot exists" as sufficient to initiate restore, update that runbook to gate on this workflow's verdict — but do so as a documented change to your DR plan and communicate it to whoever executes that runbook during an actual incident, not as an implicit assumption buried in this guide.

---

## Prerequisites

### ONTAP Version

- **FlexClone REST API** (`clone.is_flexclone`): Available from ONTAP 9.8+
- **Volume creation/deletion REST API**: Available from ONTAP 9.6+

> **Patch-management note**: This guide lists the *minimum* ONTAP version each API requires, not a *recommended* version to run. Treat this workflow the same as any other component touching your ONTAP management endpoint: track [NetApp Security Advisories](https://security.netapp.com/) for the ONTAP version you run, and patch on your organization's normal cadence — a verification workflow that itself calls the ONTAP REST API over credentials with `secretsmanager:GetSecretValue` access is not exempt from the same patch-management discipline you'd apply to any other ONTAP API consumer. Separately, the Lambda runtime (`python3.12`) and its `boto3`/`botocore`/`urllib3` dependencies (see the CloudFormation template's inline `ZipFile` code) are not pinned to specific versions in this stack — Lambda resolves the latest available `python3.12` managed runtime and its bundled SDK versions at deploy time, so track AWS's Lambda runtime deprecation schedule and re-deploy periodically rather than assuming a one-time deployment stays current indefinitely.

> **Security note — must-fix before production**: The `CreateFlexClone` Lambda (the only one remaining that calls the ONTAP REST API directly — see the FlexClone-deletion note under Step 5 above for why `Cleanup` no longer does) constructs its `urllib3.PoolManager` with `cert_reqs="CERT_NONE"` — TLS certificate verification is disabled unconditionally in this stack's inline Lambda code, meaning any TLS certificate (including a self-signed one presented by an on-path attacker) is accepted without validation for `secretsmanager`-sourced ONTAP admin credentials sent over that connection. The standalone `restore_verification.py` library this stack's logic is based on *does* support a `ca_cert_path` parameter to enable proper verification, but the CloudFormation template's inline `ZipFile` code does not expose or use that option — it hardcodes `CERT_NONE`. This is acceptable for a PoC in an isolated lab VPC; it is not acceptable for a production deployment carrying real ONTAP admin credentials over the wire. Before production use, either (a) provide the FSx for ONTAP management endpoint's CA certificate to the Lambda (via a Lambda Layer, an environment variable pointing to a bundled cert, or Secrets Manager) and change `cert_reqs` to `"CERT_REQUIRED"` with `ca_certs` set accordingly, or (b) confirm your compensating network control (e.g., the Lambda reaching the ONTAP management IP only through a private, non-transited VPC subnet) makes on-path TLS interception infeasible in your environment, and document that decision explicitly rather than leaving it as an unstated default.

### AD-Joined SVM Requirement — AD Domain Controller Must Be Reachable

**If your target SVM is AD-joined (CIFS enabled), Active Directory domain controllers must be reachable from the SVM's network interfaces.** If AD DCs are unreachable, the S3 Access Point is created successfully, but `ScanForIndicators`' `ListObjectsV2` fails with `AccessDenied`.

**Mechanism**: On a CIFS-enabled SVM, ONTAP performs a unix→win reverse name-mapping lookup for every S3 AP data operation. This lookup requires LDAP/Kerberos connectivity to AD DCs. If DCs are stopped, deleted, or network-unreachable, the file-system authorization layer fails — returning `AccessDenied` even though IAM/policy layers are completely correct.

**Confusing behavior**: `HeadBucket` (AP existence check) succeeds, but `ListObjectsV2`/`GetObject`/`PutObject` all fail. This is because metadata-only operations don't traverse the file-system layer, while data operations do.

**This workflow's defense**: The `CreateFlexClone` Lambda performs a pre-flight check using `protocols/cifs/services` and `protocols/cifs/domains` APIs to verify AD DC discoverability. If DCs cannot be found, the workflow fails immediately (within seconds) with a clear `AD CONNECTIVITY FAILURE` error rather than waiting 30+ minutes through clone creation and FSx discovery only to hit AccessDenied at the scan step.

```bash
# Manual verification
curl -sk -u "<user>:<pass>" "https://<mgmt-ip>/api/protocols/cifs/services?svm.name=<svm-name>&fields=ad_domain.fqdn"
# If records are returned → CIFS is enabled (AD-joined)
# Then check DC reachability:
curl -sk -u "<user>:<pass>" "https://<mgmt-ip>/api/protocols/cifs/domains?svm.name=<svm-name>&fields=discovered_servers"
# Empty discovered_servers = AD DC unreachable → fix before running workflow
```

> **This check is unnecessary for pure NFS/UNIX SVMs.** If CIFS is not enabled on the SVM, no name-mapping lookup occurs, and S3 AP data operations work regardless of AD availability.

### SVM Requirement — No Existing ONTAP-Native S3 Server

**Check this before your first run, not after a failure.** The target SVM must not have an ONTAP-native S3 object storage server already configured (`vserver object-store-server` in the ONTAP CLI, `protocols/s3/services` in the REST API). If it does, `AttachAccessPoint` fails deterministically — every time, not intermittently — with:

```
Amazon FSx is unable to create an S3 access point because of an existing
ONTAP object storage server on SVM <svm-name>. Please delete the existing
s3 server and retry.
```

This is a **structural conflict, not a timing issue** — retrying, waiting longer, or widening `AttachAccessPoint`'s retry budget will never resolve it, unlike the FSx for ONTAP sync delay discussed in Step 2 below. This project's own end-to-end verification hit this exact case: a shared test file system's SVM had an unrelated ONTAP S3 server (with buckets holding another team's test data) already configured, entirely unrelated to this workflow, and every verification run against a volume on that SVM failed here regardless of retry settings.

Check before deploying against any SVM you don't fully control:

```bash
# Run this against your ONTAP management endpoint (requires ONTAP admin
# credentials) — see the AttachAccessPoint Lambda's own docstring for the
# equivalent programmatic check this workflow could add as a pre-flight step
curl -sk -u "<user>:<pass>" "https://<mgmt-ip>/api/protocols/s3/services?svm.name=<svm-name>"
# {"records": [], "num_records": 0}  <- safe to proceed
# {"records": [...], "num_records": 1}  <- conflict; pick a different SVM
```

If the check returns a record, **do not delete it** without confirming who owns that S3 server and what data it holds — in a shared file system, it is very likely serving an unrelated use case. Deploy this workflow against a different SVM (or a newly created one) instead; this is almost always simpler and safer than negotiating deletion of someone else's ONTAP S3 server configuration.

### Volume Requirement — UNIX Security Style Only

**This workflow, as shipped, only works against UNIX-security-style volumes.** `AttachAccessPoint` always sets `FileSystemIdentity.Type=UNIX` via the `UnixUser` parameter (default `root`). AWS's own documentation is explicit that this pairing is required: "Use the UNIX file system identity type for volumes with UNIX security style and the Windows identity type for volumes with NTFS security style" ([Managing access point access](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/s3-ap-manage-access-fsxn.html)). S3 access points for FSx for ONTAP use a "dual-layer authorization model" — both the S3 IAM/resource policy *and* the underlying file system's own permission check must pass, and a UNIX identity against an NTFS-security-style volume fails the second layer.

This project's own end-to-end verification hit this exact case: `AttachAccessPoint` and the S3 Access Point resource policy both succeeded, but `ScanForIndicators`' `ListObjectsV2` call failed with `AccessDenied` — a confusing failure mode because the IAM side looks completely correct. The failure only makes sense once you check the target volume's security style:

```bash
curl -sk -u "<user>:<pass>" \
  "https://<mgmt-ip>/api/storage/volumes?name=<volume-name>&svm.name=<svm-name>&fields=nas.security_style"
# "security_style": "unix"  <- this workflow works
# "security_style": "ntfs"  <- ScanForIndicators will get AccessDenied
```

If your target volume is NTFS-security-style (common for SMB-shared volumes serving Windows clients), this workflow does not support it as shipped — extending `AttachAccessPoint` to accept a Windows identity (`FileSystemIdentity.Type=WINDOWS`, requiring Active Directory integration) would be required first. Choose or create a UNIX-security-style volume for verification instead.

### AWS Permissions

The Lambda execution role requires:

```
# ONTAP REST API (via Secrets Manager credentials)
- secretsmanager:GetSecretValue

# FSx S3 Access Point lifecycle (no resource-level permissions supported
# for these actions as of this writing)
- fsx:CreateAndAttachS3AccessPoint
- fsx:DetachAndDeleteS3AccessPoint
- fsx:DescribeS3AccessPointAttachments
- fsx:DescribeVolumes

# FlexClone deletion — via the FSx API, not the ONTAP REST API (see the
# FlexClone-deletion note under "How Verification Works" for why)
- fsx:DeleteVolume  (scoped to arn:aws:fsx:*:*:volume/<file-system-id>/*)

# S3 Access Point lifecycle — the caller's own role needs these S3 actions
# in addition to the fsx:* actions above: fsx:CreateAndAttachS3AccessPoint /
# fsx:DetachAndDeleteS3AccessPoint call S3Control's CreateAccessPoint /
# GetAccessPoint / DeleteAccessPoint on the caller's behalf, and the FSx-side
# permission alone is not sufficient (observed AccessDeniedException on both
# create AND detach-and-delete without this in end-to-end verification)
- s3:CreateAccessPoint / s3:GetAccessPoint / s3:DeleteAccessPoint  (scoped to arn:aws:s3:*:*:accesspoint/*)

# S3 Access Point object read (scanning) + policy management
- s3:ListBucket / s3:GetObject  (scoped to arn:aws:s3:*:*:accesspoint/*)
- s3:PutAccessPointPolicy / s3:GetAccessPointPolicy

# Verdict ledger
- dynamodb:PutItem / dynamodb:UpdateItem  (scoped to the ledger table)

# SNS notification (optional)
- sns:Publish  (scoped to NotificationTopicArn when set)
```

> **Least-privilege note**: The `sns:Publish` statement's `Resource` is scoped to `NotificationTopicArn` only when that parameter is set at deploy time; if you deploy without `NotificationTopicArn` (notifications disabled), the template's IAM policy falls back to `Resource: arn:aws:sns:<region>:<account-id>:*` — every SNS topic in the account and Region — rather than denying the action outright. In practice this permission is never exercised in that configuration (`RecordVerdict`/`RecordErrorVerdict` skip the `sns.publish()` call entirely when `NOTIFICATION_TOPIC_ARN` is empty), but the IAM policy itself does not reflect that runtime behavior — a compromised Lambda execution environment in this configuration would still be able to publish to any topic in the account. If your organization's least-privilege standard requires the policy itself (not just the application logic) to reflect "notifications disabled," tighten this statement to a fixed, non-wildcard placeholder ARN or omit the `SNSPublish` statement entirely when deploying without notifications, rather than relying on the Lambda code path to make the broad grant harmless.

### Network Access

Which Lambdas run inside the VPC depends on what each one calls, not on a uniform "verification workflow" rule:

| Lambda | Calls | Runs inside VPC? | Why |
|--------|-------|-------------------|-----|
| `CreateFlexClone` | ONTAP REST API directly (volume create) | ✅ Yes | Needs the management-IP route via `SubnetIds`/`SecurityGroupId` |
| `AttachAccessPoint` | FSx control-plane API only (`CreateAndAttachS3AccessPoint`, `DescribeVolumes`) — never touches ONTAP directly | ❌ No | The FSx API is a public AWS API reachable without a VPC; running it outside the VPC avoids needing an FSx Interface Endpoint just for this step |
| `ScanForIndicators` | `ListObjectsV2` against the **Internet-origin** S3 Access Point created in the previous step | ❌ No | Internet-origin AP is accessible via the public S3 endpoint — VPC-external Lambda is the simplest and verified pattern (see [FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns)) |
| `RecordVerdict` | DynamoDB + SNS only | ❌ No | Neither API requires VPC access |
| `Cleanup` | FSx control-plane API only (`DetachAndDeleteS3AccessPoint`, `DeleteVolume`) — never touches ONTAP directly | ❌ No | Both are public AWS APIs reachable without a VPC. Earlier versions of this stack deleted the FlexClone via the ONTAP REST API from inside the VPC — see the FlexClone-deletion note under Step 5 above for why that path was replaced with the FSx API, and why `Cleanup` no longer needs an FSx Interface Endpoint or VPC access at all |

> **Critical: VPC Endpoints**. `CreateFlexClone` needs Secrets Manager + STS reachable from the VPC (for ONTAP credentials). `ScanForIndicators` runs **outside the VPC** (Internet-origin AP) and does NOT need any VPC Endpoint. Neither `AttachAccessPoint` nor `Cleanup` needs any VPC Endpoint — both call only the FSx control-plane API, which is a public AWS API. The remaining two endpoints have independent parameters (`CreateSecretsManagerEndpoint`/`CreateStsEndpoint`, both default `true`) — set the ones your VPC already has to `false`. `CreateS3GatewayEndpoint` is still available for other Lambda functions in the VPC that may access S3, but is not required by this workflow's scan step.

---

## Before You Deploy: Check Existing VPC Endpoints

This is the single most common deployment failure for this stack, and it fails in a way that is easy to misdiagnose on a first read of the error: CloudFormation rejects a **second** Interface VPC Endpoint for a service that already has one in the same VPC with `PrivateDnsEnabled: true`, because both endpoints would try to register the same private DNS domain (e.g., `secretsmanager.<region>.amazonaws.com`) inside the VPC. The error message names the conflicting DNS domain, not the resource that already registered it, so it's easy to spend time looking in the wrong place.

**Run this before choosing your `CreateXxxEndpoint` parameter values:**

```bash
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=<your-vpc-id>" \
  --query "VpcEndpoints[].{Service:ServiceName,Type:VpcEndpointType,State:State}" \
  --output table
```

Match what you see against this table:

| If output shows... | Set this parameter to... |
|---------------------|---------------------------|
| `com.amazonaws.<region>.secretsmanager` (Interface) | `CreateSecretsManagerEndpoint=false` |
| `com.amazonaws.<region>.sts` (Interface) | `CreateStsEndpoint=false` |
| `com.amazonaws.<region>.s3` (Gateway) **and** it's already associated with the same route tables you're passing as `RouteTableIds` | `CreateS3GatewayEndpoint=false` |
| None of the above present | Leave all three at their `true` default |

Note there is no `CreateFsxEndpoint` parameter — `AttachAccessPoint` and `Cleanup` both call only the FSx control-plane API (a public AWS API), never the ONTAP REST API, so no Lambda in this stack needs an FSx Interface Endpoint. This is a deliberate simplification from an earlier version of this stack — see the FlexClone-deletion note under Step 5 in "How Verification Works" above for why.

If you're deploying this stack into the same VPC as [`automated-response.yaml`](automated-response-guide.md), that stack does not create any VPC Endpoints of its own — it relies on whatever your VPC already had before either stack was deployed. So the check above still applies in full; do not assume `automated-response.yaml` having been deployed first means any of these four endpoints already exist.

> **Route53-private-hosted-zone note**: Some organizations run FSx for ONTAP and other AWS services inside a VPC that is itself a "spoke" attached to a shared-services VPC via Transit Gateway, VPC peering, or a Virtual Private Gateway, with Interface VPC Endpoints created centrally in that shared-services VPC and their private hosted zones associated (via `AssociateVPCWithHostedZone`) to every spoke VPC that needs to resolve them. If your VPC is a spoke in this kind of topology, `describe-vpc-endpoints` run against the spoke VPC will show **no** endpoints even though `secretsmanager.<region>.amazonaws.com` (for example) already resolves inside that VPC via the shared-services VPC's endpoint — because the endpoint resource itself lives in a different VPC, while only its Route 53 private hosted zone association reaches yours. In this topology, creating this stack's own Interface Endpoint with `PrivateDnsEnabled: true` fails with the same DNS-domain-conflict error, but `describe-vpc-endpoints` alone won't have warned you. If `describe-vpc-endpoints` shows nothing but you suspect a shared-services topology, additionally check `aws route53 list-hosted-zones-by-vpc --vpc-id <your-vpc-id> --vpc-region <region>` for a private hosted zone matching the AWS service domain (e.g., `secretsmanager.<region>.amazonaws.com.`) before assuming `CreateXxxEndpoint=true` is safe — a matching zone means the service already resolves privately in your VPC via a centrally-managed endpoint, and this stack's own endpoint attempt for that service will fail the same way a literal duplicate would. This is not a hypothetical: it is the exact failure mode this project's own verification deployment hit against a shared-services VPC with FSx-, Secrets Manager-, SSM-, and SNS-related private hosted zones already associated from outside the VPC.

**If you deploy anyway and hit the conflict**: the stack rolls back automatically (`ROLLBACK_COMPLETE`), and CloudFormation does not let you retry a `deploy`/`create-stack` against a stack in that state — you must delete it first:

```bash
# Confirm the failure and see which resource conflicted
aws cloudformation describe-stack-events \
  --stack-name fsxn-restore-verification \
  --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].{Resource:LogicalResourceId,Reason:ResourceStatusReason}" \
  --output table

# Delete the rolled-back stack (safe — it never reached a working state,
# so there is no verification history in the ledger table to lose)
aws cloudformation delete-stack --stack-name fsxn-restore-verification
aws cloudformation wait stack-delete-complete --stack-name fsxn-restore-verification

# Re-deploy with the correct CreateXxxEndpoint values per the table above
```

---

## Deployment

### One Stack Deploy

> **`--s3-bucket` is required for this template.** It is 56,946 bytes, and
> CloudFormation accepts an inline template body only up to 51,200 bytes — larger
> templates have to be staged in S3 (up to 1 MB). Without `--s3-bucket` the deploy
> fails on a `ValidationError` that quotes the start of the template rather than
> naming the size limit, which reads like a syntax problem. Any bucket you can
> write to in the same region works; the CLI uploads the template and passes it by
> URL.

```bash
aws cloudformation deploy \
  --template-file shared/templates/restore-verification.yaml \
  --s3-bucket <your-cfn-staging-bucket> \
  --stack-name fsxn-restore-verification \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=<secret-arn> \
    FileSystemId=<fs-id> \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-1>,<subnet-2> \
    SecurityGroupId=<sg-id> \
    RouteTableIds=<route-table-1>,<route-table-2> \
    NotificationTopicArn=<optional-sns-topic-arn> \
    CreateSecretsManagerEndpoint=<true-or-false> \
    CreateStsEndpoint=<true-or-false> \
    CreateS3GatewayEndpoint=<true-or-false> \
  --capabilities CAPABILITY_NAMED_IAM
```

Set the three `CreateXxxEndpoint` values per the table in [Before You Deploy](#before-you-deploy-check-existing-vpc-endpoints) above — do not deploy with the defaults unchanged without first running the `describe-vpc-endpoints` check.

The stack creates:
- Step Functions state machine (`{stack-name}-workflow`), with a `Retry` block on `AttachAccessPoint` sized for the measured FSx for ONTAP sync delay (see Step 2 in "How Verification Works" above)
- 5 Lambda functions (create-clone, attach-ap, scan, record-verdict, cleanup) — only `create-clone` and `scan` run inside the VPC; `attach-ap`, `record-verdict`, and `cleanup` run outside it (see [Network Access](#network-access) above)
- DynamoDB ledger table (`{stack-name}-ledger`)
- CloudWatch Logs for the state machine (365-day retention) and each Lambda (90-day, except record-verdict at 365-day since it doubles as compliance evidence)
- Whichever of the Secrets Manager/STS (Interface) and S3 Gateway VPC Endpoints you left at `true` — see [Network Access](#network-access) above for which Lambda needs which endpoint, and [Before You Deploy](#before-you-deploy-check-existing-vpc-endpoints) for choosing each one independently

> **Change-management note**: For a change-advisory process, note what this deployment does and doesn't touch: it creates new, additive resources (a new Step Functions state machine, new Lambdas, a new DynamoDB table, and whichever VPC Endpoints you opted into) — it does not modify the FSx for ONTAP file system, existing ONTAP volumes, or any pre-existing VPC networking. The blast radius of a bad deploy is contained to these new resources; a `cloudformation delete-stack` rollback removes them without touching production storage. The one shared-state risk worth flagging in a change ticket: if any `CreateXxxEndpoint` parameter is left `true` for a service the target VPC already has an Interface Endpoint for (e.g., from `automated-response.yaml`'s VPC, or a centrally-managed shared-services VPC — see the Route53-private-hosted-zone note above), this deploy fails on a duplicate-endpoint DNS conflict and rolls back the whole stack rather than silently reusing the existing one. Run the [Before You Deploy](#before-you-deploy-check-existing-vpc-endpoints) check as part of the change ticket's pre-deployment validation, not as an afterthought if the first attempt fails.

> **Resource-tagging note**: Only `VerificationLedgerTable` carries the `Project`/`Purpose` tag pair in this stack's CloudFormation template — the five Lambda functions and the Step Functions state machine have no `Tags` property at all. If your organization relies on cost-allocation tags or resource-group tags to attribute spend or enforce tagging policy (e.g., AWS Config's `required-tags` rule, or a Service Control Policy denying untagged resource creation), this stack as shipped will either fail that enforcement or silently escape cost attribution for its Lambda/Step Functions spend, even though the DynamoDB table is correctly tagged. Add equivalent `Tags` blocks to the Lambda `AWS::Lambda::Function` resources and the `AWS::StepFunctions::StateMachine` resource before deploying into an account with tagging governance enforced.

> **Stack-update note**: No resource in this template sets `DeletionPolicy` or `UpdateReplacePolicy` (beyond the DynamoDB table's own delete-protection gap noted above) — a CloudFormation stack **update** that happens to replace `VerificationLedgerTable` (e.g., a change to `KeySchema` or `AttributeDefinitions`, which forces replacement rather than an in-place update) would delete the old table and its full verification history by default, not just a stack **deletion**. This is a narrower but easy-to-miss risk than the already-documented `delete-stack` case: a well-intentioned template change during routine maintenance can trigger the same data loss. Before making any change to this stack's DynamoDB table definition, run `aws cloudformation create-change-set` first and check whether the table shows `Replacement: True` in the change set output — if it does, export the table's contents first or add `DeletionPolicy: Retain` to `VerificationLedgerTable` so an update-triggered replacement leaves the old table orphaned-but-intact rather than deleted.

### Starting a Verification Run

```bash
aws stepfunctions start-execution \
  --state-machine-arn <StateMachineArn output> \
  --input '{
    "svm_name": "svm-prod-01",
    "volume_name": "vol_data",
    "snapshot_name": "incident_response_20260708_143022",
    "vpc_id": "vpc-0123456789abcdef0"
  }'
```

Chain this after the [Automated Response Guide](automated-response-guide.md)'s `create_snapshot` action by having your SOAR playbook or Step Functions fan-out (see that guide's FAQ) invoke this state machine with the newly created snapshot's name once containment completes.

> **Capacity-planning note**: This workflow does not set `ReservedConcurrentExecutions` or `MaxConcurrency` anywhere — each Step Functions execution runs its own five Lambda invocations against the account/Region's shared unreserved concurrency pool. On a large fleet where many snapshots are verified around the same time (e.g., a scheduled sweep across hundreds of volumes, or a mass-incident scenario triggering many `create_snapshot` actions at once), a burst of concurrent executions competes for that same pool with every other Lambda function in the account, including `automated-response.yaml`'s response handlers. Size your expected concurrent verification count against your account's Lambda concurrency limit before scheduling this against a large fleet, and consider a `MaxConcurrency` cap on whatever mechanism fans out executions (EventBridge Scheduler, Step Functions `Map` state, or your SOAR tool) rather than relying on the account-wide limit as an implicit throttle.

### Querying the Ledger

```bash
aws dynamodb query \
  --table-name fsxn-restore-verification-ledger \
  --key-condition-expression "snapshot_key = :sk" \
  --expression-attribute-values '{":sk": {"S": "svm-prod-01/vol_data/incident_response_20260708_143022"}}'
```

> **Query-pattern note**: `VerificationLedgerTable` defines only the base table's primary key (`snapshot_key` HASH, `started_at` RANGE) — no Global Secondary Index. The query above works well when you already know the `snapshot_key` (svm/volume/snapshot), which is the common case for "did this specific snapshot pass verification." It does not support a cross-partition query like "show me every `suspicious` verdict across the fleet this week" or "show me every verification that ran against `svm-prod-01`" — either requires a full table `Scan` with a `FilterExpression` (works, but scans every item and does not scale as the ledger grows) or adding a GSI if that access pattern becomes routine rather than occasional. If you add one, avoid `verdict` alone as the GSI partition key — it only takes three values (`clean`/`suspicious`/`error`), and on a large fleet where most runs are `clean`, that skew concentrates most of the GSI's write and query traffic onto a single partition; a composite key that includes something with higher cardinality (e.g., `svm_name` or a truncated `started_at` date) alongside `verdict` avoids that hot-partition risk. Decide which access pattern you actually need before assuming the base table alone is sufficient for a fleet-wide dashboard or report.

---

## Configuration Reference

| Parameter | Default | Purpose | Why this value |
|-----------|---------|---------|----------------|
| `FsxSyncWaitSeconds` | 600 | Static wait (seconds) before polling FSx API for clone discovery | FSx sync delay measured at 12–36 min. A 10-min static wait avoids ~5 guaranteed-to-fail Lambda invocations, saving cost and reducing CloudWatch Logs noise. Set to 0 to skip (all delay absorbed by polling). |
| `StepTimeoutSeconds` | 180 | Timeout for each Lambda invocation (CreateFlexClone, Scan, RecordVerdict, Cleanup) | ONTAP job completion + UUID lookup fits well within 180s. Increase only if your volume has millions of objects (Scan's `ListObjectsV2` pagination) |
| `AttachAccessPointTimeoutSeconds` | 60 | Timeout for a single AttachAccessPoint Lambda invocation | One attempt = 2-3 AWS API calls (DescribeVolumes + CreateAndAttachS3AccessPoint or DescribeS3AccessPointAttachments). 60s is generous for API calls alone — the multi-minute sync delay is handled by Step Functions Retry, not this timeout |
| `SuspiciousRatioThreshold` | 0.05 | Fraction of scanned objects with ransomware extensions to flag "suspicious" | 5% means a volume needs significant rename activity, not just a few test files |
| `SuspiciousMinCount` | 20 | Absolute floor on suspicious object count | Avoids false positives on small volumes (e.g., 3 legitimately-named `.encrypted` files out of 50) |
| `UnixUser` | root | UNIX identity for the S3 Access Point file-system access check | `root` (UID 0) can read all UNIX-style files. Change if your volume denies root or uses a specific service account |
| `LambdaMemorySize` | 512 MB | Memory for all verification Lambdas | 256 MB is sufficient for most runs; 512 provides headroom for large `ListObjectsV2` pagination |
| `CreateSecretsManagerEndpoint` | true | Create Interface VPC Endpoint for Secrets Manager | Set `false` if your VPC already has one (duplicate = deploy failure). Run `preflight-check.sh` first |
| `CreateStsEndpoint` | true | Create Interface VPC Endpoint for STS | Same as above — set `false` if already exists |
| `CreateS3GatewayEndpoint` | true | Create S3 Gateway VPC Endpoint | No longer required by ScanForIndicators (now runs outside VPC with Internet-origin AP). Still useful if other Lambda functions in this VPC need S3 access. Set `false` if not needed or already exists |

### Timing Budget Breakdown

The workflow's total wait budget for FSx for ONTAP sync is:

```
Total budget = FsxSyncWaitSeconds + (IntervalSeconds × MaxAttempts)
             = 600s (10 min)      + (120s × 25 = 3000s / 50 min)
             = 3600s (60 min)
```

| Phase | Duration | Cost | What happens |
|-------|----------|------|--------------|
| `WaitForFsxSync` | 10 min (configurable) | $0 (native Wait state) | Silent wait — no Lambda, no logs |
| `WaitForFsxDiscovery` polling | 120s interval × up to 25 attempts | ~$0.001 per attempt (Lambda sub-second) | Calls `fsx:DescribeVolumes`, raises `FsxDiscoveryPending` if not found |
| `AttachAccessPoint` S3 AP lifecycle | 15s interval × up to 12 attempts | ~$0.001 per attempt | Creates AP, polls for AVAILABLE status |
| `ScanForIndicators` AP policy propagation | 15s interval × up to 5 attempts | ~$0.001 per attempt | Retries on `AccessDenied` (S3 AP resource policy eventual consistency) |

**Design rationale — why a static Wait before polling?**

Without the static wait, the workflow would invoke Lambda every 120 seconds for the first 10+ minutes knowing each call will fail (FSx sync hasn't had enough time). Each failed invocation:
- Costs money (Lambda billing, minimal but non-zero)
- Generates a CloudWatch Logs entry (noise for operators monitoring the workflow)
- Consumes Step Functions state transitions (priced per transition for STANDARD type)

The `Wait` state is a Step Functions native state — zero Lambda invocations, zero CloudWatch Logs, zero cost. It simply pauses the execution for the configured duration, then hands off to the polling step which (after the wait) has a much higher probability of succeeding on the first or second attempt.

**Tuning for your environment:**

| Your observed FSx sync delay | Recommended `FsxSyncWaitSeconds` | Remaining poll budget |
|-----------------------------|----------------------------------|----------------------|
| < 10 min | 300 (5 min) | 50 min (plenty) |
| 10–20 min | 600 (10 min, default) | 50 min |
| 20–40 min | 900–1200 (15–20 min) | 50 min |
| Unknown (first deployment) | 600 (default) | 50 min — monitor first run, tune after |

> **How to measure your FSx sync delay**: Start a verification run and note the timestamp when `WaitForFsxDiscovery` first succeeds (visible in Step Functions execution history as the first `LambdaFunctionSucceeded` event for that state). Subtract the `CreateFlexClone` completion time and the `FsxSyncWaitSeconds` value. The remainder is how long polling actually took. Repeat 2-3 times to get a range, then set `FsxSyncWaitSeconds` to slightly below your minimum observed delay.

> **Cost note**: The recurring cost drivers per verification run are Lambda invocation time (5 short-lived functions, typically seconds each), DynamoDB on-demand writes (2-3 `PutItem`/`UpdateItem` calls per run against a `PAY_PER_REQUEST` table), Step Functions state transitions (this stack does not set `StateMachineType`, so it deploys as the default `STANDARD` type — priced per state transition, not per-request/per-duration like `EXPRESS`; `STANDARD` is the correct choice here specifically because `AttachAccessPoint`'s `Retry` block can span up to ~60 minutes, well past `EXPRESS`'s hard 5-minute execution limit), and — if `CreateVpcEndpoints=true` — the hourly cost of the Interface VPC Endpoints (Secrets Manager, STS, FSx) plus their per-GB data-processing charge; the S3 Gateway Endpoint itself has no hourly charge. None of these are metered per-scan-object the way [Amazon Comprehend pricing](https://aws.amazon.com/comprehend/pricing/) is for the companion PII scanner — cost here scales with *run frequency*, not volume size, so scheduling this workflow hourly against every snapshot on a large fleet is a meaningfully different cost profile than running it once per incident. Reuse Interface VPC Endpoints across this stack and `automated-response.yaml` in the same VPC (`CreateVpcEndpoints=false`) rather than paying for duplicates.

> **Fixed bug — `DeleteVolume` was silently creating a backup of every throwaway clone**: `fsx.delete_volume()` takes a final backup of the volume by default unless `OntapConfiguration.SkipFinalBackup=True` is explicitly passed — this is standard, sensible behavior for a real data volume, but it means `Cleanup`, unless it passes this flag, was creating one `USER_INITIATED` backup (billed backup storage, retained indefinitely with no automatic expiry) *per verification run*, for a clone that exists only to be scanned and discarded. Worse: this project's own end-to-end verification hit a case where the final-backup step itself failed partway through, leaving the volume stuck in a `FAILED` lifecycle state that then blocked all further deletion attempts (`Cannot take backup while <volume> is in FAILED`) until retried with `SkipFinalBackup=True`. The current implementation passes `OntapConfiguration={"SkipFinalBackup": True}` on every `delete_volume` call. If you fork or extend this workflow, keep that flag — omitting it silently accumulates backup storage cost with no corresponding recovery value (the clone was never independently valuable data; it's a scan target). This is unrelated to your file system's own `DailyAutomaticBackupStartTime`/`AutomaticBackupRetentionDays` settings, which govern automatic backups of your real volumes and should not be disabled to work around this — the fix belongs in the `delete_volume` call, not in your file system's backup policy.

> **Sustainability note**: This is a fundamentally lightweight workload from a compute-carbon perspective — five short-lived Lambda invocations per run, each doing I/O-bound work (an ONTAP REST call, an S3 list, a DynamoDB write) rather than sustained CPU-bound computation, and no persistent compute (no EC2, no always-on container) idling between runs. The same observation as the cost note above applies here in different units: *run frequency* is the lever that matters for energy consumption, not volume size, since `ScanForIndicators` only lists object keys (`ListObjectsV2`) rather than reading file contents. Scheduling this hourly against every snapshot on a large fleet multiplies invocation count (and therefore energy draw) linearly with schedule frequency — if a periodic recovery-readiness check is the goal rather than post-incident verification, a daily or weekly cadence against representative volumes is likely to meet that goal with meaningfully less aggregate energy use than an hourly sweep of an entire fleet.

---

## Security Considerations

- **No production data path**: The FlexClone shares blocks with its parent via copy-on-write, but the S3 Access Point only exposes the *clone*, not the parent volume. Deleting the clone at cleanup does not affect the parent volume or the original snapshot.

> **Evidence-integrity note**: This property matters beyond production-impact avoidance if the snapshot being verified was itself created as protective evidence — for example, by the [Automated Response Guide](automated-response-guide.md)'s `create_snapshot` action during an active incident. Because this workflow only ever reads from and deletes the *clone* — `CreateFlexClone`, `AttachAccessPoint`, `ScanForIndicators`, and `Cleanup` never write back to the source snapshot — running verification against a protective snapshot does not alter or compromise that snapshot's own chain of custody. That's a separate question from whether the *verdict* this workflow records would itself hold up as evidence in an investigation; see the [Automated Response Security Addendum](automated-response-security-addendum.md#chain-of-custody-requirements-dfir)'s Chain of Custody Requirements table for the gap on that side (pre-action state and the triggering message are not currently hashed).
- **Internet-origin access point with IAM enforcement**: The access point is created as Internet-origin (no VpcConfiguration), but FSx for ONTAP enforces S3 Block Public Access on all attached APs — anonymous/public access is impossible. Access requires valid IAM credentials. This is the verified working pattern; VPC-origin APs caused persistent AccessDenied in E2E testing (see Step 2 above for the full rationale).

> **Data-residency note**: Every resource this workflow creates — the FlexClone (same Region as the parent FSx for ONTAP file system, by definition), the S3 Access Point, the DynamoDB ledger table, and the Lambda functions themselves — stays within the single AWS Region you deploy this stack into. There is no cross-Region data movement anywhere in this workflow, which is a relevant point to confirm affirmatively for a data-residency questionnaire rather than leaving it as an inference. If your organization operates this workflow across multiple Regions (one stack per Region, per the [Multi-Account Deployment](multi-account-deployment.md) pattern referenced in the Cyber Resilience Capability Map), each Region's ledger is independent — there is no built-in cross-Region replication or aggregation of verification history, so a residency-driven multi-Region deployment also means a per-Region evidence trail unless you build your own aggregation layer.
- **Least privilege for `fsx:*S3AccessPoint*` actions**: These actions do not currently support resource-level permissions or condition keys as fine-grained as other FSx actions; the IAM policy in this stack scopes them to `Resource: '*'` with a documented rationale. Revisit this if AWS adds resource-level support.

> **IAM-design note**: `VerificationLambdaRole` is a single shared role attached to all five Lambdas (see the CloudFormation template), rather than five distinct roles scoped to each function's actual needs — `ScanForIndicators` (which only needs `s3:ListBucket`/`s3:GetObject`) can technically assume the same broad `Resource: '*'` `fsx:*S3AccessPoint*` permissions that `AttachAccessPoint`/`Cleanup` actually use, even though it never calls those APIs. This is a common, reasonable tradeoff for a single-purpose stack (fewer roles to audit, no cross-role `iam:PassRole` complexity), but it does mean a compromised `ScanForIndicators` execution environment has more IAM reach than its own code requires. If your organization's IAM standard requires role-per-function least privilege even within a single workflow, split `VerificationLambdaRole` into per-Lambda roles scoped to exactly the policy statements each function's code path calls.
- **Verdict ledger as evidence, not a substitute for governance**: The DynamoDB ledger supplies auditable evidence (who verified what, when, with what result) that a CSF 2.0 Govern program can consume — see the [Cyber Resilience Capability Map](cyber-resilience-capability-map.md#govern-gv)'s Govern-function discussion for why this repo does not attempt to automate the governance program itself.
- **The ledger table has no delete protection by default**: `VerificationLedgerTable` is created with Point-in-Time Recovery enabled but without `DeletionProtectionEnabled` or an explicit `DeletionPolicy: Retain`. If this table is your primary evidence of periodic restore testing (see the cyber-insurance evidence note below), add `DeletionProtectionEnabled: true` and consider a `DeletionPolicy: Retain` override before relying on it for audit purposes — an accidental `aws cloudformation delete-stack` should not be able to erase your evidence history.

> **Encryption-at-rest note**: `VerificationLedgerTable`'s `SSESpecification` enables encryption with `SSEEnabled: true` but does not set `SSEType`/`KMSMasterKeyId`, which means it encrypts with the AWS-owned DynamoDB key rather than a customer-managed KMS key. This is a reasonable default for most workloads and meets "encrypted at rest," but if your organization's key-management policy requires customer-managed KMS keys (for independent rotation control, cross-account key policies, or a specific compliance mapping), add `SSEType: KMS` and `KMSMasterKeyId: <your-key-arn>` explicitly — the current template does not expose this as a parameter. The same applies to the state machine's and Lambdas' CloudWatch Log Groups, which are also encrypted with the AWS-owned CloudWatch Logs key by default (no `KmsKeyId` set on any `AWS::Logs::LogGroup` resource in this stack).

> **Audit-trail-completeness note**: The `VerificationStateMachine`'s `LoggingConfiguration` sets `Level: ALL` and `IncludeExecutionData: true`, which is exactly what you want for full observability — but "full observability" here also means the complete input/output payload of every state (including `svm_name`, `volume_name`, `snapshot_name`, and any `reason`/`error` text) is written to CloudWatch Logs on every execution, not just the DynamoDB ledger's curated fields. If `volume_name` or `snapshot_name` values in your environment ever embed sensitive context (an incident ticket number, an organization or individual name in a snapshot label), that context now also lives in CloudWatch Logs with this stack's log-group retention (365 days) rather than only in the ledger table you might otherwise assume is the single source of truth. Review your snapshot- and volume-naming conventions with this in mind, or set `IncludeExecutionData: false` if the full payload in logs is more exposure than you want, accepting the corresponding loss of debugging detail.

> **Cyber-insurance evidence note**: Underwriting checklists for cyber insurance increasingly ask for evidence of *tested* backups, not just backup existence — "periodic restore verification" is called out explicitly in several 2026 underwriting guides. The DynamoDB ledger this workflow produces (timestamped `snapshot_key`, verdict, and `reason` per run) is a reasonable evidence artifact for that question, but it wasn't designed with an insurer's evidence format in mind: there is no built-in export to PDF/CSV for a renewal questionnaire, and (as noted above) no delete protection guarding the evidence itself. If you plan to cite this ledger during underwriting or renewal, export a snapshot of the relevant `dynamodb query` output alongside your other control evidence, and enable the delete protection above first.

> **Audit-evidence note**: If this workflow is cited as a control in an internal audit or SOX control matrix (e.g., a control asserting "recovery points are verified before restoration"), the auditor's test of operating effectiveness will typically want to see: (1) evidence the control actually *ran* on a sample of periods, not just that it *exists* — the DynamoDB ledger's `started_at`/`completed_at` timestamps support this; (2) evidence someone with appropriate authority *reviewed* a "suspicious" or "error" verdict and took action, which this workflow does not itself produce — an SNS notification firing is evidence an alert was *sent*, not evidence anyone *acted* on it; (3) segregation of duties between whoever can modify this stack's IAM permissions/code (effectively controlling what "clean" means) and whoever relies on its verdict to authorize a restore. If a SOX control depends on this workflow, pair the automated verdict with a manual attestation step (a ticket, an approval workflow) that captures human review and action, and retain that attestation alongside the DynamoDB record as the actual audit evidence — the ledger alone documents that a scan ran, not that anyone exercised judgment on its result.

---

## Testing

The `restore_verification.py` module has 23 unit tests covering:

| Category | What's Verified |
|----------|-----------------|
| FlexClone create/delete | Success, job failure, clone-lookup failure, already-deleted (404) |
| S3 Access Point attach/detach | Success, fsvol resolution timeout, MISCONFIGURED state, ClientError handling, not-found on detach |
| Ransomware indicator scan | Clean volume, suspicious volume, empty volume, case-insensitive extension matching |
| Full orchestration | Clean verdict, suspicious verdict, below-min-count false-positive avoidance, error path with cleanup, error-after-clone-created cleanup, result serialization capping (`suspicious_objects` truncated to the first 50 entries in `to_dict()`; `suspicious_object_count` always reflects the true total) |

```bash
python3 -m pytest shared/python/tests/test_restore_verification.py -v
# 23 passed in 0.11s
```

> **Test-coverage note**: All 23 tests run against mocked `boto3` clients and mocked ONTAP HTTP responses — none of them exercise a real FSx for ONTAP file system or a real S3 Access Point. This keeps the suite fast and CI-safe (no AWS credentials or live infrastructure required), but it also means these tests cannot catch drift between the mocked API contracts and the real ONTAP REST API/AWS FSx API behavior. Treat `pytest` passing as necessary but not sufficient evidence the workflow works end-to-end — validate against a real (non-production) FSx for ONTAP file system before relying on this in production, and re-validate after any ONTAP or AWS SDK version bump that touches the FlexClone or S3 Access Point APIs.

> **CI-coverage note**: "CI-safe" above describes the tests' own design (no live infrastructure needed) — it does not mean this repository's CI workflow actually runs them. As of this writing, `.github/workflows/ci.yaml`'s Python test steps cover each vendor integration's `tests/` directory and `shared/lambda-layers/ems-parser/tests/`, but not `shared/python/tests/`, so these 23 tests execute only when a contributor runs `pytest` locally, not automatically on every push or pull request. A change to `restore_verification.py` that breaks one of these tests will not fail CI today. If you rely on this test suite as a merge gate, add a step to the CI workflow that runs `python -m pytest shared/python/tests/ -v` alongside the existing per-vendor and shared-layer steps, the same way `ems-parser` is already covered.

> **License note**: This workflow's runtime dependencies are `boto3`, `botocore`, and `urllib3` — all bundled with the Lambda `python3.12` managed runtime rather than vendored into this repo, so there is no `requirements.txt` or lockfile in this stack to license-scan directly. All three are [Apache License 2.0](https://github.com/boto/boto3/blob/develop/LICENSE), which is permissive and imposes no copyleft obligations, so this is a low-friction dependency set from a license-compliance standpoint. If your organization runs automated license scanning (e.g., an SBOM generator or a tool like `pip-licenses`) as part of its supply-chain review, note that scanning the inline CloudFormation `ZipFile` code directly won't surface these dependencies the way a `requirements.txt`-based deployment would — you'd need to scan against the Lambda managed runtime's own published dependency manifest instead, or migrate to the pinned-dependency deployment approach mentioned in the Patch-management note above, which would also make this scanning straightforward.

> **Operational-triage note**: If paged for a failed verification run, start here: (1) check the Step Functions execution history for the `StateMachineExecutionArn` — the failing state name tells you which of the five Lambdas failed; (2) check that Lambda's CloudWatch Logs (`/aws/lambda/{stack-name}-{create-clone|attach-ap|scan|record-verdict|cleanup}`) for the actual exception; (3) query the DynamoDB ledger for the `snapshot_key` in question — an `error` verdict with a `reason` field is often enough to triage without touching logs at all. A failed run does not require immediate remediation at 2am: the workflow's own `Cleanup`/`CleanupAfterError` states already guarantee no orphaned FlexClone or S3 Access Point is left behind (see Architecture above), so this is a "investigate when convenient" alert, not a "stop the bleeding" one — unless the same `snapshot_key` fails repeatedly, which may indicate an ONTAP-side or IAM-side problem worth escalating sooner.

> **Failure-injection note**: The Testing section above validates the happy path and several mocked failure modes, but the claim that "cleanup runs on every path" (see Architecture) is best validated by actually injecting failures against a real (non-production) deployment rather than trusting the Step Functions `Catch` topology alone. Worthwhile experiments: (1) kill the `ScanForIndicators` Lambda mid-execution (e.g., a temporary IAM deny on `s3:ListBucket`) and confirm the FlexClone is actually deleted afterward, not just that the state machine reports success; (2) temporarily revoke `fsx:DeleteVolume` from the Lambda role and confirm `Cleanup` (the one Lambda with no downstream catch of its own) reports `cleaned_up: false` and logs the failure clearly, rather than silently swallowing it — and confirm you can detect and manually remediate an orphaned clone when cleanup-of-cleanup has no safety net; (3) verify what happens if `RecordVerdict` succeeds but the subsequent `Cleanup` step's own Lambda invocation fails to even start (a Step Functions-level fault, not an application error) — confirm your CloudWatch alarms would actually catch that gap; (4) since this project's own verification observed FSx for ONTAP sync delay *increasing* across three separate runs against the same idle file system (~12 min, then ~24 min, then ~36 min), run several verification executions back-to-back — not just once — and confirm none of them exhaust `AttachAccessPoint`'s 28-attempt retry budget; a single successful run does not confirm the budget is adequate given the trend this project observed. If your environment's actual delay runs longer than this project's own measurements (e.g., a busier or larger file system, or the same increasing pattern continuing further), you will need to increase `MaxAttempts` in the state machine definition — there is currently no evidence of an upper bound on this delay. None of this requires production data; a throwaway SVM/volume with a synthetic snapshot is sufficient. Treat these four experiments as a small, workflow-specific game day rather than a one-time pre-launch checklist — run them again after any change to `AttachAccessPoint`'s retry parameters, `Cleanup`'s logic, or an ONTAP/AWS SDK version bump, the same way you'd re-run a chaos experiment after a change to the system it targets.

> **Retry-policy note**: Only `AttachAccessPoint` defines a `Retry` block (sized for the measured FSx for ONTAP sync delay — see Step 2 above); `CreateFlexClone`, `ScanForIndicators`, `RecordVerdict`, and `Cleanup` define a `Catch` block but no `Retry` block. A transient failure in any of those four (an ONTAP management endpoint momentarily unreachable, a brief Secrets Manager or DynamoDB throttle, a one-off network blip inside the VPC) routes straight to `CleanupAfterError`/`RecordErrorVerdict` on the first attempt rather than retrying with backoff first. This means a workflow that would have succeeded on a second attempt instead records a permanent "error" verdict and triggers a full clone-and-cleanup cycle for what was, in fact, a recoverable blip. If your environment sees occasional transient ONTAP API or AWS API errors on these four states, consider adding a `Retry` block (e.g., `ErrorEquals: ["States.Timeout", "States.TaskFailed"]` with a short `IntervalSeconds`/`MaxAttempts`/`BackoffRate`) before falling through to `Catch` — this is a standard Step Functions pattern only `AttachAccessPoint` currently uses in this workflow, and for a different reason (a known, expected delay) than a generic transient-error retry would be.

> **SNS-delivery note**: Both `RecordVerdict` and `RecordErrorVerdict`'s SNS `publish` calls are wrapped in a bare `try`/`except Exception` that only logs a warning on failure (`logger.warning("Verdict notification failed: %s", e)`) — the DynamoDB `put_item` before it always succeeds or raises, but a failed SNS publish (a deleted topic, a permissions change, throttling) is swallowed silently from the caller's perspective. The Step Functions execution still reports success, and the DynamoDB ledger still has the correct verdict, but the notification you were relying on to learn about a "suspicious" result may simply never have arrived — and nothing in this workflow's own CloudWatch metrics distinguishes "no PII found, no notification needed" from "notification attempted and failed." If SNS delivery is part of your incident-response trigger chain, add a CloudWatch Alarm on this Lambda's own log pattern (`"notification failed"`) rather than assuming a lack of alerts means a lack of suspicious verdicts.

---

## Related Documents

- [Cyber Resilience Capability Map](cyber-resilience-capability-map.md#recover-rc) — the Recover-function discussion this guide implements a fix for
- [Automated Response Guide](automated-response-guide.md) — the Respond-phase blocking and protective snapshot creation that typically precedes running this workflow
- [ARP Incident Response Guide](arp-incident-response-guide.md) — the live-volume entropy detection this workflow's scan complements but does not replace
- [Content-Level PII Classification Scanner](content-classification-scanner.md) — a related content-scanning capability for the CSF 2.0 Identify function (data classification), built on the same FlexClone + S3 Access Point pattern
- [Governance & Compliance](governance-and-compliance.md) — where the verdict ledger fits as Govern-function evidence
- [Compliance Evidence Pack](compliance-evidence-pack.md) — audit-trail evidence artifact templates

## FAQ

**Q: Does a "clean" verdict guarantee the snapshot is safe to restore?**
A: No — see the Recovery-sufficiency note above. It's a fast, automated pre-filter based on file-extension patterns, not a full malware scan or application-level integrity check. Treat it as a necessary first gate, not the final word.

**Q: Why FlexClone instead of just scanning the production volume directly?**
A: Scanning the live volume would compete with production I/O and risk interacting with an active attack. A FlexClone is an isolated, point-in-time, read/write copy that shares blocks via copy-on-write — verification runs against it with zero production impact, and it's deleted afterward.

**Q: Why an S3 Access Point instead of mounting the clone via NFS/SMB?**
A: Mounting requires network-level access to the SVM's data LIF and a running NFS/SMB client in the verification environment. An S3 Access Point lets a stateless Lambda list and read files via the S3 API with no mount step, and (when VPC-scoped) with no path to the production data plane beyond the read-only access point itself.

**Q: What happens if the workflow fails partway through — is the clone left behind?**
A: No. The Step Functions `Catch` blocks route every failure mode to the same `Cleanup` Lambda used on the success path. The cleanup Lambda tolerates partial state (e.g., a clone was created but the access point attach failed) and cleans up whatever exists.

**Q: Can I run this against snapshots other than the ones created by the Automated Response module?**
A: Yes. `verify_snapshot()` / the Step Functions input only need `svm_name`, `volume_name`, and `snapshot_name` — any existing ONTAP snapshot works, including scheduled Snapshot Policy snapshots unrelated to an incident.

**Q: A user reports "the verification never finishes" — what's the first thing to check, before escalating?**
A: Check the Step Functions execution's current state in the console or via `describe-execution` first — "never finishes" almost always means it's stuck in (or retrying within) a specific state, not silently hung everywhere. If it's `RUNNING` inside `AttachAccessPoint` with several `FsxDiscoveryPending`/`S3AttachPending` failures visible in the execution history, that's very likely **expected behavior**, not a hang — see Step 2 in "How Verification Works" for the measured ~12/~24/~36-minute (and increasing across runs) FSx for ONTAP sync delay this state's `Retry` block is designed around; check whether the elapsed time is still within the ~60-minute retry budget before treating it as an incident, and be aware this project's own data suggests the delay may not have a confirmed upper bound. If it's stuck in `ScanForIndicators`, the most common cause is a VPC networking gap (see Network Access above — e.g., the S3 Gateway Endpoint missing, which fails deterministically every time, not intermittently). If it's stuck in `CreateFlexClone`, check whether the ONTAP volume/SVM name in the request actually exists and whether the ONTAP job it's polling shows progress via `GET /api/cluster/jobs/{uuid}` directly against the management endpoint. `CreateFlexClone`, `ScanForIndicators`, `RecordVerdict`, and `Cleanup` each have a `StepTimeoutSeconds` bound (default 180s) with no retry, so a true hang in one of those four should surface as a Step Functions `States.Timeout` error within that window rather than running indefinitely.

**Q: `AttachAccessPoint` exhausted all 28 retry attempts and the workflow recorded an error verdict — is 60 minutes not long enough?**
A: Possibly — this project's own measurements across three separate runs against the same idle file system (~12 min, then ~24 min, then ~36 min) showed an *increasing* pattern, not a stable range, so there is no confirmed upper bound to size a retry budget against with full confidence. A busier or larger file system in your environment could plausibly exceed 60 minutes. Check the DynamoDB ledger's `reason` field and the `Cleanup` Lambda's logs first to confirm the clone was actually torn down (see the Orphaned-clone note under Step 5 — this project's own verification left an orphaned clone that only became visible roughly 35-37 minutes after creation, after the workflow had already given up under a smaller retry budget in effect at the time). If this recurs, increase `MaxAttempts` (and/or `MaxDelaySeconds`) in the `AttachAccessPoint` Retry block in the state machine's `DefinitionString`, redeploy, and run this project's own measurement approach (continuous polling with `aws fsx describe-volumes`, no observation gaps, across multiple separate runs — not just one) against your specific file system to get your own number to size against, rather than assuming this project's measured range applies unchanged to your environment.

**Q: We're migrating an existing FSx for ONTAP fleet to this verification workflow — where do we start?**
A: Deploy this stack against one volume first, and run `verify_snapshot()` manually against an existing scheduled snapshot before wiring it into any automated trigger — this confirms the ONTAP permissions, VPC networking, and FSx S3 Access Point support actually work against your specific environment (ONTAP version, VPC configuration) before you depend on it. Existing snapshots created before this workflow existed work without any special handling; there's no metadata or tagging this workflow requires a snapshot to have retroactively. Once validated on one volume, expand to your full fleet using the same [Multi-Account Deployment](multi-account-deployment.md) StackSets pattern referenced in the Cyber Resilience Capability Map, rather than manually deploying per-volume or per-account.

**Q: A "suspicious" verdict was recorded in the DynamoDB ledger, but no one received an SNS notification — is that expected?**
A: Check CloudWatch Logs for the `RecordVerdict` Lambda for a `"Verdict notification failed"` warning before assuming the notification pipeline is broken end-to-end. The SNS `publish` call is wrapped in a try/except that logs and continues rather than failing the workflow (see the SNS-delivery note under Security Considerations above) — this is intentional, so a notification-delivery problem never blocks the verdict from being recorded, but it also means notification failures are silent unless you're watching for that specific log line. If you rely on this notification as an incident-response trigger, add a CloudWatch Alarm on that log pattern rather than treating "no alert received" as equivalent to "nothing suspicious happened."

**Q: We changed the DynamoDB table's key schema and now the old verification history is gone — what happened?**
A: A CloudFormation stack update that changes `VerificationLedgerTable`'s `KeySchema` or `AttributeDefinitions` forces DynamoDB table replacement rather than an in-place update, and this template does not set `DeletionPolicy: Retain` on that table (see the Stack-update note under Deployment above) — CloudFormation deleted the old table as part of applying the replacement. This is recoverable only if Point-in-Time Recovery was enabled and you restore within its retention window; going forward, run `aws cloudformation create-change-set` before any schema-affecting update and check for `Replacement: True` in the output.


---

## See Also

- [Deployment Guide](deployment-guide.md) — Pre-flight validation, VPC Endpoint conflict avoidance, and parameter file setup for this stack
- [Automated Response Guide](automated-response-guide.md) — Incident response actions that precede recovery verification
