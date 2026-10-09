# UVM Language Server

A UVM-aware SystemVerilog language server and VS Code extension built on [slang](https://github.com/MikePopoloski/slang) (through `pyslang`).
The goal is a DVT-class experience for UVM testbenches that is open, fast, and visual.

## What works today

| Area | Feature |
|---|---|
| **Class graph** | One click (editor title button, `Ctrl+Alt+G`, or the status bar). A UML class diagram: orthogonal edge routing, shared inheritance trunks with hollow triangles, aggregation/composition diamonds (filled when the owner creates the member), «create» and «override» dependencies, optional member compartments and package boxes. UVM roles (test/env/agent/driver/monitor/…) are detected from the inheritance chain and shown as stereotypes. Includes search, focus-on-neighbourhood, "everything between A and B", a details panel with clickable members, and PNG export. Projects with more than 300 classes open as a package overview you drill into. |
| **Topology** | The `uvm_test_top` tree for any test, built from `type_id::create()` calls (inherited ones included). The test's factory overrides are applied, components created under an `if` are marked conditional, and `connect()` calls plus port aliases are drawn between instances. |
| **config_db** | Every `set`/`get` grouped by field. Flags a get with no set, and a set/get type mismatch (an exact-type miss that fails silently at runtime). |
| **Inlay hints** | Factory creates show `→ replacement_type` for the selected test's overrides, with instance counts when only some paths match. The tooltip names the override; Ctrl+click jumps to it. The first use of each parameter-typed inherited member per line shows its bound type (`req: alu_seq_item` in an ALU driver); the tooltip names the binding. `uvm.inlayHints` turns hints off; `uvm.inlayHintsTest` chooses the test (empty uses the topology's default). VS Code's `editor.inlayHints.enabled` also controls display. |
| **Navigation** | Definition, type definition, hover, references, document highlights, structural folding, completion, document/workspace symbols, type hierarchy, `` `include `` links and code lenses. config_db lenses show reads, candidate sets and type mismatches; `uvm.codeLens.configDb` turns them off. Clicking a read with one candidate set opens and selects that set; other lenses open the cross-reference. Hover on a config_db field literal or method shows set types, instance patterns, read locations and lint status, with a link to the cross-reference when the client supports it. F12 on a config_db field literal jumps from a read to its sets, or from a set to its reads. Resolution follows class type parameters (`req` in `extends uvm_driver #(my_item)` is a `my_item`), virtual interfaces, clocking blocks, `extern` methods and package scopes. Document highlights show same-name identifiers in the current file, including declarations; they are name-based and may include another scope's identifiers. Where the server indexes nothing (comments, strings, identifiers in the UVM library), VS Code's own word highlighting applies. On a structure's opening or closing keyword (`class`, `function`, `task`, `begin`, `module`, `case`, `fork`, `covergroup` and the like), highlights select only the matched keyword pair. VS Code's `editor.occurrencesHighlight` controls their display. Folding covers multiline classes, concrete functions/tasks (including extern bodies), modules, interfaces, programs, packages, `begin`/`end` and `fork`/`join` blocks, `case`, covergroups, coverpoints, generate regions, clocking blocks, properties, sequences and constraints; `if`/`else`, loops, `always`/`initial` blocks, timing controls and case items whose body starts on a later line, with or without `begin`; `` `uvm_*_begin `` … `` `uvm_*_end `` blocks such as `` `uvm_object_utils_begin ``; port, parameter, argument and instance connection lists and enum/struct bodies; block comments and runs of line comments; `` `ifdef `` branches, multiline `` `define ``s and macro calls; and `// #region` / `// #endregion` markers (the `#` is optional). A block's or list's opening and closing lines stay visible; comments, multiline `` `define ``s, regions and a body without `begin` fold through their last line. When a header's port or parameter list folds on its own, the body's fold starts on the header's last line. Prototypes, generated blocks and unmatched delimiters have no body folds, and an `` `ifdef `` branch, region, `` `uvm_*_begin `` block or statement body that would cross a block is left out. These folds replace VS Code's indentation folding, and some folds it gave have no syntax behind them: a continued expression or assignment, irregularly indented or commented-out code, and a call whose closing bracket ends its last line, which stays visible (so a two-line call does not fold). In the UVM library, lists inside prototypes, declarations and expressions do not fold. Use **Fold** / **Unfold** (`editor.fold`, `editor.unfold`) and `editor.folding`. Hover shows the comment above a declaration, and the UVM library's NaturalDocs comments as formatted text; a method without one shows the doc of the method it overrides (a TLM port's `get_next_item` shows `uvm_sqr_if_base`'s). |
| **Diagnostics** | slang parse errors and eleven UVM lints, with quick fixes for factory registration, constructors, component creation, phase calls and config_db mistakes. See the lint codes, fix-all and source waivers below. |
| **Signature help** | **Trigger Parameter Hints** (`editor.action.triggerParameterHints`) shows class methods and explicit constructors, including inherited methods, extern prototypes, parameterized receivers and typed `new`. Factory `type_id::create` shows the selected library’s registry API with the return type bound to the requested class. Declaration comments and inherited UVM NaturalDocs use the same rendering as hover. Commas select the active formal; named arguments select it by name, even when reordered. Nested expressions, calls on a call's result or on its members and elements (`phase.get_objection().set_drain_time(`), calls written in the arguments of a macro usage that has all its arguments and its `)` (a usage still being typed is not expanded), empty positional slots and unfinished calls are supported. Unknown or partially typed named labels get no hint. VS Code’s `editor.parameterHints.enabled` controls display. Unresolved calls and constructors without an indexed declaration get no popup. Built-in UVM mode shows user methods, but cannot supply library signatures without argument metadata. |
| **Formatting** | **Format Document** (`editor.action.formatDocument`) and **Format Selection** (`editor.action.formatSelection`) run an optional local `verible-verilog-format` on the current buffer, including unsaved edits. They work without a successful index or elaboration and return edits for the editor to apply. Library sources are left alone, including while source discovery is pending. |
| **Elaboration** | slang compiles and elaborates the whole design in a separate process and reports what a simulator's elaboration would: type errors, unknown members, wrong argument counts, missing modules or packages, bad overrides, and (from slang's analysis pass) multiple or mixed drivers and assertions without a clock (source `slang-elab`). `uvm.elaboration` runs it on save (the default), also as you type (`onChange`, about a second after the last edit, unsaved text included) or never (`off`); **UVM: Elaborate Now** runs it on demand. An error inside a macro expansion points at the macro argument you wrote, or else at the whole macro call in your file. Errors the UVM library reports on its own (with the same defines) are left out; errors your code causes inside it (a component constructor without a parent, an analysis imp whose owner has no `write()`, a wrong type parameter) are reported at the library line, naming the specialization, with your type as related information. slang's notes ("did you mean", "declared here") are related information. Between runs its diagnostics move with your edits, and those on lines you change go away until the next run. There is no error limit: up to 100 diagnostics per file and 1,000 in all are listed, and the counts include the rest. The status bar shows a spinner, then the error and warning counts; its tooltip names the UVM library, the top modules and the time taken. |
| **Projects** | Reads simulator filelists (`-f`/`-F`, `+incdir+`, `+define+`, `-D`, `$VAR`, `-timescale` for elaboration, and unknown VCS/Xcelium/Questa options are ignored), or works with zero configuration by indexing every `.sv/.svh` file and inferring include paths (simulator output such as `work/` or `csrc/` inside the workspace is skipped, as are `uvm.exclude` globs). The UVM library is `uvm.uvmHome`, else `$UVM_HOME` (a directory without `uvm_pkg.sv` is skipped with a warning), else the project's own `uvm_pkg.sv` (in a filelist, the workspace or an include directory), else the Accellera UVM 1.2 that ships with the extension, whose files open read-only (a default for `files.readonlyInclude`). Turning off `uvm.useBundledUvm` falls back to a built-in UVM macro shim and class model. |
| **CLI / CI** | `python -m uvmls lint -f tb.f` (exit code 1 on warnings or errors; info findings are listed but do not fail), `elab -f tb.f [--top T]` (full elaboration; exit code 1 on errors, 2 if it could not run), `graph`, `topology`, `configdb`, `stats`, all with `--json`. `lint` and `elab` accept `--sarif FILE` for SARIF 2.1.0, including info findings as notes. `--uvm-home DIR` or `--no-bundled-uvm` picks the UVM library. |

