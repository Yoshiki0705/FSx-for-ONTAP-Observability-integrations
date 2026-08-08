# Mackerel Log Feature Setup Guide (Open Beta)

🌐 [日本語](../ja/setup-guide.md)

> **Scope of this guide**: This guide covers preparing Mackerel credentials and configuring an OpenTelemetry Collector to receive and forward logs to Mackerel (Steps 1–4), plus a direct-send alternative that bypasses the Collector entirely (Step 5). The underlying Lambda/CloudFormation code for both paths now exists in this repo's [OTel Collector integration](../../../otel-collector/) — see [../../README.md#implementation-status](../../README.md#implementation-status) for exactly what has been verified. **Both the Collector-mediated path and the direct-send path (Step 5) have been confirmed end-to-end against a real Mackerel organization** (2026-07-18). Note that the direct-send path requires `OTLP_CONTENT_TYPE=protobuf` — Mackerel's OTLP endpoint rejects JSON — see Step 5.1 below.

## Overview

Mackerel's log feature (opened as public beta on 2026-07-16) accepts logs **only** via OpenTelemetry Protocol (OTLP). There is no proprietary REST API for direct log ingestion. This guide walks through:

1. Obtaining a Mackerel API key with Write scope
2. Preparing an OpenTelemetry Collector configuration for the `logs` pipeline
3. Sending a sample OTLP log payload and confirming arrival in the Mackerel log screen
4. Understanding the beta constraints before committing to production use

## Prerequisites

- A Mackerel account (Free plan, Standard plan, or a trial of either)
- Docker (for local Collector verification) — not required if you already run a Collector elsewhere
- `curl` or an OTLP-capable test client

## Step 1: Prepare Mackerel Credentials

### 1.1 Obtaining a Write-scoped API Key

