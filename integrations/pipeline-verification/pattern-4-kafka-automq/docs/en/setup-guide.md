# Pattern 4 Setup Guide: Kafka + AutoMQ WAL-on-FSx-for-ONTAP Verification Environment

🌐 [日本語](../ja/setup-guide.md) | **English** (this page)

How to stand up the Kafka + AutoMQ diskless-Kafka pipeline verification
environment on top of reused existing assets. This covers a verification
environment and a verification record, not a production-grade deployment.

## Overview

A Kafka cluster ingests high-volume telemetry, an OpenTelemetry Collector (or a
Kafka-native consumer) routes it, and a time-series or columnar store holds it.
This verification environment focuses on one net-new layer: a Kafka broker fleet
running AutoMQ diskless-Kafka, which uses an FSx for ONTAP Generation 2 volume as
a Multi-AZ shared write-ahead log (WAL) in front of S3. The OpenTelemetry
Collector layer and the ClickHouse consumer schema are reused as documentation
pointers, not copied here.

This verification environment covers the broker layer and its WAL configuration,
not a clustered production deployment.

## Existing Coverage vs Net-New

| Layer | Category | Where |
|---|---|---|
| Kafka broker fleet + AutoMQ WAL-on-FSx-for-ONTAP Gen2 | net-new | `template.yaml` (this pattern) |
| OpenTelemetry Collector (routing/transform) | reused | `integrations/otel-collector/` (doc pointer) |
| ClickHouse consumer DDL | reused | `ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` (doc pointer) |
| VPC endpoints (S3 Gateway + Secrets Manager Interface) | reused | `shared/templates/vpc-endpoints.yaml` (nested stack) |
| IAM least-privilege pattern | reused | `shared/templates/iam-base-roles.yaml` (copy-paste reference; implemented inline per stack) |
| FSx for ONTAP file system + SVM | reused | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml` (doc pointer) |

The Kafka broker fleet and the Gen2 WAL configuration are net-new because the
repository has an OTel Collector integration and a generic archive layer but no
Kafka broker layer. The VPC endpoints reference the existing shared template as a
nested stack; they are not copied. The broker's own EC2, security group, and
instance role/profile resources share no shape with any existing template.

## Choosing a Kafka Storage Model

AutoMQ diskless-Kafka and a traditional three-replica Multi-AZ Kafka suit
different contexts; this is a right-tool-for-the-job choice, not a contest. The
trade-offs are stated symmetrically, including the AutoMQ option's own
constraints. AutoMQ is BYOC-dependent (you run it in your own account), so it is
presented as one option, not a default recommendation.

| Model | Suits | Trade-off / consideration |
|---|---|---|
| AutoMQ diskless-Kafka + FSx for ONTAP Gen2 WAL (this pattern's net-new) | Teams wanting S3-durable storage with local-disk-like latency and lower steady-state cost, willing to run a BYOC product | BYOC operational ownership; depends on FSx for ONTAP Gen2 high-throughput provisioning, which costs more than standard; a newer combination than classic brokers |
| Traditional three-replica Multi-AZ Kafka | Teams wanting the most mature, widely operated model with the broadest tooling and managed-service options | Higher storage cost (three full replicas across AZs); local-disk log storage to operate and rebalance; recovery rebuilds replicas from peers |

Both models can front the same OTel Collector and ClickHouse consumer. The choice
does not change the FSx for ONTAP silly-rename guardrail below when a broker log
directory is placed on NFS.

## OTel Collector and ClickHouse DDL Reference

The OpenTelemetry Collector layer is reused from `integrations/otel-collector/`
(collector configs, scripts, and compose files) as a documentation pointer, not
copied. When ClickHouse is the consumer store, reuse the schema at
`ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` (the sibling repository, local
checkout directory `edge-to-cloud-ai`). Do not copy the SQL into this repository;
reference it at that exact path so it stays a single source of truth.

## FSx for ONTAP Silly-Rename Guardrail

A Kafka broker's log directory on NFS historically crashed during partition
reassignment because of the "silly rename" behavior: NFS renames a file that
still has open references and deletes it on last close, and Kafka's rebalance
deletes files that still have open references. The fix is the ONTAP volume
setting `-is-preserve-unlink-enabled=true`, introduced in **ONTAP 9.12.1**, plus
the client-side change (RHEL 8.7 / 9.1).

Two things must be kept distinct here:

- **The ONTAP version prerequisite is met.** FSx for ONTAP file systems run
  **ONTAP 9.18.1 or later**. This is a direct field confirmation with the AWS
  service team, **not a publicly published version number** — readers cannot
  verify it from public FSx for ONTAP documentation alone. Because 9.18.1 is well
  past 9.12.1, the version supports the fix.
- **A specific FSx for ONTAP volume being configured and validated end-to-end is
  not confirmed.** No functional test like NetApp's NFSv3-vs-NFSv4.1
  partition-reassignment comparison has been run against FSx for ONTAP directly
  that this project could find. The original silly-rename validation was done by
  NetApp on **NetApp Cloud Volumes ONTAP** (ONTAP 9.12.1, NFSv4.1, Confluent
  7.2.1), which is not, by itself, evidence for FSx for ONTAP.

For this reason `deploy.sh` does **not** auto-mount the WAL/log directory on NFS.
Set and validate `-is-preserve-unlink-enabled=true` on the volume first (via the
ONTAP CLI or REST API, which FSx for ONTAP volumes expose), then mount it. The
live crash/no-crash sample run is spec task 16 (billed). Note that AutoMQ's WAL is
a fixed-size circular buffer and may not exercise the partition-rebalance
delete-while-open code path the silly-rename issue depends on, so the AutoMQ case
does not confirm that specific fix.

## Prerequisites

- A running FSx for ONTAP file system (including an SVM), Generation 2 for the WAL
- A target VPC and private subnets in at least two AZs (the WAL is Multi-AZ)
- AWS CLI v2 with CloudFormation / EC2 / IAM / S3 permissions
- For the durable tier, an S3 bucket AutoMQ writes to (named per the broker role's scoped prefix)

## What You Need Before Starting

The most common first-run stumble is hunting for your existing resource IDs
mid-deploy. Gather the values below **first**. None of them are created by this
verification environment; they are referenced from the existing shared
foundation. Use a Generation 2 file system for the WAL.

| What to gather | How to obtain |
|---|---|
| `VpcId` (target VPC) | `aws ec2 describe-vpcs --query 'Vpcs[].VpcId' --output text` |
| `SubnetIds` (≥2 AZs) | `aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc-id> --query 'Subnets[].[SubnetId,AvailabilityZone]' --output text` |
| `FileSystemId` (FSx for ONTAP Gen2) | `aws fsx describe-file-systems --query 'FileSystems[?FileSystemType==\`ONTAP\`].FileSystemId' --output text` |
| `OntapMgmtIp` (ONTAP management IP) | `aws fsx describe-file-systems --query 'FileSystems[].OntapConfiguration.Endpoints.Management.IpAddresses' --output text` |
| `SvmId` (SVM identifier, optional) | `aws fsx describe-storage-virtual-machines --query 'StorageVirtualMachines[].StorageVirtualMachineId' --output text` |
| AutoMQ durable-tier S3 bucket | `aws s3 ls` (named per the broker role's scoped prefix) |
| `PACKAGE_BUCKET` (S3 bucket for the nested-stack template) | `aws s3 ls` (a same-region bucket CloudFormation can read the nested template from; not needed when reusing existing VPC endpoints) |

## Parameters

Follows the AllowedPattern conventions from
`shared/templates/restore-verification.yaml` and pattern 2 / pattern 5. The
account ID is not hardcoded; `deploy.sh` resolves it at runtime with
`aws sts get-caller-identity`. The region is overridable via the `AWS_REGION`
environment variable (default `ap-northeast-1`). Example values are placeholders;
substitute your own.

| Parameter | Description | How to obtain / example value | Required? |
|---|---|---|---|
| `VpcId` | VPC where the broker fleet is placed | `aws ec2 describe-vpcs`. e.g. `vpc-0123456789abcdef0` | Required |
| `SubnetIds` | Private subnets in ≥2 AZs (the WAL is Multi-AZ) | `aws ec2 describe-subnets`. e.g. `subnet-0123456789abcdef0,subnet-0123456789abcdef1` | Required |
| `FileSystemId` | FSx for ONTAP Gen2 file system ID (`^fs-[0-9a-f]{17}$`) | `aws fsx describe-file-systems`. e.g. `fs-0123456789abcdef0` | Required |
| `OntapMgmtIp` | ONTAP management endpoint IP | `aws fsx describe-file-systems`. e.g. `198.51.100.10` | Required |
| `SvmId` | SVM identifier | `aws fsx describe-storage-virtual-machines`. e.g. `svm-0123456789abcdef0` | Optional |
| `KafkaMode` | `automq-wal` (default) or `traditional` | See "Choosing a Kafka Storage Model" below. e.g. `automq-wal` | Optional (default `automq-wal`) |
| `Gen2HighThroughput` | `true` (default) or `false` — the cost driver | e.g. `true` (high-throughput WAL, costs more than standard) | Optional (default `true`) |
| `WalMountPath` | Broker path for the FSx for ONTAP Gen2 WAL mount | e.g. `/var/lib/automq/wal` | Optional (default `/var/lib/automq/wal`) |
| `VpcEndpointsTemplateUrl` | HTTPS S3 URL of the packaged shared VPC endpoints template | e.g. `https://my-cfn-package-bucket.s3.ap-northeast-1.amazonaws.com/pattern-4/vpc-endpoints.yaml`. Empty to reuse existing endpoints | Optional (empty to skip) |

