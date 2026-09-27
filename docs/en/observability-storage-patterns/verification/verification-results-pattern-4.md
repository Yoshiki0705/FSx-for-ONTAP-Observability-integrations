# Verification Record: Pattern 4 (Kafka + AutoMQ WAL-on-FSx-for-ONTAP)

🌐 [日本語](../../../ja/observability-storage-patterns/verification/verification-results-pattern-4.md) | **English** (this page)

Verification record for the Pattern 4 (Kafka + AutoMQ WAL-on-FSx-for-ONTAP)
verification environment. It follows the format of the repository's existing
`docs/en/verification-results-*.md` records. Use placeholders for real IPs,
account IDs, and resource IDs.

> The live-AWS step (NFS-hosted Kafka silly-rename crash/no-crash behavior) is
> spec task 16; it requires a running FSx for ONTAP account and is billed. It
> does not run in CI. For now its Actual is left blank, its Verdict `⬜`, and its
> Claim_Tier `hypothesis` for the silly-rename target and `unverified` for the
> deploy steps. The AutoMQ WAL latency/cost row is `documented` (AWS's own
> published benchmark, cited); it stays `documented` and is never relabeled
> `verified` or `sample-run`.

## Metadata

| Field | Value |
|---|---|
| Pattern | 4 (Kafka + AutoMQ WAL-on-FSx-for-ONTAP) |
| Verification date (measured) | `<YYYY-MM-DDTHH:MM:SS+09:00>` (fill in on the live run) |
| AWS Region | `ap-northeast-1` (default; override with `AWS_REGION`) |
| CloudFormation stack name | `fsxn-pattern-4-kafka-automq` |
| FSx for ONTAP file system | `fs-0123456789abcdef0` (placeholder) |
| SVM | `svm-0123456789abcdef0` (placeholder) |
| Component versions | `<AutoMQ x.y / Kafka x.y / ONTAP x.y / RHEL x.y>` (fill in on the live run) |

## Claim Tier Legend

Every claim in this record carries exactly one Claim_Tier. Do not present an
unmeasured performance or cost value as fact; mark it as not measured. A cited
public benchmark stays `documented` and is kept distinct from this environment's
own measurements.

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
| 1 | `bash shared/scripts/preflight-check.sh --profile pipeline-pattern-4 --vpc-id <vpc>` | All checks pass (no VPC EP conflict) | `<fill in>` | ⬜ | unverified |
| 2 | `bash scripts/deploy.sh --profile pipeline-pattern-4 --mode automq-wal` | Stack `CREATE_COMPLETE` | `<fill in>` | ⬜ | unverified |
| 3 | `bash scripts/verify.sh` | Stack healthy, broker online in SSM, Gen2 high-throughput WAL intent recorded | `<fill in>` | ⬜ | unverified |
| 4 | NFS-hosted Kafka silly-rename: partition reassignment on a broker log dir on FSx for ONTAP NFSv4.1 with `-is-preserve-unlink-enabled=true` (spec task 16, live, billed) | Reassignment completes without the silly-rename crash | `<fill in>` | ⬜ | hypothesis |

## Silly-Rename Measurement Target

Pattern 4's **named measurement target is the NFS-hosted Kafka silly-rename
behavior**: whether a Kafka broker log directory on FSx for ONTAP NFSv4.1 with
`-is-preserve-unlink-enabled=true` completes partition reassignment without the
silly-rename crash. Two facts are kept distinct, and neither is inflated:

- The ONTAP **version prerequisite is met** on FSx for ONTAP (ONTAP 9.18.1+,
  which is past the 9.12.1 that introduced the setting). This is a **direct field
  confirmation with the AWS service team, not a publicly published version
  number** — it cannot be verified from public FSx for ONTAP documentation alone.
- A **specific FSx for ONTAP volume being configured and validated end-to-end is
  not confirmed.** The original validation was NetApp's, on **NetApp Cloud Volumes
  ONTAP** (ONTAP 9.12.1, NFSv4.1, Confluent 7.2.1) — cited below — which is not,
  by itself, evidence for FSx for ONTAP.

