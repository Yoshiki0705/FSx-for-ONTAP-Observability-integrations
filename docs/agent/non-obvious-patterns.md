# Non-Obvious Patterns

FSx for ONTAP S3 Access Point network constraints, AD-joined SVM data-operation
requirements, unsupported S3 features, audit log formats, and the credential
caching and bilingual-sync patterns.

> Extracted from AGENTS.md so it is not loaded into every agent turn.
> AGENTS.md keeps a one-line index entry pointing here, and
> .kiro/steering/ carries a conditional loader that pulls this in when
> the work touches these areas. Tracked in git on purpose: .kiro/ is not
> published, so the body must live here to stay visible on GitHub.

### ⚠️ CRITICAL: FSx for ONTAP S3 Access Points — Network Constraints

**VPC-internal Lambda with only a Gateway Endpoint timed out accessing Internet-origin FSx for ONTAP S3 Access Points in our environment.**

This is the #1 source of deployment failures. The observed behavior is that Internet-origin S3 APs require an internet-routed path (NAT Gateway or VPC-external Lambda) when accessed from within a VPC.

| Lambda Placement | S3 AP Access | ONTAP REST API Access | Recommendation |
|-----------------|-------------|----------------------|----------------|
| **VPC 外 (no VPC config)** | ✅ Works | ❌ Requires VPC | Simplest for S3 AP only |
| **VPC 内 + S3 Gateway EP only** | ⚠️ TIMEOUT (Internet-origin AP) | ✅ Works | Use NAT or VPC-origin AP |
| **VPC 内 + NAT Gateway** | ✅ Works | ✅ Works | Production recommended |
| **VPC 内 + VPC-origin AP + Gateway EP** | ✅ Expected per AWS docs | ✅ Works | Requires VPC-origin AP creation |

**Observed behavior**: In our environment (Internet-origin S3 AP), VPC Lambda with only a Gateway Endpoint timed out. AWS [documents](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/configuring-network-access-for-s3-access-points.html) that VPC-origin access points work with Gateway Endpoints for traffic originating within the bound VPC. The network origin cannot be changed after creation.

**Design pattern for this project**:
- Lambda functions that ONLY read from S3 AP → Deploy **outside VPC** (simplest, lowest cost)
- Lambda functions that need BOTH S3 AP + ONTAP REST API → Deploy **in VPC with NAT Gateway**
- Lambda functions that ONLY call ONTAP REST API → Deploy **in VPC** (no NAT needed if using Interface VPC Endpoints for FSx)

### S3 Access Points for FSx for ONTAP — ARN and IAM

FSx for ONTAP S3 Access Points provide dual-protocol (NFS/SMB + S3) access to the same data without copying.

**Correct ARN format**:
```
arn:aws:s3:{region}:{account-id}:accesspoint/{access-point-name}
```

**IAM policy resource format**:
```yaml
# For GetObject/PutObject on objects:
Resource: arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap/object/*

# For ListBucket on the access point itself:
Resource: arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap
```

**boto3 usage** — Use the AP ARN as the `Bucket` parameter:
```python
s3_client.get_object(
    Bucket="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
    Key="audit/svm-prod-01/2026/01/15/audit.json"
)
```

**S3 AP Resource Policy (same-account)**: For same-account access, the IAM identity policy alone is sufficient — an AP resource policy is NOT required. FSx for ONTAP S3 APs work without `put_access_point_policy` when the caller is in the same AWS account as the AP. Add an explicit AP resource policy only for cross-account access or when using condition keys (e.g., `aws:PrincipalAccount`).

### ⚠️ CRITICAL: AD-Joined SVM + S3 Access Point — AD DC Must Be Reachable

**On an AD-joined SVM (CIFS enabled), ALL S3 AP data operations (ListObjectsV2, GetObject, PutObject) require AD DC connectivity.** If AD DCs are unreachable, data operations return `AccessDenied` even though IAM/policy layers are correct. `HeadBucket` (metadata-only) succeeds — this is a false positive that makes diagnosis confusing.

**Root cause**: ONTAP performs unix→win reverse name-mapping lookup on every data operation through S3 AP. This requires LDAP/Kerberos connectivity to AD DCs.

