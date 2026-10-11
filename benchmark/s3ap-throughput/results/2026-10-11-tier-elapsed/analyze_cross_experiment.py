"""Cross-experiment figures for the eight volumes of the three tiering experiments of 2026-10-11.

Experiment 1 is the tier comparison (../2026-10-11-tier/), experiment 2 the five-volume tiering
path run (../2026-10-11-tier-path/), experiment 3 this directory. Reads only committed files:

  this directory   cw-all.tsv, invocations.jsonl, tier-comparison-cw-dataread.tsv,
                   fs-DataReadBytes-experiment-2.tsv, fs-DataReadBytes-experiment-3.tsv
  ../2026-10-11-tier/        cw-all.tsv, cw-b-tiering.tsv, invocations.jsonl,
                             fs-DataReadBytes.tsv
  ../2026-10-11-tier-path/   cw-all.tsv, invocations.jsonl

Run from any directory: python3 analyze_cross_experiment.py

Blocks, in order:
  1. First reads of the eight volumes (B of experiment 1; K1, S3p, S2, S3, S4 of experiment 2;
     DIR and SW of experiment 3): the write path, whether the first read caused capacity pool
     read operations, and the elapsed time from the data entering the capacity pool to the
     read, by the rule of 2026-10-11-tier-path/analyze_capacity_pool.py (block 8): "at least"
     from the end of the last one-minute bucket with CapacityPoolWriteOperations above 0, "at
     most" from the start of the first such bucket (B has no "at most": its pull range was not
     recorded). "At most" takes the bucket label as not later than the writes.
  2. The write path against the result: how many volumes had a positive first read, by path.
  3. Elapsed time: whether the range of a read with no capacity pool reads lies inside, or
     overlaps, the range of a read with capacity pool reads.
  4. Post-hoc: the bytes read between a volume's last tiering write bucket and its first read.
     The buckets counted are those t with E <= t < m (one-minute CloudWatch DataReadBytes
     buckets, labels as recorded), where E is the end of the last one-minute bucket with
     CapacityPoolWriteOperations above 0 and m is the start minute of the first read. Per read:
       (a) other volumes of the same experiment only (the column used in the write-up);
       (b) the volume's own earlier reads in the same buckets;
       (c) the file-system-level DataReadBytes over the same buckets (every volume on the file
           system), and its difference from (a) + (b).
     The resolution is one minute and the label of a bucket can be later than the event. Writes
     by other volumes and non-data activity are not measured by DataReadBytes. This is an
     observation across eight volumes, not a controlled comparison.

The first reads counted as "the first read of a volume" are: B, the nine reads of the three
runs (each size of each run reads objects that no earlier read had touched); K1, the 12
cohort first-read invocations together; S2, S3, S4, S3p, one invocation each; DIR and SW,
cohort 1 (primary) and cohort 2 (auxiliary: read after cohort 1 of the same volume).

The DataReadBytes of the experiment 1 volumes (A1, A2, B) are in tier-comparison-cw-dataread.tsv
in this directory. They were pulled from CloudWatch after that experiment and are not in the
2026-10-11-tier/ directory, which holds file-system-wide DataReadBytes only
(fs-DataReadBytes.tsv). That file starts at 08:36 UTC, four one-minute buckets after the end of
B's last tiering write bucket (08:32); those four buckets are compared by the per-volume file
only (it has no positive DataReadBytes datapoint of A1, A2, or B before 08:38).
"""
from __future__ import annotations

import collections
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TIER = os.path.join(HERE, "..", "2026-10-11-tier")
PATH = os.path.join(HERE, "..", "2026-10-11-tier-path")

CW = dict[tuple[str, str, datetime], float]
NAMES = {"b": "B", "k1": "K1", "s2": "S2", "s3": "S3", "s3p": "S3p", "s4": "S4",
         "dir": "DIR", "sw": "SW"}


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def minute(s: str) -> datetime:
    return ts(s).replace(second=0, microsecond=0)


def hms(t: datetime) -> str:
    return t.strftime("%H:%M:%S")


def hm(t: datetime) -> str:
    return t.strftime("%H:%M")


