"""FSx for ONTAP AWS Backup / RAM event shipper for Datadog.

Receives AWS Backup / logically-air-gapped-vault (LAG-vault) and AWS RAM events
from EventBridge, normalises them with the shared ``aws_backup_event`` module,
and ships to the Datadog Logs Intake API v2.

Supports all Datadog sites (US1, US3, US5, EU1, AP1, US1-FED, AP2).
See: https://docs.datadoghq.com/getting_started/site/

The shared normaliser is bundled next to this handler in the Lambda zip by
``scripts/deploy.sh`` (the same way the EMS parser is bundled for ems_handler).
The ``sys.path`` shim below lets it import both at runtime (handler directory)
and under pytest (the repo's shared/python directory, wired by conftest.py).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import urllib3

# ─── Shared normaliser import ──────────────────────────────────────────────
# At runtime the module sits beside this file; under pytest it lives in the
# repo's shared/python directory (added to sys.path by the vendor conftest).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aws_backup_event import (  # noqa: E402
    extract_detail,
    normalize_backup_event,
)

# ─── Configuration from environment ────────────────────────────────────────
# All configuration is driven by environment variables for multi-region support.
# No hardcoded values — each deployment can target any Datadog site.

DATADOG_SITE = os.environ.get("DATADOG_SITE", "datadoghq.com")
API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
DD_ENV = os.environ.get("DD_ENV", "production")
ENABLE_GZIP = os.environ.get("ENABLE_GZIP", "false").lower() == "true"

# ─── Constants ──────────────────────────────────────────────────────────────

# Overridable so the log pipeline / facet filters can be retargeted without a
# code change. The defaults match what template-ems-fpolicy.yaml sets.
DD_SOURCE = os.environ.get("DD_SOURCE", "fsxn-backup")
DD_SERVICE = os.environ.get("DD_SERVICE", "fsxn-ontap")
MAX_BATCH_SIZE_BYTES = 5 * 1024 * 1024  # 5MB per request (Datadog limit)
MAX_BATCH_ITEMS = 1000  # Max items per batch (Datadog limit)
MAX_RETRIES = 3

# Datadog Logs Intake URL — constructed from DATADOG_SITE env var.
INTAKE_URL = f"https://http-intake.logs.{DATADOG_SITE}/api/v2/logs"

# ─── Logger setup ──────────────────────────────────────────────────────────

logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL))

# ─── AWS clients (initialized outside handler for connection reuse) ─────────

secrets_client = boto3.client("secretsmanager")

# HTTP client with connection pooling
http = urllib3.PoolManager(
    num_pools=4,
    maxsize=10,
    retries=urllib3.Retry(total=0),  # We handle retries ourselves
)

# Cache for API key (Lambda execution context reuse)
_api_key_cache: str | None = None


def get_api_key() -> str:
    """Retrieve the Datadog API key from Secrets Manager with caching.

    Supports both plain string and JSON format secrets:
    - Plain string: "your-api-key"
    - JSON: {"api_key": "your-api-key"} or {"DD_API_KEY": "your-api-key"}

    Returns:
        Datadog API key string.
    """
    global _api_key_cache
    if _api_key_cache is None:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = response["SecretString"]
        try:
            parsed = json.loads(secret)
            _api_key_cache = parsed.get("api_key", parsed.get("DD_API_KEY", secret))
        except (json.JSONDecodeError, AttributeError):
            _api_key_cache = secret
    return _api_key_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle an AWS Backup / RAM event from EventBridge.

    Normalises the event with the shared ``aws_backup_event`` module, formats
    it for the Datadog Logs API v2, and ships it with retry logic.

    Args:
        event: EventBridge event (source ``aws.backup`` or ``aws.ram``).
        context: Lambda context object.

    Returns:
        Dict with ``statusCode`` and a processing summary.
    """
    logger.info("Backup handler invoked: id=%s", event.get("id", "unknown"))

    try:
        extract_detail(event)
        record = normalize_backup_event(event)
    except ValueError as e:
        logger.error("Failed to normalise backup event: %s", str(e))
        return {"statusCode": 400, "error": f"Invalid backup event: {e}"}

    try:
        api_key = get_api_key()
    except Exception as e:
        logger.error("Failed to retrieve API key: %s", str(e))
        return {"statusCode": 500, "error": "Failed to retrieve API key"}

    dd_logs = _format_for_datadog([record])
    shipped = _ship_to_datadog(dd_logs, api_key)

    return {
        "statusCode": 200 if shipped == len(dd_logs) else 207,
        "classification": record["classification"],
        "shipped": shipped,
    }