## Preflight Validation

Before standup, run the existing shared preflight script with the pattern-4
profile. `deploy.sh` calls it automatically.

```bash
bash shared/scripts/preflight-check.sh --profile pipeline-pattern-4 --vpc-id vpc-0123456789abcdef0
```

If it detects a VPC endpoint conflict, standup does not proceed; leave
`VpcEndpointsTemplateUrl` empty to reuse the VPC's existing endpoints.

## VPC Endpoint Conflict Matrix

The most common deployment failure in this project is a CREATE_FAILED from a VPC
endpoint conflict. Pattern 4 reuses an S3 Gateway Endpoint (AutoMQ durable tier)
and a Secrets Manager Interface Endpoint via a nested stack. If the same VPC
already has a same-service endpoint, they conflict. Preflight (`--profile
pipeline-pattern-4`) detects this.

| Endpoint type | Conflict condition | Avoidance (parameter) |
|---|---|---|
| S3 Gateway Endpoint | A same-service S3 Gateway Endpoint already exists on the same VPC + route table | Leave `VpcEndpointsTemplateUrl` empty to reuse the existing one |
| Secrets Manager Interface Endpoint | A Secrets Manager Interface Endpoint (PrivateDns enabled) already exists in the VPC | Leave `VpcEndpointsTemplateUrl` empty to reuse the existing one |