def mins(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 60


def down(x: float) -> float:
    return math.floor(x * 10 + 1e-6) / 10


def up(x: float) -> float:
    return math.ceil(x * 10 - 1e-6) / 10


def load_cw(path: str) -> CW:
    cw: CW = {}
    with open(path) as f:
        for line in f:
            vol, metric, t, val = line.rstrip("\n").split("\t")
            cw[(vol, metric, minute(t))] = float(val)
    return cw


def load_fs(path: str) -> dict[datetime, float]:
    fs: dict[datetime, float] = {}
    with open(path) as f:
        for line in f:
            t, val = line.rstrip("\n").split("\t")
            fs[minute(t)] = float(val)
    return fs


def load_invs(path: str) -> list[dict]:
    with open(path) as f:
        invs = [json.loads(x) for x in f]
    for i in invs:
        i["volume"] = i.get("vol") or i["cond"]
    return invs


def window(invs: list[dict]) -> list[datetime]:
    out: set[datetime] = set()
    for inv in invs:
        t, end = minute(inv["start_utc"]), minute(inv["end_utc"]) + timedelta(minutes=1)
        while t <= end:
            out.add(t)
            t += timedelta(minutes=1)
    return sorted(out)


def write_buckets(cw: CW, vol: str) -> list[datetime]:
    return sorted(t for (n, m, t), x in cw.items()
                  if n == vol and m == "CapacityPoolWriteOperations" and x > 0)


class Read:
    """One first read of one volume: the invocations, and what the files say about it."""

    def __init__(self, exp: str, cw_label: str, path: str, label: str, invs: list[dict]) -> None:
        self.exp, self.cw_label, self.path, self.label, self.invs = exp, cw_label, path, label, invs
        self.vol = NAMES[cw_label]
        self.start = min(ts(i["start_utc"]) for i in invs)
        self.last_start = max(ts(i["start_utc"]) for i in invs)
        self.cp_ops = 0.0
        self.last_end = self.start
        self.first_start: datetime | None = None
        self.dataread: CW = {}
        self.others: list[str] = []
        self.fs: dict[datetime, float] = {}

    @property
    def at_least(self) -> float:
        return down(mins(self.start, self.last_end))

    @property
    def at_most(self) -> float | None:
        if self.first_start is None:
            return None
        return up(mins(self.last_start, self.first_start))


def build() -> list[Read]:
    reads: list[Read] = []

    # --- experiment 1, the tier comparison: B (policy ALL from creation); A1 and A2 are the others
    cw1 = load_cw(os.path.join(TIER, "cw-all.tsv"))
    b_tiering = load_cw(os.path.join(TIER, "cw-b-tiering.tsv"))
    dr1 = load_cw(os.path.join(HERE, "tier-comparison-cw-dataread.tsv"))
    fs1 = load_fs(os.path.join(TIER, "fs-DataReadBytes.tsv"))
    invs1 = load_invs(os.path.join(TIER, "invocations.jsonl"))
    b_last_end = write_buckets(b_tiering, "b")[-1] + timedelta(minutes=1)
    for i in invs1:
        if i["volume"] == "b" and i["tag"].startswith("tier-r"):
            r = Read("experiment 1", "b", "direct", i["tag"].replace("tier-", "").replace("-b-", " "), [i])
            r.last_end = b_last_end
            r.first_start = None  # pull range of cw-b-tiering.tsv not recorded: no "at most"
            r.cp_ops = sum(cw1.get(("b", "CapacityPoolReadOperations", t), 0) for t in window([i]))
            r.dataread, r.others, r.fs = dr1, ["a1", "a2"], fs1
            reads.append(r)

    # --- experiment 2: K1, S3p direct; S2, S3, S4 switched
    cw2 = load_cw(os.path.join(PATH, "cw-all.tsv"))
    fs2 = load_fs(os.path.join(HERE, "fs-DataReadBytes-experiment-2.tsv"))
    invs2 = load_invs(os.path.join(PATH, "invocations.jsonl"))
    vols2 = ["k1", "s2", "s3", "s3p", "s4"]
    spec2 = [("k1", "direct", [i for i in invs2 if i["tag"].startswith("x2-k1first-")], "first reads (12)"),
             ("s3p", "direct", [i for i in invs2 if i["tag"] == "x2-cold-s3p"], "first read"),
             ("s2", "switch", [i for i in invs2 if i["tag"] == "x2-cold-s2"], "first read"),
             ("s3", "switch", [i for i in invs2 if i["tag"] == "x2-cold-s3"], "first read"),
             ("s4", "switch", [i for i in invs2 if i["tag"] == "x2-cold-s4"], "first read")]
    for vol, path, sel, label in spec2:
        r = Read("experiment 2", vol, path, label, sel)
        wb = write_buckets(cw2, vol)
        r.first_start, r.last_end = wb[0], wb[-1] + timedelta(minutes=1)
        r.cp_ops = sum(cw2.get((vol, "CapacityPoolReadOperations", t), 0) for t in window(sel))
        r.dataread, r.others, r.fs = cw2, [v for v in vols2 if v != vol], fs2
        reads.append(r)

    # --- experiment 3: DIR direct, SW switched; cohort 1 primary, cohort 2 auxiliary
    cw3 = load_cw(os.path.join(HERE, "cw-all.tsv"))
    fs3 = load_fs(os.path.join(HERE, "fs-DataReadBytes-experiment-3.tsv"))
    invs3 = load_invs(os.path.join(HERE, "invocations.jsonl"))
    for vol, path in (("dir", "direct"), ("sw", "switch")):
        for c in (1, 2):
            sel = [i for i in invs3 if i["tag"] == f"x3-{vol}-c{c}"]
            r = Read("experiment 3", vol, path, f"cohort {c} ({'primary' if c == 1 else 'auxiliary'})", sel)
            wb = write_buckets(cw3, vol)
            r.first_start, r.last_end = wb[0], wb[-1] + timedelta(minutes=1)
            r.cp_ops = sum(cw3.get((vol, "CapacityPoolReadOperations", t), 0) for t in window(sel))
            r.dataread, r.others, r.fs = cw3, [v for v in ("dir", "sw") if v != vol], fs3
            reads.append(r)
    return reads


def overlap(a: tuple[float, float | None], b: tuple[float, float | None]) -> bool:
    a_hi = math.inf if a[1] is None else a[1]
    b_hi = math.inf if b[1] is None else b[1]
    return a[0] <= b_hi and b[0] <= a_hi


def buckets_between(e: datetime, m: datetime) -> list[datetime]:
    out, t = [], e
    while t < m:
        out.append(t)
        t += timedelta(minutes=1)
    return out


def main() -> int:
    reads = build()

    # ------------------------------------------------------------------ 1. first reads
    print("== 1. first reads of the eight volumes: write path, capacity pool reads, elapsed time (minutes)")
    print(f"{'experiment':13s} {'volume':6s} {'path':7s} {'read':20s} {'starts (UTC)':26s} "
          f"{'CP read ops':>11s}  {'at least':>8s} {'at most':>8s}")
    for r in reads:
        starts = hms(r.start) if r.start == r.last_start else f"{hms(r.start)}..{hms(r.last_start)}"
        most = "none" if r.at_most is None else f"{r.at_most:.1f}"
        print(f"{r.exp:13s} {r.vol:6s} {r.path:7s} {r.label:20s} {starts:26s} "
              f"{r.cp_ops:11.0f}  {r.at_least:8.1f} {most:>8s}")
    print("B has no 'at most': the pull range of cw-b-tiering.tsv was not recorded, so it does not show "
          "whether tiering writes occurred before its first bucket.")

    # ------------------------------------------------------------------ 2. path against result
    print("\n== 2. write path against the result of the first read (volumes)")
    positive: dict[str, set[str]] = collections.defaultdict(set)
    zero: dict[str, set[str]] = collections.defaultdict(set)
    for r in reads:
        (positive if r.cp_ops > 0 else zero)[r.path].add(r.vol)
    # a volume counts as positive if any of its first reads caused capacity pool reads
    for path in ("direct", "switch"):
        zero[path] -= positive[path]
    for path in ("direct", "switch"):
        print(f"  written {path:6s}: first read with capacity pool reads: {sorted(positive[path]) or 'none'}; "
              f"first read with none: {sorted(zero[path]) or 'none'}")
    n_direct = len(positive["direct"] | zero["direct"])
    n_switch = len(positive["switch"] | zero["switch"])
    print(f"  volumes written directly: {n_direct}; volumes switched from NONE to ALL: {n_switch}")
    print(f"  every volume with a positive first read was written directly: {not positive['switch']}")
    print(f"  a volume written directly had no capacity pool reads at its first reads: {sorted(zero['direct'])}")
    print("  B counts as positive: its 1 MiB and 5 MiB reads had capacity pool reads in all three runs; "
          "its 64 KiB reads had none in any run.")

    # ------------------------------------------------------------------ 3. elapsed-time overlap
    print("\n== 3. elapsed time: does the range of a read with no capacity pool reads overlap one with?")
    print("  only reads with both ends are compared (every read except those of B, which has lower ends only)")
    bounded = [r for r in reads if r.at_most is not None]
    pos_bounded = [r for r in bounded if r.cp_ops > 0]
    print("  ranges of the reads with capacity pool reads and both ends:")
    for p in pos_bounded:
        print(f"    {p.exp:13s} {p.vol:4s} {p.path:7s} {p.label:20s} {p.at_least:.1f} to {p.at_most:.1f}")
    print("  ranges of the reads with no capacity pool reads and both ends:")
    for r in bounded:
        if r.cp_ops > 0:
            continue
        rng = (r.at_least, r.at_most)
        inside = [f"{p.vol} {p.label}" for p in pos_bounded if p.at_least <= rng[0] and rng[1] <= p.at_most]
        over = [f"{p.vol} {p.label}" for p in pos_bounded if overlap(rng, (p.at_least, p.at_most))]
        print(f"    {r.exp:13s} {r.vol:4s} {r.path:7s} {r.label:20s} {r.at_least:.1f} to {r.at_most:.1f}: "
              f"inside the range of {inside or 'none'}; overlaps the range of {over or 'none'}")
    print("  ('overlaps' includes a shared end point; the ranges are rounded outward, so a shared end point "
          "is not evidence of a shared time)")
    print("  B, lower ends only, in read order (minutes since the end of the last tiering write bucket; "
          "result):")
    for r in reads:
        if r.at_most is None:
            print(f"    {r.label:8s} at least {r.at_least:5.1f}  capacity pool read ops {r.cp_ops:4.0f}")

    # ------------------------------------------------------------------ 4. bytes read in between
    print("\n== 4. (post-hoc) bytes read between the last tiering write bucket and the first read")
    print("(a) other volumes of the experiment; (b) the volume's own earlier reads; (c) file-system-level "
          "DataReadBytes over the same buckets\n")
    print(f"{'experiment':13s} {'volume':6s} {'read':20s} {'E':6s} {'m':6s} {'(a) others B':>15s} "
          f"{'(a) MB':>8s} {'(b) own B':>12s} {'(c) FS B':>15s} {'(c) MB':>8s} {'(c)-(a)-(b) B':>13s} "
          f"{'CP ops':>6s}")
    largest = 0.0
    for r in reads:
        e, m_ = r.last_end, r.start.replace(second=0, microsecond=0)
        bs = buckets_between(e, m_)
        per = {v: sum(r.dataread.get((v, "DataReadBytes", t), 0) for t in bs) for v in r.others}
        others = sum(per.values())
        own = sum(r.dataread.get((r.cw_label, "DataReadBytes", t), 0) for t in bs)
        covered = [t for t in bs if t in r.fs]
        missing = [t for t in bs if t not in r.fs]
        fs_sum = sum(r.fs[t] for t in covered)
        # the experiment-level sum over the buckets the file-system file covers
        same = sum(r.dataread.get((v, "DataReadBytes", t), 0)
                   for v in r.others + [r.cw_label] for t in covered)
        diff = fs_sum - same
        largest = max(largest, abs(diff))
        print(f"{r.exp:13s} {r.vol:6s} {r.label:20s} {hm(e):6s} {hm(m_):6s} {others:15,.0f} "
              f"{others / 1e6:8.1f} {own:12,.0f} {fs_sum:15,.0f} {fs_sum / 1e6:8.1f} {diff:13,.0f} "
              f"{r.cp_ops:6.0f}")
        contrib = ", ".join(f"{v}={int(x):,}" for v, x in per.items() if x)
        note = f"      (a) by volume: {contrib or 'none'}"
        if missing:
            note += (f"; the file-system file has no point for {len(missing)} of {len(bs)} buckets "
                     f"({hm(missing[0])} to {hm(missing[-1])}), compared by the per-volume file only")
        print(note)
    print(f"\nlargest absolute difference between (c) and (a) + (b) over the buckets that (c) covers: "
          f"{largest:,.0f} B")
    print("E is the end of the last one-minute bucket with tiering writes of the volume; m is the start "
          "minute of its first read. Buckets t with E <= t < m are counted, with the labels as recorded.")
    print("(c) counts every volume on the file system, so a read on a volume outside the experiment "
          "would show in (c) - (a) - (b). DataReadBytes does not measure writes or non-data activity.")
    print("Not counted by any column: writes by other volumes that overlapped, and any read whose bucket "
          "label differs from the minute of the read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
