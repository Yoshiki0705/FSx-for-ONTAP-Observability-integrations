# Deployment Guide — Integrating with Existing FSx for ONTAP Environments

🌐 [日本語](../ja/deployment-guide.md) | **English** (this page)

## Purpose

This guide helps you deploy the observability and security stacks from this project into an environment where Amazon FSx for NetApp ONTAP already exists. Every template in this project is an **overlay** — none create FSx file systems, SVMs, or volumes. You bring your existing infrastructure; the templates add observability, incident response, and data protection capabilities on top.

## What You Need Before Starting

Gather the following from your existing environment:

| # | Resource | Where to Find | Used By |
|---|----------|---------------|---------|
| 1 | FSx File System ID | FSx Console → File systems → `fs-xxxxxxxxxxxxxxxxx` | `fsxn-audit-config`, `restore-verification` |
| 2 | Management Endpoint IP | FSx Console → File system details → Management DNS/IP | `automated-response`, `restore-verification`, `fpolicy-apigw`, `lakehouse-monitoring` |
| 3 | SVM Name | FSx Console → Storage virtual machines | `automated-response`, `automated-response-ttl` |
| 4 | SVM ID | FSx Console → SVM details → `svm-xxxxxxxxxxxxxxxxx` | `demo-ad-environment` (`FsxSvmId`, optional) |
| 5 | VPC ID | VPC Console | All VPC-mode stacks |
| 6 | Private Subnet IDs | VPC Console → Subnets (same AZ as FSx ENIs) | All VPC-mode stacks |
| 7 | Security Group ID | SG that allows HTTPS (443) to FSx management IP | All VPC-mode stacks |
| 8 | Route Table IDs | VPC Console → Route tables → associated with subnets | `restore-verification`, `content-classification-scanner` |
| 9 | ONTAP Admin Credentials Secret ARN | Secrets Manager → `arn:aws:secretsmanager:...` | `automated-response`, `restore-verification` |
| 10 | S3 Access Point ARN | `aws fsx describe-s3-access-point-attachments` (**not** `describe-data-repository-associations`, which covers a different feature). Also record `NetworkOrigin` — see the Tier 1 note below | `prerequisites`, vendor stacks |

## Stack Catalog

### Tier 1: Audit Log Shipping (Vendor Integrations)

These stacks ship FSx for ONTAP audit logs to observability vendors. They are the simplest to deploy — no VPC configuration required when Lambda runs outside VPC.

| Stack | Template | Key Parameters | VPC Required |
|-------|----------|---------------|--------------|
| Prerequisites | `shared/templates/prerequisites.yaml` | FsxS3AccessPointArn | No |
| S3 Access Point | `shared/templates/s3-access-point.yaml` | BucketName, VpcId (optional) | No |
| Vendor (×10) | `integrations/<vendor>/template.yaml` | FsxS3AccessPointArn, VendorSecretArn | No (default) |
| Datadog: EMS + FPolicy | `integrations/datadog/template-ems-fpolicy.yaml` | DatadogApiKeySecretArn, FPolicySqsQueueArn | No |
| Datadog: Log Archive | `integrations/datadog/template-log-archive.yaml` | DatadogExternalId, RetentionDays | No |
| Datadog: Snapshot Remediation | `integrations/datadog/template-snapshot-remediation.yaml` | OntapManagementIp, Secret ARN, VPC/Subnet/SG | **Yes** |

> **Two things that make a Tier 1 deployment fail:**
>
> 1. **The stack alone is not functional.** CloudFormation cannot inline a
>    multi-hundred-line handler, so every vendor `template.yaml` ships a
>    placeholder that raises `NotImplementedError`. Use the vendor's
>    `scripts/deploy.sh`, which deploys the stack and uploads the real code, or
>    run `aws lambda update-function-code` yourself afterwards. The vendor's
>    `scripts/verify.sh` detects a forgotten upload.
> 2. **Access point network origin must match the Lambda placement.** An
>    Internet-origin access point works with a VPC-external Lambda (the default);
>    a VPC-origin one requires `VpcEnabled=true` plus internet egress for the
>    vendor API. Mismatched, it fails with
>    `AccessDenied ... explicit deny in a resource-based policy`, which reads like
>    an IAM problem. The origin cannot be changed after creation.

### Tier 2: Incident Response

| Stack | Template | Key Parameters | VPC Required |
|-------|----------|---------------|--------------|
| Automated Response | `shared/templates/automated-response.yaml` | OntapMgmtIp, Secret ARN, VPC/Subnet/SG, DefaultSvmName | Yes |
| TTL Auto-Unblock | `shared/templates/automated-response-ttl.yaml` | Same as above + BlockTtlMinutes, CheckIntervalMinutes | Yes |

### Tier 3: Data Protection & Classification

| Stack | Template | Key Parameters | VPC Required |
|-------|----------|---------------|--------------|
| Restore Verification | `shared/templates/restore-verification.yaml` | OntapMgmtIp, FileSystemId, Secret ARN, VPC, Subnet, SG, Route Tables | Yes |
| Content Classification | `shared/templates/content-classification-scanner.yaml` | VpcId (optional), LanguageCode | No (default) |

### Tier 4: Advanced Monitoring

| Stack | Template | Key Parameters | VPC Required |
|-------|----------|---------------|--------------|
| **Performance & Capacity Dashboard** | `shared/templates/fsxn-monitoring-dashboard.yaml` | FileSystemId, FileSystemName, CapacityThresholdPercent, NotificationEmail | No |
| **Qtree Quota Monitor** | `shared/templates/qtree-quota-monitor.yaml` | OntapMgmtIp, SvmName, VPC/Subnet/SG, QuotaThresholdPercent | Yes |
| Syslog → CloudWatch | `shared/templates/syslog-vpce-cloudwatch.yaml` | VpcId, SubnetIds, VpcCidr | Yes |
| FPolicy Server | `shared/templates/fpolicy-server-fargate.yaml` | VpcId, SubnetIds, FsxnSvmSecurityGroupId, ContainerImage, AlarmNotificationTopicArn | Yes |
| CloudWatch Log Alarm | `shared/templates/cloudwatch-log-alarm.yaml` | LogGroupName, TargetPattern | No |
| Lakehouse Monitoring | `shared/templates/lakehouse-monitoring.yaml` | OntapMgmtEndpoint, S3AccessPointArn, VPC/Subnet/SG | Yes |

