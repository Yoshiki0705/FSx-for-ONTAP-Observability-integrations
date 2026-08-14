# Deploying Prerequisites and Adding a New Vendor Integration

Prerequisite stack deployment, EMS/FPolicy capability requirements, the
step-by-step procedure for adding a vendor, and stack deletion order.

> Extracted from AGENTS.md so it is not loaded into every agent turn.
> AGENTS.md keeps a one-line index entry pointing here, and
> .kiro/steering/ carries a conditional loader that pulls this in when
> the work touches these areas. Tracked in git on purpose: .kiro/ is not
> published, so the body must live here to stay visible on GitHub.

Before any vendor integration, deploy the prerequisites stack:

```bash
# 1. Deploy S3 bucket + Access Point + EventBridge
aws cloudformation deploy \
  --template-file shared/templates/prerequisites.yaml \
  --stack-name fsxn-observability-prerequisites \
  --parameter-overrides AuditLogBucketName=<unique-name> \
  --capabilities CAPABILITY_IAM

# 2. Enable FSx for ONTAP audit logging
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
- **Pattern A (existing FSx for ONTAP)**: Deploy prerequisites.yaml → enable audit → deploy vendor stack
- **Pattern B (from scratch)**: Create FSx for ONTAP → then Pattern A

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
14. If you add a `docs/{en,ja}/verification-results-<vendor>.md`, the same
    blanket-ignore trap applies: `.gitignore` has `docs/**/verification-results*.md`,
    so `git add -A` skips it without saying anything and CI then fails on
    `generate-docs-index.py --check` complaining the file "does not exist".
    Force-add both languages: `git add -f docs/en/verification-results-<vendor>.md
    docs/ja/verification-results-<vendor>.md`. All 18 tracked verification records
    got there this way. Before force-adding, confirm the file carries no real
    account ID, resource ID, private IP or instance hostname — that exposure is
    why the blanket rule exists, and it stays.

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

