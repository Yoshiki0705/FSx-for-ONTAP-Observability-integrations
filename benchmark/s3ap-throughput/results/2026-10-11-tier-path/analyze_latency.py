"""Per-volume and per-read latency figures for the tiering path experiment.

Reads only the files committed next to this script (raw/, invocations.jsonl). Run from any
directory: python3 analyze_latency.py

Groups, from the benchmark run ID (the invocation tag):
  x2-pre-<volume>-r<n>           reads before the switch (volume s2, s3; r1 to r4),
                                 pooled over the four invocations per volume
  x2-cold-<volume>               the first read after tiering, one invocation per volume
  x2-k1first-c<cohort>-<size>    K1 first reads, per cohort and pooled over the four cohorts
  x2-k1re-c<cohort>-<size>       K1 re-reads, per cohort
  x2-coldre-m<30|60>-<volume>    re-reads of s2, s3, s4 about 30 and 60 minutes later

For each group and size class the script prints n, p50, p90, smallest, and largest, by nearest
rank over the samples: sorted(samples)[ceil(p * n) - 1]. For a single invocation the p50 is
compared with the tool's own per_size_class p50_ms, and a line says whether they agree.

Within a cold/ invocation the objects are read in the order the listing returns them (key
order); that order is printed once.
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE_LABEL = {"64KB": "64 KiB", "1MB": "1 MiB", "5MB": "5 MiB"}
SIZE_ORDER = ["64KB", "1MB", "5MB"]
SIZE_TAG = {"s64k": "64KB", "s1m": "1MB", "s5m": "5MB"}
SLOW_MS = 300.0  # a chosen cut-off for counting slow 5 MiB samples; not a decision rule


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[max(0, min(len(s) - 1, math.ceil(p * len(s)) - 1))]


def load(tag: str) -> dict:
    with open(os.path.join(HERE, "raw", f"{tag}.json")) as f:
        return json.load(f)["result"]


def by_size(result: dict) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for o in result["per_object"]:
        out.setdefault(o["size_label"], []).extend(o["samples_ms"])
    return out


def line(name: str, size: str, vals: list[float]) -> str:
    slow = f" n>{SLOW_MS:.0f}ms={sum(v > SLOW_MS for v in vals)}" if size == "5MB" else ""
    return (f"{name:34s} {SIZE_LABEL[size]:7s} n={len(vals):4d}  p50={pct(vals, 0.5):7.1f}  "
            f"p90={pct(vals, 0.9):7.1f}  min={min(vals):7.1f}  max={max(vals):7.1f}{slow}")


def main() -> int:
    with open(os.path.join(HERE, "invocations.jsonl")) as f:
        invs = [json.loads(x) for x in f]
    tags = [i["tag"] for i in invs]
    checked = mismatched = 0

    def single(tag: str) -> None:
        nonlocal checked, mismatched
        res = load(tag)
        samples = by_size(res)
        tool = {c["size_label"]: c["p50_ms"] for c in res["per_size_class"]}
        for size in SIZE_ORDER:
            if size not in samples:
                continue
            print(line(tag, size, samples[size]))
            checked += 1
            if abs(pct(samples[size], 0.5) - tool[size]) > 0.001:
                mismatched += 1
                print(f"  p50 differs from the tool's per_size_class value {tool[size]}")

    print("== read order inside a cold/ invocation (key order, one read each)")
    order = [o["key"].rsplit("/", 1)[0] for o in load("x2-cold-s2")["per_object"]]
    runs = [(k, order.count(k)) for k in dict.fromkeys(order)]
    print("  " + ", then ".join(f"{n} x {k}" for k, n in runs))

    print("\n== reads before the switch: x2-pre-<volume>-r1 to r4 pooled per volume (policy NONE, SSD)")
    for vol in ("s2", "s3"):
        pooled: dict[str, list[float]] = {}
        for t in tags:
            if t.startswith(f"x2-pre-{vol}-r"):
                for size, vals in by_size(load(t)).items():
                    pooled.setdefault(size, []).extend(vals)
        for size in SIZE_ORDER:
            print(line(f"x2-pre-{vol} (r1 to r4 pooled)", size, pooled[size]))

    print("\n== first reads after the switch, one invocation per volume")
    for t in ("x2-cold-s2", "x2-cold-s3", "x2-cold-s4", "x2-cold-s3p"):
        single(t)

    print("\n== K1 first reads, per cohort (one invocation per cohort and size)")
    for c in (1, 2, 3, 4):
        for s in ("s64k", "s1m", "s5m"):
            single(f"x2-k1first-c{c}-{s}")
    print("\n== K1 first reads, four cohorts pooled per size (n = 40)")
    for s in ("s64k", "s1m", "s5m"):
        vals: list[float] = []
        for c in (1, 2, 3, 4):
            vals += by_size(load(f"x2-k1first-c{c}-{s}"))[SIZE_TAG[s]]
        print(line("x2-k1first (c1 to c4 pooled)", SIZE_TAG[s], vals))

    print("\n== K1 re-reads, per cohort (c1 about +3, c2 about +8.4, c3 +30, c4 +60 min)")
    for c in (1, 2, 3, 4):
        for s in ("s64k", "s1m", "s5m"):
            single(f"x2-k1re-c{c}-{s}")
    print("\n5 MiB K1 re-read samples, sorted (ms), to show the spread behind the p50:")
    for c in (1, 2, 3, 4):
        vals = by_size(load(f"x2-k1re-c{c}-s5m"))["5MB"]
        print(f"  c{c}: " + " ".join(f"{v:.1f}" for v in sorted(vals)))

    print("\n== re-reads of s2, s3, s4 about 30 and 60 minutes after the first read")
    for m in (30, 60):
        for v in ("s2", "s3", "s4"):
            single(f"x2-coldre-m{m}-{v}")

    print(f"\nsingle-invocation p50 values checked against the tool's per_size_class p50_ms: "
          f"{checked} checked, {mismatched} differ")
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
