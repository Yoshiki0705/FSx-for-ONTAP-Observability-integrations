"""Tests that inline Lambda code in CloudFormation templates matches its source.

Three templates embed their function source in Code.ZipFile so a plain
`aws cloudformation deploy` yields a working stack. That removes a manual upload
step but introduces a second copy of the code, and a second copy nobody checks
drifts. These tests are the check.

They also assert the two properties that let the previous arrangement fail
silently. All three stacks reported CREATE_COMPLETE while serving a stub:

  ems-webhook-apigw.yaml     authorizer raised Unauthorized for every request
  console.yaml               S3 copy returned 501 "Deploy actual handler"
  observability.yaml         dashboard import was a logging no-op

  1. CloudFormation writes inline source to a file named `index`, so the handler
     must be `index.<function>`. console.yaml declared
     `Handler: s3_copy_handler.lambda_handler`, which cannot resolve against
     inline code.
  2. The named entry point must exist in the source. observability.yaml declared
     `Handler: index.handler` while dashboard_importer.py defines
     `lambda_handler`, so even uploading the real code would not have resolved.

The extraction helpers come from shared/scripts/sync-inline-lambda.py rather than
being reimplemented here, so the tests exercise the same code path the generator
uses. That also keeps the suite free of a PyYAML dependency -- cfn-lint already
validates these templates as YAML in CI.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC_SCRIPT = REPO_ROOT / "shared" / "scripts" / "sync-inline-lambda.py"


def _load_sync_module():
    """Import sync-inline-lambda.py, whose filename is not a valid module name."""
    spec = importlib.util.spec_from_file_location("sync_inline_lambda", SYNC_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load_sync_module()


def _block(target):
    lines = (REPO_ROOT / target.template).read_text(encoding="utf-8").splitlines(True)
    block = sync.locate(lines, target.resource)
    assert block is not None, (
        f"cannot locate the Handler line and inline ZipFile of "
        f"{target.resource} in {target.template}"
    )
    return block


def _ids(targets):
    return [f"{t.resource}@{Path(t.template).name}" for t in targets]


@pytest.mark.parametrize("target", sync.TARGETS, ids=_ids(sync.TARGETS))
class TestInlineLambdaSync:
    def test_inline_code_matches_source(self, target):
        body = sync.strip_banner(_block(target).body)
        expected = (REPO_ROOT / target.source).read_text(encoding="utf-8")
        assert body == expected, (
            f"{target.label} inline code has drifted from {target.source}. "
            "Run: python3 shared/scripts/sync-inline-lambda.py"
        )

    def test_inline_code_carries_provenance_banner(self, target):
        first_line = _block(target).body.splitlines()[0].strip()
        assert first_line.startswith("# Generated from"), (
            "Inline code must start with a banner naming its source file, so a "
            "reader editing the template knows the edit will be overwritten."
        )
        assert target.source in first_line

    def test_handler_is_index(self, target):
        handler = _block(target).handler
        assert handler == target.handler, (
            f"{target.label} declares Handler: {handler}, expected "
            f"{target.handler}. CloudFormation writes inline code to a file named "
            "'index', so any other module name raises an import error at "
            "invocation time."
        )

    def test_entry_point_exists_in_source(self, target):
        source = (REPO_ROOT / target.source).read_text(encoding="utf-8")
        assert re.search(rf"^def {re.escape(target.entry)}\(", source, re.M), (
            f"{target.source} does not define `def {target.entry}(` but "
            f"{target.label} names it as the handler entry point."
        )

    def test_inline_code_compiles(self, target):
        body = sync.strip_banner(_block(target).body)
        compile(body, target.label, "exec")

    def test_no_placeholder_left_behind(self, target):
        body = sync.strip_banner(_block(target).body).lower()
        for marker in ("placeholder", "deploy actual", "deploy this handler"):
            assert marker not in body, (
                f"{target.label} still contains a {marker!r} marker. A stack that "
                "deploys placeholder code reports CREATE_COMPLETE while returning "
                "a stub for every invocation."
            )


class TestSyncScript:
    def _run_check(self):
        return subprocess.run(
            [sys.executable, str(SYNC_SCRIPT), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

    def test_check_mode_reports_in_sync(self):
        result = self._run_check()
        assert result.returncode == 0, (
            "sync-inline-lambda.py --check failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )

    @pytest.mark.parametrize(
        ("find", "replace", "expected_text"),
        [
            pytest.param(
                "      Handler: index.lambda_handler\n",
                "      Handler: shared_secret_authorizer.lambda_handler\n",
                "must be index.lambda_handler",
                id="handler-drift",
            ),
            pytest.param(
                "          import hmac\n",
                "          import hmac  # drifted\n",
                "no longer matches",
                id="body-drift",
            ),
        ],
    )
    def test_check_mode_detects_drift(self, find, replace, expected_text):
        """Prove --check works, rather than assuming a clean result means clean."""
        template_path = REPO_ROOT / sync.TARGETS[0].template
        original = template_path.read_text(encoding="utf-8")
        mutated = original.replace(find, replace, 1)
        assert mutated != original, "mutation did not apply; test needs updating"
        try:
            template_path.write_text(mutated, encoding="utf-8")
            result = self._run_check()
            assert result.returncode == 1
            assert expected_text in result.stdout
        finally:
            template_path.write_text(original, encoding="utf-8")


class TestAuthorizerSource:
    SOURCE = REPO_ROOT / "shared/lambda/authorizers/shared_secret_authorizer.py"

    def test_uses_constant_time_comparison(self):
        source = self.SOURCE.read_text(encoding="utf-8")
        assert "hmac.compare_digest" in source, (
            "Token comparison must be constant time. A plain == returns at the "
            "first differing byte, which leaks how much of the submitted token "
            "was correct to anyone who can call the endpoint repeatedly."
        )
        assert "token == expected_secret" not in source
