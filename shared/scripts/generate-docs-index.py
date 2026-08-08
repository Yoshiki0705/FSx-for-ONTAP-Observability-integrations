#!/usr/bin/env python3
"""Generate the documentation index in docs/en/README.md and docs/ja/README.md.

Both indexes come from the one CATEGORIES map below, so they cannot drift into
listing different documents. Before this existed, docs/en/README.md indexed 39 of
the 78 English documents and docs/ja/README.md indexed 17 of the 78 Japanese ones,
which left 39 English and 61 Japanese documents reachable only by guessing the
filename.

Any file in docs/<lang>/ that is not in the map is an error rather than a silent
omission -- a new document added without a category would otherwise be invisible
from the index, which is the state this replaced.

Usage:
    python3 shared/scripts/generate-docs-index.py           # rewrite both READMEs
    python3 shared/scripts/generate-docs-index.py --check    # exit 1 if out of date
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
START = "<!-- docs-index:start -->"
END = "<!-- docs-index:end -->"

# (english label, japanese label, [document stems in display order])
CATEGORIES: list[tuple[str, str, list[str]]] = [
    (
        "Getting Started",
        "はじめに",
        [
            "getting-started",
            "prerequisites",
            "quick-start-minimum",
            "vendor-deployment-common",
            "deployment-guide",
            "ontap-audit-setup",
        ],
    ),
    (
        "Architecture & Reference",
        "アーキテクチャ・リファレンス",
        [
            "architecture",
            "architecture-evolution-syslog-vpce",
            "event-sources",
            "normalized-event-schema",
            "s3ap-fsxn-specification",
            "s3-access-points-knowledge",
            "ontap-rest-api-reference",
        ],
    ),
    (
        "Operations",
        "運用",
        [
            "operational-guide",
            "pipeline-slo",
            "delivery-guarantees",
            "retention-policy-matrix",
            "pagerduty-escalation-guide",
            "syslog-vpce-setup-guide",
            "cloudwatch-log-alarm",
        ],
    ),
    (
        "Runbooks",
        "Runbook",
        [
            "runbooks/dlq-replay",
            "runbooks/lambda-errors",
            "runbooks/checkpoint-stale",
            "runbooks/log-alarm-triggered",
        ],
    ),
    (
        "Security & Detection",
        "セキュリティ・検知",
        [
            "security-best-practices",
            "security-review-checklist",
            "security-monitoring-index",
            "detection-use-cases",
            "ems-detection-capabilities",
            "cyber-resilience-capability-map",
            "webhook-security",
        ],
    ),
    (
        "Automated Response",
        "自動インシデント対応",
        [
            "automated-response-guide",
            "automated-response-security-addendum",
            "arp-incident-response-guide",
            "verified-recovery-point-guide",
            "content-classification-scanner",
        ],
    ),
    (
        "FPolicy",
        "FPolicy",
        [
            "fpolicy-quick-deploy",
            "fpolicy-operational-guide",
            "fpolicy-production-architecture-patterns",
            "fpolicy-poc-checklist",
            "operational-notes-fpolicy",
            "agent-fpolicy-correlation-pattern",
        ],
    ),
    (
        "Governance & Compliance",
        "ガバナンス・コンプライアンス",
        [
            "governance-and-compliance",
            "compliance-evidence-pack",
            "data-classification",
            "data-residency",
        ],
    ),
    (
        "Enterprise & Scale",
        "エンタープライズ・スケール",
        [
            "multi-account-deployment",
            "cross-region-replication",
            "lakehouse-long-term-retention",
            "lakehouse-monitoring-patterns",
        ],
    ),
    (
        "Choosing an Approach",
        "アプローチの選択",
        [
            "decision-tree-management-monitoring",
            "native-alternative-matrix",
            "vendor-comparison",
            "ec2-comparison",
            "existing-audit-tool-coexistence",
            "file-access-audit-format-comparison",
            "system-manager-gui-guide",
            "observability-integration-addendum",
        ],
    ),
    (
        "Cost",
        "コスト",
        ["cost-model", "cost-validation", "s3ap-throughput-benchmark"],
    ),
    (
        "Partner & Workshop",
        "パートナー・ワークショップ",
        [
            "partner-solution-brief",
            "partner-faq",
            "poc-success-criteria",
            "poc-proposal-template",
            "workshop-agenda",
            "workshop-hands-on-half-day",
        ],
    ),
    (
        "Demos & Screenshots",
        "デモ・スクリーンショット",
        [
            "demo-scenarios",
            "demo-automated-response",
            "demo-arp-incident-response",
            "demo-content-classification",
            "screenshot-capture-guide-ems-fpolicy",
        ],
    ),
    (
        "Verification Results",
        "検証結果",
        [
            "verification-results-datadog",
            "verification-results-splunk",
            "verification-results-otel-collector",
            "verification-results-new-relic",
            "verification-results-elastic",
            "verification-results-dynatrace",
            "verification-results-sumo-logic",
            "verification-results-honeycomb",
            "verification-results-ems-fpolicy",
        ],
    ),
    ("Project", "プロジェクト", ["ci-policy"]),
]

HEADING = {"en": "### All documents", "ja": "### ドキュメント一覧"}
INTRO = {
    "en": (
        "Every document in this directory, by category. Generated by "
        "`shared/scripts/generate-docs-index.py` from a single category map, so the\n"
        "English and Japanese indexes always list the same set."
    ),
    "ja": (
        "このディレクトリの全ドキュメントをカテゴリ別に掲載しています。"
        "`shared/scripts/generate-docs-index.py` が単一のカテゴリ表から生成するため、\n"
        "日本語版と英語版は常に同じ集合を列挙します。"
    ),
}


def title_of(path: Path) -> str:
    """First level-1 heading of a document, minus decorations."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def render(lang: str) -> str:
    docs_dir = REPO_ROOT / "docs" / lang
    lines = [START, "", HEADING[lang], "", INTRO[lang], ""]

    for en_label, ja_label, stems in CATEGORIES:
        lines.append(f"**{en_label if lang == 'en' else ja_label}**")
        lines.append("")
        for stem in stems:
            path = docs_dir / f"{stem}.md"
            if not path.exists():
                raise SystemExit(
                    f"ERROR: {path} is listed in CATEGORIES but does not exist."
                )
            lines.append(f"- [{title_of(path)}]({stem}.md)")
        lines.append("")

    lines.append(END)
    return "\n".join(lines) + "\n"


