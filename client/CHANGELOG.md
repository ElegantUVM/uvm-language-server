# Changelog

All notable changes to ElegantUVM. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/) once 1.0 is reached.

## [Unreleased]

## [0.1.0] - 2026-11-05

The first public preview. Everything below is in `docs/STATUS.md` with its known limits.

### Navigation and editing
- Go to definition and type definition, hover with UVM documentation, references, completion,
  document and workspace symbols, type hierarchy, `` `include `` links, code lenses, document
  highlights with keyword-pair matching, structural folding, signature help.
- Inlay hints: factory override targets on `type_id::create()` and bound types of
  parameter-typed members.
- Built-in SystemVerilog and Verilog syntax highlighting (the Verilog-HDL/SystemVerilog grammars,
  MIT), with the UVM highlighting injected on top.

### UVM views
- One-click UML class graph of the testbench with roles, create/override edges, search, focus,
  package overview and PNG export.
- `uvm_test_top` topology per test, with factory overrides applied, conditional creates marked
  and `connect()` edges drawn; it follows an env (or any component) created through a class type
  parameter of a base test (OpenTitan's `ENV_T`, UVMF).
- `config_db` cross-reference: every set and get by field, with no-set and type-mismatch status;
  lenses, hover and go-to-definition between gets and sets. Calls through a typedef count: UVM's
  `uvm_config_int`, `uvm_config_string`, `uvm_config_object`, `uvm_config_wrapper`, and a project's
  `typedef uvm_config_db #(T) t_db;`.

### Diagnostics and fixes
- slang elaboration diagnostics (on save, on change, or on demand) in a separate process.
- Eleven UVM lints with quick fixes, fix-all, and `// uvm-lint: ignore` waivers. `unknown-base`
  does not fire on `extends T` when `T` is a type parameter of the class; a `uvm_config_db` set
  whose field name is computed silences `config-db-no-set` only for gets of its own type.

### Projects
- Filelists (`-f/-F`, `+incdir+`, `+define+`, `$VAR`, `-timescale`) and zero-configuration discovery.
- UVM library selection (`uvm.uvmHome`, `$UVM_HOME`, a project copy, or the bundled Accellera UVM 1.2).

### Formatting
- Format Document and Format Selection through Verible, bundled for Linux x86_64.

### CLI, CI and AI
- `python -m uvmls lint | elab | graph | topology | configdb | stats`, all with `--json`; SARIF
  output and a composite GitHub Action for code scanning; `uvmls --version`.
- `python -m uvmls mcp`: a read-only Model Context Protocol server with 21 tools and 6 resources
  over the same index, registered with VS Code automatically.

### Packaging
- Apache License 2.0; NOTICE, THIRD_PARTY_NOTICES, AI-USE, CONTRIBUTING and SECURITY documents.
- Publisher `elegantuvm`, extension `elegantuvm`, PyPI package `uvmls`. The server requires Python 3.11
  or newer (pyslang 12).
- A `linux-x64` package that carries the server's Python wheels and Verible, so first-run setup needs
  no network; `uvm.pipArgs` for a mirror or proxy on the generic package; `python3.11`–`python3.14`
  are probed after `python3`; first-run setup does not upgrade pip.
- A feature tour (`docs/media/`), recorded on the ALU example.
