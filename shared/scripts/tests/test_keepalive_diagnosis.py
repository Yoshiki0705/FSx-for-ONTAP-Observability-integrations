"""The KeepAlive diagnosis must not report a working pipeline as broken.

What these tests prove, and what they do not
--------------------------------------------
They pin the *window arithmetic* and the *wording* of
``diagnose_keepalive_messages``. They drive a stub CloudWatch Logs client, so
they say nothing about how often a real ONTAP sends a KeepAlive. The evidence
for the 120 s interval is a measurement, recorded separately:
``docs/en/verification-results-fpolicy-s3ap-and-session.md`` — 4,694 KeepAlive
lines over one session, widest gap 120.4 s, against an engine configured with
``keep_alive_interval=PT2M``.

Read that distinction literally. A green run here does not mean the interval is
still 120 s. It means that whatever interval is passed in, the window derived
from it is wide enough to contain a KeepAlive, and that an empty window is not
reported as a disconnection.

Why the wording is asserted
---------------------------
The defect had two halves. The window was too narrow (30 s against a 120 s
interval, so roughly three runs in four saw nothing), and the empty case then
stated "This indicates ONTAP is NOT connected to the FPolicy server" as a
conclusion. Widening the window alone leaves the second half in place: a
genuinely quiet wide window still produces the false conclusion. So the third
test asserts on the text. Without it the phrasing can regress while the
arithmetic tests stay green.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e2e_test_fpolicy import (  # noqa: E402
    DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
    diagnose_keepalive_messages,
    keepalive_lookback_seconds,
)

MEASURED_INTERVAL_SECONDS = 120
BROKEN_LEGACY_WINDOW_SECONDS = 30


class StubLogsClient:
    """Serves KeepAlive lines spaced at a fixed interval.

    Returns only the events whose timestamp falls inside the requested window,
    which is what makes the window width observable in a test.
    """

    def __init__(
        self,
        interval_seconds: float,
        count: int = 40,
        phase_seconds: float | None = None,
    ) -> None:
        # The newest KeepAlive is phase_seconds old, not zero seconds old. A
        # run starts at an arbitrary point within the current interval, so a
        # stub that always places a line at "now" would make even a 1 s window
        # succeed and the window width would stop being observable. Default to
        # three quarters of the way through the interval.
        if phase_seconds is None:
            phase_seconds = 0.75 * interval_seconds
        now_ms = int(time.time() * 1000)
        step_ms = int(interval_seconds * 1000)
        phase_ms = int(phase_seconds * 1000)
        # seq counts up towards the present so the newest line is the last one.
        self.events = [
            {
                "timestamp": now_ms - phase_ms - (i * step_ms),
                "message": (
                    f"[KeepAlive] seq={count - i} "
                    f"| since_prev={interval_seconds:.1f}s"
                ),
            }
            for i in reversed(range(count))
        ]
        self.requested_start_times: list[int] = []

    def filter_log_events(
        self, logGroupName: str, startTime: int, filterPattern: str, limit: int
    ) -> dict[str, Any]:
        self.requested_start_times.append(startTime)
        matched = [e for e in self.events if e["timestamp"] >= startTime]
        return {"events": matched[:limit]}


class EmptyLogsClient:
    """A log group with no KeepAlive lines at all."""

    def __init__(self) -> None:
        self.requested_start_times: list[int] = []

    def filter_log_events(
        self, logGroupName: str, startTime: int, filterPattern: str, limit: int
    ) -> dict[str, Any]:
        self.requested_start_times.append(startTime)
        return {"events": []}


def test_narrow_window_misses_a_healthy_stream() -> None:
    """Case 1: the old 30 s window over a 120 s stream reports nothing found.

    This is the defect reproduced. It is asserted rather than merely fixed so
    that the window can never quietly return to a value narrower than the
    interval.
    """
    client = StubLogsClient(interval_seconds=MEASURED_INTERVAL_SECONDS)

    found, report = diagnose_keepalive_messages(
        client,
        "/ecs/fsxn-fpolicy-server",
        keepalive_interval_seconds=MEASURED_INTERVAL_SECONDS,
        lookback_seconds=BROKEN_LEGACY_WINDOW_SECONDS,
    )

    assert found is False
    assert "No KeepAlive messages were observed" in report


def test_derived_window_finds_the_same_healthy_stream() -> None:
    """Case 2: the derived window over the same stream reports found.

    Same stub, same interval, only the window differs — so a failure here is
    attributable to the window and nothing else.
    """
    client = StubLogsClient(interval_seconds=MEASURED_INTERVAL_SECONDS)

    found, report = diagnose_keepalive_messages(
        client,
        "/ecs/fsxn-fpolicy-server",
        keepalive_interval_seconds=MEASURED_INTERVAL_SECONDS,
    )

    assert found is True
    assert "KeepAlive messages found" in report
    # The observed interval has to come out of the log, not off a constant in
    # the documentation. That constant is what went stale.
    assert "Observed interval:" in report
    assert f"{float(MEASURED_INTERVAL_SECONDS):.1f}s" in report
    assert (
        f"configured keep_alive_interval: {MEASURED_INTERVAL_SECONDS}s" in report
    )


def test_empty_window_does_not_assert_disconnection() -> None:
    """Case 3: nothing observed is reported as nothing observed.

    This is the half of the defect that widening the window does not fix. The
    absence of an observation is not evidence of a disconnected engine, and the
    report must not say that it is.
    """
    client = EmptyLogsClient()

    found, report = diagnose_keepalive_messages(
        client,
        "/ecs/fsxn-fpolicy-server",
        keepalive_interval_seconds=MEASURED_INTERVAL_SECONDS,
    )

    assert found is False
    assert "not by itself evidence that ONTAP is disconnected" in report
    # The retired phrasing, in the forms it could plausibly return in.
    assert "NOT connected" not in report
    assert "is not connected" not in report
    # keep_alive_interval must be offered before the engine IP, since a window
    # narrower than the interval is the cheaper explanation.
    assert report.index("keep_alive_interval") < report.index("Fargate task IP")
    # STATUS_REQ arrives every 10 s by default and is a different message.
    # Naming it here is what stops a reader at DEBUG level from reading those
    # lines as KeepAlives.
    assert "STATUS_REQ" in report


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        (120, 300),  # ONTAP's PT2M default
        (10, 60),  # floor applies; a tiny interval must not give a tiny window
        (600, 1500),  # PT10M
    ],
)
def test_window_is_derived_from_the_interval(interval: int, expected: int) -> None:
    """The window follows the configured interval rather than a magic number.

    A fixed 300 s would break again on any engine configured above PT2M, and
    would leave no trace of where 300 came from.
    """
    assert keepalive_lookback_seconds(interval) == expected


def test_window_factor_exceeds_two() -> None:
    """At a factor of exactly 2 a run can still straddle two KeepAlives.

    The margin above 2 also absorbs the lag between ONTAP sending and
    CloudWatch Logs timestamping the line.
    """
    interval = MEASURED_INTERVAL_SECONDS
    assert keepalive_lookback_seconds(interval) > 2 * interval


def test_default_interval_matches_the_measured_value() -> None:
    """The default is the measured interval, not the retracted one.

    ~6 s was the figure in an earlier record; it did not reproduce.
    """
    assert DEFAULT_KEEPALIVE_INTERVAL_SECONDS == MEASURED_INTERVAL_SECONDS
