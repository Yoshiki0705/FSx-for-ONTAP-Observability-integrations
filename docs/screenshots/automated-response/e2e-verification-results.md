# E2E Verification Results -- Automated Response

**Date**: 2026-07-08 (UTC 20:30-21:00)
**Region**: ap-northeast-1 (Tokyo)
**FSx for ONTAP**: fs-002ec851eba809979 (Single-AZ)
**SVM**: fsxsvm01

## Test Results

| # | Action | Status | Duration | Notes |
|---|--------|--------|----------|-------|
| 1 | health_check | PASS | 825ms | /cluster + /svm/svms both OK |
| 2 | create_snapshot | PASS | 545ms | incident_response_20260708_203432 on vol1 |
| 3 | block_nfs_ip (direct API) | PASS | 201 Created | index:999, client:203.0.113.199 |
| 4 | block_smb_user (direct API) | PASS | 201 Created | index:99, CORP\\testuser99 |
| 5 | list_active_blocks | PASS | <1s | API responding correctly |
| 6 | TTL cleanup (EventBridge) | PASS | 1.5s | "no expired blocks found" (correct) |
| 7 | unblock_smb_user | PASS | <1s | entries_removed: 1 |
| 8 | cooldown (2nd snapshot) | PASS | <1s | "cooldown active — 0.2 min ago (limit: 15m)" |

## Verified Environment

| Item | Value |
|------|-------|
| ONTAP Version | NetApp Release 9.17.1P7D1 (June 2026) |
| FSx Deployment | Single-AZ |
| Region | ap-northeast-1 (Tokyo) |
| Lambda Runtime | Python 3.12 |
| Layer | fsxn-shared-python:2 |

## Deployment Issues Encountered and Resolved

| # | Issue | Root Cause | Resolution |
|---|-------|-----------|-----------|
| 1 | Lambda timeout (60s) | No VPC Endpoint for Secrets Manager | Created Interface VPC Endpoint |
| 2 | SNS notification timeout | No VPC Endpoint for SNS | Created Interface VPC Endpoint |
| 3 | VPC Endpoint SG blocking | Lambda SG not in VPCE SG ingress | Added VPC CIDR TCP 443 rule |
| 4 | ONTAP API 401 on all paths | Secrets Manager password stale | Reset via aws fsx update-file-system |
| 5 | export-policy rule HTTP 400 | protocols: nfs3/nfs4/cifs invalid | Fixed to any |
| 6 | name-mapping HTTP 409 | Existing entry at index 1 | Use higher index (99+) |
| 7 | Lambda ImportError | ZipFile inline lacks ontap_response | Published Lambda Layer v2 |

## Infrastructure Deployed

| Resource | ID/ARN |
|----------|--------|
| Main Stack | fsxn-automated-response |
| TTL Stack | fsxn-automated-response-ttl |
| Lambda (main) | fsxn-automated-response-handler (512MB, 120s) |
| Lambda (TTL) | fsxn-automated-response-ttl-cleanup (256MB, 60s) |
| Lambda Layer | fsxn-shared-python:2 |
| SNS Trigger | fsxn-automated-response-trigger |
| SNS Notifications | fsxn-automated-response-notifications |
| DLQ | fsxn-automated-response-dlq |
| VPC Endpoint (Secrets Manager) | vpce-0fc6e6e8c6afe5dfa |
| VPC Endpoint (SNS) | vpce-0dbb53fc94b47c31d |
| SG Rule | sgr-074120573f3f40915 (Lambda to ONTAP TCP 443) |
| EventBridge Schedule | fsxn-automated-response-ttl-cleanup-schedule (every 2 min) |

## Key Performance Metrics

| Metric | Value | Target |
|--------|-------|--------|
| health_check E2E | 825ms | < 10s |
| create_snapshot E2E | 545ms | < 10s |
| TTL cleanup cycle | 1.5s | < 30s |
| Cold start (VPC Lambda) | ~320ms | < 5s |
| All actions complete within | 120s timeout | No timeout |

## CLI Evidence (replaces console screenshots where masking issues exist)

### VPC Endpoints (Available)

```
$ aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=vpc-05192d06e1e91d756" \
    --query 'VpcEndpoints[].{Service:ServiceName,Type:VpcEndpointType,State:State}' \
    --output table

+-------------------------------------------+------------+-------------+
|                  Service                  |   State    |    Type     |
+-------------------------------------------+------------+-------------+
|  com.amazonaws.ap-northeast-1.s3          |  available |  Gateway    |
|  com.amazonaws.ap-northeast-1.ssm         |  available |  Interface  |
|  com.amazonaws.ap-northeast-1.ssmmessages |  available |  Interface  |
|  com.amazonaws.ap-northeast-1.ec2messages |  available |  Interface  |
|  com.amazonaws.ap-northeast-1.secretsmanager | available | Interface |
|  com.amazonaws.ap-northeast-1.sns         |  available |  Interface  |
+-------------------------------------------+------------+-------------+
```

### FSx for ONTAP Volumes

```
$ aws fsx describe-volumes --filters Name=file-system-id,Values=fs-002ec851eba809979 \
    --query 'Volumes[].{Name:Name,Id:VolumeId}' --output table

+--------------------------------------------------+---------------------+
|                        Id                        |        Name         |
+--------------------------------------------------+---------------------+
|  fsvol-0183178c8b9ec5392                         |  vol1               |
|  fsvol-03476bedce8e1d0da                         |  fsxsvm01_root      |
|  fsvol-0dd4e8f7e2527299e                         |  testvol01          |
|  ...                                             |  (12 volumes total) |
+--------------------------------------------------+---------------------+
```

## Lessons Learned (Reflected in Docs/CFn)

1. **VPC Lambda needs VPC Endpoints** -- Added to CFn template (CreateVpcEndpoints param)
2. **ONTAP protocols field** -- Changed from explicit list to "any"
3. **Lambda Layer required** -- Added build-layer.sh + post-deploy step in guide
4. **Password management** -- Documented aws fsx update-file-system reset method
5. **SG rule direction** -- Document Lambda SG -> ONTAP ENI SG (not the reverse)
6. **DNS propagation** -- VPC Endpoint DNS needs ~1 min after creation
