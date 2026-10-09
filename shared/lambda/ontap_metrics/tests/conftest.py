"""Fixture that imports the ONTAP metrics modules with recording fakes.

Modelled on ``load_lambda`` in shared/python/tests/test_qtree_quota_monitor.py.
Helpers live in ontap_test_support.py, which this file puts on sys.path.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ontap_test_support import (  # noqa: E402
    FILE_SYSTEM_ID,
    MGMT_IP,
    MODULES,
    SECRET_ARN,
    SOURCE_DIR,
    SVM,
    Loaded,
    Resp,
)


@pytest.fixture
def load_modules(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Loaded]]:
    def _load(
        responder: Callable[[int, str], Resp],
        *,
        env: dict[str, str | None] | None = None,
    ) -> Loaded:
        base = {
            "ONTAP_MGMT_IP": MGMT_IP,
            "ONTAP_CREDENTIALS_SECRET_ARN": SECRET_ARN,
            "FILE_SYSTEM_ID": FILE_SYSTEM_ID,
            "COLLECTORS": "qtree,snapmirror",
            "SVM_NAME": SVM,
            "SNAPMIRROR_MAX_RELATIONSHIPS": "100",
            "CREDENTIALS_CACHE_TTL_SECONDS": "300",
        }
        for key in (*base, "CA_CERT_PATH"):
            monkeypatch.delenv(key, raising=False)
        for key, value in {**base, **(env or {})}.items():
            if value is not None:
                monkeypatch.setenv(key, value)

        cw = MagicMock(name="cloudwatch")
        sm = MagicMock(name="secretsmanager")
        sm.get_secret_value.return_value = {
            "SecretString": json.dumps({"username": "monitor", "password": "example-only"})
        }
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = lambda name, **_: {"cloudwatch": cw, "secretsmanager": sm}[name]  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

        requests: list[tuple[str, dict[str, Any]]] = []

        def _request(method: str, url: str, **kwargs: Any) -> Resp:
            assert method == "GET"
            requests.append((url, kwargs))
            return responder(len(requests), url)

        http = MagicMock(name="http")
        http.request.side_effect = _request
        pool = MagicMock(name="PoolManager", return_value=http)
        monkeypatch.setattr(urllib3, "PoolManager", pool)

        monkeypatch.syspath_prepend(str(SOURCE_DIR))
        for name in MODULES:
            monkeypatch.delitem(sys.modules, name, raising=False)
        handler = importlib.import_module("ontap_metrics_handler")
        return Loaded(handler, cw, sm, pool, requests)

    yield _load
    for name in MODULES:
        sys.modules.pop(name, None)


