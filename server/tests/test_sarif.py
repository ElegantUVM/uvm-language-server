"""SARIF's required shape and CLI output, checked without downloading a schema."""

import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote

import pytest

from uvmls import __version__
from uvmls.cli import main as cli
from uvmls.model import Diag, Range

from conftest import EXAMPLE


def check_log(log):
    assert log["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert log["version"] == "2.1.0" and len(log["runs"]) == 1
    [run] = log["runs"]
    assert run["columnKind"] == "utf16CodeUnits"
    driver = run["tool"]["driver"]
    assert driver["name"] == "uvmls" and driver["version"] == __version__
    assert driver["informationUri"].startswith("https://")
    rules = driver["rules"]
    assert len({r["id"] for r in rules}) == len(rules)
    assert {r["id"] for r in rules} == {r["ruleId"] for r in run["results"]}
    for rule in rules:
        assert rule["shortDescription"]["text"] and "\n" not in rule["shortDescription"]["text"]
        assert rule["defaultConfiguration"]["level"] in ("error", "warning", "note")
        assert set(rule["properties"]["tags"]) <= {"uvm", "slang", "slang-elab"}
    assert run["originalUriBaseIds"]["SRCROOT"]["uri"].startswith("file://")
    assert run["originalUriBaseIds"]["SRCROOT"]["uri"].endswith("/")
    for result in run["results"]:
        assert rules[result["ruleIndex"]]["id"] == result["ruleId"]
        assert result["level"] in ("error", "warning", "note")
        assert result["message"]["text"]
        location = result["locations"][0]["physicalLocation"]
        artifact = location["artifactLocation"]
        assert artifact["uri"] and "\\" not in artifact["uri"] and " " not in artifact["uri"]
        if artifact["uri"].startswith("file://"):
            assert "uriBaseId" not in artifact
        else:
            assert artifact["uriBaseId"] == "SRCROOT" and not artifact["uri"].startswith("/")
        region = location["region"]
        assert region["startLine"] >= 1 and region["startColumn"] >= 1
        if "endLine" in region:
            assert (region["endLine"], region["endColumn"]) > (region["startLine"], region["startColumn"])
        else:
            assert "endColumn" not in region
        assert re.fullmatch(r"[0-9a-f]{64}", result["partialFingerprints"]["primaryLocationLineHash"])
    return run


def invoke(capsys, args):
    with pytest.raises(SystemExit) as exc:
        cli(args)
    return exc.value.code, capsys.readouterr()


@pytest.fixture
def tb(tmp_path, monkeypatch):
    monkeypatch.delenv("UVM_HOME", raising=False)
    root = tmp_path / "alu tb é"
    shutil.copytree(EXAMPLE, root)
    agent = root / "agent" / "alu_agent.svh"
    agent.write_bytes(agent.read_bytes().replace(b'create("m_driver", this)', b'create("m_driver")'))
    return root


def test_lint_example_regions_and_stable_bytes(tb, tmp_path, capsys):
    output = tmp_path / "out.sarif"
    args = ["lint", str(tb), "-f", str(tb / "alu_tb.f")]
    plain_code, plain = invoke(capsys, args)
    code, text = invoke(capsys, [*args, "--sarif", str(output)])
    assert code == plain_code == 1 and text == plain
    first = output.read_bytes()
    run = check_log(json.loads(first))
    assert run["originalUriBaseIds"]["SRCROOT"]["uri"] == tb.as_uri() + "/"
    assert len(run["results"]) == len(text.out.splitlines())
    [finding] = [r for r in run["results"] if r["ruleId"] == "create-no-parent"]
    physical = finding["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"] == {"uri": "agent/alu_agent.svh", "uriBaseId": "SRCROOT"}
    lines = (tb / "agent" / "alu_agent.svh").read_text().splitlines()
    line = next(i for i, text in enumerate(lines) if 'create("m_driver")' in text)
    col = lines[line].index("alu_driver::")
    assert physical["region"] == {"startLine": line + 1, "startColumn": col + 1,
                                  "endLine": line + 1, "endColumn": lines[line].index(";", col) + 1}
    assert invoke(capsys, [*args, "--sarif", str(output)])[0] == code
    assert output.read_bytes() == first


@pytest.mark.parametrize("command", ["lint", "elab"])
@pytest.mark.parametrize("as_json", [False, True])
def test_stdout_is_only_sarif_and_file_can_accompany_json(tb, tmp_path, capsys, command, as_json):
    args = [command, str(tb), "-f", str(tb / "alu_tb.f"), *(["--json"] if as_json else [])]
    plain_code, plain = invoke(capsys, args)
    code, out = invoke(capsys, [*args, "--sarif", "-"])
    assert code == plain_code == 1
    run = check_log(json.loads(out.out))
    if command == "elab":
        assert all(r["properties"]["tags"] == ["slang-elab"] for r in run["tool"]["driver"]["rules"])
    output = tmp_path / "out.sarif"
    code, out = invoke(capsys, [*args, "--sarif", str(output)])
    assert code == plain_code
    if as_json and command == "elab":
        assert json.loads(out.out)["diagnostics"] == json.loads(plain.out)["diagnostics"]
    else:
        assert out.out == plain.out
    assert json.loads(output.read_text())["runs"][0]["results"] == run["results"]


def test_info_crlf_and_non_ascii_path(tmp_path, capsys):
    path = tmp_path / "réglage #1.sv"
    path.write_bytes(b'class child extends absent;\r\nendclass\r\n')
    code, out = invoke(capsys, ["lint", str(tmp_path), "--no-bundled-uvm", "--sarif", "-"])
    assert code == 0
    run = check_log(json.loads(out.out))
    [result] = run["results"]
    assert result["ruleId"] == "unknown-base" and result["level"] == "note"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == quote(path.name)
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 1


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_lint_columns_use_utf16(tb, capsys, newline):
    path = tb / "agent" / "alu_agent.svh"
    text = path.read_text().replace('alu_driver::type_id::create', '/* é😀 */ alu_driver::type_id::create')
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    code, out = invoke(capsys, ["lint", str(tb), "-f", str(tb / "alu_tb.f"), "--sarif", "-"])
    assert code == 1
    [run] = json.loads(out.out)["runs"]
    [result] = [r for r in run["results"] if r["ruleId"] == "create-no-parent"]
    lines = text.split("\n")
    line = next(i for i, s in enumerate(lines) if 'create("m_driver")' in s)
    start = lines[line].index("alu_driver::")
    end = lines[line].index(";", start)
    assert result["locations"][0]["physicalLocation"]["region"] == {
        "startLine": line + 1, "startColumn": len(lines[line][:start].encode("utf-16-le")) // 2 + 1,
        "endLine": line + 1, "endColumn": len(lines[line][:end].encode("utf-16-le")) // 2 + 1}
    assert run["columnKind"] == "utf16CodeUnits"


def test_elab_columns_use_utf16(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("UVM_HOME", raising=False)
    text = 'module top; /* é😀 */ initial $display(no_such); endmodule\n'
    (tmp_path / "top.sv").write_text(text, encoding="utf-8")
    code, out = invoke(capsys, ["elab", str(tmp_path), "--sarif", "-"])
    assert code == 1
    [run] = json.loads(out.out)["runs"]
    [result] = [r for r in run["results"] if "no_such" in r["message"]["text"]]
    col = len(text[:text.index("no_such")].encode("utf-16-le")) // 2 + 1
    assert result["locations"][0]["physicalLocation"]["region"] == {
        "startLine": 1, "startColumn": col, "endLine": 1, "endColumn": col + len("no_such")}
    assert run["columnKind"] == "utf16CodeUnits"


def test_rules_ranges_external_paths_and_fingerprints(tmp_path):
    from uvmls.sarif import build_log

    root = tmp_path / "src"
    root.mkdir()
    path = root / "t.sv"
    path.write_bytes(b'\r\n  call( x );\r\n')
    external = tmp_path / "include é" / "t.svh"
    external.parent.mkdir()
    external.write_text("external\n")
    diags = [Diag(Range(str(path), 1, 2, 1, 12), "first", "warning", "config-db-no-set", "uvm"),
             Diag(Range(str(path), 0, 0, 0, 0), "second", "info", "config-db-no-set", "uvm"),
             Diag(Range(str(external), 0, 0, 0, 8), "third", "error", "TypoIdentifier"),
             Diag(Range(str(root / "missing.sv"), 0, 0, 0, 0), "missing", "error", "io")]
    run = check_log(build_log(root, diags))
    rules = {r["id"]: r for r in run["tool"]["driver"]["rules"]}
    assert rules["config-db-no-set"]["defaultConfiguration"]["level"] == "warning"
    assert rules["config-db-no-set"]["properties"]["tags"] == ["uvm"]
    assert rules["TypoIdentifier"]["shortDescription"]["text"] == "TypoIdentifier"
    assert rules["TypoIdentifier"]["properties"]["tags"] == ["slang"]
    results = {r["message"]["text"]: r for r in run["results"]}
    assert results["second"]["locations"][0]["physicalLocation"]["region"] == {"startLine": 1, "startColumn": 1}
    assert results["third"]["locations"][0]["physicalLocation"]["artifactLocation"] == {"uri": external.as_uri()}
    fingerprint = results["first"]["partialFingerprints"]
    expected = hashlib.sha256(json.dumps(["t.sv", "config-db-no-set", "call( x );"],
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    assert fingerprint == {"primaryLocationLineHash": expected}
    path.write_text("// unrelated edit\n\n\tcall(   x );\n")
    moved = Diag(Range(str(path), 2, 1, 2, 13), "first", "warning", "config-db-no-set", "uvm")
    assert build_log(root, [moved])["runs"][0]["results"][0]["partialFingerprints"] == fingerprint


def test_uvm_descriptions_match_the_readme(tmp_path):
    from uvmls.sarif import build_log

    readme = Path(__file__).resolve().parents[2] / "README.md"
    table = dict(re.findall(r"^\| `([^`]+)` \| ([^|]+) \|$", readme.read_text(), re.M))
    assert len(table) == 11
    span = Range(str(tmp_path / "t.sv"), 0, 0, 0, 1)
    run = build_log(tmp_path, [Diag(span, "finding", "warning", code, "uvm") for code in table])["runs"][0]
    assert {r["id"]: r["shortDescription"]["text"] for r in run["tool"]["driver"]["rules"]} == table


def test_clean_elab_and_skipped_elab_keep_exit_codes(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("UVM_HOME", raising=False)
    (tmp_path / "top.sv").write_text("module top; endmodule\n")
    args = ["elab", str(tmp_path), "--sarif", "-"]
    code, out = invoke(capsys, args)
    assert code == 0 and check_log(json.loads(out.out))["results"] == []
    code, out = invoke(capsys, [*args, "--no-bundled-uvm"])
    assert code == 2 and "elaboration skipped" in out.err


def test_sarif_rejects_non_diagnostic_commands(capsys):
    code, out = invoke(capsys, ["stats", "--sarif", "-"])
    assert code == 2 and "lint and elab" in out.err


def test_filesystem_root_keeps_the_file_uri_authority(tmp_path):
    from uvmls.sarif import build_log

    root = Path(tmp_path.anchor)
    run = build_log(root, [])["runs"][0]
    assert run["originalUriBaseIds"]["SRCROOT"]["uri"] == root.as_uri()
