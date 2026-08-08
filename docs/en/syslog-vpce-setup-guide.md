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
| VPC ID | FSx Console → File system → Network | `vpc-0ae01826f906191af` |
| Subnet ID | Same AZ as FSx | `subnet-0e36804c7fbc819a6` |
| VPC CIDR | VPC Console → Target VPC | `10.0.0.0/16` |
| FSx Management IP | FSx Console → Management endpoint | `10.0.3.72` |

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

> **Note**: As of June 2026, AWS CLI/boto3 does not have `put-syslog-configuration`. This script uses raw SigV4 signing. Alternatively, use the AWS Console: CloudWatch → Logs → Syslog configurations → Create.

---

## Step 3: Configure ONTAP Log-Forwarding

> **CLI command naming**: ONTAP 9.11.1+ uses `security audit log-forwarding` (replacing the older `cluster log-forwarding`). Both refer to the same feature.

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

### Protocol Options

| Protocol | Port | ONTAP parameter | Recommendation |
|----------|------|-----------------|----------------|
| TCP+TLS | 6514 | `tcp-encrypted` | **Production (recommended)** |
| TCP Plaintext | 1514 | `tcp-unencrypted` | Validation fallback only |

> **Production security hardening**:
> - Restrict Security Group source to FSx subnet CIDR (not full VPC CIDR)
> - Use Secrets Manager for credentials (not inline `curl -u`)
> - Always use `tcp-encrypted` (port 6514) in production

---

## Step 4: Verify

### Generate Logs (Perform an Admin Operation)

```bash
# Any REST API operation produces an audit log entry
curl -sk -u fsxadmin:<PASSWORD> \
  https://<FSx-Management-IP>/api/storage/volumes?fields=name \
  --max-time 10 > /dev/null
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
<190>Jun 28 02:06:40 FsxId09ffe72a3b2b7dbbd-01: ... [kern_audit:info:6392]
  ... FsxId09ffe72a3b2b7dbbd:http ... POST /api/storage/volumes ... :: Success
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

> **Finding from verification**
>
> The FSx for ONTAP node ENIs use an internal security group that is not the one you assigned to the file system. Specifying "inbound from the FSx security group" as the source on the VPC endpoint's security group therefore **does not work**. Use the **VPC CIDR** as the source instead.

---

## Cleanup

```bash
# 1. Remove the ONTAP forwarding destination
curl -sk -u fsxadmin:<PASSWORD> \
  -X DELETE "https://<FSx-Management-IP>/api/security/audit/destinations/<VPCE_IP>/6514" \
  --max-time 10

# 2. Delete the CloudFormation stack
aws cloudformation delete-stack \
  --stack-name fsxn-syslog-vpce-admin-audit \
  --region ap-northeast-1

# 3. Delete the log group by hand. The stack sets DeletionPolicy: Retain on it,
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

> **Tip**
>
> The ONTAP fsx-control-plane runs periodic access checks, so logs normally arrive continuously. More than an hour with no logs suggests a problem with the VPCE connection or the ONTAP configuration.

### Other Next Steps

- **CloudWatch Alarms** — detect specific operations (privilege escalation, user creation) with metric filters
- **Subscription Filter** — CloudWatch Logs → Lambda → Datadog/Splunk/SIEM for secondary delivery
- **S3 Export** — export to S3 for long-term retention (can transition to Glacier)
- **CloudWatch Logs Insights** — analysis queries over admin operations

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
