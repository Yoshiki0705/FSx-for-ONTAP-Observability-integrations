# S3 Access Points Throughput Benchmark (tooling)

English only. This is a cross-cutting operator tool directory, not a vendor
integration. Vendor integration dirs under `integrations/<vendor>/` ship
bilingual setup guides under `docs/{ja,en}/`; standalone tool dirs in this repo
(for example `shared/scripts/`) are en-only, so this follows that norm.

The benchmark **methodology and reference numbers** live in the bilingual
document [docs/en/s3ap-throughput-benchmark.md](../../docs/en/s3ap-throughput-benchmark.md)
([日本語](../../docs/ja/s3ap-throughput-benchmark.md)). This directory is the
tool that [issue #98](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues/98)
records as missing: it deploys and runs the measurement. It does not edit that
document — the operator records real numbers there after a real run.

## What it measures

Against an FSx for ONTAP S3 Access Point:

- **list**: `ListObjectsV2` latency over N iterations — p50 / p99 / mean / min / max (ms).
- **get**: `GetObject` latency and derived throughput per object, grouped into
  size classes. Reads each object fully and derives `throughput_mbps` from the
  mean latency and object size.

Percentiles use a safe nearest-rank method (`index = min(n-1, ceil(p*n)-1)`)
that never indexes out of range on small N. The stats math is a set of pure,
boto3-free helpers in `handler.py`, unit-tested in `tests/`.

## Files

| File | Purpose |
|------|---------|
| `handler.py` | Lambda handler + pure stats helpers (single source of truth). |
| `template.yaml` | CloudFormation stack. Handler is inlined from `handler.py`; regenerate with `python3 shared/scripts/sync-inline-lambda.py`. |
| `run-benchmark.sh` | Operator invoke helper (env-var driven). Writes raw JSON. |
| `tests/` | Offline pytest suite (stub S3 client; no AWS calls). |

## Deploy

Placement matches the doc's test environment: 256 MB memory, 300 s timeout, and
**no VPC config** (outside-VPC path to an internet-origin S3 Access Point; a VPC
with a Gateway Endpoint only would time out). The execution role is scoped to
`s3:GetObject` + `s3:ListBucket` on the one parameterized S3 Access Points ARN,
plus its own CloudWatch Logs. No DLQ: the function is invoked synchronously by
an operator, so errors return to the caller and there is no async retry to
capture.

```bash
# Replace the placeholder ARN with your S3 Access Points ARN.
aws cloudformation deploy \
  --template-file benchmark/s3ap-throughput/template.yaml \
  --stack-name fsxn-s3ap-benchmark \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    S3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    BenchmarkRegion=ap-northeast-1
```

`aws cloudformation deploy --parameter-overrides` takes only inline `Key=Value`
pairs (no `file://`). The stack creates a `CAPABILITY_NAMED_IAM` role, hence the
capability flag.

## Run

`run-benchmark.sh` invokes the deployed Lambda for both tests and writes a
combined JSON file. Every environment-specific value comes from an environment
variable — no account IDs, ARNs, or IPs are baked in.

```bash
FUNCTION_NAME=fsxn-s3ap-benchmark-fn \
S3AP=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
PREFIX=audit/svm-prod-01/2026/05/ \
AWS_REGION=ap-northeast-1 \
  benchmark/s3ap-throughput/run-benchmark.sh
```

`S3AP` accepts either the S3 Access Points **alias** or its **ARN**; both work
as the Lambda's `bucket` parameter. Optional overrides: `LIST_ITERATIONS` (20),
`GET_ITERATIONS` (5), `MAX_KEYS` (10), `BENCHMARK_RUN_ID`, `OUT_FILE`.

Record the result together with its environment context (FSx throughput
capacity, AP network origin, region, date). These are a **sizing reference, not
a service limit**.

## Test

```bash
make test-py PY=.venv/bin/python   # runs this suite among all others
.venv/bin/python -m pytest benchmark/s3ap-throughput/tests/ -v
```

The suite is deterministic and offline: boto3 is stubbed, so no AWS credentials
or network access are needed.
