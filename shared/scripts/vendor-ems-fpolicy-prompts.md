# EMS/FPolicy Integration Prompts for Vendor E2E Specs

This document contains ready-to-use prompts for updating each vendor's E2E verification spec to include EMS/FPolicy event scenarios. Each prompt is self-contained and can be directly pasted into a new Kiro session targeting the respective vendor spec.

## Architecture Summary (Verified Working)

### FPolicy Path (VERIFIED)
```
ONTAP FPolicy → TCP:9898 → ECS Fargate (FPolicy Server) → SQS (FPolicy_Q) → Bridge Lambda → EventBridge (fpolicy.fsxn) → Vendor Lambda
```

### EMS Webhook Path
```
ONTAP EMS → HTTPS → API Gateway (REST, REGIONAL) → Lambda → Vendor API
```

### Key Facts
- FPolicy server runs on ECS Fargate (ARM64, 256 CPU, 512 MB)
- Container image required: ECR-hosted FPolicy server (e.g., `v2-timeout-fix` tag)
- ONTAP connects directly to Fargate task IP on TCP port 9898 (NOT via NLB, NOT HTTPS)
- NLB is for health checks only
- EventBridge custom bus source: `fpolicy.fsxn`
- Template: `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern)
- **IMPORTANT**: Both `ems-webhook-apigw.yaml` and `fpolicy-apigw.yaml` create named IAM roles, so deployment requires `--capabilities CAPABILITY_NAMED_IAM` (not just `CAPABILITY_IAM`)

## Table of Contents

1. [Datadog](#1-datadog-e2e-verification)
2. [Dynatrace](#2-dynatrace-e2e-verification)
3. [Elastic](#3-elastic-e2e-verification)
4. [Grafana](#4-grafana-e2e-verification)
5. [Honeycomb](#5-honeycomb-e2e-verification)
6. [New Relic](#6-new-relic-e2e-verification)
7. [OTel Collector](#7-otel-collector-e2e-verification)
8. [Splunk Serverless](#8-splunk-serverless-e2e-verification)
9. [Sumo Logic](#9-sumo-logic-e2e-verification)

---

## 1. Datadog E2E Verification

```
Update the datadog-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)
- Fargate task: 256 CPU, 512 MB memory
- IP Auto-Updater Lambda handles Fargate task IP changes on restart

## ONTAP CLI Commands (Verified)

```bash
# Create FPolicy External Engine (port 9898, async, no TLS)
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous

# Create FPolicy Event
vserver fpolicy policy event create -vserver FPolicySMB \
  -event-name file-ops-event \
  -protocol cifs \
  -file-operations create,write,rename,delete

# Create and enable FPolicy Policy
vserver fpolicy policy create -vserver FPolicySMB \
  -policy-name file-screening \
  -events file-ops-event \
  -engine fpolicy_lambda_engine \
  -is-mandatory false

vserver fpolicy enable -vserver FPolicySMB \
  -policy-name file-screening \
  -sequence-number 1
```

## Vendor-Specific Details (Datadog)

- **Endpoint**: `https://http-intake.logs.{site}/api/v2/logs`
- **Auth Header**: `DD-API-KEY: <key>`
- **Max Batch Size**: 5MB / 1000 items per request
- **Firehose Support**: Yes

## Requirements to Add

### Scenario A: EMS Webhook → Datadog Log Forwarding

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events into Datadog Logs API format (JSON array)
   - Forwards to `https://http-intake.logs.{site}/api/v2/logs` with `DD-API-KEY` header
   - Respects 5MB / 1000 items batch limit
   - Includes `source:fsxn-ems`, `service:fsxn-ontap` tags

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: Event arrives in Datadog Logs within 120 seconds
   - Verify: Log entry contains `event_name`, `severity`, `volume_name`, `state`
   - Verify: Datadog log has correct `source` and `service` tags

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: Event arrives in Datadog Logs within 180 seconds
   - Verify: Log entry contains `volume_name`, `quota_target`, `used_bytes`, `limit_bytes`

