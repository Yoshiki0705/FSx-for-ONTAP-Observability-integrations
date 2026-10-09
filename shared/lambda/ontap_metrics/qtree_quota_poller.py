"""Qtree Quota Monitor — polls ONTAP REST API and publishes CloudWatch metrics.

Per qtree (dimensions SvmName, VolumeName, QtreeName):
  - QtreeQuotaUsedPercent
  - QtreeQuotaUsedBytes
  - QtreeQuotaLimitBytes

Per SVM, once per run (dimension SvmName only):
  - QtreeQuotaUsedPercentMax: max QtreeQuotaUsedPercent across the
    qtrees read this run. QtreeQuotaAlarm reads this series. Not
    published when no qtree has a usable hard limit (see below).
  - QtreeQuotaReportTruncated: 1 when the page cap stopped the read
    before the report ended, else 0.

This file is also the ONTAP REST client shared with the Terraform handler
(ontap_metrics_handler.py in the same directory), because CloudFormation
inline code must be a single file. The CloudFormation entry point,
lambda_handler below, passes none of the keyword arguments that the
Terraform handler uses (retries, credentials_ttl), so its behaviour is the
one this template has always had: no urllib3 retries, credentials cached for
the container lifetime, and no heartbeat metric.
"""
import json
import logging
import os
import time
import urllib.parse
from typing import Any

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

QTREE_NAMESPACE = "FSxONTAP/Qtree"
MAX_RECORDS = 200  # records per ONTAP page
MAX_PAGES = 50  # hard cap: 50 x 200 = 10,000 records per run
PUT_BATCH_SIZE = 20  # datums per PutMetricData call


class OntapAuthError(RuntimeError):
    """ONTAP rejected the credentials with HTTP 401 or 403."""


def _build_http() -> urllib3.PoolManager:
    """Build the connection pool, verifying TLS only when CA_CERT_PATH is set.

    Returns:
        A PoolManager with CERT_REQUIRED and the given CA bundle, or with
        CERT_NONE (and a warning) when CA_CERT_PATH is empty.
    """
    ca_cert_path = os.environ.get("CA_CERT_PATH", "")
    if ca_cert_path:
        return urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs=ca_cert_path)
    logger.warning(
        "TLS certificate verification DISABLED (cert_reqs=CERT_NONE). "
        "Acceptable for a PoC only. For production, set the CaCertPath "
        "(and CaCertLayerArn) stack parameters to the FSx for ONTAP CA "
        "certificate. Retrieve it via: security certificate show -type "
        "root-ca -vserver <svm> (ONTAP CLI), or in the AWS Management "
        "Console open the FSx for ONTAP file system, then "
        "Administration > Certificate."
    )
    return urllib3.PoolManager(cert_reqs="CERT_NONE")


http = _build_http()
cw = boto3.client("cloudwatch")
_creds_cache: dict[str, Any] | None = None
_creds_loaded_at: float = 0.0


def _get_credentials(ttl_seconds: float | None = None) -> dict[str, Any]:
    """Return the ONTAP credentials from Secrets Manager, cached.

    Args:
        ttl_seconds: Seconds after which the cached secret is read again.
            None caches it for the lifetime of the container.

    Returns:
        The secret JSON, with ``username`` and ``password`` keys.
    """
    global _creds_cache, _creds_loaded_at
    now = time.monotonic()
    expired = ttl_seconds is not None and now - _creds_loaded_at >= ttl_seconds
    if _creds_cache is None or expired:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=os.environ["ONTAP_CREDENTIALS_SECRET_ARN"])
        _creds_cache = json.loads(secret["SecretString"])
        _creds_loaded_at = now
    return _creds_cache


def invalidate_credentials() -> None:
    """Drop the cached credentials so the next request reads the secret again."""
    global _creds_cache
    _creds_cache = None


