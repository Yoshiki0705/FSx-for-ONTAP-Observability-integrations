"""Fixture that imports the handler with recording fakes for the six boto3 clients.

The six are fsx, cloudwatch, sns, s3, dynamodb and logs. Helpers and fakes live
in ssd_test_support.py, which this file puts on sys.path (under
--import-mode=importlib a test module cannot import from conftest).
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ssd_test_support import (  # noqa: E402
    BASE_ENV,
    MODULES,
    FakeCloudWatch,
    FakeDynamoResource,
    FakeFsx,
    FakeLogs,
    FakeS3,
    FakeSns,
    Loaded,
    alarm_response,
)


def _conditional_error(code: str, operation: str) -> Exception:
    """Build a real botocore ClientError for the FakeTable conditional check."""
    import botocore.exceptions

    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {}}, operation
    )


@pytest.fixture
def load_handler(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Loaded]]:
    def _load(
        *,
        fsx: FakeFsx,
        alarms: dict[str, Any] | None = None,
        s3: FakeS3 | None = None,
        sns: FakeSns | None = None,
        logs: FakeLogs | None = None,
        lock_store: dict[str, dict[str, Any]] | None = None,
        fail_put_states: set[str] | None = None,
        env: dict[str, str] | None = None,
        cloudwatch: FakeCloudWatch | None = None,
        journal: list[tuple[str, str | None, str | None]] | None = None,
    ) -> Loaded:
        # AGGREGATE_NAMES is optional (absent on first generation), so it is
        # cleared even when BASE_ENV does not set it.
        for key in (*BASE_ENV, "AGGREGATE_NAMES", *(env or {})):
            monkeypatch.delenv(key, raising=False)
        for key, value in {**BASE_ENV, **(env or {})}.items():
            monkeypatch.setenv(key, value)

        if cloudwatch is None:
            cloudwatch = FakeCloudWatch(alarms if alarms is not None else alarm_response("ALARM"))
        sns_client = sns if sns is not None else FakeSns()
        s3_client = s3 if s3 is not None else FakeS3()
        logs_client = logs if logs is not None else FakeLogs()
        store = lock_store if lock_store is not None else {}

        clients: dict[str, Any] = {
            "fsx": fsx,
            "cloudwatch": cloudwatch,
            "sns": sns_client,
            "s3": s3_client,
            "logs": logs_client,
        }

        fail_states = fail_put_states if fail_put_states is not None else set()
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = lambda name, **_: clients[name]  # type: ignore[attr-defined]
        fake_boto3.resource = lambda name, **_: FakeDynamoResource(  # type: ignore[attr-defined]
            store, _conditional_error, fail_states, journal
        )
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

        for name in MODULES:
            monkeypatch.delitem(sys.modules, name, raising=False)
        handler = importlib.import_module("ssd_auto_increase_handler")
        handler._CLIENTS = None  # noqa: SLF001
        return Loaded(handler, fsx, cloudwatch, sns_client, s3_client, logs_client, store)

    yield _load
    for name in MODULES:
        sys.modules.pop(name, None)
