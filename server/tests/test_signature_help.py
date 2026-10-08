"""Parameter hints use the current index and buffer over stdio."""

import os
import shutil

import pytest

from conftest import EXAMPLE
from test_code_actions import Doc, start
from test_lsp import Client

CAPS = {"textDocument": {"signatureHelp": {}}}


@pytest.fixture(scope="module")
def tb(tmp_path_factory):
    root = tmp_path_factory.mktemp("signatures") / "alu_tb"
    shutil.copytree(EXAMPLE, root)
    return str(root)


@pytest.fixture(scope="module")
def client(tb):
    c = start(tb, capabilities=CAPS)
    yield c
    c.close()


@pytest.fixture
def doc(client, tb):
    d = Doc(client, os.path.join(tb, "agent", "alu_driver.svh"))
    yield d
    d.close()


def help_at(doc, needle, *, context=None, encoding="utf-16-le"):
    offset = doc.text.index(needle) + len(needle)
    prefix = doc.text[:offset]
    line = prefix.count("\n")
    column = len(prefix.rsplit("\n", 1)[-1].encode(encoding)) // (2 if encoding == "utf-16-le" else 1)
    params = {"textDocument": {"uri": doc.uri}, "position": {"line": line, "character": column}}
    if context is not None:
        params["context"] = context
    return doc.client.request("textDocument/signatureHelp", params)


def signature(result, formals, active=0, label=None):
    assert result and result["activeSignature"] == 0
    [sig] = result["signatures"]
    assert [p["label"] for p in sig["parameters"]] == formals
    if formals:
        assert result["activeParameter"] == active
    else:
        assert "activeParameter" not in result
    assert "activeParameter" not in sig
    if label:
        assert sig["label"] == label
    return sig


@pytest.mark.parametrize("caps,present", [({}, False), (CAPS, True),
    ({"textDocument": {"signatureHelp": {"dynamicRegistration": False}}}, True),
    ({"textDocument": {"formatting": {}, "rangeFormatting": {}, "signatureHelp": {},
                       "documentHighlight": {}, "foldingRange": {}}}, True)])
def test_registration(tb, caps, present):
    c = Client()
    try:
        result = c.request("initialize", {"processId": None, "rootUri": f"file://{tb}", "capabilities": caps})
        provider = result["capabilities"].get("signatureHelpProvider")
        assert provider == ({"triggerCharacters": ["(", ","], "retriggerCharacters": [")"]} if present else None)
    finally:
        c.close()


def test_extern_and_super(doc):
    signature(help_at(doc, "drive_item(r"), ["alu_seq_item item"], label="task drive_item(alu_seq_item item)")
    signature(help_at(doc, "super.new(n"), ["string name", "uvm_component parent"], 0)
    signature(help_at(doc, "super.new(name,"), ["string name", "uvm_component parent"], 1)


SOURCE = '''class base #(type T = int);
  function new(string name, int count = 2); endfunction
  function void f(input T a, b, output bit [3:0] bits[], const ref string text = "T,a"); endfunction
endclass
class child extends base #(byte);
  typedef base #(string) strings;
  strings obj;
  extern function void public_api(int count = 7);
  function void old_style;
    input int a;
    output byte b;
  endfunction
  function void zero(); endfunction
  function int inner(int a, int b); return a; endfunction
  function void test();
    base #(bit) local_obj;
    CALL
  endfunction
endclass
function void child::public_api(int count); endfunction
'''