**Symptoms**:
- `HeadBucket` → 200 OK ✅
- `ListObjectsV2` → `AccessDenied` ❌
- IAM policy, AP resource policy, Security Group, VPC Endpoint all correct
- Error message says "Access Denied" with no additional detail

**Pre-flight check (in CreateFlexClone Lambda or any S3 AP workflow)**:
```python
# Check if SVM has CIFS (AD-joined)
cifs_data = _request("GET", f"/protocols/cifs/services?svm.name={svm_name}&fields=ad_domain.fqdn")
if cifs_data.get("records"):
    # CIFS enabled — verify DC reachability
    dc_check = _request("GET", f"/protocols/cifs/domains?svm.name={svm_name}&fields=discovered_servers")
    # If discovered_servers is explicitly empty → AD DC unreachable → fail fast
```

**Resolution**: Ensure AD DCs (listed in SVM DNS config) are running and network-reachable from the SVM's ENIs. If AD was deleted/recreated, update SVM DNS IPs to new DC addresses.

**Impact on Step Functions workflows**: The `restore-verification.yaml` template includes this check in `CreateFlexClone` Lambda, failing immediately with `AD CONNECTIVITY FAILURE` instead of waiting 30+ min for FSx discovery + AP creation only to hit AccessDenied at scan time.

### FSx for ONTAP S3 AP — Unsupported S3 Features

The following S3 features are NOT supported on FSx for ONTAP S3 Access Points:

| Feature | Status | Workaround |
|---------|--------|-----------|
| S3 Event Notifications / EventBridge | ❌ Not supported | Use EventBridge Scheduler (polling + checkpointing) |
| GetBucketNotificationConfiguration | ❌ Not supported | N/A — this is why we use a separate S3 bucket for audit logs |
| Object Lifecycle policies | ❌ Not supported | Implement custom cleanup Lambda |
| Object Versioning | ❌ Not supported | Use DynamoDB for version tracking |
| Presigned URLs | ❌ Not supported | Copy to standard S3 + presign |
| SSE-KMS (custom keys) | ❌ SSE-FSX only | Use FSx volume-level KMS encryption |
| PutObject > 5GB | ❌ 5GB limit | Multipart upload within 5GB |

**Key implication for this project**: We use a **standard S3 bucket** as the audit log destination (which supports EventBridge notifications), NOT the FSx for ONTAP S3 Access Point directly. The S3 AP is used for Lambda to read the logs from the bucket.