### Scenario B: FPolicy → Datadog Log Forwarding

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats into Datadog Logs API format
   - Forwards to Datadog with `source:fsxn-fpolicy` tag
   - Includes `operation`, `file_path`, `user`, `client_ip` as structured attributes

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: Event arrives in Datadog Logs within 30 seconds
   - Verify: Log contains operation type, file path, user, client IP

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results:
- Each step: step number, step name, command (code block), expected result, actual result, judgment (PASS/FAIL)
- Include sections: EMS→Datadog path verification, FPolicy→Datadog path verification
- Record Datadog API response status codes and request IDs
```

---

## 2. Dynatrace E2E Verification

```
Update the dynatrace-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)
- Fargate task: 256 CPU, 512 MB memory

## ONTAP CLI Commands (Verified)

```bash
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

## Vendor-Specific Details (Dynatrace)

- **Endpoint**: `https://<env>.live.dynatrace.com/api/v2/logs/ingest`
- **Auth Header**: `Authorization: Api-Token <token>`
- **Max Batch Size**: 1MB per request
- **Firehose Support**: Yes

## Requirements to Add

### Scenario A: EMS Webhook → Dynatrace Log Ingestion

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events into Dynatrace Log Ingest API format
   - Forwards to `https://<env>.live.dynatrace.com/api/v2/logs/ingest` with `Authorization: Api-Token <token>` header
   - Respects 1MB batch limit (split large batches)
   - Sets `log.source` to `fsxn-ems` and includes `dt.entity.host` if available

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: Event arrives in Dynatrace Log Viewer within 120 seconds
   - Verify: Dynatrace API returns HTTP 204 (success)

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: Event arrives in Dynatrace within 180 seconds

### Scenario B: FPolicy → Dynatrace Log Ingestion

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats into Dynatrace Log Ingest API format
   - Forwards with `log.source` set to `fsxn-fpolicy`
   - Includes `operation`, `file_path`, `user`, `client_ip` as custom attributes

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: Event arrives in Dynatrace within 30 seconds

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results.
```

---

## 3. Elastic E2E Verification

```
Update the elastic-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)

## ONTAP CLI Commands (Verified)

```bash
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

## Vendor-Specific Details (Elastic)

- **Endpoint**: `https://<cluster>/_bulk`
- **Auth Header**: `Authorization: ApiKey <key>`
- **Max Batch Size**: ~10MB recommended per request
- **Firehose Support**: No

## Requirements to Add

### Scenario A: EMS Webhook → Elasticsearch Bulk Ingestion

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events into Elasticsearch Bulk API NDJSON format
   - Forwards to `https://<cluster>/_bulk` with `Authorization: ApiKey <key>` header
   - Uses index pattern `fsxn-ems-YYYY.MM.DD` (date-based indices)

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: Document appears in Elasticsearch index within 120 seconds
   - Verify: Bulk API response shows no errors (`"errors": false`)

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: Document appears in Elasticsearch within 180 seconds

### Scenario B: FPolicy → Elasticsearch Bulk Ingestion

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats into Elasticsearch Bulk API NDJSON format
   - Forwards with index pattern `fsxn-fpolicy-YYYY.MM.DD`
   - Maps fields: `operation`, `file_path`, `user`, `client_ip`, `@timestamp`

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: Document appears in Elasticsearch within 30 seconds

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results.
```

---

## 4. Grafana E2E Verification

```
Update the grafana-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)

## ONTAP CLI Commands (Verified)

```bash
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

## Vendor-Specific Details (Grafana/Loki)

- **Endpoint**: `https://<instance>.grafana.net/loki/api/v1/push`
- **Auth**: Basic Auth (Grafana Cloud user ID + API token)
- **Max Batch Size**: ~4MB recommended per request
- **Firehose Support**: No

## Requirements to Add

