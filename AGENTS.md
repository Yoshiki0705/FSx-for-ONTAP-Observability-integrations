# AGENTS.md — FSx for ONTAP Observability Integrations

## Project Overview

Serverless observability integrations shipping Amazon FSx for NetApp ONTAP audit logs to 9 vendors (all E2E verified) via S3 Access Points. CloudFormation (YAML) + Python 3.12 Lambda + TypeScript tooling. Multi-vendor pattern library with fully synchronized bilingual (ja/en) documentation.

**Current state**: Phase 1 (Foundation) and Phase 3 (Enterprise Features) complete. 9 vendors E2E verified. See `ROADMAP.md` for Phase 4 plans.

## Key Commands

```bash
# Install dependencies
npm install

# TypeScript typecheck
npx tsc --noEmit

# Lint
npm run lint

# Run all TypeScript tests
npm test

# Run ALL Python tests (9 vendors + shared layers)
python -m pytest \
  integrations/datadog/tests/ \
  integrations/grafana/tests/ \
  integrations/splunk-serverless/tests/ \
  integrations/otel-collector/tests/ \
  integrations/new-relic/tests/ \
  integrations/elastic/tests/ \
  integrations/dynatrace/tests/ \
  integrations/sumo-logic/tests/ \
  integrations/honeycomb/tests/ \
  shared/lambda-layers/ems-parser/tests/ \
  -v --tb=short

# Run Python tests for a specific vendor
python -m pytest integrations/datadog/tests/ -v

# Validate CloudFormation templates
pip install cfn-lint
cfn-lint integrations/*/template.yaml
cfn-lint shared/templates/*.yaml

# Run cfn-guard critical security rules
cfn-guard validate -d integrations/*/template*.yaml -r guard/rules/critical-security.guard --show-summary fail

# Check bilingual documentation sync
bash shared/scripts/check-bilingual-sync.sh

# Pre-flight deployment validation (check VPC Endpoints, SG, ONTAP S3 server)
bash shared/scripts/preflight-check.sh --vpc-id vpc-xxx --profile automated-response
bash shared/scripts/preflight-check.sh --list-profiles

# Deploy a vendor integration
bash integrations/<vendor>/scripts/deploy.sh

# Full observability setup (deploy + alerts + forensics dashboard + verify)
bash integrations/<vendor>/scripts/setup-full-observability.sh

# Post-deployment E2E verification only
bash integrations/<vendor>/scripts/verify.sh

# Create security alerts only
bash integrations/<vendor>/scripts/create-alerts.sh

# Run full test suite
bash shared/scripts/test.sh
```

## FPolicy Operations

```bash
# Build and push FPolicy server image (MUST use linux/amd64 for Fargate)
bash shared/fpolicy-server/build-and-push.sh v2-timeout-fix

# Start/stop FPolicy Fargate service
bash shared/scripts/fpolicy-fargate-control.sh start
bash shared/scripts/fpolicy-fargate-control.sh stop
bash shared/scripts/fpolicy-fargate-control.sh status

# Update ONTAP FPolicy External Engine IP after task restart
bash shared/scripts/fpolicy-update-engine-ip.sh --auto
```

## Project Structure

```
integrations/<vendor>/       # Vendor-specific implementations (9 vendors, all E2E verified)
  ├── template.yaml          # CloudFormation (single self-contained stack)
  ├── template-ems.yaml      # EMS webhook handler stack
  ├── template-fpolicy.yaml  # FPolicy EventBridge handler stack
  ├── lambda/handler.py      # Python 3.12 Lambda function
  ├── scripts/               # deploy.sh, cleanup.sh
  ├── docs/{ja,en}/          # Bilingual setup guides
  └── tests/                 # pytest unit tests

shared/
  ├── python/                # Shared Python modules (importable by all vendors)
  │   ├── auth_cache.py      # Secrets Manager TTL cache + reload-on-401/403
  │   ├── object_ledger.py   # DynamoDB per-object state tracker (Level 3)
  │   └── sqs_buffer.py      # SQS producer + consumer with partial batch failures
  ├── lambda-layers/         # Reusable Lambda Layers (log-parser, ems-parser, s3ap-reader)
  ├── templates/             # Shared CloudFormation templates
  │   ├── prerequisites.yaml       # S3 AP + EventBridge Scheduler + checkpoint
  │   ├── ems-webhook-apigw.yaml   # API Gateway + Lambda Authorizer
  │   ├── fpolicy-server-fargate.yaml  # ECS Fargate + SQS
  │   ├── object-ledger.yaml       # DynamoDB table + poison-pill alarm (Level 3)
  │   ├── sqs-buffering.yaml       # SQS buffer queue + DLQ + alarms (Level 3)
  │   ├── secrets-rotation-sample.yaml  # Auto-rotation Lambda (all vendors)
  │   └── multi-account-stackset.yaml  # StackSets deployment (Enterprise)
  ├── fpolicy-server/        # FPolicy TCP server (Go, linux/amd64)
  └── scripts/               # Operational scripts
      ├── deploy.sh, test.sh, cleanup-vendor.sh
      ├── check-bilingual-sync.sh   # ja/en doc sync verification
      ├── fpolicy-fargate-control.sh
      ├── fpolicy-update-engine-ip.sh
      └── pre-push-security-check.sh

guard/rules/                 # cfn-guard policy rules
  ├── critical-security.guard    # BLOCKING in CI (wildcard IAM, secrets in env, DLQ encryption)
  ├── lambda-security.guard      # Advisory (timeout, memory, DLQ)
  └── secrets-management.guard   # Advisory (descriptions, no hardcoded values)

docs/
  ├── en/                    # English documentation (50 files)
  │   ├── runbooks/          # DLQ replay, Lambda errors, checkpoint staleness
  │   ├── pipeline-slo.md    # SLO definitions + Go/No-Go criteria
  │   ├── data-classification.md  # PII field mapping + handling patterns
  │   ├── compliance-evidence-pack.md  # ISMAP/FISC/SOC2 evidence template
  │   ├── multi-account-deployment.md  # StackSets guide
  │   ├── cross-region-replication.md  # DR patterns (Active-Passive/Active-Active/S3 CRR)
  │   └── ...
  ├── ja/                    # Japanese documentation (56 files, fully synced)
  └── images/                # Shared images

.github/
  ├── workflows/ci.yaml      # Full CI: all vendors pytest + coverage + cfn-lint + cfn-guard + bilingual sync
  └── ISSUE_TEMPLATE/        # Bug report + feature request templates

ROADMAP.md                   # Phase 1-4 milestones
CONTRIBUTING.md              # Contribution guidelines
```

