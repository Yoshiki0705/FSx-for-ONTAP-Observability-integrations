# ONTAP System Manager GUI Operations Guide

🌐 [日本語](../ja/system-manager-gui-guide.md) | **English** (this page)

## Overview

This document provides step-by-step instructions for operations teams to use **ONTAP System Manager (GUI)** for:

- Accessing System Manager
- Configuring file share audit logging
- Setting up Qtree quotas (capacity limits)
- Configuring capacity monitoring and notifications

> **Target audience**: Operations staff familiar with Windows File Resource Manager

> **Where this fits**: this guide covers the NetApp Console / System Manager GUI path, which
> this project treats as an alternative to its AWS-native default. The default GUI path is
> the self-hosted [management-console/](../../management-console/) (Cognito/IAM) plus the
> Amplify Gen2 file portal built in the sibling repo
> ([solutions/amplify-portal](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/tree/main/solutions/amplify-portal)).
> NetApp Console / System Manager stays a valid choice, suited to teams already using a
> NetApp SaaS relationship. The trade-offs are symmetric: the native options are apps you
> deploy and operate yourself, while NetApp Console is managed SaaS requiring an NSS account.
> See the [management-plane decision tree](decision-tree-management-monitoring.md) to choose.

---

## Background: System Manager vs NetApp BlueXP<!-- allow:naming --> vs NetApp Console<!-- allow:naming -->

| Tool | Type | Cost | Account Required | Purpose |
|------|------|------|-----------------|---------|
| ONTAP System Manager | ONTAP GUI (accessed via NetApp Console<!-- allow:naming -->) | Free | NSS account required | General storage management |
| NetApp Console<!-- allow:naming --> (formerly BlueXP<!-- allow:naming -->) | SaaS portal | Free (basic features) | NSS account required | System Manager host + multi-cloud management |
| [Self-hosted Management Console](../../management-console/) | AWS-native (ECS Fargate + AMP/AMG) | ~$250/month (24/7) | None (AWS account only) | VPC-internal monitoring + management UI |
| ONTAP REST API | HTTP API (direct access) | Free | None (fsxadmin auth) | Automation & scripting |
| ONTAP CLI | SSH command line | Free | None (fsxadmin auth) | Advanced configuration |

> ⚠️ **Critical constraint**: Unlike on-premises ONTAP, FSx for ONTAP does **NOT** display the System Manager UI when you directly browse to `https://<management-endpoint-ip>` (returns 404 error). System Manager GUI access requires **NetApp Console<!-- allow:naming -->**. REST API (`/api/`) and CLI (SSH) are directly accessible.

GUI-based storage management requires **NetApp Console<!-- allow:naming --> setup**. CLI/REST API can be used immediately without any NetApp account.

---

## 1. Accessing System Manager (via NetApp Console<!-- allow:naming -->)

### 1.1 NetApp Console<!-- allow:naming --> Setup Steps

To use System Manager with FSx for ONTAP, the following setup is required:

#### Step 1: Create a NetApp Account (NSS)