### Scenario A: EMS Webhook → Grafana Loki Push

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events into Loki Push API format
   - Forwards to `https://<instance>.grafana.net/loki/api/v1/push` with Basic Auth
   - Uses labels: `{job="fsxn-ems", source="ontap", severity="<severity>"}`

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: Log entry appears in Grafana Loki within 120 seconds
   - Verify: Loki Push API returns HTTP 204

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: Log entry appears in Loki within 180 seconds

### Scenario B: FPolicy → Grafana Loki Push

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats into Loki Push API format
   - Uses labels: `{job="fsxn-fpolicy", source="ontap", operation="<op>"}`

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: Log entry appears in Loki within 30 seconds

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results.
```

---

## 5. Honeycomb E2E Verification

```
Update the honeycomb-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)

## ONTAP CLI Commands (Verified)

```bash
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

## Vendor-Specific Details (Honeycomb)

- **Endpoint**: `https://api.honeycomb.io/1/batch/<dataset>`
- **Auth Header**: `X-Honeycomb-Team: <key>`
- **Max Batch Size**: 5MB per request
- **Firehose Support**: No

## Requirements to Add

### Scenario A: EMS Webhook → Honeycomb Batch Events

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events into Honeycomb Batch API format
   - Forwards to `https://api.honeycomb.io/1/batch/fsxn-ems` with `X-Honeycomb-Team` header
   - Respects 5MB batch limit

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: Event appears in Honeycomb dataset `fsxn-ems` within 120 seconds
   - Verify: Honeycomb Batch API returns HTTP 200

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: Event appears in Honeycomb within 180 seconds

### Scenario B: FPolicy → Honeycomb Batch Events

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats into Honeycomb Batch API format
   - Forwards to dataset `fsxn-fpolicy`
   - Maps fields: `operation`, `file_path`, `user`, `client_ip`, `timestamp`

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: Event appears in Honeycomb dataset `fsxn-fpolicy` within 30 seconds

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results.
```

---

## 6. New Relic E2E Verification

```
Update the new-relic-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)

## ONTAP CLI Commands (Verified)

```bash
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

## Vendor-Specific Details (New Relic)

- **Endpoint**: `https://log-api.newrelic.com/log/v1` (US region)
- **Auth Header**: `Api-Key: <license_key>`
- **Max Batch Size**: 1MB per request
- **Firehose Support**: Yes

## Requirements to Add

### Scenario A: EMS Webhook → New Relic Log API

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events into New Relic Log API format
   - Forwards to `https://log-api.newrelic.com/log/v1` with `Api-Key` header
   - Respects 1MB batch limit
   - Sets `logtype` to `fsxn-ems`

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: Log appears in New Relic Logs UI within 120 seconds
   - Verify: New Relic API returns HTTP 202 (accepted)

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: Log appears in New Relic within 180 seconds

### Scenario B: FPolicy → New Relic Log API

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats into New Relic Log API format
   - Forwards with `logtype` set to `fsxn-fpolicy`
   - Includes `operation`, `file_path`, `user`, `client_ip` as log attributes

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: Log appears in New Relic within 30 seconds

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results.
```

---

## 7. OTel Collector E2E Verification

```
Update the otel-collector-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)

## ONTAP CLI Commands (Verified)

```bash
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

## Vendor-Specific Details (OpenTelemetry / OTLP)

- **Endpoint**: `http://<collector>:4318/v1/logs` (OTLP HTTP)
- **Auth**: Configurable (typically none for internal collectors, or Bearer token)
- **Max Batch Size**: Configurable (collector-dependent)
- **Firehose Support**: No

## Requirements to Add

### Scenario A: EMS Webhook → OTel Collector OTLP Logs

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events into OTLP Log format (JSON)
   - Forwards to `http://<collector>:4318/v1/logs`
   - Sets resource attributes: `service.name=fsxn-ems`, `service.namespace=fsxn-ontap`

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: LogRecord appears in OTel Collector's configured exporter within 120 seconds
   - Verify: OTLP endpoint returns HTTP 200

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: LogRecord appears in collector within 180 seconds

