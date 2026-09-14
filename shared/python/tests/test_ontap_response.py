"""Unit tests for ontap_response module.

Tests cover:
- SMB user blocking/unblocking via name-mapping
- NFS IP blocking/unblocking via export-policy rules
- Snapshot creation with cooldown logic
- CIFS session disconnect
- Composite containment actions
- Error handling and edge cases
"""

import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

# Add shared/python to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ontap_response import (
    OntapResponseClient,
    OntapResponseError,
    RESPONSE_MARKER,
)


def make_response(status: int = 200, data: dict | None = None) -> MagicMock:
    """Create a mock urllib3 response."""
    resp = MagicMock()
    resp.status = status
    if data is None:
        data = {}
    resp.data = json.dumps(data).encode("utf-8")
    return resp


@pytest.fixture
def client():
    """Create OntapResponseClient with mocked HTTP."""
    c = OntapResponseClient(
        mgmt_ip="10.0.1.100",
        username="fsxadmin",
        password="test-password",
    )
    c._http = MagicMock()
    return c


# ==========================================================================
# SMB User Blocking Tests
# ==========================================================================


class TestBlockSmbUser:
    """Tests for block_smb_user method."""

    def test_block_smb_user_success(self, client):
        """Block an SMB user via name-mapping."""
        # Mock: SVM lookup, CIFS services check (no AD), name-mapping creation
        client._http.request.side_effect = [
            make_response(200, {"records": [{"uuid": "svm-uuid-123", "name": "svm-prod"}]}),
            make_response(200, {"records": []}),  # No CIFS service = not AD-joined
            make_response(201, {}),
        ]

        result = client.block_smb_user(
            svm_name="svm-prod",
            domain="CORP",
            username="jdoe",
        )

        assert result["action"] == "block_smb_user"
        assert result["status"] == "blocked"
        assert result["pattern"] == "CORP\\\\jdoe"
        assert result["svm"] == "svm-prod"
        assert result["position"] == 1

        # Verify the POST body — non-AD SVM uses space replacement
        post_call = client._http.request.call_args_list[2]
        assert post_call[0][0] == "POST"
        body = json.loads(post_call[1]["body"])
        assert body["direction"] == "win_unix"
        assert body["replacement"] == " "

    def test_block_smb_user_reports_the_security_style_boundary(self, client, caplog):
        """The result must not present itself as verified access denial.

        The addendum used to claim this block works on every security style,
        on an unsourced mechanism. An NTFS style volume evaluates the Windows
        ACL directly and never consults the win->unix mapping, so the mapping
        is created and has no effect. Nothing in this repository has ever
        attempted SMB access after a block -- the E2E record measured a 201
        from the API and that was read as containment.

        This pins the boundary in the return value and in the log, so the
        retracted claim cannot come back through the code while the docs stay
        corrected.
        """
        client._http.request.side_effect = [
            make_response(200, {"records": [{"uuid": "svm-uuid-123", "name": "svm-prod"}]}),
            make_response(200, {"records": []}),
            make_response(201, {}),
        ]

        with caplog.at_level(logging.WARNING):
            result = client.block_smb_user(
                svm_name="svm-prod", domain="CORP", username="jdoe"
            )

        assert result["effective_on_security_styles"] == ["unix", "mixed"]
        assert result["ineffective_on_security_styles"] == ["ntfs"]
        # "blocked" is the mapping's existence. Denial is a separate question
        # and is explicitly unanswered.
        assert result["mapping_created"] is True
        assert result["access_denial_verified"] is False

        # The caller cannot see the volumes from here, so silence would read as
        # "effective". The warning fires on every call.
        assert "ntfs" in caplog.text.lower()
        assert "has not been verified" in caplog.text

    def test_block_smb_user_ad_joined_svm(self, client):
        """AD-joined SVM uses 'nobody' replacement instead of space."""
        client._http.request.side_effect = [
            make_response(200, {"records": [{"uuid": "svm-uuid-123", "name": "svm-ad"}]}),
            # CIFS services check: AD-joined (has CIFS service)
            make_response(200, {"records": [{"name": "ADSERVER"}]}),
            make_response(201, {}),
        ]

        result = client.block_smb_user(
            svm_name="svm-ad",
            domain="DEMO",
            username="jdoe",
        )

        assert result["status"] == "blocked"
        # Verify replacement is "nobody" for AD-joined SVMs
        post_call = client._http.request.call_args_list[2]
        body = json.loads(post_call[1]["body"])
        assert body["replacement"] == "nobody"

    def test_block_smb_user_svm_not_found(self, client):
        """Raise error when SVM does not exist."""
        client._http.request.return_value = make_response(
            200, {"records": []}
        )

        with pytest.raises(OntapResponseError, match="SVM not found"):
            client.block_smb_user(
                svm_name="nonexistent",
                domain="CORP",
                username="jdoe",
            )

    def test_block_smb_user_api_error(self, client):
        """Handle ONTAP API errors gracefully."""
        client._http.request.side_effect = [
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            make_response(409, {"error": {"message": "Name mapping already exists"}}),
        ]

        with pytest.raises(OntapResponseError, match="HTTP 409"):
            client.block_smb_user(
                svm_name="svm-prod",
                domain="CORP",
                username="jdoe",
            )


