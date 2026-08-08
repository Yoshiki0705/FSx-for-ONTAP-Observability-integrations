# Contributing

Thank you for your interest in contributing to FSx for ONTAP Observability Integrations.

## How to Contribute

### Asking Questions

- Use [GitHub Discussions](https://github.com/Yoshiki0705/fsxn-observability-integrations/discussions/categories/q-a) (Q&A category) for setup questions, "which vendor should I use", and deployment troubleshooting
- Discussions keep Issues focused on actionable defects, and answered threads stay searchable for the next person with the same question

### Reporting Issues

- Use [GitHub Issues](https://github.com/Yoshiki0705/fsxn-observability-integrations/issues) for bug reports and feature requests
- Include your environment details (AWS region, vendor, Lambda runtime)
- For security issues, email directly instead of opening a public issue

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes following the code style below
4. Run tests: `python -m pytest integrations/<vendor>/tests/ -v`
5. Validate templates: `cfn-lint integrations/<vendor>/template.yaml`
6. Submit a PR with a clear description

### Priority Contribution Areas

- Additional vendor integrations (Axiom, Mezmo, Coralogix, Chronosphere)
- Terraform equivalents of CloudFormation templates
- CDK constructs
- Localization (Korean, Chinese, Portuguese)
- Benchmark data from different FSx for ONTAP configurations
- Bug fixes and documentation improvements

## Code Style

### Python (Lambda functions)

- Python 3.12, PEP 8
- Type hints required
- Google-style docstrings
- Use `urllib3` for HTTP (included in Lambda runtime), not `requests`
- Secrets from Secrets Manager, never environment variables

### CloudFormation (YAML)

- 2-space indent
- PascalCase resource logical IDs
- Always include: IAM least-privilege, DLQ, CloudWatch Alarms

### Documentation

- Bilingual: Japanese (primary) + English
- Same heading structure in both languages
- Code examples identical across languages

## Adding a New Vendor Integration

1. Create directory: `mkdir -p integrations/<vendor>/{lambda,docs/{ja,en},tests,scripts}`
2. Copy reference: use `integrations/grafana/` as the template
3. Implement `lambda/handler.py` with vendor-specific API formatting
4. Create `template.yaml`, `template-ems.yaml`, `template-fpolicy.yaml`
5. Write bilingual docs: `docs/ja/setup-guide.md` and `docs/en/setup-guide.md`
6. Add pytest tests with mocked API responses
7. Create `scripts/deploy.sh` and `scripts/cleanup.sh`
8. Update root `README.md` vendor table
9. Run the full test suite before submitting

## Testing

- All Lambda handler logic must have unit tests
- Mock all AWS service calls (boto3) and HTTP calls (urllib3)
- Use `conftest.py` for shared fixtures
- Tests must be deterministic (no real API calls)

```bash
# Run all tests
python -m pytest integrations/*/tests/ -v

# Run specific vendor
python -m pytest integrations/datadog/tests/ -v

# Validate CloudFormation
pip install cfn-lint
cfn-lint --ignore-checks W -- integrations/*/template*.yaml shared/templates/*.yaml
```

## Documentation and policy checks

The first two fail the build. Run them before opening a PR if you touched docs or
templates.

```bash
# Executable code blocks must be identical between docs/ja and docs/en.
# Prose is translated; the commands are not. Drop --check to fix drift.
python3 shared/scripts/sync-code-blocks.py --check

# The per-language document index is generated, not hand-edited.
# A new document needs a category in the script, or this fails.
python3 shared/scripts/generate-docs-index.py --check

# cfn-guard rules, including a self-test that proves the rules still fire.
bash guard/tests/run-guard-selftest.sh

# Heading structure between languages (advisory, does not fail the build).
bash shared/scripts/check-bilingual-sync.sh
```

Diagram fences (untagged, `mermaid`, `text`) stay localised on purpose and are not
touched by the code-block check — see AGENTS.md for why.

## Commit Convention

```
feat: add Axiom integration
fix: handle empty EVTX files in log parser
docs: update Datadog setup guide
test: add batch splitting edge case tests
chore: update cfn-lint to v1.x
```

Conventional Commits format. English only. Keep subject under 72 characters.

Allowed types: `feat` `fix` `docs` `bench` `chore` `refactor` `test` `ci` `perf` `style`.

**Your PR title needs the same prefix.** CI fails the PR if it does not have one,
because this repository squash-merges and GitHub builds the squash commit message
from the PR title. Titles over 70 characters get a warning, not a failure. Fixing
the title re-runs the check automatically — no new push needed.

```
feat: add S3 AP presigned URL support
fix(shared): handle empty ONTAP response
feat!: drop Python 3.11 support          # ! marks a breaking change
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
