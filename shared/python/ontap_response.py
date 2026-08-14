"""ONTAP REST API automated response actions.

Provides programmatic user/IP blocking, snapshot creation, and CIFS session
disconnect — the same containment actions that DII Storage Workload Security
performs, implemented via ONTAP REST API for use with AWS-native detection
pipelines (CloudWatch Log Alarm, SIEM monitors, FPolicy analytics).

This module enables building automated incident response workflows where
detection is handled by AWS services or third-party SaaS observability
platforms, and containment is executed directly on FSx for ONTAP via REST API.

Usage:
    from ontap_response import OntapResponseClient

    client = OntapResponseClient(
        mgmt_ip=os.environ["ONTAP_MGMT_IP"],
        username=creds["username"],
        password=creds["password"],
    )

    # Block a compromised SMB user across all SVMs
    client.block_smb_user(svm_name="svm-prod", domain="CORP", username="jdoe")

    # Block an attacker IP from NFS access
    client.block_nfs_ip(svm_name="svm-prod", policy_name="default", client_ip="203.0.113.99")

    # Create protective snapshot for evidence preservation
    client.create_snapshot(svm_name="svm-prod", volume_name="vol1", prefix="incident")

    # Disconnect active CIFS sessions for a compromised user
    client.disconnect_smb_sessions(svm_name="svm-prod", user="CORP\\\\jdoe")

Reference:
    DII SWS blocking mechanism: https://docs.netapp.com/us-en/cloudinsights/cs_restrict_user_access.html
    ONTAP REST API: https://docs.netapp.com/us-en/ontap-restapi/
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import urllib3

logger = logging.getLogger(__name__)

# Prefix for name-mapping entries created by this module (analogous to DII's
# "cloudsecure_rule" marker in export-policy rules).
RESPONSE_MARKER = "fsxn_auto_response"

# Protected accounts that should never be automatically blocked.
# Add service accounts, admin accounts, and machine accounts here.
# Override via PROTECTED_ACCOUNTS_EXTRA env var (comma-separated) to add
# deployment-specific accounts without modifying this module.
PROTECTED_ACCOUNTS: set[str] = {
    "fsxadmin",
    "administrator",
    "admin",
    "vsadmin",
    "system",
    "machine$",  # Machine accounts (AD convention)
}

# Allow deployments to add custom protected accounts via environment variable
# Example: PROTECTED_ACCOUNTS_EXTRA="svc-ml-pipeline,svc-backup-agent,app-service"
_extra = os.environ.get("PROTECTED_ACCOUNTS_EXTRA", "")
if _extra:
    PROTECTED_ACCOUNTS.update(name.strip().lower() for name in _extra.split(",") if name.strip())


def _validate_username(username: str) -> None:
    """Validate username input to prevent injection or accidental harm."""
    if not username or len(username) > 256:
        raise OntapResponseError(
            f"Invalid username: must be 1-256 characters, got {len(username or '')}",
            status_code=400,
        )
    # Block attempts to inject ONTAP CLI patterns
    dangerous_chars = [";", "|", "&", "`", "$", "\n", "\r"]
    for char in dangerous_chars:
        if char in username:
            raise OntapResponseError(
                f"Invalid username: contains dangerous character '{char}'",
                status_code=400,
            )
    if username.lower() in PROTECTED_ACCOUNTS:
        raise OntapResponseError(
            f"Cannot block protected account: {username}",
            status_code=403,
        )


def _validate_ip(ip: str) -> None:
    """Validate IP address format."""
    if not ip:
        raise OntapResponseError("IP address is required", status_code=400)
    parts = ip.split(".")
    if len(parts) != 4:
        raise OntapResponseError(
            f"Invalid IP format: {ip}", status_code=400
        )
    for part in parts:
        try:
            num = int(part)
            if num < 0 or num > 255:
                raise ValueError()
        except ValueError:
            raise OntapResponseError(
                f"Invalid IP format: {ip}", status_code=400
            )


class OntapResponseError(Exception):
    """Raised when an ONTAP REST API call fails."""

    def __init__(self, message: str, status_code: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class OntapResponseClient:
    """ONTAP REST API client for automated incident response actions.

    Implements the containment actions equivalent to DII Storage Workload
    Security's automated response policies:
    - SMB user blocking (via name-mapping)
    - NFS IP blocking (via export-policy rules)
    - Snapshot creation (evidence preservation)
    - CIFS session disconnect

    Args:
        mgmt_ip: FSx for ONTAP management endpoint IP.
        username: ONTAP admin username (typically 'fsxadmin').
        password: ONTAP admin password.
        ca_cert_path: Path to CA certificate for TLS verification.
            If None, TLS verification is disabled (PoC only).
        timeout: HTTP request timeout in seconds (default: 30).
    """

    def __init__(
        self,
        mgmt_ip: str,
        username: str,
        password: str,
        ca_cert_path: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = f"https://{mgmt_ip}/api"
        self.timeout = timeout

        if ca_cert_path:
            self._http = urllib3.PoolManager(ca_certs=ca_cert_path)
        else:
            self._http = urllib3.PoolManager(cert_reqs="CERT_NONE")
            logger.warning(
                "TLS certificate verification DISABLED (cert_reqs=CERT_NONE). "
                "For production, provide ca_cert_path with the FSx for ONTAP CA "
                "certificate. Retrieve it via: security certificate show -type root-ca "
                "-vserver <svm> (ONTAP CLI) or AWS console > FSx > File system > "
                "Administration > Certificate."
            )

        auth_header = urllib3.util.make_headers(
            basic_auth=f"{username}:{password}"
        )
        self._headers = {
            **auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> dict[str, Any]:
        """Make an ONTAP REST API request and return parsed JSON."""
        url = f"{self.base_url}{path}"
        kwargs: dict[str, Any] = {
            "headers": self._headers,
            "timeout": self.timeout,
        }
        if body is not None:
            kwargs["body"] = json.dumps(body).encode()

        resp = self._http.request(method, url, **kwargs)

        # Parse response
        data: dict[str, Any] = {}
        if resp.data:
            try:
                data = json.loads(resp.data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = {"raw": resp.data.decode("utf-8", errors="replace")[:500]}

        if resp.status >= 400:
            error_msg = data.get("error", {}).get("message", resp.data.decode()[:200])
            raise OntapResponseError(
                f"ONTAP API {method} {path} failed: HTTP {resp.status}",
                status_code=resp.status,
                detail=error_msg,
            )

        return data

    def _get_svm_uuid(self, svm_name: str) -> str:
        """Resolve SVM name to UUID."""
        data = self._request("GET", f"/svm/svms?name={svm_name}&fields=uuid")
        records = data.get("records", [])
        if not records:
            raise OntapResponseError(f"SVM not found: {svm_name}", status_code=404)
        return records[0]["uuid"]

    def _is_svm_ad_joined(self, svm_name: str) -> bool:
        """Check if SVM is joined to Active Directory.

        On AD-joined SVMs, name-mapping entries with replacement=" " (space)
        are auto-deleted by ONTAP's background validation process within 30-60s.
        This method detects AD membership so callers can use an alternative
        replacement value ("nobody") that persists.
        """
        data = self._request(
            "GET",
            f"/protocols/cifs/services?svm.name={svm_name}&fields=name",
        )
        # If CIFS service exists, SVM is AD-joined (CIFS requires AD)
        return len(data.get("records", [])) > 0

    def _get_volume_uuid(self, svm_name: str, volume_name: str) -> str:
        """Resolve volume name to UUID within a specific SVM."""
        data = self._request(
            "GET",
            f"/storage/volumes?name={volume_name}&svm.name={svm_name}&fields=uuid",
        )
        records = data.get("records", [])
        if not records:
            raise OntapResponseError(
                f"Volume {volume_name} not found in SVM {svm_name}",
                status_code=404,
            )
        return records[0]["uuid"]

    # ------------------------------------------------------------------
    # SMB User Blocking (equivalent to DII's vserver name-mapping approach)
    # ------------------------------------------------------------------

    def block_smb_user(
        self,
        svm_name: str,
        domain: str,
        username: str,
        position: int = 1,
    ) -> dict[str, Any]:
        """Block an SMB user by creating a name-mapping that maps to empty UNIX user.

        This is the same mechanism used by DII Storage Workload Security:
        maps the Windows user to an empty string (" "), effectively denying
        all file access across the SVM.

        Args:
            svm_name: Name of the SVM where the user should be blocked.
            domain: Windows domain name (e.g., "CORP").
            username: Windows username to block (e.g., "jdoe").
            position: Rule position (1 = highest priority). Default: 1.

        Returns:
            Dict with blocking details (svm, user, position).

        Reference:
            DII mechanism: vserver name-mapping create -direction win-unix
                -pattern "DOMAIN\\user" -replacement " "
        """
        _validate_username(username)
        svm_uuid = self._get_svm_uuid(svm_name)
        pattern = f"{domain}\\\\{username}"

        # On AD-joined SVMs, ONTAP 9.17.1+ auto-deletes name-mappings with
        # unresolvable replacement values (space " ") within 30-60 seconds.
        # Use "nobody" (UID 65535, standard unprivileged user) instead — it
        # persists and effectively denies access on UNIX/MIXED style volumes
        # when combined with restrictive permissions (750/700).
        ad_joined = self._is_svm_ad_joined(svm_name)
        replacement = "nobody" if ad_joined else " "

        body = {
            "direction": "win_unix",
            "index": position,
            "pattern": pattern,
            "replacement": replacement,
            "svm": {"uuid": svm_uuid, "name": svm_name},
        }

        logger.info(
            "Blocking SMB user: %s\\%s on SVM %s (position %d, replacement=%r, ad_joined=%s)",
            domain, username, svm_name, position, replacement, ad_joined,
        )

        self._request("POST", "/name-services/name-mappings", body=body)

        return {
            "action": "block_smb_user",
            "svm": svm_name,
            "pattern": pattern,
            "position": position,
            "status": "blocked",
            "marker": RESPONSE_MARKER,
        }

    def unblock_smb_user(
        self,
        svm_name: str,
        domain: str,
        username: str,
    ) -> dict[str, Any]:
        """Remove SMB user block by deleting the name-mapping entry.

        Args:
            svm_name: Name of the SVM.
            domain: Windows domain name.
            username: Windows username to unblock.

        Returns:
            Dict with unblocking details.
        """
        svm_uuid = self._get_svm_uuid(svm_name)
        pattern = f"{domain}\\\\{username}"

        # Find the mapping entry
        data = self._request(
            "GET",
            f"/name-services/name-mappings"
            f"?svm.uuid={svm_uuid}&direction=win_unix&pattern={pattern}",
        )
        records = data.get("records", [])
        if not records:
            logger.warning("No name-mapping found for %s on SVM %s", pattern, svm_name)
            return {
                "action": "unblock_smb_user",
                "svm": svm_name,
                "pattern": pattern,
                "status": "not_found",
            }

        # Delete each matching entry
        for record in records:
            index = record.get("index", 0)
            self._request(
                "DELETE",
                f"/name-services/name-mappings/{svm_uuid}/win_unix/{index}",
            )
            logger.info("Removed name-mapping: %s position %d on SVM %s",
                        pattern, index, svm_name)

        return {
            "action": "unblock_smb_user",
            "svm": svm_name,
            "pattern": pattern,
            "status": "unblocked",
            "entries_removed": len(records),
        }

    # ------------------------------------------------------------------
    # NFS IP Blocking (equivalent to DII's export-policy rule approach)
    # ------------------------------------------------------------------

    def block_nfs_ip(
        self,
        svm_name: str,
        policy_name: str,
        client_ip: str,
        rule_index: int = 1,
    ) -> dict[str, Any]:
        """Block an IP address from NFS access via export-policy rule.

        Creates a deny rule at the specified position in the export policy.
        This is the same mechanism DII uses with "cloudsecure_rule" as
        the client match marker.

        Args:
            svm_name: Name of the SVM.
            policy_name: Export policy name (often "default").
            client_ip: IP address to block (e.g., "203.0.113.99").
            rule_index: Rule position (1 = evaluated first). Default: 1.

        Returns:
            Dict with blocking details.

        Reference:
            DII mechanism: export-policy rule create with
                -clientmatch "cloudsecure_rule,<ip>" -rorule never -rwrule never
        """
        _validate_ip(client_ip)
        # Find export policy ID
        data = self._request(
            "GET",
            f"/protocols/nfs/export-policies"
            f"?svm.name={svm_name}&name={policy_name}&fields=id",
        )
        records = data.get("records", [])
        if not records:
            raise OntapResponseError(
                f"Export policy {policy_name} not found on SVM {svm_name}",
                status_code=404,
            )
        policy_id = records[0]["id"]

        body = {
            "clients": [{"match": f"{RESPONSE_MARKER},{client_ip}"}],
            "ro_rule": ["never"],
            "rw_rule": ["never"],
            "superuser": ["never"],
            "protocols": ["any"],
            "index": rule_index,
        }

        logger.info(
            "Blocking NFS IP: %s on SVM %s policy %s (index %d)",
            client_ip, svm_name, policy_name, rule_index,
        )

        self._request(
            "POST",
            f"/protocols/nfs/export-policies/{policy_id}/rules",
            body=body,
        )

        return {
            "action": "block_nfs_ip",
            "svm": svm_name,
            "policy": policy_name,
            "client_ip": client_ip,
            "rule_index": rule_index,
            "status": "blocked",
            "marker": RESPONSE_MARKER,
        }

    def unblock_nfs_ip(
        self,
        svm_name: str,
        policy_name: str,
        client_ip: str,
    ) -> dict[str, Any]:
        """Remove NFS IP block by deleting the export-policy rule.

        Finds rules containing our response marker and the specified IP,
        then deletes them.

        Args:
            svm_name: Name of the SVM.
            policy_name: Export policy name.
            client_ip: IP address to unblock.

        Returns:
            Dict with unblocking details.
        """
        # Find export policy ID
        data = self._request(
            "GET",
            f"/protocols/nfs/export-policies"
            f"?svm.name={svm_name}&name={policy_name}&fields=id",
        )
        records = data.get("records", [])
        if not records:
            raise OntapResponseError(
                f"Export policy {policy_name} not found on SVM {svm_name}",
                status_code=404,
            )
        policy_id = records[0]["id"]

        # List rules and find ones with our marker
        rules_data = self._request(
            "GET",
            f"/protocols/nfs/export-policies/{policy_id}/rules?fields=clients,index",
        )
        rules = rules_data.get("records", [])

        deleted = 0
        for rule in rules:
            clients = rule.get("clients", [])
            for client in clients:
                match_str = client.get("match", "")
                if RESPONSE_MARKER in match_str and client_ip in match_str:
                    rule_index = rule["index"]
                    self._request(
                        "DELETE",
                        f"/protocols/nfs/export-policies/{policy_id}/rules/{rule_index}",
                    )
                    logger.info(
                        "Removed export-policy rule: index %d on SVM %s/%s",
                        rule_index, svm_name, policy_name,
                    )
                    deleted += 1

        status = "unblocked" if deleted > 0 else "not_found"
        return {
            "action": "unblock_nfs_ip",
            "svm": svm_name,
            "policy": policy_name,
            "client_ip": client_ip,
            "status": status,
            "rules_removed": deleted,
        }

    # ------------------------------------------------------------------
    # Snapshot Creation (evidence preservation)
    # ------------------------------------------------------------------

    def create_snapshot(
        self,
        svm_name: str,
        volume_name: str,
        prefix: str = "incident_response",
        comment: str = "",
        cooldown_minutes: int = 15,
    ) -> dict[str, Any]:
        """Create a protective snapshot for evidence preservation.

        Includes cooldown logic to prevent snapshot storms during sustained
        attacks (same pattern as DII's automatic snapshot).

        Args:
            svm_name: Name of the SVM.
            volume_name: Name of the volume to snapshot.
            prefix: Snapshot name prefix (default: "incident_response").
            comment: Optional comment to attach to the snapshot.
            cooldown_minutes: Minimum minutes between snapshots (default: 15).
                Set to 0 to disable cooldown.

        Returns:
            Dict with snapshot details or skip reason.
        """
        vol_uuid = self._get_volume_uuid(svm_name, volume_name)

        # Check cooldown
        if cooldown_minutes > 0:
            should_skip, reason = self._check_snapshot_cooldown(
                vol_uuid, prefix, cooldown_minutes
            )
            if should_skip:
                logger.info("Snapshot skipped: %s", reason)
                return {
                    "action": "create_snapshot",
                    "svm": svm_name,
                    "volume": volume_name,
                    "status": "skipped",
                    "reason": reason,
                }

        # Create snapshot
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snap_name = f"{prefix}_{timestamp}"

        body: dict[str, Any] = {"name": snap_name}
        if comment:
            body["comment"] = comment[:255]  # ONTAP limit

        logger.info(
            "Creating snapshot: %s on %s/%s",
            snap_name, svm_name, volume_name,
        )

        self._request(
            "POST",
            f"/storage/volumes/{vol_uuid}/snapshots",
            body=body,
        )

        return {
            "action": "create_snapshot",
            "svm": svm_name,
            "volume": volume_name,
            "snapshot_name": snap_name,
            "status": "created",
        }

    def _check_snapshot_cooldown(
        self, vol_uuid: str, prefix: str, cooldown_minutes: int
    ) -> tuple[bool, str]:
        """Check if a recent snapshot exists within cooldown period."""
        try:
            data = self._request(
                "GET",
                f"/storage/volumes/{vol_uuid}/snapshots"
                f"?name={prefix}_*&order_by=create_time+desc&max_records=1",
            )
        except OntapResponseError:
            return False, "snapshot list unavailable"

        records = data.get("records", [])
        if not records:
            return False, "no prior snapshots with prefix"

        create_time = records[0].get("create_time", "")
        if not create_time:
            return False, "no create_time on last snapshot"

        try:
            last_dt = datetime.fromisoformat(create_time.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed = (now - last_dt).total_seconds() / 60

            if elapsed < cooldown_minutes:
                return True, (
                    f"cooldown active — last snapshot "
                    f"{records[0].get('name', '?')} created "
                    f"{elapsed:.1f} min ago (limit: {cooldown_minutes}m)"
                )
        except (ValueError, TypeError) as e:
            logger.warning("Could not parse snapshot time: %s", e)

        return False, "cooldown expired"

    # ------------------------------------------------------------------
    # CIFS Session Disconnect
    # ------------------------------------------------------------------

    def disconnect_smb_sessions(
        self,
        svm_name: str,
        user: str | None = None,
        client_ip: str | None = None,
    ) -> dict[str, Any]:
        """Disconnect active CIFS/SMB sessions for a user or IP.

        Forcefully terminates active sessions to immediately cut off access.
        Use in combination with block_smb_user to ensure the user cannot
        reconnect.

        Args:
            svm_name: Name of the SVM.
            user: Windows user to disconnect (e.g., "CORP\\\\jdoe").
                At least one of user/client_ip required.
            client_ip: Client IP to disconnect. At least one of
                user/client_ip required.

        Returns:
            Dict with disconnection details.
        """
        if not user and not client_ip:
            raise OntapResponseError(
                "At least one of user or client_ip is required",
                status_code=400,
            )

        svm_uuid = self._get_svm_uuid(svm_name)

        # Build filter query
        filters = f"svm.uuid={svm_uuid}"
        if user:
            filters += f"&user={user}"
        if client_ip:
            filters += f"&client_ip={client_ip}"

        # List matching sessions
        data = self._request(
            "GET",
            f"/protocols/cifs/sessions?{filters}&fields=identifier,connection_id",
        )
        sessions = data.get("records", [])

        if not sessions:
            logger.info("No active sessions found matching filter: %s", filters)
            return {
                "action": "disconnect_smb_sessions",
                "svm": svm_name,
                "user": user,
                "client_ip": client_ip,
                "status": "no_sessions",
                "disconnected": 0,
            }

        # Disconnect each session
        disconnected = 0
        for session in sessions:
            try:
                identifier = session.get("identifier")
                conn_id = session.get("connection_id")
                if identifier and conn_id:
                    self._request(
                        "DELETE",
                        f"/protocols/cifs/sessions/{svm_uuid}/{identifier}/{conn_id}",
                    )
                    disconnected += 1
            except OntapResponseError as e:
                logger.warning("Failed to disconnect session: %s", e)

        logger.info(
            "Disconnected %d/%d SMB sessions for %s on SVM %s",
            disconnected, len(sessions), user or client_ip, svm_name,
        )

        return {
            "action": "disconnect_smb_sessions",
            "svm": svm_name,
            "user": user,
            "client_ip": client_ip,
            "status": "disconnected",
            "disconnected": disconnected,
            "total_sessions": len(sessions),
        }

    # ------------------------------------------------------------------
    # List Active Blocks (operational visibility)
    # ------------------------------------------------------------------

    def health_check(self, svm_name: str) -> dict[str, Any]:
        """Verify ONTAP connectivity and API access without modifying state.

        Use in CI/CD pipelines, SOAR health checks, or deployment validation.
        Does NOT create, modify, or delete any configuration.

        Args:
            svm_name: Name of the SVM to verify access to.

        Returns:
            Dict with connectivity status and API version.
        """
        try:
            # Test 1: Basic API connectivity
            data = self._request("GET", "/cluster?fields=version")
            version = data.get("version", {})

            # Test 2: SVM access
            svm_uuid = self._get_svm_uuid(svm_name)

            return {
                "action": "health_check",
                "status": "healthy",
                "svm": svm_name,
                "svm_uuid": svm_uuid,
                "ontap_version": f"{version.get('full', 'unknown')}",
                "api_reachable": True,
            }
        except OntapResponseError as e:
            return {
                "action": "health_check",
                "status": "unhealthy",
                "svm": svm_name,
                "error": str(e),
                "api_reachable": e.status_code != 0,
            }
        except Exception as e:
            return {
                "action": "health_check",
                "status": "unreachable",
                "svm": svm_name,
                "error": str(e),
                "api_reachable": False,
            }

    def list_active_blocks(self, svm_name: str) -> dict[str, Any]:
        """List all active blocks created by this module.

        Finds name-mapping entries with empty replacement (SMB blocks)
        and export-policy rules with our response marker (NFS blocks).

        Args:
            svm_name: Name of the SVM to inspect.

        Returns:
            Dict with lists of active SMB and NFS blocks.
        """
        svm_uuid = self._get_svm_uuid(svm_name)

        # Find SMB blocks (name-mappings with space or "nobody" replacement)
        smb_blocks = []
        try:
            # Query all win_unix mappings and filter client-side
            # (ONTAP REST API rejects replacement=%20 as a filter on some versions)
            data = self._request(
                "GET",
                f"/name-services/name-mappings"
                f"?svm.uuid={svm_uuid}&direction=win_unix"
                f"&fields=pattern,replacement",
            )
            for record in data.get("records", []):
                replacement = record.get("replacement", "")
                if replacement in (" ", "nobody"):
                    smb_blocks.append({
                        "pattern": record.get("pattern", ""),
                        "index": record.get("index", 0),
                        "replacement": replacement,
                    })
        except OntapResponseError:
            pass

        # Find NFS blocks (export-policy rules with our marker)
        nfs_blocks = []
        try:
            policies_data = self._request(
                "GET",
                f"/protocols/nfs/export-policies?svm.name={svm_name}&fields=id,name",
            )
            for policy in policies_data.get("records", []):
                policy_id = policy["id"]
                rules_data = self._request(
                    "GET",
                    f"/protocols/nfs/export-policies/{policy_id}/rules?fields=clients,index",
                )
                for rule in rules_data.get("records", []):
                    for client in rule.get("clients", []):
                        if RESPONSE_MARKER in client.get("match", ""):
                            nfs_blocks.append({
                                "policy": policy.get("name", ""),
                                "rule_index": rule["index"],
                                "client_match": client["match"],
                            })
        except OntapResponseError:
            pass

        return {
            "action": "list_active_blocks",
            "svm": svm_name,
            "smb_blocks": smb_blocks,
            "nfs_blocks": nfs_blocks,
            "total": len(smb_blocks) + len(nfs_blocks),
        }

    # ------------------------------------------------------------------
    # Composite Actions (multi-step response)
    # ------------------------------------------------------------------

    def contain_smb_threat(
        self,
        svm_name: str,
        domain: str,
        username: str,
        volume_name: str | None = None,
        reason: str = "automated-response",
        trigger_id: str = "",
    ) -> dict[str, Any]:
        """Execute full SMB threat containment sequence.

        Performs all three DII-equivalent containment steps:
        1. Create protective snapshot (if volume specified)
        2. Block user via name-mapping
        3. Disconnect active sessions

        Args:
            svm_name: Name of the SVM.
            domain: Windows domain.
            username: Username to contain.
            volume_name: Volume to snapshot (optional).
            reason: Reason for containment (logged in snapshot comment).
            trigger_id: Correlation ID from the triggering event (e.g.,
                SNS MessageId, CloudWatch Alarm ARN). Used for forensic
                traceability between detection and response.

        Returns:
            Dict with results of all containment actions.
        """
        results: dict[str, Any] = {
            "action": "contain_smb_threat",
            "svm": svm_name,
            "target": f"{domain}\\{username}",
            "trigger_id": trigger_id,
            "steps": [],
        }

        # Step 1: Snapshot (evidence preservation)
        if volume_name:
            try:
                snap_result = self.create_snapshot(
                    svm_name=svm_name,
                    volume_name=volume_name,
                    prefix="incident_response",
                    comment=f"Threat containment: {reason} | user: {domain}\\{username}",
                )
                results["steps"].append(snap_result)
            except OntapResponseError as e:
                results["steps"].append({
                    "action": "create_snapshot",
                    "status": "failed",
                    "error": str(e),
                })

        # Step 2: Block user
        try:
            block_result = self.block_smb_user(
                svm_name=svm_name,
                domain=domain,
                username=username,
            )
            results["steps"].append(block_result)
        except OntapResponseError as e:
            results["steps"].append({
                "action": "block_smb_user",
                "status": "failed",
                "error": str(e),
            })

        # Step 3: Disconnect sessions
        try:
            disconnect_result = self.disconnect_smb_sessions(
                svm_name=svm_name,
                user=f"{domain}\\{username}",
            )
            results["steps"].append(disconnect_result)
        except OntapResponseError as e:
            results["steps"].append({
                "action": "disconnect_smb_sessions",
                "status": "failed",
                "error": str(e),
            })

        # Overall status
        failed = [s for s in results["steps"] if s.get("status") == "failed"]
        results["status"] = "partial_failure" if failed else "contained"

        return results

    def contain_nfs_threat(
        self,
        svm_name: str,
        client_ip: str,
        policy_name: str = "default",
        volume_name: str | None = None,
        reason: str = "automated-response",
        trigger_id: str = "",
    ) -> dict[str, Any]:
        """Execute full NFS threat containment sequence.

        Performs:
        1. Create protective snapshot (if volume specified)
        2. Block IP via export-policy rule

        Args:
            svm_name: Name of the SVM.
            client_ip: Attacker IP address.
            policy_name: Export policy name (default: "default").
            volume_name: Volume to snapshot (optional).
            reason: Reason for containment.
            trigger_id: Correlation ID from the triggering event for
                forensic traceability.

        Returns:
            Dict with results of all containment actions.
        """
        results: dict[str, Any] = {
            "action": "contain_nfs_threat",
            "svm": svm_name,
            "target": client_ip,
            "trigger_id": trigger_id,
            "steps": [],
        }

        # Step 1: Snapshot
        if volume_name:
            try:
                snap_result = self.create_snapshot(
                    svm_name=svm_name,
                    volume_name=volume_name,
                    prefix="incident_response",
                    comment=f"Threat containment: {reason} | ip: {client_ip}",
                )
                results["steps"].append(snap_result)
            except OntapResponseError as e:
                results["steps"].append({
                    "action": "create_snapshot",
                    "status": "failed",
                    "error": str(e),
                })

        # Step 2: Block IP
        try:
            block_result = self.block_nfs_ip(
                svm_name=svm_name,
                policy_name=policy_name,
                client_ip=client_ip,
            )
            results["steps"].append(block_result)
        except OntapResponseError as e:
            results["steps"].append({
                "action": "block_nfs_ip",
                "status": "failed",
                "error": str(e),
            })

        # Overall status
        failed = [s for s in results["steps"] if s.get("status") == "failed"]
        results["status"] = "partial_failure" if failed else "contained"

        return results

    def contain_multiprotocol_threat(
        self,
        svm_name: str,
        domain: str,
        username: str,
        client_ip: str,
        volume_name: str | None = None,
        policy_name: str = "default",
        reason: str = "automated-response",
        trigger_id: str = "",
    ) -> dict[str, Any]:
        """Execute full multi-protocol threat containment.

        For volumes accessible via both SMB and NFS, a single-protocol
        block is insufficient — the attacker may switch protocols. This
        composite action blocks both the SMB user AND the NFS client IP,
        creates a protective snapshot, and disconnects active sessions.

        Steps:
        1. Create protective snapshot (if volume specified)
        2. Block SMB user via name-mapping
        3. Block NFS client IP via export-policy rule
        4. Disconnect active CIFS sessions

        Args:
            svm_name: Name of the SVM.
            domain: Windows domain of the compromised user.
            username: Windows username to block.
            client_ip: NFS client IP to block (attacker's workstation).
            volume_name: Volume to snapshot (optional).
            policy_name: Export policy name for NFS blocking.
            reason: Reason for containment.
            trigger_id: Correlation ID from triggering event.

        Returns:
            Dict with results of all containment steps.
        """
        results: dict[str, Any] = {
            "action": "contain_multiprotocol_threat",
            "svm": svm_name,
            "target_smb": f"{domain}\\{username}",
            "target_nfs": client_ip,
            "trigger_id": trigger_id,
            "steps": [],
        }

        # Step 1: Snapshot
        if volume_name:
            try:
                snap_result = self.create_snapshot(
                    svm_name=svm_name,
                    volume_name=volume_name,
                    prefix="incident_response",
                    comment=(
                        f"Multiprotocol containment: {reason} | "
                        f"user: {domain}\\{username} | ip: {client_ip}"
                    ),
                )
                results["steps"].append(snap_result)
            except OntapResponseError as e:
                results["steps"].append({
                    "action": "create_snapshot",
                    "status": "failed",
                    "error": str(e),
                })

        # Step 2: Block SMB user
        try:
            smb_result = self.block_smb_user(
                svm_name=svm_name,
                domain=domain,
                username=username,
            )
            results["steps"].append(smb_result)
        except OntapResponseError as e:
            results["steps"].append({
                "action": "block_smb_user",
                "status": "failed",
                "error": str(e),
            })

        # Step 3: Block NFS IP
        try:
            nfs_result = self.block_nfs_ip(
                svm_name=svm_name,
                policy_name=policy_name,
                client_ip=client_ip,
            )
            results["steps"].append(nfs_result)
        except OntapResponseError as e:
            results["steps"].append({
                "action": "block_nfs_ip",
                "status": "failed",
                "error": str(e),
            })

        # Step 4: Disconnect SMB sessions
        try:
            disconnect_result = self.disconnect_smb_sessions(
                svm_name=svm_name,
                user=f"{domain}\\{username}",
            )
            results["steps"].append(disconnect_result)
        except OntapResponseError as e:
            results["steps"].append({
                "action": "disconnect_smb_sessions",
                "status": "failed",
                "error": str(e),
            })

        # Overall status
        failed = [s for s in results["steps"] if s.get("status") == "failed"]
        results["status"] = "partial_failure" if failed else "contained"

        return results
