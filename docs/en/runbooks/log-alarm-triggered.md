# Runbook: CloudWatch Log Alarm Triggered

🌐 [日本語](../../ja/runbooks/log-alarm-triggered.md) | **English** (this page)

> **Applies to**: `fsxn-sensitive-file-access-*`, `fsxn-failed-access-*`, `fsxn-bulk-delete-*`, `fsxn-user-activity-*` alarms
> **First responder**: Security operations / SRE / Storage administrators
> **Target**: MTTA < 5 min, root cause identification < 30 min

---

## 1. Initial Assessment (MTTA < 5 min)

### 1.1 Review Alarm Notification

Check the SNS email or Slack notification for:

- **Alarm name**: Which detection pattern fired (sensitive-file-access / failed-access / bulk-delete / user-activity)
- **Log lines**: Included log lines (up to 50) for immediate context
- **Timestamp**: When the event occurred

### 1.2 CloudWatch Console Verification

```
CloudWatch Console → Alarms → Target alarm → History tab
```

- State transition timestamp
- Query result value (count)
- Number of matched log entries

> **Note**: Sparse-pattern alarms (`count(*) > 0`) may enter ALARM only during the window
> that contains a match and return to OK right after (flapping). Even if it shows OK now,
> the SNS notification already fired on the OK→ALARM transition. Check the History tab for
> past transitions.

### 1.3 Logs Insights Deep Dive

```
CloudWatch Console → Logs → Log Insights → /syslog/fsxn-admin-audit
```

Query to retrieve recent matches:

```
fields @timestamp, @message
| filter @message like /<alarm pattern>/
| sort @timestamp desc
| limit 50
```

---

## 2. Response by Detection Pattern

### 2.1 sensitive-file-access

**Verify**:
- Who accessed (username / client IP)
- Which file was accessed (path)
- Access type (read / write / delete)
- Whether the user has legitimate access rights

**Decision flow**:

```
Legitimate access → Consider threshold adjustment / whitelist
Suspected unauthorized → Escalate (Step 3)
Unable to determine → Contact user's department
```

### 2.2 failed-access-attempts

The query counts requests that ONTAP rejected with `Error: not authorized` (the user lacks the role for the command). It does not count failed logins: a wrong-password REST request (HTTP 401) wrote no audit line in the 2026-10-09 run ([record](../verification-results-cloudwatch-monitoring.md#terraform-log-alarm-module-run-on-2026-10-09)).

**Verify**:
- Rejected username(s) and their role (the `<user>:<role>` token of the line)
- Client IP pattern (single concentrated IP vs distributed)
- Rejected commands (one task the role does not cover, or many different commands)
- Recent role or account changes

**Decision flow**:

```
Routine task by a user whose role lacks it → Correct the role or the automation
Single IP, many different rejected commands → Suspected privilege probing → Escalate (Step 3)
Several accounts or distributed IPs → Suspected stolen credentials → Escalate (Step 3)
```

### 2.3 bulk-delete-operations

**Verify**:
- Target volume / path
- User performing deletions
- Deletion rate (files/minute)
- ONTAP ARP (Autonomous Ransomware Protection) status

**Decision flow**:

```
Planned cleanup (change ticket exists) → Normal, acknowledge alarm
Single user abnormal deletion rate → Suspected ransomware → Escalate (Step 3, URGENT)
```

**Emergency response for suspected ransomware**:
1. Immediately create volume Snapshot: `volume snapshot create -vserver <svm> -volume <vol> -snapshot emergency-$(date +%Y%m%d%H%M)`
2. Consider force-disconnecting affected user CIFS sessions
3. Check ARP status: `security anti-ransomware volume show`
4. Escalate to security team

### 2.4 specific-user-activity

**Verify**:
- What operations the monitored user performed
- Whether operations are authorized (change ticket exists)
- Whether within normal business hours

**Decision flow**:

```
Authorized operation → Log and close
Unauthorized operation → Confirm with manager → Escalate if needed (Step 3)
```

---

## 3. Escalation

### Escalation Criteria

| Severity | Condition | Escalation Target |
|----------|-----------|-------------------|
| P1 (Critical) | Suspected ransomware, active mass deletion | Security + Storage Admin + Incident Response |
| P2 (High) | Suspected successful unauthorized access, potential data leak | Security + Data Owner |
| P3 (Medium) | Brute force attempts, suspicious patterns | Security (next business day OK) |
| P4 (Low) | Legitimate user error, threshold adjustment needed | Ops team internal |

### Information to Include in Escalation

- Alarm name and trigger timestamp
- Matched log lines (from SNS notification)
- Impact scope (volume name, file count, user count)
- Interim actions taken (Snapshot, session disconnect, etc.)
- Logs Insights query URL (console query link)

---

## 4. Post-Incident

### 4.1 Normal Resolution

- [ ] Confirm alarm state returned to OK
- [ ] Adjust threshold / pattern if needed
- [ ] Document the response

### 4.2 Security Incident

- [ ] Create incident report
- [ ] Root cause analysis (RCA)
- [ ] Implement preventive measures
- [ ] Improve alarm rules (fix false negatives or false positives)
- [ ] Conduct postmortem

---

## 5. Alarm Tuning

### Too Many False Positives

- Increase `AlarmThreshold` (0 → 5, etc.)
- Increase `QueryResultsToAlarm` (1 → 2, require sustained breach)
- Add exclusion conditions to query (`| filter @message not like /known-safe-pattern/`)

### Missing Detections

- Decrease `EvaluationFrequencyMinutes` (5 → 1 min)
- Broaden query filter patterns
- Lower `AlarmThreshold`
- **Watch for sparse patterns**: `stats count(*)` returns no result row on zero matches
  (treated as missing). The alarm only fires when a match lands in an evaluation window,
  so shorten the frequency to reduce window gaps (see the [setup guide pitfall
  section](../cloudwatch-log-alarm.md#common-pitfall-sparse-patterns-rarely-reach-alarm-confirmed-in-e2e))

### High Cost

- Increase `EvaluationFrequencyMinutes` (5 → 15 min)
- Add `limit` clause to queries
- Remove unnecessary alarms

---

## Related Documents

- [CloudWatch Log Alarm Setup Guide](../cloudwatch-log-alarm.md)
- [Detection Use Cases](../detection-use-cases.md)
- [DLQ Replay Runbook](./dlq-replay.md)
- [Lambda Errors Runbook](./lambda-errors.md)
- [Pipeline SLO](../pipeline-slo.md)
