# Syslog VPC Endpoint Setup Guide — FSx for ONTAP Admin Audit Logs → CloudWatch Logs

🌐 [日本語](../ja/syslog-vpce-setup-guide.md) | **English** (this page)

> **Time required**: ~15 minutes (CloudFormation deploy + ONTAP configuration)
> **Prerequisite**: Running FSx for ONTAP file system
> **Template**: `shared/templates/syslog-vpce-cloudwatch.yaml`

---

## Overview

Ship FSx for ONTAP management activity audit logs (ONTAP CLI/API operations) directly to CloudWatch Logs — no EC2 syslog server required.

```
FSx for ONTAP (ONTAP log-forwarding)
    │ Syslog (TCP port 6514 or 1514)
    ▼
VPC Endpoint (com.amazonaws.{region}.syslog-logs)
    │ AWS PrivateLink
    ▼
CloudWatch Logs (/syslog/fsxn-admin-audit)
```

---

## Prerequisites

| Parameter | How to find | Example |
|-----------|-------------|---------|
| VPC ID | FSx Console → File system → Network | `vpc-0123456789abcdef0` |
| Subnet ID | Same AZ as FSx | `subnet-0123456789abcdef0` |
| VPC CIDR | VPC Console → Target VPC | `10.0.0.0/16` |
| FSx Management IP | FSx Console → Management endpoint | `198.51.100.72` |

---

## Step 1: Deploy CloudFormation Stack

```bash
aws cloudformation deploy \
  --template-file shared/templates/syslog-vpce-cloudwatch.yaml \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --parameter-overrides \
    VpcId=<YOUR_VPC_ID> \
    SubnetIds=<YOUR_SUBNET_ID> \
    VpcCidr=<YOUR_VPC_CIDR> \
    LogGroupName=/syslog/fsxn-admin-audit \
    LogRetentionDays=90 \
  --region ap-northeast-1 \
  --no-fail-on-empty-changeset
```

