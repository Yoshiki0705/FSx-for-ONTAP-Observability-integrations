"""Terraform entry point: one guarded SSD auto-increase evaluation.

Deployed by terraform/fsxn-ssd-auto-increase with handler
``ssd_auto_increase_handler.lambda_handler``. The behaviour is specified in
docs/en/capacity-automation-t4-design.md. This module wires the guards
(guards.py), the single-flight lock (lock.py), the decision archive
(archive.py), the reporter (report.py), the utilization report value
(utilization.py) and the failure classification (classify.py) into one
evaluation that calls ``fsx:UpdateFileSystem`` only when
every guard passes and only from the ``calling`` lock state.

NO secret or credential is read from the environment; only resource names and
ARNs are. The function calls AWS APIs only (fsx, cloudwatch, sns, s3, dynamodb,
logs) and runs outside any VPC.

Every archive event is also written as one JSON line to the decision log group
through :meth:`Evaluation._emit`, so the operational history and the audit
record carry the same event bodies. Post-call archive events that fail to
write are recorded in the lock item's ``pending_events`` and flushed by the
next invocation before it releases the item; their retention proofs are
carried into the report rather than dropped.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from archive import (
    ACCEPTED,
    AMBIGUOUS,
    CAPACITY_AVAILABLE,
    DECISION,
    RECONCILED,
    REJECTED,
    TERMINAL,
    DecisionArchive,
    RetentionCheck,
)
from classify import FailureClass, classify_error
from config import Config, load_config, shape_max_gib
from guards import (
    TERMINAL_STATUSES,
    administrative_action_in_progress,
    alarm_in_alarm,
    compute_iops,
    compute_target,
    cooldown_active,
)
from lock import (
    BLOCKED,
    CALLING,
    EVALUATING,
    INDETERMINATE,
    MANUAL_DISPOSITION_REQUIRED,
    OPTIMIZING,
    SUBMITTED,
    LockContention,
    LockItem,
    LockStore,
    lease_expired,
    utcnow,
)
from report import Reporter
from utilization import read_utilization

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Lease padding beyond the function timeout, so a crashed owner's lease outlives
# any in-flight run before a take-over.
LEASE_PADDING_SECONDS = 60
# Must match `timeout` on aws_lambda_function.evaluator in
# terraform/fsxn-ssd-auto-increase/main.tf. The timeout is not a module variable,
# so the two cannot drift through configuration; this value only pads the lease
# (expires_at = now + FUNCTION_TIMEOUT_SECONDS + LEASE_PADDING_SECONDS), and the
# lease-takeover risk of a mismatch is bounded by reserved_concurrent_executions
# = 1 and the DynamoDB conditional-write lock. If the HCL timeout changes, change
# this constant in the same commit.
FUNCTION_TIMEOUT_SECONDS = 300


class Clients:
    """The six boto3 clients, created once per execution environment."""

    def __init__(self) -> None:
        self.fsx = boto3.client("fsx")
        self.cloudwatch = boto3.client("cloudwatch")
        self.sns = boto3.client("sns")
        self.s3 = boto3.client("s3")
        self.dynamodb = boto3.resource("dynamodb")
        self.logs = boto3.client("logs")


_CLIENTS: Clients | None = None


def _clients() -> Clients:
    global _CLIENTS
    if _CLIENTS is None:
        _CLIENTS = Clients()
    return _CLIENTS


def _describe_file_system(clients: Clients, file_system_id: str) -> dict[str, Any]:
    response = clients.fsx.describe_file_systems(FileSystemIds=[file_system_id])
    file_systems = response.get("FileSystems", [])
    if not file_systems:
        raise RuntimeError(f"file system {file_system_id} not found")
    return file_systems[0]


def _ssd_capacity(file_system: dict[str, Any]) -> int:
    return int(file_system["StorageCapacity"])


def _iops_settings(file_system: dict[str, Any]) -> tuple[str, int]:
    config = file_system.get("OntapConfiguration", {}).get("DiskIopsConfiguration", {})
    return config.get("Mode", "AUTOMATIC"), int(config.get("Iops", 0))


def _administrative_actions_snapshot(
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten AdministrativeActions to the fields the report and archive carry."""
    snapshot: list[dict[str, Any]] = []
    for action in actions:
        entry: dict[str, Any] = {
            "type": action.get("AdministrativeActionType"),
            "status": action.get("Status"),
        }
        request_time = action.get("RequestTime")
        if isinstance(request_time, datetime):
            entry["request_time"] = request_time.isoformat()
        target = action.get("TargetFileSystemValues", {}).get("StorageCapacity")
        if target is not None:
            entry["target_storage_capacity"] = target
        progress = action.get("ProgressPercent")
        if progress is not None:
            entry["progress_percent"] = progress
        snapshot.append(entry)
    return snapshot


