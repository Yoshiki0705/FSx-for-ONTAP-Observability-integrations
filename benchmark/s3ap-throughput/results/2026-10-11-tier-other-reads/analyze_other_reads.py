"""Reads of other volumes before each read of q, p1, and p2, counted two ways (post-hoc).

Reads only the files committed next to this script (cw-all.tsv, fs-DataReadBytes.tsv,
invocations.jsonl, run-log-times.tsv, raw/). Run from any directory:
python3 analyze_other_reads.py

The question behind the table: how many bytes did the other volumes of this run read between
the end of a volume's last tiering write bucket and the start of its read? Experiments 1 to 3
counted this with one-minute CloudWatch buckets (analyze_cross_experiment.py in
../2026-10-11-tier-elapsed/). This run counts it a second time, per invocation, from the bytes
each invocation read. Both are post-hoc: the plan fixed the doses (about 0.5, 200, and 800 MB)
before the first invocation, and the bucket rule and the per-invocation recount were chosen or
added after the results were seen.

Rules, in order:
  1. The bucket rule of experiments 1 to 3. The buckets counted are the one-minute CloudWatch
     DataReadBytes buckets t with E <= t < m, labels as recorded, where E is the end of the last
     one-minute bucket with CapacityPoolWriteOperations above 0 of the volume and m is the start
     minute of its read. (a) the other volumes of the run; (b) the volume's own earlier reads in
     the same buckets; (c) the file-system-level DataReadBytes over the same buckets and its
     difference from (a) + (b).
  2. The per-invocation recount (post-hoc). The bytes of an invocation are its GetObject reads in
     raw/: the sum over objects of size_bytes times the number of samples. An invocation counts
     for a read when it ran on another volume of the run, started after the end of the direct
     writes (U, run-log-times.tsv), and ended before the read started (client-side times in
     invocations.jsonl). The three warm-up invocations (262,144 B each) therefore count; they ran
     at 18:05:14 to 18:05:18, before the end of the last tiering write bucket (18:06:00) by the
     clock, and their operations are labelled 18:06 by CloudWatch.
  3. The difference between the two, invocation by invocation: the bytes the invocation read,
     the CloudWatch buckets in which its operations are labelled, and whether the bucket rule
     counted them.

Why the bucket rule undercounts here. In this run the operations of an invocation are labelled
by CloudWatch in a later minute than the one in which the invocation ran: the invocation chn-b
ended at 18:13:13.7 and 15 of its 110 reads are labelled 18:14, and the invocations that started
at hh:mm:29 are labelled entirely in the following minute (block 3 lists every invocation). A
read of another volume that finished before a later read started can therefore carry a label
equal to the later read's start minute m. The rule t < m leaves that bucket out, so the reads
are missed although they were finished earlier. Two reads are affected here: chn-a (131 MB, label
18:11 = m of p1 cohort 1) and the last 15 reads of chn-b (78.6 MB, label 18:14 = m of p2
cohort 1). The cause of the labelling was not examined. The per-invocation recount does not
depend on the label. It has its own limit: it uses client-side times to the second and counts an
invocation whole, also when the volume's tiering writes were still in the same minute (the
warm-ups).

cw-all.tsv holds only datapoints whose Sum is above 0.
"""
from __future__ import annotations

import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DIRECT = ("q", "p1", "p2")
VOLS = ("q", "p1", "p2", "chn")
READS = ("x4-q-c1", "x4-p1-c1", "x4-p2-c1", "x4-q-c2", "x4-p1-c2", "x4-p2-c2")


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def minute(s: str) -> datetime:
    return ts(s).replace(second=0, microsecond=0)


def hm(t: datetime) -> str:
    return t.strftime("%H:%M")


def hmsf(t: datetime) -> str:
    return t.strftime("%H:%M:%S.") + f"{t.microsecond // 100000}"


def window(inv: dict) -> list[datetime]:
    t, end = minute(inv["start_utc"]), minute(inv["end_utc"]) + timedelta(minutes=1)
    out = []
    while t <= end:
        out.append(t)
        t += timedelta(minutes=1)
    return out


