# Automated Incident Response Guide — User/IP Blocking via ONTAP REST API

🌐 [日本語](../ja/automated-response-guide.md) | **English** (this page)

## Executive Summary

This guide describes how to implement automated storage-layer access blocking for Amazon FSx for NetApp ONTAP using AWS-native detection services combined with ONTAP REST API response actions. The approach delivers the same containment-phase capabilities — user blocking, IP blocking, and protective snapshots — available in dedicated storage security products, while keeping detection and orchestration within the AWS ecosystem and third-party observability platforms.

> **Scope note**: This module automates the *containment* phase (NIST SP 800-61) at the storage layer only — blocking access and preserving evidence. Host isolation, malware removal, credential rotation, and blocking lateral movement to other systems (eradication/recovery) are out of scope and still require human judgment or another IR tool.

**Key capabilities:**
- Block compromised SMB users via ONTAP name-mapping (access denied across all volumes)
- Block attacker IPs from NFS access via export-policy rules
- Create protective snapshots for evidence preservation (with storm-prevention cooldown)
- Disconnect active CIFS sessions to immediately cut off a compromised user
- Composite containment actions that execute all steps in sequence

**Response timing (verified on ONTAP 9.17.1P7D1)**:
- Lambda execution (contain_smb_threat): **1.8 seconds** (snapshot + block + session disconnect)
- End-to-end (detection → routing → response): **under 2 minutes** (typical), **under 3 minutes** (worst-case with Lambda cold start + VPC ENI attach)
- SMB block effective: immediately on next authentication attempt (existing sessions continue until token expiry or session disconnect)

**Detection sources (any combination):**
- CloudWatch Log Alarm (admin audit log anomalies)
- EMS Webhook (ARP ransomware detection, quota events)
- FPolicy analytics (mass deletion, abnormal extension changes)
- Third-party SIEM monitors (Datadog, Splunk, Elastic, etc.)

---

## Architecture

```
+-------------------------------------------------------------------+
| Detection Layer (AWS-native + SaaS Observability)                 |
+-------------------------------------------------------------------+
|                                                                   |
|  CloudWatch Log Alarm --+                                         |
|  EMS Webhook -> Monitor-+                                         |
|  FPolicy -> SIEM -------+-- SNS Trigger Topic                     |
|  Manual invocation -----+                                         |
|                                                                   |
+-------------------------------------------------------------------+
| Response Layer (Lambda + ONTAP REST API)                          |
+-------------------------------------------------------------------+
|                                                                   |
|  SNS -> Lambda (VPC) -> ONTAP REST API                            |
|           |                                                       |
|           +-> Block SMB user (name-mapping)                       |
|           +-> Block NFS IP (export-policy rule)                   |
|           +-> Create protective snapshot                          |
|           +-> Disconnect CIFS sessions                            |
|           +-> SNS notification (action result)                    |
|                                                                   |
|  DLQ -> Alarm -> Notification (failed actions)                    |
|                                                                   |
+-------------------------------------------------------------------+
```

```mermaid
graph TD
    subgraph Detection["Detection Layer (any source)"]
        CWA[CloudWatch Log Alarm]
        DD[Datadog Monitor]
        ES[Elastic SIEM]
        CLI[Manual CLI]
    end

    subgraph Trigger["SNS Decoupling"]
        SNS[SNS Trigger Topic]
    end

    subgraph Response["Response Layer (Lambda + ONTAP)"]
        LMB[Lambda VPC]
        LMB --> BLK_SMB[Block SMB User<br/>name-mapping deny]
        LMB --> BLK_NFS[Block NFS IP<br/>export-policy + NACL]
        LMB --> SNAP[Create Snapshot<br/>evidence preservation]
        LMB --> DISC[Disconnect Sessions<br/>force re-auth]
        LMB --> NOTIFY[SNS Notification<br/>security team]
    end

    subgraph Verification["Recovery Verification (Step Functions)"]
        SF[Step Functions Workflow]
        SF --> CLONE[FlexClone]
        SF --> WAIT[WaitForFsxSync<br/>10 min]
        SF --> DISC2[FSx Discovery<br/>120s poll]
        SF --> AP[S3 Access Point<br/>Internet-origin]
        SF --> SCAN[Scan for Indicators<br/>extension check]
        SF --> VERDICT[Record Verdict<br/>DynamoDB]
    end

    CWA --> SNS
    DD --> SNS
    ES --> SNS
    CLI --> SNS
    SNS --> LMB
    SNAP -.->|"verify before restore"| SF
```

*Three-layer architecture: Detection (any SIEM/monitor) → Response (Lambda, < 2 min E2E) → Verification (Step Functions, 25-50 min). SNS decouples detection from response — no vendor lock-in.*

---

## How Blocking Works

### SMB User Blocking

The mechanism uses ONTAP name-mapping to deny access:

| Step | What Happens | ONTAP Equivalent |
|------|-------------|-----------------|
| 1 | Lambda receives trigger with domain + username | — |
| 2 | Creates a name-mapping: `DOMAIN\user` → `" "` (empty) | `vserver name-mapping create -direction win-unix -pattern "DOMAIN\\user" -replacement " "` |
| 3 | User's next file operation is denied | SID-to-UNIX translation fails → access denied |
| 4 | All SVM volumes affected | Name-mapping applies SVM-wide |

