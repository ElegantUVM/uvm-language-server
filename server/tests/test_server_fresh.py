"""Request barriers cover indexing and publication without blocking asyncio."""

import asyncio
import threading
import time

import pytest
from lsprotocol import types as T
from pygls.workspace import Workspace as LspWorkspace

from test_fixes import WAIVE, M, findings, fixes_ignore, index
from uvmls import elab
from uvmls.server import UvmLanguageServer, code_action, code_lens, definition, hover


@pytest.mark.parametrize("handler", [code_action, code_lens, hover, definition])
def test_full_index_wait_has_a_deadline(tmp_path, handler):
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
        if handler is code_lens:
            params = T.CodeLensParams(params.text_document)
        elif handler is hover:
            params = T.HoverParams(params.text_document, rng.start)
        elif handler is definition:
            params = T.DefinitionParams(params.text_document, rng.start)
        start = time.monotonic()
        try:
            assert not await handler(ls, params)
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


def test_full_index_overlapping_flush_keeps_the_action_deadline(tmp_path):
    async def run():
        ls = UvmLanguageServer()
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.protocol.client_capabilities = T.ClientCapabilities()
        ls.settings = {'elaboration': 'off'}
        updating, release_update, configuring = threading.Event(), threading.Event(), threading.Event()
        ls.protocol.notify = lambda *args: None
        ls.text_document_publish_diagnostics = lambda params: None

        def update(path, text):
            updating.set()
            assert release_update.wait(2)

        def configure(config):
            with ls.ws.lock:
                configuring.set()
                time.sleep(.4)

        ls.ws.update_document = update
        ls.ws.configure = configure
        original_fresh = ls.fresh

        async def fresh():
            return await original_fresh(timeout=.08)
        ls.fresh = fresh
        ls.pending.add(str(tmp_path / 't.sv'))
        ls._start_flush()
        while not updating.is_set():
            await asyncio.sleep(.001)
        full = asyncio.create_task(ls.full_index())
        await asyncio.sleep(.01)
        rng = T.Range(T.Position(0, 0), T.Position(0, 1))
        params = T.CodeActionParams(T.TextDocumentIdentifier((tmp_path / 't.sv').as_uri()), rng,
                                    T.CodeActionContext([T.Diagnostic(rng, 'x', source='uvm', code='x')]))
        start = time.monotonic()
        release_update.set()
        try:
            assert await code_action(ls, params) is None
            assert time.monotonic() - start < .25
            assert configuring.is_set()
            assert not full.done()
        finally:
            await full
            await ls._flush_task
    asyncio.run(run())


@pytest.mark.parametrize('superseded', [False, True])
@pytest.mark.parametrize('started', [False, True])
def test_elaboration_during_index_keeps_the_action_deadline(tmp_path, monkeypatch, superseded, started):
    async def run():
        ls = UvmLanguageServer()
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.protocol.client_capabilities = T.ClientCapabilities()
        ls.settings = {'elaboration': 'off'}
        ls.protocol.notify = lambda *args: None
        published = []
        ls.text_document_publish_diagnostics = published.append
        compiling, finish = asyncio.Event(), asyncio.Event()
        configuring = threading.Event()
        path = tmp_path / 't.sv'

        async def worker(run, request):
            compiling.set()
            await finish.wait()
            return {'diagnostics': [{'file': str(path), 'line': 0, 'col': 0, 'endLine': 0, 'endCol': 1,
                                      'message': 'test error', 'severity': 'error', 'code': 'test', 'related': []}],
                    'counts': {'errors': 1}}

        def configure(config):
            with ls.ws.lock:
                configuring.set()
                time.sleep(.4)

        ls.run_worker = worker
        ls.ws.configure = configure
        monkeypatch.setattr(elab, 'skip_reason', lambda ws: None)
        monkeypatch.setattr(elab, 'request_for', lambda ws, overrides: {'overrides': {}})
        original_fresh = ls.fresh

        async def fresh():
            return await original_fresh(timeout=.08)
        ls.fresh = fresh
        if started:
            compilation = asyncio.create_task(ls._elaborate(ls._elab_run))
            await compiling.wait()
        full = asyncio.create_task(ls.full_index())
        while not configuring.is_set():
            await asyncio.sleep(.001)
        if not started:
            compilation = asyncio.create_task(ls._elaborate(ls._elab_run))
        rng = T.Range(T.Position(0, 0), T.Position(0, 1))
        params = T.CodeActionParams(T.TextDocumentIdentifier(path.as_uri()), rng,
                                    T.CodeActionContext([T.Diagnostic(rng, 'x', source='uvm', code='x')]))
        asyncio.get_running_loop().call_later(.01, finish.set)
        start = time.monotonic()
        try:
            assert await code_action(ls, params) is None
            assert time.monotonic() - start < .25
            assert not full.done() and not compilation.done()
            assert ls.elab_status['state'] == ('running' if started else 'off')
            if superseded:
                ls.stop_elaboration()
        finally:
            await full
            result = await compilation
        if superseded:
            assert result is None and not ls.elab_diags and not published
        else:
            assert result['state'] == 'done' and result['errors'] == 1
            assert ls.elab_diags[str(path)][0].message == 'test error'
            assert published[-1].diagnostics[0].message == 'test error'
    asyncio.run(run())
