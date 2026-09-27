# Pipeline Verification Environments

🌐 [日本語](docs/ja/README.md) | **English** (this page)

Reproducible, CI-verifiable test environments and verification records for the
five real-time telemetry pipeline patterns documented in
[`docs/{en,ja}/observability-storage-patterns/`](../../docs/en/observability-storage-patterns/README.md).
The goal is to move the Amazon FSx for NetApp ONTAP (hereafter FSx for ONTAP)
claims in those pattern documents from *documented* / *hypothesis* toward
*verified* / *sample-run*.

The overriding constraint is **reuse named existing assets; do not reinvent
them**. Each verification environment builds on IaC templates, the preflight
script, verification harnesses, deployed implementations, and the verification
record format that already exist in this repository and in the sibling
repository `ontap-edge-to-cloud-ai`. Only the layer that no existing asset
covers is net-new (the pattern-2 Prometheus layer, the pattern-4 Kafka cluster,
the pattern-5 QuestDB/TimescaleDB server).

## Two Delivery Layers

| Layer | Live AWS? | Cost | Where |
|---|---|---|---|
| CI-verifiable scaffolding | No | None | This directory, `scripts/tests/test_reuse_references.py`, `shared/reuse-references.yaml` |
| Sample-run validation | Yes (running FSx for ONTAP) | Billed; touches irreversible resources | Per-pattern `verify.sh`, recorded in the verification records |

Stage 0 (this scaffolding) completes and merges without any live AWS account.
The sample-run layer is optional (`*` tasks in the spec) and never runs in CI.

## Staged Implementation Order

Priority is set by net-new value, confirmed in the design as **P2 → P5 → P4 →
P1**, with pattern 3 as reuse-and-record only.

| Order | Pattern | Net-new | Reused assets |
|---|---|---|---|
| 1 | Pattern 2 (Prometheus + remote_write) | Prometheus server IaC | `shared/templates/s3-access-point.yaml`, `integrations/grafana/` |
| 2 | Pattern 5 (QuestDB / TimescaleDB) | Single-node server IaC | `shared/templates/s3-access-point.yaml`, `ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` |
| 3 | Pattern 4 (Kafka + AutoMQ WAL) | Kafka cluster IaC | `integrations/otel-collector/`, `ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` |
| later | Pattern 1 (MQTT → InfluxDB → Grafana Live) | Minimal MQTT + InfluxDB | `integrations/grafana/`, `shared/templates/s3-access-point.yaml`, `ontap-edge-to-cloud-ai:cloud/iot_ingestion/` |
| reuse only | Pattern 3 (managed IoT → Timestream/S3) | None | `integrations/lakehouse-retention/`, `ontap-edge-to-cloud-ai:cloud/iot_ingestion/` |

The rationale (complexity, external-product dependency, cost) lives in the
spec's design document under 設計判断.

## Reuse Map

Every pattern's reused assets and net-new files are declared in
[`shared/reuse-references.yaml`](shared/reuse-references.yaml). That file is the
single input to the Duplication_Check gate and the allowlist for legitimate
net-new work. Prose mentions do not count as a reuse reference; the structured
declaration does.

## Duplication_Check Execution Point

The Duplication_Check gate is `scripts/tests/test_reuse_references.py`, and its
execution point is a **CI gate**, not the preflight script. It runs under
`make test-py` (via `scripts/tests/` in the Makefile's `PYTEST_DIRS`) and under
`make drift`, so both local and CI runs execute it from the same path list.
Preflight stays focused on environment-dependent checks (VPC endpoint conflicts
and reachability); duplication detection is a static check over the artifact
file set and does not depend on a deploy environment, so the two concerns are
separated.

The gate does two things:

1. **Reference resolution** — every `same-repo-path` in `reuse-references.yaml`
   resolves to a real path or stack reference in this repository, else the gate
   fails. `sibling-repo-path` entries are verified only when the sibling
   checkout is present (the gate does not fail when it is absent).
2. **Duplication detection** — each net-new file's normalized CloudFormation
   resource blocks are compared against existing assets; a file that reproduces
   an existing asset above the similarity threshold, without a `rationale`
   declaration, is reported as a suspected degraded copy.

Run its guard-the-guard first, as with the repository's other gates:

```bash
.venv/bin/python scripts/tests/test_reuse_references.py --selftest
```

## Directory Layout

```
integrations/pipeline-verification/
├── README.md                       # this file
├── docs/{ja,en}/README.md          # bilingual entry point
├── pattern-1-mqtt-influxdb/        # net-new minimal (later stage)
├── pattern-2-prometheus/           # priority stage 1
├── pattern-3-managed-iot/          # reuse only (no template)
├── pattern-4-kafka-automq/         # priority stage 3
├── pattern-5-questdb-timescaledb/  # priority stage 2
└── shared/
    ├── reuse-references.yaml        # all reuse references + net-new allowlist
    └── teardown-template.sh         # reverse-dependency teardown discipline
```

## Preflight Profiles

Preflight is the existing `shared/scripts/preflight-check.sh`, extended (not
forked) with `pipeline-pattern-1/2/3/4/5` profiles. List them with:

```bash
bash ../../shared/scripts/preflight-check.sh --list-profiles
```

## Scope Boundary

This feature delivers verification environments and verification records. It is
not production-grade deployment. Multi-region HA for each TSDB, and load/scale
benchmarks beyond a single sample run, are out of scope unless explicitly
pursued.

## Related Documents

- [Storage consolidation patterns](../../docs/en/observability-storage-patterns/README.md)
- [Verification records](../../docs/en/observability-storage-patterns/verification/verification-results-pattern-1.md)
- [Lakehouse retention (pattern 3 reuse target)](../lakehouse-retention/README.md)
