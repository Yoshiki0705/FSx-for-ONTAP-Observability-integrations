# FSx for ONTAP Elastic Integration

🌐 [日本語](docs/ja/setup-guide.md) | [English](docs/en/setup-guide.md)

> 📖 **Shared docs**: [Delivery Guarantee Patterns](../../docs/en/delivery-guarantees.md) | [Webhook Security](../../docs/en/webhook-security.md)

## Overview

Ships Amazon FSx for NetApp ONTAP audit logs to Elasticsearch via the Bulk API. Logs are mapped to Elastic Common Schema (ECS) for compatibility with Kibana dashboards and Security SIEM features.

**PoC time estimate**: ~30 minutes from deploy to first queryable document in Kibana.

## Architecture

```
FSx for ONTAP → S3 Access Point → EventBridge → Lambda → Elasticsearch Bulk API
```

## Prerequisites

See [Prerequisites Guide](../../docs/en/prerequisites.md) for ONTAP audit logging setup and S3 Access Point configuration.

## Event Source Templates

| Event Source | Template | Description |
|-------------|----------|-------------|
| Audit Logs (S3 AP polling) | `template.yaml` | Primary audit log shipper |
| EMS Webhooks | `template-ems.yaml` | ONTAP EMS events via API Gateway |
| FPolicy (file operations) | `template-fpolicy.yaml` | Real-time file operation events |

## Quick Deploy

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name fsxn-elastic-integration \
  --parameter-overrides \
    S3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit \
    ElasticApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:elastic-key \
    ElasticEndpoint=https://my-cluster.es.ap-northeast-1.aws.found.io:9243 \
    S3BucketName=my-fsxn-audit-bucket \
  --capabilities CAPABILITY_IAM
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| S3AccessPointArn | ✅ | - | FSx for ONTAP S3 Access Point ARN |
| ElasticApiKeySecretArn | ✅ | - | Secrets Manager ARN for Elastic API key |
| ElasticEndpoint | ✅ | - | Elasticsearch cluster endpoint URL |
| S3BucketName | ✅ | - | S3 bucket name for EventBridge rule matching |
| S3KeyPrefix | ❌ | (empty) | S3 key prefix filter |
| IndexPrefix | ❌ | fsxn-audit | Elasticsearch index name prefix |
| LogLevel | ❌ | INFO | Lambda log level |
| LambdaMemorySize | ❌ | 256 | Lambda memory (MB) |
| LambdaTimeout | ❌ | 300 | Lambda timeout (seconds) |

## Authentication

Elastic supports **API Key** (recommended) or Basic Auth.

### API Key (Recommended)

```bash
# Create API key in Kibana: Stack Management → API Keys → Create
aws secretsmanager create-secret \
  --name "elastic/fsxn-api-key" \
  --secret-string '{"api_key":"base64_encoded_id:api_key"}' \
  --region ap-northeast-1
```

### Elastic Cloud vs Self-Hosted

| Deployment | Endpoint Format | Notes |
|-----------|----------------|-------|
| Elastic Cloud | `https://<deployment-id>.es.<region>.aws.found.io:9243` | Managed, no infra ops |
| Self-hosted (EC2/EKS) | `https://<your-host>:9200` | Full control, VPC access |
| Amazon OpenSearch | `https://<domain>.<region>.es.amazonaws.com` | Use OpenSearch-compatible API |

> For self-hosted Elasticsearch in a private VPC, deploy Lambda in the same VPC with appropriate security group rules.

## Elastic-Side Setup

1. **Elastic Cloud**: Create a deployment at [cloud.elastic.co](https://cloud.elastic.co/)
2. **Create API Key**: Kibana → Stack Management → Security → API Keys
3. **Create Index Template** (recommended):
   ```json
   PUT _index_template/fsxn-audit
   {
     "index_patterns": ["fsxn-audit-*"],
     "template": {
       "settings": {"number_of_replicas": 1},
       "mappings": {
         "properties": {
           "@timestamp": {"type": "date"},
           "event.type": {"type": "keyword"},
           "user.name": {"type": "keyword"},
           "source.ip": {"type": "ip"},
           "fsxn.operation": {"type": "keyword"},
           "fsxn.path": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
           "fsxn.result": {"type": "keyword"},
           "fsxn.svm": {"type": "keyword"}
         }
       }
     }
   }
   ```
4. Store API key in AWS Secrets Manager (see above)

## Query Examples (KQL / Kibana)

```
# Failed access attempts
fsxn.result: "Failure"

# Specific user activity
user.name: "admin@corp.local" AND fsxn.operation: "WriteData"

# File access by path pattern
fsxn.path: *confidential*

# Operations by SVM
fsxn.svm: "svm-prod-01" AND event.type: "4663"
```

## Index Pattern

- Daily indices: `fsxn-audit-YYYY.MM.DD`
- ECS-compatible field mapping
- ILM (Index Lifecycle Management) recommended for retention

## Document Format (ECS)

```json
{
  "@timestamp": "2026-01-15T12:00:00Z",
  "event": {"type": "4663"},
  "user": {"name": "admin@corp.local"},
  "source": {"ip": "198.51.100.50"},
  "fsxn": {
    "operation": "ReadData",
    "path": "/vol/data/file.txt",
    "result": "Success",
    "svm": "svm-01"
  },
  "cloud": {"provider": "aws", "service": {"name": "fsx-ontap"}}
}
```

## Monitoring

- **CloudWatch Alarm**: Lambda errors > 5 in 10 minutes
- **Dead Letter Queue**: Failed events preserved for 14 days (KMS encrypted)
- **Kibana**: Monitor index ingestion rate via Stack Monitoring

## Limits & Known Issues

- Recommended max ~10MB per Bulk API request
- No built-in Firehose support — Lambda direct delivery only
- Self-signed certificates: set `VerifySSL=false` (⚠️ not recommended for production)
- Elastic Cloud free trial: 14 days, then paid
- **Data residency**: For Elastic Cloud, select a deployment region that meets your data residency requirements. For self-hosted, data remains in your VPC. Evaluate cross-border data transfer requirements with your compliance team.

## Cost Estimate

| Monthly Log Volume | Storage (est.) | Elastic Cloud Cost |
|-------------------|---------------|-------------------|
| 1 GB | ~1.5 GB (with replicas) | Free trial / minimal |
| 10 GB | ~15 GB | Standard tier (~$95/month) |
| 100 GB | ~150 GB | Dedicated tier required |

> Self-hosted Elasticsearch has no per-GB ingestion cost but requires infrastructure management.

## Related Documents

- [Vendor Comparison](../../docs/en/vendor-comparison.md)
- [PoC Success Criteria](../../docs/en/poc-success-criteria.md)
