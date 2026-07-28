# Honest scope — what this action checks

The analysis is [`ctbench`](https://github.com/nickharris808/ctbench); its
[SCOPE.md](https://github.com/nickharris808/ctbench/blob/main/SCOPE.md) is the
authoritative statement. In short:

- Verdicts cover **completion timing** against the **declared** secrets. Not power,
  not EM, not cache, not microarchitectural state.
- Secrets are **never inferred**. A file with no declared secrets is *skipped and
  reported as skipped*, because guessing which inputs are sensitive produces
  confident verdicts about the wrong property.
- Within the supported Verilog subset the analysis over-approximates, so
  `constant-time` is conservative there and `LEAKY` names the reaching signals.

## The three outcomes, and why `unknown` is separate

| Status | Meaning | Counted in |
|---|---|---|
| `constant-time` | checked, no declared secret reaches the completion signal | `checked` |
| `LEAKY` | checked, a secret reaches it — annotation on the diff, job fails | `checked`, `leaks` |
| `UNKNOWN — not checked` | the analysis could not read the file. **No verdict.** | `unknown` |
| `skipped` | no secrets declared for this file | neither |

`unknown` is a separate output, and unanalysable files are excluded from `checked`,
for one reason: the summary table used to render anything that was not `LEAKY` as
"constant-time". A file the checker could not read then appeared on the pull request
as a pass — the worst place for that error to happen, because a green table is
exactly what a reviewer trusts.

An unanalysable file now carries a warning annotation saying it was **NOT** checked,
and the summary states the count up front so a skimmed headline cannot mislead.

## What it does not do

This runs inside your CI, on your source. If you need a third party to believe the
result without receiving the RTL, that needs a proof bound to a commitment of a
design that is never disclosed — a commercial capability, not part of this action.
