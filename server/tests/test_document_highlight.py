"""Document highlights use source-spelled index locations over stdio."""

import os
import shutil

import pytest

from conftest import EXAMPLE
from test_code_actions import Doc, start
from test_lsp import Client

CAPS = {"textDocument": {"documentHighlight": {}}}


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    root = tmp_path_factory.mktemp("highlights") / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    return str(root)


@pytest.fixture(scope="module")
def client(tb):
    c = start(tb, capabilities={"textDocument": {"documentHighlight": {}, "foldingRange": {}}})
    yield c
    c.close()


@pytest.fixture
def doc(client, tb):
    d = Doc(client, os.path.join(tb, "agent", "alu_agent.svh"))
    yield d
    d.close()


def position(text, offset, encoding="utf-16-le"):
    prefix = text[:offset]
    return {"line": prefix.count("\n"), "character": len(prefix.rsplit("\n", 1)[-1].encode(encoding)) //
            (2 if encoding == "utf-16-le" else 1)}


def highlight_at(doc, needle, offset=0, encoding="utf-16-le"):
    return doc.client.request("textDocument/documentHighlight", {
        "textDocument": {"uri": doc.uri},
        "position": position(doc.text, doc.text.index(needle) + offset, encoding)})


def highlights(text, needles, encoding="utf-16-le"):
    return [{"range": {"start": position(text, text.index(needle) + offset, encoding),
                       "end": position(text, text.index(needle) + offset + len(word), encoding)}, "kind": 1}
            for needle, offset, word in needles]


@pytest.mark.parametrize("caps,present", [({}, False), (CAPS, True),
    ({"textDocument": {"documentHighlight": {"dynamicRegistration": False}}}, True),
    ({"textDocument": {"formatting": {}, "rangeFormatting": {}, "signatureHelp": {},
                       "documentHighlight": {}, "foldingRange": {}}}, True)])
def test_registration(tb, caps, present):
    c = Client()
    try:
        result = c.request("initialize", {"processId": None, "rootUri": f"file://{tb}", "capabilities": caps})
        assert result["capabilities"].get("documentHighlightProvider", False) == present
    finally:
        c.close()


def agent_highlights(text, encoding="utf-16-le"):
    return highlights(text, [("m_driver;", 0, "m_driver"), ("m_driver    =", 0, "m_driver"),
                             ("m_driver.seq_item_port", 0, "m_driver")], encoding)


def test_agent_occurrences_and_current_buffer(doc):
    original = doc.text
    expected = agent_highlights(original)
    assert highlight_at(doc, "m_driver;", 2) == expected
    doc.change("\n" + original)
    assert highlight_at(doc, "m_driver;") == agent_highlights(doc.text)
    doc.change(doc.text.replace("m_driver", "renamed"))
    assert len(highlight_at(doc, "renamed;")) == 3
    doc.close()
    doc.text = original
    assert highlight_at(doc, "m_driver;") == expected


@pytest.mark.parametrize("needle,offset", [("m_driver;", 8), ("m_driver    =", 8),
    ("// m_driver", 4), ('"m_driver"', 2), ("`ifdef NEVER_DEFINED\n  int m_driver", 28)])
def test_non_identifiers(doc, needle, offset):
    doc.change(doc.text + '\n// m_driver\n`ifdef NEVER_DEFINED\n  int m_driver;\n`endif\n')
    assert highlight_at(doc, needle, offset) == []


