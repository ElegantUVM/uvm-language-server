from uvmls.indexer import Indexer


def classes(ws):
    return {c.name: c for c in ws.classes if not c.library}


def test_all_classes_found(any_ws):
    names = set(classes(any_ws))
    assert {"alu_driver", "alu_error_driver", "alu_agent", "alu_env", "alu_base_test",
            "alu_random_seq", "alu_seq_item"} <= names
    assert len(names) == 19


def test_packages_and_registration(ws):
    c = classes(ws)
    assert c["alu_driver"].package == "alu_pkg"
    assert c["alu_env"].package == "alu_env_pkg"
    assert c["alu_driver"].registration == "component"
    assert c["alu_seq_item"].registration == "object"
    assert c["alu_base_seq"].registration is None
    assert c["alu_base_seq"].is_virtual


def test_factory_creates_with_conditions(ws):
    agent = classes(ws)["alu_agent"]
    creates = {cr.target: cr for cr in ws.calls_of(agent).creates}
    assert creates["m_monitor"].type.name == "alu_monitor"
    assert not creates["m_monitor"].conditional
    assert creates["m_driver"].conditional
    assert "is_active" in creates["m_driver"].condition


def test_config_db_calls(ws):
    env = classes(ws)["alu_env"]
    calls = {(c.op, c.field_name): c for c in ws.calls_of(env).config_db}
    s = calls[("set", "alu_agent_config")]
    assert s.inst == "m_agent*" and s.type_text == "alu_agent_config"
    assert ("get", "alu_env_config") in calls


def test_overrides(ws):
    t = classes(ws)["alu_error_test"]
    (ov,) = ws.calls_of(t).overrides
    assert (ov.kind, ov.original, ov.override) == ("type", "alu_driver", "alu_error_driver")
    t = classes(ws)["alu_add_test"]
    (ov,) = ws.calls_of(t).overrides
    assert (ov.original, ov.override) == ("alu_random_seq", "alu_add_only_seq")


def test_out_of_block_methods_attach_to_class(ws):
    drv = classes(ws)["alu_driver"]
    assert ("alu_pkg::alu_driver", "run_phase") in ws.impls
    proto = next(m for m in drv.methods if m.name == "run_phase")
    assert proto.is_prototype


def test_macro_shim_keeps_if_bodies_intact(ws):
    # Without the shim, `if (...) `uvm_fatal(...)` swallows the following statement.
    agent = classes(ws)["alu_agent"]
    creates = {cr.target: cr for cr in ws.calls_of(agent).creates}
    assert not creates["m_monitor"].conditional


def test_unsaved_buffer_override(tmp_path):
    f = tmp_path / "a.sv"
    f.write_text("class a; endclass\n")
    ix = Indexer([], {}, use_shim=True)
    idx = ix.parse(str(f), {str(f): "class b extends a; endclass\n"})
    assert [c.name for c in idx.classes] == ["b"]


def test_syntax_errors_are_reported(tmp_path):
    f = tmp_path / "bad.sv"
    f.write_text("class c;\n  int x = ;\nendclass\n")
    idx = Indexer([], {}, use_shim=True).parse(str(f))
    assert any(d.severity == "error" and d.range.line == 1 for d in idx.diagnostics)
