# UVM Language Server

A UVM-aware SystemVerilog language server and VS Code extension built on [slang](https://github.com/MikePopoloski/slang) (through `pyslang`).
The goal is a DVT-class experience for UVM testbenches that is open, fast, and visual.

## What works today

| Area | Feature |
|---|---|
| **Class graph** | One click (editor title button, `Ctrl+Alt+G`, or the status bar). A UML class diagram: orthogonal edge routing, shared inheritance trunks with hollow triangles, aggregation/composition diamonds (filled when the owner creates the member), «create» and «override» dependencies, optional member compartments and package boxes. UVM roles (test/env/agent/driver/monitor/…) are detected from the inheritance chain and shown as stereotypes. Includes search, focus-on-neighbourhood, "everything between A and B", a details panel with clickable members, and PNG export. Projects with more than 300 classes open as a package overview you drill into. |
| **Topology** | The `uvm_test_top` tree for any test, built from `type_id::create()` calls (inherited ones included). The test's factory overrides are applied, components created under an `if` are marked conditional, and `connect()` calls plus port aliases are drawn between instances. |
| **config_db** | Every `set`/`get` grouped by field. Flags a get with no set, and a set/get type mismatch (an exact-type miss that fails silently at runtime). |
| **Navigation** | Definition, type definition, hover, references, completion, document/workspace symbols, type hierarchy, `` `include `` links and code lenses. Resolution follows class type parameters (`req` in `extends uvm_driver #(my_item)` is a `my_item`), virtual interfaces, clocking blocks, `extern` methods and package scopes. Hover shows the comment above a declaration, and the UVM library's NaturalDocs comments as formatted text; a method without one shows the doc of the method it overrides (a TLM port's `get_next_item` shows `uvm_sqr_if_base`'s). |
| **Diagnostics** | slang parse errors, plus UVM lint: unregistered class created through the factory, `uvm_object_utils` on a component (or the reverse), `create()` on a virtual class, a config_db get without a set, config_db type mismatch, and a component created without a parent. |
| **Elaboration** | slang compiles and elaborates the whole design in a separate process and reports what a simulator's elaboration would: type errors, unknown members, wrong argument counts, missing modules or packages, bad overrides, and (from slang's analysis pass) multiple or mixed drivers and assertions without a clock (source `slang-elab`). `uvm.elaboration` runs it on save (the default), also as you type (`onChange`, about a second after the last edit, unsaved text included) or never (`off`); **UVM: Elaborate Now** runs it on demand. An error inside a macro expansion points at the macro argument you wrote, or else at the whole macro call in your file. Errors the UVM library reports on its own (with the same defines) are left out; errors your code causes inside it (a component constructor without a parent, an analysis imp whose owner has no `write()`, a wrong type parameter) are reported at the library line, naming the specialization, with your type as related information. slang's notes ("did you mean", "declared here") are related information. Between runs its diagnostics move with your edits, and those on lines you change go away until the next run. There is no error limit: up to 100 diagnostics per file and 1,000 in all are listed, and the counts include the rest. The status bar shows a spinner, then the error and warning counts; its tooltip names the UVM library, the top modules and the time taken. |
| **Projects** | Reads simulator filelists (`-f`/`-F`, `+incdir+`, `+define+`, `-D`, `$VAR`, `-timescale` for elaboration, and unknown VCS/Xcelium/Questa options are ignored), or works with zero configuration by indexing every `.sv/.svh` file and inferring include paths. The UVM library is `uvm.uvmHome`, else `$UVM_HOME` (a directory without `uvm_pkg.sv` is skipped with a warning), else the project's own `uvm_pkg.sv` (in a filelist, the workspace or an include directory), else the Accellera UVM 1.2 that ships with the extension, whose files open read-only (a default for `files.readonlyInclude`). Turning off `uvm.useBundledUvm` falls back to a built-in UVM macro shim and class model. |
| **CLI / CI** | `python -m uvmls lint -f tb.f` (exit code 1 on findings), `elab -f tb.f [--top T]` (full elaboration; exit code 1 on errors, 2 if it could not run), `graph`, `topology`, `configdb`, `stats`, all with `--json`. `--uvm-home DIR` or `--no-bundled-uvm` picks the UVM library. |

## Try it

```bash
# Python side (once)
python3 -m venv .venv && .venv/bin/pip install pyslang pygls pytest

# Extension side (once)
cd client && npm install && npm run build && cd ..
```

Then open this folder in VS Code and press **F5** ("Run extension on the ALU example").
A second VS Code window opens on `examples/alu_tb`. Open any `.svh` file and click the graph icon in the editor title bar.

To install it like a normal extension instead: `cd client && npm run package`, then **Extensions → … → Install from VSIX** and pick `client/uvm-language-server-0.1.0.vsix`.
On first start the extension offers to create its own Python environment with pyslang and pygls.

