# ONTAP REST API Quick Reference for FSx for ONTAP

[日本語](../ja/ontap-rest-api-reference.md) | **English** (this page)

## Overview

This project uses the ONTAP REST API for:
- FPolicy configuration (engine, event, policy creation)
- Automated response actions (user blocking, IP blocking, snapshot)
- ARP (Autonomous Ransomware Protection) management

This reference documents practical patterns, common pitfalls, and verified behaviors based on real-world deployment with ONTAP 9.17.1P7D1 on FSx for ONTAP.

---

## Authentication

```bash
# Set credentials (retrieve from Secrets Manager — never hardcode)
export ONTAP_USER="fsxadmin"
export ONTAP_PASS=$(aws secretsmanager get-secret-value \
  --secret-id <secret-arn> --query 'SecretString' --output text | jq -r .password)

# Basic Auth (all endpoints)
curl -sk -u "${ONTAP_USER}:${ONTAP_PASS}" https://<management-ip>/api/cluster
```

**Credentials in Secrets Manager** (recommended):
```json
{
  "username": "fsxadmin",
  "password": "<password>"
}
```

Retrieve the management IP:
```bash
aws fsx describe-file-systems --file-system-ids <fs-id> \
  --query 'FileSystems[0].OntapConfiguration.Endpoints.Management.IpAddresses[0]' \
  --output text
```

> **Security note**: Always use `verify=False` / `-k` for self-signed certs in test environments. For production, retrieve the CA cert via `security certificate show -type root-ca -vserver <svm>` and provide it to your HTTP client.

---

## Common Pitfalls (Verified)

### 1. `svm.uuid` Duplicate Error (Code 262188)

**Symptom**: `Field "svm.uuid" was specified twice`

**Cause**: When the URL path already contains the SVM UUID (e.g., `/api/protocols/fpolicy/{svm-uuid}/engines`), including `"svm": {"uuid": "..."}` in the request body causes a conflict.

**Solution**: Remove the `svm` field from the body when the URL path already specifies the SVM:

```python
# WRONG — causes 262188
url = f"https://{mgmt_ip}/api/protocols/fpolicy/{svm_uuid}/engines"
body = {"svm": {"uuid": svm_uuid}, "name": "my_engine", ...}

# CORRECT
url = f"https://{mgmt_ip}/api/protocols/fpolicy/{svm_uuid}/engines"
body = {"name": "my_engine", "port": 9898, "primary_servers": ["198.51.100.74"], ...}
```

### 2. `allow_privileged_access` Cannot Be Set (Code 262196)

**Symptom**: `Field "allow_privileged_access" cannot be set in this operation`

**Cause**: This field is read-only in the FPolicy policy creation endpoint on FSx for ONTAP.

**Solution**: Omit `allow_privileged_access` from the request body:

```python
# WRONG
body = {"name": "my_policy", "allow_privileged_access": False, ...}

# CORRECT
body = {"name": "my_policy", "engine": {"name": "my_engine"}, "events": [...], ...}
```

### 3. FPolicy Scope — Use Inline, Not Separate Endpoint

**Symptom**: `POST /api/protocols/fpolicy/{svm-uuid}/policies/{policy}/scope` returns 404

**Cause**: In ONTAP 9.17.1, scope is set inline during policy creation, not as a separate sub-resource.

**Solution**: Include `scope` in the policy creation body:

```python
body = {
    "name": "my_policy",
    "engine": {"name": "my_engine"},
    "events": [{"name": "my_event"}],
    "mandatory": False,
    "scope": {
        "include_volumes": ["target_volume"]
    }
}
requests.post(f"https://{mgmt_ip}/api/protocols/fpolicy/{svm_uuid}/policies", json=body)
```

### 4. Async Jobs — Always Check Final State

**Symptom**: Operation appears to succeed (HTTP 202) but actually fails.

**Cause**: Many ONTAP REST API operations return HTTP 202 with a job UUID. The actual result is only available after polling the job.

