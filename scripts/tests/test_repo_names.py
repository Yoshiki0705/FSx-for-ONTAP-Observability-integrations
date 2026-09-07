"""The redirect-only repository name check must be able to fail, offline.

Why this exists
---------------
`make repo-names` and `.github/workflows/cross-repo-names.yml` run the network half
weekly. This file runs the half that does not need the network, on every pull request,
because the checker's failure modes all exit 0 and a weekly green run is not read closely:

  - The URL pattern stops matching this repository's link syntax, so the walk finds no
    names and there is nothing to resolve.
  - The file walk returns an empty list (a changed `git ls-files` invocation, a suffix
    allowlist that no longer covers `.md`).
  - `normalise()` stops trimming `.git`, so every clone command reports as its own
    unresolvable repository and the real findings are lost in the noise.
  - The verdict inverts and a renamed name is reported as fine.

The resolver is injected in these tests, so a rename is simulated rather than fetched.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check_repo_names.py"

CURRENT = "FSx-for-ONTAP-Observability-integrations"
FORMER = "fsxn-observability-integrations"


def _load():
    spec = importlib.util.spec_from_file_location("check_repo_names", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def chk():
    assert CHECKER.is_file(), f"{CHECKER} is missing"
    return _load()


@pytest.fixture
def resolver():
    """Stands in for github.com. Raises with the status code the classifier reads."""
    codes = {"deleted-repo": 404, "gone-repo": 410, "throttled-repo": 403, "busy-repo": 429}

    def resolve(repo: str) -> str:
        if repo in codes:
            raise urllib.error.HTTPError(
                url="https://github.com", code=codes[repo], msg="", hdrs=None, fp=None
            )
        if repo == "unreachable-repo":
            raise urllib.error.URLError("connection refused")
        return {FORMER: CURRENT}.get(repo, repo)

    return resolve


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------


def test_walk_sees_this_repository(chk) -> None:
    """Guards the guard: an empty file list makes every other assertion vacuous."""
    files = chk.target_files()
    assert len(files) >= 100, (
        f"only {len(files)} files found; the walk is probably broken, which would make "
        "the check resolve nothing and still exit 0"
    )


def test_the_tree_references_at_least_this_repository(chk) -> None:
    """Zero matches over the real tree is the pattern breaking, not a clean tree."""
    found = chk.scan(chk.target_files())
    assert found, "no repository reference found anywhere; the URL pattern is broken"
    assert CURRENT in found, (
        f"{CURRENT} is not referenced anywhere in the tree. Either the badges in "
        "README.md were removed, or the pattern no longer matches them."
    )


def test_no_linked_name_is_a_known_former_name(chk) -> None:
    """The one finding this gate was added for, asserted without the network.

    Resolving names needs github.com, so the pull-request tier can only check the
    names it already knows moved. The weekly workflow catches the ones nobody knows
    about yet.
    """
    known_former = {
        FORMER: CURRENT,
        "fsxn-lakehouse-integrations": "FSx-for-ONTAP-Lakehouse-Integrations",
        "fsxn-s3ap-serverless-patterns": "FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns",
    }
    found = chk.scan(chk.target_files())
    offenders = {
        old: found[old] for old in known_former if old in found
    }
    assert not offenders, "\n".join(
        f"{old} -> {known_former[old]}: " + ", ".join(files)
        for old, files in offenders.items()
    )


def test_selftest_passes(chk) -> None:
    assert chk.selftest() == 0


# --------------------------------------------------------------------------
# Absolute links back into this repository: offline, so this tier can enforce it
# --------------------------------------------------------------------------


def test_every_self_link_names_a_path_that_exists(chk) -> None:
    """The name being right is what makes this one invisible to the name check."""
    findings = chk.check_self_paths(chk.target_files())
    broken = [f.message for f in findings if f.severity == chk.DEAD]
    assert not broken, "\n".join(broken)


def test_a_missing_self_link_target_is_reported(chk, tmp_path) -> None:
    url = f"https://github.com/{chk.OWNER}/{chk.THIS_REPO}/blob/main/docs/en/no-such-file.md"
    scratch = REPO_ROOT / "scratch-self-link-check.md"
    scratch.write_text(f"see [it]({url})\n", encoding="utf-8")
    try:
        findings = chk.check_self_paths([scratch])
        assert len(findings) == 1
        assert findings[0].severity == chk.DEAD
        assert "docs/en/no-such-file.md" in findings[0].message
    finally:
        scratch.unlink()


def test_a_commit_pinned_self_link_is_inconclusive_not_dead(chk) -> None:
    """A pinned ref points at a revision this tree may not hold; that is not a defect."""
    scratch = REPO_ROOT / "scratch-pinned-link-check.md"
    url = f"https://github.com/{chk.OWNER}/{chk.THIS_REPO}/blob/abc1234/docs/en/gone.md"
    scratch.write_text(f"see [it]({url})\n", encoding="utf-8")
    try:
        findings = chk.check_self_paths([scratch])
        assert len(findings) == 1
        assert findings[0].severity == chk.INCONCLUSIVE
    finally:
        scratch.unlink()


def test_sibling_repository_paths_are_out_of_scope(chk) -> None:
    """Only this tree can be resolved offline; a sibling's path needs the network."""
    assert not chk.self_path_refs(
        f"https://github.com/{chk.OWNER}/FSx-for-ONTAP-Lakehouse-Integrations/blob/main/x.md"
    )