@pytest.mark.parametrize("line_directives", [False, True])
def test_scopes_files_macros_and_named_labels(tmp_path, line_directives):
    source = '''`define USE(x) x = x;
`define GENERATED value = value;
class a;
  int value;
  function void f(int value);
    `USE(value)
    `GENERATED
    f(.value(value));
  endfunction
endclass
class b; int value; endclass
'''
    if line_directives:
        source = '`line 100 "virtual.sv" 0\n' + source.replace(
            "class b;", '`line 1 "nested.sv" 1\nclass b;')
    header = tmp_path / "shared.svh"
    header.write_text(source)
    (tmp_path / "first.sv").write_text('`include "shared.svh"\n')
    (tmp_path / "second.sv").write_text('`include "shared.svh"\nclass other; int value; endclass\n')
    c = start(str(tmp_path), capabilities=CAPS,
              settings={"useBundledUvm": False, "elaboration": "off"})
    try:
        d = Doc(c, str(header))
        expected = highlights(source, [("int value;", 4, "value"), ("int value)", 4, "value"),
            ("`USE(value)", 5, "value"), (".value(value)", 1, "value"), (".value(value)", 7, "value"),
            ("class b; int value;", 13, "value")])
        assert highlight_at(d, "int value;", 4) == expected
        assert highlight_at(d, ".value(value)", 1) == expected
        assert highlight_at(d, "`define GENERATED value", 18) == []
        refs = c.request("textDocument/references", {"textDocument": {"uri": d.uri},
            "position": position(source, source.index("int value;") + 4), "context": {"includeDeclaration": True}})
        assert len(refs) == len(expected) + 1
        assert len({(r["uri"], r["range"]["start"]["line"], r["range"]["start"]["character"]) for r in refs}) == len(refs)
    finally:
        c.close()


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8"])
def test_position_encoding_crlf(tb, encoding):
    c = start(tb, capabilities={**CAPS, "general": {"positionEncodings": [encoding]}})
    try:
        d = Doc(c, os.path.join(tb, "agent", "alu_agent.svh"))
        d.change(d.text.replace("m_driver", "/* 😀 */ m_driver").replace("\n", "\r\n").rstrip("\r\n"))
        codec = "utf-16-le" if encoding == "utf-16" else "utf-8"
        assert highlight_at(d, "m_driver;", 1, codec) == agent_highlights(d.text, codec)
        assert highlight_at(d, "m_driver;", 8, codec) == []
    finally:
        c.close()


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8"])
@pytest.mark.parametrize("offset", [0, 7])
def test_escaped_identifiers(tmp_path, encoding, offset):
    text = ('`define USE(x) x = x;\nclass c;\n  int \\foo.bar ;\n'
            '  function void f();\n    /* 😀 */ \\foo.bar = 1;\n'
            '    `USE(\\foo.bar )\n  endfunction\nendclass').replace("\n", "\r\n")
    path = tmp_path / "shared.svh"
    path.write_bytes(text.encode())
    for name in ("first", "second"):
        (tmp_path / (name + ".sv")).write_text('`include "shared.svh"\n')
    c = start(str(tmp_path), capabilities={**CAPS, "general": {"positionEncodings": [encoding]}},
              settings={"useBundledUvm": False, "elaboration": "off"})
    try:
        d = Doc(c, str(path))
        codec = "utf-16-le" if encoding == "utf-16" else "utf-8"
        needles = [("\\foo.bar ;", 0, "\\foo.bar"), ("\\foo.bar =", 0, "\\foo.bar"),
                   ("\\foo.bar )", 0, "\\foo.bar")]
        expected = highlights(text, needles, codec)
        for needle, _, word in needles:
            assert highlight_at(d, needle, offset, codec) == expected
            assert highlight_at(d, needle, len(word), codec) == []
    finally:
        c.close()


def test_line_directive_identifiers(doc):
    text = '`line 100 "virtual.sv" 0\nclass c;\n int value;\nendclass\n'
    doc.change(text)
    assert highlight_at(doc, "value") == highlights(text, [("value", 0, "value")])
    assert highlight_at(doc, "c;", 0) == highlights(text, [("c;", 0, "c")])


def test_deadline_and_intervening_edit(tmp_path):
    import asyncio
    import threading

    from lsprotocol import types as T
    from uvmls import server as module
    from uvmls.documents import Workspace as LspWorkspace

    async def run():
        path = tmp_path / "a.sv"
        path.write_text("class a; int value; endclass\n")
        ls = module.UvmLanguageServer()
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.workspace_roots = [str(tmp_path)]
        ls.settings = {"useBundledUvm": False}
        ls.ws.configure(ls.config())
        params = T.DocumentHighlightParams(T.TextDocumentIdentifier(path.as_uri()), T.Position(0, 14))
        fresh = ls.fresh

        async def expired():
            return False

        ls.fresh = expired
        assert await module.document_highlight(ls, params) == []
        ls.fresh = fresh
        entered, release = threading.Event(), threading.Event()
        compute = module._compute_document_highlight

        def delayed(*args):
            entered.set()
            release.wait(5)
            return compute(*args)

        module._compute_document_highlight = delayed
        try:
            task = asyncio.create_task(module.document_highlight(ls, params))
            await asyncio.get_running_loop().run_in_executor(None, entered.wait, 5)
            assert entered.is_set()
            ls.workspace.put_text_document(T.TextDocumentItem(path.as_uri(), "systemverilog", 2, "// changed"))
            release.set()
            assert await task == []
        finally:
            release.set()
            module._compute_document_highlight = compute
        assert await module.document_highlight(ls, params)

    asyncio.run(run())