#### Quick Deploy: Performance Dashboard (No VPC required)

```bash
aws cloudformation deploy \
  --template-file shared/templates/fsxn-monitoring-dashboard.yaml \
  --stack-name fsxn-monitoring \
  --parameter-overrides \
    FileSystemId=fs-0123456789abcdef0 \
    FileSystemName=fsxn-prod \
    CapacityThresholdPercent=80 \
    NotificationEmail=storage-team@example.com
```

#### Quick Deploy: Qtree Quota Monitor (VPC required)

```bash
aws cloudformation deploy \
  --template-file shared/templates/qtree-quota-monitor.yaml \
  --stack-name fsxn-qtree-monitor \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=arn:aws:secretsmanager:<region>:<account>:secret:<name> \
    SvmName=svm-prod-01 \
    VpcId=vpc-0123456789abcdef0 \
    SubnetIds=subnet-aaa,subnet-bbb \
    SecurityGroupId=sg-0123456789abcdef0 \
    QuotaThresholdPercent=85 \
    NotificationEmail=storage-team@example.com \
    CreateSecretsManagerEndpoint=false \
  --capabilities CAPABILITY_NAMED_IAM
```

> **VPC Endpoint note**: Set `CreateSecretsManagerEndpoint=false` if you already deployed Tier 2 stacks (which create the Secrets Manager endpoint). See the VPC Endpoint Conflict Matrix above.

### Tier 5: Operational Add-ons

| Stack | Template | Purpose |
|-------|----------|---------|
| Object Ledger | `shared/templates/object-ledger.yaml` | DynamoDB per-file processing state |
| SQS Buffering | `shared/templates/sqs-buffering.yaml` | SQS buffer + DLQ for high-volume |
| Secrets Rotation | `shared/templates/secrets-rotation-sample.yaml` | Auto-rotate vendor API keys |
| PagerDuty Escalation | `shared/templates/pagerduty-escalation.yaml` | Alert routing to PagerDuty |

---

## VPC Endpoint Conflict Matrix

Multiple stacks can create VPC Endpoints. Understanding the two types of conflicts is critical:

- **Interface Endpoints** (SecretsManager, SNS, STS, Comprehend): A second Interface Endpoint for the same service with `PrivateDnsEnabled=true` in the same VPC fails with `"private-dns-enabled cannot be set because there is already a conflicting DNS domain"`. This is the most common deployment failure.
- **Gateway Endpoints** (S3, DynamoDB): These do not conflict via DNS, but you cannot attach the same Gateway Endpoint service to the same route table twice. If an S3 Gateway EP already exists on your route tables, a second one for the same route tables fails.

Use this matrix to set `CreateXxxEndpoint=false` when an endpoint already exists.

| Service | Type | automated-response | restore-verification | content-classification | syslog-vpce | vpc-endpoints |
|---------|------|:------------------:|:--------------------:|:----------------------:|:-----------:|:-------------:|
| **SecretsManager** | Interface | `CreateVpcEndpoints` | `CreateSecretsManagerEndpoint` | — | — | Always |
| **SNS** | Interface | `CreateVpcEndpoints` | — | `CreateVpcEndpoints` | — | — |
| **STS** | Interface | — | `CreateStsEndpoint` | — | — | — |
| **Comprehend** | Interface | — | — | `CreateVpcEndpoints` | — | — |
| **CW Logs Syslog** | Interface | — | — | — | Always | — |
| **S3** | Gateway | — | `CreateS3GatewayEndpoint` | `CreateVpcEndpoints` | — | Always |
| **DynamoDB** | Gateway | — | — | `CreateVpcEndpoints` | — | — |

### How to Check Existing Endpoints

```bash
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=<your-vpc-id>" \
  --query 'VpcEndpoints[].{Service:ServiceName,Type:VpcEndpointType,State:State}' \
  --output table
```

### Decision Rule

For each stack you deploy:
1. Run the check above
2. For each endpoint the stack would create, check if that service already exists in the table
3. If it exists → set the corresponding `Create*Endpoint=false`
4. If it does not exist → leave as `true` (default)

---

## Verified Deployment Paths

### Path 1: Audit Log Shipping (Simplest)

**Goal**: Ship FSx for ONTAP audit logs to your observability vendor.

```
s3-access-point.yaml → prerequisites.yaml → integrations/<vendor>/template.yaml
```

**Steps**:
1. Enable audit logging on your SVM (ONTAP CLI: `vserver audit create`)
2. Create an S3 bucket for log delivery
3. Deploy `s3-access-point.yaml` (creates the AP for Lambda to read logs)
4. Deploy `prerequisites.yaml` (EventBridge scheduler, checkpoint table)
5. Deploy vendor template (e.g., `integrations/datadog/template.yaml`)

**No VPC configuration required.** Lambda deploys outside VPC by default.

### Path 2: Incident Response

**Goal**: Automated user/IP blocking with TTL-based auto-unblock.

```
automated-response.yaml (CreateVpcEndpoints=true)
  → automated-response-ttl.yaml
```

**Steps**:
1. Store ONTAP admin credentials in Secrets Manager
2. Deploy `automated-response.yaml` with your VPC/Subnet/SG
3. Deploy Lambda Layer (`shared/lambda-layers/`) and update the function
4. Deploy `automated-response-ttl.yaml` with the same `SubnetIds` / `SecurityGroupId` (it takes no `VpcId`)