class Evaluation:
    """One evaluation: holds the context passed between the branch methods."""

    def __init__(self, config: Config, clients: Clients) -> None:
        self.config = config
        self.clients = clients
        self.correlation_id = str(uuid.uuid4())
        self.now = utcnow()
        self.lock = LockStore(
            clients.dynamodb.Table(config.lock_table_name), config.file_system_id
        )
        self.archive = DecisionArchive(
            clients.s3,
            config.decision_archive_bucket,
            config.decision_archive_prefix,
            config.file_system_id,
            config.decision_archive_required_mode,
            config.decision_archive_min_retention_days,
        )
        self.reporter = Reporter(clients.sns, config.notify_topic_arn, config.file_system_id)
        self._sequence = 0
        # The bucket's default-retention proof is read once per evaluation,
        # before the first archive event, and cached: the design's retention
        # contract requires GetBucketObjectLockConfiguration before the first
        # event of every evaluation, including the no-call and existing-state
        # paths, not only the mode handlers.
        self._bucket_gap_cached: str | None = None
        self._bucket_gap_read = False

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    # -- small helpers ---------------------------------------------------

    def _lease_expires_at(self) -> int:
        return int(self.now.timestamp()) + FUNCTION_TIMEOUT_SECONDS + LEASE_PADDING_SECONDS

    def _decision_log(
        self,
        correlation_id: str,
        event: str,
        body: dict[str, Any],
        *,
        sequence: int | None = None,
        archive_result: str | None = None,
    ) -> None:
        """Write one JSON line to the decision log group (operational history).

        A failure here never blocks the evaluation: the decision log is
        searchable history, not the audit record. The archive is the record.

        Each line carries the archive ``sequence`` and an ``archive_result``
        marker (``written``, ``replayed``, ``write_failed`` or ``unproven``) so
        that a failed write and its later replay produce distinct, tie-able
        attempt records for one archive key rather than indistinguishable
        duplicate lines: the (correlation_id, sequence, event) triple is the
        archive key, and ``archive_result`` tells the attempts apart.
        """
        entry: dict[str, Any] = {
            "correlation_id": correlation_id,
            "file_system_id": self.config.file_system_id,
            "event": event,
            **body,
        }
        if sequence is not None:
            entry["sequence"] = sequence
        if archive_result is not None:
            entry["archive_result"] = archive_result
        line = json.dumps(entry, default=str, sort_keys=True)
        stream = f"{correlation_id}"
        try:
            try:
                self.clients.logs.create_log_stream(
                    logGroupName=self.config.decision_log_group,
                    logStreamName=stream,
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                    raise
            self.clients.logs.put_log_events(
                logGroupName=self.config.decision_log_group,
                logStreamName=stream,
                logEvents=[{"timestamp": int(time.time() * 1000), "message": line}],
            )
        except (ClientError, BotoCoreError):
            # The decision log is searchable history, not the audit record, so a
            # failure here never blocks evaluation. A transport failure
            # (BotoCoreError) is swallowed for the same reason a service error
            # is: the archive, not the log, is the record.
            logger.exception("decision log write failed for %s", event)

    def _emit(
        self,
        event: str,
        body: dict[str, Any],
        *,
        correlation_id: str | None = None,
        sequence: int | None = None,
    ) -> RetentionCheck | None:
        """Write one event to both the decision log and the Object Lock archive.

        Args:
            event: The event name (one of the archive event constants).
            body: The complete event body (same fields in both destinations).
            correlation_id: The evaluation that owns the event; defaults to this
                invocation's correlation ID. Existing-state handlers pass the
                lock item's owner so the follow-up events keep one sequence.
            sequence: The archive sequence number; defaults to this
                invocation's monotonic counter.

        Returns:
            The retention check for the archive write, or ``None`` when the
            archive ``put_object`` itself failed (treated as "write failed").
        """
        cid = correlation_id or self.correlation_id
        seq = sequence if sequence is not None else self._next_sequence()
        # Archive first, then log exactly one line describing this attempt's
        # outcome. Logging after the write (rather than before) means a failed
        # write and its later replay each log one line keyed on the same
        # (correlation_id, sequence, event) with a different archive_result, so
        # the two attempts are distinguishable and tie-able to one archive key
        # instead of producing indistinguishable duplicate lines.
        try:
            check = self.archive.write_event(cid, seq, event, body)
        except (ClientError, BotoCoreError):
            # A transport failure (BotoCoreError) during PutObject is a write
            # failure, the same as a service error: the object may not exist, so
            # the event is treated as not written (returns None) and the caller
            # records it as pending for a post-call event or fails the intent.
            logger.exception("archive write failed for %s", event)
            self._decision_log(cid, event, body, sequence=seq, archive_result="write_failed")
            return None
        if check.already_written:
            result = "replayed"
        elif check.proven:
            result = "written"
        else:
            result = "unproven"
        self._decision_log(cid, event, body, sequence=seq, archive_result=result)
        return check

    def _report(self, subject: str, body: dict[str, Any]) -> None:
        """Report under this invocation's own correlation ID (fresh evaluations)."""
        self._report_as(self.correlation_id, subject, body)

    def _report_as(self, correlation_id: str, subject: str, body: dict[str, Any]) -> None:
        """Report under a specific correlation ID.

        Follow-up handlers pass ``item.owner`` (the original evaluation ID the
        archive sequence and lock item use), so capacity/terminal/resent/manual
        reports carry the same ID as the archive and lock item they describe,
        not the follower invocation's ID.
        """
        self.reporter.publish(subject, {"correlation_id": correlation_id, **body})

    def _try_report(self, subject: str, body: dict[str, Any]) -> bool:
        """Report and return whether the publish succeeded.

        Used where the required single report's delivery state must be persisted
        (the ``blocked`` latch), so a failed initial report is retried by a later
        invocation instead of being silently dropped.
        """
        try:
            self._report(subject, body)
            return True
        except (ClientError, BotoCoreError):
            # A transport failure (BotoCoreError) is as much a non-delivery as a
            # service error, so the delivery state is persisted for retry rather
            # than the report being silently dropped.
            logger.exception("report publish failed; delivery state persisted for retry")
            return False

    @staticmethod
    def _gap(check: RetentionCheck | None) -> str | None:
        """Translate a retention check into an archive gap for the report.

        A missing check means the archive write failed; an unproven check
        carries its detail. A proven check means no gap.
        """
        if check is None:
            return "write failed"
        if check.proven:
            return None
        return check.detail or "retention unproven"

    # -- entry -----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Run one evaluation and return a machine-readable result."""
        existing = self.lock.read()
        if existing is not None and existing.state != EVALUATING:
            return self._handle_existing_state(existing)
        if (
            existing is not None
            and existing.state == EVALUATING
            and not lease_expired(existing, self.now)
        ):
            logger.info("evaluation already running (owner %s)", existing.owner)
            return {"decision": "evaluation_already_running"}

        superseded = (
            existing.owner
            if existing is not None and existing.state == EVALUATING
            else None
        )
        try:
            self.lock.acquire(self.correlation_id, self._lease_expires_at(), self.now)
        except LockContention:
            logger.info("lost the race to acquire the lock")
            return {"decision": "evaluation_already_running"}
        return self._evaluate(superseded)

    # -- the fresh evaluation --------------------------------------------

    def _evaluate(self, superseded: str | None) -> dict[str, Any]:
        config = self.config

        alarms = self.clients.cloudwatch.describe_alarms(
            AlarmNames=list(config.trigger_alarm_names)
        )
        # The report value, read with GetMetricData from the series the trigger
        # alarms read. Read before the alarm branch so the alarm_not_in_alarm
        # decision carries it too (the decision log lists utilization among the
        # inputs of every event). Never None: a missing value is classified.
        utilization = self._read_utilization()
        if not alarm_in_alarm(alarms):
            return self._decide_and_release(
                "alarm_not_in_alarm",
                {
                    "superseded": superseded,
                    "alarm_states": self._alarm_states(alarms),
                    "utilization": utilization,
                },
            )

        file_system = _describe_file_system(self.clients, config.file_system_id)
        current_gib = _ssd_capacity(file_system)
        deployment_type = file_system["OntapConfiguration"].get("DeploymentType", "")
        ha_pairs = int(file_system["OntapConfiguration"].get("HAPairs", 1))
        actions = file_system.get("AdministrativeActions", [])
        actions_snapshot = _administrative_actions_snapshot(actions)

        context = {
            "superseded": superseded,
            "alarm_states": self._alarm_states(alarms),
            "utilization": utilization,
            "current_gib": current_gib,
            "ceiling": config.max_storage_capacity_gib,
            "mode": config.mode,
            "administrative_actions": actions_snapshot,
        }

        # Run-time ceiling re-check against the shape maximum.
        maximum = shape_max_gib(deployment_type, ha_pairs)
        if config.max_storage_capacity_gib > maximum:
            return self._latch_blocked(
                "ceiling_exceeds_service_maximum",
                {"ceiling": config.max_storage_capacity_gib, "shape_maximum": maximum, **context},
            )

        # Administrative actions in progress.
        blocking = administrative_action_in_progress(actions)
        if blocking is not None:
            return self._decide_and_release(
                "administrative_action_in_progress",
                {
                    "status": blocking.get("Status"),
                    "type": blocking.get("AdministrativeActionType"),
                    **context,
                },
            )

        # 6-hour cooldown.
        eligible_at = cooldown_active(actions, self.now)
        if eligible_at is not None:
            return self._decide_and_release(
                "cooldown_active", {"next_eligible": eligible_at.isoformat(), **context}
            )

        # Target.
        target = compute_target(
            current_gib, config.increase_percent, config.max_storage_capacity_gib
        )
        if not target.should_call:
            return self._decide_and_release(
                target.reason or "no_increase", context
            )

        # IOPS mode.
        iops_mode, current_iops = _iops_settings(file_system)
        region = self.clients.fsx.meta.region_name
        iops = compute_iops(
            iops_mode, current_iops, target.target_gib, deployment_type, ha_pairs, region
        )
        if iops.exceeds_maximum:
            return self._latch_blocked(
                "iops_exceeds_maximum",
                {"requested_iops": iops.iops, "target": target.target_gib, **context},
            )

        decision_body = {
            "decision": "increase",
            "mode": config.mode,
            "current_gib": current_gib,
            "target_gib": target.target_gib,
            "ceiling": config.max_storage_capacity_gib,
            "iops_mode": iops_mode,
            "iops": iops.iops if iops.include_iops else None,
            "superseded": superseded,
            "correlation_id": self.correlation_id,
            "alarm_states": context["alarm_states"],
            "utilization": utilization,
            "cooldown_state": "clear",
            "administrative_actions": actions_snapshot,
            "lock_state": CALLING if config.mode == "auto" else "none",
        }

        if config.mode == "notify_only":
            return self._notify_only(decision_body)
        if config.mode == "approve":
            return self._approve(decision_body, target.target_gib, iops)
        return self._auto(decision_body, target.target_gib, iops)

    def _alarm_states(self, alarms: dict[str, Any]) -> dict[str, str]:
        return {
            a.get("AlarmName", ""): a.get("StateValue", "")
            for a in alarms.get("MetricAlarms", [])
        }

    def _read_utilization(self) -> dict[str, Any]:
        """Read the report's utilization value with ``cloudwatch:GetMetricData``.

        Queries ``StorageCapacityUtilization`` with ``FileSystemId`` +
        ``StorageTier=SSD`` + ``DataType=All``, plus one series per configured
        Aggregate (utilization.py). The value is carried into the report, the
        decision log and the archive; it is not a guard, because the alarm
        state decides. A missing or failed read is classified (``incomplete``
        or ``query_failed`` with a ``detail``) and logged, never returned as
        None, and it does not stop the evaluation.
        """
        reading = read_utilization(
            self.clients.cloudwatch,
            self.config.file_system_id,
            self.config.aggregate_names,
            self.now,
        )
        if reading["status"] != "ok":
            logger.warning(
                "utilization %s for %s: %s",
                reading["status"],
                self.config.file_system_id,
                reading.get("detail"),
            )
        return reading

    # -- release branches ------------------------------------------------

    def _decide_and_release(self, reason: str, extra: dict[str, Any]) -> dict[str, Any]:
        """Archive a non-call decision (fail-open), report it, and release the lock.

        A missing retention check means the archive object was not written; the
        gap is named in both the log line and the report so the missing object
        is visible rather than silent.
        """
        # The retention contract requires the bucket-default proof before the
        # first event of every evaluation, including these no-call decisions.
        # They make no call, so the gap is reported, not fatal.
        bucket_gap = self._bucket_gap()
        body = {"decision": reason, "mode": self.config.mode, **extra}
        check = self._emit(DECISION, body)
        gap = self._merge_gaps(bucket_gap, self._gap(check))
        self._report(
            f"T4 {reason} for {self.config.file_system_id}",
            {**body, "archive_gap": gap, "bucket_default_gap": bucket_gap},
        )
        self._safe_release(EVALUATING)
        return {"decision": reason, "archive_gap": gap}

    def _latch_blocked(self, reason: str, extra: dict[str, Any]) -> dict[str, Any]:
        """Archive a deterministic refusal, report once, and latch blocked.

        The retention check of the refusal archive is carried into the report
        so a missing object is disclosed, not hidden.
        """
        # Prove the bucket default retention before this first event too (the
        # deterministic-refusal decision). The refusal makes no call, so the gap
        # is disclosed in the report, not fatal.
        bucket_gap = self._bucket_gap()
        body = {"decision": reason, "mode": self.config.mode, **extra}
        check = self._emit(DECISION, body)
        gap = self._merge_gaps(bucket_gap, self._gap(check))
        # Persist the delivery state of the one required report: a failed publish
        # leaves report_sent False so a later blocked invocation retries it,
        # rather than the latch going permanently silent.
        report_sent = self._try_report(
            f"T4 blocked ({reason}) for {self.config.file_system_id}",
            {**body, "archive_gap": gap, "bucket_default_gap": bucket_gap},
        )
        self.lock.transition(
            self.correlation_id,
            EVALUATING,
            {
                "state": BLOCKED,
                "reason": reason,
                "config_fingerprint": self.config.config_fingerprint,
                "sequence": self._sequence,
                "report_sent": report_sent,
                "expires_at": self._lease_expires_at(),
            },
            self.now,
        )
        return {"decision": reason, "state": BLOCKED, "archive_gap": gap}

    def _bucket_gap(self) -> str | None:
        """Return the bucket default-retention gap, reading it once per evaluation.

        The design's retention contract requires
        ``GetBucketObjectLockConfiguration`` before the first event of every
        evaluation. This caches the proof so the no-call, mode-handler and
        existing-state paths all share one read, and returns a gap string when
        the bucket default retention is unproven (``None`` when proven). The
        fail-open modes report the gap; the ``auto`` path fails closed on it.
        """
        if not self._bucket_gap_read:
            bucket_check = self.archive.check_bucket_retention()
            self._bucket_gap_cached = (
                None
                if bucket_check.proven
                else (bucket_check.detail or "bucket default retention unproven")
            )
            self._bucket_gap_read = True
        return self._bucket_gap_cached

    def _bucket_default_gap(self, item: LockItem) -> str | None:
        """Prove the bucket default retention before an existing-state event.

        Existing-state handlers (reconcile, operator disposition, blocked-latch
        clear, terminal) write post-call lifecycle events; the retention
        contract requires the bucket-default proof before the first of them too.
        These paths never call ``UpdateFileSystem``, so a gap is reported
        (fail-open, under the item's owner) rather than fatal. Returns the gap
        string, or ``None`` when proven.
        """
        gap = self._bucket_gap()
        if gap is not None:
            self._report_as(
                item.owner,
                f"T4 archive bucket default retention unproven for {self.config.file_system_id}",
                {"bucket_default_gap": gap},
            )
        return gap

    @staticmethod
    def _merge_gaps(*gaps: str | None) -> str | None:
        """Combine the bucket-default and object gaps into one report value."""
        present = [g for g in gaps if g]
        return "; ".join(present) if present else None

    def _notify_only(self, decision_body: dict[str, Any]) -> dict[str, Any]:
        bucket_gap = self._bucket_gap()
        check = self._emit(DECISION, decision_body)
        gap = self._merge_gaps(bucket_gap, self._gap(check))
        self._report(
            f"T4 would increase {self.config.file_system_id}",
            {**decision_body, "archive_gap": gap, "bucket_default_gap": bucket_gap},
        )
        self._safe_release(EVALUATING)
        return {
            "decision": "notify_only",
            "target_gib": decision_body["target_gib"],
            "archive_gap": gap,
        }

    def _approve(
        self, decision_body: dict[str, Any], target_gib: int, iops: Any
    ) -> dict[str, Any]:
        bucket_gap = self._bucket_gap()
        check = self._emit(DECISION, decision_body)
        gap = self._merge_gaps(bucket_gap, self._gap(check))
        command = self.reporter.approve_command(
            target_gib, iops.iops if iops.include_iops else None, self.correlation_id
        )
        self._report(
            f"T4 approval needed for {self.config.file_system_id}",
            {**decision_body, "command": command, "archive_gap": gap,
             "bucket_default_gap": bucket_gap},
        )
        self._safe_release(EVALUATING)
        return {"decision": "approve", "target_gib": target_gib, "command": command}

    def _auto(
        self, decision_body: dict[str, Any], target_gib: int, iops: Any
    ) -> dict[str, Any]:
        config = self.config
        # Fail closed: prove the bucket's retention, then write the intent and
        # prove its retention, before the call. The bucket proof is the same
        # once-per-evaluation read the no-call paths use; a gap here fails the
        # call closed. The intent is one archive event, written through _emit so
        # the decision log carries the same body.
        bucket_gap = self._bucket_gap()
        if bucket_gap is not None:
            return self._archive_retention_unproven(bucket_gap, decision_body)
        intent_seq = self._next_sequence()
        # Archive the intent first, then log one line with the outcome (same
        # order as _emit), so the intent is never logged as present when its
        # object was not written.
        try:
            intent_check = self.archive.write_event(
                self.correlation_id, intent_seq, DECISION, decision_body
            )
        except (ClientError, BotoCoreError):
            # A transport failure writing the intent is pre-call archive
            # uncertainty: in auto this fails closed (no call), the same as a
            # service PutObject error, so the request never goes out without a
            # durable intent.
            logger.exception("intent archive write failed in auto")
            self._decision_log(
                self.correlation_id, DECISION, decision_body,
                sequence=intent_seq, archive_result="write_failed",
            )
            return self._archive_retention_unproven("intent write failed", decision_body)
        self._decision_log(
            self.correlation_id, DECISION, decision_body,
            sequence=intent_seq,
            archive_result="written" if intent_check.proven else "unproven",
        )
        if not intent_check.proven:
            return self._archive_retention_unproven(intent_check.detail, decision_body)

        # Re-read AdministrativeActions immediately before the call, holding the
        # lock, and rerun BOTH the administrative-action and the cooldown guard
        # on this second snapshot. A terminal action that became visible between
        # the first read and now can start the shared six-hour cooldown without
        # being "in progress", so checking only the active-action guard would let
        # the request go out during a newly started cooldown. When either guard
        # now blocks, the increase intent already archived above is superseded by
        # an explicit no-call decision event, so the audit sequence never ends on
        # an intent that produced no request.
        file_system = _describe_file_system(self.clients, config.file_system_id)
        actions = file_system.get("AdministrativeActions", [])
        blocking = administrative_action_in_progress(actions)
        if blocking is not None:
            return self._supersede_intent(
                "administrative_action_in_progress",
                {
                    "status": blocking.get("Status"),
                    "type": blocking.get("AdministrativeActionType"),
                    "superseded_decision": "increase",
                    **decision_body,
                },
            )
        eligible_at = cooldown_active(actions, self.now)
        if eligible_at is not None:
            return self._supersede_intent(
                "cooldown_active",
                {
                    "next_eligible": eligible_at.isoformat(),
                    "superseded_decision": "increase",
                    **decision_body,
                },
            )

        # Publish the complete before-call report after the durable intent and
        # before the calling transition / API request. The snapshot carries the
        # utilization, action, cooldown and lock context the design requires.
        self._report(
            f"T4 will increase {config.file_system_id}",
            {**decision_body, "lock_state": CALLING, "pre_call": True},
        )

        # Move to calling, recording the request facts, the token and the
        # sequence so the submitted/optimizing/terminal lifecycle can match the
        # administrative action and keep one archive sequence.
        self.lock.transition(
            self.correlation_id,
            EVALUATING,
            {
                "state": CALLING,
                "target_gib": target_gib,
                "iops": iops.iops if iops.include_iops else None,
                "client_request_token": self.correlation_id,
                "request_time": int(self.now.timestamp()),
                "sequence": self._sequence,
                "report_sent": False,
                # Persist the request facts the design requires every later
                # state-change report to carry (current GiB, target GiB, mode,
                # reason, cooldown state, AdministrativeActions status), so a
                # follower invocation that sends the submitted/capacity/terminal
                # report can reconstruct the full contract rather than emitting
                # only event-specific fields.
                "report_context": self._report_context(decision_body),
                "expires_at": self._lease_expires_at(),
            },
            self.now,
        )
        return self._call_update(target_gib, iops)

    @staticmethod
    def _report_context(decision_body: dict[str, Any]) -> dict[str, Any]:
        """Build the persisted report context from the decision body.

        These fields are stored on the lock item at the ``calling`` transition
        and carried forward through the lifecycle, so every later state-change
        report (submitted, capacity_available, reconciled, terminal) carries the
        design's current/target/mode/reason/cooldown/action fields even though
        the follower invocation did not compute them.
        """
        return {
            "current_gib": decision_body.get("current_gib"),
            "target_gib": decision_body.get("target_gib"),
            "ceiling": decision_body.get("ceiling"),
            "mode": decision_body.get("mode"),
            "reason": "increase",
            "cooldown_state": decision_body.get("cooldown_state"),
            "iops_mode": decision_body.get("iops_mode"),
            "iops": decision_body.get("iops"),
            "utilization": decision_body.get("utilization"),
            "administrative_actions": decision_body.get("administrative_actions"),
        }

    def _report_body(
        self, item: LockItem, event_fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge the persisted report context with event-specific fields.

        The persisted context carries the design-required current/target/mode/
        reason/cooldown/action fields; ``event_fields`` adds the per-event data
        (status, request id, archive gap, resulting state). Event fields win on
        a key collision so an event can override, for example, ``reason``.
        """
        context = dict(item.attributes.get("report_context", {}))
        return {**context, **event_fields}

    def _pending_report(
        self, item: LockItem, subject: str, event_fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a stored report directive for a pending follow-up.

        The body is published through ``_try_report`` on a later invocation, so
        it bakes in ``correlation_id = item.owner`` (the original evaluation ID
        the archive and lock item use); otherwise the follower invocation's own
        correlation ID would label the replayed report.
        """
        return {
            "subject": subject,
            "body": self._report_body(item, {"correlation_id": item.owner, **event_fields}),
        }

    def _supersede_intent(self, reason: str, extra: dict[str, Any]) -> dict[str, Any]:
        """Archive a no-call decision that supersedes an already-written intent.

        Used when the second pre-call snapshot (held under the lock) shows a
        guard now blocks after the ``increase`` intent was archived. The reason
        is placed last so the superseding decision value is not overwritten by
        the carried-forward ``decision=increase`` in ``extra``. The lock is then
        released without a request.
        """
        body = {**extra, "decision": reason, "mode": self.config.mode}
        check = self._emit(DECISION, body)
        gap = self._gap(check)
        self._report(
            f"T4 {reason} (superseding increase intent) for {self.config.file_system_id}",
            {**body, "archive_gap": gap},
        )
        self._safe_release(EVALUATING)
        return {"decision": reason, "superseded_intent": True, "archive_gap": gap}

    def _call_update(self, target_gib: int, iops: Any) -> dict[str, Any]:
        config = self.config
        kwargs: dict[str, Any] = {
            "FileSystemId": config.file_system_id,
            "StorageCapacity": target_gib,
            "ClientRequestToken": self.correlation_id,
        }
        if iops.include_iops:
            kwargs["OntapConfiguration"] = {
                "DiskIopsConfiguration": {"Mode": "USER_PROVISIONED", "Iops": iops.iops}
            }
        call_time = utcnow()
        try:
            response = self.clients.fsx.update_file_system(**kwargs)
        except ClientError as exc:
            return self._handle_call_error(exc)
        except Exception:  # noqa: BLE001 -- a client-side timeout or no response is ambiguous
            logger.exception("UpdateFileSystem raised a non-ClientError; treating as ambiguous")
            return self._handle_ambiguous(None)
        return self._handle_call_success(response, call_time)

    def _current_calling_attributes(self) -> dict[str, Any]:
        """Read the request facts recorded on the calling item for a merge.

        A full put_item replace must carry these forward, or _match_action can
        never match the administrative action the request created.
        """
        item = self.lock.read()
        if item is None:
            return {}
        return {
            k: v
            for k, v in item.attributes.items()
            if k not in ("file_system_id", "owner", "updated_at", "state")
        }

    def _handle_call_success(
        self, response: dict[str, Any], call_time: datetime
    ) -> dict[str, Any]:
        request_id = response.get("ResponseMetadata", {}).get("RequestId")
        returned_actions = _administrative_actions_snapshot(
            response.get("FileSystem", {}).get("AdministrativeActions", [])
        )
        body = {
            "decision": "accepted",
            "request_id": request_id,
            "response_time": call_time.isoformat(),
            "returned_administrative_actions": returned_actions,
        }
        # Carry the calling item's request facts forward: a full put_item
        # replace would otherwise drop target_gib/request_time/token/sequence,
        # wedging the submitted->optimizing->terminal lifecycle.
        carried = self._current_calling_attributes()
        pending, sequence = self._write_post_call(ACCEPTED, body, carried)
        carried = {**carried, "sequence": sequence}
        # report_sent is stored False first, so an SNS failure here is recovered
        # by the next invocation (which reports and only then sets it True). If
        # this lock-table write fails as well as the archive write above (the
        # dual-write-failure path), the item stays calling: its lease expires
        # and reconciliation takes over from the still-current state, as the
        # design specifies, rather than the invocation raising.
        try:
            self.lock.transition(
                self.correlation_id,
                CALLING,
                {
                    **carried,
                    "state": SUBMITTED,
                    "request_id": request_id,
                    "report_sent": False,
                    "pending_events": pending,
                    "expires_at": self._lease_expires_at(),
                },
                self.now,
            )
        except (LockContention, ClientError, BotoCoreError):
            logger.exception(
                "lock-table write failed after a successful call; item stays calling "
                "for reconciliation"
            )
            return {"decision": "submitted", "request_id": request_id, "lock_write_failed": True}
        published = self._publish_submitted_report(request_id, carried)
        if published:
            self._mark_report_sent(SUBMITTED, carried, request_id, pending)
        return {"decision": "submitted", "request_id": request_id}

    def _publish_submitted_report(
        self, request_id: str | None, carried: dict[str, Any]
    ) -> bool:
        try:
            # Merge the persisted report context so the submitted report carries
            # the design-required current/target/mode/reason/cooldown/action
            # fields, not only the decision and request id.
            context = dict(carried.get("report_context", {}))
            self._report(
                f"T4 submitted increase for {self.config.file_system_id}",
                {
                    **context,
                    "decision": "accepted",
                    "reason": "submitted",
                    "request_id": request_id,
                    "lock_state": SUBMITTED,
                },
            )
            return True
        except (ClientError, BotoCoreError):
            logger.exception("submitted report publish failed; next invocation retries")
            return False

    def _mark_report_sent(
        self,
        state: str,
        carried: dict[str, Any],
        request_id: str | None,
        pending: list[dict[str, Any]],
    ) -> None:
        try:
            self.lock.transition(
                self.correlation_id,
                state,
                {
                    **carried,
                    "state": state,
                    "request_id": request_id,
                    "report_sent": True,
                    "pending_events": pending,
                    "expires_at": self._lease_expires_at(),
                },
                self.now,
            )
        except LockContention:
            logger.warning("could not mark report_sent; next invocation reconciles")

    def _handle_call_error(self, exc: ClientError) -> dict[str, Any]:
        code = exc.response["Error"]["Code"]
        failure = classify_error(code)
        carried = self._current_calling_attributes()
        if failure is FailureClass.DETERMINISTIC_REJECTION:
            pending, sequence = self._write_post_call(
                REJECTED,
                {"decision": "rejected", "error_code": code, "error_class": failure.value},
                carried,
            )
            # Persist the delivery state of the required blocked report so a
            # failed publish is retried from _on_blocked rather than lost.
            report_sent = self._try_report(
                f"T4 blocked (rejected {code}) for {self.config.file_system_id}",
                {
                    **dict(carried.get("report_context", {})),
                    "reason": "deterministic_rejection",
                    "lock_state": BLOCKED,
                    "error_code": code,
                },
            )
            self.lock.transition(
                self.correlation_id,
                CALLING,
                {
                    **carried,
                    "state": BLOCKED,
                    "reason": "deterministic_rejection",
                    "error_code": code,
                    "config_fingerprint": self.config.config_fingerprint,
                    "sequence": sequence,
                    "report_sent": report_sent,
                    "pending_events": pending,
                    "expires_at": self._lease_expires_at(),
                },
                self.now,
            )
            return {"decision": "rejected", "state": BLOCKED, "error_code": code}
        if failure is FailureClass.RETRYABLE_REJECTION:
            pending, sequence = self._write_post_call(
                REJECTED,
                {"decision": "rejected", "error_code": code, "error_class": failure.value},
                carried,
            )
            # The design re-evaluates on the next tick; report the retryable
            # rejection as a state change so an operator sees the deferral. A
            # failed publish here is non-fatal: the item is released/kept and
            # the next scheduled run re-evaluates regardless.
            self._try_report(
                f"T4 retryable rejection ({code}) for {self.config.file_system_id}",
                {
                    **dict(carried.get("report_context", {})),
                    "reason": "retryable_rejection",
                    "error_code": code,
                    "error_class": failure.value,
                },
            )
            if pending:
                # A pending event must not be lost; keep the item (calling) so
                # the next invocation flushes it, then apply the release the
                # design records for a retryable rejection. release_after_flush
                # carries that intent across invocations: without it, the item's
                # lease would expire and it would be reclassified as ambiguous
                # (indeterminate) even though the response was a known
                # retryable rejection that the design releases.
                self.lock.transition(
                    self.correlation_id,
                    CALLING,
                    {**carried, "state": CALLING, "sequence": sequence,
                     "pending_events": pending,
                     "release_after_flush": "retryable_rejection",
                     "release_error_code": code,
                     "expires_at": self._lease_expires_at()},
                    self.now,
                )
            else:
                self._safe_release(CALLING)
            return {"decision": "retryable_rejection", "error_code": code}
        return self._handle_ambiguous(code)

    def _handle_ambiguous(self, code: str | None) -> dict[str, Any]:
        carried = self._current_calling_attributes()
        pending, sequence = self._write_post_call(
            AMBIGUOUS,
            {
                "decision": "ambiguous",
                "error_code": code,
                "request_facts": {
                    "target_gib": carried.get("target_gib"),
                    "request_time": carried.get("request_time"),
                    "client_request_token": carried.get("client_request_token"),
                },
                "observation_time": self.now.isoformat(),
            },
            carried,
        )
        self.lock.transition(
            self.correlation_id,
            CALLING,
            {
                **carried,
                "state": INDETERMINATE,
                "client_request_token": carried.get("client_request_token", self.correlation_id),
                "request_time": int(carried.get("request_time", int(self.now.timestamp()))),
                "reconcile_until": int(self.now.timestamp())
                + self.config.indeterminate_reconcile_hours * 3600,
                "error_code": code,
                "sequence": sequence,
                "pending_events": pending,
                "expires_at": self._lease_expires_at(),
            },
            self.now,
        )
        self._report(
            f"T4 ambiguous result for {self.config.file_system_id}",
            {
                **dict(carried.get("report_context", {})),
                "reason": "ambiguous",
                "lock_state": INDETERMINATE,
                "error_code": code,
            },
        )
        return {"decision": "ambiguous", "state": INDETERMINATE, "error_code": code}

    def _write_post_call(
        self, event: str, body: dict[str, Any], carried: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int]:
        """Write a post-call archive event; return (pending-events, next-sequence).

        A failed archive write (or a failed retention proof on the written
        object) cannot undo the call, so the body is recorded in
        ``pending_events`` for the next invocation to flush before releasing.
        The returned list is written onto the lock item by the caller, together
        with the state transition, in one put. The caller preserves any pending
        events already present on the calling item. The returned sequence is the
        value the lock item should store so the next event does not collide with
        this one.
        """
        pending = list(carried.get("pending_events", []))
        base = int(carried.get("sequence", self._sequence))
        sequence = base + len(pending) + 1
        check = self._emit(event, body, sequence=sequence)
        if check is None:
            logger.warning("post-call archive write failed for %s; recorded as pending", event)
            pending.append({"event": event, "body": body, "sequence": sequence})
        elif not check.proven:
            logger.warning(
                "post-call archive retention unproven for %s: %s", event, check.detail
            )
            self._report(
                f"T4 post-call archive retention unproven for {self.config.file_system_id}",
                {"event": event, "archive_gap": self._gap(check)},
            )
        return pending, sequence

    def _emit_followup(
        self, item: LockItem, event: str, body: dict[str, Any], sequence: int
    ) -> tuple[bool, RetentionCheck | None]:
        """Write a post-call lifecycle event (reconciled/capacity_available/terminal).

        These events are handled "as for accepted": a failed write (archive
        ``put_object`` failure) cannot undo the call, so the body is persisted
        into the lock item's ``pending_events`` and the item is NOT advanced or
        released. The caller checks the returned ``proven`` flag and only
        advances the lifecycle when it is True; otherwise it calls
        :meth:`_pend_followup` to persist the pending event and stop for this
        invocation. A written-but-retention-unproven object reports a gap (as
        the no-call paths do) but still counts as written, because the object
        exists and the retention gap is a bucket-configuration issue, not a lost
        event.

        Returns:
            ``(proven, check)`` where ``proven`` is False only when the archive
            ``put_object`` itself failed (``check is None``).
        """
        check = self._emit(event, body, correlation_id=item.owner, sequence=sequence)
        if check is None:
            logger.warning(
                "post-call lifecycle archive write failed for %s; pending", event
            )
            return False, None
        if not check.proven:
            self._report(
                f"T4 post-call archive retention unproven for {self.config.file_system_id}",
                {"event": event, "archive_gap": self._gap(check)},
            )
        return True, check

    def _pend_followup(
        self,
        item: LockItem,
        event: str,
        body: dict[str, Any],
        sequence: int,
        post: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a failed follow-up event into pending_events, keeping state.

        The item stays in its current state (not advanced, not released) so the
        next invocation flushes the pending event before acting. The ``post``
        directive records the state change the owner would have made had the
        write succeeded: ``{"release": True}`` to delete the item, or
        ``{"state": <new-state>, ...}`` to advance it. The ``report`` directive
        records the state-change report the owner would have sent, as
        ``{"subject": ..., "body": ...}``; the flush sends it before applying
        the post action, so a transition or release never happens without the
        design's report. The flush applies the post once the event is durable
        and the report is sent, so the handler does not re-derive and re-write
        the same event on the next tick (the design: "the next invocation writes
        the pending events first and then applies the release the owner
        recorded"). If the lock write itself fails, the next invocation
        reconciles from the still-current state.
        """
        pending = list(item.attributes.get("pending_events", []))
        entry: dict[str, Any] = {"event": event, "body": body, "sequence": sequence}
        if post is not None:
            entry["post"] = post
        if report is not None:
            entry["report"] = report
        pending.append(entry)
        attrs = {**item.attributes, "pending_events": pending, "state": item.state}
        try:
            self.lock.transition(item.owner, item.state, attrs, self.now)
        except LockContention:
            logger.warning("could not persist pending follow-up; next invocation retries")
        return {"decision": "followup_pending", "event": event, "state": item.state}

    def _flush_pending_events(self, item: LockItem) -> dict[str, Any] | None:
        """Write any pending archive events before the item is used, if present.

        Returns the item with pending_events cleared when it successfully wrote
        them (and persisted the clear), or None when there was nothing to flush.
        A write that still fails leaves the pending events in place and stops the
        handler for this invocation.
        """
        pending = list(item.attributes.get("pending_events", []))
        if not pending:
            return None
        remaining: list[dict[str, Any]] = []
        for entry in pending:
            check = self._emit(
                entry["event"],
                entry["body"],
                correlation_id=item.owner,
                sequence=int(entry.get("sequence", 0)),
            )
            if check is None:
                remaining.append(entry)
            elif not check.proven:
                # The replayed object exists but its retention is unproven. The
                # object was written, so the event is not lost and the entry is
                # cleared; the post-call retention gap is still reported, as the
                # original post-call write would have, rather than dropped.
                self._report_as(
                    item.owner,
                    f"T4 post-call archive retention unproven for {self.config.file_system_id}",
                    {"event": entry["event"], "archive_gap": self._gap(check)},
                )
        attrs = {k: v for k, v in item.attributes.items() if k != "pending_events"}
        if remaining:
            attrs["pending_events"] = remaining
        # When every pending event is durable, apply the post-write action the
        # owner recorded (release or advance) in the SAME write that clears
        # pending_events, so the handler does not re-derive and re-write the
        # same event. The last flushed entry carries the net post action and the
        # state-change report the owner would have sent (each handler pends at
        # most one follow-up event per invocation).
        post = None
        report = None
        if not remaining:
            for entry in pending:
                if "post" in entry:
                    post = entry["post"]
                if "report" in entry:
                    report = entry["report"]
        # The design gates each transition or release on BOTH archive and report
        # handling: a terminal archive write that becomes durable on replay must
        # not delete the lock item (nor advance it) until the state-change report
        # is also sent, so a transient archive failure cannot leave the record
        # with the event but no report. Send the stored report first; when it
        # fails, keep the pending entry (and so the post action) for the next
        # invocation rather than advancing or releasing without the report.
        if report is not None:
            if not self._try_report(report.get("subject", ""), report.get("body", {})):
                try:
                    self.lock.transition(
                        item.owner, item.state,
                        {**item.attributes, "pending_events": pending, "state": item.state},
                        self.now,
                    )
                except LockContention:
                    logger.warning(
                        "could not persist pending report retry; next invocation retries"
                    )
                return {"flushed": False, "report_pending": True}
        if post is not None and post.get("release"):
            try:
                self.lock.release(item.owner, item.state)
            except LockContention:
                logger.warning("could not release after pending flush; next invocation retries")
                return {"flushed": False}
            return {"flushed": True, "post_applied": "release"}
        if post is not None and post.get("state"):
            advanced = {**attrs, "expires_at": self._lease_expires_at()}
            advanced.update({k: v for k, v in post.items() if k != "release"})
            try:
                self.lock.transition(item.owner, item.state, advanced, self.now)
            except LockContention:
                logger.warning("could not advance after pending flush; next invocation retries")
                return {"flushed": False}
            return {"flushed": True, "post_applied": post["state"]}
        try:
            self.lock.transition(item.owner, item.state, {**attrs, "state": item.state}, self.now)
        except LockContention:
            logger.warning("could not persist pending flush; next invocation retries")
            return {"flushed": False}
        return {"flushed": not remaining, "attributes": attrs}

    def _archive_retention_unproven(
        self, detail: str | None, decision_body: dict[str, Any]
    ) -> dict[str, Any]:
        """Fail-closed outcome in auto: no call, record the outcome, release.

        decision=archive_retention_unproven is placed last so the carried-forward
        decision_body (decision=increase) cannot overwrite it. The outcome is
        written to the decision log (operational history) as well as reported,
        so the refusal is recorded, not only disclosed in the transient report.
        The archive itself could not be proven, so the fail-closed outcome is not
        written to the Object Lock archive (there is no proven destination); it
        goes to the decision log and the report.
        """
        body = {**decision_body, "decision": "archive_retention_unproven", "detail": detail}
        self._decision_log(self.correlation_id, DECISION, body)
        self._report(
            f"T4 archive retention unproven for {self.config.file_system_id}", body
        )
        self._safe_release(EVALUATING)
        return {"decision": "archive_retention_unproven", "detail": detail}

    def _safe_release(self, expected_state: str) -> None:
        try:
            self.lock.release(self.correlation_id, expected_state)
        except LockContention:
            logger.warning("lock changed before release; leaving it for the next invocation")

    # -- existing non-evaluating states ----------------------------------

    def _handle_existing_state(self, item: LockItem) -> dict[str, Any]:
        # Prove the bucket default retention before the first event of this
        # invocation when there are pending events to flush (the flush writes
        # archive events). The writing handlers prove it before their own first
        # event; a lease-valid calling item writes nothing and is left as is.
        if item.attributes.get("pending_events"):
            self._bucket_default_gap(item)
        # Flush any pending post-call archive events the owner recorded before
        # this item is acted on, so a release never strands them.
        flush = self._flush_pending_events(item)
        if flush is not None:
            if not flush.get("flushed", False):
                # The archive object is durable but its state-change report has
                # not been delivered yet; the item is kept with its pending entry
                # so the next invocation retries the report before advancing or
                # releasing (report_pending), distinct from an archive write that
                # still has not landed.
                return {
                    "decision": "pending_flush_incomplete",
                    "report_pending": flush.get("report_pending", False),
                    "state": item.state,
                }
            # The flush applied the owner's recorded post-write action (release
            # or advance) in the same write that cleared pending_events, so the
            # follow-up is complete; do not re-derive and re-write the same
            # event in a handler this invocation.
            post_applied = flush.get("post_applied")
            if post_applied is not None:
                return {
                    "decision": "followup_flushed",
                    "post_applied": post_applied,
                    "state": item.state,
                }
            # Re-read with the cleared pending_events so later writes carry on.
            refreshed = self.lock.read()
            if refreshed is not None:
                item = refreshed
            # Apply a release the owner recorded to follow a durable pending
            # write (a retryable rejection: the design archives `rejected` then
            # deletes the item). Now that the pending `rejected` event is
            # durable, release the item rather than letting it fall through to a
            # handler and be reclassified.
            release_reason = item.attributes.get("release_after_flush")
            if release_reason is not None:
                self._clear_item(item, item.state)
                return {
                    "decision": release_reason,
                    "released": True,
                    "error_code": item.attributes.get("release_error_code"),
                }

        handlers = {
            BLOCKED: self._on_blocked,
            SUBMITTED: self._on_submitted,
            OPTIMIZING: self._on_submitted,  # same follow: read status, archive terminal/capacity
            INDETERMINATE: self._on_indeterminate,
            MANUAL_DISPOSITION_REQUIRED: self._on_manual_disposition,
            CALLING: self._on_calling,
        }
        handler = handlers.get(item.state)
        if handler is None:
            logger.warning("unknown lock state %s; leaving it", item.state)
            return {"decision": "unknown_state", "state": item.state}
        return handler(item)

    def _on_blocked(self, item: LockItem) -> dict[str, Any]:
        disposition = item.attributes.get("disposition")
        fingerprint_changed = (
            item.attributes.get("config_fingerprint") != self.config.config_fingerprint
        )
        seq_base = int(item.attributes.get("sequence", 0))
        if fingerprint_changed:
            # Configuration changed: archive the clear and re-evaluate afresh in
            # this same invocation (no extra re-evaluation interval). Pend the
            # reconciled event on a write failure rather than clearing the latch
            # without the audit event. Prove the bucket default first.
            self._bucket_default_gap(item)
            body = {"source": "configuration_change", "resulting_state": "cleared"}
            proven, check = self._emit_followup(item, RECONCILED, body, seq_base + 1)
            if not proven:
                # On a write failure, pend the clear with a release post-action:
                # the next invocation flushes the reconciled event and deletes
                # the latch, then a later tick re-evaluates afresh (the alarm
                # stays in ALARM), rather than re-deriving the clear here.
                return self._pend_followup(
                    item, RECONCILED, body, seq_base + 1, post={"release": True},
                    report=self._pending_report(
                        item,
                        f"T4 blocked latch cleared (configuration change) "
                        f"for {self.config.file_system_id}",
                        {"reason": "blocked_cleared", "source": "configuration_change",
                         "lock_state": "released"},
                    ),
                )
            self._report_as(
                item.owner,
                f"T4 blocked latch cleared (configuration change) for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {"reason": "blocked_cleared", "source": "configuration_change",
                     "lock_state": "released", "archive_gap": self._gap(check)},
                ),
            )
            self._clear_item(item, BLOCKED)
            return self._reevaluate_after_clear("configuration_change")
        if disposition == "cleared":
            # An operator took the corrective action (an IAM fix or a quota
            # increase the fingerprint does not cover) and recorded the
            # evidence. Archive the clear and re-evaluate afresh now.
            self._bucket_default_gap(item)
            body = {
                "source": "operator",
                "resulting_state": "cleared",
                "evidence": item.attributes.get("evidence"),
            }
            proven, check = self._emit_followup(item, RECONCILED, body, seq_base + 1)
            if not proven:
                return self._pend_followup(
                    item, RECONCILED, body, seq_base + 1, post={"release": True},
                    report=self._pending_report(
                        item,
                        f"T4 blocked latch cleared (operator) for {self.config.file_system_id}",
                        {"reason": "blocked_cleared", "source": "operator",
                         "lock_state": "released"},
                    ),
                )
            self._report_as(
                item.owner,
                f"T4 blocked latch cleared (operator) for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {"reason": "blocked_cleared", "source": "operator",
                     "lock_state": "released", "archive_gap": self._gap(check)},
                ),
            )
            self._clear_item(item, BLOCKED)
            return self._reevaluate_after_clear("operator_cleared")
        if not item.attributes.get("report_sent", True):
            # The one required blocked report never reached SNS; retry it under
            # the original evaluation ID and persist the delivery state, so the
            # latch is not permanently silent after a failed initial publish.
            reason = item.attributes.get("reason")
            try:
                self._report_as(
                    item.owner,
                    f"T4 blocked ({reason}) for {self.config.file_system_id}",
                    self._report_body(
                        item,
                        {"reason": reason, "lock_state": BLOCKED,
                         "error_code": item.attributes.get("error_code")},
                    ),
                )
                sent = True
            except (ClientError, BotoCoreError):
                logger.exception("blocked report retry failed; next invocation retries")
                sent = False
            if sent:
                self.lock.transition(
                    item.owner,
                    BLOCKED,
                    {**item.attributes, "state": BLOCKED, "report_sent": True},
                    self.now,
                )
                return {"decision": "blocked_report_sent", "reason": reason}
            return {"decision": "blocked", "reason": reason, "report_pending": True}
        logger.info(
            "blocked latch holds for %s (%s)",
            self.config.file_system_id,
            item.attributes.get("reason"),
        )
        return {"decision": "blocked", "reason": item.attributes.get("reason")}

    def _reevaluate_after_clear(self, source: str) -> dict[str, Any]:
        """Acquire the lock afresh and run a full evaluation in this invocation."""
        try:
            self.lock.acquire(self.correlation_id, self._lease_expires_at(), self.now)
        except LockContention:
            logger.info("could not acquire after clearing blocked; next invocation evaluates")
            return {"decision": "blocked_cleared", "source": source}
        result = self._evaluate(None)
        return {"decision": "blocked_cleared", "source": source, "reevaluation": result}

    def _on_submitted(self, item: LockItem) -> dict[str, Any]:
        if not item.attributes.get("report_sent", True):
            # Resend under the original evaluation's correlation ID (item.owner),
            # the ID the archive sequence and lock item use, not this follower's.
            self._report_as(
                item.owner,
                f"T4 submitted increase for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {
                        "decision": "accepted",
                        "reason": "submitted",
                        "request_id": item.attributes.get("request_id"),
                        "lock_state": item.state,
                    },
                ),
            )
            self.lock.transition(
                item.owner,
                item.state,
                {**item.attributes, "state": item.state, "report_sent": True},
                self.now,
            )
            return {"decision": "report_resent", "state": item.state}
        matched = self._match_action(item)
        if matched is None:
            return {"decision": "awaiting_action", "state": item.state}
        status = matched.get("Status")
        seq_base = int(item.attributes.get("sequence", 0))
        # Retention contract: prove the bucket default before the first event
        # (terminal or capacity_available).
        self._bucket_default_gap(item)
        if status in TERMINAL_STATUSES:
            # Terminal is a post-call lifecycle event handled "as for accepted":
            # advance (delete the item) only when its archive write is proven,
            # so a failed terminal write pends instead of leaving the audit
            # archive without a terminal event.
            proven, _ = self._write_terminal(item, matched, seq_base + 1)
            if not proven:
                # A failed terminal archive write pends the terminal event AND
                # the final report. The item is deleted only after the next
                # invocation makes the terminal object durable and sends the
                # final report, so a transient archive failure never writes the
                # terminal object and deletes the lock item with no final report.
                return self._pend_followup(
                    item, TERMINAL, self._terminal_body(item, matched), seq_base + 1,
                    post={"release": True},
                    report=self._pending_report(
                        item,
                        f"T4 increase {matched.get('Status')} for {self.config.file_system_id}",
                        {"reason": "terminal", "status": matched.get("Status"),
                         "lock_state": "released"},
                    ),
                )
            self._clear_item(item, item.state)
            return {"decision": "terminal", "status": status}
        if status == "UPDATED_OPTIMIZING" and item.state != OPTIMIZING:
            optimization = self._storage_optimization(item)
            body = {
                "status": status,
                "storage_capacity": matched.get("TargetFileSystemValues", {}).get(
                    "StorageCapacity"
                ),
                "optimization_status": optimization.get("status"),
                "progress_percent": optimization.get("progress_percent"),
            }
            proven, check = self._emit_followup(item, CAPACITY_AVAILABLE, body, seq_base + 1)
            if not proven:
                return self._pend_followup(
                    item, CAPACITY_AVAILABLE, body, seq_base + 1,
                    post={"state": OPTIMIZING, "sequence": seq_base + 1},
                    report=self._pending_report(
                        item,
                        f"T4 capacity available for {self.config.file_system_id}",
                        {"reason": "capacity_available", "status": status,
                         "lock_state": OPTIMIZING},
                    ),
                )
            # Report with the original evaluation's correlation ID (item.owner),
            # the ID the archive and lock item use, not this follower invocation.
            self._report_as(
                item.owner,
                f"T4 capacity available for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {
                        "reason": "capacity_available",
                        "status": status,
                        "lock_state": OPTIMIZING,
                        "archive_gap": self._gap(check),
                    },
                ),
            )
            self.lock.transition(
                item.owner,
                item.state,
                {
                    **item.attributes,
                    "state": OPTIMIZING,
                    "sequence": seq_base + 1,
                    "expires_at": self._lease_expires_at(),
                },
                self.now,
            )
            return {"decision": "capacity_available", "state": OPTIMIZING}
        return {"decision": "optimizing", "status": status}

    def _storage_optimization(self, item: LockItem) -> dict[str, Any]:
        """Read the STORAGE_OPTIMIZATION status and progress, if present."""
        try:
            file_system = _describe_file_system(self.clients, self.config.file_system_id)
        except RuntimeError:
            return {}
        for action in file_system.get("AdministrativeActions", []):
            if action.get("AdministrativeActionType") == "STORAGE_OPTIMIZATION":
                return {
                    "status": action.get("Status"),
                    "progress_percent": action.get("ProgressPercent"),
                }
        return {}

    def _on_indeterminate(self, item: LockItem) -> dict[str, Any]:
        seq_base = int(item.attributes.get("sequence", 0))
        # Retention contract: prove the bucket default before the first event.
        self._bucket_default_gap(item)
        matched = self._match_action(item)
        if matched is not None:
            body = {"source": "administrative_action", "status": matched.get("Status")}
            proven, check = self._emit_followup(item, RECONCILED, body, seq_base + 1)
            if not proven:
                return self._pend_followup(
                    item, RECONCILED, body, seq_base + 1,
                    post={"state": SUBMITTED, "report_sent": True, "sequence": seq_base + 1},
                    report=self._pending_report(
                        item,
                        f"T4 reconciled to submitted for {self.config.file_system_id}",
                        {"reason": "reconciled", "source": "administrative_action",
                         "status": matched.get("Status"), "lock_state": SUBMITTED},
                    ),
                )
            # Report the reconciliation under the original evaluation ID.
            self._report_as(
                item.owner,
                f"T4 reconciled to submitted for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {"reason": "reconciled", "source": "administrative_action",
                     "status": matched.get("Status"), "lock_state": SUBMITTED,
                     "archive_gap": self._gap(check)},
                ),
            )
            self.lock.transition(
                item.owner,
                INDETERMINATE,
                {
                    **item.attributes,
                    "state": SUBMITTED,
                    "report_sent": True,
                    "sequence": seq_base + 1,
                    "expires_at": self._lease_expires_at(),
                },
                self.now,
            )
            return {"decision": "reconciled", "state": SUBMITTED}
        reconcile_until = int(item.attributes.get("reconcile_until", 0))
        if int(self.now.timestamp()) >= reconcile_until:
            self._report_as(
                item.owner,
                f"T4 manual disposition required for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {"reason": "manual_disposition_required", "owner": item.owner,
                     "lock_state": MANUAL_DISPOSITION_REQUIRED,
                     "request_time": item.attributes.get("request_time")},
                ),
            )
            self.lock.transition(
                item.owner,
                INDETERMINATE,
                {
                    **item.attributes,
                    "state": MANUAL_DISPOSITION_REQUIRED,
                    "expires_at": self._lease_expires_at(),
                },
                self.now,
            )
            return {"decision": "manual_disposition_required"}
        logger.info("reconcile pending for %s", self.config.file_system_id)
        return {"decision": "reconcile_pending"}

    def _on_manual_disposition(self, item: LockItem) -> dict[str, Any]:
        disposition = item.attributes.get("disposition")
        seq_base = int(item.attributes.get("sequence", 0))
        # Retention contract: prove the bucket default before the first event.
        self._bucket_default_gap(item)
        if disposition == "accepted":
            body = {"source": "operator", "evidence": item.attributes.get("evidence")}
            proven, check = self._emit_followup(item, RECONCILED, body, seq_base + 1)
            if not proven:
                return self._pend_followup(
                    item, RECONCILED, body, seq_base + 1,
                    post={"state": SUBMITTED, "report_sent": True, "sequence": seq_base + 1},
                    report=self._pending_report(
                        item,
                        f"T4 operator-accepted to submitted for {self.config.file_system_id}",
                        {"reason": "operator_accepted", "source": "operator",
                         "lock_state": SUBMITTED},
                    ),
                )
            self._report_as(
                item.owner,
                f"T4 operator-accepted to submitted for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {"reason": "operator_accepted", "source": "operator",
                     "lock_state": SUBMITTED, "archive_gap": self._gap(check)},
                ),
            )
            self.lock.transition(
                item.owner,
                MANUAL_DISPOSITION_REQUIRED,
                {
                    **item.attributes,
                    "state": SUBMITTED,
                    "report_sent": True,
                    "sequence": seq_base + 1,
                    "expires_at": self._lease_expires_at(),
                },
                self.now,
            )
            return {"decision": "operator_accepted", "state": SUBMITTED}
        # The final automatic defence against delayed visibility: look once more
        # for a matching administrative action BEFORE applying a not_accepted
        # disposition. A request the operator judged unaccepted may have become
        # visible since their check; the design prefers service-side evidence
        # over the disposition, so the request is not closed while it is in fact
        # in flight.
        matched = self._match_action(item)
        if matched is not None:
            body = {"source": "administrative_action", "status": matched.get("Status")}
            proven, check = self._emit_followup(item, RECONCILED, body, seq_base + 1)
            if not proven:
                return self._pend_followup(
                    item, RECONCILED, body, seq_base + 1,
                    post={"state": SUBMITTED, "report_sent": True, "sequence": seq_base + 1},
                    report=self._pending_report(
                        item,
                        f"T4 reconciled to submitted for {self.config.file_system_id}",
                        {"reason": "reconciled", "source": "administrative_action",
                         "status": matched.get("Status"), "lock_state": SUBMITTED},
                    ),
                )
            self._report_as(
                item.owner,
                f"T4 reconciled to submitted for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {"reason": "reconciled", "source": "administrative_action",
                     "status": matched.get("Status"), "lock_state": SUBMITTED,
                     "archive_gap": self._gap(check)},
                ),
            )
            self.lock.transition(
                item.owner,
                MANUAL_DISPOSITION_REQUIRED,
                {
                    **item.attributes,
                    "state": SUBMITTED,
                    "report_sent": True,
                    "sequence": seq_base + 1,
                    "expires_at": self._lease_expires_at(),
                },
                self.now,
            )
            return {"decision": "reconciled", "state": SUBMITTED}
        if disposition == "not_accepted":
            body = {"source": "operator", "resulting_state": "not_accepted"}
            proven, check = self._emit_followup(item, RECONCILED, body, seq_base + 1)
            if not proven:
                return self._pend_followup(
                    item, RECONCILED, body, seq_base + 1, post={"release": True},
                    report=self._pending_report(
                        item,
                        f"T4 operator-not-accepted (request closed) "
                        f"for {self.config.file_system_id}",
                        {"reason": "operator_not_accepted", "source": "operator",
                         "lock_state": "released"},
                    ),
                )
            self._report_as(
                item.owner,
                f"T4 operator-not-accepted (request closed) for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {"reason": "operator_not_accepted", "source": "operator",
                     "lock_state": "released", "archive_gap": self._gap(check)},
                ),
            )
            self._clear_item(item, MANUAL_DISPOSITION_REQUIRED)
            return {"decision": "operator_not_accepted"}
        self._report_as(
            item.owner,
            f"T4 manual disposition required for {self.config.file_system_id}",
            self._report_body(
                item,
                {"reason": "manual_disposition_required", "owner": item.owner,
                 "lock_state": MANUAL_DISPOSITION_REQUIRED},
            ),
        )
        return {"decision": "manual_disposition_required"}

    def _on_calling(self, item: LockItem) -> dict[str, Any]:
        if not lease_expired(item, self.now):
            logger.info("evaluation already running (owner %s, calling)", item.owner)
            return {"decision": "evaluation_already_running"}
        # Lease expired on a calling item: a request may have gone out. Archive
        # ambiguous and move to indeterminate, keeping every request fact. This
        # is a post-call lifecycle event: pend it on a write failure rather than
        # advancing without the audit event.
        seq_base = int(item.attributes.get("sequence", 0))
        # Retention contract: prove the bucket default before the first event.
        self._bucket_default_gap(item)
        body = {
            "observed": "calling item with expired lease",
            "request_facts": {
                "target_gib": item.attributes.get("target_gib"),
                "request_time": item.attributes.get("request_time"),
                "client_request_token": item.attributes.get("client_request_token"),
            },
            "observation_time": self.now.isoformat(),
        }
        request_time = int(item.attributes.get("request_time", int(self.now.timestamp())))
        reconcile_until = request_time + self.config.indeterminate_reconcile_hours * 3600
        proven, check = self._emit_followup(item, AMBIGUOUS, body, seq_base + 1)
        if not proven:
            return self._pend_followup(
                item, AMBIGUOUS, body, seq_base + 1,
                post={
                    "state": INDETERMINATE,
                    "reconcile_until": reconcile_until,
                    "sequence": seq_base + 1,
                },
                report=self._pending_report(
                    item,
                    f"T4 ambiguous (expired calling lease) for {self.config.file_system_id}",
                    {"reason": "ambiguous", "lock_state": INDETERMINATE},
                ),
            )
        self._report_as(
            item.owner,
            f"T4 ambiguous (expired calling lease) for {self.config.file_system_id}",
            self._report_body(
                item,
                {"reason": "ambiguous", "lock_state": INDETERMINATE,
                 "archive_gap": self._gap(check)},
            ),
        )
        self.lock.transition(
            item.owner,
            CALLING,
            {
                **item.attributes,
                "state": INDETERMINATE,
                "reconcile_until": reconcile_until,
                "sequence": seq_base + 1,
                "expires_at": self._lease_expires_at(),
            },
            self.now,
        )
        return {"decision": "ambiguous", "state": INDETERMINATE}

    # -- shared helpers for existing-state handlers ----------------------

    def _match_action(self, item: LockItem) -> dict[str, Any] | None:
        """Find the FILE_SYSTEM_UPDATE matching the recorded request, if any.

        The design requires BOTH facts before a match is accepted: a
        ``RequestTime`` that is a usable timestamp at or after the recorded
        request time, and ``TargetFileSystemValues.StorageCapacity`` equal to
        the recorded target. An action whose ``RequestTime`` is absent or has
        an unusable shape is not matched (fail closed), because an older or
        unidentifiable action with the same target is not evidence that the
        unknown request this item tracks was accepted.
        """
        try:
            file_system = _describe_file_system(self.clients, self.config.file_system_id)
        except RuntimeError:
            return None
        target = item.attributes.get("target_gib")
        request_time = int(item.attributes.get("request_time", 0))
        for action in file_system.get("AdministrativeActions", []):
            if action.get("AdministrativeActionType") != "FILE_SYSTEM_UPDATE":
                continue
            action_time = action.get("RequestTime")
            if not isinstance(action_time, datetime):
                # No usable timestamp: cannot prove the action is this request's.
                continue
            if action_time.tzinfo is None:
                action_time = action_time.replace(tzinfo=timezone.utc)
            if action_time.timestamp() < request_time:
                continue
            if action.get("TargetFileSystemValues", {}).get("StorageCapacity") == target:
                return action
        return None

    def _terminal_body(self, item: LockItem, action: dict[str, Any]) -> dict[str, Any]:
        """Build the terminal archive body (also used to re-pend on write failure)."""
        post_capacity = None
        try:
            file_system = _describe_file_system(self.clients, self.config.file_system_id)
            post_capacity = file_system.get("StorageCapacity")
        except RuntimeError:
            post_capacity = None
        return {
            "status": action.get("Status"),
            "failure_details": action.get("FailureDetails"),
            "storage_capacity": post_capacity,
        }

    def _write_terminal(
        self, item: LockItem, action: dict[str, Any], sequence: int
    ) -> tuple[bool, RetentionCheck | None]:
        """Archive the terminal event and send the final report.

        Returns ``(proven, check)``; the caller advances (deletes the item) only
        when the write is proven, so a failed terminal write is pended instead
        of leaving the audit archive without a terminal event.
        """
        body = self._terminal_body(item, action)
        proven, check = self._emit_followup(item, TERMINAL, body, sequence)
        if proven:
            # Final report under the original evaluation's correlation ID.
            self._report_as(
                item.owner,
                f"T4 increase {action.get('Status')} for {self.config.file_system_id}",
                self._report_body(
                    item,
                    {
                        "reason": "terminal",
                        "status": action.get("Status"),
                        "lock_state": "released",
                        "archive_gap": self._gap(check),
                    },
                ),
            )
        return proven, check

    def _clear_item(self, item: LockItem, expected_state: str) -> None:
        try:
            self.lock.release(item.owner, expected_state)
        except LockContention:
            logger.warning("item changed before clear; leaving it")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one guarded SSD auto-increase evaluation.

    Args:
        event: The SNS trigger event or EventBridge scheduled event (unused;
            the function reads the alarm state itself).
        context: Lambda context (unused).

    Returns:
        A machine-readable result describing the decision taken.
    """
    config = load_config()
    evaluation = Evaluation(config, _clients())
    result = evaluation.run()
    logger.info("evaluation %s: %s", evaluation.correlation_id, result)
    return result
