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

# Install pinned dev + gate tooling (ruff, bandit, cfn-lint, pytest)
make install

# Run ALL Python tests. Do not hand-write the directory list here again:
# this block used to enumerate 10 directories while 16 existed, and the two
# it omitted (scripts/verification/tests, 172 tests, and
# shared/lambda-layers/log-parser/tests, 12 tests) therefore ran nowhere.
# The list lives in the Makefile as PYTEST_DIRS, CI calls this same target,
# and scripts/tests/test_test_dir_coverage.py fails if a tests/ directory on
# disk is missing from it.
make test-py

# See which directories that covers
make print-PYTEST_DIRS

# Run Python tests for a specific vendor
.venv/bin/python -m pytest integrations/datadog/tests/ -v

# Lint (blocking tier: definite defects only), and the advisory full set
make lint-py
make lint-py-full

# Python security scan (bandit, baselined to the 6 reviewed B314 findings)
make security        # blocking: fails on anything beyond the baseline
make security-full   # the honest full view, including the baselined 6

# Validate CloudFormation (blocking; ignores W and E3006)
make cfn-lint

# cfn-guard, rule self-test first because cfn-guard exits 0 on an unparseable
# rule file and a rule matching nothing reports no findings
make cfn-guard

# Secret scan, including the vendor-credential rules
make gitleaks

# Drift guards: .PHONY coverage, test-dir coverage, bandit baseline integrity,
# always-loaded context budget, steering reachability
make drift

# Check bilingual documentation sync
make bilingual

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
  ├── workflows/pr-title-check.yml  # Conventional Commits gate on PR titles (blocking)
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

S3 Access Point network constraints, AD-joined SVM data-operation requirements, unsupported S3 features, audit log formats, credential caching, bilingual sync: [docs/agent/non-obvious-patterns.md](docs/agent/non-obvious-patterns.md)

## Vendor API Reference (Quick Lookup)

Endpoint, auth header, batch limit and Firehose support per vendor: [docs/agent/vendor-api-reference.md](docs/agent/vendor-api-reference.md)

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
ledger = ObjectLedger(
    table_name=os.environ["LEDGER_TABLE_NAME"],
    ttl_days=int(os.environ.get("LEDGER_TTL_DAYS", "0")),
)
if ledger.should_process(key, etag):
    process(key)
    ledger.mark_success(key, etag)
# Auto-promotes to poison_pill after 3 failures
```

Retention needs both halves: TTL enabled on the table (`object-ledger.yaml`, on
attribute `expires_at`) **and** the module writing `expires_at` from
`LEDGER_TTL_DAYS`. Setting one without the other reads as configured while the
table grows without bound. Poison-pill entries never expire — that list is what
suppresses files already known to be unprocessable.

`shared/python/idempotency.py` was removed. It defined a second `ObjectLedger`
keyed on `object_key`, which no template here creates.

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

## Deploying Prerequisites and Adding a Vendor

Prerequisite stacks, EMS/FPolicy capabilities, the add-a-vendor procedure, stack deletion order: [docs/agent/deploying-and-adding-vendors.md](docs/agent/deploying-and-adding-vendors.md)

## Commit Convention

```
feat: add New Relic integration
fix: handle empty EVTX files in log parser
docs: update Datadog setup guide for AP1 region
test: add batch splitting edge case tests
chore: update cfn-lint to v1.x
```

Conventional Commits format. English only. Keep subject under 72 characters.

Allowed types: `feat` `fix` `docs` `bench` `chore` `refactor` `test` `ci` `perf` `style`.

### PR titles are enforced

`.github/workflows/pr-title-check.yml` fails a PR whose title lacks a valid
Conventional Commits prefix, and warns (without failing) above 70 characters.
This matters because the repository squash-merges and GitHub seeds the squash
commit message from the PR title — an unprefixed title becomes an unprefixed
commit, discoverable only after merge.

```
<type>(<optional-scope>)<optional-!>: <description>

feat: add S3 AP presigned URL support
fix(shared): handle empty ONTAP response
feat!: drop Python 3.11 support          # ! marks a breaking change
```

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
