"""Analyze raw/tier-r{run}-{cond}-{size}.json with the decision rule.

Reads only the files committed next to this script (raw/). Run from any
directory: python3 analyze_latency.py

Classes per (run, size, cond):
  first   - samples_ms[0] of each of the 10 objects (n=10)
  repeat  - samples_ms[1:] of each object (n=30)
  all     - every sample (n=40)
  all_x1  - all, minus the first sample of the invocation (no warm-up in the tool)
Statistic for the rule: median (nearest-rank on the pooled samples).

Rule (fixed before the first invocation): for size s and class c, B is "slower"
only if in ALL runs
  D(r) = m_B(r) - mean(m_A1(r), m_A2(r))  >  0   and   D(r) > N_largest,
  N(r) = |m_A1(r) - m_A2(r)|,  N_largest = the largest N(r) over the runs.
Otherwise: no difference established (open).
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = ["s64k", "s1m", "s5m"]
CONDS = ["a1", "a2", "b"]
CLASSES = ["first", "repeat", "all", "all_x1"]


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    i = max(0, min(len(s) - 1, math.ceil(p * len(s)) - 1))
    return s[i]


def load(run: int, cond: str, size: str):
    path = os.path.join(HERE, "raw", f"tier-r{run}-{cond}-{size}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)["result"]["per_object"]


def classes(per_object):
    first = [o["samples_ms"][0] for o in per_object]
    repeat = [x for o in per_object for x in o["samples_ms"][1:]]
    allv = [x for o in per_object for x in o["samples_ms"]]
    return {"first": first, "repeat": repeat, "all": allv, "all_x1": allv[1:]}


def stats(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "p50": pct(values, 0.5),
        "p90": pct(values, 0.9),
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
    }


def main() -> int:
    runs = [r for r in (1, 2, 3) if all(load(r, c, s) for c in CONDS for s in SIZES)]
    print(f"complete runs: {runs}")
    table: dict[tuple, dict] = {}
    for r in runs:
        for s in SIZES:
            for c in CONDS:
                cl = classes(load(r, c, s))
                for k in CLASSES:
                    table[(r, s, c, k)] = stats(cl[k])
    print("\n== medians (ms): run, size, class -> a1 / a2 / b")
    for s in SIZES:
        for k in CLASSES:
            for r in runs:
                a1, a2, b = (table[(r, s, c, k)]["p50"] for c in CONDS)
                print(f"r{r} {s:5s} {k:7s} n={table[(r, s, 'a1', k)]['n']:2d}  "
                      f"{a1:8.2f} {a2:8.2f} {b:8.2f}   D={b - (a1 + a2) / 2:+8.2f}  N={abs(a1 - a2):6.2f}")
    if len(runs) == 3:
        print("\n== decision rule")
        for s in SIZES:
            for k in ("first", "all"):
                D = [table[(r, s, "b", k)]["p50"] - (table[(r, s, "a1", k)]["p50"] + table[(r, s, "a2", k)]["p50"]) / 2 for r in runs]
                N = [abs(table[(r, s, "a1", k)]["p50"] - table[(r, s, "a2", k)]["p50"]) for r in runs]
                n_largest = max(N)
                slower = all(d > 0 for d in D) and all(d > n_largest for d in D)
                print(f"{s:5s} {k:6s} D={[round(d, 2) for d in D]} N_largest={n_largest:.2f} -> "
                      f"{'B slower (rule met)' if slower else 'no difference established (open)'}")
    if len(runs) == 3:
        print("\n== pooled across the 3 runs (descriptive only; A = A1+A2 pooled)")
        for s in SIZES:
            for k in ("first", "repeat", "all"):
                a, b = [], []
                for r in runs:
                    cls_a1 = classes(load(r, "a1", s))[k]
                    cls_a2 = classes(load(r, "a2", s))[k]
                    a += cls_a1 + cls_a2
                    b += classes(load(r, "b", s))[k]
                sa, sb = stats(a), stats(b)
                print(f"{s:5s} {k:7s} A n={sa['n']:3d} p50={sa['p50']:7.2f} p90={sa['p90']:7.2f} | "
                      f"B n={sb['n']:3d} p50={sb['p50']:7.2f} p90={sb['p90']:7.2f} | p50 diff={sb['p50'] - sa['p50']:+7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
