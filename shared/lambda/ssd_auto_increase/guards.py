"""Run-time guards: alarm state, target, administrative actions, cooldown, IOPS.

These are the Python half of the guard split in
docs/en/capacity-automation-t4-design.md. Each function reads AWS API responses
(already fetched by the handler) and returns a decision; none calls
UpdateFileSystem. The handler wires them in order and only reaches the API call
when every guard passes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from config import max_ssd_iops

# AdministrativeAction statuses that mean an action is still active, so no new
# call is made. UPDATED_OPTIMIZING is active: the capacity is usable but
# optimization is running.
ACTIVE_UPDATE_STATUSES = frozenset(
    {"PENDING", "IN_PROGRESS", "UPDATED_OPTIMIZING", "OPTIMIZING", "PAUSED"}
)
# Statuses T4 treats as terminal.
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

COOLDOWN_HOURS = 6
# AdministrativeAction types whose RequestTime starts the shared cooldown.
COOLDOWN_ACTION_TYPES = frozenset(
    {"FILE_SYSTEM_UPDATE", "STORAGE_OPTIMIZATION"}
)


@dataclass(frozen=True)
class TargetDecision:
    """Result of the ceil-at-10%-floor target computation.

    Attributes:
        should_call: True when a request should go out.
        target_gib: The computed target in GiB.
        reason: A machine-readable reason when ``should_call`` is False.
    """

    should_call: bool
    target_gib: int
    reason: str | None


def alarm_in_alarm(describe_alarms_response: dict[str, Any]) -> bool:
    """Return True when at least one trigger alarm is in ALARM.

    Args:
        describe_alarms_response: A ``DescribeAlarms`` response.

    Returns:
        True when any ``MetricAlarms`` entry has ``StateValue == "ALARM"``.
    """
    return any(
        alarm.get("StateValue") == "ALARM"
        for alarm in describe_alarms_response.get("MetricAlarms", [])
    )


def compute_target(current_gib: int, increase_percent: int, ceiling_gib: int) -> TargetDecision:
    """Compute the SSD target, honouring the 10% floor and the ceiling.

    ``target = min(ceiling, max(ceil(current*1.10), ceil(current*(1+pct/100))))``.
    No call when ``target < ceil(current*1.10)`` (the ceiling leaves no room for
    the 10% minimum) or ``target <= current``.

    Args:
        current_gib: Current SSD capacity in GiB.
        increase_percent: Requested increase percent.
        ceiling_gib: Absolute ceiling in GiB.

    Returns:
        A :class:`TargetDecision`.
    """
    # Integer-rational ceiling arithmetic. Binary floating-point multiplication
    # (``current_gib * 1.10``) can round an exact boundary up: 1500 * 1.10 is
    # 1650.0000000000002, so ``math.ceil`` would return 1651 and reject a valid
    # ceiling of 1650. ``(n * num + den - 1) // den`` is ceil(n * num / den) with
    # no rounding error, and agrees with Terraform's decimal-number check.
    floor_target = (current_gib * 110 + 99) // 100
    requested = (current_gib * (100 + increase_percent) + 99) // 100
    target = min(ceiling_gib, max(floor_target, requested))
    if target <= current_gib:
        return TargetDecision(False, target, "ceiling_reached")
    if target < floor_target:
        # The ceiling caps below the 10% minimum, so the service would reject it.
        return TargetDecision(False, target, "ceiling_reached")
    return TargetDecision(True, target, None)


def administrative_action_in_progress(
    administrative_actions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the first active administrative action blocking a new request.

    Any ``FILE_SYSTEM_UPDATE`` in an active status, or any
    ``STORAGE_OPTIMIZATION`` that is not COMPLETED, blocks a call.

    Args:
        administrative_actions: The ``AdministrativeActions`` list from
            DescribeFileSystems.

    Returns:
        The blocking action, or ``None`` when none is active.
    """
    for action in administrative_actions:
        action_type = action.get("AdministrativeActionType")
        status = action.get("Status")
        if action_type == "FILE_SYSTEM_UPDATE" and status in ACTIVE_UPDATE_STATUSES:
            return action
        if action_type == "STORAGE_OPTIMIZATION" and status != "COMPLETED":
            return action
    return None


def cooldown_active(
    administrative_actions: list[dict[str, Any]], now: datetime
) -> datetime | None:
    """Return the next-eligible time when the 6-hour cooldown is active.

    The cooldown is shared across SSD, IOPS and throughput changes. It reads the
    most recent ``RequestTime`` of a cooldown-relevant administrative action.

    Args:
        administrative_actions: The ``AdministrativeActions`` list.
        now: Current time (timezone-aware UTC).

    Returns:
        The time the cooldown expires when it is still active, else ``None``.
    """
    latest: datetime | None = None
    for action in administrative_actions:
        if action.get("AdministrativeActionType") not in COOLDOWN_ACTION_TYPES:
            continue
        request_time = action.get("RequestTime")
        if not isinstance(request_time, datetime):
            continue
        if request_time.tzinfo is None:
            request_time = request_time.replace(tzinfo=timezone.utc)
        if latest is None or request_time > latest:
            latest = request_time
    if latest is None:
        return None
    eligible_at = latest + timedelta(hours=COOLDOWN_HOURS)
    return eligible_at if eligible_at > now else None


@dataclass(frozen=True)
class IopsDecision:
    """Result of IOPS-mode handling.

    Attributes:
        include_iops: True when an Iops argument should be sent.
        iops: The Iops value (0 when not included).
        exceeds_maximum: True when the computed value exceeds the Region maximum.
    """

    include_iops: bool
    iops: int
    exceeds_maximum: bool


def compute_iops(
    iops_mode: str,
    current_iops: int,
    target_gib: int,
    deployment_type: str,
    ha_pairs: int,
    region: str,
) -> IopsDecision:
    """Compute the IOPS argument for the target.

    AUTOMATIC: no IOPS argument. USER_PROVISIONED: ``Iops = max(current, 3*target)``
    because user-provisioned IOPS must be at least 3 per requested GiB; when that
    exceeds the Region maximum the handler latches ``iops_exceeds_maximum``.

    Args:
        iops_mode: ``AUTOMATIC`` or ``USER_PROVISIONED``.
        current_iops: Currently provisioned IOPS.
        target_gib: The computed target in GiB.
        deployment_type: The file system's deployment type.
        ha_pairs: HA-pair count.
        region: AWS Region name.

    Returns:
        An :class:`IopsDecision`.
    """
    if iops_mode != "USER_PROVISIONED":
        return IopsDecision(False, 0, False)
    iops = max(current_iops, 3 * target_gib)
    maximum = max_ssd_iops(deployment_type, ha_pairs, region)
    return IopsDecision(True, iops, iops > maximum)
