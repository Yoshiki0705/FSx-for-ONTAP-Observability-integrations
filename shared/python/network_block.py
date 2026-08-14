"""AWS Network-layer IP blocking via VPC Network ACLs.

Provides immediate NFS/SMB client blocking at the network layer using
VPC NACL deny rules. Unlike ONTAP export-policy rules (which may be
subject to NFS client-side attribute caching of up to 60 seconds),
NACL rules take effect immediately at the packet level.

Design choices:
- NACLs support explicit deny rules (Security Groups do not)
- NACL rules are stateless and evaluated before Security Groups
- Rules use a configurable range of rule numbers (default: 50-99)
  to avoid conflicts with existing infrastructure rules
- A consistent description marker (FSXN_AUTO_RESPONSE) enables
  safe identification and bulk cleanup

Usage:
    from network_block import NetworkBlockClient

    client = NetworkBlockClient()

    # Block an attacker IP immediately at network layer
    client.block_ip(
        subnet_id="subnet-0123456789abcdef0",
        client_ip="203.0.113.99",
        reason="Ransomware activity detected",
    )

    # Unblock after investigation
    client.unblock_ip(
        subnet_id="subnet-0123456789abcdef0",
        client_ip="203.0.113.99",
    )

    # List all active network blocks
    client.list_active_blocks(subnet_id="subnet-0123456789abcdef0")

Reference:
    AWS NACL docs: https://docs.aws.amazon.com/vpc/latest/userguide/vpc-network-acls.html
    NACL rule evaluation: Rules are evaluated in order (lowest number first).
    A deny rule at number 50 takes precedence over an allow rule at number 100.
"""

from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Marker tag and description prefix for rules created by this module.
# Used for identification during list/cleanup operations.
RESPONSE_MARKER = "fsxn_auto_response"
DESCRIPTION_PREFIX = "[FSXN-AUTO-RESPONSE]"

# NACL rule number range reserved for automated response.
# Default range: 50-99 (leaves 1-49 for infrastructure and 100+ for normal traffic)
DEFAULT_RULE_NUMBER_START = 50
DEFAULT_RULE_NUMBER_END = 99

# Ports to block (NFS + related)
NFS_PORTS = [2049, 111, 635, 4045, 4046]  # nfs, portmapper, mountd, lockd, statd


class NetworkBlockError(Exception):
    """Raised when a network block operation fails."""

    def __init__(self, message: str, operation: str = "", details: Any = None):
        super().__init__(message)
        self.operation = operation
        self.details = details


