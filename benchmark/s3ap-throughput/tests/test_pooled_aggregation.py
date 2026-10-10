"""Size-class percentiles come from pooled samples, not from per-object means.

Before this change ``_summarize_size_classes`` reported a class ``p50_ms`` and
``p99_ms`` as the simple mean of each object's own p50 and p99. With few
iterations per object, each per-object p99 is that object's maximum, so the
class "p99" was an average of maxima. These tests pin the pooled behaviour on
small fixed inputs where the two methods give different numbers, so they fail
against the averaging code.

Everything here is offline and deterministic. The end-to-end cases script
``time.perf_counter`` so the measured latencies are exact.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

import handler


class StubBody:
    """Minimal stand-in for a boto3 StreamingBody: supports .read()."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self) -> bytes:
        return self._buf.read()


class StubS3:
    """Serves canned ListObjectsV2 / GetObject responses. No network."""

    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects

    def list_objects_v2(self, **kwargs):
        return {"Contents": [{"Key": key, "Size": len(data)} for key, data in self._objects.items()]}

    def get_object(self, **kwargs):
        return {"Body": StubBody(self._objects[kwargs["Key"]])}

# Two objects in one size class. Per-object nearest-rank percentiles:
#   A: p50 = 10, p99 = 100 (the maximum of 5 samples)
#   B: p50 = 20, p99 = 20
# Averaging per-object values gives p50 = 15 and p99 = 60.
# Pooling all 10 samples (sorted: 10 x4, 20 x5, 100) gives p50 = 20 and p99 = 100.
SAMPLES_A = [10.0, 10.0, 10.0, 10.0, 100.0]
SAMPLES_B = [20.0, 20.0, 20.0, 20.0, 20.0]


def _per_object(key: str, label: str, samples: list[float]) -> dict:
    """Build a per-object result the way ``_run_get`` does."""
    summary = handler.summarize_latencies(samples)
    return {
        "key": key,
        "size_bytes": 1024,
        "size_label": label,
        "iterations": len(samples),
        "throughput_mbps": handler.throughput_mbps(1024, summary["mean_ms"]),
        "samples_ms": list(samples),
        **summary,
    }


def _scripted_clock(latencies_ms: list[float]) -> MagicMock:
    """Return a stand-in for the ``time`` module whose perf_counter replays latencies.

    ``_run_get`` calls perf_counter twice per iteration (start, then end), so
    each latency becomes a (0.0, latency / 1000) pair.
    """
    ticks: list[float] = []
    for latency in latencies_ms:
        ticks.extend([0.0, latency / 1000.0])
    clock = MagicMock()
    clock.perf_counter.side_effect = iter(ticks)
    return clock


def _run_get_scripted(objects: dict[str, bytes], iterations: int, latencies_ms: list[float]):
    stub = StubS3(objects)
    with patch.object(handler, "time", _scripted_clock(latencies_ms)):
        return handler._run_get(stub, "my-ap-alias", "", 100, iterations, None)


