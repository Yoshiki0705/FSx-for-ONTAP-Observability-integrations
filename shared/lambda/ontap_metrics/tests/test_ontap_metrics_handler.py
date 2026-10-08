"""Terraform handler: collector isolation, heartbeats, auth handling, retries.

The handler's failure signalling is the point of these tests. A collector
failure must not raise (the heartbeat alarm reports it), an ONTAP 401/403
must not be repeated within a run (basic-auth failures can lock the account),
and a heartbeat that cannot be published must raise (Lambda Errors and DLQ).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from ontap_test_support import (
    FILE_SYSTEM_ID,
    SOURCE_DIR,
    Resp,
    page,
    quota_record,
    relationship,
    route,
)

QTREE_PAGES = [page([quota_record("vol_a", "q1", 50, 100)])]
SNAP_PAGES = [page([relationship(1)])]


def _heartbeat(lam, namespace: str, collector: str) -> float:
    [d] = [
        d for d in lam.by_name("CollectorSucceeded", namespace)
        if {"Name": "Collector", "Value": collector} in d["Dimensions"]
    ]
    assert d["Dimensions"] == [
        {"Name": "FileSystemId", "Value": FILE_SYSTEM_ID},
        {"Name": "Collector", "Value": collector},
    ]
    assert d["Unit"] == "Count"
    return d["Value"]


def test_both_collectors_succeed_with_heartbeats(load_modules) -> None:
    lam = load_modules(route(quota=QTREE_PAGES, snapmirror=SNAP_PAGES))
    result = lam.run()
    assert _heartbeat(lam, "FSxONTAP/Qtree", "qtree") == 1.0
    assert _heartbeat(lam, "FSxONTAP/SnapMirror", "snapmirror") == 1.0
    assert result["collectors"]["qtree"]["succeeded"] is True
    assert result["collectors"]["snapmirror"]["succeeded"] is True
    # The qtree collector emits the same per-SVM datums as the CFN path.
    [mx] = lam.by_name("QtreeQuotaUsedPercentMax", "FSxONTAP/Qtree")
    assert mx["Value"] == pytest.approx(50.0)
    assert len(lam.by_name("QtreeQuotaReportTruncated", "FSxONTAP/Qtree")) == 1


def test_qtree_failure_does_not_stop_snapmirror(load_modules, caplog) -> None:
    lam = load_modules(route(quota=lambda _n: Resp(500, {}), snapmirror=SNAP_PAGES))
    with caplog.at_level("ERROR"):
        result = lam.run()
    assert _heartbeat(lam, "FSxONTAP/Qtree", "qtree") == 0.0
    assert _heartbeat(lam, "FSxONTAP/SnapMirror", "snapmirror") == 1.0
    assert result["collectors"]["qtree"]["succeeded"] is False
    assert "HTTP 500" in result["collectors"]["qtree"]["error"]
    assert any("Collector qtree failed" in r.getMessage() for r in caplog.records)


def test_auth_failure_stops_further_ontap_calls_and_rereads_secret(load_modules, caplog) -> None:
    lam = load_modules(route(quota=lambda _n: Resp(401, {}), snapmirror=SNAP_PAGES))
    with caplog.at_level("ERROR"):
        result = lam.run()
    assert len(lam.urls) == 1, lam.urls
    assert _heartbeat(lam, "FSxONTAP/Qtree", "qtree") == 0.0
    assert _heartbeat(lam, "FSxONTAP/SnapMirror", "snapmirror") == 0.0
    assert result["collectors"]["snapmirror"]["succeeded"] is False
    assert "skipped" in result["collectors"]["snapmirror"]["error"]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    assert lam.sm.get_secret_value.call_count == 1

    # The cached credentials were dropped, so the next invocation reads the
    # secret again (a rotated password is picked up without a cold start).
    lam.run()
    assert lam.sm.get_secret_value.call_count == 2


def test_forbidden_is_treated_as_auth_failure(load_modules) -> None:
    lam = load_modules(route(quota=QTREE_PAGES, snapmirror=lambda _n: Resp(403, {})),
                       env={"COLLECTORS": "snapmirror,qtree"})
    lam.run()
    assert len(lam.urls) == 1
    assert _heartbeat(lam, "FSxONTAP/Qtree", "qtree") == 0.0


def test_credentials_ttl_expiry_rereads_secret(load_modules, monkeypatch) -> None:
    lam = load_modules(route(quota=QTREE_PAGES * 3, snapmirror=SNAP_PAGES * 3))
    clock = {"now": 1000.0}
    monkeypatch.setattr(lam.poller.time, "monotonic", lambda: clock["now"])
    lam.run()
    assert lam.sm.get_secret_value.call_count == 1
    clock["now"] += 299.0
    lam.run()
    assert lam.sm.get_secret_value.call_count == 1
    clock["now"] += 2.0
    lam.run()
    assert lam.sm.get_secret_value.call_count == 2


def test_heartbeat_publish_failure_raises(load_modules) -> None:
    lam = load_modules(route(quota=QTREE_PAGES, snapmirror=SNAP_PAGES))

    def put(**kwargs):
        if any(d["MetricName"] == "CollectorSucceeded" for d in kwargs["MetricData"]):
            raise ConnectionError("no route to monitoring endpoint")

    lam.cw.put_metric_data.side_effect = put
    with pytest.raises(ConnectionError):
        lam.run()


def test_snapmirror_only_needs_no_svm_and_reads_no_quota(load_modules) -> None:
    lam = load_modules(route(snapmirror=SNAP_PAGES),
                       env={"COLLECTORS": "snapmirror", "SVM_NAME": None})
    result = lam.run()
    assert all("/api/storage/quota/reports" not in u for u in lam.urls)
    assert list(result["collectors"]) == ["snapmirror"]
    assert lam.by_name("CollectorSucceeded", "FSxONTAP/Qtree") == []


def test_qtree_without_svm_name_fails_before_any_request(load_modules) -> None:
    lam = load_modules(route(), env={"COLLECTORS": "qtree", "SVM_NAME": None})
    with pytest.raises(ValueError, match="SVM_NAME"):
        lam.run()
    assert lam.urls == []


@pytest.mark.parametrize("collectors", ["qtree,volume", "", " , "])
def test_unknown_or_empty_collectors_raise_before_any_request(load_modules, collectors) -> None:
    lam = load_modules(route(), env={"COLLECTORS": collectors})
    with pytest.raises(ValueError):
        lam.run()
    assert lam.urls == []
    lam.cw.put_metric_data.assert_not_called()


def test_every_request_carries_the_retry_policy(load_modules) -> None:
    lam = load_modules(route(quota=QTREE_PAGES, snapmirror=SNAP_PAGES))
    lam.run()
    assert len(lam.requests) == 2
    for _url, kwargs in lam.requests:
        retry = kwargs["retries"]
        assert retry.total == 3
        assert retry.redirect == 0
        assert {429, 500, 502, 503, 504} <= set(retry.status_forcelist)
        assert 401 not in retry.status_forcelist and 403 not in retry.status_forcelist
        assert "GET" in retry.allowed_methods
        assert retry.backoff_factor > 0
        assert kwargs["timeout"] == 30.0


def test_secret_is_never_read_from_the_environment() -> None:
    """Only the secret ARN is in the environment; no password key is read."""
    keys: set[str] = set()
    for path in sorted(SOURCE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.Subscript):
                target = node.value, node.slice
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "get" and node.args:
                target = node.func.value, node.args[0]
            if target is None:
                continue
            obj, key = target
            if (isinstance(obj, ast.Attribute) and obj.attr == "environ"
                    and isinstance(key, ast.Constant) and isinstance(key.value, str)):
                keys.add(key.value)
    assert "ONTAP_CREDENTIALS_SECRET_ARN" in keys, keys  # the scan found something
    assert not [k for k in keys if re.search(r"(?i)pass|secret_value|token", k)], keys
    assert keys <= {
        "ONTAP_MGMT_IP", "ONTAP_CREDENTIALS_SECRET_ARN", "FILE_SYSTEM_ID", "COLLECTORS",
        "SVM_NAME", "METRIC_NAMESPACE", "SNAPMIRROR_MAX_RELATIONSHIPS",
        "CREDENTIALS_CACHE_TTL_SECONDS", "CA_CERT_PATH",
    }, keys


def test_source_uses_urllib3_not_requests() -> None:
    for path in sorted(Path(SOURCE_DIR).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert "requests" not in imported, path.name
