# Pattern 1 Setup Guide: MQTT → InfluxDB → Grafana Live Verification Environment

🌐 [日本語](../ja/setup-guide.md) | **English** (this page)

How to stand up the MQTT broker + InfluxDB verification environment on top of
reused existing assets. This covers a verification environment and a verification
record, not a production-grade deployment.

## Overview

An MQTT broker (Mosquitto by default) receives telemetry from intra-VPC
publishers, and a single-node InfluxDB server (v1 or v2, both on the TSM storage
engine) keeps recent data on a local hot tier (a local EBS block volume). Older
data is exported to a cold archive on FSx for ONTAP S3 Access Points. The live
dashboard layer is Grafana, reused from `integrations/grafana/` as a mandatory
documentation pointer, not rebuilt here.

This verification environment covers the net-new broker + InfluxDB server layer
and the downstream archive path, not a clustered production deployment.

## Existing Coverage vs Net-New

| Layer | Category | Where |
|---|---|---|
| MQTT broker (Mosquitto) | net-new | `template.yaml` (this pattern) |
| InfluxDB v1/v2 single-node server (local hot tier) | net-new | `template.yaml` (this pattern) |
| Grafana Live visualization | reused (partially covered) | `integrations/grafana/` (mandatory doc pointer, requirement 2-9) |
| archive target (S3 Access Points) | reused | `shared/templates/s3-access-point.yaml` (nested stack) |
| ingestion example (IoT Core → Lambda → S3 Access Point) | reused | `ontap-edge-to-cloud-ai:cloud/iot_ingestion/` (doc pointer) |
| FSx for ONTAP file system + SVM | reused | ontap-edge-to-cloud-ai `cloud/fsxn/template.yaml` (doc pointer) |

The broker and the InfluxDB server are net-new because the repository has a
generic archive layer (S3 Access Points) and a Grafana integration, but no MQTT
broker and no time-series database server. The Grafana visualization layer is
**partially covered** already — `integrations/grafana/` provides the dashboards
and alerting — so it is reused as a mandatory documentation pointer, not
reimplemented (requirement 2-9). The archive references the existing shared
template as a nested stack; it is not copied. The server's own EC2, security
group, self-referencing MQTT ingress, and instance role/profile resources share
no shape with any existing template.

## Grafana Visualization Reference

The live-dashboard layer is **reused, not rebuilt**. Follow
`integrations/grafana/` to point Grafana at the InfluxDB HTTP API on the server
host (reachable intra-VPC through the self-referencing security-group ingress).
Do not copy Grafana dashboards or provisioning into this pattern; reference that
integration so it stays a single source of truth.

## Ingestion Example Reference

For an end-to-end managed ingestion path (IoT Core → Lambda → FSx for ONTAP S3
Access Point), reference `ontap-edge-to-cloud-ai:cloud/iot_ingestion/` (the
sibling repository, local checkout directory `edge-to-cloud-ai`) as a
documentation pointer. It is an example of the ingestion side, not copied into
this repository; the MQTT broker here is the net-new intra-VPC path.

## Choosing InfluxDB v1 vs v2

InfluxDB v1 and v2 suit different contexts; this is a right-tool-for-the-job
choice, not a version contest. Both use the TSM storage engine and share the same
FSx for ONTAP guardrail below. The trade-offs are stated symmetrically.

| Line | Suits | Trade-off / consideration |
|---|---|---|
| v2 (default here) | New setups wanting Flux, the built-in UI, tokens, and buckets/orgs | Flux is a different query language; teams with InfluxQL tooling and dashboards must migrate or use the v1 compatibility API |
| v1 | Teams with existing InfluxQL queries, Grafana panels, and Telegraf configs targeting v1 | Older line; the ecosystem is consolidating on v2/v3, so plan the eventual migration path |

Both keep their data directory on local disk in this environment; the choice does
not change the FSx for ONTAP guardrail below.

## FSx for ONTAP Guardrail

**InfluxDB v1/v2 (TSM engine) document an NFS locking failure** (stale NFS file
handle) for the data directory. Do not point the InfluxDB data directory at an
FSx for ONTAP NFS mount. For this reason the template places the hot tier on a
dedicated local EBS volume (`/dev/xvdb`) and mounts no NFS.

The **iSCSI-LUN hot tier is a Hot_Tier_Storage_Hypothesis**: using an iSCSI LUN
as a local block device for the hot tier may work, but it is not asserted here.
It is distinguished from the default EBS hot tier and tracked in the
Verification_Record as the named measurement target — iSCSI-LUN InfluxDB v1/v2
read/write/lock correctness — starting at Claim_Tier `hypothesis`. A single
read → write → lock correctness sample run (the live spec task 19, EC2-based
because ECS Fargate has no iSCSI initiator) promotes it to `sample-run`. FSx for
ONTAP touches only the downstream archive export, never the hot data directory.

