# Pattern 4: Kafka + AutoMQ WAL-on-FSx-for-ONTAP + OTel Collector (priority stage 3)

Net-new: Kafka cluster IaC (AutoMQ diskless-Kafka, WAL-on-FSx-for-ONTAP Gen2).
Reused: `integrations/otel-collector/` (collector layer, doc pointer),
`ontap-edge-to-cloud-ai:cloud/clickhouse/ddl/` (ClickHouse consumer DDL).

Delivered (spec tasks 14.x): `template.yaml` (net-new Kafka broker fleet +
AutoMQ Gen2 WAL config), `scripts/{deploy,verify,teardown}.sh`, and
`docs/{ja,en}/setup-guide.md`. The Verification_Record is
`docs/{en,ja}/observability-storage-patterns/verification/verification-results-pattern-4.md`.
The live NFS silly-rename sample run (spec task 16) is not part of this delivery.

Guardrail: NFS-hosted Kafka broker log directories depend on silly-rename
behavior; ONTAP 9.12.1+ `-is-preserve-unlink-enabled`, and FSx for ONTAP meets
the prerequisite from ONTAP 9.18.1. AutoMQ is a BYOC-dependent option presented
vendor-neutrally. FSx for ONTAP Gen2 high-throughput settings cost more than the
standard configuration.
