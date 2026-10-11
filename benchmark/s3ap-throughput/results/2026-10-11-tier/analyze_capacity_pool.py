"""Capacity pool reads and position check for the tier comparison.

Reads only the files committed next to this script (cw-all.tsv, cw-b-tiering.tsv,
invocations.jsonl, raw/). Run from any directory:
python3 analyze_capacity_pool.py

1. Map each invocation's [start, end] UTC to the 1-minute CloudWatch buckets it touches and
   sum the per-volume CapacityPoolRead{Operations,Bytes} there. Buckets touched by no
   invocation window are reported separately.
2. Position check: samples above 3x the invocation's median, excluding first reads of B.
3. Elapsed time: for each B invocation, the minutes from the end of the last one-minute
   bucket with CapacityPoolWriteOperations above 0 (cw-b-tiering.tsv) to the invocation's
   start. This is a lower bound only: the pull range of cw-b-tiering.tsv was not recorded, so
   the file does not show whether tiering writes occurred before its first bucket, and the
   time at which an individual object was tiered was not recorded. Minutes are rounded down.

The node-level object_store_client_op counter deltas are not computed here; the
counter dumps are not committed. The derived deltas are in README.md.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def main() -> None:
    cw: dict[tuple, float] = {}
    with open(os.path.join(HERE, "cw-all.tsv")) as f:
        for line in f:
            vol, metric, ts, val = line.rstrip("\n").split("\t")
            cw[(vol, metric, parse_ts(ts).replace(second=0, microsecond=0))] = float(val)

    invs = [json.loads(x) for x in open(os.path.join(HERE, "invocations.jsonl"))]
    invs = [i for i in invs if i["tag"].startswith("tier-r")]
    touched: set[datetime] = set()
    rows = []
    for i in invs:
        s = parse_ts(i["start_utc"]).replace(second=0, microsecond=0)
        # CloudWatch labels ops from the second half of a minute with the NEXT minute
        # (seen: invocation 09:00:45-09:00:48Z -> bucket 09:01Z), so extend by one minute.
        # B invocations are >= 3 slots (>= 3 min) apart, so the extension cannot overlap.
        e = parse_ts(i["end_utc"]).replace(second=0, microsecond=0) + timedelta(minutes=1)
        buckets = []
        t = s
        while t <= e:
            buckets.append(t)
            t += timedelta(minutes=1)
        touched.update(buckets)
        ops = sum(cw.get(("b", "CapacityPoolReadOperations", t), 0) for t in buckets)
        byt = sum(cw.get(("b", "CapacityPoolReadBytes", t), 0) for t in buckets)
        a_ops = sum(cw.get((v, "CapacityPoolReadOperations", t), 0) for v in ("a1", "a2") for t in buckets)
        rows.append((i["tag"], i["cond"], ops, byt, a_ops))

    print("== B per-invocation capacity pool reads (CloudWatch, 1-min buckets touched by the invocation)")
    for tag, cond, ops, byt, a_ops in rows:
        if cond == "b":
            print(f"{tag:20s} ops={ops:6.0f} bytes={byt:12.0f}  (a1+a2 ops in same buckets: {a_ops:.0f})")

    print("\n== CloudWatch buckets with non-zero CapacityPoolRead* NOT touched by any invocation window")
    stray = [(k, v) for k, v in sorted(cw.items(), key=lambda kv: kv[0][2])
             if k[1].startswith("CapacityPoolRead") and v > 0 and k[2] not in touched]
    for (vol, metric, t), v in stray:
        print(f"  {vol} {metric} {t.strftime('%H:%MZ')} {v:.0f}")
    if not stray:
        print("  none")

    print("\n== A volumes: any non-zero CapacityPool* in the whole window?")
    a_nonzero = [(k, v) for k, v in cw.items() if k[0] in ("a1", "a2") and v > 0]
    print("  none" if not a_nonzero else f"  {a_nonzero[:5]}")

    print("\n== Position check (samples > 3x invocation median; B first reads excluded)")
    found = 0
    for i in invs:
        raw = json.load(open(os.path.join(HERE, "raw", f"{i['tag']}.json")))["result"]["per_object"]
        flat = [(oi * 4 + k + 1, x, k) for oi, o in enumerate(raw) for k, x in enumerate(o["samples_ms"])]
        med = sorted(x for _, x, _ in flat)[len(flat) // 2]
        for pos, x, k in flat:
            if x > 3 * med and not (i["cond"] == "b" and k == 0):
                found += 1
                print(f"  {i['tag']:20s} GET#{pos:2d} (request #{pos + 1:2d}) {x:8.1f} ms  (median {med:.1f})")
    if not found:
        print("  none")

    print("\n== Elapsed time: B invocation start against the end of the last bucket with tiering writes")
    tier_writes: dict[tuple, float] = {}
    with open(os.path.join(HERE, "cw-b-tiering.tsv")) as f:
        for line in f:
            vol, metric, ts, val = line.rstrip("\n").split("\t")
            tier_writes[(vol, metric, parse_ts(ts).replace(second=0, microsecond=0))] = float(val)
    wops = sorted(t for (v, m, t), x in tier_writes.items() if m == "CapacityPoolWriteOperations" and x > 0)
    wbytes = sum(x for (v, m, t), x in tier_writes.items() if m == "CapacityPoolWriteBytes")
    print(f"  cw-b-tiering.tsv: {len(wops)} one-minute buckets with CapacityPoolWriteOperations above 0, "
          f"first {wops[0].strftime('%H:%MZ')}, last {wops[-1].strftime('%H:%MZ')}; "
          f"{sum(tier_writes[('b', 'CapacityPoolWriteOperations', t)] for t in wops):.0f} operations, {wbytes:,.0f} bytes")
    cw_overlap = [t for (v, m, t) in cw if v == "b" and m == "CapacityPoolWriteOperations" and cw[(v, m, t)] > 0]
    agree = all(cw[("b", "CapacityPoolWriteOperations", t)] == tier_writes.get(("b", "CapacityPoolWriteOperations", t))
                for t in cw_overlap)
    print(f"  cw-all.tsv holds {len(cw_overlap)} of those buckets "
          f"({', '.join(t.strftime('%H:%MZ') for t in sorted(cw_overlap))}); values agree: {agree}")
    last_end = wops[-1] + timedelta(minutes=1)
    print(f"  last bucket ends {last_end.strftime('%H:%M:%S')}Z")
    ops_by_tag = {tag: ops for tag, cond, ops, byt, a_ops in rows if cond == "b"}
    for i in invs:
        if i["cond"] != "b":
            continue
        minutes = (parse_ts(i["start_utc"]) - last_end).total_seconds() / 60
        print(f"  {i['tag']:20s} start {parse_ts(i['start_utc']).strftime('%H:%M:%S.%f')[:10]}Z  "
              f"at least {minutes:.3f} min -> rounded down {math.floor(minutes * 10 + 1e-6) / 10:.1f}  "
              f"(capacity pool read ops in the windows: {ops_by_tag[i['tag']]:.0f})")


if __name__ == "__main__":
    main()