## Prerequisites

- A running FSx for ONTAP file system (including an SVM)
- A target VPC and private subnets
- An existing S3 bucket backing the archive (when enabling the archive target)
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
| `ARCHIVE_BUCKET_NAME` (archive S3 bucket) | `aws s3 ls` (use the parallel archive path's write target) |
| `PACKAGE_BUCKET` (S3 bucket for the nested-stack template) | `aws s3 ls` (a same-region bucket CloudFormation can read the nested template from) |

## Parameters

Follows the AllowedPattern conventions from
`shared/templates/restore-verification.yaml` and pattern 2/5. The account ID is
not hardcoded; `deploy.sh` resolves it at runtime with
`aws sts get-caller-identity`. The region is overridable via the `AWS_REGION`
environment variable (default `ap-northeast-1`). Example values are placeholders;
substitute your own.

| Parameter | Description | How to obtain / example value | Required? |
|---|---|---|---|
| `VpcId` | VPC where the broker and InfluxDB server are placed | `aws ec2 describe-vpcs`. e.g. `vpc-0123456789abcdef0` | Required |
| `SubnetIds` | Private subnets (the instance goes into the first) | `aws ec2 describe-subnets`. e.g. `subnet-0123456789abcdef0,subnet-0123456789abcdef1` | Required |
| `FileSystemId` | FSx for ONTAP file system ID (`^fs-[0-9a-f]{17}$`) | `aws fsx describe-file-systems`. e.g. `fs-0123456789abcdef0` | Required |
| `OntapMgmtIp` | ONTAP management endpoint IP | `aws fsx describe-file-systems`. e.g. `198.51.100.10` | Required |
| `SvmId` | SVM identifier | `aws fsx describe-storage-virtual-machines`. e.g. `svm-0123456789abcdef0` | Optional |
| `InfluxDbLine` | `v2` (default) or `v1` | See "Choosing InfluxDB v1 vs v2" below. e.g. `v2` | Optional (default `v2`) |
| `ReuseArchiveStackName` | Name of the reused S3 Access Point archive stack | Any name. e.g. `fsxn-pattern-1-archive` | Optional (has default) |
| `MqttBrokerPort` | Mosquitto broker MQTT listener port | e.g. `1883` (reachable from intra-VPC publishers only) | Optional (default 1883) |
| `LocalHotTierVolumeSizeGiB` | Local hot-tier EBS volume size (GiB) | e.g. `50` | Optional (default 50) |

## Preflight Validation

Before standup, run the existing shared preflight script with the pattern-1
profile. `deploy.sh` calls it automatically.

```bash
bash shared/scripts/preflight-check.sh --profile pipeline-pattern-1 --vpc-id vpc-0123456789abcdef0
```

If it detects a VPC endpoint conflict, standup does not proceed; it presents the
fix (for example `CreateXxxEndpoint=false`).

## VPC Endpoint Conflict Matrix

The most common deployment failure in this project is a CREATE_FAILED from a VPC
endpoint conflict. Pattern 1's archive path uses an S3 Gateway Endpoint. If the
same VPC and route table already has a same-service Gateway Endpoint, they
conflict. Preflight (`--profile pipeline-pattern-1`) detects this, along with the
S3 Access Point network origin and AD DC reachability (for an AD-joined SVM).

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
| Broker + InfluxDB server stack (EC2 + EBS + IAM + SG) | 3–5 minutes |
| Nested archive stack (S3 Access Point) | 2–4 minutes |
| Total | roughly 5–10 minutes |

## Intended Deployment Paths

These are the **intended** paths; the iSCSI hot-tier live run (spec task 19) has
not been executed. Legs that involve live reachability or lock correctness are
therefore treated as **Claim_Tier: unverified/hypothesis** and are not presented
as verified. They become `sample-run` only after the live run records a result in
the Verification_Record.

| Stack | Endpoint setting | Claim_Tier |
|---|---|---|
| Broker + InfluxDB server stack only (archive disabled) | No VPC EP added | unverified (live not run) |
| Broker + InfluxDB + nested archive (set `ARCHIVE_BUCKET_NAME` + `PACKAGE_BUCKET`) | Reuse the existing S3 Gateway Endpoint | unverified (live not run) |
| iSCSI-LUN hot-tier InfluxDB v1/v2 read/write/lock correctness | Above plus an iSCSI LUN mounted on EC2 | hypothesis (live not run; planned in spec task 19) |

## Deployment Steps

```bash
export ONTAP_MGMT_IP="198.51.100.10"
export FILE_SYSTEM_ID="fs-0123456789abcdef0"
export VPC_ID="vpc-0123456789abcdef0"
export SUBNET_IDS="subnet-0123456789abcdef0,subnet-0123456789abcdef1"
export ARCHIVE_BUCKET_NAME="my-archive-bucket"
export PACKAGE_BUCKET="my-cfn-package-bucket"
bash integrations/pipeline-verification/pattern-1-mqtt-influxdb/scripts/deploy.sh --profile pipeline-pattern-1 --line v2
```

`deploy.sh` runs in this order: (1) resolve the Reuse_Reference, (2) preflight,
(3) resolve the account with `aws sts get-caller-identity`, (4) package the shared
archive template and `aws cloudformation deploy`.

## Verification Steps

```bash
bash integrations/pipeline-verification/pattern-1-mqtt-influxdb/scripts/verify.sh
```

Confirms stack health, the InfluxDB server's SSM reachability, and resolution of
the reused archive target. It runs no load or scale measurement.

The iSCSI-LUN hot-tier sample run requires a running FSx for ONTAP account and is
billed (spec task 19). Record its result in the Verification_Record with
Claim_Tier `sample-run` once run.

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, single AZ, minimal sizing) and
are based on published unit prices, not measured here.

