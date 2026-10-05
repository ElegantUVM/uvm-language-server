import pytest

from test_fixes import index, offered, slang_findings


@pytest.mark.parametrize('suffix', ['f', 'vf', 'flist'])
def test_include_fix_never_edits_an_unsynced_filelist(tmp_path, suffix):
    name = 'tb.' + suffix
    files = {'inc/sub/hdr.svh': '// header\n', name: '// header\n+incdir+rtl\nt.sv\n',
             't.sv': '`include "sub/hdr.svh"\nmodule t; endmodule\n'}
    ws = index(tmp_path, files, filelists=[str(tmp_path / name)])
    [d] = slang_findings(ws, 'CouldNotOpenIncludeFile')
    # The editor may hold arbitrary unsaved filelist text: it never syncs that buffer.
    actions = offered(ws, d, commands=frozenset({'uvm.addIncludeDir'}))
    assert actions and all(a.command and not a.changes for a in actions)
    assert offered(ws, d) == []
    # Saving the option before its watcher notification cannot produce a duplicate edit either.
    (tmp_path / name).write_text(files[name] + '+incdir+inc\n')
    assert all(not a.changes for a in offered(ws, d, commands=frozenset({'uvm.addIncludeDir'})))
