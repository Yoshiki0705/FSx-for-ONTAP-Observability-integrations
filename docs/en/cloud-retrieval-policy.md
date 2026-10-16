# Controlling SSD read-back with cloud-retrieval-policy

🌐 [日本語](../ja/cloud-retrieval-policy.md) | **English** (this page)

> **Status / audience / evidence tiers**
>
> Status is a reference. Every claim on this page rests on a cited public source (the AWS FSx for ONTAP documentation and docs.netapp.com). Audience — engineers using capacity-pool tiering (FabricPool) on Amazon FSx for NetApp ONTAP who need to control how data in the capacity pool is restored to SSD. Evidence tiers used here: `documented` (stated in a cited AWS or NetApp page), `hypothesis` (inferred, not checked), and `unverified` (no dated run exists on this branch). This vocabulary matches [monitoring-design.md](monitoring-design.md). Advice about read patterns (web server, CDN) is stated as an operational consideration, not asserted. Sources are placed inline at the point of claim.

## Executive summary

`cloud-retrieval-policy` decides whether cold data in the capacity pool is restored to SSD when it is read. The default on an Auto volume restores to SSD only on random reads and leaves sequential reads in the capacity pool. Because a full read for virus scanning or analytics does not then pull cold data back, this default is considered desirable for many use cases ([modify-cloud-retrieval-policy-task](https://docs.netapp.com/us-en/ontap/fabricpool/modify-cloud-retrieval-policy-task.html), `documented`). on-read restores regardless of read pattern and acts on the whole volume, so continuous use is not recommended. If response time is a concern, consider extending the cooling period (the Auto default is 31 days) before switching to on-read.

## Behavior of the four policies

### default (restores to SSD on random reads only)

default restores only the blocks touched by random reads to SSD and leaves blocks touched by sequential reads in the capacity pool. Because it avoids pulling cold data back to SSD during a full sequential scan of a volume (virus scanning, analytics), it is considered desirable for many use cases ([modify-cloud-retrieval-policy-task](https://docs.netapp.com/us-en/ontap/fabricpool/modify-cloud-retrieval-policy-task.html), `documented`).

### on-read (restores regardless of read pattern; whole-volume effect)

on-read restores read blocks to SSD regardless of the read pattern (random or sequential). Because it acts on the whole volume, continuous use is not recommended; the documented use is to switch to it only when you temporarily need to read the same data repeatedly ([oracle-tiering-retrieval-policies](https://docs.netapp.com/us-en/ontap-apps-dbs/oracle/oracle-tiering-retrieval-policies.html), `documented`).

### Behavior of never and promote

never does not restore capacity-pool blocks to SSD even when they are read. promote pulls capacity-pool data up to SSD according to the policy. The settings and behavior of each value are documented in the FSx for ONTAP documentation ([set-cloud-retrieval](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/set-cloud-retrieval.html), `documented`).

## How the read pattern is judged

### Why a web server's sequential reads may not be judged random

Because default restores only random reads to SSD, behavior depends on how data is read. A web server often reads files from the beginning in order, in which case the reads may be judged sequential and not restored to SSD. Note that "a web server's reads are always treated as random" does not hold (for the judging mechanism see [modify-cloud-retrieval-policy-task](https://docs.netapp.com/us-en/ontap/fabricpool/modify-cloud-retrieval-policy-task.html); operational consideration).

### Why CDN-fronted reads still reach FSx (cache miss, purge, non-cacheable, bypass)

Even with a CDN in front, reads to the origin do not disappear entirely. On cache expiry, a manual purge, non-cacheable content, or processing that does not go through the CDN, reads reach FSx for ONTAP. If the read is then judged sequential on a default volume, cold data is not restored to SSD and is read from the capacity pool (an operational consideration based on general CDN behavior, not a measurement of a specific configuration).

## Why to extend the cooling period before using on-read

If the response time of capacity-pool reads is a concern, switching to on-read triggers read-back across the whole volume and increases SSD usage. What you can consider first is extending the cooling period so frequently read data stays on SSD longer. The default cooling period for Auto is 31 days ([modify-cloud-retrieval-policy-task](https://docs.netapp.com/us-en/ontap/fabricpool/modify-cloud-retrieval-policy-task.html), `documented`). This is a documented default, not a measurement from this repository's verification environment (the first-generation, single-HA-pair file system described in [monitoring-design.md](monitoring-design.md)).

## Unconfirmed scope

- The actual behavior of `cloud-retrieval-policy` set to on-read on FSx for ONTAP. There is no record on this branch confirming on FSx for ONTAP the documented statement that it restores to SSD regardless of read pattern (`unverified`). To test, run a full sequential read under on-read and observe whether the Performance Tier footprint from `volume show-footprint` grows, with a default volume as the control.
- Whether reads through S3 Access Points are restored to SSD under the Auto default. There is no run record on this branch for whether an S3 GET is judged random or sequential (`unverified`). Plan to observe this with a NFS/SMB random read as the control.

## Related Documents

- [snapmirror-migration-tiering.md](snapmirror-migration-tiering.md): restoring cold data to SSD after migration (reading back after break).
- [capacity-automation.md](capacity-automation.md): the relationship between SSD capacity and autosize.
- [tiering-optimizer (S3AP repository)](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/operations/tiering-optimizer/README.en.md): a tool that recommends tiering policy and cooling period. This page covers the read-back policy.
- [FabricPool overview (NetApp)](https://docs.netapp.com/us-en/ontap/fabricpool/index.html): general documentation on capacity-pool tiering.