def pair_highlights(text, opening, closing, encoding="utf-16-le"):
    return highlights(text, [(opening, 0, opening.split()[0]), (closing, 0, closing.split()[0])], encoding)


@pytest.mark.parametrize("opening,closing", [
    ("class alu_agent", "endclass"),
    ("function new", "endfunction\n\n  function void build_phase"),
    ("function void build_phase", "endfunction\n\n  function void connect_phase"),
    ("function void connect_phase", "endfunction\nendclass"),
    ("begin\n      m_driver", "end\n  endfunction"),
])
def test_agent_keyword_pairs(doc, opening, closing):
    expected = pair_highlights(doc.text, opening, closing)
    assert highlight_at(doc, opening, 1) == expected
    assert highlight_at(doc, closing, 1) == expected
    assert highlight_at(doc, opening, len(opening.split()[0])) == []
    assert highlight_at(doc, closing, len(closing.split()[0])) == []


@pytest.mark.parametrize("needle", ["class c", "endclass"])
def test_line_directive_keywords(doc, needle):
    doc.change('`line 100 "virtual.sv" 0\nclass c;\n int value;\nendclass\n')
    assert highlight_at(doc, needle) == pair_highlights(doc.text, "class c", "endclass")


def test_extern_task_keywords(client, tb):
    d = Doc(client, os.path.join(tb, "agent", "alu_driver.svh"))
    try:
        for opening, closing in [("task alu_driver::run_phase", "endtask\n\n"),
                                 ("task alu_driver::drive_item", "endtask\n\n// Error"),
                                 ("begin\n    seq_item_port", "end\nendtask")]:
            expected = pair_highlights(d.text, opening, closing)
            assert highlight_at(d, opening) == expected
            assert highlight_at(d, closing) == expected
        assert highlight_at(d, "task run_phase") == []
        assert highlight_at(d, "task drive_item") == []
    finally:
        d.close()


def test_nested_named_pairs_and_edits(doc):
    from test_folding_range import SOURCE, folds, source_folds

    original = doc.text
    doc.change(SOURCE)
    for opening, closing in [("begin : outer", "end : outer"), ("begin : inner", "end : inner"),
                             ("begin : proc", "end : proc"), ("begin : gen", "end : gen")]:
        expected = pair_highlights(doc.text, opening, closing)
        assert highlight_at(doc, opening) == expected
        assert highlight_at(doc, closing) == expected
    assert folds(doc) == source_folds(SOURCE)
    doc.change("\n\n" + SOURCE)
    assert highlight_at(doc, "begin : inner") == pair_highlights(doc.text, "begin : inner", "end : inner")
    doc.change("module m; initial begin : unmatched\n  int x;\nendmodule\n")
    assert highlight_at(doc, "begin : unmatched") == []
    assert folds(doc) == []
    doc.close()
    doc.text = original
    assert highlight_at(doc, "class alu_agent") == pair_highlights(original, "class alu_agent", "endclass")


def test_same_line_nested_pairs(doc):
    doc.change("module m; initial begin begin\n  int x;\nend end\nendmodule\n")
    expected = highlights(doc.text, [("begin begin", 0, "begin"), ("end end", 4, "end")])
    assert highlight_at(doc, "begin begin") == expected
    assert highlight_at(doc, "end end", 4) == expected
    expected = highlights(doc.text, [("begin begin", 6, "begin"), ("end end", 0, "end")])
    assert highlight_at(doc, "begin begin", 6) == expected
    assert highlight_at(doc, "end end") == expected
    doc.change("class c; function new(); endfunction endclass")
    assert highlight_at(doc, "function new") == pair_highlights(doc.text, "function new", "endfunction endclass")


@pytest.mark.parametrize("needle,offset", [("`define BLOCK begin", 14), ("\n end\nvirtual", 2),
    ('"class function', 1), ("// begin function", 3), ("class hidden", 0),
    ("function void prototype", 0), ("task abstract_method", 0)])
def test_nonstructural_keywords(doc, needle, offset):
    from test_folding_range import SOURCE

    doc.change(SOURCE)
    assert highlight_at(doc, needle, offset) == []


