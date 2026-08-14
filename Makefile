# Makefile — single source of truth for which files the gates inspect.
#
# Why this file exists
# -------------------
# Before this Makefile, every gate carried its own copy of "which paths to
# look at": .github/workflows/ci.yaml inlined a vendor list, AGENTS.md
# documented a different (stale) pytest command, and the two had drifted.
# The result was 184 passing tests in scripts/verification/tests/ and
# shared/lambda-layers/log-parser/tests/ that no automation ran — they
# executed only when a human remembered the command in the docs.
#
# The path lists below are the authoritative ones. CI calls these targets
# rather than re-listing paths, so local and CI cannot inspect different
# trees. scripts/tests/test_test_dir_coverage.py fails when a tests/
# directory exists on disk but is missing from PYTEST_DIRS, which is the
# specific drift that produced the 184 orphaned tests.
#
# EVERY target must appear in .PHONY. Targets named after a real directory
# (docs, scripts, guard, security) are otherwise satisfied by the directory
# itself: make prints "up to date" and runs no recipe, and a gate that never
# runs is indistinguishable from a gate that passes.
# scripts/tests/test_makefile_phony.py enforces this.

# --- Interpreters: prefer the project venv over whatever is on PATH --------
# A gate that resolves to a different tool version locally than in CI
# returns different answers for the same code. requirements-dev.txt pins the
# versions; these guards make sure the pinned copies are the ones used.
VENV        := .venv
PY          := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PYTEST      := $(PY) -m pytest
RUFF        := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
BANDIT      := $(if $(wildcard $(VENV)/bin/bandit),$(VENV)/bin/bandit,bandit)
CFN_LINT    := $(if $(wildcard $(VENV)/bin/cfn-lint),$(VENV)/bin/cfn-lint,cfn-lint)

# --- Path lists (authoritative) -------------------------------------------
# Vendors that ship a test suite. Vendors without tests/ (lakehouse-retention,
# mackerel, netapp-console) are intentionally absent; test_test_dir_coverage
# derives its expectation from the filesystem, so adding tests/ to one of them
# fails the drift test until it is listed here.
VENDOR_TEST_DIRS := \
  integrations/crowdstrike/tests \
  integrations/datadog/tests \
  integrations/dynatrace/tests \
  integrations/elastic/tests \
  integrations/grafana/tests \
  integrations/honeycomb/tests \
  integrations/new-relic/tests \
  integrations/otel-collector/tests \
  integrations/splunk-serverless/tests \
  integrations/sumo-logic/tests

# Non-vendor suites. scripts/verification/tests and log-parser/tests are the
# two that were previously unreferenced by any automation.
SHARED_TEST_DIRS := \
  management-console/tests \
  scripts/tests \
  scripts/verification/tests \
  shared/lambda-layers/ems-parser/tests \
  shared/lambda-layers/log-parser/tests \
  shared/python/tests

PYTEST_DIRS := $(VENDOR_TEST_DIRS) $(SHARED_TEST_DIRS)

