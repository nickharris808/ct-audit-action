#!/usr/bin/env python3
"""Run the constant-time audit over a set of Verilog files, for CI.

Reads its configuration from `CTA_*` environment variables so the same script runs
under the composite action and from a shell. Emits three things:

* GitHub *annotations* on stdout, so leaks appear inline on the diff;
* a *job summary* table, so the PR page shows what was checked;
* a JSON *report*, so downstream jobs can consume the result.

Exit status is 1 when a leak is found and `CTA_FAIL_ON_LEAK` is true.

The design decision worth stating: a file whose secrets are not declared is
**skipped and reported as skipped**, never guessed at. Inventing a secret set would
produce confident verdicts about the wrong property, which is worse than no verdict.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _split(value: str) -> list[str]:
    return [c.strip() for c in value.replace(",", "\n").splitlines() if c.strip()]


def discover_files(spec: str) -> list[Path]:
    """Expand the file spec, or fall back to every git-tracked .v file."""
    if spec:
        out: list[Path] = []
        for pattern in _split(spec):
            matched = [Path(p) for p in glob.glob(pattern, recursive=True)]
            out.extend(matched or ([Path(pattern)] if Path(pattern).is_file() else []))
        return sorted({p for p in out if p.is_file()})
    try:
        listed = subprocess.run(
            ["git", "ls-files", "*.v"], capture_output=True, text=True, check=True
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        listed = glob.glob("**/*.v", recursive=True)
    return sorted({Path(p) for p in listed if Path(p).is_file()})


def load_config(path: str) -> dict[str, dict[str, Any]]:
    """Per-file overrides, keyed by file name or path."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    entries = data.get("scored", data) if isinstance(data, dict) else data
    if isinstance(entries, dict):
        return entries
    return {e["file"]: e for e in entries}


def audit_one(path: Path, observation: str, secrets: list[str],
              module: str | None) -> dict[str, Any]:
    from ctbench.cone import UNKNOWN, check

    try:
        v = check(path.read_text(), observation, secrets, module)
    except ValueError as exc:
        return {"file": str(path), "status": "error", "detail": str(exc)}
    d = v.to_dict()
    d["file"] = str(path)
    # An UNKNOWN is not a check that happened to come out clean: nothing was
    # analysed.  Keeping it out of "checked" is what stops the summary table from
    # rendering it in the same column as a real constant-time verdict.
    d["status"] = "unknown" if v.status == UNKNOWN else "checked"
    if v.status == UNKNOWN:
        d["detail"] = v.reason or "no verdict could be reached"
    return d


def annotate(result: dict[str, Any]) -> None:
    """Emit a GitHub annotation so the finding lands on the diff."""
    f = result["file"]
    if result["status"] == "error":
        print(f"::warning file={f}::ct-audit could not parse this file: {result['detail']}")
    elif result["status"] == "unknown":
        print(
            f"::warning file={f}::ct-audit reached NO VERDICT for this file — it was "
            f"NOT checked and must not be read as constant-time. {result['detail']}"
        )
    elif result["status"] == "skipped":
        print(f"::notice file={f}::ct-audit skipped: {result['detail']}")
    elif result["verdict"] == "LEAKY":
        reaching = ", ".join(result["reaching_secrets"])
        print(
            f"::error file={f}::Completion signal '{result['observation']}' depends on "
            f"secret input(s): {reaching}. Timing reveals secret-dependent behaviour."
        )


def summary(results: list[dict[str, Any]]) -> str:
    leaky = [r for r in results if r.get("verdict") == "LEAKY"]
    clean = [r for r in results if r.get("verdict") == "CONSTANT_TIME" and r["status"] == "checked"]
    unknown = [r for r in results if r["status"] == "unknown"]

    lines = ["## Constant-time audit", ""]
    if leaky:
        lines.append(f"**{len(leaky)} file(s) leak.** Completion timing depends on a secret.")
    elif clean:
        lines.append(f"All {len(clean)} checked file(s) are constant-time.")
    else:
        lines.append("Nothing was checked.")
    if unknown:
        # Stated separately and up front: a reader who skims the headline must not
        # come away thinking these files were cleared.
        lines.append(
            f"\n**{len(unknown)} file(s) could not be analysed and were NOT checked.** "
            f"They are neither constant-time nor leaky here — no verdict was reached."
        )
    lines += ["", "| File | Verdict | Reaching secrets |", "|---|---|---|"]
    for r in results:
        if r["status"] == "unknown":
            lines.append(f"| `{r['file']}` | **UNKNOWN — not checked** | {r.get('detail', '')[:90]} |")
        elif r["status"] != "checked":
            lines.append(f"| `{r['file']}` | {r['status']} | {r.get('detail', '')} |")
        else:
            mark = "**LEAKY**" if r["verdict"] == "LEAKY" else "constant-time"
            lines.append(
                f"| `{r['file']}` | {mark} | {', '.join(r['reaching_secrets']) or '—'} |"
            )
    lines += [
        "",
        (
            "<sub>Verdicts cover completion timing against declared secrets only — not "
            "power, EM, or cache channels. The checker is a syntactic over-approximation: "
            "`constant-time` is conservative, `LEAKY` names the reaching signals so you "
            "can confirm. Secrets are declared, never inferred.</sub>"
        ),
    ]
    return "\n".join(lines)


def main() -> int:
    files = discover_files(_env("CTA_FILES"))
    default_secrets = _split(_env("CTA_SECRETS"))
    default_obs = _env("CTA_OBSERVATION", "done") or "done"
    config = load_config(_env("CTA_CONFIG"))
    fail_on_leak = _env("CTA_FAIL_ON_LEAK", "true").lower() != "false"

    results: list[dict[str, Any]] = []
    for path in files:
        entry = config.get(str(path)) or config.get(path.name) or {}
        secrets = entry.get("secrets", default_secrets)
        observation = entry.get("observation", default_obs)
        module = entry.get("module")
        if not secrets:
            results.append({
                "file": str(path), "status": "skipped",
                "detail": "no secrets declared for this file; secrets are never inferred",
            })
            continue
        results.append(audit_one(path, observation, secrets, module))

    for r in results:
        annotate(r)

    leaks = sum(1 for r in results if r.get("verdict") == "LEAKY")
    checked = sum(1 for r in results if r["status"] == "checked")
    unknown = sum(1 for r in results if r["status"] == "unknown")

    report_path = Path("ct-audit-report.json")
    report_path.write_text(json.dumps({
        "checked": checked, "leaks": leaks, "unknown": unknown, "results": results,
    }, indent=2) + "\n")

    text = summary(results)
    print("\n" + text)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a") as fh:
            fh.write(text + "\n")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as fh:
            fh.write(
                f"leaks={leaks}\nchecked={checked}\nunknown={unknown}\n"
                f"report={report_path}\n"
            )

    if leaks and fail_on_leak:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