**Scope**: The block applies to the entire SVM. All volumes, shares, and exports within the SVM deny access to the blocked user.

> **Operational safety note (position 1 insertion)**: The deny mapping is inserted at position 1 by default, meaning it's evaluated *before* any existing name-mappings. If your SVM has existing name-mappings for service accounts or automation workflows, the new entry may shadow them. Always test with `health_check` + a non-production user first. To immediately undo an accidental block: `./automated-response-cli.sh unblock-smb --domain <DOMAIN> --user <username>`. For multi-tenant SVMs, verify that blocking one tenant's compromised user doesn't affect shared service accounts evaluated at higher positions.

**Reversal**: Delete the name-mapping entry to restore access.

### NFS IP Blocking

The mechanism adds a deny rule to the export policy:

| Step | What Happens | ONTAP Equivalent |
|------|-------------|-----------------|
| 1 | Lambda receives trigger with client IP | — |
| 2 | Creates export-policy rule: `clientmatch=<marker>,<ip>`, `ro_rule=never`, `rw_rule=never` | `export-policy rule create -clientmatch "fsxn_auto_response,<ip>" -rorule never -rwrule never` |
| 3 | Rule inserted at position 1 (highest priority) | Evaluated before allow rules |
| 4 | Client IP blocked from all NFS access on that policy | — |

**Scope**: Affects all volumes using the specified export policy.

**Marker**: Rules include `fsxn_auto_response` in the client match field, making them easy to identify and clean up.

**Reversal**: Delete export-policy rules containing the response marker.

### CIFS Session Disconnect

Forcefully terminates active sessions so the blocked user cannot continue operations until their access is revoked:

- Lists active CIFS sessions matching user or IP
- Deletes each session via REST API
- Used in combination with user blocking for immediate effect

### NFS Immediate Blocking: Multi-Layer Options

Unlike SMB (where session disconnect forces re-authentication), NFS has no equivalent session-terminate API. The ONTAP export-policy deny rule takes effect on the next I/O, but Linux NFS clients cache access decisions for up to 60 seconds (`actimeo` default). Three options are available, and can be combined:

| Option | Layer | Effect Timing | Scope | Configuration |
|--------|-------|---------------|-------|---------------|
| **NACL deny rule** (recommended) | Network (VPC) | **Immediate** (packet-level) | All traffic from IP to subnet | `FsxSubnetId` parameter + `block_nfs_ip_network` action |
| Export-policy deny rule | ONTAP | Next I/O (up to 60s cache) | NFS volumes using that policy | `block_nfs_ip` action (existing) |
| Security Group modification | Network (ENI) | Immediate | Allow-only (cannot deny specific IP without removing broader allow) | Manual or custom automation |

**Recommended approach**: Use `contain_nfs_threat` with `FsxSubnetId` configured. This executes both layers automatically:
1. ONTAP export-policy deny rule (persistent, survives NACL removal)
2. NACL deny rule (immediate, network-level, no client cache bypass)

```bash
# Deploy with network-layer blocking enabled:
aws cloudformation deploy \
  --template-file shared/templates/automated-response.yaml \
  --stack-name fsxn-automated-response \
  --parameter-overrides \
    ... \
    FsxSubnetId=<subnet-where-fsx-enis-reside> \
  --capabilities CAPABILITY_NAMED_IAM
```

**Standalone network block** (without ONTAP-layer):
```json
{"action": "block_nfs_ip_network", "svm_name": "svm-prod", "client_ip": "203.0.113.99", "reason": "Mass deletion"}
```

**Unblock**:
```json
{"action": "unblock_nfs_ip_network", "svm_name": "svm-prod", "client_ip": "203.0.113.99"}
```

> **Scope note**: NACL deny rules block ALL traffic from the specified IP to the entire subnet (not just NFS port 2049). Set `block_all_ports: false` in the SNS message to restrict blocking to NFS port only. The ONTAP export-policy rule is NFS-specific and more granular.

> **Cleanup note**: NACL rules created by this module use rule numbers 50-99. Rules outside this range are never modified. Use `unblock_nfs_ip_network` or the TTL auto-cleanup to remove rules.

> **VPC Endpoint requirement**: The Lambda calls the EC2 API (`CreateNetworkAclEntry`) to manage NACL rules. If your VPC has a NAT Gateway, this works automatically. If not, add an EC2 Interface VPC Endpoint (`com.amazonaws.<region>.ec2`). Without EC2 API access, the NACL block will timeout (non-fatal — the ONTAP export-policy block still succeeds as a fallback).

> **NFSv4 lease note**: NFSv4 has internal lease management, but ONTAP does not expose a REST API endpoint for forced lease revocation of a specific client. The NACL approach works for both NFSv3 and NFSv4 regardless of protocol version.

