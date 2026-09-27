# Pattern 2 Setup Guide: Prometheus + remote_write Verification Environment

🌐 [日本語](../ja/setup-guide.md) | **English** (this page)

How to stand up the Prometheus + remote_write pipeline verification environment on
top of reused existing assets. This covers a verification environment and a
verification record, not a production-grade deployment.

## Overview

Prometheus writes scraped metrics to its local TSDB (a local EBS block volume) and
forwards samples via `remote_write` to a long-term storage backend (Thanos, Mimir,
Cortex, or a vendor platform). That backend's object-store export is archived to
FSx for ONTAP S3 Access Points.

This verification environment covers the downstream archive path of remote_write,
not Prometheus's own local TSDB.

## Existing Coverage vs Net-New

| Layer | Category | Where |
|---|---|---|
| Prometheus server (local TSDB) | net-new | `template.yaml` (this pattern) |
| remote_write archive target (S3 Access Points) | reused | `shared/templates/s3-access-point.yaml` (nested stack) |
| Visualization and alerting | reused | `integrations/grafana/` (doc pointer) |
| FSx for ONTAP file system + SVM | reused | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml` (doc pointer) |

The Prometheus layer is net-new because the repository already has a generic
archive layer (S3 Access Points) but no Prometheus layer. The archive target
references the existing shared template as a nested stack; it is not copied.

## FSx for ONTAP Guardrail

**Prometheus documents NFS/EFS as unsupported for its local TSDB.** Do not point
`--storage.tsdb.path` at an FSx for ONTAP NFS mount. For this reason the template
places the local TSDB on a dedicated EBS volume (`/dev/xvdb`) and mounts no NFS.

FSx for ONTAP touches only the downstream remote_write export (the long-term
backend's object-store output), never Prometheus's own hot TSDB.

## Prerequisites

- A running FSx for ONTAP file system (including an SVM)
- A target VPC and private subnets
- An existing S3 bucket backing the remote_write long-term archive (when enabling the archive target)
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
| `ARCHIVE_BUCKET_NAME` (archive S3 bucket) | `aws s3 ls` (use the remote_write backend's export target) |
| `PACKAGE_BUCKET` (S3 bucket for the nested-stack template) | `aws s3 ls` (a same-region bucket CloudFormation can read the nested template from) |

## Parameters

Follows the AllowedPattern conventions from
`shared/templates/restore-verification.yaml`. The account ID is not hardcoded;
`deploy.sh` resolves it at runtime with `aws sts get-caller-identity`. The region
is overridable via the `AWS_REGION` environment variable (default
`ap-northeast-1`). Example values are placeholders; substitute your own.

| Parameter | Description | How to obtain / example value | Required? |
|---|---|---|---|
| `VpcId` | VPC where the Prometheus server is placed | `aws ec2 describe-vpcs`. e.g. `vpc-0123456789abcdef0` | Required |
| `SubnetIds` | Private subnets (the instance goes into the first) | `aws ec2 describe-subnets`. e.g. `subnet-0123456789abcdef0,subnet-0123456789abcdef1` | Required |
| `FileSystemId` | FSx for ONTAP file system ID (`^fs-[0-9a-f]{17}$`) | `aws fsx describe-file-systems`. e.g. `fs-0123456789abcdef0` | Required |
| `OntapMgmtIp` | ONTAP management endpoint IP | `aws fsx describe-file-systems`. e.g. `198.51.100.10` | Required |
| `SvmId` | SVM identifier | `aws fsx describe-storage-virtual-machines`. e.g. `svm-0123456789abcdef0` | Optional |
| `ReuseArchiveStackName` | Name of the reused S3 Access Point archive stack | Any name. e.g. `fsxn-pattern-2-remote-write-archive` | Optional (has default) |
| `PrometheusInstanceType` | EC2 instance type for the Prometheus server | e.g. `t3.medium` (verification sizing) | Optional (default `t3.medium`) |
| `LocalTsdbVolumeSizeGiB` | Local TSDB EBS volume size (GiB) | e.g. `50` | Optional (default 50) |

## Preflight Validation

Before standup, run the existing shared preflight script with the pattern-2
profile. `deploy.sh` calls it automatically.

```bash
bash shared/scripts/preflight-check.sh --profile pipeline-pattern-2 --vpc-id vpc-0123456789abcdef0
```

If it detects a VPC endpoint conflict, standup does not proceed; it presents the
fix (for example `CreateXxxEndpoint=false`).

## VPC Endpoint Conflict Matrix

The most common deployment failure in this project is a CREATE_FAILED from a VPC
endpoint conflict. Pattern 2's archive path uses an S3 Gateway Endpoint. If the
same VPC and route table already has a same-service Gateway Endpoint, they
conflict. Preflight (`--profile pipeline-pattern-2`) detects this.

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
| Prometheus server stack (EC2 + EBS + IAM) | 3–5 minutes |
| Nested archive stack (S3 Access Point) | 2–4 minutes |
| Total | roughly 5–10 minutes |

## Intended Deployment Paths

These are the **intended** paths; the live run (spec task 10) has not been
executed. Legs that involve live reachability are therefore treated as
**Claim_Tier: unverified/hypothesis** and are not presented as verified. They
become `sample-run` only after the live run records a result in the
Verification_Record.

| Stack | Endpoint setting | Claim_Tier |
|---|---|---|
| Prometheus server stack only (archive disabled) | No VPC EP added | unverified (live not run) |
| Prometheus + nested archive (set `ARCHIVE_BUCKET_NAME` + `PACKAGE_BUCKET`) | Reuse the existing S3 Gateway Endpoint | unverified (live not run) |
| remote_write → S3 Access Points reachability | Above plus remote_write backend connectivity | hypothesis (live not run; planned in spec task 10) |

## Deployment Steps

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export FILE_SYSTEM_ID="fs-0123456789abcdef0"
export VPC_ID="vpc-0123456789abcdef0"
export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export ARCHIVE_BUCKET_NAME="my-remote-write-archive-bucket"
export PACKAGE_BUCKET="my-cfn-package-bucket"
bash integrations/pipeline-verification/pattern-2-prometheus/scripts/deploy.sh --profile pipeline-pattern-2
```

