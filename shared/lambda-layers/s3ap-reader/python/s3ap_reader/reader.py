"""S3 Access Point reader utility.

Handles reading objects from S3 Access Points with retry logic
and streaming support for large files.
"""

import logging
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Retry configuration
RETRY_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=30,
)


class S3AccessPointReader:
    """Reader for S3 Access Point objects."""

    def __init__(self, region: str | None = None):
        """Initialize the S3 Access Point reader.

        Args:
            region: AWS region. If None, uses default region.
        """
        self._client = boto3.client("s3", region_name=region, config=RETRY_CONFIG)

    def read_object(self, access_point_arn: str, key: str) -> bytes:
        """Read an object from an S3 Access Point.

        Args:
            access_point_arn: ARN of the S3 Access Point.
            key: Object key.

        Returns:
            Object content as bytes.

        Raises:
            ClientError: If the object cannot be read.
        """
        try:
            response = self._client.get_object(Bucket=access_point_arn, Key=key)
            return response["Body"].read()
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            logger.error(
                "Failed to read object from S3 AP: %s/%s (error: %s)",
                access_point_arn,
                key,
                error_code,
            )
            raise

    def read_object_stream(
        self, access_point_arn: str, key: str, chunk_size: int = 8192
    ):
        """Read an object from S3 Access Point as a stream.

        Args:
            access_point_arn: ARN of the S3 Access Point.
            key: Object key.
            chunk_size: Size of each chunk in bytes.

        Yields:
            Chunks of object content.
        """
        try:
            response = self._client.get_object(Bucket=access_point_arn, Key=key)
            body = response["Body"]
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        except ClientError:
            logger.error(
                "Failed to stream object from S3 AP: %s/%s", access_point_arn, key
            )
            raise

    def get_object_metadata(
        self, access_point_arn: str, key: str
    ) -> dict[str, Any]:
        """Get object metadata from S3 Access Point.

        Args:
            access_point_arn: ARN of the S3 Access Point.
            key: Object key.

        Returns:
            Object metadata dictionary.
        """
        try:
            response = self._client.head_object(Bucket=access_point_arn, Key=key)
            return {
                "content_length": response["ContentLength"],
                "content_type": response.get("ContentType", ""),
                "last_modified": response["LastModified"].isoformat(),
                "etag": response["ETag"],
            }
        except ClientError:
            logger.error(
                "Failed to get metadata from S3 AP: %s/%s", access_point_arn, key
            )
            raise