@pytest.mark.parametrize("call,needle,formals,active", [
    ("f(1, 2);", "f(1,", ["input byte a", "input byte b", "output bit[3:0] bits[]", 'const ref string text = "T,a"'], 1),
    ("this.f(1);", "this.f(", ["input byte a", "input byte b", "output bit[3:0] bits[]", 'const ref string text = "T,a"'], 0),
    ("obj.f(1);", "obj.f(", ["input string a", "input string b", "output bit[3:0] bits[]", 'const ref string text = "T,a"'], 0),
    ("local_obj.f(1);", "local_obj.f(", ["input bit a", "input bit b", "output bit[3:0] bits[]", 'const ref string text = "T,a"'], 0),
    ("public_api(1, 2, 3);", "public_api(1, 2,", ["int count = 7"], 0),
    ("old_style(1, 2);", "old_style(1,", ["input int a", "output byte b"], 1),
    ("zero();", "    zero(", [], None),
    ("obj = new(\"x\", 3);", 'new("x",', ["string name", "int count = 2"], 1),
    ("base #(bit) x = new(\"x\");", 'new("x"', ["string name", "int count = 2"], 0),
    ("local_obj = base #(bit)::new(\"x\");", '::new("x"', ["string name", "int count = 2"], 0),
    ("f(inner(1, 2), 3);", "inner(1,", ["int a", "int b"], 1),
    ("f(inner(1, 2), 3);", "inner(1, 2)", ["input byte a", "input byte b", "output bit[3:0] bits[]", 'const ref string text = "T,a"'], 0),
    ('f({1, 2}, /* , */ 3, \'{1, 2}, "a,\\\"b");', '/* , */ 3,', ["input byte a", "input byte b", "output bit[3:0] bits[]", 'const ref string text = "T,a"'], 2),
    ("public_api(", "    public_api(", ["int count = 7"], 0),
    ("public_api(1,", "    public_api(1,", ["int count = 7"], 0),
    ("public_api(\n 1,\n", "public_api(\n 1,\n", ["int count = 7"], 0),
])
def test_methods_and_constructors(doc, call, needle, formals, active):
    doc.change(SOURCE.replace("CALL", call))
    signature(help_at(doc, needle), formals, active)


@pytest.mark.parametrize("call,needle", [
    ("unknown.public_api(1);", "unknown.public_api("),
    ("unknown = new(1);", "new("),
    ("child c = new(1);", "new("),  # constructors do not inherit
    ("obj = new[3];", "new["),
    ("obj = new local_obj;", "new "),
    ("public_api(1);", "public_api(1)"),
    ('string s = "public_api(1)";', '"public_api('),
    ("// public_api(1)\n", "// public_api("),
    ("`ifdef NEVER_DEFINED\npublic_api(1);\n`endif", "public_api("),
    ("`define FAKE public_api(1)\n", "public_api("),
    ("if (1) begin end", "if ("),
])
def test_no_guess(doc, call, needle):
    doc.change(SOURCE.replace("CALL", call))
    assert help_at(doc, needle) is None


def test_declaration_is_not_a_call(doc):
    doc.change(SOURCE.replace("CALL", ""))
    assert help_at(doc, "function void public_api(") is None


def test_context_and_current_declaration(client, tb, doc):
    context = {"triggerKind": 2, "triggerCharacter": ",", "isRetrigger": True,
               "activeSignatureHelp": {"signatures": [{"label": "stale"}], "activeParameter": 8}}
    signature(help_at(doc, "drive_item(r", context=context), ["alu_seq_item item"])
    doc.change(doc.text.replace("drive_item(alu_seq_item item)", "drive_item(alu_seq_item changed, int n = 5)"))
    signature(help_at(doc, "drive_item(r"), ["alu_seq_item changed", "int n = 5"])


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8"])
def test_unicode_crlf_and_declaration_edits(tb, encoding):
    caps = {**CAPS, "general": {"positionEncodings": [encoding]}}
    c = start(tb, capabilities=caps)
    try:
        doc = Doc(c, os.path.join(tb, "agent", "alu_driver.svh"))
        doc.change(SOURCE.replace("CALL", '/*😀é*/ public_api(1);').replace("\n", "\r\n").rstrip())
        signature(help_at(doc, '/*😀é*/ public_api(', encoding="utf-8" if encoding == "utf-8" else "utf-16-le"),
                  ["int count = 7"])
        doc.close()
        signature(help_at(Doc(c, doc.path), "drive_item(r"), ["alu_seq_item item"])
    finally:
        c.close()


