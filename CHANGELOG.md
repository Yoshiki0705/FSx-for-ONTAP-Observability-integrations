# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Governance and compliance review guides (bilingual ja/en)
- Security review checklists (bilingual ja/en)
- PoC success criteria and production readiness levels
- CI policy documentation with cfn-guard adoption roadmap (bilingual ja/en)
- cfn-guard policy checks in GitHub Actions (non-blocking)
- Markdown link check and actionlint CI jobs
- Sample payloads for audit, EMS, and FPolicy validation in examples/
- Shared Python observability module (Lambda Powertools logger/metrics/tracer)
- Shared Python object ledger module (DynamoDB-backed idempotent processing)
- Choose your path decision guide in README
- Recommended first 30 minutes section in README
- Try with sample data section in README
- Community disclaimer in README
- `guard/tests/` self-test (`run-guard-selftest.sh`) with parse-probe, negative-control and positive-control fixtures, wired into CI as a blocking step. A cfn-guard rule file that fails to parse still exits 0, so a broken rule set was indistinguishable from a clean scan.
- `guard/README.md` documenting the two silent-failure modes, the rule-authoring checklist and the cfn-guard syntax pitfalls found while fixing the rules.
- `shared/scripts/sync-inline-lambda.py` (with `--check`) plus drift tests, so Lambda code that templates inline stays byte-identical to its source of truth.
- `shared/scripts/generate-docs-index.py` (with `--check`) plus tests, generating the per-language documentation index from one category map. Documents with no category are an error rather than a silent omission.
- `create-alerts.sh` and `setup-full-observability.sh` for the CrowdStrike Falcon LogScale and OTel Collector integrations — the last two vendors missing them, which made the README's per-vendor one-command setup claim untrue for both.
- `Rules` sections in `shared/templates/demo-ad-environment.yaml` and `shared/templates/ems-webhook-apigw.yaml`, rejecting parameter combinations that previously deployed cleanly and then failed at runtime.
- `PostgresEngineVersion` parameter in `management-console/templates/console.yaml`.

### Removed
- `shared/python/idempotency.py`. It defined a second class also named `ObjectLedger`, keyed on `object_key` — a partition key no template in this repository creates, so every call against the shipped ledger table raised `ValidationException`. Use `shared/python/object_ledger.py`, which matches `shared/templates/object-ledger.yaml` and is covered by tests.
- Unused parameters that implied behaviour the templates never had: `SelfManagedAdUsername` / `SelfManagedAdPassword` / `SelfManagedAdOu` from `demo-ad-environment.yaml`, `VpcId` from `automated-response-ttl.yaml`, and `SvmId` / `SubnetId` / `SecurityGroupId` from `fsxn-audit-config.yaml`. See Breaking changes below.
- Dead condition `HasWebhookSecret` from `ems-webhook-apigw.yaml`, replaced by a `Rules` assertion that actually enforces it.

