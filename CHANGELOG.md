# Changelog

All notable changes to ElegantUVM. The format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/) once 1.0 is reached.

## [Unreleased]

### Added
- Windows x64 and macOS arm64/x64 package builds with pinned offline wheels, per-target caches,
  archive checks and native setup smoke jobs. Native CI acceptance remains pending.
- Bundled Verible on Windows x64 and macOS arm64. Intel macOS ships wheels only; use an external
  formatter through `uvm.formatter.path` (for example Homebrew Verible).
- Apache License 2.0; NOTICE, THIRD_PARTY_NOTICES, AI-USE, CONTRIBUTING and SECURITY documents.
- Marketplace identity: publisher `elegantuvm`, extension `elegantuvm`.
- A `linux-x64` package that carries the server's Python wheels, so first-run setup needs no network;
  `uvm.pipArgs` for a mirror or proxy on the generic package; `python3.11`–`python3.14` are probed after `python3`.
- `python -m uvmls --version` / `uvmls --version`.
- config_db calls through a typedef count: UVM's `uvm_config_int`, `uvm_config_string`, `uvm_config_object`,
  `uvm_config_wrapper`, and a project's `typedef uvm_config_db #(T) t_db;`.

### Changed
- The generic VSIX ships no formatter or wheels; the Linux x64 package retains both.
- The server requires Python 3.11 or newer (pyslang 12 dropped 3.10).
- First-run setup no longer upgrades pip.

### Fixed
- Windows setup probes explicit `py` launcher versions; index keys normalize drive letters and editor URIs.
- The topology follows an env (or any component) created through a class type parameter of a base test
  (OpenTitan's `ENV_T`, UVMF): trees that stopped at the test now expand (OpenTitan uart 1 → 42 nodes).
- `unknown-base` no longer fires on `extends T` when `T` is a type parameter of the class.
- A `uvm_config_db` set whose field name is computed only silences `config-db-no-set` for gets of its own type.

## [0.1.0] - unreleased preview

The first public preview. Everything below is in `docs/STATUS.md` with its known limits.

### Navigation and editing
- Go to definition and type definition, hover with UVM documentation, references, completion,
  document and workspace symbols, type hierarchy, `` `include `` links, code lenses, document
  highlights with keyword-pair matching, structural folding, signature help.
- Inlay hints: factory override targets on `type_id::create()` and bound types of
  parameter-typed members.

### UVM views
- One-click UML class graph of the testbench with roles, create/override edges, search, focus,
  package overview and PNG export.
- `uvm_test_top` topology per test, with factory overrides applied, conditional creates marked
  and `connect()` edges drawn.
- `config_db` cross-reference: every set and get by field, with no-set and type-mismatch status;
  lenses, hover and go-to-definition between gets and sets.

### Diagnostics and fixes
- slang elaboration diagnostics (on save, on change, or on demand) in a separate process.
- Eleven UVM lints with quick fixes, fix-all, and `// uvm-lint: ignore` waivers.

### Projects
- Filelists (`-f/-F`, `+incdir+`, `+define+`, `$VAR`, `-timescale`) and zero-configuration discovery.
- UVM library selection (`uvm.uvmHome`, `$UVM_HOME`, a project copy, or the bundled Accellera UVM 1.2).

### Formatting
- Format Document and Format Selection through Verible, bundled for Linux x86_64.

### CLI, CI and AI
- `python -m uvmls lint | elab | graph | topology | configdb | stats`, all with `--json`; SARIF
  output and a composite GitHub Action for code scanning.
- `python -m uvmls mcp`: a read-only Model Context Protocol server with 21 tools and 5 resources
  over the same index, registered with VS Code automatically.
