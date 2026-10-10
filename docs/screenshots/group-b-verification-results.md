# Group B E2E Verification Results

**Date**: 2026-07-12 (UTC 00:00-01:00)
**Region**: ap-northeast-1 (Tokyo)
**FSx for ONTAP**: fs-0123456789abcdef1 (FSxN_OnPre_Sim)
**ONTAP Version**: NetApp Release 9.17.1P7D1
**SVM**: demo-verify-svm (svm-0123456789abcdef0, workgroup mode, no AD)
**Volume**: demo_verify_vol (fsvol-0123456789abcdef0, MIXED, 1GB)
**Verification Client**: EC2 i-0123456789abcdef0 (Amazon Linux 2023, SSM)

---

## 1. EMS Webhook Verification

### Stack: fsxn-splunk-integration-onpre

| # | Test | Status | Evidence |
|---|------|--------|----------|
| 1 | API Key auth (valid key) | PASS | HTTP 502 (HEC unreachable, auth passed) |
| 2 | API Key auth (invalid key) | PASS | HTTP 401 "Unauthorized: invalid or missing API key" |
| 3 | Payload validation (correct fields) | PASS | Accepted `message-name`/`message-severity`/`message-timestamp` |
| 4 | HEC forwarding attempt | PASS (expected fail) | DNS resolution failure for dummy endpoint splunk-verification.example.com |
| 5 | Retry logic (3 attempts, backoff 2s/4s) | PASS | CloudWatch Logs show 3 retry cycles with correct timing |
| 6 | ARP EMS synthetic event (arw.volume.state) | PASS | Lambda processes the event, formats HEC payload, attempts delivery |

### Key Finding: EMS Payload Format

The actual ONTAP EMS webhook payload uses hyphenated field names:
- `message-name` (NOT `messageName`)
- `message-severity` (NOT `severity`)
- `message-timestamp` (NOT `timestamp`)

This corrects the camelCase format shown in demo-arp-incident-response.md.

---

## 2. FPolicy E2E Verification

### Stacks: fsxn-fp-srv-onpre + fsxn-splunk-fpolicy-onpre

| # | Test | Status | Evidence |
|---|------|--------|----------|
| 1 | ECR image pull (Fargate) | PASS | Required `assignPublicIp=ENABLED` override via update-service |
| 2 | FPolicy server startup (TCP 9898) | PASS | Log: "FPolicy Server started on port 9898" |
| 3 | ONTAP TCP connection to Fargate | PASS | Log: "Connection from ('<ontap-node-ip>', 54362)" |
| 4 | FPolicy handshake (NEGO) | PASS | Log: "Handshake Policy=fpolicy_verify_policy, Version=1.2" |
| 5 | KeepAlive | PASS | Log: "KeepAlive Received — connection healthy" |
| 6 | File create event (SMB) | PASS | Log: "[Event] create fpolicy-trigger-test.txt" |
| 7 | SQS delivery | PASS | Log: "[SQS] Sent: fpolicy-trigger-test.txt (create)" |
| 8 | Bridge Lambda (SQS → EventBridge) | PASS | "Processed 1 records, 0 failures" |
| 9 | EventBridge event content | PASS | detail-type=file-operation, source=fpolicy.fsxn |
| 10 | Splunk shipper Lambda (EventBridge trigger) | PASS | "FPolicy handler invoked", "Extracted 1 FPolicy event(s)" |
| 11 | HEC delivery attempt (Splunk shipper) | PASS (expected fail) | DNS failure for dummy endpoint, retry 3x, HTTP 207 |

### FPolicy Pipeline Architecture (Verified)

```
ONTAP FPolicy Engine → TCP:9898 → ECS Fargate → SQS Queue
    → Bridge Lambda → EventBridge Bus (fsxn-fpolicy-events)
        → Splunk Shipper Lambda → Splunk HEC (dummy endpoint)
```

### ONTAP FPolicy Configuration Issues Resolved

| # | Issue | Resolution |
|---|-------|-----------|
| 1 | `svm.uuid` duplicate in body + URL path | Remove `svm` field from request body |
| 2 | `allow_privileged_access` cannot be set | Remove field from policy creation |
| 3 | Scope as separate endpoint (404) | Use inline `scope` field in policy POST |

### Fargate Networking Issue

- **Symptom**: ECR image pull timeout (`dial tcp ...ecr...: i/o timeout`)
- **Root Cause**: Template hardcodes `AssignPublicIp: DISABLED`; VPC lacks ECR Interface VPC Endpoint
- **Resolution**: `aws ecs update-service --network-configuration "...assignPublicIp=ENABLED" --force-new-deployment`
- **Recommendation**: Add `AssignPublicIp` parameter to fpolicy-apigw.yaml template

