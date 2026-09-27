"""Teardown must be able to undo standup: Property 2.

Why this exists
---------------
Spec requirement 4 and Property 2: for every pattern, every resource the standup
creates must appear in that pattern's teardown delete steps, in reverse
dependency order. A verification environment that stands up an FSx for ONTAP
file system and a Kafka cluster but forgets one of them in teardown leaves it
billing silently — the exact failure the teardown discipline exists to prevent.

This is a static test: it reads the standup/delete markers each pattern's
teardown.sh declares and checks the delete set covers the standup set. It does
not deploy anything.

Marker format (see integrations/pipeline-verification/shared/teardown-template.sh):

    # ---- teardown:standup-begin ----
    # STANDUP: PrometheusServer
    # STANDUP: RemoteWriteArchiveStack
    # ---- teardown:standup-end ----
    # ---- teardown:delete-begin ----
    # DELETE: RemoteWriteArchiveStack
    # DELETE: PrometheusServer
    # ---- teardown:delete-end ----

Guarding the guard
------------------
Stage 0 ships only the template; no pattern teardown.sh exists yet, so a naive
"loop over pattern teardowns" test would pass by iterating over nothing. The
tests below parse a synthetic fixture with a known gap and a known-complete
case, so the checker's ability to detect a missing delete step is asserted
regardless of how many real pattern teardowns exist.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PV_ROOT = REPO_ROOT / "integrations" / "pipeline-verification"

STANDUP_RE = re.compile(r"^\s*#\s*STANDUP:\s*(\S+)", re.MULTILINE)
DELETE_RE = re.compile(r"^\s*#\s*DELETE:\s*(\S+)", re.MULTILINE)


def standup_set(text: str) -> list[str]:
    return STANDUP_RE.findall(text)


def delete_set(text: str) -> list[str]:
    return DELETE_RE.findall(text)


def uncovered_resources(text: str) -> set[str]:
    """Standup resources with no matching delete step."""
    return set(standup_set(text)) - set(delete_set(text))


def delete_order_is_reverse(text: str) -> bool:
    """Delete order must be the reverse of standup order for shared resources.

    Only resources present in both lists are compared; a teardown may delete
    extra scaffolding not listed as standup.
    """
    standup = standup_set(text)
    deletes = [d for d in delete_set(text) if d in standup]
    expected = [s for s in reversed(standup) if s in deletes]
    return deletes == expected


def pattern_teardowns() -> list[Path]:
    """Every pattern's scripts/teardown.sh that exists. May be empty in Stage 0."""
    return sorted(PV_ROOT.glob("pattern-*/scripts/teardown.sh"))


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------


def test_teardown_template_exists() -> None:
    """The discipline template is the Stage 0 deliverable this test guards."""
    template = PV_ROOT / "shared" / "teardown-template.sh"
    assert template.is_file(), f"{template} is missing"
    text = template.read_text(encoding="utf-8")
    # The template must carry the marker blocks patterns fill in, or the format
    # this test parses has drifted from what patterns are told to emit.
    assert "teardown:standup-begin" in text and "teardown:delete-begin" in text, (
        "the teardown template no longer carries the STANDUP/DELETE marker blocks; "
        "this reversibility test parses those markers"
    )


def test_every_pattern_teardown_covers_its_standup() -> None:
    """Property 2 over whatever pattern teardowns exist.

    Empty in Stage 0 (no pattern teardown.sh yet). The synthetic fixture tests
    below keep the checker honest until pattern teardowns land.
    """
    offenders = []
    for path in pattern_teardowns():
        text = path.read_text(encoding="utf-8")
        missing = uncovered_resources(text)
        if missing:
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}: standup resources with no delete "
                f"step: {sorted(missing)}"
            )
    assert not offenders, "\n".join(offenders)


def test_every_pattern_teardown_deletes_in_reverse_order() -> None:
    offenders = []
    for path in pattern_teardowns():
        text = path.read_text(encoding="utf-8")
        if not delete_order_is_reverse(text):
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}: delete order is not the reverse of "
                "standup order"
            )
    assert not offenders, "\n".join(offenders)


# --------------------------------------------------------------------------
# Negative controls: prove the checker detects a gap and a wrong order
# --------------------------------------------------------------------------

_COMPLETE = """\
# ---- teardown:standup-begin ----
# STANDUP: PrometheusServer
# STANDUP: RemoteWriteArchiveStack
# ---- teardown:standup-end ----
# ---- teardown:delete-begin ----
# DELETE: RemoteWriteArchiveStack
# DELETE: PrometheusServer
# ---- teardown:delete-end ----
"""

_MISSING_DELETE = """\
# ---- teardown:standup-begin ----
# STANDUP: PrometheusServer
# STANDUP: RemoteWriteArchiveStack
# ---- teardown:standup-end ----
# ---- teardown:delete-begin ----
# DELETE: PrometheusServer
# ---- teardown:delete-end ----
"""

_WRONG_ORDER = """\
# ---- teardown:standup-begin ----
# STANDUP: PrometheusServer
# STANDUP: RemoteWriteArchiveStack
# ---- teardown:standup-end ----
# ---- teardown:delete-begin ----
# DELETE: PrometheusServer
# DELETE: RemoteWriteArchiveStack
# ---- teardown:delete-end ----
"""


def test_complete_teardown_is_accepted() -> None:
    assert uncovered_resources(_COMPLETE) == set()
    assert delete_order_is_reverse(_COMPLETE)


def test_missing_delete_step_is_detected() -> None:
    assert uncovered_resources(_MISSING_DELETE) == {"RemoteWriteArchiveStack"}


def test_wrong_delete_order_is_detected() -> None:
    # Coverage is fine; order is not.
    assert uncovered_resources(_WRONG_ORDER) == set()
    assert not delete_order_is_reverse(_WRONG_ORDER)


def test_parsers_ignore_non_marker_comments() -> None:
    text = "# just a comment\n# STANDUP: A\n# DELETE: A\n# STANDUPX: B\n"
    assert standup_set(text) == ["A"]
    assert delete_set(text) == ["A"]
