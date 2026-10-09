"""Helpers for the ONTAP metrics tests: fakes, payload builders, constants.

conftest.py puts this directory on sys.path, because under
--import-mode=importlib a test module cannot import from conftest.

The load_modules fixture in conftest.py replaces boto3 with a module whose
clients are MagicMocks and urllib3.PoolManager with a factory whose
``request`` records every call and answers from a responder (see ``route``).
The three source modules are popped from sys.modules before every load, so
module-level state (the PoolManager, the credential cache) starts fresh.
No network and no AWS calls. Payloads use placeholders only.
"""
from __future__ import annotations

import json
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock


SOURCE_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MODULES = ("ontap_metrics_handler", "snapmirror_collector", "qtree_quota_poller")

MGMT_IP = "198.51.100.10"
FILE_SYSTEM_ID = "fs-0123456789abcdef0"
SVM = "svm-prod-01"
SECRET_ARN = "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ontap-monitor-XXXXXX"


class Resp:
    """Minimal stand-in for urllib3.BaseHTTPResponse."""

    def __init__(self, status: int, payload: dict[str, Any] | None = None) -> None:
        self.status = status
        self.data = json.dumps(payload).encode() if payload is not None else b""


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Loaded:
    """Handles to the imported modules and the fakes behind them."""

    def __init__(
        self,
        handler: types.ModuleType,
        cw: MagicMock,
        sm: MagicMock,
        pool: MagicMock,
        requests: list[tuple[str, dict[str, Any]]],
    ) -> None:
        self.handler = handler
        self.poller = sys.modules["qtree_quota_poller"]
        self.snapmirror = sys.modules["snapmirror_collector"]
        self.cw = cw
        self.sm = sm
        self.pool = pool
        self.requests = requests

    @property
    def urls(self) -> list[str]:
        return [url for url, _ in self.requests]

    def run(self) -> dict[str, Any]:
        return self.handler.lambda_handler({}, None)

    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (c.kwargs["Namespace"], d)
            for c in self.cw.put_metric_data.call_args_list
            for d in c.kwargs["MetricData"]
        ]

    def datums(self, namespace: str | None = None) -> list[dict[str, Any]]:
        return [d for ns, d in self.calls() if namespace is None or ns == namespace]

    def by_name(self, name: str, namespace: str | None = None) -> list[dict[str, Any]]:
        return [d for d in self.datums(namespace) if d["MetricName"] == name]

    def emitted(self) -> set[tuple[str, str, frozenset[str]]]:
        return {
            (ns, d["MetricName"], frozenset(dim["Name"] for dim in d["Dimensions"]))
            for ns, d in self.calls()
        }


def route(
    *,
    quota: list[dict[str, Any]] | Callable[[int], Resp] | None = None,
    snapmirror: list[dict[str, Any]] | Callable[[int], Resp] | None = None,
) -> Callable[[int, str], Resp]:
    """Answer quota-report and SnapMirror requests from separate page lists.

    Each argument is a list of page payloads (served in order) or a callable
    taking the 1-based request count for that endpoint.
    """
    counts = {"quota": 0, "snapmirror": 0}

    def _serve(kind: str, source: Any) -> Resp:
        counts[kind] += 1
        if source is None:
            raise AssertionError(f"unexpected {kind} request")
        if callable(source):
            return source(counts[kind])
        return Resp(200, source[counts[kind] - 1])

    def _responder(_n: int, url: str) -> Resp:
        if "/api/storage/quota/reports" in url:
            return _serve("quota", quota)
        if "/api/snapmirror/relationships" in url:
            return _serve("snapmirror", snapmirror)
        raise AssertionError(f"unexpected URL {url}")

    return _responder


def quota_record(volume: str, qtree: str, used: int, limit: int) -> dict[str, Any]:
    return {
        "volume": {"name": volume},
        "qtree": {"name": qtree},
        "space": {"used": {"total": used}, "hard_limit": limit},
    }


def page(records: list[dict[str, Any]], next_href: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"records": records, "num_records": len(records)}
    if next_href is not None:
        body["_links"] = {"next": {"href": next_href}}
    return body


def relationship(
    n: int,
    *,
    healthy: Any = True,
    lag: str | None = "PT1H",
    state: str = "snapmirrored",
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "uuid": f"00000000-0000-0000-0000-{n:012d}",
        "state": state,
        "source": {"path": f"svm1:vol{n}"},
        "destination": {"path": f"svm1_dr:vol{n}_dst"},
    }
    if healthy is not None:
        rec["healthy"] = healthy
    if lag is not None:
        rec["lag_time"] = lag
    return rec