| Driver | Note |
|---|---|
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). This environment uses an S3 Gateway Endpoint, so an Interface Endpoint is not required; count it only if your existing setup creates one |
| FSx for ONTAP file system | Billed continuously while running. This verification environment does not create or delete it; it reuses it as a shared foundation |
| EC2 InfluxDB server | Billed continuously while running (default `t3.medium`) |
| EBS local hot-tier volume | Billed continuously while running (default 50 GiB gp3) |

## Ephemeral Configuration

Stand up the minimal configuration verification needs, then tear it down promptly.
`t3.medium` and a 50 GiB local hot tier are verification sizing, not production
capacity planning. Remove it with `teardown.sh` once verification is done.

## Teardown Steps

```bash
bash integrations/pipeline-verification/pattern-1-mqtt-influxdb/scripts/teardown.sh
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
  InfluxDB server's SSM reachability, and resolution of the reused archive target.
  Enter the server via SSM Session Manager and confirm that the InfluxDB data
  directory points at the local EBS mount (`/var/lib/influxdb`) and mounts no NFS
  (the TSM lock guardrail).
- **Ongoing checks**: watch MQTT ingest, the hot tier's (local EBS or the
  iSCSI-LUN hypothesis) write and lock-acquisition health, and whether the archive
  export to S3 Access Points succeeds. The hot tier expires at
  `HotTierRetentionDays` (default 15 days), so confirm the archive is the system of
  record before then.
- **Where alarms/monitoring attach**: for live dashboards and alerting, reference
  `integrations/grafana/` (the mandatory visualization-layer documentation pointer,
  requirement 2-9). For the AWS resources (EC2, EBS, S3), attach CloudWatch metrics
  and alarms using `shared/templates/fsxn-monitoring-dashboard.yaml` as a pointer.
  This pattern does not create these automatically (it is a verification
  environment).
- **Periodic review**: do not leave the ephemeral configuration running. Tear it
  down with `teardown.sh` once verification is done to stop the continuous EC2/EBS
  charges.

## Rollback and Cleanup

This is distinct from ordinary teardown (removal after verification): it is what
to do when a deploy fails midway.

- **Recover from CREATE_FAILED**: if the stack stops at CREATE_FAILED, delete it
  with `aws cloudformation delete-stack --stack-name fsxn-pattern-1-mqtt-influxdb`.
  A stack in ROLLBACK_COMPLETE cannot be updated, so delete it and re-create.
- **Re-run preflight**: after deletion, fix the cause (most often a VPC endpoint
  conflict) and re-run `preflight-check.sh --profile pipeline-pattern-1` until it
  is green before redeploying.
- **The common VPC EP conflict fix**: if a same-service Gateway/Interface Endpoint
  already exists, switch to reusing it (see the conflict matrix above).
- **Full removal**: for ordinary removal after verification, use `teardown.sh`
  (the Teardown Steps above).

## ONTAP Version Requirements

Pattern 1 touches FSx for ONTAP only through the downstream archive export
(writing to S3 Access Points); the hot data directory stays on local EBS. This S3
Access Points path works on standard S3-capable FSx for ONTAP versions and carries
no version-specific prerequisite such as the silly-rename fix. The iSCSI-LUN
hot-tier hypothesis path (spec task 19) likewise needs no special version
prerequisite, since serving an iSCSI LUN is a standard FSx for ONTAP capability.
Confirm only that your FSx for ONTAP runs a version that supports S3 Access Points
and iSCSI.

## Scope Boundary

This verification environment covers a verification environment and a verification
record, not a production-grade deployment. Multi-region HA for InfluxDB is out of
scope. Load and scale benchmarks beyond a single sample run are out of scope unless
explicitly pursued.

## Related Documents

- [Verification record: Pattern 1](../../../../docs/en/observability-storage-patterns/verification/verification-results-pattern-1.md)
- [Integration entry point](../../README.md)
- [Storage consolidation patterns](../../../../docs/en/observability-storage-patterns/README.md)
