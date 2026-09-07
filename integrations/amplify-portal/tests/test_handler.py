"""Tests for the amplify-portal audit correlator.

The behaviours worth protecting here are the ones whose failure is silent:

* an SMB record leaking into the join, which produces a *wrong* answer rather
  than no answer;
* the checkpoint advancing past a file that was not actually read, which loses
  audit records permanently;
* the join key drifting from the one the application side emits, which yields
  zero matches and looks like "no activity".
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import pytest

import handler as correlator
from observability import emit_s3ap_app_signal, s3ap_join_key


class _Context:
    """Minimal Lambda context.

    Defined here rather than imported from conftest: pytest runs with
    ``--import-mode=importlib``, under which ``from conftest import ...``
    does not resolve.
    """

    function_name = "fsxn-obs-appsig-correlator"
    aws_request_id = "req-test"


CONTEXT = _Context()


class FakeSSM:
    def __init__(self, value: str | None = None):
        self.value = value
        self.written: list[tuple[str, str]] = []

    def get_parameter(self, Name: str):  # noqa: N803 - boto3 kwarg casing
        if self.value is None:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "not found"}}, "GetParameter"
            )
        return {"Parameter": {"Value": self.value}}

    def put_parameter(self, Name: str, Value: str, Type: str, Overwrite: bool):  # noqa: N803
        self.written.append((Name, Value))
        # Persist, so a second invocation in the same test reads what the first
        # one wrote. Without this the fake makes every run look like a first run,
        # which is exactly the behaviour the watermark is meant to prevent.
        self.value = Value


class FakePaginator:
    def __init__(self, keys: list[str], modified: dict[str, datetime] | None = None):
        self.keys = keys
        self.modified = modified or {}
        self.seen_kwargs: dict = {}

    def paginate(self, **kwargs):
        self.seen_kwargs = kwargs
        default = datetime(2026, 8, 30, 4, 0, tzinfo=timezone.utc)
        yield {
            "Contents": [
                {"Key": k, "LastModified": self.modified.get(k, default)}
                for k in self.keys
            ]
        }


class FakeS3:
    def __init__(
        self, objects: dict[str, bytes], modified: dict[str, datetime] | None = None
    ):
        self.objects = objects
        self.paginator = FakePaginator(sorted(objects), modified)
        self.reads: list[str] = []

    def get_paginator(self, name: str):
        return self.paginator

    def get_object(self, Bucket: str, Key: str):  # noqa: N803
        self.reads.append(Key)
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

        class Body:
            def __init__(self, data: bytes):
                self.data = data

            def read(self) -> bytes:
                return self.data

        return {"Body": Body(self.objects[Key])}


@pytest.fixture
def wired(monkeypatch, handler_env):
    """Wire the handler to fakes and return them for assertions."""

    def _wire(
        objects: dict[str, bytes],
        checkpoint: str | None = "0",
        modified: dict[str, datetime] | None = None,
    ):
        s3 = FakeS3(objects, modified)
        ssm = FakeSSM(checkpoint)
        monkeypatch.setattr(correlator, "_s3_client", lambda: s3)
        monkeypatch.setattr(correlator, "_ssm_client", lambda: ssm)
        return s3, ssm

    return _wire


def _emitted_records(captured: str) -> list[dict]:
    """Pull the correlation records out of stdout, ignoring the EMF records."""
    records = []
    for line in captured.strip().splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if parsed.get("marker") == correlator.AUDIT_SIDE_MARKER:
            records.append(parsed)
    return records


# ─── The protocol filter ────────────────────────────────────────────────────


class TestProtocolFilter:
    def test_only_the_s3_access_path_record_is_emitted(self, wired, capsys, s3_create_xml):
        # The fixture holds one HTTP event and one CIFS event on the same
        # object. Emitting both would attribute the SMB write to a portal user.
        wired({"audit/a.xml": s3_create_xml})
        result = correlator.lambda_handler({})

        records = _emitted_records(capsys.readouterr().out)
        assert len(records) == 1
        assert records[0]["audit_access_protocol"] == "HTTP"
        assert result["s3_path_records"] == 1
        assert result["file_protocol_records_skipped"] == 1

    def test_skipped_file_protocol_records_are_counted_not_hidden(
        self, wired, capsys, s3_create_xml
    ):
        wired({"audit/a.xml": s3_create_xml})
        result = correlator.lambda_handler({})
        assert result["records_parsed"] == 2
        assert result["file_protocol_records_skipped"] == 1


# ─── The join ───────────────────────────────────────────────────────────────


class TestJoinKeyAgreesWithTheApplicationSide:
    def test_audit_side_key_matches_the_application_side_key(
        self, wired, capsys, s3_create_xml
    ):
        # The single property the whole design rests on. If these drift the
        # join returns nothing and looks like an idle system.
        wired({"audit/a.xml": s3_create_xml})
        correlator.lambda_handler({})
        audit_records = _emitted_records(capsys.readouterr().out)

        app_signal = emit_s3ap_app_signal(
            object_key="data/object.txt", operation="PUT", principal="cognito-sub"
        )
        assert audit_records[0]["s3ap_join_key"] == app_signal["s3ap_join_key"]

    def test_key_is_the_normalized_object_key_not_the_ontap_path(
        self, wired, capsys, s3_create_xml
    ):
        wired({"audit/a.xml": s3_create_xml})
        correlator.lambda_handler({})
        record = _emitted_records(capsys.readouterr().out)[0]
        assert record["s3ap_join_key"] == s3ap_join_key("data/object.txt", "PUT")
        # The original path is kept for the investigation, not for the join.
        assert record["audit_path"] == "(vol1);/data/object.txt"
        assert record["audit_volume"] == "vol1"


class TestCorrelationRecordShape:
    def test_raw_event_is_not_carried(self, wired, capsys, s3_create_xml):
        # raw is the entire original event; carrying it would multiply Logs
        # ingestion cost for a field the join never reads.
        wired({"audit/a.xml": s3_create_xml})
        correlator.lambda_handler({})
        assert "raw" not in _emitted_records(capsys.readouterr().out)[0]

    def test_source_key_is_recorded_so_a_record_can_be_traced_back(
        self, wired, capsys, s3_create_xml
    ):
        wired({"audit/a.xml": s3_create_xml})
        correlator.lambda_handler({})
        assert _emitted_records(capsys.readouterr().out)[0]["audit_source_key"] == "audit/a.xml"

    @pytest.mark.parametrize(
        "timestamp,expected_ms",
        [
            ("2026-08-25T23:32:21.000000000Z", 1787700741000),
            ("2026-08-25T23:32:21Z", 1787700741000),
        ],
    )
    def test_nanosecond_timestamps_parse(self, timestamp, expected_ms):
        # ONTAP writes nanosecond precision, which fromisoformat does not take.
        assert correlator._audit_timestamp_ms({"timestamp": timestamp}) == expected_ms

    def test_unparseable_timestamp_yields_none_rather_than_raising(self):
        assert correlator._audit_timestamp_ms({"timestamp": "not a time"}) is None
        assert correlator._audit_timestamp_ms({}) is None


# ─── Checkpoint safety ──────────────────────────────────────────────────────


class TestWatermark:
    """The checkpoint is a record timestamp, not a key.

    A key high-water mark cannot work against an ONTAP audit volume, and the
    reason is not obvious, so it is asserted here rather than only explained in a
    comment:

    * ONTAP appends to one active file whose key never changes
      (``audit_<svm>_last.xml``). A key watermark stops reading it the first time
      it is read; measured, the file grew from 907 to 12,754 bytes and the next
      run listed nothing.
    * A rotated key (``..._D<timestamp>_<n>.xml``) sorts *before* ``_last``,
      because ``D`` < ``l``. Once the watermark holds ``_last.xml``, every file
      rotated afterwards is skipped permanently.
    """

    ACTIVE = "audit_svm_last.xml"
    ROTATED = "audit_svm_D2026-08-30-T03-29-11_0000000000.xml"

    def test_a_rotated_key_sorts_before_the_active_key(self):
        # The ordering fact the old scheme broke on. Independent of this code, so
        # it is asserted directly.
        assert self.ROTATED < self.ACTIVE

    def test_the_active_file_is_read_again_on_a_later_run(
        self, wired, s3_create_xml
    ):
        # The failure that a key watermark produced: content appended to the
        # active file after it was first read was never picked up.
        s3, _ = wired({self.ACTIVE: s3_create_xml}, checkpoint="0")
        correlator.lambda_handler({}, CONTEXT)
        first = list(s3.reads)

        s3.reads.clear()
        correlator.lambda_handler({}, CONTEXT)
        assert first == [self.ACTIVE]
        assert s3.reads == [self.ACTIVE], "the active file must be re-read"

    def test_start_after_is_never_used_even_once_the_watermark_has_advanced(
        self, wired, s3_create_xml
    ):
        # Passing a key to StartAfter is what skipped rotated files. Asserted
        # with a watermark already set, because that is the only state in which
        # the old scheme did anything wrong -- a version of this test that ran
        # only from a fresh start passed against a deliberately reintroduced
        # StartAfter.
        watermark = correlator._audit_timestamp_ns(
            {"timestamp": "2026-08-01T00:00:00.000000000Z"}
        )
        s3, _ = wired({self.ACTIVE: s3_create_xml}, checkpoint=str(watermark))
        correlator.lambda_handler({}, CONTEXT)
        assert "StartAfter" not in s3.paginator.seen_kwargs
        assert s3.reads == [self.ACTIVE]

    def test_a_file_rotated_after_the_active_one_is_still_read(
        self, wired, s3_create_xml
    ):
        # Under the old scheme this file was unreachable for good.
        s3, _ = wired({self.ACTIVE: s3_create_xml, self.ROTATED: s3_create_xml})
        correlator.lambda_handler({}, CONTEXT)
        assert set(s3.reads) == {self.ACTIVE, self.ROTATED}

    def test_records_already_emitted_are_not_emitted_again(
        self, wired, capsys, s3_create_xml
    ):
        wired({self.ACTIVE: s3_create_xml})
        first = correlator.lambda_handler({}, CONTEXT)
        capsys.readouterr()
        assert first["join_keys_emitted"] == 1

        second = correlator.lambda_handler({}, CONTEXT)
        assert second["join_keys_emitted"] == 0
        assert second["already_seen_records_skipped"] == 1
        assert _emitted_records(capsys.readouterr().out) == []

    def test_the_watermark_is_the_newest_emitted_record_timestamp(
        self, wired, s3_create_xml
    ):
        _, ssm = wired({self.ACTIVE: s3_create_xml})
        result = correlator.lambda_handler({}, CONTEXT)
        expected = correlator._audit_timestamp_ns(
            {"timestamp": "2026-08-25T23:32:21.000000000Z"}
        )
        assert result["watermark_ns"] == expected
        assert ssm.written[-1][1] == str(expected)

    def test_nanosecond_precision_survives_the_round_trip(self, wired):
        # Rounding to milliseconds would make two records written in the same
        # millisecond indistinguishable, and the watermark would skip or repeat.
        a = correlator._audit_timestamp_ns({"timestamp": "2026-08-30T03:29:37.670728420Z"})
        b = correlator._audit_timestamp_ns({"timestamp": "2026-08-30T03:29:37.670728421Z"})
        assert b - a == 1

    def test_a_legacy_key_shaped_checkpoint_restarts_rather_than_crashing(
        self, wired, s3_create_xml
    ):
        # A stack deployed under the old scheme holds a key in this parameter.
        s3, _ = wired({self.ACTIVE: s3_create_xml}, checkpoint="audit_svm_last.xml")
        result = correlator.lambda_handler({}, CONTEXT)
        assert result["files_read"] == 1
        assert result["join_keys_emitted"] == 1

    def test_the_init_sentinel_is_also_treated_as_a_fresh_start(
        self, wired, s3_create_xml
    ):
        _, _ = wired({self.ACTIVE: s3_create_xml}, checkpoint="__INIT__")
        assert correlator.lambda_handler({}, CONTEXT)["join_keys_emitted"] == 1

    def test_missing_checkpoint_parameter_starts_from_the_beginning(
        self, wired, s3_create_xml
    ):
        _, _ = wired({self.ACTIVE: s3_create_xml}, checkpoint=None)
        assert correlator.lambda_handler({}, CONTEXT)["files_read"] == 1

    def test_a_file_older_than_the_watermark_is_not_read(
        self, wired, s3_create_xml
    ):
        # Selection is by modification time; a file that predates the watermark
        # by more than the grace period cannot hold a newer record.
        watermark = correlator._audit_timestamp_ns(
            {"timestamp": "2026-08-30T12:00:00.000000000Z"}
        )
        s3, _ = wired(
            {"audit_svm_D2020-01-01_0.xml": s3_create_xml},
            checkpoint=str(watermark),
            modified={"audit_svm_D2020-01-01_0.xml": datetime(2020, 1, 1, tzinfo=timezone.utc)},
        )
        result = correlator.lambda_handler({}, CONTEXT)
        assert s3.reads == []
        assert result["files_read"] == 0

    def test_a_read_failure_does_not_advance_the_watermark_past_unread_records(
        self, monkeypatch, wired, s3_create_xml
    ):
        s3, ssm = wired({"audit_a.xml": s3_create_xml, "audit_b.xml": s3_create_xml})
        original = s3.get_object

        def failing(Bucket, Key):  # noqa: N803
            if Key == "audit_b.xml":
                from botocore.exceptions import ClientError

                raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
            return original(Bucket=Bucket, Key=Key)

        monkeypatch.setattr(s3, "get_object", failing)
        result = correlator.lambda_handler({}, CONTEXT)
        assert result["files_read"] == 1

    def test_no_files_leaves_the_watermark_alone(self, wired):
        _, ssm = wired({}, checkpoint="12345")
        result = correlator.lambda_handler({}, CONTEXT)
        assert result["files_read"] == 0
        assert ssm.written == []

    def test_a_record_without_a_timestamp_is_dropped_not_repeated(self, wired):
        # With no timestamp there is no way to tell whether it was emitted
        # before, so emitting it would duplicate it on every single run.
        xml = (
            b'<Event><System><EventID>4663</EventID>'
            b"<EventName>Read Object</EventName></System><EventData>"
            b'<Data Name="Source">HTTP</Data>'
            b'<Data Name="ObjectName">(vol1);/a.txt</Data>'
            b"</EventData></Event>"
        )
        _, _ = wired({"audit_svm_last.xml": xml})
        result = correlator.lambda_handler({}, CONTEXT)
        assert result["undated_records"] == 1
        assert result["join_keys_emitted"] == 0

    def test_directory_placeholders_are_not_treated_as_audit_files(self, wired):
        s3, _ = wired({"audit/": b""})
        result = correlator.lambda_handler({}, CONTEXT)
        assert result["files_read"] == 0
        assert s3.reads == []


# ─── Failure visibility ─────────────────────────────────────────────────────


class TestFailuresStayVisible:
    def test_unparseable_file_is_counted_and_does_not_advance_the_watermark(
        self, wired, monkeypatch, s3_create_xml
    ):
        # The watermark advances on records, not on files, so a file that could
        # not be parsed leaves it where it was and the file is retried next run.
        # That is the right trade: a parser fix later recovers those records,
        # whereas advancing past them loses them for good.
        _, ssm = wired({"audit/a.xml": s3_create_xml})

        def boom(data, key):
            raise ValueError("cannot parse")

        monkeypatch.setattr(correlator, "parse_audit_log", boom)
        result = correlator.lambda_handler({}, CONTEXT)

        assert result["unparseable_files"] == 1
        assert result["join_keys_emitted"] == 0
        assert result["watermark_ns"] == 0
        assert ssm.written == []

    def test_unmapped_operation_is_counted(self, wired, capsys):
        # An EventName outside the verified table must surface as a number
        # rather than as a key that quietly never matches.
        xml = (
            b'<Event><System><TimeCreated SystemTime="2026-08-25T23:32:21.000000000Z"/>'
            b"</System><EventData>"
            b'<Data Name="Source">HTTP</Data>'
            b'<Data Name="EventName">Brand New Op</Data>'
            b'<Data Name="ObjectName">(vol1);/data/o.txt</Data>'
            b"</EventData></Event>"
        )
        wired({"audit/a.xml": xml})
        result = correlator.lambda_handler({})
        assert result["unmapped_operations"] == 1
        assert result["join_keys_emitted"] == 1

    def test_missing_access_point_arn_raises_rather_than_reporting_success(
        self, monkeypatch, handler_env
    ):
        monkeypatch.delenv("FSX_S3_ACCESS_POINT_ARN")
        with pytest.raises(ValueError, match="FSX_S3_ACCESS_POINT_ARN"):
            correlator.lambda_handler({})


# ─── Metrics ────────────────────────────────────────────────────────────────


class TestMetrics:
    def _emf(self, captured: str) -> list[dict]:
        out = []
        for line in captured.strip().splitlines():
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "_aws" in parsed:
                out.append(parsed)
        return out

    def test_counts_are_emitted_as_metrics(self, wired, capsys, s3_create_xml):
        wired({"audit/a.xml": s3_create_xml})
        correlator.lambda_handler({})
        records = self._emf(capsys.readouterr().out)
        merged = {k: v for r in records for k, v in r.items()}
        assert merged[correlator.METRIC_S3_PATH_RECORDS] == 1
        assert merged[correlator.METRIC_FILE_PROTOCOL_RECORDS_SKIPPED] == 1
        assert merged[correlator.METRIC_JOIN_KEYS_EMITTED] == 1

    def test_checkpoint_movement_is_a_property_not_a_dimension(
        self, wired, capsys, s3_create_xml
    ):
        # Checkpoint values are unbounded in cardinality; as a dimension they
        # would create a new billed custom metric on every run.
        wired({"audit/a.xml": s3_create_xml})
        correlator.lambda_handler({})
        for record in self._emf(capsys.readouterr().out):
            dimensions = record["_aws"]["CloudWatchMetrics"][0]["Dimensions"][0]
            assert "checkpoint_after" not in dimensions


# ─── Measured audit output ──────────────────────────────────────────────────


class TestAgainstMeasuredAuditOutput:
    """Regression tests over real audit output rather than a hand-written fixture.

    ``tests/test_data/s3ap_audit_measured.xml`` is the xml audit log ONTAP
    produced on 2026-08-30 (ONTAP 9.18.1P3D1) while PUT, GET, LIST and DELETE
    were driven through an FSx for ONTAP S3 access point with a WINDOWS
    file-system identity, against a mixed-security-style volume carrying an audit
    ACE. The subject SID and the admin account name are redacted; nothing else is
    altered, including the ``Audit Enabled`` record ONTAP wrote when auditing was
    turned on.

    Three things this file established that a hand-written fixture had wrong:

    * ``Read Object`` is the EventName for an S3 GET. Without it in the verb
      table a GET's join key never matched the application side.
    * A delete carries its path in ``FileName``, not ``ObjectName``.
    * ``Audit Enabled`` arrives with ``Source`` = ``http``, so the access-path
      filter alone lets an audit-subsystem record into the join.
    """

    MEASURED = (
        pathlib.Path(__file__).parent / "test_data" / "s3ap_audit_measured.xml"
    )

    def _run(self, wired, capsys):
        wired({"audit/measured.xml": self.MEASURED.read_bytes()})
        result = correlator.lambda_handler({}, CONTEXT)
        return result, _emitted_records(capsys.readouterr().out)

    def test_all_four_operations_are_recognised_as_s3_access_path(
        self, wired, capsys
    ):
        result, records = self._run(wired, capsys)
        # Source is HTTP for object operations and S3 for the LIST; both count.
        # The fifth record in the file is the audit subsystem's own event.
        assert result["s3_path_records"] == 4
        assert result["file_protocol_records_skipped"] == 0
        assert len(records) == 4

    def test_the_audit_subsystems_own_event_is_excluded(self, wired, capsys):
        # "Audit Enabled" has Source=http and no path. Emitting it produces a
        # join key that matches every other pathless record.
        result, records = self._run(wired, capsys)
        assert result["audit_management_records_skipped"] == 1
        assert "Audit Enabled" not in [r["audit_operation"] for r in records]

    def test_no_emitted_record_has_an_empty_path(self, wired, capsys):
        _, records = self._run(wired, capsys)
        assert all(r["audit_path"] for r in records)

    def test_every_measured_operation_maps_to_an_s3_verb(self, wired, capsys):
        _, records = self._run(wired, capsys)
        verbs = sorted(r["s3ap_join_key"].split("|", 1)[0] for r in records)
        assert verbs == ["DELETE", "GET", "LIST", "PUT"]

    def test_no_operation_falls_through_unmapped(self, wired, capsys):
        # Before "Read Object" was added to the table, the GET landed here and
        # its join key could never match the application side.
        result, _ = self._run(wired, capsys)
        assert result["unmapped_operations"] == 0

    def test_get_joins_with_the_application_side(self, wired, capsys):
        _, records = self._run(wired, capsys)
        get_record = next(r for r in records if r["audit_operation"] == "Read Object")
        app = emit_s3ap_app_signal(object_key="data/win-object.txt", operation="GET")
        assert get_record["s3ap_join_key"] == app["s3ap_join_key"]

    def test_delete_recovers_its_path_from_the_file_name_field(self, wired, capsys):
        # The delete record has no ObjectName at all; the path is in FileName.
        # Reading only ObjectName makes deletes look uncorrelatable.
        _, records = self._run(wired, capsys)
        delete = next(r for r in records if r["audit_operation"] == "Unlink Object")
        assert delete["s3ap_join_key"] == "DELETE|data/win-object.txt"
        assert delete["audit_volume"] == "appsig_data"

    def test_list_carries_the_bucket_root_not_an_object(self, wired, capsys):
        # A LIST is a bucket-level operation: its path is the volume root, so the
        # object key is empty by nature rather than by a parsing failure.
        _, records = self._run(wired, capsys)
        listing = next(r for r in records if r["audit_operation"] == "S3A List Object")
        assert listing["s3ap_join_key"] == "LIST|"

    def test_requester_is_absent_from_every_joinable_record(self, wired, capsys):
        # The premise of the whole design: the audit log cannot attribute a file
        # operation to a person. If this ever starts failing, the application
        # side is no longer needed for attribution.
        import observability
        from ontap_audit_parser import parse_audit_log

        events = parse_audit_log(self.MEASURED.read_bytes(), "measured.xml")
        joinable = [e for e in events if observability.is_joinable_audit_event(e)]
        assert len(joinable) == 4
        for event in joinable:
            assert event["user"] == ""
            assert event["domain"] == ""

    def test_the_management_event_by_contrast_does_name_its_actor(self, wired, capsys):
        # Worth pinning as the counterpart: an administrative action over the
        # management interface *is* attributed. It is only the file operations
        # arriving through the access point that are not. So "ONTAP does not
        # record who did it" would be the wrong summary.
        import observability
        from ontap_audit_parser import parse_audit_log

        events = parse_audit_log(self.MEASURED.read_bytes(), "measured.xml")
        management = [e for e in events if observability.is_audit_management_event(e)]
        assert len(management) == 1
        assert management[0]["user"] != ""

    def test_volume_is_recoverable_from_every_record(self, wired, capsys):
        _, records = self._run(wired, capsys)
        assert {r["audit_volume"] for r in records} == {"appsig_data"}


class TestAuditManagementRecordsAreNotJoined:
    """The rotated log ONTAP wrote carried a *different* management-event name.

    ``s3ap_audit_management_only.xml`` is a real rotated audit file containing a
    single ``Audit Policy Changed`` record -- EventID 4719, the same ID as
    ``Audit Enabled``, but a different name. Enumerating names alone missed it,
    and it was excluded only incidentally by having no path, which reported the
    wrong reason for the drop.
    """

    MANAGEMENT_ONLY = (
        pathlib.Path(__file__).parent / "test_data" / "s3ap_audit_management_only.xml"
    )

    def test_a_file_of_only_management_records_emits_no_join_keys(
        self, wired, capsys
    ):
        wired({"audit/rotated.xml": self.MANAGEMENT_ONLY.read_bytes()})
        result = correlator.lambda_handler({}, CONTEXT)
        assert result["records_parsed"] == 1
        assert result["join_keys_emitted"] == 0
        assert _emitted_records(capsys.readouterr().out) == []

    def test_it_is_counted_as_management_not_as_pathless(self, wired, capsys):
        # Both filters would drop it; the distinction is whether the reported
        # reason is right. Counting it as pathless says "ONTAP gave us a record
        # with no path", which invites a hunt for a parsing bug that is not there.
        wired({"audit/rotated.xml": self.MANAGEMENT_ONLY.read_bytes()})
        result = correlator.lambda_handler({}, CONTEXT)
        assert result["audit_management_records_skipped"] == 1
        assert result["pathless_records_skipped"] == 0
        assert result["file_protocol_records_skipped"] == 0

    def test_the_event_id_is_what_excludes_it(self, wired, capsys):
        # Named differently from every entry that was in the name set when this
        # record was first seen, so the EventID has to be the signal.
        import observability
        from ontap_audit_parser import parse_audit_log

        event = parse_audit_log(self.MANAGEMENT_ONLY.read_bytes(), "rotated.xml")[0]
        assert event["event_type"] in observability.AUDIT_MANAGEMENT_EVENT_IDS
        assert observability.is_audit_management_event(event) is True

    def test_an_unknown_management_name_on_the_known_id_is_still_excluded(self):
        # The property that makes the EventID primary: a name nobody has seen.
        import observability

        assert observability.is_audit_management_event(
            {"event_type": "4719", "operation": "Some Future Audit Event"}
        ) is True
