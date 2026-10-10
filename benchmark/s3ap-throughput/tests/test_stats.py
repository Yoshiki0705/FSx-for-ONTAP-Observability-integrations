"""Unit tests for the pure statistics helpers (no boto3, offline, deterministic).

The percentile math is the part most likely to go subtly wrong: the reference
script in docs used ``sorted(xs)[int(n * 0.99)]``, which indexes out of range
once ``int(n * 0.99) == n``. These tests pin the safe nearest-rank behaviour
including the small-N edges (len 1/2/3) that the doc script's GetObject branch
also had to clamp.
"""

import math

import pytest

import handler


class TestPercentile:
    def test_single_value(self):
        assert handler.percentile([42.0], 0.99) == 42.0
        assert handler.percentile([42.0], 0.5) == 42.0

    def test_two_values(self):
        # ceil(0.99*2)-1 = 1 -> last; ceil(0.5*2)-1 = 0 -> first.
        assert handler.percentile([1.0, 2.0], 0.99) == 2.0
        assert handler.percentile([1.0, 2.0], 0.5) == 1.0

    def test_three_values(self):
        values = [1.0, 2.0, 3.0]
        assert handler.percentile(values, 0.99) == 3.0
        assert handler.percentile(values, 0.5) == 2.0  # ceil(1.5)-1 = 1

    def test_p99_never_indexes_out_of_range_for_large_n(self):
        # The exact off-by-one the doc script risked: int(100 * 0.99) == 99 is
        # fine, but int(101 * 0.99) would be 99 too and sorted()[int(n*0.99)]
        # raises at n where int(n*0.99) == n. Our method clamps to n-1.
        for n in range(1, 500):
            values = [float(i) for i in range(n)]
            result = handler.percentile(values, 0.99)
            assert result == values[min(n - 1, math.ceil(0.99 * n) - 1)]
            assert result <= values[-1]

    def test_p50_matches_nearest_rank(self):
        values = [10.0, 20.0, 30.0, 40.0]
        # ceil(0.5*4)-1 = 1 -> 20.0
        assert handler.percentile(values, 0.5) == 20.0

    def test_fraction_zero_clamps_to_first(self):
        assert handler.percentile([5.0, 6.0, 7.0], 0.0) == 5.0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            handler.percentile([], 0.5)


class TestSummarizeLatencies:
    def test_summary_fields_and_rounding(self):
        summary = handler.summarize_latencies([5.0, 1.0, 3.0, 2.0, 4.0])
        assert summary["min_ms"] == 1.0
        assert summary["max_ms"] == 5.0
        assert summary["mean_ms"] == 3.0
        assert summary["p50_ms"] == 3.0  # ceil(0.5*5)-1 = 2 -> sorted[2] = 3.0
        assert summary["p99_ms"] == 5.0  # ceil(0.99*5)-1 = 4 -> sorted[4] = 5.0

    def test_single_sample(self):
        summary = handler.summarize_latencies([7.5])
        assert summary == {
            "p50_ms": 7.5,
            "p99_ms": 7.5,
            "mean_ms": 7.5,
            "min_ms": 7.5,
            "max_ms": 7.5,
        }

    def test_unsorted_input_is_handled(self):
        # Caller passes raw samples; summarize must sort internally.
        summary = handler.summarize_latencies([9.0, 1.0, 5.0])
        assert summary["min_ms"] == 1.0
        assert summary["max_ms"] == 9.0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            handler.summarize_latencies([])


class TestThroughputMbps:
    def test_one_megabyte_in_one_second(self):
        # 1 MiB read in 1000 ms -> 1 MB/s.
        assert handler.throughput_mbps(1024 * 1024, 1000.0) == 1.0

    def test_five_megabytes_in_hundred_ms(self):
        # 5 MiB in 100 ms -> 50 MB/s.
        assert handler.throughput_mbps(5 * 1024 * 1024, 100.0) == 50.0

    def test_zero_latency_returns_zero(self):
        assert handler.throughput_mbps(1024 * 1024, 0.0) == 0.0

    def test_negative_latency_returns_zero(self):
        assert handler.throughput_mbps(1024 * 1024, -1.0) == 0.0

    def test_small_object_rounds_to_two_decimals(self):
        # 1 KiB in 30 ms -> ~0.03 MB/s.
        assert handler.throughput_mbps(1024, 30.0) == 0.03


class TestSizeLabel:
    @pytest.mark.parametrize(
        ("size", "label"),
        [
            (0, "0B"),
            (-5, "0B"),
            (512, "512B"),
            (1024, "1KB"),
            (100 * 1024, "100KB"),
            (1024 * 1024, "1MB"),
            (5 * 1024 * 1024, "5MB"),
        ],
    )
    def test_rounded_labels(self, size, label):
        assert handler._size_label(size) == label
