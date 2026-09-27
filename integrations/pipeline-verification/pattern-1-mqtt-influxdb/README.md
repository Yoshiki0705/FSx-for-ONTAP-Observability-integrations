# Pattern 1: MQTT → InfluxDB → Grafana Live (later stage)

Net-new: minimal MQTT broker + InfluxDB v1/v2 verification configuration.
Reused: `integrations/grafana/` (visualization, mandatory reference),
`shared/templates/s3-access-point.yaml` (archive),
`ontap-edge-to-cloud-ai:cloud/iot_ingestion/` (ingestion example).

Stage 0 delivers only the scaffolding. The `template.yaml`, `scripts/`, and
`docs/{ja,en}/setup-guide.md` are added in the later-stage tasks (spec tasks
17.x). Placed after the priority stages because the Grafana layer is already
partially covered by `integrations/grafana/`.

Guardrail: InfluxDB v1/v2 (TSM) lock failures on NFS are documented. The iSCSI
LUN configuration is a Hot_Tier_Storage_Hypothesis, verified by sample-run using
the method established in pattern 5.
