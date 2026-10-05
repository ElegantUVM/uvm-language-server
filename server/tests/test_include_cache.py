"""Include candidates are shared across requests and invalidated by filesystem changes."""

import asyncio
from unittest.mock import patch

import pytest
from lsprotocol import types as T
from pygls.workspace import Workspace as LspWorkspace

from test_fixes import index, offered, slang_findings
from uvmls import fixes
from uvmls.server import UvmLanguageServer, did_change_watched


def test_include_candidates_reuse_the_walk(tmp_path):
    ws = index(tmp_path, {'inc/sub/hdr.svh': '// header\n', 't.sv': '`include "sub/hdr.svh"\n'})
    [d] = slang_findings(ws, 'CouldNotOpenIncludeFile')
    with patch.object(fixes, 'discover_files', wraps=fixes.discover_files) as walk:
        first = offered(ws, d, commands=frozenset({'uvm.addIncludeDir'}))
        assert first and offered(ws, d, commands=frozenset({'uvm.addIncludeDir'})) == first
        assert walk.call_count == 1
        ws.update_document(str(tmp_path / 't.sv'), '// edited\n`include "sub/hdr.svh"\n')
        assert offered(ws, d, commands=frozenset({'uvm.addIncludeDir'})) == first
        assert walk.call_count == 1
        ws.config.exclude.append('inc/**')
        assert offered(ws, d, commands=frozenset({'uvm.addIncludeDir'})) == []
        assert walk.call_count == 2


def test_include_cache_tracks_created_deleted_and_full_index(tmp_path):
    async def run():
        ws = index(tmp_path, {'inc/sub/hdr.svh': '// header\n', 't.sv': '`include "sub/hdr.svh"\n'})
        [d] = slang_findings(ws, 'CouldNotOpenIncludeFile')
        ls = UvmLanguageServer()
        ls.ws = ws
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.workspace_roots = [str(tmp_path)]
        ls.settings = {'elaboration': 'off', 'useBundledUvm': False}
        ls.after_index = lambda: None
        ls.outdate_elaboration = lambda: None

        def candidates():
            return [f.command[1][0]['value'] for f in offered(ws, d, commands=frozenset({'uvm.addIncludeDir'}))]
        assert candidates() == ['inc']
        other = tmp_path / 'inc2' / 'sub' / 'hdr.svh'
        other.parent.mkdir(parents=True)
        other.write_text('// another\n')
        await did_change_watched(ls, T.DidChangeWatchedFilesParams([T.FileEvent(other.as_uri(), T.FileChangeType.Created)]))
        assert candidates() == ['inc', 'inc2']
        other.unlink()
        await did_change_watched(ls, T.DidChangeWatchedFilesParams([T.FileEvent(other.as_uri(), T.FileChangeType.Deleted)]))
        assert candidates() == ['inc']
        other.write_text('// again\n')
        await ls.full_index()
        assert candidates() == ['inc', 'inc2']
    asyncio.run(run())


@pytest.mark.parametrize('suffix', ['h', 'inc'])
@pytest.mark.parametrize('move', [False, True])
def test_include_cache_rechecks_unwatched_headers(tmp_path, suffix, move):
    name = f'defs.{suffix}'
    text = f'`include "{name}"\n'
    ws = index(tmp_path, {f'a/{name}': '// header\n', 't.sv': text})

    def candidates():
        [d] = slang_findings(ws, 'CouldNotOpenIncludeFile')
        return [f.command[1][0]['value'] for f in offered(ws, d, commands=frozenset({'uvm.addIncludeDir'}))]

    with patch.object(fixes, 'discover_files', wraps=fixes.discover_files) as walk:
        assert candidates() == ['a']
        if move:
            (tmp_path / 'b').mkdir()
            (tmp_path / 'a' / name).rename(tmp_path / 'b' / name)
        else:
            (tmp_path / 'a' / name).unlink()
        ws.update_document(str(tmp_path / 't.sv'), '// edited\n' + text)
        assert candidates() == (['b'] if move else [])
        assert walk.call_count == 2
        assert candidates() == (['b'] if move else [])
        assert walk.call_count == 2  # unchanged requests still reuse the refreshed walk