**Security note**: The Lambda runs inside the VPC to reach the ONTAP management IP directly. VPC Endpoints for Secrets Manager and SNS are created by the first stack.

### Path 3: Recovery Point Verification

**Goal**: Verify snapshots are clean before restoring (ransomware indicator scan).

**Expected execution time**: 25–50 minutes per verification run (dominated by FSx-ONTAP sync delay).

```
automated-response.yaml (first, creates SecretsManager EP)
  → restore-verification.yaml (CreateSecretsManagerEndpoint=false)
```

**Timing design** (why it takes this long):

| Phase | Duration | Reason |
|-------|----------|--------|
| CreateFlexClone | ~5 seconds | ONTAP REST API, instant CoW clone |
| WaitForFsxSync | 10 min (configurable) | Static wait — FSx API needs 12-36 min to discover ONTAP-created volumes ([AWS docs](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/managing-resources-ontap-apps.html)). This avoids wasted Lambda invocations |
| WaitForFsxDiscovery | 0-40 min (polling) | 120s interval polling until FSx finds the clone. After 10min static wait, typically succeeds within a few attempts |
| AttachAccessPoint | 1-3 min | S3 AP creation + AVAILABLE transition |
| ScanForIndicators | seconds-minutes | Depends on object count (ListObjectsV2 pagination) |
| RecordVerdict + Cleanup | ~1 min | DynamoDB write + AP detach + volume delete |

**Prerequisites specific to this path**:
- Target volume must be **UNIX security style** (check: `volume show -fields security-style`)
- Target SVM must have **no ONTAP-native S3 server** enabled (check: `vserver object-store-server show`)
- Route Table IDs for your subnets must be provided

**Steps**:
1. Verify prerequisites (run `preflight-check.sh --profile restore-verification`)
2. Deploy `automated-response.yaml` if not already deployed
3. Deploy `restore-verification.yaml` with `CreateSecretsManagerEndpoint=false`
4. Invoke the Step Functions workflow with snapshot details

### Path 4: Content Classification (PII Scanner)

**Goal**: Scan files on FSx for ONTAP volumes for PII using Amazon Comprehend.

```
content-classification-scanner.yaml (VPC外 mode)
```

**Simplest deployment** — leave VpcId empty. Requires an Internet-origin S3 Access Point attached to the target volume.

For VPC-scoped access points (created by restore-verification), set VpcId and disable endpoint creation if they already exist.

### Path 5: Full Suite (All Capabilities)

Deploy in this order to avoid VPC Endpoint conflicts:

| Order | Stack | Endpoint Settings |
|-------|-------|-------------------|
| 1 | `automated-response.yaml` | `CreateVpcEndpoints=true` (creates SecretsManager + SNS) |
| 2 | `automated-response-ttl.yaml` | No endpoints created |
| 3 | `restore-verification.yaml` | `CreateSecretsManagerEndpoint=false`, `CreateStsEndpoint=true`, `CreateS3GatewayEndpoint=false` (if S3 GW EP exists) |
| 4 | `content-classification-scanner.yaml` | Deploy in **VPC-外 mode** (VpcId='') for simplicity |
| 5 | `syslog-vpce-cloudwatch.yaml` | Always creates CW Logs Syslog EP (unique, no conflict) |

---

## Pre-flight Validation

Before deploying VPC-mode stacks, run the pre-flight check script:

```bash
bash shared/scripts/preflight-check.sh \
  --vpc-id vpc-0123456789abcdef0 \
  --profile automated-response

# Available profiles:
#   audit-shipping        — Path 1
#   automated-response    — Path 2
#   restore-verification  — Path 3
#   content-classification — Path 4
#   full-suite            — Path 5
```

The script checks:
- Existing VPC Endpoints (prevents duplicate creation failures)
- Security Group egress rules (HTTPS 443 to ONTAP management IP)
- Route Table associations for specified subnets
- ONTAP S3 server presence on target SVM (restore-verification only)
- S3 Access Point network origin (Internet vs VPC)

---

## Parameter File Templates

Sample parameter files are in `cfn-params/` using the standard CloudFormation JSON format:

```
cfn-params/
├── README.md                              ← Usage instructions
├── automated-response.example.json
├── automated-response-ttl.example.json
├── restore-verification.example.json
├── monitoring-dashboard.example.json
├── qtree-quota-monitor.example.json
├── content-classification.example.json
└── vendor-datadog.example.json
```

Copy, rename (remove `.example`), fill in your values, then deploy:

```bash
cp cfn-params/automated-response.example.json cfn-params/automated-response.json
# Edit with your values

# Option A: create-stack (supports file:// parameter files)
aws cloudformation create-stack \
  --stack-name fsxn-automated-response \
  --template-body file://shared/templates/automated-response.yaml \
  --parameters file://cfn-params/automated-response.json \
  --capabilities CAPABILITY_NAMED_IAM

# Option B: deploy (inline Key=Value only — no file:// support)
aws cloudformation deploy \
  --template-file shared/templates/automated-response.yaml \
  --stack-name fsxn-automated-response \
  --parameter-overrides \
    OntapMgmtIp=198.51.100.10 \
    OntapCredentialsSecretArn=arn:aws:secretsmanager:... \
    VpcId=vpc-xxx SubnetIds=subnet-aaa,subnet-bbb \
    SecurityGroupId=sg-xxx DefaultSvmName=svm-prod \
    CreateVpcEndpoints=true \
  --capabilities CAPABILITY_NAMED_IAM
```

**Operational note**: `aws cloudformation deploy --parameter-overrides` does **not** support `file://`. Use inline `Key=Value` pairs with `deploy`, or use `create-stack --parameters file://` for JSON file-based deployment.

---

## Deployment Time Estimates

