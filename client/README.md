# UVM Language Server

UVM-aware SystemVerilog support for VS Code, built on the [slang](https://github.com/MikePopoloski/slang) compiler front end.

- **One-click class graph.** Click the graph button in the editor title bar (or press `Ctrl+Alt+G`). You get inheritance, composition, factory creates and overrides, with UVM roles (driver, monitor, agent, …) recognised automatically.
- **Component topology.** The `uvm_test_top` tree for any test, built from `type_id::create()` calls. Factory overrides are applied, conditional components are marked, and TLM `connect()` calls are drawn.
- **config_db cross-reference.** Every `set` and `get`, matched by field and type. A `get` that nothing sets, or a set/get type mismatch, is flagged in the editor.
- **Navigation that understands UVM.** Go to definition, hover, completion, references, type hierarchy and document/workspace symbols. Navigation follows type parameters (`req` in a driver resolves to your item class), virtual interfaces, clocking blocks and `extern` methods.
- **UVM lint.** Unregistered classes created through the factory, `uvm_object_utils` on a component, virtual classes passed to `create()`, and config_db mismatches.
- **Elaboration diagnostics.** slang compiles and elaborates the whole design in a background process when you save (`uvm.elaboration`), and reports what a simulator's elaboration would: type errors, unknown members, wrong argument counts, missing modules or packages. Errors raised inside a macro point at your code, not the macro. Errors the UVM library reports on its own are left out, while errors your code causes inside it (a constructor without a parent, a wrong type parameter) are reported, pointing at your type. The status bar shows the result; **UVM: Elaborate Now** runs it on demand.

Point `uvm.filelists` at the same `.f` files your simulator uses, or leave it empty and every SystemVerilog file in the workspace is indexed with include paths inferred.
The UVM library comes from `uvm.uvmHome` when set, else from `$UVM_HOME`, else from the project's own `uvm_pkg.sv` when it compiles one, and otherwise from the Accellera UVM 1.2 that ships with the extension. Turn off `uvm.useBundledUvm` to use a lightweight built-in UVM model instead.

Base syntax highlighting comes from a SystemVerilog syntax extension such as *Verilog-HDL/SystemVerilog* (`mshr-h.veriloghdl`); this extension adds UVM-specific highlighting on top rather than replacing it.

The bundled UVM 1.2 is Accellera's, under the Apache License 2.0. Its `LICENSE.txt` and `NOTICE.txt` are in `server/uvmls/third_party/uvm-1.2/` inside the extension.
The npm packages bundled into the extension's JavaScript are listed with their licence texts in `THIRD_PARTY_NOTICES.txt`: elkjs, under the Eclipse Public License 2.0 (source code at https://github.com/kieler/elkjs), the vscode-languageclient packages (MIT), semver and minimatch (ISC), and brace-expansion and balanced-match (MIT).
