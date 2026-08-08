# Grafana Cloud Integration Verification Results

🌐 [日本語](../ja/verification-results-grafana.md) | **English** (this page)

> **This is an evidence index, not a dated run log.** The other vendor records on
> this page's siblings (Datadog, Honeycomb, Elastic, …) were written up while a
> verification run was in progress, so they carry the date, the account, the
> stack name and the exact log counts. That was never captured for Grafana. What
> exists is the screenshot evidence below, which is what the
> [telemetry path coverage](README.md#telemetry-path-coverage) matrix relies on
> when it marks all three Grafana paths ✅.
>
> The gap is the write-up, not the verification. This file records what can be
> shown and states plainly what was not recorded, rather than reconstructing
> environment details after the fact.

---

## What the evidence shows

All screenshots live in `integrations/grafana/docs/screenshots/` and were captured
against Grafana Cloud with real data flowing through the pipeline. Each one is
reproducible by following the query listed beside it.

| Path | Screenshot | Query run | What it demonstrates |
|------|-----------|-----------|----------------------|
| Audit logs | `explore-log-arrival.png` | `{service_name="fsxn-ontap"}` | FSx for ONTAP audit events arriving in Grafana Cloud Loki and queryable in Explore, with timestamp and content fields populated |
| Audit logs | `dashboard-overview.png` | (dashboard) | All four dashboard panels rendering with data: log volume, operations breakdown, user activity, failure events |
| Audit logs | `grafana-unauthorized-access.png` | filter on failed access | Failed-access events distinguishable from successful ones |
| EMS events | `grafana-ems-events.png` | `{service_name="fsxn-ems"}` | EMS events arriving with `event_name`, `severity` and `svm` fields extracted |
| FPolicy file ops | `grafana-fpolicy-events.png` | `{service_name="fsxn-fpolicy"}` | FPolicy file operations arriving with `operation`, `file_path` and `user` fields extracted |

Capture instructions for each, including the exact navigation path and the fields
to confirm, are in
[`integrations/grafana/docs/screenshots/README.md`](../../integrations/grafana/docs/screenshots/README.md).

> **Note on `grafana-logs-arrival.png`**: it is byte-identical to
> `explore-log-arrival.png` (same MD5). It is a duplicate under a second name, not
> independent evidence of a second run.

---

## Delivery path that was verified

```
FSx for ONTAP audit volume
  → S3 Access Point
  → EventBridge Scheduler (poll + SSM checkpoint)
  → Lambda
  → Grafana Cloud OTLP Gateway
```

The OTLP Gateway is the verified path — `otlp_http` exporter against
`https://otlp-gateway-prod-<region>.grafana.net/otlp`, authenticated with Basic
auth over `base64(instanceID:token)`. The `loki` exporter remains in the
integration as a legacy fallback and is **not** the verified path. See
[the integration README](../../integrations/grafana/README.md) for why.

---

## What is not recorded

Stating these explicitly so the absence is not mistaken for a pass:

| Item | Status |
|------|--------|
| Verification date and verifier | Not recorded |
| AWS account, stack name, Grafana instance ID | Not recorded |
| Exact log counts per path | Not recorded (the screenshots show arrival, not a count) |
| Whether the EMS screenshot came from a live ONTAP webhook or an injected payload | Not recorded |
| Firehose delivery path | Not applicable — Grafana Cloud has no Firehose destination, so this integration is Lambda-only |

To produce a full dated record for this integration, work through
[`shared/scripts/vendor-verification-checklist.md`](../../shared/scripts/vendor-verification-checklist.md)
and write the results up in the same shape as
[the Honeycomb record](verification-results-honeycomb.md).

---

## Automated coverage

Unit and property tests do not verify delivery to Grafana, but they do cover the
payload construction that delivery depends on:

```bash
python -m pytest integrations/grafana/tests/ -v
```

102 tests, covering OTLP payload shaping, the Loki fallback formatter, auth header
construction, batch splitting and checkpoint handling.

---

## Related Documents

- [Grafana Integration README](../../integrations/grafana/README.md)
- [Grafana Setup Guide](../../integrations/grafana/docs/en/setup-guide.md)
- [OTel Collector Verification Results](verification-results-otel-collector.md) — Grafana Cloud also appears there as an OTLP backend
- [EMS/FPolicy E2E Verification Results](verification-results-ems-fpolicy.md) — the shared EMS and FPolicy infrastructure
- [Vendor Verification Checklist](../../shared/scripts/vendor-verification-checklist.md)