def test_package_scopes_and_ambiguous_types(client, tb, doc):
    pkg = Doc(client, os.path.join(tb, "agent", "alu_pkg.sv"))
    pkg.change('''package first;
class twin; static function void go(int first_arg); endfunction endclass
endpackage
package second;
class twin; static function void go(string second_arg); endfunction endclass
endpackage
package calls;
`include "alu_driver.svh"
endpackage
''')
    try:
        doc.change('''class caller;
function void test();
first::twin::go(1);
second::twin::go("x");
twin::go(1);
endfunction
endclass
''')
        signature(help_at(doc, "first::twin::go("), ["int first_arg"])
        signature(help_at(doc, "second::twin::go("), ["string second_arg"])
        assert help_at(doc, "\ntwin::go(") is None
        pkg.change(pkg.text.replace("int first_arg", "byte changed"))
        signature(help_at(doc, "first::twin::go("), ["byte changed"])
    finally:
        pkg.close()


def test_cold_request_yields_and_discards_intervening_edits(tmp_path):
    import asyncio
    import threading
    from unittest.mock import patch

    from lsprotocol import types as T
    from test_fixes import index
    from uvmls import signatures
    from uvmls.documents import Workspace as LspWorkspace
    from uvmls.navigation import Navigator
    from uvmls.server import UvmLanguageServer, signature_help

    text = SOURCE.replace('CALL', 'public_api(1);')
    ws = index(tmp_path, {'t.sv': text})
    path = tmp_path / 't.sv'

    async def run():
        ls = UvmLanguageServer()
        ls.ws, ls.nav = ws, Navigator(ws)
        ls.protocol._workspace = LspWorkspace(tmp_path.as_uri())
        ls.protocol.client_capabilities = T.ClientCapabilities()
        ls.workspace.put_text_document(T.TextDocumentItem(path.as_uri(), 'systemverilog', 1, text))
        entered, release = threading.Event(), threading.Event()
        original = signatures.signature

        def delayed(*args):
            entered.set()
            assert release.wait(2)
            return original(*args)

        line = next(i for i, ln in enumerate(text.splitlines()) if '    public_api(' in ln)
        params = T.SignatureHelpParams(T.TextDocumentIdentifier(path.as_uri()), T.Position(line, 15))
        with patch.object(signatures, 'signature', side_effect=delayed):
            task = asyncio.create_task(signature_help(ls, params))
            try:
                while not entered.is_set():
                    if task.done():
                        await task
                    await asyncio.sleep(.001)
                ls.workspace.update_text_document(T.VersionedTextDocumentIdentifier(2, path.as_uri()),
                                                  T.TextDocumentContentChangeWholeDocument('\n' + text))
            finally:
                release.set()
            assert await task is None
        from unittest.mock import AsyncMock
        with patch.object(ls, 'fresh', new=AsyncMock(return_value=False)):
            assert await signature_help(ls, params) is None
    asyncio.run(run())


def test_shared_argument_spans():
    from uvmls.fixes import call_args
    from uvmls.indexer import argument_spans, mask

    text = 'f(, {1, 2}, "a,\\\"b", g(1,2),)'
    masked = mask(text)
    expected = [(2, 2), (3, 10), (11, 19), (20, 27), (28, 28)]
    assert argument_spans(masked, 2, len(text) - 1) == expected
    assert call_args(masked) == (1, expected)


@pytest.mark.parametrize('markdown,offsets,active_support', [(True, True, True), (False, False, False),
                                                         (True, False, False), (False, True, True)])
