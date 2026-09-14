"""Text a sibling repository's gate reads out of this one must not silently move.

Why this exists
---------------
The FSx for ONTAP Adoption Playbook cites findings from this repository and
verifies each citation with a probe: a literal string it expects to find in a
named file here. Nine were registered against `main` (see #71). The Playbook's
check fails when a probe stops matching.

That contract runs the wrong way for us to notice. The strings live in our files
and the gate lives in theirs, so an ordinary edit here -- rewording a sentence,
splitting a table, translating a heading -- breaks their check with nothing on
this side reporting it. They would find out on their next scheduled run, and the
diff that caused it would be days old by then.

This test makes the failure land in the pull request that causes it. It does not
protect the sentences from being changed: it requires the change to be
deliberate, so whoever makes it can tell the Playbook rather than surprise it.

Deliberately not networked
--------------------------
Whether the Playbook still registers these nine is their state, not ours, and
reading it would make this test depend on their repository being reachable. If
they retire a probe, this list goes stale in the harmless direction -- pinning a
string nobody reads any more. The other direction, a probe they rely on
vanishing here without warning, is the one worth a gate.

When a probe must change
------------------------
Change the sentence and this list in the same commit, and say so on #71 or its
successor. Removing an entry from PROBES is a decision about their gate, not a
formality -- the same way appending to the bandit baseline is a decision about
ours.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Registered on 2026-09-07 against main, each verified to match exactly once.
# Ordered by file so a reader can see the blast radius of touching one document.
PROBES: tuple[tuple[str, str], ...] = (
    ("docs/en/vendor-deployment-common.md", "ONTAP holds the engine's"),
    ("docs/en/vendor-deployment-common.md", "change for every path except FPolicy"),
    ("docs/en/vendor-deployment-common.md", "Throughput did not appear"),
    ("docs/en/vendor-deployment-common.md", "no cross-site placement has"),
    ("docs/ja/vendor-deployment-common.md", "到達性を足しても解消しません"),
    ("docs/ja/vendor-deployment-common.md", "スループットは現れませんでした"),
    ("docs/ja/vendor-deployment-common.md", "拠点をまたぐ配置は測定していません"),
    (
        "management-console/README.md",
        "Collection running and delivery arriving are two",
    ),
    (
        "management-console/harvest/dashboards/README.md",
        "Quota is not an example of that",
    ),
)

# The hub rather than a note, because notes get renamed. The English URL appears
# in the Japanese README too: a parity test here compares the two language
# versions, and per-language hub URLs would fail it.
#
# The three entry-point READMEs are pinned. The link also appears in
# decision-tree-management-monitoring.md, native-alternative-matrix.md and
# vendor-deployment-common.md (both languages), which are not pinned -- those
# cite the Playbook where it happens to be relevant, and a restructuring may
# legitimately drop one. The READMEs are the answer to "the link runs one way",
# so losing all three would reopen the request without anyone noticing.
PLAYBOOK_HUB = (
    "https://github.com/Yoshiki0705/FSx-for-ONTAP-Adoption-Playbook"
    "/blob/main/docs/en/domains/observability/README.md"
)
HUB_LINKING_FILES = ("README.md", "docs/en/README.md", "docs/ja/README.md")


@pytest.mark.parametrize(
    ("relative_path", "probe"),
    PROBES,
    ids=[f"{p.split('/')[-1]}::{probe[:24]}" for p, probe in PROBES],
)
def test_probe_matches_exactly_once(relative_path: str, probe: str) -> None:
    """Exactly once, not merely present.

    A second occurrence is as much of a problem as none: the Playbook counts
    matches, so a duplicated sentence turns their probe ambiguous rather than
    satisfied.
    """
    path = REPO_ROOT / relative_path
    assert path.is_file(), (
        f"{relative_path} is gone, and a sibling repository's gate reads a probe "
        "out of it. Moving the file means updating the probe on their side; see "
        "the module docstring"
    )

    count = path.read_text(encoding="utf-8").count(probe)
    assert count == 1, (
        f"{relative_path}: expected exactly 1 occurrence of {probe!r}, found "
        f"{count}. The Adoption Playbook verifies its citation of this "
        "repository with that string. If the wording changed on purpose, update "
        "PROBES here in the same commit and tell them"
    )


@pytest.mark.parametrize("relative_path", HUB_LINKING_FILES)
def test_the_playbook_hub_link_is_present(relative_path: str) -> None:
    """The one link the Playbook asked for, which closed the one-way reference.

    Pinned because it was added in response to a request rather than for a
    reason visible in this repository, so it is the kind of line a later
    restructuring drops without noticing what it was for.
    """
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert PLAYBOOK_HUB in text, (
        f"{relative_path} no longer links the Adoption Playbook observability "
        "hub. It was added to close a reference that ran one way -- see #71 -- "
        "and points at the module hub rather than a note because notes get "
        "renamed"
    )


def test_probe_list_has_no_duplicates() -> None:
    """A duplicated entry would make the count assertion above unreadable."""
    assert len(set(PROBES)) == len(PROBES), "duplicate entries in PROBES"