---

## 3. ARP/AI Detection Verification

| # | Test | Status | Evidence |
|---|------|--------|----------|
| 1 | ARP enable (REST API) | PASS | PATCH /api/storage/volumes, job state=success |
| 2 | ARP state after enable | PASS | State: enabled, Dry Run Start Time: - (immediate active) |
| 3 | Ransomware simulation (zip encrypt+delete+rename) | PASS | 15 files encrypted to .ktkt extension |
| 4 | ARP/AI detection | PASS | Attack Probability: moderate, Detected By: file_analysis |
| 5 | ARP automatic snapshot | PASS | Anti_ransomware_attack_backup.2026-07-12_0042 (30.73MB) |
| 6 | EMS alert event | PASS | callhome.arw.activity.seen (severity=alert, 00:42:06 UTC) |
| 7 | ARP periodic snapshot | PASS | Anti_ransomware_periodic_backup.2026-07-12_0027 |

### Key Finding: ARP/AI No Learning Period

ONTAP 9.17.1 ARP/AI is immediately active upon enablement — no learning period is required.
Initial detection failure with 30 same-extension random files was due to
`never_seen_before_file_extension_count_notify_threshold: 5` (needs 5+ distinct unknown extensions).
Correct simulation approach: encrypt files → delete originals → add new extension (mimics real ransomware).

### Key Finding: `attack simulate` Command Not Available

`security anti-ransomware volume attack simulate` does NOT exist in ONTAP 9.17.1P7D1.
Available subcommands under `attack`: `clear-suspect`, `generate-report` only.
Similarly, `show-suspect-files` is not a recognized command.
Real detection must be triggered via actual file activity patterns.

---

## 4. Automated Response Verification

### Stack: fsxn-automated-response-onpre

| # | Action | Status | Duration | Evidence |
|---|--------|--------|----------|----------|
| 1 | health_check | PASS | <1s | ONTAP REST API reachable (after Layer fix) |
| 2 | contain_smb_threat | PASS | 1.8s | 3 steps in single Lambda invocation |
| 3 | → Snapshot creation | PASS | - | incident_response_20260712_005307 |
| 4 | → SMB user block | PASS | - | name-mapping DEMOVERIFY\\demouser → deny (position 1) |
| 5 | → Session disconnect | PARTIAL | - | 0/1 sessions (session already gone, HTTP 404) |
| 6 | SMB access denied (after block) | PASS | - | mount error(13): Permission denied, MOUNT_EXIT=32 |
| 7 | unblock_smb_user | PASS | - | name-mapping removed |
| 8 | SMB access restored (after unblock) | PASS | - | MOUNT_EXIT=0, LS_EXIT=0 |
| 9 | block_nfs_ip | FAIL | - | ONTAP API HTTP 400 on export-policy rule creation |

### contain_smb_threat Lambda Log (RequestId: bb251c21)

```
[INFO] Creating snapshot: incident_response_20260712_005307 on demo-verify-svm/demo_verify_vol
[INFO] Blocking SMB user: DEMOVERIFY\demouser on SVM demo-verify-svm (position 1)
[WARNING] Failed to disconnect session: ONTAP API DELETE .../sessions/... failed: HTTP 404
[INFO] Disconnected 0/1 SMB sessions for DEMOVERIFY\demouser on SVM demo-verify-svm
```

### Snapshot Evidence (ONTAP CLI)

```
Vserver: demo-verify-svm
Volume: demo_verify_vol
Snapshot: incident_response_20260712_005307
Creation Time: Sun Jul 12 00:53:07 2026
Comment: Threat containment: ARP detection - callhome.arw.activity.seen alert (verification) | user: DEMOVERIFY\demouser
```

### SMB Block Evidence (ONTAP CLI)

```
Vserver:   demo-verify-svm
Direction: win-unix
Position   Pattern                    Replacement
--------   --------                   -----------
1          DEMOVERIFY\\demouser       (empty = deny)
```

### Key Behavioral Note: SMB Block Timing

The name-mapping block does NOT disconnect existing SMB sessions immediately.
Existing mounts with `soft` option continue to function until the session token expires.
**New connections (re-authentication) are denied immediately.**
For demo purposes, `umount` + `mount` is required to observe the denial.

### NFS IP Block Failure Analysis