## Formatting

Install Verible separately on the server's machine. The server uses `uvm.formatter.path` when nonempty, otherwise `verible-verilog-format` on its PATH. A bare executable name is searched on PATH; a path names one executable, not a shell command. Paths support `~`, environment variables and `${workspaceFolder}`; relative paths use the first workspace root. Without a workspace root, use an absolute path or bare executable name. An invalid explicit path does not fall back to PATH.

Verible uses the editor's tab size but emits spaces even when `insertSpaces` is false. Other optional whitespace preferences do not change its output. Formatting preserves uniform LF or CRLF endings and whether the buffer has a final newline; mixed endings are left unchanged. Empty or unchanged buffers return no edits. Failures and a five-second timeout leave the buffer unchanged and write to the **UVM Language Server** output channel. Cancellation and shutdown also stop the formatter, with a bounded wait for subprocess cleanup. A missing executable is logged once per formatter configuration, with discovery retried on later requests; there is no startup popup. Results are discarded if the buffer closes or changes, or formatter settings change, while the process runs.

Format Selection expands to complete touched lines. An end at the start of a later line excludes that line; an empty selection formats its containing line. Verible receives the whole buffer with `--lines=L-H`. If it changes any text outside those lines, the server returns no edits and explains why in the output channel. Versions that reject `--lines` also leave the buffer unchanged.

