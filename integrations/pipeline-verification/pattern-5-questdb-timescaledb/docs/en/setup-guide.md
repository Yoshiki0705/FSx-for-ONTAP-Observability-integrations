# Pattern 5 Setup Guide: QuestDB / TimescaleDB Verification Environment

🌐 [日本語](../ja/setup-guide.md) | **English** (this page)

How to stand up the QuestDB / TimescaleDB single-node pipeline verification
environment on top of reused existing assets. This covers a verification
environment and a verification record, not a production-grade deployment.

## Overview

A single-node time-series database (QuestDB by default, TimescaleDB as the
documented alternative) keeps recent data on a local hot tier (a local EBS block
volume) and exports older data to a cold tier archived to FSx for ONTAP S3 Access
Points. ClickHouse is a third option when a columnar analytics engine fits the
workload; its schema is referenced as a documentation pointer, not copied here.

This verification environment covers the single-node server layer and the
downstream cold-tier archive path, not a clustered production deployment.

## Existing Coverage vs Net-New

| Layer | Category | Where |
|---|---|---|
| QuestDB / TimescaleDB single-node server (local hot tier) | net-new | `template.yaml` (this pattern) |
| cold-tier archive target (S3 Access Points) | reused | `shared/templates/s3-access-point.yaml` (nested stack) |
| S3 Access Point + checkpoint prerequisites | reused | `shared/templates/prerequisites.yaml` (nested stack) |
| ClickHouse DDL (when ClickHouse is used) | reused | `ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` (doc pointer) |
| FSx for ONTAP file system + SVM | reused | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml` (doc pointer) |

The single-node TSDB server layer is net-new because the repository already has a
generic archive layer (S3 Access Points) but no time-series database server. The
cold-tier archive references the existing shared template as a nested stack; it is
not copied. The server's own EC2, security group, and instance role/profile
resources share no shape with any existing template.

## Choosing an Engine

QuestDB, TimescaleDB, and ClickHouse suit different contexts; this is a
right-tool-for-the-job choice, not a contest. The trade-offs are stated
symmetrically, the default (QuestDB) included.

| Engine | Suits | Trade-off / consideration |
|---|---|---|
| QuestDB (default here) | High-ingest time-series with SQL and a simple single-binary footprint | Younger ecosystem; fewer managed-service options than PostgreSQL |
| TimescaleDB | Teams already on PostgreSQL wanting time-series extensions and the PostgreSQL tooling | Inherits PostgreSQL operational characteristics; hypertable partitioning needs planning |
| ClickHouse | Columnar analytical queries over large volumes | Not a general-purpose OLTP store; schema design (see the DDL pointer below) drives performance |

All three keep their hot data directory on local disk in this environment; the
choice does not change the FSx for ONTAP guardrail below.

## ClickHouse DDL Reference

When ClickHouse is the engine, reuse the schema at
`ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` (the sibling repository, local
checkout directory `edge-to-cloud-ai`). It provides the raw-events, source-table,
rollup, and export DDL as a documentation pointer. Do not copy the SQL into this
repository; reference it at that exact path so it stays a single source of truth.

## FSx for ONTAP Guardrail

**QuestDB, TimescaleDB, and ClickHouse document NFS/EFS as unsupported for the hot
data directory.** Do not point the engine's data directory at an FSx for ONTAP NFS
mount. For this reason the template places the hot tier on a dedicated EBS volume
(`/dev/xvdb`) and mounts no NFS.

The **iSCSI-LUN hot tier is a Hot_Tier_Storage_Hypothesis**: using an iSCSI LUN as
a local block device for the hot tier may work, but it is not asserted here. It is
distinguished from the default EBS hot tier and tracked in the Verification_Record
as the named measurement target — iSCSI-LUN QuestDB/ClickHouse read/write/lock
correctness — starting at Claim_Tier `hypothesis`. A single read → write → lock
correctness sample run (the live spec task 13, EC2-based because ECS Fargate has no
iSCSI initiator) promotes it to `sample-run`. FSx for ONTAP touches only the
downstream cold-tier export, never the hot data directory.

## Prerequisites

- A running FSx for ONTAP file system (including an SVM)
- A target VPC and private subnets
- An existing S3 bucket backing the cold-tier archive (when enabling the archive target)
- AWS CLI v2 with CloudFormation / EC2 / IAM / S3 permissions

## What You Need Before Starting

The most common first-run stumble is hunting for your existing resource IDs
mid-deploy. Gather the values below **first**. None of them are created by this
verification environment; they are referenced from the existing shared
foundation.

| What to gather | How to obtain |
|---|---|
| `VpcId` (target VPC) | `aws ec2 describe-vpcs --query 'Vpcs[].VpcId' --output text` |
| `SubnetIds` (private subnets) | `aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc-id> --query 'Subnets[].SubnetId' --output text` |
| `FileSystemId` (FSx for ONTAP) | `aws fsx describe-file-systems --query 'FileSystems[?FileSystemType==\`ONTAP\`].FileSystemId' --output text` |
| `OntapMgmtIp` (ONTAP management IP) | `aws fsx describe-file-systems --query 'FileSystems[].OntapConfiguration.Endpoints.Management.IpAddresses' --output text` |
| `SvmId` (SVM identifier, optional) | `aws fsx describe-storage-virtual-machines --query 'StorageVirtualMachines[].StorageVirtualMachineId' --output text` |
| `ARCHIVE_BUCKET_NAME` (cold-tier archive S3 bucket) | `aws s3 ls` (use the TSDB's cold-tier export target) |
| `PACKAGE_BUCKET` (S3 bucket for the nested-stack template) | `aws s3 ls` (a same-region bucket CloudFormation can read the nested template from) |

## Parameters

Follows the AllowedPattern conventions from
`shared/templates/restore-verification.yaml` and pattern 2. The account ID is not
hardcoded; `deploy.sh` resolves it at runtime with `aws sts get-caller-identity`.
The region is overridable via the `AWS_REGION` environment variable (default
`ap-northeast-1`). Example values are placeholders; substitute your own.

| Parameter | Description | How to obtain / example value | Required? |
|---|---|---|---|
| `VpcId` | VPC where the TSDB server is placed | `aws ec2 describe-vpcs`. e.g. `vpc-0123456789abcdef0` | Required |
| `SubnetIds` | Private subnets (the instance goes into the first) | `aws ec2 describe-subnets`. e.g. `subnet-0123456789abcdef0,subnet-0123456789abcdef1` | Required |
| `FileSystemId` | FSx for ONTAP file system ID (`^fs-[0-9a-f]{17}$`) | `aws fsx describe-file-systems`. e.g. `fs-0123456789abcdef0` | Required |
| `OntapMgmtIp` | ONTAP management endpoint IP | `aws fsx describe-file-systems`. e.g. `198.51.100.10` | Required |
| `SvmId` | SVM identifier | `aws fsx describe-storage-virtual-machines`. e.g. `svm-0123456789abcdef0` | Optional |
| `TsdbEngine` | `questdb` (default) or `timescaledb` | See "Choosing an Engine" below. e.g. `questdb` | Optional (default `questdb`) |
| `ReuseArchiveStackName` | Name of the reused S3 Access Point cold-tier archive stack | Any name. e.g. `fsxn-pattern-5-cold-tier-archive` | Optional (has default) |
| `TsdbInstanceType` | EC2 instance type for the TSDB server | e.g. `t3.medium` (verification sizing) | Optional (default `t3.medium`) |
| `LocalHotTierVolumeSizeGiB` | Local hot-tier EBS volume size (GiB) | e.g. `50` | Optional (default 50) |

## Preflight Validation

Before standup, run the existing shared preflight script with the pattern-5
profile. `deploy.sh` calls it automatically.

```bash
bash shared/scripts/preflight-check.sh --profile pipeline-pattern-5 --vpc-id vpc-0123456789abcdef0
```

If it detects a VPC endpoint conflict, standup does not proceed; it presents the
fix (for example `CreateXxxEndpoint=false`).

## VPC Endpoint Conflict Matrix

The most common deployment failure in this project is a CREATE_FAILED from a VPC
endpoint conflict. Pattern 5's cold-tier export path uses an S3 Gateway Endpoint.
If the same VPC and route table already has a same-service Gateway Endpoint, they
conflict. Preflight (`--profile pipeline-pattern-5`) detects this. The iSCSI hot
tier is EC2-based, so preflight also checks iSCSI target reachability.

| Endpoint type | Conflict condition | Avoidance |
|---|---|---|
| S3 Gateway Endpoint | A same-service S3 Gateway Endpoint already exists on the same VPC + route table | Reuse the existing one. This pattern does not add a Gateway Endpoint, so an existing one is used as-is |
| Interface Endpoint (SSM, etc.) | A same-service Interface Endpoint (PrivateDns enabled) already exists in the VPC | Reuse the existing one. SSM Session Manager reachability is met by the existing Interface Endpoint |

On a detected conflict, switch to reusing the existing endpoint before re-running.

## Deployment Time Estimates

The figures below are estimates, not measured guarantees (Claim_Tier:
unverified). Actual times vary with region, instance type, and account state.

| Step | Estimate |
|---|---|
| Preflight | under 1 minute |
| TSDB server stack (EC2 + EBS + IAM) | 3–5 minutes |
| Nested cold-tier archive stack (S3 Access Point) | 2–4 minutes |
| Total | roughly 5–10 minutes |

## Intended Deployment Paths

These are the **intended** paths; the iSCSI hot-tier live run (spec task 13) has
not been executed. Legs that involve live reachability or lock correctness are
therefore treated as **Claim_Tier: unverified/hypothesis** and are not presented
as verified. They become `sample-run` only after the live run records a result in
the Verification_Record.

| Stack | Endpoint setting | Claim_Tier |
|---|---|---|
| TSDB server stack only (cold tier disabled) | No VPC EP added | unverified (live not run) |
| TSDB + nested cold tier (set `ARCHIVE_BUCKET_NAME` + `PACKAGE_BUCKET`) | Reuse the existing S3 Gateway Endpoint | unverified (live not run) |
| iSCSI-LUN hot-tier read/write/lock correctness | Above plus an iSCSI LUN mounted on EC2 | hypothesis (live not run; planned in spec task 13) |

## Deployment Steps

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export FILE_SYSTEM_ID="fs-0123456789abcdef0"
export VPC_ID="vpc-0123456789abcdef0"
export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export ARCHIVE_BUCKET_NAME="my-cold-tier-archive-bucket"
export PACKAGE_BUCKET="my-cfn-package-bucket"
bash integrations/pipeline-verification/pattern-5-questdb-timescaledb/scripts/deploy.sh --profile pipeline-pattern-5 --engine questdb
```

