# SnapMirror migration tiering and restoring blocks to SSD

🌐 [日本語](../ja/snapmirror-migration-tiering.md) | **English** (this page)

> **Status / audience / evidence tiers**
>
> Status is a migration runbook. Every claim on this page rests on a cited public source (the AWS FSx for ONTAP documentation, docs.netapp.com, and public KB articles). Audience — engineers migrating from an existing ONTAP system to Amazon FSx for NetApp ONTAP with SnapMirror who need to understand SSD-tier behavior during and after the migration. Evidence tiers used here: `documented` (stated in a cited AWS or NetApp page), `hypothesis` (inferred, not checked), and `unverified` (no dated run exists on this branch). This vocabulary matches [monitoring-design.md](monitoring-design.md). Sources are placed inline at the point of claim. Values are placeholders (`fs-0123456789abcdef0`, `svm-0123456789abcdef0`, `svm_name`, `vol_name`). This page covers a generalized migration scenario (for example, migrating from on-premises ONTAP with SnapMirror), not any specific customer environment.

## Executive summary

SnapMirror does not carry block temperature (access history) to the destination. If you run the initial transfer with the destination tiering policy left at Auto, the received baseline stays on SSD until it meets the cooling period again at the destination, which can fill the SSD tier and stop the transfer with "No space left on device". The AWS documentation describes using All for the destination during migration so received data moves to the capacity pool, and monitoring SSD utilization in CloudWatch so it stays below 80% during migration ([migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html), `documented`). At cutover, you make the destination RW with `snapmirror break` after a final Update. To bring the data you need back onto SSD before publishing, you have to actually read it, not `touch` it: `touch` rewrites metadata only and does not read the data, so it does not restore blocks to SSD. This page merges tiering during migration (block A) and cutover with break/resync (block B) into one runbook.

## Scope and prerequisites

- The scope is migrating from an existing ONTAP system (on-premises or in the cloud) to FSx for ONTAP over a data-protection (DP) relationship, making the destination RW at cutover.
- It assumes capacity-pool tiering (FabricPool) is enabled on the destination FSx for ONTAP.
- Administrative operations use the ONTAP CLI or the ONTAP REST API. The FSx console and the AWS CLI/API cannot reach some settings (parts of tiering-policy behavior, and autosize), so read the relevant sections of [cloud-retrieval-policy.md](cloud-retrieval-policy.md) and [capacity-automation.md](capacity-automation.md) alongside this page.
- For prior art on how tiering is handled when a SnapMirror destination is created, see [pitfalls-flexcache-snapmirror](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/docs/agent/pitfalls-flexcache-snapmirror.md) in the S3AP repository.

## Tiering policy during migration

### Why block temperature does not carry to the destination

SnapMirror does not transfer block temperature (the history of access frequency) to the destination. When the destination tiering policy is Auto, received data stays on SSD (the Performance Tier) until it meets the cooling period again at the destination ([volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html), `documented`). Blocks that were cold at the source are treated as freshly arrived blocks at the destination.

### Cooling period and 24-48h tiering when the destination is Auto

Under the Auto policy, a block is tiered only after it meets the cooling period. The AWS documentation states that tiering happens 24-48 hours after the cooling period elapses ([volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html), `documented`). So a block does not move to the capacity pool the instant the cooling period ends; there is an additional wait.

### Why 50% SSD utilization is only one of the tiering conditions

Tiering under Auto proceeds once SSD utilization exceeds 50%, but that is only one of the conditions. Even above 50% SSD utilization, data that has not met the cooling period is not tiered and stays on SSD ([volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html), `documented`). So "once SSD exceeds 50% it automatically drains to the capacity pool" does not hold.

### How an Auto initial transfer can fill the SSD tier

Taken together, if you run the initial transfer with the destination at Auto (with its long cooling period), the entire received baseline keeps accumulating on SSD because none of it has met the cooling period. When the baseline exceeds SSD capacity, the transfer can stop with "No space left on device" ([migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html), `documented`).

### Using All for the initial transfer

The AWS documentation describes setting the destination tiering policy to All during migration. Under All, received data moves to the capacity pool immediately, which avoids accumulating the whole baseline on SSD ([migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html), `documented`).

```text
::> volume modify -vserver svm_name -volume vol_name -tiering-policy all
::> volume show -vserver svm_name -volume vol_name -fields tiering-policy
```

### Metadata always on SSD and the 1:10 guideline

Even under All, metadata always stays on SSD. The AWS documentation gives a 1:10 guideline for the ratio of SSD to capacity pool ([migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html), `documented`). So even when data drains to the capacity pool, you still need SSD headroom for the metadata.

### Monitoring SSD utilization below 80% during migration

Monitoring SSD utilization in CloudWatch so it stays below 80% during migration is recommended ([migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html), `documented`). You can build this monitoring from the `StorageCapacityUtilization` (`StorageTier=SSD`) alarm design in `## Metric catalog` of this repository's [monitoring-design.md](monitoring-design.md), and from the capacity alarm in `terraform/fsxn-monitoring-dashboard/`.

