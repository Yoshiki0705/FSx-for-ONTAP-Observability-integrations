# Elastic Setup Guide

🌐 [日本語](../ja/setup-guide.md)

## Overview

Setup guide for shipping FSx for ONTAP audit logs to Elasticsearch via Bulk API and visualizing in Kibana.

## Prerequisites

- Elastic Cloud or self-hosted Elasticsearch cluster
- [Prerequisites stack](../../../../docs/en/prerequisites.md) deployed

## Step 1: Create Elasticsearch API Key

```bash
# Elastic Cloud: Kibana -> Stack Management -> API Keys -> Create
aws secretsmanager create-secret \
  --name "elastic/fsxn-api-key" \
  --secret-string '{"api_key":"YOUR_ENCODED_API_KEY"}' \
  --region ap-northeast-1
```

## Step 2: Deploy CloudFormation

### Recommended: use the deploy script

The script deploys the stack **and** uploads the real Lambda code. The
CloudFormation template cannot carry the handler inline, so this is the only
one-step path to a working integration.

```bash
export ELASTIC_SECRET_ARN="..."
export S3_ACCESS_POINT_ARN="..."
export S3_BUCKET_NAME="..."
export ELASTIC_ENDPOINT="..."

bash integrations/elastic/scripts/deploy.sh
```

First run takes **3-5 minutes**, almost all of it CloudFormation creating the
IAM role, Lambda, scheduler and alarms. Re-runs of an unchanged stack finish in
seconds. Run `--help` for every supported variable.