## Code Style

### Python (Lambda functions)

```python
"""Module docstring: one-line summary.

Extended description if needed.
"""

import json
import logging
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_BATCH_SIZE_BYTES = 5 * 1024 * 1024  # Constants: UPPER_SNAKE_CASE


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point. Type hints required. Google-style docstrings."""
    ...
```

- Python 3.12, PEP 8, type hints mandatory
- Use `urllib3` for HTTP (included in Lambda runtime), not `requests`
- Secrets from Secrets Manager, never environment variables for sensitive values
- Exponential backoff for all vendor API calls (max 3 retries)
- Batch processing respecting vendor size limits

### TypeScript

- Strict mode, named exports only
- ESLint + Prettier formatting
- `@aws-sdk/client-*` v3 (modular SDK)

### CloudFormation (YAML)

- 2-space indent
- PascalCase resource logical IDs: `LambdaExecutionRole`, `DeadLetterQueue`
- Stack name pattern: `fsxn-<vendor>-integration`
- Always include: IAM least-privilege, DLQ, CloudWatch Alarms

## Non-Obvious Patterns

### ⚠️ CRITICAL: FSx ONTAP S3 Access Points — Network Constraints

**VPC-internal Lambda with only a Gateway Endpoint timed out accessing Internet-origin FSx ONTAP S3 Access Points in our environment.**

This is the #1 source of deployment failures. The observed behavior is that Internet-origin S3 APs require an internet-routed path (NAT Gateway or VPC-external Lambda) when accessed from within a VPC.

| Lambda Placement | S3 AP Access | ONTAP REST API Access | Recommendation |
|-----------------|-------------|----------------------|----------------|
| **VPC 外 (no VPC config)** | ✅ Works | ❌ Requires VPC | Simplest for S3 AP only |
| **VPC 内 + S3 Gateway EP only** | ⚠️ TIMEOUT (Internet-origin AP) | ✅ Works | Use NAT or VPC-origin AP |
| **VPC 内 + NAT Gateway** | ✅ Works | ✅ Works | Production recommended |
| **VPC 内 + VPC-origin AP + Gateway EP** | ✅ Expected per AWS docs | ✅ Works | Requires VPC-origin AP creation |

**Observed behavior**: In our environment (Internet-origin S3 AP), VPC Lambda with only a Gateway Endpoint timed out. AWS [documents](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/configuring-network-access-for-s3-access-points.html) that VPC-origin access points work with Gateway Endpoints for traffic originating within the bound VPC. The network origin cannot be changed after creation.

**Design pattern for this project**:
- Lambda functions that ONLY read from S3 AP → Deploy **outside VPC** (simplest, lowest cost)
- Lambda functions that need BOTH S3 AP + ONTAP REST API → Deploy **in VPC with NAT Gateway**
- Lambda functions that ONLY call ONTAP REST API → Deploy **in VPC** (no NAT needed if using Interface VPC Endpoints for FSx)

### S3 Access Points for FSx ONTAP — ARN and IAM

FSx ONTAP S3 Access Points provide dual-protocol (NFS/SMB + S3) access to the same data without copying.

**Correct ARN format**:
```
arn:aws:s3:{region}:{account-id}:accesspoint/{access-point-name}
```

**IAM policy resource format**:
```yaml
# For GetObject/PutObject on objects:
Resource: arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap/object/*

# For ListBucket on the access point itself:
Resource: arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap
```

**boto3 usage** — Use the AP ARN as the `Bucket` parameter:
```python
s3_client.get_object(
    Bucket="arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap",
    Key="audit/svm-prod-01/2026/01/15/audit.json"
)
```

**S3 AP Resource Policy (same-account)**: For same-account access, the IAM identity policy alone is sufficient — an AP resource policy is NOT required. FSx for ONTAP S3 APs work without `put_access_point_policy` when the caller is in the same AWS account as the AP. Add an explicit AP resource policy only for cross-account access or when using condition keys (e.g., `aws:PrincipalAccount`).

### ⚠️ CRITICAL: AD-Joined SVM + S3 Access Point — AD DC Must Be Reachable

**On an AD-joined SVM (CIFS enabled), ALL S3 AP data operations (ListObjectsV2, GetObject, PutObject) require AD DC connectivity.** If AD DCs are unreachable, data operations return `AccessDenied` even though IAM/policy layers are correct. `HeadBucket` (metadata-only) succeeds — this is a false positive that makes diagnosis confusing.

**Root cause**: ONTAP performs unix→win reverse name-mapping lookup on every data operation through S3 AP. This requires LDAP/Kerberos connectivity to AD DCs.

**Symptoms**:
- `HeadBucket` → 200 OK ✅
- `ListObjectsV2` → `AccessDenied` ❌
- IAM policy, AP resource policy, Security Group, VPC Endpoint all correct
- Error message says "Access Denied" with no additional detail