> **Template defect note:** Observed on 2026-10-09 and fixed since. Copies of the template taken from `main` before the fix fail this deploy with `ROLLBACK_COMPLETE`: EC2 rejects the security group description ("Invalid security group description"), because `GroupDescription` was a YAML folded scalar `>`, which keeps a trailing newline. The template now uses `>-`, the change the 2026-10-09 run deployed from a local copy, and `scripts/tests/test_cfn_security_group_description.py` fails if a description EC2 rejects comes back. If you deployed an older copy and it failed, note that the log group has `DeletionPolicy: Retain`, so the failed stack leaves `/syslog/fsxn-admin-audit` behind; delete it (`aws logs delete-log-group`) and the `ROLLBACK_COMPLETE` stack before you retry. A retry while that log group still exists was not tried. See the [2026-10-09 record](verification-results-cloudwatch-monitoring.md#terraform-log-alarm-module-run-on-2026-10-09).

Then retrieve the VPC Endpoint ENI IP:

```bash
VPCE_ID=$(aws cloudformation describe-stacks \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --query "Stacks[0].Outputs[?OutputKey=='VpcEndpointId'].OutputValue" \
  --output text --region ap-northeast-1)

ENI_ID=$(aws ec2 describe-vpc-endpoints --vpc-endpoint-ids $VPCE_ID \
  --query 'VpcEndpoints[0].NetworkInterfaceIds[0]' \
  --output text --region ap-northeast-1)

VPCE_IP=$(aws ec2 describe-network-interfaces --network-interface-ids $ENI_ID \
  --query 'NetworkInterfaces[0].PrivateIpAddress' \
  --output text --region ap-northeast-1)

echo "VPC Endpoint IP: $VPCE_IP"
```

---

## Step 2: Create Syslog Configuration

```bash
python3 shared/scripts/create-syslog-configuration.py \
  --vpce-id $VPCE_ID \
  --log-group-arn "arn:aws:logs:ap-northeast-1:$(aws sts get-caller-identity --query Account --output text):log-group:/syslog/fsxn-admin-audit" \
  --region ap-northeast-1
```

> **Note**: The script signs the request with SigV4 directly; it was written when the AWS CLI and boto3 had no syslog-configuration commands, and it returned HTTP 200 on 2026-10-09. AWS CLI 2.36.5 has `aws logs put-syslog-configuration`, `list-syslog-configurations` and `delete-syslog-configuration` (parameters `--log-group-identifier` and `--vpc-endpoint-id`). `list` and `delete` were run against AWS on 2026-10-09. `put` was not: its presence was checked only by calling it locally without arguments, which made the CLI ask for `--log-group-identifier`. Alternatively, use the AWS Console: CloudWatch → Logs → Syslog configurations → Create.

---

## Step 3: Configure ONTAP Log-Forwarding

> **CLI command naming**: ONTAP 9.11.1+ uses `security audit log-forwarding` (replacing the older `cluster log-forwarding`). Both refer to the same feature.

> **Port choice note:** The first command in each option creates the TLS destination on port 6514. In the 2026-10-09 run, that destination returned 201 and then delivered nothing, while a destination on port 1514 without TLS delivered its first line about 3 seconds after it was created (see the [TLS delivery note](#protocol-options)). Each option therefore also shows the 1514 form. Port 1514 carries the audit lines, which include the request bodies of change operations, unencrypted between ONTAP and the endpoint inside the VPC. Keeping 6514 avoids that, and you may then have to make TLS delivery work in your environment; using 1514 trades encryption in transit for a path that delivered in this run. That choice is a security decision for your environment. Whichever port you use, run [Check That Lines Arrive](#check-that-lines-arrive) before relying on the destination.

### Option A: REST API (recommended for automation)

```bash
curl -sk -u fsxadmin:<PASSWORD> \
  -X POST "https://<FSx-Management-IP>/api/security/audit/destinations?force=true" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "'$VPCE_IP'",
    "port": 6514,
    "protocol": "tcp_encrypted",
    "facility": "local7"
  }'
```

Validation alternative on port 1514 without TLS. This is the call that delivered on 2026-10-09:

```bash
curl -sk -u fsxadmin:<PASSWORD> \
  -X POST "https://<FSx-Management-IP>/api/security/audit/destinations?force=true" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "'$VPCE_IP'",
    "port": 1514,
    "protocol": "tcp_unencrypted",
    "facility": "local7"
  }'
```

### Option B: SSH + ONTAP CLI

```bash
ssh fsxadmin@<FSx-Management-IP>

FsxId*> security audit log-forwarding create \
  -destination <VPCE_IP> \
  -port 6514 \
  -protocol tcp-encrypted \
  -facility local7

FsxId*> security audit log-forwarding show
```

Validation alternative on port 1514 without TLS. The 2026-10-09 run used the REST form in Option A; this CLI form was not run then:

```bash
FsxId*> security audit log-forwarding create \
  -destination <VPCE_IP> \
  -port 1514 \
  -protocol tcp-unencrypted \
  -facility local7
```

### Check That Lines Arrive

Creating the destination is itself a change operation, so it writes audit lines of its own. In the 2026-10-09 run, the 1514 destination's own `Pending` line was in the stream `<VPCE_ID>_Syslog_<region>` about 3 seconds after the call, and the 6514 destination produced no stream in about 4 minutes. A stream that already exists does not show that the new destination delivers: the stream is named after the endpoint, so it remains from any earlier delivery, for example a re-run or a 6514 attempt followed by 1514. Search instead for the destination's own line, from a time taken just before you create it:

```bash
# Before creating the destination: note the time in milliseconds since the epoch
START_MS=$(( $(date +%s) * 1000 ))

# After creating it: look for its own audit line (REST or CLI form)
aws logs filter-log-events \
  --log-group-name /syslog/fsxn-admin-audit \
  --start-time "$START_MS" \
  --filter-pattern '?"audit/destinations" ?"log-forwarding create"' \
  --query 'events[].message' \
  --region ap-northeast-1
```

An empty list a few minutes after the destination was created means the new destination is not delivering; see [Logs Are Not Arriving](#logs-are-not-arriving). This search was written after the 2026-10-09 run and was not run in it; it looks for the line that run observed for the REST form. The CLI form's line was not captured.

### Protocol Options

| Protocol | Port | ONTAP parameter | Recommendation |
|----------|------|-----------------|----------------|
| TCP+TLS | 6514 | `tcp-encrypted` | Production, once you have confirmed that lines arrive (see the note below) |
| TCP Plaintext | 1514 | `tcp-unencrypted` | Validation, and the setting that delivered in the 2026-10-09 run |

> **TLS delivery note:** Observed on 2026-10-09. With the destination addressed by the endpoint IP, port 6514 and `tcp_encrypted`, the `POST` returned 201 and ONTAP set `verify_server: true`, but nothing reached the log group in about 4 minutes, and ONTAP wrote no EMS event about it. Addressing it by `syslog-logs.<region>.amazonaws.com` instead was rejected with "Cannot resolve the destination host", because the cluster could not resolve that name. Port 1514 with `tcp_unencrypted` delivered its first line about 3 seconds after it was created. The cause is inferred, not confirmed: ONTAP's certificate check cannot match an IP address against the endpoint certificate's name. After creating a destination, check that its own audit line arrives before relying on it ([Check That Lines Arrive](#check-that-lines-arrive)).

> **Production security hardening:** apply the following three measures in production.
>
> | Measure | Detail |
> |---------|--------|
> | Restrict the Security Group source to the FSx subnet CIDR | Narrow the template default VPC CIDR (`10.0.0.0/16`) to the subnet CIDR where FSx for ONTAP sits (e.g. `10.0.3.0/24`). |
> | Use Secrets Manager for credentials | Passing a password on the command line (`curl -u fsxadmin:<PASSWORD>`) is for verification only. In production, read it from Secrets Manager and pass it via an environment variable. |
> | Use TLS | Prefer `tcp-encrypted` (port 6514) in production and confirm that lines arrive (see the TLS delivery note above). Even inside PrivateLink, encryption is recommended as defense in depth. |

---

## Step 4: Verify

### Generate Logs (Perform an Admin Operation)

The destination `POST` in Step 3 is itself a change operation, so its own lines are the first to arrive. GET requests are audited only when GET auditing is on: on 2026-10-09, `GET /api/security/audit` returned `http: false`, and a GET wrote no line. Each change operation writes 2 lines, one ending `:: Pending` and one ending with the result (`:: Success:` or `:: Error: ...`). To produce lines on demand, create and delete a qtree on a test volume:

```bash
# Is GET auditing on? ("http": false means REST GETs write no audit line)
curl -sk -u fsxadmin:<PASSWORD> \
  "https://<FSx-Management-IP>/api/security/audit" --max-time 10

# A change operation is audited: create a qtree on a test volume
curl -sk -u fsxadmin:<PASSWORD> -X POST \
  "https://<FSx-Management-IP>/api/storage/qtrees" \
  -H "Content-Type: application/json" \
  -d '{"svm":{"name":"<SVM_NAME>"},"volume":{"name":"<TEST_VOLUME>"},"name":"audit_probe"}' \
  --max-time 10

# Delete it again: look up the volume UUID and qtree ID, then DELETE
curl -sk -u fsxadmin:<PASSWORD> \
  "https://<FSx-Management-IP>/api/storage/qtrees?name=audit_probe&fields=id,volume.uuid" \
  --max-time 10
curl -sk -u fsxadmin:<PASSWORD> -X DELETE \
  "https://<FSx-Management-IP>/api/storage/qtrees/<VOLUME_UUID>/<QTREE_ID>" \
  --max-time 10
```

### Verify in CloudWatch Logs

![CloudWatch Logs — Admin Audit Events](../screenshots/syslog-vpce/02-cloudwatch-log-events-ontap-audit.png)

```bash
# Check the log stream (appears within seconds to a minute)
aws logs describe-log-streams \
  --log-group-name /syslog/fsxn-admin-audit \
  --region ap-northeast-1

# Check the latest events
aws logs get-log-events \
  --log-group-name /syslog/fsxn-admin-audit \
  --log-stream-name "<VPCE_ID>_Syslog_<region>" \
  --limit 5 \
  --region ap-northeast-1
```

**Expected output** (from an actual verification run):

```
<190>Jun 28 02:06:40 FsxId0123456789abcdef0-01: ... [kern_audit:info:6392]
  ... FsxId0123456789abcdef0:http ... POST /api/storage/volumes ... :: Success
```

---

## Troubleshooting

### Logs Are Not Arriving

| Cause | How to check | Fix |
|-------|-------------|-----|
| Blocked by the security group | Look for REJECT in VPC Flow Logs | Add VPC CIDR → 1514/6514 to the SG |
| Syslog Configuration not created | No log stream exists | Run Step 2 |
| ONTAP destination not configured | `security audit log-forwarding show` | Run Step 3 |
| fsxadmin locked | REST API returns "User is not authorized" | Reset the password (below) |
| No ONTAP → VPCE connectivity | Fails without `force=true` | Check the SG, then re-create with `force=true` |
| 6514 `tcp-encrypted` destination created, nothing arrives | No log stream minutes after the destination was created; no EMS error | See the TLS delivery note in Step 3; port 1514 `tcp-unencrypted` delivered in the 2026-10-09 run |
| One operation missing, the next one present | The operation succeeded, but the log group has no line for it. Compare with ONTAP's `GET /api/security/audit/messages` | See [First Operation Lost After an Idle Connection](#first-operation-lost-after-an-idle-connection) |

### First Operation Lost After an Idle Connection

Observed 3 times on 2026-10-09 on one node of a single-HA-pair file system. After a node had sent nothing for about 4–5 minutes, the endpoint closed its connection (`SyslogConnectionsClosed` in `AWS/Logs`). The next operation, an `fsxadmin` qtree create, returned HTTP 201 and never reached the log group. In the first case, ONTAP's own `GET /api/security/audit/messages` listed that operation; it was not read in the other two. In two of the cases, an operation about 20 seconds later was delivered; in one of them, `SyslogConnectionsEstablished` rose in the same minute, and the metric was not read for the other. No EMS event recorded the loss, and the account had no `SyslogMessagesDropped` series. The mechanism is inferred from the metric timing, not confirmed.

What this means: a single operation on a quiet node can be missing from the log group, so an alarm that depends on one line can miss it. In the one case checked, ONTAP's own audit log (`GET /api/security/audit/messages`) still had the entry. A mitigation, for example a periodic write that keeps each node's connection active, was not tested.

### If the fsxadmin Account Is Locked

Repeated SSH password failures lock the account. Reset it through the AWS API:

```bash
aws fsx update-file-system \
  --file-system-id <FS_ID> \
  --ontap-configuration '{"FsxAdminPassword":"<NEW_PASSWORD>"}' \
  --region ap-northeast-1
```

> Wait about 30 seconds after the reset before connecting again.

### Security Group Caveat

> **Finding from verification:** The FSx for ONTAP node ENIs use an internal security group that is not the one you assigned to the file system. Specifying "inbound from the FSx security group" as the source on the VPC endpoint's security group therefore **does not work**. Use the **VPC CIDR** as the source instead.

---

## Cleanup

Remove every destination you created. A 1514 fallback destination left in place keeps pointing at the endpoint's IP after the endpoint is gone; on 2026-10-09 the cluster held two destinations (1514 and 6514) pointing at the IP of an endpoint that no longer existed, and the account held a syslog configuration for an endpoint that no longer existed.

```bash
# 1. List the ONTAP forwarding destinations, then remove each one you created
curl -sk -u fsxadmin:<PASSWORD> \
  "https://<FSx-Management-IP>/api/security/audit/destinations" \
  --max-time 10
curl -sk -u fsxadmin:<PASSWORD> \
  -X DELETE "https://<FSx-Management-IP>/api/security/audit/destinations/<VPCE_IP>/6514" \
  --max-time 10
curl -sk -u fsxadmin:<PASSWORD> \
  -X DELETE "https://<FSx-Management-IP>/api/security/audit/destinations/<VPCE_IP>/1514" \
  --max-time 10

# 2. Delete the syslog configuration
aws logs delete-syslog-configuration \
  --log-group-identifier "<LOG_GROUP_ARN>" \
  --vpc-endpoint-id $VPCE_ID \
  --region ap-northeast-1

# 3. Delete the CloudFormation stack
aws cloudformation delete-stack \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --region ap-northeast-1

# 4. Delete the log group by hand. The stack sets DeletionPolicy: Retain on it,
#    so deleting the stack deliberately leaves the audit history in place.
aws logs delete-log-group \
  --log-group-name /syslog/fsxn-admin-audit \
  --region ap-northeast-1
```

---

## Next Steps

### Operational Monitoring (Recommended)

Configure these CloudWatch metrics and alarms to monitor the health of the syslog pipeline itself:

```bash
# Alarm on the SyslogMessagesDropped metric
aws cloudwatch put-metric-alarm \
  --alarm-name "FSx-ONTAP-SyslogDropped" \
  --metric-name SyslogMessagesDropped \
  --namespace AWS/Logs \
  --statistic Sum \
  --period 300 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --dimensions Name=LogGroupName,Value=/syslog/fsxn-admin-audit \
  --alarm-actions <SNS_TOPIC_ARN> \
  --region ap-northeast-1
```

| Metric | Meaning | Suggested alarm threshold |
|--------|---------|--------------------------|
| `SyslogMessagesDropped` | Messages dropped because delivery failed | > 0 (5 min) |
| `IncomingLogEvents` | Received log events | < 1 (1 hour) detects "logs stopped arriving" |

> **Syslog metrics note:** Observed on 2026-10-09. During that run, `AWS/Logs` had `SyslogConnectionsEstablished` and `SyslogConnectionsClosed` with no dimension, and `SyslogMessagesReceived` per log group. There was no `SyslogMessagesDropped` series, including at the 3 losses described in [First Operation Lost After an Idle Connection](#first-operation-lost-after-an-idle-connection), so the alarm above would not have caught them.

> **Tip:** The ONTAP fsx-control-plane runs periodic access checks, so logs normally arrive continuously for the cluster as a whole. That does not hold per node: on 2026-10-09 one node sent nothing for about 4–5 minutes three times, and the first operation after each quiet period was lost ([First Operation Lost After an Idle Connection](#first-operation-lost-after-an-idle-connection)). More than an hour with no logs at all suggests a problem with the VPCE connection or the ONTAP configuration.

### Other Next Steps

| Capability | Use |
|------------|-----|
| CloudWatch Alarms | Detect specific operations (privilege escalation, user creation) with metric filters, for example with the Terraform module [`terraform/fsxn-log-alarm/`](../../terraform/fsxn-log-alarm/README.md); check its verification status before using the default patterns |
| Subscription Filter | CloudWatch Logs → Lambda → Datadog/Splunk/SIEM for secondary delivery |
| S3 Export | Export to S3 for long-term retention (can transition to Glacier) |
| CloudWatch Logs Insights | Analysis queries over admin operations |

### CloudWatch Logs Insights Query Examples

```sql
-- Detect privilege escalation operations
fields @timestamp, @message
| filter @message like /set -privilege/
| sort @timestamp desc
| limit 20

-- Success/failure breakdown of REST API operations
fields @timestamp, @message
| filter @message like /POST|GET|PATCH|DELETE/
| parse @message "* :: *" as operation, result
| stats count() by result
```

---

## Related Documents

- [Architecture Evolution — Syslog VPCE](architecture-evolution-syslog-vpce.md)
- [Event Sources Guide](event-sources.md)
- [AWS Docs: Syslog ingestion](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Syslog.html)
- [AWS Docs: Setting up syslog ingestion](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Syslog_Setup.html)
- [NetApp: ONTAP audit destinations](https://docs.netapp.com/us-en/ontap/system-admin/forward-command-history-log-file-destination-task.html)
- [Classmethod: FSx for ONTAP admin audit logs to CW Logs](https://dev.classmethod.jp/articles/amazon-fsx-for-netapp-ontap-security-audit-log-syslog-to-cw-logs/)
