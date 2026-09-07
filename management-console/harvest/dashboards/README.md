# Harvest Grafana Dashboards for FSx for ONTAP

This directory contains Grafana dashboard JSON files from the [NetApp Harvest](https://github.com/NetApp/harvest) project, customized for Amazon FSx for NetApp ONTAP monitoring via Amazon Managed Grafana (AMG).

## Supported Harvest Dashboards for FSx for ONTAP

NetApp Harvest ships 60+ Grafana dashboards for ONTAP. FSx for ONTAP **exposes a different
metric set than on-premises ONTAP**, so only a subset works, and the split is not a matter
of dashboard format — it is which metrics exist to query.

AWS publishes the classification. The three lists below are that classification, not an
independent judgement: **19 supported (tagged `fsx`), 8 supported but not enabled by default
in Harvest, 10 unsupported.** Source:
[AWS — Monitoring FSx for ONTAP file systems using Harvest and Grafana](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-harvest-grafana.html),
retrieved **2026-09-07**. Confirm against that page for your own Harvest version before
planning around it.

> **Correction note (2026-09-07)**: this file previously carried a hand-written list that
> was never sourced, and it disagreed with AWS on `ONTAP: Disk` — four Disk dashboards
> (health, utilization, errors, spare count) were listed as "supported and recommended"
> while AWS lists `ONTAP: Disk` as unsupported. Anyone who followed the old list downloaded
> a dashboard that cannot populate. The old list also named 21 JSON files, none of which
> were ever committed here; they were download instructions, not shipped artifacts. See
> [Downloading](#how-to-download-dashboard-json-from-harvest-github) for what this
> directory actually contains.

### Supported (tagged `fsx`) — 19

```text
Harvest: Metadata
ONTAP: Aggregate
ONTAP: cDOT
ONTAP: Cluster
ONTAP: Compliance
ONTAP: Datacenter
ONTAP: Data Protection
ONTAP: LUN
ONTAP: Network
ONTAP: Node
ONTAP: Qtree
ONTAP: Security
ONTAP: SnapMirror
ONTAP: SnapMirror Destinations
ONTAP: SnapMirror Sources
ONTAP: SVM
ONTAP: Volume
ONTAP: Volume by SVM
ONTAP: Volume Deep Dive
```

**Supported is not the same as fully populated.** AWS states that some panels in these
dashboards may be missing information that is not supported. `ONTAP: Node` is the clearest
case: the dashboard is supported, and its node CPU and memory panels have nothing behind
them, because FSx manages the nodes. That panel-level gap is what
[Step 1](#step-1-remove-unsupported-panels) strips, and it is only findable by opening the
dashboard — the classification does not describe it.

### Supported but disabled by default in Harvest — 8

```text
ONTAP: FlexCache
ONTAP: FlexGroup
ONTAP: NFS Clients
ONTAP: NFSv4 Storepool Monitors
ONTAP: NFS Troubleshooting
ONTAP: NVMe Namespaces
ONTAP: SMB
ONTAP: Workload
```

These are available; they are not switched on. SMB and NFS troubleshooting, FlexCache and
per-workload views all sit in this list, so a migration plan that assumes them needs to
budget the enabling step rather than treat them as present.

### Unsupported — 10

```text
ONTAP: Disk
ONTAP: External Service Operation
ONTAP: File Systems Analytics (FSA)
ONTAP: Headroom
ONTAP: Health
ONTAP: MAV Request
ONTAP: MetroCluster
ONTAP: Power
ONTAP: Shelf
ONTAP: S3 Object Stores
```

**These 10 do not have equal operational weight.** Disk, Shelf, Power and MetroCluster are
absent because AWS owns the physical layer and MetroCluster is not an FSx deployment type —
nothing to replace. Two of the ten change how you operate:

| Dashboard | What its absence costs |
|-----------|------------------------|
| `ONTAP: Health` | No single-screen entry point for "is the system healthy". Health becomes a set of CloudWatch alarms over individual metrics plus what FSx for ONTAP itself reports |
| `ONTAP: Headroom` | No indicator of remaining performance margin. "How much more load can this take" has to be rebuilt from throughput capacity and credit balance |

> **Naming note**: `ONTAP: S3 Object Stores` covers ONTAP's own S3 object store feature. It
> is not a view of FSx for ONTAP S3 Access Points, which this repository monitors through
> the audit-log pipeline instead.

### Scope: "does not transfer" is about the exposed set, not about what is achievable

The classification above is bounded to **the metric set the managed service publishes**. It
is not a statement that a missing value can never be seen. The ONTAP REST API is reachable,
and a value absent from the published set can be read from it and republished — this
repository does exactly that in
[`shared/templates/qtree-quota-monitor.yaml`](../../../shared/templates/qtree-quota-monitor.yaml),
which polls `/storage/quota/reports` and writes `FSxONTAP/Qtree` custom metrics to
CloudWatch.

Two things that keeps straight:

- **A dashboard in the unsupported ten is not a dead end.** It means the shipped dashboard
  has nothing behind it, not that the underlying number is unreachable. Whether rebuilding
  it is worth the code is a separate judgement, and for `Health` and `Headroom` it is a
  substantial one.
- **Quota is not an example of that.** AWS lists `ONTAP: Qtree` among the supported 19, so
  the Harvest route already covers it. The gap `qtree-quota-monitor.yaml` fills is in the
  **CloudWatch** metric set — the route that uses no collection stack at all. Reading it as
  a Harvest-route gap gets the reason for the template wrong.

## How to Download Dashboard JSON from Harvest GitHub

### Option 1: Download Individual Dashboards (Recommended)

```bash
# Base URL for Harvest Grafana dashboards
HARVEST_REPO="https://raw.githubusercontent.com/NetApp/harvest/main/grafana/dashboards/cmode"

# Download volume performance dashboards
curl -sL "${HARVEST_REPO}/volume.json" -o volume_performance.json
curl -sL "${HARVEST_REPO}/volume_top.json" -o volume_top_n.json

# Download aggregate dashboards
curl -sL "${HARVEST_REPO}/aggregate.json" -o aggregate_capacity.json

# Download SVM dashboards
curl -sL "${HARVEST_REPO}/svm.json" -o svm_overview.json
curl -sL "${HARVEST_REPO}/nfs.json" -o svm_nfs_operations.json
curl -sL "${HARVEST_REPO}/cifs.json" -o svm_cifs_operations.json
curl -sL "${HARVEST_REPO}/iscsi.json" -o svm_iscsi_operations.json

# Download network dashboards
curl -sL "${HARVEST_REPO}/lif.json" -o network_lif_throughput.json
curl -sL "${HARVEST_REPO}/network.json" -o network_port_status.json
```

> **No `disk.json`.** It was in this list until 2026-09-07. `ONTAP: Disk` is in the AWS
> unsupported list above, so that download produced a dashboard with no data behind it.

> **The filename-to-dashboard mapping above is not verified here**, and Harvest renames
> files between versions. The authoritative selection is the **`fsx` tag in Grafana** and
> the AWS list above — match on the dashboard title after import, not on the filename. A
> file that downloads successfully is not evidence that the dashboard it contains is one of
> the supported 19.

**This directory ships one dashboard**, `arp-status.json` (ARP state distribution and alert
timeline, built here rather than taken from Harvest). Everything else is fetched by the
commands above; nothing else is committed.

### Option 2: Clone Entire Harvest Repository

```bash
# Clone Harvest repo (sparse checkout for dashboards only)
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/NetApp/harvest.git /tmp/harvest

cd /tmp/harvest
git sparse-checkout set grafana/dashboards/cmode

# Copy relevant dashboards
cp grafana/dashboards/cmode/*.json \
  /path/to/management-console/harvest/dashboards/

# Clean up
rm -rf /tmp/harvest
```

### Option 3: Use Harvest CLI Export

If you have Harvest installed locally:

```bash
# Export dashboards from a running Harvest instance
harvest grafana export --directory ./dashboards/
```

## Customizing Dashboards for FSx for ONTAP

After downloading, dashboards need customization to work with FSx for ONTAP and AMP:

### Step 1: Remove Unsupported Panels

Some panels inside otherwise-supported dashboards reference metrics FSx for ONTAP does not
expose. This is the panel-level gap AWS warns about, and it is a **metric** problem, not a
dashboard-format one — which is why the filter below matches on the PromQL expression and
not on anything about the dashboard itself.

```bash
# Use jq to remove panels referencing unsupported metrics
jq '
  .panels |= map(
    select(
      (.targets // [] | map(.expr // "") | join("")) |
      test("node_cpu|node_memory|shelf_|metrocluster_|fabricpool_|cluster_peer_|autosupport_") | not
    )
  )
' input_dashboard.json > output_dashboard.json
```

**Metrics to remove** (not available on FSx for ONTAP). The list and the `test()` pattern
above must stay in step; `cluster_peer_` and `autosupport_` were listed here for a while
without being in the pattern, so the command stripped five of the seven:
- `node_cpu_*` — Node CPU metrics (managed by AWS)
- `node_memory_*` — Node memory metrics (managed by AWS)
- `shelf_*` — Physical shelf metrics
- `metrocluster_*` — MetroCluster metrics
- `fabricpool_*` — FabricPool tiering metrics
- `cluster_peer_*` — Cluster peering metrics
- `autosupport_*` — AutoSupport metrics

### Step 2: Update Data Source References

The import script (`import-dashboards.sh`) handles this automatically, but for manual customization:

```bash
# Replace all datasource references with AMP
jq '
  walk(
    if type == "object" and has("datasource") then
      if .datasource | type == "string" then
        .datasource = "Amazon Managed Prometheus"
      elif .datasource | type == "object" then
        .datasource = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
      else .
      end
    else .
    end
  )
' dashboard.json > dashboard_patched.json
```

### Step 3: Adjust Variable Templates

Harvest dashboards use template variables that may need adjustment for FSx for ONTAP:

```bash
# Update cluster variable to use FSx file system identifier
jq '
  .templating.list |= map(
    if .name == "Cluster" then
      .query = "label_values(volume_read_ops, cluster)"
    elif .name == "Datacenter" then
      .query = "label_values(volume_read_ops, datacenter)"
    else .
    end
  )
' dashboard.json > dashboard_updated.json
```

### Step 4: Validate Customized Dashboard

```bash
# Validate JSON syntax
jq empty dashboard.json

# Check for remaining unsupported metric references
jq -r '
  [.panels[].targets[]?.expr // empty] |
  map(select(test("node_cpu|shelf_|metrocluster_"))) |
  if length > 0 then
    "WARNING: Found unsupported metrics:\n" + join("\n")
  else
    "OK: No unsupported metrics found"
  end
' dashboard.json
```

## Panel Embed URL Format for ToolJet Integration

### URL Format

AMG supports embedding individual panels via the solo panel URL:

```
https://<amg-workspace-url>/d-solo/<dashboard-uid>?orgId=1&panelId=<panel-id>&from=<start>&to=<end>&refresh=<interval>
```

### Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `<amg-workspace-url>` | AMG workspace base URL | `g-abc123.grafana-workspace.ap-northeast-1.amazonaws.com` |
| `<dashboard-uid>` | Dashboard unique identifier (assigned on import) | `fsxn-vol-perf` |
| `<panel-id>` | Panel ID within the dashboard | `2` |
| `from` | Start time (relative or absolute) | `now-1h`, `now-7d` |
| `to` | End time | `now` |
| `refresh` | Auto-refresh interval | `1m`, `5m`, `30s` |
| `var-Volume` | Template variable filter | `vol_data_01` |
| `var-SVM` | SVM filter variable | `svm-prod-01` |

### ToolJet iframe Component Configuration

In ToolJet, use an **iframe** component with the embed URL:

```html
<iframe
  src="https://<amg-workspace-url>/d-solo/<dashboard-uid>?orgId=1&panelId=<panel-id>&from=now-1h&to=now&refresh=1m&var-Volume={{selectedVolume}}"
  width="100%"
  height="300"
  frameborder="0"
></iframe>
```

### Example Embed URLs by Use Case

#### Volume Detail Page — IOPS Panel

```
https://<amg-url>/d-solo/vol-iops?orgId=1&panelId=2&from=now-1h&to=now&refresh=1m&var-Volume={{volume_name}}&var-SVM={{svm_name}}
```

#### Volume Detail Page — Throughput Panel

```
https://<amg-url>/d-solo/vol-throughput?orgId=1&panelId=3&from=now-1h&to=now&refresh=1m&var-Volume={{volume_name}}&var-SVM={{svm_name}}
```

#### Volume Detail Page — Latency Panel

```
https://<amg-url>/d-solo/vol-latency?orgId=1&panelId=4&from=now-1h&to=now&refresh=1m&var-Volume={{volume_name}}&var-SVM={{svm_name}}
```

#### SVM Overview — Aggregated Metrics

```
https://<amg-url>/d-solo/svm-overview?orgId=1&panelId=1&from=now-1h&to=now&refresh=1m&var-SVM={{svm_name}}
```

#### Dashboard Overview — Top Volumes

```
https://<amg-url>/d-solo/vol-top-n?orgId=1&panelId=1&from=now-1h&to=now&refresh=5m
```

### Authentication for Embedded Panels

Embedded panels share the same Cognito session as the ToolJet application because both are served through the same ALB domain:

1. User authenticates via Cognito (ALB authenticate action)
2. Session cookie is set for the ALB domain
3. ToolJet loads at `/app/*` with the session cookie
4. Embedded Grafana panels at `/grafana/*` share the same domain cookie
5. AMG validates the session and renders the panel

**No additional authentication configuration is needed** for embedded panels when using the ALB + Cognito pattern described in the architecture.

### Panel Embed URL Output File

After running `import-dashboards.sh`, the script generates `panel-embed-urls.json` containing all panel URLs:

```json
{
  "workspace_url": "https://<amg-workspace-url>",
  "generated_at": "2026-01-15T10:30:00Z",
  "embed_url_format": "<workspace_url>/d-solo/<dashboard_uid>?orgId=1&panelId=<panel_id>&from=now-1h&to=now&refresh=1m",
  "tooljet_iframe_template": "<iframe src=\"{embed_url}\" width=\"100%\" height=\"300\" frameborder=\"0\"></iframe>",
  "panels": [
    {
      "dashboard_title": "Volume Performance",
      "dashboard_uid": "fsxn-vol-perf",
      "panel_id": 2,
      "panel_title": "Volume IOPS",
      "embed_url": "https://<amg-workspace-url>/d-solo/fsxn-vol-perf?orgId=1&panelId=2&from=now-1h&to=now&refresh=1m",
      "iframe_html": "<iframe src=\"...\" width=\"100%\" height=\"300\" frameborder=\"0\"></iframe>"
    }
  ]
}
```

## Troubleshooting

### Dashboard shows "No data"

1. Verify Harvest is collecting metrics: check ECS task logs
2. Verify AMP data source is configured correctly in AMG
3. Check that the Prometheus query uses correct metric names
4. Verify template variables match your FSx for ONTAP cluster/SVM names

### Panel embed returns 403

1. Verify the Cognito session cookie is valid
2. Check that the ALB listener rule for `/grafana/*` has Cognito auth action
3. Verify AMG workspace allows embedding (check workspace settings)

### Import script fails with "data source not found"

1. Ensure AMP workspace is deployed (`fsxn-mgmt-observability` stack)
2. Verify `--amp-workspace-id` parameter or stack output is correct
3. Check AMG workspace has permissions to query AMP (SigV4 auth)

## References

- [NetApp Harvest GitHub — Grafana Dashboards](https://github.com/NetApp/harvest/tree/main/grafana/dashboards)
- [AWS Docs — Amazon Managed Grafana](https://docs.aws.amazon.com/grafana/latest/userguide/what-is-Amazon-Managed-Service-Grafana.html)
- [AWS Docs — FSx for ONTAP Monitoring](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-overview.html)
- [AWS Docs — Monitoring FSx for ONTAP using Harvest and Grafana](https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/monitoring-harvest-grafana.html) — the authoritative 19 / 8 / 10 dashboard classification used above
- [Grafana HTTP API — Dashboard](https://grafana.com/docs/grafana/latest/developers/http_api/dashboard/)
- [Grafana Embedding — Solo Panel](https://grafana.com/docs/grafana/latest/dashboards/share-dashboards-panels/#embed-a-panel)