| Path | Stacks | Estimated Duration | Notes |
|------|--------|--------------------|-------|
| 1: Audit Log Shipping | 3 | 5–10 minutes | No VPC Endpoints to create |
| 2: Incident Response | 2 | 8–15 minutes | Interface EP creation: ~2 min each |
| 3: Recovery Point Verification | 2 | 10–20 minutes | STS EP creation adds time |
| 4: Content Classification | 1 | 3–5 minutes (VPC外) | VPC mode adds 5–10 min for EPs |
| 5: Full Suite | 5 | 25–40 minutes | Deploy sequentially |

**Operational note**: Step Functions execution for restore-verification (Path 3) takes an additional 15–40 minutes per run due to FSx for ONTAP internal synchronization when attaching an S3 Access Point to a FlexClone.

---

## Cost Considerations

### VPC Endpoints (Fixed Monthly)

| Endpoint Type | Cost (per endpoint) | Notes |
|---------------|--------------------:|-------|
| Interface (SecretsManager, SNS, STS, Comprehend) | ~$7.20/month + $0.01/GB | Per-AZ ENI charge |
| Gateway (S3, DynamoDB) | Free | No hourly or data charges |

**Full Suite baseline** (4 Interface EPs × 2 AZs): ~$57.60/month before any data processing.

### Compute and API Costs (Usage-Based)

| Service | Pricing | Typical Monthly (light usage) |
|---------|---------|------------------------------:|
| Lambda (audit polling, 5-min schedule) | $0.20/1M requests + compute | $1–5 |
| Lambda (incident response, event-driven) | Same | < $1 |
| Step Functions (restore-verification) | $0.025/1K transitions | < $1 |
| Comprehend DetectPiiEntities | $0.0001/unit (100 chars) | Varies by scan volume |
| DynamoDB (checkpoint/ledger) | Pay-per-request | < $1 |
| EventBridge Scheduler | $1/1M invocations | < $1 |
| SNS notifications | $0.50/1M publishes | < $1 |

**Cost note**: The largest variable cost is Comprehend PII scanning (content-classification). Scanning 10,000 files averaging 10 KB each costs approximately $10 per scan run.

### Cost Optimization