def main() -> int:
    cw: dict[tuple[str, str, datetime], float] = {}
    with open(os.path.join(HERE, "cw-all.tsv")) as f:
        for line in f:
            vol, metric, t, val = line.rstrip("\n").split("\t")
            cw[(vol, metric, minute(t))] = float(val)
    fs: dict[datetime, float] = {}
    with open(os.path.join(HERE, "fs-DataReadBytes.tsv")) as f:
        for line in f:
            t, val = line.rstrip("\n").split("\t")
            fs[minute(t)] = float(val)
    with open(os.path.join(HERE, "invocations.jsonl")) as f:
        invs = [json.loads(x) for x in f]
    by_tag = {i["tag"]: i for i in invs}
    events: dict[str, datetime] = {}
    with open(os.path.join(HERE, "run-log-times.tsv")) as f:
        next(f)
        for line in f:
            event, _vols, t = line.rstrip("\n").split("\t")
            events[event] = ts(t.split(" to ")[0])
    u_end = events["direct_upload_end_U"]

    # bytes each invocation read, from raw/
    inv_bytes: dict[str, int] = {}
    for i in invs:
        with open(os.path.join(HERE, "raw", f"{i['tag']}.json")) as f:
            per_object = json.load(f)["result"]["per_object"]
        inv_bytes[i["tag"]] = sum(o["size_bytes"] * len(o["samples_ms"]) for o in per_object)

    # E per direct volume: end of the last bucket with tiering writes
    last_end = {}
    for v in DIRECT:
        last_end[v] = max(t for (n, m, t), x in cw.items()
                          if n == v and m == "CapacityPoolWriteOperations" and x > 0) + timedelta(minutes=1)

    # CloudWatch buckets of each invocation (windows of one volume do not overlap)
    labelled: dict[str, dict[datetime, float]] = {}
    for i in invs:
        labelled[i["tag"]] = {t: cw[(i["vol"], "DataReadBytes", t)] for t in window(i)
                              if (i["vol"], "DataReadBytes", t) in cw}
    overlap = [(a["tag"], b["tag"]) for a in invs for b in invs
               if a["tag"] < b["tag"] and a["vol"] == b["vol"] and set(window(a)) & set(window(b))]

    print("== 0. inputs")
    print(f"invocations: {len(invs)}; bytes read per invocation from raw/ "
          f"(total {sum(inv_bytes.values()):,} B)")
    print(f"windows of two invocations on the same volume that overlap: {overlap or 'none'}")
    print(f"end of the direct writes U (run-log-times.tsv): {hmsf(u_end)}Z; start of the first "
          f"invocation of the run: {hmsf(min(ts(i['start_utc']) for i in invs))}Z")
    early = sum(x for t, x in fs.items() if t < minute("2026-10-11T18:06:00Z"))
    print(f"file-system-level DataReadBytes in the buckets before 18:06Z: {early:,.0f} B "
          f"(so no volume of the file system was read before the first invocation)")
    print("E per volume (end of the last bucket with tiering writes): "
          + ", ".join(f"{v} {hm(last_end[v])}Z" for v in DIRECT))

    # ------------------------------------------------------------------ 1. bucket rule
    print("\n== 1. bucket rule of experiments 1 to 3: one-minute buckets t with E <= t < m (MB = 10^6 B)")
    print(f"{'read':10s} {'E':6s} {'m':6s} {'(a) others B':>14s} {'(a) MB':>8s} {'(b) own B':>12s} "
          f"{'(c) FS B':>14s} {'(c)-(a)-(b) B':>13s}  (a) by volume")
    bucket_a: dict[str, float] = {}
    largest = 0.0
    for tag in READS:
        i = by_tag[tag]
        v = i["vol"]
        e, m_ = last_end[v], minute(i["start_utc"])
        bs, t = [], e
        while t < m_:
            bs.append(t)
            t += timedelta(minutes=1)
        per = {o: sum(cw.get((o, "DataReadBytes", t), 0) for t in bs) for o in VOLS if o != v}
        a = sum(per.values())
        own = sum(cw.get((v, "DataReadBytes", t), 0) for t in bs)
        fsum = sum(fs.get(t, 0) for t in bs)
        diff = fsum - a - own
        largest = max(largest, abs(diff))
        bucket_a[tag] = a
        contrib = ", ".join(f"{o}={int(x):,}" for o, x in per.items() if x)
        print(f"{tag:10s} {hm(e):6s} {hm(m_):6s} {a:14,.0f} {a / 1e6:8.1f} {own:12,.0f} {fsum:14,.0f} "
              f"{diff:13,.0f}  {contrib or 'none'}")
    print(f"largest absolute difference (c) - (a) - (b): {largest:,.0f} B "
          f"(a nonzero value is a read on a volume outside the run, or a bucket shifted between the files)")

    # ------------------------------------------------------------------ 2. per invocation
    print("\n== 2. per-invocation recount (post-hoc): bytes read by the invocations of other volumes "
          "that started after U and ended before the read started")
    inv_a: dict[str, int] = {}
    members: dict[str, list[str]] = {}
    for tag in READS:
        i = by_tag[tag]
        v, start = i["vol"], ts(i["start_utc"])
        mem = [j["tag"] for j in invs if j["vol"] != v and ts(j["start_utc"]) > u_end
               and ts(j["end_utc"]) < start]
        members[tag] = mem
        inv_a[tag] = sum(inv_bytes[t] for t in mem)
        print(f"{tag:10s} read starts {hmsf(start)}Z  others={inv_a[tag]:13,} B = {inv_a[tag] / 1e6:7.1f} MB "
              f"({inv_a[tag] / 1e6:.3f}); {len(mem)} invocations:")
        print("           " + ", ".join(f"{t} {inv_bytes[t]:,}" for t in mem))

    # ------------------------------------------------------------------ 3. the difference
    print("\n== 3. bucket rule against the per-invocation recount")
    print(f"{'read':10s} {'bucket rule MB':>14s} {'per invocation MB':>17s} {'difference B':>13s}")
    for tag in READS:
        print(f"{tag:10s} {bucket_a[tag] / 1e6:14.1f} {inv_a[tag] / 1e6:17.1f} "
              f"{inv_a[tag] - bucket_a[tag]:13,.0f}")
    print("\ninvocations of other volumes whose bytes the bucket rule leaves out of a read (the invocation "
          "ended before the read started):")
    for tag in READS:
        i = by_tag[tag]
        m_ = minute(i["start_utc"])
        for j in members[tag]:
            counted = sum(x for t, x in labelled[j].items() if last_end[i["vol"]] <= t < m_)
            if counted != inv_bytes[j]:
                jj = by_tag[j]
                print(f"  {tag:9s} {j:11s} started {hmsf(ts(jj['start_utc']))}Z ended {hmsf(ts(jj['end_utc']))}Z "
                      f"read {inv_bytes[j]:,} B; CloudWatch labels "
                      f"{ {hm(t): int(x) for t, x in sorted(labelled[j].items())} }; "
                      f"m={hm(m_)}Z; counted by the bucket rule {counted:,.0f} B")
    print("\nclient start seconds of the invocations (hh:mm:ss.s) and the CloudWatch minute holding the "
          "first of their data-read bytes:")
    for j in invs:
        if j["tag"].startswith("x4-warm-"):
            continue
        first = min(labelled[j["tag"]]) if labelled[j["tag"]] else None
        print(f"  {j['tag']:10s} start {hmsf(ts(j['start_utc']))}Z end {hmsf(ts(j['end_utc']))}Z  first "
              f"labelled bucket {hm(first) + 'Z' if first else 'none'}  last "
              f"{hm(max(labelled[j['tag']])) + 'Z' if first else 'none'}")
    print("\n== 4. the file-system-level and the per-volume metric place some reads in different minutes")
    vol_sum: dict[datetime, float] = collections.defaultdict(float)
    for (n, m, t), x in cw.items():
        if m == "DataReadBytes":
            vol_sum[t] += x
    shifted = [(t, fs.get(t, 0), vol_sum.get(t, 0)) for t in sorted(set(fs) | set(vol_sum))
               if fs.get(t, 0) != vol_sum.get(t, 0)]
    for t, a, b in shifted:
        print(f"  {hm(t)}Z file-system-level {a:,.0f} B, sum of the four volumes {b:,.0f} B, "
              f"difference {a - b:+,.0f} B")
    print(f"  sum of the differences over the file: {sum(a - b for _, a, b in shifted):,.0f} B "
          f"(0 means the same bytes, placed in different minutes); 5 MiB = 5,242,880 B, so the "
          f"first difference is {abs(shifted[0][1] - shifted[0][2]) / 5_242_880:.0f} reads of chn")
    print("\nplan values: about 0.5, 200, and 800 MB for q, p1, p2. Per invocation (cohort 1): "
          + ", ".join(f"{t.split('-')[1]} {inv_a[t] / 1e6:.1f}" for t in READS[:3]) + " MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