### Fixed
- `shared/templates/object-ledger.yaml` exposed a `TTLDays` parameter with no `TimeToLiveSpecification`, and `object_ledger.py` wrote no expiry attribute. DynamoDB needs both, so retention read as configured while the table grew without bound. TTL is now enabled on `expires_at`, which the module writes from `ttl_days` / `LEDGER_TTL_DAYS`. Poison-pill entries have the attribute removed so they never expire — the permanent skip list has to outlive the retention window, or the pipeline resumes failing on files already known to be unprocessable.
- `shared/python/build-layer.sh` shipped a hardcoded list of seven modules that had drifted from the thirteen present, omitting `ontap_audit_parser`, `vendor_shipper`, `ems_event` and `fpolicy_event`. Handlers import those defensively and fall back to a reduced parser, so the layer was delivering the degraded path without failing. Contents are now discovered from the directory, the archive is verified after packing, and deliberate omissions are listed with a reason.
- `guard/rules/lambda-security.guard` and `guard/rules/secrets-management.guard` failed to parse, so none of their rules ran. Nine silent failures were found in total, including three rule files using the idiom `Parameters.*[ keys == ... ]`, which selects nothing and therefore always passes; a `management-console-security.guard` rule whose `when %x !empty { %x !empty }` body could never fail; and `management-console/templates/*.yaml` being scanned by neither cfn-lint nor cfn-guard despite the rule file declaring itself blocking.
- CI ran `cfn-lint --ignore-checks W integrations/*/template.yaml` without a `--` separator, so argparse absorbed the paths into the ignore list and the command exited on a usage error. With `continue-on-error: true` this counted as success, and the integration templates were never linted.
- Three templates deployed placeholder Lambdas with no documented way to replace the code, and two of them also had a `Handler` that could not resolve against inline code — returning HTTP 500 rather than the intended 401/501. Real handler code is now inlined and kept in sync by `sync-inline-lambda.py`.
- `shared/lambda/authorizers/shared_secret_authorizer.py` compared the bearer token with `==`, allowing the token to be recovered a byte at a time through timing. Now uses `hmac.compare_digest`.
- Missing dead-letter queues on four asynchronously invoked Lambdas, including `automated-response-ttl.yaml`'s cleanup function — whose failure silently turns a time-limited block into a permanent one.
- Pattern B of `demo-ad-environment.yaml` passed a Pattern C parameter as `directoryName` and configured DNS on the Windows instance only for Pattern A, so domain join failed without a usable reason in the association status.
- `management-console/templates/console.yaml` pinned PostgreSQL `17.4`, which RDS has deprecated for new instances — the stack could no longer be created. Now tracks the major version, with `AutoMinorVersionUpgrade` explicit.
- `shared/templates/sqs-buffering.yaml` allowed `MaxConcurrency=1`, which passes parameter validation and then fails when the event source mapping is created: `ScalingConfig.MaximumConcurrency` starts at 2.
- `shared/templates/lakehouse-monitoring.yaml` passed `DATASYNC_TASK_ARN` to a collector whose role held no `datasync:*` permission at all. Scoped read permissions are now attached when a task ARN is supplied.
- `shared/scripts/check-bilingual-sync.sh` counted headings with `grep -c "^#"`, so lines inside fenced code blocks counted as headings. It reported one document as 60/70 when it was 43/43 and, more importantly, hid eleven real gaps behind a tolerance of three. Counting is now fence-aware and any non-zero difference is reported.
- The documented deploy command for `shared/templates/restore-verification.yaml` omitted `--s3-bucket`. At ~57 KB the template exceeds CloudFormation's 51,200-byte inline template body limit, so the command as written fails with a `ValidationError` that quotes the top of the template instead of naming the size limit. Both language guides now require the flag and explain why.
- Documentation: the per-language index covered 39 of 78 documents in English and 17 of 78 in Japanese; twelve Japanese pages linked to the English version of a document that exists in Japanese; and the telemetry coverage matrix marked EMS and FPolicy with the same tick for implemented and verified paths. The matrix now distinguishes the two, and a broken `#verification-results` anchor was corrected.

### Breaking changes
- `shared/templates/fpolicy-apigw.yaml`: parameter `FsxnCredentialsSecret` renamed to `FsxnCredentialsSecretArn`, matching the `...SecretArn` convention used by the other seventeen secret-reference parameters (its `AllowedPattern` already required an ARN). Pass the new name on the next stack update.
- Removed parameters (see Removed above) will be rejected as unknown if your saved parameter files still list them: `SelfManagedAdUsername`, `SelfManagedAdPassword`, `SelfManagedAdOu`, `VpcId` (TTL stack only), `SvmId`, `SubnetId`, `SecurityGroupId` (audit-config stack only). Drop those entries; no replacement is needed, since no template ever read them.
- `shared/templates/sqs-buffering.yaml`: `MaxConcurrency` minimum raised from 1 to 2. A stack currently passing 1 was already failing at event source mapping creation.

### Changed
- README restructured as decision guide with production readiness levels
- Clarified FSx for ONTAP S3 Access Point trigger model (polling, not event-driven)
- Updated Grafana Cloud path to OTLP Gateway primary, Loki Push API fallback
- Reworded ObjectLedger semantics as idempotent object processing and duplicate suppression
- Added compliance disclaimer to governance docs (not an attestation)
- Updated .markdown-link-check.json with flaky link mitigation

## [0.3.0] - 2026-05-15

### Added
- Splunk serverless integration (HEC via Lambda)
- Splunk EMS webhook handler
- Splunk Firehose alternative path template
- Splunk verification tooling and bilingual setup guides
- FPolicy → Splunk HEC delivery path
- EMS → Splunk HEC delivery path (ARP ransomware detection)

## [0.2.0] - 2026-04-20

### Added
- OTel Collector integration (vendor-neutral OTLP/HTTP)
- Triple-backend delivery verified (Datadog + Grafana Cloud + Honeycomb)
- OTel Collector production config with memory_limiter and sending_queue
- OTel config validation CI workflow
- Enterprise documentation suite (ADR, PoC checklist, security hardening, etc.)
- FPolicy server on ECS Fargate (TCP:9898 binary protocol)
- EMS webhook via API Gateway

## [0.1.0] - 2026-03-01

### Added
- Initial Datadog integration (Logs API v2 via Lambda)
- S3 Access Point reader Lambda Layer
- Log parser Lambda Layer (EVTX/JSON)
- EventBridge Scheduler polling with SSM checkpoint
- CloudFormation templates for prerequisites and Datadog
- Bilingual documentation (ja/en)
- CI pipeline (cfn-lint, pytest, jest)
- Security scan workflow
