"""Tests for the ct-audit GitHub Action.

The action is a shell-free Python script driven by environment variables, which is
precisely so it can be tested without a GitHub runner. Every test below drives
`audit.main()` the same way `action.yml` does.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audit

ACTION_DIR = Path(__file__).resolve().parent.parent

LEAKY = """
module cmp_leaky (clk, rst, start, x, y, done);
    input clk, rst, start;
    input [7:0] x, y;
    output done;
    reg [7:0] xr, yr;
    reg running;
    assign done = running & (xr == yr);
    always @(posedge clk) begin
        if (rst) begin running <= 1'b0; end
        else if (start) begin xr <= x; yr <= y; running <= 1'b1; end
        else if (running) begin if (xr != yr) running <= 1'b0; end
    end
endmodule
"""

CLEAN = """
module cmp_ct (clk, rst, start, x, y, done);
    input clk, rst, start;
    input [7:0] x, y;
    output done;
    reg [7:0] xr, yr;
    reg running;
    reg [3:0] cnt;
    assign done = running & (cnt == 4'd8);
    always @(posedge clk) begin
        if (rst) begin running <= 1'b0; cnt <= 4'd0; end
        else if (start) begin xr <= x; yr <= y; cnt <= 4'd0; running <= 1'b1; end
        else if (running) begin cnt <= cnt + 1'b1; end
    end
endmodule
"""


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A scratch repo with a leaky and a clean module, and a clean environment."""
    (tmp_path / "leaky.v").write_text(LEAKY)
    (tmp_path / "clean.v").write_text(CLEAN)
    monkeypatch.chdir(tmp_path)
    for k in list(os.environ):
        if k.startswith(("CTA_", "GITHUB_")):
            monkeypatch.delenv(k, raising=False)
    return tmp_path


def _run(monkeypatch, **env) -> int:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return audit.main()


# ---------------------------------------------------------------------------
# Verdicts and exit codes.
# ---------------------------------------------------------------------------

def test_leak_fails_the_job(workdir, monkeypatch, capsys):
    rc = _run(monkeypatch, CTA_FILES="leaky.v", CTA_SECRETS="x,y")
    assert rc == 1
    out = capsys.readouterr().out
    assert "::error file=leaky.v::" in out
    assert "x, y" in out


def test_clean_file_passes(workdir, monkeypatch):
    assert _run(monkeypatch, CTA_FILES="clean.v", CTA_SECRETS="x,y") == 0


def test_fail_on_leak_false_reports_without_failing(workdir, monkeypatch):
    rc = _run(monkeypatch, CTA_FILES="leaky.v", CTA_SECRETS="x,y", CTA_FAIL_ON_LEAK="false")
    assert rc == 0
    report = json.loads(Path("ct-audit-report.json").read_text())
    assert report["leaks"] == 1, "the leak must still be reported"


# ---------------------------------------------------------------------------
# Secrets are declared, never inferred.
# ---------------------------------------------------------------------------

def test_file_without_declared_secrets_is_skipped_not_guessed(workdir, monkeypatch, capsys):
    rc = _run(monkeypatch, CTA_FILES="leaky.v")
    assert rc == 0, "a skip is not a failure"
    out = capsys.readouterr().out
    assert "::notice file=leaky.v::ct-audit skipped" in out
    report = json.loads(Path("ct-audit-report.json").read_text())
    assert report["checked"] == 0
    assert report["results"][0]["status"] == "skipped"
    assert "never inferred" in report["results"][0]["detail"]


# ---------------------------------------------------------------------------
# Config file, globs, discovery.
# ---------------------------------------------------------------------------

def test_config_file_supplies_per_file_secrets(workdir, monkeypatch):
    cfg = workdir / "ct.json"
    cfg.write_text(json.dumps({
        "scored": [
            {"file": "leaky.v", "observation": "done", "secrets": ["x", "y"]},
            {"file": "clean.v", "observation": "done", "secrets": ["x", "y"]},
        ]
    }))
    rc = _run(monkeypatch, CTA_FILES="leaky.v,clean.v", CTA_CONFIG=str(cfg))
    assert rc == 1
    report = json.loads(Path("ct-audit-report.json").read_text())
    assert report["checked"] == 2
    assert report["leaks"] == 1


def test_config_overrides_the_default_secrets(workdir, monkeypatch):
    """A file listed in the config uses its own secrets, not the global input."""
    cfg = workdir / "ct.json"
    cfg.write_text(json.dumps([{"file": "leaky.v", "observation": "done", "secrets": []}]))
    # empty per-file secrets -> skipped, even though CTA_SECRETS is set
    rc = _run(monkeypatch, CTA_FILES="leaky.v", CTA_SECRETS="x,y", CTA_CONFIG=str(cfg))
    assert rc == 0
    report = json.loads(Path("ct-audit-report.json").read_text())
    assert report["results"][0]["status"] == "skipped"


def test_glob_expansion(workdir, monkeypatch):
    _run(monkeypatch, CTA_FILES="*.v", CTA_SECRETS="x,y")
    report = json.loads(Path("ct-audit-report.json").read_text())
    assert {Path(r["file"]).name for r in report["results"]} == {"leaky.v", "clean.v"}


def test_discovery_falls_back_when_no_files_given(workdir, monkeypatch):
    _run(monkeypatch, CTA_SECRETS="x,y")
    report = json.loads(Path("ct-audit-report.json").read_text())
    assert report["checked"] == 2


def test_newline_separated_files(workdir, monkeypatch):
    _run(monkeypatch, CTA_FILES="leaky.v\nclean.v", CTA_SECRETS="x,y")
    report = json.loads(Path("ct-audit-report.json").read_text())
    assert report["checked"] == 2


# ---------------------------------------------------------------------------
# Robustness.
# ---------------------------------------------------------------------------

def test_unparseable_file_warns_and_does_not_crash(workdir, monkeypatch, capsys):
    (workdir / "junk.v").write_text("this is not verilog at all")
    rc = _run(monkeypatch, CTA_FILES="junk.v", CTA_SECRETS="x")
    out = capsys.readouterr().out
    assert "::warning file=junk.v::" in out
    assert rc == 0, "an unparseable file is not a leak"


def test_missing_observation_signal_is_an_error_not_a_pass(workdir, monkeypatch, capsys):
    """A file with no completion signal must never land in the checked-and-clean column."""
    (workdir / "nodone.v").write_text("module nodone(a); input a; endmodule")
    _run(monkeypatch, CTA_FILES="nodone.v", CTA_SECRETS="a")
    report = json.loads(Path("ct-audit-report.json").read_text())
    assert report["results"][0]["status"] in ("error", "unknown")
    assert report["checked"] == 0
    assert report["results"][0].get("verdict") != "CONSTANT_TIME"


def test_an_unanalysable_file_is_reported_as_unknown_not_constant_time(workdir, monkeypatch, capsys):
    """The Action-level form of the unsoundness bug.

    `sub u(...)` hides every dependency edge, so the checker cannot see whether
    `done` depends on the key. The summary table used to render anything that was
    not LEAKY as "constant-time", which turned "we could not read this" into a pass
    displayed on the PR page.
    """
    (workdir / "hier.v").write_text(
        "module top(clk, key, done);\n"
        "  input clk; input [7:0] key; output done;\n"
        "  child u_child (.clk(clk), .key(key), .done(done));\n"
        "endmodule\n"
    )
    _run(monkeypatch, CTA_FILES="hier.v", CTA_SECRETS="key")
    out = capsys.readouterr().out
    report = json.loads(Path("ct-audit-report.json").read_text())

    assert report["results"][0]["status"] == "unknown"
    assert report["unknown"] == 1
    assert report["checked"] == 0
    assert "::warning" in out and "NO VERDICT" in out
    # the rendered table must not describe it as constant-time
    assert "UNKNOWN — not checked" in out
    assert "| constant-time |" not in out


# ---------------------------------------------------------------------------
# GitHub integration surfaces.
# ---------------------------------------------------------------------------

def test_step_summary_and_outputs_are_written(workdir, monkeypatch):
    smry, outs = workdir / "summary.md", workdir / "outputs.txt"
    _run(monkeypatch, CTA_FILES="leaky.v", CTA_SECRETS="x,y",
         GITHUB_STEP_SUMMARY=str(smry), GITHUB_OUTPUT=str(outs))
    s = smry.read_text()
    assert "## Constant-time audit" in s
    assert "**LEAKY**" in s
    assert "never inferred" in s, "the summary must state the scope"
    o = outs.read_text()
    assert "leaks=1" in o and "checked=1" in o and "report=ct-audit-report.json" in o


def test_summary_lists_every_file_including_skips(workdir, monkeypatch):
    smry = workdir / "s.md"
    cfg = workdir / "c.json"
    cfg.write_text(json.dumps([{"file": "clean.v", "secrets": []}]))
    _run(monkeypatch, CTA_FILES="*.v", CTA_SECRETS="x,y", CTA_CONFIG=str(cfg),
         GITHUB_STEP_SUMMARY=str(smry))
    s = smry.read_text()
    assert "leaky.v" in s and "clean.v" in s
    assert "skipped" in s


# ---------------------------------------------------------------------------
# The action metadata itself.
# ---------------------------------------------------------------------------

def test_action_yml_is_valid_and_wires_every_input():
    meta = yaml.safe_load((ACTION_DIR / "action.yml").read_text())
    assert meta["runs"]["using"] == "composite"
    env = meta["runs"]["steps"][-1]["env"]
    # every declared input must reach the script
    for name in meta["inputs"]:
        if name == "python-version":
            continue
        key = "CTA_" + name.replace("-", "_").upper()
        assert key in env, f"input {name!r} is declared but never passed to audit.py"
    for key in env:
        assert key.startswith("CTA_")


def test_action_outputs_match_what_the_script_writes():
    meta = yaml.safe_load((ACTION_DIR / "action.yml").read_text())
    assert set(meta["outputs"]) == {"leaks", "checked", "unknown", "report"}


def test_action_installs_the_checker():
    meta = yaml.safe_load((ACTION_DIR / "action.yml").read_text())
    install = [s for s in meta["runs"]["steps"] if "pip install" in s.get("run", "")]
    assert install and "ctbench" in install[0]["run"]
