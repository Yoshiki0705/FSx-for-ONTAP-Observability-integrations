"""Lambda authorizer for EMS webhook shared secret authentication.

Validates a bearer token or shared secret from the Authorization header
against a value stored in AWS Secrets Manager. Returns an IAM policy
allowing or denying the API Gateway method invocation.

This is a REQUEST-type Lambda authorizer that checks the Authorization
header for a Bearer token matching the secret stored in Secrets Manager.

Environment variables:
    WEBHOOK_SECRET_ARN: ARN of the Secrets Manager secret containing
        the shared secret as JSON: {"webhook_secret": "<token>"}

Reference:
    https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-use-lambda-authorizer.html
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

WEBHOOK_SECRET_ARN = os.environ.get("WEBHOOK_SECRET_ARN", "")

# Module-level cache for the secret (persists across warm invocations)
_secret_cache: str | None = None
_secret_loaded_at: float = 0.0
_SECRET_TTL = 300  # 5 minutes

secrets_client = boto3.client("secretsmanager")


def _get_webhook_secret() -> str:
    """Retrieve webhook secret from Secrets Manager with TTL cache.

    Returns:
        The shared secret string.
    """
    global _secret_cache, _secret_loaded_at

    now = time.time()
    if _secret_cache is not None and (now - _secret_loaded_at) < _SECRET_TTL:
        return _secret_cache

    response = secrets_client.get_secret_value(SecretId=WEBHOOK_SECRET_ARN)
    secret_data = json.loads(response["SecretString"])
    _secret_cache = secret_data["webhook_secret"]
    _secret_loaded_at = now
    logger.info("Loaded webhook secret from Secrets Manager")
    return _secret_cache


def _generate_policy(
    principal_id: str, effect: str, resource: str
) -> dict[str, Any]:
    """Generate an IAM policy document for API Gateway.

    Args:
        principal_id: Identifier for the caller.
        effect: 'Allow' or 'Deny'.
        resource: The API Gateway method ARN.

    Returns:
        IAM policy document as a dictionary.
    """
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda authorizer entry point.

    Validates the Authorization header against the stored webhook secret.
    Accepts formats:
        - Bearer <token>
        - <token> (raw token without prefix)

    Args:
        event: API Gateway authorizer event containing headers and methodArn.
        context: Lambda context object.

    Returns:
        IAM policy allowing or denying the request.

    Raises:
        Exception: With message 'Unauthorized' for missing/invalid headers,
            which API Gateway translates to HTTP 401.
    """
    # Extract Authorization header (case-insensitive lookup)
    headers = event.get("headers", {})
    auth_value = None
    for key, value in headers.items():
        if key.lower() == "authorization":
            auth_value = value
            break

    if not auth_value:
        logger.warning("No Authorization header present")
        raise Exception("Unauthorized")  # noqa: TRY002 — API Gateway expects this

    # Strip 'Bearer ' prefix if present
    token = auth_value
    if token.lower().startswith("bearer "):
        token = token[7:]

    token = token.strip()
    if not token:
        logger.warning("Empty token in Authorization header")
        raise Exception("Unauthorized")  # noqa: TRY002

    # Validate against stored secret
    try:
        expected_secret = _get_webhook_secret()
    except Exception:
        logger.exception("Failed to retrieve webhook secret")
        raise Exception("Unauthorized")  # noqa: TRY002

    method_arn = event.get("methodArn", "*")

    # compare_digest rather than ==. A plain string comparison returns as soon as
    # it hits a differing byte, so the time it takes leaks how many leading bytes
    # of the submitted token were correct. Against an endpoint a caller can hit
    # repeatedly that is enough to recover the secret one byte at a time.
    if hmac.compare_digest(token, expected_secret):
        logger.info("Authorization successful")
        return _generate_policy("ontap-ems-webhook", "Allow", method_arn)

    logger.warning("Token mismatch — denying request")
    return _generate_policy("ontap-ems-webhook", "Deny", method_arn)