`deploy.sh` runs in this order: (1) resolve the Reuse_Reference, (2) preflight,
(3) resolve the account with `aws sts get-caller-identity`, (4) package the shared
archive template and `aws cloudformation deploy`.

## Verification Steps

```bash
bash integrations/pipeline-verification/pattern-5-questdb-timescaledb/scripts/verify.sh
```

Confirms stack health, the TSDB server's SSM reachability, and resolution of the
reused cold-tier archive target. It runs no load or scale measurement.

The iSCSI-LUN hot-tier sample run requires a running FSx for ONTAP account and is
billed (spec task 13). Record its result in the Verification_Record with Claim_Tier
`sample-run` once run.

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, single AZ, minimal sizing) and
are based on published unit prices, not measured here.

| Driver | Note |
|---|---|
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). This environment uses an S3 Gateway Endpoint, so an Interface Endpoint is not required; count it only if your existing setup creates one |
| FSx for ONTAP file system | Billed continuously while running. This verification environment does not create or delete it; it reuses it as a shared foundation |
| EC2 TSDB server | Billed continuously while running (default `t3.medium`) |
| EBS local hot-tier volume | Billed continuously while running (default 50 GiB gp3) |

## Ephemeral Configuration

Stand up the minimal configuration verification needs, then tear it down promptly.
`t3.medium` and a 50 GiB local hot tier are verification sizing, not production
capacity planning. Remove it with `teardown.sh` once verification is done.

