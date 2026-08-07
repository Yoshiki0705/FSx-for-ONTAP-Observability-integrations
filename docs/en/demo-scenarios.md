# Demo Scenarios

🌐 [日本語](../ja/demo-scenarios.md) | **English** (this page)

## Overview

Demo procedures and scenarios for each vendor integration. Use these during actual demo sessions.

---

## Scenario 1: Unauthorized Access Detection (Datadog)

### Story
Detect unauthorized access attempts to confidential files and trigger real-time alerts in Datadog.

### Steps

1. **Setup**: Datadog integration deployed
2. **Action**: Access a restricted file on the FSx for ONTAP mount point without permission
   ```bash
   # Attempt to access a confidential file as an unauthorized user
   sudo -u testuser cat /mnt/fsxn/confidential/secret-report.pdf
   # → Permission denied (recorded as Failure in audit log)
   ```
3. **Verify**: Check in Datadog Logs
   - Search: `source:fsxn @attributes.result:Failure`
   - Confirm spike in failed access on the dashboard
4. **Alert**: Datadog Monitor triggers Slack/PagerDuty notification on threshold breach

### Expected Result
- Event arrives in Datadog within 30 seconds
- Traceable by `@attributes.user`, `@attributes.path`, `@attributes.client_ip`

---

## Scenario 2: Ransomware Detection (Splunk + EMS)

### Story
ARP/AI detects ransomware-like activity and sends alert to Splunk via EMS event.

### Steps

1. **Setup**: Splunk integration + EMS webhook configured
2. **Simulate**: Execute mass file rename operations
   ```bash
   # Simulate ransomware-like mass rename (test environment only)
   for f in /mnt/fsxn/test-data/*.txt; do
     mv "$f" "${f}.encrypted"
   done
   ```
3. **ARP Detection**: ONTAP ARP/AI detects the anomaly
   - EMS event `arw.volume.state` is emitted
   - Automatic snapshot `Anti_ransomware_backup_*` is created
4. **Splunk Verification**:
   - Search: `index=fsxn_audit sourcetype=fsxn:ontap:audit event_type=arw*`
   - Alert: Confirm on "ARP Detection" dashboard

### Expected Result
- Alert arrives in Splunk within 1 minute of ARP detection
- Attack timeline visualized in Splunk dashboard

---

## Scenario 3: Quota Threshold Alert (New Relic + EMS)

### Story
User storage exceeds soft quota, admin notified via New Relic.

### Steps

1. **Setup**: New Relic integration + EMS CloudWatch integration configured
2. **Action**: Write a large file to exceed quota
   ```bash
   # Create a large test file
   dd if=/dev/zero of=/mnt/fsxn/user-data/large-file.bin bs=1M count=500
   ```
3. **EMS Emission**: `wafl.quota.softlimit.exceeded` event fires
4. **New Relic Verification**:
   - Filter in Logs UI: `source:fsxn-ontap`
   - NRQL: `SELECT count(*) FROM Log WHERE source='fsxn-ontap' AND event_type LIKE 'wafl.quota%' SINCE 1 hour ago`

### Expected Result
- Log arrives in New Relic within 2 minutes of quota breach
- Automatic notification via New Relic Alert Condition

---

## Scenario 4: Multi-Vendor Fan-Out (OTel Collector)

### Story
Ship same audit logs to Grafana Cloud and Honeycomb simultaneously via OTel Collector.

### Steps

1. **Setup**: OTel Collector integration deployed + Collector configured
   ```yaml
   # otel-collector-config.yaml (verified working)
   exporters:
     otlp_http/grafana:
       endpoint: https://otlp-gateway-prod-ap-southeast-0.grafana.net/otlp
       headers:
         Authorization: Basic ${GRAFANA_BASIC_AUTH}
     otlp_http/honeycomb:
       endpoint: https://api.honeycomb.io
       headers:
         x-honeycomb-team: ${HONEYCOMB_KEY}
         x-honeycomb-dataset: fsxn-audit
   service:
     pipelines:
       logs:
         exporters: [otlp_http/grafana, otlp_http/honeycomb]
   ```
2. **Action**: Perform file operations on FSx for ONTAP
3. **Verify**:
   - Grafana: Explore → Loki → `{job="fsxn-audit"}`
   - Honeycomb: Dataset `fsxn-audit` → Query

### Expected Result
- Identical events arrive at both vendors
- No code changes required when switching vendors

---

## Scenario 5: Compliance Audit Report (Elastic)

### Story
Generate quarterly compliance report from Elasticsearch audit log indices.

### Steps