> **Blast radius note**: By default, the NACL rule blocks ALL protocols from the IP (not just NFS port 2049). If the blocked IP is also used for monitoring or management, set `"block_all_ports": false` in the SNS message to restrict blocking to NFS port only.

---

## Comparison: This Approach vs Dedicated Storage Security Products

Dedicated storage security products (such as DII Storage Workload Security) provide ML-based behavioral analytics with integrated containment. This project achieves equivalent containment using AWS-native and SaaS detection with ONTAP REST API response:

| Capability | Dedicated Product | This Approach |
|-----------|------------------|---------------|
| SMB user blocking | ✅ Automated | ✅ Automated (same ONTAP API) |
| NFS IP blocking | ✅ Automated | ✅ Automated (same ONTAP API) |
| Protective snapshot | ✅ Automated | ✅ Automated (with cooldown) |
| Session disconnect | ✅ Automated | ✅ Automated |
| User behavior ML | ✅ Built-in (per-user baselines) | Via SIEM (Datadog ML Anomaly, Elastic ML Jobs, etc.) |
| AD user tracking | ✅ Built-in collector | Via FPolicy logs (user field) + AD lookup |
| Detection scope | Storage only | Storage + network + application (broader context) |
| Data residency | SaaS (external) | AWS Region (data stays in your VPC) |
| Integration with SIEM | Limited export | Native (detection originates from SIEM) |
| Cost model | Per-node license | Pay-per-use (Lambda invocations) |

> **Operational note**: The underlying ONTAP mechanisms are identical — both approaches use the same REST API endpoints. The difference is where detection intelligence lives. Dedicated products embed ML in their SaaS; this approach delegates detection to your chosen observability platform.

> **Security-architecture note**: AWS-native detection provides broader attack context (VPC Flow Logs, CloudTrail, GuardDuty findings) that storage-only solutions cannot correlate. For organizations with existing SIEM investments, this is a natural extension of their security operations workflow.

---

## Prerequisites

### ONTAP Version

- **FSx for ONTAP**: All currently supported versions (ONTAP 9.11.1+)
- **Name-mapping REST API**: Available from ONTAP 9.6+
- **Export-policy REST API**: Available from ONTAP 9.6+
- **CIFS sessions REST API**: Available from ONTAP 9.8+
- **ARP (Autonomous Ransomware Protection)**: ONTAP 9.10.1+ (learning mode), 9.13.1+ (ARP/AI)

### ONTAP Permissions

The Lambda execution role connects to ONTAP with `fsxadmin` credentials stored in Secrets Manager. The following ONTAP operations are used:

```
# Required permissions (fsxadmin has these by default)
- GET /api/svm/svms
- GET /api/storage/volumes
- POST /api/storage/volumes/{uuid}/snapshots
- GET/POST/DELETE /api/name-services/name-mappings
- GET/POST/DELETE /api/protocols/nfs/export-policies/{id}/rules
- GET/DELETE /api/protocols/cifs/sessions
```

### Network Access

The Lambda function must be deployed in a VPC with:
- Private subnet with route to ONTAP management endpoint
- Security Group allowing HTTPS (TCP 443) outbound to ONTAP management IP
- No NAT Gateway required (ONTAP endpoint is private)

#### Network Prerequisites Checklist

| Requirement | How to Verify |
|------------|---------------|
| Lambda subnet has route to ONTAP mgmt IP | `aws ec2 describe-route-tables` — verify route to ONTAP CIDR |
| Security Group allows TCP 443 egress | SG outbound rule: `TCP 443 → <ONTAP-mgmt-IP>/32` |
| ONTAP management LIF is UP | `network interface show -role cluster-mgmt` (SSH to ONTAP) |
| DNS resolution (if using hostname) | Typically not needed (use IP directly for FSx for ONTAP) |
| Cross-VPC (if applicable) | VPC peering or Transit Gateway route + SG reference |
| Same-VPC different subnet | Ensure no NACL blocking TCP 443 between subnets |

> **TLS Note**: The default module configuration disables certificate verification (`CERT_NONE`). For production, retrieve the FSx for ONTAP CA certificate and pass it via `CA_CERT_PATH` environment variable. Get the cert via: `security certificate show -type root-ca -vserver <svm>` (ONTAP CLI).

### Secrets Manager

Store ONTAP credentials as JSON:
```json
{
  "username": "fsxadmin",
  "password": "<your-password>"
}
```

---

## Deployment

### Pre-Deployment Checklist

Before deploying, verify the following (most common causes of deployment failures):

| # | Check | How to Verify | If Missing |
|---|-------|---------------|-----------|
| 1 | ONTAP admin password in Secrets Manager | `aws secretsmanager get-secret-value --secret-id <name>` | Create: `{"username":"fsxadmin","password":"<pwd>"}` |
| 2 | Password matches actual FSx for ONTAP | SSH to ONTAP should connect | Reset: `aws fsx update-file-system --file-system-id <id> --ontap-configuration FsxAdminPassword=<new>` then update Secrets Manager |
| 3 | Lambda subnet can reach ONTAP mgmt IP | Same VPC/subnet, or route exists | Verify route table |
| 4 | SG allows Lambda → ONTAP TCP 443 | ONTAP ENI SG has ingress for Lambda SG | Add rule (see below) |
| 5 | VPC has DNS support enabled | `aws ec2 describe-vpc-attribute --vpc-id <id> --attribute enableDnsSupport` | Must be `true` for VPC Endpoints |

