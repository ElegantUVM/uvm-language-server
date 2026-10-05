# UVM Language Server

UVM-aware SystemVerilog support for VS Code, built on the [slang](https://github.com/MikePopoloski/slang) compiler front end.

- **One-click class graph.** Click the graph button in the editor title bar (or press `Ctrl+Alt+G`). You get inheritance, composition, factory creates and overrides, with UVM roles (driver, monitor, agent, …) recognised automatically.
- **Component topology.** The `uvm_test_top` tree for any test, built from `type_id::create()` calls. Factory overrides are applied, conditional components are marked, and TLM `connect()` calls are drawn.
- **config_db cross-reference.** Every `set` and `get`, matched by field and type. A `get` that nothing sets, or a set/get type mismatch, is flagged in the editor.
- **Navigation that understands UVM.** Go to definition, hover, completion, references, type hierarchy and document/workspace symbols. Navigation follows type parameters (`req` in a driver resolves to your item class), virtual interfaces, clocking blocks and `extern` methods.
- **UVM lint.** Factory registration and creation, constructor compatibility, phase calls and config_db checks, with lightbulb fixes and source waivers (details below).
- **Elaboration diagnostics.** slang compiles and elaborates the whole design in a background process when you save (`uvm.elaboration`), and reports what a simulator's elaboration would: type errors, unknown members, wrong argument counts, missing modules or packages. Errors raised inside a macro point at your code, not the macro. Errors the UVM library reports on its own are left out, while errors your code causes inside it (a constructor without a parent, a wrong type parameter) are reported, pointing at your type. The status bar shows the result; **UVM: Elaborate Now** runs it on demand.

## Quick fixes and UVM lint

Use the lightbulb or **Quick Fix…** (`Ctrl+.`) on a finding. Existing checks cover `create-unregistered`, `utils-mismatch`, `create-no-parent`, `create-virtual`, `unknown-base`, `config-db-no-set` and `config-db-type-mismatch`. Optional or conditional config_db gets with no set are informational; required gets are warnings. A dynamic field name in a set suppresses the no-set check. Matching is static and cannot resolve every runtime scope or value.

Four more checks help repair common UVM mistakes:

- `factory-ctor`: a registered class cannot accept its factory's constructor calls (error); add a compatible constructor or an object's missing name default when possible. The selected UVM library and defines determine the required calls.
- `create-new`: direct `new(name, parent)` bypasses the factory for a registered project component (warning); change it to `type_id::create`.
- `utils-param`: used specializations collide under name registration, or a non-parameterized test uses param-utils without registering its name (warning); change the registration macro.
- `phase-no-super`: a nonempty `build_phase` or `connect_phase` skips work in a project base class (warning); insert a `super` call when its ordering is unambiguous.

Other fixes register a class, swap object/component utils, supply a missing parent, correct a config_db field typo or compatible type, and show the field in the cross-reference. Virtual-class and unknown-base findings currently offer only Ignore. Supported slang “did you mean” diagnostics can replace a misspelling. A missing-include action adds to `uvm.includeDirs` with status-bar feedback, leaving simulator filelists unchanged: one candidate is preferred, two or three offer a choice, and more than three offer none.

Fixes check current text and do not edit macro-generated code or library files. A registration fix can edit another workspace file, named in its title. Saving those edits is controlled by the editor; check `files.refactoring.autoSave` for background edits and save any buffers left dirty.

**Fix all N 'code' problems in this file** appears when at least two findings of that code have safe fixes. **Source Action… → Fix all auto-fixable UVM problems** (`source.fixAll.uvm`) applies safe fixes across codes in the current file. Only safe registration, missing-parent, constructor and utils-mismatch fixes qualify; an object-to-component registration change still needs individual review. Factory rewrites, param-utils changes, phase calls, config_db/typo fixes, include settings and Ignore are excluded. Another pass can expose a fix hidden by the first problem.

Enable safe fixes on an explicit save with:

```json
{
  "editor.codeActionsOnSave": {
    "source.fixAll.uvm": "explicit"
  }
}
```

Every UVM finding can offer **Ignore 'code' on this line**. For example:

```systemverilog
m = mon::type_id::create("m"); // uvm-lint: ignore create-no-parent
```

Use the diagnostic code, with commas between multiple codes. After code the directive applies to that line; on its own line it applies to the next line containing code, skipping blank lines and comments. Preprocessor directives count as code; this is not a block-wide waiver. `uvmls lint` honours the same directive. slang parse and elaboration errors cannot be waived this way.

Point `uvm.filelists` at the same `.f` files your simulator uses, or leave it empty and every SystemVerilog file in the workspace is indexed with include paths inferred; simulator output folders such as `work/` are skipped, and `uvm.exclude` leaves out more.
The UVM library comes from `uvm.uvmHome` when set, else from `$UVM_HOME`, else from the project's own `uvm_pkg.sv` when it compiles one, and otherwise from the Accellera UVM 1.2 that ships with the extension. Turn off `uvm.useBundledUvm` to use a lightweight built-in UVM model instead.

Base syntax highlighting comes from a SystemVerilog syntax extension such as *Verilog-HDL/SystemVerilog* (`mshr-h.veriloghdl`); this extension adds UVM-specific highlighting on top rather than replacing it.
With Verible installed, set `"verible.arguments": ["--rules_config_search"]` to keep its style warnings about the bundled UVM sources out of Problems: the extension ships a `.rules.verible_lint` that turns Verible's lint off for them, and your own files keep Verible's defaults.

The bundled UVM 1.2 is Accellera's, under the Apache License 2.0. Its `LICENSE.txt` and `NOTICE.txt` are in `server/uvmls/third_party/uvm-1.2/` inside the extension.
The npm packages bundled into the extension's JavaScript are listed with their licence texts in `THIRD_PARTY_NOTICES.txt`: elkjs, under the Eclipse Public License 2.0 (source code at https://github.com/kieler/elkjs), the vscode-languageclient packages (MIT), semver and minimatch (ISC), and brace-expansion and balanced-match (MIT).