`uvm.formatter.enabled` defaults to true; turning it off returns no edits and runs no process. Formatter settings take effect on the next request without reindexing or elaboration. When other formatting extensions are installed, choose this extension with VS Code settings:

```json
{
  "[systemverilog]": {
    "editor.defaultFormatter": "uvmls.uvm-language-server"
  },
  "[verilog]": {
    "editor.defaultFormatter": "uvmls.uvm-language-server"
  }
}
```

Use the standard **Format Document** and **Format Selection** commands. Enable `editor.formatOnSave` separately if wanted; the extension does not change it or choose the default formatter for you.

## UVM lint and quick fixes

The Problems panel and `uvmls lint` use these codes:

| Code | Finding |
|---|---|
| `create-unregistered` | A factory create targets a UVM class without registration. |
| `utils-mismatch` | Object/component registration disagrees with the class's UVM role. |
| `create-no-parent` | A component factory create omits its parent; an error when the indexed registry requires it, otherwise a warning. |
| `create-virtual` | A factory create targets a virtual class. |
| `unknown-base` | A non-UVM base class was not found in the indexed sources (info). |
| `config-db-no-set` | No matching field is set in the indexed sources; optional or conditional gets are info, required gets are warnings. Dynamic field names in sets suppress this check. |
| `config-db-type-mismatch` | A get uses a different type from the field's sets. Type names with competing declarations are treated as unknown and produce no mismatch warning. Matching is static, so runtime scopes and values can remain uncertain. |
| `factory-ctor` | A registered class's constructor cannot accept the calls its UVM factory makes (error). The check follows the selected UVM library and defines. |
| `create-new` | Direct `new(name, parent)` of a registered project component bypasses factory overrides (warning). |
| `utils-param` | Name registration collides across used specializations, or a non-parameterized test uses a param-utils macro that does not register its name (warning). |
| `phase-no-super` | A nonempty `build_phase` or `connect_phase` override skips work in a project base class (warning). |

