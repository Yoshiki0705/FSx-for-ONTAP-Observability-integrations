"""Capacity pool reads, tiering writes, and tier state for the tiering path experiment.

Reads only the files committed next to this script (cw-all.tsv, cw-storageused.tsv,
invocations.jsonl, manifest.txt, raw/). Run from any directory:
python3 analyze_capacity_pool.py

Blocks, in order:
  0. Inputs: invocation counts per kind, status, GetObject reads and bytes in raw/.
  1. Tiering writes: CapacityPoolWrite{Operations,Bytes} per volume against the logical size.
  2. Reads: f = CapacityPoolReadBytes in the window / bytes the invocation(s) read.
     Window rule (fixed before the first invocation): the 1-minute buckets from the
     invocation's start minute through the minute after its end.
  3. Stored-bytes ratio g (added after the S3p first-read result was seen): CapacityPoolReadBytes
     of the first read / CapacityPoolWriteBytes of the tiering. Post-hoc.
  4. Buckets with a non-zero CapacityPoolRead* value that lie outside every read window.
  5. Totals of the four CapacityPool* metrics over all rows, for the node-counter comparison.
  6. Per-volume DataReadOperations/DataReadBytes against the GetObject samples in raw/.
  7. Tier state from the one-minute StorageUsed points (SSD and StandardCapacityPool).

cw-all.tsv holds only datapoints whose Sum is above 0. A metric with no row in a window
therefore means "no positive datapoint", and DataReadOperations in the same window is the
control that the reads were recorded.

The node-level object_store_client_op counter differences are not computed here; the counter
dumps are not committed. The differences are in README.md.
"""
from __future__ import annotations

import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
VOLS = ("k1", "s2", "s3", "s3p", "s4")
CP_METRICS = (
    "CapacityPoolReadOperations",
    "CapacityPoolReadBytes",
    "CapacityPoolWriteOperations",
    "CapacityPoolWriteBytes",
)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


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


def read_bytes(manifest: dict[str, dict[str, int]], vol: str, prefixes: list[str]) -> int:
    return sum(s for k, s in manifest[vol].items() if any(k.startswith(p) for p in prefixes))


