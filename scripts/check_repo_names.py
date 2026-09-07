#!/usr/bin/env python3
"""Fail on a sibling-repository name that only still resolves through a redirect.

Why this exists
---------------
Renaming a repository on GitHub is normal and the old name keeps working. That is the
problem. Every link built on the old name reports reachable, so a link checker is silent,
and two documents can spell the same repository differently with nothing to compare them
against. Seven renamed repositories went undetected this way in the sibling
FSx-for-ONTAP-Adoption-Playbook, and **this repository's own former name
(`fsxn-observability-integrations`) was one of them** — corrected in two passes, because a
document added the old name again a day after the first sweep. A one-time find-and-replace
does not hold; a gate does.

Ported from `tools/check_cross_repo.py::check_repo_names()` in that repository. Two
deliberate differences, both because the failure being caught is different here:

  - **Fenced code blocks are scanned, not stripped.** The upstream copy blanks fences so
    an example link inside one is not mistaken for a citation. Here the single most
    damaging occurrence of a stale name is `git clone https://github.com/...`, which lives
    inside a ```bash fence and is a command a reader runs. Stripping fences would skip
    exactly the lines that matter. (Verified: `docs/en/workshop-hands-on-half-day.md` and
    `docs/customer/crowdstrike-logscale-deployment-guide.md` carried the old name only
    inside fences.)
  - **This repository's own name is resolved too, not skipped.** The upstream copy skips
    self-references, which is right for a citation table. It is wrong for a rename gate:
    if this repository is renamed again, its own name becomes the redirect-only one, and a
    skip would make the gate silent about the case it exists for.

Needs the network, so it does not run per-PR — see `.github/workflows/cross-repo-names.yml`
for why, and `Makefile` (`repo-names`) for the local entry point.

Usage:
  python3 scripts/check_repo_names.py --selftest   # prove the check can fail, offline
  python3 scripts/check_repo_names.py             # resolve every name found in the tree
  python3 scripts/check_repo_names.py --list      # print occurrences without the network
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent

# This checker and its test are excluded from **name resolution only**. Both spell
# deliberately fake names (`some-repo`, `a-repo`) as real URLs, because the pattern under
# test only matches real ones — so resolving them reports repositories that do not exist,
# and the 404s bury the findings that matter. Verified: before this exclusion, a run
# reported `some-repo: cannot resolve` alongside a genuine rename.
#
# Two ways this exclusion could be drawn too wide, both avoided deliberately:
#
#   - **Not "anything under tests/".** That would be a blind spot wide enough to hide a
#     stale name in a fixture a reader does copy.
#   - **Not the whole walk.** The rationale above is about *fake names*; it says nothing
#     about whether a path exists. Applied to `check_self_paths()` as well it would exclude
#     these files from a check its justification does not cover — a correct comment
#     licensing a wider exclusion than it supports. So the filter lives in `scan()`, next to
#     the half it is true of, and the path half sees every file.
FIXTURE_FILES = frozenset(
    {
        Path(__file__).resolve(),
        (Path(__file__).resolve().parent / "tests" / "test_repo_names.py"),
    }
)

OWNER = "Yoshiki0705"

# Used only to recognise a link that points back into this tree, so its path can be resolved
# offline. It is deliberately NOT used to skip resolving our own name: if this repository is
# renamed, the name half flags this constant as redirect-only, which is what makes the
# constant self-correcting rather than a second place to forget.
THIS_REPO = "FSx-for-ONTAP-Observability-integrations"

# Any reference to a repository under OWNER, in prose or in a command. Kept deliberately
# wider than "links in prose": a stale name in a clone command breaks a reader's copy-paste
# just as surely as a stale name in a sentence.
REPO_REF = re.compile(rf"github\.com/{OWNER}/(?P<repo>[A-Za-z0-9._-]+)")

# An absolute URL pointing back into this repository. These are the one kind of path-bearing
# link that can be checked without the network and without a citation index, because the
# target is the tree the checker is already standing in. Links into a *sibling* repository
# need both, which is why only the name half of those is covered here.
SELF_PATH_REF = re.compile(
    rf"github\.com/{OWNER}/{THIS_REPO}/(?P<kind>blob|tree|raw)/(?P<ref>[^/\s)\"']+)/"
    rf"(?P<path>[^\s)\"'#>]+)"
)

# Suffixes worth reading. An allowlist rather than "every tracked file" so the walk cannot
# wander into a lockfile or a binary and report a match nobody can act on.
SUFFIXES = (".md", ".txt", ".yaml", ".yml", ".sh", ".py", ".json", ".ts", ".tsx", ".go")

# Only used when git is unavailable (a tarball checkout). git is the preferred source
# because it excludes ignored drafts, which exist locally and not in CI; an rglob would
# make the gate red locally and green in CI for the same tree.
SKIP_PARTS = {
    ".git",
    ".venv",
    ".hypothesis",
    ".kiro",
    ".private",
    "__pycache__",
    "node_modules",
}

USER_AGENT = "repo-name-check"
TIMEOUT_SECONDS = 30


def normalise(repo: str) -> str:
    """Trim what a URL carries but a repository name does not.

    `https://github.com/OWNER/name.git` and a name at the end of a sentence both match the
    pattern with something extra attached. Left in place, every clone command reports as a
    separate unresolvable repository — five of them in this tree at the time of writing.
    """
    name = repo.rstrip(".")
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name.rstrip(".")


def target_files() -> list[Path]:
    """Tracked files plus untracked-but-not-ignored ones.

    `--others` is what makes a local run catch a bad name **before** it is committed. The
    dead link this gate found first (`fsxn-s3ap-serverless-patterns`, HTTP 404) was in
    untracked files, and that is where it was caught.

    The scheduled run has a narrower scope, and that scope is correct rather than deficient:
    CI checks out committed content, so a green weekly run is a true statement about what is
    published and **says nothing either way about a working tree**. Both halves are worth
    keeping straight, because reading either one as the other loses something:

      - a local failure appears before the commit, which is the cheapest place to fix it
      - a green schedule does not include untracked work, so it is not a substitute for
        running this before committing

    `--exclude-standard` keeps gitignored drafts out, so the gate is not red locally and
    green in CI for the same committed content.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        paths = [ROOT / rel for rel in out.split("\0") if rel]
    except (OSError, subprocess.CalledProcessError):
        paths = [
            p
            for p in ROOT.rglob("*")
            if not any(part in SKIP_PARTS for part in p.relative_to(ROOT).parts)
        ]
    return sorted(p for p in paths if p.is_file() and p.suffix in SUFFIXES)


