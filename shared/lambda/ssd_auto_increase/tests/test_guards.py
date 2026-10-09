"""Guard functions: target, alarm state, admin actions, cooldown, IOPS, ceiling."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ConfigError, max_ssd_iops, shape_max_gib  # noqa: E402
from guards import (  # noqa: E402
    administrative_action_in_progress,
    alarm_in_alarm,
    compute_iops,
    compute_target,
    cooldown_active,
)
from ssd_test_support import admin_action  # noqa: E402


# -- target --------------------------------------------------------------


def test_target_ceils_at_10_percent() -> None:
    # ceil(1024 * 1.10) = 1127, above the ceiling-free requested value.
    decision = compute_target(1024, 10, 4096)
    assert decision.should_call
    assert decision.target_gib == 1127


def test_target_capped_at_ceiling() -> None:
    decision = compute_target(1024, 100, 1200)
    assert decision.should_call
    assert decision.target_gib == 1200


def test_target_at_ceiling_does_not_call() -> None:
    decision = compute_target(1200, 10, 1200)
    assert not decision.should_call
    assert decision.reason == "ceiling_reached"


def test_target_ceiling_leaves_no_room_for_10_percent() -> None:
    # ceiling below ceil(current*1.10) means the service minimum cannot be met.
    decision = compute_target(1024, 10, 1100)
    assert not decision.should_call
    assert decision.reason == "ceiling_reached"


def test_higher_increase_percent_respected() -> None:
    # ceil(1000 * 1.50) = 1500.
    decision = compute_target(1000, 50, 4096)
    assert decision.target_gib == 1500


def test_exact_10_percent_ceiling_is_accepted() -> None:
    # 1500 * 1.10 == 1650 exactly. Binary float multiplication produces
    # 1650.0000000000002, so math.ceil would return 1651 and reject a valid
    # ceiling of 1650; the integer-rational ceiling must return 1650 and call.
    decision = compute_target(1500, 10, 1650)
    assert decision.should_call
    assert decision.target_gib == 1650


def test_exact_10_percent_floor_matches_terraform_decimal_check() -> None:
    # The run-time floor target must equal the deploy-time decimal computation
    # so the two guards never disagree on an exact boundary.
    decision = compute_target(1500, 10, 4096)
    assert decision.target_gib == 1650


# -- alarm state ---------------------------------------------------------


def test_alarm_in_alarm_true() -> None:
    assert alarm_in_alarm({"MetricAlarms": [{"StateValue": "ALARM"}]})


@pytest.mark.parametrize("state", ["OK", "INSUFFICIENT_DATA"])
def test_alarm_not_in_alarm(state: str) -> None:
    assert not alarm_in_alarm({"MetricAlarms": [{"StateValue": state}]})


def test_alarm_one_of_many_in_alarm() -> None:
    assert alarm_in_alarm(
        {"MetricAlarms": [{"StateValue": "OK"}, {"StateValue": "ALARM"}]}
    )


# -- administrative actions ---------------------------------------------


@pytest.mark.parametrize(
    "status", ["PENDING", "IN_PROGRESS", "UPDATED_OPTIMIZING", "OPTIMIZING", "PAUSED"]
)
def test_file_system_update_active_blocks(status: str) -> None:
    actions = [admin_action(action_type="FILE_SYSTEM_UPDATE", status=status)]
    assert administrative_action_in_progress(actions) is not None


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "CANCELLED"])
def test_file_system_update_terminal_does_not_block(status: str) -> None:
    actions = [admin_action(action_type="FILE_SYSTEM_UPDATE", status=status)]
    assert administrative_action_in_progress(actions) is None


def test_storage_optimization_not_completed_blocks() -> None:
    actions = [admin_action(action_type="STORAGE_OPTIMIZATION", status="IN_PROGRESS")]
    assert administrative_action_in_progress(actions) is not None


def test_storage_optimization_completed_does_not_block() -> None:
    actions = [admin_action(action_type="STORAGE_OPTIMIZATION", status="COMPLETED")]
    assert administrative_action_in_progress(actions) is None


def test_no_actions_do_not_block() -> None:
    assert administrative_action_in_progress([]) is None


# -- cooldown ------------------------------------------------------------


def test_cooldown_active_within_6_hours() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    actions = [admin_action(request_time=now - timedelta(hours=2), status="COMPLETED")]
    eligible = cooldown_active(actions, now)
    assert eligible == now - timedelta(hours=2) + timedelta(hours=6)


def test_cooldown_expired() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    actions = [admin_action(request_time=now - timedelta(hours=7), status="COMPLETED")]
    assert cooldown_active(actions, now) is None


def test_cooldown_no_relevant_actions() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    assert cooldown_active([], now) is None


def test_cooldown_uses_latest_request_time() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    actions = [
        admin_action(request_time=now - timedelta(hours=7), status="COMPLETED"),
        admin_action(request_time=now - timedelta(hours=1), status="COMPLETED"),
    ]
    eligible = cooldown_active(actions, now)
    assert eligible == now - timedelta(hours=1) + timedelta(hours=6)


# -- IOPS mode -----------------------------------------------------------


def test_iops_automatic_sends_no_argument() -> None:
    decision = compute_iops("AUTOMATIC", 3072, 2048, "SINGLE_AZ_1", 1, "ap-northeast-1")
    assert not decision.include_iops
    assert not decision.exceeds_maximum


def test_iops_user_provisioned_three_per_gib() -> None:
    decision = compute_iops("USER_PROVISIONED", 3072, 2048, "SINGLE_AZ_1", 1, "ap-northeast-1")
    assert decision.include_iops
    assert decision.iops == max(3072, 3 * 2048)


def test_iops_keeps_current_when_higher() -> None:
    decision = compute_iops("USER_PROVISIONED", 100000, 2048, "us-east-1", 1, "us-east-1")
    assert decision.iops == 100000


def test_iops_exceeds_region_maximum() -> None:
    # ap-northeast-1 is a first-generation 80,000 Region; 3 * 30000 = 90000.
    decision = compute_iops("USER_PROVISIONED", 0, 30000, "SINGLE_AZ_1", 1, "ap-northeast-1")
    assert decision.exceeds_maximum


def test_iops_within_high_region_maximum() -> None:
    # us-east-1 first-generation maximum is 160,000; 3 * 30000 = 90000.
    decision = compute_iops("USER_PROVISIONED", 0, 30000, "SINGLE_AZ_1", 1, "us-east-1")
    assert not decision.exceeds_maximum


# -- shape maximum (run-time ceiling re-check) ---------------------------


@pytest.mark.parametrize(
    ("deployment_type", "ha_pairs", "expected"),
    [
        ("SINGLE_AZ_1", 1, 196608),
        ("MULTI_AZ_1", 1, 196608),
        ("MULTI_AZ_2", 1, 524288),
        ("SINGLE_AZ_2", 1, 524288),
        ("SINGLE_AZ_2", 2, 1048576),
        ("SINGLE_AZ_2", 3, 1048576),
    ],
)
def test_shape_max_gib(deployment_type: str, ha_pairs: int, expected: int) -> None:
    assert shape_max_gib(deployment_type, ha_pairs) == expected


def test_shape_max_gib_unknown_type_raises() -> None:
    with pytest.raises(ConfigError):
        shape_max_gib("SINGLE_AZ_9", 1)


def test_max_ssd_iops_regions() -> None:
    assert max_ssd_iops("SINGLE_AZ_1", 1, "us-east-1") == 160000
    assert max_ssd_iops("SINGLE_AZ_1", 1, "ap-northeast-1") == 80000
    assert max_ssd_iops("SINGLE_AZ_2", 3, "ap-northeast-1") == 600000
    assert max_ssd_iops("MULTI_AZ_2", 1, "ap-northeast-1") == 200000
