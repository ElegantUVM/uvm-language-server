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
    c = start(tb, capabilities=CAPS)
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


def test_scopes_files_macros_and_named_labels(tmp_path):
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
