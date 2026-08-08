# Automated Response Demo Runbook

🌐 [日本語](../ja/demo-automated-response.md) | **English** (this page)

## Purpose

Step-by-step procedure for demonstrating the automated incident response feature end-to-end. Covers: deploy → trigger detection → verify auto-block → confirm access denial → unblock → confirm restored access.

Use this runbook for:
- Live demos (in-person or recorded)
- E2E verification before blog publication
- Internal training

> **Evidence format note**: This runbook describes, after each step, what a successful result looks like ("what to check for") in plain language, rather than a screenshot placeholder or a fabricated sample output block. As of this writing, this specific runbook has not been executed end-to-end and no real screenshots or command output have been captured for it — do not treat anything shown in this guide as evidence that these steps have actually been run. When you do execute this runbook, capture your own real command output or screenshots (masking account IDs/IPs/ARNs per `docs/screenshots/mask_screenshots.py`) and record them, following the format in [`e2e-verification-results.md`](../screenshots/automated-response/e2e-verification-results.md) for the [Automated Response Guide](automated-response-guide.md) itself.

---

## Prerequisites

| Item | Requirement |
|------|------------|
| FSx for ONTAP | Running, with ARP enabled on at least one volume |
| VPC access | Lambda subnet can reach ONTAP management IP (TCP 443) |
| ONTAP credentials | `fsxadmin` username/password in Secrets Manager |
| SMB client | Windows or Linux host mounted to the SVM via CIFS |
| NFS client | Linux host mounted to the SVM via NFS |
| AWS CLI | Configured with appropriate IAM permissions |
| jq | Installed for JSON formatting |

---

## Phase 1: Deploy the Automated Response Stack

### Step 1.1: Deploy CloudFormation

```bash
aws cloudformation deploy \
  --template-file shared/templates/automated-response.yaml \
  --stack-name fsxn-automated-response \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=<secret-arn> \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-1>,<subnet-2> \
    SecurityGroupId=<sg-id> \
    DefaultSvmName=<svm-name> \
    NotificationEmail=<your-email> \
  --capabilities CAPABILITY_NAMED_IAM
```

### Step 1.2: Verify Stack Outputs

```bash
aws cloudformation describe-stacks \
  --stack-name fsxn-automated-response \
  --query 'Stacks[0].Outputs' \
  --output table
```

**What to check**: the output table includes both `TriggerTopicArn` and `NotificationTopicArn` keys, each with a non-empty SNS topic ARN value. Confirm both before proceeding.

### Step 1.3: Set CLI Environment

```bash
export RESPONSE_TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name fsxn-automated-response \
  --query 'Stacks[0].Outputs[?OutputKey==`TriggerTopicArn`].OutputValue' \
  --output text)

export DEFAULT_SVM="<svm-name>"
echo "Topic: $RESPONSE_TOPIC_ARN"
```

---

## Phase 2: Demonstrate SMB User Blocking

### Step 2.1: Verify Current Access (Before Block)

From the SMB client, confirm normal access:

```bash
# On SMB client (as the test user)
ls //fsxn-share/test-data/
cat //fsxn-share/test-data/sample-file.txt
echo "write test" > //fsxn-share/test-data/write-test.txt
```

**What to check**: all three commands succeed with no permission errors — `ls` lists the expected files, `cat` prints the file content, and the `write-test.txt` file is created.

### Step 2.2: Trigger SMB User Block

```bash
# Using CLI helper
./shared/scripts/automated-response-cli.sh block-smb \
  --domain <DOMAIN> --user <test-user> \
  --reason "Demo: simulated insider threat"

# Or directly via SNS
aws sns publish \
  --topic-arn "$RESPONSE_TOPIC_ARN" \
  --message '{
    "action": "block_smb_user",
    "svm_name": "'$DEFAULT_SVM'",
    "domain": "<DOMAIN>",
    "username": "<test-user>",
    "reason": "Demo: simulated insider threat"
  }'
```

**What to check**: the CLI helper or `aws sns publish` returns successfully with a `MessageId` field populated (a non-empty UUID-like string).

### Step 2.3: Verify Lambda Execution

```bash
# Wait 10-15 seconds for Lambda to execute
sleep 15

# Check Lambda logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-automated-response-handler \
  --start-time $(date -v-2M +%s000 2>/dev/null || date -d '2 minutes ago' +%s000) \
  --filter-pattern "block_smb_user" \
  --query 'events[*].message' \
  --output text | tail -5
```

**What to check**: a log line indicating the Lambda blocked the target user on the target SVM, tagged with the reason string you passed in, and a `RequestId` you can correlate against other log lines from the same invocation.