1. **Setup**: Elastic integration deployed
2. **Data Accumulation**: Audit logs accumulate in daily indices during normal operation
3. **Report Generation**: Visualize in Kibana
   ```
   GET fsxn-audit-2026.01.*/_search
   {
     "query": {"bool": {"must": [
       {"term": {"fsxn.svm": "svm-prod-01"}},
       {"range": {"@timestamp": {"gte": "2026-01-01", "lte": "2026-03-31"}}}
     ]}},
     "aggs": {
       "by_user": {"terms": {"field": "user.name.keyword"}},
       "by_operation": {"terms": {"field": "fsxn.operation.keyword"}}
     }
   }
   ```
4. **Dashboard**: Visualize access patterns in Kibana Dashboard

---

## Scenario 6: FPolicy Real-Time File Monitoring (Dynatrace)

### Story
Alert immediately when files with specific extensions (.exe, .bat, .ps1) are created.

### Steps

1. **Setup**: FPolicy + Dynatrace integration configured
2. **FPolicy Configuration**:
   ```bash
   fpolicy policy event create -vserver svm-prod-01 \
     -event-name suspicious-files \
     -protocol cifs \
     -file-operations create \
     -filters-on-extension exe,bat,ps1,vbs
   ```
3. **Action**: Create a suspicious file
   ```bash
   echo "test" > /mnt/fsxn/shared/malware.exe
   ```
4. **Dynatrace Verification**: Problems → Custom events

---

## Scenario 7: Native Detection with CloudWatch Log Alarm (AWS-native)

### Story
Detection that stays entirely within AWS, no vendor product required. Alert the moment a single access to a sensitive path is detected (`sensitive-file-access`), and flag a ransomware indicator when deletions exceed 50 within 5 minutes (`bulk-delete-operations`). Assumes admin audit logs already reach CloudWatch Logs via the Syslog VPC endpoint built earlier.

### Steps

1. **Setup**: Admin audit logs delivered to CloudWatch Logs (`/syslog/fsxn-admin-audit`)
2. **Deploy (sensitive file access detection)**:
   ```bash
   DETECTION_TYPE=sensitive-file-access \
   TARGET_PATTERN="/vol/data/confidential" \
   CREATE_SNS_TOPIC=true \
   SNS_TOPIC_NAME=fsxn-security-alerts \
     bash shared/scripts/deploy-log-alarm.sh
   ```
3. **Deploy (bulk delete detection)**:
   ```bash
   DETECTION_TYPE=bulk-delete-operations \
   ALARM_THRESHOLD=50 \
   QUERY_RESULTS_TO_ALARM=2 \
   SNS_TOPIC_ARN=<YOUR_SNS_ARN> \
     bash shared/scripts/deploy-log-alarm.sh
   ```
4. **Verify (console)**: CloudWatch → Alarms shows the alarm as a "Log alarm" type

   ![CloudWatch Alarms list — Log alarm type](../screenshots/01-cloudwatch-alarms-list.png)

5. **Verify (real data)**: Run the query in Logs Insights and confirm audit logs match (below: `/volume/` filter, 12 matches, 3,482 records scanned)

   ![Logs Insights — audit log query result (/volume/ filter, 12 matches)](../screenshots/03-logs-insights-query-result.png)

### Expected Result
- While there is no matching access, the alarm stays **OK** (INSUFFICIENT_DATA → OK transition confirmed)
- When sensitive-path access or a threshold-exceeding delete occurs, it transitions to ALARM and notifies via SNS (with `ActionLogLineCount` set, the matched log lines are included in the notification)

   ![Log alarm — state OK (INSUFFICIENT_DATA → OK transition confirmed)](../screenshots/04-log-alarm-state-ok.png)

> For full steps, the five detection presets, and IAM requirements, see the [CloudWatch Log Alarm guide](cloudwatch-log-alarm.md).

---

## Demo Environment Checklist

Work through this before the demo — a stack that deployed successfully is not the
same as a working pipeline.

- [ ] FSx for ONTAP file system running
- [ ] Audit logging enabled, and you know which volume is the `-destination`
- [ ] FSx for ONTAP S3 Access Point attached to that volume
- [ ] Access point `NetworkOrigin` matches the Lambda placement
      (`Internet` ⇒ Lambda outside VPC; `VPC` ⇒ `VpcEnabled=true` + internet egress)
- [ ] Target vendor integration stack deployed
- [ ] **Real Lambda code uploaded** — the template ships a placeholder that raises
      `NotImplementedError`. `scripts/deploy.sh` does this; `scripts/verify.sh`
      check 2 confirms it
- [ ] `bash integrations/<vendor>/scripts/verify.sh` passes all checks
- [ ] One real audit event has already reached the vendor
      (ONTAP must rotate the audit log first — do not discover this mid-demo)
- [ ] Test files/directories prepared
- [ ] Vendor-side dashboard/alerts configured
- [ ] Screenshot capture tools ready

