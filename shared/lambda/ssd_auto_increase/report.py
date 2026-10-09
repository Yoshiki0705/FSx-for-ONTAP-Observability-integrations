"""SNS reports on the notification topic.

The report is sent before the call and at each later state change. It is not
the record: SNS delivery is transient and an email subscription can stay
unconfirmed, so the archive, not the report, is the audit record.

In ``approve`` mode the report carries the computed ``aws fsx update-file-system``
command for a person to run; the function makes no API call in that mode.
"""

from __future__ import annotations

import json
import shlex
from typing import Any


class Reporter:
    """Publishes reports to the notification SNS topic."""

    def __init__(self, sns_client: Any, topic_arn: str, file_system_id: str) -> None:
        """Create a reporter.

        Args:
            sns_client: A boto3 SNS client.
            topic_arn: The notification topic ARN.
            file_system_id: The managed file system ID.
        """
        self._sns = sns_client
        self._topic_arn = topic_arn
        self._fs = file_system_id

    def publish(self, subject: str, body: dict[str, Any]) -> None:
        """Publish one report.

        Args:
            subject: SNS subject (truncated by SNS to 100 characters).
            body: The report payload, serialized as JSON.
        """
        message = json.dumps({"file_system_id": self._fs, **body}, default=str, sort_keys=True)
        self._sns.publish(TopicArn=self._topic_arn, Subject=subject[:100], Message=message)

    def approve_command(
        self, target_gib: int, iops: int | None, correlation_id: str
    ) -> str:
        """Build the aws fsx update-file-system command for approve mode.

        Args:
            target_gib: The computed target in GiB.
            iops: User-provisioned IOPS to set, or ``None`` for automatic mode.
            correlation_id: The correlation ID, used as the ClientRequestToken.

        Returns:
            The CLI command string.
        """
        parts = [
            "aws",
            "fsx",
            "update-file-system",
            "--file-system-id",
            shlex.quote(self._fs),
            "--storage-capacity",
            str(target_gib),
            "--client-request-token",
            shlex.quote(correlation_id),
        ]
        if iops is not None:
            # The JSON value contains spaces, so it must be one shell token:
            # pasted unquoted, the shell would split it into several arguments
            # and the AWS CLI would reject --ontap-configuration. shlex.quote
            # wraps it as a single argument for both IOPS modes.
            ontap_config = json.dumps(
                {"DiskIopsConfiguration": {"Mode": "USER_PROVISIONED", "Iops": iops}}
            )
            parts.append("--ontap-configuration")
            parts.append(shlex.quote(ontap_config))
        return " ".join(parts)
