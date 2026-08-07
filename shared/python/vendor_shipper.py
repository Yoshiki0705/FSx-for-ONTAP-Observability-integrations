"""Shared HTTP shipping and secret caching for vendor Lambdas.

Every vendor integration needs the same three things around its own payload
format: a cached credential, a retry loop that distinguishes retryable from
permanent HTTP failures, and size-aware batching. Duplicating those per handler
is how retry semantics drift between vendors.

What stays with the vendor: the endpoint URL, the auth header shape, and the
payload format.

Retry policy implemented by :func:`post_with_retry`:

======================  ==========================================
HTTP status             Behaviour
======================  ==========================================
< 300                   success
429                     retry, honouring ``Retry-After`` when sent
>= 500                  retry with exponential backoff
other 4xx               permanent failure, no retry
transport error         retry with exponential backoff
======================  ==========================================

A 4xx other than 429 is not retried because it means the request itself is
wrong — a bad token or a malformed body will fail identically on every attempt,
and retrying only delays the DLQ.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import boto3
import urllib3

__all__ = [
    "SecretCache",
    "batch_by_size",
    "build_pool",
    "post_with_retry",
]

DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY_SECONDS = 2
DEFAULT_TIMEOUT_SECONDS = 30.0


def build_pool(num_pools: int = 2, maxsize: int = 4) -> urllib3.PoolManager:
    """Create a connection-pooling HTTP client with retries disabled.

    Retries are handled by :func:`post_with_retry` so that 429 handling and
    logging are consistent; letting urllib3 retry as well would multiply the
    attempt count silently.

    Args:
        num_pools: Number of connection pools to cache.
        maxsize: Connections to keep per pool.

    Returns:
        Configured :class:`urllib3.PoolManager`.
    """
    return urllib3.PoolManager(
        num_pools=num_pools,
        maxsize=maxsize,
        retries=urllib3.Retry(total=0),
    )


class SecretCache:
    """Secrets Manager value cached for the Lambda execution context.

    Caching per execution context rather than per invocation is deliberate: a
    warm container would otherwise call Secrets Manager on every event, adding
    latency and cost to a path that runs once per audit poll.

    Accepts either a plain string secret or a JSON object. For JSON, the first
    matching key from ``json_keys`` wins, falling back to the raw string — so
    the same code works whether the operator stored
    ``"abc123"`` or ``{"api_key": "abc123"}``.
    """

    def __init__(
        self,
        secret_arn: str,
        json_keys: tuple[str, ...] = ("api_key", "token"),
        client: Any = None,
    ) -> None:
        """Initialize the cache.

        Args:
            secret_arn: Secrets Manager secret ARN or name.
            json_keys: Keys to try when the secret is a JSON object.
            client: Optional boto3 Secrets Manager client (for tests).
        """
        self._secret_arn = secret_arn
        self._json_keys = json_keys
        self._client = client or boto3.client("secretsmanager")
        self._value: str | None = None

    def get(self) -> str:
        """Return the secret value, fetching it once per execution context.

        Returns:
            The secret string.

        Raises:
            botocore.exceptions.ClientError: If the secret cannot be read. This
                is transient from the caller's perspective, so it is allowed to
                propagate rather than being turned into a success response.
        """
        if self._value is not None:
            return self._value

        response = self._client.get_secret_value(SecretId=self._secret_arn)
        secret = response["SecretString"]
        try:
            parsed = json.loads(secret)
        except (json.JSONDecodeError, TypeError):
            self._value = secret
            return self._value

        if isinstance(parsed, dict):
            for key in self._json_keys:
                if parsed.get(key):
                    self._value = str(parsed[key])
                    return self._value
        self._value = secret
        return self._value

    def clear(self) -> None:
        """Drop the cached value so the next :meth:`get` refetches.

        Used after a 401/403, where the stored credential may have been rotated.
        """
        self._value = None


def post_with_retry(
    http: urllib3.PoolManager,
    url: str,
    body: bytes,
    headers: dict[str, str],
    logger: logging.Logger | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: int = DEFAULT_BASE_DELAY_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """POST a payload, retrying only failures that can plausibly succeed later.

    Args:
        http: Pool manager from :func:`build_pool`.
        url: Full destination URL.
        body: Encoded request body.
        headers: Request headers including auth.
        logger: Optional logger.
        max_retries: Total attempts, not additional attempts.
        base_delay: Seconds for the first backoff; doubles per attempt.
        timeout: Per-request timeout in seconds.

    Returns:
        True when the endpoint accepted the payload, False otherwise. Callers
        must treat False as "not delivered" — returning success on False is how
        telemetry gets silently dropped.
    """
    log = logger or logging.getLogger(__name__)

    for attempt in range(max_retries):
        last_attempt = attempt == max_retries - 1
        try:
            response = http.request(
                "POST", url, body=body, headers=headers, timeout=timeout
            )

            if response.status < 300:
                log.debug("Delivered payload (attempt %d)", attempt + 1)
                return True

            if response.status == 429:
                if last_attempt:
                    log.error("Rate limited and out of retries (429)")
                    return False
                # Retry-After may be absent; fall back to exponential backoff.
                try:
                    wait = int(response.headers.get("Retry-After", base_delay * 2**attempt))
                except (TypeError, ValueError):
                    wait = base_delay * 2**attempt
                log.warning("Rate limited (429), retrying in %ds", wait)
                time.sleep(wait)
                continue

            if response.status >= 500:
                if last_attempt:
                    log.error(
                        "Server error %d and out of retries after %d attempts",
                        response.status, max_retries,
                    )
                    return False
                wait = base_delay * 2**attempt
                log.warning(
                    "Server error %d, retrying in %ds", response.status, wait
                )
                time.sleep(wait)
                continue

            # 4xx other than 429 will fail identically on a retry.
            log.error(
                "Permanent HTTP error %d: %s",
                response.status,
                response.data.decode("utf-8", errors="replace")[:500],
            )
            return False

        except urllib3.exceptions.HTTPError as e:
            if last_attempt:
                log.error(
                    "Transport error on final attempt %d/%d: %s",
                    attempt + 1, max_retries, str(e),
                )
                return False
            wait = base_delay * 2**attempt
            log.warning(
                "Transport error (attempt %d/%d): %s, retrying in %ds",
                attempt + 1, max_retries, str(e), wait,
            )
            time.sleep(wait)

    return False


def batch_by_size(
    items: list[Any],
    max_bytes: int,
    max_items: int | None = None,
    sizer: Any = None,
) -> list[list[Any]]:
    """Split items into batches that respect a byte and item ceiling.

    An item larger than ``max_bytes`` on its own is emitted as a single-item
    batch rather than dropped; the vendor will reject it and the failure becomes
    visible, which beats silently discarding an audit record.

    Args:
        items: Items to batch.
        max_bytes: Maximum encoded size per batch.
        max_items: Optional maximum item count per batch.
        sizer: Callable returning an item's encoded size. Defaults to the
            length of its compact JSON encoding.

    Returns:
        List of batches, each a list of items. Empty input gives an empty list.
    """
    if not items:
        return []

    size_of = sizer or (lambda x: len(json.dumps(x, default=str).encode("utf-8")))

    batches: list[list[Any]] = []
    current: list[Any] = []
    current_size = 0

    for item in items:
        item_size = size_of(item)
        too_big = current and (current_size + item_size > max_bytes)
        too_many = current and max_items is not None and len(current) >= max_items
        if too_big or too_many:
            batches.append(current)
            current = [item]
            current_size = item_size
        else:
            current.append(item)
            current_size += item_size

    if current:
        batches.append(current)
    return batches