**Pre-flight check (in CreateFlexClone Lambda or any S3 AP workflow)**:
```python
# Check if SVM has CIFS (AD-joined)
cifs_data = _request("GET", f"/protocols/cifs/services?svm.name={svm_name}&fields=ad_domain.fqdn")
if cifs_data.get("records"):
    # CIFS enabled — verify DC reachability
    dc_check = _request("GET", f"/protocols/cifs/domains?svm.name={svm_name}&fields=discovered_servers")
    # If discovered_servers is explicitly empty → AD DC unreachable → fail fast
```

**Resolution**: Ensure AD DCs (listed in SVM DNS config) are running and network-reachable from the SVM's ENIs. If AD was deleted/recreated, update SVM DNS IPs to new DC addresses.

**Impact on Step Functions workflows**: The `restore-verification.yaml` template includes this check in `CreateFlexClone` Lambda, failing immediately with `AD CONNECTIVITY FAILURE` instead of waiting 30+ min for FSx discovery + AP creation only to hit AccessDenied at scan time.

### FSx ONTAP S3 AP — Unsupported S3 Features

The following S3 features are NOT supported on FSx ONTAP S3 Access Points:

| Feature | Status | Workaround |
|---------|--------|-----------|
| S3 Event Notifications / EventBridge | ❌ Not supported | Use EventBridge Scheduler (polling + checkpointing) |
| GetBucketNotificationConfiguration | ❌ Not supported | N/A — this is why we use a separate S3 bucket for audit logs |
| Object Lifecycle policies | ❌ Not supported | Implement custom cleanup Lambda |
| Object Versioning | ❌ Not supported | Use DynamoDB for version tracking |
| Presigned URLs | ❌ Not supported | Copy to standard S3 + presign |
| SSE-KMS (custom keys) | ❌ SSE-FSX only | Use FSx volume-level KMS encryption |
| PutObject > 5GB | ❌ 5GB limit | Multipart upload within 5GB |

**Key implication for this project**: We use a **standard S3 bucket** as the audit log destination (which supports EventBridge notifications), NOT the FSx ONTAP S3 Access Point directly. The S3 AP is used for Lambda to read the logs from the bucket.

