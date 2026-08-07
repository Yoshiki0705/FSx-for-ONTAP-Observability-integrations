# Snapshot Remediation Setup (Datadog Workflow → ONTAP Snapshot)

This optional component lets a Datadog Workflow create an ONTAP snapshot for
evidence preservation once an analyst confirms a mass-deletion or ransomware
signal. It is a **containment action**, not part of log shipping — deploy it only
if you want Datadog to be able to act on your storage.

> **Scope note**: this creates a snapshot. It does not block the user or the
> client IP. For user/IP blocking see
> [automated-response-guide.md](../../../../docs/en/automated-response-guide.md).

## What gets deployed

| Resource | Purpose |
|----------|---------|
| Lambda `<stack>-snapshot` | Creates the snapshot via the ONTAP REST API |
| SQS DLQ | Captures remediation requests that failed after retries |
| CloudWatch Log Group | Audit trail, 365-day retention |
| Error alarm (threshold 0) | A single failed containment action pages you |
| DLQ alarm | A request that never executed at all |
| IAM role | Secrets Manager read + VPC ENI management only |

## Why this Lambda runs in a VPC (and the shipper does not)

This is the most common source of confusion in this repository:

| Function | Talks to | Network placement |
|----------|----------|-------------------|
| Audit log shipper | FSx for ONTAP S3 Access Point | **Outside** the VPC — Internet-origin S3 APs timed out from a VPC with only a Gateway Endpoint in our testing |
| Snapshot remediation | ONTAP management LIF (TCP 443) | **Inside** the VPC — the management LIF has no internet-facing path |

Do not copy `template.yaml`'s network configuration here. `VpcSubnetIds` and
`VpcSecurityGroupIds` are required parameters for this stack, not optional ones.

## Prerequisites

1. **ONTAP credentials in Secrets Manager**, as JSON:

   ```bash
   aws secretsmanager create-secret \
     --name fsxn-ontap-admin \
     --secret-string '{"username":"fsxadmin","password":"<password>"}' \
     --region ap-northeast-1
   ```

   The account needs permission to create snapshots on the target volume.

2. **Private subnets** with a route to the ONTAP management LIF on TCP 443.

3. **Secrets Manager reachable from those subnets** — either a NAT Gateway or an
   interface VPC endpoint for `com.amazonaws.<region>.secretsmanager`. Without
   this the function times out reading its own credentials.

4. **The ONTAP management IP**:

   ```bash
   aws fsx describe-file-systems \
     --file-system-ids fs-0123456789abcdef0 \
     --query 'FileSystems[0].OntapConfiguration.Endpoints.Management.IpAddresses' \
     --output text
   ```

## Step 1: Deploy

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export ONTAP_CREDENTIALS_SECRET_ARN="arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-ontap-admin-XXXXXX"
export VPC_SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export VPC_SECURITY_GROUP_IDS="sg-0123456789abcdef0"

# Recommended for production
export CA_CERT_PATH="/opt/certs/ontap-ca.pem"
export CA_CERT_LAYER_ARN="arn:aws:lambda:ap-northeast-1:123456789012:layer:ontap-ca:1"
export ALARM_TOPIC_ARN="arn:aws:sns:ap-northeast-1:123456789012:fsxn-alerts"