Command line on the example:

```bash
cd examples/alu_tb
PYTHONPATH=../../server ../../.venv/bin/python -m uvmls topology -f alu_tb.f --test alu_error_test
PYTHONPATH=../../server ../../.venv/bin/python -m uvmls lint -f alu_tb.f
PYTHONPATH=../../server ../../.venv/bin/python -m uvmls elab -f alu_tb.f
```

Tests: `cd server && ../.venv/bin/python -m pytest -q`. The example tests run on the built-in model, the bundled UVM 1.2, or both. With `UVM_HOME` set, the real-library navigation test uses that UVM instead of the bundled one.

## Layout

```
server/uvmls/
  project.py     which files to parse: filelists or workspace discovery, include dirs, defines
  indexer.py     slang syntax tree -> plain data (classes, members, creates, config_db, overrides, connects, ...)
  third_party/uvm-1.2/  Accellera UVM 1.2: src/, LICENSE.txt and NOTICE.txt, unmodified (Apache-2.0);
                 indexed when no other UVM library is configured or part of the project
  third_party/.rules.verible_lint  turns Verible's lint off for the bundled UVM (with --rules_config_search)
  shim/          UVM macro stand-ins for the built-in model (uvm.useBundledUvm off)
  workspace.py   cross-file semantic layer: name resolution, typedefs, inheritance chains with
                 type-parameter binding, UVM roles, incremental re-indexing
  views.py       class graph, topology and config_db report (JSON for the webview and CLI)
  lints.py       UVM checks
  elab.py        full slang compile and elaboration; the server runs it as `python -m uvmls elab-worker`
  navigation.py  definition / hover / references / completion / symbols
  naturaldocs.py UVM's NaturalDocs comments as Markdown (hover) or plain text (class graph)
  server.py      LSP over stdio (pygls) plus custom requests uvm/classGraph, uvm/topology, uvm/configDb
  cli.py         command line
server/tools/gen_uvm_shim.py   regenerates the macro shim from a UVM source tree
server/tests/                  unit tests and an end-to-end LSP test over stdio
client/                        VS Code extension (TypeScript); webview/diagram.ts renders ELK layouts as SVG
examples/alu_tb/               a complete small UVM testbench (it elaborates cleanly with slang + UVM)
```

### Why syntax-level indexing

The indexer works on slang's syntax trees, not on an elaborated design. That makes it fast, tolerant of code that does not compile yet, and incremental: an edit re-parses only the root file that includes the edited file.
UVM macros matter here. Without definitions, slang drops `` `uvm_fatal(...) `` and the statement after `if (...) `uvm_fatal(...)` silently becomes the `if` body.
By default the real macros come from the bundled UVM 1.2. A file that uses them without `` `include "uvm_macros.svh" `` gets them predefined, because simulators usually have them in scope already.
With the bundled copy turned off, the shim in `server/uvmls/shim/` keeps every UVM macro's name and arity with a minimal body. Factory registration is visible through the same `type_id` typedef the real macros produce, so the rest of the code does not care which one is in use.

## Measured performance

On this machine (WSL2, Python 3.14, pyslang 12):

| Workload | Time |
|---|---|
| Bundled Accellera UVM 1.2 (142 files, 75k lines, 323 classes): parse + index, doc comments included | 0.27 s |
| ALU example (19 classes), full index: bundled UVM 1.2 / built-in model | 0.30 s / 0.013 s |
| ALU example, re-index after editing one file: bundled UVM 1.2 / built-in model | 0.014 s / 0.004 s |
| Synthetic project: 2,400 files, 2,002 classes, full index: bundled UVM 1.2 / built-in model | 2.6 s / 1.6 s |
| Same project, re-index after editing one file: bundled UVM 1.2 / built-in model | 0.018 s / 0.011 s |
| Same project, class graph / topology (800 instances) | 0.07 s / 0.02 s |
| Webview first render, same project (package overview) | 1.1 s; drill-down 0.2 s; all 2,000 classes in one diagram 11 s |
| Elaboration, ALU example with the bundled UVM 1.2: compile + elaborate (with slang's analysis pass) / through the server's worker process / `elab -f` CLI | 0.11 s / 0.20 s / 0.20 s |
| Elaboration, synthetic project (no top module, no UVM copy of its own) | 0.83 s compile + elaborate; 1.1 s through the worker; `elab -f` CLI 1.2 s |
| Hover while the synthetic project elaborates | median 0.4 ms as when idle, slowest 14 ms (it runs in its own process) |
| Peak memory of the elaboration process: ALU example / synthetic project | 0.1 GB / 0.9 GB (freed when it exits) |

The synthetic project is regular and much smaller per file than a real one. Treat these as a lower bound until it has run on a production testbench.

## Where this stands against DVT

Be clear-eyed about it. AMIQ's DVT IDE already ships, in VS Code, things this project only starts to cover: a UVM Browser, ConfigDB and Factory Overrides views, **runtime-accurate UVM elaboration** (it handles loops and conditions), an AI assistant with an agentic mode, and an MCP server.
"Outshine DVT in every way" is not a realistic near-term target. Being better on specific axes is:

1. **Speed and zero setup.** Sub-second incremental updates, and reading the simulator filelist as-is.
2. **Visual exploration.** A live, searchable class graph and topology that update as you type, with drill-down that scales.
3. **Open and scriptable.** CLI and JSON for CI gates, plus an index others can build on.
4. **Static analysis people can trust.** Every finding links to source; approximations are labelled ("conditional", "static view").

## Roadmap (suggested order)

1. Run it on one or two real testbenches and fix what breaks: macros, `ifdef` configurations, parameterized classes.
2. Topology precision: resolve `config_db` scopes against instance paths (which `set` reaches which `get`), propagate simple constant knobs (`is_active = UVM_PASSIVE`), and handle loop-created arrays of components.
3. Sequence ↔ sequencer view (`start()`, `` `uvm_do_on ``, `p_sequencer`) and a register-model view.
4. Rename, semantic tokens and signature help. Scale work: cache parsed roots on disk, and move the identifier/body walks to C++ (a fork of slang-server, or pyslang visitors).
5. MCP server over the same index, so AI assistants can query real facts ("where is this agent's config set?").
6. Optional full elaboration with slang when the design compiles, to replace the static topology with an exact one.

