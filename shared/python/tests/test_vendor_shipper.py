"""Tests for the shared vendor shipping helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vendor_shipper as vs  # noqa: E402


def resp(status, headers=None, data=b""):
    r = MagicMock()
    r.status = status
    r.headers = headers or {}
    r.data = data
    return r


@pytest.fixture(autouse=True)
def no_sleep():
    """Retry backoff would otherwise make the suite take tens of seconds."""
    with patch.object(vs.time, "sleep") as s:
        yield s


class TestSecretCache:
    def test_plain_string_secret(self):
        client = MagicMock()
        client.get_secret_value.return_value = {"SecretString": "abc123"}
        assert vs.SecretCache("arn", client=client).get() == "abc123"

    def test_json_secret_first_matching_key(self):
        client = MagicMock()
        client.get_secret_value.return_value = {
            "SecretString": json.dumps({"api_key": "k1", "token": "k2"})
        }
        assert vs.SecretCache("arn", client=client).get() == "k1"

    def test_json_secret_falls_through_to_second_key(self):
        client = MagicMock()
        client.get_secret_value.return_value = {
            "SecretString": json.dumps({"token": "k2"})
        }
        assert vs.SecretCache("arn", client=client).get() == "k2"

    def test_custom_json_keys(self):
        client = MagicMock()
        client.get_secret_value.return_value = {
            "SecretString": json.dumps({"license_key": "lk"})
        }
        cache = vs.SecretCache("arn", json_keys=("license_key",), client=client)
        assert cache.get() == "lk"

    def test_json_without_known_key_returns_raw(self):
        raw = json.dumps({"unexpected": "v"})
        client = MagicMock()
        client.get_secret_value.return_value = {"SecretString": raw}
        assert vs.SecretCache("arn", client=client).get() == raw

    def test_fetched_once_per_context(self):
        client = MagicMock()
        client.get_secret_value.return_value = {"SecretString": "abc"}
        cache = vs.SecretCache("arn", client=client)
        cache.get(); cache.get(); cache.get()
        assert client.get_secret_value.call_count == 1

    def test_clear_forces_refetch(self):
        client = MagicMock()
        client.get_secret_value.return_value = {"SecretString": "abc"}
        cache = vs.SecretCache("arn", client=client)
        cache.get(); cache.clear(); cache.get()
        assert client.get_secret_value.call_count == 2

    def test_client_error_propagates(self):
        """A credential failure must fail the invocation, not return success."""
        from botocore.exceptions import ClientError

        client = MagicMock()
        client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
            "GetSecretValue",
        )
        with pytest.raises(ClientError):
            vs.SecretCache("arn", client=client).get()


class TestPostWithRetry:
    def test_success_first_attempt(self):
        http = MagicMock(); http.request.return_value = resp(202)
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is True
        assert http.request.call_count == 1

    def test_retries_on_5xx_then_succeeds(self):
        http = MagicMock(); http.request.side_effect = [resp(503), resp(200)]
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is True
        assert http.request.call_count == 2

    def test_5xx_exhausted_returns_false(self):
        http = MagicMock(); http.request.return_value = resp(500)
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is False
        assert http.request.call_count == 3

    def test_429_honours_retry_after(self, no_sleep):
        http = MagicMock()
        http.request.side_effect = [resp(429, {"Retry-After": "7"}), resp(200)]
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is True
        assert no_sleep.call_args_list[0][0][0] == 7

    def test_429_without_header_uses_backoff(self, no_sleep):
        http = MagicMock(); http.request.side_effect = [resp(429), resp(200)]
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is True
        assert no_sleep.call_args_list[0][0][0] == 2

    def test_429_with_garbage_header_uses_backoff(self, no_sleep):
        http = MagicMock()
        http.request.side_effect = [resp(429, {"Retry-After": "soon"}), resp(200)]
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is True
        assert no_sleep.call_args_list[0][0][0] == 2

    def test_4xx_is_not_retried(self):
        """A bad token or body fails identically on retry; fail fast instead."""
        http = MagicMock(); http.request.return_value = resp(401, data=b"denied")
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is False
        assert http.request.call_count == 1

    def test_transport_error_retried_then_succeeds(self):
        http = MagicMock()
        http.request.side_effect = [urllib3.exceptions.HTTPError("boom"), resp(200)]
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is True

    def test_transport_error_exhausted_returns_false(self):
        http = MagicMock()
        http.request.side_effect = urllib3.exceptions.HTTPError("boom")
        assert vs.post_with_retry(http, "http://x", b"{}", {}) is False
        assert http.request.call_count == 3

    def test_exponential_backoff_doubles(self, no_sleep):
        http = MagicMock(); http.request.return_value = resp(500)
        vs.post_with_retry(http, "http://x", b"{}", {})
        assert [c[0][0] for c in no_sleep.call_args_list] == [2, 4]

    def test_no_sleep_after_final_attempt(self, no_sleep):
        """Sleeping after the last attempt only adds latency to a known failure."""
        http = MagicMock(); http.request.return_value = resp(500)
        vs.post_with_retry(http, "http://x", b"{}", {}, max_retries=1)
        no_sleep.assert_not_called()


class TestBatchBySize:
    def test_empty_input(self):
        assert vs.batch_by_size([], 100) == []

    def test_single_batch_when_under_limits(self):
        assert len(vs.batch_by_size([{"a": 1}, {"b": 2}], 10_000)) == 1

    def test_splits_on_byte_limit(self):
        items = [{"k": "x" * 100} for _ in range(10)]
        batches = vs.batch_by_size(items, 300)
        assert len(batches) > 1
        assert sum(len(b) for b in batches) == 10

    def test_splits_on_item_limit(self):
        batches = vs.batch_by_size([{"i": i} for i in range(10)], 10_000, max_items=3)
        assert [len(b) for b in batches] == [3, 3, 3, 1]

    def test_oversized_item_kept_as_its_own_batch(self):
        """Dropping it would silently lose an audit record."""
        big = {"k": "x" * 5000}
        batches = vs.batch_by_size([{"a": 1}, big], 100)
        assert sum(len(b) for b in batches) == 2
        assert [big] in batches

    def test_no_item_is_lost(self):
        items = [{"i": i, "pad": "y" * 50} for i in range(37)]
        batches = vs.batch_by_size(items, 400, max_items=5)
        flat = [x for b in batches for x in b]
        assert flat == items

    def test_custom_sizer(self):
        batches = vs.batch_by_size(["a", "b", "c"], 2, sizer=lambda _: 1)
        assert [len(b) for b in batches] == [2, 1]