1. Go to [NetApp User Registration](https://mysupport.netapp.com/site/user/registration)
2. Select the **NetApp Customer/End User** access level (this is the literal label of the UI option — the underlying access tier is end-user/standard, not vendor-support)
3. Enter your FSx for ONTAP **File System ID** in the **SERIAL NUMBER** field
4. After registration, end-user-level access is granted within 1 business day

> **Note**: Account creation is free. Support case filing requires a paid support contract, but this is NOT needed for System Manager usage.

#### Step 2: Log in to NetApp Console<!-- allow:naming -->

1. Go to [NetApp Console<!-- allow:naming -->](https://console.netapp.com)
2. Log in with NSS credentials
3. Set up account name on first login

#### Step 3: Register AWS Credentials

Add AWS credentials to NetApp Console<!-- allow:naming -->:

| Permission mode | Operations allowed |
|-----------------|--------------------|
| Read-only | Discovery and monitoring of FSx for ONTAP only |
| Read/write | Volume creation, modification, and other management operations |

Reference: [Set up permissions](https://docs.netapp.com/us-en/storage-management-fsx-ontap/requirements/task-setting-up-permissions-fsx.html)

#### Step 4: Create a Console Agent or Link

To use management features including System Manager, one of the following is required:

| Method | Description | Recommended For |
|--------|-------------|-----------------|
| **Console Agent** | EC2 instance (t3.xlarge) deployed in your VPC | Production, managing multiple file systems |
| **Link** | AWS Lambda-based trust relationship | Lightweight management, cost minimization |

- Console Agent: [Creation guide for AWS](https://docs.netapp.com/us-en/console-setup-admin/concept-install-options-aws.html)
- Link: [Link creation guide](https://docs.netapp.com/us-en/workload-fsx-ontap/create-link.html)

#### Step 5: Discover FSx for ONTAP

1. NetApp Console<!-- allow:naming --> → **Systems** page
2. **Discover** → Select **Amazon FSx for NetApp ONTAP**
3. Specify AWS region and credentials
4. Existing FSx for ONTAP file systems are discovered

#### Step 6: Open System Manager

1. NetApp Console<!-- allow:naming --> → **Systems** page → Select target file system
2. Click **System Manager**
3. Enter `fsxadmin` credentials
4. System Manager UI is displayed within NetApp Console<!-- allow:naming -->

### 1.2 Alternative: CLI / REST API (No NetApp Console<!-- allow:naming --> Required)

Management methods that do NOT require NetApp Console<!-- allow:naming --> setup:

| Method | Access Target | Authentication | Use Case |
|--------|--------------|----------------|----------|
| ONTAP CLI | `ssh fsxadmin@<management-endpoint-ip>` | fsxadmin password | All ONTAP operations |
| ONTAP REST API | `https://<management-endpoint-ip>/api/` | Basic Auth (fsxadmin) | Automation & scripting |
| **AWS CLI** | `aws fsx ...` | IAM authentication | File system-level management |

> **Recommended**: Perform initial configuration (audit logs, quotas) via CLI/REST API, then use NetApp Console<!-- allow:naming --> (System Manager) for day-to-day monitoring and management. This hybrid approach is the most practical.

> **Security best practices:** observe the following two points.
>
> - Store `fsxadmin` password in AWS Secrets Manager
> - All System Manager operations are recorded in ONTAP audit logs

---

## 2. Audit Log Configuration (GUI Steps)

### 2.1 Prerequisites

- A volume for storing audit logs must exist
- An SVM (Storage Virtual Machine) must exist

### 2.2 Creating the Audit Log Volume

> Skip if a volume already exists

1. System Manager → **Storage** → **Volumes**
2. Click **+ Add**
3. Configure:
   - Volume name: `audit_logs`
   - SVM: Select target SVM
   - Size: 50GB+ recommended (adjust based on log volume)
   - Export Policy: None (internal use only)
4. Click **Save**

### 2.3 Enabling Audit Logging

1. System Manager → **Storage** → **Storage VMs**
2. Click the target SVM
3. Select the **Settings** tab
4. **Security** section → Click the pencil icon (edit) next to **Audit**
5. Toggle **Enable Auditing** on
6. Configuration:

| Setting | Recommended Value | Description |
|---------|-------------------|-------------|
| Log Destination | `/vol/audit_logs` | Audit log storage path |
| Log Format | **EVTX** | Windows Event Log format (familiar to Windows admins) |
| Rotation Schedule | Size-based | Size-based rotation |
| Rotation Size | 100 MB | Maximum size per file |
| Rotation Limit | 0 (unlimited) | Number of files to retain (0=unlimited) |

7. Click **Save**

### 2.4 Configuring Audit Targets (SACL)

Which folders/files are audited is controlled by Windows SACL (System Access Control List).

#### Configure from Windows Explorer
1. Right-click target folder → **Properties**
2. **Security** tab → **Advanced**
3. **Auditing** tab → **Add**
4. Configure audit entry:
   - Principal: `Everyone` (all users)
   - Type: Both **Success** and **Failure**
   - Permissions: **Full Control** (to log all operations)

> **Note**: SACL configuration uses Windows File Resource Manager — existing operations team skills apply directly.

### 2.5 Verifying Audit Logs

After configuration, verify:

1. Create/access a file in the CIFS share
2. System Manager → **Events** to check audit events
3. Or verify EVTX files are generated in the audit log volume

---

## 3. Qtree Quota (Capacity Limit) Configuration

### 3.1 Overview

Qtree quotas enable per-folder capacity limits.

| Quota Type | Behavior |
|-----------|----------|
| **Soft limit** | Warning on threshold exceeded (EMS event issued). Writes continue |
| **Hard limit** | Writes rejected when threshold exceeded |

### 3.2 Creating a Qtree

1. System Manager → **Storage** → **Volumes** → Click target volume
2. Select **Qtrees** tab
3. Click **+ Add Qtree**
4. Configure:
   - Name: `dept-sales` (e.g., department name)
   - Security Style: **NTFS** (for Windows environments)
   - Export Policy: Configure as needed
5. Click **Save**

### 3.3 Creating Quota Rules

1. System Manager → **Storage** → **Volumes** → Click target volume
2. Select **Quota Rules** tab (may be **Quotas** depending on ONTAP version)
3. Click **+ Add Quota Rule**
4. Configure:

| Setting | Example | Description |
|---------|---------|-------------|
| Quota Type | **Tree** | Per-Qtree limit |
| Qtree | `dept-sales` | Target Qtree |
| Disk Space Hard Limit | 100 GB | Write rejection threshold |
| Disk Space Soft Limit | 80 GB | Warning threshold (80%) |
| File Count Hard Limit | 1,000,000 | File count limit (optional) |

5. Click **Save**

### 3.4 Activating Quotas

After creating quota rules, quotas must be initialized:

1. System Manager → **Storage** → **Volumes** → Target volume
2. **Quota Rules** tab → Click **Initialize Quotas**

> **Note**: Quota initialization may take several minutes. Initial scan takes longer with many existing files.

### 3.5 Checking Quota Usage

1. System Manager → **Storage** → **Volumes** → Target volume
2. **Quota Rules** tab → Usage per Qtree is displayed

---

## 4. Capacity Monitoring and Notifications

### 4.1 Monitoring Method Selection

| Method | Monitoring Target | Real-time | Notification | Recommended Scenario |
|--------|------------------|-----------|--------------|---------------------|
| **A: EMS Webhook** | Qtree quota thresholds | ◎ Real-time | Lambda → SNS → Email | Immediate quota exceeded alerts |
| **B: CloudWatch Alarms** | Volume-level capacity | ○ 5-min interval | SNS → Email | Volume capacity monitoring |
| **C: ONTAP EMS → CloudWatch Events** | General EMS events | ○ Minutes | EventBridge → SNS | AWS-native integration |
| **D: Harvest + Grafana** | All metrics | ○ 60-sec interval | Grafana Alerting | Detailed dashboards |

### 4.2 Method A: EMS Webhook (Qtree Quota Notification — Recommended)

ONTAP automatically issues EMS events when Qtree quota thresholds are exceeded:

| EMS Event | Trigger Condition | Severity |
|-----------|-------------------|----------|
| `wafl.quota.softlimit.exceeded` | Soft limit exceeded | warning |
| `wafl.quota.hardlimit.exceeded` | Hard limit exceeded | error |

#### Architecture

```
Qtree capacity exceeded
  → ONTAP EMS event issued
  → Webhook (HTTPS POST)
  → API Gateway
  → Lambda (parse + deliver)
  → SNS Topic
  → Email notification
```

#### Setup Steps

##### Step 1: Deploy AWS-side resources

Use the EMS Webhook template from this repository:

```bash
# Deploy EMS Webhook stack
aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-ems-webhook \
  --parameter-overrides \
    LambdaFunctionArn=<EMS handler Lambda ARN> \
  --capabilities CAPABILITY_NAMED_IAM
```

##### Step 2: Create SNS Topic + Email Subscription

```bash
# Create SNS topic
aws sns create-topic --name fsxn-quota-alerts

# Subscribe email address
aws sns subscribe \
  --topic-arn arn:aws:sns:ap-northeast-1:123456789012:fsxn-quota-alerts \
  --protocol email \
  --notification-endpoint ops-team@example.com
```

##### Step 3: Configure ONTAP EMS Webhook (CLI)

> ⚠️ EMS Webhook configuration is **CLI-only** (not available in System Manager GUI).

```bash
# SSH to ONTAP management endpoint
ssh fsxadmin@<management-endpoint-ip>

# 1. Create webhook notification destination
event notification destination create -name quota-webhook \
  -rest-api-url https://<api-gateway-id>.execute-api.ap-northeast-1.amazonaws.com/prod/ems

# 2. Create quota event filter
event filter create -filter-name quota-alerts
event filter rule add -filter-name quota-alerts -type include \
  -message-name wafl.quota.*

# 3. Create notification
event notification create -filter-name quota-alerts \
  -destinations quota-webhook

# 4. Verify
event notification show
event notification destination show
```

##### Step 4: Verification

```bash
# Test: Write data to Qtree exceeding soft limit
# Copy a large file via Windows Explorer
# → EMS event issued → Webhook → Lambda → SNS → Verify email received
```

### 4.3 Method B: CloudWatch Alarms (Volume Capacity Monitoring)

CloudWatch monitors **volume-level** capacity only (not per-Qtree).

```bash
# Create CloudWatch alarm
aws cloudwatch put-metric-alarm \
  --alarm-name "FSx-ONTAP-Volume-Capacity-Warning" \
  --metric-name "StorageCapacityUtilization" \
  --namespace "AWS/FSx" \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:ap-northeast-1:123456789012:fsxn-quota-alerts \
  --dimensions Name=FileSystemId,Value=fs-0123456789abcdef0
```

| CloudWatch Metric | Description | Threshold Example |
|-------------------|-------------|-------------------|
| `StorageCapacityUtilization` | Volume usage (%) | 80% warning, 90% critical |
| `StorageUsed` | Usage (bytes) | Absolute value monitoring |

### 4.4 Method C: ONTAP EMS → CloudWatch Events → EventBridge

FSx for ONTAP automatically publishes some EMS events as CloudWatch Events.

```json
{
  "source": ["aws.fsx"],
  "detail-type": ["FSx for ONTAP EMS Event"],
  "detail": {
    "event-name": ["wafl.quota.softlimit.exceeded"]
  }
}
```

```bash
# Create EventBridge rule
aws events put-rule \
  --name "FSx-ONTAP-Quota-Alert" \
  --event-pattern '{"source":["aws.fsx"],"detail-type":["FSx for ONTAP EMS Event"],"detail":{"event-name":["wafl.quota.softlimit.exceeded","wafl.quota.hardlimit.exceeded"]}}'

# Add SNS target
aws events put-targets \
  --rule "FSx-ONTAP-Quota-Alert" \
  --targets "Id"="1","Arn"="arn:aws:sns:ap-northeast-1:123456789012:fsxn-quota-alerts"
```

> **Note**: Not all EMS events are delivered via CloudWatch Events. See [AWS documentation](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-cloudwatch-events.html) for supported events.

### 4.5 Recommended Configuration (Combined)

| Monitoring Target | Method | Notification |
|-------------------|--------|--------------|
| Qtree quota exceeded (immediate) | EMS Webhook (Method A) | Email + Slack |
| Volume capacity > 80% | CloudWatch Alarm (Method B) | Email |
| Volume capacity > 90% | CloudWatch Alarm (Method B) | Email + PagerDuty |
| Ransomware detection | EMS Webhook (Method A) | Email + Slack + PagerDuty |

---

## 5. System Manager Capabilities and Limitations

### ✅ Available in System Manager

| Category | Operations |
|----------|-----------|
| **Volume management** | Create, resize, delete, snapshots |
| **Qtree management** | Create, delete, change security style |
| **Quota management** | Create rules, initialize, check usage |
| **Audit logging** | Enable, configure, check status |
| **CIFS shares** | Create, ACL configuration, property changes |
| **NFS exports** | Policy creation, rule addition |
| **SnapMirror** | Replication setup, status monitoring |
| **Network** | LIF status, DNS/NIS configuration |
| **Performance** | Real-time IOPS, throughput, latency display |

### ⚠️ CLI Required

| Category | Operation | Reason |
|----------|-----------|--------|
| **EMS Webhook** | Destination, filter, notification rule setup | Not available in GUI |
| **FPolicy** | External engine, policy configuration | Not available in GUI (FSx for ONTAP) |
| **Advanced audit settings** | Detailed event filtering | GUI provides basic settings only |
| **S3 Access Point** | Bucket policy configuration | Use AWS CLI / Console |

### ❌ Not Available on FSx for ONTAP

| Feature | Reason |
|---------|--------|
| Node management | Managed by AWS (managed service) |
| Disk management | Managed by AWS (managed service) |
| Cluster configuration | Managed by AWS (managed service) |
| ONTAP upgrades | Performed via AWS Console |
| License management | Included in FSx service |

---

## 5.1 FSA Explorer: Folder Drill-Down and File Path Analysis

FSA Explorer enables **folder-level drill-down** to analyze file access patterns at any directory depth.

### Accessing FSA Explorer

1. System Manager → **Storage** → **Volumes** → Click target volume
2. Select **File system** tab
3. Click **Explorer** sub-tab
4. Ensure **Analytics enabled** toggle is ON

### Explorer Capabilities

| Feature | Description |
|---------|-------------|
| **Directory tree navigation** | Left panel shows folder hierarchy; click any folder to drill down |
| **File list with metadata** | Right panel shows files in selected directory with Name and Size |
| **Subdirectory/file count** | Displays correct count of subdirectories and files at each level |
| **Access history column** | Shows last access time for each file/directory |
| **Modify history column** | Shows last modification time |
| **Breadcrumb navigation** | Path bar shows current location (e.g., `/folder1/folder2/`) |

### Drill-Down Behavior (Verified)

When clicking a folder in the Explorer directory tree:

```
Root (/)
  └── folder1 (click)
        ├── text2.txt, text22.txt          ← Files in folder1
        ├── folder2 (click)
        │     ├── text3.txt, text33.txt    ← Files in folder2
        │     ├── folder3
        │     ├── folder4
        │     └── folder5
        ├── folder3
        ├── folder4
        └── folder5
```

**Verified behavior**:
- Clicking `folder1` shows: 4 subdirectories (folder2-5), 8 files (text2-5.txt + text22-55.txt)
- Clicking `folder2` shows: 3 subdirectories (folder3-5), 6 files (text3-5.txt + text33-55.txt)
- Directory and file counts update correctly at each level
- Current directory path is displayed in the breadcrumb bar

### CSV Export from Explorer

The Explorer view supports CSV download:
- Click the download icon (↓) in the Explorer toolbar
- CSV contains: file/directory name, size, access history, modify history
- This is a **point-in-time snapshot of the currently displayed view, NOT a time-series export**

> ⚠️ **Limitation**: Explorer CSV captures only what is currently visible. For long-term access history analysis, use audit logs (S3 → Athena). See [Decision Tree](decision-tree-management-monitoring.md) for the recommended architecture.

### Use Cases

| Use Case | Explorer Capability | Limitation |
|----------|-------------------|-----------|
| Identify inactive files | ✅ Access history column shows last access date | Requires `-atime-update` enabled |
| Verify folder structure | ✅ Full directory tree navigation | — |
| Count files per department folder | ✅ File/directory count at each level | — |
| Export file list for review | ✅ CSV download | Point-in-time only |
| Long-term access trend analysis | ❌ Not available | Use audit logs + Athena |

---

## 5.2 Alternative Implementation Options and Navigation

For each operation available in System Manager (via NetApp Console), this section maps alternative approaches using AWS-native services, third-party tools, or direct API access. Use this when NetApp Console setup is not desired, or when automation without GUI dependency is the goal.

### Audit Log Configuration and Delivery

| Approach | Tool/Service | Benefit | Link |
|----------|-------------|---------|------|
| System Manager GUI | Via NetApp Console | Intuitive, familiar to ops teams | This guide Section 2 |
| ONTAP CLI (SSH) | `vserver audit create` | No NetApp Console needed, reproducible | [Prerequisites — Enable Audit Logging](prerequisites.md#step-2-enable-fsx-ontap-audit-logging) |
| ONTAP REST API | `POST /api/protocols/audit` | IaC-friendly, fully automatable | [ONTAP REST API Reference](ontap-rest-api-reference.md) |
| Setup script | `ontap-audit-setup.sh` | Dry-run support, parameterized | [Script](../../shared/scripts/ontap-audit-setup.sh) |

#### Delivery options

| Method | Architecture | Cost Estimate | Link |
|--------|-------------|---------------|------|
| S3 AP + EventBridge + Lambda | AWS-native (this repo) | $2-8/month | [Prerequisites](prerequisites.md) |
| AWS DataSync | Periodic sync (5 min interval) | $5-15/month | [AWS DataSync docs](https://docs.aws.amazon.com/datasync/) |
| Kinesis Data Firehose | High-throughput delivery | Volume-dependent | [Vendor Comparison](vendor-comparison.md#architecture-pattern-comparison) |

### Quota (Capacity Limit) Configuration

| Approach | Tool/Service | Benefit | Link |
|----------|-------------|---------|------|
| System Manager GUI | Via NetApp Console | Intuitive Qtree/quota management | This guide Section 3 |
| ONTAP CLI | `quota policy rule create` | Scriptable | [NetApp Docs — Qtree Quotas](https://docs.netapp.com/us-en/ontap/volumes/manage-volumes-task.html) |
| ONTAP REST API | `POST /storage/quota/rules` | IaC integration, CI/CD pipeline | [ONTAP REST API Reference](ontap-rest-api-reference.md), [Quota setup script](../../shared/scripts/ontap-quota-setup.sh) |

### Capacity Monitoring and Alerting

| Approach | Granularity | Timeliness | Link |
|----------|------------|------------|------|
| **CloudWatch Alarms** | Volume level | 5 min interval | This guide Section 4.3 |
| **EMS Webhook → Lambda → SNS** | Qtree level (quota exceeded) | Real-time | This guide Section 4.2, [EMS Detection](ems-detection-capabilities.md) |
| **CloudWatch Events (EventBridge)** | Select EMS events | Minutes | This guide Section 4.4 |
| **NetApp Harvest + Grafana** | All metrics (300+) | 60 sec | [Metrics Collection Comparison](#10-metrics-collection-tools-comparison) |
| **OTel Collector** | Custom configuration | Configurable | [OTel Collector integration](../../integrations/otel-collector/) |
| **Datadog / Splunk / Elastic** | Audit logs + metrics | Vendor-dependent | [Vendor Comparison — Dashboard Integration](vendor-comparison.md#dashboard-integration--how-events-appear-in-each-vendor) |

### File Access Analysis (FSA Explorer Alternatives)

| Approach | Analysis Scope | Long-term Analysis | Link |
|----------|---------------|-------------------|------|
| **FSA Explorer (System Manager)** | Point-in-time file list, size, access date | ❌ | This guide Section 5.1 |
| **Audit logs + Amazon Athena** | Full access history (who/what/when) | ✅ | [Management/Monitoring Decision Tree](decision-tree-management-monitoring.md) |
| **S3 Inventory (via S3 AP)** | Object list, size | ✅ (daily) | [AWS S3 Inventory](https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-inventory.html) |
| **FPolicy + SIEM** | Real-time file operation tracking | ✅ | [FPolicy Architecture Patterns](fpolicy-production-architecture-patterns.md) |
| **Amazon Comprehend (PII scan)** | File content classification | ✅ | [Content Classification Scanner](content-classification-scanner.md) |

### Performance Monitoring

| Approach | Metric Scope | Custom Dashboards | Link |
|----------|-------------|-------------------|------|
| **System Manager** | Volume IOPS/throughput/latency | ❌ (screen only) | Via NetApp Console |
| **CloudWatch metrics** | FSx level (volume, FS) | ✅ | [AWS Docs — CloudWatch Metrics](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-cloudwatch.html) |
| **NetApp Harvest → Prometheus → Grafana** | All ONTAP metrics (300+) | ✅ | [Harvest GitHub](https://github.com/NetApp/harvest) |
| **OTel Collector → any backend** | Custom configuration | ✅ | [OTel Collector integration](../../integrations/otel-collector/) |

### Ransomware Protection (ARP + Automated Response)

| Approach | Detection | Auto-Containment | Link |
|----------|-----------|-----------------|------|
| **ARP/AI + this repo's automated response** | ONTAP ARP/AI (immediate, no learning period) | ✅ Lambda → ONTAP REST API | [Automated Response Guide](automated-response-guide.md) |
| **DII Storage Workload Security** | Built-in ML (per-user baselines) | ✅ Built-in | [Automated Response Guide — Comparison](automated-response-guide.md#comparison-this-approach-vs-dedicated-storage-security-products) |
| **CloudWatch Log Alarm → SNS → Lambda** | Log pattern detection | ✅ This repo | [CloudWatch Log Alarm Guide](cloudwatch-log-alarm.md) |
| **Datadog/Splunk ML + SNS** | SIEM-side ML detection | ✅ This repo | [Vendor Comparison — Auto-Containment](vendor-comparison.md#auto-containment-integration-pattern-all-vendors) |

### Decision Flowchart

```mermaid
flowchart TD
    A[What do you want to achieve?] --> B{Want GUI?}
    B -- Yes --> C[NetApp Console setup<br/>This guide Section 1]
    B -- No --> D{Want automation?}
    D -- Yes --> E{AWS-only?}
    E -- Yes --> F[CloudWatch + Lambda<br/>This repo templates]
    E -- No --> G[OTel Collector or<br/>Harvest + Grafana]
    D -- No --> H[ONTAP CLI/REST API<br/>Direct access]
```

---

## 6. Verification Checklist

### Phase 1: System Manager Access

- [ ] Confirm management endpoint IP
- [ ] Verify security group allows port 443
- [ ] Confirm browser access to `https://<management-ip>`
- [ ] Confirm `fsxadmin` login succeeds
- [ ] Verify dashboard displays correctly

### Phase 2: Audit Log Configuration

- [ ] Confirm audit log volume exists (create if needed)
- [ ] Enable auditing via Storage VMs → Settings → Audit
- [ ] Verify EVTX format logs are generated
- [ ] Confirm CIFS share access is recorded in logs

### Phase 3: Qtree Quota Configuration

- [ ] Create test Qtree
- [ ] Configure quota rules (soft: 80MB, hard: 100MB)
- [ ] Initialize quotas
- [ ] Verify EMS event is issued when soft limit is exceeded

### Phase 4: Capacity Monitoring & Notifications

- [ ] Configure CloudWatch alarm (volume capacity 80%)
- [ ] Configure EMS Webhook (quota exceeded)
- [ ] Set up SNS topic + email subscription
- [ ] Verify email notification on test data write

---

## 7. Troubleshooting

### Cannot Access System Manager

| Symptom | Cause | Resolution |
|---------|-------|-----------|
| Connection timeout | Security group not configured | Allow port 443 |
| Certificate error | Self-signed certificate | Add browser exception |
| Login failure | Password mismatch | Reset via AWS Console |
| Page not loading | Browser compatibility | Use latest Chrome/Firefox |

### Audit Logs Not Generated

| Symptom | Cause | Resolution |
|---------|-------|-----------|
| No log files created | Auditing disabled | Check with `vserver audit show` |
| Access not recorded | SACL not configured | Configure SACL in Windows |
| Logs are stale | Rotation settings | Check rotation size |

### Quotas Not Working

| Symptom | Cause | Resolution |
|---------|-------|-----------|
| Can write beyond limit | Quotas not initialized | Run Initialize Quotas |
| Usage shows 0 | Scan not complete | Wait a few minutes |
| No EMS event | Soft limit not set | Configure soft limit |

---

## 8. Security and Availability Considerations

### 8.1 Providing AWS Credentials to NetApp Console<!-- allow:naming -->

Security model when registering AWS credentials with NetApp Console<!-- allow:naming -->:

| Aspect | Details |
|--------|---------|
| **Auth method** | IAM Role AssumeRole (same trust model as Datadog AWS Integration) |
| **Data NetApp Console<!-- allow:naming --> accesses** | FSx for ONTAP metadata (file system ID, capacity, SVM list, etc.) |
| **Data NetApp Console<!-- allow:naming --> does NOT access** | File data, audit log contents, user data |
| **Least privilege recommendation** | `fsx:Describe*` + `ec2:Describe*` only (read-only) |

> **Comparison**: Datadog AWS Integration also assumes an IAM Role to collect metrics. NetApp Console<!-- allow:naming --> uses the same trust model. If granting read/write permissions, understand the scope of management operations enabled.

### 8.2 NetApp Console<!-- allow:naming --> Availability and Dependencies

| Component | During NetApp Console<!-- allow:naming --> Outage | Impact |
|-----------|------------------------------|--------|
| System Manager GUI | ❌ Unavailable | GUI management operations blocked |
| ONTAP CLI (SSH) | ✅ Unaffected | All ONTAP operations available |
| ONTAP REST API | ✅ Unaffected | Automation/scripts continue |
| Audit log delivery pipeline | ✅ Unaffected | Lambda uses S3 AP directly |
| EMS Webhook | ✅ Unaffected | ONTAP sends directly to API Gateway |

> **Conclusion**: NetApp Console<!-- allow:naming --> is a convenience layer for GUI access but is NOT in the critical path. This project's audit log delivery pipeline has zero dependency on NetApp Console<!-- allow:naming -->.

### 8.3 Link Lambda Permissions and Communication

The Link Lambda bridges NetApp Console<!-- allow:naming --> and the FSx for ONTAP management endpoint:

| Aspect | Details |
|--------|---------|
| **Communication targets** | NetApp Console<!-- allow:naming --> backend + FSx for ONTAP management IP |
| **Data sent** | ONTAP REST API responses (metadata, configuration) |
| **Data NOT sent** | File data, audit log contents |
| **Encryption** | All communication over HTTPS (TLS 1.2+) |

### 8.4 Data Shared via NSS Account

| Information | Purpose | Required |
|-------------|---------|----------|
| Email address | Account authentication | ✅ |
| Name / Company | Account identification | ✅ |
| FSx File System ID | Service association | ✅ |

> NSS account creation is free. Support case filing requires a paid contract, but System Manager usage does not.

---

## 9. Connecting GUI Configuration to Pipeline Delivery

Flow from System Manager GUI audit log configuration to this project's Lambda pipeline:

```
Setup phase (one-time): System Manager → Audit Enable → /vol/audit_logs
    ↓
Operations phase (automatic):
  ONTAP → audit log writes → S3 AP → Lambda → vendor delivery
```

> After enabling audit logs via GUI, deploy the CloudFormation template to start delivery. GUI configuration and pipeline deployment are independent.

---

## 10. Metrics Collection Tool Comparison

| Tool | Type | ONTAP Metrics Scope | Delivery Target |
|------|------|-------------------|-----------------|
| **NetApp Harvest** | NetApp-specific | All metrics (300+) | Prometheus/Grafana |
| **OTel Collector** | Vendor-neutral | Custom configuration | Any OTLP backend |
| **Grafana Alloy** | Grafana-native | Custom configuration | Grafana Cloud |
| **CloudWatch** | AWS-native | FSx-level only | CloudWatch |

**Selection guide**: Grafana ecosystem → Harvest, Vendor-neutral → OTel Collector, AWS-native → CloudWatch

---

## 11. CLI/REST API: The Recommended Automation Path

| Aspect | GUI (System Manager) | CLI/REST API |
|--------|---------------------|-------------|
| Initial setup | Intuitive | Scriptable, reproducible |
| During outages | Depends on NetApp Console<!-- allow:naming --> | Direct access (no dependency) |
| IaC integration | Not possible | CloudFormation compatible |

**Recommended**: Day 1 — CLI for initial setup (reproducible). Day 2+ — GUI for status checks.

---

## References

- [AWS Docs — FSx for ONTAP File Access Auditing](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html)
- [AWS Docs — FSx for ONTAP Monitoring](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring_overview.html)
- [AWS Docs — CloudWatch Metrics](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-cloudwatch.html)
- [NetApp Docs — ONTAP System Manager](https://docs.netapp.com/us-en/ontap/task_admin_manage_storage_system.html)
- [NetApp Docs — Qtree Quota Management](https://docs.netapp.com/us-en/ontap/volumes/manage-volumes-task.html)
- [This repository — EMS Event Sources Guide](event-sources.md)
- [This repository — Prerequisites Guide](prerequisites.md)
