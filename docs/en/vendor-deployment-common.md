# Deploying a vendor integration

🌐 [日本語](../ja/vendor-deployment-common.md) | **English**

> The steps that are identical for every vendor. Each vendor's `setup-guide.md`
> covers only what is specific to that vendor and links here for the rest.

## The template alone is not functional

Every vendor's `template.yaml` ships a **placeholder** Lambda:

```yaml
Code:
  ZipFile: "def lambda_handler(e,c): raise NotImplementedError"
```

CloudFormation cannot inline a multi-hundred-line handler, so the real code must
be uploaded separately. If you run `aws cloudformation deploy` and stop there,
you get a stack that looks healthy, a scheduler that fires on time, and a Lambda
that raises `NotImplementedError` on every invocation. No telemetry is delivered
and nothing says so unless you have wired up the alarms.

**Use `scripts/deploy.sh`.** It deploys the stack and uploads the handler:

```bash
bash integrations/<vendor>/scripts/deploy.sh
```

Run it with `--help` for the environment variables it expects. First run takes
3-5 minutes, almost all of it CloudFormation creating the IAM role, Lambda,
scheduler and alarms. Re-runs of an unchanged stack finish in seconds.

`integrations/otel-collector` is the exception: its template takes `S3Bucket` /
`S3Key` instead of an inline placeholder, so the code must be packaged and
uploaded to S3 first. Its guide covers this.

### If you deploy CloudFormation by hand

Upload the handler afterwards, or the stack stays inert:

```bash
cd integrations/<vendor>/lambda

# Bundle the shared ONTAP audit parser alongside the handler. Without it the
# handler falls back to JSON-only parsing and every XML/EVTX audit log — which
# is every real ONTAP audit log — arrives without parsed fields.
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name <stack-name>-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name <stack-name>-shipper \
  --region ap-northeast-1
```

`scripts/verify.sh` detects a forgotten upload by inspecting the deployed code
size, so run it after deploying either way.

## Values to gather before you start

| Value | How to get it |
|-------|---------------|
| FSx for ONTAP S3 Access Point ARN | `aws fsx describe-s3-access-point-attachments --names <ap-name> --query 'S3AccessPointAttachments[0].S3AccessPoint.ResourceARN'` |
| Audit log S3 bucket name | Output of the [prerequisites stack](prerequisites.md) |
| Vendor credential secret ARN | `aws secretsmanager create-secret ...` output, or `describe-secret` |
| AWS account ID | `aws sts get-caller-identity --query Account --output text` |
| Region | Must match the FSx file system's region |

## Access point network origin and Lambda placement

The origin is fixed at creation. Getting this wrong is the most common cause of a
failed first deployment.

| Lambda placement | Internet-origin AP | VPC-origin AP |
|-----------------|-------------------|---------------|
| Outside VPC (default) | ✅ Works | ❌ No route |
| In VPC + S3 Gateway Endpoint only | ⚠️ Timed out in our testing | ✅ Works |
| In VPC + NAT Gateway | ✅ Works | ✅ Works |

Check an existing access point before choosing:

```bash
aws s3control get-access-point \
  --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --name <ap-name> --region ap-northeast-1 \
  --query '{Origin:NetworkOrigin,Vpc:VpcConfiguration.VpcId}'
```

A VPC-origin access point rejects requests from outside its VPC with
`AccessDenied ... explicit deny in a resource-based policy`, even when no access
point policy exists. The wording points at IAM; the cause is the network origin.

> **AD-joined SVM note**: if the SVM has CIFS enabled, **every** S3 access point
> data operation requires the AD domain controllers to be reachable from the SVM.
> A successful `HeadBucket` combined with `AccessDenied` on `ListObjectsV2` is the
> signature of unreachable AD DCs, not an IAM or policy problem.

## Alarm notifications

Every stack creates CloudWatch alarms for Lambda errors and dead-letter queue
depth. By default they have **no notification action** — visible in the console,
but they page nobody.

Pass an SNS topic to make them actionable:

```bash
export ALARM_TOPIC_ARN="arn:aws:sns:ap-northeast-1:123456789012:fsxn-alerts"
bash integrations/<vendor>/scripts/deploy.sh
```

Deploying by hand, the equivalent is
`--parameter-overrides AlarmNotificationTopicArn=arn:aws:sns:...`.

A dead-letter queue alarm firing means telemetry was accepted and never
delivered. Messages are retained for 14 days and lost after that — see the
[DLQ replay runbook](runbooks/dlq-replay.md).

## Verify

```bash
bash integrations/<vendor>/scripts/verify.sh
```

It checks that the stack exists, that real handler code was uploaded, that the
schedule is enabled, and that the checkpoint is advancing. Exit codes follow
`sysexits.h`: `0` pass, `69` a checked resource is unavailable, `78` a
configuration error.

## Cleanup

```bash
bash integrations/<vendor>/scripts/cleanup.sh          # stacks only
bash integrations/<vendor>/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/<vendor>/scripts/cleanup.sh --all -y  # non-interactive
```

Stacks are deleted in dependency-safe order: any vendor-declared extra stacks
first, then `-fpolicy`, `-ems-webhook`, `-ems`, `-integration`. The API Gateway
stack must go before the EMS Lambda stack it references.

Buckets that hold undelivered records are declared `DeletionPolicy: Retain` and
survive cleanup by design — the Splunk Firehose backup bucket and the Datadog log
archive bucket. Delete them manually once their contents are recovered or written
off.

Shared resources are never deleted by a vendor cleanup: the FPolicy Fargate
stack, the S3 access point, the audit log bucket and the prerequisites stack. Use
`shared/scripts/cleanup-shared.sh` after removing every vendor.

> If a vendor's deploy scripts create a stack beyond the four standard ones, the
> vendor's `cleanup.sh` must declare it in `EXTRA_STACKS` or it is silently left
> running. `integrations/splunk-serverless/scripts/cleanup.sh` is the worked
> example. Datadog's snapshot remediation stack is deliberately opt-in via
> `--delete-snapshot-remediation`, because it can act on the storage.

## Related Documents

- [Prerequisites](prerequisites.md) — FSx for ONTAP, audit logging, access point
- [Deployment guide](deployment-guide.md) — stack catalog, VPC endpoint conflicts, cost
- [Pipeline SLO](pipeline-slo.md) — Go/No-Go criteria between readiness levels
- [DLQ replay runbook](runbooks/dlq-replay.md)