class TestUnblockSmbUser:
    """Tests for unblock_smb_user method."""

    def test_unblock_smb_user_success(self, client):
        """Remove SMB user block."""
        client._http.request.side_effect = [
            # SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            # Find mapping
            make_response(200, {"records": [{"index": 1}]}),
            # Delete mapping
            make_response(200, {}),
        ]

        result = client.unblock_smb_user(
            svm_name="svm-prod",
            domain="CORP",
            username="jdoe",
        )

        assert result["status"] == "unblocked"
        assert result["entries_removed"] == 1

    def test_unblock_smb_user_not_found(self, client):
        """Return not_found when no mapping exists."""
        client._http.request.side_effect = [
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            make_response(200, {"records": []}),
        ]

        result = client.unblock_smb_user(
            svm_name="svm-prod",
            domain="CORP",
            username="nobody",
        )

        assert result["status"] == "not_found"


# ==========================================================================
# NFS IP Blocking Tests
# ==========================================================================


class TestBlockNfsIp:
    """Tests for block_nfs_ip method."""

    def test_block_nfs_ip_success(self, client):
        """Block an IP via export-policy rule."""
        client._http.request.side_effect = [
            # Find export policy
            make_response(200, {"records": [{"id": 42}]}),
            # Create rule
            make_response(201, {}),
        ]

        result = client.block_nfs_ip(
            svm_name="svm-prod",
            policy_name="default",
            client_ip="10.0.5.99",
        )

        assert result["action"] == "block_nfs_ip"
        assert result["status"] == "blocked"
        assert result["client_ip"] == "10.0.5.99"
        assert result["marker"] == RESPONSE_MARKER

        # Verify the rule body includes our marker
        post_call = client._http.request.call_args_list[1]
        body = json.loads(post_call[1]["body"])
        assert body["ro_rule"] == ["never"]
        assert body["rw_rule"] == ["never"]
        assert RESPONSE_MARKER in body["clients"][0]["match"]

    def test_block_nfs_ip_policy_not_found(self, client):
        """Raise error when export policy does not exist."""
        client._http.request.return_value = make_response(
            200, {"records": []}
        )

        with pytest.raises(OntapResponseError, match="Export policy.*not found"):
            client.block_nfs_ip(
                svm_name="svm-prod",
                policy_name="nonexistent",
                client_ip="10.0.5.99",
            )


