# Content-Level PII Classification Scanner — Closing the CSF 2.0 Identify Gap

🌐 [日本語](../ja/content-classification-scanner.md) | **English** (this page)

## Executive Summary

The [Cyber Resilience Capability Map](cyber-resilience-capability-map.md#identify-id) is direct about a gap in the Identify function: this repository's [Data Classification Guide](data-classification.md) defines a schema-level classification (which *fields* — `UserName`, `ObjectName` — are PII), but does not scan file *contents* the way NetApp's data classification tooling does for the CSF 2.0 Identify function.

This guide implements that content-level scan using Amazon Comprehend's managed PII entity detection, exposed as a standalone Lambda function that reads files through an existing S3 Access Point:

1. **List objects** through a given S3 Access Point (`ListObjectsV2`) and filter to scannable text/structured-data extensions (`.txt`, `.csv`, `.json`, `.log`, and others; binary formats like Office documents are out of scope; see [Remaining Limitations](#remaining-limitations) below).
2. **Read and chunk** each file's content into byte-bounded segments under Amazon Comprehend's per-call size ceiling.
3. **Call `DetectPiiEntities`** per chunk and aggregate entity type, count, and confidence score per file. Only entity type/offset/confidence is kept; **the PII values themselves are never persisted**.
4. **Write a classification report** to DynamoDB and optionally notify via SNS when PII is found.

> **Scope note**: This is a PII *discovery* tool, not a redaction, remediation, or general-purpose malware/content scanner. It answers "does this volume contain content that looks like PII, and roughly how much", which is the CSF 2.0 Identify-function question of asset/data understanding, not Protect, Detect, or Respond.

### Key capabilities
- Amazon Comprehend `DetectPiiEntities` provides managed PII detection across [12 languages](https://docs.aws.amazon.com/comprehend/latest/dg/supported-languages.html), with dozens of entity types (SSN, credit card numbers, bank accounts, national ID numbers, and more)
- Data-minimizing by design: findings record entity type + confidence, never the matched text itself
- Works against any existing FSx for ONTAP S3 Access Point, standalone or chained after the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s FlexClone-backed access point for zero production impact
- Oversized files are sampled (first N bytes), not skipped entirely, so large log/CSV files still contribute a partial signal
- DynamoDB report ledger, doubling as CSF 2.0 Identify-function evidence for audits

### When to run this
- Periodically against volumes containing user-generated content (shares, home directories, exports) to maintain an up-to-date picture of where PII actually lives
- Against a [FlexClone verification access point](verified-recovery-point-guide.md) immediately after ransomware verification, combining both scans against the same isolated clone
- Before onboarding a volume to a new observability/SIEM destination, to confirm what a forensics dashboard or exported log sample might expose

> **Positioning note**: The accurate positioning here is "content-level PII discovery for plain-text/structured-data formats," not "PII discovery" unqualified. The [Remaining Limitations](#remaining-limitations) section below is explicit that Office/PDF content is out of scope today. In an engagement conversation, especially one where the organization's data is dominated by Office documents, lead with the schema-level [Data Classification Guide](data-classification.md) as the complete-coverage layer and this scanner as the content-level complement for the formats it does cover, rather than implying this scanner alone provides comprehensive content-level PII coverage across all file types.

---

## Architecture

```
+-------------------------------------------------------------------+
| Input: any existing S3 Access Point ARN                           |
| (standalone, or from verified-recovery-point-guide.md's            |
|  AttachAccessPoint step)                                          |
+-------------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
| Lambda: ScannerFunction                                            |
|                                                                    |
|  ListObjectsV2 (via access point)                                  |
|       |                                                            |
|       +-> filter: scannable extension? size > 0?                  |
|              |                                                     |
|              v                                                     |
|         GetObject (full or Range-sampled if oversized)             |
|              |                                                     |
|              v                                                     |
|         chunk into <100KB UTF-8 segments                           |
|              |                                                     |
|              v                                                     |
|         Comprehend DetectPiiEntities (per chunk)                   |
|              |                                                     |
|              v                                                     |
|         aggregate entity_type -> count, max confidence             |
+-------------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------------+
| DynamoDB: classification report (per access point, per run)        |
| SNS: optional notification if PII found                            |
+-------------------------------------------------------------------+
```

> **Diagram description (text alternative)**: A single Lambda (`ScannerFunction`) takes an S3 Access Point ARN as input — either standalone or from the Verified-Clean Recovery Point Guide's `AttachAccessPoint` step. It lists objects via `ListObjectsV2`, filters to scannable extensions with non-zero size, reads each file (fully or Range-sampled if oversized), splits the content into under-100KB UTF-8 chunks, calls Comprehend `DetectPiiEntities` per chunk, and aggregates results by entity type with count and max confidence. The aggregated report is written to DynamoDB, and an SNS notification fires if PII was found. The diagram above is ASCII art; this paragraph is the complete textual equivalent for screen-reader users.

---

## How Classification Works

### File Selection

Only files with a scannable extension are read; this scanner does not implement document-format parsing (no Office/PDF text extraction):

```
.txt  .csv  .tsv  .json  .xml  .log  .md  .yaml  .yml  .ini  .conf  .sql  .html  .htm
```

Zero-byte files are skipped. Files above `DEFAULT_MAX_FILE_BYTES` (5 MB) are **sampled**, not skipped. Only the first 500 KB is read via an S3 Range `GetObject`, and the finding records `sampled: true` so you know the result is partial.

> **Sustainability note**: The 500 KB sampling cap on oversized files (rather than reading and scanning the full file) is itself an energy-saving design choice as much as a cost-control one — the `max_files` cap and sampling behavior together bound both the Comprehend inference workload and the total bytes read from S3 per run. The cost note under Configuration Reference below covers the dollar-cost implication of this same behavior; the energy implication is directly proportional, since Comprehend's inference cost and its underlying compute both scale with characters processed. Scanning a representative subset rather than an entire multi-terabyte volume (see the FAQ's cost-avoidance guidance) reduces energy use by the same proportion it reduces cost.

### Chunking for Comprehend's Size Limit

[`DetectPiiEntities`](https://docs.aws.amazon.com/comprehend/latest/dg/how-pii.html) enforces a 100 KB UTF-8 byte-size ceiling per call. The scanner splits file content into chunks under that limit, breaking on line boundaries where possible so an entity (e.g., an email address) is not split across a chunk boundary unnecessarily:

```python
# Simplified — see content_classifier.py's _chunk_text for the full
# line-boundary-aware implementation
for chunk in _chunk_text(file_text, target_bytes=98_000):
    entities = comprehend.detect_pii_entities(Text=chunk, LanguageCode="en")
```

### Entity Aggregation — Data Minimization by Design

For each file, findings record **only**:
- Entity `Type` (e.g., `EMAIL`, `SSN`, `CREDIT_DEBIT_NUMBER`, `BANK_ACCOUNT_NUMBER` — [full type list](https://docs.aws.amazon.com/comprehend/latest/dg/how-pii.html))
- Count of occurrences per type
- Highest confidence `Score` observed per type

The matched text itself (the actual email address, SSN, etc.) is **never written to the report, logs, or DynamoDB**. This mirrors the [Data Classification Guide](data-classification.md)'s pseudonymization guidance. A report saying "this file contains 3 SSN-pattern matches at 0.97+ confidence" is useful for prioritizing remediation without itself becoming a new PII exposure surface.

> **Privacy note**: The report records `highest_confidence_by_type` per entity, but the scanner does **not** filter or flag low-confidence matches — a single `EMAIL` detection at Comprehend confidence 0.31 counts toward `files_with_pii` exactly the same as one at 0.99. Before using `files_with_pii`/`pii_density_by_type` as an input to a regulatory PII inventory or a DPIA, review the per-entity `highest_confidence_by_type` in the raw findings (not just the summary counts) and apply your own confidence floor — treating every low-confidence match as confirmed PII risks overstating exposure; treating every match as noise risks understating it. Neither judgment call is one this scanner makes for you.

> **Data-pipeline note**: Before writing the report to DynamoDB, the CloudFormation template's inline Lambda code round-trips it through `json.loads(json.dumps(report), parse_float=str)` — a workaround for the fact that `boto3`'s DynamoDB resource API rejects native Python `float` values (DynamoDB's number type requires `Decimal`, not `float`). The side effect: `highest_confidence_by_type` scores are persisted to DynamoDB as **strings**, not numbers. This means a DynamoDB query that tries to filter or sort on confidence score numerically (e.g., `FilterExpression` with a numeric comparison, or a downstream Athena/QuickSight query expecting a numeric column) will not work against this field without an explicit cast at query time — string `"0.31"` does not compare numerically to string `"0.99"` the way you'd expect. If you plan to build analytics or dashboards on top of this report table's confidence scores, either cast to `Decimal` properly in the Lambda before the `put_item` call (removing the `parse_float=str` workaround) or account for the string-typed field explicitly in every downstream query.

### Error Handling — One Bad File Doesn't Abort the Scan

Read failures (`AccessDenied`, `NoSuchKey`), decode failures, and Comprehend API errors (`TextSizeLimitExceededException`, throttling) are captured per-file in the `error` field rather than raised — a single problematic file is recorded and skipped, and the scan continues across the rest of the volume.

> **API contract note**: This scanner's Lambda invocation contract is a plain JSON dict in, JSON dict out (`{"access_point_arn": ..., "language_code": ..., "max_files": ...}` → the aggregated report), with no request ID, idempotency key, or de-duplication mechanism — invoking it twice with identical input produces two independent DynamoDB rows (the `started_at` sort key guarantees this, per the concurrency note above), not an error or a merged result. This is the right design for a stateless discovery tool where "run it again" is always safe and cheap relative to Comprehend cost, but if you're chaining this into a larger Step Functions workflow (per the [Restore-testing note](#remaining-limitations) above) and need retry-safety guarantees stronger than "duplicate rows are harmless," add your own idempotency token to the calling workflow rather than assuming this Lambda enforces one.

> **Retry-policy note**: If you invoke this scanner as a Step Functions `Task` state chained after the Verified-Clean Recovery Point Guide's workflow (as the Restore-testing note above suggests), be aware that guide's own state machine defines `Catch` blocks but no `Retry` blocks on any state (see that guide's own Retry-policy note) — if you add this scanner as an additional state in that same state machine, a transient Comprehend throttle or S3 read blip on your inserted state will, by default, fall through to whatever `Catch` handling you wire up rather than retrying automatically, unless you explicitly add a `Retry` block to the state you insert. Don't assume the surrounding workflow's error-handling conventions include automatic retries just because `Catch` blocks are present elsewhere in the same state machine.

> **Concurrency note**: Unlike the FlexClone workflow in the Verified-Clean Recovery Point Guide (where two concurrent runs against the same volume can collide on a shared resource name), this scanner has no such collision risk — the DynamoDB key (`access_point_arn` + microsecond-precision `started_at`) means multiple concurrent invocations, even against the same access point, simply produce multiple independent report rows rather than overwriting each other or conflicting. The more relevant concurrency concern here is upstream: running several scanner invocations in parallel against different access points multiplies concurrent `DetectPiiEntities` calls, which can hit [Comprehend's account-level transactions-per-second quota](https://docs.aws.amazon.com/comprehend/latest/dg/guidelines-and-limits.html) and surface as the `TextSizeLimitExceededException`/throttling errors mentioned above, recorded per-file rather than failing the whole run. If you run this scanner concurrently across many access points on a schedule, monitor Comprehend throttling in CloudWatch and stagger invocations if you see `error` fields accumulating from throttling rather than genuine read failures.

---

## Comparison: Schema-Level vs Content-Level Classification

| Aspect | Schema-Level (Data Classification Guide) | Content-Level (this scanner) |
|--------|---------------------------------------------|-------------------------------|
| What it classifies | Which *fields* in audit/FPolicy events are PII (`UserName`, `ObjectName`) | Which *file contents* on the volume contain PII-shaped data |
| CSF 2.0 function | Identify (metadata-level asset understanding) | Identify (data-level asset understanding) |
| Mechanism | Static field classification matrix (documentation) | Amazon Comprehend `DetectPiiEntities` (ML inference) |
| Coverage | Every event field this repo's pipelines emit | Text/structured-data files matching scannable extensions only |
| Automation in this repo | ✅ Full (reference table) | ✅ Full (this scanner) |

> **Compliance note (HIPAA/FISC/SOC2)**: Combine both. The schema-level guide tells you which *pipeline fields* to restrict via vendor RBAC (dashboards showing `user`/`path` are displaying PII by definition). This scanner tells you which *volumes/files* likely contain PII in their content, informing where to apply DLP controls, access restrictions, or a formal data classification label at the storage layer. Neither replaces a manual review by your data protection officer for regulatory purposes — see the scope note in the Data Classification Guide.

> **Legal-hold note**: This differs from the Privacy note elsewhere in this guide — that one covers regulatory PII inventory judgment calls; this one covers what happens if the *scan itself* becomes relevant to litigation. Two considerations: (1) if a legal hold is in effect on a volume, running this scanner against it does not itself violate the hold (it's read-only and non-destructive — see Security Considerations), but the resulting DynamoDB report and its file-path findings may themselves become discoverable material, since they document what sensitive content existed and where at scan time; preserve scan reports under the same hold if the underlying data is subject to one. (2) The scanner never captures matched PII values (see Entity Aggregation above), so it cannot serve as a substitute for a legal team's own document review when specific PII values (not just entity types) need to be produced or redacted for a matter — route that to your standard eDiscovery tooling instead.

---

## Remaining Limitations

Being direct about what this scanner does **not** do:

1. **No document-format parsing.** Office documents (`.docx`, `.xlsx`, `.pdf`) are not text-extracted — this scanner only reads raw bytes and UTF-8-decodes them, which works for plain text/structured formats but produces garbage (and no useful findings) for binary Office/PDF formats. Extending this would require a document-parsing library (e.g., via a Lambda Layer) not currently included.
2. **English-centric extension list, but multi-language content detection.** The `SCANNABLE_EXTENSIONS` filter is format-based, not language-based — Comprehend itself supports 12 languages via `language_code`, but this scanner processes one language per invocation. Mixed-language volumes need either multiple runs with different `language_code` values or a language-detection pre-step (not implemented).

> **Patch-management note**: `COMPREHEND_SUPPORTED_LANGUAGES` and the PII entity type list this scanner relies on are hardcoded against Comprehend's capabilities as of this writing — AWS periodically adds supported languages and PII entity types to `DetectPiiEntities`. This scanner will not automatically pick up newly-supported languages until `COMPREHEND_SUPPORTED_LANGUAGES` (in both `content_classifier.py` and the CloudFormation template's inline Lambda code) is updated to match. Periodically check the [Comprehend supported languages page](https://docs.aws.amazon.com/comprehend/latest/dg/supported-languages.html) against this scanner's hardcoded list if your organization's data increasingly includes languages outside the current 12.
3. **Sampling, not full-file scanning, above 5 MB.** Large log or CSV files are only scanned in their first 500 KB by default — PII appearing later in a large file is not detected. Increase `DEFAULT_MAX_FILE_BYTES`/`sample_bytes` if this matters for your data, at increased Comprehend cost.
4. **Cost scales with file count and size.** Comprehend `DetectPiiEntities` is priced per unit of text processed. Scanning a large volume with many/large scannable files can generate meaningful Comprehend charges — the `max_files` cap exists specifically to bound this per invocation.

> **Capacity-planning note**: `max_files` bounds cost and runtime per invocation, but it is a count cap, not a throughput cap — the scanner calls `DetectPiiEntities` synchronously, one chunk at a time, with no explicit rate limiting or backoff beyond what the per-file `error` capture provides on throttling (see Error Handling above). If you plan to run this against many access points concurrently on a schedule (see the concurrency note above), size your expected concurrent invocation count against [Comprehend's account-level TPS quota](https://docs.aws.amazon.com/comprehend/latest/dg/guidelines-and-limits.html) the same way you would for any other shared-quota AWS service, rather than assuming `max_files` alone bounds your account's total Comprehend throughput across simultaneous runs.
5. **No automatic re-scan on file change.** This is an on-demand/scheduled scanner, not an event-driven one. Pair it with your own EventBridge Scheduler rule (following the pattern in the [Automated Response Guide](automated-response-guide.md#deployment)'s TTL cleanup schedule) if you want periodic re-classification.
6. **A Lambda timeout mid-scan loses the entire run's findings, not just the unscanned files.** The scanner writes its DynamoDB report once, after the full `ListObjectsV2`/classify loop completes — there is no partial-progress checkpoint. If `LambdaTimeoutSeconds` is exceeded partway through a large volume, Lambda kills the invocation before the `put_item` call, and no report (not even a partial one) is written for that run. Size `LambdaTimeoutSeconds` and `max_files` conservatively for your expected file count and average file size, and check CloudWatch Logs (not just the DynamoDB table) to distinguish "scan found nothing" from "scan never completed."

> **Restore-testing note**: If chaining this scanner after the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s workflow, sequencing matters — that workflow's `Cleanup` step always detaches the S3 Access Point and deletes the FlexClone, on both the success and failure paths (see that guide's Architecture). Invoke this scanner's classification *before* that workflow reaches `Cleanup`, either as an additional Step Functions state inserted ahead of it or as a synchronous follow-up call using the same execution's `access_point_arn` output — invoking it after the workflow has already completed will fail because the access point no longer exists.

---

## Prerequisites

### AWS Permissions

The Lambda execution role requires:

```
# S3 Access Point object read
- s3:ListBucket / s3:GetObject  (scoped to arn:aws:s3:*:*:accesspoint/*)

# Amazon Comprehend (no resource-level permissions supported — DetectPiiEntities
# is a stateless synchronous inference API with no addressable resource)
- comprehend:DetectPiiEntities

# Classification report ledger
- dynamodb:PutItem  (scoped to the report table)
```

> **IAM-design note**: `ScannerLambdaRole` grants `s3:ListBucket`/`s3:GetObject` scoped to `arn:aws:s3:*:*:accesspoint/*` — a wildcard across *all* access points in the account/Region, not just the one you intend to scan (see the CloudFormation template; this is necessary because the access point ARN is only known at invocation time, not deploy time, so a tighter `Resource` can't be expressed in the role's static policy). In practice this means the scanner's execution role can read from *any* S3 Access Point in the account if invoked with a different `access_point_arn` than intended — a broader reach than "this one scanner, this one access point" implies. If your organization's IAM standard requires resource-level scoping even at the cost of flexibility, consider a wrapper Lambda or Step Functions task that validates `access_point_arn` against an allow-list before invoking this scanner, since IAM policy alone cannot enforce that constraint here.

### An Existing S3 Access Point

This scanner does **not** create or manage S3 Access Points. Pass the ARN of one that already exists. Two common sources:
- An access point you manage directly against a production or DR volume
- The `access_point_arn` output from the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s `AttachAccessPoint` step, scanning the same FlexClone used for ransomware verification

> **Network origin determines whether you need `VpcId`**: a [VPC-scoped access point](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/access-points-for-fsxn-vpc.html) — which is what `AttachAccessPoint` creates — has no route from outside the VPC it's bound to; a Lambda outside that VPC cannot reach it at all, regardless of IAM permissions. An internet-origin access point (any access point without a `VpcConfiguration`) is reachable without a VPC. See [Deployment](#deployment) below for the `VpcId` parameter that governs which mode this stack deploys in.

---

## Deployment

### Deploy Mode 1: Standalone (Internet-Origin Access Point)

Use this when scanning an access point that does **not** have a `VpcConfiguration`. It is the simpler, cheaper mode, since no VPC Endpoints are created:

```bash
aws cloudformation deploy \
  --template-file shared/templates/content-classification-scanner.yaml \
  --stack-name fsxn-content-classification \
  --parameter-overrides \
    DefaultLanguageCode=en \
    DefaultMaxFiles=500 \
    NotificationTopicArn=<optional-sns-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM
```

### Deploy Mode 2: In-VPC (VPC-Scoped Access Point)

Use this when scanning a VPC-scoped access point. It is required if chaining after the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s `AttachAccessPoint` step, since that step always creates a VPC-scoped access point:

```bash
aws cloudformation deploy \
  --template-file shared/templates/content-classification-scanner.yaml \
  --stack-name fsxn-content-classification \
  --parameter-overrides \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-1>,<subnet-2> \
    SecurityGroupId=<sg-id> \
    RouteTableIds=<route-table-1>,<route-table-2> \
    DefaultLanguageCode=en \
    DefaultMaxFiles=500 \
    NotificationTopicArn=<optional-sns-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM
```

Setting `VpcId` changes both the scanner Lambda (adds `VpcConfig`) and the stack's resources (adds S3/DynamoDB Gateway Endpoints and Comprehend/SNS Interface Endpoints, unless `CreateVpcEndpoints=false` because your VPC already has them — e.g., reusing `restore-verification.yaml`'s VPC only covers Secrets Manager/STS/FSx from that stack, not these four).

### What the Stack Creates

- One Lambda function (`{stack-name}-scanner`) — with or without `VpcConfig` depending on `VpcId`
- DynamoDB report table (`{stack-name}-reports`)
- CloudWatch Logs (365-day retention — reports double as compliance evidence)
- If `VpcId` is set and `CreateVpcEndpoints=true` (default): S3 Gateway Endpoint, DynamoDB Gateway Endpoint, Comprehend Interface Endpoint, SNS Interface Endpoint

> **Change-management note**: Like the Verified-Clean Recovery Point Guide's stack, this deployment is purely additive — it creates a new Lambda, a new DynamoDB table, and (in Deploy Mode 2) new VPC Endpoints, without modifying any existing FSx for ONTAP resource, S3 Access Point, or networking configuration. This scanner never creates or manages S3 Access Points itself (see Prerequisites above) — it only reads from ones you point it at — so a bad deploy's blast radius is limited to this stack's own resources, and a rollback via `cloudformation delete-stack` removes them without touching the access point or volume being scanned. As with the recovery-verification stack, flag the VPC Endpoint duplicate-conflict risk in your change ticket if deploying Mode 2 into a VPC that may already have some of these four endpoints from another stack.

> **Resource-tagging note**: As with `restore-verification.yaml`, only `ClassificationReportTable` carries a `Project`/`Purpose` tag pair — the `ScannerFunction` Lambda has no `Tags` property in this stack's CloudFormation template. If your account enforces tagging policy via AWS Config rules or Service Control Policies, add an equivalent `Tags` block to the `AWS::Lambda::Function` resource before deploying into a governed account, or this Lambda's compute spend will not be attributable through cost-allocation tags the way the DynamoDB table's spend is.

### Invoking the Scanner

```bash
aws lambda invoke \
  --function-name fsxn-content-classification-scanner \
  --payload '{
    "access_point_arn": "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/verify-vol-data-20260710",
    "language_code": "en",
    "max_files": 500
  }' \
  --cli-binary-format raw-in-base64-out \
  response.json
```

### Chaining After Recovery Verification

To scan the same isolated FlexClone used for ransomware verification (rather than production data), invoke this Lambda with the `access_point_arn` produced by the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s `AttachAccessPoint` Step Functions task — either by adding a state to that state machine, or as a separate follow-up invocation using the same execution's output.

> **This chaining pattern requires Deploy Mode 2 (In-VPC)**: `AttachAccessPoint` always creates a VPC-scoped access point, and this scanner must run inside that same VPC to reach it — see the network-origin note in [Prerequisites](#an-existing-s3-access-point) above. Deploying this stack in standalone mode (no `VpcId`) against a chained FlexClone access point will fail at the `ListObjectsV2` call every time, since there is no network route to try intermittently.

### Quick Validation

Before pointing this scanner at real data, confirm it works end-to-end against a synthetic file. This exact sequence is what this project's own E2E verification used, against Deploy Mode 1 (standalone, no `VpcId`):

```bash
# 1. Create a synthetic PII file — no real personal data, safe to commit
#    to a scratch location or delete immediately after the test
cat > /tmp/pii-test-sample.txt <<'EOF'
Support Ticket #48213
Name: John Sample Doe
Email: john.sample.doe@example.com
Phone: 555-0142-9981
SSN: 078-05-1120
Address: 123 Example Street, Springfield, IL 62704
EOF

# 2. Upload it through an S3 Access Point that has write access to your
#    FSx for ONTAP volume (a read-only access point, if that's what you
#    have, will reject the PutObject — see Prerequisites above for the
#    read-only-vs-read-write access point distinction)
aws s3api put-object \
  --bucket <your-access-point-arn> \
  --key validation/pii-test-sample.txt \
  --body /tmp/pii-test-sample.txt

# 3. Invoke the scanner directly (bypasses any EventBridge/Step Functions
#    trigger you may wire up later, isolating the Lambda + Comprehend path)
aws lambda invoke \
  --function-name fsxn-content-classification-scanner \
  --payload '{"access_point_arn":"<your-access-point-arn>","max_files":50}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/scan-response.json
cat /tmp/scan-response.json

# 4. Confirm the finding landed in the ledger, not just the Lambda response
aws dynamodb get-item \
  --table-name fsxn-content-classification-reports \
  --key '{"access_point_arn":{"S":"<your-access-point-arn>"},"started_at":{"S":"<started_at from step 3 response>"}}'

# 5. Clean up the synthetic file so it doesn't linger in production storage
aws s3api delete-object --bucket <your-access-point-arn> --key validation/pii-test-sample.txt
rm -f /tmp/pii-test-sample.txt /tmp/scan-response.json
```

A successful run reports `"files_with_pii": 1` (or more, if other scannable files already exist under the access point) with `pii-test-sample.txt` listed in `findings`, showing `NAME`/`EMAIL`/`PHONE`/`SSN`/`ADDRESS` entity types at confidence scores typically above 0.9 for the well-formed fields (`EMAIL`, `PHONE`, `SSN`, `ADDRESS`) and lower for `NAME` (Comprehend's name-detection confidence varies more with surrounding context than the other types). If step 3 times out or returns an access-denied error instead, revisit [Prerequisites](#an-existing-s3-access-point) — the most common cause is invoking against a VPC-scoped access point ARN from a standalone (non-VPC) deployment, or vice versa.

---

## Configuration Reference

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `DefaultLanguageCode` | en | Amazon Comprehend language code for `DetectPiiEntities` (12 supported languages) |
| `DefaultMaxFiles` | 500 | Cap on files scanned per invocation (cost/runtime control); overridable per-invocation via the `max_files` payload key |
| `LambdaMemorySize` | 512 MB | Scanner Lambda memory |
| `LambdaTimeoutSeconds` | 600 | Scanner Lambda timeout — size to your expected file count |

> **Cost note**: [Amazon Comprehend's synchronous PII detection pricing](https://aws.amazon.com/comprehend/pricing/) is metered per 100-character unit processed, with a per-request minimum — cost here scales with total scanned *content volume*, not file count, so a handful of very large text files can cost more than thousands of small ones. Lambda invocation cost and DynamoDB `PutItem` cost are comparatively negligible next to Comprehend charges for any non-trivial scan. Before running this against a large volume, estimate cost from a representative sample (scan one department's share with `max_files` capped low, note the aggregate size scanned, then extrapolate) rather than assuming a fixed per-file cost — see [Remaining Limitations](#remaining-limitations) item 4 and the FAQ for related guidance on bounding a single run's cost.

---

## Security Considerations

- **Data minimization by design**: findings never contain the matched PII text — only entity type, count, and confidence score. See [Entity Aggregation](#entity-aggregation--data-minimization-by-design) above.
- **No write access to scanned volumes**: the scanner only calls `s3:GetObject`/`s3:ListBucket` — it cannot modify, redact, or delete the files it scans.
- **Report table access should be restricted**: while the report itself doesn't contain PII values, it does contain *where* PII-shaped data was found (file paths) and *how much* — apply the same access restrictions you'd apply to any inventory of sensitive-data locations.
- **Comprehend is a regional, AWS-managed service**: text sent to `DetectPiiEntities` is processed within the AWS Region you invoke it in; see [Amazon Comprehend's data privacy documentation](https://docs.aws.amazon.com/comprehend/latest/dg/data-privacy.html) for the service's own data handling commitments.

> **Data-residency note**: File content read by this scanner never leaves the AWS Region you deploy the stack into — the Lambda, the `DetectPiiEntities` calls, and the DynamoDB report table are all regional resources, and the [Comprehend data-privacy point](https://docs.aws.amazon.com/comprehend/latest/dg/data-privacy.html) above confirms Comprehend itself does not process data outside the invoked Region. This is a straightforward affirmative answer for a residency questionnaire. One nuance: if you deploy this scanner in multiple Regions against a multi-Region fleet, each Region's DynamoDB report table is independent — there is no cross-Region replication or aggregation of classification reports, so a residency-driven decision to keep each Region's scan results within that Region is already the default behavior, not something you need to additionally configure.
- **VPC-scoped access points have no route from outside their bound VPC**: this is a network property of the access point itself (see [AWS's network-origin comparison](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/configuring-network-access-for-s3-access-points.html)), not something IAM alone can work around. When scanning a VPC-scoped access point, deploy this stack with `VpcId` set (Deploy Mode 2) so the scanner Lambda runs inside that VPC with a route to S3 via the Gateway Endpoint this stack creates.

> **Third-party-risk note**: For vendor-assessment questionnaires that ask "is data sent to a third party" or "where is data processed" — this pipeline sends file content to Amazon Comprehend, a first-party AWS managed service, not an external third-party vendor; processing stays within the AWS Region you deploy to (see the data-privacy point above). Data retention: this scanner does not delete anything on its own — the DynamoDB report table has no TTL configured by default, so classification reports persist indefinitely until you delete the stack or add your own TTL attribute; factor that into your organization's data-retention schedule if reports are subject to one, since the reports themselves contain file paths (Sensitive per the Data Classification Guide) even though they exclude raw PII values.

> **Audit-evidence note**: If this scanner's reports are cited as evidence for a data-classification or PII-inventory control, an auditor testing operating effectiveness will want to see, beyond the DynamoDB row itself: (1) that scans are run at the frequency your control asserts (periodic, on-demand-with-a-defined-trigger, etc.) — this scanner has no built-in schedule (see Remaining Limitations item 5), so the schedule enforcing "periodic" is external to this Lambda and needs to be evidenced separately (e.g., an EventBridge rule's own CloudFormation definition and its CloudWatch invocation history); (2) that someone reviewed a report showing `files_with_pii > 0` and took a documented action — this scanner's SNS notification is evidence an alert fired, not evidence of a human response. As with the recovery-verification workflow's own audit-evidence note, pair the automated report with a manual review/attestation artifact if this scanner backs a formal compliance control.

---

## Testing

The `content_classifier.py` module has 23 unit tests covering:

| Category | What's Verified |
|----------|-----------------|
| Text chunking | Empty input, single chunk, multi-chunk splitting, content-preserving reassembly, hard-split of an oversized single line, chunk target below Comprehend's limit |
| Per-file classification | No PII found, PII found with aggregated counts/confidence, multi-chunk aggregation, read failure handling, Comprehend failure handling, empty-file skip, oversized-file Range sampling, confidence rounding |
| Volume-level orchestration | Unsupported language rejection, unscannable-extension filtering, zero-byte skip, PII density aggregation, clean-file exclusion from findings, error-file inclusion in findings, `max_files` cap enforcement, report serialization capping, sampled-file tracking |

```bash
python3 -m pytest shared/python/tests/test_content_classifier.py -v
# 23 passed in 0.08s
```

> **Test-coverage note**: All 23 tests mock the `s3` and `comprehend` boto3 clients directly — no test calls a real S3 Access Point or Amazon Comprehend endpoint. This makes the suite fast and independent of AWS credentials, but it also means the tests validate this module's *own* logic (chunking, aggregation, error handling) correctly, not whether the mocked Comprehend response shape still matches the real `DetectPiiEntities` API. Re-run a manual smoke test against a real (non-production) access point and Comprehend endpoint after any `boto3`/`botocore` version bump, and don't treat `pytest` passing as proof the Lambda will behave identically in a live AWS account.

> **License note**: Like the recovery-verification stack, this scanner's only runtime dependencies (`boto3`, `botocore`) ship with the Lambda `python3.12` managed runtime rather than being vendored in a `requirements.txt` — both are Apache License 2.0, permissive with no copyleft obligations, so license risk here is low. The same caveat applies to automated SBOM/license scanning: scanning this stack's inline CloudFormation `ZipFile` code will not surface these dependencies, since they aren't declared anywhere in this repo — scan against the Lambda managed runtime's published manifest instead if your supply-chain review process requires it.

> **Observability note**: The stack ships CloudWatch Logs only — no CloudWatch Alarm on Lambda errors, throttles, or timeouts. Given the "timeout loses the whole run" behavior noted in [Remaining Limitations](#remaining-limitations) item 6, add your own alarm on this Lambda's `Errors`/`Duration` metrics (or route failures through the SNS topic already wired for PII-found notifications) so a silently-timed-out scan doesn't read as "no PII found" by omission.

> **Operational-triage note**: This scanner is not a paging-worthy service by design — it's an on-demand/scheduled batch job (see [Remaining Limitations](#remaining-limitations) item 5), not a request-serving pipeline with an SLO. If you do get paged because a scheduled invocation failed: check CloudWatch Logs for the invocation first (see the FAQ's "zero findings" question for why the DynamoDB table alone can be misleading), then re-invoke manually with the same `access_point_arn` once the underlying cause (timeout, throttling, or a stale/deleted access point if chained after `restore-verification.yaml`) is addressed. There is no ledger row to clean up and no orphaned resource to worry about on failure — this scanner never creates infrastructure, only reads from an access point someone else manages.

> **Failure-injection note**: A useful failure-injection test for this scanner: throttle or deny `comprehend:DetectPiiEntities` mid-run (e.g., a temporary IAM deny) against a multi-file volume, and confirm the actual behavior matches what [Remaining Limitations](#remaining-limitations) item 6 describes — that files scanned *before* the injected failure are lost too, not just the ones after it, because the report is written once at the end rather than incrementally. This is worth confirming experimentally rather than trusting the documentation alone, since "lose only the unscanned files" vs. "lose the entire run" is an easy assumption to get backwards when reading code quickly. A second worthwhile experiment: kill the Lambda via a forced timeout partway through a large `max_files` batch and confirm CloudWatch Logs show enough detail (which file it was on, how many completed) to distinguish that scenario from a clean "no PII found" run purely from the logs, without needing to re-run the scan to find out.

> **SNS-delivery note**: Like the recovery-verification workflow's `RecordVerdict` Lambda, this scanner's SNS `publish` call is wrapped in a bare try/except that only logs a warning on failure (`logger.warning("Notification failed: %s", e)`) — a failed publish (deleted topic, permissions drift, throttling) never surfaces as a Lambda error or a failed invocation, only as a log line. Because this scanner only calls `sns.publish` when `files_with_pii > 0` (see the CloudFormation template's inline handler code), a silently-failed notification for a genuinely PII-containing volume looks identical, from the outside, to a scan that found no PII at all — both produce "no notification arrived." If your PII-discovery process depends on this notification firing reliably, alarm on this Lambda's own `"Notification failed"` log pattern rather than treating notification silence as evidence of a clean scan.

> **Encryption-at-rest note**: `ClassificationReportTable`'s `SSESpecification` enables encryption but (like the recovery-verification stack's ledger table) does not set a customer-managed KMS key — it uses the AWS-owned DynamoDB key. If your data-classification program requires customer-managed KMS keys for anything that documents where sensitive content lives (which this report table does, even though it excludes the PII values themselves — see Security Considerations), add `SSEType: KMS`/`KMSMasterKeyId` explicitly; this template does not expose that as a parameter today.

---

## Related Documents

- [Cyber Resilience Capability Map](cyber-resilience-capability-map.md#identify-id) — the Identify-function gap this scanner implements a fix for
- [Data Classification Guide](data-classification.md) — the schema-level (field-name) classification this scanner complements at the content level
- [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) — the FlexClone + S3 Access Point pattern this scanner can chain after, for zero-production-impact scanning
- [Automated Response Guide](automated-response-guide.md) — the containment-phase module whose protective snapshots are a natural scan target via the recovery verification workflow
- [Governance & Compliance](governance-and-compliance.md) — where classification reports fit as Identify-function evidence

## FAQ

**Q: Does this scanner redact or remove PII from files?**
A: No. This is a discovery-only tool, matching the CSF 2.0 Identify function's scope (inventory and understanding, not remediation). Redaction would require a separate write path this module deliberately does not implement.

**Q: Can I scan a production volume directly, not just a FlexClone?**
A: Yes. Pass any S3 Access Point ARN, including one attached directly to a production volume. The FlexClone pattern in the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md) is recommended when you want zero read-load impact on production and want to combine the scan with ransomware verification, but it's not required.

**Q: Why does the scanner skip Office documents and PDFs?**
A: This module intentionally scopes to plain-text and structured-data formats to avoid depending on a document-parsing library. If your volumes are dominated by Office/PDF content, consider Amazon Textract or a document-extraction Lambda Layer as a pre-processing step feeding this scanner's `classify_object` logic with extracted text instead of raw bytes.

**Q: How do I avoid excessive Comprehend costs on a very large volume?**
A: Use `max_files` to cap the per-invocation scan size, and consider running the scanner against a representative subset (e.g., one department's share) rather than an entire multi-terabyte volume in one pass. Comprehend pricing is per-unit of text processed, so cost scales with total scanned content, not file count alone.

**Q: I deployed standalone (no `VpcId`) and every invocation fails at `ListObjectsV2` — why?**
A: The `access_point_arn` you're passing is almost certainly VPC-scoped (most commonly, one produced by the [Verified-Clean Recovery Point Guide](verified-recovery-point-guide.md)'s `AttachAccessPoint` step). A VPC-scoped access point has no network route from outside its bound VPC — this fails deterministically, not intermittently, regardless of IAM permissions. Redeploy with `VpcId`/`SubnetIds`/`SecurityGroupId`/`RouteTableIds` set (Deploy Mode 2) so the scanner runs inside the same VPC the access point is bound to.

**Q: Does a low `Score` (confidence) on a PII entity mean I can ignore it?**
A: Not automatically. This scanner doesn't apply a confidence floor for you (see the Privacy note under [Entity Aggregation](#entity-aggregation--data-minimization-by-design)). It records the highest confidence seen per entity type per file so *you* can apply a threshold appropriate to your regulatory context; it does not decide for you what confidence level constitutes "found" PII.

**Q: My scan of a large volume shows zero findings in DynamoDB — did it actually run?**
A: Check CloudWatch Logs for that invocation before concluding "no PII found." Because the report is written once at the end of the handler (see [Remaining Limitations](#remaining-limitations) item 6), a Lambda timeout partway through a large scan produces the same DynamoDB-side symptom (no report row) as a scan that genuinely found nothing — the two are only distinguishable via the Lambda's own logs/duration metrics.

**Q: A user reports the scanner "just says no PII found" but they're confident the volume has sensitive data — first troubleshooting step before escalating?**
A: Check the response's `files_scanned` and `files_skipped_unscannable` counts first, not just `files_with_pii` — a common cause is that the volume is dominated by extensions outside `SCANNABLE_EXTENSIONS` (Office documents, PDFs, or an unlisted text format), in which case most files were skipped, not scanned-and-cleared. See [Remaining Limitations](#remaining-limitations) items 1 and the FAQ's Office/PDF question. Second common cause: the scan hit the `max_files` cap before reaching the files that actually contain PII (see [Capacity-planning note](#configuration-reference) note) — check whether `files_scanned` equals the configured/requested `max_files`, which indicates the cap was hit rather than the volume being exhausted.

**Q: We're onboarding an existing, previously-unscanned FSx for ONTAP volume to this scanner — any special first-run considerations?**
A: Run the first scan with a conservative `max_files` value and review the resulting `files_skipped_unscannable`/`files_sampled_too_large` counts before scheduling recurring runs — this tells you up front how much of the volume's content this scanner can actually see (see Remaining Limitations) versus how much will require a document-parsing extension or a higher `sample_bytes` value to cover. There's no state or metadata this scanner needs a volume to have been pre-configured with; any existing S3 Access Point works immediately.

**Q: A scan found PII but nobody was notified — how do we tell the difference between "no PII found" and "notification failed"?**
A: Check the DynamoDB report row's `files_with_pii` field directly rather than relying on notification arrival as a proxy — this scanner only attempts an SNS publish when `files_with_pii > 0`, and that publish call can fail silently (see the SNS-delivery note under Security Considerations). If `files_with_pii` is greater than zero in the report but no notification arrived, the scan worked correctly and the notification delivery is what failed; check CloudWatch Logs for a `"Notification failed"` line to confirm.