def test_factory_and_inherited_documentation(tb, bundled_ws, markdown, offsets, active_support):
    from uvmls import naturaldocs
    from uvmls.navigation import Navigator

    caps = {'textDocument': {'signatureHelp': {'signatureInformation': {
        'documentationFormat': ['markdown'] if markdown else ['plaintext'],
        'parameterInformation': {'labelOffsetSupport': offsets}, 'activeParameterSupport': active_support}}}}
    c = start(tb, capabilities=caps)
    render = naturaldocs.markdown if markdown else naturaldocs.plain
    try:
        cases = [
            ('agent/alu_agent.svh', 'alu_driver::type_id::create("m_driver",', 'alu_driver',
             'uvm_component_registry', ['string name', 'uvm_component parent', 'string contxt = ""'], 1),
            ('seq/alu_sequences.svh', 'alu_seq_item::type_id::create(', 'alu_seq_item',
             'uvm_object_registry', ['string name = ""', 'uvm_component parent = null', 'string contxt = ""'], 0),
        ]
        for path, needle, typename, registry, formals, active in cases:
            d = Doc(c, os.path.join(tb, path))
            result = help_at(d, needle)
            assert result and result['activeParameter'] == active and result['activeSignature'] == 0
            [sig] = result['signatures']
            assert sig['label'] == 'function ' + typename + ' create(' + ', '.join(formals) + ')'
            assert formal_text(sig) == formals
            assert all(isinstance(p['label'], list) == offsets for p in sig['parameters'])
            assert sig.get('activeParameter') == (active if active_support else None)
            assert ('activeParameter' in sig) == active_support
            cls = bundled_ws.find_class(registry)
            method = next(m for m in cls.methods if m.name == 'create')
            assert sig['documentation'] == {'kind': 'markdown' if markdown else 'plaintext', 'value': render(method.doc)}
            d.close()
        d = Doc(c, os.path.join(tb, 'agent/alu_driver.svh'))
        result = help_at(d, 'seq_item_port.get_next_item(')
        [sig] = result['signatures']
        assert sig['label'] == 'task get_next_item(output alu_seq_item t)'
        assert formal_text(sig) == ['output alu_seq_item t']
        cls = bundled_ws.find_class('uvm_seq_item_pull_port')
        method = next(m for m in cls.methods if m.name == 'get_next_item')
        raw, source = Navigator(bundled_ws).method_doc(method, cls)
        attribution = f'doc from `{source.name}::get_next_item`' if markdown else f'doc from {source.name}::get_next_item'
        assert sig['documentation']['value'] == attribution + '\n\n' + render(raw)
        assert 'Retrieves the next available item' in sig['documentation']['value']
    finally:
        c.close()


def formal_text(sig):
    encoded = sig['label'].encode('utf-16-le')
    return [p['label'] if isinstance(p['label'], str) else encoded[p['label'][0] * 2:p['label'][1] * 2].decode('utf-16-le')
            for p in sig['parameters']]


def test_user_docs_and_unicode_label_offsets(tb):
    from uvmls import naturaldocs

    caps = {'general': {'positionEncodings': ['utf-8']}, 'textDocument': {'signatureHelp': {
        'signatureInformation': {'documentationFormat': ['markdown'],
                                 'parameterInformation': {'labelOffsetSupport': True}}}}}
    c = start(tb, capabilities=caps)
    try:
        d = Doc(c, os.path.join(tb, 'agent/alu_driver.svh'))
        d.change('''class base;
  // Function: new
  // Only the base constructor owns this prose.
  function new(); endfunction
  // Function: f
  // Read ~text~ and <other>.
  //
  // - one
  // - two
  //
  // | f("example");
  extern function void f(string text = "😀,T", int second = 2);
endclass
class child extends base;
 function new(); endfunction
 function void test(); f("x", 2); child::new(); endfunction
endclass
function void base::f(string text, int second); endfunction
''')
        result = help_at(d, ' f("x",', encoding='utf-8')
        [sig] = result['signatures']
        assert formal_text(sig) == ['string text = "😀,T"', 'int second = 2']
        assert sig['parameters'][1]['label'][0] == len('function void f(string text = "😀,T", '.encode('utf-16-le')) // 2
        assert sig['documentation']['value'] == naturaldocs.markdown('Read ~text~ and <other>.\n\n- one\n- two\n\n| f("example");')
        ctor = help_at(d, 'child::new(', encoding='utf-8')['signatures'][0]
        assert 'documentation' not in ctor
    finally:
        c.close()


def test_builtin_library_does_not_manufacture_signatures(tb):
    c = start(tb, capabilities=CAPS, settings={'filelists': [os.path.join(tb, 'alu_tb.f')],
                                            'elaboration': 'off', 'useBundledUvm': False})
    try:
        d = Doc(c, os.path.join(tb, 'agent/alu_driver.svh'))
        signature(help_at(d, 'drive_item(r'), ['alu_seq_item item'])
        assert help_at(d, 'seq_item_port.get_next_item(') is None
        d.close()
        d = Doc(c, os.path.join(tb, 'agent/alu_agent.svh'))
        assert help_at(d, 'alu_driver::type_id::create(') is None
    finally:
        c.close()


