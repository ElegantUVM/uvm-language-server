"""Include candidates are shared across requests and invalidated by filesystem changes."""

import asyncio
from unittest.mock import patch

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
