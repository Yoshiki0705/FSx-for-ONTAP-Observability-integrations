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
    """One inline-code site: a resource in a template, fed by a source file."""

    def __init__(self, template: str, resource: str, source: str, entry: str):
        self.template = template
        self.resource = resource
        self.source = source
        self.entry = entry

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
        "shared/lambda/authorizers/shared_secret_authorizer.py",
        "lambda_handler",
    ),
    Target(
        "management-console/templates/console.yaml",
        "S3CopyFunction",
        "management-console/lambda/s3_copy_handler.py",
        "lambda_handler",
    ),
    Target(
        "management-console/templates/observability.yaml",
        "DashboardImporterFunction",
        "management-console/lambda/dashboard_importer.py",
        "lambda_handler",
    ),
]


def banner(source: str) -> str:
    return (
        f"# Generated from {source} -- do not edit here.\n"
        "# Regenerate with: python3 shared/scripts/sync-inline-lambda.py\n"
        "# shared/python/tests/test_inline_lambda_sync.py fails if the two drift"
        " apart.\n"
    )


def render(source: str) -> str:
    """Return the indented block-scalar body for a source file."""
    text = (REPO_ROOT / source).read_text(encoding="utf-8")
    lines = (banner(source) + text).splitlines()
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
    """Confirm the source actually defines the entry point the handler names."""
    text = (REPO_ROOT / target.source).read_text(encoding="utf-8")
    if not re.search(rf"^def {re.escape(target.entry)}\(", text, re.M):
        return (
            f"{target.source} does not define `def {target.entry}(`, but the "
            f"handler is set to {target.handler}. The function would fail to "
            "resolve at invocation time."
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

        expected_body = render(target.source)
        handler_ok = block.handler == target.handler
        body_ok = block.body == expected_body

        if handler_ok and body_ok:
            print(f"in sync: {label} <- {target.source}")
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
                    f"{target.source}. Run: "
                    "python3 shared/scripts/sync-inline-lambda.py"
                )
            problems += 1
            continue

        lines[block.body_start:block.body_end] = [expected_body]
        lines[block.handler_idx] = f"      Handler: {target.handler}\n"
        tpl_path.write_text("".join(lines), encoding="utf-8")
        print(f"updated: {label} <- {target.source}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(process(check="--check" in sys.argv[1:]))