class TestUnblockNfsIp:
    """Tests for unblock_nfs_ip method."""

    def test_unblock_nfs_ip_success(self, client):
        """Remove NFS IP block."""
        client._http.request.side_effect = [
            # Find export policy
            make_response(200, {"records": [{"id": 42}]}),
            # List rules
            make_response(200, {"records": [
                {"index": 1, "clients": [{"match": f"{RESPONSE_MARKER},10.0.5.99"}]},
                {"index": 2, "clients": [{"match": "0.0.0.0/0"}]},
            ]}),
            # Delete rule index 1
            make_response(200, {}),
        ]

        result = client.unblock_nfs_ip(
            svm_name="svm-prod",
            policy_name="default",
            client_ip="10.0.5.99",
        )

        assert result["status"] == "unblocked"
        assert result["rules_removed"] == 1

    def test_unblock_nfs_ip_not_found(self, client):
        """Return not_found when no matching rule exists."""
        client._http.request.side_effect = [
            make_response(200, {"records": [{"id": 42}]}),
            make_response(200, {"records": [
                {"index": 1, "clients": [{"match": "0.0.0.0/0"}]},
            ]}),
        ]

        result = client.unblock_nfs_ip(
            svm_name="svm-prod",
            policy_name="default",
            client_ip="10.0.5.99",
        )

        assert result["status"] == "not_found"
        assert result["rules_removed"] == 0


# ==========================================================================
# Snapshot Tests
# ==========================================================================


class TestCreateSnapshot:
    """Tests for create_snapshot method."""

    def test_create_snapshot_success(self, client):
        """Create a protective snapshot."""
        client._http.request.side_effect = [
            # Volume lookup
            make_response(200, {"records": [{"uuid": "vol-uuid-456"}]}),
            # Cooldown check — no prior snapshots
            make_response(200, {"records": []}),
            # Create snapshot
            make_response(201, {}),
        ]

        result = client.create_snapshot(
            svm_name="svm-prod",
            volume_name="vol1",
            comment="Test snapshot",
        )

        assert result["action"] == "create_snapshot"
        assert result["status"] == "created"
        assert "incident_response_" in result["snapshot_name"]

    def test_create_snapshot_cooldown_active(self, client):
        """Skip snapshot when cooldown is active."""
        recent_time = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat()

        client._http.request.side_effect = [
            # Volume lookup
            make_response(200, {"records": [{"uuid": "vol-uuid-456"}]}),
            # Cooldown check — recent snapshot exists
            make_response(200, {"records": [
                {"name": "incident_response_20260708_120000", "create_time": recent_time}
            ]}),
        ]

        result = client.create_snapshot(
            svm_name="svm-prod",
            volume_name="vol1",
            cooldown_minutes=15,
        )

        assert result["status"] == "skipped"
        assert "cooldown active" in result["reason"]

    def test_create_snapshot_cooldown_expired(self, client):
        """Create snapshot when cooldown has expired."""
        old_time = (
            datetime.now(timezone.utc) - timedelta(minutes=30)
        ).isoformat()

        client._http.request.side_effect = [
            # Volume lookup
            make_response(200, {"records": [{"uuid": "vol-uuid-456"}]}),
            # Cooldown check — old snapshot
            make_response(200, {"records": [
                {"name": "incident_response_old", "create_time": old_time}
            ]}),
            # Create snapshot
            make_response(201, {}),
        ]

        result = client.create_snapshot(
            svm_name="svm-prod",
            volume_name="vol1",
            cooldown_minutes=15,
        )

        assert result["status"] == "created"

    def test_create_snapshot_cooldown_disabled(self, client):
        """Create snapshot immediately when cooldown is 0."""
        client._http.request.side_effect = [
            # Volume lookup
            make_response(200, {"records": [{"uuid": "vol-uuid-456"}]}),
            # Create snapshot (no cooldown check)
            make_response(201, {}),
        ]

        result = client.create_snapshot(
            svm_name="svm-prod",
            volume_name="vol1",
            cooldown_minutes=0,
        )

        assert result["status"] == "created"

    def test_create_snapshot_volume_not_found(self, client):
        """Raise error when volume does not exist."""
        client._http.request.return_value = make_response(
            200, {"records": []}
        )

        with pytest.raises(OntapResponseError, match="Volume.*not found"):
            client.create_snapshot(
                svm_name="svm-prod",
                volume_name="nonexistent",
            )


# ==========================================================================
# CIFS Session Disconnect Tests
# ==========================================================================


