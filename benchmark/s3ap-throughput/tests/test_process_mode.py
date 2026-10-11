"""The "process" test mode times the read leg and the parse leg separately.

These tests build small ONTAP XML in memory, run ``_run_process`` against a
FakeS3 stub that returns those bytes, and assert that the read and parse legs
are timed as separate non-negative numbers, that ``events_parsed`` equals the
number of ``<Event>`` blocks, and that the size-class summary pools both legs
and carries ``sample_count`` and ``percentile_method``.

Everything here is offline and deterministic: no real network or boto3 call,
and the end-to-end cases script ``time.perf_counter`` so the measured legs are
exact. The parser is the real one from shared/python (put on sys.path by the
tests' conftest), so the parse leg exercises genuine parsing.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

import handler

NS = 'xmlns="http://schemas.microsoft.com/win/2004/08/events/event"'


def _event_block(user: str = "u1", obj: str = "/vol/data/a.txt") -> str:
    """Return one namespaced ONTAP ``<Event>`` block the parser recognises."""
    return (
        f"<Event {NS}>"
        "<System><EventID>4663</EventID>"
        '<TimeCreated SystemTime="2026-08-07T04:00:00Z"/>'
        "<Computer>svm1</Computer></System>"
        "<EventData>"
        f'<Data Name="SubjectUserName">{user}</Data>'
        f'<Data Name="ObjectName">{obj}</Data>'
        '<Data Name="ObjectType">ReadData</Data>'
        "</EventData>"
        "</Event>"
    )


def _xml_with(n_events: int) -> bytes:
    """Return a parseable ONTAP XML document holding ``n_events`` events."""
    body = "".join(_event_block(obj=f"/vol/data/{i}.txt") for i in range(n_events))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Events>' + body + "</Events>"
    ).encode("utf-8")


class StubBody:
    """Minimal stand-in for a boto3 StreamingBody: supports .read()."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self) -> bytes:
        return self._buf.read()


class FakeS3:
    """Serves canned ListObjectsV2 / GetObject responses. No network."""

    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects
        self.get_calls: list[dict] = []

    def list_objects_v2(self, **kwargs):
        return {
            "Contents": [
                {"Key": key, "Size": len(data)} for key, data in self._objects.items()
            ]
        }

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Body": StubBody(self._objects[kwargs["Key"]])}


def _scripted_clock(pairs: list[tuple[float, float]]) -> MagicMock:
    """Return a stand-in for ``time`` replaying (read_ms, parse_ms) per iteration.

    ``_run_process`` calls perf_counter three times per iteration: start, after
    read, after parse. Each (read_ms, parse_ms) pair becomes the ticks
    ``(0, read_ms/1000, read_ms/1000 + parse_ms/1000)``.
    """
    ticks: list[float] = []
    for read_ms, parse_ms in pairs:
        ticks.extend([0.0, read_ms / 1000.0, (read_ms + parse_ms) / 1000.0])
    clock = MagicMock()
    clock.perf_counter.side_effect = iter(ticks)
    return clock


class TestLegsAreTimedSeparately:
    def test_read_and_parse_are_non_negative_and_distinct(self):
        objects = {"audit/a.xml": _xml_with(3)}
        out = handler._run_process(FakeS3(objects), "my-ap-alias", "", 10, 4, None)
        (obj,) = out["per_object"]
        assert obj["read"]["mean_ms"] >= 0.0
        assert obj["parse"]["mean_ms"] >= 0.0
        # The parse leg of real XML takes a measurable, independent amount of
        # time, so both legs carry their own summary with the full field set.
        for leg in ("read", "parse"):
            for field in ("p50_ms", "p99_ms", "mean_ms", "min_ms", "max_ms"):
                assert field in obj[leg], f"{leg}.{field}"

    def test_legs_use_their_own_scripted_timings(self):
        objects = {"audit/a.xml": _xml_with(2)}
        # Two iterations: read legs 10/20 ms, parse legs 1/3 ms.
        pairs = [(10.0, 1.0), (20.0, 3.0)]
        with patch.object(handler, "time", _scripted_clock(pairs)):
            out = handler._run_process(FakeS3(objects), "my-ap-alias", "", 10, 2, None)
        (obj,) = out["per_object"]
        assert obj["read"]["min_ms"] == 10.0
        assert obj["read"]["max_ms"] == 20.0
        assert obj["parse"]["min_ms"] == 1.0
        assert obj["parse"]["max_ms"] == 3.0
        assert obj["read_samples_ms"] == [10.0, 20.0]
        assert obj["parse_samples_ms"] == [1.0, 3.0]


class TestEventsParsed:
    @pytest.mark.parametrize("n", [1, 3, 7])
    def test_events_parsed_matches_number_of_event_blocks(self, n):
        objects = {"audit/a.xml": _xml_with(n)}
        out = handler._run_process(FakeS3(objects), "my-ap-alias", "", 10, 2, None)
        (obj,) = out["per_object"]
        assert obj["events_parsed"] == n