## Known limitations

- Topology and config_db matching are static approximations. Runtime-computed instance names, `for` loops creating arrays, and knobs set in derived tests are shown as conditional or by expression, not evaluated.
- References are name-based (all identifier occurrences), not type-resolved.
- Code inside inactive `` `ifdef `` branches is not indexed. Defines come from the filelist and `uvm.defines`.
- The bundled library is UVM 1.2. A project on UVM 1.1d or IEEE 1800.2 should set `uvm.uvmHome` (or `$UVM_HOME`) or list its own `uvm_pkg.sv` in the filelist; otherwise navigation and the class graph show the 1.2 declarations.
- Every full re-index (startup, a settings change, a file created or deleted) parses the UVM library again (about 0.27 s), and every root that includes `uvm_macros.svh` preprocesses the real macro headers: on the synthetic project a full index takes 2.6 s with the bundled UVM against 1.6 s with the built-in model.
- Other SystemVerilog extensions lint every file VS Code opens, the bundled UVM sources included, and Verible keeps those warnings until it restarts. Go to Definition into UVM opens a file, and so does hovering a UVM name when the *SystemVerilog* extension (`eirikpre.systemverilog`) is installed, because its hover reads the line the definition points at. With Verible's default rules, `uvm_component.svh` alone has 303 style warnings. The Problems filter `!**/uvmls/third_party/**` hides them. To stop them instead, set `"verible.arguments": ["--rules_config_search"]`: Verible then reads `.rules.verible_lint` files, and the one in `server/uvmls/third_party/` turns its lint rules off for the bundled UVM. Your own files keep Verible's defaults, unless your project has a `.rules.verible_lint` of its own, which Verible then applies to them.
- With `uvm.useBundledUvm` off, the built-in model knows only the common UVM base classes and a few of their members, has no UVM sources to navigate into, and cannot be elaborated.
- Elaboration gets the same files, include directories and defines as the index, plus the filelist's `-timescale`. Other options, such as `-y`/`+libext+` library directories or a simulator's compatibility switches, are not passed to slang, which is also stricter than most simulators about non-standard code. A design that depends on them can show errors its simulator would not.
- Elaboration is skipped in discovery mode when a module, interface or program, or a package, would be defined more than once: in several files, or in one file that several roots include (usually several testbenches in one workspace); set `uvm.filelists`. Discovery leaves out of the compile any `.svh`/`.vh` header that nothing includes and that holds no module, interface, program or package (an old copy, a template). A file that exists only in an unsaved editor cannot be a compilation root.
- No license has been chosen yet (`UNLICENSED` in the manifests); pick one before publishing. The bundled UVM 1.2 is Apache-2.0; its `LICENSE.txt` and `NOTICE.txt` ship next to it and must stay there. The npm packages bundled into the extension (elkjs under EPL-2.0, the vscode-languageclient family under MIT, and their ISC/MIT dependencies) ship their licence texts in `THIRD_PARTY_NOTICES.txt`, which every client build regenerates; `npm run package` fails if it, elkjs's copyright headers or the UVM licence would be missing from the .vsix.