### Finding Your Parameter Values

> **Automated pre-flight check**: Run `bash shared/scripts/preflight-check.sh --vpc-id <vpc-id> --profile automated-response` to automatically detect existing VPC Endpoints, verify security group egress, and generate recommended parameter values. See the [Deployment Guide](deployment-guide.md) for the full pre-flight workflow.

```bash
# 1. FSx for ONTAP Management IP
aws fsx describe-file-systems --file-system-ids <fs-id> \
  --query 'FileSystems[0].OntapConfiguration.Endpoints.Management.IpAddresses[0]' \
  --output text

# 2. VPC and Subnet (same as FSx)
aws fsx describe-file-systems --file-system-ids <fs-id> \
  --query 'FileSystems[0].{VpcId:VpcId,SubnetIds:SubnetIds}' --output json

# 3. SVM name
aws fsx describe-storage-virtual-machines \
  --filters Name=file-system-id,Values=<fs-id> \
  --query 'StorageVirtualMachines[].Name' --output text

# 4. Secrets Manager ARN (for ONTAP creds)
aws secretsmanager list-secrets --filters Key=name,Values=fsx \
  --query 'SecretList[].ARN' --output text

# 5. Security Groups in the VPC
aws ec2 describe-security-groups --filters "Name=vpc-id,Values=<vpc-id>" \
  --query 'SecurityGroups[].{Id:GroupId,Name:GroupName}' --output table
```

### Security Group Configuration (Required)

The Lambda function must be able to reach the ONTAP management endpoint on TCP 443. Add an ingress rule to the ONTAP ENI's Security Group:

```bash
# Find the Security Group attached to FSx for ONTAP's ENI
FSX_ENI_SG=$(aws ec2 describe-network-interfaces \
  --filters "Name=vpc-id,Values=<vpc-id>" "Name=requester-id,Values=*fsx*" \
  --query 'NetworkInterfaces[0].Groups[0].GroupId' --output text 2>/dev/null)

# If the above returns empty, find manually:
# aws fsx describe-file-systems → NetworkInterfaceIds → describe-network-interfaces

# Add rule: allow Lambda SG → ONTAP SG on TCP 443
aws ec2 authorize-security-group-ingress \
  --group-id $FSX_ENI_SG \
  --ip-permissions 'IpProtocol=tcp,FromPort=443,ToPort=443,UserIdGroupPairs=[{GroupId=<your-lambda-sg>,Description="Automated response Lambda to ONTAP mgmt"}]'
```

### Deploy the CloudFormation Stack

```bash
aws cloudformation deploy \
  --template-file shared/templates/automated-response.yaml \
  --stack-name fsxn-automated-response \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=arn:aws:secretsmanager:<region>:<account>:secret:<name> \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-id> \
    SecurityGroupId=<sg-id> \
    DefaultSvmName=<svm-name> \
    SharedPythonLayerArn=<layer-arn> \
    NotificationEmail=<your-email> \
    CreateVpcEndpoints=true \
  --capabilities CAPABILITY_NAMED_IAM
```

> **`SharedPythonLayerArn` parameter (new)**: Provide the ARN of the `fsxn-shared-python` Lambda Layer containing `ontap_response.py`. Without this, the handler fails with `ModuleNotFoundError`. Build and publish the layer first using `bash shared/python/build-layer.sh`.

> **`CreateVpcEndpoints` parameter**: The stack creates Interface VPC Endpoints for Secrets Manager and SNS by default. Lambda in VPC **cannot** reach AWS APIs without these endpoints (or a NAT Gateway). Set to `false` if your VPC already has them. If your VPC has a NAT Gateway, set to `false` to avoid the additional ~$14/month cost.

### Post-Deployment: Attach Lambda Layer

The ontap_response module is delivered as a Lambda Layer (not embedded in the inline code):

```bash
# Build the layer zip
bash shared/python/build-layer.sh

# Publish as Lambda Layer
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name fsxn-shared-python \
  --zip-file fileb://shared/python/dist/fsxn-shared-python-layer.zip \
  --compatible-runtimes python3.12 \
  --description "FSx for ONTAP shared modules (ontap_response, auth_cache)" \
  --query 'LayerVersionArn' --output text)

# Attach to the response Lambda
aws lambda update-function-configuration \
  --function-name fsxn-automated-response-handler \
  --layers $LAYER_ARN
```

> **AD-aware SMB blocking (Layer v3+)**: The `ontap_response.py` module automatically detects whether the target SVM is joined to Active Directory (by checking for CIFS service presence). On AD-joined SVMs, `block_smb_user` uses `replacement: "nobody"` instead of `replacement: " "` (space). This is because ONTAP 9.17.1's background validation process auto-deletes name-mapping entries with unresolvable replacement values within 30-60 seconds. The `nobody` user (UID 65535) exists in the local UNIX user table and persists indefinitely. Verified on ONTAP 9.17.1P7D1, July 2026.