def _format_for_datadog(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Format normalised backup records for Datadog Logs Intake API v2.

    Each log entry includes the facet attributes the vault dashboard keys on:
    ``event_name``, ``state``, ``classification``, ``backup_vault_name``,
    ``resource_type``, and ``source``.

    Args:
        records: Normalised backup records from ``normalize_backup_event``.

    Returns:
        List of Datadog-formatted log entries.
    """
    dd_logs: list[dict[str, Any]] = []

    for record in records:
        dd_log: dict[str, Any] = {
            "ddsource": DD_SOURCE,
            "ddtags": (
                f"source:{DD_SOURCE},"
                f"service:{DD_SERVICE},"
                f"env:{DD_ENV}"
            ),
            "hostname": record.get("source", "fsxn-ontap") or "fsxn-ontap",
            "service": DD_SERVICE,
        }

        status_message = record.get("status_message", "")
        dd_log["message"] = status_message or json.dumps(record["raw"], default=str)

        timestamp = record.get("timestamp", "")
        if timestamp:
            dd_log["date"] = timestamp

        dd_log["attributes"] = {
            "event_name": record.get("event_name", ""),
            "state": record.get("state", ""),
            "classification": record.get("classification", ""),
            "backup_vault_name": record.get("backup_vault_name", ""),
            "resource_type": record.get("resource_type", ""),
            "resource_arn": record.get("resource_arn", ""),
            "job_id": record.get("job_id", ""),
            "source": record.get("source", ""),
        }

        dd_logs.append(dd_log)

    return dd_logs


def _ship_to_datadog(logs: list[dict[str, Any]], api_key: str) -> int:
    """Ship logs to the Datadog Logs Intake API v2 in batches.

    Respects Datadog batch limits (5MB / 1000 items per request). Raises
    RuntimeError if any batch fails after retries, preventing the caller from
    treating the invocation as successful.

    Args:
        logs: Datadog-formatted log entries.
        api_key: Datadog API key.

    Returns:
        Number of successfully shipped logs.

    Raises:
        RuntimeError: If one or more batches fail after all retries.
    """
    if not logs:
        return 0

    shipped = 0
    failed_batches = 0
    batches = _create_batches(logs)

    for batch in batches:
        success = _send_batch(batch, api_key)
        if success:
            shipped += len(batch)
        else:
            failed_batches += 1
            logger.error("Failed to ship batch of %d logs", len(batch))

    if failed_batches:
        raise RuntimeError(
            f"{failed_batches} Datadog batch(es) failed after retries. "
            f"Shipped {shipped}/{len(logs)} logs."
        )

    return shipped


def _create_batches(logs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split logs into batches respecting Datadog size limits.

    Each batch must be under 5MB uncompressed and 1000 items.

    Args:
        logs: List of Datadog-formatted log entries.

    Returns:
        List of batches, each batch being a list of log entries.
    """
    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_size = 0

    for log in logs:
        log_size = len(json.dumps(log).encode("utf-8"))

        if (
            current_size + log_size > MAX_BATCH_SIZE_BYTES
            or len(current_batch) >= MAX_BATCH_ITEMS
        ):
            if current_batch:
                batches.append(current_batch)
            current_batch = [log]
            current_size = log_size
        else:
            current_batch.append(log)
            current_size += log_size

    if current_batch:
        batches.append(current_batch)

    return batches


def _send_batch(batch: list[dict[str, Any]], api_key: str) -> bool:
    """Send a batch of logs to Datadog with exponential backoff retry.

    Supports optional gzip compression (controlled by the ENABLE_GZIP env var).

    Args:
        batch: List of Datadog-formatted log entries.
        api_key: Datadog API key.

    Returns:
        True if successfully sent, False otherwise.
    """
    json_payload = json.dumps(batch).encode("utf-8")

    if ENABLE_GZIP:
        payload = gzip.compress(json_payload)
        headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "DD-API-KEY": api_key,
        }
    else:
        payload = json_payload
        headers = {
            "Content-Type": "application/json",
            "DD-API-KEY": api_key,
        }

    for attempt in range(MAX_RETRIES):
        try:
            response = http.request(
                "POST",
                INTAKE_URL,
                body=payload,
                headers=headers,
                timeout=30.0,
            )

            if response.status < 300:
                logger.debug(
                    "Successfully shipped %d logs (attempt %d)",
                    len(batch),
                    attempt + 1,
                )
                return True

            if response.status == 429:
                retry_after = int(
                    response.headers.get("Retry-After", 2 ** (attempt + 1))
                )
                logger.warning(
                    "Rate limited by Datadog, retrying in %ds", retry_after
                )
                time.sleep(retry_after)
                continue

            if response.status >= 500:
                wait_time = 2 ** (attempt + 1)
                logger.warning(
                    "Datadog server error %d, retrying in %ds",
                    response.status,
                    wait_time,
                )
                time.sleep(wait_time)
                continue

            logger.error(
                "Datadog API error %d: %s",
                response.status,
                response.data.decode("utf-8", errors="replace")[:500],
            )
            return False

        except urllib3.exceptions.HTTPError as e:
            wait_time = 2 ** (attempt + 1)
            logger.warning(
                "HTTP error shipping to Datadog (attempt %d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                str(e),
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait_time)

    return False
