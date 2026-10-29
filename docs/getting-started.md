# Getting started

Ten minutes from install to the first class graph, topology and quick fix, on the bundled ALU example or on
your own testbench. Settings are listed with their defaults in VS Code under **Settings → Extensions → UVM**.

## 1. Install and set up Python

Install **ElegantUVM** from the Marketplace or Open VSX (see [Install](../README.md#install) for offline and
pinned installs). Open a folder with SystemVerilog in it and trust it when VS Code asks: the extension starts a
Python process, so it stays disabled in Restricted Mode. The status bar shows **UVM: set up Python** and a
notification offers **Set Up…**: click it, then **Install automatically** in the dialog, and the extension creates a
private virtual environment with `pyslang` and `pygls` (from the wheels it ships on Linux x86_64 for Python 3.11 to
3.14, else with `pip`). On Debian and Ubuntu install `python3-venv` first; the `venv` module is a separate package
there. The status bar then shows **UVM: indexing** and, when done, **UVM: N classes**. Nothing else is needed for a
first look.

If you already have an interpreter with `pyslang` and `pygls`, set `uvm.pythonPath` instead.

## 2. Point it at your project

With no configuration, every `.sv`/`.svh` file in the workspace is indexed and include directories are
inferred. That is enough for most testbenches. For a project with a simulator filelist, set it, so
defines, include directories and the file order match your compile:

```json
"uvm.filelists": ["${workspaceFolder}/sim/tb.f"]
```

`-f`/`-F`, `+incdir+`, `+define+`, `-D` and `$VAR` are understood; simulator-only options are ignored. The UVM
library is `uvm.uvmHome`, else `$UVM_HOME`, else the project's own `uvm_pkg.sv`, else the Accellera UVM 1.2
that ships with the extension. **UVM: Re-index Workspace** picks up a changed filelist.

To follow this walk-through on the example, open `examples/alu_tb` from a checkout of this repository; its
own `.vscode/settings.json` already sets `uvm.filelists` to `alu_tb.f`.

## 3. See the testbench

- **Class graph.** Click the graph icon in the editor title bar, press `Ctrl+Alt+G`, or click the status bar.
  Every class with its UVM role as a stereotype, inheritance, creates and overrides. Type a name in the search
  box to focus on it; the details panel lists members you can click to open.
- **Topology.** **UVM: Show Component Topology**, then choose a test. The `uvm_test_top` tree with the
  test's factory overrides applied. In the example, `alu_error_test` shows the driver replaced by
  `alu_error_driver`. The view is static: in `alu_passive_test` the driver and sequencer are still drawn, dashed, as
  conditional on `m_cfg.is_active == UVM_ACTIVE`, because a knob a derived test sets is not propagated (I20 in
  `docs/STATUS.md`).
- **config_db.** **UVM: Show config_db Cross-Reference**: every `set` and `get` by field, with reads that
  have no set and type mismatches flagged. In the editor, the code lens above a `get` names its candidate
  sets, and F12 on the field literal jumps between them.
- **Inlay hints.** In `alu_agent.svh`, a `type_id::create` shows `→ alu_error_driver` once
  `uvm.inlayHintsTest` is `alu_error_test`. In `alu_driver.svh`, `req: alu_seq_item` appears on the uses of `req`
  in `run_phase`, the driver's parameter-typed member.

## 4. Fix something

Lint diagnostics appear as you type. To see a quick fix on the example, open `agent/alu_monitor.svh` and
replace `` `uvm_component_utils(alu_monitor) `` with `` `uvm_object_utils(alu_monitor) ``: the `utils-mismatch`
warning appears, and the light bulb (`Ctrl+.`) offers the right macro. Delete the macro entirely and the
`create-unregistered` warning appears on the `alu_monitor::type_id::create` call in `agent/alu_agent.svh` (open it
from the Problems panel); its light-bulb fix edits `alu_monitor.svh` and adds the macro back. **Fix All** (`source.fixAll.uvm`) applies every safe fix in
the file; `// uvm-lint: ignore` waives one line.

Elaboration runs on save (`uvm.elaboration`): slang compiles the whole design and reports what a simulator's
elaboration would, as `slang-elab` diagnostics. **UVM: Elaborate Now** runs it on demand; the status bar
shows the counts.

## 5. Everyday navigation

Go to definition and type definition follow class type parameters, `extern` bodies, virtual interfaces
and packages. Hover shows the declaration with its comment, and the UVM library's own documentation for
library methods. **Trigger Parameter Hints** shows a method's signature, `type_id::create` included.
**Format Document** runs Verible (bundled on Linux x86_64; set `uvm.formatter.path` elsewhere).

## 6. The same analyses outside the editor

```bash
pip install uvmls
python -m uvmls lint -f sim/tb.f                 # exit code 1 on warnings or errors
python -m uvmls elab -f sim/tb.f --top tb_top    # full elaboration
python -m uvmls topology -f sim/tb.f --test my_test
python -m uvmls lint -f sim/tb.f --sarif uvmls.sarif   # for GitHub code scanning
```

The GitHub Action and the MCP server for AI assistants are described in the [README](../README.md#ci).

## If something is off

- **UVM: failed to start** or **UVM: stopped**: open **Output → UVM Language Server** for the server log, then
  **UVM: Restart Language Server**.
- Unknown-base or missing-include warnings on a file that compiles: the filelist is not set or a define is
  missing; `uvm.defines` adds project defines, and the quick fix on a missing include adds its directory.
- A slow first index on a large project is expected (about 7 s for 2,400 classes with the bundled UVM 1.2, about
  2 s with the built-in model; see the README's performance table); later edits are incremental.
