"""The eleven volumes of the four tiering experiments of 2026-10-11 (derived, post-hoc).

Experiments 1 to 3 are the eight volumes of analyze_cross_experiment.py in
../2026-10-11-tier-elapsed/ (this script imports its build() and rules, so the first eight
volumes are computed exactly as there). Experiment 4 is this directory: Q, P1, and P2, all
created with policy ALL and written directly. Reads only committed files:

  this directory             cw-all.tsv, fs-DataReadBytes.tsv, invocations.jsonl
  ../2026-10-11-tier-elapsed/  analyze_cross_experiment.py and the files it reads
  ../2026-10-11-tier/, ../2026-10-11-tier-path/   (through that script)

Run from any directory: python3 analyze_eleven_volumes.py

Blocks, in order:
  1. First reads of the eleven volumes: write path, whether the first read caused capacity pool
     read operations, the elapsed time from the data entering the capacity pool (rule of
     analyze_cross_experiment.py), and the bytes read by other volumes of the experiment before
     the read by the one-minute bucket rule of experiments 1 to 3 (post-hoc). For experiment 4
     the per-invocation figure is in analyze_other_reads.py.
  2. The write path against the result: how many volumes had a positive first read, by path.
  3. DIR (experiment 3) against Q (experiment 4): both created with ALL and written directly,
     random content, 127,205,376 B, storage efficiency flag specified as false, no reads of the
     volume before its cohort 1; elapsed time, other reads, and result of cohort 1 and cohort 2.
  4. Elapsed time: whether a one-sided threshold ("capacity pool reads only above T", "only
     below T") fits the bounds of the reads with both ends, with the bounds as recorded and
     with the allowance that a bucket label can be up to one minute later than the writes (every
     "at most" rises by 1.0).

The differences between experiments 3 and 4 that the files show are printed in block 3 as facts;
the script does not rank them.
"""
from __future__ import annotations

import collections
import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ELAPSED = os.path.join(HERE, "..", "2026-10-11-tier-elapsed")
sys.dont_write_bytecode = True  # do not leave a __pycache__ in the sibling result directory
sys.path.insert(0, ELAPSED)
import analyze_cross_experiment as cross  # noqa: E402  (same rules, same Read class)

cross.NAMES.update({"q": "Q", "p1": "P1", "p2": "P2"})
DIRECT4 = ("q", "p1", "p2")
VOLS4 = ("q", "p1", "p2", "chn")
ORDER = ("B", "K1", "S3p", "DIR", "Q", "P1", "P2", "S2", "S3", "S4", "SW")


def build_exp4() -> list:
    cw = cross.load_cw(os.path.join(HERE, "cw-all.tsv"))
    fs = cross.load_fs(os.path.join(HERE, "fs-DataReadBytes.tsv"))
    invs = cross.load_invs(os.path.join(HERE, "invocations.jsonl"))
    reads = []
    for vol in DIRECT4:
        for c in (1, 2):
            sel = [i for i in invs if i["tag"] == f"x4-{vol}-c{c}"]
            r = cross.Read("experiment 4", vol, "direct", f"cohort {c} ({'primary' if c == 1 else 'auxiliary'})", sel)
            wb = cross.write_buckets(cw, vol)
            r.first_start, r.last_end = wb[0], wb[-1] + timedelta(minutes=1)
            r.cp_ops = sum(cw.get((vol, "CapacityPoolReadOperations", t), 0) for t in cross.window(sel))
            r.dataread, r.others, r.fs = cw, [v for v in VOLS4 if v != vol], fs
            reads.append(r)
    return reads


def others_mb(r) -> float:
    e, m_ = r.last_end, r.start.replace(second=0, microsecond=0)
    bs = cross.buckets_between(e, m_)
    return sum(r.dataread.get((v, "DataReadBytes", t), 0) for v in r.others for t in bs) / 1e6


