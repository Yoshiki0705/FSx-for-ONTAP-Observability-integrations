"""Terraform entry point: run the enabled ONTAP collectors and publish a heartbeat.

Deployed by terraform/fsxn-ontap-custom-metrics with handler
``ontap_metrics_handler.lambda_handler``. The CloudFormation template
shared/templates/qtree-quota-monitor.yaml does not use this file; it runs
qtree_quota_poller.lambda_handler inline.

Environment (secrets are never in the environment, only the secret ARN):
  ONTAP_MGMT_IP                    management endpoint IP of the file system
  ONTAP_CREDENTIALS_SECRET_ARN     Secrets Manager secret {"username","password"}
  FILE_SYSTEM_ID                   value of the FileSystemId dimension
  COLLECTORS                       comma list of ``qtree`` and ``snapmirror``
  SVM_NAME                         required when ``qtree`` is enabled
  SNAPMIRROR_MAX_RELATIONSHIPS     per-relationship series cap (default 100)
  CREDENTIALS_CACHE_TTL_SECONDS    secret re-read interval (default 300)
  CA_CERT_PATH                     optional CA bundle for TLS verification

Failure signalling:
  Each collector runs fetch, build and publish inside its own try/except and
  then publishes ``CollectorSucceeded`` (1 or 0) into its own namespace with
  dimensions FileSystemId and Collector. A collector failure does not raise:
  the heartbeat alarm (TreatMissingData breaching) reports it, and raising
  would make Lambda retry the invocation twice, repeating a failing ONTAP
  login. An HTTP 401 or 403 drops the cached credentials and marks every
  remaining collector failed without another ONTAP request, because repeated
  basic-auth failures can lock the account. If publishing the heartbeat itself
  fails, the handler raises, so the Lambda Errors alarm and the DLQ see it.
"""
import logging
import os
from collections.abc import Callable
from typing import Any

import urllib3

import qtree_quota_poller as ontap
import snapmirror_collector as snapmirror

logger = logging.getLogger()
logger.setLevel(logging.INFO)

COLLECTOR_NAMESPACES = {
    "qtree": ontap.QTREE_NAMESPACE,
    "snapmirror": snapmirror.SNAPMIRROR_NAMESPACE,
}
DEFAULT_MAX_RELATIONSHIPS = 100
DEFAULT_CREDENTIALS_TTL_SECONDS = 300.0


def build_retry() -> urllib3.util.Retry:
    """Return the retry policy for ONTAP GET requests.

    Three retries with exponential backoff on connection errors, read errors
    and 429/5xx. 401 and 403 are not in the forcelist, so a rejected login is
    never repeated. Redirects are refused so credentials cannot follow one.
    Only arguments present in both urllib3 1.26 and 2.x are used.

    Returns:
        A urllib3 Retry.
    """
    return urllib3.util.Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        redirect=0,
        raise_on_status=False,
        respect_retry_after_header=True,
    )


def parse_collectors(raw: str) -> list[str]:
    """Parse COLLECTORS into an ordered, de-duplicated list.

    Args:
        raw: Comma-separated collector names.

    Returns:
        Collector names in the given order.

    Raises:
        ValueError: For an unknown name or an empty list.
    """
    names: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if name not in COLLECTOR_NAMESPACES:
            raise ValueError(
                f"Unknown collector {name!r} in COLLECTORS; expected "
                f"{sorted(COLLECTOR_NAMESPACES)}"
            )
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError("COLLECTORS is empty; enable at least one collector")
    return names


def _run_qtree(
    file_system_id: str, retries: urllib3.util.Retry, credentials_ttl: float
) -> dict[str, Any]:
    """Qtree collector: same datums as the CloudFormation entry point.

    Args:
        file_system_id: Unused by the qtree series (kept for a uniform signature).
        retries: Retry policy for ONTAP requests.
        credentials_ttl: Credential cache TTL in seconds.

    Returns:
        A run summary.
    """
    del file_system_id  # qtree series carry SvmName, not FileSystemId (CFN parity)
    svm_name = os.environ["SVM_NAME"]
    records, pages, truncated = ontap._fetch_quota_records(
        svm_name, retries=retries, credentials_ttl=credentials_ttl
    )
    metric_data, max_percent = ontap._build_metric_data(records, svm_name)
    ontap._append_svm_summary(metric_data, svm_name, max_percent, truncated)
    published = ontap._put_metric_data(ontap.QTREE_NAMESPACE, metric_data)
    logger.info(
        "qtree: %d quota report(s) for SVM %s in %d page(s), %d datum(s) published",
        len(records), svm_name, pages, published,
    )
    return {
        "qtrees_monitored": len(records),
        "pages_read": pages,
        "truncated": truncated,
        "max_used_percent": max_percent,
        "metrics_published": published,
    }