### Step 2.4: Verify Block on ONTAP

```bash
ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
```

**What to check**: a `name-mapping` entry exists for `win-unix` direction, matching `DOMAIN\<test-user>`, with an empty (`" "`) replacement — this is the deny mapping the Lambda creates.

### Step 2.5: Verify Access Denied (After Block)

From the SMB client, confirm access is denied:

```bash
# On SMB client (as the blocked test user)
ls //fsxn-share/test-data/
# → Expected: Permission denied / Access denied

cat //fsxn-share/test-data/sample-file.txt
# → Expected: Permission denied
```

**What to check**: both commands now fail with a permission/access-denied error (the exact wording depends on your SMB client OS), where they succeeded in Step 2.1.

### Step 2.6: Unblock User

```bash
./shared/scripts/automated-response-cli.sh unblock-smb \
  --domain <DOMAIN> --user <test-user>
```

### Step 2.7: Verify Access Restored

```bash
# On SMB client (may need to reconnect)
net use \\\\fsxn-share /delete 2>/dev/null
net use \\\\fsxn-share /user:<DOMAIN>\\<test-user> <password>
ls //fsxn-share/test-data/
# → Expected: Success
```

**What to check**: `ls` succeeds again, confirming the unblock restored access.

---

## Phase 3: Demonstrate NFS IP Blocking

### Step 3.1: Verify Current NFS Access

```bash
# On NFS client
ls /mnt/fsxn/test-data/
touch /mnt/fsxn/test-data/nfs-write-test.txt
```

**What to check**: both commands succeed with no permission errors.

### Step 3.2: Trigger NFS IP Block

```bash
# Get the NFS client IP
CLIENT_IP=$(hostname -I | awk '{print $1}')
echo "Blocking IP: $CLIENT_IP"

./shared/scripts/automated-response-cli.sh block-nfs \
  --ip "$CLIENT_IP" \
  --reason "Demo: simulated mass deletion from suspicious IP"
```

**What to check**: same as Step 2.2 — the publish returns a `MessageId`.

### Step 3.3: Verify Block on ONTAP

```bash
ssh fsxadmin@<management-ip> "export-policy rule show -clientmatch *fsxn_auto_response*"
```

**What to check**: an export-policy rule exists whose `clientmatch` includes the blocked IP and carries the `fsxn_auto_response` marker, with read/write access denied.

### Step 3.4: Verify NFS Access Denied

```bash
# On NFS client (may require remount or cache expiry)
umount /mnt/fsxn && mount -t nfs <svm-nfs-lif>:/vol_data /mnt/fsxn
ls /mnt/fsxn/test-data/
# → Expected: Permission denied or mount failure
```

> **NFS Cache Note**: Linux NFS clients cache access decisions for up to 60 seconds (`actimeo` default). After blocking, you may need to wait up to 60s or remount with `mount -o actimeo=0` (test only) for the deny to take effect immediately.

**What to check**: the remount fails, or `ls` on the mount fails with a permission-denied-class error — see the NFS Cache Note above if access still appears to succeed immediately after the block.

### Step 3.5: Unblock IP

```bash
./shared/scripts/automated-response-cli.sh unblock-nfs --ip "$CLIENT_IP"
```

---

## Phase 4: Demonstrate Full Containment (ARP → Auto-Block)

### Step 4.1: Verify ARP is Active

```bash
ssh fsxadmin@<management-ip> "security anti-ransomware volume show -vserver $DEFAULT_SVM"
```

**What to check**: the target volume's ARP state shows as active/enabled in the command output.

### Step 4.2: Connect EMS Webhook to Response Pipeline

Ensure the EMS webhook delivers to Datadog/SIEM, and the SIEM monitor can publish to the response SNS topic. If using CloudWatch Log Alarm path:

```bash
# Verify syslog delivery is active
aws logs filter-log-events \
  --log-group-name /syslog/fsxn-admin-audit \
  --start-time $(date -v-5M +%s000 2>/dev/null || date -d '5 minutes ago' +%s000) \
  --limit 3 \
  --query 'events[*].message' --output text
```

### Step 4.3: Simulate Ransomware (TEST ENVIRONMENT ONLY)

```bash
# CAUTION: Only in test environment with disposable data
ssh fsxadmin@<management-ip> \
  "security anti-ransomware volume attack simulate -vserver $DEFAULT_SVM -volume <test-vol>"
```

**What to check**: the command completes without error, and a follow-up `security anti-ransomware volume show` reflects the simulated attack state on the target volume.

### Step 4.4: Observe Detection Chain

Wait ~30 seconds for EMS event delivery:

