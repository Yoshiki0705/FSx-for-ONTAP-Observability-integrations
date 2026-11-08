# Long-Run Discipline — repo-specific wiring

Decision records, auditor sub-agent triggers, and measure-variance-first, as
they apply **in this repository**. The reasoning behind each discipline lives in
global Kiro steering (`ai-agent-workflow` — Parallel-by-default, Auditor
Sub-Agent; `benchmark-and-evidence-standards`) and is not copied here. This file
carries only the wiring that is specific to this repo.

> Extracted so it is not loaded into every agent turn. AGENTS.md keeps a
> one-line index entry pointing here. Tracked in git on purpose: `.kiro/` is not
> published, so the body must live here to stay visible on GitHub.

## Decision records

On a multi-step task (new vendor integration, verification run, IaC change),
keep three sections alongside the implementation notes — not in separate files,
and not in a new kind of container:

```markdown
## Decisions
- <what was decided>: <why. basis: verified / documented / hypothesis>

## Rejected alternatives
- <option rejected>: <why. could it come back if conditions change?>

## Assumptions
- <taken as given without checking>: <what breaks if it is wrong>
```

**Where it goes in this repo.** `.agents/tasks/<task>/` — it is gitignored (see
`.gitignore` → "Agent run artifacts"), which is where process metadata
(rejected options, unverified assumptions, evidence basis) belongs. If the task
already has a record file (`plan.md`, `design.md`, `report.md`,
`impl-note.md`), append the three sections to it; do not add a second file and
do not rename the existing one. Match the existing name — there is no house
rule that it be `impl-note.md`.

## Evidence tier in the Decisions basis

The basis field uses this repo's live vocabulary: `verified`, `documented`,
`unverified`, with granularity added as `unverified:<reason>` (e.g.
`unverified:single-run`, `unverified:hypothesis`). These are the words already
in the code (`restore_verification.py` returns `verified` / `unverified:<reason>`;
`ssd_auto_increase/classify.py` cites "documented, read <date>"), so `grep`
finds both the records and the code. Do not introduce a fifth word.

**This is not the ledger.** Two things share vocabulary but are separate:

| | Decisions basis (this file) | Evidence ledger (sibling repos) |
|---|---|---|
| What it tags | your own design decision's footing | an external claim ("the vendor cannot X") |
| Gate | none — internal memo | a public gate enforces it |
| Registration | none needed | required |

This repo has **no** ledger gate (no `check_verification_ledger.py`). The
evidence-ledger pattern lives in sibling repos (Adoption-Playbook,
Cyber-Resilience, S3AccessPoints-Serverless). Do not create one here for
decision records. A vendor-capability claim that needs a public gate is a
different concern and does not belong in `.agents/tasks/`.

## Auditor sub-agent triggers

Before continuing, call `semantic_reviewer` (re-examine the judgement) or
`context-gatherer` (check the assumptions against the implementation) when any
of these appears, and hand it the decision record plus the diff:

- the same approach failed twice
- the same file has been re-edited three or more times
- the same error message recurs across a multi-step task

Frame the ask to **doubt**, not reinforce: which bases are `hypothesis`, which
rejected alternative should come back now, which unverified assumption would
waste the work if it is wrong. No automatic hook.

Pre-commit review of published output is the persona review board's job — keep
it separate from the auditor.

## Measure variance before claiming an effect

Before claiming a change moved a performance / throughput / latency / cost
number, first measure run-to-run variance on the unchanged configuration. If the
change's delta is inside that variance, it is noise — say so, or raise the
iteration count until the delta clears the variance band. A one-run-each "A is
faster than B" gets the same treatment. Match the existing variance-aware
naming: `bench-*.json` + `bench-r2-*.json` for a second run (see
`.agents/tasks/s3ap-benchmark-private/`), and `r2`/`r3`/`remeasure` for repeats.

## Repeat task-type checklists

When applying the above surfaces a gap in a recurring task type, add one
checklist item to the relevant playbook — `docs/agent/deploying-and-adding-vendors.md`
for vendor additions, or a new `docs/agent/` file for another recurring type.
A second failure of the same kind is the signal that the checklist is missing an
item; grow it one line at a time rather than in a batch.