def _run_snapmirror(
    file_system_id: str, retries: urllib3.util.Retry, credentials_ttl: float
) -> dict[str, Any]:
    """SnapMirror collector on the destination file system.

    Args:
        file_system_id: Value of the FileSystemId dimension.
        retries: Retry policy for ONTAP requests.
        credentials_ttl: Credential cache TTL in seconds.

    Returns:
        A run summary.
    """
    max_relationships = int(
        os.environ.get("SNAPMIRROR_MAX_RELATIONSHIPS", str(DEFAULT_MAX_RELATIONSHIPS))
    )
    records, pages, page_truncated = snapmirror.fetch_relationships(
        retries=retries, credentials_ttl=credentials_ttl
    )
    metric_data, summary = snapmirror.build_snapmirror_metric_data(
        records, file_system_id, max_relationships, page_truncated
    )
    published = ontap._put_metric_data(snapmirror.SNAPMIRROR_NAMESPACE, metric_data)
    return {**summary, "pages_read": pages, "metrics_published": published}


RUNNERS: dict[str, Callable[[str, urllib3.util.Retry, float], dict[str, Any]]] = {
    "qtree": _run_qtree,
    "snapmirror": _run_snapmirror,
}


def _heartbeat(file_system_id: str, collector: str, succeeded: bool) -> None:
    """Publish CollectorSucceeded for one collector. Raises on failure."""
    ontap._put_metric_data(
        COLLECTOR_NAMESPACES[collector],
        [{
            "MetricName": "CollectorSucceeded",
            "Dimensions": [
                {"Name": "FileSystemId", "Value": file_system_id},
                {"Name": "Collector", "Value": collector},
            ],
            "Value": 1.0 if succeeded else 0.0,
            "Unit": "Count",
        }],
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run each enabled collector in isolation, then publish the heartbeats.

    Args:
        event: EventBridge scheduled event (unused).
        context: Lambda context (unused).

    Returns:
        ``{"collectors": {<name>: {"succeeded": bool, ...summary}}}``.

    Raises:
        ValueError: For an unknown or empty COLLECTORS value, or qtree
            enabled without SVM_NAME. Raised before any ONTAP request.
        KeyError: When FILE_SYSTEM_ID or COLLECTORS is missing.
        Exception: Whatever publishing a heartbeat raised.
    """
    file_system_id = os.environ["FILE_SYSTEM_ID"]
    collectors = parse_collectors(os.environ["COLLECTORS"])
    if "qtree" in collectors and not os.environ.get("SVM_NAME"):
        raise ValueError("SVM_NAME is required when the qtree collector is enabled")
    credentials_ttl = float(
        os.environ.get("CREDENTIALS_CACHE_TTL_SECONDS", str(DEFAULT_CREDENTIALS_TTL_SECONDS))
    )
    retries = build_retry()

    results: dict[str, dict[str, Any]] = {}
    auth_failure: str | None = None
    for name in collectors:
        if auth_failure is not None:
            results[name] = {
                "succeeded": False,
                "error": f"skipped after ONTAP authentication failure ({auth_failure})",
            }
            continue
        try:
            summary = RUNNERS[name](file_system_id, retries, credentials_ttl)
            results[name] = {"succeeded": True, **summary}
        except ontap.OntapAuthError as exc:
            ontap.invalidate_credentials()
            auth_failure = str(exc)
            remaining = collectors[collectors.index(name) + 1:]
            logger.error(
                "ONTAP rejected the credentials in collector %s (%s); cached "
                "credentials dropped, not retried, remaining collectors %s skipped",
                name, exc, remaining,
            )
            results[name] = {"succeeded": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 -- isolation is the point
            logger.exception("Collector %s failed", name)
            results[name] = {"succeeded": False, "error": f"{type(exc).__name__}: {exc}"}

    # Outside the per-collector try on purpose: a heartbeat that cannot be
    # published must fail the invocation (Lambda Errors alarm, DLQ).
    for name in collectors:
        _heartbeat(file_system_id, name, results[name]["succeeded"])

    return {"collectors": results}