class TestSizeClassPoolsBothLegs:
    def test_class_pools_read_and_parse_with_counts_and_method(self):
        # Two objects in one size class, scripted so the pools differ per leg.
        objects = {"audit/a.xml": _xml_with(2), "audit/b.xml": _xml_with(2)}
        # a: reads 10/10/10, parses 1/1/1 ; b: reads 50/50/50, parses 9/9/9
        pairs = [(10.0, 1.0)] * 3 + [(50.0, 9.0)] * 3
        with patch.object(handler, "time", _scripted_clock(pairs)):
            out = handler._run_process(
                FakeS3(objects), "my-ap-alias", "", 10, 3, {"audit/a.xml": "5KB", "audit/b.xml": "5KB"}
            )
        (cls,) = out["per_size_class"]
        assert cls["size_label"] == "5KB"
        assert cls["object_count"] == 2
        assert cls["percentile_method"] == "pooled_nearest_rank"
        # Both legs pool 3 + 3 = 6 samples.
        assert cls["read"]["sample_count"] == 6
        assert cls["parse"]["sample_count"] == 6
        # Pooled read: min 10, max 50; pooled parse: min 1, max 9.
        assert cls["read"]["min_ms"] == 10.0
        assert cls["read"]["max_ms"] == 50.0
        assert cls["parse"]["min_ms"] == 1.0
        assert cls["parse"]["max_ms"] == 9.0

    def test_class_matches_summarize_latencies_over_each_pool(self):
        objects = {"audit/a.xml": _xml_with(2), "audit/b.xml": _xml_with(2)}
        pairs = [(10.0, 1.0), (30.0, 5.0), (50.0, 9.0), (70.0, 2.0)]
        with patch.object(handler, "time", _scripted_clock(pairs)):
            out = handler._run_process(
                FakeS3(objects), "my-ap-alias", "", 10, 2, {"audit/a.xml": "5KB", "audit/b.xml": "5KB"}
            )
        (cls,) = out["per_size_class"]
        expected_read = handler.summarize_latencies([10.0, 30.0, 50.0, 70.0])
        expected_parse = handler.summarize_latencies([1.0, 5.0, 9.0, 2.0])
        for field in ("p50_ms", "p99_ms", "mean_ms", "min_ms", "max_ms"):
            assert cls["read"][field] == expected_read[field], f"read.{field}"
            assert cls["parse"][field] == expected_parse[field], f"parse.{field}"

    def test_classes_pooled_separately_and_sorted_by_size(self):
        objects = {
            "audit/small.xml": _xml_with(1),
            "audit/big.xml": _xml_with(1),
        }
        out = handler._run_process(
            FakeS3(objects),
            "my-ap-alias",
            "",
            10,
            2,
            {"audit/small.xml": "5KB", "audit/big.xml": "2MB"},
        )
        labels = [c["size_label"] for c in out["per_size_class"]]
        # Both files are the same tiny size here, so the sort key is the mean
        # size in bytes; the labels are distinct and each class holds one object.
        by_label = {c["size_label"]: c for c in out["per_size_class"]}
        assert set(by_label) == {"5KB", "2MB"}
        assert by_label["5KB"]["object_count"] == 1
        assert by_label["2MB"]["object_count"] == 1
        assert labels == sorted(
            labels, key=lambda label: by_label[label]["mean_size_bytes"]
        )


class TestRouting:
    def test_lambda_handler_routes_process_and_is_json_serializable(self):
        objects = {"audit/a.xml": _xml_with(3)}
        with patch.object(handler.boto3, "client", return_value=FakeS3(objects)):
            out = handler.lambda_handler(
                {"test": "process", "bucket": "my-ap-alias", "iterations": 2}, None
            )
        json.dumps(out)
        assert out["result"]["operation"] == "ReadAndParse"
        assert out["result"]["objects_measured"] == 1
        (obj,) = out["result"]["per_object"]
        assert obj["events_parsed"] == 3

    def test_process_reads_each_key_iterations_times(self):
        objects = {"audit/a.xml": _xml_with(1), "audit/b.xml": _xml_with(1)}
        stub = FakeS3(objects)
        with patch.object(handler.boto3, "client", return_value=stub):
            handler.lambda_handler(
                {"test": "process", "bucket": "my-ap-alias", "iterations": 3}, None
            )
        assert len(stub.get_calls) == 2 * 3


class TestParserGuard:
    def test_process_raises_when_parser_missing(self):
        objects = {"audit/a.xml": _xml_with(1)}
        with patch.object(handler, "parse_audit_log", None):
            with pytest.raises(RuntimeError, match="ontap_audit_parser"):
                handler._run_process(FakeS3(objects), "my-ap-alias", "", 10, 1, None)


def _load_generator():
    """Import gen-audit-objects.py, whose filename is not a valid module name."""
    import importlib.util
    from pathlib import Path

    path = Path(handler.__file__).resolve().parent / "scripts" / "gen-audit-objects.py"
    spec = importlib.util.spec_from_file_location("gen_audit_objects", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGeneratorProducesParseableXml:
    def test_build_xml_meets_target_size_and_parses(self):
        gen = _load_generator()
        document = gen.build_xml(5 * 1024)
        encoded = document.encode("utf-8")
        assert len(encoded) >= 5 * 1024
        events = handler.parse_audit_log(encoded, "audit-5kb.xml")
        assert len(events) > 0
