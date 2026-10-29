# f7 spec: MCP server over the live UVM index

Written 2026-10-09 against main 2bbf7c9; corrected the same day after an independent review of every
code pointer and protocol claim. Roadmap item "MCP server over the same index, so AI assistants can
query real facts" (differentiator: DVT's MCP server has 40 tools, is separately licensed, and has no
config_db, factory-override or sequence tools). Scope: one batch, shippable on its own: implement ->
independent check -> refix -> review -> replica/e2e -> merge.

## Summary

`python -m uvmls mcp [DIR] [-f tb.f ...]` speaks the Model Context Protocol over stdio and answers
questions about a UVM testbench from the same index the editor uses: classes and roles, the topology
of a test with factory overrides applied, config_db sets and gets, lint findings, definitions and hover
text, elaboration diagnostics. It works without the IDE running (like DVT's standalone endpoint), and
inside VS Code the extension registers it so Copilot/Claude agents in the editor can call it.

## Design decisions

- **No new runtime dependency.** The `mcp` SDK (2.3.0, MIT) pulls 28 packages (pydantic, httpx,
  jsonschema, OpenTelemetry, PyJWT, cryptography, starlette, uvicorn); the server today depends only
  on pyslang and pygls. MCP over stdio is JSON-RPC 2.0 with newline-delimited messages (no
  Content-Length framing), and the methods we need are few. Implement them directly in
  `server/uvmls/mcp.py` (~300 lines) as an initialize-handshake server at protocol version
  `2025-11-25`. The current revision (`2026-07-28`) is stateless: no `initialize`, a mandatory
  `server/discover`, no `ping`. Modern clients probe `server/discover` first and fall back to the
  legacy handshake on any error, so: answer `server/discover` with `-32601` at once; on `initialize`,
  echo the client's version when it is one we support (`2024-11-05`, `2025-03-26`, `2025-06-18`,
  `2025-11-25`), else answer with `2025-11-25`. The initialize result declares `capabilities`
  (`tools`, `resources`, `prompts`, each `{"listChanged": false}`) and `serverInfo` (`name: "uvmls"`,
  `version`). Methods: `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`,
  `resources/list`, `resources/templates/list`, `resources/read`, `prompts/list` (empty). Never answer
  a notification (no `id`, e.g. `notifications/cancelled`). Unknown method: `-32601`; unknown tool
  name: `-32602`; bad arguments: an `isError` result naming the argument. Tools are listed in a fixed
  order, each with `title`, `description`, `inputSchema` and `annotations: {"readOnlyHint": true}`.
  Log to stderr only.