def test_factory_aliases_and_parameterization(doc):
    doc.change('''class item #(type V = bit) extends uvm_object;
 `uvm_object_param_utils(item #(V))
 function new(string name = "ctor"); super.new(name); endfunction
endclass
class unregistered extends uvm_object; endclass
class caller;
 typedef item #(byte) alias_t;
 function void test();
  item #(byte)::type_id::create("x");
  alias_t::type_id::create("x");
  unregistered::type_id::create("x");
 endfunction
endclass
''')
    for needle in ('item #(byte)::type_id::create(', 'alias_t::type_id::create('):
        sig = signature(help_at(doc, needle), ['string name = ""', 'uvm_component parent = null', 'string contxt = ""'])
        assert sig['label'].startswith('function item#(byte) create(')
        assert 'ctor' not in sig['label']
    assert help_at(doc, 'unregistered::type_id::create(') is None


def test_selected_library_supplies_registry_api(tmp_path):
    src = tmp_path / 'uvm' / 'src'
    src.mkdir(parents=True)
    (src / 'uvm_macros.svh').write_text('')
    (src / 'uvm_pkg.sv').write_text('''package uvm_pkg;
class uvm_component; endclass
class uvm_object; endclass
class registry #(type T = uvm_object);
 // Function: create
 // Selected library API.
 static function T create(string name = "selected,😀", int mode = 4); endfunction
endclass
endpackage
''')
    root = tmp_path / 'tb'
    root.mkdir()
    path = root / 't.sv'
    path.write_text('''import uvm_pkg::*;
class item;
 typedef registry #(item) type_id;
endclass
class caller;
 function void test(); item::type_id::create("x", 2); endfunction
endclass
''')
    c = start(str(root), capabilities=CAPS, settings={'uvmHome': str(src.parent), 'elaboration': 'off'})
    try:
        d = Doc(c, str(path))
        sig = signature(help_at(d, 'item::type_id::create("x",'), ['string name = "selected,😀"', 'int mode = 4'], 1)
        assert sig['label'].startswith('function item create(')
        assert sig['documentation'] == {'kind': 'plaintext', 'value': 'Selected library API.'}
    finally:
        c.close()


def test_factory_specialization_preserves_string_parameters(doc):
    doc.change('''class item #(type V = bit, string TAG = "") extends uvm_object;
 `uvm_object_param_utils(item #(V, TAG))
endclass
class caller;
 function void test();
  alu_pkg::item #(byte, "x,y")::type_id::create("x");
 endfunction
endclass
''')
    sig = signature(help_at(doc, '::create('), ['string name = ""', 'uvm_component parent = null', 'string contxt = ""'])
    assert sig['label'].startswith('function item#(byte,"x,y") create(')


def test_ordinary_registry_methods(doc):
    doc.change('''class caller;
 uvm_component_registry #(alu_driver) comp;
 uvm_object_registry #(alu_seq_item) obj;
 function void test();
  comp.create_component("x", null);
  obj.create_object("x");
 endfunction
endclass
''')
    signature(help_at(doc, 'comp.create_component("x",'), ['string name', 'uvm_component parent'], 1,
              'function uvm_component create_component(string name, uvm_component parent)')
    signature(help_at(doc, 'obj.create_object('), ['string name = ""'], 0,
              'function uvm_object create_object(string name = "")')