### Scenario B: FPolicy → OTel Collector OTLP Logs

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats into OTLP Log format
   - Sets resource attributes: `service.name=fsxn-fpolicy`
   - Maps fields to LogRecord attributes: `operation`, `file_path`, `user`, `client_ip`

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: LogRecord appears in collector within 30 seconds

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results.
```

---

## 8. Splunk Serverless E2E Verification

```
Update the splunk-serverless-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)

## ONTAP CLI Commands (Verified)

```bash
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

## Vendor-Specific Details (Splunk HEC)

- **Endpoint**: `https://<host>:8088/services/collector/event`
- **Auth Header**: `Authorization: Splunk <token>`
- **Max Batch Size**: No hard limit (recommended: keep under 1MB per event)
- **Firehose Support**: Yes (built-in Splunk Firehose destination)

## Requirements to Add

### Scenario A: EMS Webhook → Splunk HEC

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events into Splunk HEC format
   - Forwards to `https://<host>:8088/services/collector/event` with `Authorization: Splunk <token>` header
   - Sets `sourcetype` to `fsxn:ems:webhook` and `source` to `fsxn-ems`

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: Event appears in Splunk index within 120 seconds
   - Verify: HEC returns HTTP 200 with `{"text":"Success","code":0}`

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: Event appears in Splunk within 180 seconds

### Scenario B: FPolicy → Splunk HEC

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats into Splunk HEC format
   - Sets `sourcetype` to `fsxn:fpolicy:event` and `source` to `fsxn-fpolicy`
   - Maps fields: `operation`, `file_path`, `user`, `client_ip` into `event` object

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: Event appears in Splunk within 30 seconds

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results.
```

---

## 9. Sumo Logic E2E Verification

```
Update the sumo-logic-e2e-verification spec to add EMS/FPolicy event forwarding scenarios.

## Context

The project now supports two additional event sources beyond S3 audit logs:
- **EMS Webhook**: ONTAP EMS events delivered via API Gateway → Lambda
- **FPolicy External Engine**: Real-time file operation events via ECS Fargate → SQS → EventBridge

Shared infrastructure templates and parser layer are already implemented:
- `shared/templates/ems-webhook-apigw.yaml` — REST API Gateway (REGIONAL) for EMS Webhook reception
- `shared/templates/fpolicy-apigw.yaml` — ECS Fargate + SQS + EventBridge for FPolicy reception
- `shared/lambda-layers/ems-parser/` — Shared EMS event parsing layer (parse_ems_event, format_ems_event)

## FPolicy Architecture (VERIFIED WORKING)

The FPolicy path is:
  ONTAP → TCP:9898 → ECS Fargate (FPolicy Server container) → SQS → Bridge Lambda → EventBridge (source: fpolicy.fsxn) → Vendor Lambda

Key details:
- FPolicy uses a proprietary binary protocol over TCP (NOT HTTP/HTTPS)
- ONTAP connects directly to the Fargate task IP on port 9898
- NLB is for health checks only, NOT for routing FPolicy traffic
- Container image: ECR-hosted ARM64 FPolicy server (e.g., v2-timeout-fix tag)

## ONTAP CLI Commands (Verified)

```bash
vserver fpolicy policy external-engine create -vserver FPolicySMB \
  -engine-name fpolicy_lambda_engine \
  -primary-servers <fargate-task-ip> \
  -port 9898 \
  -extern-engine-type asynchronous
```

## Vendor-Specific Details (Sumo Logic)

- **Endpoint**: `https://endpoint<N>.collection.sumologic.com/receiver/v1/http/<unique-token>`
- **Auth**: Embedded in URL (no separate auth header required)
- **Max Batch Size**: 1MB per request
- **Firehose Support**: No

## Requirements to Add

### Scenario A: EMS Webhook → Sumo Logic HTTP Source

