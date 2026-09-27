# Verification Record Template (Pipeline Patterns)

🌐 [日本語](../../../ja/observability-storage-patterns/verification/verification-record-template.md) | **English** (this page)

Copy this template to `verification-results-pattern-<N>.md` and fill it in for
one pattern. It follows the format established by the repository's existing
`docs/en/verification-results-*.md` records. Use placeholders for real IPs,
account IDs, and resource IDs.

> This is a template, not a completed record. The example rows show the shape;
> replace them with the actual run.

## Metadata

| Field | Value |
|---|---|
| Pattern | `<1-5>` (e.g. Prometheus + remote_write) |
| Verification date (measured) | `<YYYY-MM-DDTHH:MM:SS+09:00>` |
| AWS Region | `<region, e.g. ap-northeast-1>` |
| CloudFormation stack name | `<stack-name>` |
| FSx for ONTAP file system | `fs-0123456789abcdef0` (placeholder) |
| SVM | `svm-0123456789abcdef0` (placeholder) |
| Component versions | `<Prometheus x.y / QuestDB x.y / Kafka x.y / ONTAP x.y>` |

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
| 1 | `bash scripts/deploy.sh` | Stack `CREATE_COMPLETE` | `<fill in>` | ⬜ | unverified |
| 2 | `bash shared/scripts/preflight-check.sh --profile pipeline-pattern-<N>` | All checks pass | `<fill in>` | ⬜ | unverified |
| 3 | `<pattern-specific verify command>` | `<expected>` | `<fill in>` | ⬜ | unverified |

## Hot Tier Storage Hypothesis

Applies to patterns 1, 4, and 5. Record the named measurement target and its
tier transition here.

| Target | Initial tier | After sample run |
|---|---|---|
| iSCSI LUN read/write/lock correctness for the hot TSDB (InfluxDB / QuestDB / ClickHouse) | hypothesis | sample-run |
| NFS-hosted Kafka silly-rename behavior (NFSv4.1 + `-is-preserve-unlink-enabled`, ONTAP 9.12.1+) | hypothesis | sample-run |

The sample run is a single read → write → lock correctness check. Load and
scale measurement is out of scope unless explicitly pursued.

## Cited Benchmarks

Public vendor/AWS benchmarks are cited with their source and kept distinct from
this environment's own measurements. They stay `documented`; they are not
relabeled `verified` on the strength of a citation.

| Claim | Source | Claim_Tier |
|---|---|---|
| `<e.g. AutoMQ WAL-on-FSx-for-ONTAP latency>` | `<AWS Storage blog URL>` | documented |

## Cost Drivers

Every cost value carries its measurement date, region, and configuration.

| Driver | Note |
|---|---|
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge) |
| FSx for ONTAP file system | Billed continuously while running |
| FSx for ONTAP Gen2 high-throughput (pattern 4 / AutoMQ) | Higher than the standard configuration |

## Scope Boundary

This record covers a verification environment and its results, not a
production-grade deployment. Multi-region HA and load/scale benchmarks beyond a
single sample run are out of scope unless explicitly pursued.

## Verdict Summary

| Step | Name | Verdict |
|---|---|---|
| 1 | Stack deployment | ⬜ |
| 2 | Preflight | ⬜ |
| 3 | Pattern-specific verify | ⬜ |

Overall: `<PASS / PARTIAL / not yet run>`
