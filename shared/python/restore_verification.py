"""Verified-clean recovery point workflow for FSx for ONTAP.

Closes the CSF 2.0 RC.RP (Incident Recovery Plan Execution) gap documented
in the DII Capability Map: a protective snapshot existing is a *Protect*
artifact, not evidence that the recovery point is actually clean and
restorable. This module implements the missing step — verifying a snapshot
before treating it as a recovery candidate — using AWS-native services:

    1. Create a FlexClone volume from the candidate snapshot (ONTAP REST API).
       A FlexClone is a point-in-time, writable copy that shares data blocks
       with its parent, so verification never touches the production volume
       or the original protective snapshot.
    2. Attach a VPC-scoped S3 Access Point to the clone (boto3 fsx client).
       This exposes the clone's files via the S3 API without mounting NFS/SMB,
       so verification runs from a Lambda/Fargate task with no network path
       to the production data plane.
    3. Scan the clone's file listing (S3 ListObjectsV2 via the access point)
       for indicators that the snapshot itself captured an in-progress or
       completed encryption event: mass extension changes to known ransomware
       extensions, recently-touched-file bursts, and (optionally) per-file
       entropy sampling via GetObject range reads.
    4. Record a pass/fail verification verdict, then tear down the S3 Access
       Point and FlexClone — verification leaves no residual attack surface
       and consumes no persistent storage beyond the FlexClone's copy-on-write
       delta.

This does NOT replace ONTAP ARP (which detects ransomware during the attack).
It answers a different, later question: "is this specific snapshot, that we
are about to promote to a restore point, actually clean?" — the distinction
the DII Capability Map's Resilience-maturity lens note calls out explicitly.

Usage:
    from restore_verification import RestoreVerificationClient

    client = RestoreVerificationClient(
        mgmt_ip=os.environ["ONTAP_MGMT_IP"],
        username=creds["username"],
        password=creds["password"],
    )

    result = client.verify_snapshot(
        svm_name="svm-prod",
        volume_name="vol_data",
        snapshot_name="incident_response_20260708_143022",
        vpc_id="vpc-0123456789abcdef0",
    )
    # result["verdict"] == "clean" | "suspicious" | "error"

Reference:
    ONTAP REST API (volumes/clone): https://docs.netapp.com/us-en/ontap-restapi/
    FSx S3 Access Point API: https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/access-points-for-fsxn.html
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import boto3
import urllib3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# File extensions commonly appended by ransomware families. Not exhaustive —
# this is a fast, low-cost pre-filter, not a substitute for ARP's entropy
# analysis (which runs against the live volume, not a clone snapshot scan).
SUSPICIOUS_EXTENSIONS: frozenset[str] = frozenset({
    ".encrypted", ".locked", ".crypt", ".crypted", ".enc", ".ransom",
    ".wcry", ".wncry", ".locky", ".zzz", ".ezz", ".exx", ".ecc",
    ".cerber", ".cerber3", ".crab", ".vault", ".conti",
})

# A single volume dominated by one of these extensions is a strong signal
# that the snapshot captured a completed (not just attempted) encryption
# event, rather than a handful of legitimately-renamed files.
SUSPICIOUS_RATIO_THRESHOLD = 0.05  # 5% of listed objects
SUSPICIOUS_MIN_COUNT = 20  # Absolute floor to avoid false positives on tiny volumes


class RestoreVerificationError(Exception):
    """Raised when a step in the verification workflow fails."""

    def __init__(self, message: str, step: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.step = step
        self.detail = detail


@dataclass
class VerificationResult:
    """Outcome of a single snapshot verification run."""

    svm_name: str
    volume_name: str
    snapshot_name: str
    clone_name: str = ""
    access_point_name: str = ""
    verdict: str = "unknown"  # "clean" | "suspicious" | "error"
    objects_scanned: int = 0
    suspicious_objects: list[str] = field(default_factory=list)
    suspicious_ratio: float = 0.0
    reason: str = ""
    started_at: str = ""
    completed_at: str = ""
    cleaned_up: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "svm_name": self.svm_name,
            "volume_name": self.volume_name,
            "snapshot_name": self.snapshot_name,
            "clone_name": self.clone_name,
            "access_point_name": self.access_point_name,
            "verdict": self.verdict,
            "objects_scanned": self.objects_scanned,
            "suspicious_objects": self.suspicious_objects[:50],  # cap for log/DDB size
            "suspicious_object_count": len(self.suspicious_objects),
            "suspicious_ratio": round(self.suspicious_ratio, 4),
            "reason": self.reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "cleaned_up": self.cleaned_up,
        }


class RestoreVerificationClient:
    """Verifies FSx for ONTAP snapshots are clean before use as restore points.

    Args:
        mgmt_ip: FSx for ONTAP management endpoint IP.
        username: ONTAP admin username.
        password: ONTAP admin password.
        file_system_id: FSx file system ID (fs-xxxx), required for S3 AP creation.
        ca_cert_path: Path to CA certificate for TLS verification. If None,
            TLS verification is disabled (PoC only — see ontap_response.py
            for the same tradeoff).
        timeout: HTTP request timeout in seconds.
        region: AWS region for the fsx/s3 boto3 clients.
        fsx_client: Optional pre-configured boto3 fsx client (for testing).
        s3_client: Optional pre-configured boto3 s3 client (for testing).
    """

    def __init__(
        self,
        mgmt_ip: str,
        username: str,
        password: str,
        file_system_id: str,
        ca_cert_path: str | None = None,
        timeout: float = 30.0,
        region: str | None = None,
        fsx_client: Any | None = None,
        s3_client: Any | None = None,
        strict_ad_check: bool = False,
    ) -> None:
        self.base_url = f"https://{mgmt_ip}/api"
        self.file_system_id = file_system_id
        self.timeout = timeout

        if ca_cert_path:
            self._http = urllib3.PoolManager(ca_certs=ca_cert_path)
        else:
            self._http = urllib3.PoolManager(cert_reqs="CERT_NONE")
            logger.warning(
                "TLS certificate verification DISABLED. For production, "
                "provide ca_cert_path with the FSx for ONTAP CA certificate."
            )

        auth_header = urllib3.util.make_headers(basic_auth=f"{username}:{password}")
        self._headers = {
            **auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        self._fsx = fsx_client or boto3.client("fsx", region_name=region)
        self._s3 = s3_client or boto3.client("s3", region_name=region)
        self.strict_ad_check = strict_ad_check
        # Verdict from the most recent create_flexclone, so a later AccessDenied
        # can be attributed correctly. See check_ad_connectivity.
        self.last_ad_check: str = "not_checked"

    # ------------------------------------------------------------------
    # Internal: ONTAP REST helpers (mirrors ontap_response.py conventions)
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        kwargs: dict[str, Any] = {"headers": self._headers, "timeout": self.timeout}
        if body is not None:
            kwargs["body"] = json.dumps(body).encode()

        resp = self._http.request(method, url, **kwargs)

        data: dict[str, Any] = {}
        if resp.data:
            try:
                data = json.loads(resp.data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = {"raw": resp.data.decode("utf-8", errors="replace")[:500]}

        if resp.status >= 400:
            error_msg = data.get("error", {}).get("message", resp.data.decode()[:200])
            raise RestoreVerificationError(
                f"ONTAP API {method} {path} failed: HTTP {resp.status}",
                step="ontap_api",
                detail=error_msg,
            )

        return data

    def _get_svm_uuid(self, svm_name: str) -> str:
        data = self._request("GET", f"/svm/svms?name={svm_name}&fields=uuid")
        records = data.get("records", [])
        if not records:
            raise RestoreVerificationError(f"SVM not found: {svm_name}", step="svm_lookup")
        return records[0]["uuid"]

    def _wait_for_job(self, job_uuid: str, max_wait_seconds: int = 120, poll_interval: float = 3.0) -> None:
        """Poll an ONTAP async job until it reaches a terminal state."""
        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            data = self._request("GET", f"/cluster/jobs/{job_uuid}")
            state = data.get("state", "")
            if state == "success":
                return
            if state in ("failure", "error"):
                raise RestoreVerificationError(
                    f"ONTAP job {job_uuid} failed: {data.get('message', 'unknown error')}",
                    step="job_wait",
                )
            time.sleep(poll_interval)
        raise RestoreVerificationError(
            f"ONTAP job {job_uuid} did not complete within {max_wait_seconds}s",
            step="job_wait",
        )

    # ------------------------------------------------------------------
    # Step 1: FlexClone lifecycle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Pre-flight: Active Directory domain controller reachability
    # ------------------------------------------------------------------

    def _unverified_ad(self, svm_name: str, reason: str, detail: str) -> str:
        """Record an AD state that is neither confirmed good nor confirmed bad.

        Three answers are ambiguous: ONTAP omits the discovered_servers field,
        returns no CIFS domain records, or errors on the domains endpoint. Each
        is indistinguishable from an unreachable domain, and all share one
        downstream symptom -- AccessDenied from ListObjectsV2 well after the
        clone and access point were built.

        Default is to proceed, because refusing to verify a restore candidate
        during an incident because an older ONTAP omits a field is worse than
        proceeding with the verdict recorded. Pass strict_ad_check=True where
        every SVM is AD-joined and a false pass is the greater risk.
        """
        message = (
            f"AD DC reachability UNVERIFIED for AD-joined SVM '{svm_name}': {detail} "
            "This is indistinguishable from an unreachable domain, and both surface "
            "later as AccessDenied from ListObjectsV2. Suspect AD DC reachability "
            "before the S3 Access Point policy."
        )
        if self.strict_ad_check:
            raise RestoreVerificationError(
                f"AD CONNECTIVITY FAILURE (strict mode): {message}",
                step="ad_precheck",
            )
        logger.warning("%s Proceeding because strict_ad_check is off.", message)
        return f"unverified:{reason}"

    def check_ad_connectivity(self, svm_name: str) -> str:
        """Verify the SVM can reach its domain controllers before anything is built.

        On an AD-joined SVM, ONTAP performs a unix->win reverse name-mapping
        lookup for every S3 Access Point data operation, so unreachable domain
        controllers make ListObjectsV2 and GetObject return AccessDenied at the
        file system layer while HeadBucket still succeeds. That asymmetry is what
        makes the failure hard to read: the metadata probe that an operator
        naturally reaches for is the one call that still works.

        Checking here costs one API call. Discovering it later costs the clone,
        the FSx discovery wait, and the access point.

        Returns "not_applicable", "verified", or "unverified:<reason>".
        """
        cifs = self._request(
            "GET", f"/protocols/cifs/services?svm.name={svm_name}&fields=ad_domain.fqdn"
        )
        if not cifs.get("records", []):
            logger.info("SVM '%s' has no CIFS service; AD is not involved", svm_name)
            return "not_applicable"

        ad_fqdn = cifs["records"][0].get("ad_domain", {}).get("fqdn", "unknown")
        logger.info("SVM '%s' has CIFS enabled (AD: %s)", svm_name, ad_fqdn)

        try:
            domains = self._request(
                "GET",
                f"/protocols/cifs/domains?svm.name={svm_name}&fields=discovered_servers",
            )
        except RestoreVerificationError as e:
            return self._unverified_ad(
                svm_name, "domains_api_error",
                f"the domains endpoint returned an error ({e}).",
            )

        records = domains.get("records", [])
        if not records:
            return self._unverified_ad(
                svm_name, "no_domain_records", "ONTAP returned no CIFS domain records.",
            )

        servers = records[0].get("discovered_servers", None)
        if servers is None:
            return self._unverified_ad(
                svm_name, "field_absent",
                "ONTAP did not return the discovered_servers field.",
            )
        if len(servers) == 0:
            # The one unambiguous bad answer. Always fatal, strict mode or not.
            raise RestoreVerificationError(
                f"AD CONNECTIVITY FAILURE: SVM '{svm_name}' is AD-joined "
                f"(domain: {ad_fqdn}) but cannot discover any domain controllers. "
                f"S3 Access Point data operations will fail with AccessDenied. "
                f"Verify AD ({ad_fqdn}) is running and DNS IPs are reachable "
                f"from the SVM's network interfaces.",
                step="ad_precheck",
            )

        logger.info("AD domain controllers reachable: %d discovered", len(servers))
        return "verified"

    def create_flexclone(
        self,
        svm_name: str,
        volume_name: str,
        snapshot_name: str,
        clone_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a read/write FlexClone volume from a snapshot.

        The clone shares data blocks with its parent (copy-on-write), so
        creation is near-instant and consumes no storage until verification
        writes to it (which this workflow never does — it only reads).

        Args:
            svm_name: SVM containing the parent volume.
            volume_name: Parent volume name.
            snapshot_name: Snapshot to clone from (the candidate restore point).
            clone_name: Explicit clone volume name. Defaults to a timestamped
                name derived from the snapshot.

        Returns:
            Dict with clone details (clone_name, svm, uuid).
        """
        # Pre-flight before anything is built. This module previously had no AD
        # check while the CloudFormation-embedded copy of the same workflow did,
        # so the two implementations disagreed about whether the guard existed --
        # and this is the one packaged as a Lambda layer by build-layer.sh.
        self.last_ad_check = self.check_ad_connectivity(svm_name)

        if not clone_name:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            clone_name = f"verify_{volume_name}_{timestamp}"[:203]  # ONTAP name limit

        logger.info(
            "Creating FlexClone %s from %s/%s@%s", clone_name, svm_name, volume_name, snapshot_name,
        )

        body = {
            "name": clone_name,
            "svm": {"name": svm_name},
            "clone": {
                "parent_volume": {"name": volume_name},
                "parent_snapshot": {"name": snapshot_name},
                "is_flexclone": True,
            },
        }

        data = self._request("POST", "/storage/volumes", body=body)
        job = data.get("job", {})
        if job.get("uuid"):
            self._wait_for_job(job["uuid"])

        # Resolve the clone's UUID for later cleanup/S3 AP attachment.
        lookup = self._request(
            "GET", f"/storage/volumes?name={clone_name}&svm.name={svm_name}&fields=uuid"
        )
        records = lookup.get("records", [])
        if not records:
            raise RestoreVerificationError(
                f"FlexClone {clone_name} was created but could not be resolved", step="clone_lookup",
            )

        return {
            "clone_name": clone_name,
            "svm_name": svm_name,
            "volume_uuid": records[0]["uuid"],
            "parent_volume": volume_name,
            "parent_snapshot": snapshot_name,
            # Carried so a later AccessDenied can be attributed to AD rather than
            # to the access point policy, which this workflow never sets.
            "ad_check": self.last_ad_check,
        }

    def delete_flexclone(self, volume_uuid: str) -> None:
        """Delete a FlexClone volume by UUID. Idempotent-ish: logs and
        swallows a 404 (already deleted)."""
        try:
            self._request("DELETE", f"/storage/volumes/{volume_uuid}")
            logger.info("Deleted FlexClone volume %s", volume_uuid)
        except RestoreVerificationError as e:
            if "404" in str(e) or "not found" in str(e).lower():
                logger.info("FlexClone volume %s already deleted", volume_uuid)
            else:
                logger.warning("Failed to delete FlexClone volume %s: %s", volume_uuid, e)
                raise

    # ------------------------------------------------------------------
    # Step 2: S3 Access Point lifecycle (AWS FSx API, not ONTAP REST API)
    # ------------------------------------------------------------------

    def _resolve_fsvol_id(self, clone_name: str, max_wait_seconds: int = 60) -> str:
        """Resolve the AWS-side fsvol-xxxx ID for a volume created via ONTAP
        REST API. FSx discovers ONTAP-created volumes asynchronously, so this
        polls DescribeVolumes filtered by file-system-id until the clone's
        name appears."""
        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            paginator = self._fsx.get_paginator("describe_volumes")
            for page in paginator.paginate(
                Filters=[{"Name": "file-system-id", "Values": [self.file_system_id]}]
            ):
                for volume in page.get("Volumes", []):
                    if volume.get("Name") == clone_name and volume.get("Lifecycle") in (
                        "AVAILABLE", "CREATED",
                    ):
                        return volume["VolumeId"]
            time.sleep(3)
        raise RestoreVerificationError(
            f"Timed out waiting for FSx to discover clone volume '{clone_name}'",
            step="fsvol_resolve",
        )

    def attach_access_point(
        self,
        clone_name: str,
        vpc_id: str,
        access_point_name: str | None = None,
        unix_user: str = "root",
        max_wait_seconds: int = 120,
    ) -> dict[str, Any]:
        """Create a VPC-scoped S3 Access Point attached to the clone volume.

        VPC-scoped (not internet-origin) so the access point is only reachable
        from within the verification VPC — no public exposure of the clone's
        contents at any point.

        Args:
            clone_name: Name of the FlexClone volume (as returned by create_flexclone).
            vpc_id: VPC ID to restrict the access point to.
            access_point_name: Explicit AP name. Defaults to a name derived
                from the clone name.
            unix_user: UNIX identity used for file system access checks.
            max_wait_seconds: Max time to wait for AVAILABLE lifecycle state.

        Returns:
            Dict with access point ARN and name.
        """
        fsvol_id = self._resolve_fsvol_id(clone_name, max_wait_seconds=max_wait_seconds)

        if not access_point_name:
            access_point_name = re.sub(r"[^a-z0-9-]", "-", clone_name.lower())[:50]

        logger.info("Attaching S3 Access Point %s to volume %s", access_point_name, fsvol_id)

        try:
            self._fsx.create_and_attach_s3_access_point(
                Name=access_point_name,
                Type="ONTAP",
                OntapConfiguration={
                    "VolumeId": fsvol_id,
                    "FileSystemIdentity": {"Type": "UNIX", "UnixUser": {"Name": unix_user}},
                },
                S3AccessPoint={"VpcConfiguration": {"VpcId": vpc_id}},
            )
        except ClientError as e:
            raise RestoreVerificationError(
                f"Failed to create S3 Access Point: {e}", step="s3ap_create",
            ) from e

        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            resp = self._fsx.describe_s3_access_point_attachments(
                Filters=[{"Name": "name", "Values": [access_point_name]}]
            )
            attachments = resp.get("S3AccessPointAttachments", [])
            if attachments:
                attachment = attachments[0]
                lifecycle = attachment.get("Lifecycle")
                if lifecycle == "AVAILABLE":
                    ap = attachment.get("S3AccessPoint", {})
                    return {
                        "access_point_name": access_point_name,
                        "access_point_arn": ap.get("ResourceARN", ""),
                        "fsvol_id": fsvol_id,
                    }
                if lifecycle in ("FAILED", "MISCONFIGURED"):
                    raise RestoreVerificationError(
                        f"S3 Access Point {access_point_name} entered {lifecycle} state",
                        step="s3ap_create",
                    )
            time.sleep(3)

        raise RestoreVerificationError(
            f"S3 Access Point {access_point_name} did not become AVAILABLE "
            f"within {max_wait_seconds}s",
            step="s3ap_create",
        )

    def detach_access_point(self, access_point_name: str) -> None:
        """Delete the S3 Access Point. Idempotent-ish: swallows not-found."""
        try:
            self._fsx.detach_and_delete_s3_access_point(Name=access_point_name)
            logger.info("Detached S3 Access Point %s", access_point_name)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if "NotFound" in error_code:
                logger.info("S3 Access Point %s already detached", access_point_name)
            else:
                logger.warning("Failed to detach S3 Access Point %s: %s", access_point_name, e)
                raise

    # ------------------------------------------------------------------
    # Step 3: Scan for ransomware indicators via the access point
    # ------------------------------------------------------------------

    def scan_for_ransomware_indicators(
        self, access_point_arn: str, max_keys_per_page: int = 1000
    ) -> dict[str, Any]:
        """List objects through the S3 Access Point and flag suspicious ones.

        This is a fast pre-filter (extension matching), not a substitute for
        ONTAP ARP's entropy analysis. It answers "did this snapshot capture a
        volume that is dominated by ransomware-renamed files" — the coarse
        signal appropriate for an automated go/no-go gate before a human
        reviews a borderline case.

        Args:
            access_point_arn: ARN of the S3 Access Point attached to the clone.
            max_keys_per_page: ListObjectsV2 page size.

        Returns:
            Dict with objects_scanned, suspicious_objects, suspicious_ratio.
        """
        total_objects = 0
        suspicious: list[str] = []

        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=access_point_arn, MaxKeys=max_keys_per_page):
            for obj in page.get("Contents", []):
                total_objects += 1
                key = obj["Key"]
                lower_key = key.lower()
                if any(lower_key.endswith(ext) for ext in SUSPICIOUS_EXTENSIONS):
                    suspicious.append(key)

        ratio = (len(suspicious) / total_objects) if total_objects else 0.0

        return {
            "objects_scanned": total_objects,
            "suspicious_objects": suspicious,
            "suspicious_ratio": ratio,
        }

    # ------------------------------------------------------------------
    # Orchestration: full verify → cleanup workflow
    # ------------------------------------------------------------------

    def verify_snapshot(
        self,
        svm_name: str,
        volume_name: str,
        snapshot_name: str,
        vpc_id: str,
        cleanup_on_failure: bool = True,
    ) -> VerificationResult:
        """Run the full verified-clean recovery point workflow.

        Creates a FlexClone, attaches an S3 Access Point, scans for
        ransomware indicators, records a verdict, then always tears down
        the access point and clone (success or failure) unless
        cleanup_on_failure=False is explicitly requested for post-mortem
        inspection of a failed run.

        Args:
            svm_name: SVM containing the volume.
            volume_name: Volume the snapshot belongs to.
            snapshot_name: Candidate recovery point to verify.
            vpc_id: VPC to scope the verification S3 Access Point to.
            cleanup_on_failure: If False, leaves the clone/AP in place when
                an error occurs mid-workflow, for manual investigation.

        Returns:
            VerificationResult with verdict "clean", "suspicious", or "error".
        """
        result = VerificationResult(
            svm_name=svm_name,
            volume_name=volume_name,
            snapshot_name=snapshot_name,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        clone_info: dict[str, Any] = {}
        ap_info: dict[str, Any] = {}

        try:
            clone_info = self.create_flexclone(svm_name, volume_name, snapshot_name)
            result.clone_name = clone_info["clone_name"]

            ap_info = self.attach_access_point(clone_info["clone_name"], vpc_id)
            result.access_point_name = ap_info["access_point_name"]

            scan = self.scan_for_ransomware_indicators(ap_info["access_point_arn"])
            result.objects_scanned = scan["objects_scanned"]
            result.suspicious_objects = scan["suspicious_objects"]
            result.suspicious_ratio = scan["suspicious_ratio"]

            is_suspicious = (
                len(scan["suspicious_objects"]) >= SUSPICIOUS_MIN_COUNT
                and scan["suspicious_ratio"] >= SUSPICIOUS_RATIO_THRESHOLD
            )
            if is_suspicious:
                result.verdict = "suspicious"
                result.reason = (
                    f"{len(scan['suspicious_objects'])} objects "
                    f"({scan['suspicious_ratio']:.1%}) have ransomware-associated "
                    f"extensions — this snapshot likely captured an in-progress "
                    f"or completed encryption event, not a clean recovery point"
                )
                logger.warning(
                    "Snapshot %s verification: SUSPICIOUS — %s", snapshot_name, result.reason,
                )
            else:
                result.verdict = "clean"
                result.reason = (
                    f"Scanned {scan['objects_scanned']} objects; "
                    f"{len(scan['suspicious_objects'])} flagged "
                    f"({scan['suspicious_ratio']:.1%}, below "
                    f"{SUSPICIOUS_RATIO_THRESHOLD:.0%} threshold)"
                )
                logger.info("Snapshot %s verification: CLEAN — %s", snapshot_name, result.reason)

        except RestoreVerificationError as e:
            result.verdict = "error"
            result.reason = f"Verification failed at step '{e.step}': {e}"
            logger.error("Snapshot %s verification ERROR: %s", snapshot_name, result.reason)
            if not cleanup_on_failure:
                result.completed_at = datetime.now(timezone.utc).isoformat()
                return result
        finally:
            result.cleaned_up = self._cleanup(clone_info, ap_info)

        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    def _cleanup(self, clone_info: dict[str, Any], ap_info: dict[str, Any]) -> bool:
        """Best-effort teardown of the access point and clone. Returns True
        only if both steps that were attempted succeeded (or were never
        started, in which case there's nothing to clean up)."""
        success = True

        if ap_info.get("access_point_name"):
            try:
                self.detach_access_point(ap_info["access_point_name"])
            except Exception as e:  # noqa: BLE001 — best-effort cleanup
                logger.error("Cleanup: failed to detach S3 Access Point: %s", e)
                success = False

        if clone_info.get("volume_uuid"):
            try:
                self.delete_flexclone(clone_info["volume_uuid"])
            except Exception as e:  # noqa: BLE001 — best-effort cleanup
                logger.error("Cleanup: failed to delete FlexClone: %s", e)
                success = False

        return success