On a detected conflict, leave `VpcEndpointsTemplateUrl` empty to reuse the
existing endpoints before re-running.

## Deployment Time Estimates

The figures below are estimates, not measured guarantees (Claim_Tier:
unverified). Actual times vary with region, instance type, and account state.

| Step | Estimate |
|---|---|
| Preflight | under 1 minute |
| Nested VPC endpoints stack (when created) | 3–5 minutes |
| Kafka broker stack (EC2 + IAM + SG) | 3–5 minutes |
| Total | roughly 6–12 minutes (excludes the manual WAL-volume set/validate step) |

## Intended Deployment Paths

These are the **intended** paths; the silly-rename live run (spec task 16) has not
been executed. Legs that involve live behavior confirmation are therefore treated
as **Claim_Tier: unverified/hypothesis** and are not presented as verified. They
become `sample-run` only after the live run records a result in the
Verification_Record.

| Stack | Endpoint setting | Claim_Tier |
|---|---|---|
| Broker stack only (reuse existing VPC endpoints) | `VpcEndpointsTemplateUrl` empty | unverified (live not run) |
| Broker + nested VPC endpoints (set `PACKAGE_BUCKET`) | Create S3 Gateway + Secrets Manager Interface | unverified (live not run) |
| NFS-hosted Kafka silly-rename no-crash | Above plus the WAL/log NFS mount (after validating the guardrail below) | hypothesis (live not run; planned in spec task 16) |

