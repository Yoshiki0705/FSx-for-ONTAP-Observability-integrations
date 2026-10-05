"""Exercise the inline qtree quota poller in shared/templates/qtree-quota-monitor.yaml.

Why this exists
---------------
The poller lives only in the inline ``ZipFile`` body of QuotaMonitorFunction, so
nothing executed it. Two defects survived because of that. QtreeQuotaAlarm
selected ``QtreeQuotaUsedPercent`` with only the ``SvmName`` dimension, while the
Lambda published that metric only with ``SvmName``, ``VolumeName`` and
``QtreeName``. CloudWatch treats each dimension set as a separate metric, so the
alarm matched no emitted series. The poller also read a single page of 200
records and silently ignored the rest.

These tests extract the inline source from the template, run it with boto3 and
urllib3 replaced by recording fakes (no network), and check the template-level
contract between what the Lambda emits and what the alarm reads. The membership
test has a negative control: the pre-fix alarm definition must not be in the
emitted set, otherwise the check would pass vacuously.
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import urllib3
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / "shared/templates/qtree-quota-monitor.yaml"

MGMT_IP = "198.51.100.10"
SVM = "svm-prod-01"
SECRET_ARN = (
    "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:ontap-admin-XXXXXX"
)
PER_QTREE = ("QtreeQuotaUsedPercent", "QtreeQuotaUsedBytes", "QtreeQuotaLimitBytes")


# --------------------------------------------------------------------------
# Template loading
# --------------------------------------------------------------------------


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that keeps CloudFormation tags as {"Tag": value}, unresolved."""


def _construct_tag(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> dict[str, Any]:
    tag = suffix if suffix else node.tag.lstrip("!")
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {tag: value}


_CfnLoader.add_multi_constructor("!", _construct_tag)


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)  # noqa: S506


@pytest.fixture(scope="module")
def source(template: dict[str, Any]) -> str:
    code = template["Resources"]["QuotaMonitorFunction"]["Properties"]["Code"]["ZipFile"]
    assert "def lambda_handler" in code, (
        "ZipFile extraction returned no lambda_handler; every test below would be vacuous"
    )
    return code


# --------------------------------------------------------------------------
# Executing the inline Lambda with fakes
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self.data = json.dumps(payload).encode()


def _record(volume: str, qtree: str, used: int, limit: int) -> dict[str, Any]:
    return {
        "volume": {"name": volume},
        "qtree": {"name": qtree},
        "space": {"used": {"total": used}, "hard_limit": limit},
    }