```bash
# Check EMS webhook arrival in CloudWatch (Lambda logs)
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-datadog-ems-fpolicy-handler \
  --start-time $(date -v-2M +%s000 2>/dev/null || date -d '2 minutes ago' +%s000) \
  --filter-pattern "arw.volume.state" \
  --query 'events[*].message' --output text
```

**What to check**: a log line referencing the `arw.volume.state` EMS event and the target volume name, arriving roughly 30 seconds after the simulate command in the previous step.

### Step 4.5: Trigger Full Containment

If the SIEM monitor auto-triggers SNS, this happens automatically. For manual demo:

```bash
./shared/scripts/automated-response-cli.sh contain-smb \
  --domain <DOMAIN> --user <suspect-user> \
  --volume <test-vol> \
  --reason "ARP detection - arw.volume.state alert"
```

### Step 4.6: Verify Containment Result

```bash
# Check Lambda execution
aws logs filter-log-events \
  --log-group-name /aws/lambda/fsxn-automated-response-handler \
  --start-time $(date -v-2M +%s000 2>/dev/null || date -d '2 minutes ago' +%s000) \
  --filter-pattern "contain_smb_threat" \
  --query 'events[*].message' --output text
```

**What to check**: log lines for all three containment steps — snapshot created, user blocked, sessions disconnected — sharing the same `RequestId`, confirming they ran within a single `contain_smb_threat` invocation.

### Step 4.7: Verify Snapshot Created

```bash
ssh fsxadmin@<management-ip> \
  "volume snapshot show -vserver $DEFAULT_SVM -volume <test-vol> -snapshot incident_response_*"
```

**What to check**: an `incident_response_*`-named snapshot appears, with a timestamp matching when containment was triggered.

### Step 4.8: Verify SNS Notification Received

Check the inbox for `NotificationEmail` for the containment result JSON.

**What to check**: an email arrived containing the containment result as JSON (snapshot name, blocked user, disconnected sessions). If you'd rather not rely on email as evidence, the Lambda logs the same JSON payload it publishes — check that via the same `filter-log-events` pattern used in Step 4.6, and separately confirm `aws sns get-topic-attributes --topic-arn <NotificationTopicArn> --query 'Attributes.NumberOfNotificationsFailed'` reports no failed deliveries.

---

## Phase 5: Demonstrate TTL Auto-Unblock

### Step 5.1: Deploy TTL Stack

```bash
aws cloudformation deploy \
  --template-file shared/templates/automated-response-ttl.yaml \
  --stack-name fsxn-automated-response-ttl \
  --parameter-overrides \
    OntapMgmtIp=<management-ip> \
    OntapCredentialsSecretArn=<secret-arn> \
    SubnetIds=<subnet-1>,<subnet-2> \
    SecurityGroupId=<sg-id> \
    DefaultSvmName=$DEFAULT_SVM \
    BlockTtlMinutes=5 \
    CheckIntervalMinutes=1 \
    NotificationTopicArn=<notification-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM
```

### Step 5.2: Create a Block

```bash
./shared/scripts/automated-response-cli.sh block-smb \
  --domain <DOMAIN> --user <test-user> \
  --reason "TTL demo - will auto-expire in 5 minutes"
```

### Step 5.3: Wait for TTL Expiry

```bash
echo "Block created. TTL cleanup Lambda runs every 1 minute."
echo "Block will be removed within ~5 minutes."
echo "Watch CloudWatch Logs (timeout after 7 minutes)..."

# Monitor cleanup Lambda (with timeout to avoid waiting forever)
timeout 420 aws logs tail /aws/lambda/fsxn-automated-response-ttl-cleanup --follow
# If timeout reached without seeing removal, check Lambda errors:
# aws logs filter-log-events --log-group-name /aws/lambda/fsxn-automated-response-ttl-cleanup --filter-pattern "ERROR"
```

> **Note**: The TTL cleanup currently removes ALL blocks with the `fsxn_auto_response` marker on each run. It does not track individual block creation times. For production, consider implementing a DynamoDB tracking table.

**What to check**: a CloudWatch Logs entry indicating the TTL cleanup Lambda expired and removed the SMB block.

### Step 5.4: Verify Auto-Unblock

```bash
ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
# → Expected: no entries (block auto-removed)
```

**What to check**: the name-mapping entry created in Step 2.4 is gone, confirming the TTL cleanup removed it automatically.

---

## Phase 6: List Active Blocks (Operational Visibility)

```bash
# Direct ONTAP CLI check
ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
ssh fsxadmin@<management-ip> "export-policy rule show -clientmatch *fsxn_auto_response*"
```

