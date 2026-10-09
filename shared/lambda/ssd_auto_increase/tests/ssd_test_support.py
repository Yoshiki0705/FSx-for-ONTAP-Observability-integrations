"""Helpers for the SSD auto-increase tests: fakes, builders, constants.

conftest.py puts this directory on sys.path, because under
--import-mode=importlib a test module cannot import from conftest.

The fakes record every call so a test can assert that UpdateFileSystem was or
was not called, and model the DynamoDB lock table with a conditional-write
check so the single-flight behaviour is exercised, not stubbed. No network and
no AWS calls. Payloads use placeholders only.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SOURCE_DIR = Path(__file__).resolve().parents[1]
MODULES = (
    "ssd_auto_increase_handler",
    "config",
    "guards",
    "lock",
    "archive",
    "report",
    "classify",
    "utilization",
)

FILE_SYSTEM_ID = "fs-0123456789abcdef0"
ACCOUNT_ID = "123456789012"
REGION = "ap-northeast-1"
NOTIFY_TOPIC_ARN = f"arn:aws:sns:{REGION}:{ACCOUNT_ID}:fsxn-ssd-auto-increase-notify"
LOCK_TABLE = "fsxn-ssd-auto-increase-lock"
ARCHIVE_BUCKET = "ssd-auto-increase-audit"
TRIGGER_ALARM = "fsxn-ssd-auto-increase-ssd-utilization"

BASE_ENV = {
    "FILE_SYSTEM_ID": FILE_SYSTEM_ID,
    "MODE": "auto",
    "MAX_STORAGE_CAPACITY_GIB": "4096",
    "INCREASE_PERCENT": "10",
    "TRIGGER_ALARM_NAMES": TRIGGER_ALARM,
    "LOCK_TABLE_NAME": LOCK_TABLE,
    "NOTIFY_TOPIC_ARN": NOTIFY_TOPIC_ARN,
    "DECISION_LOG_GROUP": f"/fsx/ssd-auto-increase/{FILE_SYSTEM_ID}",
    "DECISION_ARCHIVE_BUCKET": ARCHIVE_BUCKET,
    "DECISION_ARCHIVE_PREFIX": "fsx-ssd-auto-increase/",
    "DECISION_ARCHIVE_REQUIRED_MODE": "COMPLIANCE",
    "DECISION_ARCHIVE_MIN_RETENTION_DAYS": "365",
    "INDETERMINATE_RECONCILE_HOURS": "6",
    "CONFIG_FINGERPRINT": "fingerprint-v1",
}


def make_client_error(code: str, operation: str = "Op") -> Exception:
    """Build a real botocore ClientError with the given error code."""
    import botocore.exceptions

    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {}}, operation
    )


def make_transport_error(endpoint: str = "https://s3.ap-northeast-1.amazonaws.com/") -> Exception:
    """Build a real botocore transport error (a BotoCoreError, not a ClientError).

    An EndpointConnectionError models an endpoint/connect/read timeout: the
    request never produced a service response, so it is not a ClientError. The
    handler and archive must treat it the same way they treat a service error
    at each AWS operation boundary.
    """
    import botocore.exceptions

    return botocore.exceptions.EndpointConnectionError(endpoint_url=endpoint)


def _precondition_failure(operation: str = "PutObject") -> Exception:
    """A real ClientError modelling If-None-Match: * on an existing object."""
    import botocore.exceptions

    return botocore.exceptions.ClientError(
        {
            "Error": {"Code": "PreconditionFailed", "Message": "At least one of the "
                      "pre-conditions you specified did not hold"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        },
        operation,
    )


def alarm_response(state: str = "ALARM") -> dict[str, Any]:
    return {"MetricAlarms": [{"AlarmName": TRIGGER_ALARM, "StateValue": state}]}


def admin_action(
    *,
    action_type: str = "FILE_SYSTEM_UPDATE",
    status: str = "IN_PROGRESS",
    request_time: datetime | None = None,
    target_capacity: int | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {"AdministrativeActionType": action_type, "Status": status}
    if request_time is not None:
        action["RequestTime"] = request_time
    if target_capacity is not None:
        action["TargetFileSystemValues"] = {"StorageCapacity": target_capacity}
    return action


def file_system(
    *,
    capacity: int = 1024,
    deployment_type: str = "SINGLE_AZ_1",
    ha_pairs: int = 1,
    iops_mode: str = "AUTOMATIC",
    iops: int = 3072,
    administrative_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "FileSystemId": FILE_SYSTEM_ID,
        "StorageCapacity": capacity,
        "OntapConfiguration": {
            "DeploymentType": deployment_type,
            "HAPairs": ha_pairs,
            "DiskIopsConfiguration": {"Mode": iops_mode, "Iops": iops},
        },
        "AdministrativeActions": administrative_actions or [],
    }


class FakeFsx:
    def __init__(self, file_systems: list[dict[str, Any]]) -> None:
        self._file_systems = list(file_systems)
        self.update_calls: list[dict[str, Any]] = []
        self.update_result: dict[str, Any] | Exception = {
            "ResponseMetadata": {"RequestId": "req-123"}
        }

        class _Meta:
            region_name = REGION

        self.meta = _Meta()

    def describe_file_systems(self, FileSystemIds: list[str]) -> dict[str, Any]:  # noqa: N803
        current = self._file_systems[0]
        if len(self._file_systems) > 1:
            current = self._file_systems.pop(0)
        return {"FileSystems": [current]}

    def update_file_system(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(kwargs)
        if isinstance(self.update_result, Exception):
            raise self.update_result
        return self.update_result


# The utilization every GetMetricData series returns unless a test overrides it.
DEFAULT_UTILIZATION = 85.5


class FakeCloudWatch:
    def __init__(
        self,
        alarms: dict[str, Any],
        *,
        series: dict[str | None, list[float]] | None = None,
        status_codes: dict[str | None, str] | None = None,
        metric_error: Exception | None = None,
    ) -> None:
        """A recording CloudWatch fake for DescribeAlarms and GetMetricData.

        Args:
            alarms: The DescribeAlarms response.
            series: Values per series, newest first, keyed by the query's
                ``Aggregate`` dimension value (``None`` for the file-system
                series). A series not listed returns ``[DEFAULT_UTILIZATION]``;
                an empty list models a window with no datapoint.
            status_codes: ``StatusCode`` per series key (default ``Complete``).
            metric_error: Raise this from get_metric_data (a ClientError or a
                BotoCoreError) to model a failed query.
        """
        self._alarms = alarms
        self._series = series or {}
        self._status_codes = status_codes or {}
        self._metric_error = metric_error
        self.metric_data_calls: list[dict[str, Any]] = []

    def describe_alarms(self, AlarmNames: list[str]) -> dict[str, Any]:  # noqa: N803
        return self._alarms

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.metric_data_calls.append(kwargs)
        if self._metric_error is not None:
            raise self._metric_error
        end = kwargs["EndTime"]
        results = []
        for query in kwargs["MetricDataQueries"]:
            dimensions = {
                d["Name"]: d["Value"] for d in query["MetricStat"]["Metric"]["Dimensions"]
            }
            key = dimensions.get("Aggregate")
            values = list(self._series.get(key, [DEFAULT_UTILIZATION]))
            # Timestamps derive from the request's EndTime, not the wall clock,
            # newest first (the handler asks for TimestampDescending).
            timestamps = [end - timedelta(seconds=300 * (i + 1)) for i in range(len(values))]
            results.append(
                {
                    "Id": query["Id"],
                    "Label": query["MetricStat"]["Metric"]["MetricName"],
                    "Timestamps": timestamps,
                    "Values": values,
                    "StatusCode": self._status_codes.get(key, "Complete"),
                }
            )
        return {"MetricDataResults": results, "Messages": []}


class FakeSns:
    def __init__(
        self,
        *,
        fail: bool = False,
        fail_on_subject: str | None = None,
        transport: bool = False,
    ) -> None:
        """A recording SNS fake.

        Args:
            fail: Raise a ClientError on every publish.
            fail_on_subject: Raise only when the Subject contains this
                substring; other publishes succeed. Used to make exactly the
                submitted report fail while the pre-call report succeeds.
            transport: Raise a botocore transport error (BotoCoreError) rather
                than a ClientError. Used to prove the SNS boundary recovers from
                transport failures, not only service errors.
        """
        self.published: list[dict[str, Any]] = []
        self._fail = fail
        self._fail_on_subject = fail_on_subject
        self._transport = transport

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        subject = kwargs.get("Subject", "")
        if self._fail or (self._fail_on_subject and self._fail_on_subject in subject):
            if self._transport:
                raise make_transport_error()
            raise make_client_error("InternalError", "Publish")
        self.published.append(kwargs)
        return {"MessageId": "msg-1"}


class FakeS3:
    def __init__(
        self,
        *,
        lock_mode: str = "COMPLIANCE",
        lock_days: int = 365,
        lock_enabled: bool = True,
        object_mode: str = "COMPLIANCE",
        object_days: int = 365,
        fail_put: bool = False,
        fail_put_on_event: str | None = None,
        fail_put_once_on_event: str | None = None,
        unproven_object_on_event: str | None = None,
        preexisting_keys: set[str] | None = None,
        fail_bucket_check_transport: bool = False,
        fail_put_transport: bool = False,
    ) -> None:
        """A recording S3 fake with per-event failure injection.

        Args:
            fail_put: Fail every put_object.
            fail_put_on_event: Fail put_object only for the object whose key
                ends with ``<event>.json``; other puts succeed. Used to fail a
                post-call event without failing the intent.
            fail_put_once_on_event: Fail the FIRST put_object for the object
                whose key ends with ``<event>.json``; a later put of the same
                key (a replay) succeeds. Used to drive the write-fail-then-
                replay path across invocations.
            unproven_object_on_event: Return a too-short retention on
                get_object_retention for the object whose key ends with
                ``<event>.json``; other objects prove fine. Used to drive a
                post-call retention gap.
            fail_bucket_check_transport: Raise a botocore transport error
                (BotoCoreError) from get_object_lock_configuration. Used to
                prove the bucket-check boundary fails open in notify_only/approve
                and fails closed in auto on a transport failure, not only a
                service error.
            fail_put_transport: Raise a botocore transport error from
                put_object. Used to prove the object-write boundary records a
                pending event (post-call) or fails the intent (pre-call) on a
                transport failure.
        """
        self._lock_mode = lock_mode
        self._lock_days = lock_days
        self._lock_enabled = lock_enabled
        self._object_mode = object_mode
        self._object_days = object_days
        self._fail_put = fail_put
        self._fail_put_on_event = fail_put_on_event
        self._fail_put_once_on_event = fail_put_once_on_event
        self._fail_bucket_check_transport = fail_bucket_check_transport
        self._fail_put_transport = fail_put_transport
        self._failed_once: set[str] = set()
        self._unproven_object_on_event = unproven_object_on_event
        self.put_calls: list[dict[str, Any]] = []
        self._key_by_version: dict[str, str] = {}
        # Keys already written, so a repeated If-None-Match: * PUT fails the
        # precondition (the idempotent-replay path). Seeded via preexisting_keys
        # to model "a previous invocation wrote this object".
        self._written_keys: set[str] = set(preexisting_keys or ())

    def get_object_lock_configuration(self, Bucket: str) -> dict[str, Any]:  # noqa: N803
        if self._fail_bucket_check_transport:
            raise make_transport_error()
        if not self._lock_enabled:
            return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Disabled"}}
        return {
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": self._lock_mode, "Days": self._lock_days}},
            }
        }

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs.get("Key", "")
        # Model If-None-Match: * on an Object Lock bucket: a second PUT of the
        # same key fails the precondition. Used to drive the idempotent-replay
        # path (a successful write whose DynamoDB acknowledgement later fails).
        if kwargs.get("IfNoneMatch") == "*" and key in self._written_keys:
            raise _precondition_failure("PutObject")
        if (
            self._fail_put_once_on_event
            and key.endswith(f"{self._fail_put_once_on_event}.json")
            and key not in self._failed_once
        ):
            # Fail the first attempt only; the key is not recorded as written,
            # so a later replay PUT is a fresh attempt that succeeds.
            self._failed_once.add(key)
            raise make_client_error("InternalError", "PutObject")
        if self._fail_put_transport:
            raise make_transport_error()
        if self._fail_put or (
            self._fail_put_on_event and key.endswith(f"{self._fail_put_on_event}.json")
        ):
            raise make_client_error("InternalError", "PutObject")
        self.put_calls.append(kwargs)
        self._written_keys.add(key)
        version = f"v-{len(self.put_calls)}"
        self._key_by_version[version] = key
        self._key_by_version.setdefault(key, key)
        return {"VersionId": version}

    def get_object_retention(self, **kwargs: Any) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        key = self._key_by_version.get(kwargs.get("VersionId", ""), kwargs.get("Key", ""))
        days = self._object_days
        if self._unproven_object_on_event and key.endswith(
            f"{self._unproven_object_on_event}.json"
        ):
            days = 1  # below the minimum, so the proof fails for this object only
        retain_until = datetime.fromtimestamp(
            now.timestamp() + days * 86400, tz=timezone.utc
        )
        return {"Retention": {"Mode": self._object_mode, "RetainUntilDate": retain_until}}


class FakeLogs:
    """A recording CloudWatch Logs fake for the decision log group."""

    def __init__(self, *, fail: bool = False, transport: bool = False) -> None:
        self._fail = fail
        self._transport = transport
        self.streams: set[str] = set()
        self.events: list[dict[str, Any]] = []

    def create_log_stream(self, logGroupName: str, logStreamName: str) -> dict[str, Any]:  # noqa: N803
        if logStreamName in self.streams:
            raise make_client_error("ResourceAlreadyExistsException", "CreateLogStream")
        self.streams.add(logStreamName)
        return {}

    def put_log_events(  # noqa: N803
        self, logGroupName: str, logStreamName: str, logEvents: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if self._fail:
            if self._transport:
                raise make_transport_error()
            raise make_client_error("InternalError", "PutLogEvents")
        self.events.extend(logEvents)
        return {}


class FakeTable:
    """A single-item conditional-write model of the lock table.

    ``fail_put_states`` injects a transient DynamoDB failure: a ``put_item``
    whose resulting ``state`` is in the set raises a (non-conditional) error
    once per state, modelling a lock-table write that fails after an archive
    write already failed (the dual-write-failure path). The state is removed
    from the set after it fires, so a later retry succeeds.
    """

    def __init__(
        self,
        store: dict[str, dict[str, Any]],
        client_error: type,
        fail_put_states: set[str] | None = None,
        journal: list[tuple[str, str | None, str | None]] | None = None,
    ) -> None:
        self._store = store
        self._client_error = client_error
        self._fail_put_states = fail_put_states if fail_put_states is not None else set()
        # Successful writes in order, as (operation, state, owner): ("put",
        # <new state>, <owner>) or ("delete", <deleted state>, <deleted owner>).
        # Lets a test assert intermediate lock states, not only the final store.
        self._journal = journal

    def _record(self, operation: str, item: dict[str, Any] | None) -> None:
        if self._journal is not None and item is not None:
            self._journal.append((operation, item.get("state"), item.get("owner")))

    def get_item(self, Key: dict[str, Any], ConsistentRead: bool = False) -> dict[str, Any]:  # noqa: N803
        item = self._store.get(Key["file_system_id"])
        return {"Item": dict(item)} if item is not None else {}

    def _check(
        self, condition: str | None, values: dict, existing: dict | None
    ) -> bool:
        if condition is None:
            return True
        if "attribute_not_exists(file_system_id)" in condition and "OR" in condition:
            if existing is None:
                return True
            return (
                existing.get("state") == values.get(":evaluating")
                and int(existing.get("expires_at", 0)) < int(values.get(":now", 0))
                and "pending_events" not in existing
            )
        if "#o = :owner AND #s = :expected" in condition:
            if existing is None:
                return False
            return (
                existing.get("owner") == values.get(":owner")
                and existing.get("state") == values.get(":expected")
            )
        return True

    def put_item(
        self,
        Item: dict[str, Any],  # noqa: N803
        ConditionExpression: str | None = None,  # noqa: N803
        ExpressionAttributeNames: dict | None = None,  # noqa: N803
        ExpressionAttributeValues: dict | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        existing = self._store.get(Item["file_system_id"])
        if not self._check(ConditionExpression, ExpressionAttributeValues or {}, existing):
            raise self._client_error("ConditionalCheckFailedException", "PutItem")
        state = Item.get("state")
        if state in self._fail_put_states:
            self._fail_put_states.discard(state)
            raise self._client_error("InternalServerError", "PutItem")
        self._store[Item["file_system_id"]] = dict(Item)
        self._record("put", Item)
        return {}

    def delete_item(
        self,
        Key: dict[str, Any],  # noqa: N803
        ConditionExpression: str | None = None,  # noqa: N803
        ExpressionAttributeNames: dict | None = None,  # noqa: N803
        ExpressionAttributeValues: dict | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        existing = self._store.get(Key["file_system_id"])
        if not self._check(ConditionExpression, ExpressionAttributeValues or {}, existing):
            raise self._client_error("ConditionalCheckFailedException", "DeleteItem")
        removed = self._store.pop(Key["file_system_id"], None)
        self._record("delete", removed)
        return {}


class FakeDynamoResource:
    def __init__(
        self,
        store: dict[str, dict[str, Any]],
        client_error: type,
        fail_put_states: set[str] | None = None,
        journal: list[tuple[str, str | None, str | None]] | None = None,
    ) -> None:
        self._store = store
        self._client_error = client_error
        self._fail_put_states = fail_put_states if fail_put_states is not None else set()
        self._journal = journal

    def Table(self, name: str) -> FakeTable:  # noqa: N802
        return FakeTable(self._store, self._client_error, self._fail_put_states, self._journal)


def archived(*s3_fakes: FakeS3) -> list[tuple[str, int, str, dict[str, Any]]]:
    """Every archive object written, as (correlation ID, sequence, event, body).

    Keys are ``<prefix><file-system-id>/<correlation-id>/<sequence>-<event>.json``.
    """
    objects = []
    for s3 in s3_fakes:
        for call in s3.put_calls:
            correlation_id, name = call["Key"].split("/")[-2:]
            sequence, event = name.removesuffix(".json").split("-", 1)
            objects.append((correlation_id, int(sequence), event, json.loads(call["Body"])))
    return objects


def reports(*sns_fakes: FakeSns) -> list[tuple[str, dict[str, Any]]]:
    """Every SNS report published, as (subject, decoded message body)."""
    return [
        (p["Subject"], json.loads(p["Message"])) for sns in sns_fakes for p in sns.published
    ]


def log_lines(*logs_fakes: FakeLogs) -> list[dict[str, Any]]:
    """Every decision-log line written, decoded."""
    return [json.loads(e["message"]) for logs in logs_fakes for e in logs.events]


class Loaded:
    def __init__(
        self,
        handler: Any,
        fsx: FakeFsx,
        cloudwatch: FakeCloudWatch,
        sns: FakeSns,
        s3: FakeS3,
        logs: FakeLogs,
        lock_store: dict[str, dict[str, Any]],
    ) -> None:
        self.handler = handler
        self.fsx = fsx
        self.cloudwatch = cloudwatch
        self.sns = sns
        self.s3 = s3
        self.logs = logs
        self.lock_store = lock_store
