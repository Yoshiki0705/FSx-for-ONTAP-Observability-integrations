"""Tests that the documentation index stays complete and bilingual.

docs/en/README.md and docs/ja/README.md are the entry points for docs/. Their
indexes previously listed 39 of 78 English documents and 17 of 78 Japanese ones,
which left the rest reachable only by guessing a filename. Nothing reported that,
because an index that omits a file looks exactly like an index that is finished.

These tests assert the three properties that make it stay complete:

  1. every document in docs/<lang>/ appears in that language's index
  2. both indexes list the same set of documents
  3. every relative link in either README resolves

They also cover a specific defect class: a Japanese page linking to the English
copy of a document that exists in Japanese. Twelve of those were in place,
including five in the Japanese index itself, so a Japanese reader following the
operations or enterprise rows landed on English pages.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "shared" / "scripts" / "generate-docs-index.py"
LANGS = ("en", "ja")


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_docs_index", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


def _docs(lang: str) -> set[str]:
    """Every document under docs/<lang>/, as paths relative to that directory."""
    base = REPO_ROOT / "docs" / lang
    found = set()
    for path in base.rglob("*.md"):
        rel = path.relative_to(base).as_posix()
        if rel == "README.md":
            continue
        found.add(rel)
    return found


def _index_links(lang: str) -> set[str]:
    readme = (REPO_ROOT / "docs" / lang / "README.md").read_text(encoding="utf-8")
    return set(re.findall(r"\]\(([^)#]+\.md)\)", readme))


def test_every_indexed_document_is_tracked_by_git():
    """Present on disk is not the same as committed.

    `.gitignore` blanket-ignores `docs/**/verification-results*.md`, so a new
    record is skipped by `git add -A` without a word. Every other test here reads
    the filesystem, so they all pass locally while CI — which only has what was
    committed — fails on the generator with "does not exist". Comparing against
    git's index is the only way to catch that before pushing.
    """
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "docs/en", "docs/ja"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=True,
        ).stdout.split()
    )
    on_disk_but_untracked = sorted(
        f"docs/{lang}/{name}"
        for lang in ("en", "ja")
        for name in _docs(lang)
        if f"docs/{lang}/{name}" not in tracked
    )
    assert not on_disk_but_untracked, (
        "these documents are indexed but not tracked by git, so CI will not see "
        f"them: {on_disk_but_untracked}. Force-add them: "
        f"git add -f {' '.join(on_disk_but_untracked)}"
    )


@pytest.mark.parametrize("lang", LANGS)
class TestIndexCompleteness:
    def test_every_document_is_indexed(self, lang):
        missing = sorted(_docs(lang) - _index_links(lang))
        assert not missing, (
            f"docs/{lang}/README.md does not link {len(missing)} document(s): "
            f"{missing}. Add them to CATEGORIES in "
            "shared/scripts/generate-docs-index.py and regenerate."
        )

    def test_index_markers_present(self, lang):
        readme = (REPO_ROOT / "docs" / lang / "README.md").read_text(encoding="utf-8")
        assert gen.START in readme and gen.END in readme, (
            f"docs/{lang}/README.md must keep the {gen.START} / {gen.END} markers "
            "so the generator can refresh the index."
        )

    def test_relative_links_resolve(self, lang):
        base = REPO_ROOT / "docs" / lang
        readme = (base / "README.md").read_text(encoding="utf-8")
        broken = []
        for target in re.findall(r"\]\(([^)]+)\)", readme):
            if target.startswith(("http", "#", "mailto:")):
                continue
            resolved = (base / target.split("#")[0]).resolve()
            if not resolved.exists():
                broken.append(target)
        assert not broken, f"docs/{lang}/README.md has broken links: {broken}"


class TestBilingualIndexParity:
    def test_both_indexes_list_the_same_documents(self):
        en = {link for link in _index_links("en") if not link.startswith("..")}
        ja = {link for link in _index_links("ja") if not link.startswith("..")}
        assert en == ja, (
            "The English and Japanese indexes list different documents.\n"
            f"  only in en: {sorted(en - ja)}\n"
            f"  only in ja: {sorted(ja - en)}"
        )

    def test_document_filenames_match_across_languages(self):
        assert _docs("en") == _docs("ja"), (
            "docs/en and docs/ja contain different filenames.\n"
            f"  only in en: {sorted(_docs('en') - _docs('ja'))}\n"
            f"  only in ja: {sorted(_docs('ja') - _docs('en'))}"
        )


class TestJapanesePagesLinkJapanese:
    """A Japanese page must not send the reader to the English copy of a page
    that exists in Japanese. The language switcher line is the one exception --
    that is what it is for."""

    def test_no_body_links_to_english_when_japanese_exists(self):
        offenders = []
        base = REPO_ROOT / "docs" / "ja"
        for path in sorted(base.rglob("*.md")):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "🌐" in line:
                    continue
                for match in re.finditer(r"\]\(((?:\.\./)+)en/([^)#]+)", line):
                    target = match.group(2)
                    ja_equivalent = base / target
                    if ja_equivalent.exists() or ja_equivalent.is_dir():
                        rel = path.relative_to(REPO_ROOT)
                        offenders.append(f"{rel}:{lineno} -> ../en/{target}")
        assert not offenders, (
            "Japanese pages link to the English copy of documents that exist in "
            "Japanese:\n  " + "\n  ".join(offenders)
        )


class TestGenerator:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(GENERATOR), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

    def test_check_mode_reports_in_sync(self):
        result = self._run("--check")
        assert result.returncode == 0, (
            "generate-docs-index.py --check failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )

    def test_check_mode_detects_a_stale_index(self):
        """Prove --check works rather than assuming a clean result means clean."""
        readme = REPO_ROOT / "docs" / "en" / "README.md"
        original = readme.read_text(encoding="utf-8")
        mutated = re.sub(
            r"^- \[[^\]]+\]\(prerequisites\.md\)\n", "", original, count=1,
            flags=re.M,
        )
        assert mutated != original, "mutation did not apply; test needs updating"
        try:
            readme.write_text(mutated, encoding="utf-8")
            result = self._run("--check")
            assert result.returncode == 1
            assert "out of date" in result.stdout
        finally:
            readme.write_text(original, encoding="utf-8")

    def test_uncategorised_document_is_an_error(self, tmp_path):
        """A new document with no category must fail, not be quietly skipped."""
        new_docs = [
            REPO_ROOT / "docs" / lang / "zz-guard-selftest-temp.md" for lang in LANGS
        ]
        try:
            for path in new_docs:
                path.write_text("# Temporary\n", encoding="utf-8")
            result = self._run("--check")
            assert result.returncode == 1
            assert "has no category" in result.stdout
        finally:
            for path in new_docs:
                path.unlink(missing_ok=True)