@pytest.mark.parametrize('arguments,needle,active', [
    ('.text("x"), .a(1), .bits(\'{1,2}), .b(3)', '.text(', 3),
    ('.text("x"), .a(1), .bits(\'{1,2}), .b(3)', '.a(', 0),
    ('.text("x"), .a(1), .bits(\'{1,2}), .b(3)', '.bits(', 2),
    ('.text("x"), .a(1), .bits(\'{1,2}), .b(3)', '.b(', 1),
    ('.text("x"), .a(inner(1, 2))', 'inner(1, 2)', 0),
    ('.text("x"), .a(inner(1, 2))', 'inner(1,', 'inner'),
    (',,3', '    f(,,', 2),
    (',,', '    f(,,', 2),
    ('.text("a,\\\"b")', '.text("a,', 3),
    ('.bits()', '.bits(', 2),
])
def test_named_and_empty_arguments(doc, arguments, needle, active):
    doc.change(SOURCE.replace('CALL', 'f(' + arguments + ');'))
    if active == 'inner':
        signature(help_at(doc, needle), ['int a', 'int b'], 1)
    else:
        signature(help_at(doc, needle), ['input byte a', 'input byte b', 'output bit[3:0] bits[]',
                                        'const ref string text = "T,a"'], active)


@pytest.mark.parametrize('call,needle', [
    ('f(.unknown(1));', '.unknown('),
    ('f(.te', '.te'),
    ('f(.text', '.text'),
    ('f(.', '    f(.'),
    ('f(.text("x"), .un(2));', '.un('),
])
def test_unknown_and_partial_named_arguments(doc, call, needle):
    doc.change(SOURCE.replace('CALL', call))
    assert help_at(doc, needle) is None


def test_factory_named_arguments_and_incomplete_multiline(client, tb):
    d = Doc(client, os.path.join(tb, 'agent/alu_agent.svh'))
    original = d.text
    formals = ['string name', 'uvm_component parent', 'string contxt = ""']
    try:
        d.change(original.replace('create("m_driver", this)', 'create(.parent(this), .name("m_driver"))'))
        signature(help_at(d, '.parent(th'), formals, 1)
        signature(help_at(d, '.parent(this)'), formals, 1)
        signature(help_at(d, '.name("m_'), formals, 0)
        d.change(original.replace('create("m_driver", this)', 'create(\n $sformatf("m,%0d", 1),\n this,\n'))
        signature(help_at(d, '$sformatf("m,%0d", 1),'), formals, 1)
        signature(help_at(d, '\n this,\n'), formals, 2)
        d.change(original.replace('create("m_driver", this)', 'create(.contxt('))
        signature(help_at(d, '.contxt('), formals, 2)
    finally:
        d.close()


def test_named_declaration_change_in_another_buffer(client, tb):
    caller = Doc(client, os.path.join(tb, 'agent/alu_agent.svh'))
    declaration = Doc(client, os.path.join(tb, 'agent/alu_driver.svh'))
    try:
        caller.change(caller.text.replace('m_driver    = alu_driver::type_id::create("m_driver", this);',
                                         'm_driver.drive_item(.item(null));'))
        signature(help_at(caller, '.item('), ['alu_seq_item item'])
        declaration.change(declaration.text.replace('drive_item(alu_seq_item item)', 'drive_item(alu_seq_item changed)'))
        assert help_at(caller, '.item(') is None
        declaration.close()
        signature(help_at(caller, '.item('), ['alu_seq_item item'])
    finally:
        caller.close()


def test_ambiguous_typedef_receiver_and_imported_function(client, tb, doc):
    pkg = Doc(client, os.path.join(tb, 'agent', 'alu_pkg.sv'))
    pkg.change('''package first;
class box; function void go(int a); endfunction endclass
typedef box alias_t;
function void shared(int a); endfunction
endpackage
package second;
class box; function void go(string b); endfunction endclass
typedef box alias_t;
function void shared(string b); endfunction
endpackage
package calls;
import first::*;
import second::*;
`include "alu_driver.svh"
endpackage
''')
    try:
        doc.change('''class caller;
 alias_t value;
 function void test(); value.go(1); shared(1); endfunction
endclass
''')
        assert help_at(doc, 'value.go(') is None
        assert help_at(doc, 'shared(') is None
    finally:
        pkg.close()


def test_property_initializer_and_named_constructor(doc):
    doc.change('''class built;
 function new(string name = "x", int count = 2); endfunction
endclass
class caller;
 built prop = new(.count(3), .name("p"));
endclass
''')
    signature(help_at(doc, '.count('), ['string name = "x"', 'int count = 2'], 1)
    signature(help_at(doc, '.name('), ['string name = "x"', 'int count = 2'], 0)