`deploy.sh` runs in this order: (1) resolve the Reuse_Reference, (2) preflight,
(3) resolve the account with `aws sts get-caller-identity`, (4) package the shared
archive template and `aws cloudformation deploy`.

## Verification Steps

```bash
bash integrations/pipeline-verification/pattern-2-prometheus/scripts/verify.sh
```

Confirms stack health, the Prometheus server's SSM reachability, and resolution
of the reused archive target. It runs no load or scale measurement.

The remote_write to S3 Access Points reachability sample run requires a running
FSx for ONTAP account and is billed (spec task 10). Record its result in the
Verification_Record with Claim_Tier `sample-run`.

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, single AZ, minimal sizing) and
are based on published unit prices, not measured here.

| Driver | Note |
|---|---|
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). This environment uses an S3 Gateway Endpoint, so an Interface Endpoint is not required; count it only if your existing setup creates one |
| FSx for ONTAP file system | Billed continuously while running. This verification environment does not create or delete it; it reuses it as a shared foundation |
| EC2 Prometheus server | Billed continuously while running (default `t3.medium`) |
| EBS local TSDB volume | Billed continuously while running (default 50 GiB gp3) |

## Ephemeral Configuration

Stand up the minimal configuration verification needs, then tear it down promptly.
`t3.medium` and a 50 GiB local TSDB are verification sizing, not production
capacity planning. Remove it with `teardown.sh` once verification is done.

## Teardown Steps

```bash
bash integrations/pipeline-verification/pattern-2-prometheus/scripts/teardown.sh
```

Deletes the stack in reverse dependency order and judges completion by polling
stack state rather than the delete API response. It does not delete the FSx for
ONTAP file system by default (a shared foundation, not this stack's to own).
`--delete-fsxn` shows an irreversible-confirmation gate, which `-y`/`--yes` cannot
skip.

## Day 2 Operations

Post-deploy verification and the starting point for ongoing operation if you take
this toward production.

- **Immediately after deploy**: run `verify.sh` to confirm stack health, the
  Prometheus server's SSM reachability, and resolution of the reused archive
  target. Enter the server via SSM Session Manager and confirm that
  `--storage.tsdb.path` points at the local EBS mount (`/var/lib/prometheus`) and
  mounts no NFS (the guardrail).
- **Ongoing checks**: watch the remote_write success rate and whether the archive
  export from the long-term backend to FSx for ONTAP S3 Access Points succeeds.
  The local TSDB expires at `RemoteWriteRetentionDays` (default 15 days), so
  confirm remote_write is the system of record before then.
- **Where alarms/monitoring attach**: for Prometheus's own visualization and
  alerting, reference `integrations/grafana/` (the reused visualization layer).
  For the AWS resources (EC2, EBS, S3), attach CloudWatch metrics and alarms using
  `shared/templates/fsxn-monitoring-dashboard.yaml` as a pointer. This pattern
  does not create these automatically (it is a verification environment).
- **Periodic review**: do not leave the ephemeral configuration running. Tear it
  down with `teardown.sh` once verification is done to stop the continuous EC2/EBS
  charges.

## Rollback and Cleanup

This is distinct from ordinary teardown (removal after verification): it is what
to do when a deploy fails midway.

- **Recover from CREATE_FAILED**: if the stack stops at CREATE_FAILED, delete it
  with `aws cloudformation delete-stack --stack-name fsxn-pattern-2-prometheus`. A
  stack in ROLLBACK_COMPLETE cannot be updated, so delete it and re-create.
- **Re-run preflight**: after deletion, fix the cause (most often a VPC endpoint
  conflict) and re-run `preflight-check.sh --profile pipeline-pattern-2` until it
  is green before redeploying.
- **The common VPC EP conflict fix**: if a same-service Gateway/Interface Endpoint
  already exists, switch to reusing it (see the conflict matrix above).
- **Full removal**: for ordinary removal after verification, use `teardown.sh`
  (the Teardown Steps above).

## ONTAP Version Requirements

Pattern 2 touches FSx for ONTAP only through the downstream remote_write archive
export (writing to S3 Access Points); the hot TSDB stays on local EBS. This S3
Access Points path works on standard S3-capable FSx for ONTAP versions and carries
no version-specific prerequisite such as the silly-rename fix. Because it needs no
special version prerequisite, there is no direct-confirmation item like pattern
4's. Confirm only that your FSx for ONTAP runs a version that supports S3 Access
Points.

## Scope Boundary

This verification environment covers a verification environment and a verification
record, not a production-grade deployment. Multi-region HA for the TSDB is out of
scope. Load and scale benchmarks beyond a single sample run are out of scope unless
explicitly pursued.

## Related Documents

- [Pattern 2 overview (storage consolidation patterns)](../../../../docs/en/observability-storage-patterns/pattern-2-prometheus-remote-write.md)
- [Verification record: Pattern 2](../../../../docs/en/observability-storage-patterns/verification/verification-results-pattern-2.md)
- [Integration entry point](../../README.md)