# CloudFormation templates. template*.yaml (not template.yaml) so the EMS,
# FPolicy, Firehose and remediation stacks are covered too.
CFN_TEMPLATES := \
  $(wildcard integrations/*/template*.yaml) \
  $(wildcard shared/templates/*.yaml) \
  $(wildcard management-console/templates/*.yaml)

# Python source for lint and security scanning. Excludes tests: assert
# statements in tests are bandit B101 by design.
PY_SRC := integrations shared/python shared/lambda-layers scripts management-console

# cfn-lint: W = warnings (advisory). E3006 = AWS::CloudWatch::LogAlarm is GA
# (2026-07) but not yet in the cfn-lint resource spec; deployment is verified
# working. Remove E3006 once the spec ships the resource type.
CFN_LINT_IGNORE := W E3006

BANDIT_BASELINE := .bandit-baseline.json

# moto and boto3 refuse to run without a region and credentials present.
TEST_ENV := AWS_DEFAULT_REGION=ap-northeast-1 \
            AWS_ACCESS_KEY_ID=testing \
            AWS_SECRET_ACCESS_KEY=testing \
            AWS_SECURITY_TOKEN=testing \
            AWS_SESSION_TOKEN=testing

# --- Meta -----------------------------------------------------------------

help:
	@echo "Targets:"
	@echo "  install        Install pinned dev dependencies into $(VENV)"
	@echo "  test           All tests (python + typescript)"
	@echo "  test-py        pytest across every directory in PYTEST_DIRS"
	@echo "  test-ts        jest"
	@echo "  lint           ruff + eslint"
	@echo "  security       bandit over PY_SRC"
	@echo "  cfn            cfn-lint + cfn-guard over CFN_TEMPLATES"
	@echo "  gitleaks       Secret scan, including the vendor-credential rules"
	@echo "  drift          Guards that fail when config and reality diverge"
	@echo "  all            test + lint + security + cfn + drift"
	@echo ""
	@echo "Each gate whose failure mode is silence has a test that breaks it"
	@echo "deliberately and requires a failure; those live in scripts/tests/."
	@echo ""
	@echo "Path lists live here, not in CI. Print one with:"
	@echo "  make print-PYTEST_DIRS"

# Lets CI and humans read a path list without duplicating it.
print-%:
	@echo "$($*)"

all: test lint security cfn drift

# The venv is uv-created and has no pip module, so `python -m pip` fails here.
# Prefer uv when present and fall back to pip for environments without it.
install:
	@if command -v uv >/dev/null 2>&1; then \
	  uv pip install --python $(PY) -r requirements-dev.txt; \
	else \
	  $(PY) -m pip install -r requirements-dev.txt; \
	fi

# --- Tests ----------------------------------------------------------------

test: test-py test-ts

# One pytest process PER directory, not one process over all of them.
#
# This is not a stylistic choice. Every vendor ships lambda/handler.py and
# several ship tests/test_fpolicy_handler.py, and each vendor conftest puts its
# own lambda/ directory on sys.path. Collecting all 16 suites in a single
# process makes those names collide: measured 193 failures and 43 errors
# together versus 0 when run separately. --import-mode=importlib in pytest.ini
# is necessary but not sufficient, because the collision is in sys.path and in
# sys.modules, not only in the import mode.
#
# The loop keeps going after a failing suite and fails at the end, so one
# broken vendor still reports the state of the rest instead of masking them.
test-py:
	@fail=""; \
	for d in $(PYTEST_DIRS); do \
	  echo "::group::$$d"; \
	  $(TEST_ENV) $(PYTEST) "$$d" -q --tb=short || fail="$$fail $$d"; \
	  echo "::endgroup::"; \
	done; \
	if [ -n "$$fail" ]; then echo "FAILED suites:$$fail"; exit 1; fi; \
	echo "All $(words $(PYTEST_DIRS)) suites passed."

test-ts:
	npx jest --passWithNoTests

# --- Lint and security ----------------------------------------------------

lint: lint-py lint-ts

# Blocking tier: ruff.toml [lint] select is restricted to rules that only fire
# on a definite defect, and it is clean today. See ruff.toml for why the
# broader set is separate.
lint-py:
	$(RUFF) check $(PY_SRC)

# Advisory tier: ruff's full default rule set. 36 findings outside tests as of
# this writing, all hygiene. Not wired into `lint` because a gate that is red
# on arrival gets ignored.
lint-py-full:
	-$(RUFF) check --no-cache --select ALL --exclude '**/tests/**' $(PY_SRC)

lint-ts:
	npm run lint

# bandit reports pattern shapes, so a clean run is not evidence that a class of
# defect is absent. It does not report a module-level SQL template fed through
# .format(), nor a query taken straight from an event payload. This repo builds
# no SQL at all -- verified by grep for start_query_execution and QueryString,
# which return nothing, and there is no Athena or Trino dependency -- so there
# is nothing of that shape to review today. Re-audit by hand, not by scanner,
# if query construction is ever added.
#
# The baseline records 6 pre-existing B314 findings (stdlib ElementTree parsing
# audit XML whose filenames and usernames originate from whoever touches the
# volume). It suppresses exactly those 6 and nothing else: a planted shell=True
# call still fails this target. scripts/tests/test_bandit_baseline.py fails if
# the baseline grows or gains a different rule id, so it cannot quietly become
# a place to hide findings.
security:
	$(BANDIT) -q -r $(PY_SRC) -x '*/tests/*,*/node_modules/*,*/.venv/*' \
	  -ll -b $(BANDIT_BASELINE)

# The same scan with no baseline: the honest full view, including the 6.
security-full:
	-$(BANDIT) -q -r $(PY_SRC) -x '*/tests/*,*/node_modules/*,*/.venv/*' -ll

# --- CloudFormation -------------------------------------------------------

cfn: cfn-lint cfn-guard

# The `--` separator is required whenever --ignore-checks precedes the
# template paths; without it argparse swallows the paths into the ignore list
# and cfn-lint exits with a usage error. A usage error under
# continue-on-error looks exactly like a passing lint.
cfn-lint:
	$(CFN_LINT) --ignore-checks $(CFN_LINT_IGNORE) -- $(CFN_TEMPLATES)

# The rule self-test runs first and is blocking: cfn-guard exits 0 when a
# rule file fails to parse, and a rule whose filter selects nothing reports
# no findings, so a broken rule and a clean template look identical.
cfn-guard: cfn-guard-selftest
	@for t in $(CFN_TEMPLATES); do \
	  echo "  $$t"; \
	  cfn-guard validate -d "$$t" -r guard/rules/critical-security.guard --show-summary fail || exit 1; \
	  cfn-guard validate -d "$$t" -r guard/rules/management-console-security.guard --show-summary fail || exit 1; \
	done

cfn-guard-selftest:
	bash guard/tests/run-guard-selftest.sh

# --- Secrets --------------------------------------------------------------

gitleaks:
	gitleaks detect --config .gitleaks.toml --no-git --source . --redact --verbose

# --- Drift guards ---------------------------------------------------------
# Each of these fails when the configuration stops describing reality.
# They are ordinary pytest files so they run as part of test-py too.

drift: agent-config
	$(TEST_ENV) $(PYTEST) scripts/tests -q

# Reachability of steering and skills. Silent when healthy.
agent-config:
	$(PY) scripts/check_agent_context_budget.py
	@if [ -f "$$HOME/.kiro/hooks/scripts/validate_agent_config.py" ]; then \
	  $(PY) "$$HOME/.kiro/hooks/scripts/validate_agent_config.py"; \
	fi

bilingual:
	bash shared/scripts/check-bilingual-sync.sh
	$(PY) shared/scripts/sync-code-blocks.py --check

# --- Housekeeping ---------------------------------------------------------

clean:
	find . -type d -name __pycache__ -not -path './node_modules/*' -not -path './$(VENV)/*' -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache coverage-html .coverage

# Every target above must be listed here. Targets sharing a name with a real
# directory (docs, scripts, guard, security, shared, integrations) are the
# dangerous ones: without .PHONY make finds the directory, decides the target
# is up to date, and runs nothing while exiting 0.
.PHONY: help all install test test-py test-ts lint lint-py lint-py-full \
        lint-ts security security-full cfn cfn-lint cfn-guard \
        cfn-guard-selftest gitleaks drift agent-config bilingual clean
