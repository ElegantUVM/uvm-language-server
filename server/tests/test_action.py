"""Offline checks of the composite action, including its actual shell steps."""

import copy
import json
import os
import re
import runpy
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uvmls.model import Diag, Range
from uvmls.sarif import build_log

from conftest import EXAMPLE
from test_sarif import check_log

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the bash composite action runs on Linux runners")

REPO = Path(__file__).resolve().parents[2]
MERGE = REPO / "tools" / "merge_sarif.py"


def load_manifest(path):
    """Read the small YAML subset these manifests use, with no YAML dependency."""
    lines = path.read_text(encoding="utf-8").splitlines()

    def scalar(value):
        if value.startswith('"'):
            return json.loads(value)
        if value in ("true", "false"):
            return value == "true"
        return value

    def block(start, indent):
        entries = []
        mapping = {}
        i = start
        while i < len(lines):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                i += 1
                continue
            depth = len(line) - len(line.lstrip())
            if depth < indent:
                break
            assert depth == indent and "\t" not in line, (path, i, line)
            text = line[indent:]
            if text.startswith("- "):
                # Treat each step as a mapping so nested env/with/run blocks share the parser.
                lines[i] = " " * (indent + 2) + text[2:]
                value, i = block(i, indent + 2)
                entries.append(value)
                continue
            key, sep, value = text.partition(":")
            assert sep and re.fullmatch(r"[\w-]+", key), (path, i, line)
            assert key not in mapping
            value = value.strip()
            i += 1
            if value == "|":
                start_text = i
                while i < len(lines) and (not lines[i].strip() or lines[i].startswith(" " * (indent + 2))):
                    i += 1
                mapping[key] = "\n".join(s[indent + 2:] for s in lines[start_text:i]) + "\n"
            elif value:
                mapping[key] = scalar(value)
            elif i < len(lines) and len(lines[i]) - len(lines[i].lstrip()) > indent:
                mapping[key], i = block(i, indent + 2)
            else:
                mapping[key] = None
        assert not (entries and mapping)
        return entries or mapping, i

    result, end = block(0, 0)
    assert end == len(lines)
    return result


def action():
    return load_manifest(REPO / "action.yml")


def test_action_and_workflow_contract():
    manifest = action()
    assert manifest["name"] == "UVM lint" and manifest["runs"]["using"] == "composite"
    inputs = manifest["inputs"]
    assert set(inputs) == {"filelist", "uvm-home", "python-version", "elab", "fail-on", "upload"}
    assert inputs["upload"]["default"] == "true"
    assert inputs["filelist"]["required"] is True
    assert inputs["uvm-home"].get("required", False) is False
    assert {k: inputs[k]["default"] for k in ("python-version", "elab", "fail-on")} == {
        "python-version": "3.12", "elab": "false", "fail-on": "warning"}
    assert set(manifest["outputs"]) == {"sarif-file", "problems"}
    steps = manifest["runs"]["steps"]
    uses = [s["uses"] for s in steps if "uses" in s]
    assert all(re.fullmatch(r"[\w-]+/[\w/-]+@v\d+", use) for use in uses)
    assert any(use.startswith("actions/setup-python@") for use in uses)
    [upload] = [s for s in steps if s.get("uses") == "github/codeql-action/upload-sarif@v3"]
    assert upload["with"]["sarif_file"] == "uvmls.sarif"
    assert "always()" in upload["if"] and steps.index(upload) < len(steps) - 1
    assert "always()" in steps[-1]["if"]
    for step in steps:
        if "run" in step:
            assert step["shell"] == "bash"
            assert "${{ inputs." not in step["run"]
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
    [install] = [s for s in steps if "pip install" in s.get("run", "")]
    assert "pip install pyslang 'pygls>=2,<3'" in install["run"]
    assert "pip install ./server" in install["run"] and "UVMLS_ACTION_PATH" in install["run"]
    workflow = load_manifest(REPO / ".github" / "workflows" / "uvm-lint.yml")
    assert set(workflow["on"]) == {"push", "pull_request"}
    assert workflow["permissions"]["security-events"] == "write"
    [job] = workflow["jobs"].values()
    assert int(job["timeout-minutes"]) <= 2
    assert job["steps"][-1]["uses"] == "./"
    assert job["steps"][-1]["with"]["filelist"] == "examples/alu_tb/alu_tb.f"
    for step in job["steps"]:
        assert step["uses"].startswith("./") or re.fullmatch(r"[\w-]+/[\w/-]+@v\d+", step["uses"])


