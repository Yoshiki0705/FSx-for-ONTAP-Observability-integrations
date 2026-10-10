"""Handler event-routing tests with a deterministic, offline stub S3 client.

No real AWS calls: boto3.client is patched to return a StubS3 that records the
Bucket argument (to prove both an alias and an ARN flow through unchanged) and
serves canned ListObjectsV2 / GetObject responses.
"""

import io
from unittest.mock import patch

import pytest

import handler


class StubBody:
    """Minimal stand-in for a boto3 StreamingBody: supports .read()."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self) -> bytes:
        return self._buf.read()


class StubS3:
    """Records calls and returns canned responses. No network, deterministic."""

    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def list_objects_v2(self, **kwargs):
        self.list_calls.append(kwargs)
        return {
            "Contents": [{"Key": key, "Size": len(data)} for key, data in self._objects.items()]
        }

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        key = kwargs["Key"]
        return {"Body": StubBody(self._objects[key])}


@pytest.fixture
def two_objects():
    return {
        "audit/a.json": b"x" * 1024,          # 1 KiB -> "1KB"
        "audit/b.json": b"y" * (1024 * 1024),  # 1 MiB -> "1MB"
    }


def _invoke(event, stub):
    with patch.object(handler.boto3, "client", return_value=stub):
        return handler.lambda_handler(event, None)


class TestEnvelope:
    def test_echoes_benchmark_run_id(self, two_objects):
        stub = StubS3(two_objects)
        out = _invoke(
            {"test": "list", "bucket": "my-ap-alias", "benchmark_run_id": "bench-fixed-id"},
            stub,
        )
        assert out["benchmark_run_id"] == "bench-fixed-id"

    def test_generates_run_id_when_absent(self, two_objects):
        stub = StubS3(two_objects)
        out = _invoke({"test": "list", "bucket": "my-ap-alias"}, stub)
        assert out["benchmark_run_id"].startswith("bench-")
        assert "timestamp_utc" in out
        assert "region" in out


class TestListRouting:
    def test_list_runs_requested_iterations(self, two_objects):
        stub = StubS3(two_objects)
        out = _invoke(
            {"test": "list", "bucket": "my-ap-alias", "prefix": "audit/", "iterations": 7},
            stub,
        )
        assert len(stub.list_calls) == 7
        assert stub.get_calls == []
        result = out["result"]
        assert result["operation"] == "ListObjectsV2"
        assert result["iterations"] == 7
        for field in ("p50_ms", "p99_ms", "mean_ms", "min_ms", "max_ms"):
            assert field in result

    def test_alias_passed_through_as_bucket(self, two_objects):
        stub = StubS3(two_objects)
        _invoke({"test": "list", "bucket": "my-ap-alias"}, stub)
        assert stub.list_calls[0]["Bucket"] == "my-ap-alias"

    def test_arn_passed_through_as_bucket(self, two_objects):
        stub = StubS3(two_objects)
        arn = "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap"
        _invoke({"test": "list", "bucket": arn}, stub)
        assert stub.list_calls[0]["Bucket"] == arn


class TestGetRouting:
    def test_get_lists_then_reads_each_key(self, two_objects):
        stub = StubS3(two_objects)
        out = _invoke(
            {"test": "get", "bucket": "my-ap-alias", "prefix": "audit/", "iterations": 3},
            stub,
        )
        # One list call to discover keys, then iterations per key.
        assert len(stub.list_calls) == 1
        assert len(stub.get_calls) == 2 * 3
        result = out["result"]
        assert result["operation"] == "GetObject"
        assert result["objects_measured"] == 2
        assert len(result["per_object"]) == 2
        for obj in result["per_object"]:
            assert obj["iterations"] == 3
            assert obj["throughput_mbps"] >= 0
            assert "size_label" in obj

    def test_per_size_class_summary_groups_by_label(self, two_objects):
        stub = StubS3(two_objects)
        out = _invoke({"test": "get", "bucket": "my-ap-alias", "iterations": 2}, stub)
        classes = {c["size_label"]: c for c in out["result"]["per_size_class"]}
        assert set(classes) == {"1KB", "1MB"}
        assert classes["1KB"]["object_count"] == 1
        assert classes["1MB"]["object_count"] == 1
        # Sorted ascending by size.
        labels = [c["size_label"] for c in out["result"]["per_size_class"]]
        assert labels == ["1KB", "1MB"]

    def test_caller_supplied_size_labels_win(self, two_objects):
        stub = StubS3(two_objects)
        out = _invoke(
            {
                "test": "get",
                "bucket": "my-ap-alias",
                "iterations": 1,
                "size_labels": {"audit/a.json": "small-audit", "audit/b.json": "large-audit"},
            },
            stub,
        )
        labels = {o["key"]: o["size_label"] for o in out["result"]["per_object"]}
        assert labels == {"audit/a.json": "small-audit", "audit/b.json": "large-audit"}

    def test_directory_markers_excluded(self):
        stub = StubS3({"audit/": b"", "audit/real.json": b"data"})
        out = _invoke({"test": "get", "bucket": "my-ap-alias", "iterations": 1}, stub)
        keys = [o["key"] for o in out["result"]["per_object"]]
        assert keys == ["audit/real.json"]


class TestErrors:
    def test_missing_bucket_raises(self):
        with pytest.raises(ValueError, match="bucket"):
            _invoke({"test": "list"}, StubS3({}))

    def test_unknown_test_raises(self):
        with pytest.raises(ValueError, match="unknown test"):
            _invoke({"test": "delete", "bucket": "my-ap-alias"}, StubS3({}))
