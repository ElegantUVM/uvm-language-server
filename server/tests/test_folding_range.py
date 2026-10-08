"""Structural folds come from matched syntax tokens, including unsaved buffers."""

import os
import shutil

import pytest

from conftest import EXAMPLE
from test_code_actions import Doc, start
from test_lsp import Client

CAPS = {"textDocument": {"foldingRange": {}}}


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    root = tmp_path_factory.mktemp("folding") / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    return str(root)


@pytest.fixture(scope="module")
def client(tb):
    c = start(tb, capabilities=CAPS)
    yield c
    c.close()


@pytest.fixture
def doc(client, tb):
    d = Doc(client, os.path.join(tb, "agent", "alu_agent.svh"))
    yield d
    d.close()


def folds(doc):
    return doc.client.request("textDocument/foldingRange", {"textDocument": {"uri": doc.uri}})


def fold(text, opening, closing, after=""):
    start = text.index(opening, text.index(after))
    end = text.index(closing, start + len(opening))
    return {"startLine": text.count("\n", 0, start), "endLine": text.count("\n", 0, end) - 1}


def ordered(ranges):
    return sorted(ranges, key=lambda r: (r["startLine"], -r["endLine"]))


def agent_folds(text):
    return ordered([fold(text, "class alu_agent", "endclass"),
                    fold(text, "function new", "endfunction"),
                    fold(text, "function void build_phase", "endfunction"),
                    fold(text, "function void connect_phase", "endfunction"),
                    fold(text, "begin", "    end\n")])


@pytest.mark.parametrize("caps,present", [({}, False), (CAPS, True),
    ({"textDocument": {"foldingRange": {"dynamicRegistration": False}}}, True),
    ({"textDocument": {"formatting": {}, "rangeFormatting": {}, "signatureHelp": {},
                       "documentHighlight": {}, "foldingRange": {}}}, True)])
def test_registration(tb, caps, present):
    c = Client()
    try:
        result = c.request("initialize", {"processId": None, "rootUri": f"file://{tb}", "capabilities": caps})
        assert result["capabilities"].get("foldingRangeProvider", False) == present
    finally:
        c.close()


def test_agent_current_buffer(doc):
    original = doc.text
    expected = agent_folds(original)
    assert folds(doc) == expected
    doc.change("\n" + original)
    assert folds(doc) == agent_folds(doc.text)
    doc.change("// no structures\n")
    assert folds(doc) == []
    doc.close()
    assert folds(doc) == expected


def test_driver_extern_bodies(client, tb):
    d = Doc(client, os.path.join(tb, "agent", "alu_driver.svh"))
    try:
        text = d.text
        expected = [fold(text, "class alu_driver", "endclass"),
                    fold(text, "function new", "endfunction"),
                    fold(text, "function void build_phase", "endfunction"),
                    fold(text, "task alu_driver::run_phase", "endtask"),
                    fold(text, "forever begin", "  end\n"),
                    fold(text, "task alu_driver::drive_item", "endtask"),
                    fold(text, "class alu_error_driver", "endclass"),
                    fold(text, "function new", "endfunction", after="class alu_error_driver"),
                    fold(text, "task drive_item", "endtask", after="class alu_error_driver")]
        assert folds(d) == ordered(expected)
    finally:
        d.close()


SOURCE = '''`define BLOCK begin \\
 int generated; \\
 end
virtual class c;
  extern function void prototype(
    int value
  );
  pure virtual task abstract_method(
    int value
  );
  function new(); endfunction
  function void f();
    begin : outer
      begin : inner
        string s = "class function task begin end endclass";
        // begin function class endfunction end
      end : inner
      begin end
      `BLOCK
    end : outer
  endfunction
endclass
module m;
  initial begin : proc
    int x;
    x = 1;
  end : proc
  if (1) begin : gen
    wire x;
  end : gen
endmodule
`ifdef NEVER_DEFINED
class hidden;
  int x;
endclass
`endif
'''


def source_folds(text):
    return ordered([fold(text, "class c", "endclass\n"),
                    fold(text, "function void f", "  endfunction"),
                    fold(text, "begin : outer", "end : outer"),
                    fold(text, "begin : inner", "end : inner"),
                    fold(text, "begin : proc", "end : proc"),
                    fold(text, "begin : gen", "end : gen")])


def test_line_directive_fold(doc):
    doc.change('`line 100 "virtual.sv" 0\nclass c;\n int value;\nendclass\n')
    assert folds(doc) == [{"startLine": 1, "endLine": 2}]


