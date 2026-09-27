# Verification Record: Pattern 5 (QuestDB / TimescaleDB)

🌐 [日本語](../../../ja/observability-storage-patterns/verification/verification-results-pattern-5.md) | **English** (this page)

Verification record for the Pattern 5 (QuestDB / TimescaleDB) verification
environment. It follows the format of the repository's existing
`docs/en/verification-results-*.md` records. Use placeholders for real IPs,
account IDs, and resource IDs.

> The live-AWS step (iSCSI-LUN hot-tier read/write/lock correctness) is spec task
> 13; it requires a running FSx for ONTAP account and is billed. It does not run
> in CI. For now its Actual is left blank, its Verdict `⬜`, and its Claim_Tier
> `hypothesis` for the iSCSI hot-tier target and `unverified` for the deploy
> steps.

## Metadata

| Field | Value |
|---|---|
| Pattern | 5 (QuestDB / TimescaleDB) |
| Verification date (measured) | `<YYYY-MM-DDTHH:MM:SS+09:00>` (fill in on the live run) |
| AWS Region | `ap-northeast-1` (default; override with `AWS_REGION`) |
| CloudFormation stack name | `fsxn-pattern-5-questdb-timescaledb` |
| FSx for ONTAP file system | `fs-0123456789abcdef0` (placeholder) |
| SVM | `svm-0123456789abcdef0` (placeholder) |
| Component versions | `<QuestDB x.y / TimescaleDB x.y / ClickHouse x.y / ONTAP x.y>` (fill in on the live run) |

## Claim Tier Legend

Every claim in this record carries exactly one Claim_Tier. Do not present an
unmeasured performance or cost value as fact; mark it as not measured.

| Tier | Meaning |
|---|---|
| verified | Reproduced and confirmed in this environment |
| sample-run | Confirmed once; not a general service limit |
| documented | From a cited source; not measured here |
| hypothesis | Inferred, no source, not measured |
| unverified | Not yet checked |

## Verification Steps

Each step records Command / Expected / Actual / Verdict, plus the Claim_Tier of
the claim the step establishes.

| # | Command | Expected | Actual | Verdict | Claim_Tier |
|---|---|---|---|---|---|
| 1 | `bash shared/scripts/preflight-check.sh --profile pipeline-pattern-5 --vpc-id <vpc>` | All checks pass (no VPC EP conflict) | `<fill in>` | ⬜ | unverified |
| 2 | `bash scripts/deploy.sh --profile pipeline-pattern-5 --engine questdb` | Stack `CREATE_COMPLETE` | `<fill in>` | ⬜ | unverified |
| 3 | `bash scripts/verify.sh` | Stack healthy, TSDB server online in SSM, cold-tier archive target resolved | `<fill in>` | ⬜ | unverified |
| 4 | iSCSI-LUN hot-tier read → write → lock correctness (spec task 13, live, billed) | QuestDB/ClickHouse read, write, and lock acquisition succeed on the iSCSI LUN | `<fill in>` | ⬜ | hypothesis |

## Hot Tier Storage Hypothesis

Pattern 5's hot tier is on local EBS block storage by default and uses no
NFS/EFS. The **iSCSI-LUN hot tier is the named measurement target** here: whether
an iSCSI LUN used as a local block device holds up for the hot tier is not
asserted, only tracked. A single read → write → lock correctness sample run
promotes it from `hypothesis` to `sample-run`. The run is EC2-based because ECS
Fargate has no iSCSI initiator. Load and scale measurement is out of scope.

| Target | Initial tier | After sample run |
|---|---|---|
| iSCSI LUN read/write/lock correctness for QuestDB/ClickHouse | hypothesis | sample-run |

## Cited Benchmarks

Public vendor/AWS benchmarks are cited with their source and kept distinct from
this environment's own measurements. They stay `documented`; they are not
relabeled `verified` on the strength of a citation.

| Claim | Source | Claim_Tier |
|---|---|---|
| QuestDB documents its data directory as unsupported on network file systems (NFS) | [QuestDB capacity planning](https://questdb.io/docs/operations/capacity-planning/) | documented |
| ClickHouse schema/DDL for the consumer path (raw events, source table, rollup, export) | `ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` (sibling repository, doc pointer) | documented |

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, single AZ, minimal sizing),
based on published unit prices and not measured in this environment.

| Driver | Note | Claim_Tier |
|---|---|---|
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). This environment uses an S3 Gateway Endpoint, so it is usually not needed | documented |
| FSx for ONTAP file system | Billed continuously while running. Reused as a shared foundation; not created or deleted by this environment | documented |
| EC2 TSDB server | Billed continuously while running (default `t3.medium`) | documented |

## Scope Boundary

This record covers a verification environment and its results, not a
production-grade deployment. Multi-region HA and load/scale benchmarks beyond a
single sample run are out of scope unless explicitly pursued.

## Verdict Summary

| Step | Name | Verdict |
|---|---|---|
| 1 | Preflight | ⬜ |
| 2 | Stack deployment | ⬜ |
| 3 | Post-deployment verify | ⬜ |
| 4 | iSCSI-LUN hot-tier correctness (live) | ⬜ |

Overall: `not yet run` (the live-AWS step is spec task 13)