def occurrences(text: str) -> set[str]:
    """Repository names referenced in one file's text. Fences are read, not skipped."""
    return {normalise(m.group("repo")) for m in REPO_REF.finditer(text)}


def scan(files: list[Path]) -> dict[str, list[str]]:
    """Map each referenced repository name to the files that spell it that way.

    Fixture files are dropped here rather than in `target_files()`, so the exclusion applies
    to the one half its rationale covers. See `FIXTURE_FILES`.
    """
    seen: dict[str, list[str]] = {}
    for path in files:
        if path.resolve() in FIXTURE_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for repo in occurrences(text):
            seen.setdefault(repo, []).append(rel)
    return {repo: sorted(files_) for repo, files_ in sorted(seen.items())}


def self_path_refs(text: str) -> set[tuple[str, str]]:
    """`(ref, path)` for every absolute URL in `text` that points back into this tree."""
    return {
        (m.group("ref"), m.group("path").rstrip(".,;:)"))
        for m in SELF_PATH_REF.finditer(text)
    }


def check_self_paths(files: list[Path]) -> list[Finding]:
    """Absolute links into this repository must name a path that exists here.

    Offline and deterministic, so unlike the name half this runs per-PR. It covers the
    narrow case worth covering for free: a document links to
    `github.com/OWNER/THIS_REPO/blob/main/docs/...`, the file is later renamed, and the link
    keeps returning a GitHub 404 page that no gate here reads. The name is correct, so the
    name check passes; the path is gone, and nothing says so.

    A ref that is not `main` is reported as unverifiable rather than as a defect: a
    commit-pinned link points at a revision this working tree may not contain, and calling
    that broken would be wrong.
    """
    findings: list[Finding] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for ref, target in sorted(self_path_refs(text)):
            if ref != "main":
                findings.append(
                    Finding(
                        INCONCLUSIVE,
                        THIS_REPO,
                        f"{rel}: links into this repository at ref {ref!r} ({target}), "
                        "which cannot be resolved against the working tree. Verify by hand.",
                    )
                )
                continue
            if not (ROOT / target).exists():
                findings.append(
                    Finding(
                        DEAD,
                        THIS_REPO,
                        f"{rel}: links to {target} in this repository, which does not "
                        "exist. The repository name is correct, so the name check passes "
                        "— the path is what moved.",
                    )
                )
    return findings