class TestDisconnectSmbSessions:
    """Tests for disconnect_smb_sessions method."""

    def test_disconnect_sessions_by_user(self, client):
        """Disconnect sessions for a specific user."""
        client._http.request.side_effect = [
            # SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            # List sessions
            make_response(200, {"records": [
                {"identifier": 1001, "connection_id": 5001},
                {"identifier": 1002, "connection_id": 5002},
            ]}),
            # Delete session 1
            make_response(200, {}),
            # Delete session 2
            make_response(200, {}),
        ]

        result = client.disconnect_smb_sessions(
            svm_name="svm-prod",
            user="CORP\\jdoe",
        )

        assert result["status"] == "disconnected"
        assert result["disconnected"] == 2
        assert result["total_sessions"] == 2

    def test_disconnect_sessions_no_active(self, client):
        """Handle case where no sessions are active."""
        client._http.request.side_effect = [
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            make_response(200, {"records": []}),
        ]

        result = client.disconnect_smb_sessions(
            svm_name="svm-prod",
            user="CORP\\ghost",
        )

        assert result["status"] == "no_sessions"
        assert result["disconnected"] == 0

    def test_disconnect_requires_user_or_ip(self, client):
        """Raise error when neither user nor IP is provided."""
        with pytest.raises(OntapResponseError, match="At least one"):
            client.disconnect_smb_sessions(svm_name="svm-prod")


# ==========================================================================
# Composite Containment Tests
# ==========================================================================


class TestContainSmbThreat:
    """Tests for contain_smb_threat composite action."""

    def test_contain_smb_threat_full_sequence(self, client):
        """Execute full containment: snapshot + block + disconnect."""
        client._http.request.side_effect = [
            # create_snapshot: volume lookup
            make_response(200, {"records": [{"uuid": "vol-uuid-456"}]}),
            # create_snapshot: cooldown check
            make_response(200, {"records": []}),
            # create_snapshot: create
            make_response(201, {}),
            # block_smb_user: SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            # block_smb_user: CIFS services check (not AD-joined)
            make_response(200, {"records": []}),
            # block_smb_user: create mapping
            make_response(201, {}),
            # disconnect: SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            # disconnect: list sessions
            make_response(200, {"records": [
                {"identifier": 1001, "connection_id": 5001},
            ]}),
            # disconnect: delete session
            make_response(200, {}),
        ]

        result = client.contain_smb_threat(
            svm_name="svm-prod",
            domain="CORP",
            username="jdoe",
            volume_name="vol1",
            reason="ARP detection",
        )

        assert result["status"] == "contained"
        assert len(result["steps"]) == 3
        assert result["steps"][0]["action"] == "create_snapshot"
        assert result["steps"][1]["action"] == "block_smb_user"
        assert result["steps"][2]["action"] == "disconnect_smb_sessions"

    def test_contain_smb_threat_partial_failure(self, client):
        """Report partial failure when one step fails."""
        client._http.request.side_effect = [
            # create_snapshot: volume not found
            make_response(200, {"records": []}),
            # block_smb_user: SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            # block_smb_user: CIFS services check (not AD-joined)
            make_response(200, {"records": []}),
            # block_smb_user: create mapping
            make_response(201, {}),
            # disconnect: SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            # disconnect: no sessions
            make_response(200, {"records": []}),
        ]

        result = client.contain_smb_threat(
            svm_name="svm-prod",
            domain="CORP",
            username="jdoe",
            volume_name="nonexistent",
        )

        assert result["status"] == "partial_failure"
        # Snapshot failed but block and disconnect succeeded
        failed_steps = [s for s in result["steps"] if s.get("status") == "failed"]
        assert len(failed_steps) == 1


class TestContainNfsThreat:
    """Tests for contain_nfs_threat composite action."""

    def test_contain_nfs_threat_success(self, client):
        """Execute NFS containment: snapshot + IP block."""
        client._http.request.side_effect = [
            # create_snapshot: volume lookup
            make_response(200, {"records": [{"uuid": "vol-uuid-456"}]}),
            # create_snapshot: cooldown check
            make_response(200, {"records": []}),
            # create_snapshot: create
            make_response(201, {}),
            # block_nfs_ip: find policy
            make_response(200, {"records": [{"id": 42}]}),
            # block_nfs_ip: create rule
            make_response(201, {}),
        ]

        result = client.contain_nfs_threat(
            svm_name="svm-prod",
            client_ip="10.0.5.99",
            volume_name="vol1",
            reason="Mass deletion detected",
        )

        assert result["status"] == "contained"
        assert len(result["steps"]) == 2