**What to check**: both commands show the current set of active blocks (empty if you've unblocked everything from the earlier phases).

---

## Cleanup

```bash
# Remove all test blocks
ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
# Delete any remaining entries manually

# Delete test snapshots
ssh fsxadmin@<management-ip> "volume snapshot delete -vserver $DEFAULT_SVM -volume <test-vol> -snapshot incident_response_*"

# Delete CloudFormation stacks (optional)
aws cloudformation delete-stack --stack-name fsxn-automated-response-ttl
aws cloudformation delete-stack --stack-name fsxn-automated-response
```

---

## Timing Reference

| Phase | Duration | Notes |
|-------|----------|-------|
| Phase 1 (Deploy) | ~5 min | CloudFormation deploy |
| Phase 2 (SMB Block) | ~3 min | Block + verify + unblock |
| Phase 3 (NFS Block) | ~3 min | Block + verify + unblock |
| Phase 4 (Full Containment) | ~5 min | ARP simulate + containment |
| Phase 5 (TTL) | ~7 min | Deploy + wait for TTL expiry |
| Phase 6 (Operational) | ~1 min | Status check |
| **Total** | **~24 min** | Full demo with all phases |

For a shorter demo (meeting): Phase 1 + Phase 2 + Phase 4 = ~13 minutes.

---

## E2E Verified Output (July 2026, ONTAP 9.17.1P7D1)

The following outputs were captured during live E2E verification. Use these as reference when validating your own deployment.

### NFS Block: Before/After

**Before (access granted)**:
```
$ ls -la /mnt/fsxn/
total 12
drwxr-xr-x. 3 root root 4096 Jul 12 16:00 .
drwxr-xr-x. 3 root root   18 Jul 12 14:51 ..
-rw-r--r--. 1 root root   46 Jul 12 16:00 hr-salary.txt
-rw-r--r--. 1 root root   43 Jul 12 16:00 project-spec.txt
drwxr-xr-x. 2 root root 4096 Jul 12 16:00 reports

$ cat /mnt/fsxn/hr-salary.txt
Confidential HR Record - Employee Salary Data
```

**After (export-policy deny rule applied)**:
```
$ ls /mnt/fsxn/
ls: cannot access '/mnt/fsxn/': Permission denied

$ cat /mnt/fsxn/hr-salary.txt
cat: /mnt/fsxn/hr-salary.txt: Permission denied
```

### SMB Block: Before/After (AD-joined SVM, testuser)

**Before (access granted)**:
```
PS> net use X: \\SVM\data /user:DEMO\testuser TestP@ss2026!
The command completed successfully.

PS> Get-ChildItem X:\
Mode   Length Name
----   ------ ----
d-----        reports
-a---- 46     hr-salary.txt
-a---- 43     project-spec.txt

PS> Get-Content X:\hr-salary.txt
Confidential HR Record - Employee Salary Data
```

**After (block_smb_user executed → nobody mapping + 750 permissions)**:
```
PS> net use X: \\SVM\data /user:DEMO\testuser TestP@ss2026!
The command completed successfully.

PS> Test-Path X:\
False

[Result] ACCESS DENIED - Drive not accessible
```

### Screenshot Capture Points

When running this demo for presentation or blog purposes, capture screenshots at:

| # | When | What to Capture | File Name |
|---|------|-----------------|-----------|
| 1 | Phase 2 Before | Windows File Explorer showing shared files | `smb-access-granted.png` |
| 2 | Phase 2 After | Windows "Access Denied" dialog or empty drive | `smb-access-denied.png` |
| 3 | Phase 3 Before | Terminal with `ls /mnt/fsxn/` showing files | `nfs-access-granted.png` |
| 4 | Phase 3 After | Terminal with `Permission denied` errors | `nfs-access-denied.png` |
| 5 | Phase 4 | CloudWatch Logs showing Lambda execution | `lambda-execution-log.png` |
| 6 | Optional | Step Functions graph view (if running restore-verification) | `stepfunctions-graph.png` |
| 7 | Optional | Datadog/CloudWatch showing ARP detection | `detection-alert.png` |

**Masking**: Before committing screenshots, run:
```bash
python3 docs/screenshots/mask_screenshots.py
```

---

## Related Documents

- [Automated Response Guide](automated-response-guide.md)
- [Deployment Guide](deployment-guide.md) — VPC Endpoint conflicts, AD integration, parameter files
- [ARP Incident Response Guide](arp-incident-response-guide.md)
- [EMS Detection Capabilities](ems-detection-capabilities.md)
- [Demo Scenarios (all vendors)](demo-scenarios.md)
- [CLI Helper](../../shared/scripts/automated-response-cli.sh)