## Deployment Steps

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export FILE_SYSTEM_ID="fs-0123456789abcdef0"
export VPC_ID="vpc-0123456789abcdef0"
export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export PACKAGE_BUCKET="my-cfn-package-bucket"
bash integrations/pipeline-verification/pattern-4-kafka-automq/scripts/deploy.sh --profile pipeline-pattern-4 --mode automq-wal
```

`deploy.sh` runs in this order: (1) resolve the Reuse_Reference, (2) preflight,
(3) resolve the account with `aws sts get-caller-identity`, (4) package the shared
VPC endpoints template and `aws cloudformation deploy`.

## Verification Steps

```bash
bash integrations/pipeline-verification/pattern-4-kafka-automq/scripts/verify.sh
```

Confirms stack health, the broker's SSM reachability, and the Gen2
high-throughput WAL intent. It runs no load or scale measurement.

The NFS-hosted Kafka silly-rename sample run requires a running FSx for ONTAP
account and is billed (spec task 16). Record its result in the Verification_Record
with Claim_Tier `sample-run` once run.

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, minimal sizing) and are based
on published unit prices, not measured here. The AutoMQ benchmark figures are
AWS's own published numbers (cited below), not measured here.

| Driver | Note |
|---|---|
| FSx for ONTAP Gen2 high-throughput WAL | **Costs more than the standard configuration** and is billed continuously while running. This is the pattern's largest steady-state cost driver. This environment does not create or delete the file system; it reuses it as a shared foundation |
| EC2 Kafka broker(s) | Billed continuously while running (default `m7g.large`; the AWS benchmark used `m7g.4xlarge`) |
| S3 (AutoMQ durable tier) | Storage and request charges for the S3-backed durable tier |
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). This environment uses an S3 Gateway Endpoint; count the Interface Endpoint only if your setup creates one |

**AWS's published AutoMQ benchmark** (their measurement, not this document's):
the [AWS Storage blog post (2026)](https://aws.amazon.com/blogs/storage/achieving-sub-10ms-latency-and-94-cost-savings-with-diskless-kafka-using-automq-and-amazon-fsx-for-netapp-ontap/)
reports, for 3x m7g.4xlarge brokers on FSx for ONTAP Generation 2 Multi-AZ (1,024
GiB, 3,072 provisioned IOPS, 736 MBps, us-east-1), an average end-to-end latency
of 7.79 ms (P99 18.04 ms), and a cost comparison of roughly $317,000/month for
traditional three-replica Multi-AZ Kafka versus roughly $18,345/month for AutoMQ
BYOC at the same P99 write-latency target. These are cited as AWS's own figures,
kept distinct from any measurement in this environment.

## Ephemeral Configuration

Stand up the minimal configuration verification needs, then tear it down promptly.
`m7g.large` and a small broker count are verification sizing, not production
capacity planning. Remove it with `teardown.sh` once verification is done, and
lower the Gen2 high-throughput setting on the foundation stack if no other pattern
needs it.

## Teardown Steps

```bash
bash integrations/pipeline-verification/pattern-4-kafka-automq/scripts/teardown.sh
```

Deletes the stack in reverse dependency order and judges completion by polling
stack state rather than the delete API response. It surfaces a cost reminder
before you leave the Gen2 high-throughput WAL configuration billing, and does not
delete the FSx for ONTAP file system by default (a shared foundation, not this
stack's to own). `--delete-fsxn` shows an irreversible-confirmation gate, which
`-y`/`--yes` cannot skip.

## Day 2 Operations

Post-deploy verification and the starting point for ongoing operation if you take
this toward production.

- **Immediately after deploy**: run `verify.sh` to confirm stack health, the
  broker's SSM reachability, and the Gen2 high-throughput WAL intent. Enter the
  broker via SSM Session Manager and confirm the WAL/log NFS mount was not
  auto-performed (the silly-rename guardrail).
- **Ongoing checks**: watch broker liveness and write success to the AutoMQ
  durable tier (S3). When a WAL/log directory is placed on NFS, keep confirming
  that the `-is-preserve-unlink-enabled=true` setting from the guardrail below
  remains in place.
- **Where alarms/monitoring attach**: for the AWS resources (EC2, S3), attach
  CloudWatch metrics and alarms using `shared/templates/fsxn-monitoring-dashboard.yaml`
  as a pointer. For routing/transform-layer observability, reference
  `integrations/otel-collector/` (a documentation pointer). This pattern does not
  create these automatically (it is a verification environment).
- **Periodic review**: do not leave the ephemeral configuration running. The Gen2
  high-throughput WAL is the pattern's largest steady-state cost driver, so tear
  it down with `teardown.sh` once verification is done, and lower the Gen2
  high-throughput setting on the foundation stack if no other pattern needs it.

## Rollback and Cleanup

This is distinct from ordinary teardown (removal after verification): it is what
to do when a deploy fails midway.

- **Recover from CREATE_FAILED**: if the stack stops at CREATE_FAILED, delete it
  with `aws cloudformation delete-stack --stack-name fsxn-pattern-4-kafka-automq`.
  A stack in ROLLBACK_COMPLETE cannot be updated, so delete it and re-create.
- **Re-run preflight**: after deletion, fix the cause (most often a VPC endpoint
  conflict) and re-run `preflight-check.sh --profile pipeline-pattern-4` until it
  is green before redeploying.
- **The common VPC EP conflict fix**: if an S3 Gateway or Secrets Manager
  Interface Endpoint already exists, leave `VpcEndpointsTemplateUrl` empty to reuse
  it (see the conflict matrix above).
- **Full removal**: for ordinary removal after verification, use `teardown.sh`
  (the Teardown Steps above). Follow the cost reminder so the Gen2 high-throughput
  WAL is not left billing.

## ONTAP Version Requirements

When a broker's log/WAL directory is placed on NFS, it requires the silly-rename
fix `-is-preserve-unlink-enabled=true` (introduced in ONTAP 9.12.1). As stated in
"FSx for ONTAP Silly-Rename Guardrail" above, two things are kept distinct here:

- **The ONTAP version prerequisite is met.** FSx for ONTAP file systems run ONTAP
  9.18.1 or later. This is a direct field confirmation with the AWS service team,
  not a publicly published version number — readers cannot verify it from public
  FSx for ONTAP documentation alone. Because 9.18.1 is well past 9.12.1, the
  version supports the fix.
- **A specific FSx for ONTAP volume being configured and validated end-to-end is
  not confirmed.** Setting and validating `-is-preserve-unlink-enabled=true` on the
  volume before mounting the WAL/log is an operator step; the live confirmation is
  spec task 16 (billed).

For this reason `deploy.sh` does not auto-mount the WAL/log on NFS. Do not conflate
meeting the version prerequisite with a specific volume having been set and
validated.

## Scope Boundary

This verification environment covers a verification environment and a verification
record, not a production-grade deployment. Multi-region HA for Kafka is out of
scope. Load and scale benchmarks beyond a single sample run are out of scope
unless explicitly pursued; the AutoMQ latency/cost numbers here are AWS's cited
benchmark, not a reproduction.

## Related Documents

- [Verification record: Pattern 4](../../../../docs/en/observability-storage-patterns/verification/verification-results-pattern-4.md)
- [Integration entry point](../../README.md)
- [Storage consolidation patterns](../../../../docs/en/observability-storage-patterns/README.md)
