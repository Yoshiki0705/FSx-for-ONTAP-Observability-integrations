# Key Files

Reference implementations, shared modules, CloudFormation templates, security
and CI entry points, operational scripts, and the key docs for understanding the
project.

> Extracted from AGENTS.md so it is not loaded into every agent turn. AGENTS.md
> keeps a one-line index entry pointing here. Tracked in git on purpose:
> `.kiro/` is not published, so the body must live here to stay visible on
> GitHub.

## Vendor Reference Implementations
- `integrations/grafana/lambda/handler.py` — Most complete reference (audit + OTLP + Loki fallback)
- `integrations/datadog/lambda/handler.py` — Reference implementation (audit log path)
- `integrations/datadog/lambda/fpolicy_handler.py` — FPolicy handler (SQS + EventBridge dual-format)
- `integrations/datadog/template.yaml` — Reference CloudFormation template (audit log)
- `integrations/datadog/template-ems-fpolicy.yaml` — EMS + FPolicy Lambda (with SQS event source mapping)

## Shared Modules
- `shared/python/auth_cache.py` — Credential caching (TTL + reload-on-401/403)
- `shared/python/object_ledger.py` — DynamoDB per-object processing state (Level 3)
- `shared/python/sqs_buffer.py` — SQS producer + consumer with partial batch failures (Level 3)
- `shared/python/ontap_response.py` — Automated response: user/IP blocking, snapshot, session disconnect via ONTAP REST API
- `shared/lambda-layers/log-parser/python/fsxn_log_parser/parser.py` — EVTX/XML parser
- `shared/lambda-layers/s3ap-reader/python/s3ap_reader/reader.py` — S3 AP utility
- `shared/lambda-layers/ems-parser/` — EMS event parser + tests

## CloudFormation Templates
- `shared/templates/prerequisites.yaml` — S3 AP + EventBridge Scheduler + checkpoint
- `shared/templates/iam-base-roles.yaml` — IAM role pattern
- `shared/templates/fpolicy-server-fargate.yaml` — FPolicy Fargate stack (ECS + SQS)
- `shared/templates/object-ledger.yaml` — DynamoDB table + poison-pill alarm (Level 3)
- `shared/templates/sqs-buffering.yaml` — SQS buffer + DLQ + alarms (Level 3)
- `shared/templates/secrets-rotation-sample.yaml` — Auto-rotation Lambda (all vendors)
- `shared/templates/multi-account-stackset.yaml` — StackSets deployment (Enterprise)
- `shared/templates/automated-response.yaml` — Automated incident response (user/IP blocking, snapshot via ONTAP REST API)
- `shared/templates/automated-response-ttl.yaml` — Time-limited blocks with EventBridge Scheduler auto-unblock
- `shared/templates/cloudwatch-log-alarm.yaml` — CloudWatch Log Alarm (`AWS::CloudWatch::LogAlarm`, GA 2026-07); direct log-to-alarm, no metric filter. cfn-lint E3006 varies by version (none on 1.56.3).
- `shared/templates/fsxn-monitoring-dashboard.yaml` — CloudWatch Dashboard (IOPS/Throughput/Capacity) + capacity/throughput alarms. System Manager performance view replacement.
- `shared/templates/qtree-quota-monitor.yaml` — Qtree quota usage monitoring (Lambda → ONTAP REST API → CloudWatch Custom Metric + alarm). System Manager quota view replacement.

## Security & CI
- `guard/rules/critical-security.guard` — Blocking cfn-guard rules (wildcard IAM, secrets in env, DLQ encryption)
- `shared/scripts/pre-push-security-check.sh` — Security scan before push
- `shared/scripts/check-bilingual-sync.sh` — ja/en documentation sync check

## Operations
- `shared/scripts/fpolicy-fargate-control.sh` — FPolicy Fargate start/stop/status
- `shared/scripts/fpolicy-update-engine-ip.sh` — ONTAP Engine IP auto-update
- `shared/fpolicy-server/build-and-push.sh` — ECR image build (multi-platform manifest: amd64 for Fargate, arm64 for EC2 mode)
- `shared/scripts/deploy-log-alarm.sh` — Deploy CloudWatch Log Alarm (env-var driven; CLI has no `put-log-alarm` yet, use CFN)
- `shared/scripts/cleanup-log-alarm.sh` — Delete Log Alarm stacks (`--all`, `--delete-sns`, `-y`)
- `docs/screenshots/mask_screenshots.py` — Fixed-region masks for registered screenshots
- `shared/scripts/automated-response-cli.sh` — CLI helper for automated response (block/unblock/contain/test)

## Documentation (key docs for understanding the project)
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
