# Vendor Integration E2E Verification Checklist

Based on lessons learned from the Datadog integration (3-part blog series) E2E verification.

---

## Pre-Verification Setup

### ONTAP Configuration

- [ ] **Audit logging enabled** on target SVM
  ```bash
  vserver audit show -vserver <svm>
  ```
- [ ] **Time-based rotation configured** (critical for quick validation)
  ```bash
  # Verification: 5-minute rotation
  vserver audit modify -vserver <svm> \
    -rotate-schedule-minute 0,5,10,15,20,25,30,35,40,45,50,55
  ```
- [ ] **SACL/NFSv4 ACL configured** on target files/directories
  - Without SACL: no audit events generated → empty logs → verification fails
  - CIFS/SMB: Configure via Windows Security settings
  - NFS: Configure via `nfs4_setfacl`
- [ ] **Audit log format confirmed** (EVTX or XML, NOT JSON)
  ```bash
  vserver audit show -vserver <svm> -fields format
  ```

### S3 Access Point Verification

- [ ] **Verify actual key prefix** with `list-objects-v2`
  ```bash
  aws s3api list-objects-v2 \
    --bucket "arn:aws:s3:<region>:<account>:accesspoint/<ap-name>" \
    --prefix "audit/" \
    --max-keys 5
  ```
- [ ] **AuditLogPrefix matches actual prefix** in CloudFormation parameters
- [ ] **Lambda network placement** confirmed:
  - VPC-external Lambda (simplest for S3 AP only) ✅
  - VPC + NAT Gateway (if ONTAP REST API also needed) ✅
  - VPC + S3 Gateway Endpoint only (Internet-origin AP) ⚠️ TIMEOUT — use NAT or VPC-origin AP

### Vendor API Pre-checks

- [ ] **API key/token stored** in Secrets Manager
- [ ] **Endpoint URL confirmed** (check region-specific URLs)
- [ ] **Batch size limit documented**
  | Vendor | Max Batch |
  |--------|-----------|
  | Datadog | 5MB / 1000 items |
  | New Relic | 1MB |
  | Sumo Logic | 1MB |
  | Honeycomb | 5MB / 100 events |
  | Grafana/Loki | ~4MB recommended |
  | Splunk | No hard limit |
  | Elastic | ~10MB recommended |
  | Dynatrace | 1MB |
