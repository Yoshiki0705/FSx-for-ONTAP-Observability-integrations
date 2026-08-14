# Vendor API Reference (Quick Lookup)

Endpoint, auth header, batch limit and Firehose support per vendor.

> Extracted from AGENTS.md so it is not loaded into every agent turn.
> AGENTS.md keeps a one-line index entry pointing here, and
> .kiro/steering/ carries a conditional loader that pulls this in when
> the work touches these areas. Tracked in git on purpose: .kiro/ is not
> published, so the body must live here to stay visible on GitHub.

| Vendor | Endpoint | Auth Header | Max Batch | Firehose | Notes |
|--------|----------|-------------|-----------|----------|-------|
| Datadog | `https://http-intake.logs.{site}/api/v2/logs` | `DD-API-KEY: <key>` | 5MB / 1000 items | ✅ | |
| New Relic | `https://log-api.newrelic.com/log/v1` (US) | `Api-Key: <license>` | 1MB | ✅ | |
| Grafana/Loki | `https://otlp-gateway-prod-<region>.grafana.net/otlp` | Basic Auth (base64(ID:token)) | ~4MB recommended | ❌ | ✅ Verified via otlp_http exporter (NOT loki exporter) |
| Splunk | `https://<host>:8088/services/collector/event` | `Authorization: Splunk <token>` | No hard limit | ✅ (built-in) | |
| Elastic | `https://<cluster>/_bulk` | `Authorization: ApiKey <key>` | ~10MB recommended | ❌ | |
| Dynatrace | `https://<env>.live.dynatrace.com/api/v2/logs/ingest` | `Authorization: Api-Token <token>` | 1MB | ✅ | |
| Sumo Logic | `https://endpoint<N>.collection.sumologic.com/...` | Embedded in URL | 1MB | ❌ | |
| Honeycomb | `https://api.honeycomb.io` | `x-honeycomb-team: <hcaik_key>` | 5MB | ❌ | ✅ Verified via otlp_http exporter + x-honeycomb-dataset header |
| OTel (OTLP) | `http://<collector>:4318/v1/logs` | Configurable | Configurable | ❌ | ✅ Verified: Datadog + Grafana + Honeycomb multi-backend (0.152.0) |
| Mackerel | `https://otlp-vaxila.mackerelio.com` (OTLP/HTTP only) | `Mackerel-Api-Key: <key>` header | ~3.5MB (`sending_queue.batch.max_size` bytes) | ❌ | 📋 Planning only — Mackerel's own log feature is open beta (2026-07-16); no Lambda/`template.yaml` in this repo yet. Same endpoint/auth as Mackerel's tracing (APM) feature. See `integrations/mackerel/README.md`. |

Sources: [Datadog Logs API](https://docs.datadoghq.com/api/latest/logs/) | [New Relic Log API](https://docs.newrelic.com/docs/enable-new-relic-logs-http-input/) | [Grafana Loki HTTP API](https://grafana.com/docs/loki/latest/reference/loki-http-api/) | [Splunk HEC](https://docs.splunk.com/Documentation/Splunk/9.4.0/Data/FormateventsforHTTPEventCollector) | [OpenTelemetry Lambda](https://github.com/open-telemetry/opentelemetry-lambda)