def test_typed_direct_new_in_alu_extern_body(doc):
    doc.change(doc.text.replace('task alu_driver::run_phase(uvm_phase phase);',
                               'task alu_driver::run_phase(uvm_phase phase);\n'
                               '  alu_driver d;\n  d = new("d", this);'))
    signature(help_at(doc, 'd = new("d",'), ['string name', 'uvm_component parent'], 1,
              'function new(string name, uvm_component parent)')


@pytest.mark.parametrize('call,needle', [('public_api("typing', '    public_api("typing'),
                                        ('public_api(', '    public_api(')])
def test_unfinished_call_keeps_enclosing_context(doc, call, needle):
    doc.change(SOURCE.replace('CALL', call))
    signature(help_at(doc, needle), ['int count = 7'])


def test_public_typedef_is_not_ambiguous_with_another_class_member(doc):
    text = SOURCE.replace('class child extends base #(byte);', '''typedef base #(string) public_t;
class unrelated;
 typedef base #(int) public_t;
endclass
class child extends base #(byte);''')
    doc.change(text.replace('CALL', 'public_t::f("x");'))
    signature(help_at(doc, 'public_t::f('), ['input string a', 'input string b', 'output bit[3:0] bits[]',
                                           'const ref string text = "T,a"'])


@pytest.mark.parametrize('factory', [False, True])
def test_ambiguous_class_local_typedef(client, tb, doc, factory):
    pkg = Doc(client, os.path.join(tb, 'agent', 'alu_pkg.sv'))
    try:
        pkg.change('''`include "uvm_macros.svh"
package first;
import uvm_pkg::*;
class box extends uvm_object;
 `uvm_object_utils(box)
 static function void go(int first_arg); endfunction
endclass
endpackage
package second;
import uvm_pkg::*;
class box extends uvm_component;
 `uvm_component_utils(box)
 static function void go(string second_arg); endfunction
endclass
endpackage
package calls;
import first::*;
import second::*;
`include "alu_driver.svh"
endpackage
''')
        call = 'alias_t::type_id::create("x");' if factory else 'alias_t::go(1);'
        doc.change('''class caller;
 typedef box alias_t;
 function void test(); CALL endfunction
endclass
'''.replace('CALL', call))
        needle = call.split('(')[0] + '('
        assert help_at(doc, needle) is None
        doc.change(doc.text.replace('typedef box', 'typedef first::box'))
        formals = ['string name = ""', 'uvm_component parent = null', 'string contxt = ""'] if factory else \
            ['int first_arg']
        signature(help_at(doc, needle), formals)
        pkg.change(pkg.text.replace('import second::*;', ''))
        doc.change(doc.text.replace('typedef first::box', 'typedef box'))
        signature(help_at(doc, needle), formals)
    finally:
        pkg.close()


@pytest.mark.parametrize('receiver', ['hidden', 'alias_t', 'uvm_hidden'])
def test_unimported_type_is_not_visible(client, tb, doc, receiver):
    pkg = Doc(client, os.path.join(tb, 'agent', 'alu_pkg.sv'))
    try:
        pkg.change('''package first;
class hidden;
 static function void go(int secret_arg); endfunction
endclass
typedef hidden alias_t;
class uvm_hidden;
 static function void go(int secret_arg); endfunction
endclass
endpackage
package calls;
`include "alu_driver.svh"
endpackage
''')
        doc.change('''class caller;
 function void test(); RECEIVER::go(1); endfunction
endclass
'''.replace('RECEIVER', receiver))
        assert help_at(doc, receiver + '::go(') is None
        doc.change(doc.text.replace(receiver + '::go', 'first::' + receiver + '::go'))
        signature(help_at(doc, 'first::' + receiver + '::go('), ['int secret_arg'])
        doc.change(doc.text.replace('first::' + receiver + '::go', receiver + '::go'))
        pkg.change(pkg.text.replace('package calls;', 'package calls;\nimport first::*;'))
        signature(help_at(doc, receiver + '::go('), ['int secret_arg'])
    finally:
        pkg.close()