1. Log in to [Mackerel](https://mackerel.io/signin)
2. Open **API キー** (API Keys) from your organization settings
3. Create or reuse a key with **Write** permission
4. Copy the key value — it is used as the `Mackerel-Api-Key` header value

> **Important**: Read-only API keys cannot be used for log ingestion. Log ingestion requires the same Write scope used by Mackerel's tracing (APM) feature.

### 1.2 Storing the API Key in AWS Secrets Manager

Even at this documentation-only stage, store the key using the same pattern as other vendors in this repo, so future Lambda implementation can reuse it without a credentials redesign:

```bash
aws secretsmanager create-secret \
  --name "mackerel/fsxn-log-credentials" \
  --description "Mackerel API key (Write scope) for FSx for ONTAP log integration (beta)" \
  --secret-string '{"api_key":"YOUR_MACKEREL_API_KEY"}' \
  --region ap-northeast-1
```

> **Secret name**: `mackerel/fsxn-log-credentials`
>
> **JSON format**: `{"api_key":"<key>"}`

### 1.3 Confirming the OTLP Endpoint

Mackerel's log feature uses the **same OTLP endpoint as its tracing (APM) feature**:

```
https://otlp-vaxila.mackerelio.com
```

Authentication is a single header, also shared with tracing:

```
Mackerel-Api-Key: <your-write-scoped-api-key>
```

> **Note**: Unlike some vendors, Mackerel does not use Basic Auth or bearer tokens for OTLP ingestion — it is a single custom header. Also note the `Accept: */*` header is required per Mackerel's own documentation (their backend uses AWS Lambda internally and depends on this header being present).

## Step 2: Prepare an OpenTelemetry Collector Configuration

Two Collector options are documented by Mackerel:

- **OpenTelemetry Collector Contrib** — general-purpose, can fan out to multiple backends simultaneously (consistent with this repo's existing [OTel Collector integration](../../../otel-collector/))
- **Mackerel Distro of OpenTelemetry (MDOT) Collector** — Mackerel's own distribution with the Mackerel exporter pre-wired

This guide uses **OpenTelemetry Collector Contrib**, to stay consistent with the multi-backend pattern already used in this repository.

### 2.1 config.yaml

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"
      http:
        endpoint: "0.0.0.0:4318"

processors:
  batch:
    # This is a general-purpose batch processor setting (not something
    # Mackerel's own docs specifically recommend — their own config examples
    # skip this processor and rely solely on the exporter's sending_queue
    # below). This repo's actual verified config
    # (otel-collector-config-mackerel.yaml) uses a batch processor with
    # timeout: 5s / send_batch_size: 1000; this sample mirrors that.
    timeout: 5s
    send_batch_size: 1000

exporters:
  otlphttp/mackerel:
    endpoint: "https://otlp-vaxila.mackerelio.com"
    headers:
      Accept: "*/*"
      Mackerel-Api-Key: "${env:MACKEREL_APIKEY}"
    sending_queue:
      batch:
        max_size: 3500000
        sizer: bytes

extensions:
  health_check:

service:
  extensions: [health_check]
  pipelines:
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/mackerel]
```

> **Multi-backend note**: if you want to fan out FSx for ONTAP logs to Mackerel *and* another backend simultaneously (e.g., Datadog via this repo's existing OTel Collector integration), simply add a second exporter (e.g., `otlphttp/datadog`) to the same `logs` pipeline's `exporters` list. No Lambda change is required — this is the same pattern already documented for [Datadog + Grafana + Honeycomb multi-backend](../../../otel-collector/) in this repo.

### 2.2 Starting the Collector Locally (Docker)

```bash
# Preferred: keep the key out of shell history / process listings
echo "MACKEREL_APIKEY=YOUR_MACKEREL_API_KEY" > .env.mackerel   # add .env.mackerel to .gitignore
docker run --rm \
  -p 4317:4317 -p 4318:4318 \
  --env-file .env.mackerel \
  -v "$(pwd)/config.yaml:/etc/otelcol-contrib/config.yaml" \
  otel/opentelemetry-collector-contrib:latest
```

> **Important**: Never commit `config.yaml` with a real API key inline, and avoid passing secrets with `-e KEY=value` directly on the command line — it can persist in shell history and is visible to other users via `ps` on shared hosts. Use `--env-file` with a git-ignored file as shown above, or a secrets-management integration if running on ECS Fargate.

> **Cost note**: Mackerel's log ingestion itself is free during the open beta, but running the Collector (Docker locally, or ECS Fargate in a persistent deployment) has its own AWS infrastructure cost, independent of Mackerel. See the [AWS Infrastructure Cost Estimate](../../../../docs/en/vendor-comparison.md#aws-infrastructure-cost-estimate) table for typical Lambda/EventBridge/Secrets Manager costs; ECS Fargate Collector costs are not yet estimated for this integration. Mackerel has already published its post-GA pricing for the log feature (ingest-volume billing, ¥70/GB excl. tax, planned for fall 2026 GA) — see the official announcement linked in the README's References section — but until GA actually lands, treat any production cost projection based on it as provisional.

## Step 3: Sending a Sample OTLP Log Payload

Before the FSx for ONTAP audit-log Lambda shipper exists, you can validate the Collector → Mackerel path standalone with a sample OTLP log payload.

### 3.1 Sample Payload (`sample-otlp-logs.json`)

```json
{
  "resourceLogs": [
    {
      "resource": {
        "attributes": [
          { "key": "service.name", "value": { "stringValue": "fsxn-audit-poc" } },
          { "key": "service.namespace", "value": { "stringValue": "fsxn" } }
        ]
      },
      "scopeLogs": [
        {
          "logRecords": [
            {
              "timeUnixNano": "1737000000000000000",
              "severityNumber": 9,
              "severityText": "INFO",
              "body": { "stringValue": "sample audit log record for Mackerel beta verification" },
              "attributes": [
                { "key": "operation", "value": { "stringValue": "create" } },
                { "key": "svm", "value": { "stringValue": "svm-prod-01" } }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

> **Why `severityNumber` matters here**: both `severityNumber` and `severityText` are optional per the OTLP Logs Data Model, but Mackerel's own UI groups log severity by the numeric `severityNumber` (1–24, mapped to TRACE through FATAL — 9–12 is INFO). If `severityNumber` is omitted, Mackerel's official docs state the record shows as `UNSPECIFIED` in search results even if `severityText` is present. This sample sets both to avoid that.

### 3.2 Sending the Payload

```bash
curl -s -X POST "http://localhost:4318/v1/logs" \
  -H "Content-Type: application/json" \
  -d @sample-otlp-logs.json
```

A successful send returns an empty `{}` body with HTTP 200 from the local Collector's receiver (this only confirms the Collector accepted it — see Step 4 to confirm delivery to Mackerel).

### 3.3 Troubleshooting

> **Isolate Collector-side vs. Mackerel-side first**: before assuming Mackerel is rejecting the payload, confirm the Collector itself received and queued it. Check the `health_check` extension (`curl http://localhost:13133/`, if exposed) and the Collector's own stdout logs for receiver-side errors. Only after confirming the Collector accepted the payload should you look at exporter-side (Mackerel) error codes below — this avoids misdiagnosing a local Collector misconfiguration as a Mackerel-side rejection.

| Symptom | Likely cause | Resolution |
|---------|-------------|------------|
| `curl` connection refused on `:4318` | Collector not running, or wrong port mapping | Verify `docker ps` shows the container, and that `-p 4318:4318` was passed |
| Collector logs show `401` or `403` from the exporter | Wrong or missing `Mackerel-Api-Key`, or the key lacks Write scope | Re-check the key in Mackerel's API Keys settings; regenerate if needed |
| Collector logs show `400` from the exporter | Malformed OTLP payload, or missing `service.namespace`/`service.name` (Mackerel groups logs by this pair) | Validate the payload against the OTLP Logs Data Model; ensure both `service.namespace` and `service.name` resource attributes are present |
| Collector logs show request size errors | Batch exceeds Mackerel's request size limit | Confirm `sending_queue.batch.max_size: 3500000` (bytes) is set as shown above |
| Collector logs show a DNS failure, e.g. `dial tcp: lookup otlp-vaxila.mackerelio.com on 127.0.0.11:53: server misbehaving`, even though the host machine resolves the hostname fine | Docker Desktop's embedded DNS resolver (`127.0.0.11`) intermittently fails to resolve external hostnames from inside containers — this is not specific to Mackerel and not a config problem on your end | Add an explicit `dns:` block (`8.8.8.8`, `1.1.1.1`) to the `otel-collector` service in `docker-compose-mackerel.yaml` (already enabled by default) — see [OTel Collector README → Troubleshooting → "Docker Desktop DNS Resolution"](../../../otel-collector/README.md#docker-desktop-dns-resolution-server-misbehaving) |

## Step 4: Verifying Logs in Mackerel

1. Log in to Mackerel and open the **ログ** (Logs) item in the sidebar
2. Click **ログの検索を開始** (Start log search)
3. Select the service identified by the `service.namespace` / `service.name` pair you sent (e.g., `fsxn` / `fsxn-audit-poc`)
4. Confirm the sample log record appears with its `operation` and `svm` attributes visible as structured fields

The following screenshots show actual E2E verification results (personal information such as email addresses has been masked).

![Mackerel log search results](../../../../docs/screenshots/mackerel/mackerel-logs-search-results.png)
*Log search results: FSx for ONTAP audit logs delivered to the fsxn-audit service. IP addresses shown (203.0.113.x) are from RFC 5737 TEST-NET-3 documentation address blocks, not real environments.*

![Mackerel log detail](../../../../docs/screenshots/mackerel/mackerel-logs-detail.png)
*Log detail: operation type, result, SVM name, file path, user information, client IP, and timestamp attributes are retained in searchable form.*

> **Hint**: If nothing appears after a few minutes, re-check Step 3.3's troubleshooting table first — most failures happen at the Collector → Mackerel hop, not within Mackerel itself.

> **Verification gotcha — stale sample timestamps**: the sample payload in Step 3.1 has a fixed `timeUnixNano` value. If you saved that payload and re-send it later (e.g. the next day), the timestamp may fall outside Mackerel's default log search window, and the record will look like it never arrived even though delivery succeeded. Confirm delivery independently of the search UI by checking the Collector's own export metrics (`curl http://localhost:8888/metrics | grep otelcol_exporter_sent_log_records` — see `otel-collector-config-mackerel.yaml`'s telemetry block for enabling this endpoint), or widen the search time range in Mackerel's UI to cover the payload's actual timestamp. When testing with `scripts/generate-otlp-payload.sh` instead of the static sample, this isn't an issue — it always generates a current timestamp.

## Step 5: Direct-Send Alternative (Skip the Collector)

Steps 1–4 above route logs through a local OpenTelemetry Collector. If you'd rather send directly from this repo's FSx for ONTAP audit-log/EMS/FPolicy Lambda functions straight to Mackerel's OTLP endpoint — skipping a Collector entirely — the [OTel Collector integration](../../../otel-collector/)'s Lambda handlers support this via a generic custom-header auth mode.

Mackerel's `Mackerel-Api-Key` header couldn't be expressed with the existing `bearer`/`basic` auth modes (which only produce `Authorization: Bearer <token>` or `Authorization: Basic <base64>`), so a generic `AUTH_MODE=header` option was added — it is **not** Mackerel-specific; any vendor needing a custom header name can use it the same way.

> **Important — Protobuf required**: Mackerel's OTLP endpoint only accepts Protobuf-encoded request bodies and rejects OTLP/JSON with an HTTP 400 (`{"code":400,"message":"json is not supported yet"}`). The direct-send Lambda path defaults to sending OTLP/JSON, so `OTLP_CONTENT_TYPE=protobuf` (`OtlpContentType=protobuf` in CloudFormation) is **required** for Mackerel — without it, the send will fail even with correct auth. This has no effect on the Collector-mediated path (Steps 1–4), since the OTel Collector already sends Protobuf by default.

### 5.1 Required Environment Variables / CloudFormation Parameters

| Lambda env var | `template.yaml` parameter | Value for Mackerel |
|----------------|---------------------------|---------------------|
| `OTLP_ENDPOINT` | `OtlpEndpoint` | `https://otlp-vaxila.mackerelio.com` |
| `AUTH_MODE` | `AuthMode` | `header` |
| `AUTH_HEADER_NAME` | `AuthHeaderName` | `Mackerel-Api-Key` |
| `EXTRA_HEADERS_JSON` | `ExtraHeadersJson` | `{"Accept":"*/*"}` |
| `OTLP_CONTENT_TYPE` | `OtlpContentType` | `protobuf` (**required** — see note above) |
| `API_KEY_SECRET_ARN` | `ApiKeySecretArn` | ARN of the secret from [Step 1.2](#12-storing-the-api-key-in-aws-secrets-manager) above |

### 5.2 Example CloudFormation Deploy

```bash
aws cloudformation deploy \
  --template-file integrations/otel-collector/template.yaml \
  --stack-name fsxn-otel-integration \
  --parameter-overrides \
    S3BucketName=your-fsxn-audit-log-bucket \
    OtlpEndpoint=https://otlp-vaxila.mackerelio.com \
    AuthMode=header \
    AuthHeaderName=Mackerel-Api-Key \
    ExtraHeadersJson='{"Accept":"*/*"}' \
    OtlpContentType=protobuf \
    ApiKeySecretArn=arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:mackerel/fsxn-log-credentials-AbCdEf \
    LambdaCodeS3Bucket=my-lambda-code-bucket \
    LambdaCodeS3Key=otel-collector/lambda.zip \
  --capabilities CAPABILITY_NAMED_IAM
```

> **Note**: This deploys all three Lambda functions (audit log, EMS webhook, FPolicy) from the shared `otel-collector/template.yaml` — there is no separate `integrations/mackerel/template.yaml`. See the [OTel Collector README](../../../otel-collector/README.md#alternative-mackerel-backend-open-beta) for the full parameter reference.

### 5.3 Collector-Mediated vs. Direct-Send: Which to Use

| | Collector-mediated (Steps 1–4) | Direct-send (this step) |
|---|---|---|
| Lambda code changes | None | None (uses the existing generic `header` auth mode) |
| Extra infrastructure | Collector (Docker/ECS Fargate) | None |
| Multi-backend fan-out | Yes (add exporters to one Collector config) | No (one endpoint per Lambda deployment) |
| Buffering/retry before the vendor | Collector-level (in addition to Lambda's own retry) | Lambda's own retry only |
| Recommended for | Production, multi-vendor delivery | Quick validation, single-backend, cost-sensitive setups |

This repo's own [OTel Collector README](../../../otel-collector/README.md) generally recommends the Collector-mediated path for production. For a beta feature like Mackerel's log feature, the direct-send path may be preferable for initial validation since it avoids standing up Collector infrastructure just to test a feature that itself has no data-retention guarantee yet.

## Beta Constraints to Communicate Before Production Use

Per Mackerel's official beta announcement (2026-07-16):

- No guarantee of data retention during the beta period
- Unscheduled maintenance may occur
- Planned retention window (both beta and GA) is 30 days
- GA is planned for fall 2026, with ingest-volume pricing (¥70/GB, excl. tax) already published — but the exact GA date is not yet fixed

If this Collector configuration is later wired to the FSx for ONTAP audit-log pipeline for production alerting (e.g., ransomware detection), document this beta status explicitly to anyone relying on the alerts, and treat this integration as **defense-in-depth alongside**, not a replacement for, one of the other 9 GA-verified vendor integrations in this repo.

## Next Steps

- Track implementation progress in [../../README.md#implementation-status](../../README.md#implementation-status)
- Both delivery paths (`bash integrations/otel-collector/scripts/test-local-mackerel.sh` for the Collector-mediated path; a CloudFormation deploy per Step 5.2 above for the direct-send path, with `OtlpContentType=protobuf`) have already been confirmed end-to-end against a real Mackerel organization (2026-07-18)
- Once Mackerel's log feature reaches GA with a stated data-retention SLA, move it from "Emerging / Beta Vendors Under Evaluation" into `docs/en/vendor-comparison.md` / `docs/ja/vendor-comparison.md`'s main "Supported Vendors" table