**Quick fixes:** use the lightbulb or **Quick Fix…** (`Ctrl+.`). Depending on the finding, it can register the created class, swap object/component or param-utils macros, pass a missing parent, add a compatible constructor or an object's name default, replace `new` with a factory create, or insert a missing `super` phase call. For config_db it can correct a literal field typo, change the get's type when compatible, or show the field in the cross-reference. Virtual-class and unknown-base findings currently offer only Ignore.

Missing-include fixes add a directory to `uvm.includeDirs` and confirm the change in the status bar; they do not edit simulator filelists. One candidate is preferred, two or three offer a choice, and more than three offer none. slang's supported “did you mean” diagnostics offer a replacement when the source and suggestion can be checked. Fixes are revalidated against current text and withheld when edit anchors cannot be verified or a phase-ordering conflict is detected; macro-generated code and library files are not edited. Phase-call fixes are offered only when the base phase and every method it calls are fully visible and read no configuration. Configuration reads or calls with unknown bodies withhold the fix entirely; the lint finding remains. Phase-call fixes are never preferred by Auto Fix.

A registration fix may edit another workspace file, named in the action title. The server returns edits; the editor applies and saves them. Check VS Code's `files.refactoring.autoSave` policy for background edits and save any affected buffers left dirty.

**Fix all:** the lightbulb offers **Fix all N 'code' problems in this file** when at least two findings of that code have safe fixes. **Source Action…** offers **Fix all auto-fixable UVM problems** (`source.fixAll.uvm`). Both stay in the current file and use only safe registration, missing-parent, constructor and utils-mismatch fixes. In particular, changing object registration to component registration needs individual review. Factory rewrites, param-utils changes, phase calls, config_db/typo fixes, include settings and Ignore never run through fix-all. A later pass may expose another fix after the first problem is resolved.

To run safe fixes on an explicit save, add this to VS Code settings:

```json
{
  "editor.codeActionsOnSave": {
    "source.fixAll.uvm": "explicit"
  }
}
```

**Ignore a reviewed finding:** every UVM finding can offer **Ignore 'code' on this line**, which writes a source directive such as:

```systemverilog
m = mon::type_id::create("m"); // uvm-lint: ignore create-no-parent
```

Use the code shown in Problems or `uvmls lint`; separate several codes with commas. A directive after code applies to that line. On its own line it applies to the next line containing code, skipping blank lines and comments. It is not a block-wide waiver, and preprocessor directives count as code. The CLI honours it too. slang parse and elaboration errors cannot be waived this way.

## CI

`lint --sarif FILE` and `elab --sarif FILE` write SARIF 2.1.0 reports for code scanning. The composite [UVM lint action](action.yml) installs the server, runs lint on the caller's checkout and uploads `uvmls.sarif`. Results appear under **Security > Code scanning** when code scanning is enabled for the repository. The workflow needs `contents: read`, `actions: read` and `security-events: write` permissions.

| Input | Default | Meaning |
|---|---|---|
| `filelist` | required | Simulator filelist, relative to the caller's workspace. |
| `uvm-home` | empty | UVM installation directory; otherwise use `$UVM_HOME`, a project UVM or the bundled UVM 1.2. |
| `python-version` | `3.12` | Python version for the server (3.10 or newer). |
| `elab` | `false` | Also elaborate and merge its diagnostics into the same SARIF run. |
| `fail-on` | `warning` | `warning` fails on warnings or errors, matching `lint`'s exit code. `error` fails only on errors; `never` does not fail on findings. Notes never fail. |

