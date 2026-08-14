"""Unit tests for restore_verification module.

Tests cover:
- FlexClone create/delete lifecycle (ONTAP REST API)
- S3 Access Point attach/detach lifecycle (boto3 fsx client)
- Ransomware indicator scanning (boto3 s3 client)
- Full verify_snapshot orchestration (clean / suspicious / error paths)
- Cleanup-always-runs guarantee (finally block)
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent))

from restore_verification import (
    RestoreVerificationClient,
    RestoreVerificationError,
    SUSPICIOUS_MIN_COUNT,
    SUSPICIOUS_RATIO_THRESHOLD,
)


def make_response(status: int = 200, data: dict | None = None) -> MagicMock:
    """Create a mock urllib3 response."""
    resp = MagicMock()
    resp.status = status
    resp.data = json.dumps(data or {}).encode("utf-8")
    return resp


@pytest.fixture
def fsx_client():
    return MagicMock()


@pytest.fixture
def s3_client():
    return MagicMock()


@pytest.fixture
def client(fsx_client, s3_client):
    c = RestoreVerificationClient(
        mgmt_ip="10.0.1.100",
        username="fsxadmin",
        password="test-password",
        file_system_id="fs-0123456789abcdef0",
        fsx_client=fsx_client,
        s3_client=s3_client,
    )
    c._http = MagicMock()
    return c


# create_flexclone now runs an AD DC reachability pre-flight before creating
# anything, so every scripted response sequence for it begins with the CIFS
# service lookup. An SVM with no CIFS service short-circuits the check in one
# call, which keeps these sequences focused on clone creation.
# The AD paths themselves are covered in test_restore_verification_ad_precheck.py.
AD_NOT_APPLICABLE = {"records": []}


# ==========================================================================
# FlexClone lifecycle
# ==========================================================================


class TestCreateFlexclone:
    def test_create_flexclone_success(self, client):
        client._http.request.side_effect = [
            # GET /protocols/cifs/services -> no CIFS, so AD is not involved
            make_response(200, AD_NOT_APPLICABLE),
            # POST /storage/volumes -> job response (no async job, immediate)
            make_response(202, {"job": {"uuid": "job-uuid-1"}}),
            # GET /cluster/jobs/{uuid} -> success
            make_response(200, {"state": "success"}),
            # GET /storage/volumes?name=...  -> resolve clone uuid
            make_response(200, {"records": [{"uuid": "clone-uuid-1"}]}),
        ]

        result = client.create_flexclone(
            svm_name="svm-prod",
            volume_name="vol_data",
            snapshot_name="incident_response_20260708_143022",
            clone_name="verify_vol_data_test",
        )

        assert result["clone_name"] == "verify_vol_data_test"
        assert result["volume_uuid"] == "clone-uuid-1"
        assert result["parent_volume"] == "vol_data"
        assert result["parent_snapshot"] == "incident_response_20260708_143022"

        assert result["ad_check"] == "not_applicable"

        # Verify the clone request body. Index 1, not 0: the AD pre-flight GET
        # runs first, which is the ordering that makes the check useful.
        post_call = client._http.request.call_args_list[1]
        body = json.loads(post_call[1]["body"])
        assert body["clone"]["parent_volume"]["name"] == "vol_data"
        assert body["clone"]["parent_snapshot"]["name"] == "incident_response_20260708_143022"
        assert body["clone"]["is_flexclone"] is True

    def test_create_flexclone_default_name(self, client):
        client._http.request.side_effect = [
            make_response(200, AD_NOT_APPLICABLE),
            make_response(202, {"job": {"uuid": "job-uuid-1"}}),
            make_response(200, {"state": "success"}),
            make_response(200, {"records": [{"uuid": "clone-uuid-1"}]}),
        ]

        result = client.create_flexclone(
            svm_name="svm-prod", volume_name="vol_data", snapshot_name="snap1",
        )

        assert result["clone_name"].startswith("verify_vol_data_")

    def test_create_flexclone_job_failure(self, client):
        client._http.request.side_effect = [
            make_response(200, AD_NOT_APPLICABLE),
            make_response(202, {"job": {"uuid": "job-uuid-1"}}),
            make_response(200, {"state": "failure", "message": "insufficient space"}),
        ]

        with pytest.raises(RestoreVerificationError, match="job_wait|failed"):
            client.create_flexclone(
                svm_name="svm-prod", volume_name="vol_data", snapshot_name="snap1",
            )

    def test_create_flexclone_lookup_fails_after_create(self, client):
        client._http.request.side_effect = [
            make_response(200, AD_NOT_APPLICABLE),
            make_response(202, {"job": {"uuid": "job-uuid-1"}}),
            make_response(200, {"state": "success"}),
            make_response(200, {"records": []}),
        ]

        with pytest.raises(RestoreVerificationError, match="could not be resolved"):
            client.create_flexclone(
                svm_name="svm-prod", volume_name="vol_data", snapshot_name="snap1",
            )


class TestAdConnectivityPrecheck:
    """The library had no AD check while the CloudFormation-embedded copy of the
    same workflow did, and this module is the one build-layer.sh ships as a
    Lambda layer. These cover the check now that both agree.

    Why it matters: on an AD-joined SVM, ONTAP performs a unix->win lookup per
    S3 Access Point data operation, so unreachable domain controllers produce
    AccessDenied from ListObjectsV2 while HeadBucket still succeeds. The probe an
    operator naturally reaches for is the one that still works.
    """

    CIFS_ENABLED = {"records": [{"ad_domain": {"fqdn": "corp.example.com"}}]}

    def test_no_cifs_service_is_not_applicable(self, client):
        client._http.request.side_effect = [make_response(200, {"records": []})]
        assert client.check_ad_connectivity("svm-prod") == "not_applicable"
        # The domains endpoint must not be queried when there is no AD at all.
        assert client._http.request.call_count == 1

    def test_reachable_domain_controllers_verify(self, client):
        client._http.request.side_effect = [
            make_response(200, self.CIFS_ENABLED),
            make_response(200, {"records": [{"discovered_servers": [{"name": "dc1"}]}]}),
        ]
        assert client.check_ad_connectivity("svm-prod") == "verified"

    def test_zero_discovered_controllers_always_fails(self, client):
        """The one unambiguous bad answer. Fatal regardless of strict mode."""
        client._http.request.side_effect = [
            make_response(200, self.CIFS_ENABLED),
            make_response(200, {"records": [{"discovered_servers": []}]}),
        ]
        with pytest.raises(RestoreVerificationError, match="AD CONNECTIVITY FAILURE"):
            client.check_ad_connectivity("svm-prod")

    @pytest.mark.parametrize(
        "domains,reason",
        [
            ({"records": [{}]}, "field_absent"),
            ({"records": []}, "no_domain_records"),
        ],
    )
    def test_ambiguous_answers_proceed_with_a_recorded_verdict(self, client, domains, reason):
        client._http.request.side_effect = [
            make_response(200, self.CIFS_ENABLED),
            make_response(200, domains),
        ]
        assert client.check_ad_connectivity("svm-prod") == f"unverified:{reason}"

    def test_domains_endpoint_error_is_unverified_not_fatal(self, client):
        client._http.request.side_effect = [
            make_response(200, self.CIFS_ENABLED),
            make_response(500, {"error": {"message": "internal"}}),
        ]
        assert client.check_ad_connectivity("svm-prod") == "unverified:domains_api_error"

    @pytest.mark.parametrize(
        "domains", [{"records": [{}]}, {"records": []}]
    )
    def test_strict_mode_fails_closed_on_ambiguous_answers(
        self, fsx_client, s3_client, domains
    ):
        strict = RestoreVerificationClient(
            mgmt_ip="10.0.1.100",
            username="fsxadmin",
            password="test-password",
            file_system_id="fs-0123456789abcdef0",
            fsx_client=fsx_client,
            s3_client=s3_client,
            strict_ad_check=True,
        )
        strict._http = MagicMock()
        strict._http.request.side_effect = [
            make_response(200, self.CIFS_ENABLED),
            make_response(200, domains),
        ]
        with pytest.raises(RestoreVerificationError, match="strict mode"):
            strict.check_ad_connectivity("svm-prod")

    def test_create_flexclone_runs_the_check_before_creating_anything(self, client):
        """Ordering is the whole value: after the clone and access point exist,
        the check saves nothing."""
        client._http.request.side_effect = [
            make_response(200, self.CIFS_ENABLED),
            make_response(200, {"records": [{"discovered_servers": []}]}),
        ]
        with pytest.raises(RestoreVerificationError, match="AD CONNECTIVITY FAILURE"):
            client.create_flexclone(
                svm_name="svm-prod", volume_name="vol_data", snapshot_name="snap1",
            )
        methods = [c[0][0] for c in client._http.request.call_args_list]
        assert "POST" not in methods, (
            "the clone was created despite the AD check failing"
        )

    def test_verdict_is_exposed_for_downstream_diagnosis(self, client):
        client._http.request.side_effect = [
            make_response(200, self.CIFS_ENABLED),
            make_response(200, {"records": [{}]}),
            make_response(202, {"job": {"uuid": "job-1"}}),
            make_response(200, {"state": "success"}),
            make_response(200, {"records": [{"uuid": "clone-uuid-1"}]}),
        ]
        result = client.create_flexclone(
            svm_name="svm-prod", volume_name="vol_data", snapshot_name="snap1",
        )
        assert result["ad_check"] == "unverified:field_absent"
        assert client.last_ad_check == "unverified:field_absent"


class TestDeleteFlexclone:
    def test_delete_flexclone_success(self, client):
        client._http.request.return_value = make_response(200, {})
        client.delete_flexclone("clone-uuid-1")
        client._http.request.assert_called_once()
        assert client._http.request.call_args[0][0] == "DELETE"

    def test_delete_flexclone_already_gone(self, client):
        client._http.request.return_value = make_response(
            404, {"error": {"message": "volume not found"}}
        )
        # Should not raise — 404 is treated as already-deleted
        client.delete_flexclone("clone-uuid-1")


# ==========================================================================
# S3 Access Point lifecycle
# ==========================================================================


class TestAttachAccessPoint:
    def test_attach_access_point_success(self, client, fsx_client):
        fsx_client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": [{"Name": "verify_vol_data_test", "VolumeId": "fsvol-abc123", "Lifecycle": "AVAILABLE"}]}
        ]
        fsx_client.describe_s3_access_point_attachments.return_value = {
            "S3AccessPointAttachments": [
                {
                    "Lifecycle": "AVAILABLE",
                    "S3AccessPoint": {
                        "ResourceARN": "arn:aws:s3:us-east-1:123456789012:accesspoint/verify-vol-data-test"
                    },
                }
            ]
        }

        result = client.attach_access_point(
            clone_name="verify_vol_data_test", vpc_id="vpc-0123456789abcdef0",
        )

        assert result["fsvol_id"] == "fsvol-abc123"
        assert "accesspoint" in result["access_point_arn"]
        fsx_client.create_and_attach_s3_access_point.assert_called_once()
        call_kwargs = fsx_client.create_and_attach_s3_access_point.call_args[1]
        assert call_kwargs["OntapConfiguration"]["VolumeId"] == "fsvol-abc123"
        assert call_kwargs["S3AccessPoint"]["VpcConfiguration"]["VpcId"] == "vpc-0123456789abcdef0"

    def test_attach_access_point_fsvol_not_found_times_out(self, client, fsx_client, monkeypatch):
        fsx_client.get_paginator.return_value.paginate.return_value = [{"Volumes": []}]
        _fake_now = [0.0]
        monkeypatch.setattr("restore_verification.time.monotonic", lambda: _fake_now[0])
        monkeypatch.setattr(
            "restore_verification.time.sleep", lambda seconds: _fake_now.__setitem__(0, _fake_now[0] + seconds)
        )

        with pytest.raises(RestoreVerificationError, match="Timed out waiting"):
            client.attach_access_point(
                clone_name="missing-clone", vpc_id="vpc-1", max_wait_seconds=1,
            )

    def test_attach_access_point_misconfigured(self, client, fsx_client):
        fsx_client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": [{"Name": "verify_vol_data_test", "VolumeId": "fsvol-abc123", "Lifecycle": "AVAILABLE"}]}
        ]
        fsx_client.describe_s3_access_point_attachments.return_value = {
            "S3AccessPointAttachments": [{"Lifecycle": "MISCONFIGURED"}]
        }

        with pytest.raises(RestoreVerificationError, match="MISCONFIGURED"):
            client.attach_access_point(
                clone_name="verify_vol_data_test", vpc_id="vpc-1",
            )

    def test_attach_access_point_create_client_error(self, client, fsx_client):
        fsx_client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": [{"Name": "verify_vol_data_test", "VolumeId": "fsvol-abc123", "Lifecycle": "AVAILABLE"}]}
        ]
        fsx_client.create_and_attach_s3_access_point.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad request"}},
            "CreateAndAttachS3AccessPoint",
        )

        with pytest.raises(RestoreVerificationError, match="Failed to create S3 Access Point"):
            client.attach_access_point(
                clone_name="verify_vol_data_test", vpc_id="vpc-1",
            )


class TestDetachAccessPoint:
    def test_detach_access_point_success(self, client, fsx_client):
        client.detach_access_point("verify-vol-data-test")
        fsx_client.detach_and_delete_s3_access_point.assert_called_once_with(
            Name="verify-vol-data-test"
        )

    def test_detach_access_point_not_found(self, client, fsx_client):
        fsx_client.detach_and_delete_s3_access_point.side_effect = ClientError(
            {"Error": {"Code": "S3AccessPointAttachmentNotFound", "Message": "not found"}},
            "DetachAndDeleteS3AccessPoint",
        )
        # Should not raise
        client.detach_access_point("verify-vol-data-test")

    def test_detach_access_point_other_error_raises(self, client, fsx_client):
        fsx_client.detach_and_delete_s3_access_point.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}},
            "DetachAndDeleteS3AccessPoint",
        )
        with pytest.raises(ClientError):
            client.detach_access_point("verify-vol-data-test")


# ==========================================================================
# Ransomware indicator scanning
# ==========================================================================


class TestScanForRansomwareIndicators:
    def test_scan_clean_volume(self, client, s3_client):
        s3_client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": f"file{i}.docx"} for i in range(100)]}
        ]

        result = client.scan_for_ransomware_indicators("arn:aws:s3:::ap/test")

        assert result["objects_scanned"] == 100
        assert result["suspicious_objects"] == []
        assert result["suspicious_ratio"] == 0.0

    def test_scan_suspicious_volume(self, client, s3_client):
        contents = [{"Key": f"file{i}.docx"} for i in range(80)]
        contents += [{"Key": f"file{i}.encrypted"} for i in range(20)]
        s3_client.get_paginator.return_value.paginate.return_value = [{"Contents": contents}]

        result = client.scan_for_ransomware_indicators("arn:aws:s3:::ap/test")

        assert result["objects_scanned"] == 100
        assert len(result["suspicious_objects"]) == 20
        assert result["suspicious_ratio"] == 0.2

    def test_scan_empty_volume(self, client, s3_client):
        s3_client.get_paginator.return_value.paginate.return_value = [{}]

        result = client.scan_for_ransomware_indicators("arn:aws:s3:::ap/test")

        assert result["objects_scanned"] == 0
        assert result["suspicious_ratio"] == 0.0

    def test_scan_case_insensitive_extension_match(self, client, s3_client):
        s3_client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "report.DOCX"}, {"Key": "photo.LOCKED"}]}
        ]

        result = client.scan_for_ransomware_indicators("arn:aws:s3:::ap/test")

        assert result["suspicious_objects"] == ["photo.LOCKED"]


# ==========================================================================
# Full orchestration: verify_snapshot
# ==========================================================================


class TestVerifySnapshot:
    def _mock_happy_path_ontap(self, client):
        client._http.request.side_effect = [
            make_response(202, {"job": {"uuid": "job-1"}}),
            make_response(200, {"state": "success"}),
            make_response(200, {"records": [{"uuid": "clone-uuid-1"}]}),
            make_response(200, {}),  # DELETE flexclone in cleanup
        ]

    def test_verify_snapshot_clean(self, client, fsx_client, s3_client):
        self._mock_happy_path_ontap(client)
        fsx_client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": [{"Name": _clone_name_matcher(), "VolumeId": "fsvol-abc", "Lifecycle": "AVAILABLE"}]}
        ]
        fsx_client.describe_s3_access_point_attachments.return_value = {
            "S3AccessPointAttachments": [
                {"Lifecycle": "AVAILABLE", "S3AccessPoint": {"ResourceARN": "arn:aws:s3:::ap/test"}}
            ]
        }
        s3_client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": f"file{i}.docx"} for i in range(50)]}
        ]

        result = client.verify_snapshot(
            svm_name="svm-prod",
            volume_name="vol_data",
            snapshot_name="incident_response_20260708_143022",
            vpc_id="vpc-0123456789abcdef0",
        )

        assert result.verdict == "clean"
        assert result.objects_scanned == 50
        assert result.cleaned_up is True
        assert result.started_at
        assert result.completed_at
        fsx_client.detach_and_delete_s3_access_point.assert_called_once()

    def test_verify_snapshot_suspicious(self, client, fsx_client, s3_client):
        self._mock_happy_path_ontap(client)
        fsx_client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": [{"Name": _clone_name_matcher(), "VolumeId": "fsvol-abc", "Lifecycle": "AVAILABLE"}]}
        ]
        fsx_client.describe_s3_access_point_attachments.return_value = {
            "S3AccessPointAttachments": [
                {"Lifecycle": "AVAILABLE", "S3AccessPoint": {"ResourceARN": "arn:aws:s3:::ap/test"}}
            ]
        }
        contents = [{"Key": f"file{i}.docx"} for i in range(50)]
        contents += [{"Key": f"file{i}.encrypted"} for i in range(50)]
        s3_client.get_paginator.return_value.paginate.return_value = [{"Contents": contents}]

        result = client.verify_snapshot(
            svm_name="svm-prod",
            volume_name="vol_data",
            snapshot_name="incident_response_20260708_143022",
            vpc_id="vpc-0123456789abcdef0",
        )

        assert result.verdict == "suspicious"
        assert len(result.suspicious_objects) == 50
        assert result.cleaned_up is True

    def test_verify_snapshot_below_min_count_not_suspicious(self, client, fsx_client, s3_client):
        """A handful of suspicious files below SUSPICIOUS_MIN_COUNT should
        not flip the verdict even if the ratio is high (avoids false
        positives on small volumes with a couple of legitimately-named
        .locked files)."""
        self._mock_happy_path_ontap(client)
        fsx_client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": [{"Name": _clone_name_matcher(), "VolumeId": "fsvol-abc", "Lifecycle": "AVAILABLE"}]}
        ]
        fsx_client.describe_s3_access_point_attachments.return_value = {
            "S3AccessPointAttachments": [
                {"Lifecycle": "AVAILABLE", "S3AccessPoint": {"ResourceARN": "arn:aws:s3:::ap/test"}}
            ]
        }
        assert SUSPICIOUS_MIN_COUNT > 5
        contents = [{"Key": "a.docx"}, {"Key": "b.docx"}]
        contents += [{"Key": "c.locked"}]  # 1 of 3 = 33% ratio, but count is below floor
        s3_client.get_paginator.return_value.paginate.return_value = [{"Contents": contents}]

        result = client.verify_snapshot(
            svm_name="svm-prod",
            volume_name="vol_data",
            snapshot_name="snap1",
            vpc_id="vpc-1",
        )

        assert result.verdict == "clean"

    def test_verify_snapshot_error_still_cleans_up(self, client, fsx_client):
        """If clone creation fails, cleanup must still run (no-op since
        nothing was created) and the result must report verdict=error."""
        client._http.request.side_effect = RestoreVerificationError(
            "ONTAP API POST /storage/volumes failed: HTTP 500",
            step="ontap_api",
        )

        result = client.verify_snapshot(
            svm_name="svm-prod",
            volume_name="vol_data",
            snapshot_name="snap1",
            vpc_id="vpc-1",
        )

        assert result.verdict == "error"
        assert "ontap_api" in result.reason
        assert result.cleaned_up is True  # nothing to clean up = vacuously true
        fsx_client.detach_and_delete_s3_access_point.assert_not_called()

    def test_verify_snapshot_error_after_clone_created_cleans_up_clone(
        self, client, fsx_client, monkeypatch,
    ):
        """If S3 AP attachment fails after the clone was created, the clone
        must still be deleted during cleanup."""
        client._http.request.side_effect = [
            make_response(202, {"job": {"uuid": "job-1"}}),
            make_response(200, {"state": "success"}),
            make_response(200, {"records": [{"uuid": "clone-uuid-1"}]}),
            make_response(200, {}),  # DELETE flexclone in cleanup
        ]
        fsx_client.get_paginator.return_value.paginate.return_value = [{"Volumes": []}]
        # attach_access_point() uses its default max_wait_seconds (120s) here
        # since verify_snapshot() doesn't expose that parameter. Use a fake
        # clock so the polling loop's real-time deadline check fast-forwards
        # instead of burning 120 seconds of actual wall-clock time.
        _fake_now = [0.0]
        monkeypatch.setattr("restore_verification.time.monotonic", lambda: _fake_now[0])
        monkeypatch.setattr(
            "restore_verification.time.sleep", lambda seconds: _fake_now.__setitem__(0, _fake_now[0] + seconds)
        )

        result = client.verify_snapshot(
            svm_name="svm-prod",
            volume_name="vol_data",
            snapshot_name="snap1",
            vpc_id="vpc-1",
        )

        assert result.verdict == "error"
        assert result.clone_name  # was populated before the failure
        # DELETE call for the clone should be the last ONTAP call made
        delete_calls = [
            c for c in client._http.request.call_args_list if c[0][0] == "DELETE"
        ]
        assert len(delete_calls) == 1

    def test_to_dict_caps_suspicious_object_list(self, client):
        from restore_verification import VerificationResult

        result = VerificationResult(
            svm_name="svm-prod",
            volume_name="vol_data",
            snapshot_name="snap1",
            suspicious_objects=[f"file{i}.encrypted" for i in range(100)],
        )
        d = result.to_dict()
        assert len(d["suspicious_objects"]) == 50
        assert d["suspicious_object_count"] == 100


class _AnyStringStartingWith:
    """Equality helper: matches any string with the given prefix.

    verify_snapshot() generates a timestamped clone name internally (no
    explicit clone_name is passed), so tests can't hardcode the exact name
    FSx's DescribeVolumes response should match against. This object is used
    as the mocked "Name" field's value so `volume.get("Name") == clone_name`
    (evaluated by the production code) succeeds for any real generated name.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and other.startswith(self.prefix)

    def __hash__(self) -> int:  # pragma: no cover - not used as dict key
        return hash(self.prefix)


def _clone_name_matcher():
    """Matches the timestamped clone name generated by create_flexclone()
    when verify_snapshot() calls it without an explicit clone_name."""
    return _AnyStringStartingWith("verify_vol_data_")
