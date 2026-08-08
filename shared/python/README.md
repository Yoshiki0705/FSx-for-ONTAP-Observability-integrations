# Shared Python Utilities

Common modules for all FSx for ONTAP observability Lambda handlers.

## Modules

### observability.py

Standardized structured logging, custom metrics, and distributed tracing using [AWS Lambda Powertools for Python](https://docs.powertools.aws.dev/lambda/python/latest/).

```python
from shared.python.observability import logger, metrics, tracer, MetricUnit

@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event, context):
    logger.info("Processing started")
    metrics.add_metric(name="RecordsParsed", unit=MetricUnit.Count, value=10)
```

**Standard Metrics:**

| Metric | Unit | Description |
|--------|------|-------------|
| RecordsParsed | Count | Audit log records parsed from S3 object |
| RecordsShipped | Count | Records successfully delivered to backend |
| DeliveryFailures | Count | Failed delivery attempts (after retries) |
| CheckpointAgeSeconds | Seconds | Time since last successful checkpoint update |
| BatchSizeBytes | Bytes | Size of payload sent to backend |
| DeliveryLatencyMs | Milliseconds | Time from parse to backend acknowledgment |
| PoisonPillFiles | Count | Files that consistently fail processing |

**Required Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| POWERTOOLS_SERVICE_NAME | fsxn-observability | Service name in logs and traces |
| POWERTOOLS_METRICS_NAMESPACE | FSxONTAPObservability | CloudWatch Metrics namespace |
| POWERTOOLS_LOG_LEVEL | INFO | Minimum log level |

### object_ledger.py

DynamoDB-backed object ledger for idempotent object processing and duplicate
suppression (Production Readiness Level 3). Deploy the table with
[`shared/templates/object-ledger.yaml`](../templates/object-ledger.yaml).

> Replaces `idempotency.py`, which was removed. It defined a second class also
> named `ObjectLedger`, keyed on `object_key` — a partition key that no template
> in this repository creates, so every call against the shipped ledger table
> raised `ValidationException`. Use this module instead.

```python
from object_ledger import ObjectLedger

ledger = ObjectLedger(
    table_name=os.environ["LEDGER_TABLE_NAME"],
    ttl_days=int(os.environ.get("LEDGER_TTL_DAYS", "0")),
)

if ledger.should_process(key, etag):
    try:
        process_and_ship(key)
        ledger.mark_success(key, etag)
    except Exception as exc:
        ledger.mark_failure(key, etag, str(exc))
```

**DynamoDB Table Schema:**

| Attribute | Type | Description |
|-----------|------|-------------|
| s3_key | String (PK) | S3 object key |
| etag | String | S3 ETag, used for deduplication |
| status | String | `processing` / `success` / `failed` / `poison_pill` |
| failure_count | Number | Consecutive failures; `max_failures` promotes to `poison_pill` |
| last_error | String | Most recent error, truncated to 500 chars |
| processed_at | Number | Epoch of last successful processing |
| failed_at | Number | Epoch of last failure |
| created_at | Number | Epoch first seen |
| worker_id | String | Lambda request ID, for concurrency tracking |
| expires_at | Number | DynamoDB TTL epoch. Present on successful entries only |

**Retention needs both halves.** DynamoDB expires an item only if it carries the
attribute named in the table's `TimeToLiveSpecification`. The template enables TTL
on `expires_at`; this module writes it from `ttl_days` / `LEDGER_TTL_DAYS`. Set one
without the other and retention reads as configured while the table grows without
bound — which is what the stack did before: it exposed a `TTLDays` parameter with
no `TimeToLiveSpecification` and no writer.

Pass the ledger stack's `TTLDays` through to the Lambda as `LEDGER_TTL_DAYS` so the
two agree. `0` means keep entries forever.

**Poison pills never expire.** `expires_at` is removed when an entry is promoted to
`poison_pill`. That list is what suppresses files already known to be
unprocessable; if an entry expired, `should_process` would return `True` again and
the pipeline would re-enter the failure loop that produced the poison pill —
repeatedly, since each cycle re-creates and re-expires the entry.

## Lambda Powertools Dependency

The shared observability module expects AWS Lambda Powertools for Python to be packaged through one of:
- Lambda Layer
- Deployment package (bundled with function code)
- Container image

If Powertools is not packaged, handlers will fail fast during cold start rather than silently disabling metrics or tracing.

### ObjectLedger vs Powertools Idempotency

| Aspect | ObjectLedger | Powertools Idempotency |
|--------|-------------|----------------------|
| Scope | FSx audit object-level processing state | Request/event-level idempotency |
| Key | S3 object key + ETag/LastModified | Event payload hash |
| Use case | "Has this audit file been processed?" | "Has this exact invocation been handled?" |
| Persistence | DynamoDB with TTL | DynamoDB with TTL |

Use ObjectLedger for file-level deduplication. Use Powertools Idempotency for Lambda invocation-level idempotency (e.g., retried SQS messages).

## Installation

For Lambda deployment, include these modules in a Lambda Layer or bundle them with the function code:

```bash
# As a Lambda Layer
cd shared/python
zip -r ../../shared-python-layer.zip .
aws lambda publish-layer-version \
  --layer-name fsxn-shared-python \
  --zip-file fileb://../../shared-python-layer.zip \
  --compatible-runtimes python3.12
```

## Dependencies

```
aws-lambda-powertools[tracer]>=2.0.0
boto3>=1.28.0
```