- **Same index code, separate process.** The MCP process builds its own `Workspace` from the same
  `ProjectConfig` the CLI builds (`cli.py` `_workspace`, which reads flags only; for
  `.vscode/settings.json` strip the `uvm.` prefix and call `ProjectConfig.from_settings`, which
  expands `${workspaceFolder}`). It does not talk to a running language server (the extension runs
  it with `--stdio`; sharing one process would couple the editor's latency to agent traffic).
  Settings come from the CLI flags, else `.vscode/settings.json` `uvm.*` keys in DIR when present
  (read once; document that this is a convenience, not VS Code's full settings merge).
- **Elaboration runs in the worker subprocess**, never in-process: `elab.elaborate()` compiles in the
  calling process and pyslang holds the GIL for the whole compile, so nothing else (not even `ping`)
  could be answered meanwhile. Spawn `python -m uvmls elab-worker <pid> <timeout>` with the request
  from `request_for(ws)` exactly as `server.py` does (same `ELAB_TIMEOUT`, same `PYTHONPATH`), check
  `skip_reason(ws)` first, and apply `elab.displayed()` to the listing.
- **Freshness.** The index is built at startup and refreshed lazily: the MCP process keeps its own
  `{path: (mtime_ns, size)}` over `ws.files()` (roots and included headers); before answering a tool
  call, at most once per second, it stats them. A changed file is re-parsed with
  `Workspace.update_document(path, None)` (which re-parses every root that includes it, then
  rebuilds); a file created or deleted, or a changed filelist, triggers `ws.reindex_all()`. The
  `reindex` tool forces a full rebuild. Unsaved editor text is not visible (document it). Lint
  `// uvm-lint: ignore` directives are read at parse time, so they take effect at the next refresh.
- **Output size.** Every list tool takes `limit` (default 200, max 2000) and `offset`, returns
  `{"items": [...], "total": N, "truncated": bool}`. Each item carries a `location`
  `{"file","line","col","endLine","endCol"}` with **1-based lines and columns** (columns in
  characters), converted from the index's 0-based byte columns in one helper; the CLI's `--json`
  output stays as it is (0-based), so tool outputs are documented, not "equal to the CLI". File
  paths are workspace-relative when under a workspace root.
- **Tool results** are `content: [{"type":"text","text": <JSON>}]` plus `structuredContent` with the
  same object (clients that support it get typed data). Errors are `isError: true` with a one-line
  reason, never a traceback.
- **Security.** Read-only: no tool writes files, runs simulators, or executes code. `elaborate` runs
  slang in the worker subprocess with the existing timeout. Paths in arguments are resolved under
  the workspace roots; a path outside them is refused (`"outside the workspace"`). No network.

## Tools (names, arguments, what they return)

| Tool | Arguments | Returns |
|---|---|---|
| `stats` | - | `Workspace.stats()` plus `uvm` (`elab.uvm_label(ws.sources)`), `version` (`ws.version`), roots count, seconds since the last (re)index |
| `tests` | - | `views.list_tests`: every test class with its package, base and location, and the default |
| `classes` | `query?` (substring/glob on name), `role?` (test/env/agent/driver/monitor/sequencer/sequence/item/component/object), `package?`, `limit`, `offset` | items: qualname, name, package, role, base, library(bool), location |
| `class` | `name` (qualname via `ws.find_class`, or bare name via `classes_by_name`; several matches return the candidates as an error) | declaration: base chain (`ws.chain`), type parameters, members (variables with types, methods with signatures and the doc from `nav.method_doc` rendered with `naturaldocs.markdown`), registration macro, subclasses (`ws.subclasses`), where created |
| `topology` | `test?` (bare class name) | `views.topology(ws, test)`: `tests`, `default`, `runTest`, `test`, `root` (tree with `overriddenFrom`/`conditional`), `connections`, `overrides`, with locations converted as above; an unknown test gives `root: null` in the view, which the tool turns into an `isError` naming the known tests |
| `effective_type` | `test?`, `file?`+`line?` (one create) | serialized from `views.effective_type` (an `EffectiveTypes` dict of `Range -> [EffectiveType]` plus `.counts`; it shares `_effective_type_cache` with `topology`): items `{create: location, original, effective, override: {kind, owner, method, location} | null, instances, count}` |
| `config_db` | `field?`, `status?` (`ok`/`no-set`/`no-get`/`type-mismatch`/`dynamic`), `file?`, `limit`, `offset` | rows of `config_db_report`: field, canon, sets and gets (type, scope pattern `inst`, `context`, `owner`, `method`, `optional`, `conditional`, location), status |
| `lint` | `codes?`, `file?`, `severity?`, `limit`, `offset` | `lints.lint(ws)` (a `dict[path, list[Diag]]`) plus `ws.user_diagnostics()`, deduplicated as `cli.py` does, as items with code, severity, message, location, related |
| `elaborate` | `tops?` | the worker's result: `counts`, `tops`, `error`, the `displayed()` diagnostics with locations (`truncated` when the display limits cut the list); a skip reason becomes `isError` |
| `definition` | `file`, `line`, `col` | `Navigator.definition(path, line, col, text)` locations, with `text = ws.texts[path]` (user files only; a library file is refused) and `col` converted to UTF-8 bytes |
| `hover` | `file`, `line`, `col` | the hover markdown from `Navigator.hover(path, line, col, text)`, same conventions |
| `references` | `file`, `line`, `col`, `limit`, `offset` | name-based occurrences from `Navigator.references` (documented as name-based) |
| `symbols` | `query`, `limit`, `offset`, `definition?` | `Navigator.workspace_symbols` (fuzzy subsequence match, methods only when the query is non-empty); with `definition: true` and an exact qualified name, the declaration location via `ws.find_class` |
| `signature` | `file`, `line`, `col` | `signatures.signature(nav, path, line, col)` gives `(target, method, label, formals, active)`; the doc comes from `nav.method_doc` + `naturaldocs.markdown` as `server.py`'s handler does |
| `sequences` | `limit`, `offset` | sequence classes (role `sequence`) with their item type (the first argument of `uvm_sequence #(...)` in `ws.chain`), `p_sequencer` declarations, and their start sites: each `SeqStart` stores the receiver and sequencer as expression text, so the tool resolves the receiver through the starting method's creates and locals (`nav.resolve_expr`) and reports `{sequence, sequencer_expr, owner, method, location}`, with `sequence: null` when it cannot be resolved; this is the sequence view DVT's MCP lacks |
| `creates` | `type?`, `file?`, `limit`, `offset` | every factory create in the index (the factory map): created type, parent expression, owner, location |
| `includes` | `file?` | the include graph from `FileIndex.includes` and `ws.roots_for`: which roots reach a file, what a file includes |
| `units` | `kind?` (module/interface/program/package), `limit`, `offset` | design units with ports, instances and `run_test` calls |
| `macros` / `typedefs` | `query?`, `limit`, `offset` | `ws.macros_by_name` / `ws.typedefs_by_name` with locations |
| `reindex` | - | rebuilds the index; returns `stats` |

Resources: `uvm://stats`, `uvm://topology/{test}`, `uvm://configdb`, `uvm://lint` as JSON, and
`uvm://file/{relative path}` returning the indexed text (read-only mirror, same workspace-root check).
Resource templates listed through `resources/templates/list`.

## Where it lives

- `server/uvmls/mcp.py`: transport (stdio loop with `asyncio`; one request at a time, reads in a
  thread, `ping` answered from the reader thread; `elaborate` awaits the worker subprocess so `ping`
  keeps working), dispatch, the JSON schemas for every tool (hand-written dicts, validated with a
  small required/enum check, not jsonschema), the location converter, and the lazy refresh.
  `__main__.py` adds `mcp` to its subcommand list and `cli.py` adds it to `choices` and reuses the
  shared flags. Tool bodies call the existing functions; the only new index-adjacent code is the
  `EffectiveTypes` and `SeqStart` serializers.
- VS Code: `client/package.json` contributes `contributes.mcpServerDefinitionProviders: [{id, label}]`
  and the extension calls `vscode.lm.registerMcpServerDefinitionProvider` (final in VS Code 1.101:
  bump `engines.vscode` to `^1.101.0` and `@types/vscode` to `~1.101.0`; client/src uses no API newer
  than that). The provider returns one `McpStdioServerDefinition(label, command, args, env, version)`
  with `cwd` set to the server directory, never a workspace folder (`python -m` imports from the
  working directory first, so a workspace `uvmls.py` would run instead of the server), and
  `env.PYTHONPATH` set as `extension.ts` sets it for the language server (or `-m uvmls` fails when
  packaged), command = the resolved `uvm.pythonPath`,
  args = `-m uvmls mcp <folder>` plus the whole `uvm` configuration passed as flags (`-f` per filelist,
  `-I` per include dir, `-D` per define, `--uvm-home`, `--no-bundled-uvm`, excludes), and it fires
  `onDidChangeMcpServerDefinitions` when those settings change. Setting `uvm.mcp.enabled` (default
  true). Document Claude Code / Claude Desktop configuration
  (`claude mcp add uvmls -- <python> -m uvmls mcp <dir> -f tb.f`) in the README.
- README: a new "MCP server" section (what it is, the tool table, the configuration snippets, what is
  not visible: unsaved text, runtime values); the feature table row; the DVT comparison line.
- `tools/mcp_smoke.py`: a stdio client used by tests and by the e2e check.

## Non-goals

HTTP/SSE transport, authentication, writing files or running fixes through MCP, prompts, sampling,
sharing the language server's process, an in-editor chat participant (separate roadmap item).

## Cross-cutting

- Python >= 3.10, pyslang >= 9 (the shim path works without the bundled UVM).
- Startup cost equals one CLI index (documented from the README table); the first tool call after a
  file change pays one root re-parse.
- Every tool call is bounded: `limit` caps, elaboration keeps its timeout, and a tool that raises
  returns `isError` with the exception type and message (logged with traceback to stderr).
- Concurrency: requests are answered in order; a long `elaborate` blocks later tool calls (the
  README states it), while `ping` is answered from the reader thread because the compile runs in the
  worker subprocess, not in this process.

## Acceptance (tests + e2e)

1. `tools/mcp_smoke.py` against `examples/alu_tb` with `-f alu_tb.f`: `initialize` with
   `2025-06-18` is echoed, with `2026-07-28` answers `2025-11-25`, and declares capabilities and
   serverInfo; `server/discover` gets `-32601`; `tools/list` lists every tool in the table in a fixed
   order with valid JSON schemas (each has `inputSchema.type == "object"`, documented properties, and
   `readOnlyHint`); `resources/list` lists the four static resources and `resources/templates/list`
   the template.
2. `classes` with `role: "driver"` returns `alu_driver` (and `alu_error_driver`) with locations in
   `agent/alu_driver.svh`; `query: "alu_*"` matches by glob; `limit: 1` sets `truncated: true` and
   `total` to the full count; `offset` pages.
3. `class` for `alu_agent` lists `m_driver`, `m_sequencer`, `m_monitor` with their types, `build_phase`
   and `connect_phase` with signatures, the registration macro, and `alu_env` as the creator; a bare
   name declared in two packages (plant a duplicate in a test package) returns the candidates.
4. `topology` with `test: "alu_error_test"` matches the CLI's `topology --json` tree after the
   location conversion (same nodes, `m_driver` overridden from `alu_driver` to `alu_error_driver`,
   which the example already contains); without a test the default `alu_random_test` is used and
   `tests` lists them all; an unknown test is an `isError` naming the known ones; `effective_type` for
   the driver create under `alu_error_test` reports original `alu_driver`, effective
   `alu_error_driver`, the override site in `alu_tests.svh`, and plain data under `alu_random_test`.
5. `config_db` rows match the CLI's `configdb --json` after the location conversion; `field:
   "alu_agent_config"` filters; `status: "no-set"` after planting a typo'd get returns exactly that row.
6. `lint` lists the planted `config-db-no-set` with its location; after writing a `// uvm-lint:
   ignore` directive to disk, the next call (after the refresh) no longer lists it.
7. `symbols` with `query: "alu_pkg::alu_driver", definition: true` and `definition` at `(file, line,
   col)` on `m_driver` in the agent both return the declaration in `alu_driver.svh`; `hover` on
   `get_next_item` includes the NaturalDocs text with the inherited attribution; `signature` inside
   `drive_item(` names the formal; `hover` on a bundled UVM file is refused with a clear message.
8. `sequences` lists `alu_random_seq` with item `alu_seq_item` and its start in `alu_base_test`
   (`seq.start(m_env.m_agent.m_sequencer)` resolved to the sequence class); `includes` for
   `alu_agent.svh` names its including root; `creates` lists the driver create with its parent.
9. Freshness: edit `alu_env.svh` on disk (move the set one line down), call `config_db` within the
   same second and after: the location updates by the next call at the latest; `reindex` returns new
   stats. A path outside the workspace (`/etc/hostname`) in `uvm://file/` is refused.
10. `elaborate` on the example returns 0 errors and the top module (through the worker subprocess:
    assert the MCP process's own CPU time stays small); `tops: ["no_such"]` returns an error object,
    not an exception; stderr stays free of tracebacks over the whole run.
11. Protocol: an unknown method gets `-32601`; an unknown tool gets `-32602`; malformed JSON gets
    `-32700`; a tool call with a missing required argument gets `isError` naming the argument; a
    notification never gets a reply; `ping` answers while `elaborate` runs.
12. In the VS Code replica (1.140): with the extension installed, "MCP: List Servers" shows `uvmls`
    (screenshot), and the smoke client driven with the exact command line, cwd and environment the
    provider produces (log them from the extension) can call `topology`. README snippets copy-paste
    into `claude mcp add` and into `.vscode/mcp.json`.
