"""Parameter substitution is shared by navigation and inherited member hints."""

import pytest

from test_fixes import index
from uvmls.navigation import Navigator
from uvmls.workspace import bind_params


@pytest.mark.parametrize("params,args,outer,expected", [
    ([("T", "int")], ["box #(U)"], {"U": "item"}, {"T": "box #(item)"}),
    ([("T", "int")], [".T(box #(.U(U)))"], {"U": "item"}, {"T": "box #(.U(item))"}),
    ([("T", "int")], ["box #(pkg::U, U)"], {"U": "item"}, {"T": "box #(pkg::U, item)"}),
    ([("T", "int")], ['box #(U, "U")'], {"U": "item"}, {"T": 'box #(item, "U")'}),
    ([("T", "int")], ["logic [N-1:0]"], {"N": "8"}, {"T": "logic [8-1:0]"}),
    ([("T", "int"), ("R", "box #(T)")], ["item"], {"T": "outer"},
     {"T": "item", "R": "box #(item)"}),
    ([("T", "int")], ["box #(U, T)"], {"U": "T", "T": "item"}, {"T": "box #(T, item)"}),
])
def test_bind_compound_parameters(params, args, outer, expected):
    assert bind_params(params, args, outer) == expected


def test_navigation_uses_the_same_compound_binding(tmp_path):
    text = '''class item; endclass
class box #(type V = int); endclass
class base #(type T = int); T value; endclass
class mid #(type U = int) extends base #(box #(U)); endclass
class leaf extends mid #(item);
  function void f(); value = null; endfunction
endclass
'''
    ws = index(tmp_path, {"t.sv": text})
    path = str(tmp_path / "t.sv")
    nav = Navigator(ws)
    [target] = nav.resolve(path, 5, 22, text)
    spec = nav.type_of(target)
    assert spec.name == "box" and spec.args == ["item"]
    [(_, bound, _)] = nav.bound_member_hints(path)
    assert bound == "box #(item)"