bash integrations/datadog/scripts/deploy-snapshot-remediation.sh
```

Use `--dry-run` to review the parameters without deploying.

### Parameter reference

| Parameter | Env var | Default | Notes |
|-----------|---------|---------|-------|
| `OntapManagementIp` | `ONTAP_MGMT_IP` | — | Required |
| `OntapCredentialsSecretArn` | `ONTAP_CREDENTIALS_SECRET_ARN` | — | Required, JSON `{username, password}` |
| `VpcSubnetIds` | `VPC_SUBNET_IDS` | — | Required, comma-separated |
| `VpcSecurityGroupIds` | `VPC_SECURITY_GROUP_IDS` | — | Required, comma-separated |
| `DefaultVolume` | `DEFAULT_VOLUME` | `''` | Fallback when the payload omits `volume_name` |
| `DefaultSvm` | `DEFAULT_SVM` | `''` | Fallback when the payload omits `svm_name` |
| `CooldownMinutes` | `COOLDOWN_MINUTES` | `15` | Minimum gap between snapshots on one volume |
| `OntapTimeoutSeconds` | `ONTAP_TIMEOUT_SECONDS` | `10` | Per-request ONTAP read timeout |
| `CaCertPath` | `CA_CERT_PATH` | `''` | Empty ⇒ `CERT_NONE` (PoC only) |
| `CaCertLayerArn` | `CA_CERT_LAYER_ARN` | `''` | Layer providing the CA cert |
| `InvokerRoleArn` | `INVOKER_ROLE_ARN` | `''` | Datadog AWS integration role, if invoking from a Workflow |
| `AlarmNotificationTopicArn` | `ALARM_TOPIC_ARN` | `''` | SNS topic for both alarms |
| `LogLevel` | `LOG_LEVEL` | `INFO` | |
| `LambdaTimeout` | — | `60` | Must exceed 3 × `OntapTimeoutSeconds` |

## Step 2: Test the invocation

This creates a **real snapshot** — use a non-production volume first.

```bash
aws lambda invoke \
  --function-name fsxn-datadog-snapshot-remediation-snapshot \
  --region ap-northeast-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"volume_name":"vol1","svm_name":"svm-prod","reason":"deploy test","user":"operator"}' \
  /dev/stdout
```

Expected response:

```json
{"statusCode": 200, "body": "{\"snapshot_name\": \"remediation_20260807_090000_deploy_test\", \"status\": \"created\", ...}"}
```

Confirm on ONTAP:

```bash
# From a host that can reach the management LIF
curl -sku fsxadmin "https://198.51.100.10/api/storage/volumes/<vol-uuid>/snapshots?name=remediation_*"
```

### Interpreting the response

| statusCode | Meaning | Action |
|-----------|---------|--------|
| 200 `status: created` | Snapshot created | Nothing |
| 200 `status: skipped` | Cooldown active | Expected during repeated triggers |
| 400 | `volume_name` / `svm_name` missing | Set them in the payload or as stack defaults |
| 403 | ONTAP rejected the credentials | Check the secret contents and account permissions |
| 404 | Volume not found in that SVM | Check the volume and SVM names |
| 500 | Misconfiguration or snapshot API error | Read the message; it names the missing variable |
| 504 | Management LIF unreachable | Check subnets, route table, and security group (TCP 443) |

## Step 3: Wire it into a Datadog Workflow

1. Datadog console → **Workflows** → **New Workflow**
2. Add an **AWS Lambda: Invoke function** action
3. Target the ARN from the stack output `SnapshotRemediationFunctionArn`
4. Payload — map the fields from the triggering monitor:

   ```json
   {
     "volume_name": "{{ Source.volume }}",
     "svm_name": "{{ Source.svm }}",
     "reason": "{{ Source.monitor_name }}",
     "user": "{{ Source.user }}"
   }
   ```

5. Add a **human approval step before** the Lambda action. This function takes an
   action against production storage; a fully automatic path can be triggered by
   a false positive.
6. Link the workflow to a monitor with an `@workflow-<name>` mention.

If the Workflow invokes the function using the Datadog AWS integration role, set
`InvokerRoleArn` to that role ARN so the resource-based permission exists.

## Behaviour to know about

**The cooldown fails open.** If the cooldown check cannot be evaluated (ONTAP
unreachable, or the snapshot list returns an error), the function creates the
snapshot anyway. During an active incident a redundant snapshot costs far less
than a missed one. The cooldown exists to prevent snapshot storms, not to gate
evidence preservation.

**Concurrency is capped at 1** (`ReservedConcurrentExecutions: 1`). Parallel
invocations would each pass the cooldown check before any snapshot existed,
defeating it.

**Snapshots consume volume capacity.** A remediation snapshot pins the blocks it
references. Review your snapshot retention policy before enabling automatic
triggers, or a repeatedly firing monitor can fill the volume.

## Cleanup

```bash
aws cloudformation delete-stack \
  --stack-name fsxn-datadog-snapshot-remediation \
  --region ap-northeast-1
```

Snapshots already created on ONTAP are **not** removed by stack deletion —
delete them separately once the investigation is closed.

## Related Documents

- [Setup Guide](setup-guide.md) — the audit log pipeline this complements
- [Production Checklist](production-checklist.md) — the remediation items
- [EMS / FPolicy Setup](ems-fpolicy-setup.md) — real-time detection sources
- [Automated Response Guide](../../../../docs/en/automated-response-guide.md) — user and IP blocking