def _ontap_get(
    api_path: str,
    *,
    retries: urllib3.util.Retry | None = None,
    credentials_ttl: float | None = None,
) -> dict[str, Any]:
    """GET one ONTAP REST API path from the management endpoint.

    Args:
        api_path: Path starting with ``/api/``, including the query string.
        retries: urllib3 retry policy. None sends the request without a
            ``retries`` argument, as the CloudFormation template always has.
        credentials_ttl: Passed to _get_credentials as ttl_seconds.

    Returns:
        The decoded JSON body, or an empty dict for an empty body.

    Raises:
        RuntimeError: For a path outside ``/api/`` or an HTTP status >= 400.
        OntapAuthError: For HTTP 401 or 403 (a RuntimeError subclass).
    """
    # Only relative ONTAP API paths are accepted, so a next link can
    # never redirect the request (and the credentials) to another host.
    if not api_path.startswith("/api/"):
        raise RuntimeError(f"Refusing non-ONTAP API path: {api_path!r}")
    creds = _get_credentials(credentials_ttl)
    mgmt_ip = os.environ["ONTAP_MGMT_IP"]
    auth = urllib3.util.make_headers(
        basic_auth=f"{creds['username']}:{creds['password']}"
    )
    headers = {**auth, "Accept": "application/json"}
    url = f"https://{mgmt_ip}{api_path}"
    if retries is None:
        resp = http.request("GET", url, headers=headers, timeout=30.0)
    else:
        resp = http.request("GET", url, headers=headers, timeout=30.0, retries=retries)
    if resp.status in (401, 403):
        raise OntapAuthError(f"ONTAP API GET {api_path}: HTTP {resp.status}")
    if resp.status >= 400:
        raise RuntimeError(f"ONTAP API GET {api_path}: HTTP {resp.status}")
    return json.loads(resp.data) if resp.data else {}