Reference: [AWS Docs — S3 AP API Support](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/access-points-for-fsxn-object-api-support.html) | [AWS Blog — S3 Access Points for FSx](https://aws.amazon.com/blogs/storage/bridge-legacy-and-modern-applications-with-amazon-s3-access-points-for-amazon-fsx/)

### ⚠️ S3 Access Points on AD-Joined SVMs — AD DC Reachability Required

**On AD-joined SVMs (CIFS enabled), ALL S3 AP data operations require active connectivity to the AD domain controllers.** If AD DCs are unreachable, ListObjectsV2 / GetObject / PutObject return `AccessDenied`, even though HeadBucket succeeds.

This is because ONTAP's multiprotocol identity pipeline performs a `unix→win` reverse lookup for every file system operation on CIFS-enabled SVMs, regardless of the volume's security style or the AP's file system identity type.

**Dual-layer authorization flow (AD-joined SVM)**:
```
S3 API Request → IAM evaluation (Pass) → s3_unix name-mapping → UNIX UID resolution
  → unix→win reverse lookup (requires AD DC) → File system authorization
```

| Symptom | Root Cause |
|---------|-----------|
| HeadBucket: ✅ / ListObjectsV2: ❌ AccessDenied | AD DCs unreachable (does NOT mean IAM or AP policy issue) |
| AP Lifecycle: AVAILABLE but data ops fail | AP creation only validates identity existence, not runtime AD connectivity |
| Works on non-AD SVM, fails on AD-joined SVM (same FS) | CIFS service triggers multiprotocol identity resolution |

**Pre-flight check** (add to Lambda/Step Functions before S3 AP data operations):
```python
# Verify AD DC reachability from ONTAP SVM
response = ontap_client.get("/api/protocols/cifs/services", params={"svm.name": svm_name, "fields": "enabled,ad_domain"})
if response["records"] and response["records"][0]["enabled"]:
    # SVM has CIFS enabled — AD DCs must be reachable for S3 AP data ops
    logger.info("SVM is AD-joined. Verifying AD connectivity is handled by ONTAP.")
```

**FSx auto-manages `s3_unix` name-mapping**: When an S3 AP is created, FSx automatically creates a `direction: s3_unix` name-mapping entry (e.g., `amazon-fsx-XXXXXX → root`). No manual name-mapping configuration is needed for S3 AP operation.

**Recovery when AD was deleted/recreated**:
1. Force-delete CIFS service: `DELETE /api/protocols/cifs/services/{svm-uuid}` with body `{"force": true, "ad_domain": {...}}`
2. Remove stale records: `POST /api/private/cli/vserver/cifs/users-and-groups/remove-stale-records` with body `{"vserver": "<svm-name>"}`
3. Re-create CIFS service: `POST /api/protocols/cifs/services` with new AD details
4. Use a NEW NetBIOS name (previous names leave computer accounts in AD that cannot be reused)

### Audit log formats

FSx ONTAP outputs audit logs in EVTX (Windows Event Log binary) or XML format depending on SVM audit configuration (`vserver audit create -format {evtx|xml}`). The `shared/lambda-layers/log-parser/` handles both. EVTX files start with magic bytes `ElfFile\x00`. XML logs contain `<Event>` elements with system and event data.

> **ONTAP CLI note**: ONTAP 9.11+ deprecates the `vserver` prefix on FPolicy commands (e.g., `vserver fpolicy` → `fpolicy`). Both forms work for backward compatibility. This project uses the deprecated form for compatibility with older ONTAP versions on FSx.

Reference: [AWS Docs — File access auditing](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-access-auditing.html)

### Vendor API key caching in Lambda

API keys are fetched from Secrets Manager once per Lambda execution context (cold start) and cached in a module-level variable. This avoids per-invocation Secrets Manager calls. The `_api_key_cache` pattern is intentional — do not refactor into per-request fetching.

### Bilingual documentation sync

Japanese (`docs/ja/`) is the primary language. English (`docs/en/`) must mirror the same heading structure and content. When modifying docs, always update both languages. Code examples are identical across languages.

Run `bash shared/scripts/check-bilingual-sync.sh` to verify sync status. This is also checked in CI (non-blocking).

Current state: 50 English files, 56 Japanese files (fully synced + 6 verification-results files in ja/ only).

### AgentCore MCP Gateway — Integration Knowledge (verified 2026-07)

If exposing observability tool operations (automated response, log search) as MCP tools via AgentCore Gateway:

| Pitfall | Root Cause | Solution |
|---------|-----------|----------|
| AgentCore Gateway assumed us-east-1 only | Workshop examples use us-east-1 for simplicity | **ap-northeast-1 で利用可能**。Gateway + Lambda を同一リージョンに配置すること |
| Lambda event format: `event.toolName` で取得 | 誤った前提 | 正しくは `context.client_context.custom['bedrockAgentCoreToolName']`。event はフラットなパラメータ辞書。ツール名は `targetName___toolName` 形式 |
| `create-gateway-target` で Lambda not found | Gateway と Lambda のリージョン不一致 | Gateway と Lambda は**同一リージョン**に配置必須。クロスリージョン呼び出しは不可 |
| Quick Desktop MCP Remote 追加が永続化されない | Quick Desktop の間欠的バグ | **Import 方式**（JSON ファイルからの読み込み）を使う |

## Vendor API Reference (Quick Lookup)

| Vendor | Endpoint | Auth Header | Max Batch | Firehose | Notes |
|--------|----------|-------------|-----------|----------|-------|
| Datadog | `https://http-intake.logs.{site}/api/v2/logs` | `DD-API-KEY: <key>` | 5MB / 1000 items | ✅ | |
| New Relic | `https://log-api.newrelic.com/log/v1` (US) | `Api-Key: <license>` | 1MB | ✅ | |
| Grafana/Loki | `https://otlp-gateway-prod-<region>.grafana.net/otlp` | Basic Auth (base64(ID:token)) | ~4MB recommended | ❌ | ✅ Verified via otlp_http exporter (NOT loki exporter) |
| Splunk | `https://<host>:8088/services/collector/event` | `Authorization: Splunk <token>` | No hard limit | ✅ (built-in) | |
| Elastic | `https://<cluster>/_bulk` | `Authorization: ApiKey <key>` | ~10MB recommended | ❌ | |
| Dynatrace | `https://<env>.live.dynatrace.com/api/v2/logs/ingest` | `Authorization: Api-Token <token>` | 1MB | ✅ | |
| Sumo Logic | `https://endpoint<N>.collection.sumologic.com/...` | Embedded in URL | 1MB | ❌ | |
| Honeycomb | `https://api.honeycomb.io` | `x-honeycomb-team: <hcaik_key>` | 5MB | ❌ | ✅ Verified via otlp_http exporter + x-honeycomb-dataset header |
| OTel (OTLP) | `http://<collector>:4318/v1/logs` | Configurable | Configurable | ❌ | ✅ Verified: Datadog + Grafana + Honeycomb multi-backend (0.152.0) |
| Mackerel | `https://otlp-vaxila.mackerelio.com` (OTLP/HTTP only) | `Mackerel-Api-Key: <key>` header | ~3.5MB (`sending_queue.batch.max_size` bytes) | ❌ | 📋 Planning only — Mackerel's own log feature is open beta (2026-07-16); no Lambda/`template.yaml` in this repo yet. Same endpoint/auth as Mackerel's tracing (APM) feature. See `integrations/mackerel/README.md`. |

Sources: [Datadog Logs API](https://docs.datadoghq.com/api/latest/logs/) | [New Relic Log API](https://docs.newrelic.com/docs/enable-new-relic-logs-http-input/) | [Grafana Loki HTTP API](https://grafana.com/docs/loki/latest/reference/loki-http-api/) | [Splunk HEC](https://docs.splunk.com/Documentation/Splunk/9.4.0/Data/FormateventsforHTTPEventCollector) | [OpenTelemetry Lambda](https://github.com/open-telemetry/opentelemetry-lambda)

## AWS Service Patterns

### EventBridge Scheduler for audit log processing

FSx for ONTAP S3 Access Points do not support S3 Event Notifications or EventBridge object-level events. Use EventBridge Scheduler to invoke the audit log processor Lambda on a periodic schedule (e.g., every 5 minutes). Lambda uses checkpointing (DynamoDB or S3 marker objects) to track which audit log files have been processed and only reads newly rotated files.

### Lambda Powertools (recommended for new integrations)

[Powertools for AWS Lambda (Python)](https://aws.amazon.com/powertools-for-aws-lambda/) provides structured logging, tracing, and metrics out of the box. Consider adopting for new vendor integrations to standardize observability of the Lambda functions themselves.

### Kinesis Data Firehose alternative path

For high-volume logs (>1000 events/second sustained), prefer the Firehose path over direct Lambda-to-vendor delivery. Firehose provides automatic buffering, retry, and backpressure handling. Splunk and Datadog have built-in Firehose destinations.

## Testing Rules

- Write pytest unit tests for all Lambda handler logic
- Mock all AWS service calls (boto3) and HTTP calls (urllib3)
- Use `conftest.py` for shared fixtures (env vars, sample events)
- Sample event data lives in `tests/test_data/`
- Tests must be deterministic — no real API calls, no network dependencies
- CI runs ALL 9 vendors + shared layers (not just Datadog)
- Coverage report generated as CI artifact (`coverage-html/`)
- Run `python -m pytest integrations/<vendor>/tests/ -v` before marking any task complete

## Production Readiness Levels

The project defines 4 levels. When implementing features, know which level you're targeting:

| Level | Components | Key Files |
|-------|-----------|-----------|
| **Level 1**: Quickstart | Audit poller + SSM checkpoint + DLQ | `template.yaml` |
| **Level 2**: Operational PoC | + Dashboard + alerts + SLO monitoring | `docs/en/pipeline-slo.md` |
| **Level 3**: Production | + DynamoDB ledger + SQS buffer + poison-pill | `shared/python/object_ledger.py`, `shared/templates/sqs-buffering.yaml` |
| **Level 4**: Enterprise | + OTel Collector + PII redaction + multi-account + DR | `shared/templates/multi-account-stackset.yaml`, `docs/en/cross-region-replication.md` |

Go/No-Go criteria between levels: `docs/en/pipeline-slo.md`

## Shared Python Modules

These modules in `shared/python/` are designed to be imported by any vendor Lambda:

### `auth_cache.py` — Credential caching with reload-on-401
```python
from auth_cache import SecretBackedAuth, send_with_auth_retry
auth = SecretBackedAuth(secret_arn=os.environ["API_KEY_SECRET_ARN"])
creds = auth.get()  # Cached; force_refresh=True after 401/403
```

### `object_ledger.py` — DynamoDB per-object state (Level 3)
```python
from object_ledger import ObjectLedger
ledger = ObjectLedger(table_name=os.environ["LEDGER_TABLE_NAME"])
if ledger.should_process(key, etag):
    process(key)
    ledger.mark_success(key, etag)
# Auto-promotes to poison_pill after 3 failures
```

### `sqs_buffer.py` — SQS buffering with partial batch failures (Level 3)
```python
from sqs_buffer import SQSProducer, process_sqs_batch
# Producer (poller Lambda): send file keys to queue
producer = SQSProducer(queue_url=os.environ["BUFFER_QUEUE_URL"])
producer.send(key=key, etag=etag)
# Consumer (shipper Lambda): process with ReportBatchItemFailures
def lambda_handler(event, context):
    return process_sqs_batch(event, ship_single_file)
```

### `ontap_response.py` — Automated incident response via ONTAP REST API
```python
from ontap_response import OntapResponseClient

client = OntapResponseClient(
    mgmt_ip=os.environ["ONTAP_MGMT_IP"],
    username=creds["username"],
    password=creds["password"],
)
# Block compromised SMB user (same mechanism as DII SWS)
client.block_smb_user(svm_name="svm-prod", domain="CORP", username="jdoe")
# Block attacker NFS IP
client.block_nfs_ip(svm_name="svm-prod", policy_name="default", client_ip="10.0.5.99")
# Full containment: snapshot + block + disconnect
client.contain_smb_threat(svm_name="svm-prod", domain="CORP", username="jdoe", volume_name="vol1")
```

## Operational Runbooks

When alarms fire, reference these runbooks:
- `docs/en/runbooks/dlq-replay.md` — DLQ has messages (delivery failure)
- `docs/en/runbooks/lambda-errors.md` — Lambda error rate spike
- `docs/en/runbooks/checkpoint-stale.md` — Checkpoint not advancing

## Boundaries

### ✅ Allowed without asking
- Read any file in the repository
- Run lint, typecheck, tests
- Create/modify files within `integrations/<vendor>/`
- Create/modify files within `docs/`

### ⚠️ Ask first
- Modify `shared/` (affects all integrations)
- Add or remove npm/pip dependencies
- Change `.kiro/steering/` files
- Modify `.github/workflows/`

### 🚫 Never
- Commit secrets, API keys, `.env` files, or PEM keys
- Force push to main
- Modify `.git/` directory
- Delete `shared/lambda-layers/` or `shared/templates/`
- Use `requests` library in Lambda (not in runtime, use `urllib3`)
- Store secrets in Lambda environment variables (use Secrets Manager ARN only)
- Commit real AWS account IDs, resource IDs, or IP addresses (use placeholders)
- Commit screenshots without running `mask_screenshots.py`

## Security & Privacy (Public Repository)

This is a **public repository**. All committed content is visible to anyone.

### Sensitive Data Rules

| Data Type | Placeholder | Example |
|-----------|-------------|---------|
| AWS Account ID | `123456789012` | `arn:aws:s3:us-east-1:123456789012:accesspoint/...` |
| Secret ARN suffix | `-XXXXXX` | `secret:fsxn-datadog-api-key-XXXXXX` |
| FSx File System ID | `fs-0123456789abcdef0` | — |
| SVM ID | `svm-0123456789abcdef0` | — |
| VPC/Subnet/SG IDs | `vpc-0123456789abcdef0` | — |
| Private IPs | `10.0.x.x` or `<management-ip>` | — |
| Public IPs | `<bastion-ip>` | — |
| SSH key paths | `<your-ssh-key.pem>` | — |
| SVM UUID | `<svm-uuid>` | — |

### Pre-Push Checklist

```bash
# 1. Run ALL vendor tests
python -m pytest integrations/*/tests/ shared/lambda-layers/ems-parser/tests/ -v --tb=short

# 2. Validate CloudFormation (cfn-lint + cfn-guard critical)
cfn-lint integrations/*/template.yaml shared/templates/*.yaml
cfn-guard validate -d integrations/*/template*.yaml -r guard/rules/critical-security.guard --show-summary fail

# 3. Check for real account IDs in tracked files
git ls-files | xargs grep -l "<your-account-id>" 2>/dev/null && echo "FAIL" || echo "PASS"

# 4. Check .kiro/ is not tracked
git ls-files .kiro/ | wc -l  # Should be 0

# 5. Check docs/blog/ is not tracked
git ls-files docs/blog/ | wc -l  # Should be 0

# 6. Check bilingual sync
bash shared/scripts/check-bilingual-sync.sh

# 7. Mask screenshots before committing
python3 docs/screenshots/mask_screenshots.py
```

### .gitignore Protected Paths

These paths MUST remain in `.gitignore`:
- `.kiro/` — IDE steering files (contain environment-specific info)
- `docs/blog/` — Draft articles (published via dev.to, not GitHub)
- `.env` — API keys and credentials
- `*.pem` — SSH keys

### Scripts Must Be Environment-Agnostic

All scripts use environment variables with sensible defaults:
- `AWS_REGION` — defaults to `ap-northeast-1` but overridable
- `AWS_ACCOUNT_ID` — dynamically resolved via `aws sts get-caller-identity`
- `ONTAP_MGMT_IP` — required, no default (user must set)
- `SVM_UUID` — required, no default (user must set)
- `BASTION_IP` / `BASTION_KEY` — optional (only if ONTAP is behind bastion)

## Key Files

### Vendor Reference Implementations
- `integrations/grafana/lambda/handler.py` — Most complete reference (audit + OTLP + Loki fallback)
- `integrations/datadog/lambda/handler.py` — Reference implementation (audit log path)
- `integrations/datadog/lambda/fpolicy_handler.py` — FPolicy handler (SQS + EventBridge dual-format)
- `integrations/datadog/template.yaml` — Reference CloudFormation template (audit log)
- `integrations/datadog/template-ems-fpolicy.yaml` — EMS + FPolicy Lambda (with SQS event source mapping)

### Shared Modules
- `shared/python/auth_cache.py` — Credential caching (TTL + reload-on-401/403)
- `shared/python/object_ledger.py` — DynamoDB per-object processing state (Level 3)
- `shared/python/sqs_buffer.py` — SQS producer + consumer with partial batch failures (Level 3)
- `shared/python/ontap_response.py` — Automated response: user/IP blocking, snapshot, session disconnect via ONTAP REST API
- `shared/lambda-layers/log-parser/python/fsxn_log_parser/parser.py` — EVTX/XML parser
- `shared/lambda-layers/s3ap-reader/python/s3ap_reader/reader.py` — S3 AP utility
- `shared/lambda-layers/ems-parser/` — EMS event parser + tests

### CloudFormation Templates
- `shared/templates/prerequisites.yaml` — S3 AP + EventBridge Scheduler + checkpoint
- `shared/templates/iam-base-roles.yaml` — IAM role pattern
- `shared/templates/fpolicy-server-fargate.yaml` — FPolicy Fargate stack (ECS + SQS)
- `shared/templates/object-ledger.yaml` — DynamoDB table + poison-pill alarm (Level 3)
- `shared/templates/sqs-buffering.yaml` — SQS buffer + DLQ + alarms (Level 3)
- `shared/templates/secrets-rotation-sample.yaml` — Auto-rotation Lambda (all vendors)
- `shared/templates/multi-account-stackset.yaml` — StackSets deployment (Enterprise)
- `shared/templates/automated-response.yaml` — Automated incident response (user/IP blocking, snapshot via ONTAP REST API)
- `shared/templates/automated-response-ttl.yaml` — Time-limited blocks with EventBridge Scheduler auto-unblock
- `shared/templates/cloudwatch-log-alarm.yaml` — CloudWatch Log Alarm (`AWS::CloudWatch::LogAlarm`, GA 2026-07); direct log-to-alarm, no metric filter. cfn-lint E3006 expected until spec update.
- `shared/templates/fsxn-monitoring-dashboard.yaml` — CloudWatch Dashboard (IOPS/Throughput/Capacity) + capacity/throughput alarms. System Manager performance view replacement.
- `shared/templates/qtree-quota-monitor.yaml` — Qtree quota usage monitoring (Lambda → ONTAP REST API → CloudWatch Custom Metric + alarm). System Manager quota view replacement.

### Security & CI
- `guard/rules/critical-security.guard` — Blocking cfn-guard rules (wildcard IAM, secrets in env, DLQ encryption)
- `shared/scripts/pre-push-security-check.sh` — Security scan before push
- `shared/scripts/check-bilingual-sync.sh` — ja/en documentation sync check

### Operations
- `shared/scripts/fpolicy-fargate-control.sh` — FPolicy Fargate start/stop/status
- `shared/scripts/fpolicy-update-engine-ip.sh` — ONTAP Engine IP auto-update
- `shared/fpolicy-server/build-and-push.sh` — ECR image build (linux/amd64 required)
- `shared/scripts/deploy-log-alarm.sh` — Deploy CloudWatch Log Alarm (env-var driven; CLI has no `put-log-alarm` yet, use CFN)
- `shared/scripts/cleanup-log-alarm.sh` — Delete Log Alarm stacks (`--all`, `--delete-sns`, `-y`)
- `docs/screenshots/mask_screenshots.py` — Screenshot masking (PII removal)
- `shared/scripts/automated-response-cli.sh` — CLI helper for automated response (block/unblock/contain/test)

### Documentation (key docs for understanding the project)
- `docs/en/pipeline-slo.md` — SLO definitions + Go/No-Go criteria
- `docs/en/data-classification.md` — PII field mapping + handling patterns
- `docs/en/compliance-evidence-pack.md` — ISMAP/FISC/SOC2 evidence template
- `docs/en/multi-account-deployment.md` — StackSets guide
- `docs/en/cross-region-replication.md` — DR patterns
- `integrations/otel-collector/docs/en/pii-redaction-cookbook.md` — 7 OTel Collector redaction recipes
- `docs/en/automated-response-guide.md` — Automated incident response (user/IP blocking via ONTAP REST API)
- `docs/en/ems-detection-capabilities.md` — EMS event catalog (30+ events, delivery patterns, latency comparison)
- `docs/en/native-alternative-matrix.md` — System Manager / Workload Factory / DII feature-to-AWS-native mapping (40 features, DII 100% covered)
- `docs/en/deployment-guide.md` — Comprehensive deployment guide (stack catalog, parameter mapping, VPC EP conflict matrix, verified paths, cost, Day 2)
- `cfn-params/README.md` — Parameter file usage instructions (create-stack vs deploy syntax)
- `shared/scripts/preflight-check.sh` — Pre-deployment environment validation (5 profiles, VPC EP conflict detection, ONTAP S3 server check)

## Deploying Prerequisites

Before any vendor integration, deploy the prerequisites stack:

```bash
# 1. Deploy S3 bucket + Access Point + EventBridge
aws cloudformation deploy \
  --template-file shared/templates/prerequisites.yaml \
  --stack-name fsxn-observability-prerequisites \
  --parameter-overrides AuditLogBucketName=<unique-name> \
  --capabilities CAPABILITY_IAM

# 2. Enable FSx ONTAP audit logging
bash shared/scripts/ontap-audit-setup.sh --endpoint <ip> --svm <name> --dry-run

# 3. Deploy vendor stack using outputs from step 1
```

### EMS/FPolicy Stacks (CAPABILITY_NAMED_IAM Required)

The EMS Webhook and FPolicy templates create named IAM roles, so they require `CAPABILITY_NAMED_IAM`:

```bash
# EMS Webhook stack
aws cloudformation deploy \
  --template-file shared/templates/ems-webhook-apigw.yaml \
  --stack-name fsxn-ems-webhook \
  --parameter-overrides LambdaFunctionArn=<ARN> \
  --capabilities CAPABILITY_NAMED_IAM

# FPolicy stack (ECS Fargate + SQS + EventBridge)
aws cloudformation deploy \
  --template-file shared/templates/fpolicy-apigw.yaml \
  --stack-name fsxn-fp-srv \
  --parameter-overrides \
    ComputeType=fargate \
    VpcId=<vpc-id> \
    SubnetIds=<subnet-1>,<subnet-2> \
    FsxnSvmSecurityGroupId=<sg-id> \
    ContainerImage=<ecr-uri>:v2-timeout-fix \
    AlarmNotificationTopicArn=<sns-topic-arn> \
  --capabilities CAPABILITY_NAMED_IAM
```

The FPolicy stack creates the ingestion queue plus its DLQ (`MaxReceiveCount`
default 5) and two alarms: DLQ depth, and `ApproximateAgeOfOldestMessage` for a
stalled consumer. `AlarmNotificationTopicArn` is optional — without it the alarms
never notify anyone. Pass the `IngestionQueueArn` output to the vendor stack as
`FPolicySqsQueueArn`.

**Architecture:**
- EMS: ONTAP EMS → Webhook (HTTPS) → API Gateway → Lambda → Vendor
- FPolicy: ONTAP → TCP:9898 → ECS Fargate → SQS → Lambda → Vendor (SQS event source mapping)
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to Fargate task IP (not via NLB)
- Fargate task IP changes on restart — ONTAP External Engine must be updated

Two patterns exist:
- **Pattern A (existing FSx ONTAP)**: Deploy prerequisites.yaml → enable audit → deploy vendor stack
- **Pattern B (from scratch)**: Create FSx ONTAP → then Pattern A

Full guide: `docs/ja/prerequisites.md` / `docs/en/prerequisites.md`

## Adding a New Vendor Integration

1. Create directory: `mkdir -p integrations/<vendor>/{lambda,scripts,docs/{ja,en},tests}`
2. Copy reference: use `integrations/grafana/` as the template (most complete)
3. Implement `lambda/handler.py` with vendor-specific API formatting
4. Create `template.yaml` following the CloudFormation structure in steering
5. Create `template-ems.yaml` for EMS webhook Lambda
6. Create `template-fpolicy.yaml` for FPolicy EventBridge Lambda
7. Write bilingual docs: `docs/ja/setup-guide.md` and `docs/en/setup-guide.md`
8. Add pytest tests with mocked API responses
9. Create `scripts/deploy.sh` (env-var driven, no hardcoded values)
10. Create `scripts/cleanup.sh` as a thin wrapper calling `shared/scripts/cleanup-vendor.sh`
11. If the vendor needs a `.env.<vendor>.example` file (e.g. for local Docker Compose
    validation), it will be silently caught by `.gitignore`'s blanket `.env.*` rule.
    Force-add it explicitly: `git add -f integrations/otel-collector/.env.<vendor>.example`
    (or the equivalent path). Do NOT loosen the `.env.*` rule itself — force-add each
    new example file individually, matching the existing precedent for
    `.env.example`, `.env.mackerel.example`, and `.env.triple.example`.
12. Update root `README.md` vendor table (change 🚧 to ✅)
13. Update `docs/{ja,en}/vendor-comparison.md`

### Cleanup Script Template

Each vendor's `scripts/cleanup.sh` should be a thin wrapper:

```bash
#!/bin/bash
# Clean up <Vendor> integration resources.
set -euo pipefail

export STACK_PREFIX="${STACK_PREFIX:-fsxn-<vendor>}"
export SECRET_NAME="${SECRET_NAME:-<vendor>/fsxn-credentials}"
export VENDOR_NAME="<Vendor Name>"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/../../../shared/scripts/cleanup-vendor.sh" "$@"
```

The shared script (`shared/scripts/cleanup-vendor.sh`) handles:
- Dependency-safe deletion order (API Gateway before Lambda)
- DELETE_FAILED state detection and guidance
- Optional Lambda Layer, Secret, and S3 data cleanup
- `--all` flag for complete teardown
- `-y` flag for CI/CD non-interactive mode

### Deletion Order (Critical)

CloudFormation stacks MUST be deleted in this order:

```
1. ${STACK_PREFIX}-fpolicy       (no external dependencies)
2. ${STACK_PREFIX}-ems-webhook   (API Gateway references EMS Lambda ARN)
3. ${STACK_PREFIX}-ems           (safe after API Gateway is gone)
4. ${STACK_PREFIX}-integration   (independent)
```

If you delete the EMS Lambda (step 3) before the API Gateway (step 2), CloudFormation will fail with a resource-in-use error.

## Commit Convention

```
feat: add New Relic integration
fix: handle empty EVTX files in log parser
docs: update Datadog setup guide for AP1 region
test: add batch splitting edge case tests
chore: update cfn-lint to v1.x
```

Conventional Commits format. English only. Keep subject under 72 characters.

## Supply-Chain Security

### Automated Security Workflows

| Workflow | File | Purpose |
|----------|------|---------|
| zizmor | `.github/workflows/zizmor.yml` | GitHub Actions security linting (SHA-pinning, credential persistence, injection) |
| gitleaks | `.github/workflows/gitleaks.yml` | Secret detection — custom rules in `.gitleaks.toml` |
| OpenSSF Scorecard | `.github/workflows/scorecard.yml` | Automated security health scoring |

### Local Security Checks

```bash
# Pre-commit hook runs automatically on commit (via .githooks/pre-commit):
#   1. Author email verification
#   2. gitleaks secret scanning (staged files)
#   3. zizmor lint (if workflow files changed)

# Manual verification
gitleaks detect --config .gitleaks.toml --no-git --source .
zizmor .github/workflows/
```

### Actions Pinning Policy

- All third-party Actions MUST be pinned to SHA hashes: `uses: owner/action@<sha> # vX.Y.Z`
- `actions/checkout` must set `persist-credentials: false`
- Verify with `zizmor .github/workflows/` before committing workflow changes

### Custom Secret Detection (.gitleaks.toml)

Detects: internal IPs (10.x/172.16-31.x/192.168.x), AWS Account IDs, internal hostnames (`.internal.`/`.corp.`), VPN configs, NetApp internal references

## Agent Output Standards

> ユーザーレベル Kiro グローバル steering のミラー。steering 未ロードの環境でも従えるようにする。
> CI: `.github/workflows/agent-output-audit.yml`（命名/中立性/リーク/parity）と `gitleaks.yml`（シークレット）。

### Naming (NetApp / AWS)
- 初出は **Amazon FSx for NetApp ONTAP**、以降 **FSx for ONTAP**。`FSxN` / 単独 `FSx` / `FSx ONTAP` は不可。アクセスポイントは **FSx for ONTAP S3 AP**。
- NetApp Workload Factory / NetApp Console / BlueXP は禁止語ではない。文脈上適切であれば言及・提案可。
- FSxN 等の表記ゆれのみ `allow:naming` コメントでの例外運用が必要（外部引用タイトルの逐語引用時など）。

### Vendor neutrality (right-tool-for-the-job)
- ベンダー対決/優劣表現は禁止（"best", "beats X", "X より優れている", "競合ツール", "優位性", "game-changer"）。選択肢として提示し、推奨案自身の制約も含めてトレードオフを対称に記載。

### Public-output safety
- 個人名/ペルソナ名・メール・AWS アカウントID・内部IP/ホスト名・サポートケース番号・ベンダー内部チケットID をコミットしない。role ベース表記（"Storage Specialist lens"）と "an internal product request (tracked)" を使う。
- プロセスメタデータのノイズ禁止（"Persona Review Summary"・レビューラウンド・日付・レンズ数）。provenance（who/when/rounds）は `.private/`（gitignore）へ。
- **架空の役職名/ペルソナ名を inline note のラベルに使わない**（`> **Application Security Engineer (AppSec) lens**:`、`> **XXX の視点**:` など）。これらは実際のインタビュー・アンケート・担当者レビューを経たものではなく AI 支援分析であり、役職ラベルを付けると誤解を招く。中立的なトピックラベルを使う（`> **セキュリティに関する補足**:`、`> **Security note**:`）。指摘内容自体は変更不要、ラベルのみ変更する。例外は `global-evidence-backed-personas.md` の evidence-tiered 名前付きペルソナ（Public evidence 出典がある場合のみ、または Role-based archetype として一般化した場合のみ）。
- コミット前チェック: `grep -rnoE '^> \*\*[^*]+(lens|の視点)[^*]*\*\*' <files>` — 役職名/専門家名/ペルソナ名を含むラベルがヒットしたら中立ラベルに直す。

### Bilingual docs (JA primary + EN)
- JA/EN parity を維持（セクション構成/数の一致、inline note の対応）。片方を変更したら同じ変更で両方に反映。

### Technical reference / guide docs
- 必須要素: エグゼクティブサマリの結論、FAQ/よくある誤解、選択フローチャート（mermaid 可）、OT/IT セキュリティ考慮（該当時）、段階的導入ステップ、Related Documents（逆リンク）、≥10 の inline トピック別ノート（役職名ではなく `**XXXに関する補足**` 形式のラベル）。

### Before committing docs
```bash
gitleaks detect --config .gitleaks.toml --no-git --source .
# CI が agent-output チェックをミラー: .github/workflows/agent-output-audit.yml
```
