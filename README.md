# ElegantUVM

A free, open-source, UVM-aware SystemVerilog language server and VS Code extension built on [slang](https://github.com/MikePopoloski/slang) (through `pyslang`).
The goal is a DVT-class experience for UVM testbenches that is open, fast, and visual.

## What works today

| Area | Feature |
|---|---|
| **Class graph** | One click (editor title button, `Ctrl+Alt+G`, or the status bar). A UML class diagram: orthogonal edge routing, shared inheritance trunks with hollow triangles, aggregation/composition diamonds (filled when the owner creates the member), «create» and «override» dependencies, optional member compartments and package boxes. UVM roles (test/env/agent/driver/monitor/…) are detected from the inheritance chain and shown as stereotypes. Includes search, focus-on-neighbourhood, "everything between A and B", a details panel with clickable members, and PNG export. Projects with more than 300 classes open as a package overview you drill into. |
| **Topology** | The `uvm_test_top` tree for any test, built from `type_id::create()` calls (inherited ones included). The test's factory overrides are applied, components created under an `if` are marked conditional, and `connect()` calls plus port aliases are drawn between instances. |
| **config_db** | Every `set`/`get` grouped by field. Flags a get with no set, and a set/get type mismatch (an exact-type miss that fails silently at runtime). |
| **Inlay hints** | Factory creates show `→ replacement_type` for the selected test's overrides, with instance counts when only some paths match. The tooltip names the override; Ctrl+click jumps to it. The first use of each parameter-typed inherited member per line shows its bound type (`req: alu_seq_item` in an ALU driver); the tooltip names the binding. `uvm.inlayHints` turns hints off; `uvm.inlayHintsTest` chooses the test (empty uses the topology's default). VS Code's `editor.inlayHints.enabled` also controls display. |
| **Navigation** | Definition, type definition, hover, references, document highlights, structural folding, completion, document/workspace symbols, type hierarchy, `` `include `` links and code lenses. config_db lenses show reads, candidate sets and type mismatches; `uvm.codeLens.configDb` turns them off. Clicking a read with one candidate set opens and selects that set; other lenses open the cross-reference. Hover on a config_db field literal or method shows set types, instance patterns, read locations and lint status, with a link to the cross-reference when the client supports it. F12 on a config_db field literal jumps from a read to its sets, or from a set to its reads. Resolution follows class type parameters (`req` in `extends uvm_driver #(my_item)` is a `my_item`), virtual interfaces, clocking blocks, `extern` methods and package scopes. Document highlights show same-name identifiers in the current file, including declarations; they are name-based and may include another scope's identifiers. Where the server indexes nothing (comments, strings, identifiers in the UVM library), VS Code's own word highlighting applies. On a structure's opening or closing keyword (`class`, `function`, `task`, `begin`, `module`, `case`, `fork`, `covergroup` and the like), highlights select only the matched keyword pair. VS Code's `editor.occurrencesHighlight` controls their display. Folding covers multiline classes, concrete functions/tasks (including extern bodies), modules, interfaces, programs, packages, `begin`/`end` and `fork`/`join` blocks, `case`, covergroups, coverpoints, generate regions, clocking blocks, properties, sequences and constraints; `if`/`else`, loops, `always`/`initial` blocks, timing controls and case items whose body starts on a later line, with or without `begin`; `` `uvm_*_begin `` … `` `uvm_*_end `` blocks such as `` `uvm_object_utils_begin ``; port, parameter, argument and instance connection lists and enum/struct bodies; block comments and runs of line comments; `` `ifdef `` branches, multiline `` `define ``s and macro calls; and `// #region` / `// #endregion` markers (the `#` is optional). A block's or list's opening and closing lines stay visible; comments, multiline `` `define ``s, regions and a body without `begin` fold through their last line. When a header's port or parameter list folds on its own, the body's fold starts on the header's last line. Prototypes, generated blocks and unmatched delimiters have no body folds, and an `` `ifdef `` branch, region, `` `uvm_*_begin `` block or statement body that would cross a block is left out. These folds replace VS Code's indentation folding, and some folds it gave have no syntax behind them: a continued expression or assignment, irregularly indented or commented-out code, and a call whose closing bracket ends its last line, which stays visible (so a two-line call does not fold). In the UVM library, lists inside prototypes, declarations and expressions do not fold. Use **Fold** / **Unfold** (`editor.fold`, `editor.unfold`) and `editor.folding`. Hover on a variable with an initializer adds where it is initialized and to what (`initialized to 1 in alu_env_config.svh:5`): that is the declaration's value, not the value at the cursor, since assignments are not tracked. Hover shows the comment above a declaration, and the UVM library's NaturalDocs comments as formatted text; a method without one shows the doc of the method it overrides (a TLM port's `get_next_item` shows `uvm_sqr_if_base`'s). |
| **Diagnostics** | slang parse errors and eleven UVM lints, with quick fixes for factory registration, constructors, component creation, phase calls and config_db mistakes. See the lint codes, fix-all and source waivers below. |
| **Signature help** | **Trigger Parameter Hints** (`editor.action.triggerParameterHints`) shows class methods and explicit constructors, including inherited methods, extern prototypes, parameterized receivers and typed `new`. Factory `type_id::create` shows the selected library’s registry API with the return type bound to the requested class. Declaration comments and inherited UVM NaturalDocs use the same rendering as hover. Commas select the active formal; named arguments select it by name, even when reordered. Nested expressions, calls on a call's result or on its members and elements (`phase.get_objection().set_drain_time(`), calls written in the arguments of a macro usage that has all its arguments and its `)` (a usage still being typed is not expanded), empty positional slots and unfinished calls are supported. Unknown or partially typed named labels get no hint. VS Code’s `editor.parameterHints.enabled` controls display. Unresolved calls and constructors without an indexed declaration get no popup. Built-in UVM mode shows user methods, but cannot supply library signatures without argument metadata, and its shim leaves the ID and message of `` `uvm_info ``, `` `uvm_warning ``, `` `uvm_error `` and `` `uvm_fatal `` unparsed, so calls written there get no help. |
| **Formatting** | **Format Document** (`editor.action.formatDocument`) and **Format Selection** (`editor.action.formatSelection`) run `verible-verilog-format` on the current buffer, including unsaved edits. The Linux x64, Windows x64 and macOS arm64 platform packages bundle Verible v0.0-4296-g0f262651 (Apache-2.0). On Intel macOS, or with the generic package, install Verible and set `uvm.formatter.path` or put it on PATH. They work without a successful index or elaboration and return edits for the editor to apply. Library sources are left alone (the UVM library in use or configured and the files it includes, any UVM source directory, and the extension's own UVM files), including through symlinks. Until the first index finishes, a file the library includes from outside its UVM source directory is not yet known as library code. |
| **Elaboration** | slang compiles and elaborates the whole design in a separate process and reports what a simulator's elaboration would: type errors, unknown members, wrong argument counts, missing modules or packages, bad overrides, and (from slang's analysis pass) multiple or mixed drivers and assertions without a clock (source `slang-elab`). `uvm.elaboration` runs it on save (the default), also as you type (`onChange`, about a second after the last edit, unsaved text included) or never (`off`); **UVM: Elaborate Now** runs it on demand. An error inside a macro expansion points at the macro argument you wrote, or else at the whole macro call in your file. Errors the UVM library reports on its own (with the same defines) are left out; errors your code causes inside it (a component constructor without a parent, an analysis imp whose owner has no `write()`, a wrong type parameter) are reported at the library line, naming the specialization, with your type as related information. slang's notes ("did you mean", "declared here") are related information. Between runs its diagnostics move with your edits, and those on lines you change go away until the next run. There is no error limit: up to 100 diagnostics per file and 1,000 in all are listed, and the counts include the rest. The status bar shows a spinner, then the error and warning counts; its tooltip names the UVM library, the top modules and the time taken. |
| **Projects** | Reads simulator filelists (`-f`/`-F`, `+incdir+`, `+define+`, `-D`, `$VAR`, `-timescale` for elaboration, and unknown VCS/Xcelium/Questa options are ignored), or works with zero configuration by indexing every `.sv/.svh` file and inferring include paths (simulator output such as `work/` or `csrc/` inside the workspace is skipped, as are `uvm.exclude` globs). The UVM library is `uvm.uvmHome`, else `$UVM_HOME` (a directory without `uvm_pkg.sv` is skipped with a warning), else the project's own `uvm_pkg.sv` (in a filelist, the workspace or an include directory), else the Accellera UVM 1.2 that ships with the extension, whose files open read-only (a default for `files.readonlyInclude`). Turning off `uvm.useBundledUvm` falls back to a built-in UVM macro shim and class model. |
| **CLI / CI** | `python -m uvmls lint -f tb.f` (exit code 1 on warnings or errors; info findings are listed but do not fail), `elab -f tb.f [--top T]` (full elaboration; exit code 1 on errors, 2 if it could not run), `graph`, `topology`, `configdb`, `stats`, all with `--json`, and `mcp` (below). `lint` and `elab` accept `--sarif FILE` for SARIF 2.1.0, including info findings as notes. `--uvm-home DIR` or `--no-bundled-uvm` picks the UVM library; `--exclude GLOB` leaves files out of discovery. |
| **MCP server** | `python -m uvmls mcp DIR -f tb.f` answers an AI assistant's questions over the Model Context Protocol from the same index: classes by role, a test's topology with its factory overrides applied, the effective type of every create, config_db sets and gets with their status, lint, slang elaboration, definition, hover, references, signature help, sequences and their start sites, factory creates, the include graph, design units, macros and typedefs. Read-only, with 1-based locations, paged results and a workspace-root check on every path. The extension registers it with VS Code (`uvm.mcp.enabled`), and it runs without the IDE for Claude Code or Claude Desktop. See [MCP server](#mcp-server). |

## Formatting

The `linux-x64`, `win32-x64` and `darwin-arm64` packages bundle `verible-verilog-format` from Verible release v0.0-4296-g0f262651 (Apache-2.0). Its LICENSE and VERSION ship next to the binary in `server/uvmls/third_party/verible/<target>/`, and `THIRD_PARTY_NOTICES.txt` lists it. The `darwin-x64` package ships wheels only: this release's macOS formatter is arm64, so Intel Mac users must install Verible separately (for example with Homebrew) and set `uvm.formatter.path` or put it on PATH. The generic package and pip-installed server also require a separately installed formatter. The server uses `uvm.formatter.path` when nonempty, otherwise `verible-verilog-format` on PATH, otherwise the host's bundled copy when available; the **UVM Language Server** output channel names the copy in use the first time it runs. A bare executable name is searched on PATH; a path names one executable, not a shell command. Paths support `~`, environment variables and `${workspaceFolder}`; relative paths use the first workspace root. Without a workspace root, use an absolute path or bare executable name. An invalid explicit path does not fall back to PATH or the bundled copy.

Verible uses the editor's tab size but emits spaces even when `insertSpaces` is false. Other optional whitespace preferences do not change its output. Formatting preserves uniform LF or CRLF endings and whether the buffer has a final newline; mixed endings are left unchanged. Empty or unchanged buffers return no edits. Failures and a five-second timeout leave the buffer unchanged and write to the **UVM Language Server** output channel. Cancellation and shutdown also stop the formatter, with a bounded wait for subprocess cleanup. A missing executable is logged once per formatter configuration, with discovery retried on later requests; there is no startup popup. Verible runs in the document's directory, or in its nearest existing parent once that directory has been removed under an open buffer. A buffer holding a lone UTF-16 surrogate, which UTF-8 cannot encode, is left unchanged and the output channel names its line. Results are discarded if the buffer closes or changes, or formatter settings change, while the process runs.

Format Selection expands to complete touched lines. An end at the start of a later line excludes that line; an empty selection formats its containing line. Verible receives the whole buffer with `--lines=L-H`. If it changes any text outside those lines, the server returns no edits and explains why in the output channel. Versions that reject `--lines` also leave the buffer unchanged.

`uvm.formatter.enabled` defaults to true; turning it off returns no edits and runs no process. Formatter settings take effect on the next request without reindexing or elaboration. When other formatting extensions are installed, choose this extension with VS Code settings:

```json
{
  "[systemverilog]": {
    "editor.defaultFormatter": "elegantuvm.elegantuvm"
  },
  "[verilog]": {
    "editor.defaultFormatter": "elegantuvm.elegantuvm"
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
| `python-version` | `3.12` | Python version for the server (3.11 or newer). |
| `elab` | `false` | Also elaborate and merge its diagnostics into the same SARIF run. |
| `fail-on` | `warning` | `warning` fails on warnings or errors, matching `lint`'s exit code. `error` fails only on errors; `never` does not fail on findings. Notes never fail. |
| `upload` | `true` | `false` skips the code-scanning upload (a private repository without Advanced Security rejects it); the report is still written and `fail-on` still applies. |

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

## MCP server

`python -m uvmls mcp [DIR] [-f tb.f ...]` speaks the [Model Context Protocol](https://modelcontextprotocol.io) over stdio (newline-delimited JSON-RPC, implemented in `server/uvmls/mcp.py` without the `mcp` SDK, so the server still depends only on pyslang and pygls). An AI assistant then asks the same index the editor uses instead of guessing from grep: which classes play which UVM role, what `uvm_test_top` looks like under a test once its factory overrides apply, where a config_db field is set and read, what lint and slang's elaboration report, where a name is declared and what its hover says. It takes the other CLI commands' flags; without any it reads the `uvm.*` keys of `DIR/.vscode/settings.json` (one folder's file, a convenience, not VS Code's merge of user and workspace settings). Paths in `-f` and `-I` are relative to the directory the server starts in, as for the other commands. Start it from outside the workspace, or with `-P` on Python 3.11+ (`python -P -m uvmls mcp /path/to/tb -f tb.f`): `python -m` imports from the working directory first, so `cd tb; python -m uvmls mcp .` would run a `json.py` or `uvmls.py` lying in the testbench instead of the standard library or the server.

In VS Code the extension registers it as an MCP server definition provider: **MCP: List Servers** shows "UVM Language Server", started with the resolved `uvm.pythonPath` (or the language server's interpreter), `-m uvmls mcp <first workspace folder>` plus the `uvm` settings as flags (paths made absolute), `PYTHONPATH` set to the server directory and that directory as the working directory, never the workspace: `python -m` imports from the working directory first, so a workspace file named `uvmls.py` would otherwise run instead of the server (the language server runs the same way). The "UVM MCP" output channel logs that command line. The definition follows the settings, and `uvm.mcp.enabled` turns it off. Copilot and other agents in the editor can then call its tools.

Outside the IDE, the package must be importable by the interpreter (`PYTHONPATH=.../server`, or `pip install ./server`). Claude Code, run from the testbench directory:

```bash
claude mcp add uvmls -e PYTHONPATH=/path/to/uvm-language-server/server -- /path/to/.venv/bin/python -m uvmls mcp . -f tb.f
```

Claude Desktop (`claude_desktop_config.json`) and a `.vscode/mcp.json` for a folder without the extension:

```json
{
  "mcpServers": {
    "uvmls": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "uvmls", "mcp", "/path/to/tb", "-f", "/path/to/tb/tb.f"],
      "env": { "PYTHONPATH": "/path/to/uvm-language-server/server" }
    }
  }
}
```

```json
{
  "servers": {
    "uvmls": {
      "type": "stdio",
      "command": "${workspaceFolder}/../.venv/bin/python",
      "args": ["-m", "uvmls", "mcp", "${workspaceFolder}", "-f", "${workspaceFolder}/tb.f"],
      "env": { "PYTHONPATH": "${workspaceFolder}/../server" }
    }
  }
}
```

`tools/mcp_smoke.py` is a small client for checks by hand: `python tools/mcp_smoke.py --call topology '{"test": "alu_error_test"}' -- .venv/bin/python -m uvmls mcp examples/alu_tb -f examples/alu_tb/alu_tb.f`, run from the repository root with `PYTHONPATH=server` in the environment (or the package installed); `-f` is relative to that directory, not to `examples/alu_tb`. The tests drive the server through it.

| Tool | Arguments | Returns |
|---|---|---|
| `stats` | - | the index statistics, the UVM library in use, the index version, seconds since the last (re)index |
| `tests` | - | every test class with package, base and location; the default test and the `run_test()` calls |
| `classes` | `query` (substring or glob), `role`, `package`, `library`, `limit`, `offset` | qualified name, role, base class, location |
| `class` | `name` (qualified when several packages declare it; otherwise the candidates are named) | base chain with bindings, properties with types, methods with signatures, bodies and doc comments, registration macro, subclasses, where the factory creates it |
| `topology` | `test` | the `uvm_test_top` tree as `topology --json` gives it (`overriddenFrom`, `conditional`, `createdAt`), connections and override calls; an unknown test names the known ones |
| `effective_type` | `test`, `file`, `line`, `limit`, `offset` | per factory create: type written, type made after the test's overrides, the override call, instance counts |
| `config_db` | `field`, `status` (`ok`, `no-set`, `no-get`, `type-mismatch`, `dynamic`), `file`, `limit`, `offset` | rows of the cross-reference: each set and get with type, scope pattern, context, owner, method, optional, conditional |
| `lint` | `codes`, `file`, `severity`, `limit`, `offset` | what `uvmls lint` reports, `// uvm-lint: ignore` directives applied |
| `elaborate` | `tops` | slang's elaboration in the worker subprocess: top modules, counts, the first diagnostics (`truncated` when the display limits cut the list) |
| `definition`, `hover`, `references`, `signature` | `file`, `line`, `col` (1-based, columns in characters) | what the editor shows there; a library file or a path outside the workspace is refused |
| `symbols` | `query`, `definition`, `limit`, `offset` | workspace symbols (characters in order); with `definition`, an exact class or unit name's declaration |
| `sequences` | `limit`, `offset` | sequence classes with item type and `p_sequencer`, and every `seq.start(sequencer)` site with the sequence it resolves to (`null` when it cannot, as for a macro's local) |
| `creates` | `type`, `file`, `limit`, `offset` | every `type_id::create` with the resolved type, instance name, parent, owner and method |
| `includes` | `file`, `limit`, `offset` | which roots reach a file, what it includes, what includes it |
| `units` | `kind`, `library`, `limit`, `offset` | modules, interfaces, programs and packages with ports, parameters, instances, imports, `run_test()` calls |
| `macros`, `typedefs` | `query`, `library`, `limit`, `offset` | definitions with locations |
| `reindex` | - | rebuilds the index and returns `stats` |

Resources: `uvm://stats`, `uvm://topology` and `uvm://topology/{test}`, `uvm://configdb`, `uvm://lint` (JSON) and `uvm://file/{path}` (the indexed text of a file under the workspace root).

What to expect: every location has 1-based lines and character columns and a path relative to the workspace root (the CLI's `--json` keeps its 0-based byte columns). List tools take `limit` (default 200, at most 2000) and `offset` and return `items`, `total` and `truncated`. The server reads the files on disk: unsaved editor text is not visible, and the index refreshes itself when a file changes (checked before a tool call, at most once a second; a changed file re-parses the roots that include it, a file created or deleted or a changed filelist rebuilds everything, `reindex` forces it). Runtime values are not evaluated: the topology and config_db answers are the same static views as in the editor. Nothing writes files or runs code; `elaborate` compiles with slang in the `elab-worker` subprocess with the same 120 s limit as the editor (a run that cannot start, fails or overruns it is an error result), later tool calls wait for it, and `ping` is answered meanwhile. A path in an argument or a `uvm://file/` URI must lie under a workspace root once symlinks are resolved, so a link inside the workspace to a file outside it is refused. Starting costs one index (0.3 s for the example with the bundled UVM; see the performance table), and the first call after a file change pays one root re-parse. The protocol is the initialize handshake of revisions 2024-11-05 to 2025-11-25 (echoed, else 2025-11-25); the stateless 2026-07-28 revision's `server/discover` is refused so such clients fall back to it. Logs go to stderr.

## Install

**VS Code.** Install **ElegantUVM** (`elegantuvm.elegantuvm`) from the Visual Studio Marketplace, or from
[Open VSX](https://open-vsx.org/) in VSCodium, Cursor, Windsurf and Theia. Platform packages are built for Linux x64, Windows x64 and macOS arm64/x64. Each carries Python wheels for offline setup; Linux x64, Windows x64 and macOS arm64 also carry Verible. Intel macOS (`darwin-x64`) requires an external formatter. Native Windows/macOS acceptance is still pending on this branch; see the [implementation checkpoint](docs/f57-platforms-progress.md).

**Python.** The server needs Python 3.11 or newer. On first start the extension offers to create its own
environment (**Set Up…** in the status bar): it looks for `python3`, then `python3.14` … `python3.11` (Enterprise
Linux keeps `python3` old), and installs `pyslang` and `pygls` into a private venv. Or point `uvm.pythonPath` at
an interpreter that already has them. On Windows, setup tries the `py` launcher with explicit
`-3.14` … `-3.11` switches before trying `python`. With Remote-SSH, the extension runs on the remote host,
so a Linux x86_64 remote uses the Linux package even when the local computer runs Windows or macOS.

**Offline and behind a proxy.** The four platform packages install dependencies from their bundled wheels,
with no network access. The generic package installs from a package index; give pip a mirror or proxy with `uvm.pipArgs` (for example
`["--index-url", "https://mirror.example/simple", "--trusted-host", "mirror.example"]`), or build a wheelhouse on a
connected machine and point the extension at an environment made from it:

```bash
pip download -d wheels --only-binary=:all: --python-version 3.12 --platform manylinux_2_28_x86_64 --platform any "pyslang>=12" "pygls>=2,<3"
python3.12 -m venv ~/uvmls-venv && ~/uvmls-venv/bin/pip install --no-index --find-links wheels "pyslang>=12" "pygls>=2,<3"
# then: "uvm.pythonPath": "~/uvmls-venv/bin/python"
```

**Pinned installs and allowlists.** Every release is also attached to the GitHub release as `.vsix` files:
`code --install-extension elegantuvm-linux-x64-<version>.vsix` installs exactly that version (VS Code does not
auto-update VSIX installs). Organisations that allowlist extensions can list `elegantuvm.elegantuvm`, optionally with a
version, in `extensions.allowed`.

**No telemetry.** The extension and the server collect nothing and send nothing; the only network access is the
first-run `pip install` you consent to (see SECURITY.md).

**Command line and other editors.** `pip install uvmls` gives the `uvmls` language server on stdio (Neovim, Emacs,
Helix, …) and `python -m uvmls lint | elab | graph | topology | configdb | stats | mcp` (see CI and MCP server below).

Build the target for your machine with `npm run package:linux-x64`, `npm run package:win32-x64`,
`npm run package:darwin-arm64` or `npm run package:darwin-x64`. `npm run package:all` builds the generic
package and all four platform packages. `darwin-x64` bundles wheels only; point `uvm.formatter.path` at
an external Verible. The generic package contains neither wheels nor a formatter.
The bundled macOS arm64 Verible is not notarised: if macOS quarantines it, remove the quarantine from that
binary with `xattr -d com.apple.quarantine /path/to/verible-verilog-format`, or set `uvm.formatter.path`
to a separately installed Verible (for example from Homebrew).

## Try it (from source)

```bash
# Python side (once)
python3 -m venv .venv && .venv/bin/pip install pyslang pygls pytest

# Extension side (once)
cd client && npm install && npm run build && cd ..
```

Then open this folder in VS Code and press **F5** ("Run extension on the ALU example").
A second VS Code window opens on `examples/alu_tb`. Open any `.svh` file and click the graph icon in the editor title bar.

To install it like a normal extension instead: `cd client && npm run package` (generic) or `npm run package:linux-x64` (with the wheels), then **Extensions → … → Install from VSIX** and pick `client/elegantuvm-0.1.0.vsix` or `client/elegantuvm-linux-x64-0.1.0.vsix`.
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

Python 3.11 is the oldest version supported (pyslang 12 requires it). `tests/test_py_compat.py` catches syntax newer than 3.11 on any Python; the full check runs the tests on 3.11, for example:

```bash
micromamba create -y -p /tmp/py311 -c conda-forge python=3.11 pip
/tmp/py311/bin/python -m pip install pyslang 'pygls>=2,<3' pytest
cd server && /tmp/py311/bin/python -m pytest -q
```

## Layout

```
server/uvmls/
  project.py     which files to parse: filelists or workspace discovery, include dirs, defines
  indexer.py     slang syntax tree -> plain data (classes, members, creates, config_db, overrides, connects, ...)
  third_party/uvm-1.2/  Accellera UVM 1.2: src/, LICENSE.txt and NOTICE.txt, unmodified (Apache-2.0);
                 indexed when no other UVM library is configured or part of the project
  third_party/.rules.verible_lint  turns Verible's lint off for the bundled UVM (with --rules_config_search)
  third_party/verible/linux-x64/  in the .vsix only (not in git or the pip package): verible-verilog-format
                 with its LICENSE and VERSION, placed by client/scripts/bundle-server.mjs from the pinned
                 release that client/scripts/fetch-verible.mjs downloads, checks and caches in ~/.cache/uvmls
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
  mcp.py         MCP server over stdio (`python -m uvmls mcp`): JSON-RPC transport, tools and resources over the index
  sarif.py       lint and elaboration diagnostics as SARIF 2.1.0
server/tools/gen_uvm_shim.py   regenerates the macro shim from a UVM source tree
server/tests/                  unit tests and end-to-end LSP and MCP tests over stdio
tools/mcp_smoke.py             a small MCP client, for the tests and for checks by hand
client/                        VS Code extension (TypeScript); webview/diagram.ts renders ELK layouts as SVG
examples/alu_tb/               a complete small UVM testbench (it elaborates cleanly with slang + UVM)
```

### Why syntax-level indexing

The indexer works on slang's syntax trees, not on an elaborated design. That makes it fast, tolerant of code that does not compile yet, and incremental: an edit re-parses only the root file that includes the edited file.
UVM macros matter here. Without definitions, slang drops `` `uvm_fatal(...) `` and the statement after `if (...) `uvm_fatal(...)` silently becomes the `if` body.
By default the real macros come from the bundled UVM 1.2. A file that uses them without `` `include "uvm_macros.svh" `` gets them predefined, because simulators usually have them in scope already.
With the bundled copy turned off, the shim in `server/uvmls/shim/` keeps every UVM macro's name and arity with a minimal body. Factory registration is visible through the same `type_id` typedef the real macros produce, so the rest of the code does not care which one is in use.

## Measured on real projects

Public UVM testbenches with their own filelists, cold index with the bundled UVM 1.2, one process per command
(2026-10-16, Python 3.14, pyslang 12; details, the zero-configuration runs and what the findings are in
[docs/evidence.md](docs/evidence.md)):

| Case | Files | Classes | Index | RSS | Lint findings | Elab errors / warnings | Topology | Graph nodes / edges |
|---|---|---|---|---|---|---|---|---|
| opentitan_uart | 370 | 156 | 1.76 s | 111 MB | 14 | 15 / 7 (0.36 s) | 42 nodes, depth 5, 22 connections | 202 / 338 |
| opentitan_i2c | 407 | 203 | 2.15 s | 122 MB | 19 | 49 / 9 (0.38 s) | 55 nodes, depth 5, 34 connections | 249 / 449 |
| opentitan_aes | 451 | 174 | 2.16 s | 126 MB | 13 | 45 / 9 (0.52 s) | 38 nodes, depth 5, 19 connections | 220 / 365 |
| cv32e40p | 230 | 142 | 1.48 s | 99 MB | 4 | 12 / 5 (0.35 s) | 61 nodes, depth 5, 42 connections | 176 / 391 |
| caliptra_sha512 | 229 | 65 | 0.84 s | 94 MB | 0 | 10 / 16 (0.22 s) | 14 nodes, depth 4, 11 connections | 87 / 131 |
| tvip_axi | 97 | 104 | 0.74 s | 83 MB | 4 | 2 / 0 (0.18 s) | 26 nodes, depth 4, 33 connections | 153 / 231 |
| yuu_ahb | 54 | 56 | 0.62 s | 82 MB | 1 | 37 / 0 (0.17 s) | 16 nodes, depth 4, 11 connections | 87 / 183 |
| apb_ral | 19 | 15 | 0.37 s | 77 MB | 0 | 2 / 0 (0.12 s) | 8 nodes, depth 4, 4 connections | 43 / 71 |
| uvm_examples_ahb2 | 34 | 49 | 0.43 s | 79 MB | 1 | 0 / 1 (0.15 s) | 15 nodes, depth 4, 8 connections | 69 / 193 |

No crashes, tracebacks or timeouts; the nine benches produce 56 lint findings in total, none of which we would
call a bug in ElegantUVM (docs/evidence.md says what they are).

## Measured performance

On this machine (WSL2, Python 3.14, pyslang 12):

| Workload | Time |
|---|---|
| Bundled Accellera UVM 1.2 (142 files, 75k lines, 323 classes): parse + index, doc comments included | 0.27 s |
| ALU example (19 classes), full index: bundled UVM 1.2 / built-in model | 0.30 s / 0.013 s |
| ALU example, re-index after editing one file: bundled UVM 1.2 / built-in model | 0.014 s / 0.004 s |
| Synthetic project: 2,400 files, 2,400 classes, full index: bundled UVM 1.2 / built-in model | 7.0 s / 2.3 s |
| Same project, re-index after editing one file (median of five edits): bundled UVM 1.2 / built-in model | 0.04 s / 0.03 s |
| Same project, class graph / topology (800 instances) | 0.07 s / 0.02 s |
| Webview first render, same project (package overview) | 1.1 s; drill-down 0.2 s; all 2,000 classes in one diagram 11 s |
| Elaboration, ALU example with the bundled UVM 1.2: compile + elaborate (with slang's analysis pass) / through the server's worker process / `elab -f` CLI | 0.11 s / 0.20 s / 0.20 s |
| Elaboration, synthetic project (no top module, no UVM copy of its own) | 0.83 s compile + elaborate; 1.1 s through the worker; `elab -f` CLI 1.2 s |
| Hover while the synthetic project elaborates | median 0.4 ms as when idle, slowest 14 ms (it runs in its own process) |
| Peak memory of the elaboration process: ALU example / synthetic project | 0.1 GB / 0.9 GB (freed when it exits) |

The synthetic project is regular and much smaller per file than a real one. Treat these as a lower bound until it has run on a production testbench. Its full-index and edit rows are CPU seconds of the single-threaded index (`time.process_time`, the same as wall time on an idle machine); a garbage-collection pass lands on some edits and adds up to 0.02 s to one.

## Where this stands against DVT

Be clear-eyed about it. AMIQ's DVT IDE already ships, in VS Code, things this project only starts to cover: a UVM Browser, ConfigDB and Factory Overrides views, **runtime-accurate UVM elaboration** (it handles loops and conditions), an AI assistant with an agentic mode, and an MCP server (40 tools, licensed separately; it has no config_db, factory-override or sequence tools, which `uvmls mcp` has, and ours needs no IDE).
"Outshine DVT in every way" is not a realistic near-term target. Being better on specific axes is:

1. **Speed and zero setup.** Sub-second incremental updates, and reading the simulator filelist as-is.
2. **Visual exploration.** A live, searchable class graph and topology that update as you type, with drill-down that scales.
3. **Open and scriptable.** CLI and JSON for CI gates, an MCP server so agents query the same index, plus an index others can build on.
4. **Static analysis people can trust.** Every finding links to source; approximations are labelled ("conditional", "static view").

## Roadmap (suggested order)

[docs/STATUS.md](docs/STATUS.md) is the full, current list of what is done, planned, shelved and known to be wrong, with stable IDs.

1. Run it on one or two real testbenches and fix what breaks: macros, `ifdef` configurations, parameterized classes.
2. Topology precision: resolve `config_db` scopes against instance paths (which `set` reaches which `get`), propagate simple constant knobs (`is_active = UVM_PASSIVE`), and handle loop-created arrays of components.
3. Sequence ↔ sequencer view (`start()`, `` `uvm_do_on ``, `p_sequencer`) and a register-model view.
4. Rename and semantic tokens. Scale work: cache parsed roots on disk, and move the identifier/body walks to C++ (a fork of slang-server, or pyslang visitors).
5. MCP server over the same index, so AI assistants can query real facts ("where is this agent's config set?"): shipped, see [MCP server](#mcp-server); an in-editor chat participant is a separate item.
6. Optional full elaboration with slang when the design compiles, to replace the static topology with an exact one.

## Known limitations

- Topology and config_db matching are static approximations. Runtime-computed instance names, `for` loops creating arrays, and knobs set in derived tests are shown as conditional or by expression, not evaluated.
- References are name-based (all identifier occurrences), not type-resolved.
- The MCP server sees the files on disk, never unsaved editor text, and answers from the same static views: runtime values, loops and conditions are reported, not evaluated. Its `references` are name-based like the editor's.
- If a document contains identical lines, an unrelated edit can disable quick fixes for fresh diagnostics on those lines in clients that do not return diagnostic `data` with code-action requests. VS Code returns it and is not affected.
- Code inside inactive `` `ifdef `` branches is not indexed. Defines come from the filelist and `uvm.defines`.
- The bundled library is UVM 1.2. A project on UVM 1.1d or IEEE 1800.2 should set `uvm.uvmHome` (or `$UVM_HOME`) or list its own `uvm_pkg.sv` in the filelist; otherwise navigation and the class graph show the 1.2 declarations.
- Every full re-index (startup, a settings change, a file created or deleted) parses the UVM library again (about 0.27 s), and every root that includes `uvm_macros.svh` preprocesses the real macro headers: on the synthetic project a full index takes 2.6 s with the bundled UVM against 1.6 s with the built-in model.
- Other SystemVerilog extensions lint every file VS Code opens, the bundled UVM sources included, and Verible keeps those warnings until it restarts. Go to Definition into UVM opens a file, and so does hovering a UVM name when the *SystemVerilog* extension (`eirikpre.systemverilog`) is installed, because its hover reads the line the definition points at. With Verible's default rules, `uvm_component.svh` alone has 303 style warnings. The Problems filter `!**/uvmls/third_party/**` hides them. To stop them instead, set `"verible.arguments": ["--rules_config_search"]`: Verible then reads `.rules.verible_lint` files, and the one in `server/uvmls/third_party/` turns its lint rules off for the bundled UVM. Your own files keep Verible's defaults, unless your project has a `.rules.verible_lint` of its own, which Verible then applies to them.
- With `uvm.useBundledUvm` off, the built-in model knows only the common UVM base classes and a few of their members, has no UVM sources to navigate into, and cannot be elaborated.
- Elaboration gets the same files, include directories and defines as the index, plus the filelist's `-timescale`. Other options, such as `-y`/`+libext+` library directories or a simulator's compatibility switches, are not passed to slang, which is also stricter than most simulators about non-standard code. A design that depends on them can show errors its simulator would not.
- Elaboration is skipped in discovery mode when a module, interface or program, or a package, would be defined more than once: in several files, or in one file that several roots include (usually several testbenches in one workspace); set `uvm.filelists`. Discovery leaves out of the compile any `.svh`/`.vh` header that nothing includes and that holds no module, interface, program or package (an old copy, a template). A file that exists only in an unsaved editor cannot be a compilation root.
- No license has been chosen yet (`UNLICENSED` in the manifests); pick one before publishing. The bundled UVM 1.2 is Apache-2.0; its `LICENSE.txt` and `NOTICE.txt` ship next to it and must stay there. The npm packages bundled into the extension (elkjs under EPL-2.0, the vscode-languageclient family under MIT, and their ISC/MIT dependencies) ship their licence texts in `THIRD_PARTY_NOTICES.txt`, which every client build regenerates; `npm run package` fails if it, elkjs's copyright headers or the UVM licence would be missing from the .vsix. The bundled Verible formatter (the Linux x86_64 static build of release v0.0-4296-g0f262651) is Apache-2.0: `client/scripts/fetch-verible.mjs` downloads the pinned tarball, checks its SHA-256 and the binary's own SHA-256 (also on every reuse of the cache in `~/.cache/uvmls/verible/`, so a tampered cache is fetched again); the licence text is vendored in `client/third_party/verible-LICENSE`; set `UVMLS_VERIBLE_TARBALL` to a local copy of the tarball to package fully offline, and `npm run package:linux-x64` also fails if the binary, its LICENSE or its notice entry would be missing from the .vsix. The generic `npm run package` ships no Verible or wheels. Each platform has its own cache and wheel lock; the formatter archive and binary pins are in `client/verible.lock.json`.
