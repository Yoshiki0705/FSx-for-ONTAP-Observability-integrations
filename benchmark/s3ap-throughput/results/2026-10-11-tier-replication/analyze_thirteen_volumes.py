"""The thirteen volumes of the five tiering experiments of 2026-10-11 (derived, post-hoc).

Experiments 1 to 3 are the eight volumes of analyze_cross_experiment.py in
../2026-10-11-tier-elapsed/ (imported, so the first eight volumes are computed exactly as
there). Experiment 4 is the three volumes of analyze_eleven_volumes.py in
../2026-10-11-tier-other-reads/ (its build_exp4() is imported). Experiment 5 is this directory:
DIR (created with ALL, written directly) and SW (created with NONE, changed to ALL), a
replication of experiment 3. Reads only committed files:

  this directory               cw-all.tsv, fs-DataReadBytes.tsv, invocations.jsonl
  ../2026-10-11-tier-other-reads/   analyze_eleven_volumes.py and the files it reads
  ../2026-10-11-tier-elapsed/       analyze_cross_experiment.py and the files it reads
  ../2026-10-11-tier/, ../2026-10-11-tier-path/   (through those scripts)

Run from any directory: python3 analyze_thirteen_volumes.py

Blocks, in order:
  1. First reads of the thirteen volumes: write path, whether the first read caused capacity
     pool read operations, the elapsed time from the data entering the capacity pool (rule of
     analyze_cross_experiment.py), and the bytes read by other volumes of the experiment before
     the read by the one-minute bucket rule of experiments 1 to 3 (post-hoc).
  2. The write path against the result: how many volumes had a positive first read, by path.
     The thirteen-volume counts (8 direct, 6 positive, 5 switched, 13 total) are printed here;
     this is the single source for them.
  3. The two directly written volumes that read zero (experiment 3 DIR and experiment 5 DIR):
     both created with ALL, written directly, random content, 127,205,376 B, storage efficiency
     flag specified as false, no reads of the volume before cohort 1; elapsed time and result.
  4. Elapsed time: whether a one-sided threshold ("capacity pool reads only above T", "only
     below T") fits the bounds of the reads with both ends, with the bounds as recorded and
     with the allowance that a bucket label can be up to one minute later than the writes.

Experiment 5 replicates experiment 3's design (two volumes, no large preceding write, a
switched volume tiering out at the same time). Which of those three conditions matters is not
isolated; the script states the facts and does not rank them.
"""
from __future__ import annotations

import collections
import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OTHER_READS = os.path.join(HERE, "..", "2026-10-11-tier-other-reads")
ELAPSED = os.path.join(HERE, "..", "2026-10-11-tier-elapsed")
sys.dont_write_bytecode = True  # do not leave a __pycache__ in the sibling result directories
sys.path.insert(0, OTHER_READS)
sys.path.insert(0, ELAPSED)
import analyze_cross_experiment as cross  # noqa: E402  (same rules, same Read class)
import analyze_eleven_volumes as eleven  # noqa: E402  (its build_exp4 for experiment 4)

# distinct display names so experiment 3 DIR/SW are not confused with experiment 5 DIR/SW
cross.NAMES.update({"dir5": "DIR5", "sw5": "SW5"})
VOLS5 = ("dir", "sw")
PATH5 = {"dir": "direct", "sw": "switch"}
ORDER = ("B", "K1", "S3p", "DIR", "DIR5", "Q", "P1", "P2",
         "S2", "S3", "S4", "SW", "SW5")
DISPLAY = {"DIR5": "DIR (exp 5)", "SW5": "SW (exp 5)"}


def build_exp5() -> list:
    cw = cross.load_cw(os.path.join(HERE, "cw-all.tsv"))
    fs = cross.load_fs(os.path.join(HERE, "fs-DataReadBytes.tsv"))
    invs = cross.load_invs(os.path.join(HERE, "invocations.jsonl"))
    reads = []
    for vol in VOLS5:
        for c in (1, 2):
            sel = [i for i in invs if i["tag"] == f"x5-{vol}-c{c}"]
            r = cross.Read("experiment 5", vol, PATH5[vol],
                           f"cohort {c} ({'primary' if c == 1 else 'auxiliary'})", sel)
            r.vol = {"dir": "DIR5", "sw": "SW5"}[vol]  # distinct display name
            wb = cross.write_buckets(cw, vol)
            r.first_start, r.last_end = wb[0], wb[-1] + timedelta(minutes=1)
            r.cp_ops = sum(cw.get((vol, "CapacityPoolReadOperations", t), 0) for t in cross.window(sel))
            r.dataread, r.others, r.fs = cw, [v for v in VOLS5 if v != vol], fs
            reads.append(r)
    return reads


def others_mb(r) -> float:
    e, m_ = r.last_end, r.start.replace(second=0, microsecond=0)
    bs = cross.buckets_between(e, m_)
    return sum(r.dataread.get((v, "DataReadBytes", t), 0) for v in r.others for t in bs) / 1e6


def disp(name: str) -> str:
    return DISPLAY.get(name, name)


