# Pattern 5: QuestDB / TimescaleDB (priority stage 2)

Net-new: single-node server IaC (EC2, local-disk hot tier).
Reused: `shared/templates/s3-access-point.yaml` (cold tier),
`ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` (ClickHouse DDL, doc pointer).

Stage 0 delivers only the scaffolding. The `template.yaml`, `scripts/`, and
`docs/{ja,en}/setup-guide.md` are added in the priority-stage-2 tasks (spec
tasks 11.x).

Guardrail: the hot tier is not supported on NFS/EFS and assumes local disk. An
iSCSI LUN configuration is tracked as a Hot_Tier_Storage_Hypothesis, promoted
from hypothesis to sample-run by a single read/write/lock correctness run.