**Solution**: Always poll the job endpoint:

```python
response = requests.patch(url, json=body)
if response.status_code == 202:
    job_uuid = response.json()["job"]["uuid"]
    # Poll until complete
    while True:
        job = requests.get(f"https://{mgmt_ip}/api/cluster/jobs/{job_uuid}").json()
        if job["state"] in ("success", "failure"):
            break
        time.sleep(2)
    if job["state"] == "failure":
        raise RuntimeError(f"Job failed: {job.get('message')}")
```

> **Cost note**: This pattern avoids the trap documented in the project steering: "HTTP ステータスだけを見て「成功」と判定するコードは、このジョブが実際には state: failure で終わっていることに気づかない"

---

## ARP/AI (Autonomous Ransomware Protection) — Key Behaviors

### Enable ARP (REST API)

```bash
curl -sk -u "${ONTAP_USER}:${ONTAP_PASS}" -X PATCH \
  -H "Content-Type: application/json" \
  -d '{"anti_ransomware":{"state":"enabled"}}' \
  "https://<mgmt-ip>/api/storage/volumes/<volume-uuid>"
```

This returns HTTP 202 (async job). Poll the job to confirm success.

### ARP/AI: No Learning Period in ONTAP 9.16.1+

In ONTAP 9.16.1 and later with ARP/AI, protection is **immediately active** after enablement. No learning/dry-run period is required. The `dry_run_start_time` field may appear in the API response but does not indicate a waiting period.

### `attack simulate` Command — Not Available

The CLI command `security anti-ransomware volume attack simulate` does **NOT exist** in ONTAP 9.17.1. To trigger ARP detection for testing:

1. Create normal files on the volume
2. Encrypt them with a password (e.g., `zip -e -P <password> file.ext file`)
3. Delete the originals
4. The encrypted files should have a new, previously-unseen extension

ARP/AI detects this pattern through:
- High-entropy data writes
- File deletion following creation
- Never-seen-before file extensions (threshold: 5+ distinct new extensions within 48h)

### Available ARP Subcommands (ONTAP 9.17.1)

```
security anti-ransomware volume attack clear-suspect   # Clear suspect records
security anti-ransomware volume attack generate-report # Generate attack report
```

`show-suspect-files` is also **not available** as a CLI command.

### ARP EMS Events

| Event Name | Severity | Trigger |
|-----------|----------|---------|
| `arw.volume.state` | notice | ARP state change (enabled/disabled) |
| `callhome.arw.activity.seen` | alert | Attack activity detected |
| `arw.snapshot.created` | notice | ARP snapshot created |
| `arw.analytics.probability` | alert | Attack probability changed |
| `arw.new.file.extn.seen` | notice | New file extension observed |

---

## FPolicy Configuration (Complete Example)

### Step 1: Create External Engine

```bash
curl -sk -u "${ONTAP_USER}:${ONTAP_PASS}" -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fpolicy_engine",
    "port": 9898,
    "primary_servers": ["<fargate-task-ip>"],
    "type": "synchronous",
    "format": "xml",
    "ssl_option": "no_auth"
  }' \
  "https://<mgmt-ip>/api/protocols/fpolicy/<svm-uuid>/engines"
```

### Step 2: Create Event

```bash
curl -sk -u "${ONTAP_USER}:${ONTAP_PASS}" -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fpolicy_event",
    "file_operations": {
      "create": true,
      "write": true,
      "rename": true,
      "delete": true
    },
    "protocol": "cifs",
    "volume_monitoring": true
  }' \
  "https://<mgmt-ip>/api/protocols/fpolicy/<svm-uuid>/events"
```

### Step 3: Create Policy with Inline Scope

```bash
curl -sk -u "${ONTAP_USER}:${ONTAP_PASS}" -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fpolicy_policy",
    "engine": {"name": "fpolicy_engine"},
    "events": [{"name": "fpolicy_event"}],
    "mandatory": false,
    "scope": {
      "include_volumes": ["target_volume"]
    }
  }' \
  "https://<mgmt-ip>/api/protocols/fpolicy/<svm-uuid>/policies"
```

