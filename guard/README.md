# CloudFormation Guard rules

Policy-as-code checks for the CloudFormation templates in this repository.

## Layout

```
guard/
├── rules/
│   ├── critical-security.guard             # blocking, all templates
│   ├── management-console-security.guard   # blocking, management-console/templates/ only
│   ├── lambda-security.guard               # advisory
│   └── secrets-management.guard            # advisory
└── tests/
    ├── parse-probe.yaml                    # minimal template, used to detect parse errors
    ├── negative-control.yaml               # must trip every rule
    ├── positive-control.yaml               # must trip no rule
    └── run-guard-selftest.sh               # asserts all three properties
```

## Running locally

```bash
# Verify the rules themselves work (run this first)
bash guard/tests/run-guard-selftest.sh

# Blocking rules
cfn-guard validate -d integrations/datadog/template.yaml \
  -r guard/rules/critical-security.guard --show-summary fail

# Advisory rules
cfn-guard validate -d integrations/datadog/template.yaml \
  -r guard/rules/lambda-security.guard --show-summary fail
```

Install: [cloudformation-guard releases](https://github.com/aws-cloudformation/cloudformation-guard/releases).

## Why there is a self-test

A cfn-guard rule set has two failure modes that are invisible from the outside,
because both produce the same output as a clean run:

| Failure mode | What you see | What is actually happening |
|---|---|---|
| Rule file does not parse | `cfn-guard validate` exits **0** and prints `Parsing error ...` | No rule in the file runs |
| Rule filter selects nothing | Zero findings | The rule evaluates and asserts nothing |

Both occurred here. `lambda-security.guard` and `secrets-management.guard` were
unparseable, and five individual rules across all four files selected nothing —
including `no_plaintext_secret_params` in the **blocking** set. CI reported
success the entire time.

`run-guard-selftest.sh` closes this by checking three properties:

1. every rule file parses
2. every rule declared in every file reports a finding against `negative-control.yaml`
3. no rule reports a finding against `positive-control.yaml`

Check 2 catches a rule that has stopped matching. Check 3 catches a rule so broad
that its findings get dismissed by habit, which disables a rule just as
effectively as never running it.

It is the first step in the `cfn-guard` CI job and it blocks: if the rules are
not working, the results of the steps after it carry no information.

## Adding a rule

1. Write the rule in the appropriate file under `rules/`.
2. Add a matching violation to `tests/negative-control.yaml`.
3. If the rule could plausibly over-match, add the compliant shape to
   `tests/positive-control.yaml`.
4. Run `bash guard/tests/run-guard-selftest.sh`.

Step 2 is not optional — the self-test fails on any declared rule that reports
nothing, so a new rule without a fixture is treated as broken.

## Syntax notes

These are the mistakes that produced the failures above. All are silent.

**A `<<message>>` binds to the clause directly above it.** It cannot follow a
`{ ... }` block. Misplacing it makes the whole file unparseable.

```
# Broken: unparseable, and validate still exits 0
rule r {
  %lambdas.Properties {
    DeadLetterConfig exists
  }
  <<message>>
}

# Works
rule r {
  %lambdas.Properties.DeadLetterConfig exists
  <<message>>
}
```

**Filter a map by key with `Map[ keys == ... ]`, not `Map.*[ keys == ... ]`.**
The `.*` descends into each value first, so `keys` ends up matching the inner
keys (`Type`, `Default`, `NoEcho`) instead of the parameter names. Nothing is
ever selected and the rule always passes.

```
# Broken: matches nothing
Parameters.*[ keys == /[Ss]ecret/ ]

# Works
Parameters[ keys == /[Ss]ecret/ ]
```

**Guard does not combine two conditions on `keys` in one filter.**
`[ keys == /a/ keys != /b/ ]` parses but selects nothing. Express both sides as a
single pattern instead.

**Filter on `is_string` before comparing a value to a regex.** A property
supplied by an intrinsic function parses to a struct (`{"Ref": "..."}`), and
comparing a struct to a regex raises a `ComparisonError` that Guard counts as a
violation. Without the filter, every `!Ref`'d value is reported.

```
# Reports every !Ref'd SecretString as hardcoded
%secrets.Properties.SecretString == /^\{\{resolve:.*\}\}$/

# Only inspects literals
%secrets[ Properties.SecretString is_string ].Properties.SecretString == /^\{\{resolve:.*\}\}$/
```

**A rule whose condition restates its own assertion can never fail.** For
example `when %x !empty { %x !empty }` reports compliance without checking
anything.

## The DLQ exemption

`lambda_has_dlq` and `lambda_has_dead_letter_config` require either a
`DeadLetterConfig` or an explicit exemption on the resource:

```yaml
MyFunction:
  Type: AWS::Lambda::Function
  Metadata:
    guard:
      dlq_exempt: 'Synchronous API Gateway integration; the async DLQ never fires'
  Properties:
    ...
```

A blanket requirement would be wrong. Synchronously invoked functions — API
Gateway integrations and authorizers, Firehose transforms, Step Functions tasks,
CloudFormation custom resources — never fire an asynchronous DLQ, and SQS pollers
put their DLQ on the queue via `RedrivePolicy`. Flagging those produces findings
that are always dismissed.

The reason string is for humans; Guard only checks that the key exists. Adding it
is a deliberate act that appears in review, whereas a missing `DeadLetterConfig`
looks like every other omission.