# --------------------------------------------------------------------------
# Negative controls
# --------------------------------------------------------------------------


def test_a_renamed_name_is_reported(chk, resolver) -> None:
    findings = chk.check({FORMER: ["README.md"]}, resolver)
    assert len(findings) == 1
    assert findings[0].severity == chk.RENAMED
    assert CURRENT in findings[0].message, "the report does not say what to rename to"
    assert "README.md" in findings[0].message, "the report does not say which file to fix"


def test_a_current_name_is_not_reported(chk, resolver) -> None:
    assert not chk.check({CURRENT: ["README.md"]}, resolver)


# --------------------------------------------------------------------------
# Severity: a rename, a dead link and a failed request need different responses
# --------------------------------------------------------------------------


@pytest.mark.parametrize("repo", ["deleted-repo", "gone-repo"])
def test_a_missing_repository_is_dead_not_renamed(chk, resolver, repo: str) -> None:
    """404 and 410 are a verdict about the name, and the response is an edit."""
    findings = chk.check({repo: ["README.md"]}, resolver)
    assert len(findings) == 1
    assert findings[0].severity == chk.DEAD
    assert "dead link" in findings[0].message
    assert "README.md" in findings[0].message


@pytest.mark.parametrize("repo", ["throttled-repo", "busy-repo", "unreachable-repo"])
def test_a_failed_request_is_inconclusive(chk, resolver, repo: str) -> None:
    """403, 429 and a transport error say nothing about the name.

    This matters most when it happens to every name at once: unauthenticated requests to
    github.com can be throttled, and a run that labelled those DEAD or RENAMED would read
    as the whole tree having gone stale overnight.
    """
    findings = chk.check({repo: ["README.md"]}, resolver)
    assert len(findings) == 1
    assert findings[0].severity == chk.INCONCLUSIVE
    assert "re-run" in findings[0].message, "the report does not say not to edit anything"


def test_inconclusive_still_fails_the_run(chk, resolver) -> None:
    """A check that could not reach github.com must not report success."""
    assert chk.check({"throttled-repo": ["README.md"]}, resolver)


def test_a_clone_command_inside_a_fence_is_scanned(chk) -> None:
    """The upstream copy strips fences. Here the clone command is the point.

    Both files that carried the former name only inside a ```bash block
    (docs/en/workshop-hands-on-half-day.md, the CrowdStrike deployment guide) would
    have been missed by a fence-stripping walk.
    """
    text = "```bash\ngit clone https://github.com/Yoshiki0705/some-repo.git\n```\n"
    assert chk.occurrences(text) == {"some-repo"}


def test_dot_git_and_trailing_punctuation_are_trimmed(chk) -> None:
    """Untrimmed, each clone URL becomes its own phantom unresolvable repository."""
    assert chk.normalise("some-repo.git") == "some-repo"
    assert chk.normalise("some-repo.") == "some-repo"
    assert chk.occurrences("see https://github.com/Yoshiki0705/some-repo.") == {"some-repo"}


def test_another_owner_is_ignored(chk) -> None:
    assert chk.occurrences("https://github.com/NetApp/harvest") == set()


def test_fixture_files_are_excluded_from_name_resolution_only(chk) -> None:
    """The exclusion must be no wider than its reason.

    The reason is that these two files spell fake names as real URLs, so resolving them
    reports repositories that do not exist. That is true of the name half and says nothing
    about whether a path exists — so they stay in the walk, and in the path half.
    """
    fixtures = {CHECKER.resolve(), Path(__file__).resolve()}
    walked = {p.resolve() for p in chk.target_files()}
    assert fixtures <= walked, (
        "a fixture file was dropped from the walk itself, so check_self_paths() no longer "
        "sees it — an exclusion applied beyond the half its rationale covers"
    )

    named = chk.scan(chk.target_files())
    for fake in ("some-repo", "a-repo", "b-repo"):
        assert fake not in named, (
            f"{fake} reached name resolution; the fixture filter in scan() is not working "
            "and the run will report phantom 404s"
        )


def test_the_suffix_allowlist_misses_no_repository_reference(chk) -> None:
    """An allowlist is silent when it stops covering a file type that carries a URL.

    Measured rather than assumed: the only files holding a reference that the walk does not
    reach must be the two fixtures, which are reached but filtered later.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    walked = {p.resolve() for p in chk.target_files()}
    missed = []
    for rel in (r for r in out.split("\0") if r):
        path = REPO_ROOT / rel
        if not path.is_file() or path.resolve() in walked:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if chk.REPO_REF.search(text) or chk.SELF_PATH_REF.search(text):
            missed.append(f"{rel} (suffix {path.suffix or 'none'!r})")
    assert not missed, (
        "these files reference a repository but the suffix allowlist does not reach them:\n  "
        + "\n  ".join(missed)
        + "\nAdd the suffix to SUFFIXES in scripts/check_repo_names.py."
    )
