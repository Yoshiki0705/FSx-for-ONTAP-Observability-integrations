"""Capacity pool reads, tiering writes, tier state, and elapsed time for the third tiering run.

Reads only the files committed next to this script (cw-all.tsv, cw-storageused.tsv,
invocations.jsonl, manifest.txt, run-log-times.tsv, node-counters.tsv, tier-polls.log, raw/).
Run from any directory: python3 analyze_capacity_pool.py

Blocks, in order:
  0. Inputs: invocation counts per kind, status, GetObject reads and bytes in raw/.
  1. Tiering writes: CapacityPoolWrite{Operations,Bytes} per volume against the logical size.
  2. Reads: f = CapacityPoolReadBytes in the window / bytes the invocation read. Window rule
     (the one of the earlier runs): the 1-minute buckets from the invocation's start minute
     through the minute after its end. Then the reading fixed before the first invocation:
     f below 0.10, above 0.80, in between; and the row of the pre-registered table that the
     two main reads (cohort 1 of each volume) fall in.
  3. Non-zero CapacityPoolRead* buckets that lie outside every read window.
  4. Node counter differences (node-counters.tsv) against the CloudWatch sums.
  5. Per-volume DataReadOperations/DataReadBytes against the GetObject samples in raw/.
  6. Tier state from the one-minute StorageUsed points and the polls in tier-polls.log.
  7. Elapsed time between the data entering the capacity pool and each read, with the rule of
     the second tiering run (2026-10-11-tier-path/analyze_capacity_pool.py, block 8): "at
     least" is from the end of the last one-minute bucket with CapacityPoolWriteOperations
     above 0 to the start of the read; "at most" is from the start of the first such bucket.
     "At most" takes the bucket label as not later than the writes it records; a label can be
     later than the event, which would add up to about 1 minute to every "at most". Minutes are
     rounded down in "at least" and up in "at most", so each stays a bound. The run-log times
     (run-log-times.tsv) are printed as separate lines and are not used for either bound.

cw-all.tsv holds only datapoints whose Sum is above 0. A metric with no row in a window
therefore means "no positive datapoint", and DataReadOperations in the same window is the
control that the reads were recorded.

The cross-experiment comparison (elapsed time of all eight volumes, reads of other volumes
between a volume's last write bucket and its first read) is in analyze_cross_experiment.py.
"""
from __future__ import annotations

import collections
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
VOLS = ("dir", "sw")
CP_METRICS = (
    "CapacityPoolReadOperations",
    "CapacityPoolReadBytes",
    "CapacityPoolWriteOperations",
    "CapacityPoolWriteBytes",
)
READ_BYTES_30 = 63_569_920  # 10 x (65,536 + 1,048,576 + 5,242,880): one 30-object pass


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def minute(s: str) -> datetime:
    return ts(s).replace(second=0, microsecond=0)


def hms(t: datetime) -> str:
    return t.strftime("%H:%M:%S")


def hm(t: datetime) -> str:
    return t.strftime("%H:%M")


def load_cw() -> dict[tuple[str, str, datetime], float]:
    cw: dict[tuple[str, str, datetime], float] = {}
    duplicates = 0
    with open(os.path.join(HERE, "cw-all.tsv")) as f:
        for line in f:
            vol, metric, t, val = line.rstrip("\n").split("\t")
            key = (vol, metric, minute(t))
            duplicates += key in cw
            cw[key] = float(val)
    print(f"cw-all.tsv: {len(cw)} datapoints, {duplicates} duplicate keys")
    return cw


def load_manifest() -> dict[str, dict[str, int]]:
    sizes: dict[str, dict[str, int]] = collections.defaultdict(dict)
    with open(os.path.join(HERE, "manifest.txt")) as f:
        for line in f:
            vol, key, size = line.rstrip("\n").split("\t")
            sizes[vol][key] = int(size)
    return sizes