def test_merge_keeps_one_run_and_remaps_rules(tmp_path):
    merge = runpy.run_path(str(MERGE))["merge_logs"]
    span = Range(str(tmp_path / "t.sv"), 0, 0, 0, 1)
    lint = build_log(tmp_path, [Diag(span, "parse", "warning", "Shared"),
                                 Diag(span, "lint", "info", "config-db-no-set", "uvm")])
    elab = build_log(tmp_path, [Diag(span, "elab", "error", "Shared", "slang-elab"),
                                 Diag(span, "another", "error", "Other", "slang-elab")])
    before = copy.deepcopy([lint, elab])
    merged = merge([lint, elab])
    run = check_log(merged)
    assert [lint, elab] == before
    assert len(run["results"]) == 4
    assert [r["message"]["text"] for r in run["results"]] == ["parse", "lint", "elab", "another"]
    [shared] = [r for r in run["tool"]["driver"]["rules"] if r["id"] == "Shared"]
    assert shared["defaultConfiguration"]["level"] == "error"
    assert shared["properties"]["tags"] == ["slang", "slang-elab"]
    assert run["results"][2]["partialFingerprints"] == elab["runs"][0]["results"][0]["partialFingerprints"]
    elab["runs"][0]["originalUriBaseIds"]["SRCROOT"]["uri"] = "file:///elsewhere/"
    with pytest.raises(ValueError, match="workspace"):
        merge([lint, elab])


