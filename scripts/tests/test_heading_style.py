"""Japanese section headings must be noun phrases, and the check must be able to fail.

Why this exists
---------------
`make headings` runs the checker, and CI runs this file. Both are needed for
different reasons.

The checker's failure mode is silence in three specific directions, and each one
looks exactly like a clean repository:

  - The heading regex stops matching this repository's syntax, so it inspects
    nothing and reports zero violations.
  - The verbal-ending regex loses an alternative, so a predicate heading passes.
  - The fence tracker breaks, so `# comment` lines inside bash blocks are
    reported as headings and the noise makes the gate get switched off.

So the tests below check the real tree, then check the checker against inputs
that must be flagged and inputs that must not. A checker that flags everything
and a checker that flags nothing both pass a one-directional test.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check_heading_style.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_heading_style", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def chk():
    assert CHECKER.is_file(), f"{CHECKER} is missing"
    return _load()


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------


def test_checker_actually_sees_this_repository(chk) -> None:
    """Guards the guard: an empty file list makes every other assertion vacuous."""
    files = chk.target_files()
    assert len(files) >= 100, (
        f"only {len(files)} markdown files found; the file discovery is probably "
        "broken, which would make the heading check pass without checking anything"
    )


def test_checker_parses_headings_in_this_repository(chk) -> None:
    """A regex that matches no heading at all would also report zero violations."""
    sample = REPO_ROOT / "docs" / "ja" / "prerequisites.md"
    assert sample.is_file()
    text = sample.read_text(encoding="utf-8")
    # Every heading in that file is already a noun phrase, so violations() is
    # empty by design. Assert on the line scan instead: force one violation.
    injected = text + "\n## テストのために違反を挿入する\n"
    assert chk.violations(injected), (
        "the heading regex did not match a heading appended to a real document; "
        "the checker is not reading this repository's markdown"
    )


def test_no_japanese_section_heading_is_a_sentence(chk) -> None:
    offenders = []
    for path in chk.target_files():
        for line_no, level, text in chk.violations(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no} {level} {text}")
    assert not offenders, (
        "these Japanese section headings end in a verb, a question or a predicate:\n  "
        + "\n  ".join(offenders)
        + "\nNominalise them and keep the assertion with a suffix "
        "(の存在 / の不在 / の不可 / の失敗 / の不成立 / の理由 …) or a modifier. "
        "See CONTRIBUTING.md. Narrative headings take "
        "<!-- allow:heading-style --> on the heading line."
    )


def test_selftest_passes(chk) -> None:
    assert chk.selftest() == 0


# --------------------------------------------------------------------------
# Negative controls: prove the checker detects a break
# --------------------------------------------------------------------------

MUST_FLAG = [
    "## パターン A: 既存環境に追加する（推奨）",  # 末尾の限定句で隠れる動詞
    "## Harvest コンテナが起動しない（/bin/sh not found）",
    "## 自分の環境で確かめる",  # 動詞終止形
    "## 構築後の検証を自動化する",
    "## なぜこの区分が必要か",  # 疑問形
    "## 責務をどう分けるか",
    "## 記録されない読み取りがあります",  # 述語文（敬体）
    "## クロスアカウントのアクセスは成立する",  # 述語文（平叙）
    "## ボリュームは AWS 側からしか消せない",  # 述語文（否定）
    "###### 深い階層でも検査する",
]

MUST_NOT_FLAG = [
    "## 自環境での確認手順",
    "## この区分が必要な理由",
    "## 記録されない読み取りの存在",
    "## 解除の不可",
    "## 追加する流れ",  # `れ` は連用形の名詞化。終止形ではない
    "## 最小権限の崩れ",
    "## 実測の遅れ",
    "## 扱う問い",  # `い` で終わる名詞。`ない` だけがリテラルで並ぶ理由
    "## 権限の扱い",
    "## 大量削除検知（ランサムウェア指標）",  # 主辞が名詞なら限定句は無害
    "## Deleting a volume",  # 英語は対象外
    "# タイトルは主張文で書く",  # H1 は対象外
    "## 15:29 気付く <!-- allow:heading-style -->",  # 叙述の明示的な許可
]


@pytest.mark.parametrize("heading", MUST_FLAG)
def test_sentence_headings_are_flagged(chk, heading: str) -> None:
    assert chk.violations(heading), f"not flagged: {heading}"


@pytest.mark.parametrize("heading", MUST_NOT_FLAG)
def test_noun_phrase_headings_are_not_flagged(chk, heading: str) -> None:
    assert not chk.violations(heading), f"wrongly flagged: {heading}"


def test_shell_comments_inside_fences_are_not_headings(chk) -> None:
    """`# comment` in a bash block is the most common false positive shape."""
    text = "```bash\n# コピー元で実行しておく\n## これも見出しではない\n```\n"
    assert not chk.violations(text)


def test_fence_tracking_reopens_after_a_closed_block(chk) -> None:
    """An off-by-one in the fence toggle would hide every heading after a block."""
    text = "```bash\n# 実行する\n```\n\n## 環境を確かめる\n"
    assert chk.violations(text), "a heading after a closed fence was not inspected"


def test_tildes_also_open_a_fence(chk) -> None:
    assert not chk.violations("~~~bash\n# 実行する\n~~~\n")


def test_allow_marker_needs_the_exact_comment(chk) -> None:
    """A near-miss marker must not silently disable the check."""
    assert chk.violations("## 気付く <!-- allow: heading -->")