def test_unwatched_header_move_over_stdio(tmp_path):
    from test_code_actions import Doc, start

    text = '`include "defs.h"\n'
    path = tmp_path / 't.sv'
    path.write_text('// before\n' + text)
    (tmp_path / 'alu_tb.f').write_text('t.sv\n')
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / 'defs.h').write_text('// header\n')
    c = start(str(tmp_path), clientCommands=['uvm.addIncludeDir'], settings={
        'filelists': [str(tmp_path / 'alu_tb.f')], 'elaboration': 'off', 'useBundledUvm': False})
    try:
        doc = Doc(c, str(path))
        doc.change(text)
        [d] = doc.diagnostics(lambda ds: ds and ds[0]['range']['start']['line'] == 0)
        [action] = doc.actions([d])
        assert action['command']['arguments'] == [{'dir': str(tmp_path / 'a'), 'value': 'a'}]
        (tmp_path / 'b').mkdir()
        (tmp_path / 'a' / 'defs.h').rename(tmp_path / 'b' / 'defs.h')
        # The client does not watch .h: only the SV buffer triggers incremental indexing.
        doc.change('// edited\n' + text)
        [d] = doc.diagnostics(lambda ds: ds and ds[0]['range']['start']['line'] == 1)
        [action] = doc.actions([d])
        assert action['command']['arguments'] == [{'dir': str(tmp_path / 'b'), 'value': 'b'}]
        c.notify('workspace/didChangeConfiguration', {'settings': {'uvm': {
            'filelists': [str(tmp_path / 'alu_tb.f')], 'elaboration': 'off',
            'useBundledUvm': False, 'includeDirs': ['b']}}})
        doc.diagnostics(lambda ds: not ds)
    finally:
        c.close()


@pytest.mark.parametrize('suffix', ['h', 'inc'])
@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('empty_dir', [False, True])
def test_include_cache_finds_new_unwatched_headers(tmp_path, suffix, existing, empty_dir):
    name = f'defs.{suffix}'
    text = f'`include "{name}"\n'
    files = {'t.sv': text}
    if existing:
        files[f'a/{name}'] = '// original\n'
    ws = index(tmp_path, files)
    other = tmp_path / 'b' / name
    if empty_dir:
        other.parent.mkdir()

    def candidates():
        [d] = slang_findings(ws, 'CouldNotOpenIncludeFile')
        return [(f.command[1][0]['value'], f.preferred)
                for f in offered(ws, d, commands=frozenset({'uvm.addIncludeDir'}))]

    with patch.object(fixes, 'discover_files', wraps=fixes.discover_files) as walk:
        assert candidates() == ([('a', True)] if existing else [])
        other.parent.mkdir(exist_ok=True)
        other.write_text('// new\n')
        ws.update_document(str(tmp_path / 't.sv'), '// edited\n' + text)
        expected = [('a', False), ('b', False)] if existing else [('b', True)]
        assert candidates() == expected
        assert walk.call_count == 2
        assert candidates() == expected
        assert walk.call_count == 2


@pytest.mark.parametrize('existing', [False, True])
def test_new_unwatched_header_over_stdio(tmp_path, existing):
    from test_code_actions import Doc, start

    text = '`include "defs.h"\n'
    path = tmp_path / 't.sv'
    path.write_text(text)
    (tmp_path / 'alu_tb.f').write_text('t.sv\n')
    if existing:
        (tmp_path / 'a').mkdir()
        (tmp_path / 'a' / 'defs.h').write_text('// original\n')
    (tmp_path / 'b').mkdir()
    c = start(str(tmp_path), clientCommands=['uvm.addIncludeDir'], settings={
        'filelists': [str(tmp_path / 'alu_tb.f')], 'elaboration': 'off', 'useBundledUvm': False})
    try:
        doc = Doc(c, str(path))
        doc.change('// initial\n' + text)
        [d] = doc.diagnostics(lambda ds: ds and ds[0]['range']['start']['line'] == 1)
        before = doc.actions([d])
        assert [a['command']['arguments'][0]['value'] for a in before] == (['a'] if existing else [])
        (tmp_path / 'b' / 'defs.h').write_text('// new\n')
        doc.change('// edited\n// again\n' + text)
        [d] = doc.diagnostics(lambda ds: ds and ds[0]['range']['start']['line'] == 2)
        actions = doc.actions([d])
        assert [a['command']['arguments'][0]['value'] for a in actions] == (['a', 'b'] if existing else ['b'])
        assert all(bool(a.get('isPreferred')) == (not existing) for a in actions)
    finally:
        c.close()