- [ ] **Timestamp limit documented**
  - Datadog: 18 hours (API accepts but doesn't index older events)
  - Other vendors: verify during E2E
- [ ] **Compression support verified** (gzip issues on some regions/sites)

---

## Phase 1: CloudFormation Deployment

- [ ] **Stack deploys successfully** (`CREATE_COMPLETE`)
  ```bash
  aws cloudformation deploy \
    --template-file integrations/<vendor>/template.yaml \
    --stack-name fsxn-<vendor>-integration \
    --parameter-overrides \
      S3AccessPointArn=<arn> \
      ApiKeySecretArn=<arn> \
      S3BucketName=<bucket> \
    --capabilities CAPABILITY_IAM
  ```
- [ ] **Lambda function created** with correct runtime (Python 3.12)
- [ ] **DLQ created** (SQS queue)
- [ ] **Lambda DeadLetterConfig** points to DLQ ARN
  - NOT SQS source queue DLQ
  - `start-message-move-task` does not work for Lambda async DLQ
- [ ] **EventBridge Scheduler** configured (NOT S3 Event Notifications)
- [ ] **CloudWatch Alarms** created (Errors, Throttles, DLQ)
- [ ] **IAM role** has minimal permissions:
  - `s3:GetObject` on `${S3AccessPointArn}/object/*`
  - `secretsmanager:GetSecretValue` on API key secret
  - `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`
  - `sqs:SendMessage` on DLQ ARN

---

## Phase 2: Lambda Test Invocation

- [ ] **Test event sent** to Lambda
  ```bash
  aws lambda invoke \
    --function-name fsxn-<vendor>-integration-shipper \
    --payload file://tests/test_data/sample_s3_event.json \
    --cli-binary-format raw-in-base64-out \
    response.json
  ```
- [ ] **Response validates**:
  - `statusCode: 200`
  - `total_logs >= 1`
  - `total_shipped >= 1`
  - `errors: []`
- [ ] **CloudWatch Logs** show INFO-level processing summary
- [ ] **Batch failure handling verified**:
  - On failure: exception is raised (not swallowed)
  - Checkpoint does NOT advance on failure
  - Failed event goes to DLQ after Lambda async retries

---

## Phase 3: Vendor UI Log Arrival

- [ ] **Logs visible in vendor UI** within 5 minutes of Lambda invocation
- [ ] **Correct source/category tag** applied:
  - Datadog: `source:fsxn`
  - Sumo Logic: `_sourceCategory=aws/fsxn/audit`
  - Honeycomb: dataset `fsxn-audit`
  - Grafana: `{job="fsxn-audit"}`
  - Splunk: `sourcetype=fsxn:audit`
  - etc.
- [ ] **Required fields present** and non-empty:
  - `svm` / `SVMName`
  - `user` / `UserName`
  - `operation` / `Operation`
  - `client_ip` / `ClientIP`
  - `result` / `Result`
  - `path` / `ObjectName`
- [ ] **Timestamp correctly indexed** (not ingestion time)
- [ ] **Screenshot captured** of log arrival

---

## Phase 4: Vendor-Specific Configuration

- [ ] **Log pipeline/parsing** configured (if applicable)
- [ ] **Facets/fields/columns** indexed for search
- [ ] **Dashboard created** with standard panels:
  - Log volume over time
  - Operations breakdown
  - User activity top list
  - Error/failure rate
- [ ] **Alert/monitor configured** for unauthorized access detection
  - Record the exact query syntax (varies per vendor)
  - Datadog example: `logs("source:fsxn @attributes.result:Failure").index("*").rollup("count").last("5m") > 0`
- [ ] **Screenshots captured** for all configuration steps

---

## Phase 5: EMS Webhook Verification

- [ ] **EMS webhook stack deployed** (`shared/templates/ems-webhook-apigw.yaml`)
- [ ] **API Gateway endpoint accessible**
- [ ] **ARP event test** (Autonomous Ransomware Protection):
  ```bash
  # On ONTAP CLI:
  security anti-ransomware volume attack simulate -vserver <svm> -volume <vol>
  ```
  - NOTE: ARP = "Autonomous Ransomware Protection" (not "Anti-Ransomware")
- [ ] **ARP event arrives in vendor UI** within 120 seconds
  - Fields: `event_name`, `severity`, `volume_name`, `state`
- [ ] **Quota exceeded event test**:
  - Write 60MB+ to volume with 50MB soft quota
- [ ] **Quota event arrives in vendor UI** within 180 seconds
  - Fields: `volume_name`, `quota_target`, `used_bytes`, `limit_bytes`
- [ ] **EMS payload normalization** verified:
  - Raw ONTAP format differs from documentation
  - Use `shared/lambda-layers/ems-parser/` for normalization
- [ ] **Screenshot captured** of EMS events in vendor UI

---

## Phase 6: FPolicy Verification

- [ ] **FPolicy stack deployed** (`shared/templates/fpolicy-apigw.yaml`)
- [ ] **ECS Fargate task RUNNING** and healthy
- [ ] **KeepAlive messages** visible in ECS CloudWatch Logs (120 second interval, matching the engine's `keep_alive_interval`; query a window of 300 s or more, since a 30 s window shows nothing on a healthy connection roughly three runs in four)
- [ ] **File operation test** (create file via CIFS/SMB)
- [ ] **SQS message confirmed**: `[SQS] Sent: <filename> (create)` in ECS logs
- [ ] **FPolicy event arrives in vendor UI** within 30 seconds
  - Fields: `operation`, `file_path`, `user`, `client_ip`
- [ ] **Screenshot captured** of FPolicy events in vendor UI

---

## Phase 7: Documentation

- [ ] **Verification results document** created:
  - Japanese: `docs/ja/verification-results-<vendor>.md`
  - English: `docs/en/verification-results-<vendor>.md`
- [ ] **Setup guide bilingual check**:
  - Heading structure matches (ja/en)
  - Code blocks identical (ja/en)
  - Parameter tables identical (ja/en)
- [ ] **Screenshots saved** to `docs/screenshots/<vendor>/`
  - PNG format, >1KB each
  - Naming: `<vendor>-<description>.png`

---

## Common Pitfalls (from Datadog E2E)

| Pitfall | Symptom | Solution |
|---------|---------|----------|
| S3 Gateway EP only (Internet-origin AP) | Lambda timeout reading S3 AP | Use VPC-external Lambda, NAT Gateway, or VPC-origin AP |
| No time-based rotation | No new log files appear | Set 5-min rotation for verification |
| Missing SACL | Audit events not generated | Configure SACL on target files |
| AuditLogPrefix mismatch | Lambda processes 0 files | Verify with `list-objects-v2` |
| Swallowed exceptions | Lost logs, checkpoint advances | Always `raise` on batch failure |
| SQS source queue DLQ | Failed events not captured | Use Lambda `DeadLetterConfig` |
| Old timestamps | Logs accepted but not indexed | Check vendor timestamp limits |
| gzip on specific sites | API errors on some regions | Test compression per region |
| EMS raw payload | Parse errors | Use shared ems-parser layer |
| at-least-once delivery | Duplicate logs in vendor | Design for idempotency |

---

## Post-Verification

- [ ] **All screenshots present** and valid (PNG, >1KB)
- [ ] **Verification results document** complete (ja + en)
- [ ] **Setup guide** bilingual consistency confirmed
- [ ] **Root README.md** vendor table updated (🚧 → ✅)
- [ ] **Vendor comparison document** updated
- [ ] **Production rotation** configured (hourly + size-based)
  ```bash
  vserver audit modify -vserver <svm> \
    -rotate-schedule-minute 0 \
    -rotate-size 100MB
  ```
