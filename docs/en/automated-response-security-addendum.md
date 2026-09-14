# Automated Response — Security & Incident Response Addendum

🌐 [日本語](../ja/automated-response-security-addendum.md) | **English** (this page)

## Purpose

This addendum addresses advanced security considerations for the automated incident response module, organized by topic area. It complements the main [Automated Response Guide](automated-response-guide.md) with depth required for enterprise security reviews, compliance assessments, and incident response planning.

---

## 1. ONTAP Protocol & Security Style Considerations

### Volume Security Style Impact on SMB Blocking

> ### ⚠️ Retracted: this section claimed the block works on all security styles
>
> An earlier version of this page stated that *"the name-mapping blocking mechanism works
> across ALL volume security styles"*, and gave `ntfs` a ✅ on the reasoning that ONTAP
> performs SID-to-UNIX translation for internal tracking even on NTFS volumes.
>
> **That claim had no measurement behind it and the mechanism it cited is wrong.** It is
> retracted rather than quietly edited, because a response runbook is where an
> over-claimed containment costs the most: the failure is silent and it points the wrong
> way. Someone verifies the technique on a `unix` volume, sees it work, applies the same
> procedure to an `ntfs` volume, and concludes they have blocked access when they have not.
>
> The corrected table is below. See [What was and was not
> measured](#what-was-and-was-not-measured) for the provenance.

An NTFS security style volume evaluates the Windows ACL directly for the permission check,
so it never consults the result of the win→unix mapping. `unix` and `mixed` do consult it,
which is why the same operation has an effect there.

| Volume Security Style | SMB Block Effective? | Why |
|----------------------|---------------------|-----|
| `unix` | ✅ Yes | The permission check resolves a UNIX identity through name-mapping, so a deny mapping fails the resolution |
| `mixed` | ✅ Yes | Same path as `unix` when the file's effective style is UNIX |
| `ntfs` | ❌ **No** | The Windows ACL is used as-is. The win→unix mapping result is not consulted, so a deny mapping does not deny access |

Sources for the NTFS behaviour: NetApp KB [How does name-mapping work when CIFS clients
access NTFS security style
resources](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/How_does_name-mapping_work_when_CIFS_clients_access_NTFS_security_style_resources),
plus two independent repository records reaching the same conclusion.

> **Note on `mixed` security style**: `mixed` is not recommended for new deployments.
> NetApp best practice is to explicitly choose `ntfs` (Windows-only workloads) or `unix`
> (Linux/NFS-primary workloads). It is listed above because the blocking mechanism's
> behaviour differs by style and omitting it would leave the reader guessing.

### If your volumes are NTFS security style

`block_smb_user` will report success — the name-mapping is created and the API returns
`201 Created`. **That response says the mapping exists, not that access is denied.** On an
NTFS style volume it will not be denied.

Use one of these instead, or in addition:

| Action | Mechanism | Notes |
|--------|-----------|-------|
| Disable the AD account | Active Directory, outside ONTAP | Stops new authentication for the user everywhere, not only on this SVM. Sourced: a disabled or locked account produces `Kerberos Error: Clients credentials have been revoked` and blocks NTFS access **regardless of name-mapping or security style** — [NetApp KB](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/Denied_access_to_NTFS_volume_because_AD_Account_is_locked_or_disabled_or_expired), already cited in the [deployment guide](deployment-guide.md) |
| Terminate CIFS sessions | `disconnect_smb_sessions` in `ontap_response.py` | Ends current access; the user can reconnect unless authentication is also stopped |
| Modify the NTFS ACL | Windows ACL on the share or directory | Acts on the path the NTFS style volume actually consults |
| Restrict at the network layer | Security Group / NACL | Blocks the client address rather than the identity |

### What was and was not measured

The distinction matters here more than the conclusion, so it is stated per item rather
than summarised.

| Claim | Status |
|-------|--------|
| The name-mapping API call succeeds and the entry is created | **Measured.** ONTAP 9.17.1P7D1, 2026-07-08: `block_smb_user (direct API) — PASS — 201 Created — index:99, CORP\testuser99`. See [E2E verification results](../screenshots/automated-response/e2e-verification-results.md) |
| A deny mapping with `replacement: "nobody"` survives on an AD-joined SVM, where `" "` is auto-deleted within 30-60 s | **Measured.** ONTAP 9.17.1P7D1, July 2026. See [Automated Response Guide](automated-response-guide.md) |
| SMB access is actually denied on a `unix` style volume | **Not measured here.** Consistent with the documented mechanism and with external records |
| SMB access is actually denied on an `ntfs` style volume | **Measured to be false elsewhere; never measured here.** The retracted claim asserted the opposite |

**No test in this repository has ever attempted SMB access after a block.** The E2E record
tests the API call, and `201 Created` was read as containment. The security style of the
volumes in that environment was not recorded, so even the `unix` case has no local
evidence.

> **Trap when measuring this yourself**: test with a non-administrator account. Members of
> the administrators group bypass the permission check, which produces a false "the block
> does not work" on `unix`. Conversely, a denial observed on `ntfs` may have come from the
> ACL rather than from the mapping — so a control that is expected to *succeed* has to run
> in the same session, or the result records the procedure rather than the behaviour.

### NFS Authentication Method Impact on IP Blocking

| NFS Auth Method | IP-Based Block Effective? | Notes |
|----------------|--------------------------|-------|
| AUTH_SYS (most common) | ✅ Yes | Client identified by IP; export-policy rule blocks by IP |
| Kerberos (krb5/krb5i/krb5p) | ⚠️ Partial | Client authenticated by principal; same principal from different IP bypasses IP block |
| AUTH_NONE | ✅ Yes | Client identified by IP |

For Kerberos NFS environments, complement IP blocking with:
- Network-level controls (Security Group, NACL)
- Kerberos principal revocation (AD account disable)
- Export-policy rule blocking the Kerberos-authenticated client's IP range

### Multi-Protocol Threat Containment

For volumes accessed via BOTH NFS and SMB (multiprotocol), execute both containment actions:

```json
// Message 1: Block SMB
{"action": "contain_smb_threat", "svm_name": "svm-prod", "domain": "CORP", "username": "jdoe", "volume_name": "vol_data"}

// Message 2: Block NFS (same user's workstation IP)
{"action": "contain_nfs_threat", "svm_name": "svm-prod", "client_ip": "203.0.113.99", "volume_name": "vol_data"}
```

> **Simpler alternative**: The `contain_multiprotocol_threat` composite action combines both blocks (plus snapshot and session disconnect) in a single message, instead of publishing the two messages shown above. See [Automated Response Guide — Composite Actions](automated-response-guide.md#supported-actions).

---

## 2. Evidence Preservation & Forensics

### Snapshot Tamper Protection

Standard snapshots can be deleted by volume administrators. For forensic-grade evidence:

| Protection Level | Mechanism | ONTAP Version | Recommendation |
|-----------------|-----------|---------------|----------------|
| Basic | Normal snapshot (current implementation) | Any | Acceptable for operational response |
| Enhanced | Snapshot locking (`snapshot lock create`) | 9.12.1+ | Recommended for regulated environments |
| Maximum | SnapLock Compliance volume | 9.10.1+ | Required for legal-hold scenarios |

> **FSx for ONTAP**: Snapshot locking is supported. After creating the protective snapshot, optionally lock it:
> ```
> POST /api/storage/volumes/{uuid}/snapshots/{snapshot_uuid}
> Body: {"retention_period": "P30D"}  // ISO 8601 duration — lock for 30 days
> ```

### Chain of Custody Requirements (DFIR)

For forensically-valid evidence, the response action should capture:

| Element | Current Status | Enhancement Path |
|---------|---------------|-----------------|
| Trigger source (who requested) | ✅ In CloudWatch Logs (SNS message body) | — |
| Exact timestamp (UTC) | ✅ In CloudWatch Logs + snapshot metadata | — |
| Pre-action state | ⚠️ Not captured | Future: query name-mapping/export-policy before modifying |
| Post-action state | ✅ API response confirms creation | — |
| Lambda execution identity | ✅ In CloudWatch Logs (requestId) | — |
| Trigger message hash | ❌ Not implemented | Future: SHA-256 of SNS message in log entry |

### Snapshot Retention Policy

For sustained incidents generating multiple snapshots:
- Retain `incident_response_*` snapshots for 30 days (configurable)
- After 30 days: migrate to AWS Backup (long-term) or delete
- Monitor snapshot space consumption via CloudWatch metric `StorageCapacity`
- Alert if incident snapshots consume > 10% of volume capacity

---

## 3. FPolicy: Preventive vs Reactive Control

This project implements **reactive** containment (detect → block after the fact). FPolicy offers an additional **preventive** mode:

| Aspect | FPolicy Passthrough (this project) | FPolicy Mandatory | Automated Response (this module) |
|--------|-----------------------------------|-------------------|----------------------------------|
| Timing | After operation completes | Before operation completes | After detection + analysis |
| Latency | 0ms (logging only) | +1-5ms per file operation | ~65s (detection + response) |
| Can prevent damage | ❌ No | ✅ Yes (deny operation) | ❌ No (blocks future operations) |
| False positive impact | None (logging) | High (blocks legitimate files) | Medium (blocks entire user) |
| Complexity | Low | High (external server decision logic) | Medium |

> **Defense-in-depth**: Use FPolicy mandatory mode for known-bad patterns (block `.encrypted` file creation), and automated response for behavioral anomalies (block user after mass deletion pattern detected).

---

## 4. Insider Threat & System Integrity

### Threat Model: Compromised Administrator

If an attacker gains access to:

| Compromised Component | Impact | Mitigation |
|----------------------|--------|------------|
| ONTAP fsxadmin credentials | Can unblock self, delete snapshots | Use ONTAP RBAC: create a `response_blocker` role with write-only access to name-mapping (no delete permission) |
| AWS Secrets Manager | Same as above | IAM policy: restrict Secrets Manager access to the Lambda execution role only |
| SNS trigger topic | Can send unblock messages | SNS access policy: restrict `unblock_*` actions to specific IAM principals |
| Lambda code / CloudFormation | Can modify response logic | CloudTrail alerts on stack/function modifications |
| CloudWatch Logs | Can delete audit trail | Export logs to S3 with Object Lock (WORM) |

### Authorization Separation Pattern

For high-security environments, separate block and unblock credentials:

```
Block Lambda: Uses ONTAP user "response_blocker" 
  - Permissions: name-mapping create, export-policy rule create, snapshot create
  - Cannot: name-mapping delete, export-policy rule delete

Unblock Lambda: Uses ONTAP user "response_admin" (separate secret)
  - Permissions: name-mapping delete, export-policy rule delete
  - Requires: additional IAM gate (separate SNS topic with approval workflow)
```

### SNS Topic Access Policy (Restrict Unblock)

```json
{
  "Statement": [{
    "Effect": "Deny",
    "Principal": "*",
    "Action": "SNS:Publish",
    "Resource": "<trigger-topic-arn>",
    "Condition": {
      "StringLike": {"aws:PrincipalArn": "arn:aws:iam::*:role/non-security-*"},
      "ForAnyValue:StringEquals": {"sns:MessageBody": ["unblock_smb_user", "unblock_nfs_ip"]}
    }
  }]
}
```

> Note: SNS condition on message body requires custom validation. For strict control, use a separate SNS topic for unblock actions.

---

## 5. DR & Replication Considerations

### SnapMirror Replication Behavior

| Item | Replicated to DR? | Implication |
|------|-------------------|-------------|
| Name-mapping entries | ❌ No | Blocked user regains access after failover |
| Export-policy rules | ✅ Yes | IP block persists after failover |
| Incident response snapshots | ✅ Yes (if in SnapMirror scope) | Evidence preserved at DR site |
| ARP state | ❌ No (resets on DR) | ARP needs learning period at DR site |

**DR-Aware Blocking Procedure**:
1. Block on primary SVM (normal operation)
2. ALSO block on DR SVM (publish second message to DR endpoint)
3. After failover, verify blocks are active on the now-primary DR SVM

For automated dual-site blocking, maintain two CloudFormation stacks (primary + DR) with separate ONTAP management IPs.

---

## 6. Container & Dynamic Environments

### Kubernetes / ECS Considerations

IP-based NFS blocking is less effective in containerized environments because:
- Pod IPs are ephemeral (new pod = new IP = bypasses block)
- Multiple pods share the same node IP for NFS traffic (blocking the node blocks ALL pods)

**Recommendations for containerized workloads**:

| Approach | Effectiveness | Complexity |
|----------|--------------|-----------|
| Block node IP range (subnet) | ✅ High (blocks all pods on node) | Low |
| PersistentVolume access mode restriction | ✅ High (unmount at K8s level) | Medium |
| NetworkPolicy (K8s) | ✅ High (block pod→FSx traffic) | Medium |
| Service Account → ONTAP user mapping | ✅ Precise | High (requires custom mapping) |

---

## 7. Governance & Compliance

### FISC Guidelines (日本金融業界)

| FISC Requirement | How This System Addresses It |
|-----------------|------------------------------|
| Pre-approval of automated response policies | Document policies, get security officer sign-off before enabling |
| Annual review of response rules | Review `PROTECTED_ACCOUNTS`, detection thresholds, TTL settings annually |
| Quarterly testing of response mechanisms | Use `health_check` action + safe block/unblock cycle on test SVM |
| Documented escalation path | `pagerduty-escalation-guide.md` + notification topic → on-call |

### GDPR Article 22 Consideration

Automated blocking affects an individual's ability to perform their job. Under GDPR Article 22:
- Automated decisions with significant effects require human review
- **Recommendation**: For EU employee populations, implement a mandatory human review within 1 hour of automated blocking
- The TTL auto-unblock (default: 60 min) serves as a safety net
- Document the lawful basis for automated blocking (legitimate interest: preventing data breach)

### SOC2 CC7.3 — Post-Action Review

Every automated response action should be:
1. ✅ Triggered by legitimate detection (audit trail in CloudWatch Logs)
2. ⚠️ Proportionate to threat (configurable — use severity field)
3. ⚠️ Reviewed by qualified personnel post-facto (requires process, not just tooling)
4. ✅ Reversed when no longer needed (TTL auto-unblock or manual unblock)

**Recommendation**: Create a daily/weekly review cadence where security team reviews all `contain_*` actions from the notification topic.

---

## 8. SOAR Platform Integration

### Supported Operations for SOAR Playbooks

| Operation | SNS Action | Returns | Use in SOAR |
|-----------|-----------|---------|-------------|
| Block user | `block_smb_user` | Confirmation | Response step |
| Block IP | `block_nfs_ip` | Confirmation | Response step |
| Full containment | `contain_smb_threat` | Multi-step result | Composite response |
| Check status | `list_active_blocks` | Current blocks | Enrichment step |
| Health check | `health_check` | Connectivity status | Health monitoring |
| Unblock | `unblock_smb_user` | Confirmation | Recovery step |

### Idempotency

- `block_smb_user`: NOT idempotent (creates duplicate entries if called twice). SOAR should check `list_active_blocks` first.
- `block_nfs_ip`: NOT idempotent (creates duplicate rules). Check first.
- `create_snapshot`: Idempotent via cooldown (skips if recent snapshot exists).
- `health_check`: Idempotent (read-only).
- `list_active_blocks`: Idempotent (read-only).

---

## 9. Cloud-Agnostic Usage

The `OntapResponseClient` Python class is cloud-agnostic. It requires only:
- Network access to ONTAP management IP (TCP 443)
- ONTAP admin credentials
- Python 3.9+ with `urllib3`

It can be invoked from:
- AWS Lambda (this project's deployment pattern)
- Azure Functions
- GCP Cloud Functions
- On-premises Python scripts
- SOAR platform custom integrations (XSOAR, Splunk SOAR)
- CI/CD pipelines (GitLab CI, GitHub Actions)
- Any environment with Python + network access

The SNS/Lambda/CloudFormation wrapping is the AWS-native deployment pattern — the core containment logic is portable.

---

## 10. Rate Limits & Scalability

### ONTAP Management Plane Limits

| Metric | Approximate Limit | Impact |
|--------|-------------------|--------|
| REST API requests/sec | ~100 req/s (FSx for ONTAP) | Mass-blocking (20+ users) may need queuing |
| Concurrent CIFS sessions | Thousands | Session disconnect at scale is sequential |
| Name-mapping entries per SVM | 256 | Hard limit on simultaneous SMB blocks |
| Export-policy rules per policy | 1024 | Sufficient for most scenarios |

**For mass-blocking scenarios**: Implement SQS buffering between detection and the response Lambda to rate-limit ONTAP API calls.

---

## Related Documents

- [Automated Response Guide](automated-response-guide.md) — Main deployment and usage guide
- [ARP Incident Response Guide](arp-incident-response-guide.md) — ARP-specific procedures
- [EMS Detection Capabilities](ems-detection-capabilities.md) — Detection source reference
- [Demo Runbook](demo-automated-response.md) — Step-by-step verification
- [Compliance Evidence Pack](compliance-evidence-pack.md) — Audit evidence templates
- [PagerDuty Escalation Guide](pagerduty-escalation-guide.md) — Notification chains