def _page(records: list[dict[str, Any]], next_href: str | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {"records": records, "num_records": len(records)}
    if next_href is not None:
        page["_links"] = {"next": {"href": next_href}}
    return page


class Loaded:
    def __init__(self, ns: dict[str, Any], pool: MagicMock, cw: MagicMock, urls: list[str]):
        self.ns = ns
        self.pool = pool
        self.cw = cw
        self.urls = urls

    def run(self) -> dict[str, Any]:
        return self.ns["lambda_handler"]({}, None)

    def datums(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for call in self.cw.put_metric_data.call_args_list:
            out.extend(call.kwargs["MetricData"])
        return out

    def by_name(self, name: str) -> list[dict[str, Any]]:
        return [d for d in self.datums() if d["MetricName"] == name]

    def emitted(self) -> set[tuple[str, str, frozenset[str]]]:
        return {
            (
                call.kwargs["Namespace"],
                d["MetricName"],
                frozenset(dim["Name"] for dim in d["Dimensions"]),
            )
            for call in self.cw.put_metric_data.call_args_list
            for d in call.kwargs["MetricData"]
        }


@pytest.fixture
def load_lambda(
    source: str, template: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Loaded]:
    env = template["Resources"]["QuotaMonitorFunction"]["Properties"]["Environment"]["Variables"]

    def _load(
        responder: Callable[[int, str], _Resp],
        *,
        ca_cert_path: str | None = None,
    ) -> Loaded:
        monkeypatch.setenv("ONTAP_MGMT_IP", MGMT_IP)
        monkeypatch.setenv("ONTAP_CREDENTIALS_SECRET_ARN", SECRET_ARN)
        monkeypatch.setenv("SVM_NAME", SVM)
        monkeypatch.setenv("METRIC_NAMESPACE", env["METRIC_NAMESPACE"])
        if ca_cert_path is None:
            monkeypatch.delenv("CA_CERT_PATH", raising=False)
        else:
            monkeypatch.setenv("CA_CERT_PATH", ca_cert_path)

        cw = MagicMock(name="cloudwatch")
        sm = MagicMock(name="secretsmanager")
        sm.get_secret_value.return_value = {
            "SecretString": json.dumps({"username": "fsxadmin", "password": "placeholder"})
        }
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = lambda name, **_: {"cloudwatch": cw, "secretsmanager": sm}[name]  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

        urls: list[str] = []

        def _request(method: str, url: str, **_: Any) -> _Resp:
            urls.append(url)
            return responder(len(urls), url)

        http = MagicMock(name="http")
        http.request.side_effect = _request
        pool = MagicMock(name="PoolManager", return_value=http)
        monkeypatch.setattr(urllib3, "PoolManager", pool)

        ns: dict[str, Any] = {"__name__": "qtree_quota_monitor_inline"}
        exec(compile(source, "<QuotaMonitorFunction>", "exec"), ns)  # noqa: S102
        return Loaded(ns, pool, cw, urls)

    return _load


def _pages(pages: list[dict[str, Any]]) -> Callable[[int, str], _Resp]:
    return lambda n, _url: _Resp(200, pages[n - 1])


SAMPLE = [
    _record("vol_a", "q1", 50, 100),  # 50 %
    _record("vol_a", "q2", 93, 100),  # 93 % (max)
    _record("vol_b", "q3", 10, 100),  # 10 %
    _record("vol_b", "q4", 99, 0),  # no hard limit -> skipped
    _record("vol_b", "", 99, 100),  # unnamed -> skipped
]


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------


def test_follows_next_links_and_stops_when_absent(load_lambda) -> None:
    href2 = "/api/storage/quota/reports?start.uuid=page2&max_records=200"
    href3 = "/api/storage/quota/reports?start.uuid=page3&max_records=200"
    lam = load_lambda(_pages([
        _page([_record("vol_a", "q1", 1, 100)], href2),
        _page([_record("vol_a", "q2", 2, 100)], href3),
        _page([_record("vol_a", "q3", 3, 100)]),
    ]))
    result = lam.run()

    assert len(lam.urls) == 3
    assert lam.urls[0].startswith(f"https://{MGMT_IP}/api/storage/quota/reports?svm.name={SVM}")
    assert "max_records=200" in lam.urls[0]
    assert lam.urls[1] == f"https://{MGMT_IP}{href2}"
    assert lam.urls[2] == f"https://{MGMT_IP}{href3}"
    assert result["pages_read"] == 3
    assert result["truncated"] is False
    published = {d["Dimensions"][2]["Value"] for d in lam.by_name("QtreeQuotaUsedPercent")}
    assert published == {"q1", "q2", "q3"}


def test_page_cap_warns_and_reports_truncation(load_lambda, caplog) -> None:
    def always_next(n: int, _url: str) -> _Resp:
        return _Resp(200, _page(
            [_record("vol_a", f"q{n}", 1, 100)],
            f"/api/storage/quota/reports?start.uuid=page{n + 1}",
        ))

    lam = load_lambda(always_next)
    with caplog.at_level("WARNING"):
        result = lam.run()

    assert len(lam.urls) == 50
    assert result["truncated"] is True
    assert result["pages_read"] == 50
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("truncated" in w and "50-page cap" in w and SVM in w for w in warnings), warnings
    [flag] = lam.by_name("QtreeQuotaReportTruncated")
    assert flag["Value"] == 1.0


def test_normal_run_reports_not_truncated(load_lambda) -> None:
    lam = load_lambda(_pages([_page(SAMPLE)]))
    assert lam.run()["truncated"] is False
    [flag] = lam.by_name("QtreeQuotaReportTruncated")
    assert flag["Value"] == 0.0
    assert [d["Name"] for d in flag["Dimensions"]] == ["SvmName"]


def test_exactly_fifty_pages_without_a_further_link_is_not_truncated(load_lambda) -> None:
    def fifty(n: int, _url: str) -> _Resp:
        href = None if n == 50 else f"/api/storage/quota/reports?start.uuid=page{n + 1}"
        return _Resp(200, _page([_record("vol_a", f"q{n}", 1, 100)], href))

    lam = load_lambda(fifty)
    result = lam.run()
    assert len(lam.urls) == 50
    assert result["truncated"] is False
    [flag] = lam.by_name("QtreeQuotaReportTruncated")
    assert flag["Value"] == 0.0


def test_next_link_to_another_host_is_refused(load_lambda) -> None:
    lam = load_lambda(_pages([
        _page(SAMPLE, "https://198.51.100.99/api/storage/quota/reports?start.uuid=x"),
    ]))
    with pytest.raises(RuntimeError, match="Refusing non-ONTAP API path"):
        lam.run()
    assert len(lam.urls) == 1
    assert all("198.51.100.99" not in u for u in lam.urls)


def test_http_error_raises(load_lambda) -> None:
    lam = load_lambda(lambda _n, _url: _Resp(401, {}))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        lam.run()
    lam.cw.put_metric_data.assert_not_called()


# --------------------------------------------------------------------------
# Metric shape
# --------------------------------------------------------------------------


def test_max_metric_is_the_max_across_usable_records_with_svm_only(load_lambda) -> None:
    lam = load_lambda(_pages([_page(SAMPLE)]))
    result = lam.run()

    [datum] = lam.by_name("QtreeQuotaUsedPercentMax")
    assert datum["Value"] == pytest.approx(93.0)
    assert result["max_used_percent"] == pytest.approx(93.0)
    assert datum["Dimensions"] == [{"Name": "SvmName", "Value": SVM}]
    assert datum["Unit"] == "Percent"


def test_per_qtree_series_keep_three_dimensions(load_lambda) -> None:
    lam = load_lambda(_pages([_page(SAMPLE)]))
    lam.run()
    for name in PER_QTREE:
        datums = lam.by_name(name)
        assert len(datums) == 3, name  # q4 (no limit) and the unnamed one are skipped
        for d in datums:
            assert {dim["Name"] for dim in d["Dimensions"]} == {
                "SvmName", "VolumeName", "QtreeName",
            }, name


def test_put_metric_data_batches_never_exceed_twenty(load_lambda) -> None:
    records = [_record("vol_a", f"q{i}", i, 100) for i in range(17)]
    lam = load_lambda(_pages([_page(records)]))
    lam.run()
    sizes = [len(c.kwargs["MetricData"]) for c in lam.cw.put_metric_data.call_args_list]
    # 17 x 3 per-qtree + Max + Truncated = 53
    assert sizes == [20, 20, 13]


def test_no_usable_records_publishes_no_max_but_still_reports_truncation(load_lambda) -> None:
    lam = load_lambda(_pages([_page([_record("vol_a", "q1", 5, 0)])]))
    result = lam.run()
    assert lam.by_name("QtreeQuotaUsedPercentMax") == []
    assert result["max_used_percent"] is None
    [flag] = lam.by_name("QtreeQuotaReportTruncated")
    assert flag["Value"] == 0.0


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------


def test_ca_cert_path_enables_verification(load_lambda, caplog) -> None:
    with caplog.at_level("WARNING"):
        lam = load_lambda(_pages([_page(SAMPLE)]), ca_cert_path="/opt/certs/ontap-ca.pem")
    lam.pool.assert_called_once_with(cert_reqs="CERT_REQUIRED", ca_certs="/opt/certs/ontap-ca.pem")
    assert not [r for r in caplog.records if "TLS" in r.getMessage()]


def test_no_ca_cert_path_falls_back_to_cert_none_with_warning(load_lambda, caplog) -> None:
    with caplog.at_level("WARNING"):
        lam = load_lambda(_pages([_page(SAMPLE)]))
    lam.pool.assert_called_once_with(cert_reqs="CERT_NONE")
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("CERT_NONE" in w and "PoC" in w for w in warnings), warnings


# --------------------------------------------------------------------------
# Template-level contract
# --------------------------------------------------------------------------


def _alarm_key(alarm_props: dict[str, Any]) -> tuple[str, str, frozenset[str]]:
    return (
        alarm_props["Namespace"],
        alarm_props["MetricName"],
        frozenset(d["Name"] for d in alarm_props["Dimensions"]),
    )


def test_alarm_reads_a_series_the_lambda_emits(template, load_lambda) -> None:
    lam = load_lambda(_pages([_page(SAMPLE)]))
    lam.run()
    emitted = lam.emitted()
    alarm = template["Resources"]["QtreeQuotaAlarm"]["Properties"]

    assert _alarm_key(alarm) in emitted, (
        f"QtreeQuotaAlarm reads {_alarm_key(alarm)}, which the Lambda never emits: {emitted}"
    )
    # Negative control: the pre-fix definition must fail the same membership
    # test, otherwise the assertion above proves nothing.
    old = {**alarm, "MetricName": "QtreeQuotaUsedPercent"}
    assert _alarm_key(old) not in emitted


def test_alarm_settings_and_namespace_agree(template) -> None:
    res = template["Resources"]
    alarm = res["QtreeQuotaAlarm"]["Properties"]
    assert alarm["TreatMissingData"] == "missing"
    assert alarm["Statistic"] == "Maximum"

    env = res["QuotaMonitorFunction"]["Properties"]["Environment"]["Variables"]
    statements = res["QuotaMonitorRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    [publish] = [s for s in statements if s["Sid"] == "CloudWatchPublish"]
    condition_ns = publish["Condition"]["StringEquals"]["cloudwatch:namespace"]
    assert alarm["Namespace"] == env["METRIC_NAMESPACE"] == condition_ns


def test_tls_wiring_is_optional(template) -> None:
    params = template["Parameters"]
    assert params["CaCertPath"]["Default"] == ""
    assert params["CaCertLayerArn"]["Default"] == ""
    assert "HasCaCertLayer" in template["Conditions"]

    fn = template["Resources"]["QuotaMonitorFunction"]["Properties"]
    layers = fn["Layers"]
    assert list(layers) == ["If"] and layers["If"][0] == "HasCaCertLayer"
    assert fn["Environment"]["Variables"]["CA_CERT_PATH"] == {"Ref": "CaCertPath"}


def test_display_name_and_timeout_budget(template) -> None:
    display = template["Resources"]["QuotaAlarmTopic"]["Properties"]["DisplayName"]["Sub"]
    assert display.startswith("FSx for ONTAP ")
    fixed = display.replace("${SvmName}", "")
    assert len(fixed) + template["Parameters"]["SvmName"]["MaxLength"] <= 100

    fn = template["Resources"]["QuotaMonitorFunction"]["Properties"]
    assert fn["Timeout"] >= 300