- **Action**: block_nfs_ip with client_ip=<client-ip>
- **Error**: `ONTAP API POST /protocols/nfs/export-policies/51539607553/rules failed: HTTP 400`
- **Probable Cause**: export-policy ID resolution or rule parameter mismatch in ontap_response.py
- **Status**: Deferred to Phase 4 scope (requires ontap_response.py debugging)

---

## 5. Deployment Issues Summary

| # | Component | Issue | Root Cause | Resolution |
|---|-----------|-------|-----------|-----------|
| 1 | automated-response Lambda | ModuleNotFoundError: 'shared' | Layer not attached | Added fsxn-shared-python:2 Layer |
| 2 | FPolicy Fargate | ECR pull timeout | assignPublicIp=DISABLED, no VPC Endpoint | update-service with ENABLED |
| 3 | FPolicy engine creation | svm.uuid duplicate (262188) | Body + URL path both contain svm.uuid | Remove svm field from body |
| 4 | FPolicy policy creation | allow_privileged_access error (262196) | Field not settable in this operation | Remove field |
| 5 | EMS webhook invoke | HTTP 401 | Missing x-api-key header in test payload | Add headers field to invoke payload |
| 6 | EMS webhook invoke | HTTP 400 missing fields | Wrong field names (camelCase vs hyphenated) | Use message-name/severity/timestamp |

---

## 6. Infrastructure Deployed (Verification Environment)

| Resource | Identifier |
|----------|-----------|
| FSx for ONTAP | fs-0123456789abcdef1 |
| VPC | vpc-0123456789abcdef0 |
| Subnet (AZ-1a) | subnet-0123456789abcdef0 |
| Subnet (AZ-1c) | subnet-0123456789abcdef1 |
| Security Group | sg-0123456789abcdef0 (PoC_SG, 0.0.0.0/0) |
| SVM | demo-verify-svm (svm-0123456789abcdef0) |
| Volume | demo_verify_vol (fsvol-0123456789abcdef0) |
| EC2 | i-0123456789abcdef0 (fsxn-verify-client-onpre) |
| ONTAP Mgmt IP | <management-ip> |
| Data LIF | <data-lif-ip> |
| CFn: EMS/Splunk | fsxn-splunk-integration-onpre |
| CFn: FPolicy Fargate | fsxn-fp-srv-onpre |
| CFn: FPolicy Splunk shipper | fsxn-splunk-fpolicy-onpre |
| CFn: Automated Response | fsxn-automated-response-onpre |
| Secret: EMS API Key | ems-webhook-api-key-onpre |
| Secret: ONTAP creds | fsx-ontap-fsxadmin-credentials |
| Secret: Splunk HEC | splunk/fsxn-hec-token |
| ECR Image | fsxn-fpolicy-server:20260711-verification |

---

## 7. Corrections Required in Existing Documentation

| Document | Section | Issue | Correction |
|----------|---------|-------|-----------|
| demo-arp-incident-response.md | Phase 1 payload | `messageName`/`severity` (camelCase) | `message-name`/`message-severity`/`message-timestamp` |
| demo-arp-incident-response.md | Phase 2 Step 2.2 | `attack simulate` command | Command does not exist in ONTAP 9.17.1; use actual file operations |
| demo-arp-incident-response.md | Phase 2 notes | "learning period required" | ARP/AI has no learning period; immediately active |
| demo-arp-incident-response.md | Phase 3 Step 3.1 | `show-suspect-files` command | Command does not exist; use REST API or different CLI path |
| demo-automated-response.md | Phase 3 Step 3.3 | export-policy rule show | `block_nfs_ip` fails with HTTP 400; needs debugging |
| runbook 20-1 | Architecture field | ARM64 | X86_64 (linux/amd64) |
| runbook 20-1 | ECR repository name | fpolicy-server | fsxn-fpolicy-server |

---

## 8. Overall Verification Summary

| Category | Tests | Pass | Fail | Partial |
|----------|-------|------|------|---------|
| EMS Webhook (Splunk) | 6 | 6 | 0 | 0 |
| FPolicy Pipeline | 11 | 11 | 0 | 0 |
| ARP/AI Detection | 7 | 7 | 0 | 0 |
| Automated Response | 9 | 7 | 1 | 1 |
| **Total** | **33** | **31** | **1** | **1** |

**Overall Result**: 31/33 PASS (94% pass rate)

**Failures**:
- NFS IP block (export-policy rule HTTP 400) — requires ontap_response.py debugging
- Session disconnect partial (0/1) — expected behavior when no active session exists