def resolve(repo: str) -> str:
    """Canonical name GitHub redirects `repo` to. Raises on an unreachable name."""
    request = urllib.request.Request(
        f"https://github.com/{OWNER}/{repo}", headers={"User-Agent": USER_AGENT}
    )
    # The scheme and host are literals here and `repo` comes from REPO_REF, which admits
    # only [A-Za-z0-9._-] -- no colon, no slash, so neither the scheme nor the host can be
    # redirected by input. Suppressed narrowly rather than added to the bandit baseline:
    # the baseline is asserted by scripts/tests/test_bandit_baseline.py precisely so it
    # cannot become a place to park findings.
    with urllib.request.urlopen(  # noqa: S310  # nosec B310
        request, timeout=TIMEOUT_SECONDS
    ) as response:
        final = response.url
    return final.rstrip("/").rsplit("/", 1)[-1]


# Three outcomes, not two. The upstream copy folds everything that is not a clean redirect
# into one "cannot resolve" line, and that conflates findings a reader must act on
# differently:
#
#   RENAMED      the link works and is stale. Nothing is broken for a reader today; it
#                breaks the ability to tell two documents apart. Not urgent, invisible.
#   DEAD         the link does not work. A reader following it gets a 404 now. Urgent, and
#                an ordinary link checker would already have caught it — a DEAD finding here
#                means no link checker ran, which is its own thing to fix.
#   INCONCLUSIVE the request failed for a reason that says nothing about the name. Rate
#                limiting is the one that matters: unauthenticated requests to github.com
#                can return 403 or 429, and when that happens EVERY name reports at once.
#                Without the label, a scheduled run's output reads as though the whole tree
#                went stale overnight and someone starts editing files.
RENAMED = "renamed"
DEAD = "dead"
INCONCLUSIVE = "inconclusive"

# Codes that are a verdict about the name, versus codes that are a verdict about the request.
DEAD_CODES = frozenset({404, 410})
THROTTLE_CODES = frozenset({403, 429})


class Finding(NamedTuple):
    severity: str
    repo: str
    message: str


def classify(repo: str, exc: BaseException) -> Finding:
    """Turn a failed lookup into one of DEAD or INCONCLUSIVE.

    The information is in the status code, so it is read there rather than inferred from the
    exception type: `HTTPError` covers 404 and 403 alike and they mean opposite things.
    """
    code = getattr(exc, "code", None)
    if code in DEAD_CODES:
        return Finding(
            DEAD,
            repo,
            f"{repo} does not exist (HTTP {code}). This is a dead link, not a rename — a "
            "reader following it gets an error today, and an ordinary link check would "
            "have reported it.",
        )
    if code in THROTTLE_CODES:
        return Finding(
            INCONCLUSIVE,
            repo,
            f"{repo}: refused (HTTP {code}), most likely rate limiting rather than anything "
            "about the name. Do not edit files on the strength of this line; re-run it.",
        )
    return Finding(
        INCONCLUSIVE,
        repo,
        f"{repo}: lookup failed ({exc}). This says nothing about whether the name is "
        "current. Do not edit files on the strength of this line; re-run it.",
    )


def check(
    found: dict[str, list[str]], resolver: Callable[[str], str] = resolve
) -> list[Finding]:
    findings: list[Finding] = []
    for repo, files in found.items():
        listed = ", ".join(files[:4]) + (" …" if len(files) > 4 else "")
        try:
            canonical = resolver(repo)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            finding = classify(repo, exc)
            findings.append(finding._replace(message=f"{finding.message} Seen in: {listed}"))
            continue
        if canonical != repo:
            findings.append(
                Finding(
                    RENAMED,
                    repo,
                    f"{repo} resolves to {canonical}. The old name still works through a "
                    f"redirect, so no link check reports it. Update: {listed}",
                )
            )
    return findings


# --- self-test -------------------------------------------------------------
# A gate that needs the network has two ways to pass while checking nothing: the pattern
# stops matching, or the file walk returns an empty list. Both exit 0, and on a weekly
# schedule nobody is watching closely enough to notice. These cases run offline against an
# injected resolver and assert both directions.

RENAMES = {
    "fsxn-observability-integrations": "FSx-for-ONTAP-Observability-integrations",
    "fsxn-lakehouse-integrations": "FSx-for-ONTAP-Lakehouse-Integrations",
}

PARSE_CASES: list[tuple[str, set[str]]] = [
    ("see https://github.com/Yoshiki0705/some-repo for details", {"some-repo"}),
    # The case fences must not hide: a clone command a reader copies.
    (
        "```bash\ngit clone https://github.com/Yoshiki0705/some-repo.git\n```\n",
        {"some-repo"},
    ),
    ("[repo](https://github.com/Yoshiki0705/some-repo).", {"some-repo"}),
    ("badge: api.scorecard.dev/projects/github.com/Yoshiki0705/some-repo/badge", {"some-repo"}),
    ("https://github.com/NetApp/harvest is another owner", set()),
    ("no repository reference here", set()),
    (
        "https://github.com/Yoshiki0705/a-repo and https://github.com/Yoshiki0705/b-repo",
        {"a-repo", "b-repo"},
    ),
]


