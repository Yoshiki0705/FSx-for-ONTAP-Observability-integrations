"""Recompute the published read, parse, and read+parse p50 from raw/.

Reads only the four proc-*.json files committed next to this script. Run from
any directory: python3 analyze_processing_rate.py

Each raw file is the benchmark Lambda's `process` response for one size class.
Every object carries its own `read_samples_ms` and `parse_samples_ms` (one entry
per iteration, in read order). The published class p50 is the median by
nearest rank over the pooled samples of all objects in that class, the same
definition as the tool's `percentile_method` "pooled_nearest_rank". This script
pools the samples itself and recomputes, so the figures can be checked by
running it rather than by trusting the `per_size_class` block.

The per-invocation duration is read+parse p50 times the file count, concurrency 1
(sequential), delivery excluded. File count comes from the published scenarios,
not from the sample count (the 2 MB class was measured on 10 files x3, below).
"""
from __future__ import annotations

import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# (file, size label, events/file, file count of the published scenario)
CELLS = [
    ("proc-5KB.json", "5 KB", 11, 10),
    ("proc-200KB-50.json", "200 KB (50 files)", 412, 50),
    ("proc-200KB-100.json", "200 KB (100 files)", 412, 100),
    ("proc-2MB.json", "2 MB", 4212, 100),
]


def p50_nearest_rank(values: list[float]) -> float:
    s = sorted(values)
    i = max(0, min(len(s) - 1, math.ceil(0.5 * len(s)) - 1))
    return s[i]


def main() -> None:
    print("Read+parse p50 recomputed from pooled samples in raw/")
    print("(read and parse are per-object; file count does not change them)\n")
    header = (
        f"{'cell':<22}{'bytes':>10}{'events':>8}"
        f"{'read p50':>11}{'parse p50':>12}{'r+p p50':>11}{'samples':>9}"
    )
    print(header)
    print("-" * len(header))
    for fname, label, events, _files in CELLS:
        with open(os.path.join(HERE, "raw", fname)) as f:
            result = json.load(f)["result"]
        reads = [x for o in result["per_object"] for x in o["read_samples_ms"]]
        parses = [x for o in result["per_object"] for x in o["parse_samples_ms"]]
        sc = result["per_size_class"][0]
        read_p50 = p50_nearest_rank(reads)
        parse_p50 = p50_nearest_rank(parses)
        rp_p50 = read_p50 + parse_p50
        # Cross-check against the value the tool stored in the file.
        assert abs(read_p50 - sc["read"]["p50_ms"]) < 1e-6, (fname, "read")
        assert abs(parse_p50 - sc["parse"]["p50_ms"]) < 1e-6, (fname, "parse")
        print(
            f"{label:<22}{sc['mean_size_bytes']:>10}{events:>8}"
            f"{read_p50:>9.1f}ms{parse_p50:>10.1f}ms{rp_p50:>9.1f}ms"
            f"{len(reads):>9}"
        )

    print("\nPer-invocation duration = read+parse p50 x file count, concurrency 1")
    print("(delivery to a vendor is modelled, not measured, and excluded here)\n")
    dur_header = f"{'scenario':<30}{'files':>7}{'r+p p50':>11}{'duration':>12}"
    print(dur_header)
    print("-" * len(dur_header))
    scenarios = [
        ("10 small files (5 KB)", "proc-5KB.json", 10),
        ("50 medium files (200 KB)", "proc-200KB-50.json", 50),
        ("100 medium files (200 KB)", "proc-200KB-100.json", 100),
        ("100 large files (2 MB)", "proc-2MB.json", 100),
    ]
    for label, fname, files in scenarios:
        with open(os.path.join(HERE, "raw", fname)) as f:
            result = json.load(f)["result"]
        reads = [x for o in result["per_object"] for x in o["read_samples_ms"]]
        parses = [x for o in result["per_object"] for x in o["parse_samples_ms"]]
        rp_p50 = p50_nearest_rank(reads) + p50_nearest_rank(parses)
        duration_s = rp_p50 * files / 1000.0
        print(f"{label:<30}{files:>7}{rp_p50:>9.1f}ms{duration_s:>10.1f} s")


if __name__ == "__main__":
    main()