### Step 4: Enable Policy

```bash
curl -sk -u "${ONTAP_USER}:${ONTAP_PASS}" -X PATCH \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "priority": 1}' \
  "https://<mgmt-ip>/api/protocols/fpolicy/<svm-uuid>/policies/fpolicy_policy"
```

---

## Automated Response — SMB User Block Mechanism

The automated response Lambda blocks SMB users via ONTAP name-mapping:

```bash
# Create deny mapping (blocks user at authentication time)
curl -sk -u "${ONTAP_USER}:${ONTAP_PASS}" -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "direction": "win_unix",
    "index": 1,
    "pattern": "DOMAIN\\\\username",
    "replacement": ""
  }' \
  "https://<mgmt-ip>/api/name-services/name-mappings/<svm-uuid>"
```

### Behavioral Note: Block Timing

The name-mapping block alone does not terminate existing sessions — it is evaluated at authentication time, not per-I/O.

However, the `contain_smb_threat` composite action achieves **effective immediate cutoff** by combining two mechanisms:

1. **name-mapping deny** — blocks all future authentication attempts
2. **session disconnect** — forcefully terminates existing sessions:
   ```
   DELETE /api/protocols/cifs/sessions/{svm-uuid}/{identifier}/{connection-id}
   ```

When the existing session is terminated, the SMB client automatically re-authenticates — at which point the name-mapping deny takes effect. The net result: user is cut off within seconds of execution, not at the next natural session expiry.

If the session disconnect returns HTTP 404 (session already gone), this is expected — the name-mapping deny still prevents future reconnection.

---

## NFS Immediate Blocking — Network Layer (NACL)

ONTAP export-policy deny rules take effect on the next server-side I/O check, but Linux NFS clients cache access decisions for up to 60 seconds (`actimeo` default). For immediate blocking, use VPC NACL deny rules in addition to the export-policy rule.

| Approach | Layer | Timing | API |
|----------|-------|--------|-----|
| Export-policy rule | ONTAP | Next I/O (up to 60s client cache) | `POST /protocols/nfs/export-policies/{id}/rules` |
| **NACL deny rule** | AWS VPC | **Immediate** (packet level) | `ec2:CreateNetworkAclEntry` |

The `contain_nfs_threat` action applies both layers automatically when `FSX_SUBNET_ID` is configured.

> **NFSv4 lease note**: NFSv4 uses lease-based state management internally, but ONTAP does not expose a REST API endpoint for forced revocation of a specific client's lease. The NACL approach bypasses this limitation entirely by operating at the network layer, effective for both NFSv3 and NFSv4.

> **VPC Endpoint requirement**: The Lambda needs EC2 API access to manage NACL rules. Ensure your VPC has NAT Gateway or an EC2 Interface VPC Endpoint.

---

## EMS Webhook Payload Format

When ONTAP sends EMS events to a webhook destination, the payload uses **hyphenated** field names:

```json
{
  "message-name": "arw.volume.state",
  "message-severity": "alert",
  "message-timestamp": "2026-07-12T00:42:06+00:00",
  "parameters": {
    "vserver-name": "svm-prod",
    "volume-name": "vol_data",
    "state": "attack-detected"
  }
}
```

> **Caution**: This is NOT camelCase (`messageName`) or snake_case (`message_name`). The fields use hyphens: `message-name`, `message-severity`, `message-timestamp`.

---

## Related Documents

- [Prerequisites and Deployment Guide](prerequisites.md)
- [Automated Response Guide](automated-response-guide.md)
- [ARP Incident Response Guide](arp-incident-response-guide.md)
- [EMS Detection Capabilities](ems-detection-capabilities.md)
- [FPolicy Setup (Grafana example)](../../integrations/grafana/docs/en/fpolicy-setup.md)