def main() -> int:
    reads = cross.build() + eleven.build_exp4() + build_exp5()
    by_vol: dict[str, list] = collections.defaultdict(list)
    for r in reads:
        by_vol[r.vol].append(r)

    # ------------------------------------------------------------------ 1. first reads
    print("== 1. first reads of the thirteen volumes (post-hoc table)")
    print(f"{'volume':11s} {'experiment':13s} {'path':7s} {'read':20s} {'CP read ops':>11s} "
          f"{'at least':>8s} {'at most':>8s} {'others MB (bucket rule)':>24s}")
    for name in ORDER:
        for r in by_vol[name]:
            most = "none" if r.at_most is None else f"{r.at_most:.1f}"
            print(f"{disp(r.vol):11s} {r.exp:13s} {r.path:7s} {r.label:20s} {r.cp_ops:11.0f} "
                  f"{r.at_least:8.1f} {most:>8s} {others_mb(r):24.1f}")
    print("B has no 'at most' (pull range of cw-b-tiering.tsv not recorded). B's rows are its nine "
          "reads: 64 KiB, 1 MiB, 5 MiB of runs 1 to 3; its 64 KiB reads had none in any run.")

    # ------------------------------------------------------------------ 2. path against result
    print("\n== 2. write path against the result of the first read (volumes)")
    positive: dict[str, set[str]] = collections.defaultdict(set)
    zero: dict[str, set[str]] = collections.defaultdict(set)
    for r in reads:
        (positive if r.cp_ops > 0 else zero)[r.path].add(r.vol)
    for path in ("direct", "switch"):
        zero[path] -= positive[path]
        pos_names = sorted(disp(v) for v in positive[path])
        zero_names = sorted(disp(v) for v in zero[path])
        print(f"  written {path:6s}: first read with capacity pool reads: {pos_names or 'none'}; "
              f"first read with none: {zero_names or 'none'}")
    nd = len(positive["direct"] | zero["direct"])
    ns = len(positive["switch"] | zero["switch"])
    print(f"  volumes written directly: {nd} ({len(positive['direct'])} with capacity pool reads, "
          f"{len(zero['direct'])} with none); volumes switched from NONE to ALL: {ns} "
          f"({len(positive['switch'])} with capacity pool reads, {len(zero['switch'])} with none)")
    print(f"  total volumes: {nd + ns}")
    print(f"  every volume with capacity pool reads was written directly: {not positive['switch']}")
    print(f"  directly written volumes with none at all of their reads: {sorted(disp(v) for v in zero['direct'])}")
    print("  S3p counts as positive by operation count (374 operations; its f is 0.014, below 0.10) "
          "and by g, not by the f rule fixed before its run; B counts as positive by its 1 MiB and 5 MiB reads.")

    # ------------------------------------------------------------------ 3. the two directly written zeros
    print("\n== 3. the two directly written volumes that read zero: experiment 3 DIR and experiment 5 DIR")
    print(f"{'volume':11s} {'read':20s} {'elapsed (min)':>14s} {'others MB (bucket rule)':>24s} "
          f"{'CP read ops':>11s}")
    for name in ("DIR", "DIR5"):
        for r in by_vol[name]:
            print(f"{disp(name):11s} {r.label:20s} {r.at_least:6.1f} to {r.at_most:4.1f} "
                  f"{others_mb(r):24.1f} {r.cp_ops:11.0f}")
    d3c1 = by_vol["DIR"][0]
    d5c1 = by_vol["DIR5"][0]
    print(f"  cohort 1: experiment 3 DIR {d3c1.at_least:.1f} to {d3c1.at_most:.1f} min, "
          f"experiment 5 DIR {d5c1.at_least:.1f} to {d5c1.at_most:.1f} min; both none")
    print("  experiment 5 reproduces experiment 3: a directly written volume (DIR) read zero on its "
          "first read in both runs. The DIR zero is not a one-off.")
    print("  facts (not ranked, none shown to be a cause): experiment 5 replicated experiment 3's "
          "design -- two volumes uploaded in parallel, no large preceding write, a switched volume (SW) "
          "tiering out at the same time. Which of the three conditions matters is not isolated.")

    # ------------------------------------------------------------------ 4. thresholds
    print("\n== 4. does a one-sided threshold on the elapsed time fit the bounds? (reads with both ends)")
    bounded = [r for r in reads if r.at_most is not None]
    pos = [r for r in bounded if r.cp_ops > 0]
    neg = [r for r in bounded if r.cp_ops == 0]
    print(f"  reads with both ends: {len(bounded)} ({len(pos)} with capacity pool reads, {len(neg)} with none)")
    for allowance in (0.0, 1.0):
        above = [(z, p) for z in neg for p in pos if z.at_least > p.at_most + allowance]
        below = [(z, p) for z in neg for p in pos if z.at_most + allowance < p.at_least]
        print(f"  every 'at most' raised by {allowance:.1f} minute(s): 'only above T' is excluded by "
              f"{len(above)} pairs (a read with none is definitely longer than a read with capacity pool "
              f"reads), 'only below T' by {len(below)} pairs (definitely shorter)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