# ==========================================================================
# Error Handling Tests
# ==========================================================================


class TestErrorHandling:
    """Tests for error handling and edge cases."""

    def test_ontap_response_error_attributes(self):
        """OntapResponseError carries status code and detail."""
        err = OntapResponseError("Test error", status_code=403, detail="Access denied")
        assert str(err) == "Test error"
        assert err.status_code == 403
        assert err.detail == "Access denied"

    def test_api_timeout_handling(self, client):
        """Handle network timeout gracefully."""
        import urllib3.exceptions

        client._http.request.side_effect = urllib3.exceptions.TimeoutError()

        with pytest.raises(urllib3.exceptions.TimeoutError):
            client.create_snapshot(
                svm_name="svm-prod",
                volume_name="vol1",
            )

    def test_malformed_json_response(self, client):
        """Handle non-JSON response from ONTAP."""
        resp = MagicMock()
        resp.status = 200
        resp.data = b"not json"
        client._http.request.return_value = resp

        # Should not crash — falls back to raw parsing
        with pytest.raises(OntapResponseError):
            # This will fail on "records" key access, not on JSON parse
            client.block_smb_user(
                svm_name="svm-prod",
                domain="CORP",
                username="jdoe",
            )



# ==========================================================================
# Input Validation Tests (Security Architect review finding)
# ==========================================================================


class TestInputValidation:
    """Tests for input validation and protected account enforcement."""

    def test_block_protected_account_fsxadmin(self, client):
        """Cannot block fsxadmin (protected account)."""
        with pytest.raises(OntapResponseError, match="Cannot block protected account"):
            client.block_smb_user(
                svm_name="svm-prod",
                domain="CORP",
                username="fsxadmin",
            )

    def test_block_protected_account_administrator(self, client):
        """Cannot block administrator (protected account)."""
        with pytest.raises(OntapResponseError, match="Cannot block protected account"):
            client.block_smb_user(
                svm_name="svm-prod",
                domain="CORP",
                username="Administrator",
            )

    def test_block_empty_username(self, client):
        """Cannot block empty username."""
        with pytest.raises(OntapResponseError, match="Invalid username"):
            client.block_smb_user(
                svm_name="svm-prod",
                domain="CORP",
                username="",
            )

    def test_block_username_with_injection(self, client):
        """Cannot block username with dangerous characters."""
        with pytest.raises(OntapResponseError, match="dangerous character"):
            client.block_smb_user(
                svm_name="svm-prod",
                domain="CORP",
                username="jdoe;rm -rf /",
            )

    def test_block_invalid_ip(self, client):
        """Cannot block invalid IP address."""
        with pytest.raises(OntapResponseError, match="Invalid IP"):
            client.block_nfs_ip(
                svm_name="svm-prod",
                policy_name="default",
                client_ip="not-an-ip",
            )

    def test_block_empty_ip(self, client):
        """Cannot block empty IP address."""
        with pytest.raises(OntapResponseError, match="IP address is required"):
            client.block_nfs_ip(
                svm_name="svm-prod",
                policy_name="default",
                client_ip="",
            )

    def test_block_ip_out_of_range(self, client):
        """Cannot block IP with octets > 255."""
        with pytest.raises(OntapResponseError, match="Invalid IP"):
            client.block_nfs_ip(
                svm_name="svm-prod",
                policy_name="default",
                client_ip="999.0.0.1",
            )


# ==========================================================================
# List Active Blocks Tests (Operations review finding)
# ==========================================================================