def _fetch_quota_records(
    svm_name: str,
    *,
    retries: urllib3.util.Retry | None = None,
    credentials_ttl: float | None = None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Read the tree quota report, following _links.next.href.

    Args:
        svm_name: SVM whose tree quota report is read.
        retries: Passed to _ontap_get.
        credentials_ttl: Passed to _ontap_get.

    Returns:
        (records, pages_read, truncated). truncated is True only when a
        next link is still present after MAX_PAGES pages.
    """
    next_href: str | None = (
        "/api/storage/quota/reports"
        f"?svm.name={urllib.parse.quote(svm_name, safe='')}"
        "&type=tree"
        "&fields=space.used.total,space.hard_limit,qtree.name,volume.name"
        f"&max_records={MAX_RECORDS}"
    )
    records: list[dict[str, Any]] = []
    pages = 0
    while next_href is not None and pages < MAX_PAGES:
        data = _ontap_get(next_href, retries=retries, credentials_ttl=credentials_ttl)
        pages += 1
        records.extend(data.get("records", []))
        next_href = data.get("_links", {}).get("next", {}).get("href")
    truncated = next_href is not None
    if truncated:
        logger.warning(
            "Quota report for SVM %s truncated: stopped after %d pages "
            "(%d records) at the %d-page cap; qtrees beyond it are not "
            "monitored this run",
            svm_name, pages, len(records), MAX_PAGES,
        )
    return records, pages, truncated


def _build_metric_data(
    records: list[dict[str, Any]], svm_name: str
) -> tuple[list[dict[str, Any]], float | None]:
    """Build the three per-qtree datums for every qtree with a hard limit.

    Args:
        records: Quota report records.
        svm_name: Value of the SvmName dimension.

    Returns:
        (metric_data, max_percent). max_percent is None when no record has a
        qtree name and a hard limit above 0.
    """
    metric_data: list[dict[str, Any]] = []
    max_percent: float | None = None
    for record in records:
        qtree_name = record.get("qtree", {}).get("name", "")
        volume_name = record.get("volume", {}).get("name", "")
        used = record.get("space", {}).get("used", {}).get("total", 0)
        limit = record.get("space", {}).get("hard_limit", 0)

        if not qtree_name or limit == 0:
            continue  # Skip qtrees without quotas or unnamed

        used_percent = (used / limit * 100) if limit > 0 else 0
        if max_percent is None or used_percent > max_percent:
            max_percent = used_percent

        dimensions = [
            {"Name": "SvmName", "Value": svm_name},
            {"Name": "VolumeName", "Value": volume_name},
            {"Name": "QtreeName", "Value": qtree_name},
        ]

        metric_data.extend([
            {
                "MetricName": "QtreeQuotaUsedPercent",
                "Dimensions": dimensions,
                "Value": used_percent,
                "Unit": "Percent",
            },
            {
                "MetricName": "QtreeQuotaUsedBytes",
                "Dimensions": dimensions,
                "Value": used,
                "Unit": "Bytes",
            },
            {
                "MetricName": "QtreeQuotaLimitBytes",
                "Dimensions": dimensions,
                "Value": limit,
                "Unit": "Bytes",
            },
        ])
    return metric_data, max_percent


def _append_svm_summary(
    metric_data: list[dict[str, Any]],
    svm_name: str,
    max_percent: float | None,
    truncated: bool,
) -> None:
    """Append the per-SVM QtreeQuotaUsedPercentMax and truncation datums.

    Args:
        metric_data: List the datums are appended to.
        svm_name: Value of the SvmName dimension.
        max_percent: Maximum used percent this run, or None.
        truncated: Whether the page cap stopped the read.
    """
    svm_dimensions = [{"Name": "SvmName", "Value": svm_name}]

    # With no usable record there is nothing to take the max of. A 0
    # datum would read as "every quota is healthy" for an SVM this run
    # could not measure, so publish nothing; QtreeQuotaAlarm then goes
    # to INSUFFICIENT_DATA (TreatMissingData: missing). A failed run
    # surfaces through the DLQ-depth alarm instead.
    if max_percent is not None:
        metric_data.append({
            "MetricName": "QtreeQuotaUsedPercentMax",
            "Dimensions": svm_dimensions,
            "Value": max_percent,
            "Unit": "Percent",
        })
    metric_data.append({
        "MetricName": "QtreeQuotaReportTruncated",
        "Dimensions": svm_dimensions,
        "Value": 1.0 if truncated else 0.0,
        "Unit": "Count",
    })


def _put_metric_data(namespace: str, metric_data: list[dict[str, Any]]) -> int:
    """Publish datums in batches of PUT_BATCH_SIZE.

    Args:
        namespace: CloudWatch namespace.
        metric_data: Datums to publish.

    Returns:
        The number of datums published.
    """
    published = 0
    for i in range(0, len(metric_data), PUT_BATCH_SIZE):
        batch = metric_data[i:i + PUT_BATCH_SIZE]
        cw.put_metric_data(Namespace=namespace, MetricData=batch)
        published += len(batch)
    return published


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """CloudFormation entry point (index.lambda_handler): one SVM's qtree quotas.

    Args:
        event: EventBridge scheduled event (unused).
        context: Lambda context (unused).

    Returns:
        A run summary.
    """
    svm_name = os.environ["SVM_NAME"]
    namespace = os.environ["METRIC_NAMESPACE"]

    records, pages, truncated = _fetch_quota_records(svm_name)
    logger.info(
        "Found %d qtree quota reports for SVM %s in %d page(s)",
        len(records), svm_name, pages,
    )

    metric_data, max_percent = _build_metric_data(records, svm_name)
    _append_svm_summary(metric_data, svm_name, max_percent, truncated)
    published = _put_metric_data(namespace, metric_data)

    logger.info("Published %d metric data points", published)
    return {
        "qtrees_monitored": len(records),
        "pages_read": pages,
        "truncated": truncated,
        "max_used_percent": max_percent,
        "metrics_published": published,
    }