1. Deploy `shared/templates/ems-webhook-apigw.yaml` with a Lambda that:
   - Uses `shared/lambda-layers/ems-parser/` to parse incoming EMS events
   - Formats normalized events as JSON lines (newline-delimited)
   - Forwards to Sumo Logic HTTP Source URL (auth embedded in endpoint URL)
   - Respects 1MB batch limit
   - Sets HTTP headers: `X-Sumo-Category: fsxn/ems`, `X-Sumo-Name: fsxn-ems-webhook`

2. **ARP Ransomware Detection Alert Test**:
   - Trigger: ONTAP CLI `security anti-ransomware volume attack simulate`
   - Expected EMS event: `arw.volume.state` (severity: alert)
   - Verify: Log appears in Sumo Logic within 120 seconds
   - Verify: HTTP Source returns HTTP 200

3. **Quota Threshold Exceeded Alert Test**:
   - Trigger: Set soft quota (50MB) and write 60MB+ data
   - Expected EMS event: `wafl.quota.softlimit.exceeded` (severity: warning)
   - Verify: Log appears in Sumo Logic within 180 seconds

### Scenario B: FPolicy → Sumo Logic HTTP Source

4. Deploy `shared/templates/fpolicy-apigw.yaml` (ECS Fargate + SQS + EventBridge pattern).
   Create a vendor Lambda that subscribes to EventBridge custom bus events (source: `fpolicy.fsxn`) and:
   - Receives FPolicy file operation events from EventBridge
   - Formats as JSON lines (newline-delimited)
   - Forwards to Sumo Logic HTTP Source URL
   - Sets headers: `X-Sumo-Category: fsxn/fpolicy`, `X-Sumo-Name: fsxn-fpolicy-events`
   - Maps fields: `operation`, `file_path`, `user`, `client_ip`, `timestamp`

5. **FPolicy File Operation Test**:
   - Verify ECS Fargate task is running and healthy
   - Verify ONTAP KeepAlive messages in ECS logs (every 120 seconds, per the
     engine's `keep_alive_interval`; query a window of 300 s or more. A 30 s
     window shows nothing on a healthy connection about three runs in four.
     STATUS_REQ arrives every ~10 s, is logged at DEBUG, and is a different
     message — it does not stand in for a KeepAlive)
   - Trigger: Create a file via CIFS/SMB
   - Verify: `[SQS] Sent: <filename> (create)` appears in ECS CloudWatch logs
   - Verify: Log appears in Sumo Logic within 30 seconds

### Verification Results Document

Follow the pattern from `docs/ja/verification-results-ems-fpolicy.md` for structuring test results.
```

---

## Usage Instructions

1. Open a new Kiro session targeting the vendor's E2E verification spec (e.g., `.kiro/specs/datadog-e2e-verification/`)
2. Copy the entire prompt block (between the triple backticks) for the target vendor
3. Paste as the initial instruction to Kiro
4. Kiro will update the vendor spec's requirements, design, and tasks to include EMS/FPolicy scenarios

## Shared Resources Reference

| Resource | Path | Purpose |
|----------|------|---------|
| EMS Webhook Template | `shared/templates/ems-webhook-apigw.yaml` | REST API Gateway (REGIONAL) + Lambda for EMS reception |
| FPolicy Template | `shared/templates/fpolicy-apigw.yaml` | ECS Fargate + SQS + EventBridge for FPolicy reception |
| EMS Parser Layer | `shared/lambda-layers/ems-parser/` | Shared parsing module |
| EMS Receiver Lambda | `shared/lambda-layers/ems-parser/lambda/ems_receiver.py` | Reference EMS handler |
| FPolicy Receiver Lambda | `shared/lambda-layers/ems-parser/lambda/fpolicy_receiver.py` | Reference FPolicy handler |
| Verification Results Pattern | `docs/ja/verification-results-ems-fpolicy.md` | Document structure template |
| E2E Test Scripts | `shared/scripts/e2e-test-*.py` | Automated test scripts |
| Operational Notes | `docs/ja/operational-notes-fpolicy.md` | FPolicy operational knowledge |