### Verify Deployment

```bash
# Send health_check — should complete in <5 seconds with no errors
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-automated-response \
  --query 'Stacks[0].Outputs[?OutputKey==`TriggerTopicArn`].OutputValue' \
  --output text)

aws sns publish --topic-arn $TOPIC_ARN \
  --message '{"action":"health_check","svm_name":"<svm-name>"}'

# Wait and check Lambda log
sleep 15
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-automated-response-handler \
  --start-time $(( $(date +%s) - 30 ))000 \
  --query 'events[-3:].message' --output text
```

**Success indicators**: Duration < 5s, no `timeout`, no `ERROR` in logs.

**If you see `timeout`**: Check VPC Endpoints (Secrets Manager + SNS) and Security Group rules.

### Connect Detection Sources

After deployment, subscribe the trigger SNS topic to your detection sources:

**CloudWatch Log Alarm → SNS:**
```bash
# The Log Alarm's action points to the trigger topic
# (configured during Log Alarm creation)
```

**SIEM / Observability Platform → SNS:**

| Platform | How to Connect |
|----------|---------------|
| Datadog | `@sns-<topic-name>` in Monitor notification message |
| Splunk | Alert action → AWS SNS notification (Splunk Add-on for AWS) |
| Elastic | Kibana Alert → SNS connector action |
| Grafana | Alert rule → Contact point → AWS SNS |
| New Relic | Workflow → Destination → AWS SNS |
| PagerDuty | Event Orchestration → Custom Action → SNS publish via Lambda |
| Any platform | HTTP webhook → Lambda → SNS publish |

**Manual test invocation:**
```bash
aws sns publish \
  --topic-arn <TriggerTopicArn from stack outputs> \
  --message '{
    "action": "contain_smb_threat",
    "svm_name": "svm-prod-01",
    "domain": "CORP",
    "username": "test-user",
    "volume_name": "vol_data",
    "reason": "Manual test"
  }'
```

---

## Supported Actions

### Individual Actions

| Action | Required Fields | Description |
|--------|----------------|-------------|
| `block_smb_user` | svm_name, domain, username | Block SMB user via name-mapping |
| `unblock_smb_user` | svm_name, domain, username | Remove SMB user block |
| `block_nfs_ip` | svm_name, client_ip | Block IP via export-policy rule |
| `unblock_nfs_ip` | svm_name, client_ip | Remove IP block |
| `create_snapshot` | svm_name, volume_name | Create protective snapshot |

### Composite Actions (Multi-Step)

| Action | Steps | Use Case |
|--------|-------|----------|
| `contain_smb_threat` | Snapshot → Block user → Disconnect sessions | Compromised AD user detected |
| `contain_nfs_threat` | Snapshot → Block IP | Suspicious NFS client activity |
| `contain_multiprotocol_threat` | Snapshot → Block SMB user → Block NFS IP → Disconnect sessions | Volume accessed via both SMB and NFS — a single-protocol block is insufficient because the attacker can switch protocols |

---

## SNS Message Format

### Basic Format (minimum required)

```json
{
  "action": "contain_smb_threat",
  "svm_name": "svm-prod-01",
  "domain": "CORP",
  "username": "jdoe",
  "volume_name": "vol_data",
  "reason": "ARP ransomware detection - arw.volume.state alert"
}
```

### Extended Format (recommended for compliance/audit)

```json
{
  "action": "contain_smb_threat",
  "svm_name": "svm-prod-01",
  "domain": "CORP",
  "username": "jdoe",
  "volume_name": "vol_data",
  "policy_name": "default",
  "reason": "ARP ransomware detection - arw.volume.state alert",
  "incident_id": "INC-2026-0708-001",
  "detection_source": "datadog-monitor-fsxn-arp",
  "severity": "critical",
  "trigger_id": "msg-abc123-def456"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| action | Yes | One of the supported action names |
| svm_name | Yes (or set DEFAULT_SVM_NAME) | Target SVM |
| domain | SMB actions | Windows domain name |
| username | SMB actions | Windows username |
| client_ip | NFS actions | Client IP address |
| volume_name | Snapshot / composite | Volume to protect |
| policy_name | NFS actions | Export policy (default: "default") |
| reason | Yes (blocking actions) | Human-readable reason (logged) |
| incident_id | No (recommended) | External incident tracking ID (e.g., PagerDuty, ServiceNow) |
| detection_source | No (recommended) | Which system detected the threat (for forensic correlation) |
| severity | No (recommended) | `critical` / `high` / `medium` / `low` |
| trigger_id | No | SNS MessageId or upstream correlation ID |

> **Compliance note (HIPAA/FISC/SOC2)**: For regulated environments, always include `incident_id`, `detection_source`, and `severity`. These fields are passed through to CloudWatch Logs and notification messages, forming the audit trail.

---

## Integration Examples

### Severity-Based Routing (Critical — Prevents False-Positive Lockouts)

ARP emits events at two severity levels. **Only auto-block on `alert` (high confidence)**:

| ARP Severity | Confidence | Recommended Action | Rationale |
|-------------|-----------|-------------------|-----------|
| `alert` | High (file tampering + encryption confirmed) | ✅ Auto-contain (`contain_smb_threat`) | High confidence, damage in progress |
| `warning` | Moderate (suspicious but unconfirmed) | ⚠️ Notify only (do NOT auto-block) | May be false positive during learning period |

> **ARP Learning Period**: ARP requires 30 days to establish a behavioral baseline. During this period, `warning` events are common and often benign (bulk file conversions, backup software). Auto-blocking on `warning` during learning will disrupt legitimate users.

**Detection rule configuration example (any SIEM)**:
```
# Auto-contain: ONLY severity=alert
Filter: source=fsxn-ems AND event_name=arw.volume.state AND severity=alert
→ Action: SNS publish (contain_smb_threat)