@pytest.mark.parametrize("source,needle", [("class c;\n int x;", "class"),
    ("function void f();\n int x;", "function"), ("task f();\n int x;", "task")])
def test_unmatched_keywords(doc, source, needle):
    doc.change(source)
    assert highlight_at(doc, needle) == []


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8"])
@pytest.mark.parametrize("line_directives", [False, True])
def test_keyword_encoding_crlf_and_duplicate_includes(tmp_path, encoding, line_directives):
    text = ('/* 😀 */ class c;\r\n  function new();\r\n    /* 😀 */ begin : label\r\n'
            '      int x;\r\n    end : label\r\n  endfunction\r\n'
            '  task run();\r\n    int y;\r\n  endtask\r\nendclass')
    if line_directives:
        text = '`line 100 "virtual.sv" 0\r\n' + text.replace(
            "    end : label", '`line 1 "nested.sv" 1\r\n    end : label\r\n`line 200 "virtual.sv" 2')
    path = tmp_path / "café.svh"
    path.write_bytes(text.encode())
    for name in ("first", "second"):
        (tmp_path / (name + ".sv")).write_text('`include "café.svh"\n')
    c = start(str(tmp_path), capabilities={**CAPS, "general": {"positionEncodings": [encoding]}},
              settings={"useBundledUvm": False, "elaboration": "off"})
    try:
        d = Doc(c, str(path))
        codec = "utf-16-le" if encoding == "utf-16" else "utf-8"

        def check_pairs():
            for opening, closing in [("class c", "endclass"), ("function new", "endfunction"),
                                     ("begin : label", "end : label"), ("task run", "endtask")]:
                expected = pair_highlights(d.text, opening, closing, codec)
                assert highlight_at(d, opening, 1, codec) == expected
                assert highlight_at(d, closing, 1, codec) == expected

        check_pairs()
        d.change("\r\n" + text)
        check_pairs()
        d.close()
        d.text = text
        check_pairs()
    finally:
        c.close()


def test_library_keywords_use_current_source(tmp_path):
    src = tmp_path / "uvm" / "src"
    src.mkdir(parents=True)
    (src / "uvm_macros.svh").write_text("")
    path = src / "uvm_pkg.sv"
    text = "package uvm_pkg;\n/* 😀 */ class uvm_object;\n int value;\nendclass\nendpackage\n"
    path.write_text(text)
    root = tmp_path / "tb"
    root.mkdir()
    (root / "t.sv").write_text("import uvm_pkg::*;\n")
    c = start(str(root), capabilities=CAPS, settings={"uvmHome": str(src.parent), "elaboration": "off"})
    try:
        d = Doc(c, str(path))
        assert highlight_at(d, "class uvm_object") == pair_highlights(text, "class uvm_object", "endclass")
        d.change("\n" + text)
        assert highlight_at(d, "class uvm_object") == pair_highlights(d.text, "class uvm_object", "endclass")
        d.close()
        d.text = text
        assert highlight_at(d, "endclass") == pair_highlights(text, "class uvm_object", "endclass")
    finally:
        c.close()


PROVIDERS = {"formatting": "documentFormattingProvider", "rangeFormatting": "documentRangeFormattingProvider",
             "signatureHelp": "signatureHelpProvider", "documentHighlight": "documentHighlightProvider",
             "foldingRange": "foldingRangeProvider"}


@pytest.mark.parametrize("supported", [[], list(PROVIDERS), *[[name] for name in PROVIDERS]])
def test_independent_provider_registration(tb, supported):
    c = Client()
    try:
        result = c.request("initialize", {"processId": None, "rootUri": f"file://{tb}",
            "capabilities": {"textDocument": {name: {} for name in supported}}})
        for name, provider in PROVIDERS.items():
            assert bool(result["capabilities"].get(provider)) == (name in supported)
    finally:
        c.close()


def test_innermost_pair_at_shared_token():
    from uvmls.model import Range, StructuralPair
    from uvmls.navigation import Navigator
    from uvmls.workspace import Workspace

    w = Workspace()
    opening = Range("a.sv", 0, 0, 0, 5)
    inner_end, outer_end = Range("a.sv", 3, 0, 3, 3), Range("a.sv", 6, 0, 6, 3)
    w.structures_by_file["a.sv"] = [StructuralPair(opening, outer_end), StructuralPair(opening, inner_end)]
    assert Navigator(w).document_highlights("a.sv", 0, 2) == [opening, inner_end]
