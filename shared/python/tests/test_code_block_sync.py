"""Executable code blocks must be identical between docs/ja and docs/en.

The failure this guards against is quiet: someone translates a comment inside a
```bash fence, both guides still render correctly, and the Japanese reader now
runs a slightly different command from the one that was verified. Nothing breaks
at review time — it surfaces as an unreproducible support question later.

Diagram fences (untagged, ```mermaid, ```text) are deliberately excluded and
stay localised. Those are figures whose labels are prose.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "shared" / "scripts" / "sync-code-blocks.py"
JA_DIR = REPO_ROOT / "docs" / "ja"
EN_DIR = REPO_ROOT / "docs" / "en"


def _load_sync_module():
    """Import sync-code-blocks.py, whose filename is not a valid module name.

    The module has to be registered in sys.modules *before* exec_module, because
    @dataclass resolves its own module via sys.modules[cls.__module__] and fails
    with an opaque AttributeError on None if it is absent.
    """
    spec = importlib.util.spec_from_file_location("sync_code_blocks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_code_blocks = _load_sync_module()

parse_blocks = sync_code_blocks.parse_blocks
has_cjk = sync_code_blocks.has_cjk
SYNCED_LANGS = sync_code_blocks.SYNCED_LANGS


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=REPO_ROOT
    )


def _pairs() -> list[tuple[Path, Path]]:
    return [(ja, EN_DIR / ja.name) for ja in sorted(JA_DIR.glob("*.md")) if (EN_DIR / ja.name).exists()]


def test_script_exists_and_is_executable_by_python():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    assert _run("--help").returncode == 0


def test_all_synced_blocks_are_identical():
    """The whole point of the check. Run the script rather than reimplementing it,
    so the test cannot drift from the tool developers actually run."""
    result = _run("--check")
    assert result.returncode == 0, (
        "docs/ja and docs/en disagree on an executable code block.\n"
        "Fix with: python3 shared/scripts/sync-code-blocks.py\n\n" + result.stdout + result.stderr
    )


def test_no_synced_block_in_japanese_contains_cjk():
    """A CJK character inside a ```bash fence means a comment was translated.
    Checked independently of the script so a bug in the script's comparison
    cannot hide it."""
    offenders = []
    for ja_path, _ in _pairs():
        for idx, block in enumerate(parse_blocks(ja_path.read_text(encoding="utf-8").splitlines())):
            if block.lang.lower() in SYNCED_LANGS and has_cjk(block.body):
                offenders.append(f"{ja_path.name} block#{idx} (```{block.lang})")
    assert not offenders, "translated text inside executable code blocks:\n  " + "\n  ".join(offenders)


def test_fence_counts_match_between_languages():
    """Unequal fence counts make block indices meaningless, and the script skips
    those files rather than copying the wrong text. Without this test such a file
    would be silently exempt from the check above."""
    mismatched = []
    for ja_path, en_path in _pairs():
        ja_n = len(parse_blocks(ja_path.read_text(encoding="utf-8").splitlines()))
        en_n = len(parse_blocks(en_path.read_text(encoding="utf-8").splitlines()))
        if ja_n != en_n:
            mismatched.append(f"{ja_path.name}: ja={ja_n} en={en_n}")
    assert not mismatched, "fence count mismatch (blocks cannot be paired):\n  " + "\n  ".join(mismatched)


def test_language_tags_match_between_languages():
    mismatched = []
    for ja_path, en_path in _pairs():
        ja_b = parse_blocks(ja_path.read_text(encoding="utf-8").splitlines())
        en_b = parse_blocks(en_path.read_text(encoding="utf-8").splitlines())
        if len(ja_b) != len(en_b):
            continue
        for idx, (j, e) in enumerate(zip(ja_b, en_b)):
            if j.lang != e.lang:
                mismatched.append(f"{ja_path.name} block#{idx}: ja=```{j.lang} en=```{e.lang}")
    assert not mismatched, "language tag mismatch:\n  " + "\n  ".join(mismatched)


def test_diagram_blocks_are_not_forced_to_english():
    """Guards the scope decision, not just the implementation. If someone widens
    SYNCED_LANGS to cover untagged fences, the Japanese architecture diagrams get
    overwritten with English labels — a regression for the primary language. At
    least one localised diagram must survive for this to still be true."""
    assert "" not in SYNCED_LANGS and "mermaid" not in SYNCED_LANGS

    localised = 0
    for ja_path, _ in _pairs():
        for block in parse_blocks(ja_path.read_text(encoding="utf-8").splitlines()):
            if block.lang.lower() not in SYNCED_LANGS and has_cjk(block.body):
                localised += 1
    assert localised > 0, "expected Japanese diagram/output blocks to remain localised"


class TestCheckActuallyDetectsDrift:
    """`--check` returning 0 has to mean something. These mutate a real file and
    confirm the failure, then restore it."""

    def test_translated_comment_is_detected(self, tmp_path):
        target = JA_DIR / "deployment-guide.md"
        original = target.read_text(encoding="utf-8")
        backup = tmp_path / "backup.md"
        backup.write_text(original, encoding="utf-8")
        try:
            blocks = parse_blocks(original.splitlines())
            victim = next(b for b in blocks if b.lang.lower() in SYNCED_LANGS and b.body.strip())
            lines = original.splitlines()
            lines.insert(victim.start + 1, "# 日本語に翻訳されたコメント")
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")

            assert _run("--check").returncode == 1, "--check passed despite injected drift"
        finally:
            target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
        assert _run("--check").returncode == 0, "restore failed; working tree left dirty"

    def test_extra_fence_is_reported_as_a_problem(self, tmp_path):
        target = JA_DIR / "deployment-guide.md"
        original = target.read_text(encoding="utf-8")
        backup = tmp_path / "backup2.md"
        backup.write_text(original, encoding="utf-8")
        try:
            target.write_text(original + "\n```bash\necho extra\n```\n", encoding="utf-8")
            result = _run("--check")
            assert result.returncode == 1
            assert "fence count differs" in result.stdout, result.stdout
        finally:
            target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
        assert _run("--check").returncode == 0, "restore failed; working tree left dirty"


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("bash", True),
        ("BASH", True),
        ("yaml", True),
        ("json", True),
        ("python", True),
        ("sql", True),
        ("", False),
        ("mermaid", False),
        ("text", False),
    ],
)
def test_language_classification(lang, expected):
    assert (lang.lower() in SYNCED_LANGS) is expected