A single crash/no-crash sample run promotes this target from `hypothesis` to
`sample-run` (spec task 16, EC2-based, billed). It is not promoted to `verified`
on one run, and it is not a general service limit.

| Target | Initial tier | After sample run |
|---|---|---|
| NFS-hosted Kafka silly-rename (NFSv4.1 + `-is-preserve-unlink-enabled`) on FSx for ONTAP | hypothesis | sample-run |

## Cited Benchmarks

Public vendor/AWS benchmarks are cited with their source and kept distinct from
this environment's own measurements. They stay `documented`; they are not
relabeled `verified` on the strength of a citation. The AutoMQ WAL case is a
**separate** case from the silly-rename target above — AutoMQ's WAL is a
fixed-size circular buffer and may not exercise the partition-rebalance
delete-while-open path silly-rename depends on, so it does not confirm the
silly-rename fix.

| Claim | Source | Claim_Tier |
|---|---|---|
| AutoMQ diskless-Kafka WAL-on-FSx-for-ONTAP Gen2 Multi-AZ: avg end-to-end latency 7.79 ms (P99 18.04 ms), avg write latency 5.98 ms (P99 12.87 ms); 3x m7g.4xlarge, 1,024 GiB / 3,072 IOPS / 736 MBps, us-east-1 | [AWS Storage blog (2026)](https://aws.amazon.com/blogs/storage/achieving-sub-10ms-latency-and-94-cost-savings-with-diskless-kafka-using-automq-and-amazon-fsx-for-netapp-ontap/) | documented |
| Cost comparison: traditional three-replica Multi-AZ Kafka ~$317,000/month vs AutoMQ BYOC with FSx for ONTAP ~$18,345/month for the same P99 write-latency target (AWS's own published figure, not independently reproduced) | [AWS Storage blog (2026)](https://aws.amazon.com/blogs/storage/achieving-sub-10ms-latency-and-94-cost-savings-with-diskless-kafka-using-automq-and-amazon-fsx-for-netapp-ontap/) | documented |
| Silly-rename fix functional validation (NFSv3 crashed; ONTAP 9.12.1 NFSv4.1 with the fix completed reassignment) — performed on **NetApp Cloud Volumes ONTAP**, not FSx for ONTAP | [NetApp solutions doc (2025-09-15)](https://docs.netapp.com/us-en/netapp-solutions/data-analytics/kafka-nfs-functional-validation-silly-rename-fix.html); [Trident issue #808](https://github.com/NetApp/trident/issues/808) | documented |

## Cost Drivers

Every cost value carries its measurement date, region, and configuration. The
figures below are as of 2026-07 (`ap-northeast-1`, minimal sizing), based on
published unit prices and not measured in this environment. The AutoMQ figures
are AWS's own published benchmark (cited above), not measured here.

| Driver | Note | Claim_Tier |
|---|---|---|
| FSx for ONTAP Gen2 high-throughput WAL | Costs MORE than the standard configuration; billed continuously while running. The pattern's largest steady-state cost driver. Reused as a shared foundation; not created or deleted by this environment | documented |
| EC2 Kafka broker(s) | Billed continuously while running (default `m7g.large`; the AWS benchmark used `m7g.4xlarge`) | documented |
| S3 (AutoMQ durable tier) | Storage and request charges for the S3-backed durable tier | documented |
| Interface VPC Endpoint | ~7.20 USD/month per endpoint per AZ (per-AZ ENI charge). This environment uses an S3 Gateway Endpoint, so it is usually not needed | documented |

## Scope Boundary

This record covers a verification environment and its results, not a
production-grade deployment. Multi-region HA and load/scale benchmarks beyond a
single sample run are out of scope unless explicitly pursued. The AutoMQ
latency/cost numbers are AWS's cited benchmark, not a reproduction in this
environment.

## Verdict Summary

| Step | Name | Verdict |
|---|---|---|
| 1 | Preflight | ⬜ |
| 2 | Stack deployment | ⬜ |
| 3 | Post-deployment verify | ⬜ |
| 4 | NFS-hosted Kafka silly-rename (live) | ⬜ |

Overall: `not yet run` (the live-AWS step is spec task 16)
