"""DynamoDB-based object processing ledger for FSx for ONTAP audit log pipeline.

Provides per-object processing state tracking, replacing the simple SSM
high-watermark checkpoint for Level 3 (Production Baseline) deployments.

Benefits over SSM checkpoint:
- Per-object state (processed, failed, poison-pill)
- Deduplication by key + ETag
- Concurrent worker support (conditional writes)
- Replay tracking (which objects were re-processed)
- Poison-pill detection (auto-skip after N failures)

Usage:
    from object_ledger import ObjectLedger

    ledger = ObjectLedger(table_name=os.environ["LEDGER_TABLE_NAME"])

    # Check if object needs processing
    if ledger.should_process(key, etag):
        try:
            process_and_ship(key)
            ledger.mark_success(key, etag)
        except Exception as e:
            ledger.mark_failure(key, etag, str(e))

    # Check for poison pills
    poison_pills = ledger.get_poison_pills(max_failures=3)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400


class ObjectLedger:
    """DynamoDB-backed per-object processing state tracker.

    Table schema:
        PK: s3_key (String) -- S3 object key
        Attributes:
            etag: String -- S3 ETag for deduplication
            status: String -- "pending" | "processing" | "success" | "failed" | "poison_pill"
            failure_count: Number -- consecutive failure count
            last_error: String -- most recent error message
            processed_at: Number -- epoch timestamp of last successful processing
            failed_at: Number -- epoch timestamp of last failure
            created_at: Number -- epoch timestamp of first seen
            worker_id: String -- Lambda request ID (for concurrency tracking)
            expires_at: Number -- DynamoDB TTL epoch, present only while an entry
                is eligible for expiry (see the TTL notes below)

    Retention:
        DynamoDB expires an item only if it carries the attribute named in the
        table's TimeToLiveSpecification, so retention needs both halves: TTL
        enabled on the table (shared/templates/object-ledger.yaml) and this class
        writing expires_at. Configuring one without the other reads as if
        retention were set up while the table grows without bound.

        expires_at is written on successful entries and removed when an entry is
        promoted to poison_pill. Poison pills are the permanent skip list: if one
        expires, should_process starts returning True for a file already known to
        be unprocessable, and the pipeline re-enters the failure loop that
        produced the poison pill in the first place.
    """

    def __init__(
        self,
        table_name: str,
        max_failures: int = 3,
        dynamodb_client: Any | None = None,
        ttl_days: int | None = None,
    ) -> None:
        """Initialize the object ledger.

        Args:
            table_name: DynamoDB table name.
            max_failures: Number of consecutive failures before marking as poison pill.
            dynamodb_client: Optional pre-configured DynamoDB client (for testing).
            ttl_days: Days to retain successful entries. Defaults to the
                LEDGER_TTL_DAYS environment variable, or 0 (keep forever). Must
                match the TTLDays parameter of the ledger stack.
        """
        self.table_name = table_name
        self.max_failures = max_failures
        self._client = dynamodb_client or boto3.client("dynamodb")

        if ttl_days is None:
            raw = os.environ.get("LEDGER_TTL_DAYS", "0")
            try:
                ttl_days = int(raw)
            except ValueError:
                logger.warning(
                    "LEDGER_TTL_DAYS=%r is not an integer; treating as 0 "
                    "(entries kept forever)",
                    raw,
                )
                ttl_days = 0
        self.ttl_days = max(0, ttl_days)

    def _expiry(self) -> int | None:
        """Epoch seconds at which an entry becomes eligible for expiry."""
        if self.ttl_days <= 0:
            return None
        return int(time.time()) + (self.ttl_days * SECONDS_PER_DAY)

    def should_process(self, key: str, etag: str, worker_id: str = "") -> bool:
        """Check if an object should be processed.

        Returns False if:
        - Already successfully processed with the same ETag
        - Marked as poison pill
        - Currently being processed by another worker

        Uses conditional PutItem to claim the object for processing.
        """
        now = int(time.time())

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item={
                    "s3_key": {"S": key},
                    "etag": {"S": etag},
                    "status": {"S": "processing"},
                    "failure_count": {"N": "0"},
                    "created_at": {"N": str(now)},
                    "worker_id": {"S": worker_id},
                },
                ConditionExpression=(
                    "attribute_not_exists(s3_key) OR "
                    "(#s <> :success AND #s <> :poison AND etag <> :etag)"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":success": {"S": "success"},
                    ":poison": {"S": "poison_pill"},
                    ":etag": {"S": etag},
                },
            )
            logger.debug("Claimed object for processing: %s", key)
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.debug("Skipping object (already handled): %s", key)
                return False
            raise

    def mark_success(self, key: str, etag: str) -> None:
        """Mark an object as successfully processed."""
        now = int(time.time())
        update = (
            "SET #s = :status, etag = :etag, processed_at = :ts, "
            "failure_count = :zero"
        )
        values: dict[str, Any] = {
            ":status": {"S": "success"},
            ":etag": {"S": etag},
            ":ts": {"N": str(now)},
            ":zero": {"N": "0"},
        }

        expiry = self._expiry()
        if expiry is not None:
            update += ", expires_at = :exp"
            values[":exp"] = {"N": str(expiry)}

        self._client.update_item(
            TableName=self.table_name,
            Key={"s3_key": {"S": key}},
            UpdateExpression=update,
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=values,
        )
        logger.info("Marked success: %s", key)

    def mark_failure(self, key: str, etag: str, error: str) -> None:
        """Mark an object as failed. Promotes to poison pill after max_failures."""
        now = int(time.time())

        try:
            response = self._client.update_item(
                TableName=self.table_name,
                Key={"s3_key": {"S": key}},
                UpdateExpression=(
                    "SET #s = :failed, last_error = :err, failed_at = :ts, "
                    "etag = :etag "
                    "ADD failure_count :one"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":failed": {"S": "failed"},
                    ":err": {"S": error[:500]},
                    ":ts": {"N": str(now)},
                    ":etag": {"S": etag},
                    ":one": {"N": "1"},
                },
                ReturnValues="ALL_NEW",
            )

            failure_count = int(
                response["Attributes"].get("failure_count", {}).get("N", "0")
            )

            if failure_count >= self.max_failures:
                self._mark_poison_pill(key)

        except ClientError as e:
            logger.error("Failed to update ledger for %s: %s", key, str(e))

    def _mark_poison_pill(self, key: str) -> None:
        """Mark an object as a poison pill (will be skipped permanently).

        Removes expires_at so the entry outlives the retention window. A poison
        pill that expires stops suppressing the file, and the pipeline resumes
        the failure loop that created the poison pill -- indefinitely, since each
        new cycle re-creates and re-expires the entry.
        """
        self._client.update_item(
            TableName=self.table_name,
            Key={"s3_key": {"S": key}},
            UpdateExpression="SET #s = :status REMOVE expires_at",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": {"S": "poison_pill"}},
        )
        logger.warning("Marked as POISON PILL (will be skipped): %s", key)

    def get_poison_pills(self, limit: int = 100) -> list[dict[str, Any]]:
        """List objects marked as poison pills (for operational review).

        Returns a list of dicts with key, last_error, failure_count, failed_at.
        """
        response = self._client.scan(
            TableName=self.table_name,
            FilterExpression="#s = :poison",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":poison": {"S": "poison_pill"}},
            Limit=limit,
        )

        results = []
        for item in response.get("Items", []):
            results.append({
                "key": item["s3_key"]["S"],
                "last_error": item.get("last_error", {}).get("S", ""),
                "failure_count": int(item.get("failure_count", {}).get("N", "0")),
                "failed_at": int(item.get("failed_at", {}).get("N", "0")),
            })
        return results

    def get_status(self, key: str) -> dict[str, Any] | None:
        """Get the current status of a specific object."""
        response = self._client.get_item(
            TableName=self.table_name,
            Key={"s3_key": {"S": key}},
        )
        item = response.get("Item")
        if not item:
            return None
        return {
            "key": item["s3_key"]["S"],
            "status": item.get("status", {}).get("S", "unknown"),
            "etag": item.get("etag", {}).get("S", ""),
            "failure_count": int(item.get("failure_count", {}).get("N", "0")),
            "last_error": item.get("last_error", {}).get("S", ""),
            "processed_at": int(item.get("processed_at", {}).get("N", "0")),
        }
