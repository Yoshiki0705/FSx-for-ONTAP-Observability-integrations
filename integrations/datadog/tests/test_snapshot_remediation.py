"""Unit tests for the snapshot remediation Lambda (snapshot_remediation.py).

This function takes an action against production storage, so the tests focus on
the guard rails rather than the happy path alone:

- Missing volume/SVM is rejected before any ONTAP call
- Missing configuration produces an actionable message, not a bare KeyError
- The cooldown blocks a second snapshot inside the window (snapshot storm)
- The cooldown fails OPEN when it cannot be evaluated (evidence > tidiness)
- Unreachable management LIF and rejected credentials are distinguishable
- Every ONTAP request carries a timeout
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))
import snapshot_remediation  # noqa: E402


@pytest.fixture
def configured(monkeypatch):
    """Provide the required configuration and return the module."""
    monkeypatch.setenv("ONTAP_MGMT_IP", "198.51.100.10")
    monkeypatch.setenv(
        "ONTAP_CREDENTIALS_SECRET_ARN",
        "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:fsxn-ontap-admin",
    )
    return snapshot_remediation


def _resp(status: int, payload=None) -> MagicMock:
    """Build a fake urllib3 response."""
    r = MagicMock()
    r.status = status
    r.data = json.dumps(payload if payload is not None else {}).encode()
    return r


def _secrets_ok():
    """Patch boto3.client so Secrets Manager returns valid ONTAP credentials."""
    sm = MagicMock()
    sm.get_secret_value.return_value = {
        "SecretString": json.dumps({"username": "fsxadmin", "password": "pw"})
    }
    return patch.object(snapshot_remediation.boto3, "client", return_value=sm)


class TestInputValidation:
    def test_missing_volume_and_svm_rejected(self, configured):
        result = configured.lambda_handler({}, None)
        assert result["statusCode"] == 400
        assert "volume_name" in result["body"]

    def test_missing_svm_rejected(self, configured):
        result = configured.lambda_handler({"volume_name": "vol1"}, None)
        assert result["statusCode"] == 400

    def test_defaults_fill_in_missing_values(self, configured, monkeypatch):
        """DEFAULT_VOLUME / DEFAULT_SVM let a workflow omit the target."""
        monkeypatch.setenv("DEFAULT_VOLUME", "vol1")
        monkeypatch.setenv("DEFAULT_SVM", "svm-prod")

        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = [
                _resp(200, {"records": [{"uuid": "vol-uuid"}]}),  # volume lookup
                _resp(200, {"records": []}),                       # cooldown check
                _resp(201, {}),                                    # create snapshot
            ]
            result = configured.lambda_handler({}, None)

        assert result["statusCode"] == 200


class TestConfigurationValidation:
    def test_missing_env_returns_actionable_error(self, monkeypatch):
        """A bare KeyError would surface as an opaque failure mid-incident."""
        monkeypatch.delenv("ONTAP_MGMT_IP", raising=False)
        monkeypatch.delenv("ONTAP_CREDENTIALS_SECRET_ARN", raising=False)

        result = snapshot_remediation.lambda_handler(
            {"volume_name": "vol1", "svm_name": "svm-prod"}, None
        )

        assert result["statusCode"] == 500
        assert "ONTAP_MGMT_IP" in result["body"]
        assert "ONTAP_CREDENTIALS_SECRET_ARN" in result["body"]

    def test_unreadable_secret_returns_actionable_error(self, configured):
        sm = MagicMock()
        sm.get_secret_value.side_effect = Exception("AccessDeniedException")
        with patch.object(configured.boto3, "client", return_value=sm):
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert result["statusCode"] == 500
        assert "credentials" in result["body"].lower()


class TestOntapConnectivity:
    def test_unreachable_management_lif_returns_504(self, configured):
        """The most common misconfiguration: Lambda has no route to the LIF."""
        import urllib3

        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = urllib3.exceptions.ConnectTimeoutError(
                None, "timed out"
            )
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert result["statusCode"] == 504
        assert "unreachable" in result["body"]
        # The message must point at the actual fix
        assert "security group" in result["body"]

    def test_rejected_credentials_distinguishable_from_other_errors(self, configured):
        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.return_value = _resp(401, {})
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert result["statusCode"] == 403
        assert "credentials" in result["body"]

    def test_volume_not_found_returns_404(self, configured):
        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.return_value = _resp(200, {"records": []})
            result = configured.lambda_handler(
                {"volume_name": "nope", "svm_name": "svm-prod"}, None
            )

        assert result["statusCode"] == 404

    def test_all_ontap_requests_carry_a_timeout(self, configured):
        """An unbounded call would hang until the Lambda timeout."""
        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = [
                _resp(200, {"records": [{"uuid": "vol-uuid"}]}),
                _resp(200, {"records": []}),
                _resp(201, {}),
            ]
            configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert mock_http.request.call_count == 3
        for call in mock_http.request.call_args_list:
            assert call.kwargs.get("timeout") is not None


class TestCooldown:
    """The cooldown prevents a snapshot storm during a sustained attack."""

    def test_recent_snapshot_blocks_creation(self, configured, monkeypatch):
        monkeypatch.setattr(configured, "COOLDOWN_MINUTES", 15)
        recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()

        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = [
                _resp(200, {"records": [{"uuid": "vol-uuid"}]}),
                _resp(200, {"records": [{"name": "remediation_x", "create_time": recent}]}),
            ]
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "skipped"
        assert "cooldown active" in body["reason"]
        # No third call: the snapshot was never created
        assert mock_http.request.call_count == 2

    def test_expired_cooldown_allows_creation(self, configured, monkeypatch):
        monkeypatch.setattr(configured, "COOLDOWN_MINUTES", 15)
        old = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()

        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = [
                _resp(200, {"records": [{"uuid": "vol-uuid"}]}),
                _resp(200, {"records": [{"name": "remediation_x", "create_time": old}]}),
                _resp(201, {}),
            ]
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert result["statusCode"] == 200
        assert json.loads(result["body"])["status"] == "created"

    def test_cooldown_fails_open_when_list_unavailable(self, configured):
        """An extra snapshot is cheaper than a missed one during an incident."""
        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = [
                _resp(200, {"records": [{"uuid": "vol-uuid"}]}),
                _resp(500, {}),   # cooldown check fails
                _resp(201, {}),   # snapshot still created
            ]
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert json.loads(result["body"])["status"] == "created"

    def test_cooldown_fails_open_when_ontap_unreachable(self, configured):
        import urllib3

        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = [
                _resp(200, {"records": [{"uuid": "vol-uuid"}]}),
                urllib3.exceptions.ReadTimeoutError(None, "url", "timed out"),
                _resp(201, {}),
            ]
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert json.loads(result["body"])["status"] == "created"

    def test_unparseable_create_time_does_not_block(self, configured):
        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = [
                _resp(200, {"records": [{"uuid": "vol-uuid"}]}),
                _resp(200, {"records": [{"name": "r", "create_time": "not-a-date"}]}),
                _resp(201, {}),
            ]
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert json.loads(result["body"])["status"] == "created"


class TestSnapshotNaming:
    def test_name_includes_timestamp_and_sanitized_reason(self, configured):
        captured = {}

        def capture(method, url, **kwargs):
            if method == "POST":
                captured["body"] = json.loads(kwargs["body"].decode())
                return _resp(201, {})
            if "snapshots" in url:
                return _resp(200, {"records": []})
            return _resp(200, {"records": [{"uuid": "vol-uuid"}]})

        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = capture
            result = configured.lambda_handler(
                {
                    "volume_name": "vol1",
                    "svm_name": "svm-prod",
                    "reason": "mass delete confirmed",
                    "user": "soc-analyst",
                },
                None,
            )

        assert result["statusCode"] == 200
        name = captured["body"]["name"]
        assert name.startswith("remediation_")
        # Spaces must not leak into an ONTAP object name
        assert " " not in name
        assert "mass_delete" in name
        # The comment carries the audit trail
        assert "soc-analyst" in captured["body"]["comment"]

    def test_audit_trail_includes_request_id(self, configured):
        context = MagicMock()
        context.aws_request_id = "req-12345"
        captured = {}

        def capture(method, url, **kwargs):
            if method == "POST":
                captured["body"] = json.loads(kwargs["body"].decode())
                return _resp(201, {})
            if "snapshots" in url:
                return _resp(200, {"records": []})
            return _resp(200, {"records": [{"uuid": "vol-uuid"}]})

        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = capture
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, context
            )

        assert "req-12345" in captured["body"]["comment"]
        assert json.loads(result["body"])["request_id"] == "req-12345"


class TestCreateFailure:
    def test_snapshot_create_failure_returns_500(self, configured):
        with _secrets_ok(), patch.object(configured, "http") as mock_http:
            mock_http.request.side_effect = [
                _resp(200, {"records": [{"uuid": "vol-uuid"}]}),
                _resp(200, {"records": []}),
                _resp(409, {"error": {"message": "snapshot already exists"}}),
            ]
            result = configured.lambda_handler(
                {"volume_name": "vol1", "svm_name": "svm-prod"}, None
            )

        assert result["statusCode"] == 500
        assert "409" in result["body"]