def categorised_stems() -> set[str]:
    return {stem for _, _, stems in CATEGORIES for stem in stems}


def check_coverage() -> list[str]:
    """Every document in either language directory must have a category."""
    problems = []
    known = categorised_stems()
    for lang in ("en", "ja"):
        docs_dir = REPO_ROOT / "docs" / lang
        found = set()
        for path in sorted(docs_dir.rglob("*.md")):
            stem = path.relative_to(docs_dir).with_suffix("").as_posix()
            if stem == "README":
                continue
            found.add(stem)
        missing = sorted(found - known)
        for stem in missing:
            problems.append(
                f"docs/{lang}/{stem}.md has no category in CATEGORIES, so it would "
                "not appear in the index."
            )
        stale = sorted(known - found)
        for stem in stale:
            problems.append(
                f"CATEGORIES lists {stem} but docs/{lang}/{stem}.md does not exist."
            )
    return problems


def apply(check: bool) -> int:
    problems = check_coverage()
    for problem in problems:
        print(f"ERROR: {problem}")

    for lang in ("en", "ja"):
        readme = REPO_ROOT / "docs" / lang / "README.md"
        text = readme.read_text(encoding="utf-8")
        if START not in text or END not in text:
            print(
                f"ERROR: docs/{lang}/README.md is missing the {START} / {END} markers."
            )
            problems.append("markers")
            continue

        head, rest = text.split(START, 1)
        _, tail = rest.split(END, 1)
        rebuilt = head + render(lang) + tail.lstrip("\n")

        if rebuilt == text:
            print(f"in sync: docs/{lang}/README.md")
            continue
        if check:
            print(
                f"ERROR: docs/{lang}/README.md index is out of date. Run: "
                "python3 shared/scripts/generate-docs-index.py"
            )
            problems.append("stale")
            continue
        readme.write_text(rebuilt, encoding="utf-8")
        print(f"updated: docs/{lang}/README.md")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(apply(check="--check" in sys.argv[1:]))