- Deploy only the paths you need (don't deploy Full Suite if you only need audit shipping)
- Use Gateway EPs (S3, DynamoDB) instead of Interface EPs where possible — they are free
- Set `DefaultMaxFiles` in content-classification to cap per-invocation Comprehend costs
- VPC外 mode for content-classification eliminates the need for 4 additional VPC Endpoints

### Data Processing Consideration (Content Classification)

When using the content-classification-scanner, file contents from FSx for ONTAP volumes are sent to the Amazon Comprehend `DetectPiiEntities` API for analysis. Comprehend processes data within the same AWS Region and does not store it after processing. However, if your organization has data residency or classification policies that restrict sending file contents to AWS AI services, review this with your compliance team before enabling PII scanning on sensitive volumes.

---

## ONTAP Version Requirements

| Feature | Minimum ONTAP Version | FSx for ONTAP Support |
|---------|----------------------|----------------------|
| REST API (all stacks) | 9.6+ | All FSx for ONTAP versions |
| S3 Access Point (restore-verification, classification) | 9.11.1+ | Supported since launch (9.11.1+) |
| Audit logging (NAS) | 9.0+ | All FSx for ONTAP versions |
| Name-mapping (automated-response) | 9.0+ | All FSx for ONTAP versions |
| FPolicy (external server) | 9.0+ | All FSx for ONTAP versions |

FSx for ONTAP currently runs ONTAP 9.11.1 or later, so all features in this project are supported on any FSx for ONTAP file system.

---

## Existing Infrastructure Constraints

### ONTAP S3 Server Exclusivity (restore-verification only)

FSx for ONTAP S3 Access Points and ONTAP-native S3 servers are mutually exclusive on the same SVM. If your SVM has an ONTAP S3 server enabled (for any purpose), the `restore-verification` stack's FlexClone + S3 AP attach step will fail with:

> Amazon FSx is unable to create an S3 access point because of an existing ONTAP object storage server on SVM {svm}

**Resolution**: Use a different SVM without an ONTAP S3 server, or disable the existing server (data loss risk — confirm with team first).

### Volume Security Style (restore-verification only)

The S3 Access Point requires UNIX security style for the target volume. NTFS and mixed volumes are not supported for direct S3 AP attachment.

**Check**: `curl -sk -u admin:pass "https://<mgmt-ip>/api/storage/volumes?name=<vol>&fields=nas.security_style"`

### Active Directory Integration

The `automated-response` stack's SMB user blocking (name-mapping) operates at the ONTAP SVM level. To block SMB users by domain identity (e.g., `CORP\jdoe`), the SVM must be joined to Active Directory.

#### S3 Access Points on AD-Joined SVMs

If your SVM is AD-joined (CIFS enabled), **AD domain controller connectivity is required for all S3 AP data operations** — not just SMB. This applies to `restore-verification` and `content-classification` stacks that use S3 APs to access volume data.

| Condition | S3 AP Data Operations |
|-----------|:---:|
| Non-AD SVM (CIFS disabled) | ✅ Always works |
| AD-joined SVM + AD DCs reachable | ✅ Works |
| AD-joined SVM + AD DCs unreachable | ❌ AccessDenied |

**Pre-flight check**: Before deploying `restore-verification` on an AD-joined SVM, verify AD health:
```bash
# From an EC2 in the same VPC/subnet:
nslookup <AD-domain-FQDN> <AD-DNS-IP>
# If timeout → AD DCs are down → S3 AP data operations will fail
```

**Design recommendation**: For `restore-verification`, prefer using a non-AD SVM when possible. If you must use an AD-joined SVM, ensure AD infrastructure is monitored and highly available.

#### AD Join: Verified Deployment Sequence

1. **Create or identify your AD** (AWS Managed or self-managed)
2. **Deploy `demo-ad-environment.yaml`** (or use existing AD infrastructure)
3. **Run `demo-ad-join-svm.sh`** to join the SVM to AD
4. **Create a CIFS share** on the SVM (required for SMB access)

#### AD Join: Critical Configuration (Verified on ONTAP 9.17.1)

| Setting | AWS Managed AD | Self-Managed AD |
|---------|---------------|-----------------|
| OU Path | `OU=Computers,OU=<ShortName>,DC=...` | `OU=Computers,DC=...` or custom |
| FileSystemAdministratorsGroup | `Domain Admins` | `Domain Admins` or delegated group |
| NetBIOS Name | Must differ from domain ShortName | Must be unique in domain |
| Username | `Admin` | Service account with delegated perms |

**Common failure**: Using `AWS Delegated FSx Administrators` as `FileSystemAdministratorsGroup` causes the SVM to enter `MISCONFIGURED` state. Use `Domain Admins` instead.

**OU path for AWS Managed AD**: AWS Managed AD creates an intermediate OU with the domain's short name. For domain `demo.fsx.local` (ShortName: `demo`), the correct path is `OU=Computers,OU=demo,DC=demo,DC=fsx,DC=local` — not `OU=Computers,DC=demo,DC=fsx,DC=local`.

**Recovery from MISCONFIGURED**: Re-run `aws fsx update-storage-virtual-machine` with corrected parameters. No SVM deletion required.

#### Windows EC2 Domain Join (CloudFormation)

Use `AWS::SSM::Association` with the AWS-managed `AWS-JoinDirectoryServiceDomain` document. Do NOT use `SsmAssociations` on the EC2 instance or create custom SSM Documents with `aws:domainJoin`.

See `shared/templates/demo-ad-environment.yaml` for the verified pattern.

#### Prerequisites for Demo Environment

Before deploying `demo-ad-environment.yaml`, ensure these VPC Endpoints exist (required for SSM access to private-subnet EC2):

```bash
# Check existing SSM endpoints
aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=<vpc-id>" \
  --query 'VpcEndpoints[?contains(ServiceName,`ssm`)].ServiceName'

# If missing, create (one command per service):
for svc in ssm ssmmessages ec2messages; do
  aws ec2 create-vpc-endpoint --vpc-id <vpc-id> --vpc-endpoint-type Interface \
    --service-name com.amazonaws.<region>.$svc \
    --subnet-ids <subnet1>,<subnet2> --security-group-ids <sg-id> --private-dns-enabled
done
```

#### NFS Blocking: Export-Policy vs NACL

| Mechanism | Same-Subnet | Cross-Subnet | Root Client | Effect Timing |
|-----------|:-----------:|:------------:|:-----------:|:-------------:|
| Export-policy deny rule | ✅ Works | ✅ Works | ✅ Blocks root | Immediate |
| NACL deny rule | ❌ No effect | ✅ Works | ✅ Blocks all | Immediate |

For most FSx deployments (client and FSx ENI in the same subnet), **export-policy deny rule is the reliable mechanism**. NACL is useful as defense-in-depth only when client and FSx are in different subnets.

#### "Same-Subnet but I Need Packet-Level Blocking" — Options

If your security policy requires network-layer blocking even when client and FSx share a subnet, here are your options:

| Option | How | Trade-off |
|--------|-----|-----------|
| **1. Move client to a different subnet** | Separate application subnets from storage subnets. FSx ENI in `subnet-storage`, clients in `subnet-app`. | Requires re-architecture; best practice for production regardless |
| **2. Security Group deny (indirect)** | Remove the client's SG from the FSx ENI's allowed inbound sources. Not instant — existing TCP connections persist until timeout. | SG changes affect all clients in that group, not just the attacker |
| **3. Export-policy deny (recommended)** | Already implemented in this module. Effective immediately, even for root. No network-layer change needed. | Cannot block non-NFS traffic from the same IP (DNS, SSH, etc.) |
| **4. Host-level firewall (iptables/nftables)** | Push an iptables rule to the client via SSM: `iptables -A OUTPUT -d <FSx-IP> -j DROP` | Requires SSM access to the attacker's host (may be compromised) |
| **5. Subnet re-routing (advanced)** | Add a blackhole route for the client IP in the subnet's route table | Affects all traffic to/from that IP, not just NFS |

**Recommendation for same-subnet deployments**: Use export-policy deny rule (Option 3) as the primary mechanism. It is immediate, per-IP, and does not require network infrastructure changes. If you also need to block the IP's non-NFS traffic, consider Option 1 as a long-term architectural improvement.

#### SMB Blocking: Name-Mapping Limitations (Verified)

The name-mapping deny mechanism (`replacement: " "`) has important scope limitations:

| Condition | Blocks SMB? | Notes |
|-----------|:-----------:|-------|
| UNIX/MIXED volume, non-admin user | ✅ | Requires UNIX ID resolution during SMB access |
| NTFS volume, any user | ❌ | NTFS ACL evaluated directly, mapping skipped |
| Any volume, Domain Admins member | ❌ | FileSystemAdministratorsGroup bypasses mapping |

#### Blocking Admin/Privileged Accounts: Options and Considerations

Domain Admins (or members of `FileSystemAdministratorsGroup`) bypass name-mapping deny. If you need to block a compromised admin account, use one of these alternative mechanisms:

| Option | Method | Effect | Considerations |
|--------|--------|--------|----------------|
| **1. Disable AD account** | PowerShell: `Disable-ADAccount -Identity <user>` or ONTAP REST API via AD LDAP | **Immediate on re-authentication** — Kerberos ticket renewal fails with "credentials revoked" | Affects ALL services (not just storage). Existing sessions persist until ticket expiry (~10 hr default) unless combined with session disconnect |
| **2. Force session disconnect** | ONTAP REST API: `DELETE /protocols/cifs/sessions/{svm}/{id}/{conn}` | Terminates active session immediately, forces re-authentication | Combine with option 1 — disconnect alone is temporary (client will reconnect if account is still enabled) |
| **3. Enable export-policy for SMB** | `vserver cifs options modify -is-exportpolicy-enabled true` + add deny rule | IP-based SMB blocking at the ONTAP level, works regardless of user privilege | Not enabled by default. Requires SVM-level configuration change. See [AWS Blog: Restrict access using export policies](https://aws.amazon.com/blogs/storage/restrict-access-to-your-amazon-fsx-for-netapp-ontap-volumes-using-export-policies/) |
| **4. NACL deny (cross-subnet)** | AWS VPC NACL with deny rule for the admin's client IP | Network-level block, cannot be bypassed by any ONTAP mechanism | Only works cross-subnet. Same-subnet: no effect |
| **5. Security Group removal** | Remove client's SG from FSx ENI allowed sources | Prevents new TCP connections from the client | Affects ALL users from that SG, not just the compromised admin. Existing TCP connections persist |

**Recommended sequence for admin account compromise**:
1. `Disable-ADAccount` (prevents new Kerberos tickets)
2. `DELETE /protocols/cifs/sessions/...` (force-disconnect existing session)
3. Wait for Kerberos ticket cache to expire (or force `klist purge` on the host if accessible)

Source: [NetApp KB: Denied access to NTFS volume because AD Account is locked or disabled or expired](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/Denied_access_to_NTFS_volume_because_AD_Account_is_locked_or_disabled_or_expired) — confirms that disabled/locked AD accounts result in "Kerberos Error: Clients credentials have been revoked", blocking NTFS access regardless of name-mapping or security style.

> **Operational note**: Disabling an AD admin account is a high-impact action. In a real incident, coordinate with your AD team and have a documented restoration procedure. Consider using a dedicated "break-glass" admin account that is never used day-to-day, so the primary admin can be disabled without losing all management access.

**Key point for readers**: If your FSx for ONTAP volumes use NTFS security style (common in Windows-only environments), name-mapping deny will NOT block SMB access. Alternative mechanisms for NTFS volumes:
- Disable the AD user account directly (prevents Kerberos authentication)
- Modify NTFS ACL to remove the user's access
- Use Security Group changes to block network-level access (with cross-subnet limitation noted above)

**Why this happens** (from NetApp documentation):

On NTFS security style volumes, ONTAP still performs win→unix name-mapping to build internal credentials (UID/GID lookup), but the final access decision uses **Windows credentials (NTFS ACL)**, not the mapped UNIX identity. A deny mapping (`" "`) prevents UNIX ID resolution, but ONTAP falls back to the `default-unix-user` (`pcuser`, UID 65534) and proceeds with NTFS ACL evaluation using the original Windows token.

On UNIX/MIXED security style volumes, access is controlled by the **mapped UNIX UID/GID**. A deny mapping blocks UNIX ID resolution entirely, and access is denied.

Sources:
- [NetApp KB: How does name-mapping work when CIFS clients access NTFS security style resources](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/How_does_name-mapping_work_when_CIFS_clients_access_NTFS_security_style_resources) — "Access is granted or denied based on the Windows credentials because the volume is set to NTFS security style."
- [NetApp KB: Understanding name-mapping in a multiprotocol environment](https://kb.netapp.com/on-prem/ontap/da/NAS/NAS-KBs/Understanding_name-mapping_in_a_multiprotocol_environment) — CIFS→UNIX style: "Access is granted or denied based on the UID and GID(s) of the UNIX credentials"
- [NetApp ONTAP Docs: Security styles and their effects](https://docs.netapp.com/us-en/ontap/smb-admin/security-styles-their-effects-concept.html) — "Security styles only determine the type of permissions ONTAP uses to control data access"
- [NetApp ONTAP Docs: Create name mappings](https://docs.netapp.com/us-en/ontap/nfs-admin/create-name-mapping-task.html) — "You can use the -replacement statement to explicitly deny a mapping to the user by using the null replacement string \" \" (the space character)"
- [NetApp KB: How to manually unblock SMB/CIFS access blocked by Workload Security](https://kb.netapp.com/Cloud/BlueXP/DII/How_to_manually_unblock_SMB_CIFS_access_that_blocked_by_Workload_security) — Confirms DII uses `replacement: " "` (same mechanism as this project)

**Implication for DII Storage Workload Security**: DII's SMB user blocking uses the same name-mapping deny mechanism. This implies DII's blocking is also effective only on UNIX/MIXED security style volumes for SMB access. (DII's documentation does not explicitly state this limitation, but it follows from the ONTAP name-mapping behavior above.)

**Open investigation (Limitation 3)**: In our testing on AD-joined SVMs (ONTAP 9.17.1P7D1), name-mapping entries with `replacement: " "` were observed to disappear shortly after creation (HTTP 201 returned, immediate read-back confirmed, but gone within seconds). This did not occur on non-AD-joined SVMs. Root cause under investigation — may be related to `default-unix-user` configuration or CIFS server credential building. DII Workload Security uses this mechanism successfully, suggesting a configuration difference rather than a fundamental incompatibility.

**Root cause identified (Limitation 3)**: On AD-joined SVMs running ONTAP 9.17.1P7D1, a background validation process periodically checks name-mapping `replacement` values against the local UNIX user table. Values that cannot be resolved to a valid UNIX user (including `" "` space) are automatically deleted within 30–60 seconds of creation. This explains why DII Workload Security works (it likely ensures the relevant UNIX user lookup infrastructure is in place), while our minimal SVM configuration triggers the auto-cleanup.

**Verified behavior**:
| replacement value | Persists after 60s? | Why |
|-------------------|:-------------------:|-----|
| `" "` (space) | ❌ Auto-deleted | Not resolvable as UNIX user |
| `"pcuser"` (UID 65534) | ✅ Persists | Exists in local UNIX user table |
| `"nobody"` (UID 65535) | ✅ Persists | Exists in local UNIX user table |

**Workaround for AD-joined SVMs**: Use `replacement: "nobody"` instead of `replacement: " "`. The `nobody` user (UID 65535) is a standard unprivileged UNIX identity. Combined with restrictive volume permissions (mode 750 or 700, owner=root, group=root), this effectively denies SMB access on UNIX/MIXED security style volumes because `nobody` has no `other` read access.

**Action required on the volume** (if using `nobody` workaround):
```bash
# Set volume root permissions to deny 'other' access:
curl -sk -u fsxadmin:<pass> -X PATCH \
  "https://<mgmt-ip>/api/storage/volumes/<uuid>" \
  -H "Content-Type: application/json" \
  -d '{"nas":{"unix_permissions":"750"}}'
```

**Note**: This workaround changes the semantics from "mapping fails → access denied at authentication" to "mapping succeeds with unprivileged UID → access denied by UNIX permissions." The net effect is the same (user cannot access files), but the mechanism is different and requires the volume's UNIX permissions to be restrictive.

> **Status: RESOLVED** — `ontap_response.py` (Lambda Layer v3+) now auto-detects AD-joined SVMs and uses `replacement: "nobody"` automatically. No manual configuration required. E2E verified: mapping persists 90+ seconds on AD-joined SVM (previously deleted in 30-60s with space replacement).

### DNS / Route 53

No stack creates Route 53 records. VPC Endpoint private DNS is handled automatically by AWS (PrivateDnsEnabled=true). No custom DNS configuration required.

---

## Troubleshooting

### Stack rollback: "private-dns-enabled cannot be set"

**Cause**: You are creating a VPC Endpoint that already exists in the VPC.

**Fix**: Set the corresponding `Create*Endpoint=false` parameter. Use the VPC Endpoint check command above.

### Lambda timeout accessing ONTAP management IP

**Cause**: Security Group does not allow outbound HTTPS (443) to the management IP, or Lambda is not in a subnet with a route to the management IP.

**Fix**: Ensure SecurityGroupId allows egress to OntapMgmtIp:443. Verify SubnetIds are in the same VPC as FSx.

### AccessDeniedException on Secrets Manager

**Cause**: Either (a) Secret ARN has changed (Secrets Manager assigns a new random suffix when recreated), or (b) VPC Endpoint for Secrets Manager is not available.

**Fix**: Verify the exact ARN: `aws secretsmanager describe-secret --secret-id <name> --query ARN`. Update the stack with the correct ARN.

### restore-verification: "existing ONTAP object storage server"

**Cause**: The target SVM has an ONTAP S3 server. This is a structural conflict, not a timing issue — no amount of retry will resolve it.

**Fix**: Use a different SVM or delete the ONTAP S3 server (confirm no data loss first).

### S3 AP AccessDenied on AD-Joined SVM (HeadBucket OK, Data Ops Fail)

**Cause**: The SVM is joined to Active Directory (CIFS enabled), but the AD domain controllers are unreachable. On AD-joined SVMs, ONTAP performs a `unix→win` reverse lookup for every file system operation — even on UNIX-style volumes with a UNIX file system identity. When AD DCs are down, this lookup fails and all data operations return AccessDenied.

**Diagnosis**: HeadBucket succeeds (only checks the S3 AP exists at the service layer) but ListObjectsV2, GetObject, and PutObject all return AccessDenied. The IAM policy and AP resource policy are correct. This is NOT an IAM issue.

**Fix**:
1. Verify AD DC connectivity from the SVM's subnet: `nslookup <domain> <ad-dns-ip>`
2. If AD was deleted/recreated: update the SVM's DNS IPs or force-delete + re-create the CIFS service (see below)
3. If AD is permanently gone and SMB is not needed: force-delete the CIFS service to remove the AD dependency

**Force-delete CIFS and re-join to new AD** (via ONTAP REST API):
```bash
# 1. Disable secure DNS updates
curl -sk -X PATCH -u fsxadmin:$PW -H 'Content-Type: application/json' \
  -d '{"dynamic_dns":{"use_secure":false}}' \
  "https://<mgmt-ip>/api/name-services/dns/<svm-uuid>"

# 2. Force-delete CIFS service
curl -sk -X DELETE -u fsxadmin:$PW -H 'Content-Type: application/json' \
  -d '{"force":true,"ad_domain":{"fqdn":"<domain>","user":"Admin","password":"<pw>"}}' \
  "https://<mgmt-ip>/api/protocols/cifs/services/<svm-uuid>"

# 3. Clean stale records
curl -sk -X POST -u fsxadmin:$PW -H 'Content-Type: application/json' \
  -d '{"vserver":"<svm-name>"}' \
  "https://<mgmt-ip>/api/private/cli/vserver/cifs/users-and-groups/remove-stale-records"

# 4. Re-create CIFS with new AD (use a NEW NetBIOS name — old one is taken in AD)
curl -sk -X POST -u fsxadmin:$PW -H 'Content-Type: application/json' \
  -d '{"svm":{"uuid":"<svm-uuid>"},"name":"<NEW-NETBIOS>","ad_domain":{"fqdn":"<domain>","organizational_unit":"<ou>","user":"Admin","password":"<pw>"}}' \
  "https://<mgmt-ip>/api/protocols/cifs/services"
```

**Key points**:
- Each failed rejoin attempt leaves an orphaned computer account in AD — always use a new NetBIOS name
- FSx API may report the SVM as `MISCONFIGURED` even after successful ONTAP-level CIFS re-creation
- FSx automatically manages `s3_unix` name-mapping for S3 APs — no manual setup needed
- Same-account S3 AP access does NOT require an AP resource policy (IAM identity policy alone is sufficient)

---

## Day 2: Verification and Ongoing Operations

After deployment, verify the stacks are working correctly:

### Immediate Verification (within 1 hour)

| Path | Verification Step | Expected Result |
|------|-------------------|-----------------|
| 1 (Audit) | Perform a file operation on the audited share, wait 5–10 min | Logs appear in vendor platform (`source:fsxn` in Datadog) |
| 2 (Response) | Send `health_check` via SNS trigger | Lambda returns `"status": "healthy"` |
| 3 (Recovery) | Execute Step Functions with a test snapshot | Workflow completes with `verdict: clean` or `suspicious` |
| 4 (Classification) | Invoke Lambda with a test S3 AP ARN | DynamoDB table contains scan results |

### Ongoing Monitoring

Set up alarms for these CloudWatch metrics:

| Metric | Alarm Condition | Action |
|--------|----------------|--------|
| Lambda Errors (all stacks) | > 0 for 5 minutes | Investigate via CloudWatch Logs |
| DLQ ApproximateNumberOfMessagesVisible | > 0 | Replay failed messages (see [DLQ Replay Runbook](runbooks/dlq-replay.md)) |
| Lambda Duration (audit poller) | > 80% of timeout | Increase LambdaTimeout or LambdaMemorySize |
| Step Functions ExecutionsFailed | > 0 | Check execution history in console |

### Periodic Reviews (Monthly)

- **ProtectedAccountsExtra**: Review the list of accounts that cannot be auto-blocked. Service accounts change over time; stale entries accumulate and reduce the effectiveness of incident response.
- **Secret ARN validity**: If Secrets Manager secrets are rotated or recreated, the ARN suffix changes. Verify with `aws secretsmanager describe-secret --secret-id <name> --query ARN`.
- **VPC Endpoint costs**: Review Interface EP costs; remove any that are no longer needed.

---

## Rollback and Cleanup

### Automatic Rollback

CloudFormation automatically rolls back on deployment failure. No manual intervention required for the stack itself. However:

- VPC Endpoints created before the failure point **are cleaned up** by the rollback
- DynamoDB tables with `DeletionPolicy: Retain` may survive (check with `aws dynamodb list-tables`)
- If a stack enters `ROLLBACK_COMPLETE` state, delete it before re-creating with the same name: `aws cloudformation delete-stack --stack-name <name>`

### Manual Cleanup (Full Removal)

Use the vendor-specific cleanup scripts or the shared cleanup utility:

```bash
# Single vendor stack
bash integrations/<vendor>/scripts/cleanup.sh --all -y

# Shared stacks (delete in reverse deployment order)
aws cloudformation delete-stack --stack-name fsxn-content-classification
aws cloudformation delete-stack --stack-name fsxn-restore-verification
aws cloudformation delete-stack --stack-name fsxn-automated-response-ttl
aws cloudformation delete-stack --stack-name fsxn-automated-response
```

**Operational note**: Delete stacks in reverse deployment order. The `automated-response` stack creates VPC Endpoints used by other stacks; deleting it first causes other stacks to lose Secrets Manager access during their own deletion (usually harmless but generates error logs).

### E2E Verification Environment Cleanup

When tearing down a full verification environment (FSx + AD + demo stacks), follow this order to avoid dependency errors:

| Order | Resource | Command | Notes |
|-------|----------|---------|-------|
| 1 | `fsxn-rv-e2e` stack | `aws cloudformation delete-stack --stack-name fsxn-rv-e2e` | Step Functions + Lambda + DynamoDB + VPC Endpoints |
| 2 | Helper EC2 instances | `aws ec2 terminate-instances --instance-ids <id>` | SSM helper, Windows demo EC2 |
| 3 | FlexClone volumes | `aws fsx delete-volume --volume-id <fsvol-id> --ontap-configuration '{"SkipFinalBackup":true}'` | Always use `SkipFinalBackup` for throwaway clones |
| 4 | Demo volumes | Same as above | Any `demo_*` or `verify_*` volumes |
| 5 | AD environment stack | `aws cloudformation delete-stack --stack-name fsxn-demo-ad-env` | Managed AD deletion takes 15-30 min |
| 6 | SVM (if dedicated) | `aws fsx delete-storage-virtual-machine --storage-virtual-machine-id <id>` | May fail if volumes or AD relations remain |
| 7 | IAM roles/profiles | Detach policies → delete profile → delete role | See below |

**IAM cleanup sequence** (order matters):
```bash
# 1. Remove role from instance profile
aws iam remove-role-from-instance-profile \
  --instance-profile-name <name> --role-name <name>
# 2. Delete instance profile
aws iam delete-instance-profile --instance-profile-name <name>
# 3. Detach managed policies
aws iam detach-role-policy --role-name <name> --policy-arn <arn>
# 4. Delete inline policies
aws iam delete-role-policy --role-name <name> --policy-name <name>
# 5. Delete role
aws iam delete-role --role-name <name>
```

**Known issues during cleanup:**

| Issue | Cause | Resolution |
|-------|-------|-----------|
| SVM shows `MISCONFIGURED` after AD deletion | FSx API retains old AD config state | Delete SVM directly, or ignore if FS is being decommissioned |
| Volume deletion fails: "has one or more clones" | ONTAP recovery queue holds stale clone references | Wait 12h, or purge via `DELETE /private/cli/volume/recovery-queue/purge` (ONTAP admin only) |
| Volume deletion fails: "SnapMirror relationships" | FSx backup management | Always delete via FSx API (`delete-volume`), never ONTAP REST API |
| Stack DELETE_FAILED | Resource dependency | Check `describe-stack-events` for the specific resource, resolve, retry |

---

## Related Documents

- [Prerequisites and Resource Deployment](prerequisites.md) — S3 bucket, audit logging, S3 AP creation
- [Quick Start (Minimum Test)](quick-start-minimum.md) — Fastest path to verify log delivery
- [Verified Recovery Point Guide](verified-recovery-point-guide.md) — Detailed restore-verification usage
- [Automated Response Guide](automated-response-guide.md) — Incident response operations
- [Multi-Account Deployment](multi-account-deployment.md) — StackSets for organizations
