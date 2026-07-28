# ct-audit-action

**Fail the pull request when a completion signal in your RTL depends on a secret — with the leaking signals named on the diff.**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Action](https://img.shields.io/badge/GitHub-Action-2088FF.svg)](action.yml)
[![CI](https://img.shields.io/badge/CI-self--tested-brightgreen.svg)](.github/workflows/selftest.yml)

## Why this exists

Timing leaks in hardware are introduced the same way every time: someone adds an early
exit to a comparison loop, or makes a completion condition depend on operand values. It
looks like an optimisation. It reviews cleanly. Nobody catches it, because catching it
needs a fan-in analysis nobody runs by hand.

This runs that analysis on every push, and puts the answer where the review already is.

## Install

No install step — reference the action from any workflow:

```yaml
# .github/workflows/constant-time.yml
name: constant-time
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: nickharris808/ct-audit-action@v1
        with:
          files: rtl/**/*.v
          secrets: key,nonce
```

## 30-second quickstart

```yaml
- uses: nickharris808/ct-audit-action@v1
  with:
    files: rtl/**/*.v
    secrets: key,nonce
```

That is the whole configuration for the common case. The job fails if any completion
signal depends on `key` or `nonce`, and the offending signals are annotated on the diff.

Run the same check locally before you push:

```console
$ CTA_FILES="*.v" CTA_SECRETS="x,y" python audit.py
::error file=cmp_leaky.v::Completion signal 'done' depends on secret input(s): x, y. Timing reveals secret-dependent behaviour.

## Constant-time audit

**1 file(s) leak.** Completion timing depends on a secret.

| File | Verdict | Reaching secrets |
|---|---|---|
| `cmp_leaky.v` | **LEAKY** | x, y |
| `ct_cmp.v` | constant-time | — |

$ echo $?
1
```

## What it looks like on a PR

An annotation lands on the file:

```
Error: rtl/cmp.v
Completion signal 'done' depends on secret input(s): x, y.
Timing reveals secret-dependent behaviour.
```

and the job summary gets a table:

> ## Constant-time audit
>
> **1 file(s) leak.** Completion timing depends on a secret.
>
> | File | Verdict | Reaching secrets |
> |---|---|---|
> | `cmp_leaky.v` | **LEAKY** | x, y |
> | `ct_cmp.v` | constant-time | — |

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `files` | every git-tracked `.v` | newline- or comma-separated paths; globs expanded |
| `secrets` | *(none)* | comma-separated secret input names |
| `observation` | `done` | the completion signal |
| `config` | *(none)* | JSON with per-file `observation`/`secrets`, overriding the above |
| `fail-on-leak` | `true` | set `false` to report without failing |
| `python-version` | `3.11` | Python used to run the checker |

## Outputs

| Output | Meaning |
|---|---|
| `leaks` | number of files whose completion signal depends on a secret |
| `checked` | number of files actually checked |
| `report` | path to `ct-audit-report.json` |

## Per-file secrets

Different modules have different secrets. Point `config` at a JSON file in the same shape
as a ctbench manifest:

```json
{
  "scored": [
    { "file": "rtl/aes_core.v",  "observation": "done",  "secrets": ["key"] },
    { "file": "rtl/tag_cmp.v",   "observation": "valid", "secrets": ["tag", "expected"] }
  ]
}
```

Anything the config lists uses its own settings; anything else falls back to the
`secrets` and `observation` inputs.

## Secrets are declared, never inferred

A file with no declared secrets is **skipped and reported as skipped** — not guessed at:

```
Notice: rtl/util.v
ct-audit skipped: no secrets declared for this file; secrets are never inferred
```

Guessing which inputs are sensitive would produce confident verdicts about the wrong
property. A skip is visible in the summary table, so an unconfigured file cannot quietly
look like a pass.

## Scope

Verdicts cover **completion timing** against the declared secrets. Not power, not EM, not
cache, not microarchitectural state. The checker is a syntactic fan-in analysis including
every enclosing `if`/`case` guard, so it **over-approximates**: `constant-time` is
conservative, and `LEAKY` names the reaching signals so a human can confirm rather than
take it on faith. An unparseable file produces a warning, never a silent pass.

## Development

```bash
pip install -r requirements-dev.txt
pytest tests -q && ruff check .
```

16 tests, run without a GitHub runner: the action is a Python script driven by `CTA_*`
environment variables precisely so it is testable. `action.yml` itself is parsed and
checked — every declared input must reach the script, and the declared outputs must match
what the script writes. The self-test workflow also runs the action *as a user would*,
against a deliberately leaky module, and asserts it finds exactly one leak.

## Proving this to someone who cannot see your RTL

This runs inside your CI, on your source. If you need a third party to believe the result
without receiving the RTL, that needs a proof bound to a commitment of a design that is
never disclosed — a commercial capability, not part of this action.

## License

Apache-2.0. See [LICENSE](LICENSE).