## Teardown Steps

```bash
bash integrations/pipeline-verification/pattern-5-questdb-timescaledb/scripts/teardown.sh
```

Deletes the stack in reverse dependency order and judges completion by polling
stack state rather than the delete API response. It does not delete the FSx for
ONTAP file system by default (a shared foundation, not this stack's to own).
`--delete-fsxn` shows an irreversible-confirmation gate, which `-y`/`--yes` cannot
skip.

## Day 2 Operations

Post-deploy verification and the starting point for ongoing operation if you take
this toward production.

- **Immediately after deploy**: run `verify.sh` to confirm stack health, the TSDB
  server's SSM reachability, and resolution of the reused cold-tier archive
  target. Enter the server via SSM Session Manager and confirm that the engine's
  data directory points at the local EBS mount (`/var/lib/tsdb`) and mounts no NFS
  (the guardrail).
- **Ongoing checks**: watch the hot tier's (local EBS or the iSCSI-LUN hypothesis)
  write and lock-acquisition health, and whether the cold-tier export to S3 Access
  Points succeeds. The hot tier expires at `HotTierRetentionDays` (default 15
  days), so confirm the cold tier is the system of record before then.
- **Where alarms/monitoring attach**: for the AWS resources (EC2, EBS, S3), attach
  CloudWatch metrics and alarms using `shared/templates/fsxn-monitoring-dashboard.yaml`
  as a pointer. For the engine's own visualization, reference `integrations/grafana/`.
  This pattern does not create these automatically (it is a verification
  environment).
- **Periodic review**: do not leave the ephemeral configuration running. Tear it
  down with `teardown.sh` once verification is done to stop the continuous EC2/EBS
  charges.

## Rollback and Cleanup

This is distinct from ordinary teardown (removal after verification): it is what
to do when a deploy fails midway.

- **Recover from CREATE_FAILED**: if the stack stops at CREATE_FAILED, delete it
  with `aws cloudformation delete-stack --stack-name fsxn-pattern-5-questdb-timescaledb`.
  A stack in ROLLBACK_COMPLETE cannot be updated, so delete it and re-create.
- **Re-run preflight**: after deletion, fix the cause (most often a VPC endpoint
  conflict) and re-run `preflight-check.sh --profile pipeline-pattern-5` until it
  is green before redeploying.
- **The common VPC EP conflict fix**: if a same-service Gateway/Interface Endpoint
  already exists, switch to reusing it (see the conflict matrix above).
- **Full removal**: for ordinary removal after verification, use `teardown.sh`
  (the Teardown Steps above).

## ONTAP Version Requirements

Pattern 5 touches FSx for ONTAP only through the downstream cold-tier export
(writing to S3 Access Points); the hot data directory stays on local EBS. This S3
Access Points path works on standard S3-capable FSx for ONTAP versions and carries
no version-specific prerequisite such as the silly-rename fix. The iSCSI-LUN
hot-tier hypothesis path (spec task 13) likewise needs no special version
prerequisite, since serving an iSCSI LUN is a standard FSx for ONTAP capability.
Confirm only that your FSx for ONTAP runs a version that supports S3 Access Points
and iSCSI.

## Scope Boundary

This verification environment covers a verification environment and a verification
record, not a production-grade deployment. Multi-region HA for the TSDB is out of
scope. Load and scale benchmarks beyond a single sample run are out of scope unless
explicitly pursued.

## Related Documents

- [Verification record: Pattern 5](../../../../docs/en/observability-storage-patterns/verification/verification-results-pattern-5.md)
- [Integration entry point](../../README.md)
- [Storage consolidation patterns](../../../../docs/en/observability-storage-patterns/README.md)
