"""S3 Object Lock decision archive with run-time retention proof.

Each event is its own object at
``<prefix><file-system-id>/<correlation-id>/<sequence>-<event>.json``, written
with ``If-None-Match: *`` so a retry cannot replace an event, and with a
checksum algorithm because a bucket with retention configured requires one.

The function sends no retention headers and holds no ``s3:PutObjectRetention``:
it relies on the bucket's default retention and proves it at run time. In
``auto`` a failed proof fails closed (no call); in ``notify_only`` and
``approve`` it fails open and the gap is reported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

# Archive event names.
DECISION = "decision"
ACCEPTED = "accepted"
REJECTED = "rejected"
AMBIGUOUS = "ambiguous"
RECONCILED = "reconciled"
CAPACITY_AVAILABLE = "capacity_available"
TERMINAL = "terminal"


# PutObject with If-None-Match: * returns this when the object already exists.
_PRECONDITION_FAILURE_CODES = frozenset({"PreconditionFailed", "412"})


def _is_precondition_failure(exc: ClientError) -> bool:
    """True when a PutObject failed because the If-None-Match: * object exists."""
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    status = str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
    return code in _PRECONDITION_FAILURE_CODES or status == "412"


class RetentionUnproven(Exception):
    """The bucket or an object did not meet the required retention contract."""


@dataclass(frozen=True)
class RetentionCheck:
    """Result of a retention proof.

    Attributes:
        proven: True when the bucket or object met the contract.
        detail: A human-readable reason, present when ``proven`` is False.
        already_written: True when this check proved an object a previous
            invocation had already written (an idempotent ``If-None-Match: *``
            replay), rather than a fresh write by this invocation. The caller
            uses it to log the attempt as a replay rather than a first write.
    """

    proven: bool
    detail: str | None
    already_written: bool = False


class DecisionArchive:
    """Writes archive objects and proves their Object Lock retention."""

    def __init__(
        self,
        s3_client: Any,
        bucket: str,
        prefix: str,
        file_system_id: str,
        required_mode: str,
        min_retention_days: int,
    ) -> None:
        """Create a decision archive.

        Args:
            s3_client: A boto3 S3 client.
            bucket: The archive bucket name.
            prefix: The key prefix for archive objects.
            file_system_id: The managed file system ID.
            required_mode: ``COMPLIANCE`` or ``GOVERNANCE``.
            min_retention_days: Minimum retain-until period in days.
        """
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = prefix
        self._fs = file_system_id
        self._required_mode = required_mode
        self._min_days = min_retention_days

    def _key(self, correlation_id: str, sequence: int, event: str) -> str:
        return f"{self._prefix}{self._fs}/{correlation_id}/{sequence}-{event}.json"

    def check_bucket_retention(self) -> RetentionCheck:
        """Prove the bucket's default retention before the first event.

        Returns:
            A :class:`RetentionCheck`.
        """
        try:
            response = self._s3.get_object_lock_configuration(Bucket=self._bucket)
        except (ClientError, BotoCoreError) as exc:
            # A transport failure (endpoint/connect/read timeout, a BotoCoreError
            # rather than a service ClientError) is as unproven as a service
            # error: the bucket default retention could not be read, so the
            # bucket is reported as a gap. Auto fails closed on it; the fail-open
            # modes report it and continue.
            return RetentionCheck(False, f"GetBucketObjectLockConfiguration failed: {exc}")
        config = response.get("ObjectLockConfiguration", {})
        if config.get("ObjectLockEnabled") != "Enabled":
            return RetentionCheck(False, "Object Lock is not enabled on the bucket")
        rule = config.get("Rule", {}).get("DefaultRetention", {})
        mode = rule.get("Mode")
        if mode != self._required_mode:
            return RetentionCheck(
                False, f"default retention mode is {mode!r}, required {self._required_mode!r}"
            )
        days = rule.get("Days")
        years = rule.get("Years")
        effective_days = days if days is not None else (years or 0) * 365
        if effective_days < self._min_days:
            return RetentionCheck(
                False,
                f"default retention is {effective_days} day(s), required at least "
                f"{self._min_days}",
            )
        return RetentionCheck(True, None)

    def write_event(
        self, correlation_id: str, sequence: int, event: str, body: dict[str, Any]
    ) -> RetentionCheck:
        """Write one archive object and prove its retention.

        Args:
            correlation_id: The evaluation's correlation ID.
            sequence: Event sequence number within the correlation ID. The
                caller keeps it monotonic across invocations by storing the
                last value on the lock item and continuing from it; it is only
                used to build a unique, ordered key, not validated here.
            event: Event name (one of the constants in this module).
            body: The JSON body.

        Returns:
            A :class:`RetentionCheck` for the written object. When the write
            itself fails, this raises instead.

        Raises:
            ClientError: When PutObject fails with a service error other than
                the If-None-Match precondition (the caller records
                pending_events for a post-call event, or fails the intent for a
                pre-call one).
            BotoCoreError: When PutObject fails with a transport error
                (endpoint/connect/read timeout). The caller treats it the same
                way as a service PutObject failure.
        """
        key = self._key(correlation_id, sequence, event)
        before = datetime.now(timezone.utc)
        payload = json.dumps(body, default=str, sort_keys=True).encode("utf-8")
        try:
            response = self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                ContentType="application/json",
                IfNoneMatch="*",
                ChecksumAlgorithm="SHA256",
            )
        except ClientError as exc:
            # If-None-Match: * fails with PreconditionFailed (412) when the
            # object already exists. For a retried post-call event, that means a
            # previous invocation already wrote this exact event and only the
            # DynamoDB acknowledgement failed. Treat the existing object as the
            # written event and prove its retention from scratch, so a successful
            # write followed by a failed clear becomes a provable, idempotent
            # replay rather than a permanent "write failed" that never clears.
            if _is_precondition_failure(exc):
                return self._check_object_retention(key, None, before, already_written=True)
            raise
        version_id = response.get("VersionId")
        return self._check_object_retention(key, version_id, before)

    def _check_object_retention(
        self,
        key: str,
        version_id: str | None,
        before: datetime,
        *,
        already_written: bool = False,
    ) -> RetentionCheck:
        """Prove an object's Object Lock retention.

        Args:
            key: The object key.
            version_id: The version written by this invocation, or ``None`` when
                proving an object a previous invocation already wrote.
            before: The time just before the write (for a fresh write, the
                retain-until date must be at least ``min_days`` beyond it).
            already_written: True when the object was written by an earlier
                invocation (an idempotent-replay precondition failure). The
                earliest retain-until bound is then relaxed to "still protected
                now", because its retain-until was computed from the earlier
                creation time and the bucket default was proven at least
                ``min_days`` before step 1.
        """
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if version_id is not None:
            kwargs["VersionId"] = version_id
        try:
            response = self._s3.get_object_retention(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            # A transport failure leaves the object's retention unproven, the
            # same outcome as a service error: the object may have been written
            # but its protection cannot be confirmed this invocation.
            return RetentionCheck(False, f"GetObjectRetention failed: {exc}", already_written)
        retention = response.get("Retention", {})
        mode = retention.get("Mode")
        if mode != self._required_mode:
            return RetentionCheck(
                False,
                f"object retention mode is {mode!r}, required {self._required_mode!r}",
                already_written,
            )
        retain_until = retention.get("RetainUntilDate")
        if not isinstance(retain_until, datetime):
            return RetentionCheck(False, "object has no RetainUntilDate", already_written)
        if retain_until.tzinfo is None:
            retain_until = retain_until.replace(tzinfo=timezone.utc)
        earliest = before if already_written else before + timedelta(days=self._min_days)
        if retain_until < earliest:
            return RetentionCheck(
                False,
                f"RetainUntilDate {retain_until.isoformat()} is earlier than the required "
                f"{earliest.isoformat()}",
                already_written,
            )
        return RetentionCheck(True, None, already_written)
