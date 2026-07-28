# Contributing to ct-audit-action

## Testing without a runner

The action is a Python script driven by `CTA_*` environment variables, precisely so it can
be tested without GitHub. Drive `audit.main()` the way `action.yml` does:

```bash
pip install -r requirements-dev.txt
pytest tests -q && ruff check .
```

`action.yml` is itself parsed and checked: every declared input must reach the script, and
the declared outputs must match what the script writes. If you add an input, add the
matching `CTA_*` env entry or the test fails.

## Never infer secrets

A file with no declared secrets is skipped and *reported as skipped*. Do not add a
heuristic that guesses which inputs are sensitive. A confident verdict about the wrong
property is worse than no verdict, and a silent skip is worse still — which is why skips
appear in the summary table.

## Failure modes must not become silent passes

An unparseable file emits a `::warning` and does not fail the job; a missing observation
signal is recorded as an `error` and excluded from `checked`. Neither is ever counted as
constant-time. Keep it that way.

## Keep the scope note in the summary

Every job summary ends with the line about completion timing, over-approximation, and
declared secrets. It is the difference between a reviewer reading "constant-time" as
"secure" and reading it as what it actually says.

## Style

`ruff check .` clean, `pytest tests -q` green.
