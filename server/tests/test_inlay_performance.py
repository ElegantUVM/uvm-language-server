"""Cold hints leave the event loop responsive and restrict resolution to the viewport."""

import asyncio
import threading
import time
from unittest.mock import patch

from lsprotocol import types as T

from test_fixes import index
from uvmls.documents import Workspace as LspWorkspace
from uvmls.navigation import Navigator
from uvmls.server import UvmLanguageServer, inlay_hint


def test_cold_member_hints_yield_to_the_event_loop(tmp_path):
    text = '''import uvm_pkg::*;
class item extends uvm_sequence_item; endclass
class driver extends uvm_driver #(item);
  function void use_members();
''' + "    req = req;\n" * 6000 + "  endfunction\nendclass\n"
    ws = index(tmp_path, {"t.sv": text})

    async def run():
        ls = UvmLanguageServer()
        ls.ws, ls.nav = ws, Navigator(ws)
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        params = T.InlayHintParams(T.TextDocumentIdentifier((tmp_path / "t.sv").as_uri()),
                                  T.Range(T.Position(4, 0), T.Position(6, 0)))
        ticks = []
        start = time.perf_counter()
        asyncio.get_running_loop().call_soon(lambda: ticks.append(time.perf_counter() - start))
        found = await inlay_hint(ls, params)
        await asyncio.sleep(0)
        assert ticks and ticks[0] < .1
        assert [(h.position.line, h.position.character, h.label) for h in found] == [
            (4, 7, ": item"), (5, 7, ": item")]
        samples = []
        for _ in range(20):
            start = time.perf_counter()
            assert await inlay_hint(ls, params) == found
            samples.append(time.perf_counter() - start)
        assert sorted(samples)[18] < .05
    asyncio.run(run())


def test_member_hint_resolution_is_limited_to_requested_lines(tmp_path):
    text = '''import uvm_pkg::*;
class item extends uvm_sequence_item; endclass
class driver extends uvm_driver #(item);
  function void use_members();
''' + "    req = req;\n" * 6000 + "  endfunction\nendclass\n"
    ws = index(tmp_path, {"t.sv": text})

    async def run():
        ls = UvmLanguageServer()
        ls.ws, ls.nav = ws, Navigator(ws)
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        params = T.InlayHintParams(T.TextDocumentIdentifier((tmp_path / "t.sv").as_uri()),
                                  T.Range(T.Position(3000, 0), T.Position(3002, 0)))
        with patch.object(ws, "enclosing", wraps=ws.enclosing) as enclosing:
            assert len(await inlay_hint(ls, params)) == 2
            assert {call.args[1] for call in enclosing.call_args_list} <= {3000, 3001, 3002}
            enclosing.reset_mock()
            params.range = T.Range(T.Position(3001, 0), T.Position(3003, 0))
            assert len(await inlay_hint(ls, params)) == 2
            assert {call.args[1] for call in enclosing.call_args_list} <= {3002, 3003}
    asyncio.run(run())


def test_edits_during_cold_hints_discard_old_positions(tmp_path):
    text = '''import uvm_pkg::*;
class item extends uvm_sequence_item; endclass
class driver extends uvm_driver #(item);
  function void use_members(); req = req; endfunction
endclass
'''
    path = tmp_path / "t.sv"
    ws = index(tmp_path, {"t.sv": text})

    async def run():
        ls = UvmLanguageServer()
        ls.ws, ls.nav = ws, Navigator(ws)
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.workspace.put_text_document(T.TextDocumentItem(path.as_uri(), "systemverilog", 1, text))
        ls.after_index = lambda: None
        entered, release = threading.Event(), threading.Event()
        original = ls.nav.bound_member_hints

        def delayed(*args):
            entered.set()
            assert release.wait(2)
            return original(*args)

        params = T.InlayHintParams(T.TextDocumentIdentifier(path.as_uri()),
                                  T.Range(T.Position(0, 0), T.Position(100, 0)))
        with patch.object(ls.nav, "bound_member_hints", side_effect=delayed):
            task = asyncio.create_task(inlay_hint(ls, params))
            try:
                while not entered.is_set():
                    await asyncio.sleep(.001)
                ls.workspace.update_text_document(T.VersionedTextDocumentIdentifier(2, path.as_uri()),
                                                  T.TextDocumentContentChangeWholeDocument("\n\n" + text))
                ls.schedule(str(path))
            finally:
                release.set()
            [hint] = await task
            assert hint.position.line == 5
            assert await inlay_hint(ls, params) == [hint]
    asyncio.run(run())
