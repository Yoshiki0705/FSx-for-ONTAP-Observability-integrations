#!/usr/bin/env python3
"""Copy Lambda source files into the inline Code.ZipFile blocks that reference them.

Some templates ship their function source inline so that `aws cloudformation
deploy` produces a working stack with no follow-up step. That is the right
trade-off for a reference implementation. The alternative was an inline
placeholder plus a manual `aws lambda update-function-code`, and three stacks
here showed how that ends: the placeholder stayed, the upload step was in no
setup guide, and the stack reported CREATE_COMPLETE while every invocation
returned a stub.

  shared/templates/ems-webhook-apigw.yaml      authorizer denied every request
  management-console/templates/console.yaml    S3 copy returned 501
  management-console/templates/observability.yaml  dashboard import was a no-op

The cost of inlining is a second copy of the code, so it is paired with
shared/python/tests/test_inline_lambda_sync.py, which fails when a template's
copy no longer matches its source file.

Two properties this script enforces that are easy to get wrong:

  Handler must be index.<function>. CloudFormation writes inline source to a
  file named `index`, so any other module name raises an import error at
  invocation time. For an authorizer that surfaces as HTTP 500 rather than 401;
  for a custom resource it hangs the stack until the timeout.

  The entry point must actually exist in the source. observability.yaml declared
  `Handler: index.handler` while dashboard_importer.py defines `lambda_handler`,
  so uploading the real code would still have failed to resolve.

Usage:
    python3 shared/scripts/sync-inline-lambda.py           # rewrite templates
    python3 shared/scripts/sync-inline-lambda.py --check    # exit 1 if out of sync
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

BODY_INDENT = " " * 10
ZIPFILE_MARKER = "        ZipFile: |\n"


class Target:
    """One inline-code site: a resource in a template, fed by source file(s).

    Most Targets carry a single ``source`` file that is self-contained. A few
    collectors (the QoS collector below) are split across the shared ONTAP REST
    client and the collector logic, and are not self-contained; those carry an
    ordered ``sources`` list that render() concatenates into one inline body.
    Pass either ``source`` (one file) or ``sources`` (ordered list), not both.
    """

    def __init__(
        self,
        template: str,
        resource: str,
        entry: str,
        *,
        source: str | None = None,
        sources: list[str] | None = None,
    ):
        if (source is None) == (sources is None):
            raise ValueError("pass exactly one of source= or sources=")
        self.template = template
        self.resource = resource
        self.entry = entry
        self.sources: list[str] = [source] if source is not None else list(sources or [])

    @property
    def source(self) -> str:
        """The primary source file (the last one, which defines the entry point)."""
        return self.sources[-1]

    @property
    def handler(self) -> str:
        return f"index.{self.entry}"

    @property
    def label(self) -> str:
        return f"{self.template} :: {self.resource}"

    def __repr__(self) -> str:
        return f"Target({self.label})"


TARGETS: list[Target] = [
    Target(
        "shared/templates/ems-webhook-apigw.yaml",
        "AuthorizerFunction",
        "lambda_handler",
        source="shared/lambda/authorizers/shared_secret_authorizer.py",
    ),
    Target(
        "management-console/templates/console.yaml",
        "S3CopyFunction",
        "lambda_handler",
        source="management-console/lambda/s3_copy_handler.py",
    ),
    Target(
        "management-console/templates/observability.yaml",
        "DashboardImporterFunction",
        "lambda_handler",
        source="management-console/lambda/dashboard_importer.py",
    ),
    # The qtree poller is also the ONTAP REST client of the Terraform module
    # terraform/fsxn-ontap-custom-metrics, which zips the same directory, so
    # both deployment paths run one source file.
    Target(
        "shared/templates/qtree-quota-monitor.yaml",
        "QuotaMonitorFunction",
        "lambda_handler",
        source="shared/lambda/ontap_metrics/qtree_quota_poller.py",
    ),
    # The QoS policy-group collector is NOT self-contained: qos_poller.py reuses
    # the ONTAP REST client that lives in qtree_quota_poller.py (importing it as
    # `ontap` and pulling MAX_PAGES/MAX_RECORDS/... by name). CloudFormation
    # inline code must be one file, so the Target concatenates the client first,
    # then the collector, as an ordered sources list. render() strips the
    # sibling imports and the client's own CFN lambda_handler so the single
    # resulting module defines exactly one lambda_handler (the QoS one, last).
    Target(
        "shared/templates/qos-monitor.yaml",
        "QosMonitorFunction",
        "lambda_handler",
        sources=[
            "shared/lambda/ontap_metrics/qtree_quota_poller.py",
            "shared/lambda/ontap_metrics/qos_poller.py",
        ],
    ),
    # The S3 Access Points throughput benchmark. Inlined so `aws cloudformation
    # deploy` yields a working stack with no follow-up upload step; the source
    # of truth is benchmark/s3ap-throughput/handler.py.
    Target(
        "benchmark/s3ap-throughput/template.yaml",
        "BenchmarkFunction",
        "lambda_handler",
        source="benchmark/s3ap-throughput/handler.py",
    ),
]


def banner(sources: list[str]) -> str:
    """Return the provenance banner naming every source file in order."""
    names = ", ".join(sources)
    return (
        f"# Generated from {names} -- do not edit here.\n"
        "# Regenerate with: python3 shared/scripts/sync-inline-lambda.py\n"
        "# shared/python/tests/test_inline_lambda_sync.py fails if the two drift"
        " apart.\n"
    )


def _module_stem(source: str) -> str:
    """Return the import name a source file is reached by (its file stem)."""
    return Path(source).stem


def _strip_sibling_imports(text: str, siblings: set[str]) -> str:
    """Drop imports of sibling sources; rebind an aliased client to this module.

    In a concatenated module the client's top-level names (``_ontap_get``,
    ``MAX_PAGES``, ...) are already defined, so ``from <sibling> import (...)``
    and ``import <sibling>`` are removed outright. ``import <sibling> as <alias>``
    is the one that would otherwise break: the collector calls ``<alias>._x``,
    and after the import is gone ``<alias>`` is undefined. It is replaced with
    ``<alias> = sys.modules[__name__]`` so the alias resolves to the single
    concatenated module itself (which carries the client's definitions). The
    needed ``import sys`` is ensured by _concatenate.

    Args:
        text: Source text of one non-first file in the list.
        siblings: Import names of the other sources in the same Target.

    Returns:
        The text with sibling imports removed or rebound.
    """
    alt = "|".join(re.escape(s) for s in sorted(siblings))
    # `import <sibling> as <alias>` -> rebind alias to the concatenated module.
    alias_re = re.compile(rf"^import (?:{alt}) as (?P<alias>\w+)\s*$")
    # `import <sibling>` with no alias -> drop (names are module globals already).
    plain_re = re.compile(rf"^import (?:{alt})\s*$")
    # `from <sibling> import ...`, including a parenthesized multi-line form.
    from_open_re = re.compile(rf"^from (?:{alt}) import \(")
    from_one_re = re.compile(rf"^from (?:{alt}) import [^(].*$")

    out: list[str] = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip("\n")
        alias_match = alias_re.match(stripped)
        if alias_match:
            out.append(f"{alias_match.group('alias')} = sys.modules[__name__]\n")
            i += 1
            continue
        if plain_re.match(stripped) or from_one_re.match(stripped):
            i += 1
            continue
        if from_open_re.match(stripped):
            # Skip through the closing parenthesis of the import block.
            while i < len(lines) and ")" not in lines[i]:
                i += 1
            i += 1  # the line carrying ')'
            continue
        out.append(line)
        i += 1
    return "".join(out)


def _strip_entry_def(text: str, entry: str) -> str:
    """Remove the ``def <entry>(...)`` block so another source can define it.

    Removes from the ``def <entry>(`` line through the end of its body, i.e.
    up to the next line that is non-blank and not indented. Only the entry
    function is removed; everything else in the file is kept verbatim.

    Args:
        text: Source text of a non-last file whose own entry point is excluded.
        entry: Function name to remove (e.g. ``lambda_handler``).

    Returns:
        The text without that function definition.
    """
    lines = text.splitlines(keepends=True)
    def_re = re.compile(rf"^def {re.escape(entry)}\(")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if def_re.match(lines[i]):
            i += 1
            while i < len(lines) and (
                lines[i].strip() == "" or lines[i][:1] in (" ", "\t")
            ):
                i += 1
            # Drop a single trailing blank separator left before the next def.
            while out and out[-1].strip() == "":
                out.pop()
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def concatenate(target: Target) -> str:
    """Return the single-file Python body for a Target (no banner, no indent).

    Single-source Targets return their file verbatim, byte for byte, so their
    inline blocks never change. Multi-source Targets are concatenated in order:
    every source but the last has its own entry-point definition removed, every
    source but the first has its sibling imports stripped or rebound, and a lone
    ``import sys`` is inserted when an alias rebind needs it.
    """
    sources = target.sources
    if len(sources) == 1:
        return (REPO_ROOT / sources[0]).read_text(encoding="utf-8")

    sibling_stems = {_module_stem(s) for s in sources}
    pieces: list[str] = []
    needs_sys = False
    for idx, source in enumerate(sources):
        text = (REPO_ROOT / source).read_text(encoding="utf-8")
        if idx != len(sources) - 1:
            text = _strip_entry_def(text, target.entry)
        if idx != 0:
            others = sibling_stems - {_module_stem(source)}
            before = text
            text = _strip_sibling_imports(text, others)
            if "= sys.modules[__name__]" in text and "= sys.modules[__name__]" not in before:
                needs_sys = True
            elif "= sys.modules[__name__]" in text:
                needs_sys = True
        pieces.append(text.rstrip("\n"))
    body = "\n\n\n".join(pieces) + "\n"
    if needs_sys:
        body = "import sys\n" + body
    return body


def render(target: Target) -> str:
    """Return the indented block-scalar body for a Target."""
    text = concatenate(target)
    lines = (banner(target.sources) + text).splitlines()
    return "".join(
        f"{BODY_INDENT}{line}\n" if line.strip() else "\n" for line in lines
    )


def strip_banner(body: str) -> str:
    """Dedent an inline block and drop the generated banner."""
    dedented = "".join(
        line[len(BODY_INDENT):] if line.startswith(BODY_INDENT) else line
        for line in body.splitlines(True)
    )
    return re.sub(r"\A(?:#.*\n)+", "", dedented)


class Block:
    """Located Handler line and ZipFile body within a resource block."""

    def __init__(self, lines: list[str], handler_idx: int, body_start: int,
                 body_end: int):
        self.lines = lines
        self.handler_idx = handler_idx
        self.body_start = body_start
        self.body_end = body_end

    @property
    def handler(self) -> str:
        return self.lines[self.handler_idx].split(":", 1)[1].strip()

    @property
    def body(self) -> str:
        return "".join(self.lines[self.body_start:self.body_end])


def locate(lines: list[str], resource: str) -> Block | None:
    """Find the Handler line and ZipFile body of a resource.

    Handler and Code are not adjacent in every template -- observability.yaml has
    Environment between them -- so each is located independently within the
    resource's own block rather than with one contiguous pattern.
    """
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^  {re.escape(resource)}:\s*$", line):
            start = i
            break
    if start is None:
        return None

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^  \S", lines[i]):
            end = i
            break

    handler_idx = None
    zip_idx = None
    for i in range(start + 1, end):
        if re.match(r"^      Handler:\s", lines[i]):
            handler_idx = i
        elif lines[i] == ZIPFILE_MARKER:
            zip_idx = i
    if handler_idx is None or zip_idx is None:
        return None

    body_start = zip_idx + 1
    body_end = body_start
    while body_end < end and (
        lines[body_end].strip() == "" or lines[body_end].startswith(BODY_INDENT)
    ):
        body_end += 1

    return Block(lines, handler_idx, body_start, body_end)


def check_entry_point(target: Target) -> str | None:
    """Confirm the concatenated source defines the entry point exactly once.

    For a multi-source Target the entry must survive concatenation (the last
    source keeps it) and must not be defined twice (earlier sources have their
    own entry definition stripped), or CloudFormation would import an ambiguous
    module.
    """
    text = concatenate(target)
    count = len(re.findall(rf"^def {re.escape(target.entry)}\(", text, re.M))
    if count == 0:
        return (
            f"{target.label} does not define `def {target.entry}(` in the "
            f"concatenated source, but the handler is set to {target.handler}. "
            "The function would fail to resolve at invocation time."
        )
    if count > 1:
        return (
            f"{target.label} defines `def {target.entry}(` {count} times in the "
            "concatenated source; exactly one entry point is required."
        )
    return None


def process(check: bool) -> int:
    problems = 0

    for target in TARGETS:
        tpl_path = REPO_ROOT / target.template
        lines = tpl_path.read_text(encoding="utf-8").splitlines(True)
        block = locate(lines, target.resource)
        label = target.label

        if block is None:
            print(f"ERROR: {label}: cannot locate Handler and inline ZipFile")
            problems += 1
            continue

        entry_problem = check_entry_point(target)
        if entry_problem:
            print(f"ERROR: {label}: {entry_problem}")
            problems += 1
            continue

        expected_body = render(target)
        source_label = ", ".join(target.sources)
        handler_ok = block.handler == target.handler
        body_ok = block.body == expected_body

        if handler_ok and body_ok:
            print(f"in sync: {label} <- {source_label}")
            continue

        if check:
            if not handler_ok:
                print(
                    f"ERROR: {label}: declares Handler: {block.handler}. Inline "
                    f"code is written to `index`, so it must be {target.handler}."
                )
            if not body_ok:
                print(
                    f"ERROR: {label}: inline code no longer matches "
                    f"{source_label}. Run: "
                    "python3 shared/scripts/sync-inline-lambda.py"
                )
            problems += 1
            continue

        lines[block.body_start:block.body_end] = [expected_body]
        lines[block.handler_idx] = f"      Handler: {target.handler}\n"
        tpl_path.write_text("".join(lines), encoding="utf-8")
        print(f"updated: {label} <- {source_label}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(process(check="--check" in sys.argv[1:]))