def test_line_directives_inside_folds(doc):
    original = doc.text
    text = '`line 100 "virtual.sv" 0\n' + SOURCE.replace(
        "      end : inner", '`line 1 "nested.sv" 1\n      end : inner\n`line 200 "virtual.sv" 2')
    doc.change(text)
    assert folds(doc) == source_folds(text)
    doc.change("\n" + text)
    assert folds(doc) == source_folds(doc.text)
    doc.close()
    assert folds(doc) == agent_folds(original)


def test_nested_named_procedural_and_generated_blocks(doc):
    doc.change(SOURCE)
    assert folds(doc) == source_folds(SOURCE)
    doc.change(SOURCE.replace("      end : inner", "      // closing token removed"))
    result = folds(doc)
    assert len(result) < len(source_folds(SOURCE))
    assert all(r["endLine"] > r["startLine"] for r in result)


@pytest.mark.parametrize("source,expected", [
    ("class missing;\n  int value;\n", []),
    ("module m; initial begin\n  int value;\nendmodule\n", []),
    ("function void f();\n  int value;\n", []),
    ("class c;\nendclass\n", []),
    ("class c; endclass\n", []),
])
def test_unmatched_and_short_bodies(doc, source, expected):
    doc.change(source)
    assert folds(doc) == expected


@pytest.mark.parametrize("limit", [None, 0, 2])
@pytest.mark.parametrize("line_only", [False, True])
def test_capabilities_and_crlf(tb, limit, line_only):
    caps = {"lineFoldingOnly": line_only}
    if limit is not None:
        caps["rangeLimit"] = limit
    c = start(tb, capabilities={"textDocument": {"foldingRange": caps}})
    try:
        d = Doc(c, os.path.join(tb, "agent", "alu_agent.svh"))
        text = SOURCE.replace("begin : inner", "/* 😀 */ begin : inner").replace("\n", "\r\n").rstrip("\r\n")
        d.change(text)
        assert folds(d) == source_folds(SOURCE)[:limit]
    finally:
        c.close()


def test_duplicate_roots_and_include_endpoints(tmp_path):
    header = tmp_path / "shared.svh"
    header.write_text('class c;\n`include "member.svh"\nendclass\n')
    (tmp_path / "member.svh").write_text("int value;\n")
    (tmp_path / "close.svh").write_text("int x;\nendclass\n")
    (tmp_path / "first.sv").write_text('`include "shared.svh"\nclass cross_file;\n`include "close.svh"\n')
    (tmp_path / "second.sv").write_text('`include "shared.svh"\n')
    c = start(str(tmp_path), capabilities=CAPS, settings={"useBundledUvm": False, "elaboration": "off"})
    try:
        d = Doc(c, str(header))
        assert folds(d) == [{"startLine": 0, "endLine": 1}]
        for name in ("first.sv", "close.svh"):
            other = Doc(c, str(tmp_path / name))
            assert folds(other) == []
    finally:
        c.close()


def test_crossing_intervals_and_shared_start():
    from uvmls.model import Range, StructuralPair
    from uvmls.navigation import Navigator
    from uvmls.workspace import Workspace

    w = Workspace()
    pairs = [(0, 10), (0, 7), (2, 5), (4, 8), (11, 15), (0, 10)]
    w.structures_by_file["a.sv"] = [StructuralPair(Range("a.sv", a, 0, a, 5),
                                                  Range("a.sv", b + 1, 0, b + 1, 3)) for a, b in pairs]
    assert Navigator(w).folding_ranges("a.sv") == [(0, 10), (0, 7), (2, 5), (11, 15)]


def test_deadline_and_intervening_edit(tmp_path, monkeypatch):
    import asyncio
    import threading

    from lsprotocol import types as T
    from uvmls import server as module
    from uvmls.documents import Workspace as LspWorkspace

    async def run():
        path = tmp_path / "a.sv"
        path.write_text("class a;\n int value;\nendclass\n")
        ls = module.UvmLanguageServer()
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.protocol.client_capabilities = T.ClientCapabilities()
        ls.workspace_roots = [str(tmp_path)]
        ls.settings = {"useBundledUvm": False}
        ls.ws.configure(ls.config())
        params = T.FoldingRangeParams(T.TextDocumentIdentifier(path.as_uri()))
        fresh = ls.fresh

        async def expired():
            return False

        ls.fresh = expired
        assert await module.folding_range(ls, params) == []
        ls.fresh = fresh
        entered, release = threading.Event(), threading.Event()
        compute = module._compute_folding_range

        def delayed(*args):
            entered.set()
            release.wait(5)
            return compute(*args)

        monkeypatch.setattr(module, "_compute_folding_range", delayed)
        try:
            task = asyncio.create_task(module.folding_range(ls, params))
            await asyncio.get_running_loop().run_in_executor(None, entered.wait, 5)
            assert entered.is_set()
            ls.pending.add(str(path))
            release.set()
            assert await task == []
        finally:
            release.set()

    asyncio.run(run())