## Cutover and restoring blocks to SSD

### break makes the destination RW; resync restores it

You make the destination writable (RW) with `snapmirror break`. You do not need to delete the relationship. To restore the relationship to its DP state, use `snapmirror resync` ([snapmirror-resync](https://docs.netapp.com/us-en/ontap-cli/snapmirror-resync.html), `documented`).

### After delete + create, use resync, not initialize

You can recreate the relationship with `create` after deleting it, but the command to run next is `resync`, not `initialize`. `resync` uses the common snapshot to transfer only the delta. If the common snapshot is gone, you start over from an initial transfer ([snapmirror-resync](https://docs.netapp.com/us-en/ontap-cli/snapmirror-resync.html), `documented`).

### resync rewinds to the common snapshot and discards RW changes

`resync` rewinds the destination to the point of the common snapshot. So any changes written to the destination while it was RW (after break) are discarded ([snapmirror-resync](https://docs.netapp.com/us-en/ontap-cli/snapmirror-resync.html), `documented`). Note that if you keep updating the destination after cutover and then resync, those updates are lost.

### break on an All destination converts the tiering policy to Auto

If you run `snapmirror break` while the destination tiering policy is All, the policy changes to Auto ([NetApp KB: SnapMirror break converts the tiering policy of volume from all to auto](https://kb.netapp.com/on-prem/ontap/DP/SnapMirror/SnapMirror-KBs/SnapMirror_break_converts_the_tiering_policy_of_volume_from_all_to_auto), `documented`). Data that was moved to the capacity pool under All during migration stays in the capacity pool after break even though the policy becomes Auto; changing to Auto by itself does not bring it back to SSD.

### touch rewrites metadata only and does not restore to SSD

`touch` only rewrites a file's metadata (timestamps); it does not read the data blocks. So cold data in the capacity pool is not restored to SSD by `touch`. To bring it back to SSD, you have to actually read it (for how a read is judged for restore, see [cloud-retrieval-policy.md](cloud-retrieval-policy.md); for the cooling and tiering mechanism, see [volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html), `documented`).

### Recommended sequence (final Update -> quiesce -> break -> read back to SSD -> publish)

The sequence below avoids an extra break/resync just to read data back at cutover.

1. Run a final Update to bring the destination current.
2. Stop transfers with `snapmirror quiesce`.
3. Make the destination RW with `snapmirror break`. For an All destination the policy changes to Auto at this point (KB above).
4. Before publishing, actually read the data you want on SSD so it is restored to SSD (a read, not `touch`).
5. Publish the destination.

```text
::> snapmirror update -destination-path svm_name:vol_name
::> snapmirror quiesce -destination-path svm_name:vol_name
::> snapmirror break -destination-path svm_name:vol_name
::> snapmirror show -destination-path svm_name:vol_name
```

> **Cutover timing note**
>
> If you change the destination to Auto some time before cutover and keep updating, the updates just before cutover stay on SSD as Auto. However, data that moved to the capacity pool while the policy was All stays in the capacity pool after you switch to Auto and does not return to SSD automatically ([migrating-fsx-ontap-snapmirror](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/migrating-fsx-ontap-snapmirror.html), [volume-storage-capacity](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-storage-capacity.html), `documented`).

## Unconfirmed scope

- Whether data read back to SSD before a resync stays on SSD afterwards. Because resync rewinds the destination to the common snapshot, whether the SSD footprint restored before resync is retained afterwards has no run record on this branch (`unverified`). To test, plan to compare the Performance Tier footprint from `volume show-footprint` before and after resync.
- The default autosize value for a volume broken off from a SnapMirror destination. After break, autosize follows each volume's own setting, but whether autosize is enabled on a broken-off volume and what its default maximum is are separate questions and have not been confirmed (`unverified`). The detail and verification plan are in [capacity-automation.md](capacity-automation.md#autosize-inheritance-and-volume-size).
- The behavior of the T2 (`terraform/fsxn-ontap-custom-metrics/`) SnapMirror metrics for an on-premises ONTAP -> FSx for ONTAP relationship. What is verified in this repository is a relationship inside FSx in the same region; reachability, lag and intercluster-LIF behavior for an on-premises source are not confirmed (`unverified`).

## Related Documents

- [cloud-retrieval-policy.md](cloud-retrieval-policy.md): `cloud-retrieval-policy`, which controls how reads restore to SSD after migration.
- [capacity-automation.md](capacity-automation.md): autosize after break, volume size, and SSD capacity actions.
- [monitoring-design.md](monitoring-design.md): the alarm design for SSD-utilization monitoring during migration (`StorageCapacityUtilization`, `StorageTier=SSD`).
- `terraform/fsxn-monitoring-dashboard/`: the Terraform module for the capacity alarm.
- [cross-region-replication.md](cross-region-replication.md): DR patterns using SnapMirror.
- [pitfalls-flexcache-snapmirror](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/docs/agent/pitfalls-flexcache-snapmirror.md): tiering defaults when creating a SnapMirror destination (S3AP repository).