The action uploads before applying `fail-on`, so findings are available even when the check fails. Installation, analysis and upload failures still fail the action. Unreadable filelists, including nested lists, fail before analysis regardless of `fail-on`. Outputs are `sarif-file` (the report's absolute path) and `problems` (the number of listed results, including notes). With `elab: "true"`, it runs the two CLI commands separately and uses [tools/merge_sarif.py](tools/merge_sarif.py) to merge their rules and results into one run, preserving fingerprints. Findings reported by both commands remain separate results. The failure policy uses elaboration's full counts, including diagnostics beyond its listing limit.

The [sample workflow](.github/workflows/uvm-lint.yml) runs on push and pull requests against `examples/alu_tb/alu_tb.f`, with elaboration enabled and a two-minute job limit. It checks out this repository and uses the local action:

```yaml
- uses: actions/checkout@v4
- uses: ./
  with:
    filelist: examples/alu_tb/alu_tb.f
    elab: "true"
    fail-on: warning
```

For another project, check out this action alongside the project and use that local action path. The server is installed from the action's directory; the filelist and source paths remain relative to the project's checkout.

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
PYTHONPATH=../../server ../../.venv/bin/python -m uvmls lint -f alu_tb.f --sarif uvmls.sarif
```

`--sarif FILE` leaves text output and exit codes unchanged and can accompany `--json`. With `--sarif -`, stdout contains only SARIF; text diagnostics go to stderr. Source URIs are relative to the workspace directory (the optional directory argument, default `.`), with absolute file URIs for diagnostics outside it. Fingerprints use the path, code and source line with whitespace collapsed, so unrelated edits do not change them.

Tests: `cd server && ../.venv/bin/python -m pytest -q`. The example tests run on the built-in model, the bundled UVM 1.2, or both. With `UVM_HOME` set, the real-library navigation test uses that UVM instead of the bundled one.

Quick-fix performance checks are opt-in: from `server/`, run `UVMLS_PERF=1 ../.venv/bin/python -m pytest -q -s -k large_project_code_action_budgets`. The test generates a 2,000-class project on the built-in model and checks handler times without indexing waits. To generate the project separately, run `../.venv/bin/python tools/gen_big.py /tmp/uvmls-big`.

Python 3.10 is the oldest version supported, and pip gives it pyslang 11. `tests/test_py_compat.py` catches syntax newer than 3.10 on any Python; the full check runs the tests on 3.10, for example:

```bash
micromamba create -y -p /tmp/py310 -c conda-forge python=3.10 pip
/tmp/py310/bin/python -m pip install pyslang 'pygls>=2,<3' pytest
cd server && /tmp/py310/bin/python -m pytest -q
```

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
  fixes.py       quick fixes, source waivers and safe fix-all edits
  elab.py        full slang compile and elaboration; the server runs it as `python -m uvmls elab-worker`
  navigation.py  definition / hover / references / completion / symbols
  naturaldocs.py UVM's NaturalDocs comments as Markdown (hover) or plain text (class graph)
  server.py      LSP over stdio (pygls) plus custom requests uvm/classGraph, uvm/topology, uvm/configDb
  cli.py         command line
  sarif.py       lint and elaboration diagnostics as SARIF 2.1.0
server/tools/gen_uvm_shim.py   regenerates the macro shim from a UVM source tree
server/tests/                  unit tests and an end-to-end LSP test over stdio
client/                        VS Code extension (TypeScript); webview/diagram.ts renders ELK layouts as SVG
examples/alu_tb/               a complete small UVM testbench (it elaborates cleanly with slang + UVM)
```

### Why syntax-level indexing

The indexer works on slang's syntax trees, not on an elaborated design. That makes it fast, tolerant of code that does not compile yet, and incremental: an edit re-parses only the root file that includes the edited file.
UVM macros matter here. Without definitions, slang drops `` `uvm_fatal(...) `` and the statement after `if (...) `uvm_fatal(...)` silently becomes the `if` body.
By default the real macros come from the bundled UVM 1.2. A file that uses them without `` `include "uvm_macros.svh" `` gets them predefined, because simulators usually have them in scope already.
With the bundled copy turned off, the shim in `server/uvmls/shim/` keeps every UVM macro's name and arity with a minimal body; `` `uvm_info ``, `` `uvm_warning ``, `` `uvm_error `` and `` `uvm_fatal `` still parse their ID and message, so calls written there are seen. Factory registration is visible through the same `type_id` typedef the real macros produce, so the rest of the code does not care which one is in use.

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
4. Rename and semantic tokens. Scale work: cache parsed roots on disk, and move the identifier/body walks to C++ (a fork of slang-server, or pyslang visitors).
5. MCP server over the same index, so AI assistants can query real facts ("where is this agent's config set?").
6. Optional full elaboration with slang when the design compiles, to replace the static topology with an exact one.

## Known limitations

- Topology and config_db matching are static approximations. Runtime-computed instance names, `for` loops creating arrays, and knobs set in derived tests are shown as conditional or by expression, not evaluated.
- References are name-based (all identifier occurrences), not type-resolved.
- If a document contains identical lines, an unrelated edit can disable quick fixes for fresh diagnostics on those lines in clients that do not return diagnostic `data` with code-action requests. VS Code returns it and is not affected.
- Code inside inactive `` `ifdef `` branches is not indexed. Defines come from the filelist and `uvm.defines`.
- The bundled library is UVM 1.2. A project on UVM 1.1d or IEEE 1800.2 should set `uvm.uvmHome` (or `$UVM_HOME`) or list its own `uvm_pkg.sv` in the filelist; otherwise navigation and the class graph show the 1.2 declarations.
- Every full re-index (startup, a settings change, a file created or deleted) parses the UVM library again (about 0.27 s), and every root that includes `uvm_macros.svh` preprocesses the real macro headers: on the synthetic project a full index takes 2.6 s with the bundled UVM against 1.6 s with the built-in model.
- Other SystemVerilog extensions lint every file VS Code opens, the bundled UVM sources included, and Verible keeps those warnings until it restarts. Go to Definition into UVM opens a file, and so does hovering a UVM name when the *SystemVerilog* extension (`eirikpre.systemverilog`) is installed, because its hover reads the line the definition points at. With Verible's default rules, `uvm_component.svh` alone has 303 style warnings. The Problems filter `!**/uvmls/third_party/**` hides them. To stop them instead, set `"verible.arguments": ["--rules_config_search"]`: Verible then reads `.rules.verible_lint` files, and the one in `server/uvmls/third_party/` turns its lint rules off for the bundled UVM. Your own files keep Verible's defaults, unless your project has a `.rules.verible_lint` of its own, which Verible then applies to them.
- With `uvm.useBundledUvm` off, the built-in model knows only the common UVM base classes and a few of their members, has no UVM sources to navigate into, and cannot be elaborated.
- Elaboration gets the same files, include directories and defines as the index, plus the filelist's `-timescale`. Other options, such as `-y`/`+libext+` library directories or a simulator's compatibility switches, are not passed to slang, which is also stricter than most simulators about non-standard code. A design that depends on them can show errors its simulator would not.
- Elaboration is skipped in discovery mode when a module, interface or program, or a package, would be defined more than once: in several files, or in one file that several roots include (usually several testbenches in one workspace); set `uvm.filelists`. Discovery leaves out of the compile any `.svh`/`.vh` header that nothing includes and that holds no module, interface, program or package (an old copy, a template). A file that exists only in an unsaved editor cannot be a compilation root.
- No license has been chosen yet (`UNLICENSED` in the manifests); pick one before publishing. The bundled UVM 1.2 is Apache-2.0; its `LICENSE.txt` and `NOTICE.txt` ship next to it and must stay there. The npm packages bundled into the extension (elkjs under EPL-2.0, the vscode-languageclient family under MIT, and their ISC/MIT dependencies) ship their licence texts in `THIRD_PARTY_NOTICES.txt`, which every client build regenerates; `npm run package` fails if it, elkjs's copyright headers or the UVM licence would be missing from the .vsix.