def main() -> int:
    cw = load_cw()
    manifest = load_manifest()
    with open(os.path.join(HERE, "invocations.jsonl")) as f:
        invs = [json.loads(x) for x in f]
    logical = {v: sum(manifest[v].values()) for v in VOLS}

    # ------------------------------------------------------------------ 0. inputs
    print("\n== 0. inputs")
    kinds = collections.Counter(i["tag"].split("-")[1] for i in invs)
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
        print(f"  {v:4s} {logical[v]:12,} B in {len(manifest[v])} objects")

    # ------------------------------------------------------------------ 1. tiering writes
    print("\n== 1. tiering writes (whole file; logical size includes the warm/ object)")
    wbytes: dict[str, float] = {}
    for v in ("k1", "s2", "s3", "s3p", "s4"):
        wb = sum(x for (n, m, _), x in cw.items() if n == v and m == "CapacityPoolWriteBytes")
        wo = sum(x for (n, m, _), x in cw.items() if n == v and m == "CapacityPoolWriteOperations")
        wbytes[v] = wb
        print(f"{v:4s} write ops={wo:4.0f} bytes={wb:13,.0f} logical={logical[v]:12,} "
              f"ratio={wb / logical[v]:.4f} (1/{logical[v] / wb:.1f})  bytes per op={wb / wo:,.0f}")

    # ------------------------------------------------------------------ 2. reads
    print("\n== 2. reads: f = capacity pool read bytes in the window / bytes read by the invocation(s)")
    by_tag = {i["tag"]: i for i in invs}
    groups: dict[str, tuple[list[dict], str]] = collections.OrderedDict()
    for i in invs:
        tag = i["tag"]
        if tag.startswith("x2-cold-") or tag.startswith("x2-coldre-"):
            groups[tag] = ([i], i["vol"])
        elif tag.startswith("x2-k1first-"):
            groups.setdefault("x2-k1first (12 prefixes)", ([], "k1"))[0].append(i)
        elif tag.startswith("x2-k1re-c"):
            groups.setdefault(f"x2-k1re-{tag.split('-')[2]}", ([], "k1"))[0].append(i)
    sweep_end = max(ts(i["end_utc"]) for i in invs if i["tag"].startswith("x2-k1first-"))
    touched: dict[str, set[datetime]] = collections.defaultdict(set)
    first_read_bytes: dict[str, float] = {}
    for name, (ivs, vol) in groups.items():
        buckets = sorted({t for i in ivs for t in window(i)})
        ops = sum(cw.get((vol, "CapacityPoolReadOperations", t), 0) for t in buckets)
        byt = sum(cw.get((vol, "CapacityPoolReadBytes", t), 0) for t in buckets)
        dro = sum(cw.get((vol, "DataReadOperations", t), 0) for t in buckets)
        expected = read_bytes(manifest, vol, [i["prefix"] for i in ivs])
        touched[vol].update(buckets)
        start = min(ts(i["start_utc"]) for i in ivs)
        since = ""
        if name.startswith("x2-k1re-c"):
            c = name.rsplit("-", 1)[1]
            own_end = max(ts(i["end_utc"]) for i in invs if i["tag"].startswith(f"x2-k1first-{c}-"))
            since = (f"  since the end of cohort {c[1:]} first read {(start - own_end).total_seconds() / 60:5.1f} min"
                     f", since the end of the first-read sweep {(start - sweep_end).total_seconds() / 60:5.1f} min")
        elif name.startswith("x2-coldre-"):
            first = ts(by_tag[f"x2-cold-{vol}"]["start_utc"])
            since = f"  since the start of the first read of {vol} {(start - first).total_seconds() / 60:5.1f} min"
        if name.startswith("x2-cold-") or name.startswith("x2-k1first"):
            first_read_bytes[vol] = byt
        print(f"{name:26s} vol={vol:4s} ops={ops:5.0f} bytes={byt:13,.0f} expected={expected:11,} "
              f"f={byt / expected:.3f}  DataReadOps={dro:4.0f}  start={hms(start)}Z{since}")

    # ------------------------------------------------------------------ 3. post-hoc g
    print("\n== 3. stored-bytes ratio g (post-hoc): first-read capacity pool read bytes / tiering write bytes")
    for v in ("k1", "s3p", "s2", "s3", "s4"):
        print(f"{v:4s} read bytes={first_read_bytes[v]:13,.0f} / write bytes={wbytes[v]:13,.0f} "
              f"= {first_read_bytes[v] / wbytes[v]:.3f}")

    # ------------------------------------------------------------------ 4. outside read windows
    print("\n== 4. non-zero CapacityPoolRead* buckets outside every read window of block 2")
    side = [i for i in invs if i["tag"].startswith(("x2-warm-", "x2-pre-"))]
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

    # ------------------------------------------------------------------ 5. totals
    print("\n== 5. totals over all rows of cw-all.tsv")
    grand: dict[str, float] = collections.defaultdict(float)
    for v in VOLS:
        parts = []
        for m in CP_METRICS:
            x = sum(val for (n, mm, _), val in cw.items() if n == v and mm == m)
            grand[m] += x
            parts.append(f"{m.replace('CapacityPool', 'CP')}={x:,.0f}")
        print(f"{v:4s} " + "  ".join(parts))
    print("sum  " + "  ".join(f"{m.replace('CapacityPool', 'CP')}={grand[m]:,.0f}" for m in CP_METRICS))

    # ------------------------------------------------------------------ 6. data reads vs raw
    print("\n== 6. DataReadOperations / DataReadBytes (CloudWatch, whole file) against the GetObject samples in raw/")
    total_ok = True
    for v in VOLS:
        dro = sum(val for (n, m, _), val in cw.items() if n == v and m == "DataReadOperations")
        drb = sum(val for (n, m, _), val in cw.items() if n == v and m == "DataReadBytes")
        n_raw, b_raw = reads[v]
        ok = dro == n_raw and drb == b_raw
        total_ok &= ok
        print(f"{v:4s} DataReadOps={dro:6,.0f} raw reads={n_raw:6,}  DataReadBytes={drb:15,.0f} raw bytes={b_raw:15,}  "
              f"{'equal' if ok else 'DIFFERENT'}")
    print("all five volumes equal:", total_ok)

    # ------------------------------------------------------------------ 7. tier state
    print("\n== 7. tier state from cw-storageused.tsv (one-minute StorageUsed points, bytes)")
    su: dict[tuple[str, str], dict[datetime, float]] = collections.defaultdict(dict)
    with open(os.path.join(HERE, "cw-storageused.tsv")) as f:
        for line in f:
            vol, tier, t, val = line.rstrip("\n").split("\t")
            su[(vol, tier)][minute(t)] = float(val)
    day = datetime(2026, 10, 11, tzinfo=timezone.utc)

    def at(hh: int, mm: int) -> datetime:
        return day.replace(hour=hh, minute=mm)

    all_minutes = sorted({t for s in su.values() for t in s})
    print(f"points per volume and tier: "
          f"{ {f'{v}/{t[:3]}': len(su[(v, t)]) for v in VOLS for t in ('SSD', 'StandardCapacityPool')} }; "
          f"first {hm(all_minutes[0])}Z, last {hm(all_minutes[-1])}Z")
    for v in VOLS:
        missing = [hm(t) for t in all_minutes if t not in su[(v, "SSD")]]
        if missing:
            print(f"  {v}: no point at {missing}")
    first_read_start = {
        "k1": min(ts(i["start_utc"]) for i in invs if i["tag"].startswith("x2-k1first-")),
        "s3p": ts(by_tag["x2-cold-s3p"]["start_utc"]),
        "s2": ts(by_tag["x2-cold-s2"]["start_utc"]),
        "s3": ts(by_tag["x2-cold-s3"]["start_utc"]),
        "s4": ts(by_tag["x2-cold-s4"]["start_utc"]),
    }
    last = all_minutes[-1]
    print("\ncapacity pool tier:")
    for v in VOLS:
        cp, ssd = su[(v, "StandardCapacityPool")], su[(v, "SSD")]
        full = min(t for t, x in sorted(cp.items()) if x == logical[v])
        stays = all(x == logical[v] for t, x in cp.items() if t >= full)
        partial = [(hm(t), int(x)) for t, x in sorted(cp.items()) if 0 < x < logical[v]]
        print(f"{v:4s} first point at the full logical size {hm(full)}Z ({int(cp[full]):,} B); "
              f"equal to it at every later point to {hm(last)}Z: {stays}; partial points: {partial or 'none'}")
        d_start = (first_read_start[v] - full).total_seconds() / 60
        d_end = (first_read_start[v] - (full + timedelta(minutes=1))).total_seconds() / 60
        print(f"     first read started {hms(first_read_start[v])}Z: {d_start:.1f} min after the start and "
              f"{d_end:.1f} min after the end of that point (floor to 0.1: {int(d_start * 10) / 10:.1f} and "
              f"{int(d_end * 10) / 10:.1f})")
        vals = [ssd[at(12, m)] for m in (9, 14, 15, 17) if at(12, m) in ssd]
        print(f"     SSD at 12:09, 12:14, 12:15, 12:17: {[int(x) for x in vals]}; "
              f"range 12:09 to 12:14: {int(min(ssd[t] for t in ssd if at(12, 9) <= t <= at(12, 14))):,} "
              f"to {int(max(ssd[t] for t in ssd if at(12, 9) <= t <= at(12, 14))):,}")
        print(f"     SSD at {hm(last)}Z: {int(ssd[last]):,}; change from 12:17: {int(ssd[last] - ssd[at(12, 17)]):+,} B "
              f"({(ssd[last] - ssd[at(12, 17)]) / logical[v] * 100:.1f} % of the logical size)")
        steps = []
        for t in sorted(ssd):
            prev = t - timedelta(minutes=1)
            if t > at(12, 17) and prev in ssd and ssd[t] != ssd[prev]:
                if any(i["vol"] == v and t in window(i) for i in invs):
                    where = "in an invocation window of this volume"
                elif any(t in window(i) for i in invs):
                    where = "in an invocation window of another volume only"
                else:
                    where = "in no invocation window"
                steps.append((hm(t), int(ssd[t] - ssd[prev]), where))
        print("     minute-to-minute SSD changes after 12:17 (minute, change in bytes, window):")
        for step in steps:
            print(f"       {step[0]}Z {step[1]:+,}  {step[2]}")
    print("\nlargest and smallest first-read-to-full-size interval (minutes after the end of the point), "
          "by group:")
    for label, vs in (("K1 and S3p", ("k1", "s3p")), ("S2, S3, S4", ("s2", "s3", "s4"))):
        ends = []
        for v in vs:
            cp = su[(v, "StandardCapacityPool")]
            full = min(t for t, x in sorted(cp.items()) if x == logical[v])
            ends.append((first_read_start[v] - (full + timedelta(minutes=1))).total_seconds() / 60)
        print(f"  {label}: smallest {min(ends):.2f}, largest {max(ends):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
