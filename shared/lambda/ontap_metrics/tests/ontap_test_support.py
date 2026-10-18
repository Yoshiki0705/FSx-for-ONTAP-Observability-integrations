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
MODULES = (
    "ontap_metrics_handler",
    "snapmirror_collector",
    "qos_poller",
    "qtree_quota_poller",
)

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
        self.qos = sys.modules["qos_poller"]
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
    qos_schema: list[dict[str, Any]] | Callable[[int], Resp] | None = None,
    qos_workloads: list[dict[str, Any]] | Callable[[int], Resp] | None = None,
    qos_rows: list[dict[str, Any]] | Callable[[int], Resp] | None = None,
) -> Callable[[int, str], Resp]:
    """Answer each ONTAP endpoint from its own list of page payloads.

    Each argument is a list of page payloads (served in order) or a callable
    taking the 1-based request count for that endpoint. The three QoS
    endpoints are served separately: the counter-schema request, the
    counter-manager rows, and the workload map.
    """
    counts = {"quota": 0, "snapmirror": 0, "qos_schema": 0, "qos_workloads": 0, "qos_rows": 0}

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
        # Rows before the schema path: the rows URL also contains the schema
        # path as a prefix (/api/cluster/counter/tables/qos/rows).
        if "/api/cluster/counter/tables/qos/rows" in url:
            return _serve("qos_rows", qos_rows)
        if "/api/cluster/counter/tables/qos" in url:
            return _serve("qos_schema", qos_schema)
        if "/api/storage/qos/workloads" in url:
            return _serve("qos_workloads", qos_workloads)
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


# --- QoS counter-manager helpers ------------------------------------------


def qos_schema_page(
    schemas: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a counter-table schema payload (GET .../tables/qos?fields=...).

    Args:
        schemas: Counter name -> ``{"type", "unit"}``. Defaults to a rate-typed
            ``ops``/``total_data``/``read_ops``/``write_ops`` and an
            average-typed ``latency``.

    Returns:
        A single counter-table record carrying ``counter_schemas``.
    """
    if schemas is None:
        schemas = {
            "ops": {"type": "rate", "unit": "per_sec"},
            "total_data": {"type": "rate", "unit": "b_per_sec"},
            "read_ops": {"type": "rate", "unit": "per_sec"},
            "write_ops": {"type": "rate", "unit": "per_sec"},
            "latency": {"type": "average", "unit": "microsec"},
        }
    return {
        "records": [
            {
                "name": "qos",
                "counter_schemas": [
                    {"name": name, "type": meta["type"], "unit": meta["unit"]}
                    for name, meta in schemas.items()
                ],
            }
        ],
        "num_records": 1,
    }


def qos_row(
    policy: str,
    uuid: str = "00000000-0000-0000-0000-000000000001",
    *,
    counters: dict[str, float] | None = None,
    with_uuid_property: bool = True,
) -> dict[str, Any]:
    """Build one counter-manager row with id ``<policy>:<uuid>``.

    Args:
        policy: Policy-group name (and the id prefix).
        uuid: Policy-group UUID (the id suffix).
        counters: Counter name -> value. Defaults to ops/total_data/latency
            plus the read/write ops split.
        with_uuid_property: Whether the row also carries a
            ``policy_group_uuid`` property alongside ``policy_group_name``.

    Returns:
        A row with ``id``, ``counters`` and ``properties`` lists.
    """
    if counters is None:
        counters = {
            "ops": 1500.0,
            "total_data": 5_000_000.0,
            "latency": 820.0,
            "read_ops": 1000.0,
            "write_ops": 500.0,
        }
    properties = [{"name": "policy_group_name", "value": policy}]
    if with_uuid_property:
        properties.append({"name": "policy_group_uuid", "value": uuid})
    return {
        "id": f"{policy}:{uuid}",
        "counters": [{"name": name, "value": value} for name, value in counters.items()],
        "properties": properties,
    }


def qos_workload(
    policy: str,
    svm: str,
    *,
    name: str | None = None,
    volume: str | None = None,
) -> dict[str, Any]:
    """Build one QoS workload record (policy -> SVM mapping).

    Args:
        policy: Policy-group name.
        svm: SVM name resolved for the policy group.
        name: Workload name (defaults to ``wl-<policy>``).
        volume: Volume name, when the workload names one.

    Returns:
        A workload record with ``name``, ``policy.name`` and ``svm.name``.
    """
    rec: dict[str, Any] = {
        "name": name or f"wl-{policy}",
        "policy": {"name": policy},
        "svm": {"name": svm},
    }
    if volume is not None:
        rec["volume"] = {"name": volume}
    return rec
