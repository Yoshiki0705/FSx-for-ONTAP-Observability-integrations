"""Tests for shared.python.observability.

Two things these tests exist to protect:

1. **The module imports with nothing installed.** Its predecessor imported
   ``aws_lambda_powertools``, which is present nowhere in this repository while
   ``build-layer.sh`` still shipped the module to Lambda. A test that merely
   exercised the behaviour would have passed in a venv that happened to have
   Powertools; ``test_module_has_no_third_party_imports`` asserts the property
   that actually broke.

2. **Both sides of the join produce the same key.** A correlation key that is
   derived slightly differently on the audit side and the application side does
   not fail -- it silently never matches. The round-trip tests pin that.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from observability import (  # noqa: E402
    APP_SIGNAL_NAMESPACE,
    METRIC_HANDLER_DURATION_MS,
    METRIC_HANDLER_ERRORS,
    METRIC_HANDLER_SUCCESS,
    METRIC_S3AP_OPERATIONS,
    ONTAP_EVENT_TO_S3_VERB,
    EmfMetrics,
    audit_path_to_object_key,
    audit_path_volume,
    emit_s3ap_app_signal,
    instrument_handler,
    is_audit_management_event,
    is_joinable_audit_event,
    is_s3_access_path,
    join_key_from_audit_event,
    normalize_object_key,
    principal_token,
    s3ap_join_key,
    xray_annotate,
    xray_subsegment,
)

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "observability.py"

# Real audit events, captured on FSx for ONTAP, ONTAP 9.18.1P3D1, xml audit
# format. Same fixtures as test_ontap_audit_parser so the two modules are
# tested against one reality rather than two.
S3_CREATE_EVENT = {
    "access_protocol": "HTTP",
    "operation": "Create Object",
    "path": "(vol1);/data/object.txt",
    "user": "",
    "client_ip": "203.0.113.10",
}
CIFS_WRITE_EVENT = {
    "access_protocol": "CIFS",
    "operation": "Write Object",
    "path": "(vol1);/data/file.txt",
    "user": "svcuser",
    "client_ip": "10.0.0.10",
}


# ─── Dependency freedom ─────────────────────────────────────────────────────


class TestNoThirdPartyDependency:
    """The module must import using the standard library alone."""

    STDLIB_ONLY = {
        "functools",
        "hashlib",
        "json",
        "logging",
        "os",
        "re",
        "time",
        "contextlib",
        "typing",
        "__future__",
    }

    def test_module_has_no_third_party_imports_at_module_scope(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        offenders = []
        for node in tree.body:  # module scope only
            if isinstance(node, ast.Import):
                offenders += [
                    a.name for a in node.names if a.name.split(".")[0] not in self.STDLIB_ONLY
                ]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] not in self.STDLIB_ONLY:
                    offenders.append(node.module)
        assert offenders == [], f"module-scope third-party imports: {offenders}"

    def test_powertools_is_not_imported_at_any_scope(self):
        # The dependency that shipped to Lambda while being installed nowhere.
        # Checked as an import rather than as a substring: the module docstring
        # names the package to explain why it is gone, and that mention is the
        # point rather than a violation.
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [m for m in imported if "powertools" in m]

    def test_xray_sdk_is_imported_lazily_not_at_module_scope(self):
        # X-Ray is optional; importing it at module scope would make the module
        # unimportable in exactly the environment it claims to degrade in.
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "xray" not in node.module


# ─── EMF ────────────────────────────────────────────────────────────────────


class TestEmfMetrics:
    def test_flush_emits_emf_shaped_record_on_stdout(self, capsys):
        metrics = EmfMetrics(namespace="NS", service="svc", environment="test")
        metrics.put_metric("Widgets", 3, "Count")
        metrics.flush()

        record = json.loads(capsys.readouterr().out.strip())
        assert record["Widgets"] == 3
        assert record["ServiceName"] == "svc"
        assert record["Environment"] == "test"
        cw = record["_aws"]["CloudWatchMetrics"][0]
        assert cw["Namespace"] == "NS"
        assert {"Name": "Widgets", "Unit": "Count"} in cw["Metrics"]
        assert "ServiceName" in cw["Dimensions"][0]

    def test_flush_returns_the_record_so_tests_need_not_read_stdout(self):
        metrics = EmfMetrics(namespace="NS", environment="test")
        metrics.put_metric("Widgets", 1, "Count")
        assert metrics.flush()["Widgets"] == 1

    def test_flush_without_metrics_writes_nothing(self, capsys):
        assert EmfMetrics(environment="test").flush() is None
        assert capsys.readouterr().out == ""

    def test_flush_resets_so_a_second_flush_does_not_double_report(self):
        metrics = EmfMetrics(environment="test")
        metrics.put_metric("Widgets", 1, "Count")
        metrics.flush()
        assert metrics.flush() is None

    def test_properties_are_not_declared_as_metrics(self):
        # A property must stay out of the metric definitions, or it becomes a
        # billed custom metric.
        metrics = EmfMetrics(environment="test")
        metrics.put_metric("Widgets", 1, "Count")
        metrics.set_property("object_key", "data/o.txt")
        record = metrics.flush()
        names = [m["Name"] for m in record["_aws"]["CloudWatchMetrics"][0]["Metrics"]]
        assert names == ["Widgets"]
        assert record["object_key"] == "data/o.txt"

    def test_properties_reset_on_flush_but_dimensions_persist(self):
        metrics = EmfMetrics(environment="test")
        metrics.set_dimension("Operation", "GET")
        metrics.set_property("once", "x")
        metrics.put_metric("A", 1, "Count")
        metrics.flush()
        metrics.put_metric("B", 1, "Count")
        second = metrics.flush()
        assert "once" not in second
        assert second["Operation"] == "GET"

    @pytest.mark.parametrize("name", ["", "has space", "has-dash", "dots.here", "x" * 257])
    def test_rejects_names_cloudwatch_would_silently_drop(self, name):
        with pytest.raises(ValueError):
            EmfMetrics(environment="test").put_metric(name, 1, "Count")

    def test_rejects_unknown_unit(self):
        with pytest.raises(ValueError, match="Invalid unit"):
            EmfMetrics(environment="test").put_metric("Widgets", 1, "Furlongs")

    def test_rejects_more_than_one_hundred_metrics_per_record(self):
        metrics = EmfMetrics(environment="test")
        for i in range(100):
            metrics.put_metric(f"M{i}", 1, "Count")
        with pytest.raises(ValueError, match="at most 100"):
            metrics.put_metric("M100", 1, "Count")

    def test_non_serializable_property_does_not_raise(self, capsys):
        metrics = EmfMetrics(environment="test")
        metrics.put_metric("Widgets", 1, "Count")
        metrics.set_property("obj", object())
        metrics.flush()
        json.loads(capsys.readouterr().out.strip())  # must still be valid JSON


# ─── X-Ray degradation ──────────────────────────────────────────────────────


class TestXrayDegradesQuietly:
    def test_subsegment_is_a_pass_through_without_the_sdk(self):
        with xray_subsegment("s", annotations={"k": "v"}) as sub:
            assert sub is None

    def test_subsegment_propagates_the_body_exception(self):
        with pytest.raises(RuntimeError, match="boom"):
            with xray_subsegment("s"):
                raise RuntimeError("boom")

    def test_annotate_reports_that_it_did_nothing(self):
        assert xray_annotate({"k": "v"}) is False

    def test_annotate_with_nothing_to_annotate_is_false(self):
        assert xray_annotate({}) is False

    def test_disabled_by_environment(self, monkeypatch):
        monkeypatch.setenv("ENABLE_XRAY", "false")
        with xray_subsegment("s") as sub:
            assert sub is None
        assert xray_annotate({"k": "v"}) is False


# ─── Join key ───────────────────────────────────────────────────────────────


class TestAuditPathConversion:
    @pytest.mark.parametrize(
        "audit_path,expected",
        [
            ("(vol1);/data/object.txt", "data/object.txt"),
            ("(vol1);/a.txt", "a.txt"),
            ("/vol/data/first.txt", "vol/data/first.txt"),
            ("data/object.txt", "data/object.txt"),
            ("", ""),
            ("();/x.txt", "x.txt"),
        ],
    )
    def test_strips_the_volume_prefix_and_leading_slash(self, audit_path, expected):
        assert audit_path_to_object_key(audit_path) == expected

    def test_only_the_first_prefix_is_stripped(self):
        # A key may legitimately contain ");" -- it must survive.
        assert audit_path_to_object_key("(vol1);/dir(a);/b.txt") == "dir(a);/b.txt"

    @pytest.mark.parametrize(
        "audit_path,expected",
        [("(vol1);/a.txt", "vol1"), ("/a.txt", ""), ("", ""), ("();/a", "")],
    )
    def test_volume_name_is_recoverable(self, audit_path, expected):
        assert audit_path_volume(audit_path) == expected


class TestJoinKey:
    def test_application_and_audit_sides_agree(self):
        # The property that makes correlation work at all.
        app_side = s3ap_join_key(object_key="data/object.txt", operation="PUT")
        audit_side = join_key_from_audit_event(S3_CREATE_EVENT)
        assert app_side == audit_side

    def test_leading_slash_on_the_application_side_does_not_break_the_join(self):
        assert s3ap_join_key("/data/o.txt", "PUT") == s3ap_join_key("data/o.txt", "PUT")

    @pytest.mark.parametrize(
        "event_name,verb", sorted(ONTAP_EVENT_TO_S3_VERB.items())
    )
    def test_every_mapped_ontap_event_reaches_its_verb(self, event_name, verb):
        assert s3ap_join_key("k", event_name) == s3ap_join_key("k", verb)

    def test_create_and_write_both_mean_put(self):
        # ONTAP audits an object upload as Create then Write; both must join to
        # the single PUT the application performed.
        assert s3ap_join_key("k", "Create Object") == s3ap_join_key("k", "Write Object")

    def test_unmapped_operation_is_normalized_not_guessed(self):
        # An unknown EventName must degrade predictably rather than be mapped to
        # a spelling nobody observed.
        assert s3ap_join_key("k", "Some New Op") == "SOME_NEW_OP|k"

    @pytest.mark.parametrize(
        "event_name,verb",
        [
            ("Create Object", "PUT"),
            ("Read Object", "GET"),
            ("S3A List Object", "LIST"),
            ("Unlink Object", "DELETE"),
        ],
    )
    def test_measured_event_names_reach_the_verb_the_application_used(
        self, event_name, verb
    ):
        # Measured 2026-08-30 on ONTAP 9.18.1P3D1 by driving PUT/GET/LIST/DELETE
        # through an S3 access point and reading the resulting xml audit log.
        # "Read Object" was the gap that made GET uncorrelatable before.
        assert s3ap_join_key("data/o.txt", event_name) == s3ap_join_key("data/o.txt", verb)

    def test_key_excludes_time(self):
        # Time is matched as a window, not baked into the key.
        assert s3ap_join_key("k", "PUT") == "PUT|k"

    def test_differing_operations_do_not_collide(self):
        assert s3ap_join_key("k", "PUT") != s3ap_join_key("k", "DELETE")

    def test_differing_keys_do_not_collide(self):
        assert s3ap_join_key("a", "PUT") != s3ap_join_key("b", "PUT")

    @pytest.mark.parametrize("key", ["  data/o.txt  ", "data/o.txt"])
    def test_whitespace_is_not_part_of_the_key(self, key):
        assert normalize_object_key(key) == "data/o.txt"


class TestJoinabilityFilter:
    """`is_joinable_audit_event` is the single predicate the join relies on.

    Each clause is asserted separately because each was needed for a different
    real record, and a clause with no test is a clause that can be deleted
    without anything noticing.
    """

    AUDIT_ENABLED_EVENT = {
        # Real shape: Source is lowercase http and there is no path at all.
        "access_protocol": "http",
        "operation": "Audit Enabled",
        "path": "",
        "user": "some-admin",
    }
    LIST_EVENT = {
        "access_protocol": "S3",
        "operation": "S3A List Object",
        "path": "(vol1);/",
        "user": "",
    }
    PATHLESS_EVENT = {
        "access_protocol": "HTTP",
        "operation": "Read Object",
        "path": "",
        "user": "",
    }

    def test_a_file_operation_over_the_s3_path_is_joinable(self):
        assert is_joinable_audit_event(S3_CREATE_EVENT) is True

    def test_a_file_protocol_operation_is_not_joinable(self):
        assert is_joinable_audit_event(CIFS_WRITE_EVENT) is False

    def test_the_audit_subsystems_own_event_is_not_joinable(self):
        # It passes the access-path filter (Source=http) and would otherwise
        # produce a key that matches every other pathless record.
        assert is_s3_access_path(self.AUDIT_ENABLED_EVENT) is True
        assert is_audit_management_event(self.AUDIT_ENABLED_EVENT) is True
        assert is_joinable_audit_event(self.AUDIT_ENABLED_EVENT) is False

    def test_a_management_event_carrying_a_path_is_still_excluded_by_name(self):
        # The observed "Audit Enabled" record has no path, so the pathless clause
        # alone excludes it and the name check looks redundant. This is the case
        # where the name check is the only thing doing the work. Not a shape
        # observed in output so far -- it is here so that removing the name check
        # fails a test rather than passing silently.
        with_path = dict(self.AUDIT_ENABLED_EVENT, path="(vol1);/somewhere.txt")
        assert is_s3_access_path(with_path) is True
        assert bool(with_path["path"]) is True
        assert is_joinable_audit_event(with_path) is False

    def test_a_pathless_record_is_not_joinable(self):
        assert is_joinable_audit_event(self.PATHLESS_EVENT) is False

    def test_a_list_is_joinable_even_though_its_object_key_is_empty(self):
        # The path is the volume root, which is non-empty; the derived object key
        # is empty. Excluding on the key rather than the path would drop LISTs.
        assert is_joinable_audit_event(self.LIST_EVENT) is True
        assert s3ap_join_key(audit_path_to_object_key(self.LIST_EVENT["path"]), "LIST") == "LIST|"


class TestAccessPathFilter:
    def test_s3_access_path_is_recognised(self):
        assert is_s3_access_path(S3_CREATE_EVENT) is True
        assert is_s3_access_path({"access_protocol": "S3"}) is True

    def test_file_protocol_is_excluded(self):
        # Joining an SMB write to a portal user who never made it would be a
        # false attribution, so the filter has to exclude it.
        assert is_s3_access_path(CIFS_WRITE_EVENT) is False
        assert is_s3_access_path({"access_protocol": "NFS"}) is False

    def test_missing_protocol_is_not_assumed_to_be_s3(self):
        assert is_s3_access_path({}) is False


class TestPrincipalToken:
    def test_hashed_by_default(self):
        token = principal_token("cognito-sub-value")
        assert token != "cognito-sub-value"
        assert len(token) == 16

    def test_hash_is_stable_so_it_can_be_grouped_on(self):
        assert principal_token("a") == principal_token("a")

    def test_distinct_principals_get_distinct_tokens(self):
        assert principal_token("a") != principal_token("b")

    def test_empty_stays_empty(self):
        assert principal_token("") == ""

    def test_opt_out_records_verbatim(self, monkeypatch):
        monkeypatch.setenv("APP_SIGNAL_HASH_PRINCIPAL", "false")
        assert principal_token("cognito-sub") == "cognito-sub"


# ─── App signal emission ────────────────────────────────────────────────────


class TestEmitS3apAppSignal:
    def test_returns_the_join_key_and_the_normalized_fields(self):
        signal = emit_s3ap_app_signal(
            object_key="/data/object.txt", operation="Create Object", principal="sub"
        )
        assert signal["s3ap_join_key"] == "PUT|data/object.txt"
        assert signal["s3ap_object_key"] == "data/object.txt"
        assert signal["s3ap_operation"] == "PUT"
        assert signal["app_principal"] == principal_token("sub")

    def test_signal_matches_the_audit_event_it_should_join_with(self):
        signal = emit_s3ap_app_signal(object_key="data/object.txt", operation="PUT")
        assert signal["s3ap_join_key"] == join_key_from_audit_event(S3_CREATE_EVENT)

    def test_object_key_is_a_property_not_a_dimension(self, capsys):
        # Key cardinality must not multiply the billed custom metric count.
        emit_s3ap_app_signal(object_key="data/object.txt", operation="GET")
        record = json.loads(capsys.readouterr().out.strip())
        dimensions = record["_aws"]["CloudWatchMetrics"][0]["Dimensions"][0]
        assert "s3ap_object_key" not in dimensions
        assert record["s3ap_object_key"] == "data/object.txt"
        assert "Operation" in dimensions

    def test_emits_the_operation_count_metric(self, capsys):
        emit_s3ap_app_signal(object_key="k", operation="GET")
        record = json.loads(capsys.readouterr().out.strip())
        assert record[METRIC_S3AP_OPERATIONS] == 1
        assert record["_aws"]["CloudWatchMetrics"][0]["Namespace"] == APP_SIGNAL_NAMESPACE

    def test_absent_principal_omits_the_field_rather_than_emitting_empty(self):
        assert "app_principal" not in emit_s3ap_app_signal(object_key="k", operation="GET")

    def test_extra_properties_are_carried(self, capsys):
        emit_s3ap_app_signal(
            object_key="k", operation="GET", extra_properties={"tenant": "acme"}
        )
        assert json.loads(capsys.readouterr().out.strip())["tenant"] == "acme"

    def test_a_supplied_sink_is_not_flushed_by_the_callee(self, capsys):
        # The caller owns flushing so several signals can share one record.
        sink = EmfMetrics(environment="test")
        emit_s3ap_app_signal(object_key="k", operation="GET", metrics=sink)
        assert capsys.readouterr().out == ""
        assert sink.flush() is not None

    def test_unknown_operation_still_produces_a_dimension_value(self, capsys):
        emit_s3ap_app_signal(object_key="k", operation="")
        assert json.loads(capsys.readouterr().out.strip())["Operation"] == "UNKNOWN"


# ─── Handler instrumentation ────────────────────────────────────────────────


class _Context:
    function_name = "portal-list-files"
    aws_request_id = "req-1"


class TestInstrumentHandler:
    def test_success_path_reports_duration_and_success(self, capsys):
        @instrument_handler(namespace="NS", service="svc")
        def handler(event, context):
            return {"ok": True}

        assert handler({}, _Context()) == {"ok": True}
        record = json.loads(capsys.readouterr().out.strip())
        assert record[METRIC_HANDLER_SUCCESS] == 1
        assert record[METRIC_HANDLER_ERRORS] == 0
        assert record[METRIC_HANDLER_DURATION_MS] >= 0
        assert record["aws_request_id"] == "req-1"

    def test_raising_handler_still_reports_duration_and_the_error(self, capsys):
        # An error metric that only appears on success is worse than none.
        @instrument_handler(namespace="NS", service="svc")
        def handler(event, context):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            handler({}, _Context())

        record = json.loads(capsys.readouterr().out.strip())
        assert record[METRIC_HANDLER_ERRORS] == 1
        assert record[METRIC_HANDLER_SUCCESS] == 0
        assert METRIC_HANDLER_DURATION_MS in record

    def test_service_falls_back_to_the_context_function_name(self, capsys):
        @instrument_handler(namespace="NS")
        def handler(event, context):
            return None

        handler({}, _Context())
        assert json.loads(capsys.readouterr().out.strip())["ServiceName"] == "portal-list-files"

    def test_service_falls_back_to_the_function_name_without_a_context(self, capsys):
        @instrument_handler(namespace="NS")
        def handler(event, context=None):
            return None

        handler({})
        assert json.loads(capsys.readouterr().out.strip())["ServiceName"] == "handler"

    def test_metadata_is_preserved_by_the_decorator(self):
        @instrument_handler()
        def handler(event, context):
            """Doc."""

        assert handler.__name__ == "handler"
        assert handler.__doc__ == "Doc."

    def test_cold_start_is_reported_once_then_not_again(self, capsys):
        import observability

        observability._COLD_START = True

        @instrument_handler(namespace="NS", service="svc")
        def handler(event, context):
            return None

        handler({}, _Context())
        first = json.loads(capsys.readouterr().out.strip())
        handler({}, _Context())
        second = json.loads(capsys.readouterr().out.strip())

        assert first["ColdStart"] == 1
        assert "ColdStart" not in second
