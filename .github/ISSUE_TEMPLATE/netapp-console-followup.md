---
name: "NetApp Console + System Manager: Investigation & Part 9"
about: Follow-up items from May 2026 verification
title: "NetApp Console + System Manager integration: investigation items and Part 9 planning"
labels: type:feature
---

## Summary

Follow-up items from the NetApp Console / System Manager / FSA verification (May 2026) and plans for Part 9 of the blog series.

## Investigation Items

### 1. VPC-external Lambda Link behavior
- **Finding**: "Create automatically" created the Lambda function outside the VPC (SubnetIds/SecurityGroupIds = null)
- **Question**: How does a VPC-external Lambda access the ONTAP management endpoint (private IP)? Does NetApp SaaS proxy the requests?
- **Impact**: Data residency implications for regulated organizations
- **Reference**: [classmethod article](https://dev.classmethod.jp/articles/amazon-fsx-for-netapp-ontap-netapp-console/) shows VPC-internal Lambda with "Create manually"

### 2. Lambda 30-minute periodic execution
- **Finding**: Lambda is invoked every ~30 minutes even without user operations (113 invocations/day measured)
- **CloudWatch Logs show**: Calls to `/api/storage/volumes/{uuid}/top-metrics/directories` and `/api/storage/volumes/{uuid}/files`
- **Question**: Is this FSA data caching on the SaaS side? Health check? What data is stored on NetApp SaaS?

### 3. System Manager direct URL access failure
- **Finding**: Navigating directly to `/system-manager/<fs-id>` shows JavaScript errors. Only works when opened via Systems page > SERVICES > Open
- **Question**: Is this expected behavior? Session token dependency?

## Part 9 Planning: Long-term File Access Analysis Pipeline

### Goal
Build a serverless pipeline that periodically collects FSA data and audit log summaries, stores them in S3, and enables long-term analysis via Athena/QuickSight.

### Architecture
```
EventBridge Scheduler (every 15 min)
  → Lambda (VPC-internal)
    → ONTAP REST API: /api/storage/volumes/{uuid}/top-metrics/users
    → ONTAP REST API: /api/storage/volumes/{uuid}/top-metrics/files
    → S3 (Parquet format, partitioned by date)
  → Athena (SQL analysis)
  → QuickSight dashboard
    - Inactive users (90+ days no access)
    - User access frequency trends (monthly)
    - Directory capacity + last access date
```

### Deliverables
- [ ] Lambda function (Python 3.12) for periodic FSA data collection
- [ ] CloudFormation template
- [ ] Athena table definition + sample queries
- [ ] QuickSight dashboard template (optional)
- [ ] Documentation (ja/en)
- [ ] dev.to article (Part 9)

### Success Criteria
- Identify users with no file access for 90+ days
- Generate monthly inactive data report
- Total pipeline cost < $15/month
