# Verification Record: Pattern 2 (Prometheus + remote_write)

🌐 [日本語](../../../ja/observability-storage-patterns/verification/verification-results-pattern-2.md) | **English** (this page)

Verification record for the Pattern 2 (Prometheus + remote_write) verification
environment. It follows the format of the repository's existing
`docs/en/verification-results-*.md` records. Use placeholders for real IPs,
account IDs, and resource IDs.

> The live-AWS step (remote_write to S3 Access Points reachability) is spec task
> 10; it requires a running FSx for ONTAP account and is billed. It does not run
> in CI. For now its Actual is left blank, its Verdict `⬜`, and its Claim_Tier
> `unverified`.

## Metadata

| Field | Value |
|---|---|
| Pattern | 2 (Prometheus + remote_write) |
| Verification date (measured) | `<YYYY-MM-DDTHH:MM:SS+09:00>` (fill in on the live run) |
| AWS Region | `ap-northeast-1` (default; override with `AWS_REGION`) |
| CloudFormation stack name | `fsxn-pattern-2-prometheus` |
| FSx for ONTAP file system | `fs-0123456789abcdef0` (placeholder) |
| SVM | `svm-0123456789abcdef0` (placeholder) |
| Component versions | `<Prometheus x.y / ONTAP x.y>` (fill in on the live run) |

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
| 1 | `bash shared/scripts/preflight-check.sh --profile pipeline-pattern-2 --vpc-id <vpc>` | All checks pass (no VPC EP conflict) | `<fill in>` | ⬜ | unverified |
| 2 | `bash scripts/deploy.sh --profile pipeline-pattern-2` | Stack `CREATE_COMPLETE` | `<fill in>` | ⬜ | unverified |
| 3 | `bash scripts/verify.sh` | Stack healthy, Prometheus online in SSM, archive target resolved | `<fill in>` | ⬜ | unverified |
| 4 | remote_write to S3 Access Points reachability (spec task 10, live, billed) | Objects reach the archive target | `<fill in>` | ⬜ | unverified |

## Hot Tier Storage Hypothesis

Pattern 2's local TSDB is on local EBS block storage and uses no NFS/EFS. The
iSCSI hot-tier hypothesis therefore does not apply to Pattern 2 (it applies to
patterns 1, 4, and 5). This record notes only that it is out of scope here.

| Target | Initial tier | After sample run |
|---|---|---|
| (not applicable to Pattern 2) | — | — |

## Cited Benchmarks

Public vendor/AWS benchmarks are cited with their source and kept distinct from
this environment's own measurements. They stay `documented`; they are not
relabeled `verified` on the strength of a citation.

| Claim | Source | Claim_Tier |
|---|---|---|
| Prometheus local TSDB is unsupported on NFS/EFS | [prometheus/prometheus#10611](https://github.com/prometheus/prometheus/issues/10611), [SUSE Rancher Monitoring KB](https://www.suse.com/support/kb/doc?id=000021332) | documented |

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, single AZ, minimal sizing),
based on published unit prices and not measured in this environment.

| Driver | Note | Claim_Tier |
|---|---|---|
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). This environment uses an S3 Gateway Endpoint, so it is usually not needed | documented |
| FSx for ONTAP file system | Billed continuously while running. Reused as a shared foundation; not created or deleted by this environment | documented |
| EC2 Prometheus server | Billed continuously while running (default `t3.medium`) | documented |

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
| 4 | remote_write to S3 Access Points reachability (live) | ⬜ |

Overall: `not yet run` (the live-AWS step is spec task 10)
