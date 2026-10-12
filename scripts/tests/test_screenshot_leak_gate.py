"""Allowlist hygiene, exit codes, discovery and CI wiring of the screenshot leak gate.

Why this exists
---------------
A gate that scans images can fail in silence in four ways, and each one reads
as a clean run:

  - an allowlist that grows until it excuses the leaks it was meant to catch;
  - a missing tool (tesseract, a language pack) that is treated as "nothing
    found" instead of "cannot check";
  - a file discovery that returns an empty list;
  - a Makefile or workflow edit that stops running the gate.

The OCR itself is proven by `--selftest`. Here the OCR engine is replaced by a
small shell script that prints canned TSV, which exercises everything around
it (exit codes, redaction, timeouts) without needing tesseract, so these tests
run, and fail, on any machine. None of them is skipped.

Allowlist policy mirrors scripts/tests/test_bandit_baseline.py: the count is
pinned, so an entry is a change to this file and therefore visible in review.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_screenshot_leaks.py"
ALLOWLIST = REPO_ROOT / "scripts" / "screenshot-leak-allowlist.json"
MAKEFILE = REPO_ROOT / "Makefile"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yaml"
REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"

# Zero is the target. Every entry is a screenshot the gate stops protecting for
# one rule, so the preferred response to a finding is to mask the pixels.
# Raising this number is a decision that belongs in the pull request: give the
# path, the rule, and why masking is impossible.
EXPECTED_COUNT = 0

ACCT = "".join(("2109", "8765", "4321"))
TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _load():
    spec = importlib.util.spec_from_file_location("check_screenshot_leaks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def chk():
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    return _load()


# --------------------------------------------------------------------------
# Allowlist hygiene
# --------------------------------------------------------------------------


def good_entry(**over: str) -> dict[str, str]:
    entry = {
        "path": "docs/screenshots/example.png",
        "rule": "email-address",
        "reason": "Synthetic contact on a reserved training domain, verified by eye.",
    }
    entry.update(over)
    return entry


def test_shipped_allowlist_is_valid_and_within_the_pinned_count(chk) -> None:
    entries = chk.load_allowlist(ALLOWLIST)
    assert len(entries) <= EXPECTED_COUNT, (
        f"scripts/screenshot-leak-allowlist.json has {len(entries)} entries, the pinned count is "
        f"{EXPECTED_COUNT}. Mask the screenshot instead; if that is impossible, raise EXPECTED_COUNT "
        "in this file and say why in the pull request."
    )


def test_shipped_allowlist_entries_name_tracked_files(chk) -> None:
    """An entry for a file that was renamed or deleted protects nothing and hides a gap."""
    tracked = {chk.display_path(p) for p in chk.tracked_images()}
    stale = [e.path for e in chk.load_allowlist(ALLOWLIST) if e.path not in tracked]
    assert not stale, f"allowlist entries for files that are not tracked images: {stale}"


def test_a_valid_entry_parses(chk) -> None:
    (entry,) = chk.parse_allowlist({"entries": [good_entry()]})
    assert (entry.path, entry.rule) == ("docs/screenshots/example.png", "email-address")


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {},
        {"entries": "x"},
        {"entries": [], "extra": 1},
        {"entries": [{"path": "a.png", "rule": "email-address"}]},
        {"entries": [good_entry(reason="")]},
        {"entries": [good_entry(reason="too short")]},
        {"entries": [good_entry(reason="   " + "x" * 5 + "   ")]},
        {"entries": [good_entry(rule="no-such-rule")]},
        {"entries": [good_entry(path="docs/screenshots/*.png")]},
        {"entries": [good_entry(path="/abs/path.png")]},
        {"entries": [good_entry(path="")]},
        {"entries": [{**good_entry(), "note": "extra key"}]},
        {"entries": [good_entry(), good_entry()]},
        {"entries": [good_entry(path=7)]},
    ],
    ids=[
        "not-an-object",
        "no-entries-key",
        "entries-not-a-list",
        "unknown-top-level-key",
        "missing-reason",
        "empty-reason",
        "short-reason",
        "padded-short-reason",
        "unknown-rule",
        "wildcard-path",
        "absolute-path",
        "empty-path",
        "extra-entry-key",
        "duplicate",
        "non-string",
    ],
)
def test_invalid_allowlists_are_rejected(chk, raw) -> None:
    with pytest.raises(chk.AllowlistError):
        chk.parse_allowlist(raw)


def test_allowlist_file_errors_are_reported_not_ignored(chk, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(chk.AllowlistError):
        chk.load_allowlist(bad)
    with pytest.raises(chk.AllowlistError):
        chk.load_allowlist(tmp_path / "missing.json")


def _finding(chk, path: str, rule: str):
    return chk.Finding(path, rule, "e", ["original"], (1, 1, 1, 1))


def test_an_entry_suppresses_exactly_its_path_and_rule(chk) -> None:
    entries = chk.parse_allowlist({"entries": [good_entry(path="a.png", rule=chk.RULE_EMAIL)]})
    findings = [
        _finding(chk, "a.png", chk.RULE_EMAIL),
        _finding(chk, "a.png", chk.RULE_EMAIL),
        _finding(chk, "b.png", chk.RULE_EMAIL),
        _finding(chk, "a.png", chk.RULE_ACCOUNT),
    ]
    kept, stale = chk.apply_allowlist(findings, entries)
    assert [(f.path, f.rule) for f in kept] == [("b.png", chk.RULE_EMAIL), ("a.png", chk.RULE_ACCOUNT)]
    assert stale == []


def test_an_entry_that_suppressed_nothing_is_stale(chk) -> None:
    entries = chk.parse_allowlist({"entries": [good_entry(path="gone.png")]})
    kept, stale = chk.apply_allowlist([_finding(chk, "other.png", chk.RULE_EMAIL)], entries)
    assert len(kept) == 1
    assert [e.path for e in stale] == ["gone.png"]


# --------------------------------------------------------------------------
# A stand-in tesseract
# --------------------------------------------------------------------------


def tsv(*words: str) -> str:
    rows = [TSV_HEADER]
    for i, text in enumerate(words):
        rows.append(f"5\t1\t1\t1\t{i + 1}\t1\t10\t{10 + 30 * i}\t300\t20\t90.0\t{text}")
    return "\n".join(rows) + "\n"


def make_fake(tmp_path: Path, *, mode: str = "ok", output: str = "", langs: str = "eng\njpn") -> Path:
    """Write a fake `tesseract` and return the directory to put on PATH."""
    bindir = tmp_path / f"fake-{mode}"
    bindir.mkdir(exist_ok=True)
    canned = bindir / "canned.tsv"
    canned.write_text(output or tsv("Dashboard"), encoding="utf-8")
    body = {
        "ok": f"cat {shlex.quote(str(canned))}",
        "fail": "echo boom >&2; exit 3",
        "hang": "exec sleep 30",
    }[mode]
    script = (
        "#!/bin/sh\n"
        'if [ "$1" = "--list-langs" ]; then\n'
        f"  printf 'List of available languages (2):\\n%s\\n' {shlex.quote(langs)}\n"
        "  exit 0\n"
        "fi\n"
        "cat >/dev/null\n"
        f"{body}\n"
    )
    exe = bindir / "tesseract"
    exe.write_text(script, encoding="utf-8")
    exe.chmod(0o755)
    return bindir


def make_png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (64, 48), "white").save(path)
    return path


def run_gate(args: list[str], path_dirs: list[Path], tmp_path: Path, timeout: int = 120):
    path = os.pathsep.join([str(d) for d in path_dirs] + ["/usr/bin", "/bin"])
    env = {"PATH": path, "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=timeout,
        check=False,
    )


# --------------------------------------------------------------------------
# Exit codes
# --------------------------------------------------------------------------


def test_a_clean_image_exits_0_and_says_how_many_it_scanned(tmp_path: Path) -> None:
    img = make_png(tmp_path / "clean.png")
    proc = run_gate([str(img)], [make_fake(tmp_path)], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "scanned 1 image(s)" in proc.stdout
    assert "OK: no findings" in proc.stdout


def test_a_leak_exits_1_and_the_output_is_redacted(tmp_path: Path) -> None:
    img = make_png(tmp_path / "leaky.png")
    fake = make_fake(tmp_path, output=tsv(f"arn:aws:iam::{ACCT}:role/demo"))
    proc = run_gate([str(img)], [fake], tmp_path)
    assert proc.returncode == 1, proc.stderr
    assert "aws-account-id-suspicious" in proc.stdout
    assert ACCT not in proc.stdout + proc.stderr
    assert "*" * 12 in proc.stdout


def test_a_failing_tesseract_exits_2(tmp_path: Path) -> None:
    img = make_png(tmp_path / "a.png")
    proc = run_gate([str(img)], [make_fake(tmp_path, mode="fail")], tmp_path)
    assert proc.returncode == 2
    assert "tesseract exited 3" in proc.stderr
    assert "OK: no findings" not in proc.stdout


def test_a_hanging_tesseract_times_out_with_exit_2(tmp_path: Path) -> None:
    img = make_png(tmp_path / "a.png")
    proc = run_gate(["--timeout", "1", str(img)], [make_fake(tmp_path, mode="hang")], tmp_path, timeout=60)
    assert proc.returncode == 2
    assert "timed out" in proc.stderr


def test_a_missing_language_pack_exits_2_with_an_install_hint(tmp_path: Path) -> None:
    img = make_png(tmp_path / "a.png")
    proc = run_gate([str(img)], [make_fake(tmp_path, langs="eng")], tmp_path)
    assert proc.returncode == 2
    assert "jpn" in proc.stderr and "apt-get install" in proc.stderr


def test_missing_tesseract_exits_2_with_an_install_hint(tmp_path: Path) -> None:
    """PATH holds an empty directory only, so the real tesseract (and the fake) are invisible."""
    img = make_png(tmp_path / "a.png")
    empty = tmp_path / "empty"
    empty.mkdir()
    env = {"PATH": str(empty), "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(img)],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, check=False,
    )
    assert proc.returncode == 2
    assert "tesseract not found" in proc.stderr
    assert "apt-get install" in proc.stderr and "brew install" in proc.stderr
    assert "OK: no findings" not in proc.stdout


def test_a_glob_that_matches_no_image_exits_2(tmp_path: Path) -> None:
    proc = run_gate([str(tmp_path / "*.png")], [make_fake(tmp_path)], tmp_path)
    assert proc.returncode == 2
    assert "no images matched" in proc.stderr


def test_a_path_that_does_not_exist_exits_2(tmp_path: Path) -> None:
    proc = run_gate([str(tmp_path / "nope.png")], [make_fake(tmp_path)], tmp_path)
    assert proc.returncode == 2
    assert "no such file" in proc.stderr


def test_an_unreadable_image_exits_2(tmp_path: Path) -> None:
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"this is not an image")
    proc = run_gate([str(junk)], [make_fake(tmp_path)], tmp_path)
    assert proc.returncode == 2
    assert "ERROR" in proc.stderr


def test_a_leak_plus_an_unreadable_image_exits_1_and_prints_both(tmp_path: Path) -> None:
    """A retry-on-2 habit must not be able to hide a confirmed leak."""
    leaky = make_png(tmp_path / "leaky.png")
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"this is not an image")
    fake = make_fake(tmp_path, output=tsv(f"arn:aws:iam::{ACCT}:role/demo"))
    proc = run_gate([str(leaky), str(junk)], [fake], tmp_path)
    assert proc.returncode == 1
    assert "aws-account-id-suspicious" in proc.stdout
    assert "ERROR" in proc.stderr and "junk.png" in proc.stderr


def test_a_broken_allowlist_exits_2(tmp_path: Path) -> None:
    img = make_png(tmp_path / "a.png")
    bad = tmp_path / "allow.json"
    bad.write_text('{"entries": [{"path": "a.png"}]}', encoding="utf-8")
    proc = run_gate(["--allowlist", str(bad), str(img)], [make_fake(tmp_path)], tmp_path)
    assert proc.returncode == 2
    assert "allowlist entry 0" in proc.stderr


def test_a_partial_scan_does_not_call_allowlist_entries_stale(tmp_path: Path) -> None:
    img = make_png(tmp_path / "a.png")
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps({"entries": [good_entry(path="docs/screenshots/elsewhere.png")]}), encoding="utf-8")
    proc = run_gate(["--allowlist", str(allow), str(img)], [make_fake(tmp_path)], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_selftest_fails_closed_when_the_engine_recognises_nothing(tmp_path: Path) -> None:
    """A tesseract that reads no text must not pass the self-test: the planted leaks go undetected."""
    proc = run_gate(["--selftest"], [make_fake(tmp_path)], tmp_path)
    assert proc.returncode == 2
    assert "selftest FAILED" in proc.stderr and "missed" in proc.stderr


def test_selftest_fails_closed_when_the_engine_flags_everything(tmp_path: Path) -> None:
    leak = tsv(f"arn:aws:iam::{ACCT}:role/demo")
    proc = run_gate(["--selftest"], [make_fake(tmp_path, output=leak)], tmp_path)
    assert proc.returncode == 2
    assert "false positive" in proc.stderr


def test_selftest_without_tesseract_exits_2(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    env = {"PATH": str(empty), "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--selftest"],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60, check=False,
    )
    assert proc.returncode == 2
    assert "tesseract not found" in proc.stderr


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_discovery_returns_only_tracked_images(chk, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    git(repo, "init", "-q")
    make_png(repo / "docs" / "tracked.png")
    make_png(repo / "docs" / "untracked.png")
    (repo / "docs" / "notes.md").write_text("not an image", encoding="utf-8")
    (repo / "UPPER.PNG").write_bytes((repo / "docs" / "tracked.png").read_bytes())
    git(repo, "add", "docs/tracked.png", "docs/notes.md", "UPPER.PNG")
    found = [p.relative_to(repo).as_posix() for p in chk.tracked_images(repo)]
    assert found == ["UPPER.PNG", "docs/tracked.png"]


def test_discovery_outside_a_git_repository_fails_closed(chk, tmp_path: Path) -> None:
    with pytest.raises(chk.ToolError):
        chk.tracked_images(tmp_path / "not-a-repo")


def test_default_discovery_that_yields_nothing_exits_2(chk, monkeypatch, capsys) -> None:
    monkeypatch.setattr(chk, "tracked_images", lambda root=chk.ROOT: [])
    assert chk.main([]) == 2
    assert "zero images" in capsys.readouterr().err


def test_the_real_repository_has_tracked_images(chk) -> None:
    """Guards the guard: if this returns nothing, every scan above is vacuous."""
    assert len(chk.tracked_images()) > 100


def test_directory_and_glob_arguments_expand_to_images_only(chk, tmp_path: Path) -> None:
    make_png(tmp_path / "a.png")
    (tmp_path / "sub").mkdir()
    make_png(tmp_path / "sub" / "b.PNG")
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")
    from_dir = {p.name for p in chk.expand_inputs([str(tmp_path)])}
    from_glob = {p.name for p in chk.expand_inputs([str(tmp_path / "**" / "*")])}
    assert from_dir == {"a.png", "b.PNG"} == from_glob


# --------------------------------------------------------------------------
# Wiring: Makefile, CI, requirements
# --------------------------------------------------------------------------


def _make_rule(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:(.*)$", text, re.MULTILINE)
    assert match, f"Makefile has no {name} target"
    return match.group(1)


def test_make_screenshots_runs_the_selftest_first() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert _make_rule(text, "screenshots").split() == ["screenshots-selftest"]
    body = text.split("screenshots-selftest:", 1)[1].split("\n", 2)[1]
    assert "check_screenshot_leaks.py --selftest" in body
    scan = text.split("\nscreenshots:", 1)[1].split("\n", 2)[1]
    assert "check_screenshot_leaks.py" in scan and "--selftest" not in scan


def test_screenshots_is_part_of_all_but_not_of_drift() -> None:
    """`all` mirrors CI, which runs the gate. `drift` runs in the job that has no tesseract."""
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "screenshots" in _make_rule(text, "all").split()
    assert "screenshots" not in _make_rule(text, "drift").split()
    assert "screenshots" not in _make_rule(text, "lint").split()


def test_screenshots_targets_are_declared_phony() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    phony = " ".join(re.findall(r"\.PHONY:(.*?)(?=\n[^\s]|\Z)", text, re.DOTALL)).replace("\\", " ").split()
    assert {"screenshots", "screenshots-selftest"} <= set(phony)


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def test_ci_runs_the_gate_on_every_push_and_pull_request() -> None:
    wf = _workflow()
    triggers = wf.get("on", wf.get(True))
    for event in ("push", "pull_request"):
        assert event in triggers
        assert "paths" not in (triggers[event] or {}) and "paths-ignore" not in (triggers[event] or {}), (
            f"a path filter on {event} would let an image-only change skip the gate"
        )
    job = wf["jobs"]["screenshot-leak-gate"]
    runs = [s.get("run", "") for s in job["steps"]]
    assert any(r.strip() == "make screenshots" for r in runs)
    assert any("tesseract-ocr-jpn" in r and "tesseract-ocr-eng" in r for r in runs)
    assert any("tesseract --version" in r and "--list-langs" in r for r in runs)


def test_ci_job_is_least_privilege_and_pinned() -> None:
    job = _workflow()["jobs"]["screenshot-leak-gate"]
    assert job["permissions"] == {"contents": "read"}
    for step in job["steps"]:
        uses = step.get("uses")
        if uses:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", uses), f"not SHA-pinned: {uses}"
        if uses and uses.startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
        assert "${{" not in step.get("run", ""), "expression in run: is a script-injection risk"


def test_pillow_is_pinned_exactly() -> None:
    lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    pins = [ln for ln in lines if re.fullmatch(r"pillow==\d+(\.\d+)*", ln.strip())]
    assert len(pins) == 1, "requirements-dev.txt must pin Pillow to one exact version"
