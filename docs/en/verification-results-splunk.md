# Splunk Serverless Integration Verification Results

🌐 [日本語](../ja/verification-results-splunk.md) | **English** (this page)

> **This file used to be an unfilled template** — every judgment cell read
> `<PASS/FAIL>` and the environment fields were placeholders, which looked like a
> verification record while asserting nothing. It has been replaced with the
> evidence that does exist, recorded in
> [the integration README](../../integrations/splunk-serverless/README.md#e2e-verification-evidence).
> The blank form it used to be is redundant with
> [`shared/scripts/vendor-verification-checklist.md`](../../shared/scripts/vendor-verification-checklist.md),
> which is the checklist to work through for a new run.

---

## Audit log path — verified

**Environment**: Splunk Enterprise 10.4.0 running locally in Docker
(`splunk/splunk:latest`, `--platform linux/amd64`), HEC token supplied through the
`SPLUNK_HEC_TOKEN` environment variable.

**Method**: `python3 shared/scripts/test-xml-e2e.py --vendor splunk`

| Item | Result |
|------|--------|
| XML audit log parsing | ✅ 5 events parsed (EventID 4663 / 4656 / 4660) |
| HEC delivery | ✅ HTTP 200, body `{"text":"Success","code":0}` |
| Splunk indexing | ✅ 5 events confirmed in the `fsxn_audit` index |
| Field extraction | ✅ `user`, `path`, `client_ip`, `event_type`, `result`, `svm`, `timestamp` |
| Splunk Search UI | ✅ All events searchable and field-parsed |

**Screenshot**: [`integrations/splunk-serverless/screenshots/splunk-e2e-search-fsxn-audit-xml.png`](../../integrations/splunk-serverless/screenshots/splunk-e2e-search-fsxn-audit-xml.png)

### Scope of that result

Splunk Enterprise in Docker is a legitimate target — it is the same HTTP Event
Collector API and the same `/services/collector/event` contract that Splunk Cloud
exposes, so a HEC payload accepted here is accepted there. What it does not
exercise is Splunk Cloud's own ingress: DNS, TLS termination and the token issued
by a Cloud stack.

That substitution was not a shortcut. Splunk Cloud **free trial** accounts do not
reliably provision the HEC DNS record (`http-inputs-<stack>.splunkcloud.com`), so
a trial cannot be used for this test at all. Use Splunk Enterprise for local
validation, or a paid Splunk Cloud tier for a production-representative run.

---

## EMS and FPolicy paths — not recorded

`integrations/splunk-serverless/template-fpolicy.yaml` ships, and the EMS handler
is covered by unit tests, but **no end-to-end run has been recorded for either
path**. This is why the
[telemetry path coverage](README.md#telemetry-path-coverage) matrix marks both
🔧 (implemented, not yet E2E verified) rather than ✅.

Treat them as untested rather than broken: the shared EMS and FPolicy
infrastructure itself is verified in
[EMS/FPolicy E2E Verification Results](verification-results-ems-fpolicy.md), and
the Splunk handlers reuse the same `shared/python/ems_event.py` and
`shared/python/fpolicy_event.py` plumbing that the verified vendors use. What is
unverified is the Splunk-specific delivery of those two event types.

---

## Firehose path — not recorded

`template-firehose.yaml` provides the high-volume alternative (Splunk has a
built-in Firehose destination, so this path needs no Lambda per record). It has no
recorded end-to-end run either.

---

## What is not recorded

| Item | Status |
|------|--------|
| Verification date and verifier | Not recorded |
| AWS account, CloudFormation stack name | Not recorded |
| Splunk Cloud endpoint run | Not performed — see scope note above |
| EMS path E2E | Not recorded |
| FPolicy path E2E | Not recorded |
| Firehose path E2E | Not recorded |

---

## Automated coverage

```bash
python -m pytest integrations/splunk-serverless/tests/ -v
```

119 tests, covering HEC payload construction, batch splitting, the Firehose
transform function, EMS and FPolicy event parsing, and checkpoint handling. These
verify payload shape, not delivery.

---

## Related Documents

- [Splunk Serverless Integration README](../../integrations/splunk-serverless/README.md)
- [Migration from EC2](../../integrations/splunk-serverless/docs/en/migration-from-ec2.md)
- [EMS/FPolicy E2E Verification Results](verification-results-ems-fpolicy.md)
- [Vendor Verification Checklist](../../shared/scripts/vendor-verification-checklist.md)
- [Vendor Comparison](vendor-comparison.md)