# Notify only: severity=warning
Filter: source=fsxn-ems AND event_name=arw.volume.state AND severity=warning
→ Action: Notify (Slack/PagerDuty/email — investigation only, do NOT auto-block)
```

### Example 1: CloudWatch Log Alarm → Auto-Block

When the admin audit log shows 10+ failed login attempts in 5 minutes:

```
CloudWatch Log Alarm (query: failed logins > 10)
  → SNS (Trigger Topic)
  → Lambda
  → ONTAP: block_smb_user + create_snapshot
  → SNS notification to security team
```

### Example 2: SIEM ARP Monitor → Full Containment

When your observability platform receives an ARP EMS event and the detection rule fires:

```
ONTAP ARP → EMS Webhook → Observability Platform (any vendor)
  → Detection rule fires (ARP event matched)
  → Workflow/Action → SNS publish (contain_smb_threat)
  → Lambda → ONTAP: snapshot + block + disconnect
  → Notification → PagerDuty / Slack / email
```

### Example 3: FPolicy Mass Deletion → IP Block

When FPolicy analytics detect >50 deletes/5min from a single IP:

```
FPolicy → SQS → Lambda → SIEM (any vendor)
  → SIEM rule fires (mass deletion threshold)
  → SNS publish (contain_nfs_threat)
  → Lambda → ONTAP: snapshot + block IP
```

---

## Operational Procedures

### Viewing Blocked Users/IPs

```bash
# SSH to FSx for ONTAP management endpoint
ssh fsxadmin@<management-ip>

# List blocked SMB users (name-mappings with empty replacement)
vserver name-mapping show -direction win-unix -replacement " "

# List blocked NFS IPs (rules with our marker)
export-policy rule show -clientmatch *fsxn_auto_response*
```

### Manual Unblock

Via SNS message:
```bash
aws sns publish \
  --topic-arn <TriggerTopicArn> \
  --message '{"action":"unblock_smb_user","svm_name":"svm-prod-01","domain":"CORP","username":"jdoe"}'
```

Via ONTAP CLI (emergency):
```bash
# Unblock SMB user
vserver name-mapping delete -direction win-unix -position <position>

