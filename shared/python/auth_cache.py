"""Shared Secrets Manager auth cache with TTL and reload-on-401/403.

Provides a reusable credential caching layer for all vendor integrations
that use direct API delivery (Grafana, Datadog, Honeycomb, Elastic, etc.).

Usage:
    from auth_cache import SecretBackedAuth

    auth = SecretBackedAuth(secret_arn=os.environ["API_KEY_SECRET_ARN"])

    # Normal usage — returns cached credentials
    creds = auth.get()

    # After receiving 401/403 — force refresh and retry once
    creds = auth.get(force_refresh=True)

The cache stores the parsed JSON secret and tracks load time. On TTL expiry
or explicit force_refresh, it reloads from Secrets Manager. This handles
credential rotation without requiring Lambda redeployment or cold start.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import boto3

logger = logging.getLogger(__name__)


class SecretBackedAuth:
    """TTL-based credential cache backed by AWS Secrets Manager.

    Attributes:
        secret_arn: ARN of the Secrets Manager secret.
        ttl_seconds: Cache TTL in seconds (default: 600 = 10 minutes).
    """

    def __init__(
        self,
        secret_arn: str,
        ttl_seconds: int = 600,
        secrets_client: Any | None = None,
    ) -> None:
        """Initialize the auth cache.

        Args:
            secret_arn: ARN of the Secrets Manager secret containing
                credentials as a JSON string.
            ttl_seconds: How long to cache credentials before refreshing.
                Default 600s (10 min). Set to 0 to disable caching.
            secrets_client: Optional pre-configured boto3 Secrets Manager
                client (useful for testing).
        """
        self.secret_arn = secret_arn
        self.ttl_seconds = ttl_seconds
        self._client = secrets_client or boto3.client("secretsmanager")
        self._cached: dict[str, Any] | None = None
        self._loaded_at: float = 0.0

    def get(self, force_refresh: bool = False) -> dict[str, Any]:
        """Get credentials, refreshing if expired or forced.

        Args:
            force_refresh: If True, bypass cache and reload from
                Secrets Manager. Use after receiving 401/403 from
                the vendor API.

        Returns:
            Parsed JSON secret as a dictionary.

        Raises:
            ClientError: If Secrets Manager call fails.
            json.JSONDecodeError: If secret is not valid JSON.
        """
        if force_refresh or self._is_expired():
            self._load()
        return self._cached  # type: ignore[return-value]

    def _is_expired(self) -> bool:
        """Check if the cached credentials have expired."""
        if self._cached is None:
            return True
        if self.ttl_seconds <= 0:
            return True
        return (time.time() - self._loaded_at) > self.ttl_seconds

    def _load(self) -> None:
        """Load secret from Secrets Manager and update cache."""
        logger.info("Loading credentials from Secrets Manager: %s", self.secret_arn)
        response = self._client.get_secret_value(SecretId=self.secret_arn)
        self._cached = json.loads(response["SecretString"])
        self._loaded_at = time.time()


def send_with_auth_retry(
    send_fn: Any,
    auth: SecretBackedAuth,
    build_headers_fn: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Send a request with automatic credential refresh on 401/403.

    This implements the reload-on-auth-failure pattern:
    1. Get cached credentials
    2. Build headers and send request
    3. If 401/403, force-refresh credentials and retry once
    4. If still failing, raise

    Args:
        send_fn: Callable that sends the request. Must accept a 'headers'
            keyword argument and return an object with a 'status' attribute.
        auth: SecretBackedAuth instance for credential management.
        build_headers_fn: Callable that takes credentials dict and returns
            headers dict for the request.
        *args: Positional arguments passed to send_fn.
        **kwargs: Keyword arguments passed to send_fn.

    Returns:
        The response from send_fn.

    Raises:
        RuntimeError: If the request fails after credential refresh.

    Example:
        def build_headers(creds):
            token = f"{creds['instance_id']}:{creds['api_key']}"
            return {"Authorization": f"Basic {b64encode(token.encode()).decode()}"}

        def send_request(payload, headers):
            return http.request("POST", url, body=payload, headers=headers)

        response = send_with_auth_retry(
            send_fn=lambda headers: send_request(payload, headers),
            auth=auth_cache,
            build_headers_fn=build_headers,
        )
    """
    creds = auth.get()
    headers = build_headers_fn(creds)
    response = send_fn(headers=headers, *args, **kwargs)

    if response.status in (401, 403):
        logger.warning(
            "Received %d — refreshing credentials and retrying once",
            response.status,
        )
        creds = auth.get(force_refresh=True)
        headers = build_headers_fn(creds)
        response = send_fn(headers=headers, *args, **kwargs)

        if response.status in (401, 403):
            raise RuntimeError(
                f"Authentication failed after credential refresh: "
                f"HTTP {response.status}"
            )

    return response
