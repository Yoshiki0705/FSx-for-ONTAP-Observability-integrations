"""Shared AWS Backup / RAM event normaliser for vendor shipper Lambdas.

AWS Backup and AWS Resource Access Manager (RAM) reach a vendor Lambda through
an EventBridge rule on the account default bus. The raw events disagree on which
field carries the job outcome and on how a logically-air-gapped-vault (LAG-vault)
share revocation is signalled, so every vendor formatter would otherwise repeat
the same fallbacks and the same classification logic.

Two schema facts drive this module (AWS docs, read in full):

1. Backup / copy / vault / plan events carry the outcome in ``detail.state``;
   restore and recovery-point events carry it in ``detail.status``. The
   normaliser unifies both into a single ``state`` key so downstream layers
   never re-derive it. See
   https://docs.aws.amazon.com/aws-backup/latest/devguide/eventbridge.html
2. "Completed with issues" is NOT an EventBridge state. The doc defines it as a
   console representation of a backup job that reached ``COMPLETED`` while
   carrying a status message, and states it applies to backup jobs only. It is
   detected here as detail-type ``Backup Job State Change`` with
   ``state == "COMPLETED"`` and a non-empty ``statusMessage`` — never as a
   distinct state and never on copy jobs.
3. RAM revocation has no native "revoked" state value. A revocation is caught
   through the CloudTrail route (detail-type ``AWS API Call via CloudTrail``,
   ``eventSource`` ``ram.amazonaws.com``, ``eventName`` in
   {``DisassociateResourceShare``, ``DeleteResourceShare``}). See
   https://docs.aws.amazon.com/ram/latest/userguide/using-eventbridge.html

Typical use in a vendor's ``backup_handler.py``::

    from aws_backup_event import extract_detail, normalize_backup_event

    def lambda_handler(event, context):
        detail = extract_detail(event)
        record = normalize_backup_event(event)
        ...  # ship `record`
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "extract_detail",
    "normalize_backup_event",
]

# detail-type strings this normaliser recognises.
_BACKUP_JOB = "Backup Job State Change"
_CLOUDTRAIL_API_CALL = "AWS API Call via CloudTrail"
_RAM_STATE_CHANGE = "Resource Sharing State Change"

# CloudTrail event names that signal a RAM share revocation / deletion.
_REVOKE_EVENT_NAMES = frozenset(
    {"DisassociateResourceShare", "DeleteResourceShare"}
)


def extract_detail(event: dict[str, Any]) -> dict[str, Any]:
    """Extract the ``detail`` object from an EventBridge event.

    Args:
        event: EventBridge event with source ``aws.backup`` or ``aws.ram``.

    Returns:
        The ``detail`` object.

    Raises:
        ValueError: If ``detail`` is missing or is not an object.
    """
    detail = event.get("detail")
    if detail is None:
        raise ValueError("Event detail is missing")
    if not isinstance(detail, dict):
        raise ValueError(f"Unexpected detail type: {type(detail).__name__}")
    return detail


def _classify(
    detail_type: str,
    state: str,
    status_message: str,
    event_name: str,
) -> str:
    """Compute the stable classification label from the unified fields.

    This is the single place the "completed with issues" (schema fact 2) and
    "revoked" (schema fact 3) logic lives, so the dashboard and vendor layers
    never re-derive it.

    Args:
        detail_type: The EventBridge ``detail-type`` string.
        state: The unified ``detail.state`` or ``detail.status`` value.
        status_message: The ``detail.statusMessage`` value (may be empty).
        event_name: The CloudTrail ``detail.eventName`` value (may be empty).

    Returns:
        One of ``"completed_with_issues"``, ``"ok"``, ``"failed"``,
        ``"revoked"``, or the lowercased state when none of the rules match.
    """
    upper_state = state.upper()

    # RAM revocation via CloudTrail: no native state value (schema fact 3).
    if detail_type == _CLOUDTRAIL_API_CALL and event_name in _REVOKE_EVENT_NAMES:
        return "revoked"

    # "Completed with issues" — backup jobs only (schema fact 2). A backup job
    # that reached COMPLETED with a status message is the only path to this
    # label; copy jobs never get it.
    if detail_type == _BACKUP_JOB and upper_state == "COMPLETED" and status_message:
        return "completed_with_issues"

    if upper_state == "FAILED":
        return "failed"

    # A clean terminal success on any job type normalises to "ok".
    if upper_state == "COMPLETED":
        return "ok"

    if upper_state:
        return upper_state.lower()

    return "ok"


def normalize_backup_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize an AWS Backup / RAM event to one stable set of field names.

    The returned dict has a fixed shape with empty-string defaults for every
    string field, so vendor formatters can emit each field unconditionally. The
    ``state``/``status`` unification and the ``classification`` field carry the
    schema facts described in the module docstring.

    Args:
        event: Raw EventBridge event (source ``aws.backup`` or ``aws.ram``).

    Returns:
        Dict with keys ``timestamp``, ``event_name``, ``source``, ``state``,
        ``status_message``, ``classification``, ``resource_type``,
        ``resource_arn``, ``backup_vault_name``, ``source_backup_vault_arn``,
        ``destination_backup_vault_arn``, ``job_id``, ``account``, ``region``,
        and ``raw`` (the original event, preserved by identity).

    Raises:
        ValueError: If ``detail`` is missing or is not an object.
    """
    detail = extract_detail(event)

    detail_type = event.get("detail-type", "")
    # backup/copy/vault/plan carry detail.state; restore/recovery-point carry
    # detail.status — unify into one key here.
    state = detail.get("state") or detail.get("status") or ""
    status_message = detail.get("statusMessage", "")
    event_name = detail.get("eventName", "")

    resource_arn = (
        detail.get("resourceArn")
        or (event.get("resources") or [""])[0]
        or ""
    )
    job_id = (
        detail.get("backupJobId")
        or detail.get("copyJobId")
        or detail.get("restoreJobId")
        or ""
    )

    return {
        "timestamp": event.get("time", ""),
        "event_name": detail_type,
        "source": event.get("source", ""),
        "state": state,
        "status_message": status_message,
        "classification": _classify(
            detail_type, state, status_message, event_name
        ),
        "resource_type": detail.get("resourceType", ""),
        "resource_arn": resource_arn,
        "backup_vault_name": detail.get("backupVaultName", ""),
        "source_backup_vault_arn": detail.get("sourceBackupVaultArn", ""),
        "destination_backup_vault_arn": detail.get(
            "destinationBackupVaultArn", ""
        ),
        "job_id": job_id,
        "account": event.get("account", ""),
        "region": event.get("region", ""),
        "raw": event,
    }
