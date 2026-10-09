"""Failure classification: each documented code lands in the right class."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classify import (  # noqa: E402
    AMBIGUOUS_CODES,
    DETERMINISTIC_REJECTION_CODES,
    RETRYABLE_REJECTION_CODES,
    FailureClass,
    classify_error,
)


def test_deterministic_rejections() -> None:
    for code in ("BadRequest", "ServiceLimitExceeded", "AccessDeniedException", "ValidationError"):
        assert classify_error(code) is FailureClass.DETERMINISTIC_REJECTION


def test_retryable_rejections() -> None:
    for code in ("ThrottlingException", "ExpiredTokenException"):
        assert classify_error(code) is FailureClass.RETRYABLE_REJECTION


def test_ambiguous_documented_codes() -> None:
    for code in ("InternalServerError", "ServiceUnavailable", "RequestTimeoutException"):
        assert classify_error(code) is FailureClass.AMBIGUOUS


def test_unlisted_code_is_ambiguous() -> None:
    assert classify_error("SomeBrandNewCode") is FailureClass.AMBIGUOUS


def test_none_is_ambiguous() -> None:
    assert classify_error(None) is FailureClass.AMBIGUOUS


def test_sets_are_disjoint() -> None:
    assert not (DETERMINISTIC_REJECTION_CODES & RETRYABLE_REJECTION_CODES)
    assert not (DETERMINISTIC_REJECTION_CODES & AMBIGUOUS_CODES)
    assert not (RETRYABLE_REJECTION_CODES & AMBIGUOUS_CODES)