Add `--all` to also deploy the EMS and FPolicy stacks. Set
`FPOLICY_SQS_QUEUE_ARN` to the ingestion queue ARN from
`shared/templates/fpolicy-apigw.yaml` to enable the primary FPolicy trigger path
(Fargate → SQS → Lambda); without it the FPolicy stack uses the secondary
EventBridge rule only. See the
[telemetry path coverage matrix](../../../../README.md#telemetry-path-coverage).

> Set `ALARM_TOPIC_ARN` to an SNS topic ARN before running the script to make
> the CloudWatch alarms actionable. Left unset, the alarms are created without
> notification actions: visible in the console, paging nobody.

### Alternative: deploy CloudFormation by hand

```bash
aws cloudformation deploy \
  --template-file integrations/elastic/template.yaml \
  --stack-name fsxn-elastic-integration \
  --parameter-overrides \
    S3AccessPointArn=$AP_ARN \
    ElasticApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:elastic/fsxn-api-key-XXXXX \
    ElasticEndpoint=https://my-cluster.es.ap-northeast-1.aws.found.io:9243 \
    S3BucketName=$BUCKET_NAME \
    IndexPrefix=fsxn-audit \
  --capabilities CAPABILITY_IAM
```

### Upload the real Lambda code (required)

**The stack alone is not functional.** `template.yaml` ships a placeholder that
raises `NotImplementedError`, because CloudFormation cannot inline a handler this
size. `scripts/deploy.sh` already does this step; if you deployed by hand, do it
now:

```bash
cd integrations/elastic/lambda
zip -j function.zip handler.py ../../../shared/python/ontap_audit_parser.py

aws lambda update-function-code \
  --function-name fsxn-elastic-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1

aws lambda wait function-updated \
  --function-name fsxn-elastic-integration-shipper \
  --region ap-northeast-1
```

The `-j` flag flattens paths so `ontap_audit_parser` resolves at runtime. Without
that file the handler silently falls back to JSON-only parsing, and ONTAP audit
logs — which are always XML or EVTX — arrive with no parsed fields.

### Parameter Reference

<!-- generated from template.yaml; keep in sync when parameters change -->

Required:

| Parameter | Description |
|-----------|-------------|
| `S3AccessPointArn` | FSx for ONTAP S3 Access Point ARN (attached to the audit volume) |
| `ElasticApiKeySecretArn` | Secrets Manager ARN for the Elasticsearch API key |
| `ElasticEndpoint` | Elasticsearch cluster endpoint URL (https://...) |
| `S3BucketName` | S3 bucket name for EventBridge rule matching |

Optional — the defaults work for most deployments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `S3KeyPrefix` | `''` (empty) | S3 key prefix filter for audit log objects |
| `IndexPrefix` | `fsxn-audit` | Elasticsearch index name prefix. The handler appends a UTC date suffix, producing e.g. fsxn-audit-2026.08.07 |
| `LogLevel` | `INFO` | Lambda log level. Use DEBUG when troubleshooting delivery |
| `LambdaMemorySize` | `256` | Lambda memory in MB. Raise it if large EVTX files run out of memory |
| `LambdaTimeout` | `300` | Lambda timeout in seconds. Must exceed the time needed to process one batch of files |
| `AlarmNotificationTopicArn` | `''` (empty) | (Optional) SNS topic ARN notified when the alarms in this stack fire. Leave empty to create the alarms without notification actions — they will be visible in the CloudWatch console but will not page anyone. |

## Step 3: Kibana Configuration

### Index Pattern
1. Kibana → **Stack Management** → **Index Patterns**
2. Pattern: `fsxn-audit-*`, Time field: `@timestamp`

### Check in Discover

- Filter: `fsxn.operation: ReadData`
- Time range: Last 1 hour

### Dashboard
- Operations pie chart: `fsxn.operation.keyword`
- Users bar chart: `user.name.keyword`
- Failed access timeline: `fsxn.result: Failure`

## Index Lifecycle Management

```json
PUT _ilm/policy/fsxn-audit-policy
{
  "policy": {
    "phases": {
      "hot": {"actions": {"rollover": {"max_age": "30d"}}},
      "delete": {"min_age": "90d", "actions": {"delete": {}}}
    }
  }
}
```

## Forensic Investigation (Kibana Discover/Lens)

> 🔍 For a user/IP/path-centric investigation workflow (who accessed what, from where, doing what — similar to DII Storage Workload Security's Forensics dashboards), the [Normalized Event Schema](../../../../docs/en/normalized-event-schema.md) already maps ONTAP audit and FPolicy fields to ECS (`user.name`, `source.ip`, `file.path`, `event.action`), so no custom mapping is required. Build the following in Kibana:

### Saved Searches (KQL)

| Investigation View | KQL Query | Equivalent DII SWS View |
|---------------------|-----------|--------------------------|
| User Overview | `user.name: "<value>"` | Forensic User Overview |
| All Activity | `event.dataset: "fsxn"` (no filter, sorted by `@timestamp` descending) | Forensics - All Activity |
| IP-Centric Drill-Down | `source.ip: "<value>"` | Forensic User Activity Data |
| Entity / File History | `file.path: "<value>"` | Forensic Entities Page |

Save each as a Kibana **Saved Search** with a descriptive name (e.g., `fsxn-forensics-user-overview`) so investigators can select the right view from Discover without rebuilding the query.

### Lens Visualization

Add a **Lens** bar chart breaking down `event.action` (operation type) for the currently filtered saved search — this surfaces anomalous action mixes (e.g., a spike in delete operations) the same way DII SWS's Forensics dashboards highlight action distribution per user/entity.

### Export

Discover's **Share → CSV Reports** (or **Generate CSV** in newer Kibana versions) exports the current filtered view, scoped to whatever time range you've selected — equivalent to DII SWS's 31-day filtered CSV export, without the fixed 31-day ceiling (retention is governed by your ILM policy above instead).

See [Cyber Resilience Capability Map](../../../../docs/en/cyber-resilience-capability-map.md#respond-rs) for the full CSF 2.0 function coverage this implements, including known data-source caveats (FPolicy vs audit log coverage gaps, PII handling via the [Data Classification Guide](../../../../docs/en/data-classification.md)).

## Verify the deployment

```bash
bash integrations/elastic/scripts/verify.sh
```

Two layers run in sequence. First the shared AWS checks: the stack is healthy,
the deployed Lambda is the real handler rather than the placeholder, and the
schedule and checkpoint (where the stack creates them) are in place. Then a
synthetic log is sent to the vendor endpoint to prove credentials and network
reach it.

Both matter. A vendor endpoint that accepts a test log tells you nothing about
whether the pipeline is running, which is why the script exits non-zero on an
AWS-side failure even when the endpoint responded fine.

Exit codes follow `sysexits.h`: `0` pass, `69` a check failed, `78` required
configuration missing. Set `SKIP_AWS_CHECKS=1` to test only vendor reachability
before deploying.

## Cleanup

```bash
bash integrations/elastic/scripts/cleanup.sh          # stacks only
bash integrations/elastic/scripts/cleanup.sh --all    # + secret, layer, S3 test data
bash integrations/elastic/scripts/cleanup.sh --all -y  # non-interactive
```

Shared resources (S3 access point, audit log bucket, FPolicy Fargate stack,
prerequisites stack) are not touched. See
[Deploying a vendor integration](../../../../docs/en/vendor-deployment-common.md)
for the deletion order and what is retained on purpose.

## Related Documents

- [Deploying a vendor integration](../../../../docs/en/vendor-deployment-common.md) — steps shared by every vendor
- [Prerequisites](../../../../docs/en/prerequisites.md) — FSx for ONTAP, audit logging, S3 access point
- [Deployment guide](../../../../docs/en/deployment-guide.md) — stack catalog, VPC endpoint conflicts, cost