class TestSizeClassPercentilesArePooled:
    def test_class_p50_and_p99_come_from_pooled_samples(self):
        per_object = [
            _per_object("a", "1KB", SAMPLES_A),
            _per_object("b", "1KB", SAMPLES_B),
        ]
        (cls,) = handler._summarize_size_classes(per_object)
        # Pooled: p50 = sorted[4] = 20.0, p99 = max = 100.0.
        # The averaging code returns 15.0 and 60.0 here.
        assert cls["p50_ms"] == 20.0
        assert cls["p99_ms"] == 100.0

    def test_class_matches_summarize_latencies_over_the_pool(self):
        per_object = [
            _per_object("a", "1KB", SAMPLES_A),
            _per_object("b", "1KB", SAMPLES_B),
        ]
        (cls,) = handler._summarize_size_classes(per_object)
        expected = handler.summarize_latencies(SAMPLES_A + SAMPLES_B)
        for field in ("p50_ms", "p99_ms", "mean_ms", "min_ms", "max_ms"):
            assert cls[field] == expected[field], field

    def test_class_reports_sample_count_and_method(self):
        per_object = [
            _per_object("a", "1KB", SAMPLES_A),
            _per_object("b", "1KB", SAMPLES_B),
        ]
        (cls,) = handler._summarize_size_classes(per_object)
        assert cls["sample_count"] == 10
        assert cls["percentile_method"] == "pooled_nearest_rank"
        assert handler.PERCENTILE_METHOD == "pooled_nearest_rank"

    def test_existing_class_fields_keep_their_names(self):
        per_object = [
            _per_object("a", "1KB", SAMPLES_A),
            _per_object("b", "1KB", SAMPLES_B),
        ]
        (cls,) = handler._summarize_size_classes(per_object)
        for field in (
            "size_label",
            "object_count",
            "mean_size_bytes",
            "p50_ms",
            "p99_ms",
            "mean_ms",
            "mean_throughput_mbps",
        ):
            assert field in cls, field
        assert cls["object_count"] == 2
        assert cls["size_label"] == "1KB"

    def test_classes_are_pooled_separately(self):
        per_object = [
            _per_object("a", "1KB", SAMPLES_A),
            _per_object("b", "1KB", SAMPLES_B),
            _per_object("c", "1MB", [200.0, 300.0]),
        ]
        by_label = {c["size_label"]: c for c in handler._summarize_size_classes(per_object)}
        assert by_label["1KB"]["sample_count"] == 10
        assert by_label["1MB"]["sample_count"] == 2
        assert by_label["1MB"]["max_ms"] == 300.0

    def test_pooled_mean_weights_every_sample_equally(self):
        # Object A has 4 samples, object B has 1. The mean of per-object means
        # is (10 + 100) / 2 = 55; the pooled mean over 5 samples is 28.
        per_object = [
            _per_object("a", "1KB", [10.0, 10.0, 10.0, 10.0]),
            _per_object("b", "1KB", [100.0]),
        ]
        (cls,) = handler._summarize_size_classes(per_object)
        assert cls["mean_ms"] == 28.0
        assert cls["sample_count"] == 5


class TestPerObjectResultsKeepTheirShape:
    def test_per_object_fields_unchanged_and_samples_added(self):
        out = _run_get_scripted(
            {"audit/a.json": b"x" * 1024, "audit/b.json": b"y" * 1024},
            5,
            SAMPLES_A + SAMPLES_B,
        )
        obj_a, obj_b = out["per_object"]
        # Every field the earlier result files carry is still present.
        for field in (
            "key",
            "size_bytes",
            "size_label",
            "iterations",
            "throughput_mbps",
            "p50_ms",
            "p99_ms",
            "mean_ms",
            "min_ms",
            "max_ms",
        ):
            assert field in obj_a, field
        assert obj_a["iterations"] == 5
        assert obj_a["p50_ms"] == 10.0
        assert obj_a["p99_ms"] == 100.0
        assert obj_b["p50_ms"] == 20.0
        assert obj_a["samples_ms"] == SAMPLES_A
        assert obj_b["samples_ms"] == SAMPLES_B

    def test_top_level_fields_unchanged(self):
        out = _run_get_scripted({"audit/a.json": b"x" * 1024}, 5, SAMPLES_A)
        assert set(out) == {"operation", "objects_measured", "per_object", "per_size_class"}
        assert out["operation"] == "GetObject"
        assert out["objects_measured"] == 1


