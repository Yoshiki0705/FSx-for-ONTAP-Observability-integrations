#!/usr/bin/env python3
"""日本語の節見出し（`##` 以下）が体言止めであることを検査する。

動詞終止形・疑問形・述語文は、読者がラベルを期待する位置に文を置くので読みにくい。
規約と変換例は CONTRIBUTING.md「日本語の節見出しは体言止め」にある。ここには
検出の実装だけを置き、規約本体は重複させない。

対象外:
  - H1 と frontmatter の `title`（1 行の主張文という別規約に従う）
  - 英語の見出し
  - コードフェンス内の `#` 行（シェルのコメント）
  - 行に `<!-- allow:heading-style -->` がある見出し（叙述・助言・抱負）

使い方:
  python3 scripts/check_heading_style.py --selftest   # 検査が落ちる能力の確認
  python3 scripts/check_heading_style.py              # 本検査
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# git がない環境（tarball 展開など）でのフォールバック時にだけ使う。
SKIP = {
    ".git",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".private",
    ".kiro",
    ".hypothesis",
}

HEADING = re.compile(r"^(#{2,6})\s+(.*?)\s*$")
FENCE = re.compile(r"^\s*(?:```|~~~)")
ALLOW = re.compile(r"<!--\s*allow:heading-style\s*-->")
JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龠]")

# 判定は行末に固定されるので、末尾に何か付いているだけで動詞が隠れる。実測で
# 387 件の見出しが括弧付きの限定句か強調記号で終わっており、そのうち 7 件は
# 「〜する（推奨）」「〜が起動しない（/bin/sh not found）」のように、括弧を外すと
# 述語文だった。剥がさない検査は、この 7 件を無言で通していた。
#
# 剥がすのは末尾の括弧・角括弧・鉤括弧の対、強調記号（* _ `）、および HTML
# コメント。中身は見ない。「大量削除検知（ランサムウェア指標）」のように主辞が
# 名詞なら、剥がした後も名詞のままなので判定は変わらない。
COMMENT = re.compile(r"<!--.*?-->")
TRAILING_QUALIFIER = re.compile(
    r"(?:[（(【\[「『][^（()）【】\[\]「」『』]*[）)】\]」』]|[*_`]+)\s*$"
)


def head_noun(text: str) -> str:
    """見出しの主辞。末尾の限定句を剥がしきった残り。"""
    stripped = COMMENT.sub("", text).strip()
    previous = None
    while previous != stripped:
        previous = stripped
        stripped = TRAILING_QUALIFIER.sub("", stripped).strip()
    return stripped

# 文字クラスはう段だけ。動詞の終止形はう段で終わる。
#
#   `れ` を入れてはいけない。え段であって終止形にはならず、単独の `れ` は連用形の
#   名詞化（流れ / 崩れ / 遅れ / ずれ）。入れると閉じられない名詞クラスを誤検出し、
#   許可リストでは対処できない。
#
#   `ない` は個別に列挙する。`い$` で一括にしてはいけない。平叙の否定（…できない）は
#   文だが、`問い` `扱い` は名詞である。列挙を省くと否定の述語見出しを無言で通す。
VERBAL = re.compile(
    r"(?:ます|ません|ました|でした|です|ください|でしょうか|のか|か|ない|[うくぐすずつぬふぶむる])$"
)

# 名詞の許可リストは置かない。`れ` をクラスから外した時点で、許可リストの全語が
# そもそも VERBAL に一致しなくなる。発火しない許可リストは、提供していない保証を
# 表明することになる。効いているのは「`ない` をリテラルにした」点だけ。


def violations(text: str) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    in_fence = False
    for n, line in enumerate(text.split("\n"), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or ALLOW.search(line):
            continue
        m = HEADING.match(line)
        if not m:
            continue
        h = ALLOW.sub("", m.group(2)).strip()
        core = head_noun(h)
        if not JAPANESE.search(core):
            continue
        if VERBAL.search(core):
            found.append((n, m.group(1), h))
    return found


def target_files() -> list[Path]:
    """git の索引から対象を決める。ローカルと CI が別の木を見ないようにする。

    `--cached --others --exclude-standard` なので、追跡済みと「まだ追跡されていない
    が gitignore されてもいない」新規ファイルの両方が入り、gitignore された草稿
    （docs/blog/, docs/internal/, .private/）は入らない。rglob だとローカルにだけ
    存在する草稿でゲートが赤くなり、CI では緑になる。
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "*.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return [
            p
            for p in sorted(ROOT.rglob("*.md"))
            if not any(part in SKIP for part in p.parts)
        ]
    paths = {ROOT / rel for rel in out.split("\0") if rel}
    return sorted(p for p in paths if p.is_file())


# 両方向を証明する。落ちない検査は、検査が無いのと区別できない。
CASES = [
    ("## 自分の環境で確かめる", True),
    ("## 検証を自動化する", True),
    ("## なぜこの区分が必要か", True),
    ("## どう分けるか", True),
    ("## 読み取りがあります", True),
    ("## 面に分かれました", True),
    ("## 既定は「同一」です", True),
    ("## アクセスは成立する", True),
    ("## AWS 側からしか消せない", True),
    ("## この経路を見ていない", True),
    ("## 自環境での確認手順", False),
    ("## 必要な理由", False),
    ("## 読み取りの存在", False),
    ("## 解除の不可", False),
    ("## 追加する流れ", False),
    ("## 最小権限の崩れ", False),
    ("## 実測の遅れ", False),
    ("## 扱う問い", False),
    ("## 権限の扱い", False),
    ("## よくある誤解", False),
    ("## 判断フロー", False),
    ("## ログの保存先", False),
    ("## リスクの一覧", False),
    ("## Deleting a volume", False),
    ("## How to choose", False),
    ("# タイトルは主張文で書く", False),
    ("## 15:29 気付く <!-- allow:heading-style -->", False),
    # 末尾の限定句で動詞が隠れる形。剥がしてから判定する。
    ("## パターン A: 既存環境に追加する（推奨）", True),
    ("## Harvest コンテナが起動しない（/bin/sh not found）", True),
    ("## 誰が無効化できるか（改ざん耐性）", True),
    ("## **確かめる**", True),
    ("## 気付く <!-- allow: heading -->", True),  # 綴り違いの許可は許可ではない
    ("## 大量削除検知（ランサムウェア指標）", False),
    ("## パス 2: 管理監査ログ（Syslog VPC Endpoint → CloudWatch Logs）", False),
    ("## FISC ガイドライン（日本金融業界）", False),
]


def selftest() -> int:
    bad = [(c, want) for c, want in CASES if bool(violations(c)) != want]
    if violations("```bash\n# コピー元で実行しておく\n```\n"):
        bad.append(("fence", False))
    for c, want in bad:
        print(f"selftest FAIL (expected flag={want}): {c}", file=sys.stderr)
    if bad:
        return 1
    print(f"selftest: {len(CASES) + 1} case(s) passed")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    total = 0
    files = 0
    for p in target_files():
        hits = violations(p.read_text(encoding="utf-8"))
        if not hits:
            continue
        print(f"\n{p.relative_to(ROOT)}")
        for n, h, t in hits:
            print(f"  L{n:>4} {h} {t}")
        total += len(hits)
        files += 1
    if total:
        print(
            f"\n{total} 件 / {files} ファイルが体言止めではありません。"
            "接尾語で断定を保って名詞化してください。",
            file=sys.stderr,
        )
        return 1
    print("heading style: all Japanese section headings are noun phrases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
