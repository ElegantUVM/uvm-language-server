"""The code-action barrier covers indexing and publication without blocking asyncio."""

import asyncio
import threading
import time

from lsprotocol import types as T
from pygls.workspace import Workspace as LspWorkspace

from test_fixes import WAIVE, M, findings, fixes_ignore, index
from uvmls.server import UvmLanguageServer, code_action


def test_full_index_wait_has_a_deadline(tmp_path):
    async def run():
        ls = UvmLanguageServer()
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.protocol.client_capabilities = T.ClientCapabilities()
        ls.settings = {'elaboration': 'off'}
        entered = threading.Event()

        def configure(config):
            with ls.ws.lock:
                entered.set()
                time.sleep(.25)
        ls.ws.configure = configure
        ls.after_index = lambda: None
        ls.outdate_elaboration = lambda: None
        original_fresh = ls.fresh

        async def fresh():
            return await original_fresh(timeout=.04)
        ls.fresh = fresh
        task = asyncio.create_task(ls.full_index())
        while not entered.is_set():
            await asyncio.sleep(.001)
        rng = T.Range(T.Position(0, 0), T.Position(0, 1))
        params = T.CodeActionParams(T.TextDocumentIdentifier((tmp_path / 't.sv').as_uri()), rng,
                                    T.CodeActionContext([T.Diagnostic(rng, 'x', source='uvm', code='x')]))
        start = time.monotonic()
        try:
            assert await code_action(ls, params) is None
            assert time.monotonic() - start < .15
            assert not task.done()  # timing out the request must not cancel the index
        finally:
            await task
    asyncio.run(run())


def test_ignore_rechecks_the_indexed_line(tmp_path):
    text = WAIVE.replace('LINE', '    ' + M)
    ws = index(tmp_path, {'t.sv': text})
    [d, *_] = findings(ws, 'create-no-parent')
    assert fixes_ignore(ws, d)
    # No watcher event yet: the index and diagnostic still describe the old disk text.
    (tmp_path / 't.sv').write_text('// inserted\n\n' + text)
    assert fixes_ignore(ws, d) == []
