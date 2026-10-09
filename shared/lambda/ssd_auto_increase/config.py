"""Environment parsing and the shape-to-maximum SSD table.

The maximum table mirrors ``local.shape_max_gib`` in the module's main.tf: the
Lambda re-checks the ceiling against the file system's shape at run time, so a
file system whose shape changed after deployment, or a plan that ran without
the precondition, is still caught before any request.

Values are from the FSx for ONTAP quotas page
(https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/limits.html, documented,
read 2026-10-08): 192 TiB (196,608 GiB) for first-generation file systems,
512 TiB (524,288 GiB) for second-generation Multi-AZ, and 512 TiB per HA pair
up to 1 PiB (1,048,576 GiB) for second-generation Single-AZ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Per-Region maximum SSD IOPS, used for the iops_exceeds_maximum guard. First
# generation: 160,000 in us-east-2/us-east-1/us-west-2/eu-west-1, 80,000
# elsewhere. Second generation: 200,000 per HA pair Single-AZ (up to 12 pairs),
# 200,000 total Multi-AZ (quotas page, documented, read 2026-10-07/08).
FIRST_GEN_HIGH_IOPS_REGIONS = frozenset(
    {"us-east-2", "us-east-1", "us-west-2", "eu-west-1"}
)
FIRST_GEN_HIGH_IOPS = 160000
FIRST_GEN_DEFAULT_IOPS = 80000
SECOND_GEN_IOPS_PER_UNIT = 200000

FIRST_GEN_DEPLOYMENT_TYPES = frozenset({"SINGLE_AZ_1", "MULTI_AZ_1"})


class ConfigError(ValueError):
    """A required environment value is missing or malformed."""


@dataclass(frozen=True)
class Config:
    """Parsed handler environment.

    Attributes:
        file_system_id: The managed file system ID.
        mode: One of ``notify_only``, ``approve`` or ``auto``.
        max_storage_capacity_gib: Required absolute SSD ceiling in GiB.
        increase_percent: Requested increase percent (never below the 10% floor).
        trigger_alarm_names: Names of the trigger alarms to read with DescribeAlarms.
        aggregate_names: Second-generation Aggregate names (``aggregate_names``
            in the module), one per-aggregate trigger alarm each; the
            utilization query reads the same per-aggregate series. Empty on
            first generation.
        lock_table_name: DynamoDB single-flight lock table name.
        notify_topic_arn: SNS topic for reports and approve emails.
        decision_log_group: CloudWatch Logs group for the decision log.
        decision_archive_bucket: S3 bucket for the audit archive.
        decision_archive_prefix: Key prefix for archive objects.
        decision_archive_required_mode: ``COMPLIANCE`` or ``GOVERNANCE``.
        decision_archive_min_retention_days: Minimum retain-until period in days.
        indeterminate_reconcile_hours: Reconciliation window before escalation.
        config_fingerprint: Hash from HCL; a blocked latch clears when it changes.
    """

    file_system_id: str
    mode: str
    max_storage_capacity_gib: int
    increase_percent: int
    trigger_alarm_names: tuple[str, ...]
    aggregate_names: tuple[str, ...]
    lock_table_name: str
    notify_topic_arn: str
    decision_log_group: str
    decision_archive_bucket: str
    decision_archive_prefix: str
    decision_archive_required_mode: str
    decision_archive_min_retention_days: int
    indeterminate_reconcile_hours: int
    config_fingerprint: str


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"environment variable {name} is required")
    return value


def load_config() -> Config:
    """Read and validate the handler environment.

    Returns:
        A :class:`Config`.

    Raises:
        ConfigError: For a missing or malformed value.
    """
    mode = _require("MODE")
    if mode not in ("notify_only", "approve", "auto"):
        raise ConfigError(f"MODE must be notify_only, approve or auto, got {mode!r}")
    required_mode = _require("DECISION_ARCHIVE_REQUIRED_MODE")
    if required_mode not in ("COMPLIANCE", "GOVERNANCE"):
        raise ConfigError(
            f"DECISION_ARCHIVE_REQUIRED_MODE must be COMPLIANCE or GOVERNANCE, "
            f"got {required_mode!r}"
        )
    if mode == "auto" and required_mode != "COMPLIANCE":
        raise ConfigError("mode = auto requires DECISION_ARCHIVE_REQUIRED_MODE = COMPLIANCE")

    names = tuple(
        part.strip() for part in _require("TRIGGER_ALARM_NAMES").split(",") if part.strip()
    )
    if not names:
        raise ConfigError("TRIGGER_ALARM_NAMES must list at least one alarm")
    # Optional: main.tf sets AGGREGATE_NAMES only when aggregate_names is not
    # empty, so an absent variable means first generation (no per-aggregate
    # alarms and no per-aggregate utilization series).
    aggregates = tuple(
        part.strip()
        for part in os.environ.get("AGGREGATE_NAMES", "").split(",")
        if part.strip()
    )

    return Config(
        file_system_id=_require("FILE_SYSTEM_ID"),
        mode=mode,
        max_storage_capacity_gib=int(_require("MAX_STORAGE_CAPACITY_GIB")),
        increase_percent=int(_require("INCREASE_PERCENT")),
        trigger_alarm_names=names,
        aggregate_names=aggregates,
        lock_table_name=_require("LOCK_TABLE_NAME"),
        notify_topic_arn=_require("NOTIFY_TOPIC_ARN"),
        decision_log_group=_require("DECISION_LOG_GROUP"),
        decision_archive_bucket=_require("DECISION_ARCHIVE_BUCKET"),
        decision_archive_prefix=_require("DECISION_ARCHIVE_PREFIX"),
        decision_archive_required_mode=required_mode,
        decision_archive_min_retention_days=int(_require("DECISION_ARCHIVE_MIN_RETENTION_DAYS")),
        indeterminate_reconcile_hours=int(_require("INDETERMINATE_RECONCILE_HOURS")),
        config_fingerprint=_require("CONFIG_FINGERPRINT"),
    )


def shape_max_gib(deployment_type: str, ha_pairs: int) -> int:
    """Return the per-file-system SSD maximum for a deployment shape.

    Args:
        deployment_type: One of ``SINGLE_AZ_1``, ``MULTI_AZ_1``, ``MULTI_AZ_2``
            or ``SINGLE_AZ_2``.
        ha_pairs: HA-pair count (used only for ``SINGLE_AZ_2``).

    Returns:
        The maximum SSD capacity in GiB.

    Raises:
        ConfigError: For a deployment type this design does not list. Failing
            here matches the HCL map lookup, which fails the plan rather than
            falling back to a default maximum.
    """
    if deployment_type in ("SINGLE_AZ_1", "MULTI_AZ_1"):
        return 196608
    if deployment_type == "MULTI_AZ_2":
        return 524288
    if deployment_type == "SINGLE_AZ_2":
        return min(524288 * ha_pairs, 1048576)
    raise ConfigError(
        f"unknown deployment_type {deployment_type!r}; no SSD maximum is defined for it"
    )


def max_ssd_iops(deployment_type: str, ha_pairs: int, region: str) -> int:
    """Return the maximum SSD IOPS for a deployment shape and Region.

    Args:
        deployment_type: The file system's deployment type.
        ha_pairs: HA-pair count (used for second-generation Single-AZ).
        region: AWS Region name.

    Returns:
        The maximum user-provisioned SSD IOPS.
    """
    if deployment_type in FIRST_GEN_DEPLOYMENT_TYPES:
        return (
            FIRST_GEN_HIGH_IOPS
            if region in FIRST_GEN_HIGH_IOPS_REGIONS
            else FIRST_GEN_DEFAULT_IOPS
        )
    if deployment_type == "SINGLE_AZ_2":
        return SECOND_GEN_IOPS_PER_UNIT * ha_pairs
    # MULTI_AZ_2 and any other second-generation shape: 200,000 total.
    return SECOND_GEN_IOPS_PER_UNIT
