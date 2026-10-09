# f7 spec: MCP server over the live UVM index

Written 2026-10-09 against main b610a22. Roadmap item "MCP server over the same index, so AI assistants can
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

- **No new runtime dependency.** The `mcp` SDK (2.3.0, MIT) pulls pydantic, httpx, jsonschema,
  OpenTelemetry and pyjwt; the server today depends only on pyslang and pygls. MCP over stdio is
  JSON-RPC 2.0 with newline-delimited messages (no Content-Length framing), and the methods we need are
  few. Implement them directly in `server/uvmls/mcp.py` (~300 lines), protocol version `2025-06-18`
  (accept any version the client offers and answer with ours; the client decides). Methods:
  `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`, `resources/list`,
  `resources/read`, `prompts/list` (empty). Reject anything else with `-32601`. Log to stderr only.
- **Same index code, separate process.** The MCP process builds its own `Workspace` from the same
  `ProjectConfig` the CLI builds (`cli.py` `_workspace`), with elaboration available on demand through
  `elab.elaborate(request_for(ws))`. It does not talk to a running language server: the LSP server has
  no listening socket, and sharing one process would couple the editor's latency to agent traffic.
  Settings come from the CLI flags, else `.vscode/settings.json` `uvm.*` keys in DIR when present
  (read once; document that this is a convenience, not VS Code's full settings merge).
- **Freshness.** The index is built at startup and refreshed lazily: before answering a tool call,
  compare each root's mtime (`FileIndex.files` stamps, as `project.directory_stamp` does) at most once
  per second; re-parse changed roots with `Workspace.update_document(path, None)`. A `reindex` tool
  forces a full rebuild. Unsaved editor text is not visible (document it).
- **Output size.** Every list tool takes `limit` (default 200, max 2000) and `offset`, returns
  `{"items": [...], "total": N, "truncated": bool}`. Each item carries a `location`
  `{"file","line","col"}` (1-based for humans, documented) and the file path is workspace-relative
  when under a workspace root.
- **Tool results** are `content: [{"type":"text","text": <JSON>}]` plus `structuredContent` with the
  same object (clients that support it get typed data). Errors are `isError: true` with a one-line
  reason, never a traceback.
- **Security.** Read-only: no tool writes files, runs simulators, or executes code. `elaborate` runs
  slang in the existing worker process with the existing timeout. Paths in arguments are resolved
  under the workspace roots; a path outside them is refused (`"outside the workspace"`). No network.

## Tools (names, arguments, what they return)

| Tool | Arguments | Returns |
|---|---|---|
| `stats` | - | `Workspace.stats()` plus `uvm` library label, roots count, index age |
| `classes` | `query?` (substring/glob on name), `role?` (test/env/agent/driver/monitor/sequencer/sequence/item/component/object), `package?`, `limit`, `offset` | items: qualname, name, package, role, base, library(bool), location |
| `class` | `name` (qualname or name; ambiguous names return the candidates as an error) | declaration: base chain, type parameters, members (variables with types, methods with signatures and the doc hover text), registration macro, subclasses, where created |
| `topology` | `test?` | the `views.topology()` dict as today (root tree with overriddenFrom/conditional, connections, overrides); `tests` listing when `test` is omitted and no default exists |
| `effective_type` | `test?`, `create_at?` (file+line) | for every factory create (or the one at a location): original type, effective type, override site, instance counts (from `views.effective_type`) |
| `config_db` | `field?`, `status?` (ok/no-set/type-mismatch), `file?`, `limit`, `offset` | rows of `config_db_report`: field, sets (type, scope pattern, location), gets (type, required, location), status |
| `lint` | `codes?`, `file?`, `severity?`, `limit`, `offset` | `lints.lint(ws)` + index diagnostics as items with code, severity, message, location, related; honours `// uvm-lint: ignore` |
| `elaborate` | `tops?`, `wait?` (default true) | `elab.elaborate` result: counts, tops, listed diagnostics (display limits apply; say so in `truncated`) |
| `definition` | `file`, `line`, `col` or `symbol` (qualified name) | locations, as `Navigator.definition` / `workspace_symbols` |
| `hover` | `file`, `line`, `col` | the hover markdown, as `Navigator.hover` |
| `references` | `file`, `line`, `col` or `symbol`, `limit`, `offset` | name-based occurrences (documented as such) |
| `symbols` | `query`, `limit`, `offset` | `Navigator.workspace_symbols` |
| `signature` | `file`, `line`, `col` | `signatures.signature` result: label, formals, active, doc |
| `sequences` | `limit`, `offset` | sequence classes with their item type, `p_sequencer`, where started (`seq_starts` in `CallSet`) and from which sequencer/test; this is the sequence view DVT's MCP lacks |
| `reindex` | - | rebuilds the index; returns `stats` |

Resources: `uvm://stats`, `uvm://topology/{test}`, `uvm://configdb`, `uvm://lint` as JSON, and
`uvm://file/{relative path}` returning the indexed text (read-only mirror, same workspace-root check).
Resource templates listed through `resources/templates/list`.

## Where it lives

- `server/uvmls/mcp.py`: transport (stdio loop with `asyncio`; one request at a time, reads in a thread),
  dispatch, the JSON schemas for every tool (hand-written dicts, validated with a small required/enum
  check, not jsonschema), and the lazy refresh. `__main__.py` adds the `mcp` subcommand; `cli.py` adds
  its argparse (reuse the shared flags). Tool bodies call the existing functions; no new index code.
- VS Code: `client/package.json` contributes `mcpServerDefinitionProviders` (VS Code 1.99+: the
  `vscode.lm.registerMcpServerDefinitionProvider` API; bump `engines.vscode` to `^1.99.0` and
  `@types/vscode` to match) providing one stdio server definition `uvmls` with the configured
  `uvm.pythonPath`, `-m uvmls mcp`, the first workspace folder and `-f` for each `uvm.filelists`
  entry. Setting `uvm.mcp.enabled` (default true). Document Claude Code / Claude Desktop configuration
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
- Concurrency: requests are answered in order; a long `elaborate` blocks later calls, which the
  README states (clients can send `ping` in parallel; it is answered from the reader thread).

## Acceptance (tests + e2e)

1. `tools/mcp_smoke.py` against `examples/alu_tb` with `-f alu_tb.f`: `initialize` reports the protocol
   version and server name/version; `tools/list` lists the 15 tools with valid JSON schemas (each has
   `inputSchema.type == "object"` and documented properties); `resources/list` lists the four static
   resources and the template.
2. `classes` with `role: "driver"` returns `alu_driver` (and `alu_error_driver`) with locations in
   `agent/alu_driver.svh`; `query: "alu_*"` matches by glob; `limit: 1` sets `truncated: true` and
   `total` to the full count; `offset` pages.
3. `class` for `alu_agent` lists `m_driver`, `m_sequencer`, `m_monitor` with their types, `build_phase`
   and `connect_phase` with signatures, the registration macro, and `alu_env` as the creator; an
   ambiguous bare name (plant a second `config` class in a test package) returns the candidates.
4. `topology` with `test: "alu_add_test"` equals the CLI's `topology --json` output; without a test,
   the default test is used and `tests` lists them all; `effective_type` for the driver create in
   `alu_add_test` shows `alu_driver -> my_driver`-style data when a type override is planted
   (reuse the e2e/f4.js fixture edit) and plain data without it.
5. `config_db` equals the CLI's `configdb --json` rows; `field: "alu_agent_config"` filters; `status:
   "no-set"` after planting a typo'd get returns exactly that row.
6. `lint` lists the planted `config-db-no-set` with its location and respects `// uvm-lint: ignore`
   (the finding disappears after the directive is written to disk and the next call refreshes).
7. `definition` for `symbol: "alu_pkg::alu_driver"` and for `(file, line, col)` on `m_driver` in the
   agent both return the declaration in `alu_driver.svh`; `hover` on `get_next_item` includes the
   NaturalDocs text with the inherited attribution; `signature` inside `drive_item(` names the formal.
8. `sequences` lists `alu_random_seq` with item `alu_seq_item` and its start site in the test.
9. Freshness: edit `alu_env.svh` on disk (move the set one line down), call `config_db` within the
   same second and after: the location updates by the next call at the latest; `reindex` returns new
   stats. A path outside the workspace (`/etc/hostname`) in `uvm://file/` is refused.
10. `elaborate` on the example returns 0 errors and the top module; `tops: ["no_such"]` returns an
    error object, not an exception; stderr stays free of tracebacks over the whole run.
11. Protocol: an unknown method gets `-32601`; malformed JSON gets `-32700`; a tool call with a missing
    required argument gets `isError` naming the argument; `ping` answers while `elaborate` runs.
12. In the VS Code replica: with the extension installed, the MCP server definition is listed
    (`vscode.lm` provider registered; assert through the command palette "MCP: List Servers" or the
    extension's own status log), and Claude Code configured with `claude mcp add` can call `topology`
    (drive it headlessly with the smoke client against the same command line the extension would use;
    screenshot the MCP: List Servers quick pick). README snippets copy-paste into `claude mcp add` and
    into `.vscode/mcp.json`.