class TestListActiveBlocks:
    """Tests for list_active_blocks method."""

    def test_list_active_blocks_with_results(self, client):
        """List blocks when SMB and NFS blocks exist."""
        client._http.request.side_effect = [
            # SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            # SMB blocks (name-mappings — filtered client-side for space/nobody)
            make_response(200, {"records": [
                {"pattern": "CORP\\\\jdoe", "index": 1, "replacement": " "},
            ]}),
            # Export policies
            make_response(200, {"records": [{"id": 42, "name": "default"}]}),
            # Rules for policy 42
            make_response(200, {"records": [
                {"index": 1, "clients": [{"match": f"{RESPONSE_MARKER},10.0.5.99"}]},
            ]}),
        ]

        result = client.list_active_blocks(svm_name="svm-prod")

        assert result["total"] == 2
        assert len(result["smb_blocks"]) == 1
        assert len(result["nfs_blocks"]) == 1
        assert result["smb_blocks"][0]["pattern"] == "CORP\\\\jdoe"
        assert "10.0.5.99" in result["nfs_blocks"][0]["client_match"]

    def test_list_active_blocks_empty(self, client):
        """List blocks when no blocks exist."""
        client._http.request.side_effect = [
            # SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123"}]}),
            # No SMB blocks
            make_response(200, {"records": []}),
            # Export policies
            make_response(200, {"records": [{"id": 42, "name": "default"}]}),
            # No rules with marker
            make_response(200, {"records": [
                {"index": 1, "clients": [{"match": "0.0.0.0/0"}]},
            ]}),
        ]

        result = client.list_active_blocks(svm_name="svm-prod")

        assert result["total"] == 0
        assert len(result["smb_blocks"]) == 0
        assert len(result["nfs_blocks"]) == 0



class TestConfigurableProtectedAccounts:
    """Tests for PROTECTED_ACCOUNTS_EXTRA environment variable."""

    def test_extra_protected_accounts_from_env(self, client, monkeypatch):
        """Service accounts added via env var are protected."""
        monkeypatch.setenv("PROTECTED_ACCOUNTS_EXTRA", "svc-ml-pipeline,svc-backup")

        # Reload module to pick up env var
        import importlib
        import ontap_response
        importlib.reload(ontap_response)

        # Create new client after reload
        from ontap_response import OntapResponseClient, OntapResponseError
        c = OntapResponseClient(mgmt_ip="10.0.1.100", username="admin", password="pass")

        with pytest.raises(OntapResponseError, match="Cannot block protected account"):
            c.block_smb_user(svm_name="svm-prod", domain="CORP", username="svc-ml-pipeline")

        # Clean up: reload without the env var
        monkeypatch.delenv("PROTECTED_ACCOUNTS_EXTRA", raising=False)
        importlib.reload(ontap_response)



class TestHealthCheck:
    """Tests for health_check method."""

    def test_health_check_success(self, client):
        """Health check returns healthy when ONTAP is reachable."""
        client._http.request.side_effect = [
            # Cluster version query
            make_response(200, {"version": {"full": "NetApp Release 9.14.1"}}),
            # SVM lookup
            make_response(200, {"records": [{"uuid": "svm-uuid-123", "name": "svm-prod"}]}),
        ]

        result = client.health_check(svm_name="svm-prod")

        assert result["status"] == "healthy"
        assert result["api_reachable"] is True
        assert "9.14.1" in result["ontap_version"]
        assert result["svm_uuid"] == "svm-uuid-123"

    def test_health_check_svm_not_found(self, client):
        """Health check returns unhealthy when SVM doesn't exist."""
        client._http.request.side_effect = [
            make_response(200, {"version": {"full": "NetApp Release 9.14.1"}}),
            make_response(200, {"records": []}),  # SVM not found
        ]

        result = client.health_check(svm_name="nonexistent")

        assert result["status"] == "unhealthy"
        assert result["api_reachable"] is True  # API worked, just SVM not found

    def test_health_check_unreachable(self, client):
        """Health check returns unreachable on network error."""
        import urllib3.exceptions
        client._http.request.side_effect = urllib3.exceptions.TimeoutError()

        result = client.health_check(svm_name="svm-prod")

        assert result["status"] == "unreachable"
        assert result["api_reachable"] is False