# Unblock NFS IP
export-policy rule delete -vserver <svm> -policyname <policy> -ruleindex <index>
```

### Monitoring

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| DLQ depth | CloudWatch (auto-created alarm) | > 0 messages |

> **DLQ alarm = urgent (P1)**: A message in the DLQ means a containment action *failed to execute*. Unlike typical DLQs where retry-later is acceptable, here **the attacker may still have active access** to the storage layer. Treat DLQ depth > 0 as a P1 incident requiring immediate human investigation and manual containment. Recovery steps: [DLQ Replay Runbook](runbooks/dlq-replay.md).

| Lambda errors | CloudWatch Lambda metrics | > 0 in 5 min |
| Response latency | Lambda Duration metric | p95 > 10s |
| Active blocks | Custom metric (optional) | Tracking only |
| Protected-account block attempts | CloudWatch Logs filter on `Cannot block protected account` (HTTP 403 from `_validate_username`) | > 0 — investigate immediately |
| Block rate (any protocol) | Custom metric from Lambda invocation count | Alarm above your expected incident rate — see the "10. Rate Limits & Scalability" section in the [Security Addendum](automated-response-security-addendum.md) |

> **Incident-response note**: A rejected attempt to block a protected account (`fsxadmin`, `administrator`, or an entry in `PROTECTED_ACCOUNTS_EXTRA`) is a signal worth investigating on its own — it can indicate a misconfigured detection rule pointing at the wrong identity, or an attacker attempting to use the response pipeline itself to lock out legitimate admins. Alert on this condition rather than only logging it.
>
> **Reliability note**: An automated response system that blocks too aggressively becomes a self-inflicted denial-of-service vector (e.g., a noisy detection rule triggering `contain_smb_threat` against many users in a short window). Set an alarm on invocation/block rate, not just on failures, so a runaway detection source is caught before it locks out a large population.

---

## Security Considerations

- **Least privilege**: The Lambda role only has access to Secrets Manager (read), SNS (publish), and VPC network access to ONTAP. No broad IAM permissions.
- **Credential rotation**: Use Secrets Manager auto-rotation for ONTAP credentials.
- **Audit trail**: All actions are logged in CloudWatch Logs with correlation IDs.
- **Cooldown protection**: Snapshot creation has a configurable cooldown (default 15 min) to prevent storage exhaustion during sustained attacks.
- **Marker-based cleanup**: All response rules include `fsxn_auto_response` marker for safe identification and bulk removal.
- **Time-limited blocks**: Deploy the companion [`automated-response-ttl.yaml`](../../shared/templates/automated-response-ttl.yaml) stack to auto-remove blocks after a configurable duration via EventBridge Scheduler. Without it, blocks persist until manually removed — decide during deployment planning whether that is acceptable for your environment.

---

## Cost Estimate

### Comparison: This Approach vs DII Storage Workload Security

| Aspect | This approach (AWS-native) | DII Storage Workload Security |
|--------|---------------------------|-------------------------------|
| **Monthly baseline** | ~$0.51 (Lambda + SNS + Secrets Manager) | Capacity-based license (per-node or per-TB) |
| **VPC Endpoints (if needed)** | +~$14/month (2 Interface EPs) | N/A (SaaS data plane) |
| **Scales with** | Invocation count (incident frequency) | Storage capacity monitored |
| **At 10 incidents/month** | ~$0.45 | License cost unchanged |
| **At 10,000 incidents/month** | ~$36 | License cost unchanged |
| **Detection cost** | Existing SIEM (already paid) | Included in license |
| **Setup time** | 30 min (CloudFormation) | Days (agent + collector + AD config) |
| **Data residency** | Your AWS VPC only | Vendor SaaS cloud |

> **Cost note**: The comparison is between *containment-phase* costs only. DII's license also covers ML-based detection (which this approach delegates to your SIEM). If you don't already have a SIEM with anomaly detection, add that cost to this approach's side of the comparison.

| Component | Monthly Cost (typical) | Notes |
|-----------|----------------------|-------|
| Lambda | ~$0.10 | <100 invocations/month (incident response only) |
| SNS | ~$0.01 | Low message volume |
| Secrets Manager | ~$0.40 | 1 secret |
| VPC ENI | $0 (Lambda VPC) | Shared with existing VPC |
| **Total** | **~$0.51/month** | Excludes VPC/NAT costs (shared) |

### Cost at Scale

| Incident Volume | Lambda Cost | CW Logs Cost | SNS Cost | Total |
|----------------|-------------|-------------|----------|-------|
| 10/month | $0.01 | $0.03 | $0.01 | ~$0.45 + Secrets Manager |
| 100/month | $0.05 | $0.30 | $0.01 | ~$0.76 |
| 1,000/month | $0.50 | $3.00 | $0.05 | ~$3.95 |
| 10,000/month | $5.00 | $30.00 | $0.50 | ~$35.90 |

> At 10,000 incidents/month (extreme volume — likely indicates misconfigured detection thresholds), total cost remains under $40. The VPC Lambda cold start cost is negligible because Lambda ENIs are reused across invocations.

### Production Deployment Note

The CloudFormation template embeds Lambda code as `ZipFile` for quickstart deployment. For production GitOps workflows:
1. Package `ontap_response.py` as a Lambda Layer (`shared/python/` directory structure)
2. Use `CodeUri` with S3 bucket instead of `ZipFile`
3. Version the Layer independently from the CloudFormation stack
4. CI/CD pipeline: `pytest` → package Layer → deploy → integration test

---

## Cleanup / Teardown

```bash
# Delete TTL stack first (no dependencies)
aws cloudformation delete-stack --stack-name fsxn-automated-response-ttl

# Delete main stack (VPC Endpoints + Lambda + SNS)
aws cloudformation delete-stack --stack-name fsxn-automated-response

# Remove Lambda Layer (optional)
aws lambda delete-layer-version --layer-name fsxn-shared-python --version-number 2