Reference: [AWS Docs — S3 AP API Support](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/access-points-for-fsxn-object-api-support.html) | [AWS Blog — S3 Access Points for FSx](https://aws.amazon.com/blogs/storage/bridge-legacy-and-modern-applications-with-amazon-s3-access-points-for-amazon-fsx/)

### ⚠️ S3 Access Points on AD-Joined SVMs — AD DC Reachability Required

**On AD-joined SVMs (CIFS enabled), ALL S3 AP data operations require active connectivity to the AD domain controllers.** If AD DCs are unreachable, ListObjectsV2 / GetObject / PutObject return `AccessDenied`, even though HeadBucket succeeds.

This is because ONTAP's multiprotocol identity pipeline performs a `unix→win` reverse lookup for every file system operation on CIFS-enabled SVMs, regardless of the volume's security style or the AP's file system identity type.

**Dual-layer authorization flow (AD-joined SVM)**:
```
S3 API Request → IAM evaluation (Pass) → s3_unix name-mapping → UNIX UID resolution
  → unix→win reverse lookup (requires AD DC) → File system authorization
```

| Symptom | Root Cause |
|---------|-----------|
| HeadBucket: ✅ / ListObjectsV2: ❌ AccessDenied | AD DCs unreachable (does NOT mean IAM or AP policy issue) |
| AP Lifecycle: AVAILABLE but data ops fail | AP creation only validates identity existence, not runtime AD connectivity |
| Works on non-AD SVM, fails on AD-joined SVM (same FS) | CIFS service triggers multiprotocol identity resolution |

**Pre-flight check** (add to Lambda/Step Functions before S3 AP data operations):
```python
# Verify AD DC reachability from ONTAP SVM
response = ontap_client.get("/api/protocols/cifs/services", params={"svm.name": svm_name, "fields": "enabled,ad_domain"})
if response["records"] and response["records"][0]["enabled"]:
    # SVM has CIFS enabled — AD DCs must be reachable for S3 AP data ops
    logger.info("SVM is AD-joined. Verifying AD connectivity is handled by ONTAP.")
```

**FSx auto-manages `s3_unix` name-mapping**: When an S3 AP is created, FSx automatically creates a `direction: s3_unix` name-mapping entry (e.g., `amazon-fsx-XXXXXX → root`). No manual name-mapping configuration is needed for S3 AP operation.

**Recovery when AD was deleted/recreated**:
1. Force-delete CIFS service: `DELETE /api/protocols/cifs/services/{svm-uuid}` with body `{"force": true, "ad_domain": {...}}`
2. Remove stale records: `POST /api/private/cli/vserver/cifs/users-and-groups/remove-stale-records` with body `{"vserver": "<svm-name>"}`
3. Re-create CIFS service: `POST /api/protocols/cifs/services` with new AD details
4. Use a NEW NetBIOS name (previous names leave computer accounts in AD that cannot be reused)

### Audit log formats

FSx for ONTAP outputs audit logs in EVTX (Windows Event Log binary) or XML format depending on SVM audit configuration (`vserver audit create -format {evtx|xml}`). The `shared/lambda-layers/log-parser/` handles both. EVTX files start with magic bytes `ElfFile\x00`. XML logs contain `<Event>` elements with system and event data.

> **ONTAP CLI note**: ONTAP 9.11+ deprecates the `vserver` prefix on FPolicy commands (e.g., `vserver fpolicy` → `fpolicy`). Both forms work for backward compatibility. This project uses the deprecated form for compatibility with older ONTAP versions on FSx.

Reference: [AWS Docs — File access auditing](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html)

### Vendor API key caching in Lambda

API keys are fetched from Secrets Manager once per Lambda execution context (cold start) and cached in a module-level variable. This avoids per-invocation Secrets Manager calls. The `_api_key_cache` pattern is intentional — do not refactor into per-request fetching.

### Bilingual documentation sync

Japanese (`docs/ja/`) is the primary language. English (`docs/en/`) must mirror the same heading structure and content. When modifying docs, always update both languages.

#### What "code examples are identical" covers

| Fence | Treatment | Why |
|-------|-----------|-----|
| ```bash ```yaml ```json ```python ```sql ```hcl ... | **Byte-identical across languages**, English is the source | These are commands a reader executes. If the Japanese guide's comment says something the English one doesn't, the two guides describe different runs, and only one of them was verified. |
| Untagged, ```mermaid, ```text | **Localised on purpose** | ASCII architecture diagrams, flow sketches and captured output. Their labels are prose — a Japanese reader is better served by `AI エージェント層` than by an English label. Forcing English here would degrade the primary language for no benefit. |

Enforced by `python3 shared/scripts/sync-code-blocks.py --check` and
`shared/python/tests/test_code_block_sync.py`. To fix drift, run the script
without `--check`; it rewrites the Japanese side from the English one.

The script refuses to act when the two files disagree on fence count or on a
block's language tag, because block indices would no longer describe the same
content and it would copy the wrong text. Those cases are reported for a human.

Run `bash shared/scripts/check-bilingual-sync.sh` to verify heading-structure sync. This is also checked in CI.

### AgentCore MCP Gateway — Integration Knowledge (verified 2026-07)

If exposing observability tool operations (automated response, log search) as MCP tools via AgentCore Gateway:

| Pitfall | Root Cause | Solution |
|---------|-----------|----------|
| AgentCore Gateway assumed us-east-1 only | Workshop examples use us-east-1 for simplicity | **ap-northeast-1 で利用可能**。Gateway + Lambda を同一リージョンに配置すること |
| Lambda event format: `event.toolName` で取得 | 誤った前提 | 正しくは `context.client_context.custom['bedrockAgentCoreToolName']`。event はフラットなパラメータ辞書。ツール名は `targetName___toolName` 形式 |
| `create-gateway-target` で Lambda not found | Gateway と Lambda のリージョン不一致 | Gateway と Lambda は**同一リージョン**に配置必須。クロスリージョン呼び出しは不可 |
| Quick Desktop MCP Remote 追加が永続化されない | Quick Desktop の間欠的バグ | **Import 方式**（JSON ファイルからの読み込み）を使う |

