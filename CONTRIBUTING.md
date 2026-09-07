# Contributing

Thank you for your interest in contributing to FSx for ONTAP Observability Integrations.

## How to Contribute

### Asking Questions

- Use [GitHub Discussions](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/discussions/categories/q-a) (Q&A category) for setup questions, "which vendor should I use", and deployment troubleshooting
- Discussions keep Issues focused on actionable defects, and answered threads stay searchable for the next person with the same question

### Reporting Issues

- Use [GitHub Issues](https://github.com/Yoshiki0705/FSx-for-ONTAP-Observability-integrations/issues) for bug reports and feature requests
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

### Japanese section headings are noun phrases (体言止め)

Every `##` and deeper heading in Japanese. A reader scans headings as labels, so a
sentence in that position reads as a stumble. `make headings` enforces this.

| 型 | 避ける | 使う |
|---|---|---|
| 動詞終止形 | 自分の環境で確かめる | 自環境での確認手順 |
| 疑問形 | なぜこの区分が必要か | この区分が必要な理由 |
| 述語文（敬体） | 記録されない読み取りがあります | 記録されない読み取りの存在 |
| 述語文（平叙） | クロスアカウントのアクセスは成立する | クロスアカウントアクセスの成立 |
| 述語文（否定） | ボリュームは AWS 側からしか消せない | AWS 側からしか消せないボリューム |

**Nominalising must not drop the assertion.** A heading often carries the finding
itself. Turning 「監査は 2 つの面に分かれ、片方に穴があります」 into 「監査の 2 つの面と
片方の穴」 demotes "there is a hole" to the noun "hole". Keep the assertion with a
suffix or a modifier.

- 接尾語: 〜の存在 / 不在 / 成立 / 不成立 / 必要 / 不可 / 無効化 / 差 / 上限 / 失敗 /
  不着 / 未表示 / 不一致 / 理由
- 修飾: 未対応の〜 / 既定で無効な〜 / 容量に比例して伸びる〜 / 〜で止まる〜
- 例: `CopyBackup には仕組みがありません` → `CopyBackup における定期実行の仕組みの不在`
- 例: `Snapshot をロックすると上限が効かない` → `Snapshot のロックによる世代数上限の無効化`

Out of scope, and the checker skips each of them: H1 and the frontmatter `title`
(those follow the separate "one-line claim" rule), English headings, `#` lines
inside code fences (shell comments), table cells and list items.

A trailing qualifier does not exempt a heading. `## 既存環境に追加する（推奨）` is a
violation; the checker strips the parenthetical before judging the head noun.

**Narrative headings are exempt, and the test is whether the heading works as an
index entry.** Chronological narration (`15:29 チェックイン時にパスポートが無い事に
気付く`), advice whose imperative tone is the content (`心身の状態を整えておく`), and
statements of intent (`Kubernetes の学習を通じて理解を深める`) all break when
nominalised. Mark those on the heading line and say in the surrounding prose why the
section is narration:

```markdown
## 15:29 チェックイン時にパスポートが無い事に気付く <!-- allow:heading-style -->
```

Renaming a heading changes its anchor. `grep -rn '](#' --include='*.md' .` finds the
in-document references and `grep -rn '\.md#' --include='*.md' .` the cross-document
ones; update them in the same commit. GitHub serves an unknown fragment as the top of
the page, so a stale link never announces itself.

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

All of these fail the build except `check-bilingual-sync.sh`, which is advisory. Run
them before opening a PR if you touched docs or templates.

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

# Japanese section headings must be noun phrases. The self-test runs first and is
# blocking: a checker that inspects nothing also reports zero violations.
make headings

# Every repository name linked from this tree must resolve without a redirect.
# Needs the network, so it is weekly in CI rather than per-PR. Run it by hand after
# adding a link to a sibling repository.
make repo-names
```

Diagram fences (untagged, `mermaid`, `text`) stay localised on purpose and are not
touched by the code-block check — see AGENTS.md for why.

### Enabling the pre-commit hook

```bash
make hooks    # git config core.hooksPath .githooks
```

**Required once per clone**, and nothing in the repository can check that you did it.
`core.hooksPath` lives in `.git/config`, which is per-checkout and not tracked, so a gate
placed in the repository is structurally unable to see whether the hook is active. A fresh
clone runs no hook and says nothing about it — and if you have a global `core.hooksPath`
set, that one wins and the tracked hook never runs at all.

`make hooks` is idempotent. The hook itself checks the author email and runs gitleaks over
staged files; `make drift` verifies the hook is tracked and executable, which is as far as
an in-repository check can reach.

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