class TestEndToEndPooling:
    def test_pooled_class_through_run_get(self):
        out = _run_get_scripted(
            {"audit/a.json": b"x" * 1024, "audit/b.json": b"y" * 1024},
            5,
            SAMPLES_A + SAMPLES_B,
        )
        (cls,) = out["per_size_class"]
        assert cls["size_label"] == "1KB"
        assert cls["p50_ms"] == 20.0
        assert cls["p99_ms"] == 100.0
        assert cls["mean_ms"] == 24.0
        assert cls["min_ms"] == 10.0
        assert cls["max_ms"] == 100.0
        assert cls["sample_count"] == 10
        assert cls["percentile_method"] == "pooled_nearest_rank"

    def test_through_lambda_handler_is_json_serializable(self):
        stub = StubS3({"audit/a.json": b"x" * 1024, "audit/b.json": b"y" * 1024})
        with patch.object(handler.boto3, "client", return_value=stub):
            out = handler.lambda_handler(
                {"test": "get", "bucket": "my-ap-alias", "iterations": 3}, None
            )
        json.dumps(out)
        (cls,) = out["result"]["per_size_class"]
        assert cls["sample_count"] == 6


class TestSampleCap:
    def test_cap_is_a_named_positive_integer(self):
        assert isinstance(handler.MAX_SAMPLES_PER_OBJECT, int)
        assert handler.MAX_SAMPLES_PER_OBJECT >= 1

    def test_bounded_samples_keeps_the_first_n_in_order(self, monkeypatch):
        monkeypatch.setattr(handler, "MAX_SAMPLES_PER_OBJECT", 3)
        assert handler.bounded_samples([5.0, 4.0, 3.0, 2.0, 1.0]) == [5.0, 4.0, 3.0]

    def test_bounded_samples_rounds_to_three_decimals(self):
        assert handler.bounded_samples([1.23456, 2.0]) == [1.235, 2.0]

    def test_bounded_samples_returns_a_new_list(self):
        source = [1.0, 2.0]
        result = handler.bounded_samples(source)
        result.append(9.0)
        assert source == [1.0, 2.0]

    def test_object_samples_are_capped_but_its_own_stats_use_every_iteration(
        self, monkeypatch
    ):
        monkeypatch.setattr(handler, "MAX_SAMPLES_PER_OBJECT", 3)
        out = _run_get_scripted(
            {"audit/a.json": b"x" * 1024}, 5, [1.0, 2.0, 3.0, 4.0, 5.0]
        )
        (obj,) = out["per_object"]
        assert obj["samples_ms"] == [1.0, 2.0, 3.0]
        assert obj["iterations"] == 5
        assert obj["max_ms"] == 5.0
        assert obj["mean_ms"] == 3.0
        # The class pools only the samples that were kept.
        (cls,) = out["per_size_class"]
        assert cls["sample_count"] == 3
        assert cls["max_ms"] == 3.0

    def test_real_cap_bounds_the_response(self):
        iterations = handler.MAX_SAMPLES_PER_OBJECT + 7
        stub = StubS3({"audit/a.json": b"x" * 1024})
        with patch.object(handler.boto3, "client", return_value=stub):
            out = handler.lambda_handler(
                {"test": "get", "bucket": "my-ap-alias", "iterations": iterations}, None
            )
        (obj,) = out["result"]["per_object"]
        assert len(obj["samples_ms"]) == handler.MAX_SAMPLES_PER_OBJECT
        assert obj["iterations"] == iterations


class TestP99IsTheMaximumBelowOneHundredSamples:
    """The README states: below 100 pooled samples, nearest-rank p99 is the maximum."""

    @pytest.mark.parametrize("n", [1, 2, 10, 50, 99])
    def test_p99_equals_max_below_100(self, n):
        values = [float(i) for i in range(n)]
        assert handler.summarize_latencies(values)["p99_ms"] == values[-1]

    def test_p99_is_second_largest_at_exactly_100(self):
        values = [float(i) for i in range(100)]
        assert handler.summarize_latencies(values)["p99_ms"] == 98.0

    @pytest.mark.parametrize("n", [101, 150, 199])
    def test_p99_is_below_max_from_100_up(self, n):
        values = [float(i) for i in range(n)]
        assert handler.summarize_latencies(values)["p99_ms"] < values[-1]
