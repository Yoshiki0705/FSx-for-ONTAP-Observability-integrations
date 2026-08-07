# Minimum Test Path

🌐 [日本語](../ja/quick-start-minimum.md) | **English** (this page)

Ship audit events to Datadog with the simplest possible configuration.

## Prerequisites

- FSx for ONTAP file system (audit logging enabled)
- FSx for ONTAP S3 Access Point (attached to the audit volume)
- Datadog account (free trial works)
- Datadog API Key stored in Secrets Manager

## Minimum Configuration

| Setting | Value | Reason |
|---------|-------|--------|
| Lambda VPC | Outside VPC | No NAT Gateway required |
| Scheduler | rate(5 minutes) | Default |
| Audit rotation | 5-minute interval (time-based) | Rotated files appear quickly |
| Datadog site | Your site (e.g., ap1.datadoghq.com) | — |

## Steps

```bash
# 1. Deploy (single command — deploys the stack AND uploads the Lambda code)
export DATADOG_API_KEY_SECRET_ARN=<your-secret-arn>
export FSX_S3_ACCESS_POINT_ARN=<your-fsx-s3-ap-arn>
export DATADOG_SITE=<your-site>

bash integrations/datadog/scripts/deploy.sh    # 3-5 minutes on first run

# 2. Confirm the pipeline is wired end to end
export DD_API_KEY_SECRET_ID=fsxn-datadog-api-key
export DD_SITE=<your-site>

bash integrations/datadog/scripts/verify.sh    # expect 4/4 checks passed

# 3. Perform a test file operation on the audited share
#    (create/delete a file via SMB or NFS)

# 4. Wait for ONTAP to rotate the audit log, then for the next 5-minute schedule

# 5. Verify in Datadog
#    Search: source:fsxn
```

> **Do not deploy `template.yaml` on its own.** CloudFormation cannot inline the
> handler, so the template ships a placeholder that raises `NotImplementedError`.
> `deploy.sh` uploads the real code as its final step; without that step no log
> ever arrives and step 5 cannot succeed. `verify.sh` check 2 detects this.

If you prefer raw CloudFormation, see
[Setup Guide Step 3](../../integrations/datadog/docs/en/setup-guide.md#step-3-deploy)
— it covers both the manual deploy and the required code upload.

## Success Criteria

- [ ] `source:fsxn` returns at least one result in Datadog Log Explorer
- [ ] `@attributes.operation` is populated
- [ ] `@attributes.user` is populated

## Not Included in the Minimum Test

- VPC / NAT Gateway configuration
- DLQ replay procedures
- Custom metrics
- Datadog Monitor setup
- Multi-SVM / multi-account

These are production hardening steps covered in the full documentation.

## Next Steps

After confirming log arrival:
1. Review the [field mapping](../../integrations/datadog/docs/en/field-mapping.md)
2. Try [investigation queries](../../integrations/datadog/docs/en/field-mapping.md#datadog-search-queries)
3. Set up Monitors (blog series Part 3)

4. For production deployment with VPC Endpoints, cost planning, and multi-stack coordination, see the [Deployment Guide](deployment-guide.md)