def shell_step(step, cwd, env):
    python = shlex.quote(str(Path(sys.prefix) / "bin" / "python"))
    script = 'python() { ' + python + ' "$@"; }\n' + step["run"]
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                          cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("elab", ["false", "true"])
def test_action_scans_the_callers_checkout_and_writes_outputs(tmp_path, elab):
    root = tmp_path / "caller"
    shutil.copytree(EXAMPLE, root / "tb")
    agent = root / "tb" / "agent" / "alu_agent.svh"
    agent.write_bytes(agent.read_bytes().replace(b'create("m_driver", this)', b'create("m_driver")'))
    # Input quoting must survive shell metacharacters without interpreting them.
    filelist = root / "tb" / "tb $(touch unexpected).f"
    (root / "tb" / "alu_tb.f").rename(filelist)
    output = tmp_path / "outputs"
    env = {"UVMLS_FILELIST": str(filelist.relative_to(root)), "UVMLS_UVM_HOME": "",
           "UVMLS_ELAB": elab, "UVMLS_ACTION_PATH": str(REPO), "GITHUB_WORKSPACE": str(root),
           "GITHUB_OUTPUT": str(output), "UVM_HOME": "", "PYTHONPATH": str(REPO / "server")}
    steps = action()["runs"]["steps"]
    scan = next(s for s in steps if s.get("id") == "scan")
    result = shell_step(scan, root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert Path(values["sarif-file"]) == root / "uvmls.sarif"
    run = check_log(json.loads((root / "uvmls.sarif").read_text(encoding="utf-8")))
    assert int(values["problems"]) == len(run["results"]) >= 2
    assert values["lint-status"] == "1" and values["elab-status"] == ("1" if elab == "true" else "0")
    tags = {tag for r in run["tool"]["driver"]["rules"] for tag in r["properties"]["tags"]}
    assert ("slang-elab" in tags) == (elab == "true")
    assert all(r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"].startswith("tb/")
               for r in run["results"])
    assert not (root / "unexpected").exists()


@pytest.mark.parametrize("elab", ["false", "true"])
@pytest.mark.parametrize("policy", ["warning", "error", "never"])
@pytest.mark.parametrize("mode", ["missing", "unreadable", "nested-missing", "nested-unreadable", "empty"])
def test_action_rejects_unreadable_filelists(tmp_path, elab, policy, mode):
    (tmp_path / "top.sv").write_text("module top; endmodule\n", encoding="utf-8", newline="")
    filelist = tmp_path / "tb.f"
    if mode.startswith("nested"):
        (tmp_path / "lists").mkdir()
        filelist.write_text('top.sv\n-f "lists/inner list.f"\n', encoding="utf-8", newline="")
        (tmp_path / "lists" / "inner list.f").write_text("-F ../absent.f\n", encoding="utf-8", newline="")
    unreadable = tmp_path / "absent.f" if mode.startswith("nested") else filelist
    if mode.endswith("unreadable"):
        unreadable.write_text("top.sv\n", encoding="utf-8", newline="")
        unreadable.chmod(0)
        if os.access(unreadable, os.R_OK):
            unreadable.chmod(0o600)
            pytest.skip("file permissions do not prevent this user from reading")
    output = tmp_path / "outputs"
    env = {"UVMLS_FILELIST": "" if mode == "empty" else "tb.f", "UVMLS_UVM_HOME": "",
           "UVMLS_ELAB": elab, "UVMLS_ACTION_PATH": str(REPO), "GITHUB_WORKSPACE": str(tmp_path),
           "GITHUB_OUTPUT": str(output), "UVM_HOME": "", "PYTHONPATH": str(REPO / "server")}
    steps = action()["runs"]["steps"]
    scan = next(s for s in steps if s.get("id") == "scan")
    try:
        result = shell_step(scan, tmp_path, env)
    finally:
        if mode.endswith("unreadable"):
            unreadable.chmod(0o600)
    if result.returncode == 0:
        values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
        gate = shell_step(steps[-1], tmp_path, {**env, "UVMLS_FAIL_ON": policy,
                         "UVMLS_LINT_STATUS": values["lint-status"], "UVMLS_ELAB_STATUS": values["elab-status"]})
        assert gate.returncode != 0, f"action passed with {mode} filelist: {values}"
    assert result.returncode != 0, "filelist loading must fail before analysis"
    assert "cannot read filelist" in result.stderr
    assert not output.exists() and not (tmp_path / "uvmls.sarif").exists()


@pytest.mark.parametrize("policy,level,status", [
    ("warning", "note", 0), ("warning", "warning", 1), ("warning", "error", 1),
    ("error", "note", 0), ("error", "warning", 0), ("error", "error", 1),
    ("never", "note", 0), ("never", "warning", 0), ("never", "error", 0),
])
def test_fail_on_policy(tmp_path, policy, level, status):
    (tmp_path / "uvmls.sarif").write_text(json.dumps({"runs": [{"results": [{"level": level}]}]}), encoding="utf-8", newline="")
    step = action()["runs"]["steps"][-1]
    result = shell_step(step, tmp_path, {"UVMLS_FAIL_ON": policy, "UVMLS_LINT_STATUS": "1",
                                       "UVMLS_ELAB_STATUS": "0"})
    assert result.returncode == status, result.stderr


@pytest.mark.parametrize("policy,lint,elab", [("invalid", "0", "0"), ("never", "2", "0"),
                                             ("never", "0", "2"), ("never", "127", "0")])
def test_invalid_policy_and_failed_analysis_cannot_pass(tmp_path, policy, lint, elab):
    (tmp_path / "uvmls.sarif").write_text(json.dumps({"runs": [{"results": []}]}), encoding="utf-8", newline="")
    result = shell_step(action()["runs"]["steps"][-1], tmp_path,
                        {"UVMLS_FAIL_ON": policy, "UVMLS_LINT_STATUS": lint, "UVMLS_ELAB_STATUS": elab})
    assert result.returncode != 0


@pytest.mark.parametrize("policy,errors,warnings,status", [
    ("warning", 0, 1, 1), ("error", 1, 0, 1), ("error", 0, 1, 0), ("never", 1, 1, 0),
])
def test_elaboration_counts_include_diagnostics_beyond_the_listing_cap(tmp_path, policy, errors, warnings, status):
    (tmp_path / "uvmls.sarif").write_text(json.dumps({"runs": [{"results": [{"level": "note"}]}]}), encoding="utf-8", newline="")
    (tmp_path / "uvmls-elab.json").write_text(json.dumps({"counts": {"errors": errors, "warnings": warnings}}), encoding="utf-8", newline="")
    result = shell_step(action()["runs"]["steps"][-1], tmp_path,
                        {"UVMLS_FAIL_ON": policy, "UVMLS_LINT_STATUS": "0", "UVMLS_ELAB_STATUS": str(int(bool(errors))),
                         "UVMLS_ELAB": "true"})
    assert result.returncode == status, result.stderr
