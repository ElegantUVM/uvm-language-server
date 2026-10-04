# UVM Language Server

UVM-aware SystemVerilog support for VS Code, built on the [slang](https://github.com/MikePopoloski/slang) compiler front end.

- **One-click class graph.** Click the graph button in the editor title bar (or press `Ctrl+Alt+G`). You get inheritance, composition, factory creates and overrides, with UVM roles (driver, monitor, agent, …) recognised automatically.
- **Component topology.** The `uvm_test_top` tree for any test, built from `type_id::create()` calls. Factory overrides are applied, conditional components are marked, and TLM `connect()` calls are drawn.
- **config_db cross-reference.** Every `set` and `get`, matched by field and type. A `get` that nothing sets, or a set/get type mismatch, is flagged in the editor.
- **Navigation that understands UVM.** Go to definition, hover, completion, references, type hierarchy and document/workspace symbols. Navigation follows type parameters (`req` in a driver resolves to your item class), virtual interfaces, clocking blocks and `extern` methods.
- **UVM lint.** Unregistered classes created through the factory, `uvm_object_utils` on a component, virtual classes passed to `create()`, and config_db mismatches.

Point `uvm.filelists` at the same `.f` files your simulator uses, or leave it empty and every SystemVerilog file in the workspace is indexed with include paths inferred.
Set `uvm.uvmHome` (or `$UVM_HOME`) to index the real UVM library; otherwise a built-in UVM model is used.

Base syntax highlighting comes from a SystemVerilog syntax extension such as *Verilog-HDL/SystemVerilog* (`mshr-h.veriloghdl`); this extension adds UVM-specific highlighting on top rather than replacing it.