def main() -> int:
    reads = cross.build() + build_exp4()
    by_vol: dict[str, list] = collections.defaultdict(list)
    for r in reads:
        by_vol[r.vol].append(r)

    # ------------------------------------------------------------------ 1. first reads
    print("== 1. first reads of the eleven volumes (post-hoc table)")
    print(f"{'volume':6s} {'experiment':13s} {'path':7s} {'read':20s} {'CP read ops':>11s} "
          f"{'at least':>8s} {'at most':>8s} {'others MB (bucket rule)':>24s}")
    for name in ORDER:
        for r in by_vol[name]:
            most = "none" if r.at_most is None else f"{r.at_most:.1f}"
            print(f"{r.vol:6s} {r.exp:13s} {r.path:7s} {r.label:20s} {r.cp_ops:11.0f} "
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
        print(f"  written {path:6s}: first read with capacity pool reads: {sorted(positive[path]) or 'none'}; "
              f"first read with none: {sorted(zero[path]) or 'none'}")
    nd = len(positive["direct"] | zero["direct"])
    ns = len(positive["switch"] | zero["switch"])
    print(f"  volumes written directly: {nd} ({len(positive['direct'])} with capacity pool reads, "
          f"{len(zero['direct'])} with none); volumes switched from NONE to ALL: {ns} "
          f"({len(positive['switch'])} with capacity pool reads, {len(zero['switch'])} with none)")
    print(f"  every volume with capacity pool reads was written directly: {not positive['switch']}")
    print(f"  a volume written directly with none at all of its reads: {sorted(zero['direct'])}")
    print("  S3p counts as positive by operation count (374 operations; its f is 0.014, below 0.10) "
          "and by g, not by the f rule fixed before its run; B counts as positive by its 1 MiB and 5 MiB reads.")

    # ------------------------------------------------------------------ 3. DIR against Q
    print("\n== 3. DIR (experiment 3) against Q (experiment 4): the two directly written volumes with "
          "almost the same other-reads dose")
    print(f"{'volume':6s} {'read':20s} {'elapsed (min)':>14s} {'others MB (bucket rule)':>24s} "
          f"{'CP read ops':>11s}")
    for name in ("DIR", "Q"):
        for r in by_vol[name]:
            print(f"{name:6s} {r.label:20s} {r.at_least:6.1f} to {r.at_most:4.1f} {others_mb(r):24.1f} "
                  f"{r.cp_ops:11.0f}")
    dir1, dir2 = by_vol["DIR"][0], by_vol["DIR"][1]
    q1, q2 = by_vol["Q"][0], by_vol["Q"][1]
    print(f"  cohort 1: others {others_mb(dir1):.1f} MB (DIR) and {others_mb(q1):.1f} MB (Q), a difference of "
          f"{(others_mb(q1) - others_mb(dir1)) * 1e6:,.0f} B; DIR none, Q positive")
    print(f"  cohort 2 (auxiliary): ranges {dir2.at_least:.1f} to {dir2.at_most:.1f} (DIR) and "
          f"{q2.at_least:.1f} to {q2.at_most:.1f} (Q) overlap: "
          f"{cross.overlap((dir2.at_least, dir2.at_most), (q2.at_least, q2.at_most))}; DIR none, Q positive")
    print("  facts from the files (not ranked, none shown to be a cause):")
    chn_up = [ln for ln in open(os.path.join(HERE, "manifest.txt")) if ln.startswith("chn\t")]
    chn_b = sum(int(ln.split("\t")[2]) for ln in chn_up)
    print(f"    - experiment 4: {chn_b:,} B were written to the NONE volume chn (run log: 18:03:00 to 18:03:39), "
          f"before the three direct writes (18:03:51 to 18:04:29); experiment 3 had no earlier large write")
    print("    - experiment 4 wrote 3 volumes in parallel, experiment 3 wrote 2")
    print("    - experiment 3 had a switched volume (SW) tiering out at the same time as DIR "
          "(SW's tiering write bucket 15:57, DIR's 15:56); experiment 4 had no switched volume")

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
        print("    pairs among the directly written volumes of experiments 3 and 4 (DIR, Q, P1, P2):")
        for z, p in above + below:
            if z.vol == "DIR" and p.exp == "experiment 4":
                kind = "only above T" if (z, p) in above else "only below T"
                print(f"      excludes '{kind}': none DIR {z.label} {z.at_least:.1f} to {z.at_most + allowance:.1f}; "
                      f"reads {p.vol} {p.label} {p.at_least:.1f} to {p.at_most + allowance:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
