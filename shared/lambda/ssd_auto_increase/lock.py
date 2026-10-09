"""DynamoDB single-flight lock lifecycle.

The lock item is keyed on the file system ID. Each conditional write that
releases or changes it requires the owner to be the invocation's own
correlation ID and the expected current state, so two invocations cannot both
submit a request. Lease expiry is decided by comparing ``expires_at`` to the
current time, not by when DynamoDB TTL deletes the item.

States and transitions follow docs/en/capacity-automation-t4-design.md
"Lock states". The module here only writes and reads the item; the handler
decides which transition to apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

# Lock states.
EVALUATING = "evaluating"
CALLING = "calling"
SUBMITTED = "submitted"
OPTIMIZING = "optimizing"
INDETERMINATE = "indeterminate"
MANUAL_DISPOSITION_REQUIRED = "manual_disposition_required"
BLOCKED = "blocked"


class LockContention(Exception):
    """Another invocation holds a valid lease on the lock."""


@dataclass
class LockItem:
    """A decoded lock table item.

    Attributes:
        file_system_id: The hash key.
        state: One of the state constants in this module.
        owner: Correlation ID of the invocation that owns the item.
        expires_at: Epoch seconds when the lease expires.
        attributes: The full decoded item (request facts, pending_events, etc.).
    """

    file_system_id: str
    state: str
    owner: str
    expires_at: int
    attributes: dict[str, Any]


def _now_epoch(now: datetime) -> int:
    return int(now.timestamp())


def _decode(item: dict[str, Any]) -> LockItem:
    return LockItem(
        file_system_id=item["file_system_id"],
        state=item["state"],
        owner=item["owner"],
        expires_at=int(item["expires_at"]),
        attributes={k: _from_decimal(v) for k, v in item.items()},
    )


def _from_decimal(value: Any) -> Any:
    # Recurse into dicts and lists so nested structures (report_context, the
    # bodies/reports carried in pending_events) decode their Decimals too; a
    # top-level-only decode would leave a nested Decimal that json.dumps then
    # renders as a string via default=str, corrupting a persisted report field.
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


def _to_item(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, dict):
        return {k: _to_item(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_item(v) for v in value]
    return value


class LockStore:
    """Conditional-write wrapper over the DynamoDB lock table."""

    def __init__(self, table: Any, file_system_id: str) -> None:
        """Create a lock store.

        Args:
            table: A boto3 DynamoDB Table resource.
            file_system_id: The managed file system ID (the hash key value).
        """
        self._table = table
        self._fs = file_system_id

    def read(self) -> LockItem | None:
        """Return the current lock item, or ``None`` when absent."""
        response = self._table.get_item(Key={"file_system_id": self._fs}, ConsistentRead=True)
        item = response.get("Item")
        return _decode(item) if item is not None else None

    def acquire(self, owner: str, expires_at: int, now: datetime) -> None:
        """Take the lock as a new ``evaluating`` item.

        Succeeds when no item exists, or the existing item is ``evaluating``
        with an expired lease and no pending archive events (a take-over).

        Args:
            owner: This invocation's correlation ID.
            expires_at: Epoch seconds for the new lease.
            now: Current time.

        Raises:
            LockContention: When a valid lease is held by another invocation.
        """
        epoch_now = _now_epoch(now)
        try:
            self._table.put_item(
                Item={
                    "file_system_id": self._fs,
                    "state": EVALUATING,
                    "owner": owner,
                    "expires_at": Decimal(expires_at),
                    "acquired_at": Decimal(epoch_now),
                },
                ConditionExpression=(
                    "attribute_not_exists(file_system_id) OR "
                    "(#s = :evaluating AND expires_at < :now AND "
                    "attribute_not_exists(pending_events))"
                ),
                ExpressionAttributeNames={"#s": "state"},
                ExpressionAttributeValues={
                    ":evaluating": EVALUATING,
                    ":now": Decimal(epoch_now),
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise LockContention(
                    f"a valid lease is held on {self._fs}"
                ) from exc
            raise

    def transition(
        self,
        owner: str,
        expected_state: str,
        new_attributes: dict[str, Any],
        now: datetime,
    ) -> None:
        """Conditionally overwrite the item, keeping ownership and expected state.

        Args:
            owner: This invocation's correlation ID.
            expected_state: The state the item must currently be in.
            new_attributes: The full attribute set to write (minus the key,
                owner and state, which are set from the arguments and the new
                attributes' ``state``).
            now: Current time (recorded as ``updated_at``).

        Raises:
            LockContention: When ownership or the expected state does not match.
        """
        item = {
            "file_system_id": self._fs,
            "owner": owner,
            "updated_at": Decimal(_now_epoch(now)),
            **{k: _to_item(v) for k, v in new_attributes.items()},
        }
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="#o = :owner AND #s = :expected",
                ExpressionAttributeNames={"#o": "owner", "#s": "state"},
                ExpressionAttributeValues={":owner": owner, ":expected": expected_state},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise LockContention(
                    f"owner or state no longer matches on {self._fs}"
                ) from exc
            raise

    def release(self, owner: str, expected_state: str) -> None:
        """Delete the item, keeping ownership and the expected state.

        Args:
            owner: This invocation's correlation ID.
            expected_state: The state the item must currently be in.

        Raises:
            LockContention: When ownership or the expected state does not match.
        """
        try:
            self._table.delete_item(
                Key={"file_system_id": self._fs},
                ConditionExpression="#o = :owner AND #s = :expected",
                ExpressionAttributeNames={"#o": "owner", "#s": "state"},
                ExpressionAttributeValues={":owner": owner, ":expected": expected_state},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise LockContention(
                    f"owner or state no longer matches on {self._fs}"
                ) from exc
            raise


def lease_expired(item: LockItem, now: datetime) -> bool:
    """Return True when the item's lease has expired."""
    return item.expires_at < _now_epoch(now)


def utcnow() -> datetime:
    """Current UTC time (timezone-aware). Separated so tests can patch it."""
    return datetime.now(timezone.utc)