> **Timing note for live demos**: an audit event is not visible to the shipper
> until ONTAP rotates the staging file, then the 5-minute schedule has to run.
> Budget **rotation interval + schedule interval**. To force a rotation:
> `vserver audit rotate -vserver <svm-name>`. To rehearse the pipeline without
> waiting at all, see the Datadog Setup Guide's
> [validation without rotation](../../integrations/datadog/docs/en/setup-guide.md#53-optional-validate-without-waiting-for-ontap-rotation).

## Screenshot Capture Points

Capture the following screenshots for each demo:

1. **Lambda CloudWatch Logs**: Successful processing logs
2. **Vendor UI**: Log arrival confirmation screen
3. **Dashboard**: Visualized data
4. **Alert**: Notification fired screen
5. **Architecture Diagram**: Actual resource layout

---

## Scenario 8: Automated Incident Response — SMB User Block (AWS-native)

### Story
A compromised user is detected via CloudWatch Log Alarm or SIEM monitor. The automated response pipeline blocks the user on FSx for ONTAP within seconds, creates a protective snapshot, and disconnects their active sessions — all without human intervention.

### Steps

1. **Setup**: Automated response stack deployed (`shared/templates/automated-response.yaml`)
2. **Trigger**:
   ```bash
   ./shared/scripts/automated-response-cli.sh contain-smb \
     --domain CORP --user jdoe --volume vol_data \
     --reason "Simulated insider threat"
   ```
3. **Verify (Lambda)**:
   ```bash
   aws logs filter-log-events \
     --log-group-name /aws/lambda/fsxn-automated-response-handler \
     --filter-pattern "contain_smb_threat" \
     --query 'events[-1].message' --output text
   ```
4. **Verify (ONTAP)**:
   ```bash
   ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
   ```
5. **Verify (Access Denied)**: Attempt file access as blocked user → denied
6. **Unblock**:
   ```bash
   ./shared/scripts/automated-response-cli.sh unblock-smb \
     --domain CORP --user jdoe
   ```

### Expected Result
- Lambda executes 3 containment steps (snapshot + block + disconnect) within ~5 seconds
- User cannot access any share on the SVM
- After unblock, access is restored
- Email notification received with containment details

> Full demo procedure: [Automated Response Demo Runbook](demo-automated-response.md)

---

## Scenario 9: Time-Limited Blocks with Auto-Unblock (TTL)

### Story
Blocks should not persist indefinitely. The TTL stack automatically removes expired blocks after a configurable period, preventing accidental lockouts.

### Steps

1. **Setup**: TTL stack deployed (`shared/templates/automated-response-ttl.yaml`, TTL=5min)
2. **Block**:
   ```bash
   ./shared/scripts/automated-response-cli.sh block-smb \
     --domain CORP --user jdoe \
     --reason "TTL demo - auto-expires in 5 minutes"
   ```
3. **Wait**: Observe TTL cleanup Lambda logs for ~5 minutes
   ```bash
   aws logs tail /aws/lambda/fsxn-automated-response-ttl-cleanup --follow
   ```
4. **Verify**: Block auto-removed after TTL expiry
   ```bash
   ssh fsxadmin@<management-ip> "vserver name-mapping show -direction win-unix -replacement \" \""
   # → Empty (block removed)
   ```

### Expected Result
- Block is created and active for the configured TTL period
- After TTL expiry, EventBridge Scheduler invokes cleanup Lambda
- Block is automatically removed without human intervention
- Notification sent confirming auto-removal

---

## Scenario 10: ARP Detection → End-to-End Auto-Block

### Story
The complete chain: ONTAP ARP detects ransomware-like behavior → EMS Webhook → Observability platform → SIEM monitor fires → SNS → Response Lambda → User blocked + Snapshot + Sessions disconnected. Total time: ~65 seconds from detection to storage-layer block. This automates the containment phase only — eradication (e.g., removing the attacker's foothold) and recovery are not covered and still require human follow-up.

### Steps

1. **Setup**: Full pipeline deployed (EMS Webhook + SIEM integration + automated response)
2. **Simulate ARP**:
   ```bash
   ssh fsxadmin@<management-ip> \
     "security anti-ransomware volume attack simulate -vserver <svm> -volume <vol>"
   ```
3. **Observe** (within 60 seconds):
   - EMS event arrives at Observability platform (~30s)
   - SIEM monitor fires and publishes to SNS
   - Response Lambda executes containment (~5s)
4. **Verify**:
   - ONTAP snapshot created (`incident_response_*`)
   - User blocked (name-mapping entry)
   - Sessions disconnected
   - Email notification received

### Expected Result
- Storage-layer block and evidence preservation complete in ~65 seconds
- Zero manual intervention required for the blocking step
- Evidence preserved (snapshot + audit logs)

> Full procedure: [Automated Response Demo Runbook](demo-automated-response.md) Phase 4