def window(inv: dict) -> list[datetime]:
    start = minute(inv["start_utc"])
    end = minute(inv["end_utc"]) + timedelta(minutes=1)
    out, t = [], start
    while t <= end:
        out.append(t)
        t += timedelta(minutes=1)
    return out


def classify(f: float) -> str:
    if f < 0.10:
        return "below 0.10 (the reads did not reach the capacity pool)"
    if f > 0.80:
        return "above 0.80 (nearly all of the data was read from the capacity pool)"
    return "between 0.10 and 0.80 (partial; reported as it is)"


def mins(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 60


def down(x: float) -> float:
    return math.floor(x * 10 + 1e-6) / 10


def up(x: float) -> float:
    return math.ceil(x * 10 - 1e-6) / 10


def main() -> int:
    cw = load_cw()
    manifest = load_manifest()
    with open(os.path.join(HERE, "invocations.jsonl")) as f:
        invs = [json.loads(x) for x in f]
    logical = {v: sum(manifest[v].values()) for v in VOLS}

    # ------------------------------------------------------------------ 0. inputs
    print("\n== 0. inputs")
    kinds = collections.Counter(("warm" if i["tag"].startswith("x3-warm-") else "read") for i in invs)
    print("invocations by kind:", dict(sorted(kinds.items())), "total", len(invs))
    bad = [i["tag"] for i in invs if i["cli_rc"] != 0 or not i["cli_out"].startswith("200")]
    print("invocations with a non-zero CLI exit code or a status other than 200:", bad or "none")
    reads: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    total_reads = total_bytes = 0
    for i in invs:
        with open(os.path.join(HERE, "raw", f"{i['tag']}.json")) as f:
            per_object = json.load(f)["result"]["per_object"]
        for o in per_object:
            n = len(o["samples_ms"])
            reads[i["vol"]][0] += n
            reads[i["vol"]][1] += n * o["size_bytes"]
            total_reads += n
            total_bytes += n * o["size_bytes"]
    print(f"GetObject reads in raw/: {total_reads:,} reads, {total_bytes:,} bytes "
          f"(and {len(invs)} ListObjectsV2, one per invocation)")
    print("logical size per volume, from manifest.txt, including the 64 KiB warm/ object:")
    for v in VOLS:
        sizes = collections.Counter(manifest[v].values())
        print(f"  {v:4s} {logical[v]:12,} B in {len(manifest[v])} objects "
              f"(sizes: {dict(sorted(sizes.items()))})")
    print("the two volumes hold the same number of objects and the same logical size:",
          len({len(manifest[v]) for v in VOLS}) == 1 and len({logical[v] for v in VOLS}) == 1)

    # ------------------------------------------------------------------ 1. tiering writes
    print("\n== 1. tiering writes (whole file; logical size includes the warm/ object)")
    wbytes: dict[str, float] = {}
    for v in VOLS:
        wb = sum(x for (n, m, _), x in cw.items() if n == v and m == "CapacityPoolWriteBytes")
        wo = sum(x for (n, m, _), x in cw.items() if n == v and m == "CapacityPoolWriteOperations")
        buckets = sorted(t for (n, m, t), x in cw.items()
                         if n == v and m == "CapacityPoolWriteOperations" and x > 0)
        wbytes[v] = wb
        print(f"{v:4s} write ops={wo:4.0f} bytes={wb:13,.0f} logical={logical[v]:12,} "
              f"ratio={wb / logical[v]:.4f}  bytes per op={wb / wo:,.0f}  "
              f"buckets={[hm(t) + 'Z' for t in buckets]}")

    # ------------------------------------------------------------------ 2. reads
    print("\n== 2. reads: f = capacity pool read bytes in the window / bytes read by the invocation")
    cohorts = [i for i in invs if not i["tag"].startswith("x3-warm-")]
    touched: dict[str, set[datetime]] = collections.defaultdict(set)
    f_of: dict[str, float] = {}
    for i in cohorts:
        vol = i["vol"]
        buckets = window(i)
        ops = sum(cw.get((vol, "CapacityPoolReadOperations", t), 0) for t in buckets)
        byt = sum(cw.get((vol, "CapacityPoolReadBytes", t), 0) for t in buckets)
        dro = sum(cw.get((vol, "DataReadOperations", t), 0) for t in buckets)
        expected = sum(s for k, s in manifest[vol].items() if k.startswith(i["prefix"]))
        touched[vol].update(buckets)
        f_of[i["tag"]] = byt / expected
        print(f"{i['tag']:12s} vol={vol:4s} start={hms(ts(i['start_utc']))}Z "
              f"buckets={hm(buckets[0])}Z..{hm(buckets[-1])}Z ops={ops:4.0f} bytes={byt:10,.0f} "
              f"expected={expected:11,} f={byt / expected:.3f}  DataReadOps={dro:3.0f}  "
              f"-> {classify(byt / expected)}")
    print("\npre-registered reading of the two main reads (cohort 1 of each volume):")
    f_sw, f_dir = f_of["x3-sw-c1"], f_of["x3-dir-c1"]
    print(f"  SW cohort 1 f={f_sw:.3f}, DIR cohort 1 f={f_dir:.3f}")
    if f_sw < 0.10 and f_dir > 0.80:
        row = "SW below 0.10 and DIR above 0.80"
    elif f_sw > 0.80 and f_dir < 0.10:
        row = "SW above 0.80 and DIR below 0.10"
    elif f_sw > 0.80 and f_dir > 0.80:
        row = "both above 0.80"
    elif f_sw < 0.10 and f_dir < 0.10:
        row = "both below 0.10"
    else:
        row = "anything else (0.10 to 0.80 on either)"
    print(f"  row of the table fixed before the first invocation: {row}")
    print(f"  cohort 2 of DIR (auxiliary, not used for the row): f={f_of['x3-dir-c2']:.3f}")
    print(f"  cohort 2 of SW (auxiliary, not used for the row):  f={f_of['x3-sw-c2']:.3f}")

    # ------------------------------------------------------------------ 3. outside read windows
    print("\n== 3. non-zero CapacityPoolRead* buckets outside every read window of block 2")
    side = [i for i in invs if i["tag"].startswith("x3-warm-")]
    for v in VOLS:
        rows = sorted((t, x) for (n, m, t), x in cw.items()
                      if n == v and m == "CapacityPoolReadOperations" and t not in touched[v] and x > 0)
        print(v)
        for t, x in rows:
            b = cw.get((v, "CapacityPoolReadBytes", t), 0)
            note = ""
            inside = [i["tag"] for i in side if i["vol"] == v and t in window(i)]
            if inside:
                note = "  inside the window of " + ", ".join(inside)
            wr = cw.get((v, "CapacityPoolWriteOperations", t), 0)
            note += f"  (write ops in the same bucket: {wr:.0f})"
            print(f"  {hm(t)}Z ops={x:3.0f} bytes={b:9,.0f}  bytes per op={b / x:,.0f}{note}")

    # ------------------------------------------------------------------ 4. node counters
    print("\n== 4. node counters (object_store_client_op, node 1) against the CloudWatch sums")
    counters: dict[str, list[tuple[datetime, int, int]]] = collections.defaultdict(list)
    with open(os.path.join(HERE, "node-counters.tsv")) as f:
        next(f)
        for line in f:
            t, row, ops, byt = line.rstrip("\n").split("\t")
            counters[row].append((ts(t), int(ops), int(byt)))
    grand: dict[str, float] = collections.defaultdict(float)
    for m in CP_METRICS:
        grand[m] = sum(val for (_, mm, _), val in cw.items() if mm == m)
    cw_for = {"node1 get": ("CapacityPoolReadOperations", "CapacityPoolReadBytes"),
              "node1 put": ("CapacityPoolWriteOperations", "CapacityPoolWriteBytes")}
    for row, series in sorted(counters.items()):
        total_ops = series[-1][1] - series[0][1]
        total_bytes = series[-1][2] - series[0][2]
        print(f"{row}: {hms(series[0][0])}Z -> {hms(series[-1][0])}Z "
              f"increase {total_ops:,} ops, {total_bytes:,} B")
        for a, b in zip(series, series[1:], strict=False):
            cw_ops = sum(val for (_, mm, t), val in cw.items()
                         if mm == cw_for[row][0] and minute(a[0].isoformat()) <= t < minute(b[0].isoformat()))
            cw_byt = sum(val for (_, mm, t), val in cw.items()
                         if mm == cw_for[row][1] and minute(a[0].isoformat()) <= t < minute(b[0].isoformat()))
            print(f"   {hms(a[0])}Z -> {hms(b[0])}Z: counter +{b[1] - a[1]:,} ops, +{b[2] - a[2]:,} B; "
                  f"CloudWatch buckets in [{hm(minute(a[0].isoformat()))}, {hm(minute(b[0].isoformat()))}): "
                  f"{cw_ops:,.0f} ops, {cw_byt:,.0f} B")
        eq = (total_ops == grand[cw_for[row][0]]) and (total_bytes == grand[cw_for[row][1]])
        print(f"   whole-file CloudWatch sums over the two volumes: {grand[cw_for[row][0]]:,.0f} ops, "
              f"{grand[cw_for[row][1]]:,.0f} B -> equal to the counter increase: {eq}")
    print("the counters are cumulative from an unknown start, so only differences are used; the "
          "interval starts at the last counter read of the second tiering run, not just before this run.")
    get_4096 = counters["node1 get"]
    per_op = (get_4096[1][2] - get_4096[0][2]) / (get_4096[1][1] - get_4096[0][1])
    print(f"bytes per get operation in the first interval: {per_op:,.0f}")

    # ------------------------------------------------------------------ 5. data reads vs raw
    print("\n== 5. DataReadOperations / DataReadBytes (CloudWatch, whole file) against the GetObject samples in raw/")
    total_ok = True
    for v in VOLS:
        dro = sum(val for (n, m, _), val in cw.items() if n == v and m == "DataReadOperations")
        drb = sum(val for (n, m, _), val in cw.items() if n == v and m == "DataReadBytes")
        n_raw, b_raw = reads[v]
        ok = dro == n_raw and drb == b_raw
        total_ok &= ok
        print(f"{v:4s} DataReadOps={dro:6,.0f} raw reads={n_raw:6,}  DataReadBytes={drb:15,.0f} raw bytes={b_raw:15,}  "
              f"{'equal' if ok else 'DIFFERENT'}")
    print("both volumes equal:", total_ok)

    # ------------------------------------------------------------------ 6. tier state
    print("\n== 6. tier state from cw-storageused.tsv (one-minute StorageUsed points, bytes)")
    su: dict[tuple[str, str], dict[datetime, float]] = collections.defaultdict(dict)
    with open(os.path.join(HERE, "cw-storageused.tsv")) as f:
        for line in f:
            vol, tier, t, val = line.rstrip("\n").split("\t")
            su[(vol, tier)][minute(t)] = float(val)
    all_minutes = sorted({t for s in su.values() for t in s})
    last = all_minutes[-1]
    print(f"points per volume and tier: "
          f"{ {f'{v}/{t[:3]}': len(su[(v, t)]) for v in VOLS for t in ('SSD', 'StandardCapacityPool')} }; "
          f"first {hm(all_minutes[0])}Z, last {hm(last)}Z")
    for v in VOLS:
        cp, ssd = su[(v, "StandardCapacityPool")], su[(v, "SSD")]
        full = min(t for t, x in sorted(cp.items()) if x == logical[v])
        stays = all(x == logical[v] for t, x in cp.items() if t >= full)
        partial = [(hm(t), int(x)) for t, x in sorted(cp.items()) if 0 < x < logical[v]]
        print(f"{v:4s} first point at the full logical size {hm(full)}Z ({int(cp[full]):,} B); "
              f"equal to it at every later point to {hm(last)}Z: {stays}; partial points: {partial or 'none'}")
        before = full - timedelta(minutes=1)
        print(f"     SSD one point before {int(ssd[before]):,}, at the point {int(ssd[full]):,}, "
              f"at {hm(last)}Z {int(ssd[last]):,}; change from the first full point to {hm(last)}Z "
              f"{int(ssd[last] - ssd[full]):+,} B ({(ssd[last] - ssd[full]) / logical[v] * 100:.1f} % of the logical size)")
    print("\npolls (tier-polls.log; the Amazon FSx API policy and the volume-level metric, read during the run):")
    with open(os.path.join(HERE, "tier-polls.log")) as f:
        header = ""
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("##"):
                header = line[3:]
                continue
            parts = line.split("\t")
            print(f"  {header}  {parts[0]:4s} {parts[1]}  capacity pool tier {int(float(parts[-1])):,} B "
                  f"(point {hm(minute(parts[-2]))}Z)")

    # ------------------------------------------------------------------ 7. elapsed time
    print("\n== 7. elapsed time from the data entering the capacity pool to each read (minutes)")
    events: dict[str, dict[str, datetime]] = collections.defaultdict(dict)
    with open(os.path.join(HERE, "run-log-times.tsv")) as f:
        next(f)
        for line in f:
            event, event_vols, t = line.rstrip("\n").split("\t")
            for v in event_vols.split(","):
                events[event][v] = ts(t)

    def hmsf(t: datetime) -> str:
        t = t + timedelta(milliseconds=50)  # to the nearest 0.1 s
        return t.strftime("%H:%M:%S.") + f"{t.microsecond // 100000}"

    for v in VOLS:
        buckets = sorted(t for (n, m, t), x in cw.items()
                         if n == v and m == "CapacityPoolWriteOperations" and x > 0)
        first_start, last_end = buckets[0], buckets[-1] + timedelta(minutes=1)
        print(f"{v:4s} tiering write buckets {', '.join(hm(t) + 'Z' for t in buckets)}; "
              f"first starts {hms(first_start)}Z, last ends {hms(last_end)}Z")
        for i in cohorts:
            if i["vol"] != v:
                continue
            start = ts(i["start_utc"])
            a, m_ = mins(start, last_end), mins(start, first_start)
            print(f"  {i['tag']:10s} read starts {hmsf(start)}Z  at least (from {hms(last_end)}Z): {a:.3f} "
                  f"-> rounded down {down(a):.1f};  at most (from {hms(first_start)}Z): {m_:.3f} "
                  f"-> rounded up {up(m_):.1f}; with a label up to 1 minute late: up to {up(m_ + 1):.1f}")
            print(f"             range, at least to at most: {down(a):.1f} to {up(m_):.1f}")
        print("  run-log times, printed for reference and not used as bounds:")
        up_end = events["upload_end"][v]
        print(f"    writes ended {hmsf(up_end)}Z; the first PUT of the run was {hmsf(events['upload_start']['all'])}Z")
        for i in cohorts:
            if i["vol"] != v:
                continue
            start = ts(i["start_utc"])
            line = (f"    {i['tag']:10s} from the end of the volume's writes: {mins(start, up_end):.1f}; "
                    f"from the start of the writes: {mins(start, events['upload_start']['all']):.1f}")
            if v in events["switch_request"]:
                req = events["switch_request"][v]
                line += f"; from the switch request ({hmsf(req)}Z): {mins(start, req):.1f}"
            print(line)
        if v in events["switch_request"]:
            req = events["switch_request"][v]
            api = events["api_showed_all_first_poll"][v]
            full_poll = events["sw_capacity_pool_full_first_poll"][v]
            print(f"    first write bucket starts {mins(first_start, req):.1f} min after the switch request; "
                  f"the Amazon FSx API showed ALL at the first poll ({hms(api)}Z); "
                  f"the capacity pool was full at the poll of {hms(full_poll)}Z")
    return 0


if __name__ == "__main__":
    sys.exit(main())