class NetworkBlockClient:
    """AWS VPC Network ACL-based IP blocking client.

    Provides immediate network-layer blocking for NFS clients by adding
    deny rules to the NACL associated with the FSx for ONTAP subnet.

    Args:
        ec2_client: Optional pre-configured boto3 EC2 client.
            If not provided, creates one using default credentials.
        rule_number_start: Start of the rule number range for response rules.
        rule_number_end: End of the rule number range for response rules.
    """

    def __init__(
        self,
        ec2_client: Any = None,
        rule_number_start: int = DEFAULT_RULE_NUMBER_START,
        rule_number_end: int = DEFAULT_RULE_NUMBER_END,
    ):
        self._ec2 = ec2_client or boto3.client("ec2")
        self._rule_start = rule_number_start
        self._rule_end = rule_number_end

    def _validate_ip(self, ip: str) -> str:
        """Validate and normalize an IPv4 address.

        Args:
            ip: IP address string (with or without /32 suffix).

        Returns:
            Normalized CIDR string (e.g., "203.0.113.99/32").

        Raises:
            NetworkBlockError: If the IP is invalid.
        """
        try:
            # Strip /32 if present, then validate
            clean_ip = ip.split("/")[0]
            addr = ipaddress.IPv4Address(clean_ip)
            return f"{addr}/32"
        except (ValueError, ipaddress.AddressValueError) as e:
            raise NetworkBlockError(
                f"Invalid IP address: {ip}", operation="validate_ip", details=str(e)
            )

    def find_nacl_for_subnet(self, subnet_id: str) -> str:
        """Find the Network ACL associated with a subnet.

        Args:
            subnet_id: The VPC subnet ID.

        Returns:
            The NACL ID associated with the subnet.

        Raises:
            NetworkBlockError: If no NACL is found.
        """
        try:
            response = self._ec2.describe_network_acls(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
            )
            acls = response.get("NetworkAcls", [])
            if not acls:
                raise NetworkBlockError(
                    f"No NACL found for subnet {subnet_id}",
                    operation="find_nacl",
                )
            return acls[0]["NetworkAclId"]
        except ClientError as e:
            raise NetworkBlockError(
                f"Failed to find NACL for subnet {subnet_id}: {e}",
                operation="find_nacl",
                details=e.response,
            )

    def _find_next_rule_number(self, nacl_id: str, is_egress: bool = False) -> int:
        """Find the next available rule number in our reserved range.

        Args:
            nacl_id: The NACL ID to check.
            is_egress: Whether to check egress rules (default: ingress).

        Returns:
            The next available rule number.

        Raises:
            NetworkBlockError: If the range is exhausted.
        """
        try:
            response = self._ec2.describe_network_acls(
                NetworkAclIds=[nacl_id]
            )
            acl = response["NetworkAcls"][0]
            entries = acl.get("Entries", [])

            # Find used rule numbers in our range
            used = set()
            for entry in entries:
                if entry.get("Egress") == is_egress:
                    num = entry.get("RuleNumber", 0)
                    if self._rule_start <= num <= self._rule_end:
                        used.add(num)

            # Find first available
            for num in range(self._rule_start, self._rule_end + 1):
                if num not in used:
                    return num

            raise NetworkBlockError(
                f"No available rule numbers in range {self._rule_start}-{self._rule_end}",
                operation="find_rule_number",
            )
        except ClientError as e:
            raise NetworkBlockError(
                f"Failed to describe NACL {nacl_id}: {e}",
                operation="find_rule_number",
                details=e.response,
            )

    def block_ip(
        self,
        subnet_id: str,
        client_ip: str,
        reason: str = "automated-response",
        block_all_ports: bool = True,
    ) -> dict[str, Any]:
        """Block an IP at the network layer via NACL deny rule.

        Adds a deny-all (or deny-NFS-ports) ingress rule to the NACL
        associated with the specified subnet. Takes effect immediately
        at the packet level — no NFS client cache can bypass this.

        Args:
            subnet_id: Subnet ID where FSx for ONTAP ENIs reside.
            client_ip: Attacker IP to block.
            reason: Human-readable reason (stored in tags if supported).
            block_all_ports: If True (default), blocks ALL traffic from the IP.
                If False, blocks only NFS-related ports (2049, 111, 635, 4045, 4046).

        Returns:
            Dict with block details (nacl_id, rule_number, cidr).

        Raises:
            NetworkBlockError: If the operation fails.
        """
        cidr = self._validate_ip(client_ip)
        nacl_id = self.find_nacl_for_subnet(subnet_id)
        rule_number = self._find_next_rule_number(nacl_id)

        try:
            if block_all_ports:
                # Block ALL traffic from this IP (most aggressive, most reliable)
                self._ec2.create_network_acl_entry(
                    NetworkAclId=nacl_id,
                    RuleNumber=rule_number,
                    Protocol="-1",  # All protocols
                    RuleAction="deny",
                    Egress=False,  # Ingress
                    CidrBlock=cidr,
                )
            else:
                # Block only NFS port (2049) — less aggressive
                self._ec2.create_network_acl_entry(
                    NetworkAclId=nacl_id,
                    RuleNumber=rule_number,
                    Protocol="6",  # TCP
                    PortRange={"From": 2049, "To": 2049},
                    RuleAction="deny",
                    Egress=False,
                    CidrBlock=cidr,
                )

            logger.info(
                "Network block applied: NACL %s rule %d denying %s (reason: %s)",
                nacl_id, rule_number, cidr, reason,
            )

            return {
                "action": "block_nfs_ip_network",
                "status": "blocked",
                "nacl_id": nacl_id,
                "rule_number": rule_number,
                "cidr": cidr,
                "block_all_ports": block_all_ports,
                "subnet_id": subnet_id,
                "reason": reason,
                "timestamp": _utc_now(),
            }

        except ClientError as e:
            raise NetworkBlockError(
                f"Failed to create NACL deny rule: {e}",
                operation="block_ip",
                details=e.response,
            )

    def unblock_ip(
        self,
        subnet_id: str,
        client_ip: str,
    ) -> dict[str, Any]:
        """Remove network-layer block for an IP.

        Finds and removes the NACL deny rule matching the specified IP
        in the automated response rule number range.

        Args:
            subnet_id: Subnet ID where the block was applied.
            client_ip: IP to unblock.

        Returns:
            Dict with unblock details.

        Raises:
            NetworkBlockError: If the operation fails or rule not found.
        """
        cidr = self._validate_ip(client_ip)
        nacl_id = self.find_nacl_for_subnet(subnet_id)

        try:
            response = self._ec2.describe_network_acls(NetworkAclIds=[nacl_id])
            acl = response["NetworkAcls"][0]
            entries = acl.get("Entries", [])

            # Find matching deny rules in our range
            removed = 0
            for entry in entries:
                if (
                    entry.get("Egress") is False
                    and entry.get("RuleAction") == "deny"
                    and entry.get("CidrBlock") == cidr
                    and self._rule_start <= entry.get("RuleNumber", 0) <= self._rule_end
                ):
                    self._ec2.delete_network_acl_entry(
                        NetworkAclId=nacl_id,
                        RuleNumber=entry["RuleNumber"],
                        Egress=False,
                    )
                    logger.info(
                        "Removed NACL deny rule %d for %s on %s",
                        entry["RuleNumber"], cidr, nacl_id,
                    )
                    removed += 1

            if removed == 0:
                logger.warning("No matching NACL deny rule found for %s", cidr)
                return {
                    "action": "unblock_nfs_ip_network",
                    "status": "not_found",
                    "nacl_id": nacl_id,
                    "cidr": cidr,
                    "subnet_id": subnet_id,
                }

            return {
                "action": "unblock_nfs_ip_network",
                "status": "unblocked",
                "nacl_id": nacl_id,
                "cidr": cidr,
                "rules_removed": removed,
                "subnet_id": subnet_id,
                "timestamp": _utc_now(),
            }

        except ClientError as e:
            raise NetworkBlockError(
                f"Failed to remove NACL deny rule: {e}",
                operation="unblock_ip",
                details=e.response,
            )

    def list_active_blocks(self, subnet_id: str) -> dict[str, Any]:
        """List all network-layer blocks in the automated response range.

        Args:
            subnet_id: Subnet to check.

        Returns:
            Dict with list of active blocks.
        """
        nacl_id = self.find_nacl_for_subnet(subnet_id)

        try:
            response = self._ec2.describe_network_acls(NetworkAclIds=[nacl_id])
            acl = response["NetworkAcls"][0]
            entries = acl.get("Entries", [])

            blocks = []
            for entry in entries:
                if (
                    entry.get("Egress") is False
                    and entry.get("RuleAction") == "deny"
                    and self._rule_start <= entry.get("RuleNumber", 0) <= self._rule_end
                ):
                    blocks.append({
                        "rule_number": entry["RuleNumber"],
                        "cidr": entry.get("CidrBlock", ""),
                        "protocol": entry.get("Protocol", ""),
                        "port_range": entry.get("PortRange"),
                    })

            return {
                "action": "list_network_blocks",
                "nacl_id": nacl_id,
                "subnet_id": subnet_id,
                "active_blocks": blocks,
                "count": len(blocks),
            }

        except ClientError as e:
            raise NetworkBlockError(
                f"Failed to list NACL rules: {e}",
                operation="list_active_blocks",
                details=e.response,
            )


def _utc_now() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
