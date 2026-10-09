"""Failure classification for UpdateFileSystem results.

The error-code sets are from the UpdateFileSystem API reference and the Amazon
FSx common errors (documented, read 2026-10-08). The status code alone does not
decide the class: a 400 can be a throttle or an aborted request, so the class
is chosen by error code, not by HTTP status.

An unlisted code is ambiguous, which is the fail-closed reading for an increase
that cannot be undone on first generation: the request may have been accepted,
so it is reconciled rather than resent.
"""

from __future__ import annotations

from enum import Enum

# Deterministic rejections: the same request fails the same way until the
# configuration, a permission or a quota changes. An hourly resend would repeat
# a non-retryable request and its failure report.
DETERMINISTIC_REJECTION_CODES = frozenset(
    {
        "BadRequest",
        "FileSystemNotFound",
        "IncompatibleParameterError",
        "InvalidNetworkSettings",
        "MissingFileSystemConfiguration",
        "ServiceLimitExceeded",
        "UnsupportedOperation",
        "ValidationError",
        "AccessDeniedException",
    }
)

# Retryable rejections: both depend on load or credentials, not on the request.
# ThrottlingException here is one the SDK's own retries did not clear.
RETRYABLE_REJECTION_CODES = frozenset(
    {
        "ThrottlingException",
        "ExpiredTokenException",
    }
)

# Ambiguous: the request may have been accepted. Any code not in the two sets
# above is also treated as ambiguous.
AMBIGUOUS_CODES = frozenset(
    {
        "InternalServerError",
        "InternalFailure",
        "ServiceUnavailable",
        "RequestTimeoutException",
        "RequestAbortedException",
    }
)


class FailureClass(Enum):
    """Classification of an UpdateFileSystem failure."""

    DETERMINISTIC_REJECTION = "deterministic_rejection"
    RETRYABLE_REJECTION = "retryable_rejection"
    AMBIGUOUS = "ambiguous"


def classify_error(error_code: str | None) -> FailureClass:
    """Classify an UpdateFileSystem error by its code.

    Args:
        error_code: The error code from the client error, or ``None`` for a
            client-side timeout or no response.

    Returns:
        The :class:`FailureClass`. ``None`` and any unlisted code are ambiguous.
    """
    if error_code in DETERMINISTIC_REJECTION_CODES:
        return FailureClass.DETERMINISTIC_REJECTION
    if error_code in RETRYABLE_REJECTION_CODES:
        return FailureClass.RETRYABLE_REJECTION
    # AMBIGUOUS_CODES, None, and any code not listed anywhere.
    return FailureClass.AMBIGUOUS
