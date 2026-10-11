"""Per-volume and per-read latency figures for the third tiering run.

Reads only the files committed next to this script (raw/, invocations.jsonl). Run from any
directory: python3 analyze_latency.py

Groups, from the benchmark run ID (the invocation tag):
  x3-warm-<volume>        warm-up of the 64 KiB warm/ object, 4 reads
  x3-<volume>-c<cohort>   the read of 30 objects (10 per size) under cold/c<cohort>/, one
                          invocation per volume and cohort; volume dir (created with ALL) and
                          sw (created with NONE, changed to ALL after the writes)

For each group and size class the script prints n, p50, p90, smallest, and largest, by nearest
rank over the samples: sorted(samples)[ceil(p * n) - 1]. The p50 is compared with the tool's
own per_size_class p50_ms, and a line says whether they agree. For the 5 MiB class the samples
above 300 ms (a chosen cut-off, not a decision rule) are counted, and the sorted samples of
every 5 MiB group are printed, to show the spread behind each p50.

Within a cold/c<cohort>/ invocation the objects are read in the order the listing returns them
(key order); that order is printed once.
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE_LABEL = {"64KB": "64 KiB", "1MB": "1 MiB", "5MB": "5 MiB"}
SIZE_ORDER = ["64KB", "1MB", "5MB"]
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
    return (f"{name:12s} {SIZE_LABEL[size]:7s} n={len(vals):3d}  p50={pct(vals, 0.5):7.1f}  "
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

    print("== read order inside a cold/c<cohort>/ invocation (key order, one read each)")
    order = [o["key"].rsplit("/", 1)[0].split("/", 2)[2] for o in load("x3-dir-c1")["per_object"]]
    runs = [(k, order.count(k)) for k in dict.fromkeys(order)]
    print("  " + ", then ".join(f"{n} x {k}" for k, n in runs))

    print("\n== warm-up, 4 reads of the 64 KiB warm/ object")
    for t in tags:
        if t.startswith("x3-warm-"):
            single(t)

    print("\n== reads of 30 objects: dir (created with ALL) and sw (NONE, then ALL), cohort 1 and 2")
    for t in ("x3-dir-c1", "x3-dir-c2", "x3-sw-c1", "x3-sw-c2"):
        single(t)

    print("\n5 MiB samples in read order and sorted (ms), to show the spread behind the p50:")
    for t in ("x3-dir-c1", "x3-dir-c2", "x3-sw-c1", "x3-sw-c2"):
        vals = by_size(load(t))["5MB"]
        print(f"  {t}: read order " + " ".join(f"{v:.1f}" for v in vals))
        print(f"  {'':{len(t)}}  sorted     " + " ".join(f"{v:.1f}" for v in sorted(vals)))

    print(f"\nsingle-invocation p50 values checked against the tool's per_size_class p50_ms: "
          f"{checked} checked, {mismatched} differ")
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
