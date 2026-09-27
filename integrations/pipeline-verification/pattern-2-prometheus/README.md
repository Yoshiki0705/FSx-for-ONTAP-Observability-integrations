# Pattern 2: Prometheus + remote_write (priority stage 1)

Net-new: Prometheus server IaC (EC2 / ECS-on-EC2, local TSDB).
Reused: `shared/templates/s3-access-point.yaml` (remote_write archive target),
`integrations/grafana/` (visualization).

Stage 0 delivers only the scaffolding (reuse declaration, teardown discipline,
verification record template). The `template.yaml`, `scripts/`, and
`docs/{ja,en}/setup-guide.md` are added in the priority-stage-1 tasks (spec
tasks 8.x).

Guardrail: Prometheus local TSDB is not supported on NFS/EFS. FSx for ONTAP is
only the downstream remote_write archive target; do not point
`--storage.tsdb.path` at FSx for ONTAP NFS.