def selftest() -> int:
    bad: list[str] = []

    for text, want in PARSE_CASES:
        got = occurrences(text)
        if got != want:
            bad.append(f"parse: {text!r} gave {sorted(got)}, expected {sorted(want)}")

    def fake(repo: str) -> str:
        codes = {"gone": 404, "throttled": 403}
        if repo in codes:
            raise urllib.error.HTTPError(
                url="https://github.com", code=codes[repo], msg="", hdrs=None, fp=None
            )
        if repo == "offline":
            raise urllib.error.URLError("connection refused")
        return RENAMES.get(repo, repo)

    def one(repo: str) -> Finding | None:
        got = check({repo: ["README.md"]}, fake)
        if len(got) != 1:
            bad.append(f"{repo}: expected exactly one finding, got {got}")
            return None
        return got[0]

    # A current name must pass.
    if check({"FSx-for-ONTAP-Observability-integrations": ["README.md"]}, fake):
        bad.append("a canonical name was flagged")

    # A redirect-only name must be RENAMED, and must name the file to fix.
    if (f := one("fsxn-observability-integrations")) and (
        f.severity != RENAMED or "README.md" not in f.message
    ):
        bad.append(f"a renamed name was misreported: {f}")

    # 404 must be DEAD, not folded in with a network failure. The two need different
    # responses: one is an edit, the other is a re-run.
    if (f := one("gone")) and f.severity != DEAD:
        bad.append(f"a 404 was classified {f.severity}, expected {DEAD}")

    # Rate limiting must be INCONCLUSIVE. Classified as DEAD or RENAMED, a throttled run
    # reports every name in the tree as broken.
    if (f := one("throttled")) and f.severity != INCONCLUSIVE:
        bad.append(f"HTTP 403 was classified {f.severity}, expected {INCONCLUSIVE}")

    # A transport failure carries no status code and must not be read as a verdict.
    if (f := one("offline")) and f.severity != INCONCLUSIVE:
        bad.append(f"a transport error was classified {f.severity}, expected {INCONCLUSIVE}")

    # The offline half must parse a self-link and must distinguish a pinned ref.
    live = f"https://github.com/{OWNER}/{THIS_REPO}/blob/main/README.md"
    if self_path_refs(f"see [it]({live}).") != {("main", "README.md")}:
        bad.append("a self-referential blob link was not parsed, or its path not trimmed")
    if self_path_refs(f"https://github.com/{OWNER}/other-repo/blob/main/README.md"):
        bad.append("a link into a sibling repository was treated as a self-link")

    # The walk must find something. An empty list exits 0 while checking nothing.
    if not target_files():
        bad.append("target_files() found no files to scan")

    for line in bad:
        print(f"selftest FAIL: {line}", file=sys.stderr)
    if bad:
        return 1
    print(f"selftest: {len(PARSE_CASES) + 8} case(s) passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Prove offline that the check flags a renamed name and passes a current one.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print every repository name found and where, without resolving any.",
    )
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    files = target_files()
    found = scan(files)

    if args.list:
        for repo, files in found.items():
            print(f"{repo}\n  " + "\n  ".join(files))
        print(f"\n{len(found)} repository name(s) referenced")
        return 0

    if not found:
        # Nothing to resolve is not a pass. This repository links its own siblings in the
        # README; zero matches means the walk or the pattern broke.
        print(
            "repo-names: no repository reference found at all. Expected at least this "
            "repository's own name — the file walk or the pattern is broken.",
            file=sys.stderr,
        )
        return 1

    # Offline half first: it needs no network, so it should report even if the network half
    # cannot run at all.
    findings = check_self_paths(files) + check(found)
    if not findings:
        print(
            f"repo-names: {len(found)} name(s) resolve without a redirect, and every "
            "absolute link into this repository names a path that exists"
        )
        return 0

    # Grouped by severity, worst first, because the three call for different responses and
    # an interleaved list makes a throttled run look like a stale tree.
    headings = {
        DEAD: "DEAD — the link does not work. Fix now:",
        RENAMED: "RENAMED — the link works and is stale. Fix, unhurried:",
        INCONCLUSIVE: "INCONCLUSIVE — no verdict about the name. Change nothing; re-run:",
    }
    print(f"repo-name check failed ({len(findings)} finding(s)):")
    for severity, heading in headings.items():
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue
        print(f"\n  {heading}")
        for finding in group:
            print(f"    {finding.message}")

    if all(f.severity == INCONCLUSIVE for f in findings):
        print(
            "\n  Every finding is inconclusive, so this run learned nothing about the tree. "
            "That is still a failure: a check that cannot reach github.com must not report "
            "success.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