# Delete test snapshots created during verification
# ssh fsxadmin@<management-ip> "volume snapshot delete -vserver <svm> -volume <vol> -snapshot incident_response_*"
```

---

## Verified Environment

This solution was E2E verified on:

| Item | Value |
|------|-------|
| FSx for ONTAP | Single-AZ, ONTAP 9.17.1 |
| Region | ap-northeast-1 (Tokyo) |
| Lambda Runtime | Python 3.12 |
| Actions tested | health_check, create_snapshot, block_smb_user, block_nfs_ip, unblock, TTL cleanup, cooldown |
| All actions | < 1 second (after VPC Endpoints provisioned) |

---

## Related Documents

- [ARP Incident Response Guide](arp-incident-response-guide.md)
- [EMS Webhook Setup](../../integrations/datadog/docs/en/ems-fpolicy-setup.md)
- [FPolicy Operations](operational-notes-fpolicy.md)
- [Pipeline SLO](pipeline-slo.md)
- [CloudWatch Log Alarm Guide](syslog-vpce-setup-guide.md)

---

## Troubleshooting

### Lambda Execution Failures

| Symptom | Cause | Solution |
|---------|-------|----------|
| Lambda timeout (60s) | ONTAP management endpoint unreachable | Check Security Group rules (TCP 443 outbound), verify ONTAP management LIF is up |
| HTTP 401 from ONTAP | Credentials expired or incorrect | Check Secrets Manager secret value, verify fsxadmin password |
| HTTP 403 from ONTAP | Insufficient permissions | Verify fsxadmin role has required API access (default: full) |
| `SVM not found` error | SVM name mismatch | Verify SVM name matches exactly (case-sensitive). Use `vserver show` to list |
| `Volume not found` error | Volume doesn't exist or wrong SVM | Verify volume name and SVM association |
| DLQ messages accumulating | Repeated failures | Check Lambda CloudWatch Logs for the root cause, then replay from DLQ |

### Block Not Taking Effect

| Symptom | Cause | Solution |
|---------|-------|----------|
| SMB user still has access after block | Existing session active | Use `contain_smb_threat` (includes session disconnect) or wait for session timeout |
| NFS client still has access | NFS attribute cache | Wait 60s (default `actimeo`) or remount with `mount -o actimeo=0` for testing |
| Block exists but ONTAP shows no entry | Wrong SVM | Verify the SVM name in your SNS message matches the target SVM |

### TTL Auto-Unblock Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| Blocks not being removed | EventBridge Scheduler not running | Check scheduler state in EventBridge console |
| All blocks removed too early | TTL Lambda removes ALL response-marker blocks | See "TTL Limitation" note below |
| Cleanup Lambda errors | ONTAP unreachable from TTL Lambda | Same VPC/SG requirements as main Lambda |

> **TTL Limitation**: The current TTL implementation removes ALL blocks with the `fsxn_auto_response` marker on each run, regardless of when they were created. ONTAP name-mapping entries do not carry creation timestamps. For time-accurate TTL enforcement, implement a DynamoDB tracking table that records block creation time and have the TTL Lambda check that table before removal. This is tracked as a future enhancement.

### Common `aws sns publish` Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `AuthorizationError` | IAM user lacks `sns:Publish` permission | Add `sns:Publish` permission for the trigger topic ARN |
| `InvalidParameter` | Malformed JSON in message | Validate JSON with `echo '<msg>' | python3 -m json.tool` |
| `TopicNotFound` | Wrong topic ARN | Verify ARN from CloudFormation stack outputs |

---

## FAQ

**Q: Does this replace DII Storage Workload Security entirely?**
A: It provides the same *containment-phase actions* (block/snapshot/disconnect) at the storage layer. Detection intelligence differs: DII uses built-in per-user ML baselines, while this approach uses your chosen SIEM's analytics capabilities. Neither this module nor DII performs eradication or recovery (host isolation, malware removal, credential rotation) — both are containment-phase tools; the rest of the incident response lifecycle still requires a human or a separate IR tool. For organizations with existing SIEM investments, the combined approach can provide broader detection context (network + application + storage) than storage-only detection.

**Q: What happens if the Lambda cannot reach ONTAP?**
A: The invocation fails, the message goes to the DLQ, and the DLQ alarm fires. Investigate network connectivity (Security Group, route tables, ONTAP management LIF status).

**Q: Can I block a user across multiple SVMs simultaneously?**
A: Send one SNS message per SVM. The composite actions operate on a single SVM per invocation. For multi-SVM blocking, publish multiple messages or use the Step Functions fan-out pattern below:

```
EventBridge / SIEM Alert
  -> Step Functions (fan-out)
       -> Parallel state:
            Branch 1: SNS publish (svm-prod-01, contain_smb_threat)
            Branch 2: SNS publish (svm-prod-02, contain_smb_threat)
            Branch 3: SNS publish (svm-dr-01, contain_smb_threat)
       -> Wait for all branches
       -> Notify: "Blocked user on 3 SVMs"
```

Alternatively, use the `automated-response-multi-svm-cli.sh` wrapper, which fans out to multiple SVMs, reports per-SVM success/failure, and exits non-zero if any SVM failed:

```bash
export RESPONSE_TOPIC_ARN="arn:aws:sns:ap-northeast-1:123456789012:fsxn-automated-response-trigger"

./shared/scripts/automated-response-multi-svm-cli.sh contain-smb \
  --svms "svm-prod-01,svm-prod-02,svm-dr-01" \
  --domain CORP --user jdoe --volume vol_data \
  --reason "ARP detection - multi-SVM block"
```

**Q: How quickly does the block take effect?**
A: SMB name-mapping blocks are effective immediately for new connections. Existing sessions remain active until disconnected (the `contain_smb_threat` action handles this). NFS export-policy rules are effective immediately for new mounts; existing mounts may require cache expiry.

**Q: Is there a risk of blocking legitimate users?**
A: Yes — this is true for any automated response system. Mitigations: (1) set detection thresholds conservatively, (2) use the notification topic to alert operators immediately, (3) implement time-limited blocks with auto-unblock, (4) maintain a runbook for rapid manual reversal.


---

## See Also

- [Deployment Guide](deployment-guide.md) — Full deployment instructions including VPC Endpoint setup, parameter files, and pre-flight validation
- [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) — Post-incident snapshot verification workflow
